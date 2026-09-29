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
