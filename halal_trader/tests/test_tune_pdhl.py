"""Tests for the previous-day high/low split-sample test."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_pdhl import CELLS, _passes, pdhl_trades, render_markdown
from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT, gate_features


def frame(n=200, seed=3):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.012, n)))
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": close.values * (1 + rng.normal(0, 0.002, n)),
            "High": close.values * 1.012,
            "Low": close.values * 0.988,
            "Close": close.values,
            "Volume": rng.integers(2_000_000, 6_000_000, n).astype(float),
        },
        index=idx,
    )


def all_true(df):
    return pd.Series(True, index=df.index)


def test_pdh_confirmation_takes_subset_of_live_entries():
    df = frame()
    feats = gate_features(df)
    base = pdhl_trades(df, feats, all_true(df), 60, len(df), 0.1, False, False)
    pdh = pdhl_trades(df, feats, all_true(df), 60, len(df), 0.1, True, False)
    assert 0 < len(pdh["returns"]) <= len(base["returns"])


def test_pdh_rejects_closes_below_prev_high():
    """Flat tape where every close sits inside the previous day's range:
    the PDH confirmation must take zero trades."""
    n = 120
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    df = pd.DataFrame(
        {"Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 100.0,
         "Volume": 3_000_000.0},
        index=idx,
    )
    feats = gate_features(df)
    pdh = pdhl_trades(df, feats, all_true(df), 60, n, 0.1, True, False)
    assert pdh["returns"] == []


def test_pdl_stop_skips_entries_with_no_defined_risk():
    """A huge gap down leaves yesterday's low above today's close: the PDL
    cell must skip that entry and count it."""
    n = 80
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = np.full(n, 100.0)
    close[70:] = 80.0  # gap down at bar 70, then flat
    df = pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": 3_000_000.0},
        index=idx,
    )
    feats = gate_features(df)
    mask = pd.Series(False, index=df.index)
    mask.iloc[70] = True  # entry day right after the gap: prev low = 99 > close 80
    res = pdhl_trades(df, feats, mask, 60, n, 0.1, False, True)
    assert res["returns"] == []
    assert res["skipped_no_risk"] == 1


def test_pdl_stop_changes_outcomes_vs_atr_stop():
    df = frame(seed=11)
    feats = gate_features(df)
    atr = pdhl_trades(df, feats, all_true(df), 60, len(df), 0.1, False, False)
    pdl = pdhl_trades(df, feats, all_true(df), 60, len(df), 0.1, False, True)
    assert pdl["returns"] and atr["returns"] != pdl["returns"]


def test_passes_same_rule_as_entrygates():
    live = {"train": {"n": 500, "avg": 0.3, "win_rate": 60.0, "profit_factor": 1.5},
            "valid": {"n": 400, "avg": 0.3, "win_rate": 60.0, "profit_factor": 1.5}}
    good = {"train": {"n": 500, "avg": 0.3 + ADOPT_MARGIN_PCT, "win_rate": 60.0, "profit_factor": 1.5},
            "valid": {"n": 400, "avg": 0.3 + ADOPT_MARGIN_PCT, "win_rate": 60.0, "profit_factor": 1.5}}
    one_half = {"train": good["train"], "valid": {"n": 400, "avg": 0.3, "win_rate": 60.0, "profit_factor": 1.5}}
    assert _passes(good, live)
    assert not _passes(one_half, live)


def test_render_lists_all_cells_and_rule():
    s = {"n": 200, "win_rate": 60.0, "avg": 0.4, "profit_factor": 1.5}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s),
                   "skipped_no_risk": 2} for k, lbl in CELLS],
        "symbols_used": ["AAA"], "years": 3,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "No variant passes the pre-registered rule" in text
    assert "skipped 2 entries" in text
