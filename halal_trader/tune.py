"""Split-sample parameter test.

    python -m halal_trader.tune --universe data/universe_default.csv --years 3

Tunes the signal parameters (RSI band, relative-volume threshold, stop/target
ATR multiples, hold length) on the FIRST half of each symbol's history, then
evaluates the in-sample winners -- and the live defaults -- on the SECOND
half, which the tuning never saw.

Decision rule printed with the output: adopt a parameter change only if it
beats the live defaults out-of-sample by a clear margin AND with a comparable
trade count. In-sample winners that fade out-of-sample are the expected
outcome (that's multiple comparisons doing its thing), and 'no change' is a
perfectly good result.

Features are computed once per symbol with the same formulas as signals.py
(RSI/ATR/SMAs/relative volume), then each parameter combination is applied as
a vectorized mask, so the grid stays cheap. Exits reuse
track_record.simulate_exit (stop first, gap fills at the open, slippage).
"""

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .signals import _atr, _rsi
from .track_record import simulate_exit

WARMUP = 60  # bars before the first usable day (needs the 50d SMA + buffers)


@dataclass(frozen=True)
class ParamSet:
    rsi_min: float
    rsi_max: float
    min_rel_vol: float
    stop_mult: float
    target_mult: float
    hold_days: int

    def label(self) -> str:
        return (
            f"RSI {self.rsi_min:g}-{self.rsi_max:g}, vol>={self.min_rel_vol:g}x, "
            f"{self.stop_mult:g}/{self.target_mult:g} ATR, {self.hold_days}d"
        )


LIVE = ParamSet(
    rsi_min=config.DEFAULT_SIGNAL_PARAMS.rsi_min,
    rsi_max=config.DEFAULT_SIGNAL_PARAMS.rsi_max,
    min_rel_vol=config.DEFAULT_SIGNAL_PARAMS.min_relative_volume,
    stop_mult=config.DEFAULT_SIGNAL_PARAMS.stop_atr_multiple,
    target_mult=config.DEFAULT_SIGNAL_PARAMS.target_atr_multiple,
    hold_days=config.DEFAULT_SIGNAL_PARAMS.max_holding_days,
)

GRID: List[ParamSet] = [
    ParamSet(rmin, rmax, rv, sm, tm, hd)
    for (rmin, rmax) in [(45.0, 65.0), (40.0, 70.0), (50.0, 70.0)]
    for rv in [1.0, 1.2, 1.5]
    for (sm, tm) in [(1.5, 2.25), (2.0, 3.0), (1.5, 3.0)]
    for hd in [3, 5, 8]
]


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Per-day signal inputs, same formulas as signals.evaluate_signal."""
    p = config.DEFAULT_SIGNAL_PARAMS
    close, volume = df["Close"], df["Volume"]
    sma_fast = close.rolling(p.fast_sma).mean()
    sma_slow = close.rolling(p.slow_sma).mean()
    avg_vol = volume.rolling(20).mean()
    return pd.DataFrame(
        {
            "close": close,
            "rsi": _rsi(close, p.rsi_period),
            "trend": (close > sma_fast) & (sma_fast > sma_slow) & (sma_fast > sma_fast.shift(1)),
            "rel_vol": volume / avg_vol,
            "dollar_vol": close.rolling(20).mean() * avg_vol,
            "atr": _atr(df, p.atr_period),
            "shock": close.pct_change().rolling(p.shock_lookback_days).min() * 100,
        },
        index=df.index,
    )


def trades_for(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    ps: ParamSet,
    lo: int,
    hi: int,
    slippage_pct: float,
) -> List[float]:
    """Non-overlapping trades (entry bar in [lo, hi)), returns in %."""
    p = config.DEFAULT_SIGNAL_PARAMS
    mask = (
        feats["trend"]
        & feats["rsi"].between(ps.rsi_min, ps.rsi_max)
        & (feats["rel_vol"] >= ps.min_rel_vol)
        & (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    )
    out: List[float] = []
    busy_until = -1
    for i in mask.to_numpy().nonzero()[0]:
        if i < lo or i >= hi or i <= busy_until or i + 1 >= len(df):
            continue
        close, atr = feats["close"].iloc[i], feats["atr"].iloc[i]
        sub = df.iloc[i + 1 : i + 1 + ps.hold_days]
        result = simulate_exit(
            sub, close - ps.stop_mult * atr, close + ps.target_mult * atr, ps.hold_days, slippage_pct
        )
        if result is None:
            continue
        exit_day, price, _reason = result
        k = next(n for n, ts in enumerate(sub.index) if ts.date() == exit_day)
        busy_until = i + 1 + k
        out.append((price - close) / close * 100)
    return out


def stats(returns: List[float]) -> Optional[Dict]:
    if not returns:
        return None
    r = pd.Series(returns)
    wins, losses = r[r > 0], r[r <= 0]
    gross_loss = abs(losses.sum())
    return {
        "n": len(r),
        "win_rate": round(100 * len(wins) / len(r), 1),
        "avg": round(r.mean(), 3),
        "profit_factor": round(wins.sum() / gross_loss, 2) if gross_loss else None,
    }


def run_tune(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    grid: Optional[List[ParamSet]] = None,
    top_k: int = 5,
    min_train_trades: int = 150,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
    max_symbols: Optional[int] = None,
) -> Dict:
    provider = provider or YFinanceProvider()
    grid = grid if grid is not None else GRID
    cells = list(dict.fromkeys([LIVE] + grid))  # live params always evaluated
    train: Dict[ParamSet, List[float]] = {c: [] for c in cells}
    valid: Dict[ParamSet, List[float]] = {c: [] for c in cells}
    used: List[str] = []

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[tune] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = feature_frame(df)
        mid = (len(df) + WARMUP) // 2  # halves get equal usable bars
        used.append(symbol)
        for c in cells:
            train[c] += trades_for(df, feats, c, WARMUP, mid, slippage_pct)
            valid[c] += trades_for(df, feats, c, mid, len(df), slippage_pct)
        print(f"[tune] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    rows = []
    for c in cells:
        ts = stats(train[c])
        if c != LIVE and (ts is None or ts["n"] < min_train_trades):
            continue
        rows.append({"params": c, "train": ts, "valid": stats(valid[c])})
    winners = sorted(
        [r for r in rows if r["params"] != LIVE and r["train"]],
        key=lambda r: r["train"]["avg"],
        reverse=True,
    )[:top_k]
    live_row = next(r for r in rows if r["params"] == LIVE)
    return {"live": live_row, "winners": winners, "symbols_used": used, "years": years,
            "grid_size": len(cells) - 1, "min_train_trades": min_train_trades}


def render_markdown(result: Dict, as_of: date) -> str:
    def row(label, r):
        def side(s):
            if s is None:
                return "0 | -- | -- | --"
            pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
            return f"{s['n']} | {s['win_rate']}% | {s['avg']:+.3f}% | {pf}"
        return f"| {label} | {side(r['train'])} | {side(r['valid'])} |"

    head = ("| Parameters | Train n | Train win | Train avg | Train PF "
            "| Valid n | Valid win | Valid avg | Valid PF |")
    sep = "|" + "---|" * 9
    live = result["live"]
    lines = [
        f"# Split-sample parameter test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        f"Grid of {result['grid_size']} parameter sets tuned on the FIRST half of history; "
        f"the top 5 by train avg (min {result['min_train_trades']} train trades) are then "
        f"shown against the SECOND half, which tuning never saw. Live defaults always shown.",
        "",
        head, sep,
        row(f"LIVE: {live['params'].label()}", live),
    ]
    for r in result["winners"]:
        lines.append(row(r["params"].label(), r))
    lines += [
        "",
        "## How to read this",
        "",
        "- **Adopt nothing unless** a winner beats LIVE on the validation half by a clear "
        "margin (not a few hundredths of a %) with a comparable trade count.",
        "- In-sample winners fading out-of-sample is the EXPECTED outcome under multiple "
        "comparisons; 'no change' is a good result, not a failure.",
        "- All caveats of the main backtest apply (look-ahead compliance, survivorship, "
        "correlated trades, costs beyond exit slippage not modelled).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample tuning of the signal parameters")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default="backtests/tune.md")
    args = ap.parse_args(argv)
    result = run_tune(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md)
    print(md)
    print(f"Tune report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
