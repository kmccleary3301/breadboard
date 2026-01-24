from __future__ import annotations

from agentic_coder_prototype.dialects.enhanced_base_dialect import (
    EnhancedToolDefinition,
    EnhancedToolParameter,
)
from agentic_coder_prototype.compilation.tool_yaml_loader import load_yaml_tools
from agentic_coder_prototype.provider_adapters import OpenAIAdapter


def test_openai_adapter_translates_dataclass_params_with_default() -> None:
    tool = EnhancedToolDefinition(
        name="demo",
        description="demo tool",
        parameters=[
            EnhancedToolParameter(
                name="timeout",
                type="integer",
                description="timeout",
                default=30,
                required=False,
            )
        ],
        provider_settings={"openai": {"native_primary": True}},
    )

    schema = OpenAIAdapter().translate_tool_to_native_schema(tool)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "demo"
    props = schema["function"]["parameters"]["properties"]
    assert props["timeout"]["default"] == 30


def test_openai_adapter_preserves_nested_schema_from_yaml_loader() -> None:
    loaded = load_yaml_tools("implementations/tools/defs")
    tool = next(t for t in loaded.tools if t.name == "todo.create")
    items_param = next(p for p in tool.parameters if p.name == "items")

    assert items_param.schema.get("type") == "array"
    assert isinstance(items_param.schema.get("items"), dict)
    assert items_param.schema["items"].get("type") == "object"
    assert "properties" in (items_param.schema["items"] or {})

    schema = OpenAIAdapter().translate_tool_to_native_schema(tool)
    assert schema["function"]["name"] == "todo_create"
    props = schema["function"]["parameters"]["properties"]
    assert props["items"]["type"] == "array"
    assert props["items"]["items"]["type"] == "object"

