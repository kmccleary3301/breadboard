from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


class StreamingPolicy:
    """Encapsulates per-turn streaming policy decisions."""

    def __init__(self, provider_metrics: Any) -> None:
        self.provider_metrics = provider_metrics

    def apply(
        self,
        *,
        model: str,
        runtime_descriptor: Any,
        tools_schema: Optional[List[Dict[str, Any]]],
        stream_requested: bool,
        session_state: Any,
        markdown_logger: Any,
        turn_index: int,
        route_id: Optional[str],
        routing_preferences: Dict[str, Any],
        capability: Optional[Dict[str, Any]],
        logger_v2: Any = None,
        md_writer: Any = None,
        log_routing_event: Optional[Callable[..., None]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        provider_id = getattr(runtime_descriptor, "provider_id", None)
        runtime_id = getattr(runtime_descriptor, "runtime_id", None)
        effective_stream = stream_requested
        policy: Optional[Dict[str, Any]] = None

        if stream_requested and tools_schema and provider_id == "openrouter":
            effective_stream = False
            policy = {
                "turn_index": turn_index,
                "model": model,
                "provider": provider_id,
                "runtime": runtime_id,
                "reason": "openrouter_tool_turn_policy",
                "stream_requested": True,
                "stream_effective": False,
            }
            self.provider_metrics.add_stream_override(
                route=route_id,
                reason="openrouter_tool_turn_policy",
            )
            self._record_stream_policy_metadata(session_state, policy)
            self._emit_policy_transcript(session_state, policy)
            self._log_system_note(
                note=(
                    "[stream-policy] Forcing stream=false for OpenRouter tool turn "
                    f"(turn={turn_index}, model={model})."
                ),
                markdown_logger=markdown_logger,
                logger_v2=logger_v2,
                md_writer=md_writer,
            )

        disable_stream = (
            capability
            and capability.get("attempted")
            and capability.get("stream_success") is False
            and effective_stream
            and routing_preferences.get("disable_stream_on_probe_failure", True)
        )
        if disable_stream:
            effective_stream = False
            override_payload = {
                "turn_index": turn_index,
                "model": model,
                "provider": provider_id,
                "runtime": runtime_id,
                "reason": "capability_probe_stream_failure",
                "stream_requested": stream_requested,
                "stream_effective": False,
                "route_id": route_id,
            }
            if policy is None:
                override_payload["reasons"] = ["capability_probe_stream_failure"]
                policy = override_payload
            else:
                merged = dict(policy)
                reasons = merged.get("reasons")
                if not isinstance(reasons, list):
                    reasons_list: List[str] = []
                    if merged.get("reason"):
                        reasons_list.append(merged["reason"])
                else:
                    reasons_list = list(reasons)
                reasons_list.append("capability_probe_stream_failure")
                merged["reasons"] = reasons_list
                merged["capability_override"] = True
                merged["stream_effective"] = False
                merged["stream_requested"] = stream_requested
                merged["route_id"] = route_id
                policy = merged

            payload = {
                "route": route_id,
                "capabilities": {
                    "stream_success": capability.get("stream_success"),
                    "tool_stream_success": capability.get("tool_stream_success"),
                    "json_mode_success": capability.get("json_mode_success"),
                },
                "policy": policy,
            }
            message = (
                f"[stream-policy] Disabled streaming for route '{route_id}' based on capability probe results."
            )
            self.provider_metrics.add_stream_override(
                route=route_id,
                reason="capability_probe_stream_failure",
            )
            if log_routing_event:
                log_routing_event(
                    turn_index=turn_index,
                    tag="stream_policy",
                    message=message,
                    payload=payload,
                )
            try:
                session_state.set_provider_metadata(
                    "capability_stream_override",
                    {
                        "route": route_id,
                        "turn_index": turn_index,
                        "reason": "capability_probe_stream_failure",
                    },
                )
            except Exception:
                pass

        if policy is not None:
            self._record_stream_policy_metadata(session_state, policy)
            self._emit_policy_transcript(session_state, policy)
        return effective_stream, policy

    def _record_stream_policy_metadata(self, session_state: Any, policy: Dict[str, Any]) -> None:
        try:
            history = session_state.get_provider_metadata("stream_policy_history", [])
        except Exception:
            history = []
        if not isinstance(history, list):
            history = []
        updated_history = history + [policy]
        if len(updated_history) > 20:
            updated_history = updated_history[-20:]
        try:
            session_state.set_provider_metadata("stream_policy_history", updated_history)
            session_state.set_provider_metadata("last_stream_policy", policy)
        except Exception:
            pass

    def _emit_policy_transcript(self, session_state: Any, policy: Dict[str, Any]) -> None:
        try:
            session_state.add_transcript_entry({"stream_policy": policy})
        except Exception:
            pass

    def _log_system_note(self, note: str, markdown_logger: Any, logger_v2: Any, md_writer: Any) -> None:
        try:
            markdown_logger.log_system_message(note)
        except Exception:
            pass
        try:
            if getattr(logger_v2, "run_dir", None) and md_writer:
                logger_v2.append_text("conversation/conversation.md", md_writer.system(note))
        except Exception:
            pass
