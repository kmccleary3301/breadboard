"""Payload normalization helpers for CLI bridge events."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


def normalize_task_event_payload(payload: Dict[str, Any], parent_session_id: Optional[str] = None) -> Dict[str, Any]:
    """Normalize task-event payload aliases and graph fields."""
    normalized = dict(payload or {})
    kind = str(normalized.get("kind") or "").strip().lower()
    is_subagent_event = bool(
        str(normalized.get("subagent_type") or normalized.get("subagentType") or "").strip()
        or kind.startswith("subagent_")
        or kind.startswith("background_task")
    )
    if "taskId" in normalized and "task_id" not in normalized:
        normalized["task_id"] = normalized.get("taskId")
    if "subagentType" in normalized and "subagent_type" not in normalized:
        normalized["subagent_type"] = normalized.get("subagentType")
    if "artifactPath" in normalized and "artifact_path" not in normalized:
        normalized["artifact_path"] = normalized.get("artifactPath")
    artifact = normalized.get("artifact")
    if "artifact_path" not in normalized and isinstance(artifact, dict):
        path = artifact.get("path") or artifact.get("artifact_path")
        if path:
            normalized["artifact_path"] = path
    artifact_ref = _extract_artifact_ref(normalized)
    if artifact_ref is not None:
        normalized["artifact_ref"] = artifact_ref
    if "description" not in normalized:
        for key in ("description", "title", "summary"):
            value = normalized.get(key)
            if value:
                normalized["description"] = str(value)
                break
    if "status" not in normalized:
        kind = str(normalized.get("kind") or "").lower()
        status_map = {
            "subagent_spawned": "running",
            "subagent_started": "running",
            "subagent_completed": "completed",
            "subagent_failed": "failed",
        }
        status = status_map.get(kind)
        if status:
            normalized["status"] = status
    if "timestamp" not in normalized:
        for key in ("timestamp", "created_at", "completed_at", "ts"):
            value = normalized.get(key)
            if isinstance(value, (int, float)):
                normalized["timestamp"] = value
                break
        else:
            normalized["timestamp"] = time.time()
    if "parentTaskId" in normalized and "parent_task_id" not in normalized:
        normalized["parent_task_id"] = normalized.get("parentTaskId")
    if "treePath" in normalized and "tree_path" not in normalized:
        normalized["tree_path"] = normalized.get("treePath")
    if "taskDepth" in normalized and "depth" not in normalized:
        normalized["depth"] = normalized.get("taskDepth")
    if "taskPriority" in normalized and "priority" not in normalized:
        normalized["priority"] = normalized.get("taskPriority")
    if "sessionId" in normalized and "task_session_id" not in normalized:
        normalized["task_session_id"] = normalized.get("sessionId")

    child_session_id = (
        normalized.get("child_session_id")
        or normalized.get("childSessionId")
        or normalized.get("subagent_session_id")
        or normalized.get("subagentSessionId")
        or normalized.get("task_session_id")
        or normalized.get("sessionId")
    )
    child_session_id_str = str(child_session_id).strip() if child_session_id is not None else ""
    if child_session_id_str and (is_subagent_event or "child_session_id" in normalized or "childSessionId" in normalized):
        normalized.setdefault("child_session_id", child_session_id_str)
        normalized.setdefault("subagent_session_id", child_session_id_str)

    explicit_parent_session_id = normalized.get("parent_session_id") or normalized.get("parentSessionId")
    parent_session_id_str = str(explicit_parent_session_id).strip() if explicit_parent_session_id is not None else ""
    if not parent_session_id_str and is_subagent_event:
        parent_session_id_str = str(parent_session_id or "").strip()
    if parent_session_id_str:
        normalized.setdefault("parent_session_id", parent_session_id_str)

    child_label = (
        normalized.get("child_session_label")
        or normalized.get("childSessionLabel")
        or normalized.get("subagent_label")
        or normalized.get("subagentLabel")
        or normalized.get("lane_label")
        or normalized.get("laneLabel")
        or normalized.get("subagent_type")
        or normalized.get("description")
    )
    child_label_str = str(child_label).strip() if child_label is not None else ""
    if child_label_str and (is_subagent_event or child_session_id_str):
        normalized.setdefault("child_session_label", child_label_str)
        normalized.setdefault("subagent_label", child_label_str)

    lane_id = normalized.get("lane_id") or normalized.get("laneId")
    lane_id_str = str(lane_id).strip() if lane_id is not None else ""
    if not lane_id_str:
        lane_id_str = str(
            normalized.get("subagent_type")
            or normalized.get("task_id")
            or normalized.get("child_session_id")
            or ""
        ).strip()
    if lane_id_str:
        normalized.setdefault("lane_id", lane_id_str)

    lane_label = normalized.get("lane_label") or normalized.get("laneLabel")
    lane_label_str = str(lane_label).strip() if lane_label is not None else ""
    if not lane_label_str:
        lane_label_str = str(
            normalized.get("subagent_type")
            or normalized.get("child_session_label")
            or normalized.get("description")
            or ""
        ).strip()
    if lane_label_str:
        normalized.setdefault("lane_label", lane_label_str)

    task_id = normalized.get("task_id") or normalized.get("id")
    if task_id and "tree_path" not in normalized:
        normalized["tree_path"] = f"task/{task_id}"
    if "depth" not in normalized:
        normalized["depth"] = 0
    return normalized


def _extract_artifact_ref(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidate = payload.get("artifact_ref")
    if isinstance(candidate, dict):
        normalized = _normalize_artifact_ref(candidate)
        if normalized:
            return normalized
    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        normalized = _normalize_artifact_ref(artifact)
        if normalized:
            return normalized
    display = payload.get("display")
    if isinstance(display, dict):
        detail_artifact = display.get("detail_artifact")
        if isinstance(detail_artifact, dict):
            normalized = _normalize_artifact_ref(detail_artifact)
            if normalized:
                return normalized
    return None


def _normalize_artifact_ref(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    path = payload.get("path")
    sha256 = payload.get("sha256")
    schema_version = payload.get("schema_version") or "artifact_ref_v1"
    if not isinstance(path, str) or not path.strip():
        return None
    if not isinstance(sha256, str) or not sha256.strip():
        return None
    size_bytes = payload.get("size_bytes")
    size_int = int(size_bytes) if isinstance(size_bytes, (int, float)) else None
    if size_int is None or size_int < 0:
        return None
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        kind = "tool_result"
    mime = payload.get("mime")
    if not isinstance(mime, str) or not mime.strip():
        mime = "text/plain"
    storage = payload.get("storage")
    if not isinstance(storage, str) or not storage.strip():
        storage = "workspace_file"
    normalized: Dict[str, Any] = {
        "schema_version": str(schema_version),
        "id": str(payload.get("id") or f"artifact:{sha256[:16]}"),
        "kind": str(kind),
        "mime": str(mime),
        "size_bytes": int(size_int),
        "sha256": str(sha256),
        "storage": str(storage),
        "path": str(path).strip(),
    }
    preview = payload.get("preview")
    if isinstance(preview, dict):
        normalized["preview"] = preview
    return normalized
