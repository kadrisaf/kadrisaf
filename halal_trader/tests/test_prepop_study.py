"""Tests for the pre-pop precursor study (synthetic data, no network)."""

import numpy as np
import pandas as pd
import pytest

from halal_trader.prepop_study import (
    FEATURE_CLAIMS,
    LABEL_HORIZON_DAYS,
    MIN_PRIOR_BARS,
    Verdict,
    build_report,
    build_table,
    compute_features,
    judge,
    label_pops,
    quintile_lift,
    run_study,
)


def make_frame(n=400, base=100.0, seed=7):
    rng = np.random.default_rng(seed)
    close = base * np.cumprod(1 + rng.normal(0, 0.005, n))
    idx = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


def with_pop(frame, at, pct=10.0):
    f = frame.copy()
    f.iloc[at:, f.columns.get_loc("Close")] *= 1 + pct / 100.0
    f.iloc[at, f.columns.get_loc("High")] = f["Close"].iloc[at] * 1.01
    return f


# ---------------------------------------------------------------- labelling

def test_label_true_only_in_window_before_pop():
    frame = with_pop(make_frame(), at=200, pct=10.0)
    labels = label_pops(frame)
    # the HORIZON days strictly before the pop day are labelled True
    for k in range(1, LABEL_HORIZON_DAYS + 1):
        assert labels.iloc[200 - k] == True  # noqa: E712
    # the pop day itself looks forward, not at itself
    assert labels.iloc[200] == False  # noqa: E712
    # a day well before the window is False
    assert labels.iloc[150] == False  # noqa: E712


def test_label_threshold_is_exclusive_of_smaller_moves():
    frame = with_pop(make_frame(), at=200, pct=5.0)  # below the 8% bar
    labels = label_pops(frame)
    assert not labels.iloc[180:220].fillna(False).any()


def test_label_tail_rows_are_nan_not_false():
    labels = label_pops(make_frame())
    assert labels.iloc[-LABEL_HORIZON_DAYS:].isna().all()


# ---------------------------------------------------------------- features

def test_features_have_no_lookahead():
    frame = make_frame()
    t = 300
    full = compute_features(frame)
    truncated = compute_features(frame.iloc[: t + 1])
    for col in FEATURE_CLAIMS:
        a, b = full[col].iloc[t], truncated[col].iloc[t]
        assert a == pytest.approx(b, rel=1e-9), f"{col} saw the future"


def test_vol_creep_rises_when_recent_volume_doubles():
    frame = make_frame()
    quiet = compute_features(frame)["vol_creep"].iloc[-1]
    loud = frame.copy()
    loud.iloc[-5:, loud.columns.get_loc("Volume")] = 2_000_000.0
    assert compute_features(loud)["vol_creep"].iloc[-1] == pytest.approx(2 * quiet, rel=1e-6)


def test_early_rows_are_nan():
    feats = compute_features(make_frame())
    assert feats.iloc[:MIN_PRIOR_BARS].isna().all().all()


def test_build_table_drops_short_histories():
    table = build_table({"SHORT": make_frame(n=40), "OK": make_frame(n=400)})
    assert set(table["symbol"].unique()) == {"OK"}


# ---------------------------------------------------------------- lifts

def make_rigged_table(n=4000, seed=3):
    """High vol_creep deterministically predicts the label; others are noise."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
    vol_creep = rng.uniform(0.5, 2.0, n)
    label = (vol_creep > 1.7).astype(float)  # top of range -> pop
    table = pd.DataFrame({
        "date": dates, "symbol": "X",
        "vol_creep": vol_creep,
        "squeeze": rng.uniform(0.5, 1.5, n),
        "drift5": rng.normal(0, 3, n),
        "dist_high": rng.uniform(-50, 0, n),
        "rsi": rng.uniform(20, 80, n),
        "label": label,
    })
    return table


def test_quintile_lift_finds_rigged_signal_in_both_halves():
    table = make_rigged_table()
    train_mask = table["date"] <= table["date"].quantile(0.5)
    res = quintile_lift(table, "vol_creep", train_mask)
    v = judge("vol_creep", res)
    assert v.survives
    assert v.train_lift > 2 and v.test_lift > 2


def test_quintile_lift_rejects_noise_feature():
    table = make_rigged_table()
    train_mask = table["date"] <= table["date"].quantile(0.5)
    res = quintile_lift(table, "squeeze", train_mask)
    assert not judge("squeeze", res).survives


def test_quintile_edges_come_from_train_half_only():
    table = make_rigged_table()
    # shift the test half's feature far away: train-derived edges must bin it
    # into the extreme bucket rather than re-fitting
    test_rows = table["date"] > table["date"].quantile(0.5)
    table.loc[test_rows, "vol_creep"] += 100.0
    table.loc[test_rows, "label"] = 1.0
    train_mask = ~test_rows
    res = quintile_lift(table, "vol_creep", train_mask)
    qs = res["test"]["quintiles"]
    assert qs[-1]["n"] == int(test_rows.sum())  # everything landed in the top bin
    assert all(q["n"] == 0 for q in qs[:-1])


def test_quintile_lift_needs_enough_rows():
    table = make_rigged_table(n=600)  # halves of 300 < 500 minimum
    train_mask = table["date"] <= table["date"].quantile(0.5)
    assert quintile_lift(table, "vol_creep", train_mask) is None


# ---------------------------------------------------------------- report

def test_report_states_no_survivor_conclusion():
    table = make_rigged_table()
    table["label"] = 0.0
    table.loc[::10, "label"] = 1.0  # labels independent of every feature,
    # dense enough (10%) that per-quintile sampling noise stays under MIN_LIFT
    results, verdicts, split = run_study(table)
    text = build_report(table, results, verdicts, pd.Timestamp(split).date())
    assert "no precursor survived" in text
    assert "Measurement only" in text


def test_report_names_survivors_and_demands_tune_test():
    table = make_rigged_table()
    results, verdicts, split = run_study(table)
    assert any(v.survives and v.feature == "vol_creep" for v in verdicts)
    text = build_report(table, results, verdicts, pd.Timestamp(split).date())
    assert "vol_creep" in text
    assert "not yet a tradable edge" in text


def test_judge_respects_bottom_side_claims():
    res = {
        "train": {"base": 0.10, "n": 1000,
                  "quintiles": [{"n": 200, "pop_rate": 0.30}] + [{"n": 200, "pop_rate": 0.05}] * 4},
        "test": {"base": 0.10, "n": 1000,
                 "quintiles": [{"n": 200, "pop_rate": 0.25}] + [{"n": 200, "pop_rate": 0.05}] * 4},
    }
    v = judge("squeeze", res)  # claim side: bottom quintile
    assert isinstance(v, Verdict) and v.survives
    assert v.train_lift == pytest.approx(3.0)
