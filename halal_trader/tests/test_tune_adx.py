"""Tests for the ADX trend-strength gate study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_adx import (
    ADX_PERIOD,
    CELLS,
    THRESHOLDS,
    _passes,
    adx_series,
    render_markdown,
)
from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT


def ohlc(closes, wick=0.01):
    c = pd.Series(closes, dtype=float)
    idx = pd.date_range("2024-01-02", periods=len(c), freq="B")
    return pd.DataFrame(
        {"Open": c.values, "High": c.values * (1 + wick),
         "Low": c.values * (1 - wick), "Close": c.values,
         "Volume": 3_000_000.0},
        index=idx,
    )


def test_adx_high_in_persistent_trend():
    df = ohlc(100 * 1.01 ** np.arange(120))  # relentless 1%/day uptrend
    adx = adx_series(df)
    assert float(adx.iloc[-1]) > 25


def test_adx_low_in_choppy_range():
    # alternate +1%/-1%: direction flips every day, no net trend
    moves = np.tile([1.01, 1 / 1.01], 60)
    df = ohlc(100 * np.cumprod(moves))
    adx = adx_series(df)
    assert float(adx.iloc[-1]) < 20


def test_adx_warmup_is_nan():
    df = ohlc(100 * 1.005 ** np.arange(80))
    adx = adx_series(df)
    assert adx.iloc[: 2 * ADX_PERIOD].isna().all()
    assert np.isfinite(adx.iloc[-1])


def test_adx_direction_agnostic():
    """A relentless DOWNTREND is also a strong trend: ADX must be high."""
    df = ohlc(100 * 0.99 ** np.arange(120))
    assert float(adx_series(df).iloc[-1]) > 25


def test_thresholds_are_nested():
    df = ohlc(100 * np.cumprod(1 + np.random.default_rng(1).normal(0.002, 0.012, 300)))
    adx = adx_series(df)
    counts = [(adx >= t).sum() for t in THRESHOLDS]
    assert counts == sorted(counts, reverse=True)  # 20 admits >= 25 admits >= 30


def test_passes_same_rule_as_entrygates():
    live = {"train": {"n": 1400, "avg": 0.3, "win_rate": 55.0, "profit_factor": 1.3},
            "valid": {"n": 1400, "avg": 0.3, "win_rate": 55.0, "profit_factor": 1.3}}

    def row(n_valid, avg):
        s = {"win_rate": 55.0, "profit_factor": 1.3}
        return {"train": {"n": 1000, "avg": avg, **s},
                "valid": {"n": n_valid, "avg": avg, **s}}

    assert _passes(row(800, 0.3 + ADOPT_MARGIN_PCT), live)
    assert not _passes(row(600, 0.3 + ADOPT_MARGIN_PCT), live)
    assert not _passes(row(800, 0.3 + ADOPT_MARGIN_PCT - 0.01), live)


def test_render_lists_cells_and_default_verdict():
    s = {"n": 300, "win_rate": 55.0, "avg": 0.3, "profit_factor": 1.3}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s)}
                  for k, lbl in CELLS],
        "symbols_used": ["AAA"], "years": 3,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "No ADX threshold passes" in text
