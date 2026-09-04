# Halal short-term watchlist agent

A research tool that screens a configurable universe of stocks for
(approximate) Shariah compliance, then ranks the survivors for short-term
(1 trading week max) long setups. It produces a Markdown report. **It never
places a trade.** You read the report and execute manually in your broker
(Trade Republic, which has no public API this could plug into anyway).

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
4. **Report** (`report.py`) — ranks the qualifying names and writes a
   Markdown table plus the list of names excluded and why.

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

### Automated weekly run

`.github/workflows/weekly_screen.yml` runs the screener every Monday
06:00 UTC on GitHub's hosted runners and commits the report to `reports/`.
Enable GitHub Actions on the repo and it starts running on the next
scheduled trigger, or trigger it manually from the Actions tab
("Run workflow").

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
  band, SMA lengths, ATR multiples, liquidity floor, holding period).
- **Different market/broker data**: implement the `DataProvider` protocol in
  `halal_trader/data_provider.py` for another source and wire it into
  `cli.py`.
- **Crypto or other asset classes**: not covered here — halal status is
  debated coin-by-coin and needs its own, separately-sourced rule set rather
  than reusing the equity screen.
