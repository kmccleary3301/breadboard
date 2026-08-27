"""
Provider Routing System for Multi-Provider Tool Calling

Supports model ID prefixes like:
- openrouter/openai/gpt-5-nano
- openai/gpt-4
- anthropic/claude-3-sonnet

Provides provider-specific tool schema translation and native tool calling detection.
"""

import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from ..security import redaction
from .capabilities import (
    CAPABILITY_MATRIX,
    ProviderCapabilities,
    get_model_capability_override,
)
from ..provider_broker.catalog import (
    get_provider_catalog_entry,
    routable_provider_catalog,
)


@dataclass
class ProviderDescriptor:
    """Describes how to communicate with a specific provider runtime."""

    provider_id: str
    runtime_id: str
    default_api_variant: str
    supports_native_tools: bool
    supports_streaming: bool
    supports_reasoning_traces: bool
    supports_cache_control: bool
    tool_schema_format: str
    base_url: Optional[str]
    api_key_env: Optional[str]
    default_headers: Dict[str, str]


class ProviderConfig:
    """Configuration for a specific provider"""

    def __init__(
        self,
        provider_id: str,
        supports_native_tools: bool = True,
        tool_schema_format: str = "openai",  # openai, anthropic, google
        base_url: Optional[str] = None,
        api_key_env: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
        runtime_id: str = "openai_chat",
        default_api_variant: str = "chat",
        supports_streaming: bool = True,
        supports_reasoning_traces: bool = False,
        supports_cache_control: bool = False,
    ):
        self.provider_id = provider_id
        self.supports_native_tools = supports_native_tools
        self.tool_schema_format = tool_schema_format
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.default_headers = default_headers or {}
        self.runtime_id = runtime_id
        self.default_api_variant = default_api_variant
        self.supports_streaming = supports_streaming
        self.supports_reasoning_traces = supports_reasoning_traces
        self.supports_cache_control = supports_cache_control

    def to_descriptor(
        self, supports_native_override: Optional[bool] = None
    ) -> ProviderDescriptor:
        """Convert config to runtime descriptor."""

        supports_native = (
            self.supports_native_tools
            if supports_native_override is None
            else supports_native_override
        )
        return ProviderDescriptor(
            provider_id=self.provider_id,
            runtime_id=self.runtime_id,
            default_api_variant=self.default_api_variant,
            supports_native_tools=supports_native,
            supports_streaming=self.supports_streaming,
            supports_reasoning_traces=self.supports_reasoning_traces,
            supports_cache_control=self.supports_cache_control,
            tool_schema_format=self.tool_schema_format,
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            default_headers=dict(self.default_headers or {}),
        )


class ToolSchemaTranslator(ABC):
    """Abstract base class for provider-specific tool schema translation"""

    @abstractmethod
    def translate_tool_schema(self, tool_def: Dict[str, Any]) -> Dict[str, Any]:
        """Translate internal tool definition to provider-specific format"""
        pass

    @abstractmethod
    def get_provider_format(self) -> str:
        """Return the provider format identifier"""
        pass


class OpenAIToolTranslator(ToolSchemaTranslator):
    """OpenAI-compatible tool schema translator"""

    def translate_tool_schema(self, tool_def: Dict[str, Any]) -> Dict[str, Any]:
        """Convert to OpenAI function calling format"""
        return {
            "type": "function",
            "function": {
                "name": tool_def["name"],
                "description": tool_def.get("description", ""),
                "parameters": dict(tool_def.get("parameters", {})),
            },
        }

    def get_provider_format(self) -> str:
        return "openai"


class AnthropicToolTranslator(ToolSchemaTranslator):
    """Anthropic-compatible tool schema translator"""

    def translate_tool_schema(self, tool_def: Dict[str, Any]) -> Dict[str, Any]:
        """Convert to Anthropic tool format"""
        return {
            "name": tool_def["name"],
            "description": tool_def.get("description", ""),
            "input_schema": dict(tool_def.get("parameters", {})),
        }

    def get_provider_format(self) -> str:
        return "anthropic"


class ProviderRouteError(ValueError):
    """Typed fail-closed model-route error."""

    def __init__(self, code: str, provider_id: str | None = None):
        super().__init__(f"provider route rejected: {code}")
        self.code = code
        self.provider_id = provider_id


_PROVIDER_RUNTIME_OPTIONS: dict[str, dict[str, Any]] = {
    "codex": {
        "supports_native_tools": False,
        "supports_reasoning_traces": True,
    },
    "openai": {
        "supports_reasoning_traces": True,
    },
    "openrouter": {
        "supports_reasoning_traces": True,
    },
    "anthropic": {
        "supports_reasoning_traces": True,
        "supports_cache_control": True,
    },
    "mock": {
        "supports_native_tools": False,
        "supports_streaming": False,
    },
    "cli_mock": {
        "supports_native_tools": False,
        "supports_streaming": False,
    },
    "smoke": {
        "supports_native_tools": False,
        "supports_streaming": False,
    },
    "replay": {
        "supports_streaming": False,
        "supports_reasoning_traces": True,
    },
}


class ProviderRouter:
    """Routes model requests to appropriate providers with tool schema translation"""

    def __init__(self):
        self.providers: dict[str, ProviderConfig] = {}
        for entry in routable_provider_catalog():
            default_headers: dict[str, str] = {}
            if entry.provider_id == "openrouter":
                default_headers = {
                    "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", ""),
                    "X-Title": os.getenv("OPENROUTER_APP_TITLE", "Ray SCE Agent"),
                    "Accept": "application/json; charset=utf-8",
                    "Accept-Encoding": "identity",
                }
            self.providers[entry.provider_id] = ProviderConfig(
                provider_id=entry.provider_id,
                tool_schema_format=entry.compatible_protocol,
                base_url=entry.base_url,
                api_key_env=entry.api_key_env,
                default_headers=default_headers,
                runtime_id=entry.runtime_id,
                default_api_variant=entry.default_api_variant,
                **_PROVIDER_RUNTIME_OPTIONS[entry.provider_id],
            )

        self.translators = {
            "openai": OpenAIToolTranslator(),
            "anthropic": AnthropicToolTranslator(),
        }

    def parse_model_id(self, model_id: str) -> Tuple[str, str, str]:
        """Return canonical provider, provider-native model ID, and route kind."""
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_id != model_id.strip()
            or len(model_id) > 512
            or any(character.isspace() or ord(character) < 32 for character in model_id)
        ):
            raise ProviderRouteError("invalid_model_id")
        parts = model_id.split("/")
        if any(not part for part in parts):
            raise ProviderRouteError("invalid_model_id")
        if len(parts) == 1:
            entry = get_provider_catalog_entry(model_id)
            if entry is not None:
                if entry.provider_id not in self.providers:
                    raise ProviderRouteError("unsupported_provider", entry.provider_id)
                return entry.provider_id, entry.provider_id, "direct"
            return "openai", model_id, "direct"

        entry = get_provider_catalog_entry(parts[0])
        if entry is None:
            raise ProviderRouteError("unknown_provider", parts[0].lower())
        if entry.provider_id not in self.providers:
            raise ProviderRouteError("unsupported_provider", entry.provider_id)
        expected_parts = 3 if entry.provider_id == "openrouter" else 2
        if len(parts) != expected_parts:
            raise ProviderRouteError("invalid_model_id", entry.provider_id)
        actual_model = "/".join(parts[1:])
        return (
            entry.provider_id,
            actual_model,
            "routed" if entry.provider_id == "openrouter" else "direct",
        )

    def get_provider_config(self, model_id: str) -> Tuple[ProviderConfig, str, bool]:
        """
        Get provider configuration for a model ID.

        Returns: (config, actual_model_id, supports_native_tools_for_this_model)
        """
        provider, actual_model, routing_path = self.parse_model_id(model_id)
        config = self.providers[provider]

        # OpenRouter is conservative by default; exact model overrides document verified behavior.
        supports_native = config.supports_native_tools
        model_override = get_model_capability_override(provider, actual_model)
        if (
            model_override is not None
            and model_override.supports_native_tools is not None
        ):
            supports_native = model_override.supports_native_tools
        elif provider == "openrouter" and routing_path == "routed":
            # Check if this is an OpenAI model through OpenRouter
            supports_native = actual_model.startswith("openai/")

        return config, actual_model, supports_native

    def get_runtime_descriptor(self, model_id: str) -> Tuple[ProviderDescriptor, str]:
        """Return a provider runtime descriptor and resolved model ID."""

        config, actual_model, supports_native = self.get_provider_config(model_id)
        descriptor = config.to_descriptor(supports_native_override=supports_native)
        model_override = get_model_capability_override(
            descriptor.provider_id, actual_model
        )
        if model_override is not None:
            if model_override.runtime_id is not None:
                descriptor.runtime_id = model_override.runtime_id
            if model_override.api_variant is not None:
                descriptor.default_api_variant = model_override.api_variant
        # OpenRouter's GPT-5 OpenAI models are commonly served via a Responses-style backend.
        elif (
            descriptor.provider_id == "openrouter"
            and isinstance(actual_model, str)
            and actual_model.startswith("openai/gpt-5")
        ):
            descriptor.runtime_id = "openai_responses"
            descriptor.default_api_variant = "responses"
        return descriptor, actual_model

    def get_tool_translator(self, model_id: str) -> ToolSchemaTranslator:
        """Get appropriate tool schema translator for a model."""
        provider, _, _ = self.parse_model_id(model_id)
        config = self.providers[provider]
        try:
            return self.translators[config.tool_schema_format]
        except KeyError as exc:
            raise ProviderRouteError(
                "unsupported_protocol",
                provider,
            ) from exc

    def should_use_native_tools(
        self, model_id: str, user_config: Dict[str, Any]
    ) -> bool:
        """
        Determine if native tools should be used for this model/config combination.

        Considers:
        1. Provider capability
        2. Model-specific capability
        3. User configuration override
        """
        _, _, supports_native = self.get_provider_config(model_id)

        # Check user override
        provider_tools_config = user_config.get("provider_tools", {})
        user_override = provider_tools_config.get("use_native")

        if user_override is not None:
            return bool(user_override) and supports_native

        # Default: use native tools if supported
        return supports_native

    def get_capabilities(self, model_id: str) -> ProviderCapabilities:
        provider, actual_model, _ = self.parse_model_id(model_id)
        model_override = get_model_capability_override(provider, actual_model)
        if model_override is not None and model_override.capabilities is not None:
            return model_override.capabilities
        return CAPABILITY_MATRIX[provider]

    def create_client_config(self, model_id: str) -> Dict[str, Any]:
        """Return provider metadata only; secret material is never part of config."""
        config, actual_model, _ = self.get_provider_config(model_id)
        headers = {k: v for k, v in (config.default_headers or {}).items() if v}
        result: Dict[str, Any] = {"model": actual_model, "api_key": None}
        if config.base_url:
            result["base_url"] = config.base_url
        if headers:
            result["default_headers"] = headers
        if config.provider_id in {"codex", "mock", "cli_mock", "smoke", "replay"}:
            result["api_key"] = "codex" if config.provider_id == "codex" else "mock"
        return result

    def get_credential_origin(
        self,
        model_id: str,
        *,
        session_id: str = "",
        account_selector: Any = None,
        environment: Mapping[str, object] | None = None,
    ) -> dict[str, str] | None:
        """Return secret-free provider credential provenance for one route."""
        config, _actual_model, _ = self.get_provider_config(model_id)
        if config.provider_id == "codex":
            return {"kind": "fallback", "source": "provider_managed"}
        if config.provider_id in {"mock", "cli_mock", "smoke", "replay"}:
            return {"kind": "fallback", "source": "synthetic"}
        from ..provider_broker import get_provider_broker

        return get_provider_broker().get_credential_origin(
            config.provider_id,
            session_id=session_id,
            account_selector=account_selector,
            environment_key=config.api_key_env,
            environment=environment,
        )

    @contextmanager
    def execution_client_config(
        self,
        model_id: str,
        *,
        session_id: str = "",
        endpoint_id: str = "",
        account_selector: Any = None,
        environment: Mapping[str, object] | None = None,
    ):
        """Yield ephemeral SDK configuration for one operation only."""
        config, actual_model, _ = self.get_provider_config(model_id)
        base_url = config.base_url
        headers = {
            key: value for key, value in (config.default_headers or {}).items() if value
        }
        if config.provider_id == "codex":
            material: dict[str, Any] = {
                "api_key": "codex",
                "credential_origin": {
                    "kind": "fallback",
                    "source": "provider_managed",
                },
            }
        elif config.provider_id in {"mock", "cli_mock", "smoke", "replay"}:
            material = {
                "api_key": "mock",
                "credential_origin": {
                    "kind": "fallback",
                    "source": "synthetic",
                },
            }
        else:
            from ..provider_broker import get_provider_broker

            broker = get_provider_broker()
            with broker.execution_material(
                config.provider_id,
                session_id=session_id,
                endpoint_id=endpoint_id or str(model_id),
                account_selector=account_selector,
                environment_key=config.api_key_env,
                environment=environment,
            ) as leased:
                material = leased if isinstance(leased, dict) else {}
                yield from self._yield_execution_client_config(
                    material,
                    actual_model=actual_model,
                    base_url=base_url,
                    headers=headers,
                    secret_material=True,
                )
            return
        yield from self._yield_execution_client_config(
            material,
            actual_model=actual_model,
            base_url=base_url,
            headers=headers,
            secret_material=False,
        )

    def _yield_execution_client_config(
        self,
        material: Mapping[str, Any],
        *,
        actual_model: str,
        base_url: str | None,
        headers: dict[str, str],
        secret_material: bool,
    ):
        api_key = material.get("api_key")
        if material.get("base_url"):
            base_url = str(material["base_url"])
        if isinstance(material.get("headers"), Mapping):
            headers.update(
                {
                    str(key): str(value)
                    for key, value in material["headers"].items()
                    if key and value is not None
                }
            )
        secret_values: tuple[str, ...] = ()
        if secret_material:
            secret_values = redaction.credential_secret_values(
                {
                    "api_key": api_key,
                    "headers": headers,
                    "base_url": base_url,
                    "routing": material.get("routing"),
                }
            )
        result: dict[str, Any] = {
            "model": actual_model,
            "api_key": api_key,
        }
        if base_url:
            result["base_url"] = base_url
        if headers:
            result["default_headers"] = headers
        origin = material.get("credential_origin")
        if isinstance(origin, Mapping):
            result["credential_origin"] = {
                str(key): str(value) for key, value in origin.items() if key and value
            }
        with redaction.secret_value_scope(
            *secret_values,
            allow_short=True,
        ):
            try:
                yield result
            except BaseException as error:
                redaction.scrub_exception_in_place(error)
                raise
            finally:
                result.clear()
                headers.clear()


# Global instance
provider_router = ProviderRouter()
