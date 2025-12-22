"""
Provider-specific adapters for handling tool calling formats, ID matching, and result processing.

Handles the translation between our internal tool calling system and provider-specific formats.
"""
from typing import Dict, Any, List, Optional, Tuple, Union
from abc import ABC, abstractmethod
import json
import re


_OPENAI_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def sanitize_openai_tool_name(name: str) -> str:
    """
    OpenAI native tool names must match `^[a-zA-Z0-9_-]+$`.
    We keep internal tool names (e.g. `todo.create`) and sanitize only the
    provider-facing schema/tool-call names when using native tools.
    """
    raw = str(name or "")
    if _OPENAI_TOOL_NAME_PATTERN.match(raw):
        return raw
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    sanitized = sanitized.strip("_") or "tool"
    # Defensive: some providers enforce short names; keep a conservative bound.
    if len(sanitized) > 64:
        sanitized = sanitized[:64]
    return sanitized


class ProviderAdapter(ABC):
    """Base class for provider-specific tool calling adapters"""
    
    @abstractmethod
    def should_use_native_tool(self, tool_def: Dict[str, Any]) -> bool:
        """Determine if a tool should use native provider schema vs text-based"""
        pass
    
    @abstractmethod
    def translate_tool_to_native_schema(self, tool_def: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal tool definition to provider-native schema"""
        pass
    
    @abstractmethod
    def extract_tool_calls_from_response(self, response_message: Any) -> List[Dict[str, Any]]:
        """Extract tool calls from provider response, with call IDs"""
        pass
    
    @abstractmethod
    def create_tool_result_message(self, call_id: str, tool_name: str, result: Any) -> Dict[str, Any]:
        """Create provider-expected tool result message"""
        pass
    
    @abstractmethod
    def get_provider_id(self) -> str:
        """Return provider identifier"""
        pass


def _get_tool_attr(tool_def: Any, name: str, default: Any = None) -> Any:
    if isinstance(tool_def, dict):
        return tool_def.get(name, default)
    return getattr(tool_def, name, default)


def _get_provider_settings(tool_def: Any) -> Dict[str, Any]:
    if isinstance(tool_def, dict):
        return tool_def.get("provider_routing", {}) or {}
    return getattr(tool_def, "provider_settings", {}) or {}


class OpenAIAdapter(ProviderAdapter):
    """OpenAI-specific adapter for tool calling"""
    
    def should_use_native_tool(self, tool_def: Dict[str, Any]) -> bool:
        """Check if tool should use OpenAI native function calling"""
        provider_routing = _get_provider_settings(tool_def)
        openai_config = provider_routing.get("openai", {})
        return openai_config.get("native_primary", False)
    
    def translate_tool_to_native_schema(self, tool_def: Dict[str, Any]) -> Dict[str, Any]:
        """Convert to OpenAI function calling format"""
        parameters = {}
        required = []
        provider_routing = _get_provider_settings(tool_def)
        openai_config = provider_routing.get("openai", {}) if isinstance(provider_routing, dict) else {}
        additional_props = openai_config.get("additional_properties")
        if not isinstance(additional_props, bool):
            additional_props = None
        strict_flag = openai_config.get("strict")
        if not isinstance(strict_flag, bool):
            strict_flag = None
        
        for param in (_get_tool_attr(tool_def, "parameters", []) or []):
            param_name = _get_tool_attr(param, "name")
            param_type = _get_tool_attr(param, "type", "string")
            param_desc = _get_tool_attr(param, "description", "")
            param_required = bool(_get_tool_attr(param, "required", False))
            if not param_name:
                continue
            
            # Prefer the full nested schema if provided by the YAML loader.
            param_schema_raw = _get_tool_attr(param, "schema", None)
            if isinstance(param_schema_raw, dict) and param_schema_raw:
                param_schema = dict(param_schema_raw)
                if "type" not in param_schema:
                    param_schema["type"] = param_type
                if param_desc and "description" not in param_schema:
                    param_schema["description"] = param_desc
            else:
                # Fallback: minimal schema
                param_schema = {"type": param_type}
                if param_desc:
                    param_schema["description"] = param_desc
                if param_type == "array":
                    param_schema["items"] = {"type": "string"}
                elif param_type == "object":
                    param_schema["properties"] = {}
                    param_schema["additionalProperties"] = True

            # OpenAI requires array schemas to specify `items`.
            try:
                if param_schema.get("type") == "array" and "items" not in param_schema:
                    param_schema["items"] = {"type": "string"}
                if param_schema.get("type") == "object":
                    param_schema.setdefault("properties", {})
                    param_schema.setdefault("additionalProperties", True)
            except Exception:
                pass
            
            # Add default if present
            default_val = _get_tool_attr(param, "default")
            if default_val is not None and "default" not in param_schema:
                param_schema["default"] = default_val
            
            parameters[param_name] = param_schema
            
            if param_required:
                required.append(param_name)

        function_payload = {
            "name": sanitize_openai_tool_name(_get_tool_attr(tool_def, "name")),
            "description": _get_tool_attr(tool_def, "description", ""),
            "parameters": {
                "type": "object",
                "properties": parameters,
                "required": required,
            },
        }
        if additional_props is not None:
            function_payload["parameters"]["additionalProperties"] = additional_props
        if strict_flag is not None:
            function_payload["strict"] = strict_flag

        return {
            "type": "function",
            "function": function_payload,
        }
    
    def extract_tool_calls_from_response(self, response_message: Any) -> List[Dict[str, Any]]:
        """Extract OpenAI tool calls with IDs"""
        tool_calls = []
        
        if hasattr(response_message, 'tool_calls') and response_message.tool_calls:
            for tc in response_message.tool_calls:
                tool_calls.append({
                    "id": getattr(tc, "id", None),
                    "name": getattr(getattr(tc, "function", None), "name", None),
                    "arguments": getattr(getattr(tc, "function", None), "arguments", "{}"),
                    "type": "function"
                })
        
        return tool_calls
    
    def create_tool_result_message(self, call_id: str, tool_name: str, result: Any) -> Dict[str, Any]:
        """Create OpenAI-expected tool result message"""
        content: str
        if isinstance(result, dict) and isinstance(result.get("__mvi_text_output"), str):
            content = str(result["__mvi_text_output"])
        elif isinstance(result, dict):
            content = json.dumps(result, indent=2)
        else:
            content = str(result)
        
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": tool_name,
            "content": content
        }
    
    def get_provider_id(self) -> str:
        return "openai"


class OpenRouterAdapter(OpenAIAdapter):
    """OpenRouter adapter (OpenAI-compatible) with Azure call_id aliasing."""

    def create_tool_result_message(self, call_id: str, tool_name: str, result: Any) -> Dict[str, Any]:
        msg = super().create_tool_result_message(call_id, tool_name, result)
        # Some OpenRouter upstream providers (e.g. Azure) expect `call_id` for tool results.
        msg["call_id"] = call_id
        return msg

    def get_provider_id(self) -> str:
        return "openrouter"


class AnthropicAdapter(ProviderAdapter):
    """Anthropic-specific adapter for tool calling"""
    
    def should_use_native_tool(self, tool_def: Dict[str, Any]) -> bool:
        """Check if tool should use Anthropic native tool calling"""
        provider_routing = _get_provider_settings(tool_def)
        anthropic_config = provider_routing.get("anthropic", {})
        return anthropic_config.get("native_primary", False)
    
    def translate_tool_to_native_schema(self, tool_def: Dict[str, Any]) -> Dict[str, Any]:
        """Convert to Anthropic tool format"""
        properties = {}
        required = []
        provider_routing = _get_provider_settings(tool_def)
        anthropic_config = provider_routing.get("anthropic", {}) if isinstance(provider_routing, dict) else {}

        additional_props = anthropic_config.get("additional_properties")
        if not isinstance(additional_props, bool):
            additional_props = anthropic_config.get("additionalProperties")
        if not isinstance(additional_props, bool):
            additional_props = None

        schema_uri = anthropic_config.get("schema_uri") or anthropic_config.get("$schema")
        schema_uri = schema_uri if isinstance(schema_uri, str) and schema_uri else None
        
        for param in (_get_tool_attr(tool_def, "parameters", []) or []):
            param_name = _get_tool_attr(param, "name")
            param_type = _get_tool_attr(param, "type", "string")
            param_desc = _get_tool_attr(param, "description", "")
            param_required = bool(_get_tool_attr(param, "required", False))
            if not param_name:
                continue
            param_schema_raw = _get_tool_attr(param, "schema", None)
            if isinstance(param_schema_raw, dict) and param_schema_raw:
                prop_schema = dict(param_schema_raw)
                if "type" not in prop_schema:
                    prop_schema["type"] = param_type
                if param_desc and "description" not in prop_schema:
                    prop_schema["description"] = param_desc
            else:
                prop_schema = {
                    "type": param_type,
                    "description": param_desc,
                }

            try:
                if prop_schema.get("type") == "array" and "items" not in prop_schema:
                    prop_schema["items"] = {"type": "string"}
                if prop_schema.get("type") == "object":
                    prop_schema.setdefault("additionalProperties", True)
            except Exception:
                pass

            default_val = _get_tool_attr(param, "default")
            if default_val is not None and "default" not in prop_schema:
                prop_schema["default"] = default_val

            properties[param_name] = prop_schema
            
            if param_required:
                required.append(param_name)

        input_schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required
        if additional_props is not None:
            input_schema["additionalProperties"] = additional_props
        if schema_uri is not None:
            input_schema["$schema"] = schema_uri

        return {
            "name": _get_tool_attr(tool_def, "name"),
            "description": _get_tool_attr(tool_def, "description", ""),
            "input_schema": input_schema,
        }
    
    def extract_tool_calls_from_response(self, response_message: Any) -> List[Dict[str, Any]]:
        """Extract Anthropic tool calls"""
        # Anthropic uses different format - this would need to be implemented
        # based on their actual response format
        return []
    
    def create_tool_result_message(self, call_id: str, tool_name: str, result: Any) -> Dict[str, Any]:
        """Create Anthropic-expected tool result message"""
        # Anthropic uses different format for tool results
        if isinstance(result, dict) and isinstance(result.get("__mvi_text_output"), str):
            content = str(result["__mvi_text_output"])
        elif isinstance(result, dict):
            content = json.dumps(result, indent=2)
        else:
            content = str(result)
        
        return {
            "role": "user",  # Anthropic typically expects user role for tool results
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": content
                }
            ]
        }
    
    def get_provider_id(self) -> str:
        return "anthropic"


class ProviderAdapterManager:
    """Manages provider-specific adapters"""
    
    def __init__(self):
        self.adapters = {
            "openai": OpenAIAdapter(),
            "anthropic": AnthropicAdapter(),
            "openrouter": OpenRouterAdapter(),
            "mock": OpenAIAdapter(),
            "cli_mock": OpenAIAdapter(),
        }
    
    def get_adapter(self, provider_id: str) -> ProviderAdapter:
        """Get adapter for provider"""
        return self.adapters.get(provider_id, self.adapters["openai"])
    
    def filter_tools_for_provider(self, tools: List[Dict[str, Any]], provider_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Filter tools into provider-native vs text-based categories.
        
        Returns: (native_tools, text_based_tools)
        """
        adapter = self.get_adapter(provider_id)
        native_tools = []
        text_based_tools = []
        
        for tool in tools:
            if adapter.should_use_native_tool(tool):
                native_tools.append(tool)
            else:
                text_based_tools.append(tool)
        
        return native_tools, text_based_tools
    
    def translate_tools_to_native_schema(self, tools: List[Dict[str, Any]], provider_id: str) -> List[Dict[str, Any]]:
        """Convert tools to provider-native schema format"""
        adapter = self.get_adapter(provider_id)
        return [adapter.translate_tool_to_native_schema(tool) for tool in tools]


# Global instance

provider_adapter_manager = ProviderAdapterManager()
