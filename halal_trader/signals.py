"""Short-term (<= 1 week) technical signal generation.

This is a single, deliberately simple momentum/trend-confirmation setup --
not a claim that it's profitable. It exists to rank an already
Shariah-screened universe by "does this look like a reasonable short-term
long candidate right now", and to compute a mechanical stop-loss / target /
time-stop so risk is defined *before* you enter, not after.

Nothing here is investment advice. Past price action does not predict
future returns; short-term trading carries a real risk of loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from .config import SignalParams, DEFAULT_SIGNAL_PARAMS


@dataclass
class TradeSignal:
    symbol: str
    qualifies: bool
    reasons_excluded: List[str]
    last_close: Optional[float] = None
    rsi: Optional[float] = None
    fast_sma: Optional[float] = None
    slow_sma: Optional[float] = None
    relative_volume: Optional[float] = None
    avg_dollar_volume: Optional[float] = None
    atr: Optional[float] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    reward_risk: Optional[float] = None
    score: Optional[float] = None
    max_holding_days: int = 5


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def evaluate_signal(
    symbol: str,
    history: pd.DataFrame,
    params: SignalParams = DEFAULT_SIGNAL_PARAMS,
) -> TradeSignal:
    """Evaluate one symbol's OHLCV history (ascending date order) for a
    short-term long setup. Returns a TradeSignal; `qualifies` tells you
    whether it passed every filter."""
    reasons: List[str] = []
    needed = max(params.slow_sma, params.atr_period, params.rsi_period) + 5
    if history is None or len(history) < needed:
        return TradeSignal(
            symbol=symbol,
            qualifies=False,
            reasons_excluded=[f"insufficient price history (need >= {needed} bars)"],
            max_holding_days=params.max_holding_days,
        )

    close = history["Close"]
    volume = history["Volume"]

    rsi = _rsi(close, params.rsi_period).iloc[-1]
    fast_sma = close.rolling(params.fast_sma).mean().iloc[-1]
    slow_sma = close.rolling(params.slow_sma).mean().iloc[-1]
    fast_sma_prev = close.rolling(params.fast_sma).mean().iloc[-2]
    avg_volume = volume.rolling(20).mean().iloc[-1]
    last_volume = volume.iloc[-1]
    relative_volume = last_volume / avg_volume if avg_volume else None
    avg_dollar_volume = (close.rolling(20).mean() * volume.rolling(20).mean()).iloc[-1]
    atr = _atr(history, params.atr_period).iloc[-1]
    last_close = close.iloc[-1]

    if avg_dollar_volume is None or avg_dollar_volume < params.min_avg_dollar_volume:
        reasons.append(
            f"avg dollar volume {avg_dollar_volume:,.0f} < {params.min_avg_dollar_volume:,.0f} "
            "(not liquid enough for a clean 1-week round trip)"
        )

    if pd.isna(fast_sma) or pd.isna(slow_sma) or last_close < fast_sma or fast_sma < slow_sma:
        reasons.append("not in a confirmed short-term uptrend (price>20SMA>50SMA)")

    if pd.isna(fast_sma_prev) or fast_sma < fast_sma_prev:
        reasons.append("20-day SMA is not rising")

    if pd.isna(rsi) or not (params.rsi_min <= rsi <= params.rsi_max):
        reasons.append(
            f"RSI {rsi:.1f} outside [{params.rsi_min}, {params.rsi_max}] "
            "(too weak or already overbought for a fresh entry)"
        )

    if relative_volume is None or relative_volume < params.min_relative_volume:
        rv = f"{relative_volume:.2f}x" if relative_volume is not None else "n/a"
        reasons.append(
            f"relative volume {rv} < {params.min_relative_volume}x (no confirming interest)"
        )

    stop_loss = target = reward_risk = None
    if atr and not pd.isna(atr):
        stop_loss = last_close - params.stop_atr_multiple * atr
        target = last_close + params.target_atr_multiple * atr
        risk = last_close - stop_loss
        reward = target - last_close
        reward_risk = reward / risk if risk > 0 else None

    score = None
    if not reasons:
        # Simple composite: trend strength + momentum position + volume
        # confirmation. Higher is "more interesting", not "more certain".
        trend_strength = (fast_sma - slow_sma) / slow_sma if slow_sma else 0
        momentum_position = (rsi - params.rsi_min) / (params.rsi_max - params.rsi_min)
        volume_confirmation = min(relative_volume / params.min_relative_volume, 3.0)
        score = round(
            100 * trend_strength + 20 * momentum_position + 10 * volume_confirmation, 2
        )

    return TradeSignal(
        symbol=symbol,
        qualifies=len(reasons) == 0,
        reasons_excluded=reasons,
        last_close=round(float(last_close), 4),
        rsi=round(float(rsi), 2) if not pd.isna(rsi) else None,
        fast_sma=round(float(fast_sma), 4) if not pd.isna(fast_sma) else None,
        slow_sma=round(float(slow_sma), 4) if not pd.isna(slow_sma) else None,
        relative_volume=round(float(relative_volume), 2) if relative_volume is not None else None,
        avg_dollar_volume=round(float(avg_dollar_volume), 0) if avg_dollar_volume is not None and not pd.isna(avg_dollar_volume) else None,
        atr=round(float(atr), 4) if atr is not None and not pd.isna(atr) else None,
        stop_loss=round(float(stop_loss), 4) if stop_loss is not None else None,
        target=round(float(target), 4) if target is not None else None,
        reward_risk=round(float(reward_risk), 2) if reward_risk is not None else None,
        score=score,
        max_holding_days=params.max_holding_days,
    )
