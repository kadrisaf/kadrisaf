import pandas as pd

from halal_trader.cli import run
from halal_trader.data_provider import CompanyFundamentals, StaticProvider
from halal_trader.tests.test_signals import UPTREND_PATTERN, _make_ohlcv


def _fundamentals(symbol, **overrides):
    base = dict(
        symbol=symbol,
        sector="Technology",
        industry="Software—Application",
        market_cap=100_000_000_000,
        total_debt=1_000_000_000,
        cash_and_short_term_investments=2_000_000_000,
        receivables=1_000_000_000,
        currency="USD",
    )
    base.update(overrides)
    return CompanyFundamentals(**base)


def test_run_screens_signals_and_ranks_end_to_end():
    good_history = _make_ohlcv(UPTREND_PATTERN, last_volume_multiple=2.2)
    flat_history = pd.DataFrame(
        {
            "Open": [100.0] * 90,
            "High": [100.0] * 90,
            "Low": [100.0] * 90,
            "Close": [100.0] * 90,
            "Volume": [1_000_000.0] * 90,
        },
        index=pd.date_range("2024-01-01", periods=90, freq="B"),
    )

    provider = StaticProvider(
        fundamentals={
            "GOOD": _fundamentals("GOOD"),
            "BANK": _fundamentals("BANK", sector="Financial Services", industry="Banks—Regional"),
            "FLAT": _fundamentals("FLAT"),
        },
        histories={
            "GOOD": good_history,
            "FLAT": flat_history,
        },
    )

    ranked, screened_out, skipped = run(["GOOD", "BANK", "FLAT", "MISSING"], top=5, provider=provider)

    assert [sig.symbol for _, sig in ranked] == ["GOOD"]
    assert [s.symbol for s in screened_out] == ["BANK"]
    assert skipped == ["MISSING"]
    # FLAT passed the Shariah screen but never qualifies on the trade signal
    # (no volatility/trend at all), so it's simply absent from `ranked`.
