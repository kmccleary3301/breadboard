from __future__ import annotations

from typing import Any, Dict, List, Optional

from .provider_runtime import ProviderRuntime, ProviderRuntimeContext, ProviderRuntimeError, ProviderResult


class ReplayRuntime(ProviderRuntime):
    """Runtime that replays pre-recorded ProviderResult objects."""

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {"replay": True}

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        session_state = getattr(context, "session_state", None)
        if session_state is None:
            raise ProviderRuntimeError("Replay runtime requires a valid session_state")
        replay_session = None
        try:
            replay_session = session_state.get_provider_metadata("active_replay_session")
        except Exception:
            replay_session = None
        if replay_session is None:
            raise ProviderRuntimeError("No active ReplaySession found in session_state")

        result = replay_session.next_result(messages=messages, tools=tools)
        if not isinstance(result, ProviderResult):
            raise ProviderRuntimeError("Replay session returned invalid ProviderResult")

        try:
            meta = dict(result.metadata or {})
        except Exception:
            meta = {}
        meta["replay"] = True
        result.metadata = meta
        result.model = "replay-playback"
        return result
