"""Pre-pop precursor study -- a MEASUREMENT, not a signal.

    python -m halal_trader.prepop_study

Question (user, 2026-11-09, after seeing a top-movers list): can anything
observable BEFORE a big one-day up-move make it more likely? This script
does not predict anything -- it measures whether widely-claimed precursor
signatures (volume creep, volatility squeeze, prior drift, distance from
the 52-week high, RSI) actually separated pre-pop days from ordinary days
in our own universe's history.

Method, stated before the data is looked at:

- A "pop" is a single-day close-to-close gain >= +8% (the same threshold
  the whipsaw filter uses on the downside). A day t is LABELLED positive
  when a pop occurs in days t+1..t+5 -- i.e. features at t must precede
  the move entirely.
- Features use data through day t only (rolling windows ending at t).
- Split-sample by calendar date: quintile breakpoints are computed on the
  FIRST half only and applied unchanged to the second half. A signature
  counts as real only if the same extreme quintile shows a lift of at
  least MIN_LIFT in BOTH halves, in the same direction.
- Known caveats, accepted upfront: 5-day label windows overlap (serial
  correlation inflates the row count, so no significance tests are
  quoted, only lifts); pops cluster by regime and sector (one hot streak
  can paint a whole quintile); and a surviving lift is a shifted
  conditional probability, NOT a tradable edge -- adoption into scoring
  would still need its own tune.py-style out-of-sample test.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .cli import load_universe
from .data_provider import DataUnavailable, YFinanceProvider
from .signals import _atr, _rsi

POP_THRESHOLD_PCT = 8.0     # single-day close-to-close gain that counts as a pop
LABEL_HORIZON_DAYS = 5      # pop must land within this many days AFTER the feature day
MIN_PRIOR_BARS = 60         # feature rows need at least this much history behind them
MIN_LIFT = 1.3              # extreme-quintile pop rate must be >= this x base rate, both halves
N_QUANTILES = 5

# Feature name -> (column, which extreme quintile the folk claim points at)
# "top" = high values supposedly precede pops, "bottom" = low values do.
FEATURE_CLAIMS = {
    "vol_creep": ("top", "recent 5d volume vs the prior 20d (quiet accumulation)"),
    "squeeze": ("bottom", "ATR5/ATR20 -- range contraction before expansion"),
    "drift5": ("top", "5-day price drift (move already starting)"),
    "dist_high": ("bottom", "distance below the 252d high (more room to pop?)"),
    "rsi": ("top", "RSI14 (momentum already building)"),
}


def compute_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-day precursor features, each using data through that day only."""
    close, vol = frame["Close"], frame["Volume"]
    recent_vol = vol.rolling(5).mean()
    prior_vol = vol.shift(5).rolling(20).mean()  # the 20 days BEFORE the recent 5
    feats = pd.DataFrame(index=frame.index)
    feats["vol_creep"] = recent_vol / prior_vol.replace(0, np.nan)
    feats["squeeze"] = _atr(frame, 5) / _atr(frame, 20).replace(0, np.nan)
    feats["drift5"] = close.pct_change(5) * 100.0
    feats["dist_high"] = (close / close.rolling(252, min_periods=MIN_PRIOR_BARS).max() - 1.0) * 100.0
    feats["rsi"] = _rsi(close, 14)
    feats.iloc[:MIN_PRIOR_BARS] = np.nan  # not enough history behind these rows
    return feats


def label_pops(frame: pd.DataFrame, horizon: int = LABEL_HORIZON_DAYS,
               threshold_pct: float = POP_THRESHOLD_PCT) -> pd.Series:
    """True at day t when a >= threshold single-day gain occurs in t+1..t+horizon."""
    day_ret = frame["Close"].pct_change() * 100.0
    is_pop_day = day_ret >= threshold_pct
    upcoming = pd.Series(False, index=frame.index)
    for k in range(1, horizon + 1):
        upcoming |= is_pop_day.shift(-k).fillna(False).astype(bool)
    out = upcoming.astype(float)  # float so the unknowable tail can be NaN
    # Last `horizon` rows can't see their full window -- unknowable, not False.
    if horizon > 0:
        out.iloc[-horizon:] = np.nan
    return out


def build_table(histories: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per (symbol, day): features + pop-ahead label."""
    rows = []
    for symbol, frame in histories.items():
        if frame is None or len(frame) < MIN_PRIOR_BARS + LABEL_HORIZON_DAYS + 5:
            continue
        feats = compute_features(frame)
        feats["label"] = label_pops(frame)
        feats["symbol"] = symbol
        rows.append(feats.dropna())
    if not rows:
        return pd.DataFrame()
    table = pd.concat(rows)
    table.index.name = "date"
    return table.reset_index()


def quintile_lift(table: pd.DataFrame, feature: str, train_mask: pd.Series,
                  n_quantiles: int = N_QUANTILES) -> Optional[dict]:
    """Pop rate per feature-quintile, breakpoints from the train half only."""
    train = table[train_mask]
    test = table[~train_mask]
    if len(train) < 500 or len(test) < 500:
        return None
    edges = train[feature].quantile(np.linspace(0, 1, n_quantiles + 1)).to_numpy(copy=True)
    edges[0], edges[-1] = -np.inf, np.inf
    edges = np.unique(edges)
    if len(edges) < 3:
        return None  # degenerate feature

    def rates(half: pd.DataFrame) -> List[dict]:
        bins = pd.cut(half[feature], bins=edges, labels=False, include_lowest=True)
        out = []
        for q in range(len(edges) - 1):
            grp = half[bins == q]
            out.append({"n": len(grp),
                        "pop_rate": float(grp["label"].mean()) if len(grp) else np.nan})
        return out

    return {
        "edges": edges.tolist(),
        "train": {"base": float(train["label"].mean()), "n": len(train), "quintiles": rates(train)},
        "test": {"base": float(test["label"].mean()), "n": len(test), "quintiles": rates(test)},
    }


@dataclass
class Verdict:
    feature: str
    claim_side: str
    train_lift: float
    test_lift: float
    survives: bool


def judge(feature: str, result: dict, min_lift: float = MIN_LIFT) -> Verdict:
    """Does the claimed extreme quintile beat base rate by min_lift in BOTH halves?"""
    side = FEATURE_CLAIMS[feature][0]
    idx = -1 if side == "top" else 0

    def lift(half: dict) -> float:
        rate = half["quintiles"][idx]["pop_rate"]
        return rate / half["base"] if half["base"] else np.nan

    tr, te = lift(result["train"]), lift(result["test"])
    survives = bool(np.isfinite(tr) and np.isfinite(te) and tr >= min_lift and te >= min_lift)
    return Verdict(feature, side, tr, te, survives)


def fetch_histories(symbols: List[str], provider=None, period: str = "5y") -> Dict[str, pd.DataFrame]:
    provider = provider or YFinanceProvider()
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            out[sym] = provider.get_price_history(sym, period=period)
        except DataUnavailable as exc:
            print(f"[prepop] {sym}: {exc}", file=sys.stderr)
    return out


def build_report(table: pd.DataFrame, results: Dict[str, dict],
                 verdicts: List[Verdict], split_date, as_of: Optional[date] = None) -> str:
    as_of = as_of or date.today()
    pops = int(table["label"].sum())
    lines = [
        f"# Pre-pop precursor study -- {as_of.isoformat()}",
        "",
        "**Measurement only.** Nothing here is a signal, a rule, or a prediction.",
        f"Universe rows: {len(table):,} symbol-days across {table['symbol'].nunique()} symbols; "
        f"{pops:,} rows ({100 * pops / len(table):.2f}%) are followed by a >= +{POP_THRESHOLD_PCT:.0f}% "
        f"day within {LABEL_HORIZON_DAYS} trading days.",
        f"Split-sample at **{split_date}**: quintile edges fitted on the first half, applied "
        f"unchanged to the second. A claim survives only with >= {MIN_LIFT}x lift in BOTH halves.",
        "",
        "Known caveats (accepted before looking): overlapping label windows inflate row counts "
        "(no significance tests quoted, lifts only); pops cluster by regime and sector; a "
        "surviving lift is a shifted conditional probability, not a tradable edge.",
        "",
        "## Verdicts",
        "",
        "| Feature | Folk claim | Train lift | Test lift | Survives both halves? |",
        "|---|---|---|---|---|",
    ]
    for v in verdicts:
        desc = FEATURE_CLAIMS[v.feature][1]
        lines.append(f"| **{v.feature}** | {desc} | {v.train_lift:.2f}x | {v.test_lift:.2f}x | "
                     f"{'**YES**' if v.survives else 'no'} |")
    survivors = [v for v in verdicts if v.survives]
    lines += ["",
              ("**Overall: no precursor survived.** The measured signatures did not make pops "
               "meaningfully more likely out-of-sample; the top-movers list stays unpredictable "
               "from daily price data, as expected."
               if not survivors else
               f"**Overall: {len(survivors)} signature(s) survived both halves** "
               f"({', '.join(v.feature for v in survivors)}). Next step BEFORE any adoption: a "
               "tune.py-style split-sample test of the signature as a scoring input, with entry "
               "rules unchanged -- a lift on this table is not yet a tradable edge."),
              ""]
    for feature, result in results.items():
        if result is None:
            continue
        lines += [f"## {feature} -- pop rate by quintile (Q1 low ... Q{N_QUANTILES} high)", "",
                  "| Half | Base rate | " + " | ".join(f"Q{q+1}" for q in range(N_QUANTILES)) + " |",
                  "|---|---|" + "---|" * N_QUANTILES]
        for half in ("train", "test"):
            h = result[half]
            cells = " | ".join(
                f"{q['pop_rate'] * 100:.2f}% (n={q['n']:,})" if np.isfinite(q["pop_rate"]) else "--"
                for q in h["quintiles"])
            lines.append(f"| {half} (n={h['n']:,}) | {h['base'] * 100:.2f}% | {cells} |")
        lines.append("")
    lines += ["---", "_Generated by halal_trader.prepop_study._", ""]
    return "\n".join(lines)


def run_study(table: pd.DataFrame):
    split_date = table["date"].quantile(0.5)
    train_mask = table["date"] <= split_date
    results, verdicts = {}, []
    for feature in FEATURE_CLAIMS:
        res = quintile_lift(table, feature, train_mask)
        results[feature] = res
        if res is not None:
            verdicts.append(judge(feature, res))
    return results, verdicts, split_date


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pre-pop precursor study (measurement only)")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    symbols = load_universe(args.universe)
    print(f"[prepop] fetching {args.period} history for {len(symbols)} symbols...", file=sys.stderr)
    histories = fetch_histories(symbols, period=args.period)
    table = build_table(histories)
    if table.empty:
        print("No usable history.", file=sys.stderr)
        return 1
    table["label"] = table["label"].astype(float)
    results, verdicts, split_date = run_study(table)
    report = build_report(table, results, verdicts, pd.Timestamp(split_date).date())
    out = args.out or f"backtests/{date.today().isoformat()}-prepop.md"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(report)
    print(f"Written to {out}")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
