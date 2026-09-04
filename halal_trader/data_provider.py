"""Market data access layer.

Everything the screening/signal engine needs comes through the
``DataProvider`` interface below, so the rest of the code never talks to a
specific vendor directly. Two implementations are provided:

* ``YFinanceProvider`` -- free, no API key, uses the ``yfinance`` package.
  This is the default for real use, but it makes outbound HTTPS calls to
  Yahoo Finance, which some sandboxed/CI networks block. Trade Republic
  itself has no public market-data or order API, so this tool never talks
  to your broker -- it only produces a watchlist for you to act on
  manually in the Trade Republic app.
* ``StaticProvider`` -- serves pre-built in-memory data. Used by the test
  suite and useful for offline/CI runs where you feed it a CSV snapshot.

Both raise ``DataUnavailable`` on failure so callers can skip a symbol
instead of crashing the whole run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Protocol

import pandas as pd


class DataUnavailable(Exception):
    """Raised when fundamentals or price history can't be fetched for a symbol."""


@dataclass
class CompanyFundamentals:
    symbol: str
    sector: Optional[str]
    industry: Optional[str]
    market_cap: Optional[float]
    total_debt: Optional[float]
    cash_and_short_term_investments: Optional[float]
    receivables: Optional[float]
    currency: Optional[str] = None


class DataProvider(Protocol):
    def get_fundamentals(self, symbol: str) -> CompanyFundamentals: ...

    def get_price_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame:
        """Return a DataFrame indexed by date with columns
        Open, High, Low, Close, Volume (ascending date order)."""
        ...


class YFinanceProvider:
    """Live data via the yfinance package (Yahoo Finance)."""

    def __init__(self):
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "yfinance is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._yf = yf

    def get_fundamentals(self, symbol: str) -> CompanyFundamentals:
        try:
            ticker = self._yf.Ticker(symbol)
            info = ticker.info or {}
            market_cap = info.get("marketCap")
            total_debt = info.get("totalDebt")
            total_cash = info.get("totalCash")
            receivables = None

            # totalDebt/totalCash aren't always populated on `.info`; fall
            # back to the balance sheet when needed.
            if total_debt is None or total_cash is None or receivables is None:
                bs = ticker.balance_sheet
                if bs is not None and not bs.empty:
                    latest = bs.iloc[:, 0]
                    if total_debt is None:
                        total_debt = _first_present(
                            latest, ["Total Debt", "TotalDebt"]
                        )
                    if total_cash is None:
                        total_cash = _sum_present(
                            latest,
                            [
                                "Cash And Cash Equivalents",
                                "Cash Cash Equivalents And Short Term Investments",
                                "Other Short Term Investments",
                            ],
                        )
                    receivables = _first_present(
                        latest, ["Receivables", "Net Receivables", "Accounts Receivable"]
                    )

            return CompanyFundamentals(
                symbol=symbol,
                sector=info.get("sector"),
                industry=info.get("industry"),
                market_cap=market_cap,
                total_debt=total_debt,
                cash_and_short_term_investments=total_cash,
                receivables=receivables,
                currency=info.get("currency"),
            )
        except Exception as exc:
            raise DataUnavailable(f"fundamentals unavailable for {symbol}: {exc}") from exc

    def get_price_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame:
        try:
            ticker = self._yf.Ticker(symbol)
            hist = ticker.history(period=period, auto_adjust=True)
            if hist is None or hist.empty:
                raise DataUnavailable(f"no price history for {symbol}")
            return hist[["Open", "High", "Low", "Close", "Volume"]]
        except DataUnavailable:
            raise
        except Exception as exc:
            raise DataUnavailable(f"price history unavailable for {symbol}: {exc}") from exc


def _first_present(series: pd.Series, keys):
    for k in keys:
        if k in series.index and pd.notna(series[k]):
            return float(series[k])
    return None


def _sum_present(series: pd.Series, keys):
    total = 0.0
    found = False
    for k in keys:
        if k in series.index and pd.notna(series[k]):
            total += float(series[k])
            found = True
    return total if found else None


class StaticProvider:
    """In-memory provider for tests and offline snapshots."""

    def __init__(
        self,
        fundamentals: Dict[str, CompanyFundamentals],
        histories: Dict[str, pd.DataFrame],
    ):
        self._fundamentals = fundamentals
        self._histories = histories

    def get_fundamentals(self, symbol: str) -> CompanyFundamentals:
        try:
            return self._fundamentals[symbol]
        except KeyError as exc:
            raise DataUnavailable(f"no fundamentals fixture for {symbol}") from exc

    def get_price_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame:
        try:
            return self._histories[symbol]
        except KeyError as exc:
            raise DataUnavailable(f"no price history fixture for {symbol}") from exc
