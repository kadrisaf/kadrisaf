"""Split-sample test: a market-regime gate on top of the live entries.

    python -m halal_trader.tune_regime --universe data/universe_default.csv

Asked by the user on 2026-11-12 ("run the regime gate study") after a week
whose tape (post-shock whipsaws everywhere, zero qualifiers) was visibly a
regime problem. The live system already self-throttles -- volume and shock
gates produce empty reports in broken tapes -- so the question is whether an
EXPLICIT market-level gate improves the per-trade outcomes of the entries
that do fire.

Regimes are computed from SPY, used purely as a signal (never traded, so
no Shariah question arises). All values use data through the entry day
only:

  trend : SPY close above its own 200-day SMA
  calm  : SPY 20-day realized volatility (annualized std of daily returns)
          at or below its trailing 252-day 70th-percentile
  both  : trend AND calm

Variants (live entry gate and live exits throughout; the regime only
selects which live entries are taken): LIVE baseline, trend-only, calm-
only, both. Non-SPY trading days (European listings) use the most recent
prior SPY day's regime.

Pre-registered decision rule (written before the data was looked at):
adopt a gate only if it beats LIVE's average return per trade by at least
0.15%/trade on BOTH halves, with >= 150 train trades and validation n >=
half of LIVE's. Regime gates concentrate trades in time, so correlated-
sample caveats bite harder here than usual; a marginal pass would still
shadow before any config change. 'No change' is a good result.
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

TREND_SMA = 200
VOL_WINDOW = 20
VOL_LOOKBACK = 252
VOL_PERCENTILE = 0.70

CELLS = [
    ("live", "LIVE (no regime gate)"),
    ("trend", f"SPY above {TREND_SMA}d SMA"),
    ("calm", f"SPY {VOL_WINDOW}d vol <= trailing {VOL_PERCENTILE:.0%} pct"),
    ("both", "Trend AND calm"),
]


def regime_frame(spy: pd.DataFrame) -> pd.DataFrame:
    """Per-SPY-day regime flags, each computed from data through that day."""
    close = spy["Close"]
    trend = close > close.rolling(TREND_SMA).mean()
    vol = close.pct_change().rolling(VOL_WINDOW).std() * np.sqrt(252)
    calm = vol <= vol.rolling(VOL_LOOKBACK).quantile(VOL_PERCENTILE)
    out = pd.DataFrame({"trend": trend, "calm": calm})
    out.index = [ts.date() for ts in spy.index]
    return out


class RegimeLookup:
    """Regime flags for any date: the most recent SPY day at or before it.
    Dates before the first usable SPY day return None (entry not covered)."""

    def __init__(self, frame: pd.DataFrame):
        # drop the warmup rows where the SMA/percentile are undefined
        usable = frame.dropna()
        self.dates = list(usable.index)
        self.trend = usable["trend"].to_numpy(dtype=bool)
        self.calm = usable["calm"].to_numpy(dtype=bool)

    def flags(self, d: date) -> Optional[Dict[str, bool]]:
        i = int(np.searchsorted(np.array(self.dates), d, side="right")) - 1
        if i < 0:
            return None
        return {"trend": bool(self.trend[i]), "calm": bool(self.calm[i])}


def run_test(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    # +1y for the symbols' warmup, +1y more so SPY's 200d SMA and 252d vol
    # percentile are defined from the first usable symbol bar
    spy = provider.get_price_history("SPY", period=f"{years + 2}y")
    lookup = RegimeLookup(regime_frame(spy))

    keys = [k for k, _ in CELLS]
    train: Dict[str, List[float]] = {k: [] for k in keys}
    valid: Dict[str, List[float]] = {k: [] for k in keys}
    used: List[str] = []
    uncovered = 0

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[regime] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = gate_features(df)
        mid = (len(df) + WARMUP) // 2
        used.append(symbol)
        daily_dates = [ts.date() for ts in df.index]
        cell_entries: Dict[str, List[int]] = {k: [] for k in keys}
        for i in entry_mask(feats, LIVE_GATE).to_numpy().nonzero()[0]:
            flags = lookup.flags(daily_dates[i])
            if flags is None:
                uncovered += 1
                continue
            cell_entries["live"].append(i)
            if flags["trend"]:
                cell_entries["trend"].append(i)
            if flags["calm"]:
                cell_entries["calm"].append(i)
            if flags["trend"] and flags["calm"]:
                cell_entries["both"].append(i)
        for k in keys:
            train[k] += walk_trades(df, feats, cell_entries[k], WARMUP, mid, slippage_pct)
            valid[k] += walk_trades(df, feats, cell_entries[k], mid, len(df), slippage_pct)
        print(f"[regime] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    return {
        "cells": [
            {"key": k, "label": lbl, "train": stats(train[k]), "valid": stats(valid[k])}
            for k, lbl in CELLS
        ],
        "symbols_used": used,
        "uncovered_entries": uncovered,
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
        f"# Market-regime gate split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Regimes from SPY (signal only, never traded), computed through each entry day: "
        f"trend = close above its {TREND_SMA}d SMA; calm = {VOL_WINDOW}d realized vol at "
        f"or below its trailing {VOL_LOOKBACK}d {VOL_PERCENTILE:.0%} percentile. Live "
        "entries and live exits throughout; the regime only selects which entries are "
        "taken. First half of each symbol's history = train, second = validation. "
        f"{result['uncovered_entries']} entries predated SPY regime coverage and were "
        "dropped from every cell alike.",
        "",
        f"Pre-registered rule: adopt a gate only if it beats LIVE by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_TRAIN_TRADES} train "
        f"trades and validation n >= {MIN_VALID_SHARE:g}x LIVE's.",
        "",
        "| Regime gate | Train n | Train win | Train avg | Train PF "
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
        lines.append("Next step: shadow the gate in the daily report (log what it would "
                     "have excluded) before any config change -- regime gates concentrate "
                     "trades in time, so the split-sample understates their variance.")
    else:
        lines.append("**No regime gate passes the pre-registered rule. The system keeps "
                     "its implicit throttle (volume/shock gates emptying the report in "
                     "broken tapes) and adds no explicit market-level switch.**")
    lines += ["", "Caveats: regime gates concentrate trades in time, so the correlated-"
              "trades caveat bites hardest here; two halves contain few independent "
              "regime episodes (one bear phase can dominate a half); survivorship and "
              "cost caveats as in every study.",
              "", "---", "_Generated by halal_trader.tune_regime._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of a market-regime gate")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-regime.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
