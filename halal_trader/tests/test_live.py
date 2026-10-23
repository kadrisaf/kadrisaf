from datetime import date

from halal_trader.live import live_rows, render
from halal_trader.trades import Trade, open_positions


def _positions():
    return open_positions(
        [Trade("ISRG", "open", date(2026, 10, 22), 380.25, 1.95, "EUR", 357.63, 414.21)],
        date(2026, 10, 23),
    )


def test_live_rows_compute_distances_from_the_fill_levels():
    quotes = {"ISRG": {"price": 436.68, "prev_close": 428.12, "entry_day_close": 428.12}}
    rows = live_rows(_positions(), lambda s: quotes[s], today=date(2026, 10, 23))
    r = rows[0]
    assert r["live_pct"] == 2.0 and r["day_pct"] == 2.0
    # stop is -5.95% from fill -> distance 2.0 - (-5.95) = 7.95
    assert abs(r["to_stop_pct"] - 7.95) < 0.02
    assert abs(r["to_target_pct"] - (8.93 - 2.0)) < 0.02
    assert "NEAR STOP" not in render(rows)


def test_live_rows_flag_near_stop_and_survive_missing_quotes():
    quotes = {"ISRG": {"price": 382.0, "prev_close": 428.12, "entry_day_close": 428.12}}
    rows = live_rows(_positions(), lambda s: quotes[s], today=date(2026, 10, 23))
    assert "NEAR STOP" in render(rows)  # -10.8% vs stop at -5.95% -> below stop

    rows = live_rows(_positions(), lambda s: None, today=date(2026, 10, 23))
    assert rows[0].get("live_pct") is None and "n/a" in render(rows)
