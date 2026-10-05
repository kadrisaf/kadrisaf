from datetime import date

from halal_trader.backtest import Trade, render_markdown, run_backtest, simulate_trades, stats
from halal_trader.data_provider import CompanyFundamentals, StaticProvider
from halal_trader.tests.test_signals import UPTREND_PATTERN, _make_ohlcv


def _fund(symbol):
    return CompanyFundamentals(
        symbol=symbol, sector="Technology", industry="Software", market_cap=1e11,
        total_debt=1e9, cash_and_short_term_investments=1e9, receivables=1e9, currency="USD",
    )


def test_stats_summarises_wins_losses_and_exit_reasons():
    trades = [
        Trade(date(2026, 1, 1), 4.0, "target"),
        Trade(date(2026, 1, 2), -2.0, "stop"),
        Trade(date(2026, 1, 3), 1.0, "time_stop"),
        Trade(date(2026, 1, 4), -1.0, "time_stop"),
    ]
    s = stats(trades)
    assert s["n"] == 4 and s["win_rate"] == 50.0 and s["avg"] == 0.5
    assert s["profit_factor"] == round(5.0 / 3.0, 2)
    assert (s["stop_pct"], s["target_pct"], s["time_pct"]) == (25.0, 25.0, 50.0)
    assert stats([]) is None


def test_no_overlapping_entries_per_symbol():
    df = _make_ohlcv(UPTREND_PATTERN, n=160, last_volume_multiple=1.0)
    days = [(i, True, float(df["Close"].iloc[i]), 1.0) for i in range(70, 150)]
    trades = simulate_trades(df, days, 1.5, 2.25, 5, 0.0, only_qualifying=True)
    assert trades, "expected some trades"
    entry_idx = [df.index.get_loc(df.index[df.index.date == t.entry_date][0]) for t in trades]
    assert all(b - a >= 2 for a, b in zip(entry_idx, entry_idx[1:]))  # each trade lasts >= 1 bar


def test_run_backtest_end_to_end_on_synthetic_data_and_renders_caveats():
    df = _make_ohlcv(UPTREND_PATTERN, n=200, last_volume_multiple=1.0)
    provider = StaticProvider({"UP": _fund("UP")}, {"UP": df})

    result = run_backtest(["UP"], years=1, provider=provider, grid=[(1.5, 2.25)], warmup=60)

    assert result["symbols_used"] == ["UP"]
    assert result["anyday"][(1.5, 2.25)]["n"] > 0
    md = render_markdown(result, date(2026, 10, 5))
    assert "Any-day baseline" in md and "Look-ahead" in md and "Survivorship" in md
