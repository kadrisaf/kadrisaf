"""Split-sample test: cross-sectional momentum portfolio (monthly rebalance).

    python -m halal_trader.tune_momentum --universe data/universe_default.csv

Asked by the user on 2026-11-13 -- the last untested row of the strategy
catalogue. THIS IS A DIFFERENT PRODUCT from the live 6-day system: a
long-only portfolio of the halal universe's recent winners, rebalanced
every 21 trading days. Even a pass would mean a separate sleeve, not a
change to the daily screen.

The claim on trial (classic Jegadeesh-Titman momentum): stocks that rose
the most over the past 3-12 months, SKIPPING the most recent month (the
short-term reversal window), keep outperforming over the next month.

Construction, fixed in advance:
- Universe: today's Shariah-compliant names (survivorship caveat as in
  every study), union trading calendar, forward-filled closes.
- Rebalance every REBALANCE=21 rows starting after a 252+21 row warmup.
- Score at rebalance row r: close[r-21] / close[r-21-L] - 1, with
  lookback L in {63, 126, 252} -- i.e. the window ends one month ago.
- Eligible at r only if the symbol has real (non-forward-filled) data
  through the whole lookback window.
- Cells: EW baseline (every eligible name, equal weight), top-10 by each
  lookback, plus top-5 for the 12-month lookback. Equal weight at each
  rebalance, buy-and-hold inside the month.
- Costs: 0.1% slippage per side on the fraction of the portfolio
  replaced at each rebalance (first month pays the full buy-in). Weight
  drift inside the month is ignored for costing (small, and it biases
  every cell alike).

The benchmark is the EW cell -- same calendar, same universe, same cost
model. Beating "just hold everything equally" is the whole claim.

Pre-registered decision rule (written before the data was looked at):
adopt a momentum cell only if its average return per period beats EW's
by at least MARGIN_PCT = 0.15%/month on BOTH halves (first half of the
shared calendar = train, second = validation), with at least
MIN_PERIODS = 24 periods per half. One-half winners are multiple
comparisons, not discoveries; 'no change' is a good result.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .tune import stats
from .tune_pairs import build_close_matrix

REBALANCE = 21
SKIP = 21                      # most recent month excluded from the score
LOOKBACKS = {"m3": 63, "m6": 126, "m12": 252}
WARMUP_ROWS = max(LOOKBACKS.values()) + SKIP   # 273: same first period for every cell
MARGIN_PCT = 0.15              # %/month over EW, both halves
MIN_PERIODS = 24               # per half
YEARS_DEFAULT = 10             # a monthly strategy needs a long tape
SLIPPAGE_PCT = config.EXIT_SLIPPAGE_PCT  # 0.1% per side, charged on turnover

CELLS = [
    ("ew", "EW baseline (all eligible, equal weight)"),
    ("m3_top10", "3-month momentum, top 10"),
    ("m6_top10", "6-month momentum, top 10"),
    ("m12_top10", "12-month momentum, top 10"),
    ("m12_top5", "12-month momentum, top 5"),
]


def momentum_scores(
    closes: pd.DataFrame, first_real: pd.Series, r: int, lookback: int, skip: int = SKIP
) -> pd.Series:
    """Score at rebalance row r = close[r-skip]/close[r-skip-lookback] - 1,
    only for symbols whose real (pre-ffill) data starts early enough to
    cover the whole window. Causal: uses rows strictly before r only when
    skip >= 1 (it is, by construction)."""
    start = r - skip - lookback
    if start < 0:
        return pd.Series(dtype=float)
    past, recent = closes.iloc[start], closes.iloc[r - skip]
    eligible = first_real <= start
    score = (recent / past - 1.0).where(eligible)
    return score.dropna()


def eligible_at(closes: pd.DataFrame, first_real: pd.Series, r: int, lookback: int) -> List[str]:
    return list(momentum_scores(closes, first_real, r, lookback).index)


def turnover_cost_pct(old: Sequence[str], new: Sequence[str], slippage_pct: float) -> float:
    """Slippage charged per side on the replaced fraction of the book.
    First rebalance (old empty) pays the one-sided full buy-in."""
    if not new:
        return 0.0
    if not old:
        return slippage_pct
    kept = len(set(old) & set(new)) / len(new)
    return 2.0 * slippage_pct * (1.0 - kept)


def period_return_pct(closes: pd.DataFrame, members: Sequence[str], r: int, r_next: int) -> float:
    """Equal weight at r, buy-and-hold to r_next: mean of member simple
    returns, in percent."""
    if not members:
        return 0.0
    rel = closes.iloc[r_next][list(members)] / closes.iloc[r][list(members)] - 1.0
    return float(100.0 * rel.mean())


def run_test(
    universe: List[str],
    years: int = YEARS_DEFAULT,
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    histories: Dict[str, pd.DataFrame] = {}
    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years}y")
        except DataUnavailable as exc:
            print(f"[momentum] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < WARMUP_ROWS + 2 * REBALANCE:
            continue
        histories[symbol] = df
        print(f"[momentum] {symbol}: {len(df)} bars", file=sys.stderr)

    closes = build_close_matrix(histories)
    return run_on_matrix(closes, slippage_pct=slippage_pct) | {
        "symbols_used": sorted(histories), "years": years,
    }


def run_on_matrix(closes: pd.DataFrame, slippage_pct: float = SLIPPAGE_PCT) -> Dict:
    """The whole backtest on a prebuilt close matrix (tests enter here)."""
    first_real = pd.Series(
        {s: closes.index.get_loc(closes[s].first_valid_index()) for s in closes.columns}
    )
    n = len(closes)
    rebalances = [r for r in range(WARMUP_ROWS, n - 1, REBALANCE)]
    periods: List[Tuple[int, int]] = [
        (r, min(r + REBALANCE, n - 1)) for r in rebalances
    ]
    mid = len(periods) // 2

    returns: Dict[str, List[float]] = {k: [] for k, _ in CELLS}
    turnovers: Dict[str, List[float]] = {k: [] for k, _ in CELLS}
    holdings: Dict[str, List[str]] = {k: [] for k, _ in CELLS}

    def members_for(key: str, r: int) -> List[str]:
        if key == "ew":
            return eligible_at(closes, first_real, r, LOOKBACKS["m12"])
        lb_key, top = key.split("_top")
        score = momentum_scores(closes, first_real, r, LOOKBACKS[lb_key])
        return list(score.sort_values(ascending=False).head(int(top)).index)

    for r, r_next in periods:
        for key, _ in CELLS:
            new = members_for(key, r)
            cost = turnover_cost_pct(holdings[key], new, slippage_pct)
            if holdings[key] or new:  # don't log turnover before first buy-in
                turnovers[key].append(cost / (2.0 * slippage_pct) if holdings[key] else 1.0)
            returns[key].append(period_return_pct(closes, new, r, r_next) - cost)
            holdings[key] = new

    def compounded(rets: List[float]) -> float:
        return float(100.0 * (np.prod([1 + x / 100.0 for x in rets]) - 1.0))

    cells = []
    for key, label in CELLS:
        tr, va = returns[key][:mid], returns[key][mid:]
        cells.append({
            "key": key, "label": label,
            "train": stats(tr), "valid": stats(va),
            "train_total": round(compounded(tr), 1),
            "valid_total": round(compounded(va), 1),
            "avg_turnover": round(float(np.mean(turnovers[key])), 2) if turnovers[key] else 0.0,
        })
    return {"cells": cells, "n_periods": len(periods), "mid": mid}


def _passes(row: Dict, ew_row: Dict) -> bool:
    t, v = row["train"], row["valid"]
    et, ev = ew_row["train"], ew_row["valid"]
    if any(s is None for s in (t, v, et, ev)):
        return False
    return (
        t["n"] >= MIN_PERIODS
        and v["n"] >= MIN_PERIODS
        and t["avg"] >= et["avg"] + MARGIN_PCT
        and v["avg"] >= ev["avg"] + MARGIN_PCT
    )


def render_markdown(result: Dict, as_of: date) -> str:
    def side(s):
        if s is None:
            return "0 | -- | -- | --"
        pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
        return f"{s['n']} | {s['win_rate']}% | {s['avg']:+.3f}% | {pf}"

    rows = result["cells"]
    ew_row = next(r for r in rows if r["key"] == "ew")
    lines = [
        f"# Momentum portfolio split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result.get('symbols_used', []))} compliant symbols, "
        f"{result.get('years', '?')}y of bars, union calendar. Monthly "
        f"({REBALANCE}-day) rebalance; score = past-L return ending one month "
        "ago (short-term reversal month skipped); equal weight, buy-and-hold "
        f"inside the month; {SLIPPAGE_PCT}% slippage per side on turnover. "
        "NOT the live 6-day product -- a pass would mean a separate sleeve.",
        "",
        f"Pre-registered rule: adopt only if a cell beats the EW baseline by >= "
        f"{MARGIN_PCT}%/month on BOTH halves with >= {MIN_PERIODS} months per "
        f"half. Lookbacks ({', '.join(str(v) for v in LOOKBACKS.values())}d) and "
        "top-N fixed in advance.",
        "",
        "| Portfolio | Train n | Train win | Train avg | Train PF "
        "| Valid n | Valid win | Valid avg | Valid PF | Train total | Valid total | Turnover |",
        "|" + "---|" * 12,
    ]
    for r in rows:
        label = f"**{r['label']}**" if r["key"] == "ew" else r["label"]
        lines.append(
            f"| {label} | {side(r['train'])} | {side(r['valid'])} "
            f"| {r['train_total']:+.1f}% | {r['valid_total']:+.1f}% | {r['avg_turnover']:.2f} |"
        )

    adopted = [r for r in rows if r["key"] != "ew" and _passes(r, ew_row)]
    lines += ["", "## Verdict", ""]
    if adopted:
        for r in adopted:
            lines.append(f"- **{r['label']}** passes the pre-registered rule on both halves.")
        lines.append("")
        lines.append(
            "Next step: re-read the rule above and confirm the margin on both halves "
            "before building anything (no cherry-picking a half or a lookback). A pass "
            "here is a case for a separate monthly sleeve, not a change to the daily screen."
        )
    else:
        lines.append(
            "**No momentum cell passes the pre-registered rule. Holding the halal "
            "universe equally would have done as well or better after costs.**"
        )
    lines += [
        "",
        "Caveats: 4 cells = 4 comparisons (the two-half rule is the guard); today's "
        "compliant universe applied to the past (survivorship); union-calendar "
        "forward-fill can smear multi-listing gaps; monthly periods overlap market "
        "regimes, so halves are not independent draws.",
        "", "---", "_Generated by halal_trader.tune_momentum._", "",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of a monthly momentum portfolio")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=YEARS_DEFAULT)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-momentum.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
