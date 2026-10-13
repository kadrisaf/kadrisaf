import pandas as pd

from halal_trader.data_provider import (
    NewsItem,
    StaticProvider,
    _parse_news_entries,
)


def test_parse_news_entries_handles_nested_content_schema():
    raw = [
        {
            "content": {
                "title": "Nvidia authorizes $150B buyback increase",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://example.com/nvda-buyback"},
                "pubDate": "2026-09-28T14:30:00Z",
            }
        }
    ]

    items = _parse_news_entries(raw, limit=3)

    assert items == [
        NewsItem(
            title="Nvidia authorizes $150B buyback increase",
            publisher="Reuters",
            link="https://example.com/nvda-buyback",
            published="2026-09-28",
        )
    ]


def test_parse_news_entries_handles_flat_legacy_schema():
    raw = [
        {
            "title": "Microsoft beats on Azure growth",
            "publisher": "CNBC",
            "link": "https://example.com/msft-azure",
            "providerPublishTime": 1758067200,  # 2025-09-17T00:00:00Z
        }
    ]

    items = _parse_news_entries(raw, limit=3)

    assert len(items) == 1
    assert items[0].title == "Microsoft beats on Azure growth"
    assert items[0].publisher == "CNBC"
    assert items[0].published == "2025-09-17"


def test_parse_news_entries_respects_limit_and_skips_titleless_entries():
    raw = [
        {"title": "First"},
        {"title": None},  # no title -- skipped, not counted
        {"title": "Second"},
        {"title": "Third"},
    ]

    items = _parse_news_entries(raw, limit=2)

    assert [i.title for i in items] == ["First", "Second"]


def test_parse_news_entries_tolerates_malformed_entries():
    raw = ["not a dict", {}, {"content": "also not a dict"}]

    items = _parse_news_entries(raw, limit=3)

    assert items == []


def test_static_provider_returns_configured_news_and_empty_list_by_default():
    news_item = NewsItem(title="Some headline")
    provider = StaticProvider(
        fundamentals={},
        histories={},
        news={"GOOD": [news_item]},
    )

    assert provider.get_recent_news("GOOD") == [news_item]
    assert provider.get_recent_news("UNKNOWN") == []


def _tz_history(day, tz):
    prev = pd.DatetimeIndex([pd.Timestamp("2026-10-01", tz=tz), pd.Timestamp(day, tz=tz)])
    return pd.DataFrame({"Close": [1.0, 2.0]}, index=prev)


def test_drop_incomplete_bar_drops_todays_partial_us_bar_before_close():
    from datetime import datetime, timezone
    from halal_trader.data_provider import drop_incomplete_bar

    hist = _tz_history("2026-10-02", "America/New_York")
    now = datetime(2026, 10, 2, 13, 51, tzinfo=timezone.utc)  # 09:51 New York
    assert len(drop_incomplete_bar(hist, now)) == 1


def test_drop_incomplete_bar_keeps_bar_after_close_or_if_not_today():
    from datetime import datetime, timezone
    from halal_trader.data_provider import drop_incomplete_bar

    hist = _tz_history("2026-10-02", "America/New_York")
    after_close = datetime(2026, 10, 2, 21, 0, tzinfo=timezone.utc)  # 17:00 New York
    assert len(drop_incomplete_bar(hist, after_close)) == 2
    next_morning = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
    assert len(drop_incomplete_bar(hist, next_morning)) == 2


def test_drop_incomplete_bar_uses_later_cutoff_outside_the_us_and_ignores_naive():
    from datetime import datetime, timezone
    from halal_trader.data_provider import drop_incomplete_bar

    hist = _tz_history("2026-10-02", "Europe/Berlin")
    at_1700 = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)  # 17:00 Berlin
    assert len(drop_incomplete_bar(hist, at_1700)) == 1
    naive = pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.date_range("2026-10-01", periods=2))
    assert len(drop_incomplete_bar(naive)) == 2


RSS_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>AbbVie beats on Q3</title><link>https://example.com/a</link>
<pubDate>Mon, 05 Oct 2026 20:45:04 +0000</pubDate></item>
<item><title>Second headline</title><link>https://example.com/b</link>
<pubDate>not a date</pubDate></item>
<item><title>Third</title></item>
<item><title>Fourth beyond limit</title></item>
</channel></rss>"""


def test_rss_fallback_parses_titles_links_and_dates():
    from halal_trader.data_provider import _rss_news

    items = _rss_news("ABBV", limit=3, fetch=lambda s: RSS_XML)

    assert [i.title for i in items] == ["AbbVie beats on Q3", "Second headline", "Third"]
    assert items[0].link == "https://example.com/a"
    assert items[0].published == "2026-10-05"
    assert items[1].published is None  # bad date degrades, doesn't raise
    assert all(i.publisher == "Yahoo Finance RSS" for i in items)


def test_rss_fallback_failure_returns_empty():
    from halal_trader.data_provider import _rss_news

    def boom(sym):
        raise RuntimeError("blocked")

    assert _rss_news("ABBV", fetch=boom) == []


def test_merge_news_dedupes_by_title_and_caps_at_limit():
    from halal_trader.data_provider import _merge_news

    pool = [
        NewsItem(title="Big AI deal announced"),
        NewsItem(title="  big ai DEAL announced "),  # dup after normalization
        NewsItem(title="Second story"),
        NewsItem(title="Third story"),
        NewsItem(title="Fourth story"),
    ]
    merged = _merge_news(pool, limit=3)
    assert [i.title for i in merged] == ["Big AI deal announced", "Second story", "Third story"]


GOOGLE_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Marvell slides on AI costs - Reuters</title><link>https://example.com/g1</link>
<pubDate>Mon, 12 Oct 2026 18:00:00 +0000</pubDate><source url="https://reuters.com">Reuters</source></item>
<item><title>No source headline</title></item>
</channel></rss>"""


def test_google_news_parses_and_strips_publisher_suffix():
    from halal_trader.data_provider import _google_news

    items = _google_news("MRVL", limit=3, fetch=lambda s: GOOGLE_XML)
    assert items[0].title == "Marvell slides on AI costs"
    assert items[0].publisher == "Reuters" and items[0].published == "2026-10-12"
    assert items[1].title == "No source headline"

    def boom(s):
        raise RuntimeError("blocked")

    assert _google_news("MRVL", fetch=boom) == []
