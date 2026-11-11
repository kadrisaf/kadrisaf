"""Tests for the entry-gate split-sample test."""

import numpy as np
import pandas as pd
import pytest

from halal_trader.tune_entrygates import (
    ADOPT_MARGIN_PCT,
    GATES,
    LIVE_GATE,
    Gate,
    _passes,
    entry_mask,
    gate_features,
    render_markdown,
    trades_from_mask,
)
from halal_trader.tune import feature_frame


def synthetic_frame(n=400, seed=7):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.015, n)))
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": close.values * (1 + rng.normal(0, 0.003, n)),
            "High": close.values * 1.01,
            "Low": close.values * 0.99,
            "Close": close.values,
            "Volume": rng.integers(2_000_000, 6_000_000, n).astype(float),
        },
        index=idx,
    )


def test_live_gate_mask_matches_tune_feature_frame():
    """The 'live' variant must reproduce tune.py's trend definition exactly."""
    df = synthetic_frame()
    feats = gate_features(df)
    old = feature_frame(df)
    live_trend = feats["above_fast"] & feats["fast_over_slow"] & feats["fast_rising"]
    pd.testing.assert_series_equal(live_trend, old["trend"], check_names=False)


def test_looser_gates_are_supersets():
    """Every entry the live gate takes, the looser gates must also take."""
    df = synthetic_frame()
    feats = gate_features(df)
    live = entry_mask(feats, Gate("live", "live", 65.0))
    no50 = entry_mask(feats, Gate("no50", "no50", 65.0))
    above20 = entry_mask(feats, Gate("above20", "above20", 65.0))
    assert not (live & ~no50).any()
    assert not (no50 & ~above20).any()
    # and the RSI-60 cap is a subset of the RSI-65 one
    live60 = entry_mask(feats, Gate("live_rsi60", "live", 60.0))
    assert not (live60 & ~live).any()


def test_edge_split_partitions_the_live_gate():
    """RSI 45-60 and 60-65 entries together = the live gate's entries
    (as masks; the non-overlap walk may then pick different subsets)."""
    df = synthetic_frame()
    feats = gate_features(df)
    live = entry_mask(feats, LIVE_GATE)
    lo = entry_mask(feats, LIVE_GATE, rsi_min_override=45.0, rsi_max_override=60.0)
    hi = entry_mask(feats, LIVE_GATE, rsi_min_override=60.0, rsi_max_override=65.0)
    # between() is inclusive on both ends, so RSI == 60.0 may appear in both
    assert ((lo | hi) == live).all()


def test_trades_respect_half_boundary():
    df = synthetic_frame()
    feats = gate_features(df)
    mask = pd.Series(True, index=df.index)
    mid = len(df) // 2
    a = trades_from_mask(df, feats, mask, 60, mid, 0.1)
    b = trades_from_mask(df, feats, mask, mid, len(df), 0.1)
    assert len(a) > 0 and len(b) > 0


def test_passes_requires_margin_on_both_halves():
    live = {"train": {"n": 500, "avg": 0.3, "win_rate": 60.0, "profit_factor": 1.5},
            "valid": {"n": 400, "avg": 0.3, "win_rate": 60.0, "profit_factor": 1.5}}

    def row(train_avg, valid_avg, n=500, vn=400):
        return {"train": {"n": n, "avg": train_avg, "win_rate": 60.0, "profit_factor": 1.5},
                "valid": {"n": vn, "avg": valid_avg, "win_rate": 60.0, "profit_factor": 1.5}}

    assert _passes(row(0.3 + ADOPT_MARGIN_PCT, 0.3 + ADOPT_MARGIN_PCT), live)
    assert not _passes(row(0.3 + ADOPT_MARGIN_PCT, 0.3 + ADOPT_MARGIN_PCT - 0.01), live)
    assert not _passes(row(0.3 + ADOPT_MARGIN_PCT - 0.01, 0.9), live)
    assert not _passes(row(0.9, 0.9, n=100), live)          # too few train trades
    assert not _passes(row(0.9, 0.9, vn=100), live)         # valid n below share floor


def test_render_states_verdict_and_rule():
    s = {"n": 200, "win_rate": 60.0, "avg": 0.4, "profit_factor": 1.5}
    result = {
        "gates": [{"gate": g, "train": dict(s), "valid": dict(s)} for g in GATES],
        "edges": {"edge_45_60": {"train": dict(s), "valid": dict(s)},
                  "edge_60_65": {"train": dict(s), "valid": dict(s)}},
        "symbols_used": ["AAA"], "years": 3,
    }
    from datetime import date
    text = render_markdown(result, date(2026, 11, 11))
    assert "No variant passes the pre-registered rule" in text  # equal avgs: no margin
    assert "DHI zone" in text
    assert "Pre-registered rule" in text
