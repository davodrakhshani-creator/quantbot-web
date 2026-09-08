"""Cross-venue replication of frozen QuantBot v4 T2 on Bybit public spot daily data.

T2 was selected on Binance data. This lane never retunes T2; it asks whether the same
rule transports to another venue. Cross-venue replication is independent-source
support, not a substitute for future forward evidence. No live orders.
"""
from __future__ import annotations
import json, math, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v3_independent as v3
import research_v3_audit as audit

OUT=Path('data/v4_bybit_replication.json')
API='https://api.bybit.com/v5/market/kline'
SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','ADAUSDT','SOLUSDT','DOGEUSDT','TRXUSDT','LTCUSDT','LINKUSDT','BCHUSDT','DOTUSDT','AVAXUSDT','UNIUSDT','NEARUSDT','AAVEUSDT']
BASE=.0013; C40=.0040; C75=.0075


def get_daily(sym):
    sess=requests.Session(); end=int(datetime.now(timezone.utc).timestamp()*1000); rows=[]; seen=set()
    for _ in range(8):
        r=sess.get(API,params={'category':'spot','symbol':sym,'interval':'D','limit':1000,'end':end},timeout=20); r.raise_for_status(); js=r.json()
        if js.get('retCode')!=0: raise RuntimeError(str(js))
        batch=js.get('result',{}).get('list',[])
        if not batch: break
        new=0
        for x in batch:
            ts=int(x[0])
            if ts not in seen: rows.append(x); seen.add(ts); new+=1
        oldest=min(int(x[0]) for x in batch); end=oldest-1
        if new==0 or len(batch)<1000: break
        time.sleep(.05)
    if not rows: raise RuntimeError('no Bybit spot rows')
    rows.sort(key=lambda x:int(x[0]))
    idx=pd.to_datetime([int(x[0]) for x in rows],unit='ms',utc=True).floor('D')
    close=pd.Series([float(x[4]) for x in rows],index=idx,name=sym)
    return close[~close.index.duplicated(keep='last')]


def load():
    ss=[]; meta={}
    for s in SYMS:
        try:
            x=get_daily(s); meta[s]={'rows':int(len(x)),'first':str(x.index.min()),'last':str(x.index.max())}
            if len(x)>=300: ss.append(x)
        except Exception as e: meta[s]={'error':repr(e)}
    if len(ss)<8: raise RuntimeError(f'too few Bybit assets: {meta}')
    px=pd.concat(ss,axis=1).sort_index(); px=px.loc[px['BTCUSDT'].notna()]
    cut=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC'); return px.loc[px.index<cut],meta


def t2(px):
    m20,m60,m120,m180=[v3.momentum(px,n) for n in (20,60,120,180)]; raw=.25*m20+.35*m60+.25*m120+.15*m180
    btc=px['BTCUSDT']; gate=(btc>btc.rolling(200).mean())&(btc/btc.shift(60)-1>0)&(btc/btc.shift(120)-1>0)
    return v3.normalize_selected(raw,px,k=3,positive=True,freq='W',market_gate=gate)


def main():
    px,meta=load(); pos=t2(px)
    start=max(pd.Timestamp('2022-01-01',tz='UTC'),px.index.min()+pd.Timedelta(days=240))
    start_s=str(start.date())
    full={str(c):audit.diag(px,pos,start_s,None,c) for c in (BASE,C40,C75)}
    folds=[]
    for y in range(max(2022,start.year),2027):
        d=audit.diag(px,pos,f'{y}-01-01',f'{y}-12-31',BASE); d['year']=y; folds.append(d)
    rolls=audit.rolling_windows(px,pos,start=start_s,window=180,step=30,cost=BASE)
    meaningful=[x for x in folds if x.get('active_day_pct',0)>=10 and x.get('n_days',0)>=120]
    posfold=sum(x['net_pct']>0 for x in meaningful)
    gate=(full[str(BASE)]['net_pct']>0 and full[str(BASE)]['sharpe']>=.5 and full[str(BASE)]['pf']>=1.08 and full[str(BASE)]['max_dd_pct']<=20 and
          full[str(C40)]['net_pct']>0 and full[str(C75)]['net_pct']>0 and len(meaningful)>=3 and posfold>=math.ceil(.75*len(meaningful)) and
          rolls['meaningful_count']>=10 and rolls['positive_fraction_meaningful']>=.60)
    state={'version':'quantbot-v4-bybit-crossvenue-replication','updated':datetime.now(timezone.utc).isoformat(),
           'source':'Bybit public spot daily klines','rule':'Frozen T2_upup; no parameter or asset-selection retuning to Bybit performance.',
           'requested_assets':SYMS,'loaded_assets':list(px.columns),'metadata':meta,'evaluation_start':start_s,
           'metrics':full,'folds':folds,'rolling180':{k:v for k,v in rolls.items() if k!='windows'},
           'replication_gate':bool(gate),'interpretation':'T2_CROSSVENUE_REPLICATION_SUPPORTED' if gate else 'T2_CROSSVENUE_REPLICATION_FAILED',
           'live_money_authorized':False,
           'limitations':['Bybit listing histories differ from Binance and current-symbol coverage can still have survivorship effects.','Underlying crypto prices are correlated across venues, so this is source/implementation replication rather than an independent economic market.','Daily bars omit intraday market impact.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps({'loaded':len(px.columns),'start':start_s,'metrics':full,'folds_positive':f'{posfold}/{len(meaningful)}','rolling':state['rolling180'],'gate':gate},indent=2))
if __name__=='__main__': main()
