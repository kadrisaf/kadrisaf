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
