"""Tests for the live-screen validation scorer (live_validate.py) and the
live screen's earnings filter (live_screen.earnings_note)."""

from datetime import date

import pandas as pd
import pytest

from halal_trader.live_screen import earnings_note
from halal_trader.live_validate import (
    LiveSignal,
    build_validation_report,
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


class TestEarningsFilter:
    """live_screen.earnings_note: the check added after the NOW collapse."""

    def test_earnings_inside_window_flagged(self):
        # Entry Thu 2026-11-05; 5-trading-day window ends Wed 2026-11-11.
        note = earnings_note(
            "NOW", date(2026, 11, 5), max_days=5,
            next_fn=lambda s, t: date(2026, 11, 10),
        )
        assert "earnings 2026-11-10" in note

    def test_earnings_on_last_window_day_flagged(self):
        note = earnings_note(
            "X", date(2026, 11, 5), max_days=5,
            next_fn=lambda s, t: date(2026, 11, 11),
        )
        assert note != ""

    def test_earnings_after_window_clean(self):
        note = earnings_note(
            "X", date(2026, 11, 5), max_days=5,
            next_fn=lambda s, t: date(2026, 11, 12),
        )
        assert note == ""

    def test_unknown_earnings_date_clean(self):
        # Same best-effort behavior as the daily report: unknown is not
        # a block, the reader is told to check manually.
        assert earnings_note("X", date(2026, 11, 5), max_days=5,
                             next_fn=lambda s, t: None) == ""


class TestFlaggedSplit:
    """Validation report grades earnings-flagged signals separately."""

    def _outcomes(self):
        win_hist = _frame([
            ("2026-11-05", 100, 101, 99, 100),
            ("2026-11-06", 100, 111, 99, 110),
        ])
        clean = score_signal(
            _sig(symbol="CLEAN", ts="2026-11-05T15:00:00Z"),
            win_hist, max_days=5, slippage_pct=0.0)
        flagged_sig = LiveSignal(
            signalled_at_utc="2026-11-05T15:00:00Z", symbol="FLAG",
            live_price=100.0, stop=95.0, target=110.0,
            earnings_note="earnings 2026-11-09 inside 5-day hold window")
        flagged = score_signal(flagged_sig, _frame([
            ("2026-11-05", 100, 101, 99, 94),
        ]), max_days=5, slippage_pct=0.0)
        return [clean, flagged]

    def test_headline_excludes_flagged(self):
        report = build_validation_report(
            self._outcomes(), [], None, date(2026, 11, 5))
        # Headline (clean only): 1 resolved, 1 win.
        assert "Shadow signals: 1 resolved, 1 wins (100.0%)" in report
        # Flagged group graded separately.
        assert "what the filter excluded" in report
        assert "FLAG (earnings-flagged)" in report

    def test_flagged_summary_shows_the_loss(self):
        report = build_validation_report(
            self._outcomes(), [], None, date(2026, 11, 5))
        assert "Earnings-flagged signals (what the filter excluded): 1 resolved, 0 wins" in report


from halal_trader.live_validate import (
    bucket_breakdowns,
    close_entry_outcome,
    entry_value_study,
)


class TestCloseEntryCounterfactual:
    def test_not_comparable_when_close_beyond_stop(self):
        hist = _frame([("2026-10-23", 99, 102, 93, 94)])  # close < stop 95
        assert close_entry_outcome(_sig(), hist, max_days=5,
                                   slippage_pct=0.0) is None

    def test_not_comparable_when_no_bar_yet(self):
        hist = _frame([("2026-10-22", 99, 102, 98, 100)])
        assert close_entry_outcome(_sig(), hist, max_days=5,
                                   slippage_pct=0.0) is None

    def test_return_measured_from_close_same_levels(self):
        # Live entry 100, day closes 102, target 110 hits day 2: the EOD
        # leg must book +7.84% (from 102), not +10%.
        hist = _frame([
            ("2026-10-23", 99, 103, 98, 102),
            ("2026-10-26", 102, 111, 101, 110),
        ])
        out = close_entry_outcome(_sig(price=100.0), hist, max_days=5,
                                  slippage_pct=0.0)
        assert out.resolved and out.exit_reason == "target"
        assert out.return_pct == pytest.approx(7.84, abs=0.01)


class TestEntryValueStudy:
    def test_delta_is_live_minus_eod(self):
        hist = _frame([
            ("2026-10-23", 99, 103, 98, 102),
            ("2026-10-26", 102, 111, 101, 110),
        ])
        study = entry_value_study([_sig(price=100.0)], {"TEST": hist})
        assert study["pairs"] == 1
        assert study["avg_live_pct"] == pytest.approx(10.0)
        assert study["avg_eod_pct"] == pytest.approx(7.84, abs=0.01)
        assert study["avg_delta_pct"] == pytest.approx(2.16, abs=0.02)

    def test_same_day_live_exit_not_comparable(self):
        # Live entry stopped on the signal day's close: an EOD process was
        # never in the trade, so there is no pair.
        hist = _frame([("2026-10-23", 99, 102, 93, 94)])
        study = entry_value_study([_sig()], {"TEST": hist})
        assert study is None or study.get("pairs", 0) == 0

    def test_pending_pair_counted_not_compared(self):
        hist = _frame([
            ("2026-10-23", 99, 103, 98, 102),
            ("2026-10-26", 102, 104, 101, 103),
        ])
        win_hist = _frame([
            ("2026-10-23", 99, 103, 98, 102),
            ("2026-10-26", 102, 111, 101, 110),
        ])
        study = entry_value_study(
            [_sig(symbol="PEND"), _sig(symbol="DONE", price=100.0)],
            {"PEND": hist, "DONE": win_hist})
        assert study["pairs"] == 1 and study["pending"] == 1


class TestBreakdowns:
    def _outcome(self, ret, rel_vol, elapsed, score):
        sig = LiveSignal(
            signalled_at_utc="2026-10-23T14:30:00Z", symbol="X",
            live_price=100.0, stop=95.0, target=110.0,
            rel_vol_est=rel_vol, session_elapsed_pct=elapsed, score=score)
        hist = _frame([
            ("2026-10-23", 100, 101, 99, 100),
            ("2026-10-26", 100, 111 if ret > 0 else 101,
             99 if ret > 0 else 93, 110 if ret > 0 else 94),
        ])
        return score_signal(sig, hist, max_days=5, slippage_pct=0.0)

    def test_buckets_split_and_count(self):
        outs = [
            self._outcome(+1, rel_vol=1.2, elapsed=20.0, score=25.0),
            self._outcome(-1, rel_vol=2.0, elapsed=90.0, score=40.0),
        ]
        rows = bucket_breakdowns(outs)
        feats = {r["feature"] for r in rows}
        assert feats == {"score", "rel_vol_est", "session_elapsed_pct"}
        vol = {r["bucket"]: r for r in rows if r["feature"] == "rel_vol_est"}
        assert vol["<1.5x"]["n"] == 1 and vol["<1.5x"]["win_rate"] == 100.0
        assert vol[">=1.5x"]["n"] == 1 and vol[">=1.5x"]["win_rate"] == 0.0

    def test_missing_features_skipped(self):
        hist = _frame([
            ("2026-10-23", 100, 101, 99, 100),
            ("2026-10-26", 100, 111, 99, 110),
        ])
        out = score_signal(_sig(), hist, max_days=5, slippage_pct=0.0)
        assert bucket_breakdowns([out]) == []


from halal_trader.live_validate import peak_profile


class TestPeakProfile:
    def test_peak_day_is_argmax_of_highs(self):
        hist = _frame([
            ("2026-10-23", 100, 101, 99, 100),   # entry day (excluded)
            ("2026-10-26", 100, 102, 99, 101),   # day 1
            ("2026-10-27", 101, 108, 100, 107),  # day 2 <- peak
            ("2026-10-28", 107, 105, 99, 100),
            ("2026-10-29", 100, 101, 99, 100),
            ("2026-10-30", 100, 101, 99, 100),
        ])
        pk = peak_profile([("X", date(2026, 10, 23), 100.0)], {"X": hist},
                          max_days=5)
        assert pk["n"] == 1
        assert pk["histogram"] == {2: 1}
        assert pk["median_peak_day"] == 2
        assert pk["avg_mfe_pct"] == pytest.approx(8.0)

    def test_window_capped_at_max_days(self):
        rows = [("2026-10-23", 100, 101, 99, 100)]
        rows += [(d, 100, 101, 99, 100) for d in
                 ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
                  "2026-10-30")]
        rows.append(("2026-11-02", 100, 150, 99, 149))  # day 6: outside
        hist = _frame(rows)
        pk = peak_profile([("X", date(2026, 10, 23), 100.0)], {"X": hist},
                          max_days=5)
        assert max(pk["histogram"]) <= 5
        assert pk["avg_mfe_pct"] == pytest.approx(1.0)

    def test_first_day_label_offsets_shadow_population(self):
        hist = _frame([
            ("2026-10-23", 100, 101, 99, 100),
            ("2026-10-26", 100, 109, 99, 108),  # first full bar = day 2
        ])
        pk = peak_profile([("X", date(2026, 10, 23), 100.0)], {"X": hist},
                          max_days=4, first_day_label=2)
        assert pk["histogram"] == {2: 1}

    def test_none_when_no_bars_after_entry(self):
        hist = _frame([("2026-10-23", 100, 101, 99, 100)])
        assert peak_profile([("X", date(2026, 10, 23), 100.0)],
                            {"X": hist}, max_days=5) is None

    def test_median_over_several_trades(self):
        def one(peak_on):
            rows = []
            for i, d in enumerate(("2026-10-26", "2026-10-27", "2026-10-28",
                                   "2026-10-29", "2026-10-30"), start=1):
                hi = 110 if i == peak_on else 101
                rows.append((d, 100, hi, 99, 100))
            return _frame(rows)
        hists = {"A": one(1), "B": one(3), "C": one(5)}
        pk = peak_profile(
            [(s, date(2026, 10, 23), 100.0) for s in "ABC"],
            hists, max_days=5)
        assert pk["n"] == 3 and pk["median_peak_day"] == 3
