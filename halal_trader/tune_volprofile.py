"""Split-sample test: fixed-range volume profile as an entry context gate.

    python -m halal_trader.tune_volprofile --universe data/universe_default.csv

Asked by the user on 2026-11-12. A volume profile needs volume attributed
to PRICE levels, which daily bars cannot give. This is the tier-1, zero-
cost version: Yahoo's HOURLY bars (~730 days, consolidated volume), each
bar's volume assigned to the price bucket of its typical price (H+L+C)/3,
profile built over the trailing 60 trading days before each entry. Honest
limits, stated up front: ~2 years of history means a ~1y/1y split (weaker
than the 3y daily studies), and hourly granularity is coarser than the
minute data a paid feed would give. If -- and only if -- a variant passes
here, the next step is confirming on minute bars (Polygon free tier)
before any rule changes.

Variants (live entry gate throughout, live exits; the profile only decides
WHICH live entries are taken):

  above_poc   : entry close above the profile's point of control (the
                price with the most traded volume) -- "trading above value"
  below_poc   : entry close at/below the POC -- the complement
  hvn_support : a high-volume node directly below the entry (the heaviest
                bucket within one ATR below the close carries >= 1.5x the
                profile's median bucket volume) -- "thick support underneath"

Pre-registered decision rule (written before the data was looked at): a
variant is adopted only if it beats the LIVE-in-window baseline's average
return per trade by at least 0.15%/trade on BOTH halves, with at least
150 trades in each half. One-half winners are multiple comparisons, not
discoveries; 'no change' is a good result.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .tune import WARMUP, stats
from .tune_entrygates import ADOPT_MARGIN_PCT, LIVE_GATE, entry_mask, gate_features
from .tune_spring import walk_trades

MIN_HALF_TRADES = 150
PROFILE_DAYS = 60     # trailing trading days in each fixed range
BUCKETS = 50          # price buckets per profile
HVN_RATIO = 1.5       # a node is "high volume" at >= this multiple of the median bucket

CELLS = [
    ("live", "LIVE in hourly-coverage window (reference)"),
    ("above_poc", "Entry close above POC ('above value')"),
    ("below_poc", "Entry close at/below POC"),
    ("hvn_support", "High-volume node within 1 ATR below entry"),
]


def build_profile(hourly: pd.DataFrame, buckets: int = BUCKETS) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """(bucket_edges, bucket_volumes) from hourly bars: each bar's volume
    lands in the bucket of its typical price (H+L+C)/3. None if unusable."""
    if hourly is None or len(hourly) < 30:
        return None
    typ = (hourly["High"] + hourly["Low"] + hourly["Close"]) / 3.0
    vol = hourly["Volume"].to_numpy(dtype=float)
    lo, hi = float(typ.min()), float(typ.max())
    if not np.isfinite([lo, hi]).all() or hi <= lo or vol.sum() <= 0:
        return None
    edges = np.linspace(lo, hi, buckets + 1)
    idx = np.clip(np.digitize(typ.to_numpy(dtype=float), edges) - 1, 0, buckets - 1)
    volumes = np.zeros(buckets)
    np.add.at(volumes, idx, vol)
    return edges, volumes


def poc_price(profile: Tuple[np.ndarray, np.ndarray]) -> float:
    edges, volumes = profile
    i = int(np.argmax(volumes))
    return float((edges[i] + edges[i + 1]) / 2.0)


def has_hvn_support(profile: Tuple[np.ndarray, np.ndarray], close: float, atr: float,
                    ratio: float = HVN_RATIO) -> bool:
    """Is the heaviest bucket within [close - atr, close) at least `ratio`
    times the profile's MEDIAN bucket volume? (A percentile comparison
    degenerates on flat profiles -- every bucket ties the percentile -- so
    the node must stand out from the typical bucket, not merely match it.)"""
    edges, volumes = profile
    centers = (edges[:-1] + edges[1:]) / 2.0
    below = volumes[(centers >= close - atr) & (centers < close)]
    if len(below) == 0:
        return False
    med = float(np.median(volumes))
    return med > 0 and float(below.max()) >= ratio * med


def classify_entry(profile: Tuple[np.ndarray, np.ndarray], close: float, atr: float) -> Dict[str, bool]:
    poc = poc_price(profile)
    return {
        "above_poc": close > poc,
        "below_poc": close <= poc,
        "hvn_support": has_hvn_support(profile, close, atr),
    }


def fetch_hourly(symbol: str, yf=None) -> Optional[pd.DataFrame]:
    """~730 days of 1h bars (the provider interface is daily-only). None on
    any failure -- the symbol is then skipped, never silently daily-ized."""
    try:
        if yf is None:
            import yfinance as yf  # type: ignore
        h = yf.Ticker(symbol).history(period="730d", interval="1h", auto_adjust=True)
        if h is None or h.empty:
            return None
        return h[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as exc:
        print(f"[volprofile] {symbol}: hourly fetch failed: {exc}", file=sys.stderr)
        return None


def hourly_by_date(hourly: pd.DataFrame) -> Dict[date, pd.DataFrame]:
    if hourly is None or hourly.empty:
        return {}
    return {d: g for d, g in hourly.groupby(hourly.index.date)}


def run_test(
    universe: List[str],
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    keys = [k for k, _ in CELLS]
    train: Dict[str, List[float]] = {k: [] for k in keys}
    valid: Dict[str, List[float]] = {k: [] for k in keys}
    used: List[str] = []
    covered_total = 0

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period="3y")
        except DataUnavailable as exc:
            print(f"[volprofile] skip {symbol}: {exc}", file=sys.stderr)
            continue
        hourly = fetch_hourly(symbol)
        if len(df) < 2 * WARMUP + 40 or hourly is None or len(hourly) < 500:
            continue
        feats = gate_features(df)
        by_date = hourly_by_date(hourly)
        hourly_dates = sorted(by_date)
        if len(hourly_dates) < PROFILE_DAYS + 60:
            continue
        date_pos = {d: n for n, d in enumerate(hourly_dates)}
        daily_dates = [ts.date() for ts in df.index]

        live_entries = list(entry_mask(feats, LIVE_GATE).to_numpy().nonzero()[0])
        covered: List[Tuple[int, Dict[str, bool]]] = []
        for i in live_entries:
            d = daily_dates[i]
            pos = date_pos.get(d)
            if pos is None or pos < PROFILE_DAYS:
                continue  # entry predates hourly coverage (or no profile depth yet)
            window_days = hourly_dates[pos - PROFILE_DAYS : pos + 1]
            window = pd.concat([by_date[wd] for wd in window_days])
            profile = build_profile(window)
            if profile is None:
                continue
            close, atr = float(feats["close"].iloc[i]), float(feats["atr"].iloc[i])
            covered.append((i, classify_entry(profile, close, atr)))
        if not covered:
            continue
        used.append(symbol)
        covered_total += len(covered)

        # split by date midpoint of the hourly coverage, same for every cell
        mid_date = hourly_dates[(PROFILE_DAYS + len(hourly_dates)) // 2]
        first_valid_bar = next(
            (n for n, d in enumerate(daily_dates) if d >= mid_date), len(df)
        )
        cell_entries: Dict[str, List[int]] = {k: [] for k in keys}
        for i, flags in covered:
            cell_entries["live"].append(i)
            for k in ("above_poc", "below_poc", "hvn_support"):
                if flags[k]:
                    cell_entries[k].append(i)
        for k in keys:
            train[k] += walk_trades(df, feats, cell_entries[k], 0, first_valid_bar, slippage_pct)
            valid[k] += walk_trades(df, feats, cell_entries[k], first_valid_bar, len(df), slippage_pct)
        print(f"[volprofile] {symbol}: {len(covered)} covered entries", file=sys.stderr)

    return {
        "cells": [
            {"key": k, "label": lbl, "train": stats(train[k]), "valid": stats(valid[k])}
            for k, lbl in CELLS
        ],
        "symbols_used": used,
        "covered_entries": covered_total,
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
        f"# Fixed-range volume profile split-sample test (tier 1: hourly bars) -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['covered_entries']} "
        f"live-gate entries covered by hourly data. Profile = trailing {PROFILE_DAYS} "
        f"trading days of Yahoo 1h bars in {BUCKETS} price buckets (volume at typical "
        "price). Live entries and live exits throughout; the profile only selects which "
        "entries are taken. ~2y of hourly history = a ~1y/1y split: WEAKER than the 3y "
        "daily studies, said plainly.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats the in-window LIVE "
        f"baseline by >= {ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= "
        f"{MIN_HALF_TRADES} trades in each half. A pass here earns a minute-bar "
        "confirmation (Polygon free tier) before any rule changes, never a direct adoption.",
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
        lines.append("Next step per the docstring: confirm on minute bars (Polygon free "
                     "tier) before touching config.")
    else:
        lines.append("**No volume-profile subset passes the pre-registered rule. "
                     "Nothing changes, and the paid-data question stays closed.**")
    lines += ["", "Caveats: hourly granularity smears volume within each hour; ~1y halves "
              "are short and regime-dependent; above/below POC partitions the same trades, "
              "so at most one side can look good by construction; correlated trades and "
              "survivorship as in every study.",
              "", "---", "_Generated by halal_trader.tune_volprofile._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Tier-1 volume-profile study (hourly bars)")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-volprofile.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
