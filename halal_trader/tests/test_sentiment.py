from halal_trader.data_provider import NewsItem
from halal_trader.sentiment import (
    SentimentTag,
    _build_prompt,
    _parse_sentiment_response,
)


def _headline(**overrides):
    base = dict(title="Nvidia authorizes $150B buyback", publisher="Reuters", published="2026-09-28")
    base.update(overrides)
    return NewsItem(**base)


def test_build_prompt_includes_symbol_and_headlines():
    prompt = _build_prompt("NVDA", [_headline()])

    assert "NVDA" in prompt
    assert "Nvidia authorizes $150B buyback" in prompt
    assert "Reuters" in prompt
    assert "do not predict" in prompt.lower()


def test_parse_sentiment_response_accepts_well_formed_json():
    text = (
        '{"sentiment": 0.8, "event_type": "buyback", "confidence": "high", '
        '"rationale": "Large buyback authorization signals capital return confidence."}'
    )

    tag = _parse_sentiment_response(text)

    assert tag == SentimentTag(
        sentiment=0.8,
        event_type="buyback",
        confidence="high",
        rationale="Large buyback authorization signals capital return confidence.",
    )


def test_parse_sentiment_response_extracts_json_from_surrounding_prose():
    text = (
        "Here is the summary:\n"
        '{"sentiment": -0.3, "event_type": "guidance_cut", "confidence": "medium", '
        '"rationale": "Lowered outlook."}\n'
        "Let me know if you need anything else."
    )

    tag = _parse_sentiment_response(text)

    assert tag is not None
    assert tag.sentiment == -0.3
    assert tag.event_type == "guidance_cut"


def test_parse_sentiment_response_rejects_out_of_range_sentiment():
    text = '{"sentiment": 1.5, "event_type": "buyback", "confidence": "high", "rationale": "x"}'

    assert _parse_sentiment_response(text) is None


def test_parse_sentiment_response_rejects_invalid_confidence():
    text = '{"sentiment": 0.5, "event_type": "buyback", "confidence": "very_high", "rationale": "x"}'

    assert _parse_sentiment_response(text) is None


def test_parse_sentiment_response_rejects_missing_fields():
    assert _parse_sentiment_response('{"sentiment": 0.5}') is None


def test_parse_sentiment_response_rejects_malformed_json():
    assert _parse_sentiment_response("not json at all") is None


def test_parse_sentiment_response_rejects_boolean_sentiment():
    # bool is a subclass of int in Python -- explicitly guard against it,
    # since `isinstance(True, (int, float))` is True and would otherwise
    # silently accept `"sentiment": true` as 1.0.
    text = '{"sentiment": true, "event_type": "buyback", "confidence": "high", "rationale": "x"}'

    assert _parse_sentiment_response(text) is None
