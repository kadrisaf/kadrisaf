"""Tests for the Wyckoff spring study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_spring import (
    CELLS,
    MIN_HALF_TRADES,
    _passes,
    render_markdown,
    spring_entries,
    walk_trades,
)
from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT, gate_features


def base_frame(n=120, price=100.0, vol=3_000_000.0):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": price, "High": price * 1.005, "Low": price * 0.995,
         "Close": price, "Volume": vol},
        index=idx,
    )


def test_same_day_spring_detected():
    """Flat tape, one bar dips below the 20d low and closes back above it."""
    df = base_frame()
    i = 80
    df.iloc[i, df.columns.get_loc("Low")] = 98.0    # support ~99.5 -> undercut
    df.iloc[i, df.columns.get_loc("Close")] = 100.0  # reclaimed
    feats = gate_features(df)
    assert i in spring_entries(df, feats, lookback=20, reclaim_within=0)


def test_no_spring_when_close_stays_below_support():
    df = base_frame()
    i = 80
    df.iloc[i, df.columns.get_loc("Low")] = 98.0
    df.iloc[i, df.columns.get_loc("Close")] = 99.0   # support ~99.5: not reclaimed
    feats = gate_features(df)
    assert spring_entries(df, feats, lookback=20, reclaim_within=0) == []


def test_delayed_reclaim_enters_on_the_reclaim_day():
    df = base_frame()
    i = 80
    for col, val in [("Low", 98.0), ("Close", 99.0), ("High", 99.5), ("Open", 99.2)]:
        df.iloc[i, df.columns.get_loc(col)] = val
    # next day closes back above the frozen support (~99.5)
    df.iloc[i + 1, df.columns.get_loc("Close")] = 100.2
    feats = gate_features(df)
    assert spring_entries(df, feats, lookback=20, reclaim_within=0) == []
    assert (i + 1) in spring_entries(df, feats, lookback=20, reclaim_within=2)


def test_deep_break_is_not_a_spring():
    """Price crashes 5% through support and stays: the within-2-days scan
    must abandon it, not buy the dead-cat close."""
    df = base_frame()
    i = 80
    df.iloc[i, df.columns.get_loc("Low")] = 98.0
    df.iloc[i, df.columns.get_loc("Close")] = 99.0
    for j in (i + 1, i + 2):
        for col in ("Open", "High", "Low", "Close"):
            df.iloc[j, df.columns.get_loc(col)] = 94.0  # well below support*0.97
    feats = gate_features(df)
    assert spring_entries(df, feats, lookback=20, reclaim_within=2) == []


def test_quiet_variant_requires_low_volume_undercut():
    df = base_frame()
    i = 80
    df.iloc[i, df.columns.get_loc("Low")] = 98.0
    df.iloc[i, df.columns.get_loc("Close")] = 100.0
    df.iloc[i, df.columns.get_loc("Volume")] = 9_000_000.0  # loud undercut
    feats = gate_features(df)
    assert i in spring_entries(df, feats, lookback=20, reclaim_within=0)
    assert spring_entries(df, feats, lookback=20, reclaim_within=0,
                          quiet_undercut=True) == []


def test_illiquid_names_are_gated_out():
    df = base_frame(vol=1_000.0)  # fails the min dollar-volume gate
    i = 80
    df.iloc[i, df.columns.get_loc("Low")] = 98.0
    df.iloc[i, df.columns.get_loc("Close")] = 100.0
    feats = gate_features(df)
    assert spring_entries(df, feats, lookback=20, reclaim_within=0) == []


def test_walk_trades_non_overlapping():
    rng = np.random.default_rng(5)
    n = 300
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.0, 0.015, n)))
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    df = pd.DataFrame(
        {"Open": close.values, "High": close.values * 1.01,
         "Low": close.values * 0.99, "Close": close.values,
         "Volume": rng.integers(2_000_000, 6_000_000, n).astype(float)},
        index=idx,
    )
    feats = gate_features(df)
    every_day = list(range(len(df)))
    trades = walk_trades(df, feats, every_day, 60, n, 0.1)
    assert 0 < len(trades) < n - 60  # busy_until forces gaps


def test_passes_uses_absolute_floor_per_half():
    live = {"train": {"n": 1400, "avg": 0.3, "win_rate": 60.0, "profit_factor": 1.5},
            "valid": {"n": 1400, "avg": 0.3, "win_rate": 60.0, "profit_factor": 1.5}}

    def row(n_train, n_valid, avg):
        s = {"win_rate": 60.0, "profit_factor": 1.5}
        return {"train": {"n": n_train, "avg": avg, **s},
                "valid": {"n": n_valid, "avg": avg, **s}}

    good = row(MIN_HALF_TRADES, MIN_HALF_TRADES, 0.3 + ADOPT_MARGIN_PCT)
    assert _passes(good, live)  # rare family: 150/half is enough, no share test
    assert not _passes(row(MIN_HALF_TRADES - 1, 500, 0.9), live)
    assert not _passes(row(500, MIN_HALF_TRADES - 1, 0.9), live)
    assert not _passes(row(500, 500, 0.3 + ADOPT_MARGIN_PCT - 0.01), live)


def test_render_lists_cells_and_verdict():
    s = {"n": 200, "win_rate": 60.0, "avg": 0.4, "profit_factor": 1.5}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s)}
                  for k, lbl in CELLS],
        "symbols_used": ["AAA"], "years": 3,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "No spring variant passes the pre-registered rule" in text
