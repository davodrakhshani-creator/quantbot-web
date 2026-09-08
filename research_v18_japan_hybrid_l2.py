from __future__ import annotations

import json, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import requests

SYMBOL = 'BTCUSDT'
BASE = 'https://fapi.binance.com'
STATE = Path('data/v18_japan_hybrid_l2_state.json')
SAMPLE_SECONDS = 210
NEW_ENTRY_CUTOFF_SEC = 175
POLL_SEC = 1.0
DEPTH_LIMIT = 20
PAPER_NOTIONAL_USD = 100.0

# Conservative configurable execution assumptions; paper only.
MAKER_FEE_BPS = 2.0
TAKER_FEE_BPS = 5.0
TAKER_SLIPPAGE_BPS = 0.5
QUEUE_MULT = 1.25
MAX_SPREAD_BPS = 1.5
MIN_SIDE_DEPTH_USD = 250_000.0

# Immediate-feedback scalping: Jun-style invalidation, Hansan-style acceleration.
JUN_MAX_HOLD_SEC = 28
HANSAN_MAX_HOLD_SEC = 24
IMMEDIATE_CHECK_SEC = 8
IMMEDIATE_FAVORABLE_BPS = 0.7
JUN_TARGET_BPS = 9.0
HANSAN_TARGET_BPS = 12.0
HARD_STOP_BPS = 7.0


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def http_get(path, params):
    r = requests.get(BASE + path, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def get_depth():
    return http_get('/fapi/v1/depth', {'symbol': SYMBOL, 'limit': DEPTH_LIMIT})


def get_trades(limit=1000):
    return http_get('/fapi/v1/aggTrades', {'symbol': SYMBOL, 'limit': limit})


def get_klines(interval, limit=80):
    return http_get('/fapi/v1/klines', {'symbol': SYMBOL, 'interval': interval, 'limit': limit})


def ema(vals, n):
    if not vals:
        return 0.0
    a = 2.0 / (n + 1.0)
    out = float(vals[0])
    for v in vals[1:]:
        out = a * float(v) + (1.0 - a) * out
    return out


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'version': 'quantbot-v18-japan-hybrid-l2',
        'purpose': 'PUBLIC_METHOD_INSPIRED_FORWARD_PAPER_NO_LIVE',
        'symbol': SYMBOL,
        'live_orders': False,
        'runs': 0,
        'observations': 0,
        'signals': {'jun': 0, 'hansan': 0},
        'paper_trades': [],
        'created_at': utcnow(),
        'method_map': {
            'jun': 'distortion/exhaustion -> immediate snapback; invalidate fast',
            'hansan': 'level break -> acceleration/continuation; invalidate fast',
            'testa': 'order-book/tape/liquidity quality filter + expected-value accounting'
        }
    }


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    st['updated_at'] = utcnow()
    STATE.write_text(json.dumps(st, indent=2), encoding='utf-8')


def book_metrics(d):
    bids = [(float(p), float(q)) for p, q in d['bids'][:10]]
    asks = [(float(p), float(q)) for p, q in d['asks'][:10]]
    bb, bq = bids[0]
    ba, aq = asks[0]
    mid = (bb + ba) / 2.0
    spread_bps = (ba - bb) / mid * 1e4
    bid_qty = sum(q for _, q in bids)
    ask_qty = sum(q for _, q in asks)
    imb10 = (bid_qty - ask_qty) / (bid_qty + ask_qty) if bid_qty + ask_qty else 0.0
    bid3 = sum(q for _, q in bids[:3])
    ask3 = sum(q for _, q in asks[:3])
    imb3 = (bid3 - ask3) / (bid3 + ask3) if bid3 + ask3 else 0.0
    micro = (ba * bq + bb * aq) / (bq + aq) if bq + aq else mid
    microedge_bps = (micro - mid) / mid * 1e4
    bid_usd = sum(p * q for p, q in bids)
    ask_usd = sum(p * q for p, q in asks)
    return {
        'bb': bb, 'ba': ba, 'bq': bq, 'aq': aq, 'mid': mid,
        'spread_bps': spread_bps, 'imb10': imb10, 'imb3': imb3,
        'microedge_bps': microedge_bps, 'bid_usd': bid_usd, 'ask_usd': ask_usd
    }


def trade_window(trades, sec):
    if not trades:
        return {'flow': 0.0, 'notional': 0.0, 'count': 0, 'ret_bps': 0.0, 'speed': 0.0}
    end = max(int(t['T']) for t in trades)
    start = end - int(sec * 1000)
    rows = [t for t in trades if int(t['T']) >= start]
    if not rows:
        return {'flow': 0.0, 'notional': 0.0, 'count': 0, 'ret_bps': 0.0, 'speed': 0.0}
    buy = sell = 0.0
    for t in rows:
        notion = float(t['p']) * float(t['q'])
        if bool(t['m']):
            sell += notion  # buyer maker => seller taker
        else:
            buy += notion
    total = buy + sell
    flow = (buy - sell) / total if total else 0.0
    p0 = float(rows[0]['p'])
    p1 = float(rows[-1]['p'])
    ret_bps = (p1 / p0 - 1.0) * 1e4 if p0 else 0.0
    return {'flow': flow, 'notional': total, 'count': len(rows), 'ret_bps': ret_bps, 'speed': len(rows) / max(sec, 1e-9)}


def completed_context():
    k5 = get_klines('5m', 80)
    k15 = get_klines('15m', 80)
    # Exclude currently forming candle.
    c5 = [float(x[4]) for x in k5[:-1]]
    h5 = [float(x[2]) for x in k5[:-1]]
    l5 = [float(x[3]) for x in k5[:-1]]
    c15 = [float(x[4]) for x in k15[:-1]]
    e20 = ema(c15[-50:], 20)
    e50 = ema(c15[-70:], 50)
    last15 = c15[-1]
    trend = 1 if (last15 > e20 > e50) else (-1 if (last15 < e20 < e50) else 0)
    level_hi = max(h5[-8:])
    level_lo = min(l5[-8:])
    return {'trend15': trend, 'level_hi_5m': level_hi, 'level_lo_5m': level_lo, 'last5_close': c5[-1]}


def hist_value(hist, seconds_ago, key='mid'):
    if not hist:
        return None
    cutoff = time.time() - seconds_ago
    chosen = None
    for row in reversed(hist):
        if row['wall_ts'] <= cutoff:
            chosen = row
            break
    return chosen.get(key) if chosen else None


def rel_bps(a, b):
    return (a / b - 1.0) * 1e4 if b else 0.0


def book_persistence(hist, side):
    if len(hist) < 3:
        return False
    rows = list(hist)[-3:]
    if side == 1:
        return all(r['imb10'] > 0.06 and r['microedge_bps'] > 0 for r in rows)
    return all(r['imb10'] < -0.06 and r['microedge_bps'] < 0 for r in rows)


def testa_filter(m, hist, side):
    if m['spread_bps'] <= 0 or m['spread_bps'] > MAX_SPREAD_BPS:
        return False
    if min(m['bid_usd'], m['ask_usd']) < MIN_SIDE_DEPTH_USD:
        return False
    if not book_persistence(hist, side):
        return False
    # Reject obvious liquidity withdrawal on the side that should support the trade.
    prev = None
    if len(hist) >= 6:
        prev = list(hist)[-6]
    if prev:
        if side == 1 and m['bid_usd'] < 0.55 * prev['bid_usd']:
            return False
        if side == -1 and m['ask_usd'] < 0.55 * prev['ask_usd']:
            return False
    return True


def jun_signal(m, t2, t5, t15, hist, ctx):
    p3 = hist_value(hist, 3)
    p10 = hist_value(hist, 10)
    if p3 is None or p10 is None:
        return 0
    r3 = rel_bps(m['mid'], p3)
    r10 = rel_bps(m['mid'], p10)
    # Sell distortion then snapback up.
    long_ok = (
        t5['flow'] < -0.24 and t15['flow'] < -0.10 and r10 < -2.0 and r3 > 0.45
        and m['imb10'] > 0.06 and m['microedge_bps'] > 0.05 and ctx['trend15'] >= 0
    )
    # Buy distortion then snapback down.
    short_ok = (
        t5['flow'] > 0.24 and t15['flow'] > 0.10 and r10 > 2.0 and r3 < -0.45
        and m['imb10'] < -0.06 and m['microedge_bps'] < -0.05 and ctx['trend15'] <= 0
    )
    if long_ok and testa_filter(m, hist, 1):
        return 1
    if short_ok and testa_filter(m, hist, -1):
        return -1
    return 0


def hansan_signal(m, t2, t5, t15, hist, ctx):
    p3 = hist_value(hist, 3)
    if p3 is None:
        return 0
    r3 = rel_bps(m['mid'], p3)
    accel = t2['speed'] / max(t15['speed'], 1e-9)
    long_ok = (
        ctx['trend15'] >= 0 and m['mid'] > ctx['level_hi_5m']
        and t2['flow'] > 0.18 and t5['flow'] > 0.12 and r3 > 0.55 and accel > 1.15
        and m['imb10'] > 0.12 and m['microedge_bps'] > 0.08
    )
    short_ok = (
        ctx['trend15'] <= 0 and m['mid'] < ctx['level_lo_5m']
        and t2['flow'] < -0.18 and t5['flow'] < -0.12 and r3 < -0.55 and accel > 1.15
        and m['imb10'] < -0.12 and m['microedge_bps'] < -0.08
    )
    if long_ok and testa_filter(m, hist, 1):
        return 1
    if short_ok and testa_filter(m, hist, -1):
        return -1
    return 0


def cumulative_fill_qty(side, px, trades, since_ms):
    qty = 0.0
    for t in trades:
        if int(t['T']) < since_ms:
            continue
        p = float(t['p'])
        q = float(t['q'])
        buyer_maker = bool(t['m'])
        if side == 1 and buyer_maker and p <= px:
            qty += q
        elif side == -1 and (not buyer_maker) and p >= px:
            qty += q
    return qty


def summarize(st):
    trs = st.get('paper_trades', [])
    def one(rows):
        if not rows:
            return {'closed': 0, 'net_usd_100': 0.0, 'avg_net_bps': None, 'win_pct': None, 'pf': None}
        nets = [float(x['net_bps']) for x in rows]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x < 0]
        pf = sum(wins) / abs(sum(losses)) if losses else (999.0 if wins else None)
        return {
            'closed': len(rows),
            'net_usd_100': sum(float(x['pnl_usd_100']) for x in rows),
            'avg_net_bps': sum(nets) / len(nets),
            'win_pct': 100.0 * len(wins) / len(nets),
            'pf': pf
        }
    st['summary'] = {
        'all': one(trs),
        'jun': one([x for x in trs if x['engine'] == 'jun']),
        'hansan': one([x for x in trs if x['engine'] == 'hansan']),
        'assumptions': {
            'maker_fee_bps': MAKER_FEE_BPS,
            'taker_fee_bps': TAKER_FEE_BPS,
            'taker_slippage_bps': TAKER_SLIPPAGE_BPS,
            'queue_multiplier': QUEUE_MULT,
            'notional_usd': PAPER_NOTIONAL_USD
        }
    }
    daily = {}
    for x in trs:
        day = x['exit_ts'][:10]
        daily.setdefault(day, {'pnl_usd_100': 0.0, 'trades': 0})
        daily[day]['pnl_usd_100'] += float(x['pnl_usd_100'])
        daily[day]['trades'] += 1
    st['daily'] = daily


def close_trade(st, op, m, reason):
    side = op['side']
    exit_px = m['bb'] if side == 1 else m['ba']
    gross_bps = side * (exit_px / op['entry_px'] - 1.0) * 1e4
    net_bps = gross_bps - MAKER_FEE_BPS - TAKER_FEE_BPS - TAKER_SLIPPAGE_BPS
    pnl = PAPER_NOTIONAL_USD * net_bps / 1e4
    st['paper_trades'].append({
        **op, 'exit_ts': utcnow(), 'exit_px': exit_px, 'gross_bps': gross_bps,
        'net_bps': net_bps, 'pnl_usd_100': pnl, 'reason': reason
    })
    st['paper_trades'] = st['paper_trades'][-3000:]
    return None


def main():
    st = load_state()
    st['runs'] = int(st.get('runs', 0)) + 1
    ctx = completed_context()
    hist = deque(maxlen=300)
    candidate = None
    op = None
    started = time.time()
    last_ctx_refresh = started
    last_signal_key = None
    signal_streak = 0

    while time.time() - started < SAMPLE_SECONDS:
        try:
            if time.time() - last_ctx_refresh > 60:
                ctx = completed_context()
                last_ctx_refresh = time.time()

            m = book_metrics(get_depth())
            trades = get_trades(1000)
            t2 = trade_window(trades, 2)
            t5 = trade_window(trades, 5)
            t15 = trade_window(trades, 15)
            row = {**m, 'wall_ts': time.time(), 'flow2': t2['flow'], 'flow5': t5['flow']}
            hist.append(row)
            st['observations'] = int(st.get('observations', 0)) + 1
            elapsed = time.time() - started
            now_ms = int(time.time() * 1000)

            if op is not None:
                age = time.time() - op['fill_wall_ts']
                favorable_bps = op['side'] * (m['mid'] / op['entry_px'] - 1.0) * 1e4
                max_hold = JUN_MAX_HOLD_SEC if op['engine'] == 'jun' else HANSAN_MAX_HOLD_SEC
                target = JUN_TARGET_BPS if op['engine'] == 'jun' else HANSAN_TARGET_BPS
                adverse_book = (op['side'] == 1 and m['imb10'] < -0.10) or (op['side'] == -1 and m['imb10'] > 0.10)
                if favorable_bps <= -HARD_STOP_BPS:
                    op = close_trade(st, op, m, 'hard_stop')
                elif age >= IMMEDIATE_CHECK_SEC and op['best_favorable_bps'] < IMMEDIATE_FAVORABLE_BPS:
                    op = close_trade(st, op, m, 'jun_immediate_invalidation')
                elif adverse_book:
                    op = close_trade(st, op, m, 'testa_book_flip')
                elif favorable_bps >= target:
                    op = close_trade(st, op, m, 'target')
                elif age >= max_hold:
                    op = close_trade(st, op, m, 'time_stop')
                else:
                    op['best_favorable_bps'] = max(op['best_favorable_bps'], favorable_bps)

            if op is None and candidate is not None:
                filled_qty = cumulative_fill_qty(candidate['side'], candidate['px'], trades, candidate['since_ms'])
                if filled_qty >= candidate['queue_ahead_qty'] * QUEUE_MULT:
                    op = {
                        'engine': candidate['engine'], 'side': candidate['side'], 'entry_px': candidate['px'],
                        'entry_ts': utcnow(), 'fill_wall_ts': time.time(), 'best_favorable_bps': 0.0,
                        'entry_imb10': candidate['imb10'], 'entry_microedge_bps': candidate['microedge_bps'],
                        'entry_flow5': candidate['flow5'], 'entry_spread_bps': candidate['spread_bps'],
                        'queue_ahead_qty': candidate['queue_ahead_qty']
                    }
                    candidate = None
                elif time.time() - candidate['created_wall_ts'] > candidate['ttl_sec']:
                    candidate = None

            if op is None and candidate is None and elapsed < NEW_ENTRY_CUTOFF_SEC:
                js = jun_signal(m, t2, t5, t15, hist, ctx)
                hs = hansan_signal(m, t2, t5, t15, hist, ctx)
                # Conflict => no trade. Same-direction agreement => Hansan label but stronger quality.
                engine = None
                side = 0
                if js and hs and js != hs:
                    engine = None
                elif hs:
                    engine, side = 'hansan', hs
                elif js:
                    engine, side = 'jun', js

                key = (engine, side)
                if engine:
                    if key == last_signal_key:
                        signal_streak += 1
                    else:
                        last_signal_key = key
                        signal_streak = 1
                    # Require two consecutive observations: copy Testa's patience, reject one-frame noise.
                    if signal_streak >= 2:
                        px = m['bb'] if side == 1 else m['ba']
                        queue = m['bq'] if side == 1 else m['aq']
                        candidate = {
                            'engine': engine, 'side': side, 'px': px, 'queue_ahead_qty': queue,
                            'since_ms': now_ms, 'created_wall_ts': time.time(),
                            'ttl_sec': 5 if engine == 'hansan' else 8,
                            'imb10': m['imb10'], 'microedge_bps': m['microedge_bps'],
                            'flow5': t5['flow'], 'spread_bps': m['spread_bps']
                        }
                        st['signals'][engine] = int(st['signals'].get(engine, 0)) + 1
                        signal_streak = 0
                        last_signal_key = None
                else:
                    signal_streak = 0
                    last_signal_key = None

            time.sleep(POLL_SEC)
        except Exception as e:
            st['last_error'] = repr(e)
            time.sleep(2)

    # Never leave an unmonitored scalp between scheduled runs.
    if op is not None:
        try:
            m = book_metrics(get_depth())
            op = close_trade(st, op, m, 'run_end_forced_exit')
        except Exception as e:
            st['last_error'] = repr(e)
    summarize(st)
    st['last_context'] = ctx
    save_state(st)
    print(json.dumps(st, indent=2))


if __name__ == '__main__':
    main()
