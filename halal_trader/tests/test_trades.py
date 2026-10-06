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


def test_plan_vs_real_matches_nearest_tracked_candidate(tmp_path):
    from halal_trader.track_record import TrackedCandidate
    from halal_trader.trades import plan_vs_real

    trades = _trades(tmp_path)  # NVDA closed +4.09 (no entry date), LOSS closed -5.0 (entry 2026-09-01)
    tracked = [
        TrackedCandidate("2026-09-01", "LOSS", 100, 95, 110, 5, resolved=True,
                         resolution_date="2026-09-05", exit_price=95.0,
                         exit_reason="stop", return_pct=-5.0),
        TrackedCandidate("2026-08-01", "LOSS", 90, 85, 99, 5, resolved=True,
                         resolution_date="2026-08-05", exit_price=99.0,
                         exit_reason="target", return_pct=10.0),  # too far away: ignored
    ]
    rows = {r["symbol"]: r for r in plan_vs_real(trades, tracked)}

    assert rows["LOSS"]["matched"] and rows["LOSS"]["plan_pct"] == -5.0
    assert rows["LOSS"]["plan_exit"] == "stop"
    assert rows["NVDA"]["matched"] is False  # no entry date -> unmatched, not dropped
