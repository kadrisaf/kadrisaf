"""Tests for the vol_creep gate split-sample test (synthetic, no network)."""

import numpy as np
import pandas as pd

from halal_trader.prepop_study import compute_features
from halal_trader.tune import WARMUP, feature_frame
from halal_trader.tune_volcreep import (
    render_markdown,
    trades_with_gate,
    vol_creep_series,
)


def make_frame(n=300, seed=11):
    """Uptrending frame with volume spikes so the LIVE mask fires sometimes."""
    rng = np.random.default_rng(seed)
    drift = np.cumprod(1 + rng.normal(0.002, 0.01, n))
    close = 100.0 * drift
    vol = rng.uniform(0.9e7, 1.1e7, n)
    vol[::7] *= 1.8  # periodic volume spikes to clear the 1.2x rel-vol gate
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({
        "Open": close * 0.999, "High": close * 1.012, "Low": close * 0.988,
        "Close": close, "Volume": vol,
    }, index=idx)


def test_vol_creep_matches_prepop_formula():
    frame = make_frame()
    ours = vol_creep_series(frame)
    theirs = compute_features(frame)["vol_creep"]
    tail_a = ours.iloc[WARMUP:].astype(float)
    tail_b = theirs.iloc[WARMUP:].astype(float)
    assert np.allclose(tail_a, tail_b, equal_nan=True)


def test_no_gate_equals_baseline_and_gates_are_subsets():
    frame = make_frame()
    feats = feature_frame(frame)
    creep = vol_creep_series(frame)
    base = trades_with_gate(frame, feats, creep, None, WARMUP, len(frame), 0.1)
    assert len(base) > 0, "synthetic frame should produce some LIVE trades"
    prev_n = len(base)
    for thr in [1.0, 1.1, 1.3, 2.0]:
        gated = trades_with_gate(frame, feats, creep, thr, WARMUP, len(frame), 0.1)
        assert len(gated) <= prev_n, f"thr={thr} not monotone"
        assert set(np.round(gated, 6)) <= set(np.round(base, 6)) or len(gated) == 0
        prev_n = len(gated)


def test_impossible_gate_yields_no_trades():
    frame = make_frame()
    feats = feature_frame(frame)
    creep = vol_creep_series(frame)
    assert trades_with_gate(frame, feats, creep, 99.0, WARMUP, len(frame), 0.1) == []


def test_halves_do_not_overlap():
    frame = make_frame()
    feats = feature_frame(frame)
    creep = vol_creep_series(frame)
    mid = (len(frame) + WARMUP) // 2
    first = trades_with_gate(frame, feats, creep, None, WARMUP, mid, 0.1)
    second = trades_with_gate(frame, feats, creep, None, mid, len(frame), 0.1)
    both = trades_with_gate(frame, feats, creep, None, WARMUP, len(frame), 0.1)
    # splitting can only lose trades at the boundary, never create them
    assert len(first) + len(second) >= len(both) - 1
    assert len(first) + len(second) <= len(both) + 1


def test_render_contains_decision_rule_and_baseline():
    result = {
        "rows": [
            {"threshold": None,
             "train": {"n": 100, "win_rate": 55.0, "avg": 0.2, "profit_factor": 1.2},
             "valid": {"n": 90, "win_rate": 54.0, "avg": 0.15, "profit_factor": 1.1}},
            {"threshold": 1.2, "train": None, "valid": None},
        ],
        "symbols_used": ["AAA", "BBB"], "years": 3,
    }
    text = render_markdown(result, pd.Timestamp("2026-11-09").date())
    assert "LIVE baseline (no gate)" in text
    assert "vol_creep >= 1.2x" in text
    assert "'No change' is a good result" in text
    assert "0 | -- | -- | --" in text  # empty cells render, not crash
