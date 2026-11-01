# Signal backtest -- 2026-11-01

151 currently-compliant symbols, 3 years of daily bars, entry at the signal day's close, 5-day max hold, exit slippage 0.1% on stop/time-stop exits. Live default = 2.0x stop / 3.0x target.

## Signal days (full filter) -- by stop/target multiples

| Stop x ATR / Target x ATR | Trades | Win rate | Avg ret | Median | Avg win / loss | Profit factor | % stop/target/time |
|---|---|---|---|---|---|---|---|
| 1.0 / 1.5 | 2562 | 47.5% | +0.09% | -0.44% | +3.38% / -2.89% | 1.06 | 45.2/34.4/20.4 |
| 1.5 / 2.25 | 2387 | 50.8% | +0.17% | +0.12% | +3.73% / -3.50% | 1.1 | 32.4/20.2/47.4 |
| 2.0 / 3.0 (live) | 2329 | 52.7% | +0.28% | +0.31% | +3.85% / -3.70% | 1.16 | 21.7/11.4/66.9 |
| 1.5 / 3.0 | 2364 | 50.4% | +0.26% | +0.08% | +3.93% / -3.48% | 1.15 | 32.5/11.1/56.4 |
| 1.0 / 3.0 | 2450 | 44.1% | +0.23% | -1.07% | +4.16% / -2.86% | 1.15 | 47.0/10.5/42.5 |

## Any-day baseline (enter every day, same exits) -- is the filter adding anything?

| Stop x ATR / Target x ATR | Trades | Win rate | Avg ret | Median | Avg win / loss | Profit factor | % stop/target/time |
|---|---|---|---|---|---|---|---|
| 1.0 / 1.5 | 26623 | 47.4% | +0.13% | -0.49% | +3.50% / -2.91% | 1.08 | 43.9/31.1/25.0 |
| 1.5 / 2.25 | 21732 | 50.3% | +0.22% | +0.03% | +3.81% / -3.41% | 1.13 | 28.4/16.7/54.8 |
| 2.0 / 3.0 (live) | 20027 | 51.7% | +0.24% | +0.16% | +3.80% / -3.56% | 1.14 | 17.7/7.9/74.3 |
| 1.5 / 3.0 | 21035 | 49.9% | +0.24% | -0.01% | +3.87% / -3.38% | 1.14 | 28.5/7.7/63.8 |
| 1.0 / 3.0 | 23534 | 44.1% | +0.19% | -0.99% | +4.08% / -2.87% | 1.12 | 45.9/7.4/46.7 |

## Regime split: live parameters, by trend regime at entry

| Stop x ATR / Target x ATR | Trades | Win rate | Avg ret | Median | Avg win / loss | Profit factor | % stop/target/time |
|---|---|---|---|---|---|---|---|
| above own 200d SMA | 1964 | 52.6% | +0.27% | +0.29% | +3.80% / -3.64% | 1.16 | 21.8/11.5/66.6 |
| below own 200d SMA | 365 | 53.4% | +0.32% | +0.45% | +4.09% / -4.02% | 1.17 | 20.8/11.0/68.2 |
| SPY above 200d SMA | 2253 | 52.9% | +0.28% | +0.33% | +3.82% / -3.70% | 1.16 | 21.4/11.4/67.2 |
| SPY below 200d SMA | 74 | 48.6% | +0.38% | -0.58% | +4.81% / -3.83% | 1.19 | 29.7/12.2/58.1 |

A regime where the signal loses money is a reason to stand aside, not to re-tune. Small per-regime samples first: check n before believing a split.

## Stability: live parameters, first vs second half of the signal trades (by date)

| Stop x ATR / Target x ATR | Trades | Win rate | Avg ret | Median | Avg win / loss | Profit factor | % stop/target/time |
|---|---|---|---|---|---|---|---|
| first half | 1164 | 51.6% | +0.10% | +0.15% | +3.38% / -3.40% | 1.06 | 23.1/11.6/65.3 |
| second half | 1165 | 53.8% | +0.45% | +0.42% | +4.29% / -4.02% | 1.24 | 20.3/11.2/68.5 |

## Caveats (read before trusting any number above)

- **Look-ahead:** compliance uses today's fundamentals for every historical day.
- **Survivorship:** the universe is today's large caps, many chosen after big runs; that flatters any long-only test.
- **Correlation:** trades on the same days across symbols are not independent, so the real sample is smaller than the trade count.
- **Multiple comparisons:** five stop/target cells were tried; the best-looking one is partly luck. Do not re-tune the live parameters on this table alone.
- **Costs:** only exit slippage is modelled (no spread/fees/FX/tax; Trade Republic charges 1 EUR per order).
- **Execution:** entry at the signal day's close is an idealisation; the live report is read the next morning.
- Skipped (no data / too short): FTG.VI, BSP.
