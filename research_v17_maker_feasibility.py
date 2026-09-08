from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v15_microstructure_edge_screen as v15

STATE=Path('data/v17_maker_feasibility_state.json')
# Fee/adverse-selection scenarios in total round-trip bps. These are execution hypotheses, not claimed account fees.
SCENARIOS={
 'T7x2':{'entry_fee':7.0,'exit_fee':7.0,'adverse':0.0},
 'M2_T5_A2':{'entry_fee':2.0,'exit_fee':5.0,'adverse':2.0},
 'M2_T5_A4':{'entry_fee':2.0,'exit_fee':5.0,'adverse':4.0},
 'M2_T5_A6':{'entry_fee':2.0,'exit_fee':5.0,'adverse':6.0},
 'M2_M2_A4':{'entry_fee':2.0,'exit_fee':2.0,'adverse':4.0},
 'M2_M2_A6':{'entry_fee':2.0,'exit_fee':2.0,'adverse':6.0},
}
FILL_PROBS=(0.35,0.55,0.75)
RULE='E1_break_accept';H=6

def gross_events(x,side):
    entry=x.open.shift(-1);exitp=x.close.shift(-H)
    valid=(side!=0)&entry.notna()&exitp.notna()
    g=side[valid]*(exitp[valid]/entry[valid]-1.0)*10000
    return pd.Series(g,index=x.index[valid])

def summarize(g,sc):
    total=sc['entry_fee']+sc['exit_fee']+sc['adverse'];net=g-total
    out={'events':int(len(net)),'gross_mean_bps':float(g.mean()) if len(g) else 0.0,
         'roundtrip_cost_bps':float(total),'mean_net_bps':float(net.mean()) if len(net) else 0.0,
         'median_net_bps':float(net.median()) if len(net) else 0.0,'win_pct':float((net>0).mean()*100) if len(net) else 0.0,
         'break_even_roundtrip_bps':float(g.mean()) if len(g) else 0.0}
    out['fill_scenarios']={str(p):{'expected_filled_events':float(len(net)*p),'expected_bps_per_opportunity':float(net.mean()*p) if len(net) else 0.0} for p in FILL_PROBS}
    return out

def block(x,t):
    side=v15.rule_masks(x,t)[RULE];g=gross_events(x,side)
    return {name:summarize(g,sc) for name,sc in SCENARIOS.items()}

def main():
    b=v15.load_5m();fit=b[(b.index>=v15.START)&(b.index<=v15.FIT_END)];val=b[(b.index>=v15.VAL_START)&(b.index<=v15.VAL_END)];hold=b[b.index>=v15.HOLDOUT_START]
    t=v15.thresholds(fit);fitres=block(fit,t)
    conservative=fitres['M2_T5_A4']
    fit_gate=(conservative['events']>=50 and conservative['mean_net_bps']>0 and conservative['gross_mean_bps']>10)
    valres=None;holdres=None;gate=False
    if fit_gate:
        valres=block(val,t);v=valres['M2_T5_A4']
        val_gate=(v['events']>=20 and v['mean_net_bps']>0 and valres['M2_T5_A6']['mean_net_bps']>-2)
        if val_gate:
            holdres=block(hold,t);h=holdres['M2_T5_A4']
            gate=(h['events']>=20 and h['mean_net_bps']>0 and holdres['M2_T5_A6']['mean_net_bps']>0)
    state={'version':'quantbot-v17-maker-feasibility','purpose':'EXECUTION_FEASIBILITY_NO_LIVE','signal':f'{RULE}_h{H}',
           'thresholds_fit_only':t,'scenario_note':'M=maker hypothesis, T=taker hypothesis, A=explicit adverse-selection penalty; all costs are round-trip components and both sides are included.',
           'fit':fitres,'fit_gate':bool(fit_gate),'validation':valres,'holdout':holdres,'holdout_gate_pass':bool(gate),'live_orders':False,
           'interpretation':'V17_MAKER_PATH_SURVIVES' if gate else ('V17_VALIDATION_OPENED' if fit_gate else 'NO_V17_MAKER_FEASIBILITY')}
    STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(state,indent=2),encoding='utf-8');print(json.dumps(state,indent=2))
if __name__=='__main__':main()
