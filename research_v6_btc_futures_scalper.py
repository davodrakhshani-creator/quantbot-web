from __future__ import annotations

import io
import json
import math
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SYMBOL = "BTCUSDT"
START = pd.Timestamp("2023-01-01", tz="UTC")
SELECTION_END = pd.Timestamp("2026-04-24 23:59:59", tz="UTC")
HOLDOUT_START = pd.Timestamp("2026-04-25", tz="UTC")
STATE = Path("data/v6_btc_scalper_state.json")
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"
REST = "https://fapi.binance.com/fapi/v1/klines"

# All-in one-way costs (commission + slippage allowance), expressed in bps.
COST_GRID = (7.0, 12.0, 20.0)


@dataclass(frozen=True)
class Spec:
    name: str
    entry: str
    ema_fast_15: int
    ema_slow_15: int
    breakout_5: int
    stop_atr: float
    take_atr: float
    max_hold_bars: int
    vol_min_q: float
    vol_max_q: float


SPECS = [
    Spec("S0_break_32_96", "breakout", 32, 96, 12, 1.20, 2.00, 36, .10, .90),
    Spec("S1_break_24_72", "breakout", 24, 72, 12, 1.20, 2.00, 36, .10, .90),
    Spec("S2_break_fast", "breakout", 24, 72, 8, 1.10, 1.80, 24, .10, .90),
    Spec("S3_break_wide", "breakout", 32, 96, 18, 1.35, 2.30, 48, .10, .90),
    Spec("S4_pull_32_96", "pullback", 32, 96, 12, 1.20, 2.00, 36, .10, .90),
    Spec("S5_pull_24_72", "pullback", 24, 72, 12, 1.20, 2.00, 36, .10, .90),
    Spec("S6_pull_tight", "pullback", 24, 72, 12, 1.00, 1.70, 24, .10, .90),
    Spec("S7_pull_wide", "pullback", 32, 96, 12, 1.40, 2.40, 48, .10, .90),
]


def month_iter(start: pd.Timestamp, end: pd.Timestamp):
    cur = pd.Timestamp(start.year, start.month, 1, tz="UTC")
    last = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while cur <= last:
        yield cur
        cur = cur + pd.offsets.MonthBegin(1)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    if len(df.columns) >= 12:
        df = df.iloc[:, :12]
        df.columns = cols
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]].dropna().sort_index()


def fetch_month(month: pd.Timestamp) -> pd.DataFrame:
    ym = month.strftime("%Y-%m")
    url = f"{BASE_URL}/{SYMBOL}/5m/{SYMBOL}-5m-{ym}.zip"
    r = requests.get(url, timeout=45)
    if r.status_code == 404:
        return pd.DataFrame()
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = z.namelist()[0]
        raw = pd.read_csv(z.open(name), header=None)
    return _normalize(raw)


def fetch_recent(start: pd.Timestamp) -> pd.DataFrame:
    out = []
    cursor = int(start.timestamp() * 1000)
    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    while cursor < now_ms:
        params = {"symbol": SYMBOL, "interval": "5m", "startTime": cursor, "limit": 1500}
        r = requests.get(REST, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out.extend(rows)
        nxt = int(rows[-1][0]) + 5 * 60 * 1000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(rows) < 1500:
            break
        time.sleep(.05)
    return _normalize(pd.DataFrame(out)) if out else pd.DataFrame()


def load_data() -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    # Monthly archives through previous month, REST for current month.
    prev_month_end = pd.Timestamp(now.year, now.month, 1, tz="UTC") - pd.Timedelta(minutes=5)
    chunks = []
    for m in month_iter(START, prev_month_end):
        try:
            x = fetch_month(m)
            if not x.empty:
                chunks.append(x)
        except Exception as exc:
            print("month error", m, exc)
    current_month = pd.Timestamp(now.year, now.month, 1, tz="UTC")
    try:
        recent = fetch_recent(current_month)
        if not recent.empty:
            chunks.append(recent)
    except Exception as exc:
        print("recent REST error", exc)
    if not chunks:
        raise RuntimeError("No BTCUSDT 5m data loaded")
    df = pd.concat(chunks).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # Exclude the still-open 5m candle.
    closed_before = pd.Timestamp.now(tz="UTC").floor("5min")
    df = df[(df.index >= START) & (df.index < closed_before)]
    return df


def rsi(s: pd.Series, n=14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([(df.high-df.low).abs(), (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def anchored_vwap(df: pd.DataFrame) -> pd.Series:
    typ = (df.high + df.low + df.close) / 3
    day = df.index.floor("D")
    pv = typ * df.volume
    return pv.groupby(day).cumsum() / df.volume.groupby(day).cumsum().replace(0, np.nan)


def features(df: pd.DataFrame, spec: Spec) -> pd.DataFrame:
    x = df.copy()
    x["atr"] = atr(x)
    x["atrp"] = x.atr / x.close
    # Expanding-ish rolling quantiles: 14 days of 5m bars, shifted to avoid same-bar leakage.
    window = 14 * 24 * 12
    x["atr_q_lo"] = x.atrp.rolling(window, min_periods=3*24*12).quantile(spec.vol_min_q).shift(1)
    x["atr_q_hi"] = x.atrp.rolling(window, min_periods=3*24*12).quantile(spec.vol_max_q).shift(1)
    x["rsi"] = rsi(x.close)
    x["ema20_5"] = x.close.ewm(span=20, adjust=False).mean()
    x["vwap"] = anchored_vwap(x)
    x["vol_med"] = x.volume.rolling(48, min_periods=24).median().shift(1)
    x["hi_break"] = x.high.shift(1).rolling(spec.breakout_5, min_periods=spec.breakout_5).max()
    x["lo_break"] = x.low.shift(1).rolling(spec.breakout_5, min_periods=spec.breakout_5).min()

    q = x.resample("15min", label="right", closed="right").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
    q["ef"] = q.close.ewm(span=spec.ema_fast_15, adjust=False).mean()
    q["es"] = q.close.ewm(span=spec.ema_slow_15, adjust=False).mean()
    q["slope"] = q.es - q.es.shift(4)
    q["long_reg"] = (q.close > q.ef) & (q.ef > q.es) & (q.slope > 0)
    q["short_reg"] = (q.close < q.ef) & (q.ef < q.es) & (q.slope < 0)
    # Signal from completed 15m candle, forward-filled into subsequent 5m bars.
    reg = q[["long_reg","short_reg"]].reindex(x.index, method="ffill").fillna(False)
    x[["long_reg","short_reg"]] = reg
    return x


def simulate(df: pd.DataFrame, spec: Spec, cost_bps: float, risk_frac: float=.005, leverage_cap: float=3.0):
    x = features(df, spec)
    equity = 1.0
    peak = 1.0
    dd = 0.0
    trades = []
    position = 0
    entry = stop = target = np.nan
    qty_notional = 0.0
    entry_i = None
    cooldown = 0
    pending = 0
    pending_atr = np.nan

    def close_trade(ts, px, reason, i):
        nonlocal equity, peak, dd, position, entry, stop, target, qty_notional, entry_i, cooldown
        gross = position * (px / entry - 1.0) * qty_notional
        cost = (cost_bps / 10000.0) * qty_notional
        ret = gross - cost
        equity *= (1 + ret)
        peak = max(peak, equity)
        dd = min(dd, equity / peak - 1)
        trades.append({"entry_i": entry_i, "exit_i": i, "exit_ts": str(ts), "side": position, "ret": ret, "gross": gross, "reason": reason, "notional": qty_notional})
        position = 0; entry = stop = target = np.nan; qty_notional = 0.0; entry_i = None
        cooldown = 3

    for i in range(max(200, spec.ema_slow_15*3), len(x)-1):
        row = x.iloc[i]
        nxt = x.iloc[i+1]
        if cooldown > 0:
            cooldown -= 1

        if position != 0:
            # Pessimistic intrabar rule: if stop and target both touch, stop wins.
            stop_hit = (position > 0 and row.low <= stop) or (position < 0 and row.high >= stop)
            tgt_hit = (position > 0 and row.high >= target) or (position < 0 and row.low <= target)
            held = i - entry_i
            regime_off = (position > 0 and not bool(row.long_reg)) or (position < 0 and not bool(row.short_reg))
            if stop_hit:
                close_trade(x.index[i], stop, "stop", i); continue
            if tgt_hit:
                close_trade(x.index[i], target, "target", i); continue
            if held >= spec.max_hold_bars or regime_off:
                close_trade(x.index[i+1], float(nxt.open), "time_or_regime", i+1); continue

        if position == 0 and pending != 0:
            # Enter strictly at next 5m bar open after signal.
            px = float(row.open)
            if np.isfinite(pending_atr) and pending_atr > 0:
                position = pending
                entry = px
                dist = spec.stop_atr * pending_atr
                stop = entry - position * dist
                target = entry + position * spec.take_atr * pending_atr
                stop_pct = dist / entry
                qty_notional = min(leverage_cap, risk_frac / max(stop_pct, 1e-6))
                # one-way entry cost
                equity *= (1 - (cost_bps / 10000.0) * qty_notional)
                peak = max(peak, equity); dd = min(dd, equity/peak-1)
                entry_i = i
            pending = 0; pending_atr = np.nan
            continue

        if position == 0 and cooldown == 0:
            vol_ok = np.isfinite(row.atr_q_lo) and np.isfinite(row.atr_q_hi) and row.atrp >= row.atr_q_lo and row.atrp <= row.atr_q_hi
            vol_confirm = np.isfinite(row.vol_med) and row.volume >= row.vol_med
            if not (vol_ok and vol_confirm and np.isfinite(row.atr) and row.atr > 0):
                continue
            long_sig = short_sig = False
            if spec.entry == "breakout":
                long_sig = bool(row.long_reg) and row.close > row.hi_break and row.close > row.vwap
                short_sig = bool(row.short_reg) and row.close < row.lo_break and row.close < row.vwap
            else:
                long_sig = bool(row.long_reg) and row.low <= row.ema20_5 and row.close > row.ema20_5 and row.close > row.vwap and 50 <= row.rsi <= 72
                short_sig = bool(row.short_reg) and row.high >= row.ema20_5 and row.close < row.ema20_5 and row.close < row.vwap and 28 <= row.rsi <= 50
            if long_sig ^ short_sig:
                pending = 1 if long_sig else -1
                pending_atr = float(row.atr)

    if position != 0:
        close_trade(x.index[-1], float(x.close.iloc[-1]), "eod", len(x)-1)

    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades":0,"net_pct":0,"pf":0,"win_pct":0,"max_dd_pct":0,"avg_trade_bps":0,"median_trade_bps":0,"longs":0,"shorts":0,"stop_pct":0,"target_pct":0,"time_exit_pct":0}
    gains = t.loc[t.ret>0,"ret"].sum()
    losses = -t.loc[t.ret<0,"ret"].sum()
    pf = float(gains/losses) if losses > 0 else 99.0
    return {
        "trades": int(len(t)),
        "net_pct": float((equity-1)*100),
        "pf": pf,
        "win_pct": float((t.ret>0).mean()*100),
        "max_dd_pct": float(-dd*100),
        "avg_trade_bps": float(t.ret.mean()*10000),
        "median_trade_bps": float(t.ret.median()*10000),
        "longs": int((t.side>0).sum()),
        "shorts": int((t.side<0).sum()),
        "stop_pct": float((t.reason=="stop").mean()*100),
        "target_pct": float((t.reason=="target").mean()*100),
        "time_exit_pct": float((t.reason=="time_or_regime").mean()*100),
    }


def score(m: dict) -> float:
    if m["trades"] < 120 or m["net_pct"] <= 0 or m["pf"] < 1.10 or m["max_dd_pct"] > 25:
        return -1e9
    return m["net_pct"] + 25*(m["pf"]-1) + .20*m["win_pct"] - .75*m["max_dd_pct"]


def main():
    df = load_data()
    train = df[df.index <= SELECTION_END]
    hold = df[df.index >= HOLDOUT_START]
    results = {}
    selected = None; best = -1e18
    for s in SPECS:
        hist = simulate(train, s, COST_GRID[0])
        stress12 = simulate(train, s, COST_GRID[1])
        stress20 = simulate(train, s, COST_GRID[2])
        gate = hist["trades"] >= 120 and hist["net_pct"] > 0 and hist["pf"] >= 1.10 and hist["max_dd_pct"] <= 25 and stress12["net_pct"] > 0 and stress20["net_pct"] > 0
        sc = score(hist) if gate else -1e9
        results[s.name] = {"spec":asdict(s),"history_7bps":hist,"history_12bps":stress12,"history_20bps":stress20,"selection_gate":gate,"score":sc}
        if sc > best:
            best = sc; selected = s
        print(s.name, hist, "gate", gate)

    state = {
        "version":"quantbot-v6-btc-futures-scalper",
        "updated":datetime.now(timezone.utc).isoformat(),
        "symbol":SYMBOL,
        "timeframes":{"entry":"5m","regime":"15m"},
        "data_start":str(df.index.min()),"data_end":str(df.index.max()),
        "selection_end":str(SELECTION_END),"holdout_start":str(HOLDOUT_START),
        "design":"Single-asset BTCUSDT USD-M perpetual. 15m trend regime; 5m breakout/pullback entry; completed bars only; next-5m-open entry; ATR stop/target; pessimistic same-bar stop-first; one position at a time; 0.5% equity risk/trade; 3x notional cap; no live orders.",
        "costs_one_way_bps":{"base":7,"stress":12,"extreme":20},
        "candidate_count":len(SPECS),"selected": selected.name if selected else None,
        "candidates":results,
        "holdout":{},
        "promotion_gate":{"criteria":"selected only; holdout trades>=60, net>0, PF>=1.15, maxDD<=15%, 12bps net>0, 20bps net>0","passed":False},
        "research_status":"NO_EDGE_SELECTED" if selected is None else "PAPER_ONLY_NO_LIVE_MONEY",
        "live_orders":False,
        "warning":"Leverage is not the source of edge. Returns are risk-sized; high leverage can liquidate before a stop fills during gaps/slippage."
    }
    if selected is not None:
        h7 = simulate(hold, selected, 7)
        h12 = simulate(hold, selected, 12)
        h20 = simulate(hold, selected, 20)
        state["holdout"] = {"base_7bps":h7,"stress_12bps":h12,"extreme_20bps":h20}
        passed = h7["trades"] >= 60 and h7["net_pct"] > 0 and h7["pf"] >= 1.15 and h7["max_dd_pct"] <= 15 and h12["net_pct"] > 0 and h20["net_pct"] > 0
        state["promotion_gate"]["passed"] = bool(passed)
        state["research_status"] = "PAPER_CANDIDATE" if passed else "HOLDOUT_FAIL_NO_LIVE"
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps({"selected":state["selected"],"status":state["research_status"],"holdout":state["holdout"]}, indent=2))


if __name__ == "__main__":
    main()
