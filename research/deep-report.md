# Deep research report

- Runs analysed: **31**
- Candidates per run: ~142
- Cumulative candidates evaluated: **4,078**
- Window: 2026-10-20 00:41:09+00:00 -> 2026-10-26 20:36:14+00:00

## Is the search actually working?

- Train-vs-holdout rank correlation: **0.4072** (min 0.1793, max 0.612, over 31 runs)
- Verdict: **search is informative**

> A correlation near 0 means the backtest ranking carries no information about future performance - the search is noise. Above 0.3 means the search genuinely finds something.

## Strategy families

| family | mean holdout CAGR | median | mean DD | mean DSR | max DSR | times basic | times gate |
|---|---|---|---|---|---|---|---|
| `sma_trend` | 20.8% | 21.3% | -16.4% | 0.154 | 0.203 | 341 | 0 |
| `sma_trend_vol_filter` | 15.0% | 14.5% | -10.4% | 0.130 | 0.569 | 1036 | 0 |
| `sma_trend_efficiency` | 13.0% | 12.4% | -13.7% | 0.253 | 0.478 | 420 | 0 |
| `ema_trend` | 12.4% | 12.4% | -15.2% | 0.110 | 0.155 | 186 | 0 |
| `sma_trend_slope` | 8.7% | 9.5% | -14.3% | 0.104 | 0.177 | 311 | 0 |
| `ema_trend_regime` | 9.5% | 9.5% | -11.1% | 0.062 | 0.187 | 257 | 0 |
| `sma_trend_fast_slow` | 7.1% | 8.9% | -16.4% | 0.128 | 0.217 | 322 | 0 |
| `sma_trend_confirm` | 7.7% | 5.6% | -16.5% | 0.095 | 0.172 | 196 | 0 |
| `donchian_trend` | 2.4% | 1.9% | -4.2% | 0.302 | 0.429 | 62 | 0 |
| `donchian_trend_vol_filter` | 1.4% | 1.1% | -3.0% | 0.085 | 0.311 | 62 | 0 |
| `aapl_scalp_reversion` | 0.1% | 0.0% | -0.0% | 0.000 | 0.002 | 0 | 0 |
| `aapl_scalp_trend` | -5.0% | -2.7% | -4.0% | 0.037 | 0.215 | 3 | 0 |
| `sma_trend_rsi_filter` | -5.2% | -5.7% | -16.8% | 0.036 | 0.097 | 3 | 0 |

## Consistent winners (positive in every run seen)

| strategy | runs | positive | mean holdout | worst holdout | mean DSR | assets+ |
|---|---|---|---|---|---|---|
| `sma_trend_period200` | 31 | 31 | 30.4% | 28.1% | 0.151 | 9.5 |
| `sma_trend_vol_filter_period200_vol_lookback30_vol_max_pct4.0` | 31 | 31 | 30.2% | 27.4% | 0.414 | 8.5 |
| `sma_trend_vol_filter_period175_vol_lookback30_vol_max_pct4.0` | 31 | 31 | 28.0% | 25.5% | 0.437 | 7.5 |
| `sma_trend_period50` | 31 | 31 | 27.9% | 25.4% | 0.193 | 8.5 |
| `sma_trend_period175` | 31 | 31 | 27.9% | 25.7% | 0.118 | 8.5 |
| `sma_trend_vol_filter_period200_vol_lookback30_vol_max_pct3.0` | 31 | 31 | 27.4% | 25.0% | 0.150 | 9.5 |
| `sma_trend_vol_filter_period100_vol_lookback30_vol_max_pct3.0` | 31 | 31 | 25.9% | 24.5% | 0.427 | 10.5 |
| `sma_trend_period225` | 31 | 31 | 25.7% | 23.1% | 0.148 | 9.5 |
| `sma_trend_vol_filter_period225_vol_lookback30_vol_max_pct4.0` | 31 | 31 | 25.7% | 22.7% | 0.426 | 8.5 |
| `sma_trend_efficiency_period200_er_n30_er_min0.2` | 28 | 28 | 24.0% | 21.3% | 0.183 | 9.6 |

## Repeat overfits

| strategy | times | mean train | mean holdout |
|---|---|---|---|
| `sma_trend_fast_slow_slow_period250_fast_period10` | 31 | 6.9% | -4.5% |
| `sma_trend_efficiency_period250_er_n20_er_min0.3` | 28 | 6.8% | -1.5% |
| `sma_trend_fast_slow_slow_period150_fast_period10` | 19 | 5.0% | -1.9% |
| `sma_trend_slope_period100_slope_bars10` | 13 | 13.5% | -1.5% |
| `sma_trend_confirm_slow_period150_fast_period50` | 12 | 12.7% | -0.7% |
| `sma_trend_confirm_slow_period100_fast_period20` | 12 | 10.1% | -0.7% |
| `sma_trend_slope_period150_slope_bars40` | 12 | 13.2% | -3.4% |
| `sma_trend_confirm_slow_period100_fast_period50` | 4 | 10.9% | -0.2% |
| `aapl_scalp_trend_sma_period200_ema_period21_rsi_period14_rsi_max55` | 3 | -8.3% | -14.8% |
| `sma_trend_rsi_filter_period150_rsi_period14_rsi_max80` | 3 | 10.5% | -2.5% |

## What to try next

- KEEP: `sma_trend_period200` was positive in 31/31 runs (mean holdout 30.4%, worst 28.1%, mean DSR 0.151)
- KEEP: `sma_trend_vol_filter_period200_vol_lookback30_vol_max_pct4.0` was positive in 31/31 runs (mean holdout 30.2%, worst 27.4%, mean DSR 0.414)
- KEEP: `sma_trend_vol_filter_period175_vol_lookback30_vol_max_pct4.0` was positive in 31/31 runs (mean holdout 28.0%, worst 25.5%, mean DSR 0.437)
- KEEP: `sma_trend_period50` was positive in 31/31 runs (mean holdout 27.9%, worst 25.4%, mean DSR 0.193)
- KEEP: `sma_trend_period175` was positive in 31/31 runs (mean holdout 27.9%, worst 25.7%, mean DSR 0.118)
- FAMILY `sma_trend` has the best median holdout CAGR (21.3%) - add more parameters to it
- FAMILY `sma_trend_vol_filter` has the best median holdout CAGR (14.5%) - add more parameters to it
- FAMILY `sma_trend_efficiency` has the best median holdout CAGR (12.4%) - add more parameters to it
- FAMILY `ema_trend` has the best median holdout CAGR (12.4%) - add more parameters to it

