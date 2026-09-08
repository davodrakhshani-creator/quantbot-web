"""Diagnostic audit of every pre-registered QuantBot v3 carry candidate.

This file does NOT re-select a strategy using the post-lock period. It evaluates
all six rules that were already frozen before the 2026-04-25 carry holdout, so we
can tell whether the selected C5 rule went inactive specifically or whether the
whole carry family lost opportunity. No live orders.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import research_v3_carry as c

OUT = Path("data/v3_carry_family_holdout.json")
LOCK_START = "2026-04-25"


def main():
    spot, perp, fund, errors = c.load()
    cands, s, p, f, basis, ann30 = c.candidates(spot, perp, fund)

    prior = json.loads(Path("data/v3_carry_state.json").read_text(encoding="utf-8"))
    results = {}
    for name, pos in cands.items():
        base = c.diag(s, p, f, pos, LOCK_START, None, c.BASE_COST)
        stress = c.diag(s, p, f, pos, LOCK_START, None, c.STRESS)
        hist_gate = bool(prior.get("candidate_results", {}).get(name, {}).get("history_gate", False))
        hold_gate = bool(c.hold_gate(base, stress))
        results[name] = {
            "history_gate": hist_gate,
            "postlock": base,
            "postlock_stress40": stress,
            "hold_gate": hold_gate,
            "both_gates": bool(hist_gate and hold_gate),
        }

    post = ann30.loc[ann30.index >= pd.Timestamp(LOCK_START, tz="UTC")]
    max_ann = float(post.max().max()) if not post.empty else 0.0
    any_gt10_days = int((post > 0.10).any(axis=1).sum()) if not post.empty else 0
    any_positive_days = int((post > 0).any(axis=1).sum()) if not post.empty else 0

    both = [k for k, v in results.items() if v["both_gates"]]
    hold_only = [k for k, v in results.items() if v["hold_gate"]]
    active = {k: int(v["postlock"]["active_days"]) for k, v in results.items()}

    state = {
        "version": "quantbot-v3-carry-family-holdout-audit",
        "updated": datetime.now(timezone.utc).isoformat(),
        "rule": "Diagnostic only: evaluate all six pre-registered candidates on the same post-lock period; do not re-select based on holdout.",
        "lock_start": LOCK_START,
        "selected_before_holdout": prior.get("selected"),
        "results": results,
        "family_summary": {
            "hold_gate_passers": hold_only,
            "both_history_and_holdout_passers": both,
            "active_days_by_candidate": active,
            "max_30d_annualized_funding_postlock_pct": max_ann * 100.0,
            "postlock_days_any_asset_30d_funding_gt10pct": any_gt10_days,
            "postlock_days_any_asset_30d_funding_positive": any_positive_days,
        },
        "interpretation_guardrail": "A non-selected candidate passing post-lock is diagnostic evidence only, not permission to promote it after seeing holdout. Promotion still requires fresh forward paper-trading or a new untouched dataset.",
        "sources": errors,
    }
    OUT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state["family_summary"], indent=2))


if __name__ == "__main__":
    main()
