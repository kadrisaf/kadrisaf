# Halal short-term watchlist agent

A research tool that screens a configurable universe of stocks for
(approximate) Shariah compliance, then ranks the survivors for short-term
(1 trading week max) long setups. It produces a Markdown report. **It never
places a trade.** You read the report and execute manually in your broker
(Trade Republic, which has no public API this could plug into anyway).

See **[ADVISORY_PROTOCOL.md](./ADVISORY_PROTOCOL.md)** for the standing rules
this assistant follows when giving trading input based on this tool --
updated after each live cycle with what worked and what didn't.

## ⚠️ Read this before using it

- **Not a certified Shariah ruling.** The compliance screen is a rules-based
  approximation of common industry methodologies (business-activity
  exclusion list + the "33% of market cap" financial ratio tests used by
  indices like the Dow Jones Islamic Market / S&P Shariah). Different
  scholars and standards (AAOIFI, MSCI, FTSE, your own local board) draw the
  lines slightly differently. Before actually trading a name, cross-check it
  against a certified source (e.g. Zoya, Musaffa, Islamicly) or your own
  scholar. See `halal_trader/config.py` and `halal_trader/shariah_screen.py`
  for exactly which rules are applied and where the thresholds live.
- **Not investment advice.** The technical signal (`halal_trader/signals.py`)
  is one simple, documented momentum/trend rule set, not a proven edge. Past
  price action does not predict future returns. Short-term trading can lose
  money quickly — the report always includes a mechanical stop-loss, target,
  and a hard 5-trading-day time-stop so risk is defined before you act, but
  none of that guarantees an outcome.
- **You place every order.** Trade Republic doesn't expose a public trading
  or market-data API, so this tool only ever produces a report for you to
  act on by hand in the app.

## How it works

1. **Universe** — a list of tickers to check (`data/universe_default.csv` by
   default, or pass `--universe your_list.csv`). This is just a *candidate*
   list; nothing in it is pre-approved.
2. **Shariah screen** (`shariah_screen.py`) — excludes anything in a haram
   sector (conventional banks/insurance, alcohol, gambling, tobacco, pork,
   adult content, weapons, by default — edit the keyword list in
   `config.py`), then checks three financial ratios (debt, interest-bearing
   cash, receivables — each must stay under 33% of market cap).
3. **Trade-setup signal** (`signals.py`) — of the names that pass, keeps
   only those in a confirmed short-term uptrend (price > 20-day SMA >
   50-day SMA, 20-day SMA rising) with RSI(14) in a healthy 45–65 band
   (avoids both weak and already-overbought names) and above-average volume
   (confirms real interest). Computes an ATR-based stop-loss and target
   (≥1.5:1 reward:risk) and enforces a 5-trading-day time-stop.
4. **Recent headlines** (`data_provider.py`'s `get_recent_news`) — for
   every name that qualifies, fetches a few recent headlines (via
   `yfinance`) so you can eyeball whether there's an obvious catalyst
   behind the move. Unscored, purely supplementary -- never affects
   ranking, and degrades to "no headlines found" on any fetch failure.
5. **Optional LLM sentiment tag** (`sentiment.py`) — if `ANTHROPIC_API_KEY`
   is set, those same headlines get summarized into a structured tag
   (sentiment direction, event type, confidence) via the Anthropic API.
   This is a feature-extraction step over text that's already fetched, not
   a predictor: it is never combined into the quant score or blended into
   a "probability of return" -- doing that honestly would need a
   backtested, calibrated model this project doesn't have the historical
   data or infrastructure for. If the key isn't set, this step is skipped
   entirely and the report is unaffected.
6. **Recent SEC filings** (`filings.py`) — for the same qualifying names,
   fetches recent 8-K filings (material events) from SEC EDGAR's free,
   no-key API. Same role as the headlines: unscored, supplementary, for
   manual review. Only covers SEC filers, so a non-US ticker (SIE.DE,
   AIR.PA, ...) will always show none here -- expected, not a failure.
7. **Macro snapshot** (`macro.py`) — VIX and the 10-year Treasury yield,
   shown at the top of every report regardless of whether anything
   qualified that day. Purely informational: it doesn't filter candidates,
   change ranking, or affect sizing. Turning "VIX is elevated" into an
   actual rule would need backtesting this project doesn't have.
8. **Outcome tracker** (`track_record.py`) — every ranked candidate gets
   logged to `track_record/candidates.csv`, and on each run any prior
   candidate whose stop, target, or time-stop has now resolved gets its
   actual return recorded. The report shows a running win-rate/average-
   return summary once there's at least one resolved trade. This is the
   groundwork for ever validating the screen against real results -- not
   a scoring input, and not a claim of forward-looking edge on its own.
9. **Entry-timing and hold-window flags** (`signals.py`, `events.py`) —
   warnings only, never filters. For each ranked name: distance below the
   52-week high, and the reward:risk *up to that high* (the table's 1.5:1 is
   fixed by construction; this is the check against where price has actually
   been). Plus what is scheduled inside the 5-trading-day hold: macro events
   from `data/event_calendar.csv` (hand-maintained -- the report says so when
   the file doesn't reach the end of the window) and each name's next
   earnings date. This pre-computes parts of ADVISORY_PROTOCOL section 3
   factors 3 and 4; it does not replace doing them.
10. **Your real trades** (`trades.py`, `track_record/real_trades.csv`) —
    hand-maintained log of actual fills. The report lists open positions with
    the time-stop counted from the *actual* entry day (day 1 = entry day),
    flags overdue exits, and summarizes closed trades. Unlike the outcome
    tracker, which simulates every flagged candidate, this is what you
    actually did.
11. **Portfolio-risk warnings** (`risk.py`) — flags a candidate whose daily
    returns correlate >= 0.7 with an open position (partly the same bet, not
    diversification) and notes the fixed-fee drag at a typical position size.
    Warnings only.
12. **Report** (`report.py`) — ranks the qualifying names and writes a
    Markdown table plus the list of names excluded and why.

Prices are read from completed daily bars only: if Yahoo returns a partial bar
for a session that is still open, it is dropped (otherwise partial-day volume
is compared with full-day averages and every name looks illiquid).

### Backtesting the signal

```bash
python -m halal_trader.backtest --years 3 --out backtests/backtest.md
```

Replays the signal rules day by day on currently-compliant names with the
tracker's exit rules (stop first, gap fills at the open, slippage on stop and
time-stop exits), and compares the filter with entering on every day and with
other stop/target ATR multiples, and splits the live-parameter trades by
trend regime at entry (symbol above/below its own 200-day SMA, and SPY's).
Read the caveats it prints: look-ahead in the
Shariah screen, survivorship in the universe, correlated trades, and the risk
of re-tuning on the same data.

`python -m halal_trader.tune` runs the split-sample parameter test: a grid
over RSI band / volume threshold / stop-target multiples / hold length is
tuned on the first half of history and validated on the second half, with the
live defaults always shown. Its output states the decision rule (adopt nothing
without a clear out-of-sample margin). Headlines fall back to Yahoo's RSS feed
when `ticker.news` returns nothing.

## Usage

```bash
pip install -r requirements.txt
python -m halal_trader.cli --universe data/universe_default.csv --top 10 --out report.md
```

This needs outbound internet access to Yahoo Finance (via `yfinance`). If
you're running it from a locked-down environment (e.g. some CI sandboxes)
that blocks that traffic, run it on your own machine, or use the included
scheduled GitHub Action below, which runs on GitHub's own (unrestricted)
runners.

### Automated daily run

`.github/workflows/weekly_screen.yml` runs the screener every workday
(Mon-Fri) at 07:00 Europe/Berlin time on GitHub's hosted runners and
commits the report to `reports/`. GitHub Actions cron is UTC-only,
ignores DST, and often starts late, so the workflow schedules both the
summer and winter UTC equivalents; a scheduled run only proceeds if Berlin
time is 07:00-12:59 and today's report doesn't exist yet, so it produces
one report per day. Enable GitHub Actions
on the repo and it starts running on the next scheduled trigger, or
trigger it manually from the Actions tab ("Run workflow").

To enable the optional LLM sentiment tag, add an `ANTHROPIC_API_KEY` repo
secret (Settings → Secrets and variables → Actions). Without it, the
workflow runs exactly as before -- headlines still show up, just without
the sentiment summary. SEC filings and the macro snapshot need no key --
both are free, public APIs.

## Running the tests

The test suite uses an in-memory `StaticProvider` fixture, so it needs no
network access:

```bash
pip install -r requirements-dev.txt
python -m pytest halal_trader/tests -v
```

## Extending it

- **Change the halal rules**: edit `EXCLUDED_SECTOR_KEYWORDS` and the ratio
  thresholds in `halal_trader/config.py`.
- **Change the trading rules**: edit `SignalParams` in the same file (RSI
  band, SMA lengths, ATR multiples, liquidity floor, holding period). Run the
  backtest before and after; don't tune on one table.
- **Keep the calendars current**: `data/event_calendar.csv` (macro dates) and
  `NYSE_HOLIDAYS` in `config.py` are hand-maintained.
- **Log a trade**: add a row to `track_record/real_trades.csv` after each fill
  (and fill in exit fields when you sell).
- **Different market/broker data**: implement the `DataProvider` protocol in
  `halal_trader/data_provider.py` for another source and wire it into
  `cli.py`.
- **Crypto or other asset classes**: not covered here — halal status is
  debated coin-by-coin and needs its own, separately-sourced rule set rather
  than reusing the equity screen.
