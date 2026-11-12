"""Split-sample test: ADX trend-strength gate on top of the live entries.

    python -m halal_trader.tune_adx --universe data/universe_default.csv

Asked by the user on 2026-11-12, the last open item from the strategy-
catalogue triage. The live gate checks trend DIRECTION (price > rising
20SMA > 50SMA); ADX measures trend STRENGTH regardless of direction. The
claim on trial: live entries taken while the stock's own ADX is high
(an established, directional move) outperform those taken in weak/choppy
tape that happens to satisfy the SMA stack.

ADX(14), Wilder's construction, computed per symbol through the entry day
only: +DM/-DM from day-over-day high/low changes, Wilder-smoothed over the
true range, DX = |DI+ - DI-| / (DI+ + DI-), ADX = Wilder-smoothed DX.

Variants (live entries and live exits throughout; the gate only selects
which entries are taken): LIVE baseline, ADX >= 20, ADX >= 25, ADX >= 30
-- the three textbook thresholds, fixed in advance.

Pre-registered decision rule (written before the data was looked at):
adopt a gate only if it beats LIVE's average return per trade by at least
0.15%/trade on BOTH halves, with >= 150 train trades and validation n >=
half of LIVE's. One-half winners are multiple comparisons, not
discoveries; 'no change' is a good result.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .tune import WARMUP, stats
from .tune_entrygates import (
    ADOPT_MARGIN_PCT,
    MIN_TRAIN_TRADES,
    MIN_VALID_SHARE,
    LIVE_GATE,
    entry_mask,
    gate_features,
)
from .tune_spring import walk_trades

from .signals import ADX_PERIOD, adx_series  # canonical implementation lives in signals

_ = ADX_PERIOD  # re-exported for tests and callers
THRESHOLDS = [20.0, 25.0, 30.0]

CELLS = [("live", "LIVE (no ADX gate)")] + [
    (f"adx{int(t)}", f"ADX({ADX_PERIOD}) >= {t:g}") for t in THRESHOLDS
]


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
            print(f"[adx] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = gate_features(df)
        adx = adx_series(df)
        mid = (len(df) + WARMUP) // 2
        used.append(symbol)
        entries = entry_mask(feats, LIVE_GATE).to_numpy().nonzero()[0]
        cell_entries: Dict[str, List[int]] = {k: [] for k in keys}
        for i in entries:
            a = adx.iloc[i]
            if not np.isfinite(a):
                continue  # dropped from every cell alike: same trades compared
            cell_entries["live"].append(i)
            for t in THRESHOLDS:
                if a >= t:
                    cell_entries[f"adx{int(t)}"].append(i)
        for k in keys:
            train[k] += walk_trades(df, feats, cell_entries[k], WARMUP, mid, slippage_pct)
            valid[k] += walk_trades(df, feats, cell_entries[k], mid, len(df), slippage_pct)
        print(f"[adx] {symbol}: done ({len(df)} bars)", file=sys.stderr)

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
        t["n"] >= MIN_TRAIN_TRADES
        and v["n"] >= MIN_VALID_SHARE * lv["n"]
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
        f"# ADX trend-strength gate split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        f"ADX({ADX_PERIOD}) per symbol (Wilder), computed through each entry day. Live "
        "entries and live exits throughout; the gate only selects which entries are "
        "taken; entries inside the ADX warmup are dropped from every cell alike. First "
        "half of each symbol's history = train, second = validation.",
        "",
        f"Pre-registered rule: adopt a gate only if it beats LIVE by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_TRAIN_TRADES} train "
        f"trades and validation n >= {MIN_VALID_SHARE:g}x LIVE's. Thresholds "
        f"({', '.join(f'{t:g}' for t in THRESHOLDS)}) fixed in advance.",
        "",
        "| Gate | Train n | Train win | Train avg | Train PF "
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
        lines.append("Next step: re-read the rule above and confirm the margin on both "
                     "halves before touching config (no cherry-picking a half or a threshold).")
    else:
        lines.append("**No ADX threshold passes the pre-registered rule. The live gate "
                     "already encodes the trend information that matters here; ADX adds "
                     "selection without adding edge.**")
    lines += ["", "Caveats: three thresholds = three comparisons (the two-half rule is "
              "the guard); correlated trades, survivorship, and cost caveats as in "
              "every study.",
              "", "---", "_Generated by halal_trader.tune_adx._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of an ADX trend-strength gate")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-adx.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
