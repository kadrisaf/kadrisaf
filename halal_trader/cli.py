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
import sys
from pathlib import Path
from typing import List, Tuple

from . import config
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .report import build_report
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
) -> Tuple[List[Tuple[ScreenResult, TradeSignal]], List[ScreenResult], List[str]]:
    provider = provider or YFinanceProvider()
    ranked: List[Tuple[ScreenResult, TradeSignal]] = []
    screened_out: List[ScreenResult] = []
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

    ranked.sort(key=lambda pair: pair[1].score or 0, reverse=True)
    return ranked[:top], screened_out, skipped


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Halal short-term trading watchlist generator")
    parser.add_argument("--universe", help="CSV file with one ticker per line", default=None)
    parser.add_argument("--top", type=int, default=10, help="max candidates to keep")
    parser.add_argument("--out", default="report.md", help="output Markdown path")
    args = parser.parse_args(argv)

    universe = load_universe(args.universe)
    ranked, screened_out, skipped = run(universe, args.top)
    report = build_report(ranked, screened_out, skipped)

    Path(args.out).write_text(report)
    print(report)
    print(f"Report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
