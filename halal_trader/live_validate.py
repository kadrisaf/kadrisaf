"""Validation scorer for the EXPERIMENTAL live entry screen.

    python -m halal_trader.live_validate

The live screen (live_screen.py) has been logging its intraday signals to
track_record/live_signals.csv since Oct 2026, under one standing rule: it is
PAPER ONLY until this scorer says otherwise. This module is the report card.
Each logged signal is replayed through the same exit engine the daily plan
uses (track_record.simulate_exit: stop-before-target, gap fills at the open,
slippage on market exits, 5-trading-day time-stop) against the real daily
bars that followed, and the result is compared with the end-of-day screen's
own outcome tracker over the same dates.

Scoring assumptions -- chosen so the shadow system cannot grade itself
generously:

- Entry is the logged live price. That assumes an instant fill with no
  spread, which already flatters it slightly; everything else leans the
  other way.
- The signal day itself is judged on its CLOSE only. Daily bars can't say
  whether the day's low/high printed before or after the signal fired, and
  using them would leak pre-signal price action into the grade. A stop is
  checked before a target on that close, like everywhere else.
- Day 1 = the signal day (same convention as the real-trade time-stop), so
  full bars only count from day 2 onward.
- A symbol signalled several times the same day is ONE trade (first signal
  of the day). The all-rows view is also reported, but the headline is the
  deduped one -- re-logging the same setup at 15:47 and again at 19:43 is
  the same bet seen twice, not two bets.
- Overlapping signals are correlated (same tape, same week). The summary
  says so; n here is smaller than it looks.

This module never touches ranking, tickets, or the report. It only reads
the log and writes a markdown verdict.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import config
from .data_provider import DataProvider, DataUnavailable
from .track_record import load_track_record, simulate_exit


@dataclass
class LiveSignal:
    signalled_at_utc: str
    symbol: str
    live_price: float
    stop: float
    target: float
    # Non-empty when the live screen's earnings filter (added 2026-11-05,
    # after the NOW collapse) flagged the signal: logged for the
    # counterfactual, never shown as a qualifier. Rows before the filter
    # existed have "" -- they are graded as-is, which is honest: the
    # system really did signal them.
    earnings_note: str = ""

    @property
    def signal_date(self) -> date:
        # Logged timestamps are UTC; every US session hour (09:30-16:00 ET)
        # falls on the same UTC calendar date, and the EU session likewise.
        return datetime.strptime(
            self.signalled_at_utc, "%Y-%m-%dT%H:%M:%SZ"
        ).date()


@dataclass
class SignalOutcome:
    signal: LiveSignal
    resolved: bool
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "stop" | "target" | "time_stop"
    return_pct: Optional[float] = None


def load_signals(path: str) -> List[LiveSignal]:
    p = Path(path)
    if not p.exists():
        return []
    out: List[LiveSignal] = []
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                out.append(LiveSignal(
                    signalled_at_utc=row["signalled_at_utc"],
                    symbol=row["symbol"],
                    live_price=float(row["live_price"]),
                    stop=float(row["stop"]),
                    target=float(row["target"]),
                    earnings_note=(row.get("earnings_note") or "").strip(),
                ))
            except (KeyError, ValueError) as exc:
                print(f"[live_validate] skipping malformed row {row}: {exc}",
                      file=sys.stderr)
    return out


def first_per_symbol_day(signals: List[LiveSignal]) -> List[LiveSignal]:
    """One trade per (symbol, day): the first signal logged that day. The
    log keeps every scan's hits, so a setup that stays valid all afternoon
    appears two or three times -- that's the same bet, not new evidence."""
    seen: Dict[tuple, LiveSignal] = {}
    for sig in sorted(signals, key=lambda s: s.signalled_at_utc):
        key = (sig.symbol, sig.signal_date)
        if key not in seen:
            seen[key] = sig
    return list(seen.values())


def score_signal(
    sig: LiveSignal,
    history: pd.DataFrame,
    max_days: int = None,
    slippage_pct: float = None,
) -> SignalOutcome:
    """Replay one signal against the completed daily bars that followed it.

    Day 1 (the signal day) is judged on its close only -- daily bars can't
    order the day's low/high around the signal time, and granting the full
    bar would leak pre-signal price action into the result. Stop is checked
    before target on that close, matching simulate_exit's convention. From
    day 2 the full bars go through simulate_exit itself, with the remaining
    hold budget (max_days - 1).
    """
    if max_days is None:
        max_days = config.DEFAULT_SIGNAL_PARAMS.max_holding_days
    if slippage_pct is None:
        slippage_pct = config.EXIT_SLIPPAGE_PCT
    slip = slippage_pct / 100.0

    dates = history.index.date
    day1 = history[dates == sig.signal_date]
    if not day1.empty:
        close = float(day1["Close"].iloc[-1])
        if close <= sig.stop:
            return _resolved(sig, sig.signal_date, close * (1 - slip), "stop")
        if close >= sig.target:
            return _resolved(sig, sig.signal_date, float(sig.target), "target")

    subsequent = history[dates > sig.signal_date]
    if subsequent.empty:
        return SignalOutcome(signal=sig, resolved=False)

    outcome = simulate_exit(
        subsequent, sig.stop, sig.target, max_days - 1, slippage_pct
    )
    if outcome is None:
        return SignalOutcome(signal=sig, resolved=False)
    exit_date, exit_price, reason = outcome
    return _resolved(sig, exit_date, exit_price, reason)


def _resolved(sig: LiveSignal, exit_date: date, exit_price: float,
              reason: str) -> SignalOutcome:
    return SignalOutcome(
        signal=sig,
        resolved=True,
        exit_date=exit_date,
        exit_price=round(float(exit_price), 4),
        exit_reason=reason,
        return_pct=round(
            (exit_price - sig.live_price) / sig.live_price * 100, 2
        ),
    )


def score_all(
    signals: List[LiveSignal], provider: DataProvider,
) -> List[SignalOutcome]:
    outcomes: List[SignalOutcome] = []
    histories: Dict[str, pd.DataFrame] = {}
    for sig in signals:
        if sig.symbol not in histories:
            try:
                histories[sig.symbol] = provider.get_price_history(
                    sig.symbol, period="6mo"
                )
            except DataUnavailable as exc:
                print(f"[live_validate] {sig.symbol}: {exc}", file=sys.stderr)
                histories[sig.symbol] = pd.DataFrame()
        hist = histories[sig.symbol]
        if hist.empty:
            outcomes.append(SignalOutcome(signal=sig, resolved=False))
            continue
        outcomes.append(score_signal(sig, hist))
    return outcomes


def summarize(outcomes: List[SignalOutcome]) -> Optional[Dict]:
    """Same descriptive stats as the EOD tracker: count, wins, win rate,
    average return. Not a probability, not an edge claim."""
    resolved = [o for o in outcomes if o.resolved and o.return_pct is not None]
    if not resolved:
        return None
    wins = sum(1 for o in resolved if o.return_pct > 0)
    by_reason: Dict[str, int] = {}
    for o in resolved:
        by_reason[o.exit_reason] = by_reason.get(o.exit_reason, 0) + 1
    return {
        "count": len(resolved),
        "pending": len(outcomes) - len(resolved),
        "wins": wins,
        "win_rate": round(100 * wins / len(resolved), 1),
        "avg_return_pct": round(
            sum(o.return_pct for o in resolved) / len(resolved), 2
        ),
        "by_reason": by_reason,
    }


def eod_benchmark(track_record_path: str, since: date) -> Optional[Dict]:
    """The EOD screen's own tracker over the same dates -- the bar the
    shadow system has to clear, computed the same way."""
    records = [
        r for r in load_track_record(track_record_path)
        if date.fromisoformat(r.date_ranked) >= since
    ]
    resolved = [r for r in records if r.resolved and r.return_pct is not None]
    if not resolved:
        return None
    wins = sum(1 for r in resolved if r.return_pct > 0)
    return {
        "count": len(resolved),
        "pending": len(records) - len(resolved),
        "wins": wins,
        "win_rate": round(100 * wins / len(resolved), 1),
        "avg_return_pct": round(
            sum(r.return_pct for r in resolved) / len(resolved), 2
        ),
    }


def _fmt_summary(s: Optional[Dict]) -> str:
    if s is None:
        return "no resolved trades yet"
    line = (f"{s['count']} resolved, {s['wins']} wins "
            f"({s['win_rate']}%), avg {s['avg_return_pct']:+.2f}%/trade")
    if s.get("pending"):
        line += f", {s['pending']} still pending"
    if s.get("by_reason"):
        parts = ", ".join(f"{k} {v}" for k, v in sorted(s["by_reason"].items()))
        line += f" [exits: {parts}]"
    return line


def build_validation_report(
    deduped: List[SignalOutcome],
    all_rows: List[SignalOutcome],
    benchmark: Optional[Dict],
    as_of: date,
) -> str:
    clean = [o for o in deduped if not o.signal.earnings_note]
    flagged = [o for o in deduped if o.signal.earnings_note]
    ded_sum = summarize(clean)
    flag_sum = summarize(flagged)
    all_sum = summarize(all_rows)
    lines = [
        f"# Live-screen validation -- {as_of.isoformat()}",
        "",
        "The shadow (intraday) screen's logged signals, replayed through the",
        "same exit engine as the daily plan (stop before target, gap fills at",
        "the open, slippage on market exits, 5-trading-day time-stop; the",
        "signal day itself is judged on its close only, because daily bars",
        "can't order intraday prices around the signal time).",
        "",
        "**Standing rule: the live screen stays PAPER ONLY unless this page",
        "shows it clearly beating the end-of-day screen over the same dates.**",
        "",
        "## Headline (one trade per symbol per day)",
        "",
        f"- Shadow signals: {_fmt_summary(ded_sum)}",
        f"- EOD screen, same dates: {_fmt_summary(benchmark)}",
        "",
        f"All logged rows (re-scans of the same setup included): "
        f"{_fmt_summary(all_sum)}",
        "",
        "Signals logged before 2026-11-05 predate the earnings filter and",
        "are graded as-is -- the system really did signal them. Rows the",
        "filter has flagged since then are logged for the counterfactual",
        "and graded separately below, never as qualifiers.",
        "",
        f"Earnings-flagged signals (what the filter excluded): "
        f"{_fmt_summary(flag_sum)}",
        "",
        "## Per-signal outcomes (deduped)",
        "",
        "| Signalled | Symbol | Entry | Stop | Target | Exit | Reason | Return |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for o in sorted(deduped, key=lambda o: (o.signal.signal_date, o.signal.symbol)):
        s = o.signal
        sym = f"{s.symbol} (earnings-flagged)" if s.earnings_note else s.symbol
        if o.resolved:
            lines.append(
                f"| {s.signal_date} | {sym} | {s.live_price} | {s.stop} "
                f"| {s.target} | {o.exit_date} ({o.exit_price}) "
                f"| {o.exit_reason} | {o.return_pct:+.2f}% |"
            )
        else:
            lines.append(
                f"| {s.signal_date} | {sym} | {s.live_price} | {s.stop} "
                f"| {s.target} | -- | pending | -- |"
            )
    lines += [
        "",
        "## Caveats (read before believing anything above)",
        "",
        "- Entry at the logged live price assumes an instant, spread-free",
        "  fill -- the one assumption here that flatters the shadow system.",
        "- Signals cluster on the same days and names, so they are",
        "  correlated bets on the same tape; the effective sample is smaller",
        "  than the row count.",
        "- A few weeks of signals is far too little to establish an edge",
        "  either way. This page is a tripwire against an obviously bad",
        "  idea, not proof of a good one.",
        "- The EOD benchmark covers the screen's top-ranked candidates on",
        "  the same dates; the two systems do not pick identical names, so",
        "  this is a comparison of systems, not of fills.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Score the live screen's logged signals (paper validation)"
    )
    ap.add_argument("--log", default="track_record/live_signals.csv")
    ap.add_argument("--track-record", default="track_record/candidates.csv")
    ap.add_argument("--out", default=None,
                    help="markdown output path (default: backtests/<date>-live-validation.md)")
    args = ap.parse_args(argv)

    from .data_provider import YFinanceProvider

    signals = load_signals(args.log)
    if not signals:
        print("No logged live signals to score.", file=sys.stderr)
        return 1
    deduped = first_per_symbol_day(signals)
    print(f"[live_validate] {len(signals)} rows -> {len(deduped)} unique "
          f"(symbol, day) signals", file=sys.stderr)

    provider = YFinanceProvider()
    deduped_outcomes = score_all(deduped, provider)
    # Re-use the same histories implicitly: score_all caches per symbol.
    all_outcomes = score_all(signals, provider)

    since = min(s.signal_date for s in signals)
    benchmark = eod_benchmark(args.track_record, since)

    as_of = datetime.now().date()
    report = build_validation_report(deduped_outcomes, all_outcomes, benchmark, as_of)
    out = args.out or f"backtests/{as_of.isoformat()}-live-validation.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(report)
    print(report)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
