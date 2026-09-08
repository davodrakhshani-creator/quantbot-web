from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pandas as pd
import research_v11_microburst as v11
import research_v13_aggtrade_absorption as v13

STATE=Path('data/v14_tradeflow_regime_hybrid_state.json')
COSTS=(7.0,12.0,20.0)
Spec=v11.Spec

# Materially different hypothesis from v13:
# - Strong 15m trend + efficient same-direction trade flow => continuation scalp.
# - Neutral/weak 15m regime + aggressive flow that fails price acceptance => reversal scalp.
# One regime, one behavior; do not force all order-flow into the same trade direction.
SPECS=[
    Spec('H0_balanced',0.08,0.35,0.50,0.24,0.95,1.55,10,85),
    Spec('H1_strict',0.12,0.45,0.60,0.28,0.90,1.70,10,105),
    Spec('H2_trend_heavy',0.10,0.40,0.55,0.24,1.00,1.90,12,125),
    Spec('H3_reversal_heavy',0.14,0.50,0.65,0.32,0.85,1.45,8,75),
    Spec('H4_fast',0.08,0.40,0.58,0.26,0.75,1.25,6,95),
    Spec('H5_defensive',0.16,0.55,0.70,0.35,1.10,1.85,12,115),
]

def signal_frame(b,s):
    x=b.copy()
    flow_buy=(x.flow>=s.flow_thr)&(x.persist>=s.persist_thr)&(x.burst>=s.accel_thr)
    flow_sell=(x.flow<=-s.flow_thr)&(x.persist<=-s.persist_thr)&(x.burst>=s.accel_thr)

    strong_up=(x.qclose>x.qef)&(x.qef>x.qes)&(x.qslope>0)&(x.qcci>=abs(s.cci_gate))
    strong_dn=(x.qclose<x.qef)&(x.qef<x.qes)&(x.qslope<0)&(x.qcci<=-abs(s.cci_gate))
    weak_regime=~(strong_up|strong_dn)

    # Continuation: same-direction flow must actually produce price acceptance.
    cont_long=strong_up&flow_buy&(x.close>x.vwap)&(x.close>=x.hi12)&(x.close_loc>=0.70)&(x.ret_bps>=8)
    cont_short=strong_dn&flow_sell&(x.close<x.vwap)&(x.close<=x.lo12)&(x.close_loc<=0.30)&(x.ret_bps>=8)

    # Reversal: aggression reaches through the local extreme but fails to hold it.
    fail_hi=flow_buy&(x.high>x.hi12)&(x.close<x.hi12)&(x.upper_wick>=s.body_thr)&(x.close_loc<=0.58)&(x.ret_bps<=18)
    fail_lo=flow_sell&(x.low<x.lo12)&(x.close>x.lo12)&(x.lower_wick>=s.body_thr)&(x.close_loc>=0.42)&(x.ret_bps<=18)
    rev_short=weak_regime&fail_hi
    rev_long=weak_regime&fail_lo

    x['long_sig']=cont_long|rev_long
    x['short_sig']=cont_short|rev_short
    x['mode_long_cont']=cont_long
    x['mode_short_cont']=cont_short
    x['mode_long_rev']=rev_long
    x['mode_short_rev']=rev_short
    return x

v11.signal_frame=signal_frame

def score(m):
    if m['trades']<80 or m['net_pct']<=0 or m['pf']<1.12 or m['max_dd_pct']>18:
        return -1e9
    return m['net_pct']+45*(m['pf']-1)+0.08*m['win_pct']-0.9*m['max_dd_pct']

def main():
    raw=v13.load_data(); b=v13.build_trade_5m(raw)
    train=b[b.index<=v11.SELECTION_END]; hold=b[b.index>=v11.HOLDOUT_START]
    cand={}; ranked=[]
    for s in SPECS:
        tr={str(c):v11.simulate(train,s,c,risk_frac=.0018,lev_cap=1.5) for c in COSTS}
        sc=score(tr['7.0']); cand[s.name]={'spec':asdict(s),'train':tr,'score':sc}; ranked.append((sc,s.name))
    ranked.sort(reverse=True)
    selected=ranked[0][1] if ranked and ranked[0][0]>-1e8 else None
    holdout=None; gate=False; daily=None; diag=None
    if selected:
        s=next(z for z in SPECS if z.name==selected)
        h7=v11.simulate(hold,s,7.0,risk_frac=.0018,lev_cap=1.5,collect=True)
        h12=v11.simulate(hold,s,12.0,risk_frac=.0018,lev_cap=1.5)
        h20=v11.simulate(hold,s,20.0,risk_frac=.0018,lev_cap=1.5)
        holdout={'7.0':{k:v for k,v in h7.items() if k!='trade_rows'},'12.0':h12,'20.0':h20}
        gate=(h7['trades']>=30 and h7['net_pct']>0 and h7['pf']>=1.15 and h7['max_dd_pct']<=15 and h12['net_pct']>0 and h20['net_pct']>0)
        daily=v11.day100(h7.get('trade_rows',[]))
        diag={'long_only_7':v11.simulate(hold,s,7.0,risk_frac=.0018,lev_cap=1.5,side_filter=1),'short_only_7':v11.simulate(hold,s,7.0,risk_frac=.0018,lev_cap=1.5,side_filter=-1)}
    state={'version':'quantbot-v14-tradeflow-regime-hybrid','purpose':'TRADE_LEVEL_REGIME_HYBRID_NO_LIVE','symbol':v11.SYMBOL,'source':'Binance Vision USD-M aggTrades -> trade-flow 5m entries / 15m regime','selection_end':str(v11.SELECTION_END),'holdout_start':str(v11.HOLDOUT_START),'selected':selected,'candidates':cand,'holdout':holdout,'day100':daily,'side_diagnostic':diag,'holdout_gate_pass':bool(gate),'live_orders':False,'interpretation':'V14_HYBRID_CANDIDATE' if gate else 'NO_V14_EDGE_YET'}
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps(state,indent=2))

if __name__=='__main__':
    main()
