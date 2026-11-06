"""Tests for the veto-only pre-entry check (entry_check.py)."""

from datetime import datetime, timezone

from halal_trader.entry_check import (
    RankedRow,
    build_check_report,
    check_row,
    parse_ranked_rows,
)

REPORT_SNIPPET = """
| # | Symbol | Close | RSI | Rel.Vol | Stop | Target | R:R | Max hold | Score |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **ISRG** | 468.52 | 64.82 | 1.7x | 436.9549 | 515.8677 | 1.5 | 5d | 41.69 |
| 2 | **AIR.PA** | 210.5 | 60.0 | 1.3x | 200.0 | 226.0 | 1.5 | 5d | 30.0 |

## Shariah-compliant, but no qualifying setup today
- **KO**: no setup
"""


def _row(close=100.0, stop=95.0, target=110.0):
    return RankedRow(symbol="TST", close=close, stop=stop, target=target)


class TestParseRankedRows:
    def test_parses_symbol_close_stop_target(self):
        rows = parse_ranked_rows(REPORT_SNIPPET)
        assert [(r.symbol, r.close, r.stop, r.target) for r in rows] == [
            ("ISRG", 468.52, 436.9549, 515.8677),
            ("AIR.PA", 210.5, 200.0, 226.0),
        ]

    def test_non_ranked_lines_ignored(self):
        assert parse_ranked_rows("- **KO**: no setup\nrandom text") == []


class TestCheckRow:
    def test_confirm_when_everything_holds(self):
        r = check_row(_row(), live_price=100.8, signal_qualifies=True)
        assert r.verdict == "CONFIRM" and r.reasons == []
        assert r.gap_pct == 0.8

    def test_caution_on_gap_beyond_threshold(self):
        r = check_row(_row(), live_price=103.0, signal_qualifies=True)
        assert r.verdict == "CAUTION"
        assert any("gapped" in s for s in r.reasons)

    def test_negative_gap_also_flagged(self):
        r = check_row(_row(), live_price=97.0, signal_qualifies=True)
        assert any("gapped" in s for s in r.reasons)

    def test_caution_when_live_below_stop(self):
        r = check_row(_row(), live_price=94.0, signal_qualifies=True)
        assert any("at/below the report's stop" in s for s in r.reasons)

    def test_caution_when_live_above_target(self):
        r = check_row(_row(), live_price=111.0, signal_qualifies=True)
        assert any("at/above the report's target" in s for s in r.reasons)

    def test_caution_when_signal_broke(self):
        r = check_row(_row(), live_price=100.5, signal_qualifies=False)
        assert any("no longer qualifies" in s for s in r.reasons)

    def test_caution_on_earnings_note(self):
        r = check_row(_row(), live_price=100.5, signal_qualifies=True,
                      earn_note="earnings 2026-11-10 inside 5-day hold window")
        assert r.verdict == "CAUTION"
        assert any("earnings" in s for s in r.reasons)


class TestCheckReport:
    def test_report_lists_verdicts_and_reasons(self):
        ok = check_row(_row(), live_price=100.5, signal_qualifies=True)
        bad = check_row(RankedRow("BAD", 100.0, 95.0, 110.0),
                        live_price=94.0, signal_qualifies=True)
        from halal_trader.entry_check import CheckResult
        nodata = CheckResult(row=RankedRow("ND", 50.0, 45.0, 60.0))
        text = build_check_report(
            [ok, bad, nodata],
            datetime(2026, 11, 6, 14, 45, tzinfo=timezone.utc),
        )
        assert "| **TST** | 100.0 | 100.50 | +0.5% | CONFIRM |" in text
        assert "| **BAD** | 100.0 | 94.00 | -6.0% | CAUTION |" in text
        assert "| **ND** | 50.0 | -- | -- | NO DATA |" in text
        assert "at/below the report's stop" in text
        assert "VETO-ONLY" in text and "never adds any" in text
