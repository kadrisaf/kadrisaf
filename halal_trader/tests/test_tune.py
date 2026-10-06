from datetime import date

from halal_trader.data_provider import CompanyFundamentals, StaticProvider
from halal_trader.tune import LIVE, ParamSet, feature_frame, render_markdown, run_tune, trades_for
from halal_trader.tests.test_signals import UPTREND_PATTERN, _make_ohlcv


def _fund(symbol):
    return CompanyFundamentals(
        symbol=symbol, sector="Technology", industry="Software", market_cap=1e11,
        total_debt=1e9, cash_and_short_term_investments=1e9, receivables=1e9, currency="USD",
    )


def _df():
    df = _make_ohlcv(UPTREND_PATTERN, n=400, last_volume_multiple=1.0)
    df.loc[df.index[::3], "Volume"] = 2_000_000.0
    return df


def test_feature_frame_matches_evaluate_signal_on_the_last_bar():
    from halal_trader.signals import evaluate_signal

    df = _df()
    feats = feature_frame(df)
    sig = evaluate_signal("TST", df)
    assert round(float(feats["rsi"].iloc[-1]), 2) == sig.rsi
    assert round(float(feats["atr"].iloc[-1]), 4) == sig.atr
    assert round(float(feats["rel_vol"].iloc[-1]), 2) == sig.relative_volume
    # the last bar qualifies on the live thresholds iff evaluate_signal says so
    mask_ok = bool(
        feats["trend"].iloc[-1]
        and LIVE.rsi_min <= feats["rsi"].iloc[-1] <= LIVE.rsi_max
        and feats["rel_vol"].iloc[-1] >= LIVE.min_rel_vol
    )
    assert mask_ok == sig.qualifies


def test_train_and_validation_windows_do_not_overlap():
    df = _df()
    feats = feature_frame(df)
    mid = (len(df) + 60) // 2
    train = trades_for(df, feats, LIVE, 60, mid, 0.0)
    valid = trades_for(df, feats, LIVE, mid, len(df), 0.0)
    both = trades_for(df, feats, LIVE, 60, len(df), 0.0)
    assert train and valid
    assert len(both) <= len(train) + len(valid) + 1  # at most one trade straddles the split


def test_run_tune_always_reports_live_and_applies_min_trade_floor():
    provider = StaticProvider({"UP": _fund("UP")}, {"UP": _df()})
    result = run_tune(
        ["UP"], years=1, provider=provider,
        grid=[ParamSet(40, 70, 1.0, 1.5, 2.25, 3)], min_train_trades=10_000,
    )
    assert result["live"]["params"] == LIVE
    assert result["winners"] == []  # floor filtered the only grid cell out
    md = render_markdown(result, date(2026, 10, 6))
    assert "LIVE:" in md and "Adopt nothing unless" in md


def test_run_tune_ranks_winners_by_train_avg():
    provider = StaticProvider({"UP": _fund("UP")}, {"UP": _df()})
    grid = [ParamSet(40, 70, 1.0, 1.5, 2.25, 3), ParamSet(40, 70, 1.0, 2.0, 3.0, 8)]
    result = run_tune(["UP"], years=1, provider=provider, grid=grid, min_train_trades=1)
    avgs = [r["train"]["avg"] for r in result["winners"]]
    assert avgs == sorted(avgs, reverse=True)
