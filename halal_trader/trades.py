"""Log of the user's REAL trades (as opposed to `track_record.py`, which only
simulates every screener candidate as if it had been taken).

The tool never places orders -- Trade Republic has no API -- so this CSV is
filled in by hand (or by an assistant from screenshots) after each fill.
It exists so that (a) time-stops come from the actual entry day, not the
screener's ranking date, and (b) there is an honest record of what was
actually traded and how it ended.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from .events import time_stop_date, trading_days_left

FIELDNAMES = [
    "symbol", "entry_date", "entry_price", "quantity", "currency", "stop",
    "target", "status", "exit_date", "exit_price", "realized_pct", "notes",
]


@dataclass
class Trade:
    symbol: str
    status: str  # "open" | "closed"
    entry_date: Optional[date] = None
    entry_price: Optional[float] = None
    quantity: Optional[float] = None
    currency: str = ""
    stop: Optional[float] = None
    target: Optional[float] = None
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    realized_pct: Optional[float] = None  # manual override (e.g. app-reported)
    notes: str = ""


def _f(v: str) -> Optional[float]:
    v = (v or "").strip()
    return float(v) if v else None


def _d(v: str) -> Optional[date]:
    v = (v or "").strip()
    return date.fromisoformat(v) if v else None


def load_trades(path: str) -> List[Trade]:
    p = Path(path)
    if not p.exists():
        return []
    trades: List[Trade] = []
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            if not (row.get("symbol") or "").strip():
                continue
            trades.append(
                Trade(
                    symbol=row["symbol"].strip(),
                    status=(row.get("status") or "open").strip().lower(),
                    entry_date=_d(row.get("entry_date", "")),
                    entry_price=_f(row.get("entry_price", "")),
                    quantity=_f(row.get("quantity", "")),
                    currency=(row.get("currency") or "").strip(),
                    stop=_f(row.get("stop", "")),
                    target=_f(row.get("target", "")),
                    exit_date=_d(row.get("exit_date", "")),
                    exit_price=_f(row.get("exit_price", "")),
                    realized_pct=_f(row.get("realized_pct", "")),
                    notes=(row.get("notes") or "").strip(),
                )
            )
    return trades


def trade_return_pct(t: Trade) -> Optional[float]:
    if t.realized_pct is not None:
        return t.realized_pct
    if t.entry_price and t.exit_price:
        return round((t.exit_price - t.entry_price) / t.entry_price * 100, 2)
    return None


def open_positions(trades: List[Trade], today: date, max_days: int = 5) -> List[Dict]:
    """Open trades with their time-stop counted from the ACTUAL entry day.
    `time_stop`/`days_left` are None when the entry date is unknown --
    never guessed."""
    out: List[Dict] = []
    for t in trades:
        if t.status != "open":
            continue
        stop_day = time_stop_date(t.entry_date, max_days) if t.entry_date else None
        out.append(
            {
                "trade": t,
                "time_stop": stop_day,
                "days_left": trading_days_left(today, stop_day) if stop_day else None,
                "overdue": bool(stop_day and today > stop_day),
            }
        )
    return out


def summarize_trades(trades: List[Trade]) -> Optional[Dict]:
    returns = [r for t in trades if t.status == "closed" for r in [trade_return_pct(t)] if r is not None]
    if not returns:
        return None
    wins = sum(1 for r in returns if r > 0)
    return {
        "count": len(returns),
        "wins": wins,
        "win_rate": round(100 * wins / len(returns), 1),
        "avg_return_pct": round(sum(returns) / len(returns), 2),
    }
