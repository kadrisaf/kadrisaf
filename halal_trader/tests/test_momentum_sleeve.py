"""Tests for the live monthly momentum sleeve (separate long-term product)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from halal_trader.momentum_sleeve import (
    LOOKBACK,
    MIN_BARS,
    SKIP,
    TOP_N,
    Holding,
    load_holdings,
    momentum_score,
    rebalance_plan,
    render_markdown,
    save_holdings,
)


def history(n=260, daily=0.0, last_day="2026-11-12"):
    end = pd.Timestamp(last_day)
    idx = pd.bdate_range(end=end, periods=n)
    close = 100 * (1 + daily) ** np.arange(n)
    return pd.DataFrame({"Close": close}, index=idx)


def test_score_is_three_month_return_ending_one_month_ago():
    df = history(daily=0.001)
    got = momentum_score(df, today=date(2026, 11, 13))
    want = df["Close"].iloc[-1 - SKIP] / df["Close"].iloc[-1 - SKIP - LOOKBACK] - 1
    assert got == pytest.approx(want)


def test_score_ignores_the_skip_month():
    df = history(daily=0.0)
    df.iloc[-SKIP:, df.columns.get_loc("Close")] = 200.0  # spike inside skip month
    assert momentum_score(df) == pytest.approx(0.0)


def test_score_rejects_short_or_stale_history():
    assert momentum_score(history(n=MIN_BARS - 1)) is None
    stale = history(last_day="2026-10-01")
    assert momentum_score(stale, today=date(2026, 11, 13)) is None
    assert momentum_score(stale, today=date(2026, 10, 5)) is not None


def test_rebalance_plan_sell_hold_buy_and_alternates():
    ranked = [(f"S{i}", 1.0 - i * 0.01, 100.0) for i in range(16)]
    held = ["S0", "S3", "GONE1", "GONE2"]
    plan = rebalance_plan(ranked, held)
    assert plan["target"] == [f"S{i}" for i in range(TOP_N)]
    assert plan["sells"] == ["GONE1", "GONE2"]
    assert plan["holds"] == ["S0", "S3"]
    assert plan["buys"] == [f"S{i}" for i in range(TOP_N) if i not in (0, 3)]
    assert plan["alternates"] == ["S10", "S11", "S12", "S13", "S14"]


def test_holdings_roundtrip(tmp_path):
    path = str(tmp_path / "h.csv")
    hs = [Holding("AAPL", "2026-12-01", 210.5, 1.5)]
    save_holdings(hs, path)
    back = load_holdings(path)
    assert back[0].symbol == "AAPL" and back[0].quantity == 1.5
    assert load_holdings(str(tmp_path / "missing.csv")) == []


def test_render_first_buyin_splits_the_value():
    ranked = [(f"S{i}", 0.5 - i * 0.01, 50.0) for i in range(12)]
    plan = rebalance_plan(ranked, [])
    md = render_markdown(ranked, plan, [], date(2026, 11, 13), value_eur=3000.0)
    assert "~300 EUR each" in md
    assert "after 15:30 Berlin" in md
    assert "No stops, no early exits" in md


def test_rank_universe_respects_manual_overrides(monkeypatch, tmp_path):
    """Regression: first live run ranked SHOP top-10 because the sleeve ran
    only the automatic screen; manual excludes must bind here too."""
    import halal_trader.momentum_sleeve as ms

    class FakeFundamentals:
        sector = industry = business_summary = "Software"

    class FakeProvider:
        def get_fundamentals(self, symbol):
            return FakeFundamentals()

        def get_price_history(self, symbol, period="1y"):
            return history(daily=0.002)

    monkeypatch.setattr(ms, "load_overrides",
                        lambda path="": {"SHOP": {"action": "exclude", "reason": "lending"}})
    monkeypatch.setattr(ms, "screen_company",
                        lambda f, summary_cleared=False: type("R", (), {"compliant": True})())
    ranked, _ = ms.rank_universe(["SHOP", "SAP"], provider=FakeProvider(),
                                 today=date(2026, 11, 13))
    symbols = [s for s, _, _ in ranked]
    assert "SHOP" not in symbols and "SAP" in symbols


def test_render_steady_state_nothing_to_do():
    ranked = [(f"S{i}", 0.5 - i * 0.01, 50.0) for i in range(12)]
    held = [Holding(f"S{i}", "2026-12-01", 50.0, 1.0) for i in range(TOP_N)]
    plan = rebalance_plan(ranked, [h.symbol for h in held])
    md = render_markdown(ranked, plan, held, date(2026, 11, 13))
    assert "Nothing. Every holding is still in the top 10." in md
