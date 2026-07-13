from __future__ import annotations

from typing import Any, Mapping


def _required_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _lane_value(context: Mapping[str, Any], key: str) -> str:
    lane = _required_mapping(context.get("lane"), "lane")
    return _required_string(lane.get(key), f"lane.{key}")


def _constants(context: Mapping[str, Any]) -> Mapping[str, Any]:
    return _required_mapping(context.get("constants"), "constants")


def _inputs(context: Mapping[str, Any]) -> Mapping[str, Any]:
    return _required_mapping(context.get("inputs"), "inputs")


def _input_hash(context: Mapping[str, Any], path: str) -> str:
    item = _required_mapping(_inputs(context).get(path), f"inputs[{path!r}]")
    digest = item.get("sha256")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError(f"inputs[{path!r}].sha256 must be a prefixed sha256 digest")
    return digest


def _source_hash(context: Mapping[str, Any], path: str) -> str:
    source_hashes = _required_mapping(context.get("source_hashes"), "source_hashes")
    digest = source_hashes.get(path)
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError(f"source_hashes[{path!r}] must be a prefixed sha256 digest")
    return digest


def _projection_event(context: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = _lane_value(context, "lane_id")
    config_id = _lane_value(context, "config_id")
    constants = _constants(context)
    generated_at = _required_string(context.get("generated_at_utc") or constants.get("generated_at_utc"), "generated_at_utc")
    tool = _required_string(spec.get("tool"), "event.tool")
    lifecycle_state = _required_string(spec.get("lifecycle_state"), "event.lifecycle_state")
    surface_kind = _required_string(spec.get("surface_kind"), "event.surface_kind")
    stdout_path = _required_string(spec.get("stdout_path"), "event.stdout_path")
    report_path = _required_string(spec.get("report_path"), "event.report_path")
    primary_source = _required_string(constants.get("primary_source_ref"), "constants.primary_source_ref")
    source_refs = list(constants.get("source_refs", []))
    if not all(isinstance(item, str) and item for item in source_refs):
        raise ValueError("constants.source_refs must be a list of non-empty strings")
    return {
        "schema_version": "bb.projection_event.v1",
        "projection_event_id": f"{lane_id}_{tool}_{lifecycle_state}",
        "source": {
            "source_kernel_event_ref": f"{config_id}:{tool}:{lifecycle_state}",
            "source_refs": [primary_source, *source_refs, report_path],
            "source_hash": _source_hash(context, primary_source),
        },
        "projection_surface": {
            "surface_id": f"omp_gallery_{tool}_{lifecycle_state}_plain_stdout",
            "surface_kind": surface_kind,
            "audience": "host",
            "path": "stdout",
        },
        "projection_payload_ref": {
            "ref": stdout_path,
            "media_type": constants.get("payload_media_type", "text/plain; charset=utf-8"),
            "hash": _input_hash(context, stdout_path),
            "redaction_state": "none",
        },
        "status_frames": [
            {
                "frame_id": f"{lane_id}_{tool}_{lifecycle_state}_frame_0",
                "seq": 0,
                "status": "projected",
                "message_ref": stdout_path,
                "emitted_at": generated_at,
                "visible_to_model": False,
                "visible_to_host": True,
            }
        ],
        "visibility": {"model_visible": False, "host_visible": True, "provider_visible": False},
        "kernel_truth": False,
    }


def project_tui_projection_events(context: Mapping[str, Any]) -> dict[str, Any]:
    """Project declared L6 gallery stdout captures into bb.projection_event.v1 records."""
    events = _constants(context).get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("constants.events must be a non-empty list")
    records = [
        {"record_key": _required_string(event.get("record_key"), "event.record_key"), "value": _projection_event(context, event)}
        for event in events
        if isinstance(event, Mapping)
    ]
    if len(records) != len(events):
        raise ValueError("constants.events must contain only mappings")
    return {
        "records": records,
        "derived_facts": {
            "event_count": len(records),
            "projection_event_ids": [record["value"]["projection_event_id"] for record in records],
            "kernel_truth": False,
            "model_visible": False,
            "host_visible": True,
        },
        "derived_values": {
            "event_count": len(records),
            "projection_event_ids": [record["value"]["projection_event_id"] for record in records],
            "kernel_truth": False,
            "model_visible": False,
            "host_visible": True,
        },
    }


PROJECTIONS = {
    "p6_l6_tui_projection": project_tui_projection_events,
}
