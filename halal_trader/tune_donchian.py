"""Split-sample test: Donchian channel breakouts as a candidate entry family.

    python -m halal_trader.tune_donchian --universe data/universe_default.csv

Asked by the user on 2026-11-12, from the strategy-catalogue triage. The
classic breakout entry: buy when the close clears the highest HIGH of the
prior N days. Tested as its own entry family (like the spring study), not
as a filter on live entries -- breakouts and SMA-stack pullback entries
are different populations.

Variants (entry at the breakout day's close, LIVE exits -- ATR stop, ATR
target, live time-stop):

  don20        : close > prior 20-day high
  don55        : close > prior 55-day high (the Turtle lookback)
  don20_vol    : don20 AND relative volume >= the live 1.2x confirmation
  don55_vol    : don55 AND the same volume confirmation

The live liquidity gate (min dollar volume) and the post-shock whipsaw
guard apply to every cell; trend/RSI gates deliberately do not. Prior
context, stated in advance: the PDH study showed 'cleared yesterday's
high' selects WORSE entries on this horizon, and the consolidation family
has failed twice -- this is the catalogue's last breakout variant, with a
correspondingly low prior.

Pre-registered decision rule (written before the data was looked at): a
variant is adopted only if it beats the LIVE gate's average return per
trade by at least 0.15%/trade on BOTH halves, with at least 150 trades in
EACH half (absolute floor -- breakouts are uncommon). One-half winners are
multiple comparisons; 'no change' is a good result.
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
from .tune import WARMUP, stats
from .tune_entrygates import ADOPT_MARGIN_PCT, LIVE_GATE, entry_mask, gate_features
from .tune_spring import walk_trades

MIN_HALF_TRADES = 150

CELLS = [
    ("live", "LIVE trend gate (reference)"),
    ("don20", "Donchian: close > prior 20d high"),
    ("don55", "Donchian: close > prior 55d high"),
    ("don20_vol", "Donchian 20d + rel vol >= 1.2x"),
    ("don55_vol", "Donchian 55d + rel vol >= 1.2x"),
]

VARIANTS = {
    "don20": dict(lookback=20, need_volume=False),
    "don55": dict(lookback=55, need_volume=False),
    "don20_vol": dict(lookback=20, need_volume=True),
    "don55_vol": dict(lookback=55, need_volume=True),
}


def donchian_entries(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    lookback: int,
    need_volume: bool,
) -> List[int]:
    """Bar indices where the close clears the highest high of the prior
    `lookback` days, with the live liquidity + shock gates (and optionally
    the live volume confirmation) applied on the entry day."""
    p = config.DEFAULT_SIGNAL_PARAMS
    channel_top = df["High"].rolling(lookback).max().shift(1)
    mask = (
        (df["Close"] > channel_top)
        & (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    )
    if need_volume:
        mask &= feats["rel_vol"] >= p.min_relative_volume
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
            print(f"[donchian] skip {symbol}: {exc}", file=sys.stderr)
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
            entries = donchian_entries(df, feats, **kw)
            train[key] += walk_trades(df, feats, entries, WARMUP, mid, slippage_pct)
            valid[key] += walk_trades(df, feats, entries, mid, len(df), slippage_pct)
        print(f"[donchian] {symbol}: done ({len(df)} bars)", file=sys.stderr)

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
        f"# Donchian breakout split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Entry = close above the highest high of the prior N days; entry at that close, "
        "LIVE exits. Liquidity and post-shock gates apply; trend/RSI gates do not "
        "(candidate entry family, not a filter). First half of each symbol's history = "
        "train, second = validation.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats the LIVE gate by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_HALF_TRADES} trades in "
        "each half. Lookbacks (20, 55) fixed in advance.",
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
        lines.append("Next step: shadow-log breakout signals in the daily report before "
                     "any config change, as pre-committed for new entry families.")
    else:
        lines.append("**No Donchian variant passes the pre-registered rule. No breakout "
                     "entry family is added** -- consistent with the PDH finding that "
                     "buying strength confirmation selects worse entries on this horizon.")
    lines += ["", "Caveats: correlated trades (same days/sectors), survivorship in the "
              "universe, costs beyond exit slippage not modelled; daily closes only -- "
              "an intraday breakout trader would fill earlier at a different price.",
              "", "---", "_Generated by halal_trader.tune_donchian._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of Donchian breakout entries")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-donchian.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
