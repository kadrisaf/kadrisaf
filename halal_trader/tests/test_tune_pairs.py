"""Tests for the long-only pairs-divergence study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT, gate_features
from halal_trader.tune_pairs import (
    CELLS,
    CORR_WINDOW,
    MIN_HALF_TRADES,
    _passes,
    build_close_matrix,
    pairs_entries,
    peer_schedule,
    render_markdown,
)

N = 320
DIVERGE_AT = 250  # A drops ~3%/day for two days while B holds


def _closes():
    rng = np.random.default_rng(3)
    base = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, N))
    a = base * np.exp(rng.normal(0, 0.002, N))
    b = base * np.exp(rng.normal(0, 0.002, N))
    c = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, N))  # unrelated walk
    a[DIVERGE_AT:] *= 0.97
    a[DIVERGE_AT + 1:] *= 0.97
    idx = pd.date_range("2024-01-02", periods=N, freq="B")
    return pd.DataFrame({"A": a, "B": b, "C": c}, index=idx)


def _history(close: pd.Series, volume=3_000_000.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": close.values, "High": close.values * 1.005,
         "Low": close.values * 0.995, "Close": close.values, "Volume": volume},
        index=close.index,
    )


CLOSES = _closes()
HISTORIES = {s: _history(CLOSES[s]) for s in CLOSES.columns}
MATRIX = build_close_matrix(HISTORIES)
SCHED = peer_schedule(MATRIX)


def test_peer_schedule_picks_the_correlated_twin():
    assert SCHED["A"], "A has peer segments"
    for _start, _end, peer, corr in SCHED["A"]:
        assert peer == "B"
        assert corr > 0.8  # dips from ~0.95 once the divergence enters the window


def test_schedule_is_causal():
    for sym in SCHED:
        for start, end, _peer, _corr in SCHED[sym]:
            assert start >= CORR_WINDOW
            assert end > start


def test_divergence_triggers_entry_on_laggard_only():
    fa = gate_features(HISTORIES["A"])
    fb = gate_features(HISTORIES["B"])
    ea = pairs_entries("A", HISTORIES["A"], fa, MATRIX, SCHED,
                       z_thr=2.0, corr_floor=0.6)
    eb = pairs_entries("B", HISTORIES["B"], fb, MATRIX, SCHED,
                       z_thr=2.0, corr_floor=0.6)
    assert any(DIVERGE_AT <= e <= DIVERGE_AT + 6 for e in ea)
    assert not any(DIVERGE_AT <= e <= DIVERGE_AT + 6 for e in eb)


def test_crossing_fires_once_per_divergence():
    fa = gate_features(HISTORIES["A"])
    ea = pairs_entries("A", HISTORIES["A"], fa, MATRIX, SCHED,
                       z_thr=2.0, corr_floor=0.6)
    window = [e for e in ea if DIVERGE_AT - 5 <= e <= DIVERGE_AT + 15]
    assert len(window) == 1


def test_corr_floor_excludes_unrelated_names():
    fc = gate_features(HISTORIES["C"])
    ec = pairs_entries("C", HISTORIES["C"], fc, MATRIX, SCHED,
                       z_thr=1.5, corr_floor=0.6)
    assert ec == []


def test_illiquid_names_gated_out():
    thin = _history(CLOSES["A"], volume=1_000.0)
    feats = gate_features(thin)
    assert pairs_entries("A", thin, feats, MATRIX, SCHED,
                         z_thr=2.0, corr_floor=0.6) == []


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
    s = {"n": 200, "win_rate": 55.0, "avg": 0.3, "profit_factor": 1.3}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s)}
                  for k, lbl in CELLS],
        "symbols_used": ["AAA"], "years": 3,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "No pairs variant passes" in text
