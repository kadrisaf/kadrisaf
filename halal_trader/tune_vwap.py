"""Split-sample test: rolling-VWAP position as a filter on live entries.

    python -m halal_trader.tune_vwap --universe data/universe_default.csv

Asked by the user on 2026-11-12, from the strategy-catalogue triage (the
'VWAP strategies' row). Stated honestly up front: classic VWAP trading is
INTRADAY (price vs the session's volume-weighted average); we trade daily
closes on a ~6-day horizon, and the tier-1 volume-profile study already
showed hourly-resolution position information adds nothing at this
horizon. The tradable daily analogue is the rolling (anchored) VWAP:
is the entry close above or below the volume-weighted average price of
the last N days? Folklore says 'above VWAP = buyers in control, only buy
above' -- this tests that claim as a filter on the live entries.

Cells: each live entry is classified by its close vs the N-day rolling
VWAP (typical price (H+L+C)/3, volume-weighted), N in {5, 20}, fixed in
advance. Above/below pairs partition the live entries exactly; LIVE
exits throughout.

Pre-registered decision rule (same as every filter study, written before
the data was looked at): a cell is adopted only if it beats the LIVE
gate's average return per trade by at least 0.15%/trade on BOTH halves,
with at least 150 train trades and a validation count at least half of
LIVE's (a filter that throws away most trades must clear a higher bar).
One-half winners are multiple comparisons; 'no change' is a good result.
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
from .tune_entrygates import LIVE_GATE, _passes, entry_mask, gate_features
from .tune_entrygates import ADOPT_MARGIN_PCT  # noqa: F401  (re-exported for tests)
from .tune_spring import walk_trades

WINDOWS = (5, 20)

CELLS = [
    ("live", "LIVE trend gate (reference)"),
    ("v5_above", "Close > 5d rolling VWAP"),
    ("v5_below", "Close <= 5d rolling VWAP"),
    ("v20_above", "Close > 20d rolling VWAP"),
    ("v20_below", "Close <= 20d rolling VWAP"),
]


def rolling_vwap(df: pd.DataFrame, window: int) -> pd.Series:
    """N-day rolling VWAP of the typical price (H+L+C)/3."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    pv = (tp * df["Volume"]).rolling(window).sum()
    v = df["Volume"].rolling(window).sum()
    return pv / v.replace(0, pd.NA)


def split_entries(df: pd.DataFrame, feats: pd.DataFrame) -> Dict[str, List[int]]:
    """Live entries partitioned by close vs each rolling VWAP."""
    live_mask = entry_mask(feats, LIVE_GATE)
    out: Dict[str, List[int]] = {"live": list(live_mask.to_numpy().nonzero()[0])}
    close = df["Close"]
    for w in WINDOWS:
        vwap = rolling_vwap(df, w)
        above = live_mask & vwap.notna() & (close > vwap)
        below = live_mask & vwap.notna() & (close <= vwap)
        out[f"v{w}_above"] = list(above.to_numpy().nonzero()[0])
        out[f"v{w}_below"] = list(below.to_numpy().nonzero()[0])
    return out


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
            print(f"[vwap] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = gate_features(df)
        mid = (len(df) + WARMUP) // 2
        used.append(symbol)
        for key, entries in split_entries(df, feats).items():
            train[key] += walk_trades(df, feats, entries, WARMUP, mid, slippage_pct)
            valid[key] += walk_trades(df, feats, entries, mid, len(df), slippage_pct)
        print(f"[vwap] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    return {
        "cells": [
            {"key": k, "label": lbl, "train": stats(train[k]), "valid": stats(valid[k])}
            for k, lbl in CELLS
        ],
        "symbols_used": used,
        "years": years,
    }


def render_markdown(result: Dict, as_of: date) -> str:
    def side(s):
        if s is None:
            return "0 | -- | -- | --"
        pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
        return f"{s['n']} | {s['win_rate']}% | {s['avg']:+.3f}% | {pf}"

    rows = result["cells"]
    live_row = next(r for r in rows if r["key"] == "live")
    lines = [
        f"# Rolling-VWAP filter split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Each live entry is classified by its close vs the 5d / 20d rolling VWAP "
        "(typical price (H+L+C)/3, volume-weighted); above/below pairs partition the "
        "live entries exactly; LIVE exits. Classic VWAP trading is intraday -- this is "
        "the tradable daily analogue (anchored/rolling VWAP), stated in advance.",
        "",
        "Pre-registered rule: adopt a cell only if it beats the LIVE gate by >= "
        "0.15%/trade on BOTH halves, with >= 150 train trades and validation n >= "
        "0.5x LIVE's. Windows (5, 20) fixed in advance.",
        "",
        "| Entry subset | Train n | Train win | Train avg | Train PF "
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
        lines.append("Next step: shadow-log the VWAP flag in the daily report before "
                     "any config change.")
    else:
        lines.append("**No VWAP cell passes the pre-registered rule. No VWAP filter "
                     "is added to the live gate.**")
    lines += ["", "Caveats: correlated trades, survivorship, and cost caveats as in "
              "every study; daily bars only -- true session VWAP needs intraday data, "
              "and the tier-1 hourly volume-profile study already found no outcome "
              "information in intraday price position at this horizon.",
              "", "---", "_Generated by halal_trader.tune_vwap._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of rolling-VWAP filters")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-vwap.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
