"""DARA Astro-First day-bias policy.

Purpose
-------
Before intraday 1m/5m/15m setup selection, estimate a research-only daily
LONG/SHORT/NEUTRAL bias from the 11-factor geocentric astro grid.  Astro never
opens a trade by itself.  It only changes directional preference, entry
strictness and per-trade risk *within the existing risk cap*.

The policy samples the coming Tehran day at 00:00, 06:00, 12:00 and 18:00 local
so a forming/exact aspect during the day is not missed by a single midnight
snapshot.

Risk invariant
--------------
- Existing hard risk cap remains 0.25% equity per full-stop trade.
- Existing daily stop remains external and unchanged (currently -1%).
- Opposing-side risk is REDUCED; astro never raises risk above the old cap.
- Normal positive exits must still respect the existing >=3x fee floor.

Scientific status
-----------------
These are exploratory recurrent correlations from BTCUSDT Futures 2022-01-01
through 2026-08-31 split into train/validation/OOS.  They do not prove
astrology or causality.  They are contextual features only.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple

from dara_astro_fullgrid_features import snapshot

TEHRAN = ZoneInfo("Asia/Tehran")

# Directional recurrent candidates discovered in the full-grid BTC screen.
# weight is intentionally conservative.  Positive = LONG bias, negative = SHORT.
# Evidence values are documented for auditability; they are not used as causal claims.
DIRECTION_RULES = {
    "ASPECT:Mars-Jupiter:SQUARE": dict(side=+1, weight=1.00, samples=(28,12,10), edges=(0.0062695,0.0041662,0.0064772), persistence=8.332),
    "ASPECT:Moon-Saturn:OPPOSITION": dict(side=-1, weight=1.00, samples=(22,6,5), edges=(-0.0094218,-0.0149546,-0.0308706), persistence=18.844),
    "ASPECT:Sun-TrueNode:SQUARE": dict(side=-1, weight=0.82, samples=(36,10,6), edges=(-0.0049367,-0.0052676,-0.0040512), persistence=8.102),
    "ASPECT:Mars-Pluto:SQUARE": dict(side=-1, weight=0.78, samples=(24,9,8), edges=(-0.0055432,-0.0032723,-0.0066176), persistence=6.545),
    "ASPECT:Venus-Pluto:SQUARE": dict(side=+1, weight=0.58, samples=(30,11,5), edges=(0.0079920,0.0031888,0.0024576), persistence=4.915),
    "ASPECT:Venus-Neptune:TRINE": dict(side=-1, weight=0.55, samples=(30,10,5), edges=(-0.0051874,-0.0022751,-0.0065087), persistence=4.550),
    "SIGN:Venus:CANCER": dict(side=-1, weight=0.52, samples=(79,26,25), edges=(-0.0021649,-0.0024836,-0.0072247), persistence=4.330),
    "SIGN:Mercury:GEMINI": dict(side=-1, weight=0.50, samples=(74,14,15), edges=(-0.0032113,-0.0021398,-0.0036900), persistence=4.280),
    "ASPECT:Sun-Pluto:TRINE": dict(side=-1, weight=0.50, samples=(38,12,6), edges=(-0.0020957,-0.0048947,-0.0070740), persistence=4.191),
    "ASPECT:Mars-TrueNode:SQUARE": dict(side=+1, weight=0.46, samples=(35,7,7), edges=(0.0017571,0.0076678,0.0061344), persistence=3.514),
    "ASPECT:Sun-TrueNode:SEXTILE": dict(side=+1, weight=0.42, samples=(35,14,8), edges=(0.0033824,0.0068936,0.0017239), persistence=3.448),
    "ASPECT:Venus-TrueNode:SEXTILE": dict(side=+1, weight=0.40, samples=(33,6,8), edges=(0.0028740,0.0014215,0.0119874), persistence=2.843),
    "ASPECT:Moon-Mercury:TRINE": dict(side=-1, weight=0.35, samples=(37,8,6), edges=(-0.0010699,-0.0037982,-0.0112450), persistence=2.140),
    "ASPECT:Sun-Jupiter:SQUARE": dict(side=+1, weight=0.30, samples=(33,13,6), edges=(0.0008438,0.0027395,0.0104634), persistence=1.688),
    "ASPECT:Moon-Uranus:SQUARE": dict(side=+1, weight=0.30, samples=(36,11,8), edges=(0.0008169,0.0067516,0.0124076), persistence=1.634),
    "ASPECT:Sun-TrueNode:CONJ": dict(side=+1, weight=0.28, samples=(19,6,6), edges=(0.0035591,0.0007882,0.0127064), persistence=1.576),
}

# Activity/volatility context.  This does NOT choose LONG vs SHORT.
ACTIVITY_RULES = {
    "ASPECT:Sun-Venus:CONJ": -1.00,          # recurrent lower abs-return / volume
    "ASPECT:Venus-Saturn:CONJ": +0.95,      # recurrent higher abs-return
    "ASPECT:Moon-Jupiter:SQUARE": +0.80,     # recurrent higher abs-return / volume
    "ASPECT:Moon-Venus:OPPOSITION": +0.75,   # recurrent higher volume / buy activity
    "ASPECT:Mars-Pluto:SEXTILE": +0.55,      # recurrent higher abs-return
    "ASPECT:Sun-Saturn:CONJ": +0.55,         # recurrent higher abs-return
    "SIGN:Mercury:VIRGO": -0.55,             # recurrent lower abs-return
    "SIGN:Mercury:ARIES": -0.50,             # recurrent lower volume
}

@dataclass
class DayBias:
    tehran_date: str
    bias: str
    score: float
    confidence: str
    long_preference: float
    short_preference: float
    aligned_risk_pct: float
    opposed_risk_pct: float
    aligned_entry_threshold_delta: float
    opposed_entry_threshold_delta: float
    activity_score: float
    activity_regime: str
    active_directional_rules: List[dict]
    active_activity_rules: List[dict]
    sample_times_tehran: List[str]


def _condition_strengths(day: date) -> Tuple[Dict[str, float], List[str]]:
    """Return max within-day strength for active conditions."""
    strengths: Dict[str, float] = {}
    sample_times = []
    for hh in (0, 6, 12, 18):
        local = datetime.combine(day, time(hh, 0), tzinfo=TEHRAN)
        sample_times.append(local.isoformat())
        s = snapshot(local.astimezone(timezone.utc), orb=3.0)

        # Signs are treated as fully active at the sample.
        for body, p in s["positions"].items():
            key = f"SIGN:{body}:{p['sign']}"
            strengths[key] = 1.0

        # Aspects use their exactness strength; keep max over the four samples.
        for a in s["major_aspects"]:
            key = f"ASPECT:{a['pair']}:{a['aspect']}"
            strengths[key] = max(strengths.get(key, 0.0), float(a.get("strength", 0.0)))
    return strengths, sample_times


def _normalize_signed(x: float, denom: float) -> float:
    if denom <= 0:
        return 0.0
    z = x / denom
    return max(-1.0, min(1.0, z))


def day_bias(day: date) -> DayBias:
    strengths, sample_times = _condition_strengths(day)

    raw = 0.0
    possible = 0.0
    directional_hits = []
    for cond, meta in DIRECTION_RULES.items():
        st = strengths.get(cond, 0.0)
        if st <= 0:
            continue
        contribution = meta["side"] * meta["weight"] * st
        raw += contribution
        possible += abs(meta["weight"] * st)
        directional_hits.append({
            "condition": cond,
            "strength": round(st, 4),
            "side": "LONG" if meta["side"] > 0 else "SHORT",
            "weight": meta["weight"],
            "contribution": round(contribution, 4),
            "samples": meta["samples"],
            "edges": meta["edges"],
            "persistence": meta["persistence"],
        })

    score = _normalize_signed(raw, possible)
    abs_score = abs(score)
    if score >= 0.45:
        bias, conf = "LONG", "STRONG"
        lp, sp = 0.75, 0.25
        aligned_risk, opposed_risk = 0.25, 0.10
        aligned_delta, opposed_delta = -0.08, +0.14
    elif score >= 0.20:
        bias, conf = "LONG", "MODERATE"
        lp, sp = 0.65, 0.35
        aligned_risk, opposed_risk = 0.23, 0.14
        aligned_delta, opposed_delta = -0.04, +0.08
    elif score <= -0.45:
        bias, conf = "SHORT", "STRONG"
        lp, sp = 0.25, 0.75
        aligned_risk, opposed_risk = 0.25, 0.10
        aligned_delta, opposed_delta = -0.08, +0.14
    elif score <= -0.20:
        bias, conf = "SHORT", "MODERATE"
        lp, sp = 0.35, 0.65
        aligned_risk, opposed_risk = 0.23, 0.14
        aligned_delta, opposed_delta = -0.04, +0.08
    else:
        bias, conf = "NEUTRAL", "LOW"
        lp, sp = 0.50, 0.50
        aligned_risk = opposed_risk = 0.20
        aligned_delta = opposed_delta = 0.0

    activity_raw = 0.0
    activity_possible = 0.0
    activity_hits = []
    for cond, w in ACTIVITY_RULES.items():
        st = strengths.get(cond, 0.0)
        if st <= 0:
            continue
        contribution = w * st
        activity_raw += contribution
        activity_possible += abs(w * st)
        activity_hits.append({"condition": cond, "strength": round(st,4), "weight": w, "contribution": round(contribution,4)})
    activity_score = _normalize_signed(activity_raw, activity_possible)
    if activity_score >= 0.35:
        activity_regime = "HIGH_ACTIVITY"
    elif activity_score <= -0.35:
        activity_regime = "LOW_ACTIVITY"
    else:
        activity_regime = "NORMAL_ACTIVITY"

    directional_hits.sort(key=lambda x: abs(x["contribution"]), reverse=True)
    activity_hits.sort(key=lambda x: abs(x["contribution"]), reverse=True)

    return DayBias(
        tehran_date=day.isoformat(),
        bias=bias,
        score=round(score, 4),
        confidence=conf,
        long_preference=lp,
        short_preference=sp,
        aligned_risk_pct=aligned_risk,
        opposed_risk_pct=opposed_risk,
        aligned_entry_threshold_delta=aligned_delta,
        opposed_entry_threshold_delta=opposed_delta,
        activity_score=round(activity_score, 4),
        activity_regime=activity_regime,
        active_directional_rules=directional_hits,
        active_activity_rules=activity_hits,
        sample_times_tehran=sample_times,
    )


def trade_policy_for_side(bias: DayBias, side: str) -> dict:
    """Map daily bias to intraday side-specific permissions.

    Does not create an entry.  The downstream 1m/5m/15m engine still needs a
    valid price/flow/pattern/prototype setup and fee-capacity confirmation.
    """
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if bias.bias == "NEUTRAL":
        aligned = None
        risk = 0.20
        threshold_delta = 0.0
        preference = 0.50
    else:
        aligned = side == bias.bias
        risk = bias.aligned_risk_pct if aligned else bias.opposed_risk_pct
        threshold_delta = bias.aligned_entry_threshold_delta if aligned else bias.opposed_entry_threshold_delta
        preference = bias.long_preference if side == "LONG" else bias.short_preference
    return {
        "side": side,
        "astro_aligned": aligned,
        "risk_pct_equity": risk,
        "entry_threshold_delta": threshold_delta,
        "directional_preference": preference,
        "must_still_pass_intraday_engine": True,
        "must_still_pass_fee_floor": True,
        "risk_cap_pct_equity": 0.25,
    }


def as_jsonable(day: date) -> dict:
    b = day_bias(day)
    d = asdict(b)
    d["long_policy"] = trade_policy_for_side(b, "LONG")
    d["short_policy"] = trade_policy_for_side(b, "SHORT")
    return d


if __name__ == "__main__":
    import json, sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(TEHRAN).date()
    print(json.dumps(as_jsonable(d), ensure_ascii=False, indent=2))
