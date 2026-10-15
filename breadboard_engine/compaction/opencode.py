"""Native overflow classification from OpenCode 715b844c provider/error.ts.

The plugin's token-limit parser is separate. In particular, native OpenCode
never promotes ``too many tokens`` alone to context overflow.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"prompt is too long",
    r"input is too long for requested model",
    r"exceeds the context window",
    r"input token count.*exceeds the maximum",
    r"maximum prompt length is \d+",
    r"reduce the length of the messages",
    r"maximum context length is \d+ tokens",
    r"exceeds the limit of \d+",
    r"exceeds the available context size",
    r"greater than the context length",
    r"context window exceeds limit",
    r"exceeded model token limit",
    r"context[_ ]length[_ ]exceeded",
    r"request entity too large",
    r"^4(00|13)\s*(status code)?\s*\(no body\)",
))


def is_context_overflow(error: Any) -> bool:
    """Classify actual provider evidence, not the generic sanitized code.

    Runtime errors retain the original SDK exception as cause/context even
    when its text is omitted from the public error. Use that in-memory
    evidence without copying it into the compaction ledger or provider wire.
    """
    seen: set[int] = set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        status = getattr(error, "status_code", None)
        if status == 413:
            return True
        body = getattr(error, "body", None)
        if body is not None:
            return is_context_overflow(body)
        cause = error.__cause__ or error.__context__
        if cause is not None:
            error = cause
            continue
        details = getattr(error, "details", None)
        if isinstance(details, Mapping) and details.get("status_code") == 413:
            return True
        if isinstance(details, Mapping) and isinstance(details.get("message"), str):
            return is_context_overflow(details)
        error = str(error)
        break
    if isinstance(error, Mapping):
        if error.get("name") == "ContextOverflowError":
            return True
        if error.get("type") == "error" and isinstance(error.get("error"), Mapping):
            if error["error"].get("code") == "context_length_exceeded":
                return True
        data = error.get("data")
        if isinstance(data, Mapping):
            error = data
        if error.get("statusCode", error.get("status_code")) == 413:
            return True
        nested = error.get("error")
        if isinstance(nested, Mapping):
            error = nested
        error = error.get("message", nested if isinstance(nested, str) else "")
    return isinstance(error, str) and any(pattern.search(error) for pattern in _PATTERNS)
