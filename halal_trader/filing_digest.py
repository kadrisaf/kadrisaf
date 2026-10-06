"""LLM digests of SEC filings for HELD positions.

Fetches the latest 8-K's text from EDGAR (free) and has the LLM compress it
into 2-3 factual sentences. Extraction over a document you already have the
link to -- not a prediction, not a signal, never scored. Held positions only,
one filing each, to keep cost and noise down (one Haiku call per digest,
fractions of a cent).
"""

from __future__ import annotations

import re
import sys
from typing import Callable, Optional

from .filings import FilingItem

MAX_DOC_CHARS = 12_000

_PROMPT = """Summarize this SEC filing in 2-3 plain factual sentences: which 8-K item(s),
what happened, and any concrete numbers or dates. Plain prose only -- no
headings, no bullets, no markdown. No opinion, no outlook, no advice. If the
text is boilerplate with no material event, say exactly that.

Filing for {symbol}, {form} filed {filed}:
{text}"""


def _fetch_text(url: str) -> Optional[str]:
    import requests

    r = requests.get(
        url,
        headers={"User-Agent": "halal-trader research tool (kadri.safwen@gmail.com)"},
        timeout=15,
    )
    r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_DOC_CHARS]


def digest_filing(
    symbol: str,
    filing: FilingItem,
    api_key: Optional[str] = None,
    fetch: Optional[Callable[[str], Optional[str]]] = None,
    complete: Optional[Callable[[str], str]] = None,
) -> Optional[str]:
    """One short digest, or None on any failure (never raises). `fetch` and
    `complete` are injectable for tests."""
    if not filing.url:
        return None
    try:
        text = (fetch or _fetch_text)(filing.url)
        if not text or len(text) < 200:
            print(f"[digest] {symbol}: filing text too short/empty", file=sys.stderr)
            return None
        prompt = _PROMPT.format(symbol=symbol, form=filing.form, filed=filing.filed, text=text)
        if complete is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            def complete(p):
                resp = client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=300,
                    messages=[{"role": "user", "content": p}],
                )
                return resp.content[0].text
        out = complete(prompt).strip()
        print(f"[digest] {symbol}: {filing.form} {filing.filed} digested", file=sys.stderr)
        return out or None
    except Exception as exc:
        print(f"[digest] {symbol}: failed: {exc}", file=sys.stderr)
        return None
