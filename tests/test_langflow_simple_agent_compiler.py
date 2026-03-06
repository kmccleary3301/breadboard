from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard_sdk.langflow import (
    LangflowCompileError,
    compile_langflow_simple_agent_ir,
    compile_langflow_simple_agent_ir_from_path,
)


def _fixture_payload() -> dict:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/contracts/langflow/simple_agent_minimal.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_compile_langflow_simple_agent_ir_accepts_supported_minimal_slice() -> None:
    ir = compile_langflow_simple_agent_ir(_fixture_payload())
    assert ir.schema_version == "langflow_simple_agent_ir_v1"
    assert ir.source_name == "Simple Agent"
    assert ir.input_node_id == "ChatInput-2M1cy"
    assert ir.agent_node_id == "Agent-oYRYa"
    assert ir.output_node_id == "ChatOutput-z90NZ"
    assert ir.default_input_text == "Hello, how are you?"
    assert ir.system_prompt.startswith("You are a helpful assistant")
    assert ir.model is not None
    assert ir.model.provider == "Anthropic"
    assert ir.model.name == "claude-opus-4-5-20251101"
    assert [tool.tool_kind for tool in ir.tools] == ["calculator", "url"]
    assert ir.output_data_template == "{text}"


def test_compile_langflow_simple_agent_ir_rejects_missing_chat_input_edge() -> None:
    payload = _fixture_payload()
    payload["data"]["edges"] = [
        edge
        for edge in payload["data"]["edges"]
        if not (edge["source"] == "ChatInput-2M1cy" and edge["target"] == "Agent-oYRYa")
    ]
    with pytest.raises(LangflowCompileError, match="ChatInput -> Agent input_value"):
        compile_langflow_simple_agent_ir(payload)


def test_compile_langflow_simple_agent_ir_rejects_unsupported_tool_component() -> None:
    payload = _fixture_payload()
    payload["data"]["nodes"].append(
        {
            "id": "PythonTool-abc",
            "data": {
                "node": {
                    "key": "PythonREPLComponent",
                    "display_name": "Python",
                    "field_order": ["code"],
                    "metadata": {
                        "module": "lfx.components.tools.python.PythonREPLComponent"
                    },
                    "template": {
                        "code": {
                            "value": ""
                        }
                    },
                }
            },
        }
    )
    payload["data"]["edges"].append(
        {
            "source": "PythonTool-abc",
            "target": "Agent-oYRYa",
            "data": {
                "targetHandle": {
                    "fieldName": "tools"
                }
            },
        }
    )
    with pytest.raises(LangflowCompileError, match="Unsupported Langflow tool component"):
        compile_langflow_simple_agent_ir(payload)


def test_compile_langflow_simple_agent_ir_from_path_reads_fixture_file() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/contracts/langflow/simple_agent_minimal.json"
    ir = compile_langflow_simple_agent_ir_from_path(fixture)
    assert ir.input_sender == "User"
    assert ir.output_sender_name == "AI"
