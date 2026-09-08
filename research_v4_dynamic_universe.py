"""Dynamic point-in-time liquidity-universe audit for frozen T2.
Broad historically relevant Binance pool includes later-delisted and later-listed names.
At each weekly signal only assets with then-observed data are eligible; top 15 by
trailing 30d median USDT quote volume form the practical universe. No live orders.
"""
from __future__ import annotations
import json, math, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v3_independent as v3
import research_v3_audit as audit

OUT=Path('data/v4_dynamic_universe.json')
POOL=[
'BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','ADAUSDT','SOLUSDT','DOGEUSDT','TRXUSDT','LTCUSDT','LINKUSDT','BCHUSDT','ETCUSDT','XLMUSDT','DOTUSDT','AVAXUSDT','UNIUSDT','ATOMUSDT','NEARUSDT','FILUSDT','AAVEUSDT',
'EOSUSDT','XMRUSDT','THETAUSDT','XEMUSDT','VETUSDT','DASHUSDT','ZECUSDT','NEOUSDT','IOTAUSDT','QTUMUSDT','ALGOUSDT','MATICUSDT','FTMUSDT','RUNEUSDT','KAVAUSDT','COMPUSDT','SNXUSDT','MKRUSDT',
'ICPUSDT','SANDUSDT','MANAUSDT','AXSUSDT','FTTUSDT','LUNAUSDT','SHIBUSDT','CRVUSDT','GRTUSDT','EGLDUSDT','ENJUSDT','BATUSDT','ZILUSDT','WAVESUSDT'
]
START='2019-01-01'; BASE=.0013; C40=.0040; C75=.0075


def get_cv(sym):
    start=int(pd.Timestamp(START,tz='UTC').timestamp()*1000); end=int(datetime.now(timezone.utc).timestamp()*1000); rows=[]; sess=requests.Session()
    while start<end:
        batch=None
        for url in v3.ENDPOINTS:
            try:
                r=sess.get(url,params=dict(symbol=sym,interval='1d',startTime=start,endTime=end,limit=1000),timeout=20); r.raise_for_status(); x=r.json()
                if isinstance(x,list): batch=x; break
            except Exception: pass
        if not batch: break
        rows.extend(batch); nxt=int(batch[-1][0])+86400000
        if nxt<=start: break
        start=nxt
        if len(batch)<1000: break
        time.sleep(.02)
    if not rows: raise RuntimeError('no kline data')
    d=pd.DataFrame(rows); idx=pd.to_datetime(d[0].astype('int64'),unit='ms',utc=True).dt.floor('D')
    x=pd.DataFrame({'close':pd.to_numeric(d[4],errors='coerce').to_numpy(),'qv':pd.to_numeric(d[7],errors='coerce').to_numpy()},index=pd.DatetimeIndex(idx))
    return x.loc[~x.index.duplicated(keep='last')].sort_index()


def load():
    cs=[]; vs=[]; meta={}
    for s in POOL:
        try:
            d=get_cv(s); n=int(d.close.notna().sum())
            if n>=120:
                cs.append(d.close.rename(s)); vs.append(d.qv.rename(s)); meta[s]={'rows':n,'first':str(d.index.min()),'last':str(d.index.max())}
            else: meta[s]={'error':'insufficient rows'}
        except Exception as e: meta[s]={'error':repr(e)}
    px=pd.concat(cs,axis=1).sort_index(); qv=pd.concat(vs,axis=1).reindex(px.index)
    px=px.loc[px['BTCUSDT'].notna()]; qv=qv.reindex(px.index)
    return px,qv,meta


def positions(px,qv):
    m20,m60,m120,m180=[v3.momentum(px,n) for n in (20,60,120,180)]; raw=.25*m20+.35*m60+.25*m120+.15*m180
    vol=px.pct_change(fill_method=None).rolling(30,min_periods=30).std()*math.sqrt(365.25)
    liq=qv.rolling(30,min_periods=20).median()
    btc=px['BTCUSDT']; gate=(btc>btc.rolling(200).mean())&(btc/btc.shift(60)-1>0)&(btc/btc.shift(120)-1>0)
    p=pd.DataFrame(0.0,index=px.index,columns=px.columns); members={}
    for dt in px.index:
        ok=raw.loc[dt].notna()&vol.loc[dt].notna()&liq.loc[dt].notna()&px.loc[dt].notna()
        eligible=list(liq.loc[dt,ok].sort_values(ascending=False).index[:15])
        members[dt]=eligible
        if not bool(gate.loc[dt]) or not eligible: continue
        sc=raw.loc[dt,eligible]; chosen=list(sc[sc>0].sort_values(ascending=False).index[:3])
        if not chosen: continue
        inv=1/vol.loc[dt,chosen].clip(lower=.10); w=inv/inv.sum(); avg=float((w*vol.loc[dt,chosen]).sum()); scalar=min(1.0,.12/max(avg,1e-9)); p.loc[dt,chosen]=w*scalar
    rb=v3.rebalance_mask(px.index,'W'); p=p.where(rb,np.nan).ffill().fillna(0.0)
    return p,members


def diag_all(px,p):
    pre={str(c):audit.diag(px,p,'2021-01-01','2026-04-24',c) for c in (BASE,C40,C75)}
    post={str(c):audit.diag(px,p,'2026-04-25',None,c) for c in (BASE,C40,C75)}
    delay=audit.diag(px,p,'2021-01-01','2026-04-24',BASE,extra_delay=1)
    rolls=audit.rolling_windows(px.loc[px.index<=v3.LOCK_DATE],p.loc[p.index<=v3.LOCK_DATE],window=180,step=30,cost=BASE)
    folds=[]
    for y in range(2021,2026):
        d=audit.diag(px,p,f'{y}-01-01',f'{y}-12-31',BASE); d['year']=y; folds.append(d)
    meaningful=[x for x in folds if x['active_day_pct']>=10]; posfold=sum(x['net_pct']>0 for x in meaningful)
    gate=(pre[str(BASE)]['net_pct']>0 and pre[str(BASE)]['sharpe']>=.5 and pre[str(BASE)]['pf']>=1.08 and pre[str(BASE)]['max_dd_pct']<=20 and pre[str(C40)]['net_pct']>0 and pre[str(C75)]['net_pct']>0 and delay['net_pct']>0 and len(meaningful)>=3 and posfold>=max(3,math.ceil(.75*len(meaningful))) and rolls['positive_fraction_meaningful']>=.60)
    return pre,post,delay,{k:v for k,v in rolls.items() if k!='windows'},folds,gate


def main():
    px,qv,meta=load(); cut=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC'); px=px.loc[px.index<cut]; qv=qv.reindex(px.index)
    p,members=positions(px,qv); pre,post,delay,rolls,folds,gate=diag_all(px,p)
    wk=v3.rebalance_mask(px.index,'W'); counter=Counter(x for dt in px.index[wk.to_numpy()] if dt<=v3.LOCK_DATE for x in members.get(dt,[]))
    state={'version':'quantbot-v4-dynamic-liquidity-universe','updated':datetime.now(timezone.utc).isoformat(),
           'rule':'Frozen T2 signal; each week practical universe is top15 by trailing 30d median USDT quote volume among assets with observed data; top3 positive momentum selected.',
           'pool_size_requested':len(POOL),'assets_loaded':list(px.columns),'metadata':meta,
           'prelock':pre,'prelock_delay2_total':delay,'folds':folds,'rolling180':rolls,'reused_postlock_diagnostic':post,
           'top_liquidity_membership_frequency_prelock':dict(counter.most_common()),
           'dynamic_universe_gate':bool(gate),'interpretation':'T2_SURVIVES_DYNAMIC_POINT_IN_TIME_LIQUIDITY_UNIVERSE' if gate else 'T2_DYNAMIC_UNIVERSE_WEAKNESS_DETECTED',
           'live_money_authorized':False,
           'limitations':['Broad pool is intentionally much wider and includes failed/delisted names but is not an exhaustive list of every Binance asset ever listed.','Liquidity uses reported USDT quote volume and does not model order-book depth.','Postlock remains reused diagnostic evidence.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps({'loaded':len(px.columns),'pre':pre,'rolls':rolls,'gate':gate},indent=2))
if __name__=='__main__': main()
