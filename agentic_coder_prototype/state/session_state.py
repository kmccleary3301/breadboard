"""
Session state management for agentic coding loops
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path
import json
import os
import time

import re
from dataclasses import asdict

from ..reasoning_trace_store import ReasoningTraceStore
from ..ctrees.store import CTreeStore
from ..ctrees.schema import normalize_ctree_snapshot_summary
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
        self._ctree_message_map: List[Dict[str, Any]] = []
        self._ctree_task_aliases: Dict[str, str] = {}
        self._ctree_task_alias_counter = 0
        self._ctree_task_seen: set[str] = set()
        self.todo_manager = None
        self.tool_usage_summary.setdefault("todo_calls", 0)
        self.ctree_store = CTreeStore()
        self._last_compaction_turn: Optional[int] = None
        self._last_compaction_message_count: Optional[int] = None

    # --- Compaction/summary helpers ---------------------------------------

    def _compaction_config(self) -> Dict[str, Any]:
        cfg = {}
        if isinstance(self.config, dict):
            raw = self.config.get("session_compaction")
            if isinstance(raw, dict):
                cfg = dict(raw)
        return cfg

    def _env_flag(self, name: str) -> Optional[bool]:
        raw = os.environ.get(name)
        if raw is None:
            return None
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return None

    def _env_int(self, name: str) -> Optional[int]:
        raw = os.environ.get(name)
        if raw is None:
            return None
        try:
            return int(str(raw).strip())
        except Exception:
            return None

    def _resolve_compaction_settings(self) -> Tuple[bool, int, int, int, int]:
        cfg = self._compaction_config()
        enabled = self._env_flag("BREADBOARD_SESSION_COMPACTION")
        if enabled is None:
            enabled = bool(cfg.get("enabled", False))
        message_threshold = self._env_int("BREADBOARD_SESSION_COMPACTION_MESSAGE_THRESHOLD")
        if message_threshold is None:
            message_threshold = int(cfg.get("message_threshold") or cfg.get("min_messages") or 0)
        turn_interval = self._env_int("BREADBOARD_SESSION_COMPACTION_TURN_INTERVAL")
        if turn_interval is None:
            turn_interval = int(cfg.get("turn_interval") or 0)
        max_chars = self._env_int("BREADBOARD_SESSION_COMPACTION_MAX_CHARS")
        if max_chars is None:
            max_chars = int(cfg.get("max_chars") or 240)
        recent_limit = self._env_int("BREADBOARD_SESSION_COMPACTION_RECENT_LIMIT")
        if recent_limit is None:
            recent_limit = int(cfg.get("recent_limit") or 4)
        return bool(enabled), max(message_threshold, 0), max(turn_interval, 0), max(max_chars, 80), max(recent_limit, 2)

    def _summarize_recent_messages(self, *, limit: int, max_chars: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for message in reversed(self.messages):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content") or ""
            text = str(content).strip()
            if not text:
                continue
            if len(text) > max_chars:
                text = text[: max(0, max_chars - 1)] + "…"
            items.append({"role": role, "content": text})
            if len(items) >= limit:
                break
        return list(reversed(items))

    def build_compaction_summary(self, *, max_chars: int = 240, recent_limit: int = 4) -> Tuple[str, Dict[str, Any]]:
        user_count = sum(1 for msg in self.messages if isinstance(msg, dict) and msg.get("role") == "user")
        assistant_count = sum(1 for msg in self.messages if isinstance(msg, dict) and msg.get("role") == "assistant")
        tool_usage = dict(self.tool_usage_summary or {})
        recent = self._summarize_recent_messages(limit=recent_limit, max_chars=max_chars)
        recent_lines = [f"{item['role']}: {item['content']}" for item in recent]
        summary_parts = [
            f"Turns={len(self.transcript)}",
            f"messages=user:{user_count},assistant:{assistant_count}",
        ]
        if tool_usage:
            summary_parts.append(
                "tools=" + ",".join(f"{k}:{tool_usage.get(k)}" for k in sorted(tool_usage.keys()))
            )
        if recent_lines:
            summary_parts.append("recent=" + " | ".join(recent_lines))
        summary = "; ".join(summary_parts)
        if len(summary) > max_chars:
            summary = summary[: max(0, max_chars - 1)] + "…"
        details = {
            "turns": len(self.transcript),
            "message_counts": {"user": user_count, "assistant": assistant_count},
            "tool_usage": tool_usage,
            "recent_messages": recent,
        }
        return summary, details

    def maybe_emit_compaction_summary(self, *, reason: str = "auto") -> Optional[str]:
        enabled, message_threshold, turn_interval, max_chars, recent_limit = self._resolve_compaction_settings()
        if not enabled:
            return None
        turn = self._active_turn_index
        if turn is None:
            return None
        if self._last_compaction_turn is not None and turn_interval:
            if (turn - self._last_compaction_turn) < turn_interval:
                return None
        if message_threshold and len(self.messages) < message_threshold:
            return None
        if self._last_compaction_message_count is not None:
            if len(self.messages) == self._last_compaction_message_count:
                return None
        summary, details = self.build_compaction_summary(max_chars=max_chars, recent_limit=recent_limit)
        details["reason"] = reason
        self.emit_compaction_summary(summary, details=details, turn=turn)
        self._last_compaction_turn = turn
        self._last_compaction_message_count = len(self.messages)
        return summary

    def _ctree_task_alias(self, raw_task_id: Any) -> Optional[str]:
        if raw_task_id is None:
            return None
        raw = str(raw_task_id).strip()
        if not raw:
            return None
        if raw.lower() == "root":
            return "root"
        if raw in self._ctree_task_aliases:
            return self._ctree_task_aliases[raw]
        self._ctree_task_alias_counter += 1
        alias = f"task_{self._ctree_task_alias_counter:04d}"
        self._ctree_task_aliases[raw] = alias
        return alias

    def _ctree_task_alias_path(self, raw_path: Any) -> Optional[str]:
        if raw_path is None:
            return None
        path = str(raw_path).strip()
        if not path:
            return None
        parts = [part for part in re.split(r"/+", path) if part]
        if not parts:
            return None
        aliased = []
        for part in parts:
            alias = self._ctree_task_alias(part)
            aliased.append(alias or part)
        return "/".join(aliased)

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

    def emit_stream_event(self, event_type: str, payload: Dict[str, Any], *, turn: Optional[int] = None) -> Optional[int]:
        """Emit a streaming event to observers (used for provider deltas)."""
        return self._emit_event(event_type, payload, turn=turn)

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
        record_turn = self._active_turn_index
        if isinstance(enriched.get("turn"), int):
            record_turn = enriched.get("turn")
        self._emit_event("task_event", enriched, turn=record_turn)
        try:
            cfg = self.config if isinstance(self.config, dict) else {}
            ctrees_cfg = cfg.get("ctrees") or {}
            record_cfg = ctrees_cfg.get("record_task_events") if isinstance(ctrees_cfg, dict) else None
            enabled = False
            if record_cfg is True:
                enabled = True
            elif isinstance(record_cfg, dict) and record_cfg.get("enabled") is True:
                enabled = True
            if not enabled:
                return

            minimal: Dict[str, Any] = {}
            for key in ("kind", "task_id", "parent_task_id", "tree_path", "depth", "priority", "subagent_type", "status"):
                if key in enriched:
                    minimal[key] = enriched.get(key)

            raw_task_id = minimal.get("task_id")
            alias_task_id = self._ctree_task_alias(raw_task_id)
            if alias_task_id:
                minimal["task_id"] = alias_task_id
            else:
                minimal.pop("task_id", None)

            raw_parent_id = minimal.get("parent_task_id")
            alias_parent_id = self._ctree_task_alias(raw_parent_id)
            if alias_parent_id:
                minimal["parent_task_id"] = alias_parent_id
            else:
                minimal.pop("parent_task_id", None)

            aliased_path = self._ctree_task_alias_path(minimal.get("tree_path"))
            if aliased_path:
                minimal["tree_path"] = aliased_path
            else:
                minimal.pop("tree_path", None)

            session_id = enriched.get("sessionId") or enriched.get("session_id")
            # Intentionally omit runtime session/job ids from C-Trees for replay stability.
            artifact = enriched.get("artifact")
            if isinstance(artifact, dict):
                path = artifact.get("path")
                if isinstance(path, str) and path and alias_task_id and raw_task_id and str(raw_task_id) in path:
                    minimal["artifact_path"] = path.replace(str(raw_task_id), alias_task_id)

            if minimal:
                self._record_ctree("task_event", minimal, turn=record_turn)

            include_subagent_nodes = False
            if isinstance(record_cfg, dict) and record_cfg.get("include_subagent_nodes") is True:
                include_subagent_nodes = True
            if include_subagent_nodes and alias_task_id:
                if alias_task_id not in self._ctree_task_seen:
                    self._ctree_task_seen.add(alias_task_id)
                    subagent_payload = {
                        "task_id": alias_task_id,
                        "parent_task_id": minimal.get("parent_task_id"),
                        "tree_path": minimal.get("tree_path"),
                        "subagent_type": minimal.get("subagent_type"),
                        "status": minimal.get("status"),
                    }
                    self._record_ctree("subagent", subagent_payload, turn=record_turn)
        except Exception:
            pass

    def emit_permission_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit permission request/response events to observers."""
        self._emit_event(str(event_type), dict(payload or {}), turn=self._active_turn_index)

    # --- Session compaction / branch summary hooks -------------------------
    def emit_compaction_summary(
        self,
        summary: str,
        *,
        first_kept_id: Optional[str] = None,
        tokens_before: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        turn: Optional[int] = None,
    ) -> None:
        payload: Dict[str, Any] = {"summary": summary}
        if first_kept_id:
            payload["first_kept_id"] = first_kept_id
        if tokens_before is not None:
            payload["tokens_before"] = tokens_before
        if details:
            payload["details"] = dict(details)
        self._emit_event("session.compaction", payload, turn=turn or self._active_turn_index)

    def emit_branch_summary(
        self,
        summary: str,
        *,
        from_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        turn: Optional[int] = None,
    ) -> None:
        payload: Dict[str, Any] = {"summary": summary}
        if from_id:
            payload["from_id"] = from_id
        if details:
            payload["details"] = dict(details)
        self._emit_event("session.branch_summary", payload, turn=turn or self._active_turn_index)

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

        node_id: Optional[str] = None
        try:
            message_payload = {
                "role": role,
                "content": message.get("content"),
                "tool_calls": message.get("tool_calls"),
                "name": message.get("name"),
            }
            node_id = self._record_ctree("message", message_payload, turn=turn_hint)
        except Exception:
            node_id = None

        if to_provider and isinstance(node_id, str) and node_id:
            try:
                provider_index = len(self.provider_messages) - 1
                if provider_index >= 0:
                    self._ctree_message_map.append({"node_id": node_id, "provider_index": provider_index})
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
                snapshot_raw = self.ctree_store.snapshot()
                snapshot = normalize_ctree_snapshot_summary(snapshot_raw) or snapshot_raw
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
