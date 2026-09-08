"""Clean historical-snapshot validation with incomplete current UTC bar excluded.
The frozen rule, universe and validation gate are unchanged from research_v3_snapshot_validation.py.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import research_v3_independent as v3
import research_v3_audit as audit
import research_v3_snapshot_validation as sv

OUT=Path('data/v3_snapshot_clean.json')

def main():
    px,meta=sv.load()
    cutoff=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC')
    px=px.loc[px.index<cutoff]
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
      'version':'quantbot-v3-historical-snapshot-clean','updated':datetime.now(timezone.utc).isoformat(),
      'data_rule':'Only fully completed UTC daily bars; current UTC day excluded.',
      'data_end':str(px.index.max()),'frozen_rule':'E_blend_top3_btc200_gate unchanged.',
      'universe_source':'CoinMarketCap 2021-01-03 top-20 snapshot; stablecoins/WBTC/BSV excluded; 16 fixed non-stable Binance assets.',
      'loaded_symbols':list(px.columns),'symbol_metadata':meta,'periods':periods,'annual_folds':folds,
      'rolling_180d_summary':{k:v for k,v in rolls.items() if k!='windows'},
      'validation_pass':bool(passed),'interpretation':'SUPPORTS_FROZEN_RULE' if passed else 'FAILS_HISTORICAL_UNIVERSE_VALIDATION',
      'limitations':['Single historical snapshot is not a fully dynamic survivorship-free universe.','Daily bars omit intraday execution effects.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'data_end':state['data_end'],'periods':periods,'rolling':state['rolling_180d_summary'],'pass':passed},indent=2))
if __name__=='__main__': main()
