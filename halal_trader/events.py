"""Hold-window calendar: trading days, scheduled macro events, and earnings.

The screen computes stops and targets, but a 5-day hold can still run into
an earnings report or a market-moving macro print. This module answers
"what is scheduled inside the hold window?" so the report can say so,
instead of that check living only in a human's head.

Nothing here filters or ranks candidates -- it only produces warnings.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from . import config

_HOLIDAYS = {date.fromisoformat(d) for d in config.NYSE_HOLIDAYS}
_HOLIDAY_LAST = max(_HOLIDAYS)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _HOLIDAYS


def hold_window(entry_day: date, max_days: int) -> List[date]:
    """The `max_days` trading days of a hold that starts on `entry_day`
    (counted as day 1, per ADVISORY_PROTOCOL: the clock starts at the actual
    entry). If entry_day isn't a trading day, day 1 is the next one."""
    days: List[date] = []
    d = entry_day
    while len(days) < max_days:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def time_stop_date(entry_day: date, max_days: int = 5) -> date:
    return hold_window(entry_day, max_days)[-1]


def trading_days_left(today: date, stop_day: date) -> int:
    """Trading days remaining after `today` up to and including `stop_day`."""
    left, d = 0, today + timedelta(days=1)
    while d <= stop_day:
        if is_trading_day(d):
            left += 1
        d += timedelta(days=1)
    return left


def holiday_list_covers(day: date) -> bool:
    return day <= _HOLIDAY_LAST


@dataclass
class MacroEvent:
    day: date
    name: str


def load_event_calendar(path: str) -> List[MacroEvent]:
    """CSV with header `date,event`. Missing file -> empty list (the report
    then says the calendar is unavailable rather than claiming 'no events')."""
    p = Path(path)
    if not p.exists():
        return []
    events: List[MacroEvent] = []
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                events.append(MacroEvent(date.fromisoformat(row["date"].strip()), row["event"].strip()))
            except (KeyError, ValueError, AttributeError):
                continue
    return sorted(events, key=lambda e: e.day)


def events_in_window(events: List[MacroEvent], window: List[date]) -> List[MacroEvent]:
    if not window:
        return []
    lo, hi = window[0], window[-1]
    return [e for e in events if lo <= e.day <= hi]


def calendar_covers(events: List[MacroEvent], window: List[date]) -> bool:
    """True only if the calendar has entries at/after the end of the window --
    otherwise 'no events found' could just mean the file ran out."""
    return bool(events) and bool(window) and events[-1].day >= window[-1]


def next_earnings_date(symbol: str, today: Optional[date] = None, yf=None) -> Optional[date]:
    """Best-effort next earnings date via yfinance. Never raises: returns
    None when unknown (the report then tells the user to check manually)."""
    today = today or date.today()
    try:
        if yf is None:
            import yfinance as yf  # type: ignore
        cal = yf.Ticker(symbol).calendar
        raw = None
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date")
        elif cal is not None and hasattr(cal, "loc"):
            try:
                raw = list(cal.loc["Earnings Date"])
            except Exception:
                raw = None
        if raw is None:
            return None
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        candidates = []
        for item in raw:
            d = item.date() if hasattr(item, "date") and callable(item.date) else item
            if isinstance(d, date) and d >= today:
                candidates.append(d)
        return min(candidates) if candidates else None
    except Exception as exc:
        print(f"[events] {symbol}: earnings date lookup failed: {exc}", file=sys.stderr)
        return None


@dataclass
class HoldContext:
    """Everything scheduled inside one hypothetical 5-day hold."""
    window: List[date]
    macro_events: List[MacroEvent]
    calendar_ok: bool  # False when the calendar file doesn't reach the window's end
    earnings: Dict[str, Optional[date]]
