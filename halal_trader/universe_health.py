"""Universe hygiene: which names have been failing the Shariah screen in
every recent report?

    python -m halal_trader.universe_health --reports reports --days 30

Parses the committed daily reports (no network), counts how often each
symbol appeared under "Excluded on Shariah screen", and lists persistent
failers with their most recent reason -- candidates for removal from
`data/universe_default.csv`. Removal itself stays a human decision:
ratios drift back under the thresholds too.
"""

from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_LINE = re.compile(r"^- \*\*(?P<symbol>[A-Z0-9.\-]+)\*\*: (?P<reason>.+)$")


def parse_exclusions(text: str) -> Dict[str, str]:
    """Symbol -> reason from one report's Shariah-exclusion section."""
    out: Dict[str, str] = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == "## Excluded on Shariah screen"
            continue
        if in_section:
            m = _LINE.match(line.strip())
            if m:
                out[m.group("symbol")] = m.group("reason")
    return out


def collect(reports_dir: str, days: int, today: Optional[date] = None) -> Tuple[List[dict], int]:
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    counts: Dict[str, int] = {}
    latest_reason: Dict[str, str] = {}
    n_reports = 0
    for p in sorted(Path(reports_dir).glob("*.md")):
        try:
            report_day = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if report_day < cutoff:
            continue
        n_reports += 1
        for sym, reason in parse_exclusions(p.read_text()).items():
            counts[sym] = counts.get(sym, 0) + 1
            latest_reason[sym] = reason
    rows = [
        {"symbol": s, "count": c, "reason": latest_reason[s]}
        for s, c in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return rows, n_reports


def render(rows: List[dict], n_reports: int, days: int, as_of: date) -> str:
    persistent = [r for r in rows if n_reports and r["count"] >= 0.9 * n_reports]
    lines = [
        f"# Universe health -- {as_of.isoformat()}",
        "",
        f"{n_reports} reports from the last {days} days. Symbols below failed the "
        f"Shariah screen in >=90% of them -- candidates for removal from the "
        f"universe (a human decision: ratios drift back too).",
        "",
    ]
    if not persistent:
        lines.append("No persistent failers in this window.")
    else:
        lines += ["| Symbol | Failed in | Latest reason |", "|---|---|---|"]
        for r in persistent:
            lines.append(f"| **{r['symbol']}** | {r['count']}/{n_reports} reports | {r['reason']} |")
    occasional = [r for r in rows if r not in persistent]
    if occasional:
        lines += ["", "Occasional failers (borderline ratios, kept): "
                  + ", ".join(f"{r['symbol']} ({r['count']}/{n_reports})" for r in occasional)]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Universe hygiene from committed reports")
    ap.add_argument("--reports", default="reports")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rows, n = collect(args.reports, args.days)
    md = render(rows, n, args.days, date.today())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
