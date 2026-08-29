"""Episode-scoped provider profiles for exact OpenAI Chat Completions calls.

Profiles are immutable request authority.  They carry the short-lived credential
needed to construct one client, but their identity projection never contains
credential material (or caller auth headers).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..security import redaction
from .contract_wire import ProviderContractError, canonical_json

_EXACT_MODEL = "Qwen/Qwen3.5-35B-A3B"
_EXACT_CONTEXT_WINDOW = 131_072
_EXACT_MAX_OUTPUT_TOKENS = 32_000
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESERVED_CALLER_HEADERS = frozenset(
    {
        "accept",
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "keep-alive",
        "proxy-authorization",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_REQUIRED_CAPABILITIES = (
    "supports_tools",
    "supports_strict_tools",
    "supports_stream_options",
    "supports_thinking_control",
    "supports_n",
    "supports_max_tokens",
)


@dataclass(frozen=True, slots=True)
class _FrozenHeaders(Mapping[str, str]):
    _items: tuple[tuple[str, str], ...]

    def __getitem__(self, key: str) -> str:
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (name for name, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)


def _text(value: Any, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ProviderContractError(f"{field_name} must be non-empty text")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ProviderContractError(f"{field_name} contains control characters")
    return value


def _bounded_int(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ProviderContractError(f"{field_name} is outside its supported range")
    return value


def _bounded_float(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if type(value) not in (int, float) or not minimum <= float(value) <= maximum:
        raise ProviderContractError(f"{field_name} is outside its supported range")
    return float(value)


@dataclass(frozen=True)
class OpenAICompletionsSampling:
    """Sampling controls emitted in a Chat Completions request."""

    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    n: int = 1

    def __post_init__(self) -> None:
        if self.temperature is not None:
            object.__setattr__(
                self,
                "temperature",
                _bounded_float(
                    self.temperature,
                    "sampling.temperature",
                    minimum=0.0,
                    maximum=2.0,
                ),
            )
        if self.top_p is not None:
            object.__setattr__(
                self,
                "top_p",
                _bounded_float(self.top_p, "sampling.top_p", minimum=0.0, maximum=1.0),
            )
        if self.seed is not None:
            object.__setattr__(
                self,
                "seed",
                _bounded_int(self.seed, "sampling.seed", minimum=0, maximum=2**31 - 1),
            )
        for field_name in ("frequency_penalty", "presence_penalty"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _bounded_float(
                        value,
                        f"sampling.{field_name}",
                        minimum=-2.0,
                        maximum=2.0,
                    ),
                )
        _bounded_int(self.n, "sampling.n", minimum=1, maximum=1)

    @classmethod
    def from_value(
        cls, value: OpenAICompletionsSampling | Mapping[str, Any]
    ) -> OpenAICompletionsSampling:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ProviderContractError("sampling must be an object")
        allowed = {
            "temperature",
            "top_p",
            "seed",
            "frequency_penalty",
            "presence_penalty",
            "n",
        }
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise ProviderContractError(
                f"sampling contains unsupported fields: {', '.join(unknown)}"
            )
        return cls(**{str(key): item for key, item in value.items()})

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"n": self.n}
        for field_name in (
            "temperature",
            "top_p",
            "seed",
            "frequency_penalty",
            "presence_penalty",
        ):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        return result


@dataclass(frozen=True)
class OpenAICompletionsCapabilities:
    """Explicit wire capabilities for one OpenAI-compatible endpoint."""

    supports_tools: bool = True
    supports_strict_tools: bool = True
    supports_stream_options: bool = True
    supports_thinking_control: bool = True
    supports_store: bool = False
    supports_n: bool = True
    supports_max_tokens: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "supports_tools",
            "supports_strict_tools",
            "supports_stream_options",
            "supports_thinking_control",
            "supports_store",
            "supports_n",
            "supports_max_tokens",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ProviderContractError(
                    f"capabilities.{field_name} must be boolean"
                )

    @classmethod
    def from_value(
        cls,
        value: OpenAICompletionsCapabilities | Mapping[str, Any],
    ) -> OpenAICompletionsCapabilities:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ProviderContractError("capabilities must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise ProviderContractError(
                f"capabilities contains unsupported fields: {', '.join(unknown)}"
            )
        return cls(**{str(key): item for key, item in value.items()})

    def as_dict(self) -> dict[str, bool]:
        return {
            field_name: bool(getattr(self, field_name))
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class OpenAICompletionsCompatibility:
    """Compatibility contract that prevents implicit SDK/runtime behavior."""

    api_variant: str = "chat_completions"
    sdk_max_retries: int = 0
    transport_max_retries: int = 0
    provider_fallback: bool = False

    def __post_init__(self) -> None:
        if self.api_variant != "chat_completions":
            raise ProviderContractError(
                "compatibility.api_variant must be chat_completions"
            )
        _bounded_int(
            self.sdk_max_retries,
            "compatibility.sdk_max_retries",
            minimum=0,
            maximum=0,
        )
        _bounded_int(
            self.transport_max_retries,
            "compatibility.transport_max_retries",
            minimum=0,
            maximum=0,
        )
        if type(self.provider_fallback) is not bool or self.provider_fallback:
            raise ProviderContractError("compatibility.provider_fallback must be false")

    @classmethod
    def from_value(
        cls,
        value: OpenAICompletionsCompatibility | Mapping[str, Any],
    ) -> OpenAICompletionsCompatibility:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ProviderContractError("compatibility must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise ProviderContractError(
                f"compatibility contains unsupported fields: {', '.join(unknown)}"
            )
        return cls(**{str(key): item for key, item in value.items()})

    def as_dict(self) -> dict[str, Any]:
        return {
            "api_variant": self.api_variant,
            "sdk_max_retries": self.sdk_max_retries,
            "transport_max_retries": self.transport_max_retries,
            "provider_fallback": self.provider_fallback,
        }


@dataclass(frozen=True)
class OpenAICompletionsProviderProfile:
    """Immutable, episode-scoped authority for one Chat Completions route."""

    model: str
    scoped_credential: str
    base_url: str
    context_window: int
    max_output_tokens: int
    sampling: OpenAICompletionsSampling | Mapping[str, Any] = field(
        default_factory=OpenAICompletionsSampling
    )
    caller_headers: Mapping[str, str] = field(default_factory=dict)
    capabilities: OpenAICompletionsCapabilities | Mapping[str, Any] = field(
        default_factory=OpenAICompletionsCapabilities
    )
    compatibility: OpenAICompletionsCompatibility | Mapping[str, Any] = field(
        default_factory=OpenAICompletionsCompatibility
    )
    provider_id: str = "openai"
    runtime_id: str = "openai_chat"

    def __post_init__(self) -> None:
        model = _text(self.model, "profile.model", max_length=256)
        if model != _EXACT_MODEL:
            raise ProviderContractError(f"profile.model must be {_EXACT_MODEL}")
        base_url = _text(self.base_url, "profile.base_url", max_length=2048)
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderContractError("profile.base_url must be an HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ProviderContractError("profile.base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ProviderContractError(
                "profile.base_url must not contain a query or fragment"
            )
        _text(
            self.scoped_credential,
            "profile.scoped_credential",
            max_length=8192,
        )
        if self.context_window != _EXACT_CONTEXT_WINDOW:
            raise ProviderContractError(
                f"profile.context_window must be {_EXACT_CONTEXT_WINDOW}"
            )
        if self.max_output_tokens != _EXACT_MAX_OUTPUT_TOKENS:
            raise ProviderContractError(
                f"profile.max_output_tokens must be {_EXACT_MAX_OUTPUT_TOKENS}"
            )
        object.__setattr__(
            self, "sampling", OpenAICompletionsSampling.from_value(self.sampling)
        )
        object.__setattr__(
            self,
            "capabilities",
            OpenAICompletionsCapabilities.from_value(self.capabilities),
        )
        for field_name in _REQUIRED_CAPABILITIES:
            if not getattr(self.capabilities, field_name):
                raise ProviderContractError(f"capabilities.{field_name} must be true")
        if self.capabilities.supports_store:
            raise ProviderContractError("capabilities.supports_store must be false")
        object.__setattr__(
            self,
            "compatibility",
            OpenAICompletionsCompatibility.from_value(self.compatibility),
        )
        if not isinstance(self.caller_headers, Mapping):
            raise ProviderContractError("profile.caller_headers must be an object")
        if len(self.caller_headers) > 128:
            raise ProviderContractError(
                "profile.caller_headers cannot contain more than 128 entries"
            )
        headers: dict[str, str] = {}
        for key, value in self.caller_headers.items():
            header_name = _text(key, "profile.caller_headers key", max_length=256)
            normalized_name = header_name.casefold()
            if (
                _HEADER_NAME_RE.fullmatch(header_name) is None
                or normalized_name in _RESERVED_CALLER_HEADERS
                or redaction.is_secret_key(normalized_name)
            ):
                raise ProviderContractError(
                    f"profile.caller_headers contains reserved header {header_name!r}"
                )
            header_value = _text(
                value,
                f"profile.caller_headers[{header_name!r}]",
                max_length=8192,
            )
            if normalized_name in {existing.casefold() for existing in headers}:
                raise ProviderContractError(
                    "profile.caller_headers contains duplicate names"
                )
            headers[header_name] = header_value
        object.__setattr__(
            self, "caller_headers", _FrozenHeaders(tuple(headers.items()))
        )
        _text(self.provider_id, "profile.provider_id", max_length=128)
        _text(self.runtime_id, "profile.runtime_id", max_length=128)
        if self.provider_id != "openai":
            raise ProviderContractError("profile.provider_id must be openai")
        if self.runtime_id != "openai_chat":
            raise ProviderContractError("profile.runtime_id must be openai_chat")

    def as_dict(self) -> dict[str, Any]:
        """Return deterministic, secret-free profile identity data."""
        return self.identity_dict()

    def identity_dict(self) -> dict[str, Any]:
        """Return deterministic, secret-free profile identity data."""
        header_names = sorted(
            (name.casefold() for name in self.caller_headers),
        )
        return {
            "base_url_sha256": hashlib.sha256(
                self.base_url.encode("utf-8")
            ).hexdigest(),
            "caller_header_count": len(header_names),
            "caller_header_names_sha256": hashlib.sha256(
                canonical_json(header_names).encode("utf-8")
            ).hexdigest(),
            "capabilities": self.capabilities.as_dict(),
            "compatibility": self.compatibility.as_dict(),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "model": self.model,
            "provider_id": self.provider_id,
            "runtime_id": self.runtime_id,
            "sampling": self.sampling.as_dict(),
        }

    def identity_json(self) -> str:
        """Return canonical JSON identity with no credential material."""
        return canonical_json(self.identity_dict())

    def chat_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build the exact streamed Chat Completions payload for this profile."""
        if type(messages) is not list or any(
            type(message) is not dict for message in messages
        ):
            raise ProviderContractError(
                "profile messages must be an exact array of objects"
            )
        copied_messages = [dict(message) for message in messages]
        request: dict[str, Any] = {
            "model": self.model,
            "messages": copied_messages,
        }
        if tools is not None and type(tools) is not list:
            raise ProviderContractError("profile tools must be an exact array")
        if tools:
            copied_tools: list[dict[str, Any]] = []
            for tool in tools:
                if type(tool) is not dict or type(tool.get("function")) is not dict:
                    raise ProviderContractError(
                        "profile tools must contain exact function objects"
                    )
                copied = dict(tool)
                function_copy = dict(tool["function"])
                function_copy["strict"] = False
                copied["function"] = function_copy
                copied_tools.append(copied)
            request["tools"] = copied_tools
        request["stream"] = True
        request["stream_options"] = {"include_usage": True}
        request["max_tokens"] = self.max_output_tokens
        request["n"] = self.sampling.n
        for field_name in (
            "temperature",
            "top_p",
            "seed",
            "frequency_penalty",
            "presence_penalty",
        ):
            value = getattr(self.sampling, field_name)
            if value is not None:
                request[field_name] = value
        request["enable_thinking"] = False
        return request


__all__ = [
    "OpenAICompletionsCapabilities",
    "OpenAICompletionsCompatibility",
    "OpenAICompletionsProviderProfile",
    "OpenAICompletionsSampling",
]
