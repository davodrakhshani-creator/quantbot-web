"""Adversarial integrity checks for QuantBot v4 research code. No strategy selection."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import research_v3_independent as v3
import research_v3_audit as audit
import research_v3_snapshot_validation as snap
import research_v4_tournament as v4

OUT=Path('data/v4_redteam_state.json')


def check_universe(px):
    cfull=v4.build(px)
    checkpoints=[pd.Timestamp(x,tz='UTC') for x in ['2022-06-30','2023-06-30','2024-06-30','2025-06-30','2026-03-31']]
    lookahead=[]
    for cp in checkpoints:
        if cp not in px.index: continue
        trunc=px.loc[px.index<=cp]
        ct=v4.build(trunc)
        for name in cfull:
            a=cfull[name].loc[cp].fillna(0).reindex(px.columns,fill_value=0).to_numpy(float)
            b=ct[name].loc[cp].fillna(0).reindex(px.columns,fill_value=0).to_numpy(float)
            lookahead.append({'checkpoint':str(cp.date()),'candidate':name,'max_abs_diff':float(np.max(np.abs(a-b)))})
    maxdiff=max((x['max_abs_diff'] for x in lookahead),default=0.0)
    gross={name:float(pos.abs().sum(axis=1).max()) for name,pos in cfull.items()}
    cost_checks={}
    for name,pos in cfull.items():
        a=audit.diag(px,pos,'2021-01-01','2026-04-24',.0013)['net_pct']
        b=audit.diag(px,pos,'2021-01-01','2026-04-24',.0040)['net_pct']
        c=audit.diag(px,pos,'2021-01-01','2026-04-24',.0075)['net_pct']
        cost_checks[name]={'13':a,'40':b,'75':c,'monotonic':bool(a>=b-1e-10 and b>=c-1e-10)}
    completed_bar=px.index.max()<pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC')
    return {
      'lookahead_max_abs_diff':maxdiff,'lookahead_pass':bool(maxdiff<1e-10),'lookahead_details':lookahead,
      'max_gross_by_candidate':gross,'gross_pass':bool(max(gross.values(),default=0)<=1.0000001),
      'cost_monotonic_pass':bool(all(x['monotonic'] for x in cost_checks.values())),'cost_checks':cost_checks,
      'completed_bar_only_pass':bool(completed_bar)
    }


def main():
    cut=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC')
    cur,_=v3.load_prices(); cur=cur.loc[cur.index<cut]
    hs,_=snap.load(); hs=hs.loc[hs.index<cut]
    results={'current':check_universe(cur),'historical_snapshot':check_universe(hs)}
    passed=all(r['lookahead_pass'] and r['gross_pass'] and r['cost_monotonic_pass'] and r['completed_bar_only_pass'] for r in results.values())
    state={'version':'quantbot-v4-redteam','updated':datetime.now(timezone.utc).isoformat(),'results':results,
           'redteam_pass':bool(passed),'interpretation':'RESEARCH_ENGINE_INTEGRITY_CHECKS_PASS' if passed else 'RESEARCH_ENGINE_INTEGRITY_FAILURE'}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'redteam_pass':passed,'summary':{u:{k:v for k,v in r.items() if k.endswith('_pass') or k=='lookahead_max_abs_diff'} for u,r in results.items()}},indent=2))
if __name__=='__main__': main()
