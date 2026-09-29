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
from pathlib import Path
from typing import Dict, List, Tuple

from . import config
from .data_provider import DataProvider, DataUnavailable, NewsItem, YFinanceProvider
from .report import build_report
from .sentiment import AnthropicSentimentTagger, SentimentTag
from .shariah_screen import ScreenResult, screen_company
from .signals import TradeSignal, evaluate_signal


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
            history = provider.get_price_history(symbol, period="6mo")
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
    args = parser.parse_args(argv)

    universe = load_universe(args.universe)
    provider = YFinanceProvider()
    ranked, screened_out, not_qualifying, skipped = run(universe, args.top, provider=provider)

    # Supplementary, non-scoring catalyst check: only for names that already
    # qualified, so this never affects who ranks -- purely something for a
    # human to eyeball before acting. A fetch failure degrades to an empty
    # list rather than breaking the report.
    news: Dict[str, List[NewsItem]] = {}
    for _screen, sig in ranked:
        try:
            news[sig.symbol] = provider.get_recent_news(sig.symbol)
        except Exception as exc:
            print(f"[news] {sig.symbol}: fetch failed: {exc}", file=sys.stderr)
            news[sig.symbol] = []

    # Optional LLM sentiment/event tagging over those same headlines -- a
    # feature-extraction step, not a predictor (see sentiment.py). Skipped
    # entirely if no API key is configured, so this is opt-in infrastructure,
    # not a hard dependency of the report.
    sentiment: Dict[str, SentimentTag] = {}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    tagger = None
    if api_key and ranked:
        try:
            tagger = AnthropicSentimentTagger(api_key=api_key)
        except Exception as exc:
            print(f"[sentiment] could not initialize tagger: {exc}", file=sys.stderr)
    if tagger is not None:
        for _screen, sig in ranked:
            headlines = news.get(sig.symbol) or []
            if not headlines:
                continue
            try:
                tag = tagger.tag(sig.symbol, headlines)
            except Exception as exc:
                print(f"[sentiment] {sig.symbol}: tagging failed: {exc}", file=sys.stderr)
                tag = None
            if tag is not None:
                sentiment[sig.symbol] = tag
    elif ranked and not api_key:
        print("[sentiment] ANTHROPIC_API_KEY not set -- skipping sentiment tagging", file=sys.stderr)

    report = build_report(ranked, screened_out, not_qualifying, skipped, news=news, sentiment=sentiment)

    Path(args.out).write_text(report)
    print(report)
    print(f"Report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
