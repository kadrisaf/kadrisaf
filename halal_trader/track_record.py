"""Outcome tracking: records every ranked candidate's actual forward
return, so there's eventually a real track record to evaluate the screen
against -- instead of just a rolling series of reports nobody adds up.

This is the missing piece for ever honestly building a calibrated
"probability of return" model (see sentiment.py's docstring and
ADVISORY_PROTOCOL.md): you can't calibrate against a track record that
doesn't exist. This module just builds that record, nothing more --
it doesn't feed back into ranking or scoring.

Stored as a flat CSV (`track_record/candidates.csv` by default) so it's
diffable in git and needs no database.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .data_provider import DataProvider, DataUnavailable
from .shariah_screen import ScreenResult
from .signals import TradeSignal

FIELDNAMES = [
    "date_ranked",
    "symbol",
    "entry_close",
    "stop",
    "target",
    "max_holding_days",
    "resolved",
    "resolution_date",
    "exit_price",
    "exit_reason",
    "return_pct",
]


@dataclass
class TrackedCandidate:
    date_ranked: str  # ISO date of the report that ranked it
    symbol: str
    entry_close: float
    stop: float
    target: float
    max_holding_days: int
    resolved: bool = False
    resolution_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "stop" | "target" | "time_stop"
    return_pct: Optional[float] = None


def load_track_record(path: str) -> List[TrackedCandidate]:
    p = Path(path)
    if not p.exists():
        return []
    records: List[TrackedCandidate] = []
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            records.append(
                TrackedCandidate(
                    date_ranked=row["date_ranked"],
                    symbol=row["symbol"],
                    entry_close=float(row["entry_close"]),
                    stop=float(row["stop"]),
                    target=float(row["target"]),
                    max_holding_days=int(row["max_holding_days"]),
                    resolved=row["resolved"] == "True",
                    resolution_date=row["resolution_date"] or None,
                    exit_price=float(row["exit_price"]) if row["exit_price"] else None,
                    exit_reason=row["exit_reason"] or None,
                    return_pct=float(row["return_pct"]) if row["return_pct"] else None,
                )
            )
    return records


def save_track_record(path: str, records: List[TrackedCandidate]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in records:
            writer.writerow(
                {
                    "date_ranked": r.date_ranked,
                    "symbol": r.symbol,
                    "entry_close": r.entry_close,
                    "stop": r.stop,
                    "target": r.target,
                    "max_holding_days": r.max_holding_days,
                    "resolved": r.resolved,
                    "resolution_date": r.resolution_date or "",
                    "exit_price": r.exit_price if r.exit_price is not None else "",
                    "exit_reason": r.exit_reason or "",
                    "return_pct": r.return_pct if r.return_pct is not None else "",
                }
            )


def record_new_candidates(
    records: List[TrackedCandidate],
    ranked: List[Tuple[ScreenResult, TradeSignal]],
    as_of: date,
) -> List[TrackedCandidate]:
    """Append today's ranked candidates as new, unresolved rows. Skips a
    symbol already recorded for this exact date (safe to call multiple
    times per day, e.g. a manual re-run)."""
    existing = {(r.date_ranked, r.symbol) for r in records}
    as_of_str = as_of.isoformat()
    for _screen, sig in ranked:
        key = (as_of_str, sig.symbol)
        if key in existing or sig.stop_loss is None or sig.target is None:
            continue
        records.append(
            TrackedCandidate(
                date_ranked=as_of_str,
                symbol=sig.symbol,
                entry_close=sig.last_close,
                stop=sig.stop_loss,
                target=sig.target,
                max_holding_days=sig.max_holding_days,
            )
        )
    return records


def resolve_pending(
    records: List[TrackedCandidate], provider: DataProvider, slippage_pct: float = 0.0
) -> List[TrackedCandidate]:
    """Walk every unresolved row forward through real price history and
    resolve it the same way a human following the plan would: whichever
    of stop, target, or the time-stop is hit first. When a stop and a
    target would both trigger on the same bar, the stop is checked first
    -- the conservative assumption, so this never silently inflates the
    track record's apparent win rate."""
    for rec in records:
        if rec.resolved:
            continue
        try:
            history = provider.get_price_history(rec.symbol, period="3mo")
        except DataUnavailable:
            continue  # try again next run

        entry_date = date.fromisoformat(rec.date_ranked)
        subsequent = history[history.index.date > entry_date]
        if subsequent.empty:
            continue

        outcome = simulate_exit(
            subsequent, rec.stop, rec.target, rec.max_holding_days, slippage_pct
        )
        if outcome is not None:
            exit_date, exit_price, reason = outcome
            _resolve(rec, exit_date, exit_price, reason)
    return records


def simulate_exit(
    subsequent: pd.DataFrame,
    stop: float,
    target: float,
    max_days: int,
    slippage_pct: float = 0.0,
) -> Optional[Tuple[date, float, str]]:
    """Walk bars after entry (day 1 = first bar) and return
    (exit_date, exit_price, reason), or None if nothing has triggered yet.

    Execution assumptions, chosen to avoid flattering the result:
    - Stop is checked before target on the same bar.
    - A gap down through the stop fills at the open, not at the stop.
    - Stop and time-stop exits are market-style, so `slippage_pct` is taken
      off the fill. The target is a limit order: filled at the target
      exactly, with no credit for a gap above it.
    """
    slip = slippage_pct / 100.0
    holding_day = 0
    for idx, row in subsequent.iterrows():
        holding_day += 1
        open_, low, high, close = row["Open"], row["Low"], row["High"], row["Close"]
        if low <= stop:
            fill = min(stop, float(open_)) if open_ is not None and not pd.isna(open_) else stop
            return idx.date(), fill * (1 - slip), "stop"
        if high >= target:
            return idx.date(), float(target), "target"
        if holding_day >= max_days:
            return idx.date(), float(close) * (1 - slip), "time_stop"
    return None


def _resolve(rec: TrackedCandidate, exit_date: date, exit_price: float, reason: str) -> None:
    rec.resolved = True
    rec.resolution_date = exit_date.isoformat()
    rec.exit_price = round(float(exit_price), 4)
    rec.exit_reason = reason
    rec.return_pct = round((exit_price - rec.entry_close) / rec.entry_close * 100, 2)


def summarize_track_record(records: List[TrackedCandidate]) -> Optional[Dict]:
    """Plain descriptive stats over resolved trades -- count, win rate,
    average return. Not a probability estimate, not a claim of edge; just
    what actually happened so far. Returns None until there's at least
    one resolved trade."""
    resolved = [r for r in records if r.resolved and r.return_pct is not None]
    if not resolved:
        return None
    wins = sum(1 for r in resolved if r.return_pct > 0)
    avg_return = sum(r.return_pct for r in resolved) / len(resolved)
    return {
        "count": len(resolved),
        "wins": wins,
        "win_rate": round(100 * wins / len(resolved), 1),
        "avg_return_pct": round(avg_return, 2),
    }
