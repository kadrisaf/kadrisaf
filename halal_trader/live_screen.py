"""EXPERIMENTAL live entry screen -- a shadow system, not a ticket source.

    python -m halal_trader.live_screen

Runs the signal rules intraday on a provisional bar: live price as today's
close, today's partial volume scaled up by the elapsed fraction of the US
session (otherwise every name looks volume-dead before noon -- the Oct 5
bug). Symbols checked are those that passed the Shariah screen in the
latest committed report, so no fundamentals are re-fetched.

PAPER ONLY. Signals are logged to track_record/live_signals.csv so the
idea can be validated against what actually followed; it earns the right
to print tickets only if that log eventually beats the end-of-day screen.
Until then the daily report remains the only entry authority.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .data_provider import YFinanceProvider
from .signals import evaluate_signal

US_OPEN_MIN = 9 * 60 + 30     # 09:30 ET
US_CLOSE_MIN = 16 * 60        # 16:00 ET
EU_SUFFIXES = (".DE", ".PA", ".SW", ".VI", ".MI", ".AS", ".BR")
EU_OPEN_MIN = 9 * 60          # 09:00 local (Europe/Berlin -- Xetra & friends)
EU_CLOSE_MIN = 17 * 60 + 30   # 17:30
LOG_FIELDS = ["signalled_at_utc", "symbol", "live_price", "rel_vol_est",
              "session_elapsed_pct", "stop", "target", "rsi", "score"]


def session_elapsed_fraction(now_minutes: int, open_min: int = US_OPEN_MIN,
                             close_min: int = US_CLOSE_MIN) -> Optional[float]:
    """Fraction of a session elapsed; None outside market hours."""
    if now_minutes < open_min or now_minutes > close_min:
        return None
    return max((now_minutes - open_min) / (close_min - open_min), 0.05)


def symbol_session(symbol: str, now_utc: "datetime") -> Optional[float]:
    """Elapsed session fraction for the symbol's own market, or None when
    that market is closed. EU suffixes trade 09:00-17:30 Berlin; everything
    else is treated as US (09:30-16:00 New York)."""
    import zoneinfo

    if symbol.upper().endswith(EU_SUFFIXES):
        local = now_utc.astimezone(zoneinfo.ZoneInfo("Europe/Berlin"))
        return session_elapsed_fraction(local.hour * 60 + local.minute,
                                        EU_OPEN_MIN, EU_CLOSE_MIN)
    local = now_utc.astimezone(zoneinfo.ZoneInfo("America/New_York"))
    return session_elapsed_fraction(local.hour * 60 + local.minute)


def provisional_frame(history: pd.DataFrame, live_price: float,
                      partial_volume: float, elapsed: float) -> pd.DataFrame:
    """Completed daily bars + a provisional today-bar: live price as close,
    partial volume scaled to a full-session estimate."""
    est_volume = partial_volume / elapsed
    today = pd.DataFrame(
        {
            "Open": [live_price], "High": [live_price], "Low": [live_price],
            "Close": [live_price], "Volume": [est_volume],
        },
        index=pd.DatetimeIndex([history.index[-1] + pd.Timedelta(days=1)]),
    )
    return pd.concat([history, today])


def compliant_symbols_from_report(report_path: str) -> List[str]:
    """Symbols that passed the Shariah screen in a committed report: the
    ranked table plus the compliant-but-not-qualifying list."""
    text = Path(report_path).read_text()
    symbols: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^\| \d+ \| \*\*([A-Z0-9.\-]+)\*\* \|", line)
        if m:
            symbols.append(m.group(1))
    section = re.search(
        r"## Shariah-compliant, but no qualifying setup today(.*?)(\n## |\Z)", text, re.S
    )
    if section:
        symbols += re.findall(r"^- \*\*([A-Z0-9.\-]+)\*\*:", section.group(1), re.M)
    return list(dict.fromkeys(symbols))


def log_signals(rows: List[dict], path: str) -> None:
    p = Path(path)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LOG_FIELDS})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EXPERIMENTAL live entry screen (paper only)")
    ap.add_argument("--report", default=None, help="committed report to take the compliant list from")
    ap.add_argument("--log", default="track_record/live_signals.csv")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)

    report = args.report
    if report is None:
        reports = sorted(Path("reports").glob("*.md"))
        if not reports:
            print("No committed reports found.", file=sys.stderr)
            return 1
        report = str(reports[-1])
    symbols = compliant_symbols_from_report(report)
    open_syms = {s: symbol_session(s, now) for s in symbols}
    open_syms = {s: e for s, e in open_syms.items() if e is not None}
    if not open_syms:
        print("No market with universe symbols is open right now (US 15:30-22:00, "
              "EU 09:00-17:30 Berlin). Use the daily report.")
        return 0
    print(f"[live_screen] {len(open_syms)}/{len(symbols)} compliant symbols have an "
          f"open market", file=sys.stderr)

    provider = YFinanceProvider()
    hits: List[dict] = []
    for symbol, elapsed in open_syms.items():
        try:
            import yfinance as yf

            t = yf.Ticker(symbol)
            hist = provider.get_price_history(symbol, period="1y")  # completed bars only
            intraday = t.history(period="1d", interval="1d", prepost=False)
            if intraday.empty:
                continue
            live_price = float(intraday["Close"].iloc[-1])
            partial_vol = float(intraday["Volume"].iloc[-1])
            if intraday.index[-1].date() <= hist.index[-1].date():
                continue  # no live bar yet
            frame = provisional_frame(hist, live_price, partial_vol, elapsed)
            sig = evaluate_signal(symbol, frame)
            if sig.qualifies:
                hits.append({
                    "signalled_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "symbol": symbol, "live_price": round(live_price, 4),
                    "rel_vol_est": sig.relative_volume,
                    "session_elapsed_pct": round(100 * elapsed, 1),
                    "stop": sig.stop_loss, "target": sig.target,
                    "rsi": sig.rsi, "score": sig.score,
                })
        except Exception as exc:
            print(f"[live_screen] {symbol}: {exc}", file=sys.stderr)

    if not hits:
        print("No provisional qualifiers right now.")
    else:
        print("PROVISIONAL qualifiers (PAPER ONLY -- unvalidated, no tickets):")
        for h in hits:
            print(f"  {h['symbol']}: live {h['live_price']} | est rel-vol {h['rel_vol_est']}x "
                  f"| RSI {h['rsi']} | stop {h['stop']} | target {h['target']}")
        log_signals(hits, args.log)
        print(f"Logged {len(hits)} signal(s) to {args.log} for later validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
