from __future__ import annotations

import json
import math
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "BTCUSDT"
BASE = "https://fapi.binance.com"
STATE = Path("data/v19_japanese_clone_paper_state.json")

# Forward-only paper engine. No credentials, no order endpoint, no live orders.
SAMPLE_SECONDS = 180
POLL_SEC = 1.0
DEPTH_LIMIT = 20
PAPER_NOTIONAL_USD = 100.0

# Conservative research fee assumptions for USD-M regular execution.
MAKER_FEE_BPS = 2.0
TAKER_FEE_BPS = 5.0
CONSERVATIVE_RT_BPS = MAKER_FEE_BPS + TAKER_FEE_BPS
MAKER_MAKER_DIAGNOSTIC_RT_BPS = MAKER_FEE_BPS * 2.0

# Public-method translations; fixed before forward observations accumulate.
MAX_SPREAD_BPS = 1.50
ENTRY_TIMEOUT_SEC = 8
HANSAN_MAX_HOLD_SEC = 30
JUN_MAX_HOLD_SEC = 35
HANSAN_TARGET_GROSS_BPS = 14.0
JUN_TARGET_GROSS_BPS = 11.0
STOP_GROSS_BPS = 5.5
IMMEDIATE_CHECK_SEC = 8
HANSAN_MIN_MFE_BPS = 1.0
JUN_MIN_MFE_BPS = 0.8


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sgn(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def get_depth():
    r = requests.get(f"{BASE}/fapi/v1/depth", params={"symbol": SYMBOL, "limit": DEPTH_LIMIT}, timeout=10)
    r.raise_for_status()
    return r.json()


def get_trades(limit=1000):
    r = requests.get(f"{BASE}/fapi/v1/aggTrades", params={"symbol": SYMBOL, "limit": limit}, timeout=10)
    r.raise_for_status()
    return r.json()


def get_klines(interval: str, limit: int):
    r = requests.get(f"{BASE}/fapi/v1/klines", params={"symbol": SYMBOL, "interval": interval, "limit": limit}, timeout=10)
    r.raise_for_status()
    return r.json()


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "version": "quantbot-v19-japanese-public-method-clone",
        "purpose": "FORWARD_L2_PAPER_ONLY_NO_LIVE",
        "symbol": SYMBOL,
        "live_orders": False,
        "methodology": {
            "jun": "distortion/overshoot -> failed continuation -> snapback; immediate time-stop",
            "hansan": "level break -> speed/acceleration -> immediate continuation; immediate time-stop",
            "testa": "order-book/tape confirmation and expected-value veto",
            "note": "Publicly described principles translated to BTC microstructure; not proprietary/private code or an exact secret strategy.",
        },
        "runs": 0,
        "observations": 0,
        "signals": 0,
        "paper_trades": [],
        "open_paper": None,
        "created_at": utcnow(),
    }


def save(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    st["updated_at"] = utcnow()
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def depth_metrics(d):
    bids = [(float(p), float(q)) for p, q in d["bids"][:10]]
    asks = [(float(p), float(q)) for p, q in d["asks"][:10]]
    bb, bq = bids[0]
    ba, aq = asks[0]
    mid = (bb + ba) / 2.0
    spread_bps = (ba - bb) / mid * 1e4
    bsum = sum(q for _, q in bids)
    asum = sum(q for _, q in asks)
    imb = (bsum - asum) / (bsum + asum) if (bsum + asum) else 0.0
    micro = (ba * bq + bb * aq) / (bq + aq) if (bq + aq) else mid
    microedge_bps = (micro - mid) / mid * 1e4
    top_imb = (bq - aq) / (bq + aq) if (bq + aq) else 0.0
    return {
        "bb": bb,
        "ba": ba,
        "mid": mid,
        "spread_bps": spread_bps,
        "imb": imb,
        "top_imb": top_imb,
        "micro": micro,
        "microedge_bps": microedge_bps,
        "bid_depth": bsum,
        "ask_depth": asum,
    }


def trade_flow(trades, lookback_ms: int):
    if not trades:
        return 0.0
    end = max(int(t["T"]) for t in trades)
    start = end - lookback_ms
    buy = sell = 0.0
    for t in trades:
        if int(t["T"]) < start:
            continue
        notion = float(t["q"]) * float(t["p"])
        if bool(t["m"]):
            sell += notion  # buyer is maker => seller is aggressor
        else:
            buy += notion
    tot = buy + sell
    return (buy - sell) / tot if tot else 0.0


def ema(values, span: int):
    if not values:
        return float("nan")
    a = 2.0 / (span + 1.0)
    e = values[0]
    for x in values[1:]:
        e = a * x + (1 - a) * e
    return e


def context():
    k15 = get_klines("15m", 60)[:-1]  # completed bars only
    k1 = get_klines("1m", 25)[:-1]
    c15 = [float(x[4]) for x in k15]
    e20 = ema(c15, 20)
    e50 = ema(c15, 50)
    r3 = c15[-1] / c15[-4] - 1 if len(c15) >= 4 else 0.0
    regime = 0
    if c15[-1] > e20 > e50 and r3 > 0:
        regime = 1
    elif c15[-1] < e20 < e50 and r3 < 0:
        regime = -1
    highs = [float(x[2]) for x in k1[-10:]]
    lows = [float(x[3]) for x in k1[-10:]]
    return {
        "regime15": regime,
        "level_high": max(highs),
        "level_low": min(lows),
        "last_1m_high": float(k1[-1][2]),
        "last_1m_low": float(k1[-1][3]),
    }


def ret_bps(hist: deque, sec: float) -> float:
    if len(hist) < 2:
        return 0.0
    now_t, now_p = hist[-1]["t"], hist[-1]["mid"]
    target = now_t - sec
    old = None
    for x in reversed(hist):
        if x["t"] <= target:
            old = x
            break
    if old is None:
        old = hist[0]
    return (now_p / old["mid"] - 1.0) * 1e4


def testa_score(side: int, m, flow5: float, flow15: float) -> int:
    score = 0
    if m["spread_bps"] <= MAX_SPREAD_BPS:
        score += 1
    if side * m["imb"] >= 0.12:
        score += 1
    if side * m["microedge_bps"] >= 0.12:
        score += 1
    if side * flow5 >= 0.08:
        score += 1
    if side * flow15 >= 0.05:
        score += 1
    return score


def hansan_signal(hist: deque, m, ctx, flow5: float, flow15: float):
    # Public-method copy: level interaction + speed + acceleration + immediate tape confirmation.
    r3 = ret_bps(hist, 3)
    r8 = ret_bps(hist, 8)
    long_break = m["mid"] > ctx["level_high"] * (1 + 0.5 / 1e4)
    short_break = m["mid"] < ctx["level_low"] * (1 - 0.5 / 1e4)
    side = 0
    if long_break and r3 >= 0.8 and r8 >= 2.0 and flow5 >= 0.20 and flow15 >= 0.12 and ctx["regime15"] != -1:
        side = 1
    elif short_break and r3 <= -0.8 and r8 <= -2.0 and flow5 <= -0.20 and flow15 <= -0.12 and ctx["regime15"] != 1:
        side = -1
    if side and testa_score(side, m, flow5, flow15) >= 4:
        return side, {"setup": "HANSAN_BREAK_ACCEL", "r3_bps": r3, "r8_bps": r8}
    return 0, None


def jun_signal(hist: deque, m, ctx, flow5: float, flow15: float):
    # Public-method copy: distortion/overshoot, then failure to continue and reversal confirmation.
    if len(hist) < 10:
        return 0, None
    r3 = ret_bps(hist, 3)
    r15 = ret_bps(hist, 15)
    move_dir = sgn(r15)
    if move_dir == 0 or abs(r15) < 4.0:
        return 0, None
    # The aggressive flow that produced the distortion must be strong in the move direction.
    if move_dir * flow15 < 0.25:
        return 0, None
    # Continuation stalls: last 3s no longer advances strongly in original direction.
    stalled = move_dir * r3 <= 0.25
    if not stalled:
        return 0, None
    side = -move_dir
    # Near a local extreme and the book/tape begins to confirm snapback.
    mids = [x["mid"] for x in hist if hist[-1]["t"] - x["t"] <= 60]
    near_extreme = (move_dir > 0 and m["mid"] >= max(mids) * (1 - 1.0 / 1e4)) or (move_dir < 0 and m["mid"] <= min(mids) * (1 + 1.0 / 1e4))
    if not near_extreme:
        return 0, None
    reversal_votes = 0
    if side * m["imb"] >= 0.08:
        reversal_votes += 1
    if side * m["microedge_bps"] >= 0.08:
        reversal_votes += 1
    if side * flow5 >= 0.03:
        reversal_votes += 1
    if reversal_votes >= 2 and testa_score(side, m, flow5, flow15) >= 3:
        return side, {"setup": "JUN_DISTORTION_SNAPBACK", "r3_bps": r3, "r15_bps": r15}
    return 0, None


def maker_fill(side: int, px: float, trades, since_ms: int) -> bool:
    hits = 0
    for t in trades:
        if int(t["T"]) < since_ms:
            continue
        p = float(t["p"])
        buyer_maker = bool(t["m"])
        if side == 1 and buyer_maker and p <= px:
            hits += 1
        elif side == -1 and (not buyer_maker) and p >= px:
            hits += 1
        if hits >= 2:  # conservative queue proxy: require multiple aggressor prints through our limit
            return True
    return False


def close_trade(st, m, reason: str):
    op = st.get("open_paper")
    if not op:
        return
    side = int(op["side"])
    exit_px = m["bb"] if side == 1 else m["ba"]
    gross_bps = side * (exit_px / float(op["entry_px"]) - 1.0) * 1e4
    net_bps = gross_bps - CONSERVATIVE_RT_BPS
    mm_diag_bps = gross_bps - MAKER_MAKER_DIAGNOSTIC_RT_BPS
    rec = {
        **op,
        "exit_ts": utcnow(),
        "exit_px": exit_px,
        "gross_bps": gross_bps,
        "net_bps_primary": net_bps,
        "net_bps_maker_maker_diagnostic": mm_diag_bps,
        "pnl_usd_100_primary": PAPER_NOTIONAL_USD * net_bps / 1e4,
        "reason": reason,
    }
    st["paper_trades"].append(rec)
    st["paper_trades"] = st["paper_trades"][-3000:]
    st["open_paper"] = None


def summarize(st):
    trs = st.get("paper_trades", [])
    def one(z):
        if not z:
            return {"closed": 0, "avg_net_bps": None, "win_pct": None, "pf": None, "net_usd_100": 0.0}
        nets = [float(t["net_bps_primary"]) for t in z]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x < 0]
        pf = sum(wins) / abs(sum(losses)) if losses else (999.0 if wins else None)
        return {
            "closed": len(z),
            "avg_net_bps": sum(nets) / len(nets),
            "win_pct": 100.0 * len(wins) / len(nets),
            "pf": pf,
            "net_usd_100": sum(float(t["pnl_usd_100_primary"]) for t in z),
        }
    st["summary"] = {
        "ALL": one(trs),
        "JUN": one([t for t in trs if str(t.get("setup", "")).startswith("JUN")]),
        "HANSAN": one([t for t in trs if str(t.get("setup", "")).startswith("HANSAN")]),
        "primary_roundtrip_fee_bps": CONSERVATIVE_RT_BPS,
        "maker_maker_diagnostic_roundtrip_bps": MAKER_MAKER_DIAGNOSTIC_RT_BPS,
        "notional_usd": PAPER_NOTIONAL_USD,
    }


def main():
    st = load_state()
    st["runs"] = int(st.get("runs", 0)) + 1
    hist = deque(maxlen=240)
    candidate = None
    ctx = context()
    last_ctx_refresh = time.time()
    started = time.time()
    last_m = None

    while time.time() - started < SAMPLE_SECONDS:
        try:
            if time.time() - last_ctx_refresh >= 30:
                ctx = context()
                last_ctx_refresh = time.time()
            d = get_depth()
            m = depth_metrics(d)
            last_m = m
            trades = get_trades(700)
            flow5 = trade_flow(trades, 5000)
            flow15 = trade_flow(trades, 15000)
            now = time.time()
            now_ms = int(now * 1000)
            hist.append({"t": now, "mid": m["mid"], "imb": m["imb"], "microedge_bps": m["microedge_bps"], "flow5": flow5})
            st["observations"] = int(st.get("observations", 0)) + 1

            op = st.get("open_paper")
            if op:
                side = int(op["side"])
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(op["entry_ts"])).total_seconds()
                mark_px = m["bb"] if side == 1 else m["ba"]
                move_bps = side * (mark_px / float(op["entry_px"]) - 1.0) * 1e4
                op["mfe_bps"] = max(float(op.get("mfe_bps", -1e9)), move_bps)
                op["mae_bps"] = min(float(op.get("mae_bps", 1e9)), move_bps)
                setup = str(op.get("setup", ""))
                target = JUN_TARGET_GROSS_BPS if setup.startswith("JUN") else HANSAN_TARGET_GROSS_BPS
                max_hold = JUN_MAX_HOLD_SEC if setup.startswith("JUN") else HANSAN_MAX_HOLD_SEC
                min_mfe = JUN_MIN_MFE_BPS if setup.startswith("JUN") else HANSAN_MIN_MFE_BPS
                if move_bps >= target:
                    close_trade(st, m, "target")
                elif move_bps <= -STOP_GROSS_BPS:
                    close_trade(st, m, "stop")
                elif age >= IMMEDIATE_CHECK_SEC and float(op.get("mfe_bps", 0.0)) < min_mfe:
                    close_trade(st, m, "hypothesis_failed_immediately")
                elif side * m["imb"] < -0.10 and side * m["microedge_bps"] < -0.05:
                    close_trade(st, m, "testa_book_veto")
                elif age >= max_hold:
                    close_trade(st, m, "time_stop")

            if not st.get("open_paper"):
                if candidate is None and len(hist) >= 10:
                    hs, hmeta = hansan_signal(hist, m, ctx, flow5, flow15)
                    js, jmeta = jun_signal(hist, m, ctx, flow5, flow15)
                    # Conflict means no trade: Testa-style expected-value veto.
                    if hs and js and hs != js:
                        st["conflicts_vetoed"] = int(st.get("conflicts_vetoed", 0)) + 1
                    else:
                        side = hs or js
                        meta = hmeta if hs else jmeta
                        if side and meta and m["spread_bps"] <= MAX_SPREAD_BPS:
                            candidate = {
                                "side": side,
                                "setup": meta["setup"],
                                "px": m["bb"] if side == 1 else m["ba"],
                                "since_ms": now_ms,
                                "created": now,
                                "signal_mid": m["mid"],
                                "entry_imb": m["imb"],
                                "entry_microedge_bps": m["microedge_bps"],
                                "flow5": flow5,
                                "flow15": flow15,
                                "regime15": ctx["regime15"],
                                **{k: v for k, v in meta.items() if k != "setup"},
                            }
                            st["signals"] = int(st.get("signals", 0)) + 1
                elif candidate is not None:
                    # Cancel if thesis changes before fill.
                    if time.time() - candidate["created"] > ENTRY_TIMEOUT_SEC:
                        candidate = None
                    elif maker_fill(int(candidate["side"]), float(candidate["px"]), trades, int(candidate["since_ms"])):
                        st["open_paper"] = {
                            "side": int(candidate["side"]),
                            "setup": candidate["setup"],
                            "entry_px": float(candidate["px"]),
                            "entry_ts": utcnow(),
                            "signal_mid": float(candidate["signal_mid"]),
                            "entry_imb": float(candidate["entry_imb"]),
                            "entry_microedge_bps": float(candidate["entry_microedge_bps"]),
                            "entry_flow5": float(candidate["flow5"]),
                            "entry_flow15": float(candidate["flow15"]),
                            "regime15": int(candidate["regime15"]),
                            "mfe_bps": 0.0,
                            "mae_bps": 0.0,
                        }
                        candidate = None
            time.sleep(POLL_SEC)
        except Exception as e:
            st["last_error"] = repr(e)
            time.sleep(2)

    # Never carry an unobserved paper position across GitHub-run gaps.
    if st.get("open_paper") and last_m:
        close_trade(st, last_m, "run_end_flatten")
    summarize(st)
    save(st)
    print(json.dumps(st, indent=2))


if __name__ == "__main__":
    main()
