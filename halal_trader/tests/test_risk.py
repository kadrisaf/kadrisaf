import numpy as np
import pandas as pd

from halal_trader.data_provider import StaticProvider
from halal_trader.risk import correlation_flags, cost_drag_pct, return_correlation


def _hist(returns, start="2026-01-05"):
    closes = 100 * (1 + pd.Series(returns)).cumprod()
    idx = pd.date_range(start, periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes.values}, index=idx)


def test_identical_return_streams_correlate_fully():
    rng = np.random.default_rng(7)
    r = rng.normal(0, 0.01, 80)
    assert return_correlation(_hist(r), _hist(r)) == 1.0


def test_independent_streams_do_not_flag():
    rng = np.random.default_rng(7)
    a, b = _hist(rng.normal(0, 0.01, 80)), _hist(rng.normal(0, 0.01, 80))
    corr = return_correlation(a, b)
    assert corr is not None and abs(corr) < 0.5


def test_too_little_overlap_returns_none():
    rng = np.random.default_rng(7)
    a = _hist(rng.normal(0, 0.01, 80), start="2025-01-06")
    b = _hist(rng.normal(0, 0.01, 80), start="2026-06-01")
    assert return_correlation(a, b) is None


def test_correlation_flags_only_above_threshold_and_skips_self_and_missing():
    rng = np.random.default_rng(7)
    base = rng.normal(0, 0.01, 80)
    noise = base + rng.normal(0, 0.02, 80)
    provider = StaticProvider({}, {"CAND": _hist(base), "TWIN": _hist(base), "NOISY": _hist(noise)})

    flags = correlation_flags(["CAND"], ["TWIN", "NOISY", "CAND", "GONE"], provider, threshold=0.7)

    assert ("TWIN", 1.0) in flags["CAND"]
    assert all(sym != "CAND" for sym, _ in flags["CAND"])  # self skipped
    assert all(sym != "GONE" for sym, _ in flags["CAND"])  # missing data skipped
    assert all(c >= 0.7 for _, c in flags["CAND"])


def test_cost_drag_is_round_trip_fees_over_position():
    assert cost_drag_pct(500) == 0.4
    assert cost_drag_pct(100) == 2.0


def test_order_ticket_sizes_by_volatility():
    from halal_trader.risk import order_ticket

    # 10% stop distance: (1400 * 2%) / 10 = 2.8 shares (below the 33% cap)
    t = order_ticket(entry=100.0, stop=90.0, target=115.0, portfolio_eur=1400, risk_pct=2.0)
    assert t["shares"] == 2.8
    assert t["position_value"] == 280.0
    assert t["risk_amount"] == 28.0

    # calmer stock (7% stop distance) -> bigger position, same euro risk
    calm = order_ticket(entry=100.0, stop=93.0, target=110.0, portfolio_eur=1400, risk_pct=2.0)
    assert calm["position_value"] > t["position_value"]
    assert calm["risk_amount"] == 28.0


def test_order_ticket_caps_at_max_position_pct():
    from halal_trader import config
    from halal_trader.risk import order_ticket

    # near-zero stop distance -> uncapped size would exceed the per-name cap
    t = order_ticket(entry=100.0, stop=99.5, target=101.0)
    cap = config.PORTFOLIO_EUR * config.MAX_POSITION_PCT_OF_PORTFOLIO / 100
    assert t["capped"] is True
    assert t["position_value"] == round(cap, 2)


def test_order_ticket_rejects_broken_inputs():
    from halal_trader.risk import order_ticket

    assert order_ticket(100.0, 100.0, 110.0) is None  # zero-distance stop
    assert order_ticket(100.0, 105.0, 110.0) is None  # stop above entry
