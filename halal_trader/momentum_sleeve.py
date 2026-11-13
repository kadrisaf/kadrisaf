"""Monthly 3-month-momentum sleeve: live rebalance report.

    python -m halal_trader.momentum_sleeve --value 3000

Funded by the user on 2026-11-13 with LONG-TERM money, after the
2026-11-13 tune_momentum study (3-month momentum, top 10, beat the EW
baseline by >= +1.27%/month on both halves) and the momentum_risk study
(worst month -35.6%, max drawdown -35.6%, up to 18 months underwater,
and as much as -21% behind "just hold everything" before catching up --
all red-flagged and accepted with eyes open).

THIS IS A SEPARATE PRODUCT from the daily 6-day system. Separate money,
separate ledger (track_record/momentum_trades.csv), separate holdings
file (track_record/momentum_holdings.csv). Nothing here touches the
short-term budget, the daily report, or its track record.

Live rules, fixed 2026-11-13 (the faithful mapping of the backtest):
- Score = close[t-21] / close[t-84] - 1 on the symbol's own daily bars:
  the past 3-month return ending one month ago (skip month kept).
- Universe = names passing the Shariah screen ON REBALANCE DAY, with
  >= 85 daily bars and a close within the last 7 calendar days.
- Hold the top 10, equal weight AT ENTRY. Each month: SELL the names
  that left the top 10, BUY the entrants with the proceeds split
  equally. Names that stay are NOT touched (the backtest charged costs
  only on the replaced fraction -- re-weighting survivors monthly would
  be a different, more expensive strategy).
- Report on the 1st of each month (GitHub Actions); execute on the
  first trading day after it, AFTER 15:30 Berlin, never pre-market.
- A BUY name unavailable on Trade Republic -> take the next-ranked
  alternate from the report's list.
- No stop-losses, no time-stops, no discretionary exits between
  rebalances: the study held through -30% months; selling into one and
  skipping the rebound is how the backtested edge is forfeited.

Not investment advice; the sleeve can trail the lazy portfolio for
years and the user has chosen to carry that.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import load_overrides, screen_company

SKIP = 21
LOOKBACK = 63
TOP_N = 10
N_ALTERNATES = 5
MIN_BARS = SKIP + LOOKBACK + 1
STALE_DAYS = 7
HOLDINGS_CSV = "track_record/momentum_holdings.csv"
TRADES_CSV = "track_record/momentum_trades.csv"
HOLDINGS_FIELDS = ["symbol", "entry_date", "entry_price", "quantity", "currency"]
TRADES_FIELDS = ["date", "action", "symbol", "price", "quantity", "currency", "notes"]


@dataclass
class Holding:
    symbol: str
    entry_date: str
    entry_price: float
    quantity: float
    currency: str = "EUR"


def momentum_score(df: pd.DataFrame, today: Optional[date] = None) -> Optional[float]:
    """Past 3-month return ending one month ago, or None if ineligible."""
    if df is None or len(df) < MIN_BARS:
        return None
    if today is not None:
        last = df.index[-1]
        last = (last.tz_localize(None) if getattr(last, "tz", None) else last).date()
        if (today - last).days > STALE_DAYS:
            return None
    close = df["Close"]
    past, recent = close.iloc[-1 - SKIP - LOOKBACK], close.iloc[-1 - SKIP]
    if not (past > 0):
        return None
    return float(recent / past - 1.0)


def rank_universe(
    universe: List[str],
    provider: Optional[DataProvider] = None,
    today: Optional[date] = None,
) -> Tuple[List[Tuple[str, float, float]], List[str]]:
    """[(symbol, score, last_close)] sorted best-first over the names that
    pass the Shariah screen and eligibility today; plus skip notes."""
    provider = provider or YFinanceProvider()
    today = today or date.today()
    overrides = load_overrides()
    ranked: List[Tuple[str, float, float]] = []
    skipped: List[str] = []
    for symbol in universe:
        ov = overrides.get(symbol.upper())
        if ov and ov["action"] == "exclude":
            continue  # manual overrides bind the sleeve exactly like the daily screen
        try:
            fundamentals = provider.get_fundamentals(symbol)
            cleared = bool(ov and ov["action"] == "allow")
            if not screen_company(fundamentals, summary_cleared=cleared).compliant:
                continue
            df = provider.get_price_history(symbol, period="1y")
        except DataUnavailable as exc:
            skipped.append(f"{symbol}: {exc}")
            continue
        score = momentum_score(df, today)
        if score is None:
            skipped.append(f"{symbol}: ineligible (history/staleness)")
            continue
        ranked.append((symbol, score, float(df["Close"].iloc[-1])))
        print(f"[sleeve] {symbol}: {score:+.1%}", file=sys.stderr)
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked, skipped


def load_holdings(path: str = HOLDINGS_CSV) -> List[Holding]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open() as fh:
        return [
            Holding(r["symbol"], r["entry_date"], float(r["entry_price"]),
                    float(r["quantity"]), r.get("currency") or "EUR")
            for r in csv.DictReader(fh)
        ]


def save_holdings(holdings: List[Holding], path: str = HOLDINGS_CSV) -> None:
    with Path(path).open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HOLDINGS_FIELDS)
        w.writeheader()
        for h in holdings:
            w.writerow({"symbol": h.symbol, "entry_date": h.entry_date,
                        "entry_price": h.entry_price, "quantity": h.quantity,
                        "currency": h.currency})


def rebalance_plan(
    ranked: List[Tuple[str, float, float]], held: List[str]
) -> Dict[str, List]:
    target = [s for s, _, _ in ranked[:TOP_N]]
    target_set = set(target)
    held_set = set(held)
    return {
        "target": target,
        "sells": sorted(held_set - target_set),
        "holds": [s for s in target if s in held_set],
        "buys": [s for s in target if s not in held_set],
        "alternates": [s for s, _, _ in ranked[TOP_N : TOP_N + N_ALTERNATES]],
    }


def render_markdown(
    ranked: List[Tuple[str, float, float]],
    plan: Dict[str, List],
    holdings: List[Holding],
    as_of: date,
    value_eur: Optional[float] = None,
    skipped: Optional[List[str]] = None,
) -> str:
    prices = {s: px for s, _, px in ranked}
    scores = {s: sc for s, sc, _ in ranked}
    lines = [
        f"# Momentum sleeve rebalance -- {as_of.isoformat()}",
        "",
        "> Long-term sleeve, separate from the daily system and its budget. "
        "3-month momentum (skip month), top 10, equal weight at entry; survivors "
        "are not re-traded. **Execute on the first trading day after this report, "
        "after 15:30 Berlin.** No stops, no early exits -- the plan holds through "
        "bad months on purpose. Not investment advice.",
        "",
        "## Target portfolio (top 10 by 3-month momentum)",
        "",
        "| # | Symbol | 3m momentum (ending 1 mo ago) | Last close (data ccy) | Status |",
        "|---|---|---|---|---|",
    ]
    held = {h.symbol for h in holdings}
    for i, s in enumerate(plan["target"], 1):
        status = "HOLD (already owned)" if s in held else "**BUY**"
        lines.append(f"| {i} | {s} | {scores[s]:+.1%} | {prices[s]:.2f} | {status} |")

    lines += ["", "## Do this month", ""]
    if not plan["sells"] and not plan["buys"]:
        lines.append("- **Nothing. Every holding is still in the top 10.**")
    if plan["sells"]:
        lines.append("1. **SELL (left the top 10):** " + ", ".join(f"**{s}**" for s in plan["sells"]))
    if plan["buys"]:
        n_buys = len(plan["buys"])
        step = "2." if plan["sells"] else "1."
        if value_eur and not held:
            per = value_eur / TOP_N
            lines.append(
                f"{step} **BUY, ~{per:,.0f} EUR each (sleeve {value_eur:,.0f} EUR / {TOP_N}):** "
                + ", ".join(f"**{s}**" for s in plan["buys"])
            )
        else:
            lines.append(
                f"{step} **BUY (split the sell proceeds equally over {n_buys}):** "
                + ", ".join(f"**{s}**" for s in plan["buys"])
            )
    if plan["alternates"]:
        lines.append(
            f"- If a BUY is not available on Trade Republic, take the next alternate, in "
            f"order: {', '.join(plan['alternates'])}."
        )
    lines.append("- Send fill screenshots as usual; they go to `track_record/momentum_trades.csv`.")

    if holdings:
        lines += ["", "## Current holdings (from the ledger)", "",
                  "| Symbol | Entry | Qty |", "|---|---|---|"]
        for h in holdings:
            lines.append(f"| {h.symbol} | {h.entry_date} @ {h.entry_price} {h.currency} | {h.quantity} |")

    if skipped:
        lines += ["", f"<sub>{len(skipped)} symbols skipped (no data / ineligible).</sub>"]
    lines += ["", "---", "_Generated by halal_trader.momentum_sleeve._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Monthly momentum sleeve rebalance report")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--value", type=float, default=None,
                    help="EUR to deploy on the FIRST buy-in (ignored once holdings exist)")
    ap.add_argument("--holdings", default=HOLDINGS_CSV)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    ranked, skipped = rank_universe(load_universe(args.universe))
    holdings = load_holdings(args.holdings)
    plan = rebalance_plan(ranked, [h.symbol for h in holdings])
    md = render_markdown(ranked, plan, holdings, date.today(), args.value, skipped)
    out = args.out or f"reports/momentum/{date.today().isoformat()}.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
