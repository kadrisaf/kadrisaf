"""LLM-based sentiment/event tagging over already-fetched headlines.

This is a feature-extraction step, not a predictor: it summarizes what a
handful of headlines say (sentiment direction, event type, confidence) in a
structured, auditable form. It never touches the quant score in signals.py
and is never combined into a single "probability of return" -- that would
need a backtested, calibrated model this project doesn't have the
historical data or infrastructure to build honestly. See ADVISORY_PROTOCOL.md
for why that line matters here.

Only runs for symbols that already qualified on the deterministic screen,
and only if ANTHROPIC_API_KEY is set -- otherwise the whole step is skipped
and the report looks exactly like it did before this existed.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Protocol

from .data_provider import NewsItem

_VALID_CONFIDENCE = {"low", "medium", "high"}


@dataclass
class SentimentTag:
    """A structured summary of what a symbol's recent headlines say.
    LLM-generated, unverified, purely informational -- never scored or
    blended with the quant signal."""
    sentiment: float  # -1.0 (very negative) .. 1.0 (very positive)
    event_type: str   # short label, e.g. "buyback", "earnings_beat", "guidance_cut"
    confidence: str   # "low" | "medium" | "high"
    rationale: str    # one sentence: what in the headlines drives this tag


class SentimentTagger(Protocol):
    def tag(self, symbol: str, headlines: List[NewsItem]) -> Optional[SentimentTag]:
        """Best-effort. Never raises -- returns None on any failure (no
        headlines, missing credentials, malformed model output, etc.)."""
        ...


def _build_prompt(symbol: str, headlines: List[NewsItem]) -> str:
    lines = [
        f"You are summarizing recent news for {symbol}, a stock that already passed "
        "a separate, deterministic quantitative screen (technical trend/momentum/volume "
        "filters computed directly from price data). Your only job is to summarize what "
        "these headlines say. Do not predict the stock's future price, do not recommend "
        "buying or selling, and do not use any knowledge beyond what's in the headlines "
        "below.",
        "",
        "Headlines:",
    ]
    for h in headlines:
        meta_parts = [p for p in (h.publisher, h.published) if p]
        meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
        lines.append(f"- {h.title}{meta}")
    lines += [
        "",
        "Respond with ONLY a JSON object, no other text, exactly this shape:",
        '{"sentiment": <float from -1.0 (very negative) to 1.0 (very positive)>, '
        '"event_type": "<short label, e.g. buyback, earnings_beat, earnings_miss, '
        'guidance_cut, product_news, macro, legal, other, or none>", '
        '"confidence": "<low, medium, or high>", '
        '"rationale": "<one sentence: what in the headlines drives this tag>"}',
    ]
    return "\n".join(lines)


def _parse_sentiment_response(text: str) -> Optional[SentimentTag]:
    """Parse the model's response defensively. Returns None (never raises)
    on anything that doesn't cleanly match the expected shape -- a
    malformed tag should silently disappear from the report, not break it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    sentiment = data.get("sentiment")
    event_type = data.get("event_type")
    confidence = data.get("confidence")
    rationale = data.get("rationale")

    if not isinstance(sentiment, (int, float)) or isinstance(sentiment, bool):
        return None
    if not (-1.0 <= float(sentiment) <= 1.0):
        return None
    if not isinstance(event_type, str) or not event_type.strip():
        return None
    if confidence not in _VALID_CONFIDENCE:
        return None
    if not isinstance(rationale, str) or not rationale.strip():
        return None

    return SentimentTag(
        sentiment=round(float(sentiment), 2),
        event_type=event_type.strip(),
        confidence=confidence,
        rationale=rationale.strip(),
    )


class AnthropicSentimentTagger:
    """Real implementation, via the Anthropic API. Uses a small/fast model
    since this is a bounded classification task over a handful of short
    headlines, not open-ended generation."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5"):
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def tag(self, symbol: str, headlines: List[NewsItem]) -> Optional[SentimentTag]:
        if not headlines:
            return None
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=300,
                messages=[{"role": "user", "content": _build_prompt(symbol, headlines)}],
            )
            text = "".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            )
        except Exception as exc:
            # A prior run produced zero tags across 3 real candidates with
            # no error output at all -- this was the missing diagnostic.
            # Likely cause that run: an invalid/unavailable model ID raised
            # here and was silently swallowed. Never let that happen again
            # without a trace.
            print(f"[sentiment] {symbol}: API call raised: {exc}", file=sys.stderr)
            return None

        tag = _parse_sentiment_response(text)
        if tag is None:
            print(
                f"[sentiment] {symbol}: got a response but couldn't parse a "
                f"tag from it -- raw text: {text[:500]!r}",
                file=sys.stderr,
            )
        else:
            print(
                f"[sentiment] {symbol}: tagged sentiment={tag.sentiment:+.2f} "
                f"event_type={tag.event_type} confidence={tag.confidence}",
                file=sys.stderr,
            )
        return tag
