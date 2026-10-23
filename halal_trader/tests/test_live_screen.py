import pandas as pd

from halal_trader.live_screen import (
    compliant_symbols_from_report,
    provisional_frame,
    session_elapsed_fraction,
)
from halal_trader.tests.test_signals import UPTREND_PATTERN, _make_ohlcv


def test_session_fraction_handles_open_close_and_overnight():
    assert session_elapsed_fraction(9 * 60) is None            # pre-open
    assert session_elapsed_fraction(16 * 60 + 30) is None      # after close
    assert abs(session_elapsed_fraction(12 * 60 + 45) - 0.5) < 0.01  # midday
    assert session_elapsed_fraction(9 * 60 + 31) == 0.05       # floor just after open


def test_symbol_session_routes_eu_and_us_markets():
    from datetime import datetime, timezone
    from halal_trader.live_screen import symbol_session

    # 13:00 UTC = 15:00 Berlin (EU open), 09:00 New York (US still closed)
    t = datetime(2026, 10, 23, 13, 0, tzinfo=timezone.utc)
    assert symbol_session("SAP.DE", t) is not None
    assert symbol_session("AAPL", t) is None
    # 20:00 UTC = 22:00 Berlin (EU closed), 16:00 New York (US last minute)
    t2 = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)
    assert symbol_session("SAP.DE", t2) is None
    assert symbol_session("AAPL", t2) is not None


def test_provisional_frame_scales_partial_volume():
    hist = _make_ohlcv(UPTREND_PATTERN, n=80)
    frame = provisional_frame(hist, live_price=123.45, partial_volume=500_000, elapsed=0.5)
    assert len(frame) == len(hist) + 1
    assert frame["Close"].iloc[-1] == 123.45
    assert frame["Volume"].iloc[-1] == 1_000_000  # 500k at half-session -> 1M est


REPORT = """# x
| # | Symbol | Close |
|---|---|---|
| 1 | **WAT** | 455.59 |
| 2 | **AAPL** | 348.22 |

## Shariah-compliant, but no qualifying setup today

- **MSFT**: relative volume 0.9x
- **GOOGL**: not in a confirmed short-term uptrend

## Excluded on Shariah screen

- **ORCL**: debt/market-cap 41% >= 33%
"""


def test_compliant_symbols_parsed_from_both_sections_not_exclusions(tmp_path):
    f = tmp_path / "r.md"
    f.write_text(REPORT)
    syms = compliant_symbols_from_report(str(f))
    assert syms == ["WAT", "AAPL", "MSFT", "GOOGL"]
    assert "ORCL" not in syms
