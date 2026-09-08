from __future__ import annotations

import asyncio, json, time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import websockets

STATE = Path('data/v24_l2_alpha_diagnostic_state.json')
WS = ('wss://fstream.binance.com/stream?streams='
      'btcusdt@depth20@100ms/btcusdt@aggTrade')
RUN_SECONDS = 210
HORIZONS = (1, 3, 5, 10, 30, 60)
TIERS = {
    'base':   {'imb': 0.10, 'micro': 0.02, 'flow2': 0.08, 'flow5': 0.04, 'persist': 2},
    'strong': {'imb': 0.20, 'micro': 0.04, 'flow2': 0.15, 'flow5': 0.08, 'persist': 3},
    'elite':  {'imb': 0.32, 'micro': 0.07, 'flow2': 0.24, 'flow5': 0.12, 'persist': 3},
}
MAX_SPREAD_BPS = 1.5
COOLDOWN_SEC = 12


def utcnow(): return datetime.now(timezone.utc).isoformat()

def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except Exception: pass
    return {
        'version':'quantbot-v24-forward-l2-alpha-diagnostic',
        'purpose':'FORWARD_ONLY_NO_LOOKAHEAD_L2_RAW_EDGE_DIAGNOSTIC_NO_LIVE',
        'symbol':'BTCUSDT','live_orders':False,'runs':0,'ws_success_runs':0,
        'observations':0,'depth_events':0,'aggtrade_events':0,'events':[],
        'stream_counts':{},'rejections':{},'created_at':utcnow()
    }

def save_state(st):
    st['updated_at']=utcnow(); STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(st,indent=2))

def book_metrics(d):
    br = d.get('bids') if isinstance(d.get('bids'),list) else d.get('b')
    ar = d.get('asks') if isinstance(d.get('asks'),list) else d.get('a')
    if not isinstance(br,list) or not isinstance(ar,list): return None
    bids=[(float(p),float(q)) for p,q in br[:10]]; asks=[(float(p),float(q)) for p,q in ar[:10]]
    if not bids or not asks:return None
    bb,bq=bids[0]; ba,aq=asks[0]; mid=(bb+ba)/2
    b10=sum(q for _,q in bids); a10=sum(q for _,q in asks)
    imb=(b10-a10)/(b10+a10) if b10+a10 else 0.0
    micro=(ba*bq+bb*aq)/(bq+aq) if bq+aq else mid
    return {'mid':mid,'bb':bb,'ba':ba,'spread_bps':(ba-bb)/mid*1e4,
            'imb10':imb,'microedge_bps':(micro/mid-1)*1e4,
            'bid_usd':sum(p*q for p,q in bids),'ask_usd':sum(p*q for p,q in asks)}

def prune(tape,now_ms,sec=20):
    cut=now_ms-sec*1000
    while tape and tape[0]['T']<cut:tape.popleft()

def tape_window(tape,now_ms,sec):
    cut=now_ms-sec*1000; rows=[x for x in tape if x['T']>=cut]
    buy=sum(x['notional'] for x in rows if not x['m']); sell=sum(x['notional'] for x in rows if x['m']); tot=buy+sell
    return {'flow':(buy-sell)/tot if tot else 0.0,'count':len(rows),'notional':tot}

def percentile(a,p):
    return float(np.percentile(a,p)) if a else None

def summarize(st):
    out={}
    events=st.get('events',[])
    for tier in TIERS:
        out[tier]={}
        for h in HORIZONS:
            vals=[float(x['resolved'][str(h)]) for x in events if x['tier']==tier and str(h) in x.get('resolved',{})]
            if not vals:
                out[tier][str(h)]={'n':0,'mean_bps':None,'median_bps':None,'p10':None,'p90':None,
                                   'positive_pct':None,'gt4bps_pct':None,'gt7_5bps_pct':None}
            else:
                out[tier][str(h)]={'n':len(vals),'mean_bps':float(np.mean(vals)),'median_bps':float(np.median(vals)),
                                   'p10':percentile(vals,10),'p90':percentile(vals,90),
                                   'positive_pct':100*sum(v>0 for v in vals)/len(vals),
                                   'gt4bps_pct':100*sum(v>4 for v in vals)/len(vals),
                                   'gt7_5bps_pct':100*sum(v>7.5 for v in vals)/len(vals)}
    st['summary']=out

def side_for(hist,cur,t2,t5,cfg):
    if cur['spread_bps']<=0 or cur['spread_bps']>MAX_SPREAD_BPS:return 0
    if len(hist)<cfg['persist']:return 0
    rows=list(hist)[-cfg['persist']:]
    long_book=all(x['imb10']>=cfg['imb'] and x['microedge_bps']>=cfg['micro'] for x in rows)
    short_book=all(x['imb10']<=-cfg['imb'] and x['microedge_bps']<=-cfg['micro'] for x in rows)
    if long_book and t2['flow']>=cfg['flow2'] and t5['flow']>=cfg['flow5']:return 1
    if short_book and t2['flow']<=-cfg['flow2'] and t5['flow']<=-cfg['flow5']:return -1
    return 0

async def main():
    st=load_state(); st['runs']=int(st.get('runs',0))+1
    tape=deque(); hist=deque(maxlen=30); pending=[]; latest=None
    streams=Counter(st.get('stream_counts',{})); rejects=Counter(st.get('rejections',{}))
    last_event={(tier,side):-1e9 for tier in TIERS for side in (-1,1)}
    start=time.time(); last_obs=0.0; ws_ok=False
    try:
        async with websockets.connect(WS,open_timeout=12,ping_interval=20,ping_timeout=20,max_size=2**22) as ws:
            ws_ok=True; st['ws_success_runs']=int(st.get('ws_success_runs',0))+1
            while time.time()-start<RUN_SECONDS:
                timeout=min(2.0,RUN_SECONDS-(time.time()-start))
                if timeout<=0:break
                try: obj=json.loads(await asyncio.wait_for(ws.recv(),timeout=timeout))
                except asyncio.TimeoutError:continue
                stream=str(obj.get('stream','')).lower(); d=obj.get('data',obj); streams[stream or '(raw)']+=1
                now=time.time(); now_ms=int(now*1000)
                if '@depth' in stream or str(d.get('e','')).lower()=='depthupdate':
                    m=book_metrics(d)
                    if m: latest=m; st['depth_events']=int(st.get('depth_events',0))+1
                    else: rejects['depth_parse']+=1
                elif '@aggtrade' in stream or str(d.get('e','')).lower()=='aggtrade':
                    try:
                        tr={'T':int(d.get('T',d.get('E'))),'p':float(d['p']),'q':float(d['q']),'m':bool(d['m'])}
                        tr['notional']=tr['p']*tr['q']; tape.append(tr); prune(tape,tr['T'])
                        st['aggtrade_events']=int(st.get('aggtrade_events',0))+1
                    except Exception: rejects['aggtrade_parse']+=1
                if latest is None or now-last_obs<1.0:continue
                last_obs=now; st['observations']=int(st.get('observations',0))+1
                t2=tape_window(tape,now_ms,2); t5=tape_window(tape,now_ms,5)
                hist.append({**latest,'wall_ts':now})

                # Resolve old events only with information available now.
                still=[]
                for ev in pending:
                    age=now-ev['wall_ts']; side=ev['side']; ret=side*(latest['mid']/ev['entry_mid']-1)*1e4
                    ev['mfe_bps']=max(ev.get('mfe_bps',ret),ret); ev['mae_bps']=min(ev.get('mae_bps',ret),ret)
                    for h in HORIZONS:
                        if age>=h and str(h) not in ev['resolved']: ev['resolved'][str(h)]=ret
                    if age>=max(HORIZONS):
                        st['events'].append({k:v for k,v in ev.items() if k!='wall_ts'})
                    else: still.append(ev)
                pending=still

                for tier,cfg in TIERS.items():
                    side=side_for(hist,latest,t2,t5,cfg)
                    if side and now-last_event[(tier,side)]>=COOLDOWN_SEC:
                        last_event[(tier,side)]=now
                        pending.append({'tier':tier,'side':side,'signal_ts':utcnow(),'wall_ts':now,
                                        'entry_mid':latest['mid'],'spread_bps':latest['spread_bps'],
                                        'imb10':latest['imb10'],'microedge_bps':latest['microedge_bps'],
                                        'flow2':t2['flow'],'flow5':t5['flow'],'resolved':{},
                                        'mfe_bps':0.0,'mae_bps':0.0})
        # Only completed horizons are persisted; unresolved events are intentionally dropped at process end.
    except Exception as e:
        st['last_error']=repr(e)
    finally:
        st['ws_access_ok']=ws_ok; st['stream_counts']=dict(streams); st['rejections']=dict(rejects)
        st['events']=st.get('events',[])[-5000:]; summarize(st); save_state(st); print(json.dumps(st,indent=2))

if __name__=='__main__': asyncio.run(main())
