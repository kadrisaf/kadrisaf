"""Command-line entry point.

    python -m halal_trader.cli --out report.md
    python -m halal_trader.cli --universe data/universe_default.csv --top 10

Requires network access to Yahoo Finance (via yfinance). If you're running
this inside a locked-down sandbox, run it on your own machine or via the
included GitHub Actions workflow (.github/workflows/weekly_screen.yml)
instead.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from . import config
from .data_provider import DataProvider, DataUnavailable, NewsItem, YFinanceProvider
from .events import (
    HoldContext,
    calendar_covers,
    events_in_window,
    hold_window,
    load_event_calendar,
    next_earnings_date,
)
from .filing_digest import digest_filing
from .filings import FilingItem, SecEdgarFilingsProvider
from .macro import YFinanceMacroProvider
from .report import build_report
from .risk import correlation_flags
from .sentiment import AnthropicSentimentTagger, SentimentTag
from .shariah_screen import ScreenResult, screen_company
from .signals import TradeSignal, evaluate_signal
from .trades import load_trades, open_positions, plan_vs_real, summarize_trades
from .track_record import (
    load_track_record,
    record_new_candidates,
    resolve_pending,
    save_track_record,
    summarize_track_record,
)


def load_universe(path: str | None) -> List[str]:
    if not path:
        return list(config.DEFAULT_UNIVERSE)
    symbols: List[str] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            symbol = row[0].strip()
            if symbol and not symbol.startswith("#"):
                symbols.append(symbol)
    return symbols


def run(
    universe: List[str], top: int, provider: DataProvider | None = None
) -> Tuple[
    List[Tuple[ScreenResult, TradeSignal]],
    List[ScreenResult],
    List[Tuple[ScreenResult, TradeSignal]],
    List[str],
]:
    """Screen every symbol in `universe` and bucket it into exactly one of:
    ranked (compliant + qualifying setup), screened_out (fails Shariah
    screen), not_qualifying (compliant but no qualifying trade setup right
    now), or skipped (data unavailable). Every symbol lands in exactly one
    bucket -- nothing is silently dropped."""
    provider = provider or YFinanceProvider()
    ranked: List[Tuple[ScreenResult, TradeSignal]] = []
    screened_out: List[ScreenResult] = []
    not_qualifying: List[Tuple[ScreenResult, TradeSignal]] = []
    skipped: List[str] = []

    for symbol in universe:
        try:
            fundamentals = provider.get_fundamentals(symbol)
        except DataUnavailable as exc:
            print(f"[skip] {symbol}: {exc}", file=sys.stderr)
            skipped.append(symbol)
            continue

        screen = screen_company(fundamentals)
        if not screen.compliant:
            screened_out.append(screen)
            continue

        try:
            history = provider.get_price_history(symbol, period="1y")
        except DataUnavailable as exc:
            print(f"[skip] {symbol}: {exc}", file=sys.stderr)
            skipped.append(symbol)
            continue

        signal = evaluate_signal(symbol, history)
        if signal.qualifies:
            ranked.append((screen, signal))
        else:
            not_qualifying.append((screen, signal))

    ranked.sort(key=lambda pair: pair[1].score or 0, reverse=True)
    return ranked[:top], screened_out, not_qualifying, skipped


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Halal short-term trading watchlist generator")
    parser.add_argument("--universe", help="CSV file with one ticker per line", default=None)
    parser.add_argument("--top", type=int, default=10, help="max candidates to keep")
    parser.add_argument("--out", default="report.md", help="output Markdown path")
    parser.add_argument(
        "--track-record",
        default="track_record/candidates.csv",
        help="CSV path for the outcome tracker (see track_record.py)",
    )
    parser.add_argument(
        "--trades", default="track_record/real_trades.csv",
        help="CSV of your real trades (see trades.py)",
    )
    parser.add_argument(
        "--events", default="data/event_calendar.csv",
        help="CSV of scheduled macro events (date,event)",
    )
    args = parser.parse_args(argv)

    as_of = datetime.now(timezone.utc).date()
    universe = load_universe(args.universe)
    provider = YFinanceProvider()
    ranked, screened_out, not_qualifying, skipped = run(universe, args.top, provider=provider)

    # The user's REAL trades (hand-maintained CSV): open positions with
    # time-stops from the actual entry day, plus a closed-trade summary.
    real_trades = load_trades(args.trades)
    positions = open_positions(real_trades, as_of)
    real_summary = summarize_trades(real_trades)

    # Supplementary, non-scoring catalyst check -- for the names that
    # qualified AND the names currently held (you need news on what you own
    # at least as much as on what you might buy). Never affects ranking; a
    # fetch failure degrades to an empty list rather than breaking the report.
    coverage = [sig.symbol for _s, sig in ranked]
    coverage += [p["trade"].symbol for p in positions if p["trade"].symbol not in coverage]
    news: Dict[str, List[NewsItem]] = {}
    for symbol in coverage:
        try:
            news[symbol] = provider.get_recent_news(symbol)
        except Exception as exc:
            print(f"[news] {symbol}: fetch failed: {exc}", file=sys.stderr)
            news[symbol] = []

    # Optional LLM sentiment/event tagging over those same headlines -- a
    # feature-extraction step, not a predictor (see sentiment.py). Skipped
    # entirely if no API key is configured, so this is opt-in infrastructure,
    # not a hard dependency of the report.
    sentiment: Dict[str, SentimentTag] = {}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    tagger = None
    if api_key and coverage:
        try:
            tagger = AnthropicSentimentTagger(api_key=api_key)
        except Exception as exc:
            print(f"[sentiment] could not initialize tagger: {exc}", file=sys.stderr)
    if tagger is not None:
        for symbol in coverage:
            headlines = news.get(symbol) or []
            if not headlines:
                continue
            try:
                tag = tagger.tag(symbol, headlines)
            except Exception as exc:
                print(f"[sentiment] {symbol}: tagging failed: {exc}", file=sys.stderr)
                tag = None
            if tag is not None:
                sentiment[symbol] = tag
    elif coverage and not api_key:
        print("[sentiment] ANTHROPIC_API_KEY not set -- skipping sentiment tagging", file=sys.stderr)

    # Recent SEC 8-K filings for the same covered names -- free, no key,
    # only covers SEC filers (see filings.py). Same supplementary,
    # never-fails-the-run pattern as the news fetch above.
    filings_provider = SecEdgarFilingsProvider()
    filings: Dict[str, List[FilingItem]] = {}
    for symbol in coverage:
        try:
            filings[symbol] = filings_provider.get_recent_filings(symbol)
        except Exception as exc:
            print(f"[filings] {symbol}: fetch failed: {exc}", file=sys.stderr)
            filings[symbol] = []

    # LLM digest of the latest filing for each HELD position (see
    # filing_digest.py). Extraction, not prediction; held names only.
    digests: Dict[str, str] = {}
    if api_key:
        for p_ in positions:
            sym = p_["trade"].symbol
            items = filings.get(sym) or []
            if items:
                d = digest_filing(sym, items[0], api_key=api_key)
                if d:
                    digests[sym] = d

    # Macro snapshot -- always attempted, independent of whether anything
    # qualified today. Informational only; see macro.py.
    macro = None
    try:
        macro = YFinanceMacroProvider().get_snapshot()
    except Exception as exc:
        print(f"[macro] fetch failed: {exc}", file=sys.stderr)

    # Hold-window context: scheduled macro events and each ranked name's next
    # earnings date, for a hypothetical entry on the report date. Warnings only.
    window = hold_window(as_of, config.DEFAULT_SIGNAL_PARAMS.max_holding_days)
    calendar = load_event_calendar(args.events)
    hold = HoldContext(
        window=window,
        macro_events=events_in_window(calendar, window),
        calendar_ok=calendar_covers(calendar, window),
        earnings={sig.symbol: next_earnings_date(sig.symbol, as_of) for _s, sig in ranked},
    )

    # Correlation between each candidate and the open positions: "this is
    # partly the same bet you already hold". Warning only.
    open_syms = [p["trade"].symbol for p in positions]
    correlations = (
        correlation_flags([sig.symbol for _s, sig in ranked], open_syms, provider)
        if ranked and open_syms
        else {}
    )

    # Outcome tracker: resolve anything from prior runs that's now due,
    # record today's candidates, and persist. See track_record.py for why
    # this exists -- it's the groundwork for ever validating this screen
    # against real results, not a scoring input.
    track_records = load_track_record(args.track_record)
    resolve_pending(track_records, provider, slippage_pct=config.EXIT_SLIPPAGE_PCT)
    record_new_candidates(track_records, ranked, as_of)
    save_track_record(args.track_record, track_records)
    track_record_summary = summarize_track_record(track_records)
    comparison = plan_vs_real(real_trades, track_records)

    report = build_report(
        ranked,
        screened_out,
        not_qualifying,
        skipped,
        as_of=as_of,
        news=news,
        sentiment=sentiment,
        filings=filings,
        macro=macro,
        track_record_summary=track_record_summary,
        hold=hold,
        positions=positions,
        correlations=correlations,
        real_summary=real_summary,
        digests=digests,
        comparison=comparison,
    )

    Path(args.out).write_text(report)
    print(report)
    print(f"Report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
