"""Token-limit parser cloned from plugin 5137df72 parser.ts:12-209.

The provider boundary may already have redacted the body. In that case its
numeric overflow bounds preserve the same parser result without persisting text.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"(\d+)\s*tokens?\s*>\s*(\d+)\s*maximum",
    r"prompt.*?(\d+).*?tokens.*?exceeds.*?(\d+)",
    r"(\d+).*?tokens.*?limit.*?(\d+)",
    r"context.*?length.*?(\d+).*?maximum.*?(\d+)",
    r"max.*?context.*?(\d+).*?but.*?(\d+)",
))
_KEYWORDS = ("prompt is too long", "is too long", "context_length_exceeded", "max_tokens",
             "token limit", "context length", "too many tokens", "non-empty content")
_THINKING = tuple(re.compile(pattern, re.I) for pattern in (
    r"thinking.*first block", r"first block.*thinking", r"must.*start.*thinking",
    r"thinking.*redacted_thinking", r"expected.*thinking.*found", r"thinking.*disabled.*cannot.*contain",
))


def _matches(text: str) -> bool:
    return not any(pattern.search(text) for pattern in _THINKING) and any(word in text.lower() for word in _KEYWORDS)


def _bounds(text: str) -> dict[str, int] | None:
    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            first, second = map(int, match.groups())
            return {"overflow_tokens": max(first, second), "overflow_limit": min(first, second)}
    return None


def parse_token_limit_error(error: Any) -> dict[str, int] | None:
    """Return native parser bounds (zero when unknown), or no recovery signal."""
    if isinstance(error, BaseException):
        response = getattr(error, "response", None)
        payload = getattr(response, "content", None)
        if isinstance(payload, bytes):
            # This is the source APIError responseBody, before the generic
            # provider boundary replaces its wording with an overflow code.
            return parse_token_limit_error({"data": {"responseBody": payload.decode("utf-8", errors="replace")}})
        body = getattr(error, "body", None)
        if isinstance(body, (str, Mapping)):
            return parse_token_limit_error(body)
        cause = error.__cause__ or error.__context__
        if cause is not None:
            return parse_token_limit_error(cause)
        details = getattr(error, "details", None)
        if isinstance(details, Mapping):
            parsed = parse_token_limit_error(details)
            if parsed is not None:
                current, limit = details.get("overflow_tokens"), details.get("overflow_limit")
                if type(current) is int and type(limit) is int:
                    return {"overflow_tokens": current, "overflow_limit": limit}
                return parsed
        return parse_token_limit_error(str(error))
    if isinstance(error, str):
        if "non-empty content" in error.lower():
            return {"overflow_tokens": 0, "overflow_limit": 0}
        return (_bounds(error) or {"overflow_tokens": 0, "overflow_limit": 0}) if _matches(error) else None
    if not isinstance(error, Mapping):
        return None
    data = error.get("data")
    data = data if isinstance(data, Mapping) else {}
    nested = error.get("error")
    nested = nested if isinstance(nested, Mapping) else {}
    inner = nested.get("error")
    inner = inner if isinstance(inner, Mapping) else {}
    response = data.get("responseBody")
    candidates = (response, error.get("message"), nested.get("message"), error.get("body"),
                  error.get("details"), error.get("reason"), error.get("description"),
                  inner.get("message"), data.get("message"), data.get("error"))
    texts = [text for text in candidates if isinstance(text, str)]
    if not texts:
        # The stock parser has this JSON fallback; safe provider codes use it.
        try:
            rendered = json.dumps(dict(error))
        except (TypeError, ValueError):
            return None
        if _matches(rendered):
            texts.append(rendered)
    if not _matches(" ".join(texts)):
        return None
    if isinstance(response, str):
        for pattern in (r"data:\s*(\{[\s\S]*\})\s*$", r'(\{"type"\s*:\s*"error"[\s\S]*\})', r'(\{[\s\S]*"error"[\s\S]*\})'):
            match = re.search(pattern, response, re.M)
            if match:
                try:
                    body = json.loads(match[1])
                    detail = body.get("error") or {}
                    bounds = _bounds(detail.get("message") or "")
                    if bounds is not None:
                        return bounds
                except (ValueError, TypeError, AttributeError):
                    pass
        try:
            body = json.loads(response)
            if isinstance(body.get("message"), str) and _matches(body["message"]):
                return {"overflow_tokens": 0, "overflow_limit": 0}
        except (ValueError, TypeError, AttributeError):
            pass
    for text in texts:
        bounds = _bounds(text)
        if bounds is not None:
            return bounds
    return {"overflow_tokens": 0, "overflow_limit": 0}
