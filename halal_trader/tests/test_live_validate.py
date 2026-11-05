"""Tests for the live-screen validation scorer (live_validate.py)."""

import pandas as pd
import pytest

from halal_trader.live_validate import (
    LiveSignal,
    first_per_symbol_day,
    score_signal,
    summarize,
)


def _sig(symbol="TEST", ts="2026-10-23T14:30:00Z", price=100.0,
         stop=95.0, target=110.0):
    return LiveSignal(signalled_at_utc=ts, symbol=symbol,
                      live_price=price, stop=stop, target=target)


def _frame(rows):
    """rows: list of (date_str, open, high, low, close)."""
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [1_000_000] * len(rows),
        },
        index=idx,
    )


class TestSignalDate:
    def test_utc_timestamp_maps_to_session_date(self):
        assert _sig(ts="2026-11-04T20:58:37Z").signal_date.isoformat() == "2026-11-04"


class TestDayOneCloseOnly:
    """The signal day is judged on its close ONLY -- the day's low/high may
    have printed before the signal fired and must not count."""

    def test_intraday_low_through_stop_does_not_trigger(self):
        # Day-1 low (94) pierces the stop but the close (101) is fine:
        # pre-signal price action, so the trade survives to day 2+.
        hist = _frame([
            ("2026-10-23", 99, 102, 94, 101),
            ("2026-10-26", 101, 111, 100, 109),  # target 110 hit day 2
        ])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert out.resolved and out.exit_reason == "target"
        assert out.exit_price == 110.0

    def test_close_below_stop_exits_day_one(self):
        hist = _frame([("2026-10-23", 99, 102, 93, 94)])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert out.resolved and out.exit_reason == "stop"
        assert out.exit_date.isoformat() == "2026-10-23"
        assert out.exit_price == 94.0  # the close, not the stop level

    def test_close_above_target_fills_at_target_limit(self):
        hist = _frame([("2026-10-23", 99, 113, 98, 112)])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert out.resolved and out.exit_reason == "target"
        assert out.exit_price == 110.0  # limit order: no credit past it

    def test_day_one_stop_checked_before_target(self):
        # Degenerate close satisfying both (stop 95 >= close impossible with
        # target 110 -- use inverted levels to force the branch order).
        hist = _frame([("2026-10-23", 99, 113, 93, 94)])
        out = score_signal(_sig(target=94.0), hist, max_days=5, slippage_pct=0.0)
        assert out.exit_reason == "stop"

    def test_slippage_applied_to_day_one_stop(self):
        hist = _frame([("2026-10-23", 99, 102, 93, 94)])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.1)
        assert out.exit_price == pytest.approx(94 * 0.999, rel=1e-6)


class TestSubsequentDays:
    def test_time_stop_counts_signal_day_as_day_one(self):
        # max_days=5 -> signal day + 4 full bars; the 4th subsequent bar is
        # the time-stop day.
        rows = [("2026-10-23", 100, 101, 99, 100)]
        rows += [(d, 100, 101, 99, 100) for d in
                 ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29")]
        hist = _frame(rows)
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert out.resolved and out.exit_reason == "time_stop"
        assert out.exit_date.isoformat() == "2026-10-29"

    def test_gap_down_fills_at_open_on_later_days(self):
        hist = _frame([
            ("2026-10-23", 100, 101, 99, 100),
            ("2026-10-26", 92, 93, 91, 92),  # gaps through the 95 stop
        ])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert out.exit_reason == "stop"
        assert out.exit_price == 92.0  # the open, not the stop level

    def test_pending_when_nothing_triggered_yet(self):
        hist = _frame([
            ("2026-10-23", 100, 101, 99, 100),
            ("2026-10-26", 100, 101, 99, 100),
        ])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert not out.resolved

    def test_pending_when_no_bars_after_signal(self):
        hist = _frame([("2026-10-23", 100, 101, 99, 100)])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert not out.resolved

    def test_return_measured_from_live_price_not_close(self):
        # Entry logged at 100 while the day closed at 102: the return must
        # be computed off the logged live price, the shadow system's claim.
        hist = _frame([
            ("2026-10-23", 99, 103, 98, 102),
            ("2026-10-26", 102, 111, 101, 110),
        ])
        out = score_signal(_sig(price=100.0), hist, max_days=5, slippage_pct=0.0)
        assert out.return_pct == pytest.approx(10.0)


class TestDedupe:
    def test_first_signal_of_the_day_wins(self):
        sigs = [
            _sig(ts="2026-10-27T19:43:53Z", price=214.29),
            _sig(ts="2026-10-27T15:47:55Z", price=215.70),
        ]
        kept = first_per_symbol_day(sigs)
        assert len(kept) == 1
        assert kept[0].live_price == 215.70

    def test_same_symbol_different_days_both_kept(self):
        sigs = [
            _sig(ts="2026-10-27T15:47:55Z"),
            _sig(ts="2026-10-28T15:47:55Z"),
        ]
        assert len(first_per_symbol_day(sigs)) == 2

    def test_different_symbols_same_day_both_kept(self):
        sigs = [_sig(symbol="AAA"), _sig(symbol="BBB")]
        assert len(first_per_symbol_day(sigs)) == 2


class TestSummarize:
    def test_none_until_resolved(self):
        hist = _frame([("2026-10-23", 100, 101, 99, 100)])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert summarize([out]) is None

    def test_counts_and_pending(self):
        win = score_signal(_sig(), _frame([
            ("2026-10-23", 100, 101, 99, 100),
            ("2026-10-26", 100, 111, 99, 110),
        ]), max_days=5, slippage_pct=0.0)
        loss = score_signal(_sig(), _frame([
            ("2026-10-23", 100, 101, 99, 94),
        ]), max_days=5, slippage_pct=0.0)
        pend = score_signal(_sig(), _frame([
            ("2026-10-23", 100, 101, 99, 100),
        ]), max_days=5, slippage_pct=0.0)
        s = summarize([win, loss, pend])
        assert s["count"] == 2 and s["pending"] == 1
        assert s["wins"] == 1 and s["win_rate"] == 50.0
        assert s["by_reason"] == {"target": 1, "stop": 1}
