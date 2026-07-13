from __future__ import annotations

from typing import Any, Mapping

from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec

PROJECTION_ID = "p6_6_task_job_subagent"
SCHEMA_VERSION = "bb.work_item.v1"
SOURCE_ROLE = "target_probe_output"
RECORD_KEYS = ("work_items",)

_VALID_TASK_KINDS = {
    "turn",
    "step",
    "task",
    "subagent",
    "distributed_task",
    "background",
    "workflow",
    "longrun_goal",
    "workflow_objective",
}
_VALID_STATUSES = {"queued", "running", "blocked", "waiting", "completed", "failed", "cancelled", "paused"}


def project_work_items(context: Mapping[str, Any]) -> dict[str, Any]:
    """Project P6.6 target-probe observations into finalized ``bb.work_item.v1`` records.

    The projection is intentionally pure: callers supply decoded source data, lane facts,
    role paths, role hashes, and pinned constants. This function performs no filesystem,
    environment, clock, dynamic-import, or global-path reads.
    """

    source_packet = _mapping(context.get("source"), "source")
    source = _mapping(source_packet.get("value", source_packet), "source.value")
    observations_value = source.get("work_item_observations", [])
    if not isinstance(observations_value, list):
        observations_value = []

    constants = _mapping(context.get("constants"), "constants")
    generated_at = _required_str(constants, "generated_at_utc")
    parent_work_item_id = _required_str(constants, "parent_work_item_id")
    parent_task_id = _required_str(constants, "parent_task_id")
    delegated_by = _actor_from(constants, "delegated_by")
    owner = _actor_from(constants, "owner")
    host_assignee = _actor_from(constants, "host_assignee")
    cancel_actors = tuple(_actors_from(constants, "cancellable_by"))

    records: list[dict[str, Any]] = []
    for observation_value in observations_value:
        if not isinstance(observation_value, Mapping):
            continue
        raw_record = _build_work_item(
            observation_value,
            generated_at=generated_at,
            parent_work_item_id=parent_work_item_id,
            parent_task_id=parent_task_id,
            delegated_by=delegated_by,
            owner=owner,
            host_assignee=host_assignee,
            cancellable_by=cancel_actors,
            context=context,
        )
        finalized = finalize_record(get_spec(SCHEMA_VERSION), raw_record)
        records.append({"record_key": finalized["work_item_id"], "value": finalized})

    summary = _summary(source, records)
    return {"records": records, "derived_facts": summary, "derived_values": summary}


def _build_work_item(
    observation: Mapping[str, Any],
    *,
    generated_at: str,
    parent_work_item_id: str,
    parent_task_id: str,
    delegated_by: Mapping[str, str],
    owner: Mapping[str, str],
    host_assignee: Mapping[str, str],
    cancellable_by: tuple[Mapping[str, str], ...],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    task_kind = str(observation.get("task_kind") or "background")
    status = str(observation.get("status") or "completed")
    work_item_id = str(observation.get("work_item_id") or observation.get("observation_id") or "wi-unknown")
    task_id = str(observation.get("task_id") or observation.get("observation_id") or work_item_id)
    subagent_id = observation.get("subagent_id")
    job_id = observation.get("job_id")
    input_ref = _input_ref_for_observation(observation, context)
    cancelled = status == "cancelled"
    normalized_task_kind = task_kind if task_kind in _VALID_TASK_KINDS else "task"
    normalized_status = status if status in _VALID_STATUSES else "completed"

    return {
        "work_item_id": work_item_id,
        "identity": {
            "task_id": task_id,
            "task_kind": normalized_task_kind,
            "subagent_id": str(subagent_id) if subagent_id is not None else None,
            "distributed_task_id": None,
            "correlation_id": str(job_id) if job_id is not None else None,
        },
        "delegation": {
            "parent_work_item_id": parent_work_item_id,
            "parent_task_id": parent_task_id,
            "delegated_by": dict(delegated_by),
            "delegation_ref": str(observation.get("observation_id")),
        },
        "state": {
            "status": normalized_status,
            "entered_at": generated_at,
            "reason": ";".join(str(event) for event in observation.get("observed_events", [])) or None,
            "checkpoint_ref": input_ref if normalized_status in {"completed", "cancelled"} else None,
        },
        "owner": dict(owner),
        "assignee": _actor("subagent", str(subagent_id)) if subagent_id else dict(host_assignee),
        "input_artifact_refs": [_artifact(input_ref, "transcript")],
        "output_artifact_refs": [_artifact(_role_ref(context, SOURCE_ROLE), "report")],
        "cancellation_policy": {
            "mode": "immediate" if cancelled else "cooperative",
            "cancellable_by": [dict(actor) for actor in cancellable_by],
            "propagate_to_children": normalized_task_kind == "subagent",
            "on_cancel": "keep_partial_outputs" if cancelled else "checkpoint_then_stop",
        },
        "resume_policy": {
            "mode": "checkpoint" if cancelled else "replay",
            "resume_from_ref": input_ref,
            "requires_approval": False,
            "wake_refs": [str(job_id)] if job_id is not None else [],
        },
        "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True},
    }


def _input_ref_for_observation(observation: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    source_capture_id = observation.get("source_capture_id")
    if source_capture_id == "joined-subagent-target-capture":
        return _role_ref(context, "joined_subagent_target_capture")
    if source_capture_id == "detached-subagent-target-capture":
        return _role_ref(context, "detached_subagent_target_capture")
    return _role_ref(context, SOURCE_ROLE)


def _summary(source: Mapping[str, Any], records: list[Mapping[str, Any]]) -> dict[str, Any]:
    joined = sum(1 for item in records if _record_value(item)["identity"]["task_kind"] == "subagent" and _record_value(item)["identity"].get("subagent_id") == "joined-subagent" and _record_value(item)["state"]["status"] == "completed")
    detached = sum(1 for item in records if _record_value(item)["identity"]["task_kind"] == "subagent" and _record_value(item)["identity"].get("subagent_id") == "detached-subagent" and _record_value(item)["state"]["status"] in {"completed", "cancelled"})
    background = sum(1 for item in records if _record_value(item)["identity"]["task_kind"] == "background" and _record_value(item)["state"]["status"] in {"completed", "cancelled", "running"})
    cancelled = sum(1 for item in records if _record_value(item)["state"]["status"] == "cancelled")
    observations = source.get("work_item_observations", [])
    cancel_observed = cancelled > 0 or any(
        isinstance(observation, Mapping) and "job_cancel_requested" in observation.get("observed_events", [])
        for observation in observations
        if isinstance(observations, list)
    )
    job_manager_only = bool(source.get("job_manager_only_evidence")) or joined == 0 or detached == 0
    return {
        "work_item_schema_valid": True,
        "joined_subagent_target_capture_observed": joined > 0,
        "detached_subagent_target_capture_observed": detached > 0,
        "background_job_observed": background > 0,
        "cancel_observed": cancel_observed,
        "job_manager_only_evidence": job_manager_only,
        "provider_authenticated_capture": bool(source.get("provider_authenticated_capture", source.get("provider_dispatch_observed", False))),
        "provider_dispatch_observed": bool(source.get("provider_dispatch_observed", False)),
        "provider_parity_claimed": False,
        "network_observed": bool(source.get("network_observed", False)),
        "fetch_event_count": int(source.get("fetch_event_count", len(source.get("fetch_events", []))) or 0),
    }


def _record_value(item: Mapping[str, Any]) -> Mapping[str, Any]:
    value = item.get("value")
    if not isinstance(value, Mapping):
        raise TypeError("record item value must be a mapping")
    return value


def _artifact(ref: str, artifact_kind: str, *, provider_visible: bool = True) -> dict[str, Any]:
    return {
        "ref": ref,
        "artifact_kind": artifact_kind,
        "visibility": {"model_visible": True, "provider_visible": provider_visible, "host_visible": True},
    }


def _role_ref(context: Mapping[str, Any], role: str) -> str:
    roles = _mapping(context.get("roles"), "roles")
    hashes = _mapping(context.get("role_hashes"), "role_hashes")
    path = _required_str(roles, role)
    digest = _required_str(hashes, role)
    return f"{path}#{digest}"


def _actor(kind: str, actor_id: str) -> dict[str, str]:
    return {"actor_kind": kind, "actor_id": actor_id}


def _actor_from(mapping: Mapping[str, Any], key: str) -> Mapping[str, str]:
    value = _mapping(mapping.get(key), key)
    actor_kind = _required_str(value, "actor_kind")
    actor_id = _required_str(value, "actor_id")
    return _actor(actor_kind, actor_id)


def _actors_from(mapping: Mapping[str, Any], key: str) -> list[Mapping[str, str]]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return [_actor_from({"actor": item}, "actor") for item in value]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
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
    "project_work_items",
]
