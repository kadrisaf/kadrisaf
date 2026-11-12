"""Tests for the market-regime gate study."""

from datetime import date

import numpy as np
import pandas as pd

from halal_trader.tune_regime import (
    CELLS,
    TREND_SMA,
    VOL_LOOKBACK,
    RegimeLookup,
    _passes,
    regime_frame,
    render_markdown,
)
from halal_trader.tune_entrygates import ADOPT_MARGIN_PCT


def spy_frame(closes):
    idx = pd.date_range("2022-01-03", periods=len(closes), freq="B")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c * 1.005, "Low": c * 0.995, "Close": c,
         "Volume": 1_000_000.0},
        index=idx,
    )


def test_trend_flag_follows_the_200d_sma():
    n = TREND_SMA + 100
    closes = list(np.linspace(100, 120, n))          # steady rise: above SMA
    closes += [60.0] * 30                            # crash far below the SMA
    rf = regime_frame(spy_frame(closes))
    usable = rf.dropna()
    assert bool(usable["trend"].iloc[n - 1 - (len(rf) - len(usable))]) is True
    assert bool(usable["trend"].iloc[-1]) is False


def test_calm_flag_spikes_off_in_high_vol():
    rng = np.random.default_rng(2)
    n = VOL_LOOKBACK + 300
    quiet = rng.normal(0.0002, 0.004, n)             # ~6% annualized vol
    wild = rng.normal(0.0, 0.04, 30)                 # ~60% annualized vol
    closes = 100 * np.cumprod(1 + np.concatenate([quiet, wild]))
    rf = regime_frame(spy_frame(list(closes)))
    usable = rf.dropna()
    assert bool(usable["calm"].iloc[-1]) is False    # wild tail: not calm
    assert bool(usable["calm"].iloc[-40]) is True    # quiet stretch: calm


def test_lookup_uses_most_recent_prior_spy_day():
    n = TREND_SMA + 60
    rf = regime_frame(spy_frame(list(np.linspace(100, 120, n))))
    lookup = RegimeLookup(rf)
    last_spy_day = lookup.dates[-1]
    weekend = date(2100, 1, 2)                       # far after coverage
    assert lookup.flags(weekend) == lookup.flags(last_spy_day)
    assert lookup.flags(date(1990, 1, 1)) is None    # before coverage


def test_passes_same_rule_as_entrygates():
    live = {"train": {"n": 1400, "avg": 0.3, "win_rate": 55.0, "profit_factor": 1.3},
            "valid": {"n": 1400, "avg": 0.3, "win_rate": 55.0, "profit_factor": 1.3}}

    def row(n_train, n_valid, avg):
        s = {"win_rate": 55.0, "profit_factor": 1.3}
        return {"train": {"n": n_train, "avg": avg, **s},
                "valid": {"n": n_valid, "avg": avg, **s}}

    assert _passes(row(1000, 800, 0.3 + ADOPT_MARGIN_PCT), live)
    assert not _passes(row(1000, 600, 0.3 + ADOPT_MARGIN_PCT), live)  # < 0.5x valid n
    assert not _passes(row(100, 800, 0.9), live)                      # train floor
    assert not _passes(row(1000, 800, 0.3 + ADOPT_MARGIN_PCT - 0.01), live)


def test_render_lists_cells_and_default_verdict():
    s = {"n": 300, "win_rate": 55.0, "avg": 0.3, "profit_factor": 1.3}
    result = {
        "cells": [{"key": k, "label": lbl, "train": dict(s), "valid": dict(s)}
                  for k, lbl in CELLS],
        "symbols_used": ["AAA"], "uncovered_entries": 4, "years": 3,
    }
    text = render_markdown(result, date(2026, 11, 12))
    for _k, lbl in CELLS:
        assert lbl in text
    assert "No regime gate passes" in text
    assert "4 entries predated SPY regime coverage" in text
