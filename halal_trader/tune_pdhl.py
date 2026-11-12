"""Split-sample test: previous-day-high confirmation / previous-day-low stop.

    python -m halal_trader.tune_pdhl --universe data/universe_default.csv

Asked by the user on 2026-11-12 ("what about previous day high and low?").
Two mechanical, daily-bar versions of the classic PDH/PDL playbook, tested
against the live rules with everything else pinned:

  entry confirmation (PDH): take a live-gate entry only when the entry
      day's close is above the PREVIOUS day's high -- "the move already
      cleared yesterday's range". A subset of live entries.
  stop placement (PDL): replace the live ATR stop (close - 1.5*ATR) with
      the previous day's low -- the structural stop traders put "under
      yesterday's low". Target and time-stop stay live.

Four cells: LIVE, PDH gate, PDL stop, PDH gate + PDL stop. Entries where
the PDL stop would sit at or above the entry close are skipped in the PDL
cells (no defined risk), and counted.

Pre-registered decision rule (written before the data was looked at), the
same one every gate faces: adopt a variant only if it beats LIVE's average
return per trade by at least 0.15%/trade on BOTH halves (train and
validation), with >= 150 train trades and validation n >= half of LIVE's.
A one-half winner is multiple comparisons, not a discovery; 'no change'
is a good result.
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
from .track_record import simulate_exit
from .tune import WARMUP, stats
from .tune_entrygates import (
    ADOPT_MARGIN_PCT,
    MIN_TRAIN_TRADES,
    MIN_VALID_SHARE,
    LIVE_GATE,
    entry_mask,
    gate_features,
)

CELLS = [
    ("live", "LIVE: ATR stop, no PDH confirmation"),
    ("pdh", "PDH confirmation (close > prev day high), ATR stop"),
    ("pdl", "PDL stop (prev day low), no PDH confirmation"),
    ("pdh_pdl", "PDH confirmation + PDL stop"),
]


def pdhl_trades(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    mask: pd.Series,
    lo: int,
    hi: int,
    slippage_pct: float,
    require_pdh: bool,
    pdl_stop: bool,
) -> Dict:
    """Non-overlapping trades (entry bar in [lo, hi)); live exits except the
    requested PDH/PDL twist. Returns {'returns': [...], 'skipped_no_risk': n}."""
    p = config.DEFAULT_SIGNAL_PARAMS
    out: List[float] = []
    skipped = 0
    busy_until = -1
    for i in mask.to_numpy().nonzero()[0]:
        if i < lo or i >= hi or i <= busy_until or i + 1 >= len(df) or i == 0:
            continue
        close, atr = feats["close"].iloc[i], feats["atr"].iloc[i]
        if require_pdh and not close > float(df["High"].iloc[i - 1]):
            continue
        stop = float(df["Low"].iloc[i - 1]) if pdl_stop else close - p.stop_atr_multiple * atr
        if stop >= close:
            skipped += 1
            continue
        sub = df.iloc[i + 1 : i + 1 + p.max_holding_days]
        result = simulate_exit(
            sub, stop, close + p.target_atr_multiple * atr, p.max_holding_days, slippage_pct
        )
        if result is None:
            continue
        exit_day, price, _reason = result
        k = next(n for n, ts in enumerate(sub.index) if ts.date() == exit_day)
        busy_until = i + 1 + k
        out.append((price - close) / close * 100)
    return {"returns": out, "skipped_no_risk": skipped}


def run_test(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    variants = {
        "live": (False, False),
        "pdh": (True, False),
        "pdl": (False, True),
        "pdh_pdl": (True, True),
    }
    train: Dict[str, List[float]] = {k: [] for k in variants}
    valid: Dict[str, List[float]] = {k: [] for k in variants}
    skipped: Dict[str, int] = {k: 0 for k in variants}
    used: List[str] = []

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[pdhl] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = gate_features(df)
        mask = entry_mask(feats, LIVE_GATE)
        mid = (len(df) + WARMUP) // 2
        used.append(symbol)
        for key, (pdh, pdl) in variants.items():
            a = pdhl_trades(df, feats, mask, WARMUP, mid, slippage_pct, pdh, pdl)
            b = pdhl_trades(df, feats, mask, mid, len(df), slippage_pct, pdh, pdl)
            train[key] += a["returns"]
            valid[key] += b["returns"]
            skipped[key] += a["skipped_no_risk"] + b["skipped_no_risk"]
        print(f"[pdhl] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    return {
        "cells": [
            {"key": k, "label": lbl, "train": stats(train[k]), "valid": stats(valid[k]),
             "skipped_no_risk": skipped[k]}
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
        f"# Previous-day high/low split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Live entry gate throughout; the variants add a previous-day-high entry "
        "confirmation and/or swap the ATR stop for the previous day's low. "
        "First half of each symbol's history = train, second half = validation.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats LIVE by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_TRAIN_TRADES} train "
        f"trades and validation n >= {MIN_VALID_SHARE:g}x LIVE's.",
        "",
        "| Variant | Train n | Train win | Train avg | Train PF "
        "| Valid n | Valid win | Valid avg | Valid PF |",
        "|" + "---|" * 9,
    ]
    for r in rows:
        label = f"**{r['label']}**" if r["key"] == "live" else r["label"]
        lines.append(f"| {label} | {side(r['train'])} | {side(r['valid'])} |")
    pdl_skipped = next(r for r in rows if r["key"] == "pdl")["skipped_no_risk"]
    lines += ["",
              f"PDL cells skipped {pdl_skipped} entries whose previous-day low sat at or "
              "above the entry close (no defined risk).", ""]

    adopted = [r for r in rows if r["key"] != "live" and _passes(r, live_row)]
    lines += ["## Verdict", ""]
    if adopted:
        for r in adopted:
            lines.append(f"- **{r['label']}** passes the pre-registered rule on both halves.")
        lines.append("")
        lines.append("Next step: re-read the rule above and confirm the margin holds on "
                     "both halves before touching config (no cherry-picking a half).")
    else:
        lines.append("**No variant passes the pre-registered rule. The live entry and "
                     "ATR stop stay.** A one-half winner is multiple comparisons at work, "
                     "not a discovery.")
    lines += ["", "Caveats: correlated trades (same days/sectors), survivorship in the "
              "universe, costs beyond exit slippage not modelled; PDH/PDL on daily bars "
              "is the honest approximation of an intraday technique -- intraday fills "
              "could differ.", "", "---", "_Generated by halal_trader.tune_pdhl._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of PDH confirmation / PDL stop")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-pdhl.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
