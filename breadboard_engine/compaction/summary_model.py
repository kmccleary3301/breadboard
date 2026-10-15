"""Runtime-backed :class:`SummaryModel` for LLM compaction methods.

Summaries use the conductor's active provider runtime, or the runtime of a
routed ``summaryModel``, and preset-selected request options. Each one is
recorded as a labelled side request
(``meta/requests/turn_N_compaction_K.json``) so it never overwrites the
turn's model request record and is distinguishable in evidence.
"""

from __future__ import annotations

import copy
from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Dict, List, Mapping, Optional, Sequence, Tuple

from ..provider.history_projection import portable_provider_messages
from ..provider.contract_runtime import ProviderRuntimeContext
from .methods import SummaryRequest, SummaryResponse
from .state import ProjectionTarget
from .images import IMAGE_PLACEHOLDER


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


def routed_runtime(route_id: str) -> Tuple[Any, str]:
    """Provider runtime and API model for a routed model ID such as ``openai/gpt-5.1-codex-mini``."""
    from ..provider.routing import provider_router
    from ..provider.runtime import provider_registry

    descriptor, api_model = provider_router.get_runtime_descriptor(route_id)
    return provider_registry.create_runtime(descriptor), api_model


def _api_of(runtime: Any) -> Tuple[Any, Any]:
    descriptor = getattr(runtime, "descriptor", None)
    return getattr(descriptor, "provider_id", None), getattr(descriptor, "default_api_variant", None)





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
        tool_schema_provider: Optional[Callable[[str], Sequence[Mapping[str, Any]]]] = None,
        route_id: Optional[str] = None,
        route_resolver: Optional[Callable[[str], Tuple[Any, str]]] = None,
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
        self.route_resolver = route_resolver
        self._resolved_routes: Dict[str, Tuple[Any, str]] = {}
        self.requests_sent = 0

    def _route_for(self, model: str) -> Tuple[Any, str]:
        if model == self.model or self.route_resolver is None:
            return self.runtime, model
        resolved = self._resolved_routes.get(model)
        if resolved is None:
            resolved = self.route_resolver(model)
            self._resolved_routes[model] = resolved
        return resolved

    def projection_target(
        self, model: Optional[str] = None, source_target: Optional[ProjectionTarget] = None,
    ) -> ProjectionTarget:
        """Resolve the route identity used to build this summary's history view."""
        runtime, api_model = self._route_for(model or self.model)
        provider, api = _api_of(runtime)
        if source_target is not None and runtime is self.runtime and (provider is None or api is None):
            return ProjectionTarget(source_target.provider, source_target.api, str(api_model))
        return ProjectionTarget(
            provider=str(provider or "unknown"),
            api=str(api or "unknown"),
            model=str(api_model),
        )

    def complete(self, request: SummaryRequest) -> SummaryResponse:
        projection = None
        configured = (self.agent_config.get("prompts") or {}).get("summary_projection")
        if configured is not None:
            from ..compilation.context_builders import load_context_callback

            projection = load_context_callback(configured, "build_summary_projection")(self.session_state, self.agent_config)
            request = projection.prepare(request)
        model = request.model or self.model
        budget = {} if request.max_tokens is None else {"max_tokens": request.max_tokens}
        runtime, api_model = self._route_for(model)
        descriptor = getattr(runtime, "descriptor", None)
        tools = [copy.deepcopy(dict(tool)) for tool in request.tools]
        if not tools and request.request_params is None:
            tools = None
        if request.tool_names:
            # Schemas are provider-native, so they follow the summary route, not the turn.
            provider = str(getattr(descriptor, "provider_id", None) or "unknown")
            available = self.tool_schema_provider(provider) if self.tool_schema_provider is not None else ()
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
        route_api, source_api = _api_of(runtime), _api_of(self.runtime)
        same_route = api_model == self.model and (
            runtime is self.runtime or (None not in route_api and route_api == source_api)
        )
        source = request.messages if same_route else portable_provider_messages(
            request.messages,
            source_provider=str(source_api[0] or "unknown"),
            source_api=str(source_api[1] or "unknown"),
            target_provider=str(route_api[0] or "unknown"),
            target_api=str(route_api[1] or "unknown"),
            session_state=self.session_state,
            image_placeholder=IMAGE_PLACEHOLDER,
        )
        messages.extend(copy.deepcopy(dict(message)) for message in source)

        turn_index = self.turn_index or 0
        self.requests_sent += 1
        label = f"compaction_{self.requests_sent}"
        if self.recorder is not None:
            self.recorder.record_request(
                turn_index,
                provider_id=getattr(descriptor, "provider_id", "unknown"),
                runtime_id=getattr(descriptor, "runtime_id", "unknown"),
                model=api_model,
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
            native_request_field_order=request.native_request_field_order,
            extra={
                "turn_index": turn_index,
                "model": model,
                "stream": request.stream,
                "compaction_summary": True,
                "summary_purpose": request.purpose,
                **budget,
                **({"compaction_request_options_declared": True} if request.request_options_declared else {}),
                **({"compaction_request_params": dict(request.request_params)} if request.request_params is not None else {}),
            },
            session_id=(get_meta("session_id") if callable(get_meta) else None)
            or getattr(state, "session_id", None),
        )
        # Production clients are scoped to a broker lease per provider call.
        # Explicit supplied clients preserve the standalone/test contract.
        route_id = self.route_id if model == self.model and self.route_id is not None else model
        lease = self.client_lease(route_id, runtime) if self.client is None and self.client_lease is not None else nullcontext(self.client)
        with lease as client:
            result = runtime.invoke(
                client=client,
                model=api_model,
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
        response = SummaryResponse(text=text, usage=result.usage, model=result.model or model,
                                   finish_reason=finish_reason,
                                   provider_result=result if projection is not None else None)
        if projection is None:
            return response
        continuation = projection.complete(response)
        return SummaryResponse(text=text, usage=result.usage, model=result.model or model,
                               finish_reason=finish_reason, provider_result=result,
                               continuation_messages=None if continuation is None else tuple(continuation))
