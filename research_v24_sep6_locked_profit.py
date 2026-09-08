from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

import research_v19_japan_tick_clone as v19
import research_v20_japan_event_clone as v20

DAY = '2026-09-06'
STATE = Path('data/v24_sep6_locked_profit_state.json')
START_EQUITY = 100.0
ROUNDTRIP_FEE_BPS = 7.5   # maker-entry + taker-exit/slippage research hurdle used in prior lineage
STOP_GROSS_BPS = -7.0
PROFIT_ARM_GROSS_BPS = ROUNDTRIP_FEE_BPS + 3.0
TRAIL_BPS = 3.0
MAX_HOLD_SEC = 1800  # 30 minutes; profitable exits below fee remain forbidden


def side_fav_bps(side: int, px: float, entry: float) -> float:
    return side * (px / entry - 1.0) * 1e4


def simulate_day(y: pd.DataFrame, sigs: pd.DataFrame):
    equity = START_EQUITY
    rows = []
    i = 0
    n = len(y)
    idx = y.index

    while i < n - 2:
        side = int(sigs.side.iloc[i]) if pd.notna(sigs.side.iloc[i]) else 0
        eng = sigs.engine.iloc[i]
        if side == 0 or eng is None:
            i += 1
            continue

        entry_i = i + 1
        entry = float(y.open.iloc[entry_i])
        eq_before = equity
        peak_fav = -1e9
        armed = False
        exit_i = None
        gross = None
        reason = None
        max_j = min(entry_i + MAX_HOLD_SEC, n - 1)

        for j in range(entry_i, max_j + 1):
            hi = float(y.high.iloc[j])
            lo = float(y.low.iloc[j])

            # pessimistic stop-first handling inside each 1-second bucket
            adverse = side_fav_bps(side, lo if side == 1 else hi, entry)
            favorable = side_fav_bps(side, hi if side == 1 else lo, entry)
            peak_fav = max(peak_fav, favorable)

            if adverse <= STOP_GROSS_BPS:
                gross = STOP_GROSS_BPS
                exit_i = j
                reason = 'stop'
                break

            if peak_fav >= PROFIT_ARM_GROSS_BPS:
                armed = True

            if armed:
                mark = float(y.close.iloc[j])
                cur = side_fav_bps(side, mark, entry)
                # only permit profitable exit if gross profit still exceeds all round-trip fees
                if cur > ROUNDTRIP_FEE_BPS and peak_fav - cur >= TRAIL_BPS:
                    gross = cur
                    exit_i = j
                    reason = 'fee_cleared_trailing_profit'
                    break

        if exit_i is None:
            # At max hold, do NOT take a sub-fee profit. Continue until one of:
            # stop, fee-cleared trailing exit, or end of the 24h test.
            for j in range(max_j + 1, n):
                hi = float(y.high.iloc[j]); lo = float(y.low.iloc[j])
                adverse = side_fav_bps(side, lo if side == 1 else hi, entry)
                favorable = side_fav_bps(side, hi if side == 1 else lo, entry)
                peak_fav = max(peak_fav, favorable)
                if adverse <= STOP_GROSS_BPS:
                    gross = STOP_GROSS_BPS; exit_i = j; reason = 'stop_extended'; break
                if peak_fav >= PROFIT_ARM_GROSS_BPS:
                    armed = True
                if armed:
                    cur = side_fav_bps(side, float(y.close.iloc[j]), entry)
                    if cur > ROUNDTRIP_FEE_BPS and peak_fav - cur >= TRAIL_BPS:
                        gross = cur; exit_i = j; reason = 'fee_cleared_trailing_profit_extended'; break

        if exit_i is None:
            exit_i = n - 1
            gross = side_fav_bps(side, float(y.close.iloc[exit_i]), entry)
            reason = 'end_of_day_force_close'

        net_bps = gross - ROUNDTRIP_FEE_BPS
        pnl = equity * net_bps / 1e4
        equity += pnl
        rows.append({
            'signal_ts': str(idx[i]),
            'entry_ts': str(idx[entry_i]),
            'exit_ts': str(idx[exit_i]),
            'engine': eng,
            'side': side,
            'entry_price': entry,
            'gross_bps': gross,
            'fee_bps': ROUNDTRIP_FEE_BPS,
            'net_bps': net_bps,
            'pnl_usd': pnl,
            'equity_before': eq_before,
            'equity_after': equity,
            'peak_favorable_bps': peak_fav,
            'reason': reason,
            'hold_sec': int(exit_i - entry_i),
        })
        i = exit_i + 2

    return pd.DataFrame(rows), equity


def stats(t: pd.DataFrame, final_equity: float):
    if t.empty:
        return {
            'start_equity': START_EQUITY,
            'final_equity': final_equity,
            'net_pnl_usd': final_equity - START_EQUITY,
            'return_pct': (final_equity / START_EQUITY - 1) * 100,
            'trades': 0,
            'wins': 0,
            'losses': 0,
            'win_pct': None,
            'pf': None,
            'avg_net_bps': None,
            'max_drawdown_pct': None,
        }
    wins = t[t.net_bps > 0]
    losses = t[t.net_bps < 0]
    pf = wins.pnl_usd.sum() / abs(losses.pnl_usd.sum()) if len(losses) else (999.0 if len(wins) else None)
    curve = np.r_[START_EQUITY, t.equity_after.to_numpy(float)]
    peak = np.maximum.accumulate(curve)
    dd = (curve / peak - 1) * 100
    return {
        'start_equity': START_EQUITY,
        'final_equity': float(final_equity),
        'net_pnl_usd': float(final_equity - START_EQUITY),
        'return_pct': float((final_equity / START_EQUITY - 1) * 100),
        'trades': int(len(t)),
        'wins': int(len(wins)),
        'losses': int(len(losses)),
        'win_pct': float(len(wins) / len(t) * 100),
        'pf': float(pf) if pf is not None else None,
        'avg_net_bps': float(t.net_bps.mean()),
        'median_net_bps': float(t.net_bps.median()),
        'max_drawdown_pct': float(abs(dd.min())),
        'jun_trades': int((t.engine == 'jun').sum()),
        'hansan_trades': int((t.engine == 'hansan').sum()),
        'avg_hold_sec': float(t.hold_sec.mean()),
        'max_hold_sec': int(t.hold_sec.max()),
        'profit_exits': int(t.reason.str.contains('profit').sum()),
        'stop_exits': int(t.reason.str.contains('stop').sum()),
        'eod_exits': int((t.reason == 'end_of_day_force_close').sum()),
    }


def main():
    raw = v19.fetch_day(DAY)
    y = v19.features(raw)
    # Jun + Hansan public-method event engine, with v19 Testa-style tape/liquidity proxy.
    sigs = v20.events(y)
    trades, final_equity = simulate_day(y, sigs)
    out = {
        'version': 'quantbot-v24-sep6-locked-profit',
        'date_utc': DAY,
        'symbol': 'BTCUSDT',
        'starting_capital_usd': START_EQUITY,
        'position_sizing': '100% of current equity, one position at a time, no leverage',
        'method': {
            'jun': 'distortion -> first snapback event',
            'hansan': 'first local-level breakout + acceleration',
            'testa': 'historical tape/liquidity activity proxy (not true historical L2)',
        },
        'exit_lock': {
            'roundtrip_fee_bps': ROUNDTRIP_FEE_BPS,
            'stop_gross_bps': STOP_GROSS_BPS,
            'profit_arm_gross_bps': PROFIT_ARM_GROSS_BPS,
            'trail_bps': TRAIL_BPS,
            'rule': 'no profitable exit allowed unless gross profit exceeds total fee; after fee+3bps is reached, use 3bps trailing exit',
        },
        'summary': stats(trades, final_equity),
        'trades': trades.to_dict(orient='records'),
        'live_orders': False,
        'interpretation': 'SINGLE_DAY_DIAGNOSTIC_ONLY_NOT_LIVE_AUTHORIZATION',
    }
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
