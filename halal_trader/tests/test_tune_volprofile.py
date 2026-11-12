"""Tests for the tier-1 (hourly bar) volume-profile study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_volprofile import (
    CELLS,
    MIN_HALF_TRADES,
    _passes,
    build_profile,
    classify_entry,
    has_hvn_support,
    poc_price,
    render_markdown,
)
from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT


def hourly_frame(prices, volumes):
    """One hourly bar per (price, volume) pair, tight ranges around price."""
    idx = pd.date_range("2026-01-05 10:00", periods=len(prices), freq="h")
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {"Open": p, "High": p * 1.001, "Low": p * 0.999, "Close": p,
         "Volume": np.asarray(volumes, dtype=float)},
        index=idx,
    )


def test_poc_lands_where_volume_concentrates():
    # heavy trade at 100, light everywhere else across 90..110
    prices = list(np.linspace(90, 110, 40)) + [100.0] * 20
    volumes = [1_000.0] * 40 + [50_000.0] * 20
    profile = build_profile(hourly_frame(prices, volumes), buckets=20)
    assert profile is not None
    assert abs(poc_price(profile) - 100.0) < 1.0


def test_build_profile_rejects_unusable_input():
    assert build_profile(None) is None
    assert build_profile(hourly_frame([100.0] * 5, [1.0] * 5)) is None  # too few bars
    flat = hourly_frame([100.0] * 40, [0.0] * 40)  # zero volume
    assert build_profile(flat) is None


def test_hvn_support_detected_below_entry():
    # volume wall at 98, entry at 100, ATR 3 -> wall inside [97, 100)
    prices = list(np.linspace(90, 110, 40)) + [98.0] * 20
    volumes = [1_000.0] * 40 + [50_000.0] * 20
    profile = build_profile(hourly_frame(prices, volumes), buckets=20)
    assert has_hvn_support(profile, close=100.0, atr=3.0)
    # entry far above the wall: nothing heavy within 1 ATR below
    assert not has_hvn_support(profile, close=109.0, atr=3.0)


def test_flat_profile_has_no_hvn_anywhere():
    """Uniform volume everywhere: no bucket stands out, so no 'support'."""
    prices = list(np.linspace(90, 110, 60))
    volumes = [1_000.0] * 60
    profile = build_profile(hourly_frame(prices, volumes), buckets=20)
    assert not has_hvn_support(profile, close=100.0, atr=3.0)


def test_classify_partitions_above_below_poc():
    prices = list(np.linspace(90, 110, 40)) + [100.0] * 20
    volumes = [1_000.0] * 40 + [50_000.0] * 20
    profile = build_profile(hourly_frame(prices, volumes), buckets=20)
    hi = classify_entry(profile, close=105.0, atr=2.0)
    lo = classify_entry(profile, close=95.0, atr=2.0)
    assert hi["above_poc"] and not hi["below_poc"]
    assert lo["below_poc"] and not lo["above_poc"]


def test_passes_absolute_floor_and_margin():
    live = {"train": {"n": 400, "avg": 0.2, "win_rate": 55.0, "profit_factor": 1.3},
            "valid": {"n": 400, "avg": 0.2, "win_rate": 55.0, "profit_factor": 1.3}}

    def row(n, avg):
        s = {"win_rate": 55.0, "profit_factor": 1.3}
        return {"train": {"n": n, "avg": avg, **s}, "valid": {"n": n, "avg": avg, **s}}

    assert _passes(row(MIN_HALF_TRADES, 0.2 + ADOPT_MARGIN_PCT), live)
    assert not _passes(row(MIN_HALF_TRADES - 1, 0.9), live)
    assert not _passes(row(400, 0.2 + ADOPT_MARGIN_PCT - 0.01), live)


def test_render_names_cells_and_tier1_caveats():
    s = {"n": 200, "win_rate": 55.0, "avg": 0.3, "profit_factor": 1.3}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s)}
                  for k, lbl in CELLS],
        "symbols_used": ["AAA"], "covered_entries": 321,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "321" in text
    assert "WEAKER" in text  # the 1y/1y caveat is stated
    assert "No volume-profile subset passes" in text
