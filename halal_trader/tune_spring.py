"""Split-sample test of the Wyckoff 'spring' -- the one testable fragment.

    python -m halal_trader.tune_spring --universe data/universe_default.csv

Asked by the user on 2026-11-12 ("what about Wyckoff?"). Wyckoff as a whole
is a narrative, not a rule; the spring is its one fragment that reduces to
a mechanical, daily-bar definition:

  support   : the lowest LOW of the prior `lookback` days (not counting today)
  undercut  : today's low trades BELOW that support
  spring    : the close reclaims the support level -- the shakeout failed

Variants (all entries at the reclaim day's close, LIVE exits -- ATR stop,
ATR target, live time-stop -- so only the entry logic is on trial):

  spring20       : 20-day support, undercut and reclaim the same day
  spring50       : 50-day support, same-day reclaim
  spring20_d2    : 20-day support; close fails to reclaim on the undercut
                   day but reclaims within 2 days -> enter on the reclaim
  spring20_quiet : spring20 where the undercut day's relative volume <= 1.0
                   (the classic 'no supply' spring)

This is a REVERSAL family -- the live system's trend/RSI/volume gates are
deliberately NOT applied (they would contradict the setup). The live
liquidity gate (min dollar volume) and the post-shock whipsaw guard DO
apply: whatever the entry logic, the user doesn't trade illiquid names or
fresh -8% crashes.

Pre-registered decision rule (written before the data was looked at): a
spring variant is adopted as a new entry family only if it beats the LIVE
gate's average return per trade by at least 0.15%/trade on BOTH halves
(train and validation), with at least 150 trades in EACH half. Springs are
rare, so the count floor is absolute, not a share of LIVE's. One-half
winners are multiple comparisons, not discoveries; 'no change' is a good
result. The falling-knife caveat from the pre-pop study applies in advance:
reversal entries live in the crash-rich zone the live rules exclude on
purpose, so a marginal edge would still need a long real-world shadow
before real money touched it.
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
from .tune_entrygates import ADOPT_MARGIN_PCT, LIVE_GATE, entry_mask, gate_features

MIN_HALF_TRADES = 150  # absolute floor per half for a rare-event family

CELLS = [
    ("live", "LIVE trend gate (reference)"),
    ("spring20", "Spring: 20d support, same-day reclaim"),
    ("spring50", "Spring: 50d support, same-day reclaim"),
    ("spring20_d2", "Spring: 20d support, reclaim within 2 days"),
    ("spring20_quiet", "Spring: 20d support, quiet undercut (rel vol <= 1)"),
]


def spring_entries(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    lookback: int = 20,
    reclaim_within: int = 0,
    quiet_undercut: bool = False,
) -> List[int]:
    """Bar indices of spring entries. Support = lowest low of the prior
    `lookback` days; an undercut day pierces it; entry is the first day
    (undercut day itself when reclaim_within=0, else up to that many days
    later) whose CLOSE is back above the support level frozen at the
    undercut. Liquidity + shock gates applied at the entry day."""
    p = config.DEFAULT_SIGNAL_PARAMS
    low, close = df["Low"], df["Close"]
    support = low.rolling(lookback).min().shift(1)
    ok = (
        (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    )
    out: List[int] = []
    sup = support.to_numpy()
    lo_v, cl_v = low.to_numpy(), close.to_numpy()
    rv = feats["rel_vol"].to_numpy()
    okv = ok.to_numpy()
    for i in range(len(df)):
        if not (sup[i] == sup[i]) or lo_v[i] >= sup[i]:  # NaN-safe: no undercut
            continue
        if quiet_undercut and not rv[i] <= 1.0:
            continue
        for j in range(i, min(i + reclaim_within, len(df) - 1) + 1):
            if cl_v[j] > sup[i]:
                if okv[j]:
                    out.append(j)
                break
            if j > i and lo_v[j] < sup[i] * 0.97:
                break  # kept falling well through support: not a spring
    return sorted(set(out))


def walk_trades(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    entries: List[int],
    lo: int,
    hi: int,
    slippage_pct: float,
) -> List[float]:
    """Non-overlapping trades at the given entry bars, LIVE exits."""
    p = config.DEFAULT_SIGNAL_PARAMS
    out: List[float] = []
    busy_until = -1
    for i in entries:
        if i < lo or i >= hi or i <= busy_until or i + 1 >= len(df):
            continue
        close, atr = feats["close"].iloc[i], feats["atr"].iloc[i]
        sub = df.iloc[i + 1 : i + 1 + p.max_holding_days]
        result = simulate_exit(
            sub,
            close - p.stop_atr_multiple * atr,
            close + p.target_atr_multiple * atr,
            p.max_holding_days,
            slippage_pct,
        )
        if result is None:
            continue
        exit_day, price, _reason = result
        k = next(n for n, ts in enumerate(sub.index) if ts.date() == exit_day)
        busy_until = i + 1 + k
        out.append((price - close) / close * 100)
    return out


VARIANTS = {
    "spring20": dict(lookback=20, reclaim_within=0, quiet_undercut=False),
    "spring50": dict(lookback=50, reclaim_within=0, quiet_undercut=False),
    "spring20_d2": dict(lookback=20, reclaim_within=2, quiet_undercut=False),
    "spring20_quiet": dict(lookback=20, reclaim_within=0, quiet_undercut=True),
}


def run_test(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    keys = ["live", *VARIANTS]
    train: Dict[str, List[float]] = {k: [] for k in keys}
    valid: Dict[str, List[float]] = {k: [] for k in keys}
    used: List[str] = []

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[spring] skip {symbol}: {exc}", file=sys.stderr)
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
            entries = spring_entries(df, feats, **kw)
            train[key] += walk_trades(df, feats, entries, WARMUP, mid, slippage_pct)
            valid[key] += walk_trades(df, feats, entries, mid, len(df), slippage_pct)
        print(f"[spring] {symbol}: done ({len(df)} bars)", file=sys.stderr)

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
        f"# Wyckoff spring split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Spring = low undercuts the prior N-day low, close reclaims it; entry at the "
        "reclaim close, LIVE exits (ATR stop/target, live time-stop). Liquidity and "
        "post-shock gates apply; trend/RSI/volume gates deliberately do not (reversal "
        "family). First half of each symbol's history = train, second = validation.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats the LIVE gate by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_HALF_TRADES} trades in "
        "each half. A marginal pass would still shadow-trade before real money.",
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
        lines.append("Next step per the docstring: shadow-log spring signals in the daily "
                     "report (no real money) and revisit once the shadow ledger has depth.")
    else:
        lines.append("**No spring variant passes the pre-registered rule. No reversal "
                     "entry family is added.** This matches the pre-pop finding: the "
                     "undercut-and-reclaim zone is where bounces AND continued crashes "
                     "live, and on this data the mix doesn't clear the trend gate's bar.")
    lines += ["", "Caveats: correlated trades (same days/sectors), survivorship in the "
              "universe, costs beyond exit slippage not modelled; daily bars can't see "
              "the intraday order flow Wyckoff readers claim matters -- this tests the "
              "mechanical fragment, not the narrative.",
              "", "---", "_Generated by halal_trader.tune_spring._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of Wyckoff spring entries")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-spring.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
