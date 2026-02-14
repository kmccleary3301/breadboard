from __future__ import annotations

from agentic_coder_prototype.limits.parse_headers import parse_rate_limit_headers


def test_parse_rate_limit_headers_openai_like() -> None:
    headers = {
        "x-ratelimit-limit-requests": "60",
        "x-ratelimit-remaining-requests": "12",
        "x-ratelimit-reset-requests": "10",
        "retry-after": "0.5",
        "authorization": "Bearer secret",
    }
    parsed = parse_rate_limit_headers(headers, provider="openai")
    assert parsed is not None
    assert parsed["provider"] == "openai"
    assert parsed["retry_after_ms"] is not None
    assert "authorization" not in (parsed.get("raw_headers") or {})


def test_parse_rate_limit_headers_anthropic_like() -> None:
    headers = {
        "anthropic-ratelimit-requests-limit": "200",
        "anthropic-ratelimit-requests-remaining": "100",
        "anthropic-ratelimit-requests-reset": "10",
    }
    parsed = parse_rate_limit_headers(headers, provider="anthropic")
    assert parsed is not None
    names = [b.get("name") for b in parsed.get("buckets") or []]
    assert "requests" in names

