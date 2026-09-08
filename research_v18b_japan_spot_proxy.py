from __future__ import annotations

import json, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import requests

SYMBOL='BTCUSDT'
BASE='https://data-api.binance.vision'
STATE=Path('data/v18b_japan_spot_proxy_state.json')
RUN_SEC=120
POLL=1.0
NOTIONAL=100.0
# Intended futures execution cost model; this run is signal discovery only.
MAKER_FEE_BPS=2.0
TAKER_FEE_BPS=5.0
SLIP_BPS=0.5
MAX_SPREAD_BPS=1.5
MIN_DEPTH_USD=100000.0
QUEUE_MULT=1.0


def nowiso(): return datetime.now(timezone.utc).isoformat()

def get(path,params):
    r=requests.get(BASE+path,params=params,timeout=10); r.raise_for_status(); return r.json()

def depth(): return get('/api/v3/depth',{'symbol':SYMBOL,'limit':20})
def trades(): return get('/api/v3/aggTrades',{'symbol':SYMBOL,'limit':1000})
def klines(interval,limit=80): return get('/api/v3/klines',{'symbol':SYMBOL,'interval':interval,'limit':limit})

def ema(xs,n):
    a=2/(n+1); out=float(xs[0])
    for x in xs[1:]: out=a*float(x)+(1-a)*out
    return out

def context():
    k5=klines('5m',80)[:-1]; k15=klines('15m',80)[:-1]
    c15=[float(x[4]) for x in k15]; h5=[float(x[2]) for x in k5]; l5=[float(x[3]) for x in k5]
    e20=ema(c15[-50:],20); e50=ema(c15[-70:],50); last=c15[-1]
    trend=1 if last>e20>e50 else (-1 if last<e20<e50 else 0)
    return {'trend15':trend,'hi':max(h5[-8:]),'lo':min(l5[-8:])}

def bm(d):
    bids=[(float(p),float(q)) for p,q in d['bids'][:10]]; asks=[(float(p),float(q)) for p,q in d['asks'][:10]]
    bb,bq=bids[0]; ba,aq=asks[0]; mid=(bb+ba)/2
    bsum=sum(q for _,q in bids); asum=sum(q for _,q in asks)
    imb=(bsum-asum)/(bsum+asum) if bsum+asum else 0.0
    micro=(ba*bq+bb*aq)/(bq+aq) if bq+aq else mid
    return {'bb':bb,'ba':ba,'bq':bq,'aq':aq,'mid':mid,'spread':(ba-bb)/mid*1e4,'imb':imb,
            'micro':(micro-mid)/mid*1e4,'bid_usd':sum(p*q for p,q in bids),'ask_usd':sum(p*q for p,q in asks)}

def tw(ts,sec):
    if not ts: return {'flow':0.0,'count':0,'speed':0.0}
    end=max(int(t['T']) for t in ts); rows=[t for t in ts if int(t['T'])>=end-sec*1000]
    buy=sell=0.0
    for t in rows:
        v=float(t['p'])*float(t['q'])
        if bool(t['m']): sell+=v
        else: buy+=v
    tot=buy+sell
    return {'flow':(buy-sell)/tot if tot else 0.0,'count':len(rows),'speed':len(rows)/max(sec,1)}

def hist_px(hist,ago):
    cutoff=time.time()-ago
    for r in reversed(hist):
        if r['ts']<=cutoff: return r['mid']
    return None

def rbps(a,b): return (a/b-1)*1e4 if b else 0.0

def testa(m,hist,side):
    if m['spread']<=0 or m['spread']>MAX_SPREAD_BPS: return False
    if min(m['bid_usd'],m['ask_usd'])<MIN_DEPTH_USD or len(hist)<3: return False
    rows=list(hist)[-3:]
    return all((r['imb']>0.04 and r['micro']>0) if side==1 else (r['imb']<-0.04 and r['micro']<0) for r in rows)

def signals(m,t2,t5,hist,ctx):
    p3=hist_px(hist,3); p10=hist_px(hist,10)
    jun=hansan=0
    if p3 and p10:
        r3=rbps(m['mid'],p3); r10=rbps(m['mid'],p10)
        if t5['flow']<-0.22 and r10<-1.5 and r3>0.25 and m['imb']>0.05 and m['micro']>0.03 and ctx['trend15']>=0 and testa(m,hist,1): jun=1
        elif t5['flow']>0.22 and r10>1.5 and r3<-0.25 and m['imb']<-0.05 and m['micro']<-0.03 and ctx['trend15']<=0 and testa(m,hist,-1): jun=-1
        accel=t2['speed']/max(t5['speed'],1e-9)
        if ctx['trend15']>=0 and m['mid']>ctx['hi'] and t2['flow']>0.15 and t5['flow']>0.10 and r3>0.30 and accel>1.10 and m['imb']>0.08 and m['micro']>0.04 and testa(m,hist,1): hansan=1
        elif ctx['trend15']<=0 and m['mid']<ctx['lo'] and t2['flow']<-0.15 and t5['flow']<-0.10 and r3<-0.30 and accel>1.10 and m['imb']<-0.08 and m['micro']<-0.04 and testa(m,hist,-1): hansan=-1
    return jun,hansan

def cum_fill(side,px,ts,since):
    q=0.0
    for t in ts:
        if int(t['T'])<since: continue
        p=float(t['p']); tq=float(t['q']); maker=bool(t['m'])
        if side==1 and maker and p<=px: q+=tq
        elif side==-1 and (not maker) and p>=px: q+=tq
    return q

def load():
    if STATE.exists():
        try:return json.loads(STATE.read_text())
        except:pass
    return {'version':'v18B-japan-hybrid-spot-L2-proxy','purpose':'EDGE_DISCOVERY_PROXY_NOT_FUTURES_AUTHORIZATION','venue':'BINANCE_SPOT_MARKET_DATA_ONLY','live_orders':False,'runs':0,'observations':0,'signals':{'jun':0,'hansan':0},'fills':0,'paper_trades':[],'created_at':nowiso()}

def summary(st):
    xs=st['paper_trades']; nets=[x['net_bps'] for x in xs]; wins=[x for x in nets if x>0]; losses=[x for x in nets if x<0]
    st['summary']={'closed':len(xs),'net_usd_100':sum(x['pnl_usd_100'] for x in xs),'avg_net_bps':sum(nets)/len(nets) if nets else None,
                   'win_pct':100*len(wins)/len(nets) if nets else None,'pf':sum(wins)/abs(sum(losses)) if losses else (999.0 if wins else None),
                   'observations':st['observations'],'signals':st['signals'],'fills':st['fills'],'proxy_only':True}

def main():
    st=load(); st['runs']+=1; ctx=context(); hist=deque(maxlen=180); candidate=None; op=None; start=time.time()
    while time.time()-start<RUN_SEC:
        try:
            m=bm(depth()); ts=trades(); t2=tw(ts,2); t5=tw(ts,5); hist.append({'ts':time.time(),**m}); st['observations']+=1
            if op:
                age=time.time()-op['wall']; fav=op['side']*(m['mid']/op['entry']-1)*1e4
                target=9.0 if op['engine']=='jun' else 12.0; maxhold=28 if op['engine']=='jun' else 24
                reason=None
                if fav>=target: reason='target'
                elif fav<=-7.0: reason='hard_stop'
                elif age>=8 and fav<0.7: reason='immediate_invalidation'
                elif age>=maxhold: reason='time'
                if reason:
                    exitpx=m['bb'] if op['side']==1 else m['ba']; gross=op['side']*(exitpx/op['entry']-1)*1e4; net=gross-MAKER_FEE_BPS-TAKER_FEE_BPS-SLIP_BPS
                    st['paper_trades'].append({k:v for k,v in op.items() if k!='wall'}|{'exit_ts':nowiso(),'exit_px':exitpx,'gross_bps':gross,'net_bps':net,'pnl_usd_100':NOTIONAL*net/1e4,'reason':reason}); op=None
            if not op:
                if candidate:
                    if cum_fill(candidate['side'],candidate['px'],ts,candidate['since'])>=candidate['queue']*QUEUE_MULT:
                        op={'engine':candidate['engine'],'side':candidate['side'],'entry':candidate['px'],'entry_ts':nowiso(),'wall':time.time()}; st['fills']+=1; candidate=None
                    elif time.time()-candidate['wall']>8: candidate=None
                else:
                    j,h=signals(m,t2,t5,hist,ctx); engine='jun' if j else ('hansan' if h else None); side=j or h
                    if engine:
                        st['signals'][engine]+=1; candidate={'engine':engine,'side':side,'px':m['bb'] if side==1 else m['ba'],'queue':m['bq'] if side==1 else m['aq'],'since':int(time.time()*1000),'wall':time.time()}
            time.sleep(POLL)
        except Exception as e:
            st['last_error']=repr(e); time.sleep(1)
    summary(st); STATE.parent.mkdir(exist_ok=True); st['updated_at']=nowiso(); STATE.write_text(json.dumps(st,indent=2)); print(json.dumps(st,indent=2))

if __name__=='__main__': main()
