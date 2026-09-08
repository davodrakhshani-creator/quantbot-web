# QuantBot v4 — Research Decision

**Status:** STRONG PAPER CANDIDATE — FRESH FORWARD PROOF REQUIRED  
**Official core:** `T2_upup`  
**Live-money authorization:** **NO**

## Frozen rule

Weekly top-3 positive multi-horizon momentum using a 20/60/120/180-day blend. Positions are inverse-volatility weighted to a 12% annualized target, with gross exposure capped at 1x. The strategy is active only when all three BTC regime conditions are true: BTC above its 200-day SMA, BTC 60-day return positive, and BTC 120-day return positive. Otherwise the portfolio is cash.

## Why T2 is the official research core

T2 won the v4 robustness tournament using only data through 2026-04-24 for selection. It passed the current-liquid universe and a fixed 2021 historical snapshot including later-delisted assets. It also passed a broader dynamic point-in-time liquidity audit using a 52-asset historical pool, next-open execution stress, 13/40/75 bps transaction-cost stress, one-day extra execution delay, parameter-neighborhood tests, leave-one-asset-out tests, research-engine red-team checks, and a moving-block bootstrap / multiple-testing screen.

The independent walk-forward ML lane, structural carry lane, and cross-venue funding lane were rejected by their robustness gates and are not part of the core.

## Key evidence

### Current-liquid universe, prelock
- Net: +74.69%
- Annualized: +11.07%
- Sharpe: 1.47
- Profit factor: 1.41
- Max drawdown: 7.10%
- 40 bps stress: +69.30%
- 75 bps stress: +62.57%
- Rolling 180-day meaningful windows positive: 75.0%

### Historical 2021 snapshot, prelock
- Net: +34.75%
- Annualized: +5.78%
- Sharpe: 0.85
- Profit factor: 1.20
- Max drawdown: 7.56%
- 40 bps stress: +29.52%
- 75 bps stress: +23.05%
- Rolling 180-day meaningful windows positive: 64.58%

### Dynamic liquidity universe, 52-asset historical pool
- Net: +34.86%
- Annualized: +5.79%
- Sharpe: 0.88
- Profit factor: 1.22
- Max drawdown: 7.74%
- 40 bps stress: +30.48%
- 75 bps stress: +25.00%
- Rolling 180-day meaningful windows positive: 66.67%

### Statistical and fragility screens
- Reality-check p-value, current universe: ~0.0124
- Reality-check p-value, historical snapshot: ~0.0816
- Block-bootstrap positive-net fraction: 99.76% current / 96.88% snapshot
- Parameter neighborhood: 8/10 passed both prelock universes
- All 10/10 parameter variants remained postlock-positive at 40 bps on both universes
- Leave-one-asset-out: 19/19 current and 14/15 snapshot remained postlock-positive at 40 bps
- Red-team integrity: PASS
- Next-open execution: PASS on both universes

## Forward evidence

Fresh forward paper tracking was frozen before the first completed 2026-09-08 UTC bar. The pre-registered promotion gate is:

- at least 180 calendar observations
- at least 20 active observations
- positive net return
- profit factor >= 1.10
- max drawdown <= 10%
- positive net return under 40 bps transaction-cost stress

Passing this gate permits **manual operational review only**. It does not authorize live orders automatically.

## Governance

Do not retune T2 using forward data. Do not relabel the already-viewed 2026-04-25+ diagnostic period as pristine OOS. Do not merge rejected ML/carry models into the core just to improve headline returns. Do not add live exchange credentials or order placement until the forward gate and a separate execution/operational review have passed.
