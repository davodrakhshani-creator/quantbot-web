"""Independent Coinbase venue validation for frozen QuantBot T2_upup.

No selection or retuning. Coinbase Exchange public daily candles are mapped into the
same canonical asset columns used by the frozen T2 rule.
"""
from __future__ import annotations
import json, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests
import research_v3_audit as audit
import research_v4_tournament as v4

OUT=Path('data/v5_coinbase_validation.json')
BASE=.0013; C40=.0040; C75=.0075
START=pd.Timestamp('2020-01-01',tz='UTC')
LOCK=pd.Timestamp('2026-04-24',tz='UTC')
PRODUCTS={
 'BTCUSDT':'BTC-USD','ETHUSDT':'ETH-USD','SOLUSDT':'SOL-USD','XRPUSDT':'XRP-USD',
 'ADAUSDT':'ADA-USD','DOGEUSDT':'DOGE-USD','LINKUSDT':'LINK-USD','LTCUSDT':'LTC-USD',
 'BCHUSDT':'BCH-USD','AVAXUSDT':'AVAX-USD','DOTUSDT':'DOT-USD'
}
BASE_URL='https://api.exchange.coinbase.com/products/{}/candles'
HEADERS={'User-Agent':'QuantBot-research/5.0','Accept':'application/json'}


def fetch_one(canonical:str, product:str)->pd.Series:
    today=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC')
    cur=START; rows=[]; sess=requests.Session(); sess.headers.update(HEADERS)
    while cur<today:
        end=min(cur+pd.Timedelta(days=280),today)
        params={'granularity':86400,'start':cur.isoformat(),'end':end.isoformat()}
        last=None
        for attempt in range(5):
            try:
                r=sess.get(BASE_URL.format(product),params=params,timeout=25)
                if r.status_code in (429,500,502,503,504):
                    time.sleep(1.5*(attempt+1)); continue
                r.raise_for_status(); got=r.json()
                if isinstance(got,list): last=got; break
            except Exception:
                if attempt==4: raise
                time.sleep(1.5*(attempt+1))
        if last: rows.extend(last)
        cur=end
        time.sleep(.20)
    if not rows: raise RuntimeError('empty')
    d=pd.DataFrame(rows)
    if d.shape[1]<5: raise RuntimeError(f'bad candle payload {d.head().to_dict()}')
    idx=pd.to_datetime(d[0].astype('int64'),unit='s',utc=True).dt.floor('D')
    s=pd.Series(pd.to_numeric(d[4],errors='coerce').values,index=idx,name=canonical)
    s=s[~s.index.duplicated(keep='last')].sort_index()
    return s.loc[(s.index>=START)&(s.index<today)]


def load():
    ss=[]; errors={}; meta={}
    for canonical,product in PRODUCTS.items():
        try:
            s=fetch_one(canonical,product)
            meta[canonical]={'product':product,'rows':int(s.notna().sum()),'first':str(s.index.min()),'last':str(s.index.max())}
            if s.notna().sum()>=500: ss.append(s)
            else: errors[canonical]=f'only {s.notna().sum()} rows'
        except Exception as e: errors[canonical]=repr(e)
    if not any(x.name=='BTCUSDT' for x in ss) or len(ss)<6:
        raise RuntimeError(f'too few Coinbase assets: {len(ss)} {errors}')
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
    required=max(3,int(.75*len(meaningful)+.999999))
    gate=(hist.get('net_pct',0)>0 and hist.get('sharpe',0)>=.50 and hist.get('pf',0)>=1.08 and hist.get('max_dd_pct',999)<=20 and
          s40.get('net_pct',0)>0 and delay.get('net_pct',0)>0 and hist.get('active_day_pct',0)>=15 and
          len(meaningful)>=3 and positives>=required and rolls.get('meaningful_count',0)>=20 and
          rolls.get('positive_fraction_meaningful',0)>=.60)
    post={'base':audit.diag(px,pos,'2026-04-25',None,BASE),'stress40':audit.diag(px,pos,'2026-04-25',None,C40),'stress75':audit.diag(px,pos,'2026-04-25',None,C75)}
    state={'version':'quantbot-v5-independent-coinbase-validation','updated':datetime.now(timezone.utc).isoformat(),
      'venue':'Coinbase Exchange public spot daily candles','rule':'frozen T2_upup; no reselection or retuning',
      'loaded_assets':list(px.columns),'asset_errors':errors,'metadata':meta,'prelock':hist,'stress40':s40,'stress75':s75,
      'delay2_total':delay,'folds':folds,'meaningful_positive_folds':f'{positives}/{len(meaningful)}',
      'rolling180':{k:v for k,v in rolls.items() if k!='windows'},'independent_venue_gate':bool(gate),
      'postlock_reused_diagnostic':post,
      'interpretation':'FROZEN_T2_PASSES_INDEPENDENT_COINBASE_VENUE' if gate else 'FROZEN_T2_FAILS_INDEPENDENT_COINBASE_VENUE',
      'live_money_authorized':False,
      'limitations':['Coinbase listing histories and USD quote markets differ from Binance USDT markets.','Coinbase documents that historical candles can be incomplete when no ticks occur.','Daily bars do not model order-book impact, partial fills, or venue outages.','2026-04-25+ is reused diagnostic evidence only.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'gate':gate,'loaded':list(px.columns),'errors':errors,'prelock':hist,'stress40':s40,'rolling':state['rolling180']},indent=2))
if __name__=='__main__': main()
