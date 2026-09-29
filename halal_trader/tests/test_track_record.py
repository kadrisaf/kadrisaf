from datetime import date

import pandas as pd

from halal_trader.data_provider import CompanyFundamentals, StaticProvider
from halal_trader.shariah_screen import ScreenResult
from halal_trader.signals import TradeSignal
from halal_trader.track_record import (
    TrackedCandidate,
    load_track_record,
    record_new_candidates,
    resolve_pending,
    save_track_record,
    summarize_track_record,
)


def _ranked_pair(symbol="TST", last_close=100.0, stop=95.0, target=110.0):
    screen = ScreenResult(symbol=symbol, compliant=True)
    signal = TradeSignal(
        symbol=symbol,
        qualifies=True,
        reasons_excluded=[],
        last_close=last_close,
        stop_loss=stop,
        target=target,
        max_holding_days=5,
    )
    return screen, signal


def test_record_new_candidates_appends_unresolved_row():
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))

    assert len(records) == 1
    assert records[0] == TrackedCandidate(
        date_ranked="2026-01-05",
        symbol="TST",
        entry_close=100.0,
        stop=95.0,
        target=110.0,
        max_holding_days=5,
    )


def test_record_new_candidates_skips_duplicate_same_day():
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))
    records = record_new_candidates(records, [_ranked_pair()], as_of=date(2026, 1, 5))

    assert len(records) == 1


def test_save_and_load_round_trip(tmp_path):
    path = str(tmp_path / "candidates.csv")
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))
    records[0].resolved = True
    records[0].resolution_date = "2026-01-10"
    records[0].exit_price = 110.0
    records[0].exit_reason = "target"
    records[0].return_pct = 10.0

    save_track_record(path, records)
    loaded = load_track_record(path)

    assert loaded == records


def test_load_track_record_returns_empty_list_for_missing_file(tmp_path):
    assert load_track_record(str(tmp_path / "nope.csv")) == []


def _history_hitting_target_on_day(n_days, target_day, target_price, base=100.0):
    """Flat history that pokes above target_price on trading day `target_day`
    (1-indexed) and otherwise sits well inside [stop, target]."""
    rows = []
    for day in range(1, n_days + 1):
        high = target_price + 1 if day == target_day else base + 1
        rows.append({"Open": base, "High": high, "Low": base - 1, "Close": base})
    index = pd.bdate_range("2026-01-06", periods=n_days)  # business days after Jan 5
    return pd.DataFrame(rows, index=index)


def test_resolve_pending_hits_target_first():
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))
    history = _history_hitting_target_on_day(5, target_day=2, target_price=110.0)
    provider = StaticProvider(fundamentals={}, histories={"TST": history})

    resolve_pending(records, provider)

    assert records[0].resolved is True
    assert records[0].exit_reason == "target"
    assert records[0].exit_price == 110.0
    assert records[0].return_pct == 10.0


def test_resolve_pending_hits_stop_first_when_both_trigger_same_bar():
    rows = [{"Open": 100.0, "High": 111.0, "Low": 90.0, "Close": 100.0}]
    index = pd.bdate_range("2026-01-06", periods=1)
    history = pd.DataFrame(rows, index=index)
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))
    provider = StaticProvider(fundamentals={}, histories={"TST": history})

    resolve_pending(records, provider)

    # Both stop (95) and target (110) are crossed on this bar -- stop wins,
    # the conservative assumption.
    assert records[0].exit_reason == "stop"
    assert records[0].exit_price == 95.0


def test_resolve_pending_exits_at_time_stop_when_neither_level_hits():
    history = _history_hitting_target_on_day(5, target_day=None, target_price=110.0)
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))
    provider = StaticProvider(fundamentals={}, histories={"TST": history})

    resolve_pending(records, provider)

    assert records[0].resolved is True
    assert records[0].exit_reason == "time_stop"
    # Exits at day 5's close, which _history_hitting_target_on_day sets to `base`.
    assert records[0].exit_price == 100.0
    assert records[0].resolution_date == pd.bdate_range("2026-01-06", periods=5)[-1].date().isoformat()


def test_resolve_pending_leaves_unresolved_when_not_enough_history_yet():
    history = _history_hitting_target_on_day(2, target_day=None, target_price=110.0)
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))
    provider = StaticProvider(fundamentals={}, histories={"TST": history})

    resolve_pending(records, provider)

    assert records[0].resolved is False


def test_resolve_pending_skips_symbol_with_no_data_available():
    records = record_new_candidates([], [_ranked_pair(symbol="MISSING")], as_of=date(2026, 1, 5))
    provider = StaticProvider(fundamentals={}, histories={})

    resolve_pending(records, provider)  # must not raise

    assert records[0].resolved is False


def test_summarize_track_record_computes_win_rate_and_avg_return():
    records = [
        TrackedCandidate(
            date_ranked="2026-01-05", symbol="A", entry_close=100, stop=95, target=110,
            max_holding_days=5, resolved=True, return_pct=10.0,
        ),
        TrackedCandidate(
            date_ranked="2026-01-06", symbol="B", entry_close=100, stop=95, target=110,
            max_holding_days=5, resolved=True, return_pct=-5.0,
        ),
        TrackedCandidate(
            date_ranked="2026-01-07", symbol="C", entry_close=100, stop=95, target=110,
            max_holding_days=5, resolved=False,
        ),
    ]

    summary = summarize_track_record(records)

    assert summary == {"count": 2, "wins": 1, "win_rate": 50.0, "avg_return_pct": 2.5}


def test_summarize_track_record_returns_none_with_no_resolved_trades():
    records = record_new_candidates([], [_ranked_pair()], as_of=date(2026, 1, 5))

    assert summarize_track_record(records) is None
