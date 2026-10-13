"""Recent SEC filings, via EDGAR's free, no-key-required submissions API.

Same role as data_provider.py's `get_recent_news`: a supplementary,
unscored signal surfaced for manual review, not something that affects
ranking. 8-K filings (material events -- buybacks, executive changes,
M&A, restructuring) tend to be more signal-dense and less noisy than
general news headlines, and unlike news they're a matter of public
record, not editorial framing.

Only covers SEC filers -- i.e. US-listed tickers (including foreign
issuers that file with the SEC). A non-US ticker (SIE.DE, AIR.PA, ...)
just returns no filings, which is correct, not a failure.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence


@dataclass
class FilingItem:
    form: str   # e.g. "8-K"
    filed: str  # "YYYY-MM-DD"
    url: Optional[str] = None


class FilingsProvider(Protocol):
    def get_recent_filings(self, symbol: str, limit: int = 3) -> List[FilingItem]:
        """Best-effort. Never raises -- returns an empty list on any
        failure (unknown ticker, network error, malformed response)."""
        ...


class SecEdgarFilingsProvider:
    """Free, no API key. SEC asks requesters to identify themselves with
    a descriptive User-Agent (https://www.sec.gov/os/accessing-edgar-data)
    -- pass one that names this tool; a missing or generic one risks
    being rate-limited."""

    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

    def __init__(self, user_agent: str = "halal_trader research tool anyway0760@gmail.com"):
        self._user_agent = user_agent
        self._ticker_to_cik: Optional[Dict[str, int]] = None

    def _load_ticker_map(self) -> Dict[str, int]:
        if self._ticker_to_cik is None:
            import requests

            resp = requests.get(
                self.TICKER_MAP_URL, headers={"User-Agent": self._user_agent}, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            self._ticker_to_cik = {
                entry["ticker"].upper(): entry["cik_str"] for entry in data.values()
            }
        return self._ticker_to_cik

    def get_recent_filings(
        self, symbol: str, limit: int = 3, forms: Sequence[str] = ("8-K",)
    ) -> List[FilingItem]:
        try:
            import requests

            ticker_map = self._load_ticker_map()
            # Strip exchange suffixes (SIE.DE -> SIE) -- SEC's map is
            # unsuffixed; a miss here just means "not an SEC filer under
            # this ticker", handled below.
            base_symbol = symbol.split(".")[0].upper()
            cik = ticker_map.get(base_symbol)
            if cik is None:
                print(
                    f"[filings] {symbol}: no CIK match in SEC's ticker map "
                    f"({len(ticker_map)} tickers loaded) -- not an SEC filer "
                    "under this ticker, or the map lookup itself is broken",
                    file=sys.stderr,
                )
                return []
            resp = requests.get(
                self.SUBMISSIONS_URL.format(cik=cik),
                headers={"User-Agent": self._user_agent},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[filings] {symbol}: fetch/parse raised: {exc}", file=sys.stderr)
            return []

        items = _parse_filings(data, cik, forms, limit)
        raw_count = len(data.get("filings", {}).get("recent", {}).get("form", []))
        print(
            f"[filings] {symbol}: cik={cik}, {raw_count} raw filings, "
            f"{len(items)} matching {forms}",
            file=sys.stderr,
        )
        return items


def _parse_filings(data: dict, cik: int, forms: Sequence[str], limit: int) -> List[FilingItem]:
    """Pure parser for EDGAR's submissions JSON shape -- testable without
    the network. Tolerant of a missing/malformed `filings.recent` block."""
    try:
        recent = data["filings"]["recent"]
        form_list = recent["form"]
        filed_list = recent["filingDate"]
        accession_list = recent["accessionNumber"]
        primary_doc_list = recent["primaryDocument"]
    except (KeyError, TypeError):
        return []

    items: List[FilingItem] = []
    for form, filed, accession, primary_doc in zip(
        form_list, filed_list, accession_list, primary_doc_list
    ):
        if form not in forms:
            continue
        accession_no_dashes = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{primary_doc}"
        items.append(FilingItem(form=form, filed=filed, url=url))
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Insider activity (Form 4) -- added Oct 2026 after MRVL: the one pre-entry
# public warning sign was a cluster of insider-sale filings. Extraction,
# not prediction: the report prints a warning line, nothing is scored.
# ---------------------------------------------------------------------------


@dataclass
class InsiderActivity:
    filings_14d: int          # Form 4 filings inside the window
    sell_count: int           # 'S' (open-market sale) transactions parsed
    buy_count: int            # 'P' (open-market purchase) transactions parsed
    sold_value_usd: float     # sum of shares*price for sales (0 when prices missing)
    parsed_docs: int          # how many Form 4 XMLs were actually parsed


def _parse_form4_xml(xml_bytes: bytes):
    """(sell_count, buy_count, sold_value) from one Form 4 XML. Only
    open-market codes S/P count -- option exercises, grants and 10b5-1
    tax-withholding codes are noise for this purpose."""
    import xml.etree.ElementTree as ET

    sells = buys = 0
    sold_value = 0.0
    root = ET.fromstring(xml_bytes)
    for txn in root.iter("nonDerivativeTransaction"):
        code = txn.findtext("transactionCoding/transactionCode")
        shares = txn.findtext("transactionAmounts/transactionShares/value")
        price = txn.findtext("transactionAmounts/transactionPricePerShare/value")
        if code == "S":
            sells += 1
            try:
                sold_value += float(shares) * float(price)
            except (TypeError, ValueError):
                pass
        elif code == "P":
            buys += 1
    return sells, buys, sold_value


def _recent_form4_urls(data: dict, cik: int, days: int, today) -> List[str]:
    """Raw-XML URLs of Form 4 filings within `days` of `today`, newest
    first. The primaryDocument is sometimes an XSL-rendered path
    ('xslF345X05/foo.xml'); stripping the directory yields the raw XML."""
    from datetime import date, timedelta

    try:
        recent = data["filings"]["recent"]
        rows = zip(recent["form"], recent["filingDate"], recent["accessionNumber"],
                   recent["primaryDocument"])
    except (KeyError, TypeError):
        return []
    cutoff = today - timedelta(days=days)
    urls = []
    for form, filed, accession, primary in rows:
        if form != "4":
            continue
        try:
            if date.fromisoformat(filed) < cutoff:
                continue
        except ValueError:
            continue
        doc = primary.split("/")[-1]
        acc = accession.replace("-", "")
        urls.append(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}")
    return urls


class SecEdgarInsiderProvider(SecEdgarFilingsProvider):
    """Form 4 scan on top of the same submissions feed. Never raises."""

    def get_insider_activity(
        self, symbol: str, days: int = 14, max_docs: int = 5, today=None
    ) -> Optional[InsiderActivity]:
        from datetime import date as _date

        today = today or _date.today()
        try:
            import requests

            cik = self._load_ticker_map().get(symbol.split(".")[0].upper())
            if cik is None:
                return None
            resp = requests.get(
                self.SUBMISSIONS_URL.format(cik=cik),
                headers={"User-Agent": self._user_agent}, timeout=10,
            )
            resp.raise_for_status()
            urls = _recent_form4_urls(resp.json(), cik, days, today)
            sells = buys = parsed = 0
            sold_value = 0.0
            for url in urls[:max_docs]:
                try:
                    doc = requests.get(url, headers={"User-Agent": self._user_agent}, timeout=10)
                    doc.raise_for_status()
                    s, b, v = _parse_form4_xml(doc.content)
                    sells += s; buys += b; sold_value += v; parsed += 1
                except Exception as exc:
                    print(f"[insider] {symbol}: doc parse failed: {exc}", file=sys.stderr)
            activity = InsiderActivity(
                filings_14d=len(urls), sell_count=sells, buy_count=buys,
                sold_value_usd=round(sold_value, 0), parsed_docs=parsed,
            )
            print(f"[insider] {symbol}: {activity}", file=sys.stderr)
            return activity
        except Exception as exc:
            print(f"[insider] {symbol}: fetch raised: {exc}", file=sys.stderr)
            return None
