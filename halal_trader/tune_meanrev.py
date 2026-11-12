"""Split-sample test: mean-reversion entries as a candidate entry family.

    python -m halal_trader.tune_meanrev --universe data/universe_default.csv

Asked by the user on 2026-11-12, from the strategy-catalogue triage (the
'mean reversion: RSI / Bollinger / Z-score' row). A Z-score of the close
against its 20-day mean IS the Bollinger band position measured in sigma
units, so the Bollinger cells cover the Z-score row too.

Variants (entry at the signal day's close, LIVE exits -- ATR stop, ATR
target, live time-stop):

  rsi2         : RSI(2) < 10 (Connors' classic oversold trigger)
  rsi2_trend   : rsi2 AND close > 200-day SMA (Connors' own regime filter)
  boll         : close < 20-day mean - 2 x 20-day std (lower band touch)
  boll_trend   : boll AND close > 200-day SMA

The live liquidity gate (min dollar volume) and the post-shock whipsaw
guard apply to every cell; trend/RSI(14) gates deliberately do not --
mean reversion buys weakness, the exact opposite population of the live
pullback-in-uptrend entries. Note the tension, stated in advance: the
whipsaw guard removes the deepest crash days from every cell alike, so
this tests mean reversion on ORDINARY weakness, not on panics.

Because the trend variants need a 200-day SMA, every cell (including the
LIVE reference) starts its train half at bar 200, not at the usual
60-bar warmup -- the halves are identical across cells, so the
comparison stays apples-to-apples (the LIVE row's absolute numbers will
differ slightly from other studies' reports; that is expected).

Pre-registered decision rule (written before the data was looked at): a
variant is adopted only if it beats the LIVE gate's average return per
trade by at least 0.15%/trade on BOTH halves, with at least 150 trades in
EACH half (absolute floor). One-half winners are multiple comparisons;
'no change' is a good result.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .signals import _rsi
from .tune import stats
from .tune_entrygates import ADOPT_MARGIN_PCT, LIVE_GATE, entry_mask, gate_features
from .tune_spring import walk_trades

MIN_HALF_TRADES = 150
FAMILY_WARMUP = 200  # the 200d SMA needs it; applied to EVERY cell alike
RSI2_MAX = 10.0
BOLL_WINDOW = 20
BOLL_SIGMA = 2.0

CELLS = [
    ("live", "LIVE trend gate (reference)"),
    ("rsi2", "RSI(2) < 10"),
    ("rsi2_trend", "RSI(2) < 10 + close > 200d SMA"),
    ("boll", "Close < lower Bollinger (20d, 2 sigma)"),
    ("boll_trend", "Lower Bollinger + close > 200d SMA"),
]

VARIANTS = {
    "rsi2": dict(kind="rsi2", need_trend=False),
    "rsi2_trend": dict(kind="rsi2", need_trend=True),
    "boll": dict(kind="boll", need_trend=False),
    "boll_trend": dict(kind="boll", need_trend=True),
}


def meanrev_entries(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    kind: str,
    need_trend: bool,
) -> List[int]:
    """Bar indices where the mean-reversion trigger fires, with the live
    liquidity + shock gates applied on the entry day."""
    p = config.DEFAULT_SIGNAL_PARAMS
    close = df["Close"]
    if kind == "rsi2":
        trigger = _rsi(close, 2) < RSI2_MAX
    elif kind == "boll":
        mid = close.rolling(BOLL_WINDOW).mean()
        sd = close.rolling(BOLL_WINDOW).std()
        trigger = close < (mid - BOLL_SIGMA * sd)
    else:
        raise ValueError(f"unknown kind {kind!r}")
    if need_trend:
        trigger &= close > close.rolling(FAMILY_WARMUP).mean()
    mask = (
        trigger
        & (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    )
    return list(mask.fillna(False).to_numpy().nonzero()[0])


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
            print(f"[meanrev] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * FAMILY_WARMUP + 40:
            continue
        feats = gate_features(df)
        mid = (len(df) + FAMILY_WARMUP) // 2
        used.append(symbol)
        live_entries = list(entry_mask(feats, LIVE_GATE).to_numpy().nonzero()[0])
        train["live"] += walk_trades(df, feats, live_entries, FAMILY_WARMUP, mid, slippage_pct)
        valid["live"] += walk_trades(df, feats, live_entries, mid, len(df), slippage_pct)
        for key, kw in VARIANTS.items():
            entries = meanrev_entries(df, feats, **kw)
            train[key] += walk_trades(df, feats, entries, FAMILY_WARMUP, mid, slippage_pct)
            valid[key] += walk_trades(df, feats, entries, mid, len(df), slippage_pct)
        print(f"[meanrev] {symbol}: done ({len(df)} bars)", file=sys.stderr)

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
        f"# Mean-reversion split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Entry = oversold trigger at that day's close (RSI(2) < 10, or close below the "
        "lower 20d/2-sigma Bollinger band -- the Z-score formulation is the same test), "
        "with and without a 200d-SMA regime filter; LIVE exits. Liquidity and post-shock "
        "gates apply to every cell; trend/RSI(14) gates do not (candidate entry family). "
        "Every cell starts at bar 200 so the 200d-SMA variants and the LIVE reference "
        "share identical halves. First half = train, second = validation.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats the LIVE gate by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_HALF_TRADES} trades in "
        "each half. Triggers and thresholds fixed in advance.",
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
        lines.append("Next step: shadow-log mean-reversion signals in the daily report "
                     "before any config change, as pre-committed for new entry families.")
    else:
        lines.append("**No mean-reversion variant passes the pre-registered rule. No "
                     "mean-reversion entry family is added.**")
    lines += ["", "Caveats: correlated trades (oversold days cluster market-wide), "
              "survivorship in the universe, costs beyond exit slippage not modelled; "
              "the post-shock whipsaw guard removes the deepest crash days from every "
              "cell, so this measures ordinary-weakness reversion, not panic reversion; "
              "LIVE exits (ATR stop/target, short time-stop) were designed for pullback "
              "continuation, not reversion -- a dedicated exit (e.g. exit at the 20d "
              "mean) is a different, untested system.",
              "", "---", "_Generated by halal_trader.tune_meanrev._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of mean-reversion entries")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-meanrev.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
