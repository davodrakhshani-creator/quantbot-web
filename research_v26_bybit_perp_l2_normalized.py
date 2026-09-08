from __future__ import annotations

import asyncio, json, time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import websockets

STATE=Path('data/v26_bybit_perp_l2_normalized_state.json')
WS='wss://stream.bybit.com/v5/public/linear'
SYMBOL='BTCUSDT'
RUN_SECONDS=210
HORIZONS=(1,3,5,10,30,60)
NEW_EVENT_CUTOFF_SEC=RUN_SECONDS-max(HORIZONS)-5
NEW_ENTRY_CUTOFF_SEC=RUN_SECONDS-90-5-10
COOLDOWN_SEC=15
MAX_SPREAD_BPS=1.5
MAKER_FEE_BPS=2.0
TAKER_FEE_BPS=5.5
SLIPPAGE_BPS=0.5
QUEUE_MULT=1.5
PAPER_NOTIONAL_USD=100.0
FILL_TIMEOUT_SEC=5
MAX_HOLD_SEC=90
HARD_STOP_BPS=8.0
TARGET_BPS=14.0
IMMEDIATE_CHECK_SEC=10
IMMEDIATE_MIN_BPS=0.5
# micro_ratio = microprice edge / half-spread, bounded approximately [-1, 1].
# This fixes v25's absolute microedge thresholds, which were often unattainable on BTC's tight spread.
TIERS={
 'base':{'imb':0.10,'micro_ratio':0.15,'flow2':0.08,'flow5':0.04,'persist':2},
 'strong':{'imb':0.20,'micro_ratio':0.30,'flow2':0.15,'flow5':0.08,'persist':3},
 'elite':{'imb':0.32,'micro_ratio':0.45,'flow2':0.24,'flow5':0.12,'persist':3},
}


def utcnow(): return datetime.now(timezone.utc).isoformat()

def load_state():
    if STATE.exists():
        try:return json.loads(STATE.read_text())
        except Exception:pass
    return {'version':'quantbot-v26-bybit-perp-l2-normalized','purpose':'FORWARD_ONLY_TRUE_PERPETUAL_L2_TAPE_ALPHA_AND_PAPER_NO_LIVE',
            'venue':'BYBIT_USDT_PERPETUAL','symbol':SYMBOL,'live_orders':False,'runs':0,'ws_success_runs':0,
            'observations':0,'orderbook_messages':0,'trade_messages':0,'trade_events':0,'raw_events':[],
            'maker_candidates':0,'maker_fills':0,'paper_trades':[],'rejections':{},'gate_diagnostics':{},'created_at':utcnow()}

def save_state(st):
    st['updated_at']=utcnow();STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(st,indent=2))

class Book:
    def __init__(self):self.bids={};self.asks={};self.ready=False
    def apply(self,obj):
        data=obj.get('data',{});typ=obj.get('type','')
        if typ=='snapshot':self.bids={};self.asks={}
        for p,q in data.get('b',[]):
            p=float(p);q=float(q)
            if q==0:self.bids.pop(p,None)
            else:self.bids[p]=q
        for p,q in data.get('a',[]):
            p=float(p);q=float(q)
            if q==0:self.asks.pop(p,None)
            else:self.asks[p]=q
        self.ready=bool(self.bids and self.asks)
    def metrics(self):
        if not self.ready:return None
        bids=sorted(self.bids.items(),reverse=True)[:10];asks=sorted(self.asks.items())[:10]
        if not bids or not asks:return None
        bb,bq=bids[0];ba,aq=asks[0];mid=(bb+ba)/2;b10=sum(q for _,q in bids);a10=sum(q for _,q in asks)
        spread_bps=(ba-bb)/mid*1e4
        micro=(ba*bq+bb*aq)/(bq+aq) if bq+aq else mid
        microedge_bps=(micro/mid-1)*1e4
        half_spread=max(spread_bps/2,1e-12)
        micro_ratio=max(-1.0,min(1.0,microedge_bps/half_spread))
        return {'bb':bb,'ba':ba,'bq':bq,'aq':aq,'mid':mid,'spread_bps':spread_bps,
                'imb10':(b10-a10)/(b10+a10) if b10+a10 else 0.0,'microedge_bps':microedge_bps,'micro_ratio':micro_ratio,
                'bid_usd':sum(p*q for p,q in bids),'ask_usd':sum(p*q for p,q in asks)}

def prune(tape,now_ms,sec=20):
    cut=now_ms-sec*1000
    while tape and tape[0]['T']<cut:tape.popleft()

def tape_window(tape,now_ms,sec):
    cut=now_ms-sec*1000;rows=[x for x in tape if x['T']>=cut]
    buy=sum(x['notional'] for x in rows if x['side']=='Buy');sell=sum(x['notional'] for x in rows if x['side']=='Sell');tot=buy+sell
    return {'flow':(buy-sell)/tot if tot else 0.0,'count':len(rows),'notional':tot,'speed':len(rows)/sec}

def side_for(hist,cur,t2,t5,cfg):
    if cur['spread_bps']<=0 or cur['spread_bps']>MAX_SPREAD_BPS:return 0,'spread'
    if len(hist)<cfg['persist']:return 0,'warmup'
    rows=list(hist)[-cfg['persist']:]
    long_imb=all(x['imb10']>=cfg['imb'] for x in rows);short_imb=all(x['imb10']<=-cfg['imb'] for x in rows)
    long_micro=all(x['micro_ratio']>=cfg['micro_ratio'] for x in rows);short_micro=all(x['micro_ratio']<=-cfg['micro_ratio'] for x in rows)
    if long_imb and long_micro:
        if t2['flow']<cfg['flow2']:return 0,'flow2_long'
        if t5['flow']<cfg['flow5']:return 0,'flow5_long'
        return 1,'pass_long'
    if short_imb and short_micro:
        if t2['flow']>-cfg['flow2']:return 0,'flow2_short'
        if t5['flow']>-cfg['flow5']:return 0,'flow5_short'
        return -1,'pass_short'
    if not (long_imb or short_imb):return 0,'imbalance'
    return 0,'micro_ratio'

def pct(a,p):return float(np.percentile(a,p)) if a else None

def summarize(st):
    raw={};events=st.get('raw_events',[])
    for tier in TIERS:
        raw[tier]={}
        for h in HORIZONS:
            vals=[float(x['resolved'][str(h)]) for x in events if x['tier']==tier and str(h) in x.get('resolved',{})]
            raw[tier][str(h)]=({'n':0,'mean_bps':None,'median_bps':None,'p10':None,'p90':None,'positive_pct':None,'gt4bps_pct':None,'gt8bps_pct':None}
                if not vals else {'n':len(vals),'mean_bps':float(np.mean(vals)),'median_bps':float(np.median(vals)),'p10':pct(vals,10),'p90':pct(vals,90),
                                  'positive_pct':100*sum(v>0 for v in vals)/len(vals),'gt4bps_pct':100*sum(v>4 for v in vals)/len(vals),'gt8bps_pct':100*sum(v>8 for v in vals)/len(vals)})
    trs=st.get('paper_trades',[]);fill=100*st['maker_fills']/st['maker_candidates'] if st.get('maker_candidates') else None
    if trs:
        nets=[float(x['net_bps']) for x in trs];w=[x for x in nets if x>0];l=[x for x in nets if x<0]
        paper={'closed':len(trs),'avg_net_bps':sum(nets)/len(nets),'net_usd_100':sum(x['pnl_usd_100'] for x in trs),
               'win_pct':100*len(w)/len(nets),'pf':sum(w)/abs(sum(l)) if l else (999.0 if w else None),'maker_fill_rate_pct':fill}
    else:paper={'closed':0,'avg_net_bps':None,'net_usd_100':0.0,'win_pct':None,'pf':None,'maker_fill_rate_pct':fill}
    st['summary']={'raw_alpha':raw,'paper':paper,'fees_bps':{'maker':MAKER_FEE_BPS,'taker':TAKER_FEE_BPS,'slippage':SLIPPAGE_BPS}}

async def main():
    st=load_state();st['runs']=int(st.get('runs',0))+1;book=Book();tape=deque();hist=deque(maxlen=30);pending=[];candidate=None;op=None
    rejects=Counter(st.get('rejections',{}));gates=Counter(st.get('gate_diagnostics',{}));last_event={(tier,s):-1e9 for tier in TIERS for s in(-1,1)};start=time.time();last_obs=0.0;ws_ok=False;sub_ok=False
    try:
        async with websockets.connect(WS,open_timeout=12,ping_interval=20,ping_timeout=20,max_size=2**23) as ws:
            ws_ok=True;await ws.send(json.dumps({'op':'subscribe','args':[f'orderbook.50.{SYMBOL}',f'publicTrade.{SYMBOL}']}))
            while time.time()-start<RUN_SECONDS:
                timeout=min(2.0,RUN_SECONDS-(time.time()-start))
                if timeout<=0:break
                try:obj=json.loads(await asyncio.wait_for(ws.recv(),timeout=timeout))
                except asyncio.TimeoutError:continue
                now=time.time();now_ms=int(now*1000)
                if obj.get('op')=='subscribe' and obj.get('success') is True:sub_ok=True;continue
                topic=str(obj.get('topic',''))
                if topic.startswith('orderbook.50.'):
                    book.apply(obj);st['orderbook_messages']=int(st.get('orderbook_messages',0))+1
                elif topic==f'publicTrade.{SYMBOL}':
                    st['trade_messages']=int(st.get('trade_messages',0))+1
                    for tr in obj.get('data',[]):
                        try:
                            x={'T':int(tr['T']),'p':float(tr['p']),'q':float(tr['v']),'side':str(tr['S'])};x['notional']=x['p']*x['q'];tape.append(x);st['trade_events']=int(st.get('trade_events',0))+1
                            if candidate and x['T']>=candidate['created_ms']:
                                hit=(candidate['side']==1 and x['side']=='Sell' and x['p']<=candidate['entry_px']) or (candidate['side']==-1 and x['side']=='Buy' and x['p']>=candidate['entry_px'])
                                if hit:
                                    candidate['through_qty']+=x['q']
                                    if candidate['through_qty']>=candidate['queue_ahead_qty']:
                                        op={'side':candidate['side'],'entry_px':candidate['entry_px'],'fill_ts':now,'signal_ts':candidate['signal_ts'],'tier':candidate['tier']};st['maker_fills']=int(st.get('maker_fills',0))+1;candidate=None
                        except Exception:rejects['trade_parse']+=1
                    if tape:prune(tape,tape[-1]['T'])
                cur=book.metrics()
                if cur is None or now-last_obs<1.0:continue
                last_obs=now;st['observations']=int(st.get('observations',0))+1;t2=tape_window(tape,now_ms,2);t5=tape_window(tape,now_ms,5);hist.append({**cur,'wall_ts':now})
                still=[]
                for ev in pending:
                    age=now-ev['wall_ts'];ret=ev['side']*(cur['mid']/ev['entry_mid']-1)*1e4;ev['mfe_bps']=max(ev['mfe_bps'],ret);ev['mae_bps']=min(ev['mae_bps'],ret)
                    for h in HORIZONS:
                        if age>=h and str(h) not in ev['resolved']:ev['resolved'][str(h)]=ret
                    if age>=max(HORIZONS):st['raw_events'].append({k:v for k,v in ev.items() if k!='wall_ts'})
                    else:still.append(ev)
                pending=still;elapsed=now-start
                decisions={tier:side_for(hist,cur,t2,t5,cfg) for tier,cfg in TIERS.items()}
                for tier,(side,reason) in decisions.items():gates[f'{tier}:{reason}']+=1
                if elapsed<=NEW_EVENT_CUTOFF_SEC and sub_ok:
                    for tier,(side,_) in decisions.items():
                        if side and now-last_event[(tier,side)]>=COOLDOWN_SEC:
                            last_event[(tier,side)]=now;pending.append({'tier':tier,'side':side,'signal_ts':utcnow(),'wall_ts':now,'entry_mid':cur['mid'],'spread_bps':cur['spread_bps'],
                                'imb10':cur['imb10'],'microedge_bps':cur['microedge_bps'],'micro_ratio':cur['micro_ratio'],'flow2':t2['flow'],'flow5':t5['flow'],'resolved':{},'mfe_bps':0.0,'mae_bps':0.0})
                eside=decisions['elite'][0]
                if eside and elapsed<=NEW_ENTRY_CUTOFF_SEC and candidate is None and op is None and sub_ok:
                    entry=cur['bb'] if eside==1 else cur['ba'];candidate={'tier':'elite','side':eside,'entry_px':entry,'queue_ahead_qty':(cur['bq'] if eside==1 else cur['aq'])*QUEUE_MULT,
                        'through_qty':0.0,'created_ts':now,'created_ms':now_ms,'signal_ts':utcnow()};st['maker_candidates']=int(st.get('maker_candidates',0))+1
                if candidate and now-candidate['created_ts']>FILL_TIMEOUT_SEC:rejects['maker_miss']+=1;candidate=None
                if op:
                    side=op['side'];exit_px=cur['bb'] if side==1 else cur['ba'];gross=side*(exit_px/op['entry_px']-1)*1e4;age=now-op['fill_ts'];reason=None
                    if gross>=TARGET_BPS:reason='target'
                    elif gross<=-HARD_STOP_BPS:reason='hard_stop'
                    elif age>=IMMEDIATE_CHECK_SEC and gross<IMMEDIATE_MIN_BPS:reason='immediate_invalidation'
                    elif age>=MAX_HOLD_SEC:reason='time'
                    if reason:
                        net=gross-MAKER_FEE_BPS-TAKER_FEE_BPS-SLIPPAGE_BPS;st['paper_trades'].append({**op,'exit_ts':utcnow(),'exit_px':exit_px,'gross_bps':gross,'net_bps':net,
                            'pnl_usd_100':PAPER_NOTIONAL_USD*net/1e4,'reason':reason});st['paper_trades']=st['paper_trades'][-5000:];op=None
    except Exception as e:st['last_error']=repr(e)
    finally:
        st['ws_access_ok']=bool(ws_ok and sub_ok and st.get('orderbook_messages',0)>0 and st.get('trade_events',0)>0);st['subscription_ok']=sub_ok;st['rejections']=dict(rejects);st['gate_diagnostics']=dict(gates)
        if st['ws_access_ok']:st['ws_success_runs']=int(st.get('ws_success_runs',0))+1
        st['raw_events']=st.get('raw_events',[])[-10000:];summarize(st);save_state(st);print(json.dumps(st,indent=2))

if __name__=='__main__':asyncio.run(main())
