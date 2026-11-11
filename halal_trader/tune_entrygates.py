"""Split-sample test of the ENTRY GATES: trend definition x RSI top edge.

    python -m halal_trader.tune_entrygates --universe data/universe_default.csv

Asked by the user on 2026-11-11 after DHI (entered at RSI 64.01, the very
top of the allowed band, late in a month-long sector run) hit its time-stop
at -5%: "no need to have 50 days climbing to buy" (loosen the trend gate so
entries come earlier in a move) and "we should fix" buying the hottest edge
of the band. Both are testable; neither gets adopted on a feeling.

tune.py varies RSI band / volume / stops / hold but keeps the TREND
definition fixed. This module varies the trend gate itself, crossed with
the RSI top edge, everything else pinned to the live parameters:

  trend variants
    live    : close > 20SMA, 20SMA > 50SMA, 20SMA rising   (the current rule)
    no50    : close > 20SMA, 20SMA rising                   (drops the 50-day
              condition -- qualifies trends several weeks earlier)
    above20 : close > 20SMA                                 (loosest: any day
              above the short average counts)
  rsi top edge
    65 (live) and 60 (would have excluded DHI's 64.01 entry)

plus a band-edge diagnostic inside the LIVE gate: the same trades split by
whether entry-day RSI landed in [45,60] or (60,65] -- is the hot edge of the
band actually worse?

Pre-registered decision rule (written before the data was looked at):
adopt a variant only if it beats LIVE's average return per trade by at
least 0.15%/trade on BOTH halves (train and validation), with >= 150 train
trades and a validation trade count >= half of LIVE's. A variant that wins
one half and loses the other is multiple comparisons at work, not a
discovery (the vol_creep gate died exactly that death on 2026-11-09).
'No change' is a good result. The band-edge diagnostic alone never changes
a rule; if it looks bad AND the rsi<=60 variant passes the rule above, the
band tightens.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .signals import _atr, _rsi
from .track_record import simulate_exit
from .tune import WARMUP, stats

MIN_TRAIN_TRADES = 150
ADOPT_MARGIN_PCT = 0.15  # must beat LIVE by this on BOTH halves
MIN_VALID_SHARE = 0.5    # validation n must be >= this share of LIVE's


@dataclass(frozen=True)
class Gate:
    key: str
    trend: str       # "live" | "no50" | "above20"
    rsi_max: float

    def label(self) -> str:
        trends = {
            "live": "price>20SMA>50SMA rising",
            "no50": "price>20SMA rising (no 50SMA)",
            "above20": "price>20SMA only",
        }
        return f"{trends[self.trend]}, RSI 45-{self.rsi_max:g}"


LIVE_GATE = Gate("live", "live", config.DEFAULT_SIGNAL_PARAMS.rsi_max)

GATES: List[Gate] = [
    Gate("live", "live", 65.0),
    Gate("live_rsi60", "live", 60.0),
    Gate("no50", "no50", 65.0),
    Gate("no50_rsi60", "no50", 60.0),
    Gate("above20", "above20", 65.0),
    Gate("above20_rsi60", "above20", 60.0),
]


def gate_features(df: pd.DataFrame) -> pd.DataFrame:
    """Same formulas as signals.evaluate_signal / tune.feature_frame, plus
    the SMA components so the trend variants can be recombined."""
    p = config.DEFAULT_SIGNAL_PARAMS
    close, volume = df["Close"], df["Volume"]
    sma_fast = close.rolling(p.fast_sma).mean()
    sma_slow = close.rolling(p.slow_sma).mean()
    avg_vol = volume.rolling(20).mean()
    return pd.DataFrame(
        {
            "close": close,
            "rsi": _rsi(close, p.rsi_period),
            "above_fast": close > sma_fast,
            "fast_over_slow": sma_fast > sma_slow,
            "fast_rising": sma_fast > sma_fast.shift(1),
            "rel_vol": volume / avg_vol,
            "dollar_vol": close.rolling(20).mean() * avg_vol,
            "atr": _atr(df, p.atr_period),
            "shock": close.pct_change().rolling(p.shock_lookback_days).min() * 100,
        },
        index=df.index,
    )


def entry_mask(feats: pd.DataFrame, gate: Gate,
               rsi_min_override: Optional[float] = None,
               rsi_max_override: Optional[float] = None) -> pd.Series:
    p = config.DEFAULT_SIGNAL_PARAMS
    trend = feats["above_fast"].copy()
    if gate.trend in ("live", "no50"):
        trend &= feats["fast_rising"]
    if gate.trend == "live":
        trend &= feats["fast_over_slow"]
    rsi_min = rsi_min_override if rsi_min_override is not None else p.rsi_min
    rsi_max = rsi_max_override if rsi_max_override is not None else gate.rsi_max
    return (
        trend
        & feats["rsi"].between(rsi_min, rsi_max)
        & (feats["rel_vol"] >= p.min_relative_volume)
        & (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    )


def trades_from_mask(df: pd.DataFrame, feats: pd.DataFrame, mask: pd.Series,
                     lo: int, hi: int, slippage_pct: float) -> List[float]:
    """Non-overlapping trades (entry bar in [lo, hi)), live exits, returns %.
    Same walk as tune.trades_for, parameterized by an arbitrary entry mask."""
    p = config.DEFAULT_SIGNAL_PARAMS
    out: List[float] = []
    busy_until = -1
    for i in mask.to_numpy().nonzero()[0]:
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


def run_test(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    max_symbols: Optional[int] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
) -> Dict:
    provider = provider or YFinanceProvider()
    cells = {g.key: g for g in GATES}
    edges = {"edge_45_60": (45.0, 60.0), "edge_60_65": (60.0, 65.0)}
    train: Dict[str, List[float]] = {k: [] for k in [*cells, *edges]}
    valid: Dict[str, List[float]] = {k: [] for k in [*cells, *edges]}
    used: List[str] = []

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[entrygates] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = gate_features(df)
        mid = (len(df) + WARMUP) // 2
        used.append(symbol)
        for key, gate in cells.items():
            m = entry_mask(feats, gate)
            train[key] += trades_from_mask(df, feats, m, WARMUP, mid, slippage_pct)
            valid[key] += trades_from_mask(df, feats, m, mid, len(df), slippage_pct)
        for key, (lo_r, hi_r) in edges.items():
            m = entry_mask(feats, LIVE_GATE, rsi_min_override=lo_r, rsi_max_override=hi_r)
            train[key] += trades_from_mask(df, feats, m, WARMUP, mid, slippage_pct)
            valid[key] += trades_from_mask(df, feats, m, mid, len(df), slippage_pct)
        print(f"[entrygates] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    return {
        "gates": [
            {"gate": g, "train": stats(train[g.key]), "valid": stats(valid[g.key])}
            for g in GATES
        ],
        "edges": {k: {"train": stats(train[k]), "valid": stats(valid[k])} for k in edges},
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

    rows = result["gates"]
    live_row = next(r for r in rows if r["gate"].key == "live")
    head = ("| Entry gate | Train n | Train win | Train avg | Train PF "
            "| Valid n | Valid win | Valid avg | Valid PF |")
    lines = [
        f"# Entry-gate split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "Trend definition x RSI top edge, everything else pinned to the live "
        "parameters; exits are the live stop/target/time-stop with slippage. "
        "First half of each symbol's history = train, second half = validation.",
        "",
        f"Pre-registered rule: adopt a variant only if it beats LIVE by >= "
        f"{ADOPT_MARGIN_PCT}%/trade on BOTH halves, with >= {MIN_TRAIN_TRADES} train "
        f"trades and validation n >= {MIN_VALID_SHARE:g}x LIVE's.",
        "",
        head, "|" + "---|" * 9,
    ]
    for r in rows:
        label = ("**LIVE: " + r["gate"].label() + "**") if r["gate"].key == "live" else r["gate"].label()
        lines.append(f"| {label} | {side(r['train'])} | {side(r['valid'])} |")

    lines += ["", "## Band-edge diagnostic (inside the LIVE gate)", "",
              "Same live trend/volume gates, entries split by entry-day RSI. "
              "Diagnostic only -- never changes a rule by itself.", "",
              head.replace("Entry gate", "RSI at entry"), "|" + "---|" * 9]
    for key, label in [("edge_45_60", "RSI 45-60"), ("edge_60_65", "RSI 60-65 (the DHI zone)")]:
        e = result["edges"][key]
        lines.append(f"| {label} | {side(e['train'])} | {side(e['valid'])} |")

    adopted = [r for r in rows if r["gate"].key != "live" and _passes(r, live_row)]
    lines += ["", "## Verdict", ""]
    if adopted:
        for r in adopted:
            lines.append(f"- **{r['gate'].label()}** passes the pre-registered rule on both halves.")
        lines.append("")
        lines.append("Next step: change config only after re-reading the rule above and "
                     "confirming the margin holds on both halves (no cherry-picking a half).")
    else:
        lines.append("**No variant passes the pre-registered rule. The live gate stays.** "
                     "A variant that wins one half but not the other is the expected "
                     "multiple-comparisons outcome, not a discovery.")
    lines += ["", "Caveats: correlated trades (same days/sectors), survivorship in the "
              "universe, costs beyond exit slippage not modelled; the band-edge split "
              "has small n at the hot edge -- read it as a direction, not a number.",
              "", "---", "_Generated by halal_trader.tune_entrygates._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of the entry gates")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-entrygates.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
