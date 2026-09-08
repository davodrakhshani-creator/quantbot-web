from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SYMBOL = "BTCUSDT"
START = pd.Timestamp("2023-01-01", tz="UTC")
END = pd.Timestamp("2026-08-31 23:59:59", tz="UTC")
SELECTION_END = pd.Timestamp("2026-04-24 23:59:59", tz="UTC")
HOLDOUT_START = pd.Timestamp("2026-04-25", tz="UTC")
STATE = Path("data/v9_sparse_acceptance_state.json")
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
COST_GRID = (7.0, 12.0, 20.0)  # one-way bps; charged on entry and exit


@dataclass(frozen=True)
class Spec:
    name: str
    mode: str
    flow_z: float
    vol_z: float
    breakout: int
    stop_atr: float
    take_atr: float
    max_hold: int
    cci_gate: float


SPECS = [
    Spec("A0_sparse12", "acceptance", 1.50, 0.75, 12, 1.05, 2.20, 18, 60),
    Spec("A1_sparse24", "acceptance", 1.50, 0.75, 24, 1.15, 2.50, 24, 60),
    Spec("A2_extreme12", "acceptance", 2.00, 1.00, 12, 1.00, 2.30, 18, 70),
    Spec("A3_extreme24", "acceptance", 2.00, 1.00, 24, 1.10, 2.60, 24, 70),
    Spec("A4_persist12", "acceptance", 1.25, 0.60, 12, 1.10, 2.40, 24, 55),
    Spec("A5_persist24", "acceptance", 1.25, 0.60, 24, 1.20, 2.70, 30, 55),
]


def month_iter(start: pd.Timestamp, end: pd.Timestamp):
    cur = pd.Timestamp(start.year, start.month, 1, tz="UTC")
    last = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while cur <= last:
        yield cur
        cur += pd.offsets.MonthBegin(1)


def fetch_month(m: pd.Timestamp) -> pd.DataFrame:
    ym = m.strftime("%Y-%m")
    url = f"{BASE}/{SYMBOL}/5m/{SYMBOL}-5m-{ym}.zip"
    r = requests.get(url, timeout=45)
    if r.status_code == 404:
        return pd.DataFrame()
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        raw = pd.read_csv(z.open(name), header=None)
    # Some archive vintages contain a header row; numeric coercion below removes it.
    cols = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"]
    raw = raw.iloc[:, :12]
    raw.columns = cols
    for c in ["open_time","open","high","low","close","volume","taker_base"]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw = raw.dropna(subset=["open_time","open","high","low","close","volume","taker_base"])
    raw["ts"] = pd.to_datetime(raw.open_time, unit="ms", utc=True)
    return raw.set_index("ts")[["open","high","low","close","volume","taker_base"]].sort_index()


def load_data() -> pd.DataFrame:
    parts = []
    for m in month_iter(START, END):
        x = fetch_month(m)
        if not x.empty:
            parts.append(x)
    if not parts:
        raise RuntimeError("No Binance Vision futures data loaded")
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[(df.index >= START) & (df.index <= END)]


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([(df.high-df.low).abs(), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def cci(df: pd.DataFrame, n=20) -> pd.Series:
    tp = (df.high + df.low + df.close) / 3
    ma = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def anchored_vwap(df: pd.DataFrame) -> pd.Series:
    typ = (df.high + df.low + df.close) / 3
    day = df.index.floor("D")
    pv = typ * df.volume
    return pv.groupby(day).cumsum() / df.volume.groupby(day).cumsum().replace(0, np.nan)


def base_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["atr"] = atr(x)
    x["ret1"] = x.close.pct_change()
    x["range"] = (x.high - x.low).replace(0, np.nan)
    x["upper_wick"] = (x.high - x[["open","close"]].max(axis=1)) / x["range"]
    x["lower_wick"] = (x[["open","close"]].min(axis=1) - x.low) / x["range"]
    x["vwap"] = anchored_vwap(x)
    x["flow"] = ((2.0 * x.taker_base - x.volume) / x.volume.replace(0, np.nan)).clip(-1, 1)
    w = 288  # one day of 5m bars
    fmu = x.flow.rolling(w, min_periods=96).mean().shift(1)
    fsd = x.flow.rolling(w, min_periods=96).std().shift(1).replace(0, np.nan)
    x["flow_z"] = (x.flow - fmu) / fsd
    lv = np.log1p(x.volume)
    vmu = lv.rolling(w, min_periods=96).mean().shift(1)
    vsd = lv.rolling(w, min_periods=96).std().shift(1).replace(0, np.nan)
    x["vol_z"] = (lv - vmu) / vsd

    q = x.resample("15min", label="right", closed="right").agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum"
    }).dropna()
    q["ef"] = q.close.ewm(span=20, adjust=False).mean()
    q["es"] = q.close.ewm(span=50, adjust=False).mean()
    q["cci"] = cci(q, 20)
    q["slope"] = q.es - q.es.shift(4)
    reg = q[["close","ef","es","cci","slope"]].reindex(x.index, method="ffill")
    x[["qclose","qef","qes","qcci","qslope"]] = reg.to_numpy()
    return x


def add_signals(x: pd.DataFrame, s: Spec) -> pd.DataFrame:
    y = x.copy()
    y["hi_break"] = y.high.shift(1).rolling(s.breakout, min_periods=s.breakout).max()
    y["lo_break"] = y.low.shift(1).rolling(s.breakout, min_periods=s.breakout).min()
    long_reg = (y.qclose > y.qef) & (y.qef > y.qes) & (y.qslope > 0) & (y.qcci > s.cci_gate)
    short_reg = (y.qclose < y.qef) & (y.qef < y.qes) & (y.qslope < 0) & (y.qcci < -s.cci_gate)

    flow_buy = y.flow_z >= s.flow_z
    flow_sell = y.flow_z <= -s.flow_z
    # At least two of the latest three completed 5m bars must show aligned aggressive flow.
    buy_persist = (flow_buy.astype(int) + flow_buy.shift(1).fillna(False).astype(int) + flow_buy.shift(2).fillna(False).astype(int)) >= 2
    sell_persist = (flow_sell.astype(int) + flow_sell.shift(1).fillna(False).astype(int) + flow_sell.shift(2).fillna(False).astype(int)) >= 2
    vol_ok = y.vol_z >= s.vol_z

    body_frac = (y.close - y.open).abs() / y["range"]
    close_loc = (y.close - y.low) / y["range"]
    range_med = y["range"].rolling(48, min_periods=24).median().shift(1)
    expansion = y["range"] >= 1.10 * range_med

    # Sparse acceptance: do not trade merely because flow is extreme. Require the market
    # to accept the direction via local breakout, range expansion, strong close location,
    # VWAP alignment, and 15m regime agreement.
    y["long_sig"] = (
        long_reg & buy_persist & vol_ok & expansion &
        (y.close > y.hi_break) & (y.close > y.vwap) &
        (body_frac >= 0.55) & (close_loc >= 0.72)
    )
    y["short_sig"] = (
        short_reg & sell_persist & vol_ok & expansion &
        (y.close < y.lo_break) & (y.close < y.vwap) &
        (body_frac >= 0.55) & (close_loc <= 0.28)
    )
    return y

def simulate(x: pd.DataFrame, s: Spec, cost_bps: float, risk_frac: float=0.0025, leverage_cap: float=2.0) -> dict:
    y = add_signals(x, s)
    eq = peak = 1.0
    max_dd = 0.0
    pos = 0
    entry = stop = target = np.nan
    notional = 0.0
    entry_i = -1
    pending = 0
    pending_atr = np.nan
    cooldown = 0
    trades = []

    def close_trade(i: int, px: float, reason: str):
        nonlocal eq, peak, max_dd, pos, entry, stop, target, notional, entry_i, cooldown
        gross = pos * (px / entry - 1.0) * notional        exit_cost = (cost_bps / 10000.0) * notional
        ret = gross - exit_cost
        eq *= (1 + ret)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq / peak - 1)
        trades.append({"side": pos, "ret": ret, "reason": reason, "entry_i": entry_i, "exit_i": i})
        pos = 0; entry = stop = target = np.nan; notional = 0.0; entry_i = -1; cooldown = 2

    start_i = 600
    for i in range(start_i, len(y)-1):
        r = y.iloc[i]
        nxt = y.iloc[i+1]
        if cooldown > 0:
            cooldown -= 1

        if pos != 0:
            stop_hit = (pos > 0 and r.low <= stop) or (pos < 0 and r.high >= stop)
            tgt_hit = (pos > 0 and r.high >= target) or (pos < 0 and r.low <= target)
            held = i - entry_i
            if stop_hit:  # pessimistic if both touched in same bar
                close_trade(i, float(stop), "stop"); continue
            if tgt_hit:
                close_trade(i, float(target), "target"); continue
            if held >= s.max_hold:
                close_trade(i+1, float(nxt.open), "time"); continue

        if pos == 0 and pending != 0:
            px = float(r.open)
            if np.isfinite(pending_atr) and pending_atr > 0:
                pos = pending
                entry = px
                dist = s.stop_atr * pending_atr
                stop = entry - pos * dist
                target = entry + pos * s.take_atr * pending_atr
                stop_pct = dist / entry
                notional = min(leverage_cap, risk_frac / max(stop_pct, 1e-6))
                eq *= (1 - (cost_bps / 10000.0) * notional)
                peak = max(peak, eq); max_dd = min(max_dd, eq/peak - 1)
                entry_i = i
            pending = 0; pending_atr = np.nan
            continue

        if pos == 0 and cooldown == 0 and np.isfinite(r.atr) and r.atr > 0:
            ls, ss = bool(r.long_sig), bool(r.short_sig)
            if ls ^ ss:
                pending = 1 if ls else -1
                pending_atr = float(r.atr)

    if pos != 0:
        close_trade(len(y)-1, float(y.close.iloc[-1]), "end")

    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades":0,"net_pct":0,"pf":0,"win_pct":0,"max_dd_pct":0,"avg_trade_bps":0,"longs":0,"shorts":0,"stop_pct":0,"target_pct":0}
    gains = t.loc[t.ret > 0, "ret"].sum()
    losses = -t.loc[t.ret < 0, "ret"].sum()
    pf = float(gains / losses) if losses > 0 else 99.0
    return {
        "trades": int(len(t)),
        "net_pct": float((eq - 1) * 100),
        "pf": pf,
        "win_pct": float((t.ret > 0).mean() * 100),
        "max_dd_pct": float(-max_dd * 100),
        "avg_trade_bps": float(t.ret.mean() * 10000),
        "longs": int((t.side > 0).sum()),
        "shorts": int((t.side < 0).sum()),
        "stop_pct": float((t.reason == "stop").mean() * 100),
        "target_pct": float((t.reason == "target").mean() * 100),
    }


def train_score(m: dict) -> float:
    if m["trades"] < 100 or m["net_pct"] <= 0 or m["pf"] < 1.12 or m["max_dd_pct"] > 18:
        return -1e9
    return m["net_pct"] + 30*(m["pf"]-1) + .15*m["win_pct"] - .8*m["max_dd_pct"]


def main():
    raw = load_data()
    feat = base_features(raw)
    train = feat[feat.index <= SELECTION_END]
    hold = feat[feat.index >= HOLDOUT_START]

    candidates = {}
    ranked = []
    for s in SPECS:
        tr = {str(c): simulate(train, s, c) for c in COST_GRID}
        score = train_score(tr["7.0"])
        candidates[s.name] = {"spec": asdict(s), "train": tr, "score": score}
        ranked.append((score, s.name))

    ranked.sort(reverse=True)
    selected = ranked[0][1] if ranked and ranked[0][0] > -1e8 else None
    holdout = None
    gate = False
    if selected:
        s = next(z for z in SPECS if z.name == selected)
        ho = {str(c): simulate(hold, s, c) for c in COST_GRID}
        m7, m12, m20 = ho["7.0"], ho["12.0"], ho["20.0"]
        gate = (
            m7["trades"] >= 30 and m7["net_pct"] > 0 and m7["pf"] >= 1.10 and m7["max_dd_pct"] <= 12
            and m12["net_pct"] > 0 and m20["net_pct"] > 0
        )
        holdout = {"metrics": ho, "gate_pass": bool(gate)}

    state = {
        "version": "quantbot-v9-sparse-flow-acceptance",
        "symbol": SYMBOL,
        "timeframes": {"execution":"5m", "regime":"15m"},
        "public_inspiration": "Sparse multi-condition BTC futures acceptance model: extreme/persistent taker flow is only actionable when price accepts the direction through breakout, range expansion, close-location, VWAP and 15m regime agreement. Designed specifically to address v7 overtrading / weak raw-flow edge without weakening holdout standards.",
        "selection_end": str(SELECTION_END),
        "holdout_start": str(HOLDOUT_START),
        "cost_grid_one_way_bps": COST_GRID,
        "selected": selected,
        "candidates": candidates,
        "holdout": holdout,
        "research_gate": "PASS_TO_ROBUSTNESS" if gate else "REJECT_OR_RESEARCH_ONLY",
        "live_orders": False,
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps({
        "selected": selected,
        "candidate_train_7bps": {k:v["train"]["7.0"] for k,v in candidates.items()},
        "holdout": holdout,
        "research_gate": state["research_gate"]
    }, indent=2))


if __name__ == "__main__":
    main()