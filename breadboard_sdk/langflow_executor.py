from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .langflow import (
    LangflowBreadboardExecutionPayload,
    LangflowCompileError,
    LangflowSimpleAgentIR,
    build_langflow_breadboard_execution_payload,
    compile_langflow_simple_agent_ir,
    compile_langflow_simple_agent_ir_from_path,
    load_langflow_flow,
    project_breadboard_terminal_to_langflow_response,
)


BreadboardLangflowRunner = Callable[[LangflowBreadboardExecutionPayload], str | Dict[str, Any]]
LangflowNativeFallback = Callable[[Dict[str, Any], Dict[str, Any], Exception | None], Dict[str, Any]]


@dataclass(frozen=True)
class LangflowExecutorResult:
    mode: str
    response: Dict[str, Any]
    ir: Optional[LangflowSimpleAgentIR] = None
    payload: Optional[LangflowBreadboardExecutionPayload] = None
    compile_error: Optional[str] = None


def _normalize_runner_result(result: str | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(result, str):
        return {
            "terminal_text": result,
            "status": "completed",
            "errors": [],
        }
    if not isinstance(result, dict):
        raise TypeError("BreadBoard Langflow runner must return a string or dict")
    terminal_text = result.get("terminal_text")
    if not isinstance(terminal_text, str):
        raise TypeError("BreadBoard Langflow runner dict must include terminal_text: str")
    status = result.get("status", "completed")
    errors = result.get("errors", [])
    if not isinstance(status, str):
        raise TypeError("BreadBoard Langflow runner dict field status must be a string")
    if not isinstance(errors, list):
        raise TypeError("BreadBoard Langflow runner dict field errors must be a list")
    return {
        "terminal_text": terminal_text,
        "status": status,
        "errors": errors,
    }


def execute_langflow_simple_agent_sync(
    flow: Dict[str, Any],
    *,
    flow_id: str,
    flat_inputs: Optional[Dict[str, Any]],
    breadboard_runner: BreadboardLangflowRunner,
    native_fallback: Optional[LangflowNativeFallback] = None,
) -> LangflowExecutorResult:
    try:
        ir = compile_langflow_simple_agent_ir(flow)
    except LangflowCompileError as exc:
        if native_fallback is None:
            raise
        response = native_fallback(flow, flat_inputs or {}, exc)
        return LangflowExecutorResult(mode="fallback", response=response, compile_error=str(exc))

    payload = build_langflow_breadboard_execution_payload(ir, flat_inputs=flat_inputs)
    runner_result = _normalize_runner_result(breadboard_runner(payload))
    response = project_breadboard_terminal_to_langflow_response(
        ir,
        payload,
        flow_id=flow_id,
        terminal_text=runner_result["terminal_text"],
        status=runner_result["status"],
        errors=runner_result["errors"],
    )
    return LangflowExecutorResult(
        mode="breadboard",
        response=response,
        ir=ir,
        payload=payload,
    )


def execute_langflow_simple_agent_sync_from_path(
    path: str | Path,
    *,
    flow_id: str,
    flat_inputs: Optional[Dict[str, Any]],
    breadboard_runner: BreadboardLangflowRunner,
    native_fallback: Optional[LangflowNativeFallback] = None,
) -> LangflowExecutorResult:
    flow = load_langflow_flow(path)
    return execute_langflow_simple_agent_sync(
        flow,
        flow_id=flow_id,
        flat_inputs=flat_inputs,
        breadboard_runner=breadboard_runner,
        native_fallback=native_fallback,
    )


__all__ = [
    "BreadboardLangflowRunner",
    "LangflowExecutorResult",
    "LangflowNativeFallback",
    "execute_langflow_simple_agent_sync",
    "execute_langflow_simple_agent_sync_from_path",
]
