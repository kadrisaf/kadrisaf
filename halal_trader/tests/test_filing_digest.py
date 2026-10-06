from halal_trader.filing_digest import digest_filing
from halal_trader.filings import FilingItem

FILING = FilingItem(form="8-K", filed="2026-10-05", url="https://example.com/8k.htm")
LONG_TEXT = "Item 2.02 Results of Operations. " * 50


def test_digest_uses_fetched_text_and_returns_completion():
    captured = {}

    def complete(prompt):
        captured["prompt"] = prompt
        return "  Item 2.02: Q3 results; EPS guidance 13.76-13.96.  "

    out = digest_filing("ABBV", FILING, fetch=lambda u: LONG_TEXT, complete=complete)

    assert out == "Item 2.02: Q3 results; EPS guidance 13.76-13.96."
    assert "ABBV" in captured["prompt"] and "Item 2.02" in captured["prompt"]


def test_digest_degrades_to_none_on_short_text_missing_url_or_errors():
    assert digest_filing("X", FilingItem(form="8-K", filed="2026-10-05", url=None)) is None
    assert digest_filing("X", FILING, fetch=lambda u: "too short") is None

    def boom(u):
        raise RuntimeError("blocked")

    assert digest_filing("X", FILING, fetch=boom) is None
