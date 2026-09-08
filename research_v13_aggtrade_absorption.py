from __future__ import annotations

import io, json, zipfile
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v11_microburst as v11

STATE=Path('data/v13_aggtrade_absorption_state.json')
BASE='https://data.binance.vision/data/futures/um/monthly/aggTrades'
START=pd.Timestamp('2025-01-01',tz='UTC')
END=pd.Timestamp('2026-08-31 23:59:59',tz='UTC')
COSTS=(7.0,12.0,20.0)
Spec=v11.Spec

# flow_thr = 5m signed quote-flow threshold
# persist_thr = mean sign of 1m trade-flow inside the 5m bar
# accel_thr = max absolute 1m trade-flow (burst)
# body_thr = minimum rejection-wick fraction
SPECS=[
    Spec('G0_trade_absorb',0.10,0.40,0.55,0.28,0.85,1.35,8,120),
    Spec('G1_strict',0.14,0.50,0.65,0.32,0.80,1.50,8,110),
    Spec('G2_extreme',0.18,0.60,0.72,0.38,0.80,1.70,10,100),
    Spec('G3_wide',0.12,0.45,0.60,0.28,0.95,1.60,12,140),
    Spec('G4_fast',0.10,0.45,0.62,0.32,0.75,1.20,6,120),
    Spec('G5_defensive',0.16,0.55,0.70,0.40,0.90,1.80,10,90),
]

def months(a,b):
    cur=pd.Timestamp(a.year,a.month,1,tz='UTC'); last=pd.Timestamp(b.year,b.month,1,tz='UTC')
    while cur<=last:
        yield cur; cur += pd.offsets.MonthBegin(1)

def fetch_month(m):
    ym=m.strftime('%Y-%m')
    url=f'{BASE}/{v11.SYMBOL}/{v11.SYMBOL}-aggTrades-{ym}.zip'
    r=requests.get(url,timeout=120)
    if r.status_code==404: return pd.DataFrame()
    r.raise_for_status()
    parts=[]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name=z.namelist()[0]
        for raw in pd.read_csv(z.open(name),header=None,chunksize=400000,low_memory=False):
            if raw.shape[1]<7: continue
            raw=raw.iloc[:,:7].copy(); raw.columns=['id','price','qty','first','last','time','buyer_maker']
            for c in ['price','qty','time']: raw[c]=pd.to_numeric(raw[c],errors='coerce')
            raw=raw.dropna(subset=['price','qty','time'])
            if raw.empty: continue
            raw['ts']=pd.to_datetime(raw.time,unit='ms',utc=True)
            raw['quote']=raw.price*raw.qty
            bm=raw.buyer_maker.astype(str).str.lower().isin(['true','1'])
            raw['signed_quote']=np.where(bm,-raw.quote,raw.quote)
            raw['taker_buy_base']=np.where(bm,0.0,raw.qty)
            raw['minute']=raw.ts.dt.floor('1min')
            raw['n']=1
            g=raw.groupby('minute',sort=True)
            p=g.agg(open=('price','first'),high=('price','max'),low=('price','min'),close=('price','last'),volume=('qty','sum'),taker_base=('taker_buy_base','sum'),quote=('quote','sum'),signed_quote=('signed_quote','sum'),trades=('n','sum'),max_trade_quote=('quote','max'))
            parts.append(p)
    if not parts: return pd.DataFrame()
    x=pd.concat(parts).sort_index()
    x=x.groupby(level=0,sort=True).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'),taker_base=('taker_base','sum'),quote=('quote','sum'),signed_quote=('signed_quote','sum'),trades=('trades','sum'),max_trade_quote=('max_trade_quote','max'))
    return x

def load_data():
    parts=[]
    for m in months(START,END):
        x=fetch_month(m)
        if not x.empty: parts.append(x)
    if not parts: raise RuntimeError('no Binance futures aggTrades data')
    x=pd.concat(parts).sort_index(); x=x[~x.index.duplicated(keep='last')]
    return x[(x.index>=START)&(x.index<=END)]

def build_trade_5m(m1):
    x=m1.copy(); x['flow1']=x.signed_quote/x.quote.replace(0,np.nan)
    grp=x.resample('5min',label='left',closed='left')
    b=grp.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote':'sum','signed_quote':'sum','trades':'sum','max_trade_quote':'max'}).dropna()
    b['flow']=b.signed_quote/b.quote.replace(0,np.nan)
    b['persist']=grp.flow1.apply(lambda s: float(np.nanmean(np.sign(s))) if len(s) else np.nan).reindex(b.index)
    b['burst']=grp.flow1.apply(lambda s: float(np.nanmax(np.abs(s))) if len(s) else np.nan).reindex(b.index)
    b['accel']=grp.flow1.apply(lambda s: float(s.tail(2).mean()-s.head(2).mean()) if len(s)>=4 else np.nan).reindex(b.index)
    b['concentration']=b.max_trade_quote/b.quote.replace(0,np.nan)
    b['atr']=v11.atr(b)
    rng=(b.high-b.low).replace(0,np.nan)
    b['upper_wick']=(b.high-b[['open','close']].max(axis=1))/rng
    b['lower_wick']=(b[['open','close']].min(axis=1)-b.low)/rng
    b['close_loc']=(b.close-b.low)/rng
    typ=(b.high+b.low+b.close)/3; day=b.index.floor('D')
    b['vwap']=(typ*b.volume).groupby(day).cumsum()/b.volume.groupby(day).cumsum().replace(0,np.nan)
    b['ret_bps']=(b.close/b.open-1).abs()*10000
    b['hi12']=b.high.shift(1).rolling(12,min_periods=12).max(); b['lo12']=b.low.shift(1).rolling(12,min_periods=12).min()
    q=b.resample('15min',label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    q['ef']=q.close.ewm(span=20,adjust=False).mean(); q['es']=q.close.ewm(span=60,adjust=False).mean(); q['cci']=v11.cci(q,20); q['slope']=q.es-q.es.shift(4)
    reg=q[['close','ef','es','cci','slope']].reindex(b.index,method='ffill'); b[['qclose','qef','qes','qcci','qslope']]=reg.to_numpy()
    return b

def signal_frame(b,s):
    x=b.copy()
    buy=(x.flow>=s.flow_thr)&(x.persist>=s.persist_thr)&(x.burst>=s.accel_thr)
    sell=(x.flow<=-s.flow_thr)&(x.persist<=-s.persist_thr)&(x.burst>=s.accel_thr)
    # Trade-level absorption: strong taker aggression, failed local break, rejection wick, weak net response.
    fail_hi=(x.high>x.hi12)&(x.close<x.hi12)&(x.upper_wick>=s.body_thr)&(x.close_loc<=0.58)
    fail_lo=(x.low<x.lo12)&(x.close>x.lo12)&(x.lower_wick>=s.body_thr)&(x.close_loc>=0.42)
    weak_response=x.ret_bps<=18
    not_up=~((x.qclose>x.qef)&(x.qef>x.qes)&(x.qslope>0)&(x.qcci>abs(s.cci_gate)))
    not_dn=~((x.qclose<x.qef)&(x.qef<x.qes)&(x.qslope<0)&(x.qcci<-abs(s.cci_gate)))
    x['short_sig']=buy&fail_hi&weak_response&not_up
    x['long_sig']=sell&fail_lo&weak_response&not_dn
    return x

v11.signal_frame=signal_frame

def score(m):
    if m['trades']<60 or m['net_pct']<=0 or m['pf']<1.12 or m['max_dd_pct']>18: return -1e9
    return m['net_pct']+40*(m['pf']-1)+.1*m['win_pct']-.8*m['max_dd_pct']

def main():
    raw=load_data(); b=build_trade_5m(raw)
    train=b[b.index<=v11.SELECTION_END]; hold=b[b.index>=v11.HOLDOUT_START]
    cand={}; ranked=[]
    for s in SPECS:
        tr={str(c):v11.simulate(train,s,c,risk_frac=.002,lev_cap=1.5) for c in COSTS}; sc=score(tr['7.0']); cand[s.name]={'spec':asdict(s),'train':tr,'score':sc}; ranked.append((sc,s.name))
    ranked.sort(reverse=True); selected=ranked[0][1] if ranked and ranked[0][0]>-1e8 else None
    holdout=None; gate=False; daily=None; diag=None
    if selected:
        s=next(z for z in SPECS if z.name==selected)
        h7=v11.simulate(hold,s,7.0,risk_frac=.002,lev_cap=1.5,collect=True); h12=v11.simulate(hold,s,12.0,risk_frac=.002,lev_cap=1.5); h20=v11.simulate(hold,s,20.0,risk_frac=.002,lev_cap=1.5)
        holdout={'7.0':{k:v for k,v in h7.items() if k!='trade_rows'},'12.0':h12,'20.0':h20}
        gate=(h7['trades']>=30 and h7['net_pct']>0 and h7['pf']>=1.15 and h7['max_dd_pct']<=15 and h12['net_pct']>0 and h20['net_pct']>0)
        daily=v11.day100(h7.get('trade_rows',[])); diag={'long_only_7':v11.simulate(hold,s,7.0,side_filter=1),'short_only_7':v11.simulate(hold,s,7.0,side_filter=-1)}
    state={'version':'quantbot-v13-aggtrade-absorption','purpose':'TRADE_LEVEL_SCREEN_NO_LIVE','symbol':v11.SYMBOL,'source':'Binance Vision USD-M aggTrades -> 1m trade-flow -> 5m entry / 15m regime','selection_end':str(v11.SELECTION_END),'holdout_start':str(v11.HOLDOUT_START),'selected':selected,'candidates':cand,'holdout':holdout,'day100':daily,'side_diagnostic':diag,'holdout_gate_pass':bool(gate),'live_orders':False,'interpretation':'V13_TRADE_LEVEL_CANDIDATE' if gate else 'NO_V13_EDGE_YET'}
    STATE.parent.mkdir(parents=True,exist_ok=True); STATE.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))

if __name__=='__main__': main()
