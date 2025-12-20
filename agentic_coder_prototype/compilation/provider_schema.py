from __future__ import annotations

from typing import Any, Dict, List

from ..dialects.enhanced_base_dialect import EnhancedToolDefinition, EnhancedToolParameter


_TYPE_MAP = {
    None: "string",
    "string": "string",
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "array": "array",
    "object": "object",
}


def _param_to_property(param: EnhancedToolParameter) -> Dict[str, Any]:
    t = _TYPE_MAP.get(param.type, "string")
    prop: Dict[str, Any] = {"type": t}
    if param.description:
        prop["description"] = param.description
    
    # CRITICAL: Handle array types properly for OpenAI schema validation
    if t == "array":
        # Arrays must have items schema to be valid
        prop["items"] = {"type": "string"}  # Default to string items
        # You could enhance this with param.items_type if available
        
    # For object types, provide empty properties if not specified
    elif t == "object":
        prop["properties"] = {}
        prop["additionalProperties"] = True
    
    if param.default is not None:
        prop["default"] = param.default
    return prop


def to_openai_tool_schema(tool: EnhancedToolDefinition) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for p in tool.parameters or []:
        properties[p.name] = _param_to_property(p)
        if getattr(p, "required", False):
            required.append(p.name)

    schema: Dict[str, Any] = {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
    return schema


def build_openai_tools_schema_from_yaml(tools: List[EnhancedToolDefinition]) -> List[Dict[str, Any]]:
    """Convert a list of EnhancedToolDefinition to OpenAI tools array."""
    result: List[Dict[str, Any]] = []
    for t in tools:
        try:
            result.append(to_openai_tool_schema(t))
        except Exception:
            # Best-effort: skip ill-formed tool
            continue
    return result


def filter_tools_for_provider_native(tools: List[EnhancedToolDefinition], provider: str) -> List[EnhancedToolDefinition]:
    """Return tools that opt into provider-native calling for given provider.

    Respects YAML provider_routing.<provider>.native_primary = true.
    If provider settings are missing, defaults to False (opt-in only).
    """
    provider = provider.lower()
    filtered: List[EnhancedToolDefinition] = []
    for t in tools or []:
        try:
            pr = getattr(t, "provider_settings", {}) or {}
            p = pr.get(provider) or {}
            if bool(p.get("native_primary", False)):
                filtered.append(t)
        except Exception:
            continue
    return filtered


