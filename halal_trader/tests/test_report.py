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


def _ranked_one(symbol="TST", **kw):
    sig = TradeSignal(
        symbol=symbol, qualifies=True, reasons_excluded=[], last_close=100.0, rsi=55.0,
        relative_volume=1.5, stop_loss=95.0, target=107.5, reward_risk=1.5, score=10.0,
        high_52w=101.0, pct_below_52w_high=1.0, rr_to_52w_high=0.2,
        warnings=["entry is extended: within 1.0% of the 52-week high (101.00)"], **kw,
    )
    return [(ScreenResult(symbol=symbol, compliant=True), sig)]


def _hold(window_start=date(2026, 10, 5), **kw):
    from halal_trader.events import HoldContext, MacroEvent, hold_window

    base = dict(
        window=hold_window(window_start, 5),
        macro_events=[MacroEvent(date(2026, 10, 7), "FOMC minutes 14:00 ET")],
        calendar_ok=True,
        earnings={"TST": date(2026, 10, 8)},
    )
    base.update(kw)
    return HoldContext(**base)


def test_report_shows_entry_timing_flags_and_hold_window_events():
    report = build_report(
        _ranked_one(), [], [], [], as_of=date(2026, 10, 5), hold=_hold()
    )

    assert "Entry-timing flags" in report
    assert "1.0% below its high of 101.0" in report
    assert "entry is extended" in report
    assert "2026-10-05 -> 2026-10-09" in report
    assert "2026-10-07 (Wed): FOMC minutes" in report
    assert "reports earnings on 2026-10-08 -- INSIDE the hold window" in report


def test_report_says_calendar_is_stale_instead_of_claiming_no_events():
    report = build_report(
        _ranked_one(), [], [], [], as_of=date(2026, 10, 5),
        hold=_hold(macro_events=[], calendar_ok=False, earnings={"TST": None}),
    )

    assert "does not reach the end of this window" in report
    assert "earnings date unavailable" in report
    assert "No scheduled macro events" not in report


def test_report_shows_open_positions_with_time_stop_and_overdue():
    from halal_trader.trades import Trade, open_positions

    trades = [
        Trade("ABBV", "open", date(2026, 10, 5), 235.10, 2, "EUR", 228.19, 245.47),
        Trade("MU", "open", currency="EUR"),
    ]
    live = build_report([], [], [], [], as_of=date(2026, 10, 6), positions=open_positions(trades, date(2026, 10, 6)))
    assert "## Your open positions" in live
    assert "2026-10-05 @ 235.1 EUR" in live and "2026-10-09" in live
    assert "entry unconfirmed" in live and "unknown (no entry date)" in live

    late = build_report([], [], [], [], as_of=date(2026, 10, 12), positions=open_positions(trades, date(2026, 10, 12)))
    assert "OVERDUE -- exit now" in late


def test_report_flags_ranked_symbol_already_held_and_shows_real_summary():
    from halal_trader.trades import Trade, open_positions

    held = open_positions([Trade("TST", "open", date(2026, 10, 5), 100.0, 1, "EUR")], date(2026, 10, 5))
    report = build_report(
        _ranked_one(), [], [], [], as_of=date(2026, 10, 5), positions=held,
        real_summary={"count": 2, "wins": 2, "win_rate": 100.0, "avg_return_pct": 3.52},
    )
    assert "already in your open positions" in report
    assert "## Your real trades so far" in report and "+3.52%" in report


def test_report_prints_order_tickets_with_formula_sizing_and_risk_warning():
    report = build_report(_ranked_one(), [], [], [], as_of=date(2026, 10, 6))

    assert "Order tickets (mechanical sizing -- NOT a recommendation" in report
    # entry 100, stop 95 -> (portfolio * 2%) / 5 per share
    from halal_trader import config as _cfg

    shares = round(_cfg.PORTFOLIO_EUR * _cfg.RISK_PCT_PER_TRADE / 100 / 5, 4)
    assert f"| **TST** | {shares} | {shares * 100:.2f}" in report
    assert "Risk setting" not in report  # 2% is inside the conventional band
    assert "place the stop immediately" in report


def test_do_today_section_reads_the_clock_per_position():
    from halal_trader.trades import Trade, open_positions

    trades = [
        Trade("DUE", "open", date(2026, 10, 2), 100.0, 1, "EUR", 95.0, 110.0),   # day 5 = Oct 8
        Trade("LATE", "open", date(2026, 9, 28), 100.0, 1, "EUR", 95.0, 110.0),  # overdue
        Trade("SOON", "open", date(2026, 10, 5), 100.0, 1, "EUR"),               # day 5 = Oct 9, no stop
        Trade("NOENTRY", "open", currency="EUR"),
    ]
    report = build_report(
        [], [], [], [], as_of=date(2026, 10, 8),
        positions=open_positions(trades, date(2026, 10, 8)),
        hold=_hold(window_start=date(2026, 10, 8),
                   macro_events=[], calendar_ok=True, earnings={}),
    )

    assert "## Do today" in report
    assert "SELL DUE TODAY -- time-stop is today (2026-10-08)" in report
    assert "SELL LATE TODAY -- time-stop was 2026-10-02 and is OVERDUE" in report
    assert "SOON: last full day tomorrow" in report
    assert "no stop recorded for SOON" in report
    assert "NOENTRY: entry date missing" in report


def test_news_and_filings_cover_held_names_not_only_ranked():
    from halal_trader.trades import Trade, open_positions

    held = open_positions(
        [Trade("HELD", "open", date(2026, 10, 5), 100.0, 1, "EUR", 95.0, 110.0)],
        date(2026, 10, 6),
    )
    report = build_report(
        _ranked_one("RANKED"), [], [], [], as_of=date(2026, 10, 6),
        positions=held,
        news={"RANKED": [], "HELD": [NewsItem(title="Held co. wins contract")]},
        filings={"HELD": [FilingItem(form="8-K", filed="2026-10-05", url=None)]},
    )

    assert "Held co. wins contract" in report
    assert "- **HELD**:\n  - 8-K (2026-10-05)" in report
