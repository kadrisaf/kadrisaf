"""Portfolio-level risk context: correlation between candidates and open
positions, and the fixed-cost drag of small positions.

None of this predicts anything. Correlation says "this is partly the same
bet you already hold"; cost drag says "below this size, fees eat the edge
the backtest says you don't have". Both are warnings in the report, never
filters.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import config
from .data_provider import DataProvider, DataUnavailable


def return_correlation(
    a: pd.DataFrame, b: pd.DataFrame, lookback: int = 60
) -> Optional[float]:
    """Pearson correlation of the two symbols' last `lookback` daily returns
    on their common dates. None when there's too little overlap to mean
    anything (< 20 common days)."""
    ra = a["Close"].pct_change().dropna()
    rb = b["Close"].pct_change().dropna()
    ra.index, rb.index = ra.index.date, rb.index.date
    common = ra.index.intersection(rb.index)
    if len(common) < 20:
        return None
    ra, rb = ra.loc[common].iloc[-lookback:], rb.loc[common].iloc[-lookback:]
    corr = ra.corr(rb)
    return round(float(corr), 2) if pd.notna(corr) else None


def correlation_flags(
    ranked_symbols: List[str],
    open_symbols: List[str],
    provider: DataProvider,
    threshold: float = None,
    lookback: int = 60,
) -> Dict[str, List[Tuple[str, float]]]:
    """For each ranked symbol, the open positions whose daily returns
    correlate >= threshold with it. Fetch failures are skipped (warnings
    can be incomplete, never wrong)."""
    threshold = threshold if threshold is not None else config.CORRELATION_WARN
    histories: Dict[str, pd.DataFrame] = {}

    def hist(sym: str) -> Optional[pd.DataFrame]:
        if sym not in histories:
            try:
                histories[sym] = provider.get_price_history(sym, period="6mo")
            except DataUnavailable as exc:
                print(f"[risk] {sym}: no history for correlation: {exc}", file=sys.stderr)
                histories[sym] = None
        return histories[sym]

    out: Dict[str, List[Tuple[str, float]]] = {}
    for cand in ranked_symbols:
        flags: List[Tuple[str, float]] = []
        hc = hist(cand)
        for pos in open_symbols:
            if pos == cand:
                continue  # the report already flags "already held" explicitly
            hp = hist(pos) if hc is not None else None
            if hc is None or hp is None:
                continue
            corr = return_correlation(hc, hp, lookback)
            if corr is not None and corr >= threshold:
                flags.append((pos, corr))
        out[cand] = flags
    return out


def cost_drag_pct(position_eur: float = None) -> float:
    """Round-trip fixed fees as % of a position. Trade Republic: 1 EUR per
    order, so 2 EUR per round trip."""
    position_eur = position_eur or config.TYPICAL_POSITION_EUR
    return round(2 * config.FEE_PER_ORDER_EUR / position_eur * 100, 2)


def order_ticket(
    entry: float,
    stop: float,
    target: float,
    portfolio_eur: float = None,
    risk_pct: float = None,
) -> Optional[Dict]:
    """Mechanical position sizing: (portfolio * risk%) / (entry - stop),
    capped at MAX_POSITION_PCT_OF_PORTFOLIO (no leverage, ever). Returns the
    share count and the cash amounts in the *entry's own currency*, computed
    on the portfolio number as if 1:1 -- the report tells the user to convert
    at their actual fill. None when the inputs can't size a trade."""
    portfolio_eur = portfolio_eur if portfolio_eur is not None else config.PORTFOLIO_EUR
    risk_pct = risk_pct if risk_pct is not None else config.RISK_PCT_PER_TRADE
    risk_amount = portfolio_eur * risk_pct / 100.0
    per_share_risk = entry - stop
    if per_share_risk <= 0 or entry <= 0 or risk_amount <= 0:
        return None
    shares = risk_amount / per_share_risk
    capped = False
    max_value = portfolio_eur * config.MAX_POSITION_PCT_OF_PORTFOLIO / 100.0
    if shares * entry > max_value:
        shares = max_value / entry
        capped = True
    return {
        "shares": round(shares, 4),
        "position_value": round(shares * entry, 2),
        "risk_amount": round(shares * per_share_risk, 2),
        "reward_amount": round(shares * (target - entry), 2),
        "capped": capped,
        "risk_pct": risk_pct,
        "portfolio": portfolio_eur,
    }
