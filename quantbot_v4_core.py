"""Authoritative QuantBot v4 paper-execution core.

This module intentionally contains NO authenticated exchange client and NO live-order
function. It turns the frozen T2 research rule into a validated target portfolio and
an auditable paper rebalance plan. Live execution is structurally impossible here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pandas as pd

import research_v3_independent as v3
import research_v4_tournament as v4

RULE = "T2_upup"
CONSENSUS = Path("data/v4_consensus.json")
OUT = Path("data/v4_core_state.json")
MAX_GROSS = 1.0
STALE_AFTER_DAYS = 2


@dataclass(frozen=True)
class Validation:
    ok: bool
    gross: float
    errors: tuple[str, ...]


def validate_target(weights: Mapping[str, float], allowed: set[str], max_gross: float = MAX_GROSS) -> Validation:
    errors: list[str] = []
    clean = {str(k): float(v) for k, v in weights.items() if abs(float(v)) > 1e-12}
    unknown = sorted(set(clean) - allowed)
    if unknown:
        errors.append(f"unknown assets: {unknown}")
    if any(v < -1e-12 for v in clean.values()):
        errors.append("T2 is long/cash only; negative target detected")
    if any(not pd.notna(v) for v in clean.values()):
        errors.append("non-finite target weight")
    gross = float(sum(abs(v) for v in clean.values()))
    if gross > max_gross + 1e-9:
        errors.append(f"gross {gross:.6f} exceeds {max_gross:.6f}")
    return Validation(not errors, gross, tuple(errors))


def rebalance_plan(current: Mapping[str, float], target: Mapping[str, float], min_delta: float = 1e-6) -> list[dict]:
    """Return paper target deltas. Positive=buy fraction, negative=sell fraction."""
    names = sorted(set(current) | set(target))
    out = []
    for sym in names:
        cur = float(current.get(sym, 0.0))
        tar = float(target.get(sym, 0.0))
        delta = tar - cur
        if abs(delta) >= min_delta:
            out.append({"symbol": sym, "current_weight": cur, "target_weight": tar, "delta_weight": delta})
    return out


def load_consensus() -> dict:
    if not CONSENSUS.exists():
        raise RuntimeError("missing v4 consensus")
    c = json.loads(CONSENSUS.read_text(encoding="utf-8"))
    if c.get("official_research_core") != RULE:
        raise RuntimeError("consensus/core rule mismatch")
    if c.get("live_money_authorized") is not False:
        raise RuntimeError("paper core refuses a consensus that authorizes live money")
    return c


def completed_prices() -> tuple[pd.DataFrame, dict]:
    px, errors = v3.load_prices()
    cutoff = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    px = px.loc[px.index < cutoff]
    if px.empty:
        raise RuntimeError("no completed UTC daily bars")
    return px, errors


def main() -> None:
    consensus = load_consensus()
    px, data_errors = completed_prices()
    pos = v4.build(px)[RULE]
    last = px.index.max()
    row = pos.loc[last].fillna(0.0)
    target = {k: float(v) for k, v in row.items() if abs(float(v)) > 1e-12}
    validation = validate_target(target, set(v3.UNIVERSE))

    today = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    stale_days = int((today - last.normalize()).days)
    health_errors = list(validation.errors)
    if stale_days > STALE_AFTER_DAYS:
        health_errors.append(f"market data stale by {stale_days} days")
    if data_errors:
        health_errors.append(f"asset data errors: {data_errors}")

    btc = px["BTCUSDT"]
    sma200 = btc.rolling(200).mean()
    mom60 = btc / btc.shift(60) - 1
    mom120 = btc / btc.shift(120) - 1
    regime = bool(btc.loc[last] > sma200.loc[last] and mom60.loc[last] > 0 and mom120.loc[last] > 0)
    if not regime and target:
        health_errors.append("non-cash target while T2 BTC UP-UP gate is false")

    state = {
        "version": "quantbot-v4-paper-core",
        "updated": datetime.now(timezone.utc).isoformat(),
        "official_rule": RULE,
        "mode": "PAPER_ONLY",
        "live_order_capability": False,
        "signal_date": str(last),
        "effective_execution": "next bar / next-open convention",
        "target_weights": target,
        "gross_target": validation.gross,
        "paper_rebalance_from_cash": rebalance_plan({}, target),
        "regime": {
            "btc_close": float(btc.loc[last]),
            "btc_sma200": float(sma200.loc[last]),
            "btc_mom60_pct": float(mom60.loc[last] * 100),
            "btc_mom120_pct": float(mom120.loc[last] * 100),
            "upup_gate": regime,
        },
        "health": {
            "ok": not health_errors,
            "stale_days": stale_days,
            "errors": health_errors,
        },
        "consensus_status": consensus["status"],
        "forward_gate": consensus["fresh_forward"]["promotion_gate"],
        "guardrail": "No API keys, authenticated exchange client, or live-order function exists in this core.",
    }
    OUT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if health_errors:
        raise RuntimeError("; ".join(health_errors))
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
