"""Runtime-backed :class:`SummaryModel` for LLM compaction methods.

Summaries use the conductor's active provider runtime and preset-selected
request options. Each one is recorded as a labelled side request
(``meta/requests/turn_N_compaction_K.json``) so it never overwrites the
turn's model request record and is distinguishable in evidence.
"""

from __future__ import annotations

import copy
from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Dict, List, Mapping, Optional, Sequence

from ..provider.contract_runtime import ProviderRuntimeContext
from .methods import SummaryRequest, SummaryResponse


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""


class ConductorSummaryModel:
    """Preset-selected summary completions through a conductor provider runtime."""

    def __init__(
        self,
        *,
        runtime: Any,
        client: Any,
        model: str,
        session_state: Any,
        agent_config: Mapping[str, Any],
        turn_index: Optional[int] = None,
        recorder: Optional[Any] = None,
        client_lease: Optional[Callable[[str, Any], ContextManager[Any]]] = None,
        tool_schema_provider: Optional[Callable[[], Sequence[Mapping[str, Any]]]] = None,
        route_id: Optional[str] = None,
    ) -> None:
        self.runtime = runtime
        self.client = client
        self.model = model
        self.session_state = session_state
        self.agent_config = agent_config
        self.turn_index = turn_index
        self.recorder = recorder
        self.client_lease = client_lease
        self.tool_schema_provider = tool_schema_provider
        self.route_id = route_id
        self.requests_sent = 0

    def complete(self, request: SummaryRequest) -> SummaryResponse:
        model = request.model or self.model
        budget = {} if request.max_tokens is None else {"max_tokens": request.max_tokens}
        tools = [copy.deepcopy(dict(tool)) for tool in request.tools]
        if not tools and request.request_params is None:
            tools = None
        if request.tool_names:
            available = self.tool_schema_provider() if self.tool_schema_provider is not None else ()
            schemas = {
                tool.get("name") or (tool.get("function") or {}).get("name"): tool
                for tool in available
            }
            missing = [name for name in request.tool_names if name not in schemas]
            if missing:
                raise ValueError(f"Unknown summary tool names: {missing}")
            tools = [copy.deepcopy(dict(schemas[name])) for name in request.tool_names]
        messages: List[Dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(copy.deepcopy(dict(message)) for message in request.messages)

        turn_index = self.turn_index or 0
        self.requests_sent += 1
        label = f"compaction_{self.requests_sent}"
        descriptor = getattr(self.runtime, "descriptor", None)
        if self.recorder is not None:
            self.recorder.record_request(
                turn_index,
                provider_id=getattr(descriptor, "provider_id", "unknown"),
                runtime_id=getattr(descriptor, "runtime_id", "unknown"),
                model=model,
                request_headers={},
                request_body={"messages": messages, **budget, **(request.request_params or {}),
                              **({"tools": tools} if tools else {})},
                stream=request.stream,
                tool_count=len(tools or ()),
                endpoint="compaction/summary",
                extra={"compaction_summary": True, "summary_purpose": request.purpose},
                label=label,
            )

        state = self.session_state
        get_meta = getattr(state, "get_provider_metadata", None)
        context = ProviderRuntimeContext(
            session_state=state,
            agent_config=dict(self.agent_config),
            stream=request.stream,
            extra={
                "turn_index": turn_index,
                "model": model,
                "stream": request.stream,
                "compaction_summary": True,
                "summary_purpose": request.purpose,
                **budget,
                **({"compaction_request_options_declared": True} if request.request_options_declared else {}),
                **({"compaction_stateless": True} if request.stateless else {}),
                **({"compaction_request_params": dict(request.request_params)} if request.request_params is not None else {}),
            },
            session_id=(get_meta("session_id") if callable(get_meta) else None)
            or getattr(state, "session_id", None),
        )
        # Production clients are scoped to a broker lease per provider call.
        # Explicit supplied clients preserve the standalone/test contract.
        route_id = self.route_id if model == self.model and self.route_id is not None else model
        lease = self.client_lease(route_id, self.runtime) if self.client is None and self.client_lease is not None else nullcontext(self.client)
        with lease as client:
            result = self.runtime.invoke(
                client=client,
                model=model,
                messages=messages,
                tools=tools,
                stream=request.stream,
                context=context,
            )
        text = "".join(
            _text_of(message.content)
            for message in result.messages
            if getattr(message, "role", "assistant") == "assistant"
        )
        finish_reason = next((message.finish_reason for message in reversed(result.messages)
                              if getattr(message, "role", "assistant") == "assistant"), None)
        return SummaryResponse(text=text, usage=result.usage, model=result.model or model, finish_reason=finish_reason)
