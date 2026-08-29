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

import base64
import binascii
import re
import urllib.parse
from contextlib import contextmanager
from contextvars import ContextVar
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


def safe_exception_message(
    error: BaseException, *, operation: str = "provider operation"
) -> str:
    """Return actionable exception metadata without copying provider text."""
    return f"{operation} failed ({error.__class__.__name__})"


def _fail_closed_short_secret_strings(value: Any) -> Any:
    short_secrets = tuple(
        secret for secret in iter_registered_secret_values() if len(secret) < 3
    )
    if not short_secrets:
        return value

    def visit(item: Any, seen: set[int]) -> Any:
        if isinstance(item, str):
            if any(secret in item for secret in short_secrets):
                return REDACTED
            return item
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen:
                return REDACTED
            seen.add(identity)
            try:
                return {key: visit(child, seen) for key, child in item.items()}
            finally:
                seen.remove(identity)
        if isinstance(item, list):
            identity = id(item)
            if identity in seen:
                return REDACTED
            seen.add(identity)
            try:
                return [visit(child, seen) for child in item]
            finally:
                seen.remove(identity)
        if isinstance(item, tuple):
            identity = id(item)
            if identity in seen:
                return REDACTED
            seen.add(identity)
            try:
                return tuple(visit(child, seen) for child in item)
            finally:
                seen.remove(identity)
        return item

    return visit(value, set())


def scrub_exception_in_place(error: BaseException) -> BaseException:
    """Scrub exception fields before an operation releases its secret scope."""
    try:
        scrubbed_args, _ = scrub_structure(
            _fail_closed_short_secret_strings(error.args),
            path="$.exception.args",
        )
        error.args = tuple(scrubbed_args)
    except Exception:
        pass
    try:
        details = getattr(error, "details", None)
        if isinstance(details, Mapping):
            control_fields = {
                key: value
                for key, value in details.items()
                if (
                    key == "classification"
                    and value == "rate_limited"
                    and not contains_registered_secret_identity(str(value))
                    or key
                    in {
                        "status_code",
                        "http_status",
                        "retry_after",
                        "retry_after_seconds",
                    }
                    and type(value) in {int, float}
                    and not contains_registered_secret_identity(str(value))
                )
            }
            scrubbed_details, _ = scrub_structure(
                _fail_closed_short_secret_strings(details),
                path="$.exception.details",
                identity_mapping_keys=True,
            )
            if isinstance(scrubbed_details, dict):
                scrubbed_details.update(control_fields)
            error.details = scrubbed_details
    except Exception:
        pass
    try:
        notes = getattr(error, "__notes__", None)
        if isinstance(notes, list):
            error.__notes__ = [
                scrub_text(str(_fail_closed_short_secret_strings(note)))
                for note in notes
            ]
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
    "_auth",
    "_authorization",
    "_cookie",
)

_CREDENTIAL_HEADER_WORDS = frozenset(
    {
        "api",
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "key",
        "password",
        "secret",
        "token",
    }
)

_CREDENTIAL_HEADER_SUFFIXES: Tuple[str, ...] = (
    "apikey",
    "auth",
    "authentication",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
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

MIN_REGISTERED_SECRET_LENGTH = 4
_MIN_REGISTERED_SECRET_SUBSTRING_LENGTH = 3

_registered_values: ContextVar[tuple[str, ...]] = ContextVar(
    "breadboard_registered_secret_values",
    default=(),
)


def _normalized_secret_value(value: Any, *, allow_short: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    minimum = 1 if allow_short else MIN_REGISTERED_SECRET_LENGTH
    return text if len(text) >= minimum else None


@dataclass(frozen=True)
class RedactionProblem:
    """Typed scrub finding; produced instead of raising."""

    code: str  # "secret_key" | "secret_value"
    path: str  # JSONPath-ish locator, e.g. "$.headers.x-api-key"
    detail: str  # human-oriented, never contains the secret itself


@contextmanager
def secret_value_scope(*values: Any, allow_short: bool = False):
    """Keep exact-value redaction active only in the current operation context."""
    registered = tuple(
        text
        for value in values
        if (text := _normalized_secret_value(value, allow_short=allow_short))
        is not None
    )
    token = _registered_values.set((*_registered_values.get(), *registered))
    try:
        yield
    finally:
        _registered_values.reset(token)


def iter_registered_secret_values() -> tuple[str, ...]:
    """Return current-context secrets longest-first without duplicates."""
    return tuple(
        sorted(
            set(_registered_values.get()),
            key=lambda value: (-len(value), value),
        )
    )


def _registered_secret_occurs(text: str, secret: str) -> bool:
    return secret in text


def _registered_secret_occurs_in_identity(text: str, secret: str) -> bool:
    if len(secret) < MIN_REGISTERED_SECRET_LENGTH:
        return text == secret
    return secret in text


def contains_registered_secret_text(value: Any) -> bool:
    """Return whether text contains an active exact secret."""
    if not isinstance(value, str):
        return False
    return any(
        _registered_secret_occurs(value, secret)
        for secret in iter_registered_secret_values()
    )


def contains_registered_secret_identity(value: Any) -> bool:
    """Return whether an identity field exposes active credential material."""

    if not isinstance(value, str):
        return False
    return any(
        _registered_secret_occurs_in_identity(value, secret)
        for secret in iter_registered_secret_values()
    )


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
                any(
                    _registered_secret_occurs_in_identity(str(key), secret)
                    for secret in secrets
                )
                or visit(child)
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


def credential_secret_values(value: Any) -> tuple[str, ...]:
    """Extract credential-bearing strings from structured provider material."""
    values: list[str] = []
    included: set[str] = set()
    visited: set[int] = set()

    def add(item: Any) -> None:
        if isinstance(item, bool):
            return
        if isinstance(item, str):
            candidates = (item.strip(),)
        elif isinstance(item, int):
            integer = str(item)
            candidates = (integer, f"{integer}.0")
        elif isinstance(item, float):
            number = str(item)
            candidates = (number, str(int(item))) if item.is_integer() else (number,)
        else:
            return
        for text in candidates:
            if text and text not in included:
                included.add(text)
                values.append(text)

    def add_all(item: Any) -> None:
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in visited:
                return
            visited.add(identity)
            for child in item.values():
                add_all(child)
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in visited:
                return
            visited.add(identity)
            for child in item:
                add_all(child)
        else:
            add(item)

    def unquoted_component(value: str) -> str:
        text = value.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            return text[1:-1]
        return text

    def decoded_component(value: str) -> str:
        return urllib.parse.unquote(unquoted_component(value))

    def add_header(name: str, item: Any) -> None:
        normalized = name.strip().lower().replace("-", "_")
        add_all(item)
        if not isinstance(item, str):
            return
        text = item.strip()
        scheme, separator, credential = text.partition(" ")
        if separator and scheme.lower() in {"basic", "bearer", "token"}:
            add(credential)
            if scheme.lower() == "basic":
                try:
                    decoded = base64.b64decode(
                        credential,
                        validate=True,
                    ).decode("utf-8")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    pass
                else:
                    add(decoded)
                    username, password_separator, password = decoded.partition(":")
                    if password_separator:
                        add(username)
                        add(password)
        if normalized == "set_cookie" or normalized.endswith("_set_cookie"):
            _cookie_name, cookie_separator, cookie_value = text.split(";", 1)[
                0
            ].partition("=")
            if cookie_separator:
                add(unquoted_component(cookie_value))
                add(decoded_component(cookie_value))
        elif normalized == "cookie" or normalized.endswith("_cookie"):
            for part in text.split(";"):
                _cookie_name, cookie_separator, cookie_value = part.partition("=")
                if cookie_separator:
                    add(unquoted_component(cookie_value))
                    add(decoded_component(cookie_value))

    def add_url(item: Any) -> None:
        if not isinstance(item, str):
            return
        try:
            parsed = urllib.parse.urlsplit(item)
            if parsed.username:
                add(parsed.username)
                add(urllib.parse.unquote(parsed.username))
            if parsed.password:
                add(parsed.password)
                add(urllib.parse.unquote(parsed.password))
            for query_part in parsed.query.split("&"):
                raw_key, separator, raw_value = query_part.partition("=")
                if separator and is_secret_key(urllib.parse.unquote_plus(raw_key)):
                    add(raw_value)
                    add(urllib.parse.unquote_plus(raw_value))
        except (TypeError, ValueError):
            return

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in visited:
                return
            visited.add(identity)
            for key, child in item.items():
                normalized = str(key).strip().lower().replace("-", "_")
                if normalized == "headers" and isinstance(child, Mapping):
                    for header, header_value in child.items():
                        add_header(str(header), header_value)
                elif normalized == "base_url":
                    add_url(child)
                elif is_secret_key(key):
                    add_all(child)
                else:
                    visit(child)
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in visited:
                return
            visited.add(identity)
            for child in item:
                visit(child)

    visit(value)
    return tuple(values)


def clear_registered_secret_values() -> None:
    """Test hook; production code never clears the registry."""
    _registered_values.set(())


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


def scrub_text(
    text: str,
    *,
    exact_short_registered_secrets: bool = False,
) -> str:
    """Replace registered secret values and well-known credential shapes."""
    if not isinstance(text, str) or not text:
        return text
    occurs = (
        _registered_secret_occurs_in_identity
        if exact_short_registered_secrets
        else _registered_secret_occurs
    )
    for secret in iter_registered_secret_values():
        if occurs(text, secret):
            if (
                len(secret) < _MIN_REGISTERED_SECRET_SUBSTRING_LENGTH
                and not exact_short_registered_secrets
            ):
                return REDACTED
            text = text.replace(secret, REDACTED)
    for pattern in SECRET_VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def scrub_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    """Scrub an HTTP header mapping: secret keys redacted, values pattern-scrubbed."""
    scrubbed, _problems = scrub_structure(dict(headers), path="$.headers")
    return scrubbed


def scrub_structure(
    value: Any,
    *,
    path: str = "$",
    identity_mapping_keys: bool = False,
) -> tuple[Any, list[RedactionProblem]]:
    """Deep-scrub a JSON-like structure.

    ``identity_mapping_keys`` preserves non-secret key names that merely contain
    a one- to three-character registered value. Values remain conservatively
    scrubbed because provider errors can embed short credentials in prose.
    """
    problems: list[RedactionProblem] = []
    scrubbed = _scrub_node(
        value,
        path,
        problems,
        identity_mapping_keys=identity_mapping_keys,
    )
    return scrubbed, problems


def _scrub_node(
    value: Any,
    path: str,
    problems: list[RedactionProblem],
    *,
    identity_mapping_keys: bool = False,
) -> Any:
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            scrubbed_key_text = scrub_text(
                key_text,
                exact_short_registered_secrets=identity_mapping_keys,
            )
            output_key = scrubbed_key_text if scrubbed_key_text != key_text else key
            child = f"{path}.{scrubbed_key_text}"
            if scrubbed_key_text != key_text:
                problems.append(
                    RedactionProblem(
                        "secret_value",
                        child,
                        "secret value scrubbed from mapping key",
                    )
                )
            normalized_key = key_text.strip().lower().replace("-", "_")
            if normalized_key in _AUTH_URL_KEYS and isinstance(item, str):
                cleaned = scrub_auth_url(item)
                out[output_key] = cleaned
                if cleaned != item:
                    problems.append(
                        RedactionProblem(
                            "auth_url", child, "auth URL query material redacted"
                        )
                    )
            elif is_secret_key(key):
                out[output_key] = REDACTED
                problems.append(
                    RedactionProblem("secret_key", child, "secret-named key redacted")
                )
            else:
                out[output_key] = _scrub_node(
                    item,
                    child,
                    problems,
                    identity_mapping_keys=identity_mapping_keys,
                )
        return out
    if isinstance(value, (list, tuple)):
        items = [
            _scrub_node(
                item,
                f"{path}[{index}]",
                problems,
                identity_mapping_keys=identity_mapping_keys,
            )
            for index, item in enumerate(value)
        ]
        return items if isinstance(value, list) else tuple(items)
    if isinstance(value, str):
        cleaned = scrub_text(value)
        if cleaned != value:
            problems.append(
                RedactionProblem(
                    "secret_value", path, "secret value scrubbed from text"
                )
            )
        return cleaned
    if isinstance(value, (bool, int, float)) and contains_registered_secret_identity(
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
