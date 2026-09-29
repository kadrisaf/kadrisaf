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

    def __init__(self, user_agent: str = "halal_trader research tool (github.com/kadrisaf/kadrisaf)"):
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
