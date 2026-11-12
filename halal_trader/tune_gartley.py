"""Split-sample test: bullish Gartley (XABCD) patterns as an entry family.

    python -m halal_trader.tune_gartley --universe data/universe_default.csv

Asked by the user on 2026-11-12 ("check HM GARTLEY" -> "run the gartley
study"). H.M. Gartley's 1935 'pattern 222': an up-leg X->A, a pullback to
B near a 0.618 retracement, a bounce to C, and a deeper drop to D near a
0.786 retracement of X->A, where you buy expecting the uptrend to resume.
(The Fibonacci ratios are the Pesavento/Carney formalisation; Gartley's
book gave none -- stated for honesty.)

Everything here is CAUSAL: swing pivots come from a ZigZag that confirms
a swing high only after price has dropped the threshold % from it (and
vice versa), so at any bar the pattern uses only information available
that day. Entry = the close of the first bar whose LOW touches the D
level (A - 0.786 x XA) after C is confirmed, provided the bar neither
breaks below X (pattern invalidated) nor came after a new high above A
(trend resumed without D). LIVE exits throughout.

Variants fixed in advance (2 ZigZag thresholds x 2 B-ratio tolerances):

  g5_strict : 5% ZigZag, |B retrace - 0.618| <= 0.06
  g5_loose  : 5% ZigZag, |B retrace - 0.618| <= 0.12
  g8_strict : 8% ZigZag, |B retrace - 0.618| <= 0.06
  g8_loose  : 8% ZigZag, |B retrace - 0.618| <= 0.12

C must retrace 0.382..0.886 of A->B in every cell (the textbook band).
The live liquidity gate and the post-shock whipsaw guard apply on the
entry day; trend/RSI gates do not (candidate entry family).

Pre-registered decision rule (written before the data was looked at): a
variant is adopted only if it beats the LIVE gate's average return per
trade by at least 0.15%/trade on BOTH halves, with at least 150 trades
in EACH half (absolute floor -- harmonic patterns are rare, and
'insufficient trades' is itself an honest verdict). Stated prior, also
in advance: this is the most parameter-laden family in the catalogue and
nothing in it has peer-reviewed support; the prior is the lowest of the
sweep.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .tune import WARMUP, stats
from .tune_entrygates import ADOPT_MARGIN_PCT, LIVE_GATE, entry_mask, gate_features
from .tune_spring import walk_trades

MIN_HALF_TRADES = 150
B_RATIO = 0.618
D_RATIO = 0.786
C_BAND = (0.382, 0.886)

CELLS = [
    ("live", "LIVE trend gate (reference)"),
    ("g5_strict", "Gartley 5% ZigZag, B tol 0.06"),
    ("g5_loose", "Gartley 5% ZigZag, B tol 0.12"),
    ("g8_strict", "Gartley 8% ZigZag, B tol 0.06"),
    ("g8_loose", "Gartley 8% ZigZag, B tol 0.12"),
]

VARIANTS = {
    "g5_strict": dict(threshold=0.05, b_tol=0.06),
    "g5_loose": dict(threshold=0.05, b_tol=0.12),
    "g8_strict": dict(threshold=0.08, b_tol=0.06),
    "g8_loose": dict(threshold=0.08, b_tol=0.12),
}


@dataclass
class Pivot:
    idx: int          # bar of the swing extreme
    price: float
    kind: str         # "H" or "L"
    confirmed_at: int  # first bar at which the swing was knowable


def zigzag_pivots(df: pd.DataFrame, threshold: float) -> List[Pivot]:
    """Causal ZigZag: a swing high is confirmed only once a later LOW has
    dropped `threshold` below it (swing lows symmetrically). confirmed_at
    is always strictly after idx."""
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    n = len(df)
    pivots: List[Pivot] = []
    direction = 0  # +1 tracking a candidate high, -1 a candidate low, 0 unknown
    hi_i, hi = 0, highs[0]
    lo_i, lo = 0, lows[0]
    for t in range(1, n):
        if direction >= 0:
            if highs[t] > hi:
                hi_i, hi = t, highs[t]
            if lows[t] <= hi * (1 - threshold):
                pivots.append(Pivot(hi_i, hi, "H", t))
                direction = -1
                lo_i, lo = t, lows[t]
                continue
        if direction <= 0:
            if lows[t] < lo:
                lo_i, lo = t, lows[t]
            if highs[t] >= lo * (1 + threshold):
                pivots.append(Pivot(lo_i, lo, "L", t))
                direction = 1
                hi_i, hi = t, highs[t]
    return pivots


def gartley_entries(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    threshold: float,
    b_tol: float,
) -> List[int]:
    """Bar indices where a bullish Gartley completes at D, with the live
    liquidity + shock gates applied on the entry day."""
    p = config.DEFAULT_SIGNAL_PARAMS
    lows = df["Low"].to_numpy()
    highs = df["High"].to_numpy()
    n = len(df)
    gate_ok = (
        (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    ).fillna(False).to_numpy()

    pivots = zigzag_pivots(df, threshold)
    entries: List[int] = []
    for i in range(len(pivots) - 3):
        X, A, B, C = pivots[i : i + 4]
        if (X.kind, A.kind, B.kind, C.kind) != ("L", "H", "L", "H"):
            continue
        xa = A.price - X.price
        if xa <= 0:
            continue
        b_retr = (A.price - B.price) / xa
        if abs(b_retr - B_RATIO) > b_tol:
            continue
        ab = A.price - B.price
        if ab <= 0:
            continue
        c_retr = (C.price - B.price) / ab
        if not (C_BAND[0] <= c_retr <= C_BAND[1]):
            continue
        d_level = A.price - D_RATIO * xa
        if d_level <= X.price:
            continue
        for t in range(C.confirmed_at, n):
            if lows[t] < X.price:
                break  # D broke below X: pattern invalidated
            if highs[t] > A.price:
                break  # new high above A: trend resumed without D
            if lows[t] <= d_level:
                if gate_ok[t]:
                    entries.append(t)
                break  # first touch only, entered or not
    return sorted(set(entries))


def run_test(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    keys = [k for k, _ in CELLS]
    train: Dict[str, List[float]] = {k: [] for k in keys}
    valid: Dict[str, List[float]] = {k: [] for k in keys}
    used: List[str] = []

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[gartley] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = gate_features(df)
        mid = (len(df) + WARMUP) // 2
        used.append(symbol)
        live_entries = list(entry_mask(feats, LIVE_GATE).to_numpy().nonzero()[0])
        train["live"] += walk_trades(df, feats, live_entries, WARMUP, mid, slippage_pct)
        valid["live"] += walk_trades(df, feats, live_entries, mid, len(df), slippage_pct)
        for key, kw in VARIANTS.items():
            entries = gartley_entries(df, feats, **kw)
            train[key] += walk_trades(df, feats, entries, WARMUP, mid, slippage_pct)
            valid[key] += walk_trades(df, feats, entries, mid, len(df), slippage_pct)
        print(f"[gartley] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    return {
        "cells": [
            {"key": k, "label": lbl, "train": stats(train[k]), "valid": stats(valid[k])}
            for k, lbl in CELLS
        ],
        "symbols_used": used,
        "years": years,
    }


def _passes(row: Dict, live_row: Dict) -> bool:
    t, v = row["train"], row["valid"]
    lt, lv = live_row["train"], live_row["valid"]
    if any(s is None for s in (t, v, lt, lv)):
        return False
    return (
        t["n"] >= MIN_HALF_TRADES
        and v["n"] >= MIN_HALF_TRADES
        and t["avg"] >= lt["avg"] + ADOPT_MARGIN_PCT
        and v["avg"] >= lv["avg"] + ADOPT_MARGIN_PCT
    )


def render_markdown(result: Dict, as_of: date) -> str:
    def side(s):
        if s is None:
            return "0 | -- | -- | --"
        pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
        return f"{s['n']} | {s['win_rate']}% | {s['avg']:+.3f}% | {pf}"

    rows = result["cells"]
    live_row = next(r for r in rows if r["key"] == "live")
    lines = [
        f"# Gartley (bullish XABCD) split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Causal ZigZag pivots (a swing is only used after the reversal that confirms "
        "it); B near a 0.618 retrace of X->A, C in the 0.382-0.886 band, entry at the "
        "close of the first bar touching D = A - 0.786 x XA, skipped if price breaks X "
        "or makes a new high first; LIVE exits. Liquidity and post-shock gates apply; "
        "trend/RSI gates do not (candidate entry family). First half of each symbol's "
        "history = train, second = validation.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats the LIVE gate by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_HALF_TRADES} trades in "
        "each half. ZigZag thresholds (5%, 8%) and B tolerances (0.06, 0.12) fixed in "
        "advance; 'insufficient trades' is itself an honest verdict for a rare pattern.",
        "",
        "| Entry family | Train n | Train win | Train avg | Train PF "
        "| Valid n | Valid win | Valid avg | Valid PF |",
        "|" + "---|" * 9,
    ]
    for r in rows:
        label = f"**{r['label']}**" if r["key"] == "live" else r["label"]
        lines.append(f"| {label} | {side(r['train'])} | {side(r['valid'])} |")

    adopted = [r for r in rows if r["key"] != "live" and _passes(r, live_row)]
    lines += ["", "## Verdict", ""]
    if adopted:
        for r in adopted:
            lines.append(f"- **{r['label']}** passes the pre-registered rule on both halves.")
        lines.append("")
        lines.append("Next step: shadow-log Gartley signals in the daily report before "
                     "any config change, as pre-committed for new entry families.")
    else:
        lines.append("**No Gartley variant passes the pre-registered rule. No harmonic "
                     "entry family is added.**")
    lines += ["", "Caveats: pivot geometry depends on the ZigZag threshold (two were "
              "fixed in advance; infinitely many were not tested); correlated trades, "
              "survivorship, and cost caveats as in every study; daily bars only -- "
              "intraday D-touches fill differently; the Fibonacci ratios tested are "
              "the modern convention, not Gartley's own (his book specified none).",
              "", "---", "_Generated by halal_trader.tune_gartley._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of bullish Gartley entries")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-gartley.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
