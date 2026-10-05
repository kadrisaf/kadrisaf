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

## Open positions (user's real Trade Republic trades, as of Mon 2026-10-05 12:08 Berlin)

The tracker (`track_record/candidates.csv`) only logs screener candidates, NOT
the user's actual trades -- this section is the source of truth for real ones.
Values below are euro position values from the app. Time-stops count 5 trading
days from the actual entry day unless noted.

| Position | Entry | Value now | Since buy | Stop | Target | Time-stop |
|---|---|---|---|---|---|---|
| AbbVie (ABBV) | 2 sh @ EUR 235.10, Mon Oct 5 08:19 (EUR 471.20 incl. 1.00 fee) | EUR 466.00 | -1.10% | ~EUR 228.19 | ~EUR 245.47 | Fri Oct 9 |
| Microsoft (MSFT) | **CLOSED** Mon Oct 5 16:47: sold 1.209987 sh @ EUR 469.65, received EUR 567.27 (after 1.00 fee) | -- | +2.95% / +EUR 16.27 | -- | -- | -- (sold before the Oct 8 time-stop; implied cost basis ~EUR 551.00, buy date still unknown) |
| Micron (MU) | fill/date NOT confirmed (report close 1065.11 USD) | EUR 351.32 | +0.09% | 997.48 USD | 1166.55 USD | Thu Oct 8 (same caveat) |

- AbbVie stop/target are the report's ATR levels (stop 255.09 / target 274.42
  USD vs 262.82 close) scaled to the EUR fill -- an approximation, check the
  live EUR quote.
- The app shows **3 open orders** as of 12:08; their details were not seen
  (assume stops/limits, don't assume which).
- **NVDA is closed**: sold Fri Oct 2 16:53, 2.486325 sh @ EUR 210.15, +EUR 20.50
  (+4.09%). The tracker still lists NVDA rows as open -- they are simulated.
- The tracker's "1 resolved trade, -3.87%" is a simulated DHR stop-out (Oct 2),
  not a real trade.
- Open item: ask for the real MU fill and date (and the MSFT buy date) (Trade Republic -> Activity)
  to replace the placeholders above.

## State to know before continuing

- `reports/2026-10-05.md` is the latest report (141 symbols; qualifiers NVDA,
  ABBV). Universe was expanded Oct 3 by 54 symbols (commit 4a9a8a9).
- Workflow runs Mon-Fri, scheduled 07:00 Berlin (commit 8bf8cbd). The first
  DST guard (exact hour == 07) skipped GitHub's delayed runs on Oct 2; fixed
  Oct 5: a scheduled run now proceeds if Berlin time is 07:00-12:59 and
  today's report doesn't exist, plus a concurrency group. Not yet observed
  in a real scheduled run -- check `gh run list` on Tue Oct 6 (event should
  read `schedule`, not `workflow_dispatch`). Don't run the screener manually
  after the US open: it overwrites the day's report with falsely low rel-volume.
- `gh` CLI is installed locally and authenticated (fine-grained token, Actions
  + Contents write). Local runs on this machine need a Brotli workaround for
  the anthropic SDK; GitHub Actions does not.
- `ANTHROPIC_API_KEY` Actions secret is confirmed valid and funded -- don't
  assume it's broken again without re-checking logs first. Oct 2 and Oct 5
  reports found no headlines (so no sentiment tags) -- unchecked whether that
  is real or a news-fetch problem.
- Rel-volume is computed from the latest daily bar, so a screener run after
  the US open compares partial-day volume to full-day averages and reads
  falsely low. Trust only pre-open or post-close runs.

## Suggested first prompt for the new session

See the message the user was given alongside this file.
