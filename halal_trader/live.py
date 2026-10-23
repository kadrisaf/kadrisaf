"""Live position monitor: current prices against the standing plan levels.

    python -m halal_trader.live

Monitoring, not screening: the entry signals stay end-of-day by design
(partial bars distort RSI/volume -- see data_provider.drop_incomplete_bar).
This only answers "where are my open positions right now, relative to the
stops, targets and clocks they were given?" Distances are computed in
percent from each trade's own EUR fill, applied to the live USD quote's
move since the entry day's close -- FX drift makes the euro numbers
approximate, the percentages are what matter.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Callable, Dict, List, Optional

from .events import trading_days_left
from .trades import Trade, load_trades, open_positions


def live_rows(
    positions: List[dict],
    quote: Callable[[str], Optional[dict]],
    today: Optional[date] = None,
) -> List[dict]:
    """One row per open position. `quote(symbol)` returns
    {"price": float, "prev_close": float, "entry_day_close": float|None}
    or None on failure."""
    today = today or date.today()
    rows: List[dict] = []
    for p in positions:
        t: Trade = p["trade"]
        q = quote(t.symbol)
        row: Dict = {"symbol": t.symbol, "time_stop": p["time_stop"],
                     "days_left": p["days_left"], "overdue": p["overdue"]}
        if q and q.get("price") and q.get("entry_day_close"):
            live_pct = (q["price"] / q["entry_day_close"] - 1) * 100
            row["live_pct"] = round(live_pct, 2)
            row["day_pct"] = (
                round((q["price"] / q["prev_close"] - 1) * 100, 2)
                if q.get("prev_close") else None
            )
            if t.entry_price and t.stop:
                stop_pct = (t.stop / t.entry_price - 1) * 100
                row["to_stop_pct"] = round(live_pct - stop_pct, 2)
            if t.entry_price and t.target:
                target_pct = (t.target / t.entry_price - 1) * 100
                row["to_target_pct"] = round(target_pct - live_pct, 2)
        rows.append(row)
    return rows


def render(rows: List[dict]) -> str:
    out = [f"{'Pos':6} {'since entry':>12} {'today':>8} {'to stop':>9} {'to target':>10}  exit by"]
    for r in rows:
        se = f"{r['live_pct']:+.2f}%" if r.get("live_pct") is not None else "n/a"
        dy = f"{r['day_pct']:+.2f}%" if r.get("day_pct") is not None else "n/a"
        ts = f"{r['to_stop_pct']:.2f}%" if r.get("to_stop_pct") is not None else "n/a"
        tt = f"{r['to_target_pct']:.2f}%" if r.get("to_target_pct") is not None else "n/a"
        when = r["time_stop"].isoformat() if r["time_stop"] else "unknown"
        extra = " OVERDUE" if r["overdue"] else (f" ({r['days_left']}d left)" if r["days_left"] is not None else "")
        warn = ""
        if r.get("to_stop_pct") is not None and r["to_stop_pct"] < 1.5:
            warn = "  <-- NEAR STOP"
        out.append(f"{r['symbol']:6} {se:>12} {dy:>8} {ts:>9} {tt:>10}  {when}{extra}{warn}")
    return "\n".join(out)


def _yf_quote(symbol: str, entry_date: Optional[date]) -> Optional[dict]:
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        info = t.fast_info
        price = info.get("last_price") or info.get("lastPrice")
        prev = info.get("previous_close") or info.get("previousClose")
        entry_close = None
        if entry_date is not None:
            hist = t.history(period="1mo")
            sub = hist[hist.index.date <= entry_date]
            if len(sub):
                entry_close = float(sub["Close"].iloc[-1])
        return {"price": float(price) if price else None,
                "prev_close": float(prev) if prev else None,
                "entry_day_close": entry_close}
    except Exception as exc:
        print(f"[live] {symbol}: quote failed: {exc}", file=sys.stderr)
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live monitor of open positions vs plan levels")
    ap.add_argument("--trades", default="track_record/real_trades.csv")
    args = ap.parse_args(argv)
    positions = open_positions(load_trades(args.trades), date.today())
    if not positions:
        print("No open positions.")
        return 0
    entry_dates = {p["trade"].symbol: p["trade"].entry_date for p in positions}
    rows = live_rows(positions, lambda s: _yf_quote(s, entry_dates.get(s)))
    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
