"""Forward-only paper tracker for the frozen QuantBot v3 trend winner.

No credentials, no exchange orders, no live-money execution. The rule is frozen as
E_blend_top3_btc200_gate. Forward evidence starts 2026-09-08 UTC; only fully
completed daily bars are used. The signal at close t is applied to the next bar,
matching the research engine convention.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import research_v3_independent as v3

OUT = Path("data/v3_paper_trend.json")
RULE = "E_blend_top3_btc200_gate"
PAPER_START = pd.Timestamp("2026-09-08", tz="UTC")


def current_weights(pos: pd.DataFrame, dt: pd.Timestamp) -> dict[str, float]:
    row = pos.loc[dt]
    return {k: float(v) for k, v in row.items() if abs(float(v)) > 1e-12}


def main():
    px, errors = v3.load_prices()
    cutoff = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    px = px.loc[px.index < cutoff]
    if px.empty:
        raise RuntimeError("no completed daily bars")

    pos = v3.candidates(px)[RULE]
    last = px.index.max()
    weights = current_weights(pos, last)

    base = v3.slice_metrics(px, pos, str(PAPER_START.date()), None, v3.BASE_COST)
    stress = v3.slice_metrics(px, pos, str(PAPER_START.date()), None, v3.STRESS_COST)

    btc = px["BTCUSDT"]
    sma200 = btc.rolling(200).mean()
    gross = float(sum(abs(x) for x in weights.values()))

    state = {
        "version": "quantbot-v3-forward-paper-trend",
        "updated": datetime.now(timezone.utc).isoformat(),
        "paper_start": str(PAPER_START),
        "rule_frozen": RULE,
        "execution_rule": "signal at completed UTC close t; target position applies to next daily bar",
        "live_orders": False,
        "data_end": str(last),
        "universe_frozen": list(v3.UNIVERSE),
        "asset_errors": errors,
        "current_signal": {
            "signal_date": str(last),
            "effective_next_bar": True,
            "weights": weights,
            "gross_exposure": gross,
            "btc_close": float(btc.loc[last]),
            "btc_sma200": float(sma200.loc[last]) if pd.notna(sma200.loc[last]) else None,
            "btc_market_gate": bool(btc.loc[last] > sma200.loc[last]) if pd.notna(sma200.loc[last]) else False,
        },
        "forward_metrics_13bps": base,
        "forward_metrics_40bps": stress,
        "research_status": "PAPER_ONLY_NO_LIVE_MONEY",
        "promotion_guardrail": "Do not promote to live money from this tracker until a separately defined forward gate has enough active observations and passes cost stress.",
    }
    OUT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
