"""Forward-only paper tracker for QuantBot v5 P4_asset_dual.
Frozen on 2026-09-08 before the first completed UTC bar after selection. No live orders.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import research_v3_independent as v3
import research_v5_precision as p5

OUT=Path('data/v5_paper_precision.json')
RULE='P4_asset_dual'; START=pd.Timestamp('2026-09-08',tz='UTC')

def main():
    px,errors=v3.load_prices(); cutoff=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC'); px=px.loc[px.index<cutoff]
    pos=p5._positions(px,None)[RULE]; last=px.index.max()
    b13=v3.slice_metrics(px,pos,str(START.date()),None,.0013); b40=v3.slice_metrics(px,pos,str(START.date()),None,.0040); b75=v3.slice_metrics(px,pos,str(START.date()),None,.0075)
    held=pos.shift(1).fillna(0.0); ex=held.loc[held.index>=START].abs().sum(axis=1); active=int((ex>1e-12).sum())
    row=pos.loc[last]; weights={k:float(v) for k,v in row.items() if abs(float(v))>1e-12}
    gate=bool(b13.get('n_days',0)>=180 and active>=20 and b13.get('net_pct',0)>0 and b13.get('pf',0)>=1.12 and b13.get('max_dd_pct',999)<=10 and b40.get('net_pct',0)>0 and b75.get('net_pct',0)>0)
    state={'version':'quantbot-v5-forward-paper-precision','updated':datetime.now(timezone.utc).isoformat(),'paper_start':str(START),'rule_frozen':RULE,
      'rule_description':'Frozen T2 market regime plus selected-asset dual confirmation: each chosen asset must have positive 60d and 120d momentum; top3 positive blend; inverse-vol; 12% target; weekly rebalance.',
      'frozen_before_first_forward_completed_bar':True,'live_orders':False,'data_end':str(last),'asset_errors':errors,
      'current_signal':{'signal_date':str(last),'weights':weights,'gross_exposure':float(sum(abs(x) for x in weights.values()))},
      'forward_metrics_13bps':b13,'forward_metrics_40bps':b40,'forward_metrics_75bps':b75,'forward_active_days':active,
      'promotion_gate_pre_registered':{'criteria':'n_days>=180; active_days>=20; net>0; PF>=1.12; maxDD<=10%; 40bps>0; 75bps>0','passed':gate},
      'research_status':'PAPER_GATE_PASSED_MANUAL_REVIEW_ONLY' if gate else 'PAPER_ONLY_NO_LIVE_MONEY','live_money_authorized':False}
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))
if __name__=='__main__': main()
