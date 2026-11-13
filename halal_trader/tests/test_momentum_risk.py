"""Tests for the momentum sleeve risk metrics (descriptive only)."""

import pandas as pd
import pytest

from halal_trader.momentum_risk import (
    equity,
    longest_underwater_months,
    max_drawdown_pct,
    red_flags,
    render_markdown,
    risk_profile,
    run_risk,
    worst_rolling_12m_pct,
)


def test_max_drawdown_hand_case():
    # 100 -> 110 -> 88 -> 99: peak 110, trough 88 => -20%
    assert max_drawdown_pct([10.0, -20.0, 12.5]) == pytest.approx(-20.0)


def test_underwater_counts_months_until_peak_regained():
    # drop, two months below the old peak, then recovery above it
    rets = [10.0, -10.0, 1.0, 15.0]
    assert longest_underwater_months(rets) == 2
    assert longest_underwater_months([1.0, 2.0, 3.0]) == 0


def test_worst_rolling_12m():
    rets = [-2.0] * 12 + [5.0] * 12
    worst = worst_rolling_12m_pct(rets)
    assert worst == pytest.approx(100 * (0.98 ** 12 - 1), abs=0.01)
    assert worst_rolling_12m_pct([1.0] * 11) is None


def test_risk_profile_relative_and_conditional_fields():
    ew = [1.0, -5.0, 2.0, -3.0] * 6
    cell = [2.0, -8.0, 3.0, -4.0] * 6
    p = risk_profile(cell, ew)
    assert p["worst_month"] == -8.0
    # avg in EW-down months = mean of cell where ew<0 = (-8-4)/2
    assert p["avg_in_ew_down"] == pytest.approx(-6.0)
    assert p["rel_dd_vs_ew"] < 0  # falls behind EW in the bad months


def test_red_flags_fire_on_preregistered_thresholds():
    ew = {"max_dd": -20.0}
    assert red_flags({"max_dd": -31.0, "worst_month": -10.0, "underwater": 5}, ew)
    assert red_flags({"max_dd": -25.0, "worst_month": -20.0, "underwater": 5}, ew)
    assert red_flags({"max_dd": -25.0, "worst_month": -10.0, "underwater": 25}, ew)
    assert not red_flags({"max_dd": -25.0, "worst_month": -19.9, "underwater": 24}, ew)


def test_run_risk_and_render_from_backtest_result():
    dates = pd.date_range("2020-01-31", periods=30, freq="ME")
    base = [1.0, -2.0, 3.0] * 10
    result = {
        "returns": {
            "ew": base,
            "m3_top10": [r * 1.5 for r in base],
            "m6_top10": base,
            "m12_top10": base,
            "m12_top5": [r * 2.0 for r in base],
        },
        "n_periods": 30,
        "period_dates": list(dates),
        "symbols_used": ["A", "B"],
        "years": 10,
    }
    risk = run_risk(result)
    assert [r["key"] for r in risk["rows"]][0] == "ew"
    ew_row = risk["rows"][0]
    assert ew_row["flags"] == []  # baseline never flags itself
    md = render_markdown(risk, pd.Timestamp("2026-11-13").date())
    assert "Red flags" in md and "Max lag behind EW" in md
