from datetime import date

from halal_trader.report import build_report
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

    report = build_report(
        ranked=[(screen, signal)],
        screened_out=[ScreenResult(symbol="BNK", compliant=False, reasons=["excluded business activity: bank"])],
        skipped=["ZZZ"],
        as_of=date(2026, 1, 5),
    )

    assert "2026-01-05" in report
    assert "TST" in report
    assert "Not financial or Shariah advice" in report
    assert "BNK" in report
    assert "ZZZ" in report


def test_report_handles_empty_ranked_list():
    report = build_report(ranked=[], screened_out=[], skipped=[])

    assert "No symbol passed" in report
