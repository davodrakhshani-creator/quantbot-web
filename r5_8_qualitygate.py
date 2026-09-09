import csv, io, json, math, statistics, urllib.request, zipfile, time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYMBOL = "BTCUSDT"
TEHRAN = timezone(timedelta(hours=3, minutes=30))
TEST_START = datetime(2026, 9, 1, 0, 0, tzinfo=TEHRAN)
TEST_END = datetime(2026, 9, 9, 0, 0, tzinfo=TEHRAN)
WARMUP_START_UTC = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
FETCH_END_UTC = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)

START_EQUITY = 100.0
ROUNDTRIP_COST = 0.0011
STOP_PCT = 0.0015
RISK_EQUITY = 0.0075
NOTIONAL_MULT = RISK_EQUITY / (STOP_PCT + ROUNDTRIP_COST)
MAX_LEVERAGE = 3.0
TP1_PCT = 0.0033
TP1_FRACTION = 0.70
RUNNER_FRACTION = 0.30
RUNNER_TRAIL = 0.0018
MAX_HOLD_MIN = 120
MAX_DAILY_LOSS = 0.015
MAX_TRADES_DAY = 4
COOLDOWN_MIN = 20
OUT = Path("data/r5_8_qualitygate_sep1_8.json")
UA = "QuantBot-R5.8-QualityGate/1.0"

def fetch_daily_1m(day):
    ds = day.strftime("%Y-%m-%d")
    url = f"https://data.binance.vision/data/futures/um/daily/klines/{SYMBOL}/1m/{SYMBOL}-1m-{ds}.zip"
    err = None
    for k in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                b = r.read()
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                raw = z.read(z.namelist()[0]).decode("utf-8")
            rows = []
            for x in csv.reader(io.StringIO(raw)):
                if not x or not x[0].isdigit():
                    continue
                rows.append({"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5]), "tb": float(x[9])})
            return rows
        except Exception as e:
            err = e
            time.sleep(min(8, 0.8 * (2 ** k)))
    raise RuntimeError(f"failed {url}: {err}")

def fetch_all():
    rows = {}
    d = WARMUP_START_UTC.date()
    while d < FETCH_END_UTC.date():
        for r in fetch_daily_1m(datetime(d.year, d.month, d.day, tzinfo=timezone.utc)):
            rows[r["t"]] = r
        d += timedelta(days=1)
    return [rows[k] for k in sorted(rows)]

def aggregate(rows, minutes):
    span = minutes * 60000
    groups = {}
    for r in rows:
        k = (r["t"] // span) * span
        groups.setdefault(k, []).append(r)
    out = []
    for k in sorted(groups):
        g = sorted(groups[k], key=lambda x: x["t"])
        if len(g) < minutes:
            continue
        out.append({"t": k, "o": g[0]["o"], "h": max(x["h"] for x in g), "l": min(x["l"] for x in g), "c": g[-1]["c"], "v": sum(x["v"] for x in g), "tb": sum(x["tb"] for x in g)})
    return out

def ema_series(vals, n):
    a = 2.0 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else a * v + (1-a) * e
        out.append(e)
    return out

def true_ranges(bars):
    out, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"] if prev is None else max(b["h"]-b["l"], abs(b["h"]-prev), abs(b["l"]-prev))
        out.append(tr)
        prev = b["c"]
    return out

def rolling_mean(vals, n):
    out, q, s = [], deque(), 0.0
    for v in vals:
        q.append(v); s += v
        if len(q) > n:
            s -= q.popleft()
        out.append(s / len(q))
    return out

def rsi_series(vals, n=7):
    out = [50.0] * len(vals)
    gains, losses, sg, sl = deque(), deque(), 0.0, 0.0
    for i in range(1, len(vals)):
        d = vals[i] - vals[i-1]
        g, l = max(d, 0.0), max(-d, 0.0)
        gains.append(g); losses.append(l); sg += g; sl += l
        if len(gains) > n:
            sg -= gains.popleft(); sl -= losses.popleft()
        if len(gains) >= n:
            out[i] = 100.0 if sl == 0 else 100 - 100 / (1 + sg/sl)
    return out

def rolling_median_prev(vals, n):
    out, q = [], deque()
    for v in vals:
        out.append(statistics.median(q) if q else v)
        q.append(v)
        if len(q) > n:
            q.popleft()
    return out

def build_features(bars, timeframe):
    closes = [b["c"] for b in bars]
    vols = [b["v"] for b in bars]
    e9, e21 = ema_series(closes, 9), ema_series(closes, 21)
    atr = rolling_mean(true_ranges(bars), 14)
    rsi7 = rsi_series(closes, 7)
    medv = rolling_median_prev(vols, 20)
    q, pv, vv, vwap = deque(), 0.0, 0.0, []
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        item = (tp*b["v"], b["v"])
        q.append(item); pv += item[0]; vv += item[1]
        if len(q) > 48:
            old = q.popleft(); pv -= old[0]; vv -= old[1]
        vwap.append(pv/vv if vv else b["c"])
    out = []
    for i, b in enumerate(bars):
        flow = b["tb"] / max(b["v"] - b["tb"], 1e-12)
        rng = max(b["h"] - b["l"], b["c"] * 1e-9)
        cloc = (b["c"] - b["l"]) / rng
        body = (b["c"] - b["o"]) / b["o"]
        atrp = atr[i] / b["c"] if b["c"] else 0
        slope = e21[i] / e21[i-3] - 1 if i >= 3 and e21[i-3] else 0
        upthr = 0.00015 if timeframe == 5 else 0.00025
        if e9[i] > e21[i] and b["c"] > e21[i] and slope > upthr:
            regime = "UP"
        elif e9[i] < e21[i] and b["c"] < e21[i] and slope < -upthr:
            regime = "DOWN"
        else:
            regime = "RANGE"
        same2 = b["c"] / bars[i-2]["c"] - 1 if i >= 2 else 0.0
        chase = abs(body) >= 0.0025 or rng / b["o"] >= 0.0035 or abs(same2) >= 0.0030
        out.append({**b, "ema9": e9[i], "ema21": e21[i], "atr": atr[i], "atrp": atrp, "rsi7": rsi7[i], "medv": max(medv[i], 1e-12), "vwap": vwap[i], "flow": flow, "cloc": cloc, "body": body, "regime": regime, "chase": chase, "same2": same2})
    return out

def latest_completed_index(t_ms, minutes):
    span = minutes * 60000
    return ((t_ms + 60000) // span) * span - span

def minute_features(rows):
    closes = [r["c"] for r in rows]
    vols = [r["v"] for r in rows]
    e20 = ema_series(closes, 20)
    medv = rolling_median_prev(vols, 20)
    out = []
    for i, r in enumerate(rows):
        flow = r["tb"] / max(r["v"] - r["tb"], 1e-12)
        rng = max(r["h"] - r["l"], r["c"] * 1e-9)
        out.append({**r, "ema20": e20[i], "medv": max(medv[i], 1e-12), "flow": flow, "cloc": (r["c"]-r["l"])/rng, "body": (r["c"]-r["o"])/r["o"], "prev20h": max((x["h"] for x in rows[max(0,i-20):i]), default=r["h"]), "prev20l": min((x["l"] for x in rows[max(0,i-20):i]), default=r["l"]), "prev3h": max((x["h"] for x in rows[max(0,i-3):i]), default=r["h"]), "prev3l": min((x["l"] for x in rows[max(0,i-3):i]), default=r["l"])})
    return out

def signal_for(i, m1, f5_by_t, f15_by_t):
    x = m1[i]
    c5 = f5_by_t.get(latest_completed_index(x["t"], 5))
    c15 = f15_by_t.get(latest_completed_index(x["t"], 15))
    if not c5 or not c15 or i < 25:
        return None
    aligned_up = c5["regime"] == "UP" and c15["regime"] == "UP"
    aligned_dn = c5["regime"] == "DOWN" and c15["regime"] == "DOWN"
    dist = abs(x["c"] / x["ema20"] - 1) if x["ema20"] else 99

    if c5["atrp"] >= 0.0013 and c15["atrp"] >= 0.0018 and not c5["chase"] and dist <= 0.0015:
        if aligned_up and x["c"] > x["prev20h"]*1.0001 and x["flow"] >= 1.50 and x["v"] >= 1.40*x["medv"] and x["cloc"] >= 0.80 and x["body"] >= 0.0006:
            return ("CORE_BREAKOUT", "LONG", c15["regime"])
        if aligned_dn and x["c"] < x["prev20l"]*0.9999 and x["flow"] <= 1/1.50 and x["v"] >= 1.40*x["medv"] and x["cloc"] <= 0.20 and x["body"] <= -0.0006:
            return ("CORE_BREAKOUT", "SHORT", c15["regime"])

    if c5["chase"] and c5["atrp"] >= 0.0013 and c15["atrp"] >= 0.0018:
        if aligned_up and c5["body"] > 0:
            retr = (c5["h"] - x["l"]) / max(c5["h"]-c5["l"], 1e-12)
            if 0.15 <= retr <= 0.55 and x["c"] > x["prev3h"] and x["c"] >= x["ema20"] and x["flow"] >= 1.70 and x["v"] >= 1.50*x["medv"] and x["cloc"] >= 0.75:
                return ("CHASE_RETEST", "LONG", c15["regime"])
        if aligned_dn and c5["body"] < 0:
            retr = (x["h"] - c5["l"]) / max(c5["h"]-c5["l"], 1e-12)
            if 0.15 <= retr <= 0.55 and x["c"] < x["prev3l"] and x["c"] <= x["ema20"] and x["flow"] <= 1/1.70 and x["v"] >= 1.50*x["medv"] and x["cloc"] <= 0.25:
                return ("CHASE_RETEST", "SHORT", c15["regime"])

    srng = max(c5["h"] - c5["l"], 1e-12)
    shock_up = c5["v"] >= 2.5*c5["medv"] and c5["flow"] >= 2.0 and c5["cloc"] >= 0.80 and c5["body"] >= 0.0030
    shock_dn = c5["v"] >= 2.5*c5["medv"] and c5["flow"] <= 0.50 and c5["cloc"] <= 0.20 and c5["body"] <= -0.0030
    if shock_up:
        retr = (c5["h"] - x["l"]) / srng
        if retr <= 0.35 and x["c"] > x["o"] and x["flow"] >= 1.50 and x["v"] >= 1.20*x["medv"] and x["cloc"] >= 0.70:
            return ("SHOCK", "LONG", "SHOCK_UP")
    if shock_dn:
        retr = (x["h"] - c5["l"]) / srng
        if retr <= 0.35 and x["c"] < x["o"] and x["flow"] <= 1/1.50 and x["v"] >= 1.20*x["medv"] and x["cloc"] <= 0.30:
            return ("SHOCK", "SHORT", "SHOCK_DOWN")

    if c5["regime"] == "RANGE" and c15["regime"] == "RANGE" and c5["atr"] > 0:
        dev = (c5["c"] - c5["vwap"]) / c5["atr"]
        if dev <= -2.5 and c5["rsi7"] <= 15 and x["c"] > x["prev3h"] and x["flow"] >= 1.80 and x["v"] >= 1.50*x["medv"] and x["cloc"] >= 0.75:
            return ("BNF_REVERSAL", "LONG", "RANGE")
        if dev >= 2.5 and c5["rsi7"] >= 85 and x["c"] < x["prev3l"] and x["flow"] <= 1/1.80 and x["v"] >= 1.50*x["medv"] and x["cloc"] <= 0.25:
            return ("BNF_REVERSAL", "SHORT", "RANGE")
    return None

def simulate_trade(entry_i, side, m1, notional):
    entry = m1[entry_i]["o"]
    stop = entry * (1-STOP_PCT if side == "LONG" else 1+STOP_PCT)
    tp = entry * (1+TP1_PCT if side == "LONG" else 1-TP1_PCT)
    tp_hit, best, runner_exit = False, entry, None
    max_i = min(len(m1)-1, entry_i + MAX_HOLD_MIN)
    exit_i, reason = max_i, "TIME"
    for j in range(entry_i, max_i+1):
        b = m1[j]
        if not tp_hit:
            stop_touch = b["l"] <= stop if side == "LONG" else b["h"] >= stop
            tp_touch = b["h"] >= tp if side == "LONG" else b["l"] <= tp
            if stop_touch:
                gross = -STOP_PCT * notional
                cost = ROUNDTRIP_COST * notional
                return j, stop, "STOP", False, gross, cost, gross-cost
            if tp_touch:
                tp_hit = True; best = tp
        else:
            if side == "LONG":
                best = max(best, b["h"]); trail = max(entry, best*(1-RUNNER_TRAIL))
                if b["l"] <= trail:
                    runner_exit, exit_i, reason = trail, j, "TP1+RUNNER_TRAIL"; break
            else:
                best = min(best, b["l"]); trail = min(entry, best*(1+RUNNER_TRAIL))
                if b["h"] >= trail:
                    runner_exit, exit_i, reason = trail, j, "TP1+RUNNER_TRAIL"; break
    if tp_hit:
        if runner_exit is None:
            runner_exit, exit_i, reason = m1[max_i]["c"], max_i, "TP1+TIME"
        r1 = tp/entry-1 if side == "LONG" else entry/tp-1
        r2 = runner_exit/entry-1 if side == "LONG" else entry/runner_exit-1
        gross = notional * (TP1_FRACTION*r1 + RUNNER_FRACTION*r2)
        exitpx = TP1_FRACTION*tp + RUNNER_FRACTION*runner_exit
    else:
        exitpx = m1[max_i]["c"]
        r = exitpx/entry-1 if side == "LONG" else entry/exitpx-1
        gross = notional*r
        reason = "TIME_INVALIDATION"
    cost = notional * ROUNDTRIP_COST
    return exit_i, exitpx, reason, tp_hit, gross, cost, gross-cost

def stats(rows):
    if not rows:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "net_pnl": 0.0, "pf": None}
    gp = sum(max(t["net_pnl"], 0) for t in rows)
    gl = -sum(min(t["net_pnl"], 0) for t in rows)
    w = sum(t["net_pnl"] > 0 for t in rows)
    return {"trades": len(rows), "wins": w, "losses": len(rows)-w, "win_rate": round(100*w/len(rows), 1), "net_pnl": round(sum(t["net_pnl"] for t in rows), 6), "pf": round(gp/gl, 2) if gl > 0 else (99.0 if gp > 0 else None)}

def main():
    raw = fetch_all()
    m1 = minute_features(raw)
    f5 = build_features(aggregate(raw, 5), 5)
    f15 = build_features(aggregate(raw, 15), 15)
    f5_by_t = {x["t"]: x for x in f5}
    f15_by_t = {x["t"]: x for x in f15}
    equity, peak, maxdd = START_EQUITY, START_EQUITY, 0.0
    trades, last_exit_i, day_state = [], -10**9, {}
    consec_losses, pause_regime = 0, None
    start_ms = int(TEST_START.astimezone(timezone.utc).timestamp()*1000)
    end_ms = int(TEST_END.astimezone(timezone.utc).timestamp()*1000)
    i = 0
    while i < len(m1)-1:
        t = m1[i]["t"]
        if t < start_ms:
            i += 1; continue
        if t >= end_ms:
            break
        local = datetime.fromtimestamp(t/1000, timezone.utc).astimezone(TEHRAN)
        day = local.strftime("%Y-%m-%d")
        ds = day_state.setdefault(day, {"start_equity": equity, "realized": 0.0, "trades": 0})
        if i-last_exit_i < COOLDOWN_MIN or ds["trades"] >= MAX_TRADES_DAY:
            i += 1; continue
        cur15 = f15_by_t.get(latest_completed_index(t, 15))
        if pause_regime is not None:
            if cur15 and cur15["regime"] != pause_regime:
                pause_regime, consec_losses = None, 0
            else:
                i += 1; continue
        sig = signal_for(i, m1, f5_by_t, f15_by_t)
        if not sig:
            i += 1; continue
        full_stop_risk = equity * RISK_EQUITY
        if ds["realized"] - full_stop_risk < -MAX_DAILY_LOSS * ds["start_equity"]:
            i += 1; continue
        setup, side, regime = sig
        entry_i = i + 1
        if entry_i >= len(m1) or m1[entry_i]["t"] >= end_ms:
            break
        notional = min(equity*NOTIONAL_MULT, equity*MAX_LEVERAGE)
        eq_before = equity
        exit_i, exitpx, reason, tp_hit, gross, cost, net = simulate_trade(entry_i, side, m1, notional)
        equity += net; peak = max(peak, equity); maxdd = max(maxdd, (peak-equity)/peak)
        ds["realized"] += net; ds["trades"] += 1
        if net > 0:
            consec_losses, pause_regime = 0, None
        else:
            consec_losses += 1
            if consec_losses >= 2:
                pause_regime = cur15["regime"] if cur15 else regime
        et = datetime.fromtimestamp(m1[entry_i]["t"]/1000, timezone.utc).astimezone(TEHRAN)
        xt = datetime.fromtimestamp(m1[exit_i]["t"]/1000, timezone.utc).astimezone(TEHRAN)
        trades.append({"day": day, "setup": setup, "side": side, "regime": regime, "signal_time": local.isoformat(), "entry_time": et.isoformat(), "exit_time": xt.isoformat(), "entry": round(m1[entry_i]["o"], 2), "exit": round(exitpx, 2), "reason": reason, "tp1_hit": tp_hit, "equity_before": round(eq_before, 6), "notional": round(notional, 6), "gross_pnl": round(gross, 6), "cost": round(cost, 6), "net_pnl": round(net, 6), "equity_after": round(equity, 6)})
        last_exit_i = exit_i
        i = max(i+1, exit_i+1)
    daily, running = [], START_EQUITY
    for k in range(8):
        d = (TEST_START + timedelta(days=k)).strftime("%Y-%m-%d")
        tr = [x for x in trades if x["day"] == d]
        s = stats(tr); start_eq = running; running += sum(x["net_pnl"] for x in tr)
        s.update({"day": d, "start_equity": round(start_eq, 6), "end_equity": round(running, 6)})
        daily.append(s)
    by_setup = {name: stats([x for x in trades if x["setup"] == name]) for name in ["CORE_BREAKOUT", "CHASE_RETEST", "SHOCK", "BNF_REVERSAL"]}
    gp = sum(max(x["net_pnl"], 0) for x in trades); gl = -sum(min(x["net_pnl"], 0) for x in trades)
    overall = {"start_equity": START_EQUITY, "final_equity": round(equity, 6), "net_pnl": round(equity-START_EQUITY, 6), "return_pct": round((equity/START_EQUITY-1)*100, 3), "trades": len(trades), "wins": sum(x["net_pnl"] > 0 for x in trades), "losses": sum(x["net_pnl"] <= 0 for x in trades), "win_rate": round(100*sum(x["net_pnl"] > 0 for x in trades)/len(trades), 1) if trades else None, "pf": round(gp/gl, 2) if gl > 0 else (99.0 if gp > 0 else None), "max_dd_pct": round(maxdd*100, 3), "gross_pnl": round(sum(x["gross_pnl"] for x in trades), 6), "modeled_costs": round(sum(x["cost"] for x in trades), 6)}
    payload = {"version": "R5.8-HTF-QualityGate-Frozen", "generated_at": datetime.now(timezone.utc).isoformat(), "period": {"tehran_start": TEST_START.isoformat(), "tehran_end_exclusive": TEST_END.isoformat(), "symbol": "BTCUSDT USD-M Perpetual", "source": "Binance official data.binance.vision USD-M futures daily 1m klines; 5m/15m aggregated from 1m; taker-buy volume as historical order-flow proxy"}, "frozen_rules": {"start_equity": START_EQUITY, "modeled_total_roundtrip_cost_pct": ROUNDTRIP_COST*100, "hard_stop_price_pct": STOP_PCT*100, "risk_equity_per_full_stop_pct": RISK_EQUITY*100, "notional_multiplier": NOTIONAL_MULT, "max_leverage": MAX_LEVERAGE, "tp1_gross_pct": TP1_PCT*100, "tp1_fraction": TP1_FRACTION, "runner_fraction": RUNNER_FRACTION, "runner_trail_pct": RUNNER_TRAIL*100, "max_daily_realized_loss_pct": MAX_DAILY_LOSS*100, "max_trades_per_day": MAX_TRADES_DAY, "cooldown_min": COOLDOWN_MIN, "loss_pause": "2 consecutive losses => pause until 15m regime changes", "quality_gate": "5m+15m alignment mandatory for trend entries; chase only via retest; shock rare override; BNF only 5m+15m RANGE"}, "methodology_notes": ["R5.8 structure was frozen before this run; reruns after failed launches only repair source-file execution, not strategy thresholds.", "Signals use completed bars only; entry is next 1m open.", "If hard stop and TP1 are both touched in the same 1m bar before TP1, stop is assumed first (conservative).", "Historical full Level-2 order book is not claimed; Binance kline taker-buy volume is used as an order-flow proxy.", "Sep 1-5 were previously inspected during development, so Sep 1-8 is development validation rather than a clean out-of-sample proof."], "daily": daily, "overall": overall, "by_setup": by_setup, "trades": trades}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(overall, indent=2)); print("daily:")
    for d in daily: print(d)
    print("by_setup:", json.dumps(by_setup, indent=2))

if __name__ == "__main__":
    main()
