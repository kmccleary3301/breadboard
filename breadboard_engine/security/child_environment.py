"""Provider-secret-safe process and Ray child environments."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterable, Iterator, Mapping, MutableMapping
from .redaction import iter_registered_secret_values, scrub_text

_REMOTE_BROKER_URL_ENV = "BREADBOARD_AUTH_BROKER_URL"
_REMOTE_BROKER_CONFIGURED_ENV = "BREADBOARD_AUTH_BROKER_CONFIGURED"


_SAFE_CHILD_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "COLORTERM",
    "NO_COLOR",
    "TMPDIR",
    "TMP",
    "TEMP",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "NODE_PATH",
    "JAVA_HOME",
    "GOPATH",
    "GOMODCACHE",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "BREADBOARD_CODEX_BIN",
    "BREADBOARD_CODEX_APP_SERVER_POOL",
    _REMOTE_BROKER_CONFIGURED_ENV,
    "RAY_BACKEND_LOG_LEVEL",
    "RAY_LOG_TO_DRIVER",
    "RAY_LOGGER_LEVEL",
    "RAY_LOG_TO_STDERR",
    "RAY_ROTATION_BACKUP_COUNT",
    "RAY_ROTATION_MAX_BYTES",
    "RAY_SCE_LOCAL_MODE",
    "RAY_TMPDIR",
)

_SAFE_OVERRIDE_ENV_KEYS = frozenset(
    {
        *_SAFE_CHILD_ENV_KEYS,
        "CI",
        "GOMAXPROCS",
        "NODE_ENV",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "PYTHONUNBUFFERED",
        "RAY_ADDRESS",
        "RAY_DISABLE_DASHBOARD",
        "RAY_LOG_TO_STDERR",
        "RAY_TMPDIR",
        "RUST_BACKTRACE",
        "RUST_LOG",
        "SOURCE_DATE_EPOCH",
        "TZ",
    }
)

_EXACT_PROVIDER_CREDENTIAL_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "CODEX_AUTH_TOKEN",
        "MOCK_API_KEY",
        "BREADBOARD_API_TOKEN",
        "SCRAPE_DO_API_KEY",
        "SCRAPEDO_API_KEY",
        "SERPER_API_KEY",
        "SERPER_DEV_API_KEY",
        _REMOTE_BROKER_URL_ENV,
        "BREADBOARD_AUTH_BROKER_TOKEN",
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
        "BREADBOARD_OPENAI_AUTH_BASE_URL",
    }
)
_PROVIDER_PREFIXES = (
    "OPENAI_",
    "OPENROUTER_",
    "ANTHROPIC_",
    "GOOGLE_",
    "GEMINI_",
    "CODEX_",
    "BREADBOARD_OPENAI_AUTH_",
    "BREADBOARD_PROVIDER_",
    "BREADBOARD_CREDENTIAL_",
)
_CREDENTIAL_MARKERS = (
    "API_KEY",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "REFRESH_TOKEN",
    "CLIENT_SECRET",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "AUTH_HEADERS",
)
_MIN_CREDENTIAL_SUBSTRING_LENGTH = 3
_ENVIRONMENT_LOCK = threading.RLock()


def is_loader_environment_key(name: object) -> bool:
    """Return whether ``name`` can influence executable loading/bootstrap."""
    normalized = str(name).strip().upper()
    return normalized.startswith(("LD_", "DYLD_", "_RLD")) or normalized == "GCONV_PATH"


def is_provider_credential_env_key(name: object) -> bool:
    normalized = str(name).strip().upper()
    if normalized in _EXACT_PROVIDER_CREDENTIAL_KEYS:
        return True
    return normalized.startswith(_PROVIDER_PREFIXES) and any(
        marker in normalized for marker in _CREDENTIAL_MARKERS
    )


def _is_process_secret_env_key(name: object) -> bool:
    normalized = str(name).strip().upper()
    if normalized.endswith(("_BASE_URL", "_DB", "_DIR", "_PATH")):
        return False
    return is_provider_credential_env_key(normalized)


_INITIAL_PROVIDER_CREDENTIAL_KEYS = tuple(
    sorted(
        str(key)
        for key, value in os.environ.items()
        if value and _is_process_secret_env_key(key)
    )
)


def initial_provider_credential_keys() -> tuple[str, ...]:
    """Return startup secret-variable names without exposing their values."""
    return _INITIAL_PROVIDER_CREDENTIAL_KEYS


def _looks_like_credential_value(value: object) -> bool:
    return isinstance(value, str) and bool(value) and scrub_text(value) != value


def provider_credential_values(
    source: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    environment = os.environ if source is None else source
    values: dict[str, None] = {}
    for key, value in environment.items():
        if not isinstance(value, str) or not value:
            continue
        if is_provider_credential_env_key(key) or _looks_like_credential_value(value):
            values[value] = None
    return tuple(values)


def contains_provider_credential_value(
    value: object,
    *,
    values: Iterable[str] | None = None,
) -> bool:
    source_values = provider_credential_values() if values is None else tuple(values)
    candidates = tuple(
        dict.fromkeys(
            item
            for item in (*source_values, *iter_registered_secret_values())
            if isinstance(item, str) and item
        )
    )
    if not candidates:
        return False

    seen: set[int] = set()

    def contains(item: object) -> bool:
        def matches(text: str, secret: str) -> bool:
            return (
                text == secret
                if len(secret) < _MIN_CREDENTIAL_SUBSTRING_LENGTH
                else secret in text
            )

        if isinstance(item, str):
            return any(matches(item, secret) for secret in candidates)
        if isinstance(item, (bytes, bytearray, memoryview)):
            raw = bytes(item)
            return any(
                raw == secret.encode("utf-8")
                if len(secret) < _MIN_CREDENTIAL_SUBSTRING_LENGTH
                else secret.encode("utf-8") in raw
                for secret in candidates
            )
        marker = id(item)
        if marker in seen:
            return False
        if isinstance(item, Mapping):
            seen.add(marker)
            try:
                return any(
                    contains(key) or contains(nested) for key, nested in item.items()
                )
            finally:
                seen.discard(marker)
        if isinstance(item, (list, tuple, set, frozenset)):
            seen.add(marker)
            try:
                return any(contains(nested) for nested in item)
            finally:
                seen.discard(marker)
        return False

    return contains(value)


def build_child_environment(
    source: Mapping[str, object] | None = None,
    overrides: Mapping[str, object] | None = None,
    *,
    allowed_override_keys: Iterable[str] = (),
) -> dict[str, str]:
    """Build a new environment without copying ambient credentials."""
    environment = os.environ if source is None else source
    override_values = overrides or {}
    permitted_override_keys = _SAFE_OVERRIDE_ENV_KEYS.union(
        str(key) for key in allowed_override_keys
    )
    known_secrets = (
        *provider_credential_values(environment),
        *provider_credential_values(override_values),
        *iter_registered_secret_values(),
    )
    child: dict[str, str] = {}
    if str(environment.get(_REMOTE_BROKER_URL_ENV) or "").strip():
        child[_REMOTE_BROKER_CONFIGURED_ENV] = "1"
        child[_REMOTE_BROKER_URL_ENV] = "configured"
    for key in _SAFE_CHILD_ENV_KEYS:
        value = environment.get(key)
        if value is None or is_loader_environment_key(key):
            continue
        if contains_provider_credential_value(
            value,
            values=known_secrets,
        ):
            continue
        child[key] = str(value)
    rejected: list[str] = []
    for key, value in override_values.items():
        name = str(key)
        if (
            value is None
            or name not in permitted_override_keys
            or is_provider_credential_env_key(name)
            or is_loader_environment_key(name)
        ):
            rejected.append(name)
            continue
        if contains_provider_credential_value(
            value,
            values=known_secrets,
        ):
            raise ValueError(
                f"child environment override contains credential material: {name}"
            )
        child[name] = str(value)
    if rejected:
        names = ", ".join(sorted(set(rejected)))
        raise ValueError(f"child environment override keys are not permitted: {names}")
    return child


def purge_provider_credentials(
    environment: MutableMapping[str, str] | None = None,
) -> None:
    """Remove provider credential variables and exact-value aliases."""
    target = os.environ if environment is None else environment
    known_secrets = provider_credential_values(target)
    for key, value in tuple(target.items()):
        if is_provider_credential_env_key(key) or contains_provider_credential_value(
            value,
            values=known_secrets,
        ):
            target.pop(key, None)


@contextmanager
def provider_credentials_hidden(
    environment: MutableMapping[str, str] | None = None,
) -> Iterator[None]:
    """Hide provider credentials and exact-value aliases, then restore them."""
    target = os.environ if environment is None else environment
    removed: dict[str, str] = {}
    with _ENVIRONMENT_LOCK:
        known_secrets = provider_credential_values(target)
        for key, value in tuple(target.items()):
            if is_provider_credential_env_key(
                key
            ) or contains_provider_credential_value(
                value,
                values=known_secrets,
            ):
                removed[key] = target.pop(key)
        try:
            yield
        finally:
            purge_provider_credentials(target)
            target.update(removed)
            removed.clear()


@contextmanager
def sanitized_process_environment(
    environment: MutableMapping[str, str] | None = None,
    *,
    overrides: Mapping[str, object] | None = None,
) -> Iterator[None]:
    """Temporarily replace a process environment with the child allowlist."""
    target = os.environ if environment is None else environment
    original: dict[str, str] = {}
    sanitized: dict[str, str] = {}
    with _ENVIRONMENT_LOCK:
        original.update(target)
        sanitized.update(
            build_child_environment(
                source=target,
                overrides=overrides,
            )
        )
        target.clear()
        target.update(sanitized)
        try:
            yield
        finally:
            target.clear()
            target.update(original)
            sanitized.clear()
            original.clear()
