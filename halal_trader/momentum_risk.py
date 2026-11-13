"""Risk profile of the momentum-portfolio cells from tune_momentum.

    python -m halal_trader.momentum_risk --universe data/universe_default.csv

Asked by the user on 2026-11-13, after every tune_momentum cell beat the
EW baseline on both halves (the sweep's first pass). This study ADOPTS
NOTHING and TUNES NOTHING: it re-runs the identical backtest and reports
the risk the monthly sleeve would have carried. Momentum's documented
failure mode (Daniel & Moskowitz, "Momentum crashes") is a violent
snap-back month after a bear market -- exactly what a per-month average
hides.

Metrics per cell, same 10y tape and cost model as tune_momentum:
- worst single month; worst rolling 12-month compounded return
- max drawdown and longest underwater stretch (months from a peak until
  it is regained), on the compounded monthly curve
- annualized volatility (monthly std x sqrt(12))
- behaviour when the market hurts: average return in the EW baseline's
  negative months, and in EW's 10 worst months
- max RELATIVE drawdown vs EW (the ratio curve cell/EW), i.e. how far
  behind "just hold everything" the sleeve could fall

Pre-registered red flags (written before the data was looked at; they
flag, they don't tune): (1) max drawdown more than 10 points deeper
than EW's; (2) any single month <= -20%; (3) underwater longer than
24 months. A sleeve with a red flag isn't forbidden -- but the flag goes
in bold at the top and in any pitch to fund it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .cli import load_universe
from .tune_momentum import CELLS, YEARS_DEFAULT, run_test

DD_GAP_FLAG = 10.0     # points deeper than EW's max drawdown
WORST_MONTH_FLAG = -20.0
UNDERWATER_FLAG = 24   # months
N_WORST = 10           # EW's worst months examined


def equity(returns_pct: List[float]) -> np.ndarray:
    return np.cumprod([1.0 + r / 100.0 for r in returns_pct])


def max_drawdown_pct(returns_pct: List[float]) -> float:
    eq = equity(returns_pct)
    peak = np.maximum.accumulate(eq)
    return float(100.0 * (eq / peak - 1.0).min())


def longest_underwater_months(returns_pct: List[float]) -> int:
    eq = equity(returns_pct)
    peak, longest, run = 0.0, 0, 0
    for v in eq:
        if v >= peak:
            peak, run = v, 0
        else:
            run += 1
            longest = max(longest, run)
    return longest


def worst_rolling_12m_pct(returns_pct: List[float]) -> Optional[float]:
    if len(returns_pct) < 12:
        return None
    eq = np.concatenate([[1.0], equity(returns_pct)])
    rolls = eq[12:] / eq[:-12] - 1.0
    return float(100.0 * rolls.min())


def risk_profile(returns_pct: List[float], ew_pct: List[float]) -> Dict:
    r = pd.Series(returns_pct)
    ew = pd.Series(ew_pct)
    worst_idx = ew.nsmallest(N_WORST).index
    rel_curve = pd.Series(equity(returns_pct) / equity(ew_pct))
    rel_dd = float(100.0 * (rel_curve / rel_curve.cummax() - 1.0).min())
    return {
        "worst_month": round(float(r.min()), 1),
        "worst_12m": round(worst_rolling_12m_pct(returns_pct), 1)
        if worst_rolling_12m_pct(returns_pct) is not None else None,
        "max_dd": round(max_drawdown_pct(returns_pct), 1),
        "underwater": longest_underwater_months(returns_pct),
        "ann_vol": round(float(r.std(ddof=0)) * np.sqrt(12.0), 1),
        "avg_in_ew_down": round(float(r[ew < 0].mean()), 2) if (ew < 0).any() else None,
        "avg_in_ew_worst": round(float(r[worst_idx].mean()), 2),
        "min_in_ew_worst": round(float(r[worst_idx].min()), 1),
        "rel_dd_vs_ew": round(rel_dd, 1),
    }


def red_flags(profile: Dict, ew_profile: Dict) -> List[str]:
    flags = []
    if profile["max_dd"] < ew_profile["max_dd"] - DD_GAP_FLAG:
        flags.append(
            f"drawdown {profile['max_dd']:+.1f}% is more than {DD_GAP_FLAG:g} points "
            f"deeper than EW's {ew_profile['max_dd']:+.1f}%"
        )
    if profile["worst_month"] <= WORST_MONTH_FLAG:
        flags.append(f"worst month {profile['worst_month']:+.1f}% breaches {WORST_MONTH_FLAG:g}%")
    if profile["underwater"] > UNDERWATER_FLAG:
        flags.append(f"{profile['underwater']} months underwater breaches {UNDERWATER_FLAG}")
    return flags


def run_risk(result: Dict) -> Dict:
    ew_rets = result["returns"]["ew"]
    ew_profile = risk_profile(ew_rets, ew_rets)
    rows = []
    for key, label in CELLS:
        prof = risk_profile(result["returns"][key], ew_rets)
        rows.append({
            "key": key, "label": label, "profile": prof,
            "flags": [] if key == "ew" else red_flags(prof, ew_profile),
        })
    return {"rows": rows, "n_periods": result["n_periods"],
            "symbols_used": result.get("symbols_used", []),
            "years": result.get("years"),
            "first": str(result["period_dates"][0].date()),
            "last": str(result["period_dates"][-1].date())}


def render_markdown(risk: Dict, as_of: date) -> str:
    lines = [
        f"# Momentum sleeve risk profile -- {as_of.isoformat()}",
        "",
        f"Same backtest as the {as_of.isoformat()} tune_momentum study "
        f"({len(risk['symbols_used'])} compliant symbols, {risk['years']}y, "
        f"{risk['n_periods']} months, {risk['first']} to {risk['last']}). "
        "Nothing adopted or tuned here -- this is the bill for the returns.",
        "",
        f"Pre-registered red flags: max drawdown > {DD_GAP_FLAG:g} points deeper than "
        f"EW's; any month <= {WORST_MONTH_FLAG:g}%; underwater > {UNDERWATER_FLAG} months.",
        "",
        "| Portfolio | Worst month | Worst 12m | Max drawdown | Underwater (mo) "
        "| Ann. vol | Avg in EW-down months | Avg in EW's 10 worst | Worst of those "
        "| Max lag behind EW |",
        "|" + "---|" * 10,
    ]
    for row in risk["rows"]:
        p = row["profile"]
        label = f"**{row['label']}**" if row["key"] == "ew" else row["label"]
        w12 = f"{p['worst_12m']:+.1f}%" if p["worst_12m"] is not None else "--"
        ewd = f"{p['avg_in_ew_down']:+.2f}%" if p["avg_in_ew_down"] is not None else "--"
        lines.append(
            f"| {label} | {p['worst_month']:+.1f}% | {w12} | {p['max_dd']:+.1f}% "
            f"| {p['underwater']} | {p['ann_vol']:.1f}% | {ewd} "
            f"| {p['avg_in_ew_worst']:+.2f}% | {p['min_in_ew_worst']:+.1f}% "
            f"| {p['rel_dd_vs_ew']:+.1f}% |"
        )

    lines += ["", "## Red flags", ""]
    any_flag = False
    for row in risk["rows"]:
        for f in row["flags"]:
            any_flag = True
            lines.append(f"- **{row['label']}: {f}.**")
    if not any_flag:
        lines.append("None of the pre-registered red flags fired.")
    lines += [
        "",
        "Reading guide: 'Max lag behind EW' is the worst peak-to-trough of the "
        "sleeve RELATIVE to holding everything equally -- how far behind the lazy "
        "portfolio it could fall before catching up. Survivorship inflates every "
        "absolute number here just as it does in the return study; the comparisons "
        "between rows are the robust part.",
        "", "---", "_Generated by halal_trader.momentum_risk._", "",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Risk profile of the monthly momentum sleeve")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=YEARS_DEFAULT)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(run_risk(result), date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-momentum-risk.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
