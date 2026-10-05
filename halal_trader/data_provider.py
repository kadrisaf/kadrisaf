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

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

import pandas as pd


def drop_incomplete_bar(history: pd.DataFrame, now: Optional[datetime] = None) -> pd.DataFrame:
    """Drop the last bar if it is today's and the exchange session is still
    open (or just closed). Yahoo serves a partial bar for the live session;
    comparing its partial volume against 20 FULL-day averages reads falsely
    low (e.g. 0.1x relative volume 20 minutes after the open), and a partial
    close can mis-resolve a tracked trade. The cutoff is a conservative
    session-close + buffer by timezone: US 16:15, elsewhere 17:45."""
    if history is None or history.empty:
        return history
    tz = getattr(history.index, "tz", None)
    if tz is None:
        return history
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    last = history.index[-1]
    if last.date() != now_local.date():
        return history
    name = str(tz)
    cutoff_h, cutoff_m = (16, 15) if name.startswith("America/") else (17, 45)
    if (now_local.hour, now_local.minute) < (cutoff_h, cutoff_m):
        return history.iloc[:-1]
    return history


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


@dataclass
class NewsItem:
    """One headline. Purely informational -- not scored, not a signal input.
    Surfaced so a human can eyeball whether a qualifying setup has an
    obvious news catalyst behind it before acting on it."""
    title: str
    publisher: Optional[str] = None
    link: Optional[str] = None
    published: Optional[str] = None  # best-effort "YYYY-MM-DD", may be None


class DataProvider(Protocol):
    def get_fundamentals(self, symbol: str) -> CompanyFundamentals: ...

    def get_price_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame:
        """Return a DataFrame indexed by date with columns
        Open, High, Low, Close, Volume (ascending date order)."""
        ...

    def get_recent_news(self, symbol: str, limit: int = 3) -> List[NewsItem]:
        """Best-effort recent headlines for a symbol. Never raises --
        returns an empty list on any failure, since this is a supplementary
        check, not something that should take down the whole report."""
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
            hist = drop_incomplete_bar(hist[["Open", "High", "Low", "Close", "Volume"]])
            if hist.empty:
                raise DataUnavailable(f"no completed price bars for {symbol}")
            return hist
        except DataUnavailable:
            raise
        except Exception as exc:
            raise DataUnavailable(f"price history unavailable for {symbol}: {exc}") from exc

    def get_recent_news(self, symbol: str, limit: int = 3) -> List[NewsItem]:
        try:
            ticker = self._yf.Ticker(symbol)
            raw = ticker.news or []
        except Exception as exc:
            print(f"[news] {symbol}: ticker.news raised: {exc}", file=sys.stderr)
            return []
        items = _parse_news_entries(raw, limit)
        # Distinguish "genuinely no news" from "got data but couldn't parse
        # it" -- these look identical from the report alone, and silently
        # collapsing them made a prior run impossible to diagnose after
        # the fact.
        if raw and not items:
            print(
                f"[news] {symbol}: got {len(raw)} raw entries but parsed 0 -- "
                f"schema mismatch, first entry keys: "
                f"{list(raw[0].keys()) if isinstance(raw[0], dict) else type(raw[0])}",
                file=sys.stderr,
            )
        else:
            print(f"[news] {symbol}: {len(raw)} raw, {len(items)} parsed", file=sys.stderr)
        return items


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


def _parse_news_entries(raw: list, limit: int) -> List[NewsItem]:
    """Parse yfinance's `Ticker.news` payload into NewsItems. Handles both
    the newer nested schema (`entry["content"]["title"]`, etc., seen in
    yfinance >=0.2.4x) and the older flat schema (`entry["title"]`), since
    this has changed across yfinance versions and isn't itself something we
    control. Skips anything it can't parse rather than raising -- a
    malformed headline shouldn't take down the report."""
    items: List[NewsItem] = []
    for entry in raw:
        if len(items) >= limit:
            break
        if not isinstance(entry, dict):
            continue

        content = entry.get("content")
        if isinstance(content, dict):
            title = content.get("title")
            provider = content.get("provider")
            publisher = provider.get("displayName") if isinstance(provider, dict) else None
            link = None
            for url_field in ("canonicalUrl", "clickThroughUrl"):
                url_obj = content.get(url_field)
                if isinstance(url_obj, dict) and url_obj.get("url"):
                    link = url_obj["url"]
                    break
            published = _format_published(content.get("pubDate"))
        else:
            title = entry.get("title")
            publisher = entry.get("publisher")
            link = entry.get("link")
            published = _format_published(entry.get("providerPublishTime"))

        if title:
            items.append(NewsItem(title=title, publisher=publisher, link=link, published=published))

    return items[:limit]


def _format_published(value) -> Optional[str]:
    """Best-effort normalize a published timestamp to 'YYYY-MM-DD'. Accepts
    an ISO-8601 string (newer yfinance) or a Unix timestamp (older
    yfinance). Returns None rather than raising if it's neither."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return value[:10] if len(value) >= 10 else None
    return None


class StaticProvider:
    """In-memory provider for tests and offline snapshots."""

    def __init__(
        self,
        fundamentals: Dict[str, CompanyFundamentals],
        histories: Dict[str, pd.DataFrame],
        news: Optional[Dict[str, List[NewsItem]]] = None,
    ):
        self._fundamentals = fundamentals
        self._histories = histories
        self._news = news or {}

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

    def get_recent_news(self, symbol: str, limit: int = 3) -> List[NewsItem]:
        return self._news.get(symbol, [])[:limit]
