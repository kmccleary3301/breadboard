"""Central redaction substrate (C-G0c).

Single source of truth for secret handling across durable outputs:

- ``SECRET_KEY_NAMES`` / ``is_secret_key``: canonical secret key-name registry
  replacing the per-module deny-lists that previously drifted
  (``run_logger.py``, ``api_recorder.py``, ``provider_dump.py``).
- ``SECRET_VALUE_PATTERNS``: well-known credential shapes caught by value,
  independent of key naming.
- ``secret_value_scope``: reference-counted, operation-local exact-value
  redaction without projecting secrets into named environment variables or
  retaining them after the provider operation.
- ``scrub_text`` / ``scrub_headers`` / ``scrub_structure``: structured scrub
  API returning typed :class:`RedactionProblem` records, never raising.

All functions are deterministic and idempotent; scrubbing a scrubbed value is
a no-op.
"""

from __future__ import annotations

import re
import threading
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

REDACTED = "***REDACTED***"

_AUTH_SECRET_KEYS = frozenset(
    {
        "authorization_code",
        "code",
        "code_challenge",
        "code_verifier",
        "device_auth_id",
        "id_token",
        "refresh_token",
        "state",
        "user_code",
        "verifier",
    }
)
_AUTH_URL_KEYS = frozenset({"authorization_url", "callback_url", "redirect_uri"})


def scrub_auth_url(value: str) -> str:
    """Keep an auth endpoint usable while removing one-time query material."""
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        cleaned = []
        for key, item in query:
            normalized = key.strip().lower().replace("-", "_")
            cleaned.append(
                (
                    key,
                    REDACTED
                    if normalized in _AUTH_SECRET_KEYS or is_secret_key(normalized)
                    else scrub_text(item),
                )
            )
        return scrub_text(
            urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urllib.parse.urlencode(cleaned),
                    "",
                )
            )
        )
    except Exception:
        return REDACTED


def safe_exception_message(error: BaseException, *, operation: str = "provider operation") -> str:
    """Return actionable exception metadata without copying provider text."""
    return f"{operation} failed ({error.__class__.__name__})"

def scrub_exception_in_place(error: BaseException) -> BaseException:
    """Scrub exception fields before an operation releases its secret scope."""
    try:
        scrubbed_args, _ = scrub_structure(error.args, path="$.exception.args")
        error.args = tuple(scrubbed_args)
    except Exception:
        pass
    try:
        details = getattr(error, "details", None)
        if isinstance(details, Mapping):
            scrubbed_details, _ = scrub_structure(
                details,
                path="$.exception.details",
            )
            error.details = scrubbed_details
    except Exception:
        pass
    try:
        notes = getattr(error, "__notes__", None)
        if isinstance(notes, list):
            error.__notes__ = [scrub_text(str(note)) for note in notes]
    except Exception:
        pass
    return error

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
        "authorization_code",
        "code_challenge",
        "code_verifier",
        "device_auth_id",
        "user_code",
        "verifier",
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
_registered_values: dict[str, int] = {}


def _normalized_secret_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if len(text) >= _MIN_REGISTERED_LENGTH else None


@dataclass(frozen=True)
class RedactionProblem:
    """Typed scrub finding; produced instead of raising."""

    code: str  # "secret_key" | "secret_value"
    path: str  # JSONPath-ish locator, e.g. "$.headers.x-api-key"
    detail: str  # human-oriented, never contains the secret itself


def _register_secret_value(value: Any) -> None:
    """Register a known secret value for the active operation."""
    text = _normalized_secret_value(value)
    if text is None:
        return
    with _registry_lock:
        _registered_values[text] = _registered_values.get(text, 0) + 1


def _unregister_secret_value(value: Any) -> None:
    """Release one registration without disrupting overlapping operations."""
    text = _normalized_secret_value(value)
    if text is None:
        return
    with _registry_lock:
        count = _registered_values.get(text, 0)
        if count <= 1:
            _registered_values.pop(text, None)
        else:
            _registered_values[text] = count - 1


@contextmanager
def secret_value_scope(*values: Any):
    """Keep exact-value redaction active only while an operation uses secrets."""
    registered = [
        text
        for value in values
        if (text := _normalized_secret_value(value)) is not None
    ]
    for text in registered:
        _register_secret_value(text)
    try:
        yield
    finally:
        for text in registered:
            _unregister_secret_value(text)


def iter_registered_secret_values() -> tuple[str, ...]:
    with _registry_lock:
        return tuple(_registered_values)


def contains_registered_secret_text(value: Any) -> bool:
    """Return whether text contains an active exact secret."""
    if not isinstance(value, str):
        return False
    return any(secret in value for secret in iter_registered_secret_values())


def contains_registered_secret_mapping_key(value: Any) -> bool:
    """Return whether a nested mapping key contains an active exact secret."""
    secrets = iter_registered_secret_values()
    if not secrets:
        return False
    seen: set[int] = set()

    def visit(item: Any) -> bool:
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen:
                return False
            seen.add(identity)
            return any(
                any(secret in str(key) for secret in secrets) or visit(child)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen:
                return False
            seen.add(identity)
            return any(visit(child) for child in item)
        return False

    return visit(value)


def clear_registered_secret_values() -> None:
    """Test hook; production code never clears the registry."""
    with _registry_lock:
        _registered_values.clear()


def _is_provider_auth_runtime_key(value: Any) -> bool:
    return "provider_auth_runtime" in str(value).split(".")


def contains_provider_auth_runtime(value: Any) -> bool:
    """Return whether a config contains transient provider credential material."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            if _is_provider_auth_runtime_key(name):
                return True
            if contains_provider_auth_runtime(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(contains_provider_auth_runtime(item) for item in value)
    return False


def strip_provider_auth_runtime(value: Any) -> Any:
    """Copy a config while removing transient provider credential subtrees."""
    if isinstance(value, Mapping):
        return {
            key: strip_provider_auth_runtime(item)
            for key, item in value.items()
            if not _is_provider_auth_runtime_key(key)
        }
    if isinstance(value, list):
        return [strip_provider_auth_runtime(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_provider_auth_runtime(item) for item in value)
    return value


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
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _AUTH_URL_KEYS and isinstance(item, str):
                cleaned = scrub_auth_url(item)
                out[key] = cleaned
                if cleaned != item:
                    problems.append(RedactionProblem("auth_url", child, "auth URL query material redacted"))
            elif is_secret_key(key):
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
    if isinstance(value, (bool, int, float)) and contains_registered_secret_text(
        str(value)
    ):
        problems.append(
            RedactionProblem(
                "secret_value",
                path,
                "secret value scrubbed from scalar",
            )
        )
        return REDACTED
    return value
