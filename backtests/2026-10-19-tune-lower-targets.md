# Split-sample parameter test -- 2026-10-19

161 compliant symbols, 3y of bars. Grid of 5 parameter sets tuned on the FIRST half of history; the top 5 by train avg (min 1 train trades) are then shown against the SECOND half, which tuning never saw. Live defaults always shown.

| Parameters | Train n | Train win | Train avg | Train PF | Valid n | Valid win | Valid avg | Valid PF |
|---|---|---|---|---|---|---|---|---|
| LIVE: RSI 45-65, vol>=1.2x, 2/3 ATR, 5d | 1621 | 52.6% | +0.207% | 1.13 | 1489 | 52.0% | +0.309% | 1.16 |
| RSI 45-65, vol>=1.2x, 2/2.5 ATR, 5d | 1627 | 52.6% | +0.135% | 1.09 | 1499 | 52.2% | +0.275% | 1.14 |
| RSI 45-65, vol>=1.2x, 2/2 ATR, 5d | 1643 | 53.1% | +0.084% | 1.05 | 1527 | 53.2% | +0.237% | 1.13 |
| RSI 45-65, vol>=1.2x, 2/1 ATR, 5d | 1778 | 62.4% | +0.049% | 1.04 | 1635 | 61.7% | +0.065% | 1.04 |
| RSI 45-65, vol>=1.2x, 2/1.5 ATR, 5d | 1695 | 55.2% | +0.034% | 1.02 | 1568 | 55.3% | +0.157% | 1.09 |
| RSI 45-65, vol>=1.2x, 1.5/1.5 ATR, 5d | 1717 | 52.9% | +0.020% | 1.01 | 1597 | 53.4% | +0.145% | 1.08 |

## How to read this

- **Adopt nothing unless** a winner beats LIVE on the validation half by a clear margin (not a few hundredths of a %) with a comparable trade count.
- In-sample winners fading out-of-sample is the EXPECTED outcome under multiple comparisons; 'no change' is a good result, not a failure.
- All caveats of the main backtest apply (look-ahead compliance, survivorship, correlated trades, costs beyond exit slippage not modelled).

