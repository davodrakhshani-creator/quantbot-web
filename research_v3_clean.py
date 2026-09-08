"""Clean rerun of QuantBot v3 with incomplete UTC daily bar excluded.
This is the authoritative directional v3 state; candidate family and gates are unchanged.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import research_v3_independent as v3

OUT=Path('data/v3_clean_state.json')

def main():
    px,errors=v3.load_prices()
    cutoff=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC')
    px=px.loc[px.index<cutoff]
    cands=v3.candidates(px)
    locked=px.loc[px.index<=v3.LOCK_DATE]
    results={}
    years=[2021,2022,2023,2024,2025]
    for name,pos in cands.items():
        lp=pos.loc[locked.index]
        h=v3.slice_metrics(locked,lp,'2021-01-01','2026-04-24',v3.BASE_COST)
        s=v3.slice_metrics(locked,lp,'2021-01-01','2026-04-24',v3.STRESS_COST)
        folds=v3.annual_folds(locked,lp,years,v3.BASE_COST)
        results[name]={'history':h,'history_stress40':s,'folds':folds,
                       'history_gate':bool(v3.historical_gate(h,folds,s)),
                       'selection_score':float(v3.history_score(h,folds,s))}
    winner=max(results,key=lambda k:results[k]['selection_score'])
    pos=cands[winner]
    hold=v3.slice_metrics(px,pos,'2026-04-25',None,v3.BASE_COST)
    hold40=v3.slice_metrics(px,pos,'2026-04-25',None,v3.STRESS_COST)
    robust=bool(results[winner]['history_gate'] and v3.final_holdout_gate(hold,hold40))
    # exposure diagnostics for the frozen winner
    r=px.pct_change(fill_method=None).fillna(0.0); held=pos.shift(1).fillna(0.0)
    ex=held.abs().sum(axis=1); net,_to,_gross=v3.pnl_series(px,pos,v3.BASE_COST)
    mask=net.index>=pd.Timestamp('2026-04-25',tz='UTC'); active=ex.loc[mask]>1e-9
    active_net=net.loc[mask][active]
    exposure={'active_days':int(active.sum()),'active_day_pct':float(active.mean()*100),
              'avg_gross_exposure':float(ex.loc[mask].mean()),
              'median_active_gross':float(ex.loc[mask][active].median()) if active.any() else 0.0,
              'active_day_win_pct':float((active_net>0).mean()*100) if len(active_net) else 0.0}
    state={'version':'quantbot-v3-clean-completed-bars','updated':datetime.now(timezone.utc).isoformat(),
           'data_rule':'Only fully completed UTC daily bars; current UTC day excluded.',
           'data_end':str(px.index.max()),'assets':list(px.columns),'asset_errors':errors,
           'candidate_count':len(cands),'selection_lock':'2026-04-24','selected_candidate':winner,
           'selected_history':results[winner],'strict_postlock':hold,'strict_postlock_stress40':hold40,
           'postlock_exposure':exposure,'all_candidate_results':results,
           'robust_candidate':robust,'research_gate':'PROMOTE_TO_PAPER_TRADING_ONLY' if robust else 'DO_NOT_PROMOTE',
           'limitations':['Current-liquid universe has survivorship bias; historical-snapshot validation is evaluated separately.',
                          'Daily close data omit intraday execution/slippage/outages.','A robustness pass supports paper trading, not live-money deployment.']}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'winner':winner,'history':results[winner]['history'],'postlock':hold,'stress40':hold40,'exposure':exposure,'robust':robust},indent=2))
if __name__=='__main__': main()
