from __future__ import annotations

import asyncio, json, time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import research_v23_futures_ws_l2 as v23

STATE=Path('data/v24_l2_alpha_diagnostic_state.json')
RUN_SECONDS=210
HORIZONS=(1,3,5,10,30,60)
NEW_EVENT_CUTOFF_SEC=RUN_SECONDS-max(HORIZONS)-5
TIERS={
    'base':{'imb':0.10,'micro':0.02,'flow2':0.08,'flow5':0.04,'persist':2},
    'strong':{'imb':0.20,'micro':0.04,'flow2':0.15,'flow5':0.08,'persist':3},
    'elite':{'imb':0.32,'micro':0.07,'flow2':0.24,'flow5':0.12,'persist':3},
}
MAX_SPREAD_BPS=1.5
COOLDOWN_SEC=12

def utcnow():return datetime.now(timezone.utc).isoformat()
def load_state():
    if STATE.exists():
        try:return json.loads(STATE.read_text())
        except Exception:pass
    return {'version':'quantbot-v24-forward-l2-alpha-diagnostic','purpose':'FORWARD_ONLY_NO_LOOKAHEAD_L2_RAW_EDGE_DIAGNOSTIC_NO_LIVE',
            'symbol':'BTCUSDT','live_orders':False,'runs':0,'ws_success_runs':0,'observations':0,'depth_events':0,'aggtrade_events':0,
            'events':[],'rejections':{},'created_at':utcnow()}
def save_state(st):st['updated_at']=utcnow();STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(st,indent=2))
def percentile(a,p):return float(np.percentile(a,p)) if a else None
def summarize(st):
    out={};events=st.get('events',[])
    for tier in TIERS:
        out[tier]={}
        for h in HORIZONS:
            vals=[float(x['resolved'][str(h)]) for x in events if x['tier']==tier and str(h) in x.get('resolved',{})]
            out[tier][str(h)]=({'n':0,'mean_bps':None,'median_bps':None,'p10':None,'p90':None,'positive_pct':None,'gt4bps_pct':None,'gt7_5bps_pct':None}
                if not vals else {'n':len(vals),'mean_bps':float(np.mean(vals)),'median_bps':float(np.median(vals)),'p10':percentile(vals,10),'p90':percentile(vals,90),
                                  'positive_pct':100*sum(v>0 for v in vals)/len(vals),'gt4bps_pct':100*sum(v>4 for v in vals)/len(vals),'gt7_5bps_pct':100*sum(v>7.5 for v in vals)/len(vals)})
    st['summary']=out
def side_for(hist,cur,t2,t5,cfg):
    if cur['spread_bps']<=0 or cur['spread_bps']>MAX_SPREAD_BPS or len(hist)<cfg['persist']:return 0
    rows=list(hist)[-cfg['persist']:]
    if all(x['imb10']>=cfg['imb'] and x['microedge_bps']>=cfg['micro'] for x in rows) and t2['flow']>=cfg['flow2'] and t5['flow']>=cfg['flow5']:return 1
    if all(x['imb10']<=-cfg['imb'] and x['microedge_bps']<=-cfg['micro'] for x in rows) and t2['flow']<=-cfg['flow2'] and t5['flow']<=-cfg['flow5']:return -1
    return 0

async def main():
    st=load_state();st['runs']=int(st.get('runs',0))+1
    tape=deque();hist=deque(maxlen=30);pending=[];latest=None;q=asyncio.Queue(maxsize=20000);status={'depth':False,'aggtrade':False};rejects=Counter(st.get('rejections',{}))
    last_event={(tier,side):-1e9 for tier in TIERS for side in(-1,1)};start=time.time();last_obs=0.0
    tasks=[asyncio.create_task(v23.reader('depth',v23.DEPTH_WS,q,status)),asyncio.create_task(v23.reader('aggtrade',v23.TAPE_WS,q,status))]
    try:
        while time.time()-start<RUN_SECONDS:
            timeout=min(2.0,RUN_SECONDS-(time.time()-start))
            if timeout<=0:break
            try:label,d=await asyncio.wait_for(q.get(),timeout=timeout)
            except asyncio.TimeoutError:continue
            now=time.time();now_ms=int(now*1000)
            if label=='reader_error':rejects[f"{d['label']}_reader_error"]+=1;st[f"last_{d['label']}_error"]=d['error'];continue
            if label=='depth':
                m=v23.book_metrics(d)
                if m:latest=m;st['depth_events']=int(st.get('depth_events',0))+1
                else:rejects['depth_parse']+=1
            elif label=='aggtrade':
                try:
                    tr={'T':int(d.get('T',d.get('E'))),'p':float(d['p']),'q':float(d['q']),'m':bool(d['m'])};tr['notional']=tr['p']*tr['q'];tape.append(tr);v23.prune_tape(tape,tr['T'],20);st['aggtrade_events']=int(st.get('aggtrade_events',0))+1
                except Exception:rejects['aggtrade_parse']+=1
            if latest is None or now-last_obs<1.0:continue
            last_obs=now;st['observations']=int(st.get('observations',0))+1;t2=v23.tape_window(tape,now_ms,2);t5=v23.tape_window(tape,now_ms,5);hist.append({**latest,'wall_ts':now})
            still=[]
            for ev in pending:
                age=now-ev['wall_ts'];ret=ev['side']*(latest['mid']/ev['entry_mid']-1)*1e4;ev['mfe_bps']=max(ev['mfe_bps'],ret);ev['mae_bps']=min(ev['mae_bps'],ret)
                for h in HORIZONS:
                    if age>=h and str(h) not in ev['resolved']:ev['resolved'][str(h)]=ret
                if age>=max(HORIZONS):st['events'].append({k:v for k,v in ev.items() if k!='wall_ts'})
                else:still.append(ev)
            pending=still
            elapsed=now-start
            if elapsed<=NEW_EVENT_CUTOFF_SEC and status['depth'] and status['aggtrade']:
                for tier,cfg in TIERS.items():
                    side=side_for(hist,latest,t2,t5,cfg)
                    if side and now-last_event[(tier,side)]>=COOLDOWN_SEC:
                        last_event[(tier,side)]=now;pending.append({'tier':tier,'side':side,'signal_ts':utcnow(),'wall_ts':now,'entry_mid':latest['mid'],
                            'spread_bps':latest['spread_bps'],'imb10':latest['imb10'],'microedge_bps':latest['microedge_bps'],'flow2':t2['flow'],'flow5':t5['flow'],
                            'resolved':{},'mfe_bps':0.0,'mae_bps':0.0})
            elif elapsed>NEW_EVENT_CUTOFF_SEC:rejects['end_run_cutoff_observations']+=1
    finally:
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        both=bool(status['depth'] and status['aggtrade']);st['depth_ws_ok']=bool(status['depth']);st['aggtrade_ws_ok']=bool(status['aggtrade']);st['ws_access_ok']=both
        if both:st['ws_success_runs']=int(st.get('ws_success_runs',0))+1
        st['rejections']=dict(rejects);st['new_event_cutoff_sec']=NEW_EVENT_CUTOFF_SEC;st['events']=st.get('events',[])[-5000:];summarize(st);save_state(st);print(json.dumps(st,indent=2))

if __name__=='__main__':asyncio.run(main())
