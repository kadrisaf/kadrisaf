"""Tests for the Donchian breakout study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_donchian import (
    CELLS,
    MIN_HALF_TRADES,
    _passes,
    donchian_entries,
    render_markdown,
)
from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT, gate_features


def frame(closes, highs=None, volume=3_000_000.0):
    c = pd.Series(closes, dtype=float)
    h = pd.Series(highs, dtype=float) if highs is not None else c * 1.005
    idx = pd.date_range("2024-01-02", periods=len(c), freq="B")
    return pd.DataFrame(
        {"Open": c.values, "High": h.values, "Low": c.values * 0.995,
         "Close": c.values, "Volume": volume},
        index=idx,
    )


def test_breakout_of_flat_range_detected():
    closes = [100.0] * 80 + [103.0] + [103.0] * 5   # pops above the 100.5 channel
    df = frame(closes)
    feats = gate_features(df)
    entries = donchian_entries(df, feats, lookback=20, need_volume=False)
    assert 80 in entries


def test_no_entry_inside_the_range():
    df = frame([100.0] * 90)
    feats = gate_features(df)
    assert donchian_entries(df, feats, lookback=20, need_volume=False) == []


def test_55d_lookback_is_stricter_than_20d():
    rng = np.random.default_rng(4)
    closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.015, 400))
    df = frame(list(closes))
    feats = gate_features(df)
    e20 = set(donchian_entries(df, feats, lookback=20, need_volume=False))
    e55 = set(donchian_entries(df, feats, lookback=55, need_volume=False))
    assert e55 <= e20  # a 55d-high is always a 20d-high
    assert len(e20) > len(e55)


def test_volume_confirmation_subsets_entries():
    closes = [100.0] * 80 + [103.0] + [103.0] * 5
    volumes = np.full(86, 3_000_000.0)
    df = frame(closes)
    df["Volume"] = volumes  # breakout day volume == 20d average: rel_vol 1.0 < 1.2
    feats = gate_features(df)
    assert 80 in donchian_entries(df, feats, lookback=20, need_volume=False)
    assert 80 not in donchian_entries(df, feats, lookback=20, need_volume=True)


def test_illiquid_breakouts_gated_out():
    closes = [100.0] * 80 + [103.0] + [103.0] * 5
    df = frame(closes, volume=1_000.0)
    feats = gate_features(df)
    assert donchian_entries(df, feats, lookback=20, need_volume=False) == []


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
    assert "No Donchian variant passes" in text
