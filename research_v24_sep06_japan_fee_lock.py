from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

DAY="2026-09-06"
STATE=Path("data/v24_sep06_japan_fee_lock_state.json")
START_EQUITY=100.0
ROUNDTRIP_COST_BPS=7.5
PROFIT_ARM_BPS=8.5      # fee fully covered + 1bp safety
TRAIL_BPS=2.5           # once armed, allow winner to run
HARD_STOP_BPS=7.0
MAX_HOLD_SEC=1800       # only applies if trade is <=0; positive-but-unarmed stays open
COOLDOWN_SEC=30

def testa_quality(y):
    # Historical Testa-style tape/EV proxy (true L2 not available in this archive).
    # Keep only above-median activity and notional conditions already embedded in v19.tape_ok,
    # plus moderate acceleration to avoid dead tape.
    return y.tape_ok & (y.accel >= 0.9)

def build_events(y):
    s=v20.events(y).copy()
    ok=testa_quality(y)
    s.loc[~ok,"side"]=0
    s.loc[~ok,"engine"]=None
    return s

def simulate(y,s):
    rows=[]
    equity=START_EQUITY
    i=0; n=len(y); idx=y.index
    last_engine={"jun":-10**9,"hansan":-10**9}
    while i<n-2:
        side=int(s.side.iloc[i]); eng=s.engine.iloc[i]
        if side==0 or eng is None or i-last_engine[eng]<COOLDOWN_SEC:
            i+=1; continue
        last_engine[eng]=i
        ei=i+1
        entry=float(y.open.iloc[ei])
        peak=-1e9
        armed=False
        xi=n-1
        reason="day_end"
        gross=side*(float(y.close.iloc[xi])/entry-1)*1e4

        for j in range(ei,n):
            hi=float(y.high.iloc[j]); lo=float(y.low.iloc[j]); close=float(y.close.iloc[j])
            # pessimistic stop-first handling
            stop_hit=(lo<=entry*(1-HARD_STOP_BPS/1e4)) if side==1 else (hi>=entry*(1+HARD_STOP_BPS/1e4))
            if stop_hit:
                xi=j; gross=-HARD_STOP_BPS; reason="hard_stop"; break

            fav=((hi/entry-1)*1e4) if side==1 else ((entry/lo-1)*1e4)
            peak=max(peak,fav)
            if peak>=PROFIT_ARM_BPS:
                armed=True

            cur=side*(close/entry-1)*1e4
            if armed:
                # Winner can run, but never realize less than fee-covered positive net.
                trail_floor=max(PROFIT_ARM_BPS, peak-TRAIL_BPS)
                if cur<=trail_floor:
                    xi=j
                    gross=max(trail_floor, ROUNDTRIP_COST_BPS+0.1)
                    reason="fee_covered_trail"
                    break
            else:
                # If <=0 and stale for 30m, allow time exit. Positive-but-below-fee stays open.
                if j-ei>=MAX_HOLD_SEC and cur<=0:
                    xi=j; gross=cur; reason="stale_nonpositive"; break

        net_bps=gross-ROUNDTRIP_COST_BPS
        before=equity
        equity=equity*(1+net_bps/1e4)
        rows.append({
            "signal_ts":str(idx[i]),"entry_ts":str(idx[ei]),"exit_ts":str(idx[xi]),
            "engine":eng,"side":side,"gross_bps":gross,"net_bps":net_bps,
            "reason":reason,"equity_before":before,"equity_after":equity
        })
        i=xi+2
    return pd.DataFrame(rows), equity

def metrics(t,final_equity):
    if t.empty:
        return {"trades":0,"wins":0,"losses":0,"win_pct":None,"pf":None,
                "gross_pnl_usd":0.0,"net_pnl_usd":0.0,"final_equity":final_equity,
                "return_pct":0.0,"avg_net_bps":None}
    a=t.net_bps.to_numpy()
    wins=a[a>0]; losses=a[a<0]
    pf=float(wins.sum()/abs(losses.sum())) if len(losses) else (999.0 if len(wins) else None)
    return {
        "trades":int(len(t)),"wins":int((a>0).sum()),"losses":int((a<0).sum()),
        "win_pct":float((a>0).mean()*100),"pf":pf,
        "avg_net_bps":float(a.mean()),"median_net_bps":float(np.median(a)),
        "net_pnl_usd":float(final_equity-START_EQUITY),
        "final_equity":float(final_equity),
        "return_pct":float((final_equity/START_EQUITY-1)*100),
        "jun_trades":int((t.engine=="jun").sum()),
        "hansan_trades":int((t.engine=="hansan").sum()),
        "stops":int((t.reason=="hard_stop").sum()),
        "fee_covered_trails":int((t.reason=="fee_covered_trail").sum()),
        "stale_nonpositive":int((t.reason=="stale_nonpositive").sum()),
        "day_end":int((t.reason=="day_end").sum())
    }

def main():
    raw=v19.fetch_day(DAY)
    y=v19.features(raw)
    s=build_events(y)
    t,final_equity=simulate(y,s)
    out={
        "version":"quantbot-v24-sep06-japan-fee-lock",
        "purpose":"ONE_DAY_24H_HISTORICAL_PAPER_TEST_NO_LIVE",
        "day":DAY,"symbol":"BTCUSDT","starting_equity_usd":START_EQUITY,
        "notional_model":"1x current equity, one position at a time, compounded",
        "roundtrip_cost_bps":ROUNDTRIP_COST_BPS,
        "profit_exit_rule":"No profitable exit before gross profit >= 8.5bps; then 2.5bps trailing lock",
        "stop_bps":HARD_STOP_BPS,
        "method_map":{
            "jun":"public distortion/snapback event logic",
            "hansan":"public breakout/acceleration event logic",
            "testa":"historical tape activity/EV proxy only; no claim of private method or true historical L2"
        },
        "summary":metrics(t,final_equity),
        "trades":t.to_dict(orient="records"),
        "live_orders":False
    }
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2))

if __name__=="__main__":
    main()
