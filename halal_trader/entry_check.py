"""Pre-entry confirmation check -- VETO-ONLY, run shortly after the US open.

    python -m halal_trader.entry_check

Entries get decided from the morning (EOD) report; fills happen after
15:30 Berlin when the US market opens and spreads tighten. This module
closes the gap between those two moments: it re-checks the morning
report's RANKED names against live prices ~15 minutes into the US
session and says, per name, whether the setup the report described still
exists at the price you'd actually pay.

Authority model (deliberate, validated 2026-11-05):
- The EOD report remains the ONLY source of entry candidates. The live
  screen lost its validation round (55.6% win / -0.18% per trade vs the
  EOD screen's 58.6% / +0.20%), so live data gets veto power only: this
  check can downgrade a morning candidate to CAUTION, it can never
  promote a name the report didn't rank. No back-door ticket rights.

Per ranked name it reports:
- the live price vs the report's entry close (the gap you'd be paying),
- whether the signal still qualifies on a provisional live bar (same
  evaluate_signal, same partial-volume scaling as live_screen),
- whether price has already crossed the report's stop or target,
- the same earnings-window check the live screen applies.

CONFIRM means "the trade you read about this morning is still the trade
on offer". CAUTION means at least one of those checks failed -- with the
reasons, so the reader can judge. It is still not a buy instruction.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import config
from .live_screen import earnings_note, provisional_frame, symbol_session

# A live price this far from the report's entry close means you are no
# longer being offered the trade the report sized (the ATR levels and the
# R:R were computed from that close).
MAX_GAP_PCT = 2.0


@dataclass
class RankedRow:
    symbol: str
    close: float
    stop: float
    target: float


@dataclass
class CheckResult:
    row: RankedRow
    live_price: Optional[float] = None
    gap_pct: Optional[float] = None
    still_qualifies: Optional[bool] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.live_price is None:
            return "NO DATA"
        return "CONFIRM" if not self.reasons else "CAUTION"


def parse_ranked_rows(report_text: str) -> List[RankedRow]:
    """The ranked table's symbol, close, stop and target columns:
    | 1 | **EPAM** | 137.36 | 62.48 | 2.9x | 125.2423 | 155.5365 | ...
    """
    rows: List[RankedRow] = []
    for line in report_text.splitlines():
        m = re.match(
            r"^\| \d+ \| \*\*([A-Z0-9.\-]+)\*\* \| ([\d.]+) \| [\d.]+ "
            r"\| [\d.]+x \| ([\d.]+) \| ([\d.]+) \|",
            line,
        )
        if m:
            rows.append(RankedRow(
                symbol=m.group(1), close=float(m.group(2)),
                stop=float(m.group(3)), target=float(m.group(4)),
            ))
    return rows


def check_row(
    row: RankedRow,
    live_price: float,
    signal_qualifies: bool,
    earn_note: str = "",
    max_gap_pct: float = MAX_GAP_PCT,
) -> CheckResult:
    """Pure verdict logic -- everything fetched is passed in."""
    gap = (live_price - row.close) / row.close * 100
    result = CheckResult(
        row=row, live_price=live_price, gap_pct=round(gap, 2),
        still_qualifies=signal_qualifies,
    )
    if live_price <= row.stop:
        result.reasons.append(
            f"live price {live_price:.2f} is at/below the report's stop "
            f"{row.stop:.2f} -- the trade is already over"
        )
    if live_price >= row.target:
        result.reasons.append(
            f"live price {live_price:.2f} is at/above the report's target "
            f"{row.target:.2f} -- the move already happened"
        )
    if abs(gap) > max_gap_pct:
        result.reasons.append(
            f"gapped {gap:+.1f}% from the report's entry close -- the "
            f"sized levels no longer describe this entry"
        )
    if not signal_qualifies:
        result.reasons.append(
            "no longer qualifies on live data (trend/RSI/volume check failed)"
        )
    if earn_note:
        result.reasons.append(earn_note)
    return result


def build_check_report(results: List[CheckResult], now_utc: datetime) -> str:
    lines = [
        f"# Pre-entry check -- {now_utc.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "VETO-ONLY confirmation of the morning report's ranked names at",
        "live prices, ~15 minutes into the US session. CONFIRM means the",
        "setup the report sized still exists at the price on offer;",
        "CAUTION lists what changed. This check can only remove names --",
        "it never adds any, and it is not a buy instruction. The morning",
        "report's flags (stale rows, insiders, streaks, resistance) still",
        "apply and are NOT re-checked here.",
        "",
        "| Symbol | Report close | Live | Gap | Verdict |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        if r.live_price is None:
            lines.append(
                f"| **{r.row.symbol}** | {r.row.close} | -- | -- | NO DATA |"
            )
            continue
        lines.append(
            f"| **{r.row.symbol}** | {r.row.close} | {r.live_price:.2f} "
            f"| {r.gap_pct:+.1f}% | {r.verdict} |"
        )
    cautions = [r for r in results if r.verdict == "CAUTION"]
    if cautions:
        lines += ["", "## Caution details", ""]
        for r in cautions:
            for reason in r.reasons:
                lines.append(f"- **{r.row.symbol}**: {reason}")
    lines += [
        "",
        "Reminder: enter only from the morning report's tickets; place the",
        "stop with the buy; if your fill differs from the report's entry",
        "close, rescale stop/target by the report's percentage distances.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Veto-only pre-entry check of the morning report's ranked names"
    )
    ap.add_argument("--report", default=None,
                    help="report to check (default: latest committed)")
    ap.add_argument("--out-dir", default="reports/entry_checks")
    args = ap.parse_args(argv)

    report = args.report
    if report is None:
        reports = sorted(p for p in Path("reports").glob("*.md"))
        if not reports:
            print("No committed reports found.", file=sys.stderr)
            return 1
        report = str(reports[-1])
    rows = parse_ranked_rows(Path(report).read_text())
    if not rows:
        print(f"No ranked candidates in {report} -- nothing to confirm.")
        return 0

    now = datetime.now(timezone.utc)
    from .data_provider import YFinanceProvider

    provider = YFinanceProvider()
    results: List[CheckResult] = []
    for row in rows:
        elapsed = symbol_session(row.symbol, now)
        if elapsed is None:
            results.append(CheckResult(row=row))  # market closed -> NO DATA
            continue
        try:
            import yfinance as yf

            hist = provider.get_price_history(row.symbol, period="1y")
            intraday = yf.Ticker(row.symbol).history(
                period="1d", interval="1d", prepost=False
            )
            if intraday.empty or intraday.index[-1].date() <= hist.index[-1].date():
                results.append(CheckResult(row=row))
                continue
            live_price = float(intraday["Close"].iloc[-1])
            partial_vol = float(intraday["Volume"].iloc[-1])
            from .signals import evaluate_signal

            frame = provisional_frame(hist, live_price, partial_vol, elapsed)
            sig = evaluate_signal(row.symbol, frame)
            note = earnings_note(row.symbol, now.date())
            results.append(check_row(row, live_price, sig.qualifies, note))
        except Exception as exc:
            print(f"[entry_check] {row.symbol}: {exc}", file=sys.stderr)
            results.append(CheckResult(row=row))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{now.date().isoformat()}.md"
    text = build_check_report(results, now)
    out.write_text(text)
    print(text)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
