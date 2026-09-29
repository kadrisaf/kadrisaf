"""A minimal, deterministic macro overlay: VIX and the 10-year Treasury
yield, fetched via yfinance (free, no key -- same data source as
everything else). Purely informational context shown at the top of every
report, always, whether or not there's a qualifying candidate that day.

Deliberately NOT a filter or a score input: it doesn't suppress
candidates or change ranking. Turning "VIX is elevated" into an actual
trading rule (widen stops, size down, skip entries) would need backtesting
this project doesn't have -- see sentiment.py and track_record.py for the
same principle applied elsewhere. For now this is a fact on the page, for
a human to weigh, not a rule the code enforces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class MacroSnapshot:
    vix: Optional[float] = None
    vix_label: Optional[str] = None
    ten_year_yield_pct: Optional[float] = None


class MacroProvider(Protocol):
    def get_snapshot(self) -> Optional[MacroSnapshot]:
        """Best-effort. Never raises -- returns None on any failure."""
        ...


def _label_vix(vix: float) -> str:
    """Rough, widely-used VIX bands -- not a precise threshold, just a
    plain-English gloss so the number means something at a glance."""
    if vix < 15:
        return "calm"
    if vix < 20:
        return "normal"
    if vix < 30:
        return "elevated"
    return "very elevated"


class YFinanceMacroProvider:
    def __init__(self):
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError(
                "yfinance is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._yf = yf

    def get_snapshot(self) -> Optional[MacroSnapshot]:
        try:
            vix_hist = self._yf.Ticker("^VIX").history(period="5d")
            tnx_hist = self._yf.Ticker("^TNX").history(period="5d")
        except Exception:
            return None

        vix = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else None
        tnx = float(tnx_hist["Close"].iloc[-1]) if not tnx_hist.empty else None
        # yfinance's ^TNX close IS the yield in percent directly (verified
        # against a live run: raw close 5.2 vs. the real 10Y yield of
        # 5.24% quoted elsewhere the same day) -- earlier code divided by
        # 10 on the (wrong) assumption that CBOE's classic x10 TNX
        # convention still applied to this feed. It doesn't; don't
        # reintroduce that scaling without re-verifying against a second
        # source first.
        ten_year = tnx

        if vix is None and ten_year is None:
            return None

        return MacroSnapshot(
            vix=round(vix, 2) if vix is not None else None,
            vix_label=_label_vix(vix) if vix is not None else None,
            ten_year_yield_pct=round(ten_year, 2) if ten_year is not None else None,
        )
