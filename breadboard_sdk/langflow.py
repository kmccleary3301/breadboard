from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class LangflowCompileError(ValueError):
    """Raised when a Langflow flow is outside the supported phase-1 subset."""


@dataclass(frozen=True)
class LangflowModelRef:
    provider: str
    name: str
    raw: Dict[str, Any]


@dataclass(frozen=True)
class LangflowToolRef:
    node_id: str
    component_key: str
    tool_kind: str
    display_name: str
    module: Optional[str]
    field_order: List[str]


@dataclass(frozen=True)
class LangflowSimpleAgentIR:
    schema_version: str
    source_name: Optional[str]
    input_node_id: str
    agent_node_id: str
    output_node_id: str
    default_input_text: str
    input_sender: str
    input_sender_name: str
    input_should_store_message: bool
    system_prompt: str
    model: Optional[LangflowModelRef]
    max_iterations: int
    add_current_date_tool: bool
    verbose: bool
    output_sender: str
    output_sender_name: str
    output_should_store_message: bool
    output_clean_data: bool
    output_data_template: str
    tools: List[LangflowToolRef]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LangflowBreadboardExecutionPayload:
    schema_version: str
    session_id: Optional[str]
    input_text: str
    config_overrides: Dict[str, Any]
    projection: Dict[str, Any]
    raw_inputs: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


SUPPORTED_TOOL_COMPONENTS: Dict[str, str] = {
    "CalculatorComponent": "calculator",
    "URLComponent": "url",
}


def _read_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_langflow_flow(path: str | Path) -> Dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise LangflowCompileError("Langflow flow payload must be an object")
    return payload


def _bool_value(template: Dict[str, Any], key: str, *, default: bool) -> bool:
    field = template.get(key)
    if isinstance(field, dict):
        value = field.get("value", default)
        return bool(value)
    return default


def _int_value(template: Dict[str, Any], key: str, *, default: int) -> int:
    field = template.get(key)
    if isinstance(field, dict):
        value = field.get("value", default)
        if isinstance(value, int):
            return value
    return default


def _str_value(template: Dict[str, Any], key: str, *, default: str = "") -> str:
    field = template.get(key)
    if isinstance(field, dict):
        value = field.get("value", default)
        if isinstance(value, str):
            return value
    return default


def _template(node: Dict[str, Any]) -> Dict[str, Any]:
    data = node.get("data")
    if not isinstance(data, dict):
        return {}
    inner = data.get("node")
    if not isinstance(inner, dict):
        return {}
    template = inner.get("template")
    if isinstance(template, dict):
        return template
    return {}


def _node_payload(node: Dict[str, Any]) -> Dict[str, Any]:
    data = node.get("data")
    if not isinstance(data, dict):
        return {}
    inner = data.get("node")
    if isinstance(inner, dict):
        return inner
    return {}


def _node_key(node: Dict[str, Any]) -> Optional[str]:
    payload = _node_payload(node)
    key = payload.get("key")
    if isinstance(key, str) and key:
        return key
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        module = metadata.get("module")
        if isinstance(module, str) and module.endswith(".agent.AgentComponent"):
            return "Agent"
    if "system_prompt" in _template(node) and "model" in _template(node):
        return "Agent"
    return None


def _single_node(nodes: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    matches = [node for node in nodes if _node_key(node) == kind]
    if len(matches) != 1:
        raise LangflowCompileError(f"Expected exactly one {kind} node, found {len(matches)}")
    return matches[0]


def _node_id(node: Dict[str, Any]) -> str:
    value = node.get("id")
    if not isinstance(value, str) or not value:
        raise LangflowCompileError("Langflow node is missing a stable id")
    return value


def _edge_matches(edge: Dict[str, Any], *, source: str, target: str, target_field: str) -> bool:
    if edge.get("source") != source or edge.get("target") != target:
        return False
    data = edge.get("data")
    if not isinstance(data, dict):
        return False
    target_handle = data.get("targetHandle")
    if not isinstance(target_handle, dict):
        return False
    return target_handle.get("fieldName") == target_field


def _normalize_nodes(payload: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LangflowCompileError("Langflow flow payload is missing data")
    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise LangflowCompileError("Langflow flow data must contain nodes and edges")
    real_nodes: List[Dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") == "noteNode":
            continue
        payload = _node_payload(node)
        if payload.get("display_name") == "":
            continue
        real_nodes.append(node)
    return real_nodes, [edge for edge in edges if isinstance(edge, dict)]


def compile_langflow_simple_agent_ir(flow: Dict[str, Any]) -> LangflowSimpleAgentIR:
    nodes, edges = _normalize_nodes(flow)
    chat_input = _single_node(nodes, "ChatInput")
    agent = _single_node(nodes, "Agent")
    chat_output = _single_node(nodes, "ChatOutput")

    chat_input_id = _node_id(chat_input)
    agent_id = _node_id(agent)
    chat_output_id = _node_id(chat_output)

    if not any(_edge_matches(edge, source=chat_input_id, target=agent_id, target_field="input_value") for edge in edges):
        raise LangflowCompileError("Supported Langflow slice requires ChatInput -> Agent input_value")
    if not any(_edge_matches(edge, source=agent_id, target=chat_output_id, target_field="input_value") for edge in edges):
        raise LangflowCompileError("Supported Langflow slice requires Agent -> ChatOutput input_value")

    tools: List[LangflowToolRef] = []
    for edge in edges:
        if not _edge_matches(edge, source=edge.get("source"), target=agent_id, target_field="tools"):
            continue
        source_id = edge.get("source")
        if not isinstance(source_id, str):
            raise LangflowCompileError("Tool edge is missing a string source id")
        source_node = next((node for node in nodes if _node_id(node) == source_id), None)
        if source_node is None:
            raise LangflowCompileError(f"Tool edge references unknown source node {source_id}")
        source_key = _node_key(source_node)
        if source_key not in SUPPORTED_TOOL_COMPONENTS:
            raise LangflowCompileError(f"Unsupported Langflow tool component: {source_key or 'unknown'}")
        payload = _node_payload(source_node)
        metadata = payload.get("metadata")
        module = metadata.get("module") if isinstance(metadata, dict) and isinstance(metadata.get("module"), str) else None
        field_order = payload.get("field_order")
        tools.append(
            LangflowToolRef(
                node_id=source_id,
                component_key=source_key,
                tool_kind=SUPPORTED_TOOL_COMPONENTS[source_key],
                display_name=str(payload.get("display_name") or source_key),
                module=module,
                field_order=[value for value in field_order if isinstance(value, str)] if isinstance(field_order, list) else [],
            )
        )

    agent_template = _template(agent)
    model_ref: Optional[LangflowModelRef] = None
    model_field = agent_template.get("model")
    if isinstance(model_field, dict):
        value = model_field.get("value")
        if isinstance(value, list) and value and isinstance(value[0], dict):
            raw = dict(value[0])
            model_ref = LangflowModelRef(
                provider=str(raw.get("provider") or ""),
                name=str(raw.get("name") or ""),
                raw=raw,
            )

    source_name = None
    meta = flow.get("metadata")
    if isinstance(meta, dict):
        name = meta.get("name")
        if isinstance(name, str) and name:
            source_name = name

    output_template = _template(chat_output)
    input_template = _template(chat_input)

    return LangflowSimpleAgentIR(
        schema_version="langflow_simple_agent_ir_v1",
        source_name=source_name,
        input_node_id=chat_input_id,
        agent_node_id=agent_id,
        output_node_id=chat_output_id,
        default_input_text=_str_value(input_template, "input_value"),
        input_sender=_str_value(input_template, "sender", default="User"),
        input_sender_name=_str_value(input_template, "sender_name", default="User"),
        input_should_store_message=_bool_value(input_template, "should_store_message", default=True),
        system_prompt=_str_value(agent_template, "system_prompt"),
        model=model_ref,
        max_iterations=_int_value(agent_template, "max_iterations", default=15),
        add_current_date_tool=_bool_value(agent_template, "add_current_date_tool", default=False),
        verbose=_bool_value(agent_template, "verbose", default=False),
        output_sender=_str_value(output_template, "sender", default="Machine"),
        output_sender_name=_str_value(output_template, "sender_name", default="AI"),
        output_should_store_message=_bool_value(output_template, "should_store_message", default=True),
        output_clean_data=_bool_value(output_template, "clean_data", default=True),
        output_data_template=_str_value(output_template, "data_template", default="{text}"),
        tools=sorted(tools, key=lambda tool: tool.node_id),
    )


def compile_langflow_simple_agent_ir_from_path(path: str | Path) -> LangflowSimpleAgentIR:
    return compile_langflow_simple_agent_ir(load_langflow_flow(path))


def _raw_inputs(inputs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if inputs is None:
        return {}
    if not isinstance(inputs, dict):
        raise LangflowCompileError("Langflow flat inputs must be a dict")
    return dict(inputs)


def _extract_session_id(flat_inputs: Dict[str, Any]) -> Optional[str]:
    for key, value in flat_inputs.items():
        if not isinstance(key, str):
            continue
        if key.endswith(".session_id") and isinstance(value, str) and value:
            return value
    return None


def build_langflow_breadboard_execution_payload(
    ir: LangflowSimpleAgentIR,
    *,
    flat_inputs: Optional[Dict[str, Any]] = None,
) -> LangflowBreadboardExecutionPayload:
    inputs = _raw_inputs(flat_inputs)
    input_text = str(inputs.get(f"{ir.input_node_id}.input_value") or ir.default_input_text)
    system_prompt = str(inputs.get(f"{ir.agent_node_id}.system_prompt") or ir.system_prompt)

    config_overrides: Dict[str, Any] = {
        "langflow_adapter": {
            "mode": "simple_agent_v1",
            "input_node_id": ir.input_node_id,
            "agent_node_id": ir.agent_node_id,
            "output_node_id": ir.output_node_id,
        },
        "model": ir.model.raw if ir.model is not None else None,
        "system_prompt": system_prompt,
        "max_iterations": ir.max_iterations,
        "verbose": ir.verbose,
        "add_current_date_tool": ir.add_current_date_tool,
        "tools": [tool.tool_kind for tool in ir.tools],
    }

    projection = {
        "flow_output_node_id": ir.output_node_id,
        "type": "message",
        "sender": ir.output_sender,
        "sender_name": ir.output_sender_name,
        "data_template": ir.output_data_template,
        "clean_data": ir.output_clean_data,
    }

    return LangflowBreadboardExecutionPayload(
        schema_version="langflow_breadboard_execution_payload_v1",
        session_id=_extract_session_id(inputs),
        input_text=input_text,
        config_overrides=config_overrides,
        projection=projection,
        raw_inputs=inputs,
    )


def project_breadboard_terminal_to_langflow_response(
    ir: LangflowSimpleAgentIR,
    payload: LangflowBreadboardExecutionPayload,
    *,
    flow_id: str,
    terminal_text: str,
    status: str = "completed",
    errors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "flow_id": flow_id,
        "object": "response",
        "status": status,
        "errors": list(errors or []),
        "inputs": dict(payload.raw_inputs),
        "outputs": {
            ir.output_node_id: {
                "type": "message",
                "status": status,
                "content": terminal_text,
                "metadata": {
                    "sender": ir.output_sender,
                    "sender_name": ir.output_sender_name,
                    "data_template": ir.output_data_template,
                    "clean_data": ir.output_clean_data,
                    "session_id": payload.session_id,
                },
            }
        },
    }


__all__ = [
    "LangflowBreadboardExecutionPayload",
    "LangflowCompileError",
    "LangflowModelRef",
    "LangflowToolRef",
    "LangflowSimpleAgentIR",
    "SUPPORTED_TOOL_COMPONENTS",
    "build_langflow_breadboard_execution_payload",
    "compile_langflow_simple_agent_ir",
    "compile_langflow_simple_agent_ir_from_path",
    "load_langflow_flow",
    "project_breadboard_terminal_to_langflow_response",
]
