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
    # Logged features, kept for the descriptive breakdowns only -- they
    # never feed back into scoring or ranking.
    rel_vol_est: Optional[float] = None
    session_elapsed_pct: Optional[float] = None
    score: Optional[float] = None

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


def _opt_float(raw: Optional[str]) -> Optional[float]:
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


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
                    rel_vol_est=_opt_float(row.get("rel_vol_est")),
                    session_elapsed_pct=_opt_float(row.get("session_elapsed_pct")),
                    score=_opt_float(row.get("score")),
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


def fetch_histories(
    signals: List[LiveSignal], provider: DataProvider,
) -> Dict[str, pd.DataFrame]:
    histories: Dict[str, pd.DataFrame] = {}
    for sig in signals:
        if sig.symbol in histories:
            continue
        try:
            histories[sig.symbol] = provider.get_price_history(
                sig.symbol, period="6mo"
            )
        except DataUnavailable as exc:
            print(f"[live_validate] {sig.symbol}: {exc}", file=sys.stderr)
            histories[sig.symbol] = pd.DataFrame()
    return histories


def score_all(
    signals: List[LiveSignal],
    provider: DataProvider = None,
    histories: Dict[str, pd.DataFrame] = None,
) -> List[SignalOutcome]:
    if histories is None:
        histories = fetch_histories(signals, provider)
    outcomes: List[SignalOutcome] = []
    for sig in signals:
        hist = histories.get(sig.symbol, pd.DataFrame())
        if hist.empty:
            outcomes.append(SignalOutcome(signal=sig, resolved=False))
            continue
        outcomes.append(score_signal(sig, hist))
    return outcomes


def close_entry_outcome(
    sig: LiveSignal,
    history: pd.DataFrame,
    max_days: int = None,
    slippage_pct: float = None,
) -> Optional[SignalOutcome]:
    """The EOD counterfactual of the same signal: entry at the signal
    day's CLOSE instead of the logged live price, with the SAME stop and
    target levels, so the only difference measured is the entry price and
    time. Returns None when the pair isn't comparable: no completed bar
    for the signal day yet, or the close already sits at/beyond the stop
    or target -- an end-of-day process would simply not have entered
    there, so there is no honest pairing."""
    if max_days is None:
        max_days = config.DEFAULT_SIGNAL_PARAMS.max_holding_days
    if slippage_pct is None:
        slippage_pct = config.EXIT_SLIPPAGE_PCT

    dates = history.index.date
    day1 = history[dates == sig.signal_date]
    if day1.empty:
        return None
    close = float(day1["Close"].iloc[-1])
    if close <= sig.stop or close >= sig.target:
        return None

    subsequent = history[dates > sig.signal_date]
    if subsequent.empty:
        return SignalOutcome(signal=sig, resolved=False)
    outcome = simulate_exit(
        subsequent, sig.stop, sig.target, max_days - 1, slippage_pct
    )
    if outcome is None:
        return SignalOutcome(signal=sig, resolved=False)
    exit_date, exit_price, reason = outcome
    return SignalOutcome(
        signal=sig,
        resolved=True,
        exit_date=exit_date,
        exit_price=round(float(exit_price), 4),
        exit_reason=reason,
        return_pct=round((exit_price - close) / close * 100, 2),
    )


def entry_value_study(
    signals: List[LiveSignal], histories: Dict[str, pd.DataFrame],
) -> Optional[Dict]:
    """What is intraday entry actually worth? Each signal is graded twice
    -- from the logged live price and from that day's close, identical
    stop/target levels and hold window -- and only pairs where BOTH
    resolve are compared. The average per-trade difference is the entire
    value of entering intraday instead of reading the evening report.

    Not comparable (and counted as such): the live entry resolved on the
    signal day itself (an EOD process would never have been in the
    trade), or the close was already beyond the levels."""
    pairs = []
    n_not_comparable = 0
    n_pending = 0
    for sig in signals:
        hist = histories.get(sig.symbol, pd.DataFrame())
        if hist.empty:
            continue
        live = score_signal(sig, hist)
        if live.resolved and live.exit_date == sig.signal_date:
            n_not_comparable += 1
            continue
        eod = close_entry_outcome(sig, hist)
        if eod is None:
            n_not_comparable += 1
            continue
        if not (live.resolved and eod.resolved):
            n_pending += 1
            continue
        pairs.append((live.return_pct, eod.return_pct))
    if not pairs:
        return None
    deltas = sorted(l - e for l, e in pairs)
    mid = len(deltas) // 2
    median = (deltas[mid] if len(deltas) % 2
              else (deltas[mid - 1] + deltas[mid]) / 2)
    return {
        "pairs": len(pairs),
        "not_comparable": n_not_comparable,
        "pending": n_pending,
        "avg_live_pct": round(sum(l for l, _ in pairs) / len(pairs), 2),
        "avg_eod_pct": round(sum(e for _, e in pairs) / len(pairs), 2),
        "avg_delta_pct": round(sum(deltas) / len(deltas), 2),
        "median_delta_pct": round(median, 2),
    }


# Feature buckets for the descriptive breakdowns. Two buckets per feature
# on purpose: with tens of trades, finer splits are pure noise.
BREAKDOWN_BUCKETS = [
    ("score", lambda s: s.score,
     lambda v, cut: "high" if v >= cut else "low", "median"),
    ("rel_vol_est", lambda s: s.rel_vol_est,
     lambda v, cut: ">=1.5x" if v >= 1.5 else "<1.5x", 1.5),
    ("session_elapsed_pct", lambda s: s.session_elapsed_pct,
     lambda v, cut: "late session" if v >= 50 else "early session", 50),
]


def bucket_breakdowns(outcomes: List[SignalOutcome]) -> List[Dict]:
    """Descriptive splits of resolved outcomes by the logged features.
    NEVER rules: with this few, correlated trades, any bucket can look
    good by chance, and three features x two buckets is already six
    comparisons. A split only graduates into a filter via the same
    logged-counterfactual process as the earnings filter."""
    resolved = [o for o in outcomes
                if o.resolved and o.return_pct is not None]
    rows: List[Dict] = []
    for name, getter, labeler, cut in BREAKDOWN_BUCKETS:
        with_val = [o for o in resolved if getter(o.signal) is not None]
        if len(with_val) < 2:
            continue
        if cut == "median":
            vals = sorted(getter(o.signal) for o in with_val)
            cut = vals[len(vals) // 2]
        groups: Dict[str, List[SignalOutcome]] = {}
        for o in with_val:
            groups.setdefault(labeler(getter(o.signal), cut), []).append(o)
        for label, members in sorted(groups.items()):
            wins = sum(1 for o in members if o.return_pct > 0)
            rows.append({
                "feature": name,
                "bucket": label,
                "n": len(members),
                "win_rate": round(100 * wins / len(members), 1),
                "avg_return_pct": round(
                    sum(o.return_pct for o in members) / len(members), 2),
            })
    return rows


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
    study: Optional[Dict] = None,
    breakdowns: Optional[List[Dict]] = None,
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
    if study is not None:
        lines += [
            "",
            "## Entry-value study: what is intraday entry worth?",
            "",
            "Each signal graded twice -- from the logged live price and from",
            "the same day's close, identical stop/target levels and hold",
            "window. The delta is the entire value of acting intraday instead",
            "of reading the evening report. Pairs where the live entry",
            "resolved the same day, or the close was already past a level,",
            "aren't comparable and are counted, not compared.",
            "",
            f"- Comparable pairs: {study['pairs']} "
            f"({study['not_comparable']} not comparable, "
            f"{study['pending']} pending)",
            f"- Avg return from live entry: {study['avg_live_pct']:+.2f}%",
            f"- Avg return from same-day close entry: {study['avg_eod_pct']:+.2f}%",
            f"- Avg intraday-entry edge: {study['avg_delta_pct']:+.2f}% per trade "
            f"(median {study['median_delta_pct']:+.2f}%)",
        ]
    if breakdowns:
        lines += [
            "",
            "## Descriptive breakdowns (NOT rules)",
            "",
            "Resolved deduped signals split by logged features. Six",
            "comparisons over a few dozen correlated trades: any bucket can",
            "look good by chance. A split becomes a filter only via the same",
            "logged-counterfactual path the earnings filter took -- flag",
            "first, grade the excluded group, adopt on out-of-sample margin.",
            "",
            "| Feature | Bucket | n | Win rate | Avg return |",
            "|---|---|---|---|---|",
        ]
        for r in breakdowns:
            lines.append(
                f"| {r['feature']} | {r['bucket']} | {r['n']} "
                f"| {r['win_rate']}% | {r['avg_return_pct']:+.2f}% |"
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
    histories = fetch_histories(signals, provider)
    deduped_outcomes = score_all(deduped, histories=histories)
    all_outcomes = score_all(signals, histories=histories)
    study = entry_value_study(deduped, histories)
    breakdowns = bucket_breakdowns(deduped_outcomes)

    since = min(s.signal_date for s in signals)
    benchmark = eod_benchmark(args.track_record, since)

    as_of = datetime.now().date()
    report = build_validation_report(
        deduped_outcomes, all_outcomes, benchmark, as_of,
        study=study, breakdowns=breakdowns,
    )
    out = args.out or f"backtests/{as_of.isoformat()}-live-validation.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(report)
    print(report)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
