"""Frozen QuantBot v3 rule on an ex-ante historical universe.

Universe source: CoinMarketCap 2021-01-03 historical top-20 snapshot. Stablecoins,
wrapped BTC and BSV (not Binance-tradable then) are excluded. The remaining 16
large non-stable assets are fixed ex ante and include names later delisted from
Binance, reducing current-survivor selection bias.

The trading rule is NOT re-selected or tuned here.
"""
from __future__ import annotations
import io,json,zipfile
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import research_v3_independent as v3
import research_v3_audit as audit

OUT=Path('data/v3_snapshot_validation.json')
SYMS=['BTCUSDT','ETHUSDT','LTCUSDT','XRPUSDT','DOTUSDT','BCHUSDT','ADAUSDT','BNBUSDT','LINKUSDT','XLMUSDT','EOSUSDT','XMRUSDT','THETAUSDT','TRXUSDT','XEMUSDT','VETUSDT']

def month_iter(start='2019-01-01'):
    cur=pd.Timestamp(start).replace(day=1)
    end=pd.Timestamp(datetime.now(timezone.utc).date()).replace(day=1)-pd.offsets.MonthBegin(1)
    while cur<=end:
        yield cur.year,cur.month; cur+=pd.offsets.MonthBegin(1)

def archive_spot(sym):
    pieces=[]
    for y,m in month_iter():
        u=f'https://data.binance.vision/data/spot/monthly/klines/{sym}/1d/{sym}-1d-{y:04d}-{m:02d}.zip'
        try:
            r=requests.get(u,timeout=20)
            if r.status_code==404: continue
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z: raw=z.read(z.namelist()[0])
            d=pd.read_csv(io.BytesIO(raw),header=None)
            if d.empty: continue
            first=pd.to_numeric(d.iloc[:,0],errors='coerce')
            d=d.loc[first.notna()].copy(); first=pd.to_numeric(d.iloc[:,0])
            unit='us' if float(first.median())>1e14 else 'ms'
            idx=pd.to_datetime(first.astype('int64'),unit=unit,utc=True).dt.floor('D')
            pieces.append(pd.Series(pd.to_numeric(d.iloc[:,4],errors='coerce').values,index=idx))
        except Exception: continue
    if not pieces: raise RuntimeError('no archive data')
    s=pd.concat(pieces).sort_index(); s=s[~s.index.duplicated(keep='last')]; s.name=sym; return s

def load():
    arr=[];meta={}
    for sym in SYMS:
        try:
            try:
                s=v3.get_klines(sym); src='binance_api'
            except Exception:
                s=archive_spot(sym); src='binance_vision_archive'
            if s.notna().sum()>=300:
                arr.append(s); meta[sym]={'source':src,'first':str(s.index.min()),'last':str(s.index.max()),'rows':int(s.notna().sum())}
            else: meta[sym]={'error':'insufficient rows'}
        except Exception as e: meta[sym]={'error':repr(e)}
    px=pd.concat(arr,axis=1).sort_index()
    px=px.loc[px['BTCUSDT'].notna()]
    return px,meta

def main():
    px,meta=load()
    pos=audit.frozen_e(px)
    periods={
      'history':audit.diag(px,pos,'2021-01-01','2026-04-24',.0013),
      'recent_prelock':audit.diag(px,pos,'2023-01-01','2026-04-24',.0013),
      'postlock':audit.diag(px,pos,'2026-04-25',None,.0013),
      'postlock_cost40':audit.diag(px,pos,'2026-04-25',None,.0040),
      'postlock_cost75':audit.diag(px,pos,'2026-04-25',None,.0075),
      'postlock_delay2_total':audit.diag(px,pos,'2026-04-25',None,.0013,extra_delay=1),
    }
    folds=[]
    for y in range(2021,2026):
        d=audit.diag(px,pos,f'{y}-01-01',f'{y}-12-31',.0013); d['year']=y; folds.append(d)
    locked_px=px.loc[px.index<=v3.LOCK_DATE]; locked_pos=pos.loc[pos.index<=v3.LOCK_DATE]
    rolls=audit.rolling_windows(locked_px,locked_pos,window=180,step=30)
    passed=(
      len(px.columns)>=12 and periods['history']['net_pct']>0 and periods['history']['sharpe']>=.50 and
      periods['history']['max_dd_pct']<=25 and periods['recent_prelock']['net_pct']>0 and
      periods['postlock_cost40']['net_pct']>0 and periods['postlock_delay2_total']['net_pct']>0 and
      rolls['positive_fraction_meaningful']>=.60
    )
    state={
      'version':'quantbot-v3-historical-snapshot-universe-validation','updated':datetime.now(timezone.utc).isoformat(),
      'frozen_rule':'E_blend_top3_btc200_gate unchanged from v3 selection.',
      'universe_source':'CoinMarketCap historical snapshot 2021-01-03 top 20. Excluded USDT, USDC, WBTC and BSV; fixed 16 non-stable Binance-tradable assets.',
      'universe_symbols_requested':SYMS,'loaded_symbols':list(px.columns),'symbol_metadata':meta,
      'why_this_matters':'This fixed historical universe includes later-delisted names and does not add later winners, reducing current-survivor bias versus the original 20-current-asset test.',
      'periods':periods,'annual_folds':folds,
      'rolling_180d_summary':{k:v for k,v in rolls.items() if k!='windows'},
      'validation_pass':bool(passed),
      'interpretation':'SUPPORTS_FROZEN_RULE' if passed else 'FAILS_HISTORICAL_UNIVERSE_VALIDATION',
      'limitations':['A single 2021 snapshot is not a fully dynamic survivorship-free universe.','Binance availability differs across assets and delisting dates.','Daily bars omit intraday execution effects.']
    }
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'loaded':len(px.columns),'periods':periods,'rolls':state['rolling_180d_summary'],'pass':passed},indent=2))
if __name__=='__main__': main()
