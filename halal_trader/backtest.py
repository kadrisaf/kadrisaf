"""Historical backtest of the screen's signal rules.

    python -m halal_trader.backtest --universe data/universe_default.csv --years 3

Replays `evaluate_signal` day by day on each currently-Shariah-compliant
symbol's history, enters at the signal day's close (the same convention as
the outcome tracker), and exits with the tracker's own rules
(`track_record.simulate_exit`: stop first, gap fills at the open, slippage
on market-style exits, 5-day time-stop). It compares:

  * SIGNAL  -- days on which the full filter qualifies, versus
  * ANY-DAY -- entering on every day with the same stop/target rules,

so you can see whether the filter adds anything over just being long, and
how sensitive the result is to the stop/target ATR multiples.

Read the caveats it prints. The big ones: the Shariah screen uses TODAY'S
fundamentals (look-ahead), the universe is today's large caps (survivorship),
there is no cost model beyond exit slippage, and trading several symbols on
the same days is correlated, so effective sample size is smaller than the
trade count. This is a sanity check, not proof of edge.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import config
from .cli import load_universe
from .data_provider import DataProvider, DataUnavailable, YFinanceProvider
from .shariah_screen import screen_company
from .signals import evaluate_signal
from .track_record import simulate_exit

WARMUP = 260  # bars of history handed to evaluate_signal (>= a 52-week window)
# (stop ATR multiple, target ATR multiple); (1.5, 2.25) is the live default.
GRID: List[Tuple[float, float]] = [(1.0, 1.5), (1.5, 2.25), (2.0, 3.0), (1.5, 3.0), (1.0, 3.0)]


@dataclass
class Trade:
    entry_date: date
    ret_pct: float
    reason: str


def collect_days(symbol: str, df: pd.DataFrame, params: config.SignalParams, warmup: int = WARMUP):
    """One row per day: (bar index, qualifies, close, atr)."""
    rows = []
    for i in range(warmup, len(df) - 1):
        sig = evaluate_signal(symbol, df.iloc[i - warmup + 1 : i + 1], params)
        if sig.atr is None or sig.last_close is None:
            continue
        rows.append((i, sig.qualifies, sig.last_close, sig.atr))
    return rows


def simulate_trades(
    df: pd.DataFrame,
    days,
    stop_mult: float,
    target_mult: float,
    max_days: int,
    slippage_pct: float,
    only_qualifying: bool,
) -> List[Trade]:
    """One open trade per symbol at a time (no overlapping entries)."""
    out: List[Trade] = []
    busy_until = -1
    for i, qualifies, close, atr in days:
        if only_qualifying and not qualifies:
            continue
        if i <= busy_until:
            continue
        stop, target = close - stop_mult * atr, close + target_mult * atr
        sub = df.iloc[i + 1 : i + 1 + max_days]
        result = simulate_exit(sub, stop, target, max_days, slippage_pct)
        if result is None:
            continue
        exit_day, price, reason = result
        k = next(n for n, ts in enumerate(sub.index) if ts.date() == exit_day)
        busy_until = i + 1 + k
        out.append(Trade(df.index[i].date(), (price - close) / close * 100, reason))
    return out


def stats(trades: List[Trade]) -> Optional[Dict]:
    if not trades:
        return None
    rets = pd.Series([t.ret_pct for t in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    gross_loss = abs(losses.sum())
    return {
        "n": len(rets),
        "win_rate": round(100 * len(wins) / len(rets), 1),
        "avg": round(rets.mean(), 2),
        "median": round(rets.median(), 2),
        "avg_win": round(wins.mean(), 2) if len(wins) else 0.0,
        "avg_loss": round(losses.mean(), 2) if len(losses) else 0.0,
        "profit_factor": round(wins.sum() / gross_loss, 2) if gross_loss else None,
        "stop_pct": round(100 * sum(t.reason == "stop" for t in trades) / len(trades), 1),
        "target_pct": round(100 * sum(t.reason == "target" for t in trades) / len(trades), 1),
        "time_pct": round(100 * sum(t.reason == "time_stop" for t in trades) / len(trades), 1),
    }


def _regime_map(df: pd.DataFrame) -> Dict:
    """date -> True when that day's close is above its own 200-day SMA."""
    sma = df["Close"].rolling(200).mean()
    return {
        ts.date(): bool(c > m)
        for ts, c, m in zip(df.index, df["Close"], sma)
        if pd.notna(m)
    }


def run_backtest(
    universe: List[str],
    years: int = 3,
    provider: Optional[DataProvider] = None,
    grid: List[Tuple[float, float]] = GRID,
    slippage_pct: float = config.EXIT_SLIPPAGE_PCT,
    warmup: int = WARMUP,
    max_symbols: Optional[int] = None,
) -> Dict:
    provider = provider or YFinanceProvider()
    params = config.DEFAULT_SIGNAL_PARAMS
    max_days = params.max_holding_days
    signal_trades = {c: [] for c in grid}
    anyday_trades = {c: [] for c in grid}
    used, skipped = [], []
    default_cell = (params.stop_atr_multiple, params.target_atr_multiple)
    regime_trades = {"above own 200d SMA": [], "below own 200d SMA": []}
    spy_regime_trades = {"SPY above 200d SMA": [], "SPY below 200d SMA": []}
    try:
        spy_regime = _regime_map(provider.get_price_history("SPY", period=f"{years + 1}y"))
    except (DataUnavailable, Exception) as exc:
        print(f"[backtest] SPY unavailable, market-regime split skipped: {exc}", file=sys.stderr)
        spy_regime = None

    for symbol in universe[: max_symbols or None]:
        try:
            screen = screen_company(provider.get_fundamentals(symbol))
            if not screen.compliant:
                continue
            df = provider.get_price_history(symbol, period=f"{years + 1}y")
        except DataUnavailable as exc:
            print(f"[backtest] skip {symbol}: {exc}", file=sys.stderr)
            skipped.append(symbol)
            continue
        if len(df) < warmup + max_days + 5:
            skipped.append(symbol)
            continue
        days = collect_days(symbol, df, params, warmup)
        used.append(symbol)
        for cell in grid:
            sm, tm = cell
            cell_trades = simulate_trades(df, days, sm, tm, max_days, slippage_pct, True)
            signal_trades[cell] += cell_trades
            anyday_trades[cell] += simulate_trades(df, days, sm, tm, max_days, slippage_pct, False)
            if cell == default_cell:
                own = _regime_map(df)
                for t in cell_trades:
                    r = own.get(t.entry_date)
                    if r is not None:
                        regime_trades["above own 200d SMA" if r else "below own 200d SMA"].append(t)
                    if spy_regime is not None:
                        m = spy_regime.get(t.entry_date)
                        if m is not None:
                            spy_regime_trades["SPY above 200d SMA" if m else "SPY below 200d SMA"].append(t)
        print(f"[backtest] {symbol}: {len(days)} days evaluated", file=sys.stderr)

    halves = {}
    base = sorted(signal_trades.get(default_cell, []), key=lambda t: t.entry_date)
    if len(base) >= 20:
        mid = len(base) // 2
        halves = {"first half": stats(base[:mid]), "second half": stats(base[mid:])}
    return {
        "symbols_used": used,
        "symbols_skipped": skipped,
        "signal": {c: stats(t) for c, t in signal_trades.items()},
        "anyday": {c: stats(t) for c, t in anyday_trades.items()},
        "halves": halves,
        "regimes": {k: stats(v) for k, v in regime_trades.items()},
        "spy_regimes": {k: stats(v) for k, v in spy_regime_trades.items()} if spy_regime is not None else {},
        "default_cell": default_cell,
        "years": years,
        "slippage_pct": slippage_pct,
    }


def render_markdown(result: Dict, as_of: date) -> str:
    def row(label, s):
        if s is None:
            return f"| {label} | 0 | -- | -- | -- | -- | -- | -- | -- |"
        pf = s["profit_factor"] if s["profit_factor"] is not None else "inf"
        return (
            f"| {label} | {s['n']} | {s['win_rate']}% | {s['avg']:+.2f}% | {s['median']:+.2f}% | "
            f"{s['avg_win']:+.2f}% / {s['avg_loss']:+.2f}% | {pf} | "
            f"{s['stop_pct']}/{s['target_pct']}/{s['time_pct']} |"
        ).replace("|  |", "|")

    head = "| Stop x ATR / Target x ATR | Trades | Win rate | Avg ret | Median | Avg win / loss | Profit factor | % stop/target/time |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [
        f"# Signal backtest -- {as_of.isoformat()}",
        "",
        f"{len(result['symbols_used'])} currently-compliant symbols, {result['years']} years of daily bars, "
        f"entry at the signal day's close, 5-day max hold, exit slippage {result['slippage_pct']}% on stop/time-stop exits. "
        f"Live default = {result['default_cell'][0]}x stop / {result['default_cell'][1]}x target.",
        "",
        "## Signal days (full filter) -- by stop/target multiples",
        "",
        head, sep,
    ]
    for cell, s in result["signal"].items():
        tag = " (live)" if cell == result["default_cell"] else ""
        lines.append(row(f"{cell[0]} / {cell[1]}{tag}", s))
    lines += ["", "## Any-day baseline (enter every day, same exits) -- is the filter adding anything?", "", head, sep]
    for cell, s in result["anyday"].items():
        tag = " (live)" if cell == result["default_cell"] else ""
        lines.append(row(f"{cell[0]} / {cell[1]}{tag}", s))
    if result.get("regimes"):
        lines += ["", "## Regime split: live parameters, by trend regime at entry", "", head, sep]
        for k, s_ in result["regimes"].items():
            lines.append(row(k, s_))
        for k, s_ in result.get("spy_regimes", {}).items():
            lines.append(row(k, s_))
        lines.append("")
        lines.append(
            "A regime where the signal loses money is a reason to stand aside, not to "
            "re-tune. Small per-regime samples first: check n before believing a split."
        )
    if result["halves"]:
        lines += ["", "## Stability: live parameters, first vs second half of the signal trades (by date)", "", head, sep]
        for k, s in result["halves"].items():
            lines.append(row(k, s))
    lines += [
        "",
        "## Caveats (read before trusting any number above)",
        "",
        "- **Look-ahead:** compliance uses today's fundamentals for every historical day.",
        "- **Survivorship:** the universe is today's large caps, many chosen after big runs; that flatters any long-only test.",
        "- **Correlation:** trades on the same days across symbols are not independent, so the real sample is smaller than the trade count.",
        "- **Multiple comparisons:** five stop/target cells were tried; the best-looking one is partly luck. Do not re-tune the live parameters on this table alone.",
        "- **Costs:** only exit slippage is modelled (no spread/fees/FX/tax; Trade Republic charges 1 EUR per order).",
        "- **Execution:** entry at the signal day's close is an idealisation; the live report is read the next morning.",
        f"- Skipped (no data / too short): {', '.join(result['symbols_skipped']) or 'none'}.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the halal_trader signal rules")
    ap.add_argument("--universe", default="data/universe_default.csv")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default="backtests/backtest.md")
    args = ap.parse_args(argv)
    result = run_backtest(load_universe(args.universe), args.years, max_symbols=args.max_symbols)
    md = render_markdown(result, date.today())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md)
    print(md)
    print(f"Backtest written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
