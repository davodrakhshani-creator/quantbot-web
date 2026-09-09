# DARA Astro-First Directional Policy

This policy is applied **before** any 1m/5m/15m BTC Futures scalp selection.

## 1. Pre-market astro pass
For each Tehran trading day, sample the 11-factor geocentric grid at 00:00, 06:00, 12:00 and 18:00 Tehran time.

Factors:
Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto, True Lunar Node.

Use:
- sign/location state,
- all 55 pairwise major aspects,
- aspect strength/exactness,
- retrograde/direct context where available,
- recurrent BTC relationships learned from 2022-2024, validated on 2025 and checked OOS on Jan-Aug 2026.

Astro is a research timing/context layer, not a causal claim and never an entry trigger by itself.

## 2. Day Bias
The astro layer produces one of:
- STRONG LONG
- MODERATE LONG
- NEUTRAL
- MODERATE SHORT
- STRONG SHORT

### STRONG LONG
- Target directional opportunity mix: 75% LONG / 25% SHORT.
- LONG risk cap per full-stop trade: 0.25% equity.
- SHORT risk: 0.10% equity.
- LONG entry threshold gets slightly easier (-0.08 score delta).
- SHORT entry threshold gets materially harder (+0.14 score delta).

### MODERATE LONG
- 65% LONG / 35% SHORT.
- LONG risk: 0.23%.
- SHORT risk: 0.14%.
- LONG entry threshold -0.04; SHORT +0.08.

### NEUTRAL
- 50% / 50%.
- Both sides risk 0.20%.
- No astro threshold adjustment.

### MODERATE SHORT
Mirror of MODERATE LONG.

### STRONG SHORT
Mirror of STRONG LONG.

These percentages are **preferences, not forced trade quotas**. DARA must never invent a trade just to hit a ratio.

## 3. Intraday engine still decides entries
After the day bias is frozen, each trade must still pass:
1. 1m/5m/15m market structure,
2. price action / technical Pattern Memory,
3. Prototype Memory similarity to historical wins,
4. taker-flow / delta proxy,
5. volatility / movement capacity,
6. setup-specific confirmation,
7. fee-capacity gate.

A LONG day does not mean every LONG is valid. It means valid LONG setups are preferred, while counter-bias SHORT setups need stronger evidence and use smaller risk.

## 4. Fee and risk invariants
- Modeled all-in round-trip friction remains 0.11% of notional.
- Normal profitable exits remain forbidden below the existing 3x-fee floor (~0.33% gross move).
- Adaptive target can prefer ~0.44% where capacity supports it.
- Hard risk cap is never increased above 0.25% equity per full-stop trade because of astro.
- Daily loss stop remains -1% unless separately changed by validated risk research.
- No martingale / averaging losers.

## 5. Activity Bias is separate from Direction Bias
Astro also outputs activity regime:
- HIGH_ACTIVITY
- NORMAL_ACTIVITY
- LOW_ACTIVITY

This controls selectivity/target expectations, not LONG/SHORT direction.

Example: a day can be SHORT-biased but LOW_ACTIVITY. DARA should then prefer SHORT setups but demand tighter execution and fewer trades rather than forcing many shorts.

## 6. Order of operations
**11-factor Astro Day Bias → Activity Regime → 15m/5m Context → 1m Setup → Pattern/Prototype/Flow confirmation → Risk weighting → Fee-capacity gate → Trade.**

This ordering is now the intended DARA decision architecture.
