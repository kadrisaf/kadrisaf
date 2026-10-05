from datetime import date

from halal_trader.trades import load_trades, open_positions, summarize_trades, trade_return_pct

CSV = """symbol,entry_date,entry_price,quantity,currency,stop,target,status,exit_date,exit_price,realized_pct,notes
ABBV,2026-10-05,235.10,2,EUR,228.19,245.47,open,,,,fresh
MU,,,,EUR,,,open,,,,entry unknown
NVDA,,,2.5,EUR,,,closed,2026-10-02,210.15,4.09,app reported
LOSS,2026-09-01,100,1,EUR,,,closed,2026-09-04,95,,computed from prices
"""


def _trades(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(CSV)
    return load_trades(str(p))


def test_open_position_time_stop_counts_from_actual_entry_day(tmp_path):
    pos = {p["trade"].symbol: p for p in open_positions(_trades(tmp_path), date(2026, 10, 5))}
    assert pos["ABBV"]["time_stop"] == date(2026, 10, 9)
    assert pos["ABBV"]["days_left"] == 4
    assert pos["ABBV"]["overdue"] is False


def test_unknown_entry_date_is_never_guessed(tmp_path):
    pos = {p["trade"].symbol: p for p in open_positions(_trades(tmp_path), date(2026, 10, 5))}
    assert pos["MU"]["time_stop"] is None and pos["MU"]["days_left"] is None


def test_overdue_flag_after_time_stop(tmp_path):
    pos = {p["trade"].symbol: p for p in open_positions(_trades(tmp_path), date(2026, 10, 12))}
    assert pos["ABBV"]["overdue"] is True


def test_closed_trade_return_uses_override_else_prices(tmp_path):
    by = {t.symbol: t for t in _trades(tmp_path)}
    assert trade_return_pct(by["NVDA"]) == 4.09
    assert trade_return_pct(by["LOSS"]) == -5.0


def test_summary_counts_only_closed_trades_with_a_return(tmp_path):
    s = summarize_trades(_trades(tmp_path))
    assert s == {"count": 2, "wins": 1, "win_rate": 50.0, "avg_return_pct": -0.46}


def test_missing_file_is_empty(tmp_path):
    assert load_trades(str(tmp_path / "none.csv")) == []
    assert summarize_trades([]) is None
