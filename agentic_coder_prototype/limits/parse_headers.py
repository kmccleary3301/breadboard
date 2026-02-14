"""Parse rate limit headers into a normalized snapshot.

This is best-effort: providers vary wildly. The output is shaped to feed the
`limits_update` CLI bridge event payload.
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Dict, List, Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_number(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_retry_after_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Spec allows either delta-seconds or an HTTP-date.
    try:
        seconds = float(raw)
        return max(0, int(seconds * 1000))
    except Exception:
        pass
    try:
        dt = datetime.datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S %Z")
        return max(0, int((dt.timestamp() - time.time()) * 1000))
    except Exception:
        return None


def _parse_reset_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Common forms: seconds since epoch, ISO-8601, or seconds-until-reset.
    num = _parse_number(raw)
    if num is not None:
        # Heuristic: values > 10^10 are probably ms epoch already.
        if num > 10_000_000_000:
            return int(num)
        # values > 10^9 likely seconds epoch.
        if num > 1_000_000_000:
            return int(num * 1000)
        # Otherwise treat as delta seconds.
        return _now_ms() + int(num * 1000)
    try:
        clean = raw.rstrip("Z")
        dt = datetime.datetime.fromisoformat(clean + ("+00:00" if "T" in clean and "+" not in clean else ""))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def parse_rate_limit_headers(headers: Dict[str, str], *, provider: str) -> Dict[str, Any] | None:
    if not headers:
        return None
    normalized = {str(k).lower(): str(v) for k, v in headers.items() if k}
    observed_at_ms = _now_ms()

    buckets: List[Dict[str, Any]] = []

    def _maybe_add_bucket(name: str, limit: Optional[float], remaining: Optional[float], reset_ms: Optional[int]) -> None:
        if limit is None and remaining is None and reset_ms is None:
            return
        buckets.append(
            {
                "name": name,
                "limit": limit,
                "remaining": remaining,
                "reset_at_ms": reset_ms,
            }
        )

    # Generic x-ratelimit-* patterns (common across OpenAI-like providers).
    _maybe_add_bucket(
        "requests",
        _parse_number(normalized.get("x-ratelimit-limit-requests") or normalized.get("x-ratelimit-limit-request")),
        _parse_number(normalized.get("x-ratelimit-remaining-requests") or normalized.get("x-ratelimit-remaining-request")),
        _parse_reset_ms(normalized.get("x-ratelimit-reset-requests") or normalized.get("x-ratelimit-reset-request")),
    )
    _maybe_add_bucket(
        "tokens",
        _parse_number(normalized.get("x-ratelimit-limit-tokens") or normalized.get("x-ratelimit-limit-token")),
        _parse_number(normalized.get("x-ratelimit-remaining-tokens") or normalized.get("x-ratelimit-remaining-token")),
        _parse_reset_ms(normalized.get("x-ratelimit-reset-tokens") or normalized.get("x-ratelimit-reset-token")),
    )

    # Anthropic-specific headers.
    _maybe_add_bucket(
        "requests",
        _parse_number(normalized.get("anthropic-ratelimit-requests-limit")),
        _parse_number(normalized.get("anthropic-ratelimit-requests-remaining")),
        _parse_reset_ms(normalized.get("anthropic-ratelimit-requests-reset")),
    )
    _maybe_add_bucket(
        "tokens",
        _parse_number(normalized.get("anthropic-ratelimit-tokens-limit")),
        _parse_number(normalized.get("anthropic-ratelimit-tokens-remaining")),
        _parse_reset_ms(normalized.get("anthropic-ratelimit-tokens-reset")),
    )

    retry_after_ms = _parse_retry_after_ms(normalized.get("retry-after"))

    if not buckets and retry_after_ms is None:
        return None

    # raw_headers are included but should be sanitized by downstream log/event tooling.
    return {
        "provider": str(provider),
        "observed_at_ms": observed_at_ms,
        "buckets": buckets,
        "retry_after_ms": retry_after_ms,
        "raw_headers": {k: v for k, v in normalized.items() if k != "authorization"},
    }

