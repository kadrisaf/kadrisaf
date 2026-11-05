# Live-screen validation -- 2026-11-05

The shadow (intraday) screen's logged signals, replayed through the
same exit engine as the daily plan (stop before target, gap fills at
the open, slippage on market exits, 5-trading-day time-stop; the
signal day itself is judged on its close only, because daily bars
can't order intraday prices around the signal time).

**Standing rule: the live screen stays PAPER ONLY unless this page
shows it clearly beating the end-of-day screen over the same dates.**

## Headline (one trade per symbol per day)

- Shadow signals: 27 resolved, 15 wins (55.6%), avg -0.18%/trade, 17 still pending [exits: stop 5, target 7, time_stop 15]
- EOD screen, same dates: 29 resolved, 17 wins (58.6%), avg +0.20%/trade, 36 still pending

All logged rows (re-scans of the same setup included): 32 resolved, 18 wins (56.2%), avg -0.24%/trade, 25 still pending [exits: stop 6, target 9, time_stop 17]

Signals logged before 2026-11-05 predate the earnings filter and
are graded as-is -- the system really did signal them. Rows the
filter has flagged since then are logged for the counterfactual
and graded separately below, never as qualifiers.

Earnings-flagged signals (what the filter excluded): no resolved trades yet

## Per-signal outcomes (deduped)

| Signalled | Symbol | Entry | Stop | Target | Exit | Reason | Return |
|---|---|---|---|---|---|---|---|
| 2026-10-23 | AAPL | 347.73 | 335.6918 | 365.7873 | 2026-10-29 (348.5611) | time_stop | +0.24% |
| 2026-10-23 | AMGN | 441.5132 | 420.6941 | 472.7419 | 2026-10-29 (460.2693) | time_stop | +4.25% |
| 2026-10-23 | KO | 93.475 | 90.4497 | 98.0129 | 2026-10-29 (92.7272) | time_stop | -0.80% |
| 2026-10-23 | LLY | 1277.288 | 1203.828 | 1387.4779 | 2026-10-29 (1387.4779) | target | +8.63% |
| 2026-10-23 | VRSN | 302.78 | 288.1119 | 324.7821 | 2026-10-29 (305.6441) | time_stop | +0.95% |
| 2026-10-26 | AAPL | 345.33 | 333.0806 | 363.7041 | 2026-10-30 (344.2854) | time_stop | -0.30% |
| 2026-10-26 | CRWD | 262.0767 | 237.4723 | 298.9833 | 2026-10-30 (250.1796) | time_stop | -4.54% |
| 2026-10-26 | DDOG | 280.43 | 251.9692 | 323.1212 | 2026-10-30 (251.7172) | stop | -10.24% |
| 2026-10-26 | KO | 92.28 | 89.2986 | 96.7521 | 2026-10-30 (93.9859) | time_stop | +1.85% |
| 2026-10-26 | LLY | 1284.405 | 1211.8565 | 1393.2279 | 2026-10-29 (1393.2279) | target | +8.47% |
| 2026-10-26 | ORLY | 91.195 | 87.5566 | 96.6525 | 2026-10-29 (87.469) | stop | -4.09% |
| 2026-10-26 | PANW | 392.565 | 355.7424 | 447.7989 | 2026-10-30 (365.5041) | time_stop | -6.89% |
| 2026-10-26 | PLTR | 197.51 | 182.3002 | 220.3247 | 2026-10-30 (182.1179) | stop | -7.79% |
| 2026-10-26 | TSLA | 393.395 | 367.0127 | 432.9684 | 2026-10-30 (399.4102) | time_stop | +1.53% |
| 2026-10-27 | CDNS | 339.56 | 314.6657 | 376.9014 | 2026-11-02 (329.1805) | time_stop | -3.06% |
| 2026-10-27 | CTAS | 215.7 | 206.8969 | 228.9046 | 2026-11-02 (224.5452) | time_stop | +4.10% |
| 2026-10-27 | MAS | 74.89 | 71.0498 | 80.6503 | 2026-10-28 (80.6503) | target | +7.69% |
| 2026-10-27 | NOW | 155.74 | 144.2642 | 172.9536 | 2026-10-29 (120.7891) | stop | -22.44% |
| 2026-10-27 | STLD | 243.16 | 226.2057 | 268.5915 | 2026-11-02 (251.6581) | time_stop | +3.49% |
| 2026-10-27 | TSLA | 405.7635 | 378.7311 | 446.312 | 2026-11-02 (403.4162) | time_stop | -0.58% |
| 2026-10-28 | MAS | 76.32 | 72.4638 | 82.1043 | 2026-10-30 (82.1043) | target | +7.58% |
| 2026-10-28 | NOW | 150.98 | 139.4017 | 168.3475 | 2026-10-29 (120.7891) | stop | -20.00% |
| 2026-10-28 | TSLA | 401.28 | 374.9393 | 440.791 | 2026-11-03 (398.1015) | time_stop | -0.79% |
| 2026-10-29 | ORLY | 87.89 | 84.0854 | 93.5969 | 2026-11-04 (92.2477) | time_stop | +4.96% |
| 2026-10-30 | CL | 96.2986 | 92.4317 | 102.0989 | -- | pending | -- |
| 2026-10-30 | EL | 107.37 | 101.0774 | 116.8089 | -- | pending | -- |
| 2026-10-30 | IDXX | 552.69 | 525.3542 | 593.6937 | 2026-11-02 (593.6937) | target | +7.42% |
| 2026-10-30 | MDLZ | 64.9301 | 62.2803 | 68.9049 | -- | pending | -- |
| 2026-10-30 | SPOT | 587.855 | 544.7406 | 652.5266 | -- | pending | -- |
| 2026-10-30 | SYK | 296.3 | 278.934 | 322.349 | 2026-11-04 (322.349) | target | +8.79% |
| 2026-10-30 | TSLA | 400.33 | 375.4114 | 437.7079 | -- | pending | -- |
| 2026-10-30 | VRSN | 310.29 | 294.2101 | 334.4099 | -- | pending | -- |
| 2026-11-02 | ABBV | 289.497 | 276.81 | 308.5275 | -- | pending | -- |
| 2026-11-02 | EL | 105.68 | 98.7832 | 116.0252 | -- | pending | -- |
| 2026-11-02 | EW | 92.18 | 88.0636 | 98.3547 | 2026-11-04 (98.3547) | target | +6.70% |
| 2026-11-02 | WAT | 450.11 | 421.401 | 493.1735 | -- | pending | -- |
| 2026-11-03 | EPAM | 136.53 | 125.14 | 153.615 | -- | pending | -- |
| 2026-11-04 | AAPL | 353.82 | 336.8638 | 379.2543 | -- | pending | -- |
| 2026-11-04 | AMZN | 269.29 | 252.4516 | 294.5476 | -- | pending | -- |
| 2026-11-04 | EPAM | 133.42 | 121.828 | 150.8079 | -- | pending | -- |
| 2026-11-04 | MSFT | 554.525 | 525.51 | 598.0476 | -- | pending | -- |
| 2026-11-04 | NUE | 264.795 | 247.7051 | 290.4298 | -- | pending | -- |
| 2026-11-04 | TSLA | 390.035 | 363.9343 | 429.1861 | -- | pending | -- |
| 2026-11-04 | UNH | 408.055 | 387.0538 | 439.5568 | -- | pending | -- |

## Entry-value study: what is intraday entry worth?

Each signal graded twice -- from the logged live price and from
the same day's close, identical stop/target levels and hold
window. The delta is the entire value of acting intraday instead
of reading the evening report. Pairs where the live entry
resolved the same day, or the close was already past a level,
aren't comparable and are counted, not compared.

- Comparable pairs: 27 (0 not comparable, 17 pending)
- Avg return from live entry: -0.18%
- Avg return from same-day close entry: -0.12%
- Avg intraday-entry edge: -0.06% per trade (median -0.29%)

## Descriptive breakdowns (NOT rules)

Resolved deduped signals split by logged features. Six
comparisons over a few dozen correlated trades: any bucket can
look good by chance. A split becomes a filter only via the same
logged-counterfactual path the earnings filter took -- flag
first, grade the excluded group, adopt on out-of-sample margin.

| Feature | Bucket | n | Win rate | Avg return |
|---|---|---|---|---|
| score | high | 14 | 50.0% | -0.81% |
| score | low | 13 | 61.5% | +0.49% |
| rel_vol_est | <1.5x | 14 | 50.0% | +0.08% |
| rel_vol_est | >=1.5x | 13 | 61.5% | -0.46% |
| session_elapsed_pct | early session | 23 | 47.8% | -1.37% |
| session_elapsed_pct | late session | 4 | 100.0% | +6.67% |

## When do maxima actually happen? (hindsight measurement)

Day of the highest high inside the hold window, per trade. The
peak day is only knowable AFTERWARD -- an average peak day is
not an exit signal for any single trade, and a standing target
order already exits at the peak's price the moment it trades.
Any time-stop change still has to win in tune.py's split-sample
grid first (the 3/5/8-day grid's in-sample leaders were all 8d).

- EOD tracker candidates (n=108): median peak day 3, avg best excursion +4.43% -- day 1: 26 (24%), day 2: 14 (13%), day 3: 16 (15%), day 4: 23 (21%), day 5: 29 (27%)
- Shadow signals (day 1 unobservable) (n=37): median peak day 4, avg best excursion +4.92% -- day 2: 8 (22%), day 3: 7 (19%), day 4: 11 (30%), day 5: 11 (30%)

## Caveats (read before believing anything above)

- Entry at the logged live price assumes an instant, spread-free
  fill -- the one assumption here that flatters the shadow system.
- Signals cluster on the same days and names, so they are
  correlated bets on the same tape; the effective sample is smaller
  than the row count.
- A few weeks of signals is far too little to establish an edge
  either way. This page is a tripwire against an obviously bad
  idea, not proof of a good one.
- The EOD benchmark covers the screen's top-ranked candidates on
  the same dates; the two systems do not pick identical names, so
  this is a comparison of systems, not of fills.
