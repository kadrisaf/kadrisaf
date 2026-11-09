# vol_creep gate -- split-sample test -- 2026-11-09

152 compliant symbols, 3y of bars. LIVE entry rules unchanged; each row ADDS a floor on vol_creep (recent 5d avg volume vs the prior 20d) at entry. Both halves shown; only the validation half counts. Follow-up to the 2026-11-09 pre-pop study.

| Gate | Train n | Train win | Train avg | Train PF | Valid n | Valid win | Valid avg | Valid PF |
|---|---|---|---|---|---|---|---|---|
| LIVE baseline (no gate) | 1465 | 50.6% | +0.074% | 1.05 | 1427 | 52.7% | +0.346% | 1.18 |
| + vol_creep >= 1x | 1062 | 50.6% | +0.048% | 1.03 | 1060 | 51.7% | +0.262% | 1.14 |
| + vol_creep >= 1.1x | 860 | 51.0% | +0.077% | 1.05 | 824 | 52.4% | +0.331% | 1.17 |
| + vol_creep >= 1.2x | 671 | 51.6% | +0.080% | 1.05 | 613 | 52.0% | +0.433% | 1.22 |
| + vol_creep >= 1.3x | 517 | 51.1% | -0.010% | 0.99 | 445 | 54.2% | +0.655% | 1.35 |
| + vol_creep >= 1.5x | 281 | 49.1% | -0.035% | 0.98 | 222 | 56.8% | +0.930% | 1.49 |

## Decision rule (written before the data was looked at)

- Adopt a gate only if it beats the LIVE baseline's validation avg by a clear margin (not a few hundredths of a %) AND keeps a comparable trade count -- a gate that filters down to a handful of trades proves nothing.
- The pre-pop lift was measured on POPS (>= +8% days); this table measures actual rule-trades with stops, targets and slippage. Only this table can justify adoption.
- 'No change' is a good result, not a failure.
