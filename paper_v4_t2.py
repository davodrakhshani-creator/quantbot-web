"""Forward-only paper tracker for the QuantBot v4 T2_upup winner.
No live orders. Frozen before the first completed 2026-09-08 UTC bar is observed.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import research_v3_independent as v3
import research_v4_tournament as v4

OUT=Path('data/v4_paper_t2.json')
RULE='T2_upup'
START=pd.Timestamp('2026-09-08',tz='UTC')


def main():
    px,errors=v3.load_prices(); cutoff=pd.Timestamp(datetime.now(timezone.utc).date(),tz='UTC'); px=px.loc[px.index<cutoff]
    pos=v4.build(px)[RULE]; last=px.index.max()
    base=v3.slice_metrics(px,pos,str(START.date()),None,v3.BASE_COST)
    stress=v3.slice_metrics(px,pos,str(START.date()),None,v3.STRESS_COST)
    held=pos.shift(1).fillna(0.0); mask=held.index>=START; ex=held.loc[mask].abs().sum(axis=1)
    active=int((ex>1e-12).sum())
    row=pos.loc[last]; weights={k:float(v) for k,v in row.items() if abs(float(v))>1e-12}
    btc=px['BTCUSDT']; sma200=btc.rolling(200).mean(); m60=btc/btc.shift(60)-1; m120=btc/btc.shift(120)-1
    gate=bool(base.get('n_days',0)>=180 and active>=20 and base.get('net_pct',0)>0 and base.get('pf',0)>=1.10 and base.get('max_dd_pct',999)<=10 and stress.get('net_pct',0)>0)
    state={
      'version':'quantbot-v4-forward-paper-t2','updated':datetime.now(timezone.utc).isoformat(),
      'paper_start':str(START),'rule_frozen':RULE,
      'rule_description':'multi-horizon top3 positive momentum; BTC>200d SMA AND BTC 60d return>0 AND BTC 120d return>0; inverse-vol; 12% target; weekly rebalance; <=1x gross.',
      'frozen_before_first_forward_completed_bar':True,'live_orders':False,'data_end':str(last),'asset_errors':errors,
      'current_signal':{'signal_date':str(last),'weights':weights,'gross_exposure':float(sum(abs(x) for x in weights.values())),
                        'btc_close':float(btc.loc[last]),'btc_sma200':float(sma200.loc[last]),'btc_mom60_pct':float(m60.loc[last]*100),'btc_mom120_pct':float(m120.loc[last]*100),
                        'btc_upup_gate':bool(btc.loc[last]>sma200.loc[last] and m60.loc[last]>0 and m120.loc[last]>0)},
      'forward_metrics_13bps':base,'forward_metrics_40bps':stress,'forward_active_days':active,
      'promotion_gate_pre_registered':{'criteria':'n_days>=180; active_days>=20; net>0; PF>=1.10; maxDD<=10%; 40bps stress net>0','passed':gate},
      'research_status':'PAPER_GATE_PASSED_MANUAL_REVIEW_ONLY' if gate else 'PAPER_ONLY_NO_LIVE_MONEY',
      'guardrail':'No automatic live-money authorization even after gate; separate execution/venue/slippage/operational review required.'
    }
    OUT.write_text(json.dumps(state,indent=2),encoding='utf-8'); print(json.dumps(state,indent=2))
if __name__=='__main__': main()
