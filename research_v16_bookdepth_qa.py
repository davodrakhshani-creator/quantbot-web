from __future__ import annotations
import io,json,zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOL='BTCUSDT'
STATE=Path('data/v16_bookdepth_qa_state.json')
BD='https://data.binance.vision/data/futures/um/daily/bookDepth'
KL='https://data.binance.vision/data/futures/um/daily/klines'
SAMPLE_DATES=['2025-01-15','2025-03-15','2025-05-19','2025-07-15','2025-09-15','2025-11-15','2026-01-15','2026-03-15','2026-05-15','2026-07-15','2026-08-15']

def getzip(url):
    r=requests.get(url,timeout=60)
    if r.status_code==404:return None
    r.raise_for_status(); return zipfile.ZipFile(io.BytesIO(r.content))

def load_depth(day):
    z=getzip(f'{BD}/{SYMBOL}/{SYMBOL}-bookDepth-{day}.zip')
    if z is None:return pd.DataFrame()
    df=pd.read_csv(z.open(z.namelist()[0]))
    if not {'timestamp','percentage','depth','notional'}.issubset(df.columns):
        df=pd.read_csv(z.open(z.namelist()[0]),header=None,names=['timestamp','percentage','depth','notional'])
    df['ts']=pd.to_datetime(df['timestamp'],utc=True,errors='coerce')
    for c in ['percentage','depth','notional']:df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.dropna(subset=['ts','percentage','depth','notional'])

def load_kline(day):
    z=getzip(f'{KL}/{SYMBOL}/1m/{SYMBOL}-1m-{day}.zip')
    if z is None:return pd.DataFrame()
    raw=pd.read_csv(z.open(z.namelist()[0]),header=None)
    raw=raw.iloc[:,:6];raw.columns=['open_time','open','high','low','close','volume']
    for c in raw.columns:raw[c]=pd.to_numeric(raw[c],errors='coerce')
    raw=raw.dropna();raw['minute']=pd.to_datetime(raw.open_time,unit='ms',utc=True).dt.floor('1min')
    return raw[['minute','close']]

def audit_day(day):
    d=load_depth(day);k=load_kline(day)
    if d.empty or k.empty:return {'day':day,'available':False}
    d['minute']=d.ts.dt.floor('1min');d=d.merge(k,on='minute',how='left').dropna(subset=['close'])
    d=d[(d.depth>0)&(d.notional>0)&(d.percentage!=0)].copy()
    if d.empty:return {'day':day,'available':False}
    d['implied']=d.notional/d.depth
    d['rel']=(d.implied/d.close)-1.0
    d['sign_ok']=np.sign(d.rel)==np.sign(d.percentage)
    band=np.abs(d.percentage)/100.0
    d['within_band']=np.abs(d.rel)<=band+0.005
    d['bad10']=np.abs(d.rel)>0.10
    stamps=d.groupby('ts').size()
    diffs=pd.Series(sorted(d.ts.drop_duplicates())).diff().dt.total_seconds().dropna()
    return {'day':day,'available':True,'rows':int(len(d)),'timestamps':int(d.ts.nunique()),
            'levels_median':float(stamps.median()),'sign_ok_pct':float(d.sign_ok.mean()*100),
            'within_band_pct':float(d.within_band.mean()*100),'bad10_pct':float(d.bad10.mean()*100),
            'median_abs_rel_pct':float(np.abs(d.rel).median()*100),'median_cadence_sec':float(diffs.median()) if len(diffs) else None}

def main():
    rows=[audit_day(d) for d in SAMPLE_DATES];ok=[r for r in rows if r.get('available')]
    if ok:
        sign=np.mean([r['sign_ok_pct'] for r in ok]);within=np.mean([r['within_band_pct'] for r in ok]);bad=np.mean([r['bad10_pct'] for r in ok])
        usable=(len(ok)>=8 and sign>=95 and within>=95 and bad<=1)
    else: sign=within=bad=0;usable=False
    state={'version':'quantbot-v16-bookdepth-qa','purpose':'DATA_QUALITY_ONLY_NO_LIVE','symbol':SYMBOL,
           'sample_dates':SAMPLE_DATES,'days_available':len(ok),'per_day':rows,
           'aggregate':{'mean_sign_ok_pct':sign,'mean_within_band_pct':within,'mean_bad10_pct':bad},
           'historical_bookdepth_usable':bool(usable),'next':'BUILD_HISTORICAL_L2_MODEL' if usable else 'REJECT_BOOKDEPTH_BACKTEST_AND_USE_FORWARD_L2','live_orders':False}
    STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(state,indent=2),encoding='utf-8');print(json.dumps(state,indent=2))
if __name__=='__main__':main()
