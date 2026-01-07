"""
Session state management for agentic coding loops
"""

from typing import Any, Callable, Dict, List, Optional
from pathlib import Path
import json
import time

from dataclasses import asdict

from ..reasoning_trace_store import ReasoningTraceStore
from ..ctrees.store import CTreeStore
from ..monitoring.reward_metrics import RewardMetricsRecorder, RewardMetricsRecord
from ..provider_ir import (
    IRConversation,
    IRDeltaEvent,
    IRFinish,
    convert_legacy_messages,
)


class SessionState:
    """Manages session state for agentic coding loops"""
    
    def __init__(
        self,
        workspace: str,
        image: str,
        config: Optional[Dict[str, Any]] = None,
        *,
        event_emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]] = None,
    ):
        self.workspace = workspace
        self.image = image
        self.config = config or {}
        self.messages: List[Dict[str, Any]] = []
        self.provider_messages: List[Dict[str, Any]] = []
        self.transcript: List[Dict[str, Any]] = []
        self.last_tool_prompt_mode = "unknown"
        self.completion_config = {}
        self.current_native_tools = []
        self.current_text_based_tools = []
        self.completion_summary: Dict[str, Any] = {}
        self.provider_metadata: Dict[str, Any] = {}
        self.reasoning_traces = ReasoningTraceStore()
        self.ir_events: List[IRDeltaEvent] = []
        self.ir_finish: Optional[IRFinish] = None
        self.reward_metrics = RewardMetricsRecorder()
        self.tool_usage_summary: Dict[str, Any] = {
            "total_calls": 0,
            "write_calls": 0,
            "successful_writes": 0,
            "run_shell_calls": 0,
            "test_commands": 0,
            "successful_tests": 0,
        }
        self.turn_tool_usage: Dict[int, Dict[str, Any]] = {}
        self.consecutive_tool_free_turns = 0
        self.completion_guard_failures = 0
        self.guardrail_counters: Dict[str, int] = {}
        self.guardrail_events: List[Dict[str, Any]] = []
        self.lifecycle_events: List[Dict[str, Any]] = []
        self._event_seq = 0
        self._event_emitter = event_emitter
        self._active_turn_index: Optional[int] = None
        self._turn_assistant_emitted = False
        self._turn_user_emitted = False
        self._last_ctree_node_id: Optional[str] = None
        self._last_ctree_snapshot: Optional[Dict[str, Any]] = None
        self.todo_manager = None
        self.tool_usage_summary.setdefault("todo_calls", 0)
        self.ctree_store = CTreeStore()

    def set_event_emitter(
        self,
        emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]],
    ) -> None:
        self._event_emitter = emitter

    def _next_event_seq(self) -> int:
        self._event_seq += 1
        return self._event_seq

    def _emit_event(self, event_type: str, payload: Dict[str, Any], *, turn: Optional[int] = None) -> Optional[int]:
        seq = self._next_event_seq()
        if not self._event_emitter:
            if isinstance(payload, dict) and "seq" not in payload:
                payload["seq"] = seq
            return seq
        try:
            if isinstance(payload, dict) and "seq" not in payload:
                payload["seq"] = seq
            self._event_emitter(event_type, payload, turn=turn)
        except Exception:
            # Event handlers should never break the session loop.
            return seq
        return seq

    def record_lifecycle_event(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        turn: Optional[int] = None,
    ) -> None:
        entry: Dict[str, Any] = {
            "type": str(event_type),
            "payload": dict(payload or {}),
        }
        if turn is not None:
            entry["turn"] = turn
        try:
            entry["timestamp"] = time.time()
        except Exception:
            pass
        self.lifecycle_events.append(entry)
        seq = self._emit_event("lifecycle_event", entry, turn=turn)
        if isinstance(seq, int):
            entry["seq"] = seq
        try:
            self._record_ctree("lifecycle", entry, turn=turn)
        except Exception:
            pass

    # --- Todo integration ---------------------------------------------------

    def set_todo_manager(self, manager: Any) -> None:
        self.todo_manager = manager
        if manager is None:
            self.provider_metadata.pop("todos_enabled", None)
        else:
            self.provider_metadata["todos_enabled"] = True

    def get_todo_manager(self) -> Any:
        return self.todo_manager

    def emit_todo_event(self, payload: Dict[str, Any]) -> None:
        try:
            if self.todo_manager:
                snapshot = self.todo_manager.snapshot()
                self.provider_metadata["todo_snapshot"] = snapshot
        except Exception:
            pass
        self._emit_event("todo_event", payload, turn=self._active_turn_index)

    def emit_task_event(self, payload: Dict[str, Any]) -> None:
        """Emit a multi-agent/task lifecycle event to observers."""
        enriched = dict(payload or {})
        if self._last_ctree_node_id and "ctree_node_id" not in enriched:
            enriched["ctree_node_id"] = self._last_ctree_node_id
        if self._last_ctree_snapshot and "ctree_snapshot" not in enriched:
            enriched["ctree_snapshot"] = dict(self._last_ctree_snapshot)
        self._emit_event("task_event", enriched, turn=self._active_turn_index)

    def emit_permission_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit permission request/response events to observers."""
        self._emit_event(str(event_type), dict(payload or {}), turn=self._active_turn_index)

    def todo_snapshot(self) -> Optional[Dict[str, Any]]:
        if not self.todo_manager:
            return None
        try:
            return self.todo_manager.snapshot()
        except Exception:
            return None
    
    def add_message(self, message: Dict[str, Any], to_provider: bool = True):
        """Add a message to the session state"""
        self.messages.append(message)
        if to_provider:
            self.provider_messages.append(message.copy())
        if not isinstance(message, dict):
            return

        role = message.get("role")
        turn_hint = self._active_turn_index if isinstance(self._active_turn_index, int) else None
        payload = {"message": message}

        try:
            message_payload = {
                "role": role,
                "content": message.get("content"),
                "tool_calls": message.get("tool_calls"),
                "name": message.get("name"),
            }
            self._record_ctree("message", message_payload, turn=turn_hint)
        except Exception:
            pass

        if role == "assistant":
            emit_now = (not to_provider) or (not self._turn_assistant_emitted)
            if emit_now:
                self._emit_event("assistant_message", payload, turn=turn_hint)
                self._turn_assistant_emitted = True
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if isinstance(call, dict):
                            self._emit_event("tool_call", {"call": call}, turn=turn_hint)
        elif role == "user":
            emit_now = (not to_provider) or (not self._turn_user_emitted)
            if emit_now:
                self._emit_event("user_message", payload, turn=turn_hint)
                self._turn_user_emitted = True
        elif role == "tool":
            self._emit_event("tool_result", payload, turn=turn_hint)

    def add_transcript_entry(self, entry: Dict[str, Any]):
        """Add an entry to the transcript"""
        self.transcript.append(entry)
        try:
            self._record_ctree("transcript", entry, turn=self._active_turn_index)
        except Exception:
            pass

    # --- Guardrail telemetry -------------------------------------------------
    def record_guardrail_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Record a structured guardrail event and emit it to observers."""
        entry: Dict[str, Any] = {
            "type": str(event_type),
            "payload": dict(payload or {}),
            "turn": self._active_turn_index,
        }
        try:
            entry["timestamp"] = time.time()
        except Exception:
            pass
        self.guardrail_events.append(entry)
        seq = self._emit_event("guardrail_event", entry, turn=self._active_turn_index)
        if isinstance(seq, int):
            entry["seq"] = seq
        try:
            self._record_ctree("guardrail", entry, turn=self._active_turn_index)
        except Exception:
            pass

    def _record_ctree(
        self,
        kind: str,
        payload: Any,
        *,
        turn: Optional[int] = None,
    ) -> Optional[str]:
        node_id = self.ctree_store.record(kind, payload, turn=turn)
        try:
            node = self.ctree_store.nodes[-1] if self.ctree_store.nodes else None
            if isinstance(node, dict):
                self._last_ctree_node_id = node.get("id")
                snapshot = self.ctree_store.snapshot()
                self._last_ctree_snapshot = snapshot
                self._emit_event(
                    "ctree_node",
                    {
                        "node": dict(node),
                        "snapshot": snapshot,
                    },
                    turn=turn,
                )
        except Exception:
            pass
        return node_id

    def emit_ctree_snapshot(self, payload: Dict[str, Any]) -> None:
        """Emit a summary snapshot for C-Tree metadata."""
        self._emit_event("ctree_snapshot", dict(payload or {}), turn=self._active_turn_index)

    def get_guardrail_events(self) -> List[Dict[str, Any]]:
        return list(self.guardrail_events)

    # --- Provider metadata ----------------------------------------------------
    def set_provider_metadata(self, key: str, value: Any) -> None:
        self.provider_metadata[key] = value

    def get_provider_metadata(self, key: str, default: Any = None) -> Any:
        return self.provider_metadata.get(key, default)

    def clear_provider_metadata(self) -> None:
        self.provider_metadata.clear()

    # --- IR helpers ---------------------------------------------------------
    def add_ir_event(self, event: IRDeltaEvent) -> None:
        self.ir_events.append(event)

    def set_ir_finish(self, finish: IRFinish) -> None:
        self.ir_finish = finish

    def build_conversation_ir(self, conversation_id: str, ir_version: str = "1") -> IRConversation:
        messages_ir = convert_legacy_messages(self.messages)
        return IRConversation(
            id=conversation_id,
            ir_version=ir_version,
            messages=messages_ir,
            events=list(self.ir_events),
            finish=self.ir_finish,
        )

    def get_debug_info(self) -> Dict[str, Any]:
        """Get enhanced debugging information about tool usage and provider configuration"""
        provider_cfg = self.config.get("provider_tools", {})
        return {
            "provider_tools_config": provider_cfg,
            "tool_prompt_mode": self.last_tool_prompt_mode,
            "native_tools_enabled": bool(provider_cfg.get("use_native", False)),
            "tools_suppressed": bool(provider_cfg.get("suppress_prompts", False)),
            "yaml_tools_count": len(getattr(self, 'yaml_tools', [])),
            "enhanced_executor_enabled": bool(getattr(self, 'enhanced_executor', None)),
            "provider_metadata_keys": sorted(self.provider_metadata.keys()),
            "reasoning_trace_counts": {
                "encrypted": len(self.reasoning_traces.get_encrypted_traces()),
                "summaries": len(self.reasoning_traces.get_summaries()),
            },
        }
    
    def analyze_tool_usage(self) -> Dict[str, Any]:
        """Analyze messages for tool calling patterns"""
        return {
            "total_messages": len(self.messages),
            "assistant_messages": len([m for m in self.messages if m.get("role") == "assistant"]),
            "messages_with_native_tool_calls": len([m for m in self.messages if m.get("role") == "assistant" and m.get("tool_calls")]),
            "messages_with_text_tool_calls": len([m for m in self.messages if m.get("role") == "assistant" and m.get("content") and "<TOOL_CALL>" in str(m.get("content", ""))]),
            "tool_role_messages": len([m for m in self.messages if m.get("role") == "tool"]),
        }

    # --- Reward metrics ----------------------------------------------------
    def add_reward_metrics(
        self,
        turn_index: int,
        metrics: Optional[Dict[str, Any]] = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RewardMetricsRecord:
        """Add or update reward metrics for a turn."""
        return self.reward_metrics.record_turn(turn_index, metrics, metadata=metadata, overwrite=False)

    def add_reward_metric(self, turn_index: int, name: str, value: Any) -> None:
        self.reward_metrics.set_metric(turn_index, name, value)

    def reward_metrics_payload(self) -> Dict[str, Any]:
        return self.reward_metrics.as_payload()
    
    def create_snapshot(self, model: str, diff: Dict[str, Any] = None) -> Dict[str, Any]:
        """Create a session snapshot for debugging and persistence"""
        snapshot = {
            "workspace": self.workspace,
            "image": self.image,
            "model": model,
            "messages": self.messages,
            "transcript": self.transcript,
            "diff": diff or {"ok": False, "data": {"diff": ""}},
            "debug_info": self.get_debug_info(),
            "tool_analysis": self.analyze_tool_usage(),
            "completion_summary": self.completion_summary,
            "reward_metrics": self.reward_metrics_payload(),
            "provider_metadata": self.provider_metadata,
            "todos": self.todo_snapshot(),
            "reasoning_trace_counts": {
                "encrypted": len(self.reasoning_traces.get_encrypted_traces()),
                "summaries": len(self.reasoning_traces.get_summaries()),
            },
            "ir_version": "1",
            "conversation_ir": asdict(self.build_conversation_ir(conversation_id="snapshot")),
        }
        return snapshot
    
    def write_snapshot(self, output_path: Optional[str], model: str, diff: Dict[str, Any] = None):
        """Write session snapshot to JSON file"""
        if not output_path:
            return
        
        try:
            snapshot = self.create_snapshot(model, diff)
            outp = Path(output_path)
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(snapshot, indent=2))
        except Exception:
            pass

    # --- Turn / tool usage tracking ----------------------------------------

    def begin_turn(self, turn_index: int) -> None:
        """Reset per-turn tool tracking metadata before provider invocation."""
        self.set_provider_metadata("current_turn_index", turn_index)
        self.set_provider_metadata("turn_has_tool_usage", False)
        self.turn_tool_usage.setdefault(turn_index, {"tools": []})
        self._active_turn_index = turn_index if isinstance(turn_index, int) else None
        self._turn_assistant_emitted = False
        self._turn_user_emitted = False
        self._emit_event("turn_start", {"turn": turn_index}, turn=self._active_turn_index)
        self.record_lifecycle_event("turn_started", {"turn": turn_index}, turn=self._active_turn_index)

    def record_tool_event(
        self,
        turn_index: Optional[int],
        tool_name: str,
        *,
        success: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a single tool invocation for watchdogs and guardrails."""
        meta = metadata or {}
        is_todo = bool(meta.get("is_todo"))
        if is_todo:
            self.tool_usage_summary["todo_calls"] = int(self.tool_usage_summary.get("todo_calls", 0)) + 1
        else:
            self.tool_usage_summary["total_calls"] += 1
        per_turn = None
        if isinstance(turn_index, int):
            per_turn = self.turn_tool_usage.setdefault(turn_index, {"tools": []})
            per_turn["tools"].append(
                {
                    "name": tool_name,
                    "success": bool(success),
                    "meta": dict(meta),
                }
            )
        if is_todo:
            # Todo events are emitted separately; do not mark the turn as having tool usage
            return
        if meta.get("is_write"):
            self.tool_usage_summary["write_calls"] += 1
            if success:
                self.tool_usage_summary["successful_writes"] += 1
        if meta.get("is_run_shell"):
            self.tool_usage_summary["run_shell_calls"] += 1
        if meta.get("is_test_command"):
            self.tool_usage_summary["test_commands"] += 1
            if meta.get("exit_code") == 0 and success:
                self.tool_usage_summary["successful_tests"] += 1
        self.set_provider_metadata("turn_has_tool_usage", True)
        turn_hint = turn_index if isinstance(turn_index, int) else self._active_turn_index
        payload: Dict[str, Any] = {
            "tool": tool_name,
            "success": bool(success),
            "metadata": dict(metadata or {}),
            "status": "ok" if success else "error",
            "error": (not bool(success)),
        }
        call_id = meta.get("call_id") or meta.get("tool_call_id") or meta.get("toolCallId")
        if isinstance(call_id, str) and call_id.strip():
            payload["call_id"] = call_id.strip()
        self._emit_event(
            "tool_result",
            payload,
            turn=turn_hint,
        )

    def turn_had_tool_activity(self) -> bool:
        return bool(self.get_provider_metadata("turn_has_tool_usage", False))

    def turn_had_todo_activity(self, turn_index: Optional[int] = None) -> bool:
        index = turn_index
        if index is None:
            meta_index = self.get_provider_metadata("current_turn_index")
            if isinstance(meta_index, int):
                index = meta_index
            elif isinstance(self._active_turn_index, int):
                index = self._active_turn_index
        if index is None:
            return False
        bucket = self.turn_tool_usage.get(index) or {}
        for entry in bucket.get("tools", []) or []:
            meta = entry.get("meta") or {}
            if meta.get("is_todo"):
                return True
        return False

    def reset_tool_free_streak(self) -> None:
        self.consecutive_tool_free_turns = 0

    def increment_tool_free_streak(self) -> int:
        self.consecutive_tool_free_turns += 1
        return self.consecutive_tool_free_turns

    def increment_guard_failures(self) -> int:
        self.completion_guard_failures += 1
        return self.completion_guard_failures

    def guard_failure_count(self) -> int:
        return self.completion_guard_failures

    def increment_guardrail_counter(self, name: str, *, amount: int = 1) -> None:
        """Increment a guardrail counter (used for telemetry + summaries)."""
        if not name:
            return
        try:
            current = int(self.guardrail_counters.get(name, 0))
        except Exception:
            current = 0
        self.guardrail_counters[name] = current + max(amount, 0)

    def get_guardrail_counters(self) -> Dict[str, int]:
        """Return guardrail counters snapshot."""
        return dict(self.guardrail_counters)
