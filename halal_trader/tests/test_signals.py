import pandas as pd

from halal_trader.signals import evaluate_signal


def _make_ohlcv(pattern, n=90, last_volume_multiple=1.0, base_volume=1_000_000):
    prices = [100.0]
    for i in range(1, n):
        prices.append(prices[-1] * (1 + pattern[i % len(pattern)]))
    closes = pd.Series(prices)
    highs = closes * 1.004
    lows = closes * 0.996
    opens = closes.shift(1).fillna(closes.iloc[0])
    volumes = pd.Series([float(base_volume)] * n)
    volumes.iloc[-1] = base_volume * last_volume_multiple
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": opens.values,
            "High": highs.values,
            "Low": lows.values,
            "Close": closes.values,
            "Volume": volumes.values,
        },
        index=idx,
    )


# A mild, choppy uptrend (repeating +0.6%/-0.4%/+0.5%/-0.4%) with a volume
# spike on the final bar -- engineered to land RSI inside the qualifying
# band while staying in a confirmed uptrend. See git history / prototyping
# notes if these constants ever need re-tuning after a config change.
UPTREND_PATTERN = [0.006, -0.004, 0.005, -0.004]
DOWNTREND_PATTERN = [-0.006, 0.003, -0.005, 0.002]


def test_qualifying_setup_is_flagged_with_defined_risk():
    df = _make_ohlcv(UPTREND_PATTERN, last_volume_multiple=2.2)

    signal = evaluate_signal("TST", df)

    assert signal.qualifies is True
    assert signal.reasons_excluded == []
    assert 45.0 <= signal.rsi <= 65.0
    assert signal.fast_sma > signal.slow_sma
    assert signal.relative_volume >= 1.2
    # Stop is below entry, target is above, with a >= 1.5:1 reward:risk.
    assert signal.stop_loss < signal.last_close < signal.target
    assert signal.reward_risk >= 1.5
    assert signal.max_holding_days == 5
    assert signal.score is not None


def test_downtrend_is_excluded_with_a_reason():
    df = _make_ohlcv(DOWNTREND_PATTERN, last_volume_multiple=1.0)

    signal = evaluate_signal("TST", df)

    assert signal.qualifies is False
    assert signal.score is None
    assert any("uptrend" in reason for reason in signal.reasons_excluded)


def test_low_relative_volume_excludes_an_otherwise_valid_setup():
    df = _make_ohlcv(UPTREND_PATTERN, last_volume_multiple=1.0)

    signal = evaluate_signal("TST", df)

    assert signal.qualifies is False
    assert any("relative volume" in reason for reason in signal.reasons_excluded)


def test_insufficient_history_is_reported_explicitly():
    df = _make_ohlcv(UPTREND_PATTERN, n=10)

    signal = evaluate_signal("TST", df)

    assert signal.qualifies is False
    assert "insufficient price history" in signal.reasons_excluded[0]
