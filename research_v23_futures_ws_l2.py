from __future__ import annotations

import asyncio, json, time
from collections import deque, Counter
from datetime import datetime, timezone
from pathlib import Path
import websockets

SYMBOL = 'BTCUSDT'
STATE = Path('data/v23_futures_ws_l2_state.json')
WS = ('wss://fstream.binance.com/stream?streams='
      'btcusdt@depth20@100ms/btcusdt@aggTrade/btcusdt@bookTicker')
RUN_SECONDS = 210
PAPER_NOTIONAL_USD = 100.0
MAKER_FEE_BPS = 2.0
TAKER_FEE_BPS = 5.0
SLIPPAGE_BPS = 0.5
QUEUE_MULT = 1.50
FILL_TIMEOUT_SEC = 5
MAX_HOLD_SEC = 90
NEW_ENTRY_CUTOFF_SEC = RUN_SECONDS - MAX_HOLD_SEC - FILL_TIMEOUT_SEC - 10
TARGET_BPS = {'jun': 10.0, 'hansan': 14.0}
HARD_STOP_BPS = 8.0
IMMEDIATE_CHECK_SEC = 10
IMMEDIATE_MIN_BPS = 0.5


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {
        'version': 'quantbot-v23-futures-ws-l2',
        'purpose': 'FORWARD_ONLY_FUTURES_L2_EXECUTION_EDGE_PAPER_NO_LIVE',
        'symbol': SYMBOL,
        'live_orders': False,
        'runs': 0,
        'ws_success_runs': 0,
        'observations': 0,
        'depth_events': 0,
        'aggtrade_events': 0,
        'signals': {'jun': 0, 'hansan': 0},
        'maker_candidates': 0,
        'maker_fills': 0,
        'paper_trades': [],
        'rejections': {},
        'stream_counts': {},
        'event_type_counts': {},
        'created_at': utcnow(),
    }


def save_state(st):
    st['updated_at'] = utcnow()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2))


def book_metrics(d):
    bids_raw = d.get('bids') if isinstance(d.get('bids'), list) else d.get('b')
    asks_raw = d.get('asks') if isinstance(d.get('asks'), list) else d.get('a')
    if not isinstance(bids_raw, list) or not isinstance(asks_raw, list):
        return None
    bids = [(float(p), float(q)) for p, q in bids_raw[:10]]
    asks = [(float(p), float(q)) for p, q in asks_raw[:10]]
    if not bids or not asks:
        return None
    bb, bq = bids[0]; ba, aq = asks[0]
    mid = (bb + ba) / 2
    spread_bps = (ba - bb) / mid * 1e4
    b3 = sum(q for _, q in bids[:3]); a3 = sum(q for _, q in asks[:3])
    b10 = sum(q for _, q in bids); a10 = sum(q for _, q in asks)
    imb3 = (b3 - a3) / (b3 + a3) if b3 + a3 else 0.0
    imb10 = (b10 - a10) / (b10 + a10) if b10 + a10 else 0.0
    micro = (ba * bq + bb * aq) / (bq + aq) if bq + aq else mid
    microedge = (micro / mid - 1) * 1e4
    return {
        'bb': bb, 'ba': ba, 'bq': bq, 'aq': aq, 'mid': mid,
        'spread_bps': spread_bps, 'imb3': imb3, 'imb10': imb10,
        'microedge_bps': microedge,
        'bid_usd': sum(p*q for p,q in bids),
        'ask_usd': sum(p*q for p,q in asks),
    }


def prune_tape(tape, now_ms, sec=30):
    cutoff = now_ms - sec*1000
    while tape and tape[0]['T'] < cutoff:
        tape.popleft()


def tape_window(tape, now_ms, sec):
    cutoff = now_ms - sec*1000
    rows = [x for x in tape if x['T'] >= cutoff]
    if not rows:
        return {'flow': 0.0, 'notional': 0.0, 'count': 0, 'speed': 0.0}
    buy = sum(x['notional'] for x in rows if not x['m'])
    sell = sum(x['notional'] for x in rows if x['m'])
    total = buy + sell
    return {'flow': (buy-sell)/total if total else 0.0,
            'notional': total, 'count': len(rows), 'speed': len(rows)/sec}


def hist_price(hist, seconds_ago):
    if not hist:
        return None
    cutoff = time.time() - seconds_ago
    for x in reversed(hist):
        if x['wall_ts'] <= cutoff:
            return x['mid']
    return None


def rel_bps(a, b):
    return (a/b - 1.0)*1e4 if b else 0.0


def l2_support(hist, cur, side):
    if len(hist) < 8:
        return False
    old = list(hist)[-8]
    if cur['spread_bps'] <= 0 or cur['spread_bps'] > 1.5:
        return False
    if side == 1:
        return (cur['imb10'] > 0.07 and cur['microedge_bps'] > 0.02
                and cur['bid_usd'] >= 0.60*old['bid_usd'])
    return (cur['imb10'] < -0.07 and cur['microedge_bps'] < -0.02
            and cur['ask_usd'] >= 0.60*old['ask_usd'])


def summarize(st):
    rows = st.get('paper_trades', [])
    if not rows:
        st['summary'] = {'closed': 0, 'net_usd_100': 0.0, 'avg_net_bps': None,
                         'win_pct': None, 'pf': None, 'maker_fill_rate_pct':
                         (100*st['maker_fills']/st['maker_candidates'] if st['maker_candidates'] else None)}
        return
    nets = [float(x['net_bps']) for x in rows]
    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x < 0]
    pf = sum(wins)/abs(sum(losses)) if losses else (999.0 if wins else None)
    st['summary'] = {
        'closed': len(rows), 'net_usd_100': sum(float(x['pnl_usd_100']) for x in rows),
        'avg_net_bps': sum(nets)/len(nets), 'win_pct': 100*len(wins)/len(nets), 'pf': pf,
        'maker_fill_rate_pct': 100*st['maker_fills']/st['maker_candidates'] if st['maker_candidates'] else None,
    }


async def main():
    st = load_state(); st['runs'] = int(st.get('runs',0)) + 1
    tape = deque(); hist = deque(maxlen=600); rejects = Counter(st.get('rejections', {}))
    stream_counts = Counter(st.get('stream_counts', {})); event_counts = Counter(st.get('event_type_counts', {}))
    latest = None; candidate = None; op = None
    jun_arm_long = jun_arm_short = 0.0
    last_signal = {'jun': 0.0, 'hansan': 0.0}
    last_flush = 0.0; started = time.time(); ws_ok = False

    try:
        async with websockets.connect(WS, open_timeout=12, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
            ws_ok = True; st['ws_success_runs'] = int(st.get('ws_success_runs',0)) + 1
            while time.time() - started < RUN_SECONDS:
                timeout = min(2.0, RUN_SECONDS - (time.time()-started))
                if timeout <= 0: break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                obj = json.loads(msg); stream = str(obj.get('stream','')); d = obj.get('data', obj)
                stream_key = stream.lower(); etype = str(d.get('e','')).lower() if isinstance(d, dict) else ''
                stream_counts[stream_key or '(raw)'] += 1
                event_counts[etype or '(none)'] += 1
                now = time.time(); now_ms = int(time.time()*1000)

                if '@depth' in stream_key or etype == 'depthupdate':
                    m = book_metrics(d)
                    if m:
                        latest = m; st['depth_events'] = int(st.get('depth_events',0)) + 1
                    else:
                        rejects['depth_parse'] += 1
                elif '@aggtrade' in stream_key or etype == 'aggtrade':
                    try:
                        tr = {'T': int(d.get('T', d.get('E'))), 'p': float(d['p']), 'q': float(d['q']), 'm': bool(d['m'])}
                        tr['notional'] = tr['p']*tr['q']; tape.append(tr)
                        st['aggtrade_events'] = int(st.get('aggtrade_events',0)) + 1
                        prune_tape(tape, tr['T'])
                        if candidate and tr['T'] >= candidate['created_ms']:
                            hit = ((candidate['side']==1 and tr['m'] and tr['p'] <= candidate['entry_px']) or
                                   (candidate['side']==-1 and (not tr['m']) and tr['p'] >= candidate['entry_px']))
                            if hit:
                                candidate['through_qty'] += tr['q']
                                if candidate['through_qty'] >= candidate['queue_ahead_qty']:
                                    op = {'engine': candidate['engine'], 'side': candidate['side'],
                                          'entry_px': candidate['entry_px'], 'fill_ts': now,
                                          'signal_ts': candidate['signal_ts']}
                                    st['maker_fills'] = int(st.get('maker_fills',0)) + 1
                                    candidate = None
                    except Exception as e:
                        rejects['aggtrade_parse'] += 1
                        st['last_aggtrade_parse_error'] = repr(e)

                if latest is None or now - last_flush < 1.0:
                    continue
                last_flush = now; st['observations'] = int(st.get('observations',0)) + 1
                t2 = tape_window(tape, now_ms, 2); t5 = tape_window(tape, now_ms, 5); t15 = tape_window(tape, now_ms, 15)
                row = {**latest, 'wall_ts': now, 'flow2': t2['flow'], 'flow5': t5['flow'], 'speed2': t2['speed'], 'speed15': t15['speed']}
                hist.append(row)
                p3 = hist_price(hist,3); p10 = hist_price(hist,10)
                r3 = rel_bps(latest['mid'], p3) if p3 else 0.0
                r10 = rel_bps(latest['mid'], p10) if p10 else 0.0

                if t5['flow'] < -0.30 and r10 < -2.5: jun_arm_long = now + 8
                if t5['flow'] > 0.30 and r10 > 2.5: jun_arm_short = now + 8
                signal = None
                if now <= jun_arm_long and r3 > 0.35 and t2['flow'] > 0.02:
                    if l2_support(hist, latest, 1): signal = ('jun',1)
                    else: rejects['jun_l2'] += 1
                    jun_arm_long = 0
                elif now <= jun_arm_short and r3 < -0.35 and t2['flow'] < -0.02:
                    if l2_support(hist, latest, -1): signal = ('jun',-1)
                    else: rejects['jun_l2'] += 1
                    jun_arm_short = 0

                if signal is None and len(hist) >= 65:
                    prev = list(hist)[:-1][-60:]
                    hi60 = max(x['mid'] for x in prev); lo60 = min(x['mid'] for x in prev)
                    accel = t2['speed']/max(t15['speed'],1e-9)
                    if latest['mid'] > hi60 and t2['flow'] > 0.20 and t5['flow'] > 0.12 and accel > 1.15:
                        if l2_support(hist, latest, 1): signal=('hansan',1)
                        else: rejects['hansan_l2'] += 1
                    elif latest['mid'] < lo60 and t2['flow'] < -0.20 and t5['flow'] < -0.12 and accel > 1.15:
                        if l2_support(hist, latest, -1): signal=('hansan',-1)
                        else: rejects['hansan_l2'] += 1

                elapsed = now - started
                if signal and candidate is None and op is None and elapsed <= NEW_ENTRY_CUTOFF_SEC:
                    eng, side = signal
                    if now-last_signal[eng] >= 45:
                        last_signal[eng]=now; st['signals'][eng]=int(st['signals'].get(eng,0))+1
                        entry_px = latest['bb'] if side==1 else latest['ba']
                        queue = (latest['bq'] if side==1 else latest['aq'])*QUEUE_MULT
                        candidate={'engine':eng,'side':side,'entry_px':entry_px,'queue_ahead_qty':queue,
                                   'through_qty':0.0,'created_ts':now,'created_ms':now_ms,'signal_ts':utcnow()}
                        st['maker_candidates']=int(st.get('maker_candidates',0))+1
                elif signal and elapsed > NEW_ENTRY_CUTOFF_SEC:
                    rejects['end_run_cutoff'] += 1

                if candidate and now-candidate['created_ts'] > FILL_TIMEOUT_SEC:
                    rejects['maker_miss'] += 1; candidate=None

                if op:
                    side=op['side']; exit_px=latest['bb'] if side==1 else latest['ba']
                    gross=side*(exit_px/op['entry_px']-1)*1e4
                    age=now-op['fill_ts']; reason=None
                    if gross >= TARGET_BPS[op['engine']]: reason='target'
                    elif gross <= -HARD_STOP_BPS: reason='hard_stop'
                    elif age >= IMMEDIATE_CHECK_SEC and gross < IMMEDIATE_MIN_BPS: reason='immediate_invalidation'
                    elif age >= MAX_HOLD_SEC: reason='time'
                    if reason:
                        net=gross-MAKER_FEE_BPS-TAKER_FEE_BPS-SLIPPAGE_BPS
                        st['paper_trades'].append({**op,'exit_ts':utcnow(),'exit_px':exit_px,'gross_bps':gross,
                                                  'net_bps':net,'pnl_usd_100':PAPER_NOTIONAL_USD*net/1e4,'reason':reason})
                        st['paper_trades']=st['paper_trades'][-2000:]; op=None

    except Exception as e:
        st['last_error'] = repr(e)
    finally:
        st['ws_access_ok'] = ws_ok
        st['rejections'] = dict(rejects)
        st['stream_counts'] = dict(stream_counts)
        st['event_type_counts'] = dict(event_counts)
        st['new_entry_cutoff_sec'] = NEW_ENTRY_CUTOFF_SEC
        summarize(st); save_state(st)
        print(json.dumps(st, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
