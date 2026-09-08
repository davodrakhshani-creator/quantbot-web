"""Authoritative QuantBot v4 paper-execution core.

Architecture:
1) paper_v4_t2.py is the read-only public-data signal generator.
2) this core consumes that frozen signal state, validates it, and emits a paper
   rebalance plan.

This module intentionally contains NO authenticated exchange client and NO live-order
function. Live execution is structurally impossible here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pandas as pd

import research_v3_independent as v3

RULE = "T2_upup"
CONSENSUS = Path("data/v4_consensus.json")
SIGNAL_STATE = Path("data/v4_paper_t2.json")
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


def load_json(path: Path, label: str) -> dict:
    if not path.exists():
        raise RuntimeError(f"missing {label}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_consensus(c: dict) -> None:
    if c.get("official_research_core") != RULE:
        raise RuntimeError("consensus/core rule mismatch")
    if c.get("live_money_authorized") is not False:
        raise RuntimeError("paper core refuses a consensus that authorizes live money")
    if not c.get("governance", {}).get("do_not_place_live_orders", False):
        raise RuntimeError("consensus live-order guardrail missing")


def validate_signal_envelope(s: dict) -> list[str]:
    errors: list[str] = []
    if s.get("rule_frozen") != RULE:
        errors.append("signal/core rule mismatch")
    if s.get("live_orders") is not False:
        errors.append("signal state unexpectedly permits live orders")
    if s.get("frozen_before_first_forward_completed_bar") is not True:
        errors.append("forward freeze provenance missing")
    if s.get("asset_errors"):
        errors.append(f"asset data errors: {s['asset_errors']}")
    return errors


def main() -> None:
    consensus = load_json(CONSENSUS, "v4 consensus")
    signal = load_json(SIGNAL_STATE, "v4 T2 signal state")
    validate_consensus(consensus)

    health_errors = validate_signal_envelope(signal)
    sig = signal.get("current_signal", {})
    target = {str(k): float(v) for k, v in (sig.get("weights") or {}).items() if abs(float(v)) > 1e-12}
    validation = validate_target(target, set(v3.UNIVERSE))
    health_errors.extend(validation.errors)

    signal_date = pd.Timestamp(sig.get("signal_date"))
    if signal_date.tzinfo is None:
        signal_date = signal_date.tz_localize("UTC")
    else:
        signal_date = signal_date.tz_convert("UTC")
    today = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    stale_days = int((today - signal_date.normalize()).days)
    if stale_days > STALE_AFTER_DAYS:
        health_errors.append(f"signal stale by {stale_days} days")

    regime = bool(sig.get("btc_upup_gate", False))
    if not regime and target:
        health_errors.append("non-cash target while T2 BTC UP-UP gate is false")

    state = {
        "version": "quantbot-v4-paper-core",
        "updated": datetime.now(timezone.utc).isoformat(),
        "official_rule": RULE,
        "mode": "PAPER_ONLY",
        "live_order_capability": False,
        "signal_source": str(SIGNAL_STATE),
        "signal_date": str(signal_date),
        "effective_execution": "next bar / next-open convention",
        "target_weights": target,
        "gross_target": validation.gross,
        "paper_rebalance_from_cash": rebalance_plan({}, target),
        "regime": {
            "btc_close": sig.get("btc_close"),
            "btc_sma200": sig.get("btc_sma200"),
            "btc_mom60_pct": sig.get("btc_mom60_pct"),
            "btc_mom120_pct": sig.get("btc_mom120_pct"),
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
