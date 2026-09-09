import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import dara_astro_day_bias_policy as astro
import dara_v18_prototype_memory_sep7 as v18
import dara_v19_pattern_fusion_sep7 as v19
import dara_pattern_memory as pm

TEHRAN = v18.TEHRAN
DAY = date(2026, 8, 20)
START = datetime(2026, 8, 20, 0, 0, tzinfo=TEHRAN)
END = datetime(2026, 8, 21, 0, 0, tzinfo=TEHRAN)
S = int(START.astimezone(timezone.utc).timestamp() * 1000)
E = int(END.astimezone(timezone.utc).timestamp() * 1000)
OUT = Path('data/dara_aug20_current_brain_replay.json')

START_EQUITY = 100.0
DAILY_STOP = -0.01


def valid_positive(x):
    return x.get('reason') in ('TP', '3FEE_FLOOR') and x.get('net_pnl', 0) > 0


def stats(xs):
    if not xs:
        return {'n': 0, 'valid_positive': 0, 'net_positive': 0, 'negative_or_invalid': 0,
                'valid_wr': None, 'gross': 0, 'cost': 0, 'net': 0, 'pf_net': None,
                'mfe': None, 'mae': None}
    valid = sum(valid_positive(x) for x in xs)
    netpos = sum(float(x.get('net_pnl', 0)) > 0 for x in xs)
    gp = sum(float(x.get('net_pnl', 0)) for x in xs if float(x.get('net_pnl', 0)) > 0)
    gl = -sum(float(x.get('net_pnl', 0)) for x in xs if float(x.get('net_pnl', 0)) <= 0)
    return {
        'n': len(xs), 'valid_positive': valid, 'net_positive': netpos,
        'negative_or_invalid': len(xs) - valid,
        'valid_wr': round(100 * valid / len(xs), 1),
        'gross': round(sum(float(x.get('gross_pnl', 0)) for x in xs), 6),
        'cost': round(sum(float(x.get('cost', 0)) for x in xs), 6),
        'net': round(sum(float(x.get('net_pnl', 0)) for x in xs), 6),
        'pf_net': round(gp / gl, 2) if gl else (99.0 if gp else None),
        'mfe': round(sum(float(x.get('mfe_pct', 0)) for x in xs) / len(xs), 3),
        'mae': round(sum(float(x.get('mae_pct', 0)) for x in xs) / len(xs), 3),
    }


def max_drawdown(trades, start=100.0):
    eq = start
    peak = start
    md = 0.0
    for x in trades:
        eq += float(x.get('net_pnl', 0))
        peak = max(peak, eq)
        md = max(md, (peak - eq) / peak if peak else 0)
    return round(md * 100, 4)


def group_side(xs):
    return {s: stats([x for x in xs if x.get('side') == s]) for s in ('LONG', 'SHORT')}


def astro_policy(ab, side):
    p = astro.trade_policy_for_side(ab, side)
    # policy values are percent of equity, e.g. 0.25 means 0.25%.
    p['risk_fraction_equity'] = float(p['risk_pct_equity']) / 100.0
    return p


def effective_threshold(base, pol):
    return max(0.10, min(0.60, base + float(pol.get('entry_threshold_delta', 0.0))))


def build_candidate(i, m, f5, f15, side, exz, mu, sd, base_threshold, ab, stage):
    stage['side_minutes_seen'] += 1
    vec = v18.common_vec(i, m, f5, f15, side)
    if vec is None:
        return None
    stage['feature_vector_ready'] += 1
    p, wd, _ = v18.knn_score(v18.zv(vec, mu, sd), exz)
    pol = astro_policy(ab, side)
    th = effective_threshold(base_threshold, pol)
    if p >= th and wd >= 2:
        stage['prototype_pass'] += 1

    ps, primary, weak, matches = v19.pattern_side(m, i, side)
    if matches:
        stage['any_pattern_seen'] += 1
    if primary > 0:
        stage['primary_pattern_seen'] += 1

    z = m[i]
    a5, a15 = v18.b.ctx(z, f5, f15)
    if not a5 or not a15:
        return None
    stage['regime_ready'] += 1
    c5 = 4 * a5['atrp']; c15 = 2.5 * a15['atrp']; cap = .55 * c5 + .45 * c15
    if cap >= .0022 and max(c5, c15) >= .0028:
        stage['capacity_pass'] += 1
    # flow evidence is not a hard gate in v19, but record whether directional taker delta supports the side.
    flow_aligned = (z.get('delta', 0) > 0 if side == 'LONG' else z.get('delta', 0) < 0)
    if flow_aligned:
        stage['instant_flow_aligned'] += 1

    x = v19.fused_candidate(i, m, f5, f15, side, p, wd, th)
    if not x:
        return None

    # Against a non-neutral astro day, do not allow a pattern-only bypass. Opposed trades
    # must also have recurring prototype memory at the stricter astro-adjusted threshold.
    if ab.bias != 'NEUTRAL' and side != ab.bias:
        if not (p >= th and wd >= 2):
            stage['astro_opposed_rejected'] += 1
            return None

    setup, s, stop, ei, score, diag, tp = x
    diag = dict(diag)
    diag.update({
        'astro_day_bias': ab.bias,
        'astro_confidence': ab.confidence,
        'astro_score': ab.score,
        'astro_activity': ab.activity_regime,
        'astro_preference': pol['directional_preference'],
        'astro_aligned': None if ab.bias == 'NEUTRAL' else side == ab.bias,
        'risk_pct_equity': pol['risk_pct_equity'],
        'risk_fraction_equity': pol['risk_fraction_equity'],
        'effective_prototype_threshold': th,
        'instant_flow_aligned': flow_aligned,
    })
    # Preference changes ranking, not a forced quota.
    score = round(score + 8.0 * (float(pol['directional_preference']) - 0.5), 3)
    stage['final_candidate'] += 1
    return (setup, s, stop, ei, score, diag, tp), pol


def simulate_with_policy(m, x, equity, pol):
    old_risk = v18.b.RISK
    old_cost = v18.b.COST
    old_lev = v18.b.MAX_LEV
    try:
        v18.b.RISK = pol['risk_fraction_equity']
        v18.b.COST = v18.COST
        v18.b.MAX_LEV = v18.MAX_LEV
        return v18.b.simulate(m, x[3], x[1], x[2], x[6], equity)
    finally:
        v18.b.RISK = old_risk
        v18.b.COST = old_cost
        v18.b.MAX_LEV = old_lev


def row_from(x, sim, m, equity=None):
    z = v18.row(x, sim, m, equity)
    z['risk_pct_equity'] = x[5]['risk_pct_equity']
    z['astro_aligned'] = x[5]['astro_aligned']
    return z


def main():
    # STEP 1: freeze astro day bias before loading Aug20 price bars.
    ab = astro.day_bias(DAY)

    # STEP 2: build current prototype memory (Sep1-Sep6 learned experience).
    ex, counts, wins = v18.build_training()
    mu, sd = v18.scaling(ex)
    exz = [{**q, 'z': v18.zv(q['vec'], mu, sd)} for q in ex]
    base_threshold, cv = v18.choose_threshold(ex, exz)

    # STEP 3: only now load Aug20 market and enrich pattern indicators.
    m, f5, f15, idx = v18.load_market((2026, 8, 19), (2026, 8, 21))
    pm.enrich_indicators(m)
    daybars = [z for z in m if S <= z['t'] < E]
    actual = None
    if daybars:
        op = daybars[0]['o']; cl = daybars[-1]['c']
        actual = {
            'open': round(op, 2), 'close': round(cl, 2),
            'return_pct': round((cl / op - 1) * 100, 4),
            'high': round(max(z['h'] for z in daybars), 2),
            'low': round(min(z['l'] for z in daybars), 2),
            'range_pct': round((max(z['h'] for z in daybars) / min(z['l'] for z in daybars) - 1) * 100, 4),
            'realized_direction': 'UP' if cl > op else 'DOWN' if cl < op else 'FLAT'
        }

    stage = {k: 0 for k in [
        'side_minutes_seen','feature_vector_ready','prototype_pass','any_pattern_seen',
        'primary_pattern_seen','regime_ready','capacity_pass','instant_flow_aligned',
        'astro_opposed_rejected','final_candidate'
    ]}

    # Opportunity inventory: every qualifying minute, choose the best side at that minute.
    opp = []
    for i, z in enumerate(m):
        if z['t'] < S or z['t'] >= E or i < 35 or i + 1 >= len(m):
            continue
        cs = []
        for side in ('LONG', 'SHORT'):
            q = build_candidate(i, m, f5, f15, side, exz, mu, sd, base_threshold, ab, stage)
            if q:
                cs.append(q)
        if not cs:
            continue
        x, pol = max(cs, key=lambda q: q[0][4])
        sim = simulate_with_policy(m, x, START_EQUITY, pol)
        if not sim or m[sim[0]]['t'] >= E:
            continue
        opp.append(row_from(x, sim, m))

    # Sequential executable account path, one position at a time, $100 start, -1% daily stop.
    seq = []
    eq = START_EQUITY
    i = 0
    daily_stop_hit = False
    while i < len(m) - 2:
        if m[i]['t'] < S:
            i += 1; continue
        if m[i]['t'] >= E:
            break
        if eq <= START_EQUITY * (1 + DAILY_STOP):
            daily_stop_hit = True
            break
        if i < 35:
            i += 1; continue
        cs = []
        # use a private stage counter for sequential so inventory stage counts are not doubled
        dummy = {k: 0 for k in stage}
        for side in ('LONG', 'SHORT'):
            q = build_candidate(i, m, f5, f15, side, exz, mu, sd, base_threshold, ab, dummy)
            if q:
                cs.append(q)
        if not cs:
            i += 1; continue
        x, pol = max(cs, key=lambda q: q[0][4])
        sim = simulate_with_policy(m, x, eq, pol)
        if not sim or m[sim[0]]['t'] >= E:
            break
        rr = row_from(x, sim, m, eq)
        eq = rr['equity_after']
        rr['n'] = len(seq) + 1
        seq.append(rr)
        i = sim[0] + 1

    by_pattern = {}
    for x in opp:
        for p in x.get('diagnostics', {}).get('patterns', []):
            by_pattern.setdefault(p, []).append(x)

    out = {
        'version': 'DARA-Aug20-CurrentBrain-DiagnosticReplay-v1',
        'period_tehran': [START.isoformat(), END.isoformat()],
        'methodology_warning': 'Diagnostic hindsight replay, NOT clean OOS. Aug20-27 was used in older project testing, current prototype/pattern knowledge was learned later, and astro calibration includes data through 2026-08-31.',
        'pipeline_order': [
            '1 Astro day bias (frozen before loading Aug20 bars)',
            '2 Positive/Prototype memory from Sep1-Sep6 experience',
            '3 15m and 5m regime/context',
            '4 1m recognized price-action/technical pattern library',
            '5 1m taker-buy delta/flow proxy',
            '6 volatility/capacity gate',
            '7 fee-aware TP floor >=0.33%, preferred 0.44%',
            '8 astro-weighted risk and side ranking',
            '9 sequential one-position account execution'
        ],
        'astro_forecast': asdict(ab),
        'actual_market_after_forecast': actual,
        'memory': {
            'examples': len(ex), 'valid_positive_examples': sum(q['y'] for q in ex),
            'day_counts': counts, 'day_valid_positives': wins,
            'prototype_threshold_base': base_threshold, 'cross_day_validation': cv,
        },
        'rules': {
            'start_equity': START_EQUITY,
            'modeled_roundtrip_fee': v18.COST,
            'max_exposure': v18.MAX_LEV,
            'daily_stop_pct': DAILY_STOP * 100,
            'normal_profit_floor_pct': 0.33,
            'preferred_profit_pct': 0.44,
            'min_stop_pct': v18.MIN_STOP * 100,
            'max_stop_pct': v18.MAX_STOP * 100,
            'timeframes': '1m/5m/15m only',
            'historical_L2': 'unavailable; 1m taker-buy delta is used as aggressor-flow proxy',
        },
        'stage_counts_opportunity_scan': stage,
        'opportunity_inventory': stats(opp),
        'opportunities_by_side': group_side(opp),
        'sequential': {
            'stats': stats(seq),
            'by_side': group_side(seq),
            'start_equity': START_EQUITY,
            'final_equity': round(eq, 6),
            'return_pct': round((eq / START_EQUITY - 1) * 100, 4),
            'max_drawdown_pct': max_drawdown(seq, START_EQUITY),
            'daily_stop_hit': daily_stop_hit,
            'trades': len(seq),
        },
        'by_pattern': {k: stats(v) for k, v in by_pattern.items()},
        'opportunities': opp,
        'sequential_trades': seq,
    }
    OUT.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(json.dumps({
        'astro': {'bias': ab.bias, 'score': ab.score, 'confidence': ab.confidence, 'activity': ab.activity_regime},
        'actual': actual,
        'stage': stage,
        'opp': out['opportunity_inventory'],
        'opp_side': out['opportunities_by_side'],
        'seq': out['sequential'],
    }, indent=2))

if __name__ == '__main__':
    main()
