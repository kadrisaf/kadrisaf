"""Post-earnings entry study -- do trades taken soon after earnings underperform?

    python -m halal_trader.postearnings_study

Asked by the user on 2026-11-10 after DHI (entered 5 trading days post-
earnings, flagged "post-earnings tape" in the report) sat at -5.7% into
its time-stop. The report WARNS on entries within 7 calendar days of an
earnings report but does not filter them; this study measures whether the
warning deserves promotion to a filter -- the same evidence path the
earnings-inside-window filter took (validated 2026-11-05).

Method: every resolved trade in the simulated ledger (candidates.csv)
and every closed real trade (real_trades.csv) is tagged with the days
between its entry date and the symbol's most recent PRIOR earnings
report (best-effort via yfinance's earnings-dates table; undatable
trades are reported as 'unknown', never silently dropped). Outcomes are
compared across buckets.

Pre-registered promotion rule (written before the data was looked at):
promote the post-earnings warning to a hard filter only if the 0-7d
bucket underperforms the >20d/none bucket by a clear margin (not a few
hundredths of a %) in the SIMULATED ledger with at least 30 trades in
the bucket, AND the real-trade ledger points the same direction. Below
those counts the verdict is "keep measuring", not a rule change. Both
ledgers are small and trades are correlated (same days, same sectors) --
this is a first read that grows with every resolved trade.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .tune import stats

BUCKETS = [
    ("0-7d post-earnings", 0, 7),       # the report's current warning window
    ("8-20d post-earnings", 8, 20),
    (">20d / none recent", 21, 10_000),
]
UNKNOWN = "unknown (no earnings data)"
MIN_BUCKET_N = 30
PROMOTION_MARGIN_PCT = 0.5  # 0-7d bucket must trail >20d bucket's avg by at least this


def fetch_earnings_history(symbol: str, yf=None) -> Optional[List[date]]:
    """All past earnings dates yfinance knows for the symbol (best-effort,
    ~12 quarters). None = lookup failed; [] = looked up, none known."""
    try:
        if yf is None:
            import yfinance as yf  # type: ignore
        df = yf.Ticker(symbol).get_earnings_dates(limit=12)
        if df is None or len(df) == 0:
            return []
        return sorted({ts.date() for ts in df.index if hasattr(ts, "date")})
    except Exception as exc:
        print(f"[postearnings] {symbol}: earnings lookup failed: {exc}", file=sys.stderr)
        return None


def days_since_earnings(entry: date, earnings: Optional[List[date]]) -> Optional[int]:
    """Calendar days from the most recent earnings date on or before entry.
    None when the lookup failed or no prior date is known."""
    if not earnings:
        return None
    prior = [d for d in earnings if d <= entry]
    if not prior:
        return None
    return (entry - max(prior)).days


def bucket_for(days: Optional[int]) -> str:
    if days is None:
        return UNKNOWN
    for name, lo, hi in BUCKETS:
        if lo <= days <= hi:
            return name
    return UNKNOWN


def load_simulated(path: str) -> List[Tuple[str, date, float]]:
    """(symbol, entry_date, return_pct) for resolved simulated candidates."""
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("resolved", "").strip().lower() != "true":
                continue
            try:
                out.append((row["symbol"].strip(),
                            date.fromisoformat(row["date_ranked"].strip()),
                            float(row["return_pct"])))
            except (KeyError, ValueError):
                continue
    return out


def load_real(path: str) -> List[Tuple[str, date, float]]:
    """(symbol, entry_date, realized_pct) for closed real trades."""
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status", "").strip() != "closed":
                continue
            try:
                out.append((row["symbol"].strip(),
                            date.fromisoformat(row["entry_date"].strip()),
                            float(row["realized_pct"])))
            except (KeyError, ValueError):
                continue
    return out


def tag_trades(
    trades: List[Tuple[str, date, float]],
    earnings_fn: Callable[[str], Optional[List[date]]],
    cache: Optional[Dict[str, Optional[List[date]]]] = None,
) -> Dict[str, List[float]]:
    """Group trade returns by post-earnings bucket. One earnings lookup per
    symbol (shared cache across ledgers when passed in)."""
    cache = cache if cache is not None else {}
    grouped: Dict[str, List[float]] = {}
    for symbol, entry, ret in trades:
        if symbol not in cache:
            cache[symbol] = earnings_fn(symbol)
        days = days_since_earnings(entry, cache[symbol])
        grouped.setdefault(bucket_for(days), []).append(ret)
    return grouped


def verdict(sim_grouped: Dict[str, List[float]],
            real_grouped: Dict[str, List[float]]) -> str:
    early, late = BUCKETS[0][0], BUCKETS[2][0]
    s_early, s_late = stats(sim_grouped.get(early, [])), stats(sim_grouped.get(late, []))
    if s_early is None or s_late is None or s_early["n"] < MIN_BUCKET_N:
        n = 0 if s_early is None else s_early["n"]
        return (f"**Verdict: keep measuring.** The simulated 0-7d bucket has {n} trades "
                f"(promotion rule requires >= {MIN_BUCKET_N}); no rule change on this sample.")
    gap = s_late["avg"] - s_early["avg"]
    r_early, r_late = stats(real_grouped.get(early, [])), stats(real_grouped.get(late, []))
    real_agrees = (r_early is not None and r_late is not None
                   and r_late["avg"] > r_early["avg"])
    if gap >= PROMOTION_MARGIN_PCT and real_agrees:
        return (f"**Verdict: evidence supports promotion.** Simulated 0-7d entries trail "
                f"later entries by {gap:.2f}%/trade (>= {PROMOTION_MARGIN_PCT}%) and the "
                "real ledger points the same way. Next step: implement the filter "
                "flag-and-log (excluded names stay logged for counterfactual grading), "
                "as the earnings-window filter was.")
    return (f"**Verdict: warning stays a warning.** Simulated gap ({gap:+.2f}%/trade) "
            f"{'misses the pre-registered margin' if gap < PROMOTION_MARGIN_PCT else 'is not confirmed by the real ledger'}; "
            "no filter without clearer evidence. Re-run as the ledgers grow.")


def render(sim_grouped: Dict[str, List[float]], real_grouped: Dict[str, List[float]],
           as_of: Optional[date] = None) -> str:
    as_of = as_of or date.today()
    order = [b[0] for b in BUCKETS] + [UNKNOWN]

    def table(grouped: Dict[str, List[float]], title: str) -> List[str]:
        total = sum(len(v) for v in grouped.values())
        known = total - len(grouped.get(UNKNOWN, []))
        lines = [f"## {title}", "",
                 f"{total} trades; earnings data dated {known} of them.", "",
                 "| Entry timing | n | Win rate | Avg return | Profit factor |",
                 "|---|---|---|---|---|"]
        for name in order:
            s = stats(grouped.get(name, []))
            if s is None:
                lines.append(f"| {name} | 0 | -- | -- | -- |")
            else:
                pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
                lines.append(f"| {name} | {s['n']} | {s['win_rate']}% | "
                             f"{s['avg']:+.3f}% | {pf} |")
        return lines + [""]

    lines = [
        f"# Post-earnings entry study -- {as_of.isoformat()}",
        "",
        "Do entries taken soon after an earnings report underperform? The report "
        "currently WARNS on entries within 7 days of a report ('post-earnings tape') "
        "but does not filter. Pre-registered promotion rule in the module docstring; "
        "margin: " f"{PROMOTION_MARGIN_PCT}%/trade, minimum {MIN_BUCKET_N} trades in the early bucket.",
        "",
    ]
    lines += table(sim_grouped, "Simulated ledger (every flagged candidate, mechanical exits)")
    lines += table(real_grouped, "Real trades (your fills and exits)")
    lines += [verdict(sim_grouped, real_grouped), "",
              "Caveats: small correlated samples (same days, same sectors); earnings dates "
              "are best-effort (the 'unknown' rows are excluded from the verdict, which "
              "assumes they resemble the dated ones); buckets use calendar days.",
              "", "---", "_Generated by halal_trader.postearnings_study._", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Post-earnings entry outcome study")
    ap.add_argument("--candidates", default="track_record/candidates.csv")
    ap.add_argument("--trades", default="track_record/real_trades.csv")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    sim, real = load_simulated(args.candidates), load_real(args.trades)
    print(f"[postearnings] {len(sim)} simulated + {len(real)} real resolved trades",
          file=sys.stderr)
    cache: Dict[str, Optional[List[date]]] = {}
    sim_grouped = tag_trades(sim, fetch_earnings_history, cache)
    real_grouped = tag_trades(real, fetch_earnings_history, cache)
    text = render(sim_grouped, real_grouped)
    out = args.out or f"backtests/{date.today().isoformat()}-postearnings.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(text)
    print(f"Written to {out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
