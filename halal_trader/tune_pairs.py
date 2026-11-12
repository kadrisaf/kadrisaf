"""Split-sample test: long-only pairs divergence as a candidate entry family.

    python -m halal_trader.tune_pairs --universe data/universe_default.csv

Asked by the user on 2026-11-12, from the strategy-catalogue triage (the
'pairs trading / statistical arbitrage' row). Stated honestly up front:
real stat-arb is LONG the cheap name and SHORT the rich one, hedging out
the market. Shorting is not halal and not available on Trade Republic,
so this tests the only implementable half: when a stock becomes
unusually cheap against its most-correlated peer, buy the laggard. That
is NOT market-neutral arbitrage -- it is 'buy the sector laggard', and
the result speaks only for that.

Mechanics (all causal):
  * Every 21 trading days, each symbol's peer = the other compliant
    symbol with the highest Pearson correlation of daily log returns
    over the TRAILING 120 days (pairwise, >= 100 overlapping days).
  * Spread = log(close_S) - log(close_peer) on a shared calendar
    (forward-filled across holiday gaps); z = spread vs its trailing
    60-day mean/std.
  * Entry when z crosses DOWN through -threshold (first day only) on a
    day the symbol actually traded, with the live liquidity + shock
    gates; LIVE exits (a pairs-native convergence exit is a different,
    untested system -- stated in the report).

Variants fixed in advance:

  z15        : z <= -1.5, peer correlation >= 0.6
  z20        : z <= -2.0, peer correlation >= 0.6
  z25        : z <= -2.5, peer correlation >= 0.6
  z20_strong : z <= -2.0, peer correlation >= 0.8

Pre-registered decision rule (written before the data was looked at): a
variant is adopted only if it beats the LIVE gate's average return per
trade by at least 0.15%/trade on BOTH halves, with at least 150 trades
in EACH half. One-half winners are multiple comparisons; 'no change' is
a good result. Prior, stated in advance: the shared-tape autopsy showed
this universe's qualifying moves are often sector-wide, i.e. a laggard
vs its peer is usually lagging FOR a reason that is also hitting the
peer's sector -- and the mean-reversion study already found buying
ordinary weakness adds nothing here.
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
REBALANCE = 21          # trading days between peer re-selections
CORR_WINDOW = 120       # trailing days used to pick the peer
CORR_MIN_OBS = 100      # minimum overlapping days inside that window
SPREAD_WINDOW = 60      # trailing days for the spread z-score

CELLS = [
    ("live", "LIVE trend gate (reference)"),
    ("z15", "Pairs: z <= -1.5, corr >= 0.6"),
    ("z20", "Pairs: z <= -2.0, corr >= 0.6"),
    ("z25", "Pairs: z <= -2.5, corr >= 0.6"),
    ("z20_strong", "Pairs: z <= -2.0, corr >= 0.8"),
]

VARIANTS = {
    "z15": dict(z_thr=1.5, corr_floor=0.6),
    "z20": dict(z_thr=2.0, corr_floor=0.6),
    "z25": dict(z_thr=2.5, corr_floor=0.6),
    "z20_strong": dict(z_thr=2.0, corr_floor=0.8),
}


def naive_dates(df: pd.DataFrame) -> pd.DatetimeIndex:
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def build_close_matrix(histories: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Union-calendar close matrix (tz-naive dates), forward-filled."""
    cols = {}
    for sym, df in histories.items():
        s = df["Close"].copy()
        s.index = naive_dates(df)
        cols[sym] = s[~s.index.duplicated(keep="last")]
    return pd.DataFrame(cols).sort_index().ffill()


def peer_schedule(
    closes: pd.DataFrame,
    rebalance: int = REBALANCE,
    corr_window: int = CORR_WINDOW,
    min_obs: int = CORR_MIN_OBS,
) -> Dict[str, List[Tuple[int, int, str, float]]]:
    """For each symbol, (start_row, end_row, peer, corr) segments; the
    window [r - corr_window, r) strictly precedes the segment [r, r+reb)."""
    rets = np.log(closes).diff()
    n = len(closes)
    sched: Dict[str, List[Tuple[int, int, str, float]]] = {s: [] for s in closes.columns}
    for r in range(corr_window, n, rebalance):
        corr = rets.iloc[r - corr_window : r].corr(min_periods=min_obs)
        end = min(r + rebalance, n)
        for s in closes.columns:
            row = corr[s].drop(index=s).dropna()
            if row.empty:
                continue
            peer = row.idxmax()
            sched[s].append((r, end, peer, float(row[peer])))
    return sched


def pairs_entries(
    symbol: str,
    df: pd.DataFrame,
    feats: pd.DataFrame,
    closes: pd.DataFrame,
    sched: Dict[str, List[Tuple[int, int, str, float]]],
    z_thr: float,
    corr_floor: float,
    _z_cache: Optional[Dict[Tuple[str, str], pd.Series]] = None,
) -> List[int]:
    """Bar indices (in the symbol's own calendar) where the spread z-score
    crosses down through -z_thr against the scheduled peer."""
    p = config.DEFAULT_SIGNAL_PARAMS
    gate_ok = (
        (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    ).fillna(False).to_numpy()
    own_dates = naive_dates(df)
    pos_of = {d: i for i, d in enumerate(own_dates)}
    logc = np.log(closes)
    cache = _z_cache if _z_cache is not None else {}

    entries: List[int] = []
    for start, end, peer, corr in sched.get(symbol, []):
        if corr < corr_floor:
            continue
        key = (symbol, peer)
        if key not in cache:
            spread = logc[symbol] - logc[peer]
            mu = spread.rolling(SPREAD_WINDOW).mean()
            sd = spread.rolling(SPREAD_WINDOW).std()
            cache[key] = (spread - mu) / sd.replace(0, np.nan)
        z = cache[key]
        seg = z.iloc[max(start, 1) : end]
        prev = z.shift(1).iloc[max(start, 1) : end]
        crossed = seg.index[(seg <= -z_thr) & (prev > -z_thr)]
        for d in crossed:
            i = pos_of.get(d)
            if i is not None and gate_ok[i]:
                entries.append(i)
    return sorted(set(entries))


def run_test(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    histories: Dict[str, pd.DataFrame] = {}
    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[pairs] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        histories[symbol] = df

    closes = build_close_matrix(histories)
    print(f"[pairs] peer schedule over {len(closes)} days x {len(closes.columns)} symbols",
          file=sys.stderr)
    sched = peer_schedule(closes)

    keys = [k for k, _ in CELLS]
    train: Dict[str, List[float]] = {k: [] for k in keys}
    valid: Dict[str, List[float]] = {k: [] for k in keys}
    z_cache: Dict[Tuple[str, str], pd.Series] = {}

    for symbol, df in histories.items():
        feats = gate_features(df)
        mid = (len(df) + WARMUP) // 2
        live_entries = list(entry_mask(feats, LIVE_GATE).to_numpy().nonzero()[0])
        train["live"] += walk_trades(df, feats, live_entries, WARMUP, mid, slippage_pct)
        valid["live"] += walk_trades(df, feats, live_entries, mid, len(df), slippage_pct)
        for key, kw in VARIANTS.items():
            entries = pairs_entries(symbol, df, feats, closes, sched,
                                    _z_cache=z_cache, **kw)
            train[key] += walk_trades(df, feats, entries, WARMUP, mid, slippage_pct)
            valid[key] += walk_trades(df, feats, entries, mid, len(df), slippage_pct)
        print(f"[pairs] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    return {
        "cells": [
            {"key": k, "label": lbl, "train": stats(train[k]), "valid": stats(valid[k])}
            for k, lbl in CELLS
        ],
        "symbols_used": sorted(histories),
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
        f"# Long-only pairs divergence split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Peer = most return-correlated compliant symbol over the trailing 120 days, "
        "re-selected every 21 days (causal); entry when the log-price spread's z-score "
        "(trailing 60d) crosses below the threshold -- the symbol just became unusually "
        "cheap vs its peer; long leg only (shorting is not halal and not available on "
        "Trade Republic, so this is 'buy the sector laggard', NOT market-neutral "
        "stat-arb); LIVE exits. Liquidity and post-shock gates apply; trend/RSI gates "
        "do not (candidate entry family). First half of each symbol's history = train, "
        "second = validation.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats the LIVE gate by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_HALF_TRADES} trades in "
        "each half. Thresholds (1.5, 2.0, 2.5) and correlation floors (0.6, 0.8) fixed "
        "in advance.",
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
        lines.append("Next step: shadow-log pairs signals in the daily report before "
                     "any config change, as pre-committed for new entry families.")
    else:
        lines.append("**No pairs variant passes the pre-registered rule. No pairs "
                     "entry family is added.**")
    lines += ["", "Caveats: without the short leg this carries full market beta -- it "
              "is not arbitrage; LIVE exits, not a convergence exit (a z->0 exit is a "
              "different, untested system); holiday gaps are forward-filled, which "
              "slightly dampens cross-calendar correlations; correlated trades, "
              "survivorship, and cost caveats as in every study.",
              "", "---", "_Generated by halal_trader.tune_pairs._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of long-only pairs entries")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-pairs.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
