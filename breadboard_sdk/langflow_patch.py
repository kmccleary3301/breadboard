from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict

from .langflow_shim import NativeLangflowSyncExecutor, handle_langflow_v2_sync_request


AsyncLangflowNativeExecutor = Callable[..., Awaitable[Dict[str, Any]] | Dict[str, Any]]


def _coerce_request_payload(workflow_request: Any) -> Dict[str, Any]:
    if isinstance(workflow_request, dict):
        return dict(workflow_request)
    flow_id = getattr(workflow_request, "flow_id", None)
    inputs = getattr(workflow_request, "inputs", None)
    background = getattr(workflow_request, "background", False)
    stream = getattr(workflow_request, "stream", False)
    return {
        "flow_id": flow_id,
        "inputs": dict(inputs or {}),
        "background": background,
        "stream": stream,
    }


def _coerce_flow_payload(flow: Any) -> Dict[str, Any]:
    if isinstance(flow, dict):
        return dict(flow)
    data = getattr(flow, "data", None)
    if not isinstance(data, dict):
        raise ValueError("Langflow patch bridge expected flow.data to be a dict payload")
    return data


def make_langflow_v2_sync_override(
    *,
    breadboard_runner,
    native_sync_executor: AsyncLangflowNativeExecutor,
) -> Callable[..., Awaitable[Dict[str, Any]]]:
    async def _override(workflow_request: Any, flow: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        request_payload = _coerce_request_payload(workflow_request)
        flow_payload = _coerce_flow_payload(flow)

        async def _native(request_obj, raw_flow_payload, exc):
            result = native_sync_executor(workflow_request, flow, *args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        def _sync_native_bridge(request_obj, raw_flow_payload, exc):
            return _native(request_obj, raw_flow_payload, exc)

        result = handle_langflow_v2_sync_request(
            request_payload,
            flow=flow_payload,
            breadboard_runner=breadboard_runner,
            native_sync_executor=_sync_native_bridge,
        )
        if result.mode == "fallback":
            fallback_response = result.response
            if inspect.isawaitable(fallback_response):
                return await fallback_response
            return fallback_response
        return result.response

    return _override


__all__ = [
    "AsyncLangflowNativeExecutor",
    "make_langflow_v2_sync_override",
]
