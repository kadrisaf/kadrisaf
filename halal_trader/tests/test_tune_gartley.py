"""Tests for the Gartley harmonic-pattern study."""

from datetime import date

import pandas as pd

from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT, gate_features
from halal_trader.tune_gartley import (
    CELLS,
    MIN_HALF_TRADES,
    _passes,
    gartley_entries,
    render_markdown,
    zigzag_pivots,
)


def frame(closes, volume=3_000_000.0):
    """OHLC with zero wicks so pivot prices equal the closes exactly."""
    c = pd.Series(closes, dtype=float)
    idx = pd.date_range("2024-01-02", periods=len(c), freq="B")
    return pd.DataFrame(
        {"Open": c.values, "High": c.values, "Low": c.values,
         "Close": c.values, "Volume": volume},
        index=idx,
    )


# A textbook bullish Gartley under a 5% ZigZag, after 20 warmup bars:
#   X=100 (idx 22), A=110 (idx 26), B=103.82 (0.618 retrace, idx 28),
#   C=109.2 (0.87 of A->B, idx 30), D level = 110 - 0.786*10 = 102.14,
#   first touch at idx 33 (close 102.0).
TEXTBOOK = (
    [106.0] * 20                      # warmup so ATR exists at entry
    + [103.0, 101.5, 100.0]           # decline into X (confirms prior high)
    + [102.0, 105.0, 108.0, 110.0]    # X->A (X confirmed at 105)
    + [107.0, 103.82]                 # A->B (A confirmed at 103.82)
    + [106.0, 109.2]                  # B->C (B confirmed at 109.2)
    + [106.0, 103.5, 102.0]           # C->D (C confirmed at 103.5, touch at 102.0)
    + [104.0] * 10                    # room for exits
)
TOUCH_IDX = 33


def test_zigzag_confirmation_is_causal():
    df = frame(TEXTBOOK)
    for p in zigzag_pivots(df, 0.05):
        assert p.confirmed_at > p.idx


def test_textbook_gartley_detected_at_d_touch():
    df = frame(TEXTBOOK)
    feats = gate_features(df)
    entries = gartley_entries(df, feats, threshold=0.05, b_tol=0.06)
    assert entries == [TOUCH_IDX]


def test_break_below_x_invalidates():
    closes = list(TEXTBOOK)
    closes[TOUCH_IDX] = 99.0  # D leg crashes through X=100
    df = frame(closes)
    feats = gate_features(df)
    assert gartley_entries(df, feats, threshold=0.05, b_tol=0.06) == []


def test_b_tolerance_separates_strict_and_loose():
    # B=103.0 -> retrace 0.70 (off by 0.082): loose accepts, strict rejects.
    closes = list(TEXTBOOK)
    closes[28] = 103.0   # B
    closes[30] = 108.2   # C: 0.736 of the new A->B, confirms B (>103*1.05)
    closes[32] = 102.5   # confirms C (<108.2*0.95), above D level
    df = frame(closes)
    feats = gate_features(df)
    assert gartley_entries(df, feats, threshold=0.05, b_tol=0.06) == []
    assert gartley_entries(df, feats, threshold=0.05, b_tol=0.12) == [TOUCH_IDX]


def test_no_pattern_in_flat_tape():
    df = frame([100.0] * 80)
    feats = gate_features(df)
    assert gartley_entries(df, feats, threshold=0.05, b_tol=0.12) == []


def test_illiquid_names_gated_out():
    df = frame(TEXTBOOK, volume=1_000.0)
    feats = gate_features(df)
    assert gartley_entries(df, feats, threshold=0.05, b_tol=0.06) == []


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
    s = {"n": 30, "win_rate": 55.0, "avg": 0.3, "profit_factor": 1.3}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s)}
                  for k, lbl in CELLS],
        "symbols_used": ["AAA"], "years": 3,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "No Gartley variant passes" in text
