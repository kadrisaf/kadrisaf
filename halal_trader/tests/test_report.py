from datetime import date

from halal_trader.data_provider import NewsItem
from halal_trader.filings import FilingItem
from halal_trader.macro import MacroSnapshot
from halal_trader.report import build_report
from halal_trader.sentiment import SentimentTag
from halal_trader.shariah_screen import ScreenResult
from halal_trader.signals import TradeSignal


def test_report_lists_ranked_candidates_and_disclaimer():
    screen = ScreenResult(symbol="TST", compliant=True)
    signal = TradeSignal(
        symbol="TST",
        qualifies=True,
        reasons_excluded=[],
        last_close=101.2,
        rsi=55.0,
        relative_volume=1.8,
        stop_loss=97.0,
        target=108.0,
        reward_risk=1.7,
        score=12.3,
    )

    not_qualifying_screen = ScreenResult(symbol="FLT", compliant=True)
    not_qualifying_signal = TradeSignal(
        symbol="FLT", qualifies=False, reasons_excluded=["RSI 50.0 outside band"]
    )

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[ScreenResult(symbol="BNK", compliant=False, reasons=["excluded business activity: bank"])],
        not_qualifying=[(not_qualifying_screen, not_qualifying_signal)],
        skipped=["ZZZ"],
        as_of=date(2026, 1, 5),
    )

    assert "2026-01-05" in report
    assert "TST" in report
    assert "Not financial or Shariah advice" in report
    assert "BNK" in report
    assert "FLT" in report
    assert "no qualifying setup today" in report
    assert "ZZZ" in report
    # Universe count must include every bucket, not just ranked+screened_out+skipped.
    assert "Universe screened: 4 symbols" in report


def test_report_handles_empty_ranked_list():
    report = build_report(ranked=[], screened_out=[], not_qualifying=[], skipped=[])

    assert "No symbol passed" in report


def test_report_lists_recent_headlines_for_ranked_candidates():
    screen = ScreenResult(symbol="TST", compliant=True)
    signal = TradeSignal(symbol="TST", qualifies=True, reasons_excluded=[], score=12.3)

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        as_of=date(2026, 1, 5),
        news={
            "TST": [
                NewsItem(
                    title="TST wins big contract",
                    publisher="Reuters",
                    link="https://example.com/tst",
                    published="2026-01-04",
                )
            ]
        },
    )

    assert "Recent headlines" in report
    assert "unverified" in report
    assert "[TST wins big contract](https://example.com/tst)" in report
    assert "Reuters, 2026-01-04" in report


def test_report_flags_missing_news_without_failing():
    screen = ScreenResult(symbol="TST", compliant=True)
    signal = TradeSignal(symbol="TST", qualifies=True, reasons_excluded=[], score=12.3)

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        news={},
    )

    assert "no recent headlines found" in report


def test_report_shows_sentiment_tag_next_to_its_headlines():
    screen = ScreenResult(symbol="TST", compliant=True)
    signal = TradeSignal(symbol="TST", qualifies=True, reasons_excluded=[], score=12.3)

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        news={"TST": [NewsItem(title="TST wins big contract")]},
        sentiment={
            "TST": SentimentTag(
                sentiment=0.8,
                event_type="contract_win",
                confidence="high",
                rationale="A large new contract was announced.",
            )
        },
    )

    assert "sentiment +0.80" in report
    assert "contract_win" in report
    assert "high confidence" in report
    assert "not a prediction" in report
    assert "A large new contract was announced." in report


def test_report_shows_macro_snapshot_when_provided():
    report = build_report(
        ranked=[],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        macro=MacroSnapshot(vix=18.3, vix_label="normal", ten_year_yield_pct=4.12),
    )

    assert "VIX: 18.3 (normal)" in report
    assert "10-year Treasury yield: 4.12%" in report
    assert "Informational only" in report


def test_report_omits_macro_section_when_not_provided():
    report = build_report(ranked=[], screened_out=[], not_qualifying=[], skipped=[])

    assert "Macro snapshot" not in report


def test_report_shows_track_record_summary_when_provided():
    report = build_report(
        ranked=[],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        track_record_summary={
            "count": 4,
            "wins": 3,
            "win_rate": 75.0,
            "avg_return_pct": 3.21,
        },
    )

    assert "Track record so far" in report
    assert "4 resolved trades, 3 wins (75.0% win rate)" in report
    assert "average return +3.21%" in report
    assert "not a forward-looking probability" in report


def test_report_omits_track_record_section_when_not_provided():
    report = build_report(ranked=[], screened_out=[], not_qualifying=[], skipped=[])

    assert "Track record so far" not in report


def test_report_lists_recent_filings_for_ranked_candidates():
    screen = ScreenResult(symbol="TST", compliant=True)
    signal = TradeSignal(symbol="TST", qualifies=True, reasons_excluded=[], score=12.3)

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        news={"TST": [NewsItem(title="TST wins big contract")]},
        filings={
            "TST": [
                FilingItem(
                    form="8-K",
                    filed="2026-01-04",
                    url="https://www.sec.gov/Archives/edgar/data/1/1/tst-8k.htm",
                )
            ]
        },
    )

    assert "Recent SEC filings" in report
    assert "[8-K (2026-01-04)](https://www.sec.gov/Archives/edgar/data/1/1/tst-8k.htm)" in report


def test_report_flags_missing_filings_without_failing():
    screen = ScreenResult(symbol="TST", compliant=True)
    signal = TradeSignal(symbol="TST", qualifies=True, reasons_excluded=[], score=12.3)

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        news={"TST": [NewsItem(title="TST wins big contract")]},
        filings={},
    )

    assert "no recent 8-K filings found" in report


def test_report_omits_sentiment_line_when_none_available():
    screen = ScreenResult(symbol="TST", compliant=True)
    signal = TradeSignal(symbol="TST", qualifies=True, reasons_excluded=[], score=12.3)

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[],
        not_qualifying=[],
        skipped=[],
        news={"TST": [NewsItem(title="TST wins big contract")]},
        sentiment={},
    )

    assert "TST wins big contract" in report
    assert "*[LLM-generated" not in report
