from datetime import date

from halal_trader import events as ev


def test_hold_window_counts_entry_day_as_day_one_and_skips_weekends():
    # Mon Oct 5 -> Fri Oct 9 (5 trading days, entry day included)
    assert ev.time_stop_date(date(2026, 10, 5), 5) == date(2026, 10, 9)
    # Thu Oct 1 -> Wed Oct 7 (weekend skipped)
    assert ev.time_stop_date(date(2026, 10, 1), 5) == date(2026, 10, 7)


def test_hold_window_skips_nyse_holidays_but_not_columbus_day():
    # Thanksgiving Thu Nov 26 2026 is skipped: Mon 23,24,25,27, then Mon 30.
    assert ev.time_stop_date(date(2026, 11, 23), 5) == date(2026, 11, 30)
    # Columbus Day (Mon Oct 12) is an NYSE trading day.
    assert ev.is_trading_day(date(2026, 10, 12))


def test_weekend_entry_starts_on_next_trading_day():
    assert ev.hold_window(date(2026, 10, 3), 1) == [date(2026, 10, 5)]


def test_trading_days_left_excludes_today():
    assert ev.trading_days_left(date(2026, 10, 5), date(2026, 10, 9)) == 4
    assert ev.trading_days_left(date(2026, 10, 9), date(2026, 10, 9)) == 0


def test_event_calendar_load_filter_and_coverage(tmp_path):
    p = tmp_path / "cal.csv"
    p.write_text("date,event\n2026-10-07,FOMC minutes\nnot-a-date,junk\n2026-10-14,CPI\n")
    events = ev.load_event_calendar(str(p))
    assert [e.name for e in events] == ["FOMC minutes", "CPI"]

    window = ev.hold_window(date(2026, 10, 5), 5)
    assert [e.name for e in ev.events_in_window(events, window)] == ["FOMC minutes"]
    assert ev.calendar_covers(events, window) is True
    # A window past the last entry must NOT read as "no events".
    late = ev.hold_window(date(2026, 10, 19), 5)
    assert ev.calendar_covers(events, late) is False


def test_missing_calendar_file_is_empty_not_error(tmp_path):
    assert ev.load_event_calendar(str(tmp_path / "nope.csv")) == []


class _FakeTicker:
    def __init__(self, cal):
        self.calendar = cal


class _FakeYf:
    def __init__(self, cal=None, boom=False):
        self._cal, self._boom = cal, boom

    def Ticker(self, symbol):
        if self._boom:
            raise RuntimeError("network down")
        return _FakeTicker(self._cal)


def test_next_earnings_date_picks_earliest_future_date():
    cal = {"Earnings Date": [date(2026, 9, 1), date(2026, 10, 30), date(2026, 11, 5)]}
    assert ev.next_earnings_date("X", date(2026, 10, 5), yf=_FakeYf(cal)) == date(2026, 10, 30)


def test_next_earnings_date_unknown_or_failing_is_none():
    assert ev.next_earnings_date("X", date(2026, 10, 5), yf=_FakeYf({})) is None
    assert ev.next_earnings_date("X", date(2026, 10, 5), yf=_FakeYf(boom=True)) is None
