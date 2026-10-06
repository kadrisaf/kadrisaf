# Session handoff — 2026-10-01

Continuity notes for picking this project back up in a new session (local or
cloud). Not a standing protocol file -- see `ADVISORY_PROTOCOL.md` for that.
This file is disposable; overwrite it each handoff rather than accumulating
history here (git log already has that).

## Repo / branch state

- Remote: `https://github.com/kadrisaf/kadrisaf`
- Default branch (work happens directly here, no PR flow in use): 
  `claude/halal-trading-agent-vf477i`
- Working tree was clean as of this write.

## What happened this session

1. **Bug fix**: `halal_trader/sentiment.py` was silently producing zero
   sentiment tags with no error output. Root cause: stale model ID
   (`claude-haiku-4-5-20251001` -> should be `claude-haiku-4-5`) plus a
   missing diagnostic (the catch-all exception handler swallowed the error
   instead of logging it). Fixed and added `[sentiment]` stderr logging on
   every outcome (success/parse-failure/API-raise), matching the pattern
   already used in `news.py`/`filings.py`/`macro.py`. Commit `3feacb0`.
2. **Verified end-to-end via GitHub Actions** (`.github/workflows/weekly_screen.yml`,
   triggered manually via `workflow_dispatch`, not just the Monday schedule):
   - Run with stale/invalid `ANTHROPIC_API_KEY` secret -> confirmed real
     error now surfaces (`401 authentication_error: API key is invalid`)
     instead of silent failure.
   - After the user re-saved the GitHub Actions secret `ANTHROPIC_API_KEY`
     (Settings -> Secrets and variables -> Actions, on `kadrisaf/kadrisaf`),
     re-triggered run **36859570753** came back clean:
     ```
     [sentiment] MU:   tagged sentiment=+0.75 event_type=earnings_beat confidence=high
     [sentiment] MSFT: tagged sentiment=+0.85 event_type=earnings_beat confidence=high
     [sentiment] DHR:  tagged sentiment=+0.60 event_type=product_news  confidence=medium
     ```
   - **Pipeline is confirmed working end to end** (screen -> signals ->
     news/filings -> sentiment -> report -> commit).
3. **Gave trading input on today's 3 qualifiers** (MU, MSFT, DHR) by walking
   the 5-factor checklist in `ADVISORY_PROTOCOL.md` (macro calendar, entry
   timing vs 52wk high, event calendar inside the 5-day hold, instrument/halal
   check with ISINs). Conclusion: all three pass the mechanical checklist
   with no earnings/macro event inside the hold window; DHR read as the
   cleanest story, MU's catalyst (earnings beat) was already priced in, MSFT
   carries a cited valuation-above-fair-value flag. Full writeup is in that
   turn's chat transcript, not duplicated into a repo file -- nothing was
   bought, no position-tracking file was updated, since Trade Republic has
   no API and the user executes manually.

## Open positions (user's real Trade Republic trades, as of Tue 2026-10-06 10:33 Berlin)

The tracker (`track_record/candidates.csv`) only logs screener candidates, NOT
the user's actual trades -- this section is the source of truth for real ones.
Values below are euro position values from the app. Time-stops count 5 trading
days from the actual entry day unless noted.

| Position | Entry | Value now | Since buy | Stop | Target | Time-stop |
|---|---|---|---|---|---|---|
| AbbVie (ABBV) | 2 sh @ EUR 235.10, Mon Oct 5 08:19 (EUR 471.20 incl. 1.00 fee) | EUR 472.80 | +0.34% | ~EUR 228.19 | ~EUR 245.47 | Fri Oct 9 |
| Microsoft (MSFT) | **CLOSED** Mon Oct 5 16:47: sold 1.209987 sh @ EUR 469.65, received EUR 567.27 (after 1.00 fee) | -- | +2.95% / +EUR 16.27 | -- | -- | -- (sold before the Oct 8 time-stop; implied cost basis ~EUR 551.00, buy date still unknown) |
| Seagate (STX) | ~EUR 600 bought Tue Oct 6 (date inferred from the 08:09 screenshot; fill/qty unseen; ~EUR 601 cost incl. fee) | EUR 586.36 | -2.44% | 803.65 USD (-9.4%) | 1012.26 USD (+14.1%) | Mon Oct 12 |
| Micron (MU) | 0.367608 sh @ EUR 952.10, Thu Oct 1 07:48 (EUR 351.00 incl. 1.00 fee) | EUR 345.99 | -1.43% | ~EUR 891.66 | ~EUR 1042.77 | **Tue Oct 7 -- TOMORROW** |

- AbbVie stop/target are the report's ATR levels (stop 255.09 / target 274.42
  USD vs 262.82 close) scaled to the EUR fill -- an approximation, check the
  live EUR quote.
- The app shows **3 open orders** as of 12:08; their details were not seen
  (assume stops/limits, don't assume which).
- **NVDA is closed**: sold Fri Oct 2 16:53, 2.486325 sh @ EUR 210.15, +EUR 20.50
  (+4.09%). The tracker still lists NVDA rows as open -- they are simulated.
- The tracker's "1 resolved trade, -3.87%" is a simulated DHR stop-out (Oct 2),
  not a real trade.

## Pipeline upgrades made Oct 5 (all in the repo, 84 tests passing)

- **Incomplete-bar fix** (`data_provider.drop_incomplete_bar`): a partial bar for a
  still-open session is dropped, so mid-session runs no longer read relative
  volume as 0.1-0.3x. Verified live: a mid-session run reproduced the pre-open
  qualifiers exactly. (Old note "don't run after the US open" no longer applies.)
- **52-week-high / resistance warnings** (`signals.py`): `pct_below_52w_high`,
  `rr_to_52w_high`, warnings for extended entries and targets above overhead
  resistance. Warnings only -- they never change who qualifies or the ranking.
- **Hold-window events** (`events.py`, `data/event_calendar.csv`): macro events
  and each name's next earnings date inside the 5-trading-day hold, plus an NYSE
  holiday-aware trading-day counter. The calendar is hand-maintained (verified
  through Dec 2026; PPI after Oct 15 not listed) and the report says when it
  doesn't reach the end of the window.
- **Real trades** (`trades.py`, `track_record/real_trades.csv`): the report now
  lists open positions with time-stops counted from the actual entry day
  (day 1 = entry day) and a closed-trade summary. Update the CSV after every
  fill. MU's entry fill/date is still blank.
- **Tracker realism** (`track_record.simulate_exit`): gap-down through a stop fills
  at the open; stop/time-stop exits take 0.1% slippage (`config.EXIT_SLIPPAGE_PCT`);
  targets fill exactly at the target.
- **Backtest** (`halal_trader/backtest.py`): see `backtests/` for the latest output
  and its caveats (look-ahead, survivorship, correlation, multiple comparisons).
  Do not re-tune live parameters from it alone.
- **Backtest headline (backtests/2026-10-05.md, 130 compliant symbols, 3y):** the
  filter adds essentially nothing over entering on any day -- live params:
  signal +0.27% avg / 51.8% win / PF 1.15 vs any-day +0.27% / 50.7% / PF 1.16.
  First half of signal trades was flat (-0.02%, PF 0.99), second half +0.55%.
  Live exits: 31% stop, 20% target, 48% time-stop, so realised reward:risk is
  ~1.07, not 1.5. This is on a survivorship-flattered sample; treat as "no
  demonstrated edge", not as a reason to re-tune.
- **Oct 6 additions (risk.py + regime split, 90 tests passing):** report now flags
  >=0.7 return correlation between a candidate and open positions (live: STX/MU
  +0.70 -- user holds both) and the fixed-fee cost drag (0.40% at EUR 500).
  Backtest regime split (backtests/2026-10-06-regime.md): trend regime at entry
  does NOT separate outcomes (above own 200d SMA +0.28%/PF 1.16 vs below
  +0.24%/PF 1.13; SPY split similar, n=71 below). No regime rule added -- there
  is nothing there to encode. Expectancy/parameter tuning on split-sample
  validation remains NOT done (category 2 from the Oct 6 discussion).
- **Oct 6 later additions (96 tests passing):** (a) news fixed -- `ticker.news`
  returns 0 items since early Oct; `_rss_news` now falls back to Yahoo's RSS
  feed (verified live: STX headlines flowing again, incl. an Oct 6 "higher
  bid" story). (b) Split-sample tune (backtests/2026-10-06-tune.md, 80-cell
  grid, train = first half / validate = second half): every top train winner
  shares 2.0/3.0 ATR + 8-day hold; best validates +0.496%/trade vs LIVE
  +0.391% -- a ~0.1pp margin, NOT adopted (small, and an 8d hold violates the
  protocol's hard 5-day max, which is the user's call to change, not a tuning
  knob). RSI band and vol threshold barely matter. LIVE train +0.023% vs valid
  +0.391% also shows strong period dependence -- treat all levels as noisy.
- **Live params switched 2026-10-06** (user decision): stop/target ATR multiples
  1.5/2.25 -> 2.0/3.0 after three converging tests (full grid, 80-cell tune,
  focused split-sample: validate +0.455%/PF 1.23 vs +0.391%/1.21). Applies to
  FUTURE reports only -- open positions keep the levels they were entered with
  (real_trades.csv is unchanged). Wider stop = bigger per-share loss when hit:
  the sizing formula must set the share count.
- **Order tickets added 2026-10-06:** the report now prints per-qualifier
  mechanical sizing (PORTFOLIO_EUR=1400, RISK_PCT_PER_TRADE=2.0 -- user first
  asked 5%, lowered to 2% same day; >2% triggers a warning banner). Formula
  output only; the entry decision stays with the user, per the protocol.
- Still open: the fixed R:R of 1.5:1 in the table is by construction (the
  entry-timing flags are the real check); MU entry fill/date and STX fill/qty
  still unconfirmed in track_record/real_trades.csv.

## State to know before continuing

- `reports/2026-10-05.md` is the latest report (141 symbols; qualifiers NVDA,
  ABBV). Universe was expanded Oct 3 by 54 symbols (commit 4a9a8a9).
- Workflow runs Mon-Fri, scheduled 07:00 Berlin (commit 8bf8cbd). The first
  DST guard (exact hour == 07) skipped GitHub's delayed runs on Oct 2; fixed
  Oct 5: a scheduled run now proceeds if Berlin time is 07:00-12:59 and
  today's report doesn't exist, plus a concurrency group. Not yet observed
  in a real scheduled run -- check `gh run list` on Tue Oct 6 (event should
  read `schedule`, not `workflow_dispatch`).
- `gh` CLI is installed locally and authenticated (fine-grained token, Actions
  + Contents write). Local runs on this machine need a Brotli workaround for
  the anthropic SDK; GitHub Actions does not.
- `ANTHROPIC_API_KEY` Actions secret is confirmed valid and funded -- don't
  assume it's broken again without re-checking logs first. Oct 2 and Oct 5
  reports found no headlines (so no sentiment tags) -- unchecked whether that
  is real or a news-fetch problem.
- Relative volume uses completed daily bars only (partial live bars are dropped).

## Suggested first prompt for the new session

See the message the user was given alongside this file.
