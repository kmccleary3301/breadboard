"""Central redaction substrate (C-G0c).

Single source of truth for secret handling across durable outputs:

- ``SECRET_KEY_NAMES`` / ``is_secret_key``: canonical secret key-name registry
  replacing the per-module deny-lists that previously drifted
  (``run_logger.py``, ``api_recorder.py``, ``provider_dump.py``).
- ``SECRET_VALUE_PATTERNS``: well-known credential shapes caught by value,
  independent of key naming.
- ``register_secret_value``: process-local registry fed by the auth boundary
  at attach time, so scrubbing no longer depends on secrets being projected
  into named environment variables.
- ``scrub_text`` / ``scrub_headers`` / ``scrub_structure``: structured scrub
  API returning typed :class:`RedactionProblem` records, never raising.

All functions are deterministic and idempotent; scrubbing a scrubbed value is
a no-op.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

REDACTED = "***REDACTED***"

# Canonical lowered key names. Comparison normalizes "-" to "_", so each name
# is written once in underscore form. Reference fields (e.g. ``secret_ref``,
# ``token_type``) are intentionally not secrets and must not match.
SECRET_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "proxy_authorization",
        "bearer",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "private_key",
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "token",
        "x_api_key",
        "x_client_api_key",
        "x_goog_api_key",
        "openai_api_key",
        "openrouter_api_key",
        "anthropic_api_key",
        "google_api_key",
        "ciphertext",
    }
)

# Suffix rules: any key ending in one of these is treated as secret
# (``session_access_token``, ``gh_password`` ...). Exact non-secret names such
# as ``token_type`` do not end in these suffixes.
_SECRET_KEY_SUFFIXES: Tuple[str, ...] = (
    "_token",
    "_secret",
    "_password",
    "_api_key",
    "_credential",
    "_private_key",
)

SECRET_VALUE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),  # OpenAI/Anthropic-style keys
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key IDs
    re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"
    ),  # JWTs
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),  # Bearer credentials
)

_MIN_REGISTERED_LENGTH = 4

_registry_lock = threading.Lock()
_registered_values: dict[str, None] = {}


@dataclass(frozen=True)
class RedactionProblem:
    """Typed scrub finding; produced instead of raising."""

    code: str  # "secret_key" | "secret_value"
    path: str  # JSONPath-ish locator, e.g. "$.headers.x-api-key"
    detail: str  # human-oriented, never contains the secret itself


def register_secret_value(value: Any) -> None:
    """Register a known secret value (auth boundary feeds this at attach time)."""
    if not isinstance(value, str):
        return
    text = value.strip()
    if len(text) < _MIN_REGISTERED_LENGTH:
        return
    with _registry_lock:
        _registered_values[text] = None


def iter_registered_secret_values() -> tuple[str, ...]:
    with _registry_lock:
        return tuple(_registered_values)


def clear_registered_secret_values() -> None:
    """Test hook; production code never clears the registry."""
    with _registry_lock:
        _registered_values.clear()


def is_secret_key(name: Any) -> bool:
    text = str(name).strip().lower().replace("-", "_")
    if not text:
        return False
    if text in SECRET_KEY_NAMES:
        return True
    return text.endswith(_SECRET_KEY_SUFFIXES)


def scrub_text(text: str) -> str:
    """Replace registered secret values and well-known credential shapes."""
    if not isinstance(text, str) or not text:
        return text
    for secret in iter_registered_secret_values():
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern in SECRET_VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def scrub_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    """Scrub an HTTP header mapping: secret keys redacted, values pattern-scrubbed."""
    scrubbed, _problems = scrub_structure(dict(headers), path="$.headers")
    return scrubbed


def scrub_structure(value: Any, *, path: str = "$") -> tuple[Any, list[RedactionProblem]]:
    """Deep-scrub a JSON-like structure.

    Returns the scrubbed copy plus typed problems for every redaction made.
    Never raises; unknown types pass through untouched.
    """
    problems: list[RedactionProblem] = []
    scrubbed = _scrub_node(value, path, problems)
    return scrubbed, problems


def _scrub_node(value: Any, path: str, problems: list[RedactionProblem]) -> Any:
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            child = f"{path}.{key}"
            if is_secret_key(key):
                out[key] = REDACTED
                problems.append(
                    RedactionProblem("secret_key", child, "secret-named key redacted")
                )
            else:
                out[key] = _scrub_node(item, child, problems)
        return out
    if isinstance(value, (list, tuple)):
        items = [
            _scrub_node(item, f"{path}[{index}]", problems)
            for index, item in enumerate(value)
        ]
        return items if isinstance(value, list) else tuple(items)
    if isinstance(value, str):
        cleaned = scrub_text(value)
        if cleaned != value:
            problems.append(
                RedactionProblem("secret_value", path, "secret value scrubbed from text")
            )
        return cleaned
    return value
