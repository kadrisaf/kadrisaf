"""Split-sample test: does a vol_creep gate improve the LIVE entry rules?

    python -m halal_trader.tune_volcreep

Follow-up to backtests/2026-11-09-prepop.md, where vol_creep (mean volume
of the last 5 days vs the 20 days before them) was the one pop-precursor
compatible with this system that survived both halves -- with a modest
1.3-1.4x lift on a ~3% base rate. That measured "pops become likelier";
this asks the only question that matters for adoption: do the trades the
LIVE rules actually take get BETTER when a vol_creep floor is added on
top, everything else unchanged?

Methodology is tune.py's, unchanged: first half of each symbol's history
is the training half, second half is validation; non-overlapping trades;
exits via track_record.simulate_exit with slippage. The grid here is
deliberately tiny (a handful of thresholds on one feature) to keep the
multiple-comparisons tax low. Decision rule: adopt only if a threshold
beats the LIVE baseline on the validation half by a clear margin with a
comparable trade count; "no change" is a good result.
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
from .tune import WARMUP, feature_frame, stats

# None = LIVE baseline (no vol_creep gate). Thresholds are "recent 5d avg
# volume >= thr x the prior 20d avg volume" on the entry day.
THRESHOLDS: List[Optional[float]] = [None, 1.0, 1.1, 1.2, 1.3, 1.5]


def vol_creep_series(df: pd.DataFrame) -> pd.Series:
    """Same formula as prepop_study.compute_features: recent 5d mean volume
    over the mean of the 20 days BEFORE those 5."""
    vol = df["Volume"]
    return vol.rolling(5).mean() / vol.shift(5).rolling(20).mean().replace(0, pd.NA)


def trades_with_gate(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    creep: pd.Series,
    threshold: Optional[float],
    lo: int,
    hi: int,
    slippage_pct: float,
) -> List[float]:
    """LIVE-rule trades (tune.py mask with the live defaults), plus an
    optional vol_creep floor. Non-overlapping, entry bar in [lo, hi)."""
    p = config.DEFAULT_SIGNAL_PARAMS
    mask = (
        feats["trend"]
        & feats["rsi"].between(p.rsi_min, p.rsi_max)
        & (feats["rel_vol"] >= p.min_relative_volume)
        & (feats["dollar_vol"] >= p.min_avg_dollar_volume)
        & feats["atr"].notna()
        & (feats["shock"] > -p.shock_move_pct)
    )
    if threshold is not None:
        mask &= (creep >= threshold).fillna(False)
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
    thresholds: Optional[List[Optional[float]]] = None,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
    max_symbols: Optional[int] = None,
) -> Dict:
    provider = provider or YFinanceProvider()
    thresholds = THRESHOLDS if thresholds is None else thresholds
    train: Dict[Optional[float], List[float]] = {t: [] for t in thresholds}
    valid: Dict[Optional[float], List[float]] = {t: [] for t in thresholds}
    used: List[str] = []

    for symbol in universe[: max_symbols or None]:
        try:
            if not screen_company(provider.get_fundamentals(symbol)).compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[tune_volcreep] skip {symbol}: {exc}", file=sys.stderr)
            continue
        if len(df) < 2 * WARMUP + 40:
            continue
        feats = feature_frame(df)
        creep = vol_creep_series(df)
        mid = (len(df) + WARMUP) // 2
        used.append(symbol)
        for t in thresholds:
            train[t] += trades_with_gate(df, feats, creep, t, WARMUP, mid, slippage_pct)
            valid[t] += trades_with_gate(df, feats, creep, t, mid, len(df), slippage_pct)
        print(f"[tune_volcreep] {symbol}: done ({len(df)} bars)", file=sys.stderr)

    rows = [{"threshold": t, "train": stats(train[t]), "valid": stats(valid[t])}
            for t in thresholds]
    return {"rows": rows, "symbols_used": used, "years": years}


def render_markdown(result: Dict, as_of: date) -> str:
    def side(s):
        if s is None:
            return "0 | -- | -- | --"
        pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
        return f"{s['n']} | {s['win_rate']}% | {s['avg']:+.3f}% | {pf}"

    lines = [
        f"# vol_creep gate -- split-sample test -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} compliant symbols, {result['years']}y of bars. "
        "LIVE entry rules unchanged; each row ADDS a floor on vol_creep "
        "(recent 5d avg volume vs the prior 20d) at entry. Both halves shown; "
        "only the validation half counts. Follow-up to the 2026-11-09 pre-pop study.",
        "",
        "| Gate | Train n | Train win | Train avg | Train PF "
        "| Valid n | Valid win | Valid avg | Valid PF |",
        "|" + "---|" * 9,
    ]
    for r in result["rows"]:
        label = ("LIVE baseline (no gate)" if r["threshold"] is None
                 else f"+ vol_creep >= {r['threshold']:g}x")
        lines.append(f"| {label} | {side(r['train'])} | {side(r['valid'])} |")
    lines += [
        "",
        "## Decision rule (written before the data was looked at)",
        "",
        "- Adopt a gate only if it beats the LIVE baseline's validation avg by a clear "
        "margin (not a few hundredths of a %) AND keeps a comparable trade count -- a "
        "gate that filters down to a handful of trades proves nothing.",
        "- The pre-pop lift was measured on POPS (>= +8% days); this table measures "
        "actual rule-trades with stops, targets and slippage. Only this table can "
        "justify adoption.",
        "- 'No change' is a good result, not a failure.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Split-sample test of a vol_creep entry gate")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = run_test(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    out = args.out or f"backtests/{date.today().isoformat()}-tune-volcreep.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"Written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
