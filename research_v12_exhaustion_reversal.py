from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pandas as pd
import research_v11_microburst as v11

STATE=Path('data/v12_exhaustion_reversal_state.json')
COSTS=(7.0,12.0,20.0)

# Reuse v11's data loader, 1m->5m microstructure builder, simulator, and $100/day accounting.
# v12 changes the hypothesis materially: extreme aggressive flow + failed price acceptance
# is treated as exhaustion/absorption and traded CONTRARIAN, not as continuation.
Spec=v11.Spec
SPECS=[
    Spec('R0_failed_break',0.20,0.60,0.03,0.30,0.90,1.50,9,35),
    Spec('R1_strict_fail',0.26,0.70,0.04,0.35,0.85,1.65,9,45),
    Spec('R2_extreme_fail',0.32,0.80,0.05,0.35,0.90,1.80,12,55),
    Spec('R3_wide_revert',0.24,0.70,0.03,0.30,1.05,2.00,15,40),
    Spec('R4_fast_revert',0.22,0.65,0.04,0.30,0.75,1.30,6,35),
    Spec('R5_defensive',0.30,0.75,0.04,0.35,1.10,1.90,12,50),
]

def signal_frame(b,s):
    x=b.copy()
    rng=(x.high-x.low).replace(0,np.nan)
    upper=(x.high-x[['open','close']].max(axis=1))/rng
    lower=(x[['open','close']].min(axis=1)-x.low)/rng
    close_loc=(x.close-x.low)/rng
    eff_med=x.efficiency.rolling(288,min_periods=96).median().shift(1)
    inefficient=x.efficiency <= 0.70*eff_med

    # Failed local breaks: aggressive flow pushes through a local extreme, but the bar
    # closes back inside / away from that extreme with a rejection wick.
    buy_exhaust=(
        (x.flow>=s.flow_thr)&(x.persist>=s.persist_thr)&(x.accel>=s.accel_thr)&
        inefficient&(x.high>=x.hi12)&(x.close<x.hi12)&(upper>=0.22)&(close_loc<=0.62)&
        (x.close>=x.vwap)
    )
    sell_exhaust=(
        (x.flow<=-s.flow_thr)&(x.persist<=-s.persist_thr)&(x.accel<=-s.accel_thr)&
        inefficient&(x.low<=x.lo12)&(x.close>x.lo12)&(lower>=0.22)&(close_loc>=0.38)&
        (x.close<=x.vwap)
    )

    # Avoid fading the strongest 15m trends. Reversals are allowed in neutral/weak regimes
    # or when momentum is stretched but already failing locally.
    not_hard_up=~((x.qclose>x.qef)&(x.qef>x.qes)&(x.qslope>0)&(x.qcci>100))
    not_hard_dn=~((x.qclose<x.qef)&(x.qef<x.qes)&(x.qslope<0)&(x.qcci<-100))
    x['short_sig']=buy_exhaust&not_hard_up
    x['long_sig']=sell_exhaust&not_hard_dn
    return x

# Monkeypatch only the signal hypothesis; execution/risk/cost mechanics remain identical to v11.
v11.signal_frame=signal_frame

def train_score(m):
    if m['trades']<60 or m['net_pct']<=0 or m['pf']<1.12 or m['max_dd_pct']>18:
        return -1e9
    return m['net_pct']+40*(m['pf']-1)+.10*m['win_pct']-.8*m['max_dd_pct']

def main():
    raw=v11.load_data(); b=v11.build_5m(raw)
    train=b[b.index<=v11.SELECTION_END]; hold=b[b.index>=v11.HOLDOUT_START]
    candidates={}; ranked=[]
    for s in SPECS:
        tr={str(c):v11.simulate(train,s,c) for c in COSTS}; sc=train_score(tr['7.0'])
        candidates[s.name]={'spec':asdict(s),'train':tr,'score':sc}; ranked.append((sc,s.name))
    ranked.sort(reverse=True)
    selected=ranked[0][1] if ranked and ranked[0][0]>-1e8 else None
    holdout=None; gate=False; diag=None; daily=None
    if selected:
        s=next(z for z in SPECS if z.name==selected)
        h7=v11.simulate(hold,s,7.0,collect=True); h12=v11.simulate(hold,s,12.0); h20=v11.simulate(hold,s,20.0)
        holdout={'7.0':{k:v for k,v in h7.items() if k!='trade_rows'},'12.0':h12,'20.0':h20}
        gate=(h7['trades']>=30 and h7['net_pct']>0 and h7['pf']>=1.15 and h7['max_dd_pct']<=15 and h12['net_pct']>0 and h20['net_pct']>0)
        diag={'long_only_7':v11.simulate(hold,s,7.0,side_filter=1),'short_only_7':v11.simulate(hold,s,7.0,side_filter=-1)}
        daily=v11.day100(h7.get('trade_rows',[]))
    state={'version':'quantbot-v12-btc-exhaustion-reversal','purpose':'RESEARCH_ONLY_NO_LIVE','symbol':v11.SYMBOL,'source':'Binance Vision BTCUSDT 1m -> 5m microstructure, 15m regime','hypothesis':'Extreme taker-flow + failed price acceptance / false break = exhaustion reversal','selection_end':str(v11.SELECTION_END),'holdout_start':str(v11.HOLDOUT_START),'candidate_count':len(SPECS),'selected':selected,'candidates':candidates,'holdout':holdout,'side_diagnostic':diag,'day100':daily,'holdout_gate_pass':bool(gate),'live_orders':False,'interpretation':'V12_REVERSAL_CANDIDATE' if gate else 'NO_V12_EDGE_YET'}
    STATE.parent.mkdir(parents=True,exist_ok=True); STATE.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))

if __name__=='__main__': main()
