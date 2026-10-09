"""Context-overflow classification for provider failures.

Patterns are ported from OMP ``packages/ai/src/error/flags.ts``
(``CONTEXT_OVERFLOW_EVIDENCE_PATTERNS``). ``context_length_exceeded`` is the
code the verl_wrapper episode endpoint returns in eval mode when the policy
context is full; harnesses are expected to compact and resend.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

CONTEXT_OVERFLOW_CODES = frozenset(
    {
        "context_length_exceeded",
        "context_window_exceeded",
        "model_context_window_exceeded",
        "context_overflow",
        "prompt_too_long",
    }
)

_EVIDENCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"prompt is too long",
        r"input is too long for requested model",
        r"exceeds the context window",
        r"input token count.*exceeds the maximum",
        r"maximum prompt length is \d+",
        r"reduce the length of the messages",
        r"maximum context length is \d+ tokens",
        r"exceeds the available context size",
        r"requested tokens?.*exceed.*context (window|length|size)",
        r"context (window|length|size).*(exceeded|overflow|too small)",
        r"(prompt|input).*(too long|too large).*(context|n_ctx)",
        r"requested tokens?.*(exceeds?|greater than).*(n_ctx|context)",
        r"greater than the context length",
        r"context window exceeds limit",
        r"exceeded model token limit",
        r"context[_ ]length[_ ]exceeded",
        r"too many tokens",
        r"token limit exceeded",
        r"request_too_large[^\n]*\btokens?\b",
        r"\btokens?\b[^\n]*request_too_large",
        r"model_context_window_exceeded",
        r"prompt filled the context window",
        r"exceeds the limit of \d+ tokens?\b",
        r"chat history exceeds the \d+-message limit",
    )
)
_GENERIC_LIMIT_PATTERN = re.compile(r"exceeds the limit of \d+", re.IGNORECASE)
_NO_BODY_PATTERN = re.compile(r"\b4(00|13)\s*(status code)?\s*\(no body\)", re.IGNORECASE)


def text_indicates_context_overflow(text: str) -> bool:
    if not text:
        return False
    return (
        any(pattern.search(text) for pattern in _EVIDENCE_PATTERNS)
        or _GENERIC_LIMIT_PATTERN.search(text) is not None
        or _NO_BODY_PATTERN.search(text) is not None
    )


def _codes(details: Mapping[str, Any]) -> Iterable[str]:
    for key in ("code", "error_code", "type", "classification", "finish_reason"):
        value = details.get(key)
        if isinstance(value, str):
            yield value
    error = details.get("error")
    if isinstance(error, Mapping):
        yield from _codes(error)
    body = details.get("body")
    if isinstance(body, Mapping):
        yield from _codes(body)


def _texts(details: Mapping[str, Any]) -> Iterable[str]:
    for key in ("message", "body_text", "response_body_text", "body", "detail", "text"):
        value = details.get(key)
        if isinstance(value, str):
            if key == "response_body_text":
                try:
                    parsed = json.loads(value)
                except ValueError:
                    parsed = None
                if isinstance(parsed, Mapping):
                    if any(code.lower() in CONTEXT_OVERFLOW_CODES for code in _codes(parsed)):
                        yield "context_length_exceeded"
                    yield from _texts(parsed)
            yield value
    error = details.get("error")
    if isinstance(error, Mapping):
        yield from _texts(error)
    elif isinstance(error, str):
        yield error
    body = details.get("body")
    if isinstance(body, Mapping):
        yield from _texts(body)


def is_context_overflow(error: Any) -> bool:
    """True when an exception, mapping, or text reports context exhaustion.

    Walks ``__cause__``/``__context__`` chains and ``details`` mappings
    (``ProviderRuntimeError.details``).
    """
    seen: set[int] = set()
    link: Any = error
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        if isinstance(link, str):
            return text_indicates_context_overflow(link)
        if isinstance(link, Mapping):
            return any(code.lower() in CONTEXT_OVERFLOW_CODES for code in _codes(link)) or any(
                text_indicates_context_overflow(text) for text in _texts(link)
            )
        details = getattr(link, "details", None)
        if isinstance(details, Mapping) and (
            any(code.lower() in CONTEXT_OVERFLOW_CODES for code in _codes(details))
            or any(text_indicates_context_overflow(text) for text in _texts(details))
        ):
            return True
        if isinstance(link, BaseException) and text_indicates_context_overflow(str(link)):
            return True
        link = getattr(link, "__cause__", None) or getattr(link, "__context__", None)
    return False


OVERFLOW_ERROR_CODE = "context_length_exceeded"


def provider_overflow_details(exc: BaseException) -> dict[str, Any] | None:
    """Safe ``ProviderRuntimeError.details`` for an SDK/HTTP overflow error.

    Runtimes do not copy provider text into their errors (credential
    redaction), so overflow evidence must be classified where the SDK
    exception is caught. The returned mapping carries only a stable code and
    the HTTP status; callers merge it into the details they raise with.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status not in (400, 413, 422):
        return None
    candidates: list[Any] = []
    body = getattr(exc, "body", None)
    if body is not None:
        candidates.append(body)
    message = getattr(exc, "message", None)
    if isinstance(message, str):
        candidates.append(message)
    candidates.append(str(exc))
    if not any(is_context_overflow(candidate) for candidate in candidates):
        return None
    details: dict[str, Any] = {"code": OVERFLOW_ERROR_CODE, "classification": "context_overflow"}
    if isinstance(status, int):
        details["status_code"] = status
    return details


def overflow_http_details(status_code: Any, body: Any) -> dict[str, Any] | None:
    """Same as :func:`provider_overflow_details` for a raw HTTP status and body."""
    if isinstance(status_code, int) and status_code not in (400, 413, 422):
        return None
    parsed: Any = body
    if isinstance(body, (bytes, bytearray)):
        parsed = bytes(body).decode("utf-8", "replace")
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except ValueError:
            pass
    if not is_context_overflow(parsed):
        return None
    details: dict[str, Any] = {"code": OVERFLOW_ERROR_CODE, "classification": "context_overflow"}
    if isinstance(status_code, int):
        details["status_code"] = status_code
    return details
