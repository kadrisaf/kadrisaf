"""Approximate Shariah compliance screen (business activity + financial ratios).

IMPORTANT: this is a rules-based approximation of common industry screening
methodologies (similar in spirit to Dow Jones Islamic Market / S&P Shariah
indices). It is **not** a certified Shariah ruling. Use it to narrow a
universe, then verify anything you actually intend to trade against a
certified source (e.g. Zoya, Musaffa, Islamicly, or your own scholar/board)
-- especially the business-activity classification, which depends entirely
on how the data provider labels a company's sector/industry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import config
from .data_provider import CompanyFundamentals


@dataclass
class ScreenResult:
    symbol: str
    compliant: bool
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    debt_ratio: Optional[float] = None
    cash_ratio: Optional[float] = None
    receivables_ratio: Optional[float] = None


def screen_company(fundamentals: CompanyFundamentals) -> ScreenResult:
    """Apply the sector-exclusion and financial-ratio screens to one company."""
    reasons: List[str] = []
    notes: List[str] = []

    # -- 1. Business activity (sector/industry) screen -----------------
    haystack = " ".join(
        filter(None, [fundamentals.sector, fundamentals.industry])
    ).lower()
    if not haystack:
        notes.append("no sector/industry data -- business-activity screen skipped")
    else:
        for keyword in config.EXCLUDED_SECTOR_KEYWORDS:
            if keyword in haystack:
                reasons.append(
                    f"excluded business activity: '{fundamentals.industry or fundamentals.sector}' "
                    f"matches keyword '{keyword}'"
                )
                break  # one match is enough to disqualify

    # -- 2. Financial ratio screens (denominator = market cap) ---------
    market_cap = fundamentals.market_cap
    debt_ratio = cash_ratio = receivables_ratio = None

    if not market_cap or market_cap <= 0:
        notes.append("no market cap -- financial ratio screens skipped")
    else:
        if fundamentals.total_debt is not None:
            debt_ratio = fundamentals.total_debt / market_cap
            if debt_ratio >= config.MAX_DEBT_TO_MARKET_CAP:
                reasons.append(
                    f"debt/market-cap {debt_ratio:.0%} >= {config.MAX_DEBT_TO_MARKET_CAP:.0%}"
                )
        else:
            notes.append("total debt unavailable -- debt ratio skipped")

        if fundamentals.cash_and_short_term_investments is not None:
            cash_ratio = fundamentals.cash_and_short_term_investments / market_cap
            if cash_ratio >= config.MAX_CASH_AND_INTEREST_SECURITIES_TO_MARKET_CAP:
                reasons.append(
                    f"cash & interest-bearing securities/market-cap {cash_ratio:.0%} "
                    f">= {config.MAX_CASH_AND_INTEREST_SECURITIES_TO_MARKET_CAP:.0%}"
                )
        else:
            notes.append("cash position unavailable -- cash ratio skipped")

        if fundamentals.receivables is not None:
            receivables_ratio = fundamentals.receivables / market_cap
            if receivables_ratio >= config.MAX_RECEIVABLES_TO_MARKET_CAP:
                reasons.append(
                    f"receivables/market-cap {receivables_ratio:.0%} "
                    f">= {config.MAX_RECEIVABLES_TO_MARKET_CAP:.0%}"
                )
        else:
            notes.append("receivables unavailable -- receivables ratio skipped")

    return ScreenResult(
        symbol=fundamentals.symbol,
        compliant=len(reasons) == 0,
        reasons=reasons,
        notes=notes,
        debt_ratio=debt_ratio,
        cash_ratio=cash_ratio,
        receivables_ratio=receivables_ratio,
    )
