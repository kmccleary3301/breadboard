"""
Provider Routing System for Multi-Provider Tool Calling

Supports model ID prefixes like:
- openrouter/openai/gpt-5-nano
- openai/gpt-4
- anthropic/claude-3-sonnet

Provides provider-specific tool schema translation and native tool calling detection.
"""
from typing import Dict, Any, List, Optional, Tuple
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .provider_capabilities import CAPABILITY_MATRIX, ProviderCapabilities

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
    api_key_env: str
    default_headers: Dict[str, str]


class ProviderConfig:
    """Configuration for a specific provider"""

    def __init__(
        self,
        provider_id: str,
        supports_native_tools: bool = True,
        tool_schema_format: str = "openai",  # openai, anthropic, google
        base_url: Optional[str] = None,
        api_key_env: str = "OPENAI_API_KEY",
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

    def to_descriptor(self, supports_native_override: Optional[bool] = None) -> ProviderDescriptor:
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
                "parameters": {
                    "type": "object",
                    "properties": tool_def.get("parameters", {}),
                    "required": tool_def.get("required", [])
                }
            }
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
            "input_schema": {
                "type": "object",
                "properties": tool_def.get("parameters", {}),
                "required": tool_def.get("required", [])
            }
        }
    
    def get_provider_format(self) -> str:
        return "anthropic"


class ProviderRouter:
    """Routes model requests to appropriate providers with tool schema translation"""
    
    def __init__(self):
        self.providers = {
            "openai": ProviderConfig(
                provider_id="openai",
                supports_native_tools=True,
                tool_schema_format="openai",
                api_key_env="OPENAI_API_KEY",
                runtime_id="openai_chat",
                default_api_variant="chat",
                supports_streaming=True,
                supports_reasoning_traces=True,
                supports_cache_control=False,
            ),
            "openrouter": ProviderConfig(
                provider_id="openrouter",
                supports_native_tools=True,  # TRUE for OpenAI models!
                tool_schema_format="openai",  # Uses OpenAI format
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
                default_headers={
                    "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", ""),
                    "X-Title": os.getenv("OPENROUTER_APP_TITLE", "Ray SCE Agent"),
                    "Accept": "application/json; charset=utf-8",
                    "Accept-Encoding": "identity",
                },
                runtime_id="openrouter_chat",
                default_api_variant="chat",
                supports_streaming=True,
                supports_reasoning_traces=True,
                supports_cache_control=False,
            ),
            "anthropic": ProviderConfig(
                provider_id="anthropic",
                supports_native_tools=True,
                tool_schema_format="anthropic",
                api_key_env="ANTHROPIC_API_KEY",
                runtime_id="anthropic_messages",
                default_api_variant="messages",
                supports_streaming=True,
                supports_reasoning_traces=True,
                supports_cache_control=True,
            ),
            "mock": ProviderConfig(
                provider_id="mock",
                supports_native_tools=False,
                tool_schema_format="openai",
                api_key_env="MOCK_API_KEY",
                runtime_id="mock_chat",
                default_api_variant="mock",
                supports_streaming=False,
                supports_reasoning_traces=False,
                supports_cache_control=False,
            ),
            "cli_mock": ProviderConfig(
                provider_id="cli_mock",
                supports_native_tools=False,
                tool_schema_format="openai",
                api_key_env="MOCK_API_KEY",
                runtime_id="cli_mock_chat",
                default_api_variant="mock",
                supports_streaming=False,
                supports_reasoning_traces=False,
                supports_cache_control=False,
            ),
            "smoke": ProviderConfig(
                provider_id="smoke",
                supports_native_tools=False,
                tool_schema_format="openai",
                api_key_env="MOCK_API_KEY",
                runtime_id="smoke_chat",
                default_api_variant="mock",
                supports_streaming=False,
                supports_reasoning_traces=False,
                supports_cache_control=False,
            ),
            "replay": ProviderConfig(
                provider_id="replay",
                supports_native_tools=True,
                tool_schema_format="openai",
                api_key_env="MOCK_API_KEY",
                runtime_id="replay",
                default_api_variant="replay",
                supports_streaming=False,
                supports_reasoning_traces=True,
                supports_cache_control=False,
            ),
        }
        
        self.translators = {
            "openai": OpenAIToolTranslator(),
            "anthropic": AnthropicToolTranslator(),
        }
    
    def parse_model_id(self, model_id: str) -> Tuple[str, str, str]:
        """
        Parse model ID with optional provider routing.
        
        Returns: (provider, actual_model_id, routing_path)
        
        Examples:
        - "gpt-4" -> ("openai", "gpt-4", "direct")
        - "openai/gpt-4" -> ("openai", "gpt-4", "direct") 
        - "openrouter/openai/gpt-5-nano" -> ("openrouter", "openai/gpt-5-nano", "routed")
        - "anthropic/claude-3-sonnet" -> ("anthropic", "claude-3-sonnet", "direct")
        """
        parts = model_id.split("/")
        
        if len(parts) == 1:
            # Check if the single part is a known provider (e.g. 'replay', 'mock')
            if model_id in self.providers:
                return model_id, model_id, "direct"
            # Direct model ID - default to OpenAI
            return "openai", model_id, "direct"
        elif len(parts) == 2:
            # Two parts - could be provider/model or routed provider/subprovider/model
            provider, model = parts
            if provider in self.providers:
                return provider, model, "direct"
            else:
                # Assume OpenAI if unknown provider
                return "openai", model_id, "direct"
        elif len(parts) == 3:
            # Three parts - routing_provider/target_provider/model
            routing_provider, target_provider, model = parts
            if routing_provider in self.providers:
                return routing_provider, f"{target_provider}/{model}", "routed"
            else:
                # Fall back to treating as direct
                return "openai", model_id, "direct"
        else:
            # Too many parts, fall back to OpenAI
            return "openai", model_id, "direct"
    
    def get_provider_config(self, model_id: str) -> Tuple[ProviderConfig, str, bool]:
        """
        Get provider configuration for a model ID.

        Returns: (config, actual_model_id, supports_native_tools_for_this_model)
        """
        provider, actual_model, routing_path = self.parse_model_id(model_id)
        config = self.providers.get(provider, self.providers["openai"])
        
        # Special logic for OpenRouter: only supports native tools for OpenAI models
        supports_native = config.supports_native_tools
        if provider == "openrouter" and routing_path == "routed":
            # Check if this is an OpenAI model through OpenRouter
            supports_native = actual_model.startswith("openai/")

        return config, actual_model, supports_native

    def get_runtime_descriptor(self, model_id: str) -> Tuple[ProviderDescriptor, str]:
        """Return a provider runtime descriptor and resolved model ID."""

        config, actual_model, supports_native = self.get_provider_config(model_id)
        descriptor = config.to_descriptor(supports_native_override=supports_native)
        # OpenRouter's GPT-5 OpenAI models are commonly served via a Responses-style backend.
        if (
            descriptor.provider_id == "openrouter"
            and isinstance(actual_model, str)
            and actual_model.startswith("openai/gpt-5")
        ):
            descriptor.runtime_id = "openai_responses"
            descriptor.default_api_variant = "responses"
        return descriptor, actual_model

    def get_tool_translator(self, model_id: str) -> ToolSchemaTranslator:
        """Get appropriate tool schema translator for a model"""
        provider, _, _ = self.parse_model_id(model_id)
        config = self.providers.get(provider, self.providers["openai"])
        return self.translators.get(config.tool_schema_format, self.translators["openai"])
    
    def should_use_native_tools(self, model_id: str, user_config: Dict[str, Any]) -> bool:
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
        provider, _, _ = self.parse_model_id(model_id)
        return CAPABILITY_MATRIX.get(provider, CAPABILITY_MATRIX["openai"])

    def create_client_config(self, model_id: str) -> Dict[str, Any]:
        """Create client configuration for the given model"""
        config, actual_model, _ = self.get_provider_config(model_id)
        
        api_key = os.getenv(config.api_key_env)
        if config.provider_id == "mock":
            api_key = api_key or "mock"

        client_config = {
            "model": actual_model,
            "api_key": api_key,
        }
        
        if config.base_url:
            client_config["base_url"] = config.base_url
        
        if config.default_headers:
            # Filter out empty headers
            headers = {k: v for k, v in config.default_headers.items() if v}
            if headers:
                client_config["default_headers"] = headers
        
        return client_config


# Global instance
provider_router = ProviderRouter()
