from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Iterable, Mapping, Optional

SIGNAL_CODES = frozenset(
    {
        "partial_complete",
        "merge_ready",
        "complete",
        "blocked",
        "no_progress",
        "retryable_failure",
        "catastrophic_failure",
        "human_required",
    }
)

SOURCE_KINDS = frozenset(
    {
        "assistant_content",
        "text_sentinel",
        "provider_finish",
        "tool_call",
        "runtime",
        "worker",
        "supervisor",
        "host",
        "system",
    }
)

EMITTER_ROLES = frozenset({"assistant", "worker", "supervisor", "host", "runtime", "system"})

LEGACY_COMPLETION_SOURCE_KINDS = frozenset(
    {
        "assistant_content",
        "text_sentinel",
        "provider_finish",
        "tool_call",
    }
)

DEFAULT_REQUIRE_EVIDENCE_FOR = frozenset({"merge_ready", "catastrophic_failure", "human_required"})


def _clean_string(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def build_signal_proposal(
    *,
    code: str,
    task_id: str,
    source_kind: str,
    emitter_role: str,
    authority_scope: str = "task",
    parent_task_id: Optional[str] = None,
    mission_task_id: Optional[str] = None,
    source_detail: Optional[str] = None,
    evidence_refs: Optional[Iterable[str]] = None,
    payload: Optional[Mapping[str, Any]] = None,
    signal_id: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = [str(item) for item in (evidence_refs or []) if str(item).strip()]
    return {
        "schema_version": "bb.signal.v1",
        "signal_id": str(signal_id or f"signal_{uuid.uuid4().hex}"),
        "code": str(code or ""),
        "task_id": str(task_id or ""),
        "parent_task_id": _clean_string(parent_task_id),
        "mission_task_id": _clean_string(mission_task_id),
        "authority_scope": str(authority_scope or "task"),
        "status": "proposed",
        "source": {
            "kind": str(source_kind or ""),
            "emitter_role": str(emitter_role or ""),
            "detail": _clean_string(source_detail),
        },
        "evidence_refs": evidence,
        "payload": dict(payload or {}),
        "validation": None,
    }


def infer_completion_signal_source_kind(method: Any, reason: Any = None) -> str:
    method_name = str(method or "").strip().lower()
    reason_text = str(reason or "").strip().lower()
    if method_name in {"tool_based", "tool_mark_task_complete"}:
        return "tool_call"
    if method_name == "finish_reason":
        return "provider_finish"
    if "sentinel" in method_name or "explicit_completion_marker" in reason_text:
        return "text_sentinel"
    return "assistant_content"


def build_completion_signal_proposal(
    analysis: Mapping[str, Any],
    *,
    task_id: str,
    parent_task_id: Optional[str] = None,
    mission_task_id: Optional[str] = None,
    emitter_role: str = "assistant",
    authority_scope: str = "task",
    evidence_refs: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    signal_code = str(analysis.get("signal_code") or "complete")
    source_kind = str(
        analysis.get("signal_source_kind")
        or infer_completion_signal_source_kind(analysis.get("method"), analysis.get("reason"))
    )
    payload = {
        "completion_method": analysis.get("method"),
        "completion_reason": analysis.get("reason"),
        "confidence": analysis.get("confidence"),
        "source": analysis.get("source"),
    }
    return build_signal_proposal(
        code=signal_code,
        task_id=task_id,
        parent_task_id=parent_task_id,
        mission_task_id=mission_task_id,
        source_kind=source_kind,
        emitter_role=emitter_role,
        source_detail=_clean_string(analysis.get("reason")),
        evidence_refs=evidence_refs,
        authority_scope=authority_scope,
        payload=payload,
    )


def build_tool_completion_signal_proposal(
    *,
    task_id: str,
    tool_name: str,
    tool_result: Mapping[str, Any],
    parent_task_id: Optional[str] = None,
    mission_task_id: Optional[str] = None,
    emitter_role: str = "assistant",
    authority_scope: str = "task",
    evidence_refs: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    payload = {
        "completion_method": "tool_mark_task_complete",
        "completion_reason": "mark_task_complete",
        "tool_name": str(tool_name or ""),
        "tool_action": tool_result.get("action") if isinstance(tool_result, Mapping) else None,
    }
    return build_signal_proposal(
        code="complete",
        task_id=task_id,
        parent_task_id=parent_task_id,
        mission_task_id=mission_task_id,
        source_kind="tool_call",
        emitter_role=emitter_role,
        source_detail="mark_task_complete",
        evidence_refs=evidence_refs,
        authority_scope=authority_scope,
        payload=payload,
    )


def validate_signal_proposal(
    proposal: Mapping[str, Any],
    *,
    mission_owner_role: str = "assistant",
    require_evidence_for: Optional[Iterable[str]] = None,
    allow_legacy_complete_without_evidence: bool = True,
    extra_rejection_reasons: Optional[Iterable[str]] = None,
    validated_by: str = "engine",
) -> Dict[str, Any]:
    signal = dict(proposal or {})
    reasons: list[str] = []

    code = str(signal.get("code") or "")
    task_id = str(signal.get("task_id") or "")
    authority_scope = str(signal.get("authority_scope") or "task")
    source = signal.get("source") if isinstance(signal.get("source"), Mapping) else {}
    source_kind = str(source.get("kind") or "")
    emitter_role = str(source.get("emitter_role") or "")
    evidence_refs = signal.get("evidence_refs") if isinstance(signal.get("evidence_refs"), list) else []

    if code not in SIGNAL_CODES:
        reasons.append(f"unknown_signal_code:{code or 'missing'}")
    if not task_id:
        reasons.append("missing_task_id")
    if authority_scope not in {"task", "mission"}:
        reasons.append(f"invalid_authority_scope:{authority_scope or 'missing'}")
    if source_kind not in SOURCE_KINDS:
        reasons.append(f"invalid_source_kind:{source_kind or 'missing'}")
    if emitter_role not in EMITTER_ROLES:
        reasons.append(f"invalid_emitter_role:{emitter_role or 'missing'}")

    required = {str(item) for item in (require_evidence_for or DEFAULT_REQUIRE_EVIDENCE_FOR) if str(item).strip()}
    if code in required and not evidence_refs:
        legacy_compatible = allow_legacy_complete_without_evidence and source_kind in LEGACY_COMPLETION_SOURCE_KINDS
        if not legacy_compatible:
            reasons.append(f"missing_required_evidence:{code}")

    if code == "complete" and authority_scope == "mission" and emitter_role == "worker" and mission_owner_role != "worker":
        reasons.append("worker_cannot_self_finalize_mission")

    for extra in extra_rejection_reasons or []:
        text = _clean_string(extra)
        if text:
            reasons.append(text)

    accepted = not reasons
    signal["status"] = "accepted" if accepted else "rejected"
    signal["validation"] = {
        "accepted": accepted,
        "reasons": reasons or ["signal_valid"],
        "validated_by": str(validated_by or "engine"),
        "validated_at": time.time(),
    }
    return signal


def is_accepted_signal(signal: Mapping[str, Any]) -> bool:
    validation = signal.get("validation")
    if isinstance(validation, Mapping) and "accepted" in validation:
        return bool(validation.get("accepted"))
    return str(signal.get("status") or "") == "accepted"
