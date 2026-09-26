"""Episode-scoped provider profiles for exact OpenAI Chat Completions calls.

Profiles are immutable request authority.  They carry the short-lived credential
needed to construct one client, but their identity projection never contains
credential material (or caller auth headers).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from ..security import redaction
from .contract_wire import ProviderContractError, canonical_json

_MAX_SAFE_INTEGER = 2**53 - 1
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
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ProviderContractError(f"{field_name} contains control characters")
    return value


def validate_wire_model(value: Any) -> str:
    """Validate an opaque, bounded Unicode model name without rewriting it."""
    model = _text(value, "provider.model", max_length=256)
    if any(unicodedata.category(char) == "Cs" for char in model):
        raise ProviderContractError("provider.model contains a surrogate")
    return model


@dataclass(frozen=True, slots=True)
class OpenAICompletionsRequestPolicy:
    """Closed request-field policy for one OpenAI Chat Completions route."""

    schema_version: Literal["bb.openai_chat_request_policy.v1"] = (
        "bb.openai_chat_request_policy.v1"
    )
    mode: Literal["streaming", "non_streaming"] = "streaming"
    include_usage: bool = True
    max_token_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    strict_tools: bool | None = False
    enable_thinking: bool | None = False
    tool_choice: Literal["auto"] | None = None
    # A source-declared body key carrying one per-episode conversation identity.
    conversation_key_field: Literal["prompt_cache_key"] | None = None
    preserve_thinking: bool | None = None
    chat_template_kwargs: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema_version != "bb.openai_chat_request_policy.v1":
            raise ProviderContractError(
                "request_policy.schema_version is unsupported"
            )
        if self.mode not in {"streaming", "non_streaming"}:
            raise ProviderContractError("request_policy.mode is unsupported")
        if type(self.include_usage) is not bool:
            raise ProviderContractError("request_policy.include_usage must be boolean")
        if self.max_token_field not in {"max_tokens", "max_completion_tokens"}:
            raise ProviderContractError(
                "request_policy.max_token_field is unsupported"
            )
        if self.strict_tools is not None and type(self.strict_tools) is not bool:
            raise ProviderContractError(
                "request_policy.strict_tools must be boolean or null"
            )
        if (
            self.enable_thinking is not None
            and type(self.enable_thinking) is not bool
        ):
            raise ProviderContractError(
                "request_policy.enable_thinking must be boolean or null"
            )
        if (
            self.preserve_thinking is not None
            and type(self.preserve_thinking) is not bool
        ):
            raise ProviderContractError(
                "request_policy.preserve_thinking must be boolean or null"
            )
        if self.chat_template_kwargs is not None:
            if not isinstance(self.chat_template_kwargs, Mapping):
                raise ProviderContractError(
                    "request_policy.chat_template_kwargs must be an object or null"
                )
            frozen_kwargs: dict[str, Any] = {}
            for key, val in self.chat_template_kwargs.items():
                if type(key) is not str or not key:
                    raise ProviderContractError(
                        "request_policy.chat_template_kwargs keys must be non-empty text"
                    )
                if not (val is None or type(val) in (str, int, float, bool)):
                    raise ProviderContractError(
                        "request_policy.chat_template_kwargs values must be JSON scalars"
                    )
                frozen_kwargs[key] = val
            object.__setattr__(
                self,
                "chat_template_kwargs",
                MappingProxyType(frozen_kwargs),
            )
        if self.tool_choice is not None and self.tool_choice != "auto":
            raise ProviderContractError("request_policy.tool_choice is unsupported")
        if (
            self.conversation_key_field is not None
            and self.conversation_key_field != "prompt_cache_key"
        ):
            raise ProviderContractError(
                "request_policy.conversation_key_field is unsupported"
            )
        if self.mode == "non_streaming" and self.include_usage:
            raise ProviderContractError(
                "non_streaming request policy cannot include usage"
            )

    @classmethod
    def from_value(
        cls,
        value: OpenAICompletionsRequestPolicy | Mapping[str, Any],
    ) -> OpenAICompletionsRequestPolicy:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ProviderContractError("request_policy must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise ProviderContractError(
                "request_policy contains unsupported fields: " + ", ".join(unknown)
            )
        return cls(**{str(key): item for key, item in value.items()})

    @property
    def stream(self) -> bool:
        return self.mode == "streaming"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "include_usage": self.include_usage,
            "max_token_field": self.max_token_field,
            "strict_tools": self.strict_tools,
            "enable_thinking": self.enable_thinking,
            "tool_choice": self.tool_choice,
        }
        # Omitted when unset so existing profile identities are unchanged.
        if self.conversation_key_field is not None:
            result["conversation_key_field"] = self.conversation_key_field
        if self.preserve_thinking is not None:
            result["preserve_thinking"] = self.preserve_thinking
        if self.chat_template_kwargs is not None:
            result["chat_template_kwargs"] = dict(self.chat_template_kwargs)
        return result


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
    supports_streaming: bool = True
    supports_non_streaming: bool = False
    supports_max_completion_tokens: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "supports_tools",
            "supports_strict_tools",
            "supports_stream_options",
            "supports_thinking_control",
            "supports_store",
            "supports_n",
            "supports_max_tokens",
            "supports_streaming",
            "supports_non_streaming",
            "supports_max_completion_tokens",
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
    scoped_credential: str = field(repr=False)
    base_url: str
    context_window: int
    max_output_tokens: int
    sampling: OpenAICompletionsSampling | Mapping[str, Any] = field(
        default_factory=OpenAICompletionsSampling
    )
    caller_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    capabilities: OpenAICompletionsCapabilities | Mapping[str, Any] = field(
        default_factory=OpenAICompletionsCapabilities
    )
    compatibility: OpenAICompletionsCompatibility | Mapping[str, Any] = field(
        default_factory=OpenAICompletionsCompatibility
    )
    request_policy: OpenAICompletionsRequestPolicy | Mapping[str, Any] = field(
        default_factory=OpenAICompletionsRequestPolicy
    )
    provider_id: str = "openai"
    runtime_id: str = "openai_chat"
    _sampling_explicit_fields: frozenset[str] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        validate_wire_model(self.model)
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
        _bounded_int(
            self.context_window,
            "profile.context_window",
            minimum=1,
            maximum=_MAX_SAFE_INTEGER,
        )
        _bounded_int(
            self.max_output_tokens,
            "profile.max_output_tokens",
            minimum=1,
            maximum=_MAX_SAFE_INTEGER,
        )
        sampling_explicit_fields = (
            frozenset(str(key) for key in self.sampling)
            if isinstance(self.sampling, Mapping)
            else frozenset()
        )
        object.__setattr__(
            self, "_sampling_explicit_fields", sampling_explicit_fields
        )
        object.__setattr__(
            self, "sampling", OpenAICompletionsSampling.from_value(self.sampling)
        )
        object.__setattr__(
            self,
            "capabilities",
            OpenAICompletionsCapabilities.from_value(self.capabilities),
        )
        object.__setattr__(
            self,
            "request_policy",
            OpenAICompletionsRequestPolicy.from_value(self.request_policy),
        )
        if not self.capabilities.supports_n:
            raise ProviderContractError("capabilities.supports_n must be true")
        mode_capability = (
            self.capabilities.supports_streaming
            if self.request_policy.stream
            else self.capabilities.supports_non_streaming
        )
        if not mode_capability:
            raise ProviderContractError("request mode is not supported by the profile")
        token_capability = (
            self.capabilities.supports_max_tokens
            if self.request_policy.max_token_field == "max_tokens"
            else self.capabilities.supports_max_completion_tokens
        )
        if not token_capability:
            raise ProviderContractError("max-token field is not supported by the profile")
        if (
            self.request_policy.tool_choice is not None
            and not self.capabilities.supports_tools
        ):
            raise ProviderContractError(
                "capabilities.supports_tools must be true for tool_choice"
            )
        if (
            self.request_policy.stream
            and self.request_policy.include_usage
            and not self.capabilities.supports_stream_options
        ):
            raise ProviderContractError(
                "capabilities.supports_stream_options must be true for usage"
            )
        if (
            self.request_policy.enable_thinking is not None
            and not self.capabilities.supports_thinking_control
        ):
            raise ProviderContractError(
                "capabilities.supports_thinking_control must be true for thinking"
            )
        if (
            self.request_policy.preserve_thinking is not None
            and not self.capabilities.supports_thinking_control
        ):
            raise ProviderContractError(
                "capabilities.supports_thinking_control must be true for thinking"
            )
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
        header_items = sorted(
            (name.casefold(), value) for name, value in self.caller_headers.items()
        )
        header_names = [name for name, _value in header_items]
        return {
            "base_url_sha256": hashlib.sha256(
                self.base_url.encode("utf-8")
            ).hexdigest(),
            "caller_header_count": len(header_names),
            "caller_header_names_sha256": hashlib.sha256(
                canonical_json(header_names).encode("utf-8")
            ).hexdigest(),
            "caller_headers_sha256": hashlib.sha256(
                canonical_json(header_items).encode("utf-8")
            ).hexdigest(),
            "capabilities": self.capabilities.as_dict(),
            "compatibility": self.compatibility.as_dict(),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "model": self.model,
            "provider_id": self.provider_id,
            "request_policy": self.request_policy.as_dict(),
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
        """Build the exact Chat Completions payload for this profile."""
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
            if not self.capabilities.supports_tools:
                raise ProviderContractError(
                    "capabilities.supports_tools must be true for tools"
                )
            if (
                self.request_policy.strict_tools is not None
                and not self.capabilities.supports_strict_tools
            ):
                raise ProviderContractError(
                    "capabilities.supports_strict_tools must be true for strict tools"
                )
            copied_tools: list[dict[str, Any]] = []
            for tool in tools:
                if type(tool) is not dict or type(tool.get("function")) is not dict:
                    raise ProviderContractError(
                        "profile tools must contain exact function objects"
                    )
                copied = dict(tool)
                function_copy = dict(tool["function"])
                function_copy.pop("strict", None)
                if self.request_policy.strict_tools is not None:
                    function_copy["strict"] = self.request_policy.strict_tools
                copied["function"] = function_copy
                copied_tools.append(copied)
            request["tools"] = copied_tools
            if self.request_policy.tool_choice is not None:
                request["tool_choice"] = self.request_policy.tool_choice
        request["stream"] = self.request_policy.stream
        if self.request_policy.stream and self.request_policy.include_usage:
            request["stream_options"] = {"include_usage": True}
        request[self.request_policy.max_token_field] = self.max_output_tokens
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
        if self.request_policy.enable_thinking is not None:
            request["enable_thinking"] = self.request_policy.enable_thinking
        if self.request_policy.preserve_thinking is not None:
            request["preserve_thinking"] = self.request_policy.preserve_thinking
        if self.request_policy.chat_template_kwargs is not None:
            request["chat_template_kwargs"] = dict(
                self.request_policy.chat_template_kwargs
            )
        return request

    def required_request_features(self, *, tools: bool) -> tuple[str, ...]:
        """Name the selected wire features an authority observation must support."""
        features = {
            "streaming" if self.request_policy.stream else "non_streaming",
            self.request_policy.max_token_field,
            "n",
        }
        if self.request_policy.include_usage:
            features.add("stream_options")
        if self.request_policy.enable_thinking is not None:
            features.add("enable_thinking")
        if self.request_policy.preserve_thinking is not None:
            features.add("preserve_thinking")
        if self.request_policy.chat_template_kwargs is not None:
            features.add("chat_template_kwargs")
        if tools and self.request_policy.strict_tools is not None:
            features.add("strict_tools")
        if tools and self.request_policy.tool_choice is not None:
            features.add("tool_choice")
        for name in ("temperature", "top_p", "seed", "frequency_penalty", "presence_penalty"):
            if getattr(self.sampling, name) is not None:
                features.add(name)
        return tuple(sorted(features))

    def chat_request_provenance(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        requested_stream: bool | None = None,
        requested_messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Describe the source of every effective profile request field."""
        request = self.chat_request(messages, tools)
        source_messages = (
            messages if requested_messages is None else requested_messages
        )
        requested_messages_digest = hashlib.sha256(
            canonical_json(source_messages).encode("utf-8")
        ).hexdigest()
        effective_messages_digest = hashlib.sha256(
            canonical_json(request["messages"]).encode("utf-8")
        ).hexdigest()
        requested_tools = tools or []
        effective_tools = request.get("tools") or []
        policy_source = "lock.provider_profile.request_policy"
        provenance: dict[str, Any] = {
            "model": {
                "status": "effective",
                "source": "lock.provider_profile.model",
                "effective": self.model,
                "uncertainty": None,
            },
            "messages": {
                "status": "requested",
                "source": "session.model_history",
                "requested_digest": requested_messages_digest,
                "effective_digest": effective_messages_digest,
                "uncertainty": None,
            },
            "tools": {
                "status": "requested",
                "source": "provider.tool_registry",
                "requested_digest": hashlib.sha256(
                    canonical_json(requested_tools).encode("utf-8")
                ).hexdigest(),
                "effective_digest": hashlib.sha256(
                    canonical_json(effective_tools).encode("utf-8")
                ).hexdigest(),
                "uncertainty": None,
            },
            "request_policy": {
                "status": "effective",
                "source": policy_source,
                "effective": self.request_policy.as_dict(),
                "uncertainty": None,
            },
            "capabilities": {
                "status": "effective",
                "source": "lock.provider_profile.capabilities",
                "effective": self.capabilities.as_dict(),
                "uncertainty": None,
            },
            "stream": {
                "status": "effective",
                "source": f"{policy_source}.mode",
                "requested": requested_stream,
                "effective": request["stream"],
                "uncertainty": None,
            },
            self.request_policy.max_token_field: {
                "status": "effective",
                "source": f"{policy_source}.max_token_field",
                "effective": request[self.request_policy.max_token_field],
                "uncertainty": None,
            },
            "n": {
                "status": (
                    "effective"
                    if "n" in self._sampling_explicit_fields
                    else "default"
                ),
                "source": (
                    "lock.provider_profile.sampling.n"
                    if "n" in self._sampling_explicit_fields
                    else "OpenAICompletionsSampling.n"
                ),
                "effective": request["n"],
                "uncertainty": None,
            },
        }
        if "stream_options" in request:
            provenance["stream_options"] = {
                "status": "effective",
                "source": f"{policy_source}.include_usage",
                "effective": request["stream_options"],
                "uncertainty": None,
            }
        if "enable_thinking" in request:
            provenance["enable_thinking"] = {
                "status": "effective",
                "source": f"{policy_source}.enable_thinking",
                "effective": request["enable_thinking"],
                "uncertainty": None,
            }
        if "preserve_thinking" in request:
            provenance["preserve_thinking"] = {
                "status": "effective",
                "source": f"{policy_source}.preserve_thinking",
                "effective": request["preserve_thinking"],
                "uncertainty": None,
            }
        if "chat_template_kwargs" in request:
            provenance["chat_template_kwargs"] = {
                "status": "effective",
                "source": f"{policy_source}.chat_template_kwargs",
                "effective": dict(request["chat_template_kwargs"]),
                "uncertainty": None,
            }
        if "tool_choice" in request:
            provenance["tool_choice"] = {
                "status": "effective",
                "source": f"{policy_source}.tool_choice",
                "effective": request["tool_choice"],
                "uncertainty": None,
            }
        for field_name in (
            "temperature",
            "top_p",
            "seed",
            "frequency_penalty",
            "presence_penalty",
        ):
            if field_name in request:
                provenance[field_name] = {
                    "status": "effective",
                    "source": f"lock.provider_profile.sampling.{field_name}",
                    "effective": request[field_name],
                    "uncertainty": None,
                }
        if self.request_policy.strict_tools is not None:
            for index, _tool in enumerate(effective_tools):
                requested_strict = None
                if index < len(requested_tools):
                    requested_function = requested_tools[index].get("function")
                    if isinstance(requested_function, dict):
                        requested_strict = requested_function.get("strict")
                provenance[f"tools[{index}].function.strict"] = {
                    "status": "effective",
                    "source": f"{policy_source}.strict_tools",
                    "requested": requested_strict,
                    "effective": self.request_policy.strict_tools,
                    "uncertainty": None,
                }
        return provenance


__all__ = [
    "OpenAICompletionsCapabilities",
    "OpenAICompletionsCompatibility",
    "OpenAICompletionsProviderProfile",
    "OpenAICompletionsRequestPolicy",
    "OpenAICompletionsSampling",
    "validate_wire_model",
]
