from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from agentic_coder_prototype.compilation.primitive_records import get_spec, validate_record
from breadboard.product.coordination.work_items import WorkItemEvent, WorkItemSnapshot, rebuild_work_item

PROJECTION_ID = "p6_6_task_job_subagent_v2"
SCHEMA_VERSION = "bb.work_item.v2"
SOURCE_ROLE = "target_probe_output"
RECORD_KEYS = ("work_items",)

_ACCEPTED_OBSERVED_STATUSES = {"completed", "cancelled"}


def project_work_item_v2_snapshots(context: Mapping[str, Any]) -> dict[str, Any]:
    """Project P6.6 observations into semantically validated Work Item v2 snapshots."""

    source_packet = _mapping(context.get("source"), "source")
    source = _mapping(source_packet.get("value", source_packet), "source.value")
    observations = source.get("work_item_observations", [])
    if not isinstance(observations, list):
        raise TypeError("work_item_observations must be a list")

    constants = _mapping(context.get("constants"), "constants")
    generated_at = _required_str(constants, "generated_at_utc")
    parent_work_item_id = _required_str(constants, "parent_work_item_id")
    host_assignee = _mapping(constants.get("host_assignee"), "host_assignee")
    host_worker_id = _required_str(host_assignee, "actor_id")
    cancellable_by = tuple(
        dict.fromkeys(
            _required_str(_mapping(actor, "cancellable_by actor"), "actor_id")
            for actor in _list(constants.get("cancellable_by"), "cancellable_by")
        )
    )

    rows: list[dict[str, Any]] = []
    for value in observations:
        observation = _mapping(value, "work_item observation")
        work_item_id = _required_str(observation, "work_item_id")
        status = _required_str(observation, "status")
        if status not in _ACCEPTED_OBSERVED_STATUSES:
            raise ValueError(f"unsupported P6.6 observed lifecycle status: {status}")
        worker_id = str(observation.get("subagent_id") or host_worker_id)
        record = _project_snapshot(
            observation,
            work_item_id=work_item_id,
            status=status,
            worker_id=worker_id,
            parent_work_item_id=parent_work_item_id,
            cancellable_by=cancellable_by,
            generated_at=generated_at,
        )
        rows.append({"record_key": work_item_id, "schema_version": SCHEMA_VERSION, "value": record})

    summary = _summary(source, observations)
    return {"records": rows, "derived_facts": summary, "derived_values": summary}


def _project_snapshot(
    observation: Mapping[str, Any],
    *,
    work_item_id: str,
    status: str,
    worker_id: str,
    parent_work_item_id: str,
    cancellable_by: tuple[str, ...],
    generated_at: str,
) -> dict[str, Any]:
    task_id = str(observation.get("task_id") or observation.get("observation_id") or work_item_id)
    reason = ";".join(str(event) for event in observation.get("observed_events", [])) or f"observed {status} lifecycle"
    cleanup = "keep_partial_outputs" if status == "cancelled" else "checkpoint_then_stop"
    created_payload = {
        "title": task_id,
        "parent_work_item_id": parent_work_item_id,
        "dependency_refs": [],
        "retry_policy": {"max_attempts": 1, "retry_on_any_failure": False, "retryable_reasons": []},
        "resume_policy": {"mode": "never", "requires_approval": False},
        "cancellation_policy": {
            "mode": "immediate" if status == "cancelled" else "cooperative",
            "cancellable_by": list(cancellable_by),
            "propagate_to_children": observation.get("task_kind") == "subagent",
            "cleanup": cleanup,
        },
        "budget": {"token_limit": None, "cost_limit_microusd": None, "wall_time_limit_ms": None},
    }
    events = [WorkItemEvent(work_item_id, 1, "work_item.created", generated_at, created_payload)]
    if status == "cancelled":
        events.append(
            WorkItemEvent(
                work_item_id,
                2,
                "work.canceled",
                generated_at,
                {"actor_id": cancellable_by[0], "reason": reason, "cleanup": cleanup, "child_work_item_ids": []},
            )
        )
    else:
        lease_id = f"{work_item_id}:lease"
        attempt_id = f"{work_item_id}:attempt"
        session_ref = str(observation.get("source_capture_id") or f"{work_item_id}:session")
        events.extend(
            (
                WorkItemEvent(work_item_id, 2, "lease.acquired", generated_at, {"lease_id": lease_id, "worker_id": worker_id, "expires_at": None}),
                WorkItemEvent(work_item_id, 3, "attempt.started", generated_at, {"attempt_id": attempt_id, "lease_id": lease_id, "session_ref": session_ref}),
                WorkItemEvent(work_item_id, 4, "work.completed", generated_at, {"attempt_id": attempt_id, "summary": reason}),
            )
        )
    return validate_record(get_spec(SCHEMA_VERSION), _snapshot_record(rebuild_work_item(events)))


def _snapshot_record(snapshot: WorkItemSnapshot) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "work_item_id": snapshot.work_item_id,
        "title": snapshot.title,
        "status": snapshot.status,
        "parent_work_item_id": snapshot.parent_work_item_id,
        "child_work_item_ids": list(snapshot.child_work_item_ids),
        "dependency_refs": list(snapshot.dependency_refs),
        "satisfied_dependency_refs": list(snapshot.satisfied_dependency_refs),
        "wake_refs": list(snapshot.wake_refs),
        "retry_policy": snapshot.retry_policy.as_dict(),
        "resume_policy": snapshot.resume_policy.as_dict(),
        "cancellation_policy": snapshot.cancellation_policy.as_dict(),
        "budget": {"limits": snapshot.budget.as_dict(), "usage": asdict(snapshot.budget_usage)},
        "active_lease": asdict(snapshot.active_lease) if snapshot.active_lease is not None else None,
        "used_lease_ids": list(snapshot.used_lease_ids),
        "attempts": [asdict(attempt) for attempt in snapshot.attempts],
        "placement_refs": [placement.placement_id for placement in snapshot.placements],
        "latest_checkpoint_ref": snapshot.latest_checkpoint_ref,
        "terminal_reason": snapshot.terminal_reason,
        "event_count": snapshot.event_count,
    }


def _summary(source: Mapping[str, Any], observations: list[Any]) -> dict[str, Any]:
    rows = [row for row in observations if isinstance(row, Mapping)]
    joined = any(row.get("task_kind") == "subagent" and row.get("subagent_id") == "joined-subagent" and row.get("status") == "completed" for row in rows)
    detached = any(row.get("task_kind") == "subagent" and row.get("subagent_id") == "detached-subagent" and row.get("status") in _ACCEPTED_OBSERVED_STATUSES for row in rows)
    background = any(row.get("task_kind") == "background" and row.get("status") in _ACCEPTED_OBSERVED_STATUSES for row in rows)
    cancelled = any(row.get("status") == "cancelled" for row in rows)
    return {
        "work_item_schema_valid": True,
        "joined_subagent_target_capture_observed": joined,
        "detached_subagent_target_capture_observed": detached,
        "background_job_observed": background,
        "cancel_observed": cancelled,
        "job_manager_only_evidence": bool(source.get("job_manager_only_evidence")) or not joined or not detached,
        "provider_authenticated_capture": bool(source.get("provider_authenticated_capture", source.get("provider_dispatch_observed", False))),
        "provider_dispatch_observed": bool(source.get("provider_dispatch_observed", False)),
        "provider_parity_claimed": False,
        "network_observed": bool(source.get("network_observed", False)),
        "fetch_event_count": int(source.get("fetch_event_count", len(source.get("fetch_events", []))) or 0),
    }


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list")
    return value


def _required_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


__all__ = [
    "PROJECTION_ID",
    "RECORD_KEYS",
    "SCHEMA_VERSION",
    "SOURCE_ROLE",
    "project_work_item_v2_snapshots",
]
