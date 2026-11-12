"""Tests for the mean-reversion entry-family study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT, gate_features
from halal_trader.tune_meanrev import (
    CELLS,
    FAMILY_WARMUP,
    MIN_HALF_TRADES,
    _passes,
    meanrev_entries,
    render_markdown,
)


def frame(closes, volume=3_000_000.0):
    c = pd.Series(closes, dtype=float)
    idx = pd.date_range("2023-01-02", periods=len(c), freq="B")
    return pd.DataFrame(
        {"Open": c.values, "High": c.values * 1.005, "Low": c.values * 0.995,
         "Close": c.values, "Volume": volume},
        index=idx,
    )


def test_rsi2_fires_after_consecutive_down_days():
    closes = [100.0] * 60 + [99.0, 98.0, 97.0] + [97.0] * 5
    df = frame(closes)
    feats = gate_features(df)
    entries = meanrev_entries(df, feats, kind="rsi2", need_trend=False)
    assert 62 in entries  # third straight down day: RSI(2) deeply oversold


def test_rsi2_quiet_in_steady_uptrend():
    df = frame(list(100 * 1.002 ** np.arange(120)))
    feats = gate_features(df)
    assert meanrev_entries(df, feats, kind="rsi2", need_trend=False) == []


def test_bollinger_fires_on_band_break_only():
    # perfectly flat at 100 (band width zero), then a -5% day breaks below
    closes = [100.0] * 80 + [95.0] + [95.0] * 4
    df = frame(closes)
    feats = gate_features(df)
    entries = meanrev_entries(df, feats, kind="boll", need_trend=False)
    assert 80 in entries
    assert all(e >= 80 for e in entries)  # nothing inside the quiet range


def test_trend_filter_subsets_and_requires_uptrend():
    # long downtrend: price always below its 200d SMA -> trend variant empty
    closes = list(1000 * 0.998 ** np.arange(260))
    df = frame(closes)
    feats = gate_features(df)
    plain = meanrev_entries(df, feats, kind="rsi2", need_trend=False)
    trend = meanrev_entries(df, feats, kind="rsi2", need_trend=True)
    assert set(trend) <= set(plain)
    assert len(plain) > 0 and trend == []


def test_illiquid_names_gated_out():
    closes = [100.0] * 60 + [99.0, 98.0, 97.0] + [97.0] * 5
    df = frame(closes, volume=1_000.0)
    feats = gate_features(df)
    assert meanrev_entries(df, feats, kind="rsi2", need_trend=False) == []


def test_passes_absolute_floor_per_half():
    live = {"train": {"n": 1400, "avg": 0.3, "win_rate": 55.0, "profit_factor": 1.3},
            "valid": {"n": 1400, "avg": 0.3, "win_rate": 55.0, "profit_factor": 1.3}}

    def row(n, avg):
        s = {"win_rate": 55.0, "profit_factor": 1.3}
        return {"train": {"n": n, "avg": avg, **s}, "valid": {"n": n, "avg": avg, **s}}

    assert _passes(row(MIN_HALF_TRADES, 0.3 + ADOPT_MARGIN_PCT), live)
    assert not _passes(row(MIN_HALF_TRADES - 1, 0.9), live)
    assert not _passes(row(500, 0.3 + ADOPT_MARGIN_PCT - 0.01), live)


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
    assert "No mean-reversion variant passes" in text
    assert str(FAMILY_WARMUP) in text  # the shared bar-200 start is documented
