"""Tests for the rolling-VWAP filter study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_entrygates import gate_features
from halal_trader.tune_vwap import (
    CELLS,
    render_markdown,
    rolling_vwap,
    split_entries,
)


def frame(closes, volume=3_000_000.0):
    c = pd.Series(closes, dtype=float)
    idx = pd.date_range("2023-01-02", periods=len(c), freq="B")
    return pd.DataFrame(
        {"Open": c.values, "High": c.values * 1.005, "Low": c.values * 0.995,
         "Close": c.values, "Volume": volume},
        index=idx,
    )


def test_vwap_of_flat_series_is_typical_price():
    df = frame([100.0] * 40)
    vwap = rolling_vwap(df, 5)
    # typical price = (100.5 + 99.5 + 100)/3 = 100.0 for every bar
    assert np.allclose(vwap.dropna().to_numpy(), 100.0)


def test_vwap_lags_a_steady_uptrend():
    df = frame(list(100 * 1.01 ** np.arange(60)))
    vwap = rolling_vwap(df, 20)
    tail = df["Close"].iloc[30:] > vwap.iloc[30:]
    assert tail.all()  # in a steady rise the close sits above its lagging VWAP


def test_above_below_partition_live_entries_exactly():
    rng = np.random.default_rng(11)
    closes = 100 * np.cumprod(1 + rng.normal(0.002, 0.012, 400))
    df = frame(list(closes))
    feats = gate_features(df)
    cells = split_entries(df, feats)
    live = set(cells["live"])
    for w in (5, 20):
        above = set(cells[f"v{w}_above"])
        below = set(cells[f"v{w}_below"])
        assert above | below == live
        assert above & below == set()


def test_volume_weighting_matters():
    # same prices, but one huge-volume day at a low price drags VWAP down
    closes = [100.0] * 10 + [90.0] + [100.0] * 10
    df = frame(closes)
    df.loc[df.index[10], "Volume"] = 300_000_000.0
    heavy = rolling_vwap(df, 5).iloc[14]
    df2 = frame(closes)
    light = rolling_vwap(df2, 5).iloc[14]
    assert heavy < light


def test_render_lists_cells_and_default_verdict():
    s = {"n": 800, "win_rate": 55.0, "avg": 0.3, "profit_factor": 1.3}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s)}
                  for k, lbl in CELLS],
        "symbols_used": ["AAA"], "years": 3,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "No VWAP cell passes" in text
