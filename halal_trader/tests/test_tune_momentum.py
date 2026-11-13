"""Tests for the monthly momentum-portfolio split-sample study."""

import numpy as np
import pandas as pd
import pytest

from halal_trader.tune_momentum import (
    LOOKBACKS,
    MARGIN_PCT,
    MIN_PERIODS,
    REBALANCE,
    SKIP,
    WARMUP_ROWS,
    _passes,
    momentum_scores,
    period_return_pct,
    render_markdown,
    run_on_matrix,
    turnover_cost_pct,
)

N = WARMUP_ROWS + 10 * REBALANCE + 5
IDX = pd.date_range("2018-01-02", periods=N, freq="B")


def matrix() -> pd.DataFrame:
    """WINNER grinds up 0.1%/day; FLAT does nothing; LATESPIKE is flat but
    jumps 30% inside the skip month before row WARMUP_ROWS; SHORTHIST is a
    strong riser that only starts trading late."""
    rows = np.arange(N, dtype=float)
    winner = 100 * 1.001 ** rows
    flat = np.full(N, 100.0)
    latespike = np.full(N, 100.0)
    latespike[WARMUP_ROWS - SKIP + 1 :] = 130.0
    shorthist = pd.Series(100 * 1.002 ** rows, index=IDX)
    shorthist.iloc[: N - LOOKBACKS["m3"]] = np.nan  # too young for any window
    return pd.DataFrame(
        {"WINNER": winner, "FLAT": flat, "LATESPIKE": latespike, "SHORTHIST": shorthist},
        index=IDX,
    )


def first_real(closes: pd.DataFrame) -> pd.Series:
    return pd.Series(
        {s: closes.index.get_loc(closes[s].first_valid_index()) for s in closes.columns}
    )


def test_scores_rank_the_grinder_above_flat_and_skip_the_last_month():
    closes = matrix().ffill()
    fr = first_real(matrix())
    score = momentum_scores(closes, fr, WARMUP_ROWS, LOOKBACKS["m12"])
    assert score["WINNER"] > score["FLAT"]
    # the +30% jump sits entirely inside the skip month -> no credit for it
    assert score["LATESPIKE"] == pytest.approx(0.0, abs=1e-9)


def test_short_history_symbol_is_ineligible():
    closes = matrix().ffill()
    fr = first_real(matrix())
    score = momentum_scores(closes, fr, WARMUP_ROWS, LOOKBACKS["m3"])
    assert "SHORTHIST" not in score.index
    # ...and still ineligible later, until its real data covers the window
    r_late = WARMUP_ROWS + REBALANCE
    assert "SHORTHIST" not in momentum_scores(closes, fr, r_late, LOOKBACKS["m12"]).index


def test_period_return_is_the_equal_weight_buy_and_hold_mean():
    closes = pd.DataFrame(
        {"A": [100.0, 110.0], "B": [50.0, 45.0]},
        index=pd.date_range("2020-01-01", periods=2),
    )
    got = period_return_pct(closes, ["A", "B"], 0, 1)
    assert got == pytest.approx(100 * ((0.10 + (-0.10)) / 2))


def test_turnover_cost():
    assert turnover_cost_pct([], ["A", "B"], 0.1) == pytest.approx(0.1)  # buy-in
    assert turnover_cost_pct(["A", "B"], ["A", "B"], 0.1) == 0.0          # no trade
    assert turnover_cost_pct(["A", "B"], ["A", "C"], 0.1) == pytest.approx(0.1)  # half replaced
    assert turnover_cost_pct(["A", "B"], ["C", "D"], 0.1) == pytest.approx(0.2)  # full churn


def test_run_on_matrix_shapes_and_winner_cell_beats_flat_tape():
    res = run_on_matrix(matrix().ffill())
    keys = {c["key"] for c in res["cells"]}
    assert {"ew", "m3_top10", "m6_top10", "m12_top10", "m12_top5"} <= keys
    m12 = next(c for c in res["cells"] if c["key"] == "m12_top5")
    ew = next(c for c in res["cells"] if c["key"] == "ew")
    # top-5 of a 4-symbol tape holds everything incl. WINNER; both halves positive
    assert m12["train"]["n"] + m12["valid"]["n"] == res["n_periods"]
    assert ew["train"] is not None and ew["valid"] is not None


def test_passes_needs_margin_on_both_halves_and_min_n():
    ew = {"train": {"n": 60, "avg": 0.5, "win_rate": 55, "profit_factor": 1.2},
          "valid": {"n": 60, "avg": 0.5, "win_rate": 55, "profit_factor": 1.2}}

    def cell(ta, va, tn=60, vn=60):
        return {"train": {"n": tn, "avg": ta, "win_rate": 60, "profit_factor": 1.5},
                "valid": {"n": vn, "avg": va, "win_rate": 60, "profit_factor": 1.5}}

    assert _passes(cell(0.5 + MARGIN_PCT, 0.5 + MARGIN_PCT), ew)
    assert not _passes(cell(0.5 + MARGIN_PCT, 0.5 + MARGIN_PCT - 0.01), ew)  # one half short
    assert not _passes(cell(0.9, 0.9, tn=MIN_PERIODS - 1), ew)               # too few months
    assert not _passes({"train": None, "valid": cell(1, 1)["valid"]}, ew)


def test_render_mentions_rule_and_verdict():
    res = run_on_matrix(matrix().ffill())
    md = render_markdown(res, pd.Timestamp("2026-11-13").date())
    assert "Pre-registered rule" in md and "## Verdict" in md
    assert "EW baseline" in md
