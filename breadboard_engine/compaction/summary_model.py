"""Runtime-backed :class:`SummaryModel` for LLM compaction methods.

Summaries go through the conductor's active provider runtime as tool-free,
non-streaming requests. Each one is recorded as a labelled side request
(``meta/requests/turn_N_compaction_K.json``) so it never overwrites the
turn's model request record and is distinguishable in evidence.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional

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
    """Tool-free summary completions through a conductor provider runtime."""

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
    ) -> None:
        self.runtime = runtime
        self.client = client
        self.model = model
        self.session_state = session_state
        self.agent_config = agent_config
        self.turn_index = turn_index
        self.recorder = recorder
        self.requests_sent = 0

    def complete(self, request: SummaryRequest) -> SummaryResponse:
        model = request.model or self.model
        budget = {} if request.max_tokens is None else {"max_tokens": request.max_tokens}
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
                request_body={"messages": messages, **budget},
                stream=False,
                tool_count=0,
                endpoint="compaction/summary",
                extra={"compaction_summary": True, "summary_purpose": request.purpose},
                label=label,
            )

        state = self.session_state
        get_meta = getattr(state, "get_provider_metadata", None)
        context = ProviderRuntimeContext(
            session_state=state,
            agent_config=dict(self.agent_config),
            stream=False,
            extra={
                "turn_index": turn_index,
                "model": model,
                "stream": False,
                "compaction_summary": True,
                "summary_purpose": request.purpose,
                **budget,
            },
            session_id=(get_meta("session_id") if callable(get_meta) else None)
            or getattr(state, "session_id", None),
        )
        result = self.runtime.invoke(
            client=self.client,
            model=model,
            messages=messages,
            tools=None,
            stream=False,
            context=context,
        )
        text = "".join(
            _text_of(message.content)
            for message in result.messages
            if getattr(message, "role", "assistant") == "assistant"
        )
        return SummaryResponse(text=text, usage=result.usage, model=result.model or model)
