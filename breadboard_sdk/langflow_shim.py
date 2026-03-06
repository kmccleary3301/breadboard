from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .langflow_executor import BreadboardLangflowRunner
from .langflow_workflow import (
    LangflowWorkflowSyncRequest,
    execute_langflow_workflow_sync_request,
    parse_langflow_workflow_sync_request,
)


NativeLangflowSyncExecutor = Callable[[LangflowWorkflowSyncRequest, Dict[str, Any], Exception | None], Dict[str, Any]]


@dataclass(frozen=True)
class LangflowShimResult:
    mode: str
    response: Dict[str, Any]
    compile_error: Optional[str] = None


def handle_langflow_v2_sync_request(
    workflow_request: Dict[str, Any] | LangflowWorkflowSyncRequest,
    *,
    flow: Optional[Dict[str, Any]] = None,
    flow_path: str | Path | None = None,
    breadboard_runner: BreadboardLangflowRunner,
    native_sync_executor: Optional[NativeLangflowSyncExecutor] = None,
) -> LangflowShimResult:
    request = (
        workflow_request
        if isinstance(workflow_request, LangflowWorkflowSyncRequest)
        else parse_langflow_workflow_sync_request(workflow_request)
    )

    native_fallback = None
    if native_sync_executor is not None:

        def _native_fallback(flow_payload: Dict[str, Any], _: Dict[str, Any], exc: Exception | None) -> Dict[str, Any]:
            return native_sync_executor(request, flow_payload, exc)

        native_fallback = _native_fallback

    result = execute_langflow_workflow_sync_request(
        request,
        flow=flow,
        flow_path=flow_path,
        breadboard_runner=breadboard_runner,
        native_fallback=native_fallback,
    )
    return LangflowShimResult(
        mode=result.mode,
        response=result.response,
        compile_error=result.compile_error,
    )


__all__ = [
    "LangflowShimResult",
    "NativeLangflowSyncExecutor",
    "handle_langflow_v2_sync_request",
]
