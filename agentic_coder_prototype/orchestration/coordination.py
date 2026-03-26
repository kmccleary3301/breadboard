from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

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

REVIEW_VERDICT_CODES = frozenset(
    {
        "validated",
        "pending_validation",
        "retry",
        "checkpoint",
        "escalate",
        "human_required",
        "noted",
    }
)

REVIEW_SUBJECT_KINDS = frozenset({"signal"})
REVIEWER_ROLES = frozenset({"supervisor", "host", "system"})
DIRECTIVE_CODES = frozenset({"continue", "retry", "checkpoint", "escalate", "terminate"})
DIRECTIVE_ISSUER_ROLES = frozenset({"supervisor", "host", "system"})

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
BLOCKED_RECOMMENDED_ACTIONS = frozenset({"retry", "checkpoint", "escalate", "human_required"})
REVIEW_ACTION_SIGNAL_CODES = frozenset({"blocked", "human_required", "no_progress", "retryable_failure"})


def _clean_string(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _clone_json_like(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clone_json_like(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_clone_json_like(item) for item in value]
    return value


def _normalize_allowed_host_actions(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    actions = [str(item).strip() for item in value if str(item).strip()]
    return actions


def _derive_intervention_snapshots(
    *,
    signals: Sequence[Mapping[str, Any]],
    review_verdicts: Sequence[Mapping[str, Any]],
    directives: Sequence[Mapping[str, Any]],
    resolved: bool,
) -> List[Dict[str, Any]]:
    signal_by_id = {
        str(item.get("signal_id") or ""): _clone_json_like(dict(item))
        for item in signals
        if str(item.get("signal_id") or "").strip()
    }
    directives_by_verdict: Dict[str, List[Dict[str, Any]]] = {}
    host_directives_by_verdict: Dict[str, List[Dict[str, Any]]] = {}
    for directive in directives:
        verdict_id = str(directive.get("based_on_verdict_id") or "").strip()
        if not verdict_id:
            continue
        directive_copy = _clone_json_like(dict(directive))
        directives_by_verdict.setdefault(verdict_id, []).append(directive_copy)
        if str(directive.get("issuer_role") or "") == "host":
            host_directives_by_verdict.setdefault(verdict_id, []).append(directive_copy)

    snapshots: List[Dict[str, Any]] = []
    for verdict in review_verdicts:
        if str(verdict.get("verdict_code") or "") != "human_required":
            continue
        verdict_id = str(verdict.get("verdict_id") or "").strip()
        if not verdict_id:
            continue
        subject = verdict.get("subject") if isinstance(verdict.get("subject"), Mapping) else {}
        signal_id = str(subject.get("signal_id") or "").strip()
        signal = signal_by_id.get(signal_id)
        payload = signal.get("payload") if isinstance(signal, Mapping) and isinstance(signal.get("payload"), Mapping) else {}
        metadata = verdict.get("metadata") if isinstance(verdict.get("metadata"), Mapping) else {}
        host_responses = host_directives_by_verdict.get(verdict_id) or []
        is_resolved = bool(host_responses)
        if is_resolved != resolved:
            continue
        snapshots.append(
            {
                "intervention_id": f"intervention_{verdict_id}",
                "status": "resolved" if is_resolved else "pending",
                "review_verdict_id": verdict_id,
                "signal_id": signal_id,
                "source_task_id": str(subject.get("source_task_id") or ""),
                "mission_task_id": _clean_string(subject.get("mission_task_id")),
                "required_input": _clean_string(payload.get("required_input")),
                "blocking_reason": _clean_string(
                    verdict.get("blocking_reason") or payload.get("blocking_reason")
                ),
                "allowed_host_actions": _normalize_allowed_host_actions(
                    metadata.get("allowed_host_actions") if isinstance(metadata, Mapping) else payload.get("allowed_host_actions")
                ),
                "review_verdict": _clone_json_like(dict(verdict)),
                "signal": _clone_json_like(dict(signal)) if isinstance(signal, Mapping) else None,
                "directives": [_clone_json_like(item) for item in directives_by_verdict.get(verdict_id, [])],
                "host_responses": [_clone_json_like(item) for item in host_responses],
            }
        )
    return snapshots


def build_coordination_inspection_snapshot(
    *,
    signals: Iterable[Mapping[str, Any]],
    review_verdicts: Iterable[Mapping[str, Any]],
    directives: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    normalized_signals = [_clone_json_like(dict(item)) for item in signals if isinstance(item, Mapping)]
    normalized_review_verdicts = [
        _clone_json_like(dict(item)) for item in review_verdicts if isinstance(item, Mapping)
    ]
    normalized_directives = [_clone_json_like(dict(item)) for item in directives if isinstance(item, Mapping)]

    latest_signal_by_code: Dict[str, Dict[str, Any]] = {}
    for signal in normalized_signals:
        code = str(signal.get("code") or "").strip()
        if code:
            latest_signal_by_code[code] = _clone_json_like(signal)

    return {
        "signals": normalized_signals,
        "review_verdicts": normalized_review_verdicts,
        "directives": normalized_directives,
        "latest_signal_by_code": latest_signal_by_code,
        "unresolved_interventions": _derive_intervention_snapshots(
            signals=normalized_signals,
            review_verdicts=normalized_review_verdicts,
            directives=normalized_directives,
            resolved=False,
        ),
        "resolved_interventions": _derive_intervention_snapshots(
            signals=normalized_signals,
            review_verdicts=normalized_review_verdicts,
            directives=normalized_directives,
            resolved=True,
        ),
    }


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


def build_blocked_signal_proposal(
    *,
    task_id: str,
    blocking_reason: str,
    recommended_next_action: str,
    parent_task_id: Optional[str] = None,
    mission_task_id: Optional[str] = None,
    emitter_role: str = "worker",
    authority_scope: str = "task",
    support_claim_ref: Optional[str] = None,
    source_kind: str = "worker",
    evidence_refs: Optional[Iterable[str]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    action = str(recommended_next_action or "").strip().lower()
    blocked_payload = dict(payload or {})
    blocked_payload["blocking_reason"] = str(blocking_reason or "").strip()
    blocked_payload["recommended_next_action"] = action
    if support_claim_ref:
        blocked_payload["support_claim_ref"] = str(support_claim_ref)
    return build_signal_proposal(
        code="blocked",
        task_id=task_id,
        parent_task_id=parent_task_id,
        mission_task_id=mission_task_id,
        source_kind=source_kind,
        emitter_role=emitter_role,
        authority_scope=authority_scope,
        evidence_refs=evidence_refs,
        payload=blocked_payload,
    )


def build_no_progress_signal_proposal(
    *,
    task_id: str,
    stall_reason: str,
    observed_episode_index: int,
    no_progress_streak: int,
    failure_signature_streak: int,
    last_activity_marker: Optional[str] = None,
    current_item_id: Optional[str] = None,
    retry_streak: Optional[int] = None,
    parent_task_id: Optional[str] = None,
    mission_task_id: Optional[str] = None,
    authority_scope: str = "task",
    source_kind: str = "runtime",
    emitter_role: str = "runtime",
    evidence_refs: Optional[Iterable[str]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    signal_payload = dict(payload or {})
    signal_payload.update(
        {
            "stall_reason": str(stall_reason or "").strip(),
            "observed_episode_index": int(observed_episode_index),
            "no_progress_streak": int(no_progress_streak),
            "failure_signature_streak": int(failure_signature_streak),
        }
    )
    if last_activity_marker:
        signal_payload["last_activity_marker"] = str(last_activity_marker)
    if current_item_id:
        signal_payload["current_item_id"] = str(current_item_id)
    if retry_streak is not None:
        signal_payload["retry_streak"] = int(retry_streak)
    return build_signal_proposal(
        code="no_progress",
        task_id=task_id,
        parent_task_id=parent_task_id,
        mission_task_id=mission_task_id,
        source_kind=source_kind,
        emitter_role=emitter_role,
        authority_scope=authority_scope,
        evidence_refs=evidence_refs,
        payload=signal_payload,
    )


def build_retryable_failure_signal_proposal(
    *,
    task_id: str,
    failure_summary: str,
    retry_basis: str,
    attempt_count: int,
    parent_task_id: Optional[str] = None,
    mission_task_id: Optional[str] = None,
    authority_scope: str = "task",
    source_kind: str = "runtime",
    emitter_role: str = "runtime",
    current_item_id: Optional[str] = None,
    backoff_seconds: Optional[float] = None,
    error_type: Optional[str] = None,
    evidence_refs: Optional[Iterable[str]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    signal_payload = dict(payload or {})
    signal_payload.update(
        {
            "failure_summary": str(failure_summary or "").strip(),
            "retry_basis": str(retry_basis or "").strip(),
            "attempt_count": int(attempt_count),
        }
    )
    if current_item_id:
        signal_payload["current_item_id"] = str(current_item_id)
    if backoff_seconds is not None:
        signal_payload["backoff_seconds"] = float(backoff_seconds)
    if error_type:
        signal_payload["error_type"] = str(error_type)
    return build_signal_proposal(
        code="retryable_failure",
        task_id=task_id,
        parent_task_id=parent_task_id,
        mission_task_id=mission_task_id,
        source_kind=source_kind,
        emitter_role=emitter_role,
        authority_scope=authority_scope,
        evidence_refs=evidence_refs,
        payload=signal_payload,
    )


def build_human_required_signal_proposal(
    *,
    task_id: str,
    required_input: str,
    blocking_reason: str,
    parent_task_id: Optional[str] = None,
    mission_task_id: Optional[str] = None,
    authority_scope: str = "task",
    source_kind: str = "runtime",
    emitter_role: str = "runtime",
    current_item_id: Optional[str] = None,
    evidence_refs: Optional[Iterable[str]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    signal_payload = dict(payload or {})
    signal_payload.update(
        {
            "required_input": str(required_input or "").strip(),
            "blocking_reason": str(blocking_reason or "").strip(),
        }
    )
    if current_item_id:
        signal_payload["current_item_id"] = str(current_item_id)
    return build_signal_proposal(
        code="human_required",
        task_id=task_id,
        parent_task_id=parent_task_id,
        mission_task_id=mission_task_id,
        source_kind=source_kind,
        emitter_role=emitter_role,
        authority_scope=authority_scope,
        evidence_refs=evidence_refs,
        payload=signal_payload,
    )


def build_review_verdict(
    *,
    reviewer_task_id: str,
    reviewer_role: str,
    subject_signal: Mapping[str, Any],
    verdict_code: str,
    subject_event_id: Optional[int] = None,
    subscription_id: Optional[str] = None,
    trigger_signal_id: Optional[str] = None,
    trigger_event_id: Optional[int] = None,
    trigger_code: Optional[str] = None,
    mission_completed: bool = False,
    required_deliverable_refs: Optional[Iterable[str]] = None,
    deliverable_refs: Optional[Iterable[str]] = None,
    missing_deliverable_refs: Optional[Iterable[str]] = None,
    blocking_reason: Optional[str] = None,
    recommended_next_action: Optional[str] = None,
    support_claim_ref: Optional[str] = None,
    signal_evidence_refs: Optional[Iterable[str]] = None,
    verdict_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    signal = dict(subject_signal or {})
    return {
        "schema_version": "bb.review_verdict.v1",
        "verdict_id": str(verdict_id or f"review_{uuid.uuid4().hex}"),
        "reviewer_task_id": str(reviewer_task_id or ""),
        "reviewer_role": str(reviewer_role or ""),
        "subject": {
            "kind": "signal",
            "signal_id": str(signal.get("signal_id") or ""),
            "signal_event_id": subject_event_id,
            "signal_code": str(signal.get("code") or ""),
            "source_task_id": str(signal.get("task_id") or ""),
            "mission_task_id": _clean_string(signal.get("mission_task_id")),
            "subscription_id": _clean_string(subscription_id),
            "trigger_signal_id": _clean_string(trigger_signal_id or signal.get("signal_id")),
            "trigger_event_id": trigger_event_id if trigger_event_id is not None else subject_event_id,
            "trigger_code": _clean_string(trigger_code or signal.get("code")),
        },
        "verdict_code": str(verdict_code or ""),
        "mission_completed": bool(mission_completed),
        "required_deliverable_refs": [
            str(item) for item in (required_deliverable_refs or []) if str(item).strip()
        ],
        "deliverable_refs": [str(item) for item in (deliverable_refs or []) if str(item).strip()],
        "missing_deliverable_refs": [
            str(item) for item in (missing_deliverable_refs or []) if str(item).strip()
        ],
        "blocking_reason": _clean_string(blocking_reason),
        "recommended_next_action": _clean_string(recommended_next_action),
        "support_claim_ref": _clean_string(support_claim_ref),
        "signal_evidence_refs": [str(item) for item in (signal_evidence_refs or []) if str(item).strip()],
        "metadata": dict(metadata or {}),
    }


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
    payload = signal.get("payload") if isinstance(signal.get("payload"), Mapping) else {}

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
    if code == "blocked":
        blocking_reason = _clean_string(payload.get("blocking_reason"))
        recommended_next_action = str(payload.get("recommended_next_action") or "").strip().lower()
        if not blocking_reason:
            reasons.append("missing_blocking_reason")
        if recommended_next_action not in BLOCKED_RECOMMENDED_ACTIONS:
            reasons.append(
                f"invalid_recommended_next_action:{recommended_next_action or 'missing'}"
            )

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


def validate_review_verdict(
    verdict: Mapping[str, Any],
    *,
    validated_by: str = "engine",
) -> Dict[str, Any]:
    record = dict(verdict or {})
    reasons: list[str] = []

    if str(record.get("schema_version") or "") != "bb.review_verdict.v1":
        reasons.append("invalid_schema_version")
    if not _clean_string(record.get("verdict_id")):
        reasons.append("missing_verdict_id")
    if not _clean_string(record.get("reviewer_task_id")):
        reasons.append("missing_reviewer_task_id")

    reviewer_role = str(record.get("reviewer_role") or "")
    if reviewer_role not in REVIEWER_ROLES:
        reasons.append(f"invalid_reviewer_role:{reviewer_role or 'missing'}")

    subject = record.get("subject") if isinstance(record.get("subject"), Mapping) else {}
    if str(subject.get("kind") or "") not in REVIEW_SUBJECT_KINDS:
        reasons.append(f"invalid_subject_kind:{str(subject.get('kind') or '') or 'missing'}")
    if not _clean_string(subject.get("signal_id")):
        reasons.append("missing_subject_signal_id")
    if str(subject.get("signal_code") or "") not in SIGNAL_CODES:
        reasons.append(f"invalid_subject_signal_code:{str(subject.get('signal_code') or '') or 'missing'}")
    if not _clean_string(subject.get("source_task_id")):
        reasons.append("missing_subject_source_task_id")

    verdict_code = str(record.get("verdict_code") or "")
    if verdict_code not in REVIEW_VERDICT_CODES:
        reasons.append(f"invalid_verdict_code:{verdict_code or 'missing'}")

    required_deliverable_refs = [
        str(item) for item in (record.get("required_deliverable_refs") or []) if str(item).strip()
    ]
    deliverable_refs = [str(item) for item in (record.get("deliverable_refs") or []) if str(item).strip()]
    missing_deliverable_refs = [
        str(item) for item in (record.get("missing_deliverable_refs") or []) if str(item).strip()
    ]
    mission_completed = bool(record.get("mission_completed"))

    if verdict_code == "validated":
        if missing_deliverable_refs:
            reasons.append("validated_verdict_cannot_have_missing_deliverable_refs")
        if required_deliverable_refs and not deliverable_refs:
            reasons.append("validated_verdict_missing_deliverable_refs")
        if not mission_completed:
            reasons.append("validated_verdict_requires_mission_completed")
    elif mission_completed:
        reasons.append("only_validated_verdict_may_complete_mission")

    if verdict_code == "pending_validation":
        if not missing_deliverable_refs:
            reasons.append("pending_validation_requires_missing_deliverable_refs")

    blocking_reason = _clean_string(record.get("blocking_reason"))
    recommended_next_action = _clean_string(record.get("recommended_next_action"))
    if verdict_code in {"retry", "checkpoint", "escalate", "human_required"}:
        signal_code = str(subject.get("signal_code") or "")
        if signal_code not in REVIEW_ACTION_SIGNAL_CODES:
            reasons.append("review_action_verdict_requires_actionable_signal")
        if signal_code == "blocked" and verdict_code != "human_required" and not blocking_reason:
            reasons.append("blocked_review_verdict_requires_blocking_reason")
        if signal_code == "human_required" and verdict_code != "human_required":
            reasons.append("human_required_signal_requires_human_required_verdict")
        if (
            verdict_code in {"retry", "checkpoint", "escalate"}
            and signal_code in {"blocked", "no_progress", "retryable_failure"}
            and recommended_next_action not in BLOCKED_RECOMMENDED_ACTIONS
        ):
            reasons.append("review_action_verdict_requires_recommended_next_action")

    record["validation"] = {
        "accepted": not reasons,
        "reasons": reasons or ["review_verdict_valid"],
        "validated_by": str(validated_by or "engine"),
        "validated_at": time.time(),
    }
    return record


def build_directive(
    *,
    directive_code: str,
    issuer_task_id: str,
    issuer_role: str,
    target_task_id: str,
    based_on_verdict: Mapping[str, Any],
    target_job_id: Optional[str] = None,
    directive_id: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
    evidence_refs: Optional[Iterable[str]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    verdict = dict(based_on_verdict or {})
    subject = verdict.get("subject") if isinstance(verdict.get("subject"), Mapping) else {}
    return {
        "schema_version": "bb.directive.v1",
        "directive_id": str(directive_id or f"directive_{uuid.uuid4().hex}"),
        "directive_code": str(directive_code or ""),
        "issuer_task_id": str(issuer_task_id or ""),
        "issuer_role": str(issuer_role or ""),
        "target_task_id": str(target_task_id or ""),
        "target_job_id": _clean_string(target_job_id),
        "based_on_verdict_id": str(verdict.get("verdict_id") or ""),
        "based_on_signal_id": str(subject.get("signal_id") or ""),
        "payload": dict(payload or {}),
        "evidence_refs": [str(item) for item in (evidence_refs or []) if str(item).strip()],
        "metadata": dict(metadata or {}),
    }


def validate_directive(
    directive: Mapping[str, Any],
    *,
    validated_by: str = "engine",
) -> Dict[str, Any]:
    record = dict(directive or {})
    reasons: list[str] = []

    if str(record.get("schema_version") or "") != "bb.directive.v1":
        reasons.append("invalid_schema_version")
    if not _clean_string(record.get("directive_id")):
        reasons.append("missing_directive_id")
    directive_code = str(record.get("directive_code") or "")
    if directive_code not in DIRECTIVE_CODES:
        reasons.append(f"invalid_directive_code:{directive_code or 'missing'}")
    if not _clean_string(record.get("issuer_task_id")):
        reasons.append("missing_issuer_task_id")
    issuer_role = str(record.get("issuer_role") or "")
    if issuer_role not in DIRECTIVE_ISSUER_ROLES:
        reasons.append(f"invalid_issuer_role:{issuer_role or 'missing'}")
    if not _clean_string(record.get("target_task_id")):
        reasons.append("missing_target_task_id")
    if not _clean_string(record.get("based_on_verdict_id")):
        reasons.append("missing_based_on_verdict_id")
    if not _clean_string(record.get("based_on_signal_id")):
        reasons.append("missing_based_on_signal_id")

    payload = record.get("payload") if isinstance(record.get("payload"), Mapping) else {}
    wake_target = bool(payload.get("wake_target"))
    if directive_code in {"continue", "retry", "checkpoint"} and not wake_target:
        reasons.append("directive_requires_wake_target")

    record["validation"] = {
        "accepted": not reasons,
        "reasons": reasons or ["directive_valid"],
        "validated_by": str(validated_by or "engine"),
        "validated_at": time.time(),
    }
    return record


def is_accepted_signal(signal: Mapping[str, Any]) -> bool:
    validation = signal.get("validation")
    if isinstance(validation, Mapping) and "accepted" in validation:
        return bool(validation.get("accepted"))
    return str(signal.get("status") or "") == "accepted"


def is_accepted_review_verdict(verdict: Mapping[str, Any]) -> bool:
    validation = verdict.get("validation")
    if isinstance(validation, Mapping) and "accepted" in validation:
        return bool(validation.get("accepted"))
    return False


def is_accepted_directive(directive: Mapping[str, Any]) -> bool:
    validation = directive.get("validation")
    if isinstance(validation, Mapping) and "accepted" in validation:
        return bool(validation.get("accepted"))
    return False
