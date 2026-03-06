from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .langflow import load_langflow_flow
from .langflow_executor import (
    BreadboardLangflowRunner,
    LangflowExecutorResult,
    LangflowNativeFallback,
    execute_langflow_simple_agent_sync,
)


class LangflowWorkflowRequestError(ValueError):
    """Raised when a Langflow workflow request is invalid for the sync adapter."""


@dataclass(frozen=True)
class LangflowWorkflowSyncRequest:
    flow_id: str
    inputs: Dict[str, Any]
    background: bool = False
    stream: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "inputs": dict(self.inputs),
            "background": self.background,
            "stream": self.stream,
        }


def parse_langflow_workflow_sync_request(payload: Dict[str, Any]) -> LangflowWorkflowSyncRequest:
    if not isinstance(payload, dict):
        raise LangflowWorkflowRequestError("Langflow workflow request payload must be a dict")

    allowed = {"flow_id", "inputs", "background", "stream"}
    extras = sorted(key for key in payload if key not in allowed)
    if extras:
        raise LangflowWorkflowRequestError(f"Unsupported Langflow workflow request keys: {', '.join(extras)}")

    flow_id = payload.get("flow_id")
    if not isinstance(flow_id, str) or not flow_id:
        raise LangflowWorkflowRequestError("Langflow workflow request requires flow_id: str")

    background = bool(payload.get("background", False))
    stream = bool(payload.get("stream", False))
    if background:
        raise LangflowWorkflowRequestError("Background mode is not supported by the BreadBoard Langflow sync adapter")
    if stream:
        raise LangflowWorkflowRequestError("Streaming mode is not supported by the BreadBoard Langflow sync adapter")

    inputs = payload.get("inputs", {})
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise LangflowWorkflowRequestError("Langflow workflow request inputs must be a dict")

    return LangflowWorkflowSyncRequest(
        flow_id=flow_id,
        inputs=dict(inputs),
        background=False,
        stream=False,
    )


def execute_langflow_workflow_sync_request(
    workflow_request: Dict[str, Any] | LangflowWorkflowSyncRequest,
    *,
    flow: Optional[Dict[str, Any]] = None,
    flow_path: str | Path | None = None,
    breadboard_runner: BreadboardLangflowRunner,
    native_fallback: Optional[LangflowNativeFallback] = None,
) -> LangflowExecutorResult:
    request = (
        workflow_request
        if isinstance(workflow_request, LangflowWorkflowSyncRequest)
        else parse_langflow_workflow_sync_request(workflow_request)
    )
    if flow is None:
        if flow_path is None:
            raise LangflowWorkflowRequestError("Either flow or flow_path must be provided")
        flow = load_langflow_flow(flow_path)
    return execute_langflow_simple_agent_sync(
        flow,
        flow_id=request.flow_id,
        flat_inputs=request.inputs,
        breadboard_runner=breadboard_runner,
        native_fallback=native_fallback,
    )


__all__ = [
    "LangflowWorkflowRequestError",
    "LangflowWorkflowSyncRequest",
    "execute_langflow_workflow_sync_request",
    "parse_langflow_workflow_sync_request",
]
