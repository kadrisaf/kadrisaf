"""Tests for the post-earnings study and the shared-tape attribution flag."""

from datetime import date

import pandas as pd
import pytest

from halal_trader.postearnings_study import (
    BUCKETS,
    UNKNOWN,
    bucket_for,
    days_since_earnings,
    render,
    tag_trades,
    verdict,
)
from halal_trader.risk import shared_tape_flags


# ------------------------------------------------------------- bucketing

def test_days_since_uses_most_recent_prior_date_only():
    earnings = [date(2026, 8, 1), date(2026, 11, 3), date(2027, 2, 1)]
    assert days_since_earnings(date(2026, 11, 5), earnings) == 2
    assert days_since_earnings(date(2026, 7, 1), earnings) is None  # none prior
    assert days_since_earnings(date(2026, 11, 5), None) is None
    assert days_since_earnings(date(2026, 11, 5), []) is None


def test_bucket_edges_match_report_warning_window():
    assert bucket_for(0) == BUCKETS[0][0]
    assert bucket_for(7) == BUCKETS[0][0]     # the report warns through day 7
    assert bucket_for(8) == BUCKETS[1][0]
    assert bucket_for(20) == BUCKETS[1][0]
    assert bucket_for(21) == BUCKETS[2][0]
    assert bucket_for(None) == UNKNOWN


def test_tag_trades_groups_and_caches_lookups():
    calls = []

    def fake_earnings(sym):
        calls.append(sym)
        return {"A": [date(2026, 11, 3)], "B": []}[sym]

    trades = [("A", date(2026, 11, 5), 1.0),   # 2d post-earnings
              ("A", date(2026, 11, 30), -1.0),  # 27d -> late bucket
              ("B", date(2026, 11, 5), 0.5)]    # unknown
    grouped = tag_trades(trades, fake_earnings)
    assert grouped[BUCKETS[0][0]] == [1.0]
    assert grouped[BUCKETS[2][0]] == [-1.0]
    assert grouped[UNKNOWN] == [0.5]
    assert calls == ["A", "B"]  # one lookup per symbol, cached


# ------------------------------------------------------------- verdict

def early_late(n_early, avg_early, n_late, avg_late):
    return {BUCKETS[0][0]: [avg_early] * n_early, BUCKETS[2][0]: [avg_late] * n_late}


def test_verdict_requires_minimum_bucket_size():
    text = verdict(early_late(10, -1.0, 50, 1.0), early_late(3, -1.0, 5, 1.0))
    assert "keep measuring" in text


def test_verdict_promotes_only_with_margin_and_real_agreement():
    sim = early_late(40, -0.5, 60, 0.5)   # gap 1.0 >= 0.5 margin
    real_agree = early_late(4, -1.0, 6, 1.0)
    real_disagree = early_late(4, 2.0, 6, -1.0)
    assert "supports promotion" in verdict(sim, real_agree)
    assert "warning stays a warning" in verdict(sim, real_disagree)


def test_verdict_rejects_small_gap():
    sim = early_late(40, 0.4, 60, 0.5)    # gap 0.1 < margin
    assert "warning stays a warning" in verdict(sim, early_late(4, 0.0, 6, 1.0))


def test_render_reports_coverage_and_all_buckets():
    sim = {BUCKETS[0][0]: [1.0, -1.0], UNKNOWN: [0.2]}
    text = render(sim, {}, as_of=date(2026, 11, 10))
    assert "3 trades; earnings data dated 2 of them." in text
    for name, _lo, _hi in BUCKETS:
        assert name in text
    assert "keep measuring" in text


# ------------------------------------------------------------- shared tape

SECTORS = {"DHI": "Consumer Cyclical", "PHM": "Consumer Cyclical",
           "NVR": "Consumer Cyclical", "MAS": "Consumer Cyclical",
           "LLY": "Healthcare", "ABBV": "Healthcare"}


def test_shared_tape_fires_on_sector_wide_rally():
    ret5 = {"DHI": 6.0, "PHM": 5.0, "NVR": 4.0, "MAS": 3.5, "LLY": 0.3}
    flags = shared_tape_flags(["DHI"], ret5, SECTORS)
    assert "DHI" in flags
    assert "sector-wide" in flags["DHI"]
    assert "warning only" in flags["DHI"]


def test_shared_tape_silent_when_move_is_name_specific():
    ret5 = {"DHI": 6.0, "PHM": 0.5, "NVR": -0.2, "MAS": 0.8}
    assert shared_tape_flags(["DHI"], ret5, SECTORS) == {}


def test_shared_tape_needs_peers_same_sign_and_real_move():
    # too few peers
    assert shared_tape_flags(["LLY"], {"LLY": 5.0, "ABBV": 5.0}, SECTORS) == {}
    # opposite sign
    ret5 = {"DHI": 6.0, "PHM": -5.0, "NVR": -4.0, "MAS": -3.0}
    assert shared_tape_flags(["DHI"], ret5, SECTORS) == {}
    # candidate barely moved
    ret5 = {"DHI": 0.5, "PHM": 0.4, "NVR": 0.6, "MAS": 0.5}
    assert shared_tape_flags(["DHI"], ret5, SECTORS) == {}


def test_shared_tape_missing_data_is_silent():
    assert shared_tape_flags(["DHI"], {}, SECTORS) == {}
    assert shared_tape_flags(["XXX"], {"XXX": 5.0}, {}) == {}


def test_report_renders_shared_tape_flag():
    from halal_trader.report import _entry_context_lines
    from halal_trader.signals import TradeSignal

    sig = TradeSignal(symbol="DHI", qualifies=True, reasons_excluded=[])
    out = "\n".join(_entry_context_lines(
        [(None, sig)], None, [], date(2026, 11, 10),
        shared_tape={"DHI": "shared tape: test message"},
    ))
    assert "⚠ shared tape: test message" in out
