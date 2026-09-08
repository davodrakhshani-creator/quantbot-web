import json
from pathlib import Path
import research_v19_japan_tick_clone as v19

DAY="2026-09-07"
OUT=Path("data/v26_sep07_24h_replay.json")

def main():
    y=v19.features(v19.fetch_day(DAY))
    s=v19.signal_rows(y)
    t=v19.simulate(y,s)
    m=v19.metrics(t)
    start=100.0
    # v19 metrics uses $100 notional per trade, no compounding/leverage.
    end=start+m["net_usd_100"]
    gross_bps=float(t.gross_bps.sum()) if len(t) else 0.0
    net_bps=float(t.net_bps.sum()) if len(t) else 0.0
    engines={}
    for eng in ["jun","hansan"]:
        q=t[t.engine==eng] if len(t) else t
        engines[eng]=v19.metrics(q)
    out={
      "version":"v26-fixed-24h-replay",
      "date_utc":DAY,
      "symbol":"BTCUSDT USD-M perpetual",
      "method":"fixed v19 public-method Japanese tick clone: Jun distortion/snapback + Hansan breakout/acceleration + Testa tape-activity proxy",
      "important_limit":"Latest v23 uses live L2 and cannot be honestly reconstructed from aggTrades alone; this is a historical tick/tape proxy, not v23 L2.",
      "cost_bps_roundtrip":v19.COST_BPS,
      "starting_equity_usd":start,
      "ending_equity_usd":end,
      "pnl_usd":m["net_usd_100"],
      "return_pct":m["net_usd_100"],
      "gross_total_bps":gross_bps,
      "net_total_bps":net_bps,
      "all":m,
      "engines":engines,
      "trades":t.to_dict(orient="records") if len(t)<=500 else t.head(500).to_dict(orient="records"),
      "live_orders":False
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out,indent=2))
    print(json.dumps({k:v for k,v in out.items() if k!="trades"},indent=2))
if __name__=="__main__": main()
