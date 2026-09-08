"""Independent-venue validation for the frozen QuantBot v4 T2_upup rule.

No model selection is performed here. Prices come from Bybit spot rather than Binance.
The exact T2 rule and the pre-registered v4 robustness gate are reused.
"""
from __future__ import annotations
import json, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests
import research_v3_independent as v3
import research_v3_audit as audit
import research_v4_tournament as v4

OUT=Path('data/v5_bybit_validation.json')
BASE=.0013; C40=.0040; C75=.0075
START=pd.Timestamp('2020-01-01',tz='UTC')
LOCK=pd.Timestamp('2026-04-24',tz='UTC')
SYMS=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LINKUSDT','LTCUSDT','BCHUSDT','AVAXUSDT','DOTUSDT','TRXUSDT']
URL='https://api.bybit.com/v5/market/kline'


def fetch_one(sym:str)->pd.Series:
    now=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC')
    end=int((now-pd.Timedelta(milliseconds=1)).timestamp()*1000)
    start=int(START.timestamp()*1000)
    rows=[]; sess=requests.Session()
    while end>=start:
        r=sess.get(URL,params={'category':'spot','symbol':sym,'interval':'D','end':end,'limit':1000},timeout=25)
        r.raise_for_status(); js=r.json()
        if js.get('retCode')!=0: raise RuntimeError(str(js)[:500])
        batch=js.get('result',{}).get('list',[])
        if not batch: break
        rows.extend(batch)
        oldest=min(int(x[0]) for x in batch)
        if oldest<=start or len(batch)<1000: break
        end=oldest-1; time.sleep(.08)
    if not rows: raise RuntimeError('empty')
    d=pd.DataFrame(rows)
    idx=pd.to_datetime(d[0].astype('int64'),unit='ms',utc=True).dt.floor('D')
    s=pd.Series(pd.to_numeric(d[4],errors='coerce').values,index=idx,name=sym)
    s=s[~s.index.duplicated(keep='last')].sort_index()
    return s.loc[(s.index>=START)&(s.index<now)]


def load():
    ss=[]; errors={}; meta={}
    for sym in SYMS:
        try:
            s=fetch_one(sym); meta[sym]={'rows':int(s.notna().sum()),'first':str(s.index.min()),'last':str(s.index.max())}
            if s.notna().sum()>=500: ss.append(s)
            else: errors[sym]=f'only {s.notna().sum()} rows'
        except Exception as e: errors[sym]=repr(e)
    if not any(x.name=='BTCUSDT' for x in ss) or len(ss)<6: raise RuntimeError(f'too few Bybit assets: {len(ss)} {errors}')
    px=pd.concat(ss,axis=1).sort_index(); px=px.loc[px['BTCUSDT'].notna()]
    return px,errors,meta


def main():
    px,errors,meta=load(); pos=v4.build(px)['T2_upup']
    hist=audit.diag(px,pos,'2021-01-01','2026-04-24',BASE)
    s40=audit.diag(px,pos,'2021-01-01','2026-04-24',C40)
    s75=audit.diag(px,pos,'2021-01-01','2026-04-24',C75)
    delay=audit.diag(px,pos,'2021-01-01','2026-04-24',BASE,extra_delay=1)
    folds=[]
    for y in range(2021,2026):
        d=audit.diag(px,pos,f'{y}-01-01',f'{y}-12-31',BASE); d['year']=y; folds.append(d)
    meaningful=[f for f in folds if f.get('active_day_pct',0)>=10]
    positives=sum(f.get('net_pct',0)>0 for f in meaningful)
    rolls=audit.rolling_windows(px.loc[px.index<=LOCK],pos.loc[pos.index<=LOCK],window=180,step=30,cost=BASE)
    gate=(hist.get('net_pct',0)>0 and hist.get('sharpe',0)>=.50 and hist.get('pf',0)>=1.08 and hist.get('max_dd_pct',999)<=20 and
          s40.get('net_pct',0)>0 and delay.get('net_pct',0)>0 and hist.get('active_day_pct',0)>=15 and
          len(meaningful)>=3 and positives>=max(3,int(.75*len(meaningful)+.999999)) and
          rolls.get('meaningful_count',0)>=20 and rolls.get('positive_fraction_meaningful',0)>=.60)
    post={
      'base':audit.diag(px,pos,'2026-04-25',None,BASE),
      'stress40':audit.diag(px,pos,'2026-04-25',None,C40),
      'stress75':audit.diag(px,pos,'2026-04-25',None,C75),
    }
    state={'version':'quantbot-v5-independent-bybit-validation','updated':datetime.now(timezone.utc).isoformat(),
      'venue':'Bybit spot public daily klines','rule':'frozen T2_upup; no reselection or retuning','loaded_assets':list(px.columns),'asset_errors':errors,'metadata':meta,
      'prelock':hist,'stress40':s40,'stress75':s75,'delay2_total':delay,'folds':folds,
      'meaningful_positive_folds':f'{positives}/{len(meaningful)}','rolling180':{k:v for k,v in rolls.items() if k!='windows'},
      'independent_venue_gate':bool(gate),'postlock_reused_diagnostic':post,
      'interpretation':'FROZEN_T2_PASSES_INDEPENDENT_BYBIT_VENUE' if gate else 'FROZEN_T2_FAILS_INDEPENDENT_BYBIT_VENUE',
      'live_money_authorized':False,
      'limitations':['Bybit listing histories differ from Binance, so the investable cross-section is not identical.','Daily bars do not model order-book impact or venue outages.','2026-04-25+ remains diagnostic rather than pristine forward evidence.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'gate':gate,'loaded':list(px.columns),'prelock':hist,'stress40':s40,'rolling':state['rolling180']},indent=2))
if __name__=='__main__': main()
