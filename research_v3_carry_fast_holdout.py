"""Fast post-lock audit for the six already-frozen QuantBot v3 carry rules.

Only the minimum warm-up + holdout window is downloaded (2026-03-01..2026-08-31)
to avoid re-reading the entire 2020+ archive. Candidate definitions are imported
unchanged from research_v3_carry.py. This is diagnostic only and does not retune
or re-select on holdout data. No credentials and no live orders.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

import research_v3_carry as c

OUT = Path("data/v3_carry_family_holdout.json")
START = pd.Timestamp("2026-03-01", tz="UTC")
HOLD_START = "2026-04-25"
HOLD_END = "2026-08-31"
MONTHS = [(2026, m) for m in range(3, 9)]


def _zip_csv(url: str, header="infer"):
    r = requests.get(url, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0])
    return pd.read_csv(io.BytesIO(raw), header=header)


def spot(sym: str) -> pd.Series:
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(pd.Timestamp("2026-09-01", tz="UTC").timestamp() * 1000) - 1
    urls = [
        "https://data-api.binance.vision/api/v3/klines",
        "https://api.binance.com/api/v3/klines",
        "https://api1.binance.com/api/v3/klines",
    ]
    last = None
    for u in urls:
        try:
            r = requests.get(u, params={"symbol": sym, "interval": "1d", "startTime": start_ms, "endTime": end_ms, "limit": 1000}, timeout=20)
            r.raise_for_status()
            rows = r.json()
            if isinstance(rows, list) and rows:
                d = pd.DataFrame(rows)
                idx = pd.to_datetime(d[0].astype("int64"), unit="ms", utc=True).dt.floor("D")
                return pd.Series(pd.to_numeric(d[4], errors="coerce").to_numpy(), index=pd.DatetimeIndex(idx), name=sym).sort_index()
        except Exception as e:
            last = repr(e)
    raise RuntimeError(f"spot failed {sym}: {last}")


def perp(sym: str) -> pd.Series:
    pieces = []
    for y, m in MONTHS:
        url = f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1d/{sym}-1d-{y:04d}-{m:02d}.zip"
        try:
            d = _zip_csv(url, header=None)
            if d is None or d.empty:
                continue
            if not str(d.iloc[0, 0]).isdigit():
                d = d.iloc[1:]
            idx = pd.to_datetime(pd.to_numeric(d.iloc[:, 0]), unit="ms", utc=True).dt.floor("D")
            pieces.append(pd.Series(pd.to_numeric(d.iloc[:, 4], errors="coerce").to_numpy(), index=pd.DatetimeIndex(idx)))
        except Exception:
            continue
    if not pieces:
        raise RuntimeError(f"perp failed {sym}")
    s = pd.concat(pieces).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    s.name = sym
    return s


def funding(sym: str) -> pd.Series:
    pieces = []
    for y, m in MONTHS:
        url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{y:04d}-{m:02d}.zip"
        try:
            d = _zip_csv(url)
            if d is None or d.empty:
                continue
            cols = {str(x).lower(): x for x in d.columns}
            tc = cols.get("calc_time") or cols.get("fundingtime") or d.columns[0]
            rc = cols.get("last_funding_rate") or cols.get("fundingrate") or d.columns[-1]
            idx = pd.to_datetime(pd.to_numeric(d[tc]), unit="ms", utc=True).dt.floor("D")
            rate = pd.to_numeric(d[rc], errors="coerce")
            pieces.append(pd.Series(rate.to_numpy(), index=pd.DatetimeIndex(idx)))
        except Exception:
            continue
    if not pieces:
        raise RuntimeError(f"funding failed {sym}")
    s = pd.concat(pieces).groupby(level=0).sum().sort_index()
    s.name = sym
    return s


def load_fast():
    ss, pp, ff, errors = [], [], [], {}
    for sym in c.SYMS:
        try:
            s = spot(sym)
            p = perp(sym)
            f = funding(sym)
            ss.append(s); pp.append(p); ff.append(f)
        except Exception as e:
            errors[sym] = repr(e)
    if len(ss) < 5:
        raise RuntimeError(f"too few assets: {errors}")
    S = pd.concat(ss, axis=1)
    P = pd.concat(pp, axis=1)
    F = pd.concat(ff, axis=1)
    common = [x for x in S.columns if x in P.columns and x in F.columns]
    return S[common], P[common], F[common], errors


def main():
    spot_px, perp_px, fund, errors = load_fast()
    cands, s, p, f, basis, ann30 = c.candidates(spot_px, perp_px, fund)
    prior = json.loads(Path("data/v3_carry_state.json").read_text(encoding="utf-8"))

    results = {}
    for name, pos in cands.items():
        base = c.diag(s, p, f, pos, HOLD_START, HOLD_END, c.BASE_COST)
        stress = c.diag(s, p, f, pos, HOLD_START, HOLD_END, c.STRESS)
        hg = bool(prior.get("candidate_results", {}).get(name, {}).get("history_gate", False))
        hld = bool(c.hold_gate(base, stress))
        results[name] = {
            "history_gate": hg,
            "postlock": base,
            "postlock_stress40": stress,
            "hold_gate": hld,
            "both_gates": bool(hg and hld),
        }

    post = ann30.loc[(ann30.index >= pd.Timestamp(HOLD_START, tz="UTC")) & (ann30.index <= pd.Timestamp(HOLD_END, tz="UTC"))]
    state = {
        "version": "quantbot-v3-carry-family-fast-holdout",
        "updated": datetime.now(timezone.utc).isoformat(),
        "holdout": f"{HOLD_START}..{HOLD_END}",
        "warmup_start": str(START),
        "rule": "All six rules were frozen before holdout; no postlock retuning or reselection.",
        "selected_before_holdout": prior.get("selected"),
        "results": results,
        "family_summary": {
            "hold_gate_passers": [k for k, v in results.items() if v["hold_gate"]],
            "both_history_and_holdout_passers": [k for k, v in results.items() if v["both_gates"]],
            "active_days_by_candidate": {k: int(v["postlock"]["active_days"]) for k, v in results.items()},
            "max_30d_annualized_funding_postlock_pct": float(post.max().max() * 100) if not post.empty else 0.0,
            "postlock_days_any_asset_30d_funding_gt10pct": int((post > 0.10).any(axis=1).sum()) if not post.empty else 0,
            "postlock_days_any_asset_30d_funding_positive": int((post > 0).any(axis=1).sum()) if not post.empty else 0,
        },
        "guardrail": "Passing a non-selected rule is diagnostic family evidence only; it cannot be promoted from the already-viewed holdout without fresh forward evidence.",
        "asset_errors": errors,
    }
    OUT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state["family_summary"], indent=2))


if __name__ == "__main__":
    main()
