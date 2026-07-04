"""Flag-gated E4 primitive emission for CLI bridge sessions."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ...compilation import compile_capability_registry, compile_effective_config_graph, finalize_record, get_spec
from ...compilation.effective_operation_policy import compile_effective_operation_policy
from ...compilation.v2_loader import load_agent_config
from .models import SessionCreateRequest


def primitive_emission_enabled() -> bool:
    return os.environ.get("BREADBOARD_EMIT_PRIMITIVES", "").strip().lower() in {"1", "true", "yes", "on"}

_CONFIG_PLANE_DIALECT_FALLBACK_WARNED = False


def config_plane_dialects() -> set[str]:
    global _CONFIG_PLANE_DIALECT_FALLBACK_WARNED
    value = os.environ.get("BREADBOARD_CONFIG_PLANE_DIALECT")
    if value is None:
        value = os.environ.get("BREADBOARD_PRIMITIVES_DIALECT")
        if value is not None and not _CONFIG_PLANE_DIALECT_FALLBACK_WARNED:
            print(
                "BREADBOARD_PRIMITIVES_DIALECT is deprecated; use "
                "BREADBOARD_CONFIG_PLANE_DIALECT for config-plane coordination records.",
                file=sys.stderr,
            )
            _CONFIG_PLANE_DIALECT_FALLBACK_WARNED = True
    value = (value or "v2").strip().lower()
    if value == "both":
        return {"v2", "v3"}
    if value in {"v2", "v3"}:
        return {value}
    return {"v2"}



def default_runtime_record_root(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[3]
    override = os.environ.get("BREADBOARD_RUNTIME_RECORD_ROOT")
    return Path(override).resolve() if override else (root / "artifacts" / "runtime_records").resolve()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, Mapping):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return str(value)


def _config_path(repo_root: Path, config_path: str) -> Path:
    raw = Path(config_path)
    if raw.is_absolute():
        return raw
    candidate = (repo_root / raw).resolve()
    return candidate if candidate.exists() else raw.resolve()


def _tool_names(config: Mapping[str, Any]) -> list[str]:
    tools = config.get("tools") if isinstance(config, Mapping) else None
    names: set[str] = set()
    if isinstance(tools, Mapping):
        for key in ("enabled", "allowed", "names", "tool_names"):
            value = tools.get(key)
            if isinstance(value, list):
                names.update(str(item) for item in value if str(item))
        registry = tools.get("registry")
        if isinstance(registry, Mapping):
            registered = registry.get("tools") or registry.get("entries")
            if isinstance(registered, list):
                for entry in registered:
                    if isinstance(entry, Mapping):
                        name = entry.get("name") or entry.get("id") or entry.get("tool_id")
                        if name:
                            names.add(str(name))
                    elif entry:
                        names.add(str(entry))
        defs_dir = tools.get("defs_dir")
        if isinstance(defs_dir, str) and defs_dir:
            names.add(f"defs_dir:{defs_dir}")
    return sorted(names) or ["session_runtime"]


def _capability_declarations(config: Mapping[str, Any], *, generated_at: str) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = [
        {
            "capability_id": "runtime.session_service",
            "capability_type": "runtime",
            "name": "CLI bridge session service",
            "discovery_mode": "runtime_probe",
            "evidence_ref": "runtime://session/start",
            "visibility": "host_only",
            "model_visible": False,
            "exposure_mode": "host_only",
            "surface_refs": [],
            "metadata": {"generated_at": generated_at},
        }
    ]
    for name in _tool_names(config):
        tool_id = "tool." + "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in name).strip("._-")
        declarations.append(
            {
                "capability_id": tool_id or "tool.session_runtime",
                "capability_type": "tool",
                "name": name,
                "discovery_mode": "runtime_probe",
                "evidence_ref": "runtime://session/tool-list",
                "visibility": "model_visible",
                "model_visible": True,
                "surface_refs": ["surface.runtime_session_start"],
                "scopes": ["session_start"],
            }
        )
    return declarations


def _effective_tool_surface(surface_id: str, capability_registry: Mapping[str, Any]) -> dict[str, Any]:
    capabilities = capability_registry.get("capabilities")
    tool_ids = sorted(
        str(item["capability_id"])
        for item in capabilities or []
        if isinstance(item, Mapping) and item.get("capability_type") == "tool" and isinstance(item.get("capability_id"), str)
    )
    record = {
        "schema_version": "bb.effective_tool_surface.v1",
        "surface_id": surface_id,
        "tool_ids": tool_ids,
        "binding_ids": [f"binding.{tool_id}" for tool_id in tool_ids],
        "hidden_tool_ids": [],
        "projection_profile_id": "runtime_session_start",
        "surface_hash": None,
    }
    return finalize_record(get_spec("bb.effective_tool_surface.v1"), record)


def _work_item(
    session_id: str,
    *,
    status: str,
    entered_at: str,
    reason: str | None,
    operation_policy_ref: str | None,
) -> dict[str, Any]:
    actor = {"actor_kind": "service", "actor_id": "cli_bridge.session_service"}
    visibility = {"model_visible": False, "provider_visible": False, "host_visible": True}
    record = {
        "schema_version": "bb.work_item.v1",
        "work_item_id": f"{session_id}.{status}",
        "identity": {
            "task_id": session_id,
            "task_kind": "task",
            "subagent_id": None,
            "distributed_task_id": None,
            "correlation_id": session_id,
        },
        "delegation": {
            "parent_work_item_id": None,
            "parent_task_id": None,
            "delegated_by": actor,
            "delegation_ref": None,
        },
        "state": {"status": status, "entered_at": entered_at, "reason": reason, "checkpoint_ref": None},
        "owner": actor,
        "assignee": {"actor_kind": "agent", "actor_id": "session_runner"},
        "input_artifact_refs": [],
        "output_artifact_refs": [],
        "cancellation_policy": {
            "mode": "cooperative",
            "cancellable_by": [actor],
            "propagate_to_children": True,
            "on_cancel": "checkpoint_then_stop",
        },
        "resume_policy": {"mode": "manual", "resume_from_ref": None, "requires_approval": False, "wake_refs": []},
        "visibility": visibility,
        "provider_route_ref": None,
        "operation_policy_ref": operation_policy_ref,
        "budget": None,
        "isolation": None,
        "metadata": {"session_id": session_id, "runtime_source": "cli_bridge"},
    }
    return finalize_record(get_spec("bb.work_item.v1"), record)

def _coordination_slice(session_id: str, *, generated_at: str) -> dict[str, Any]:
    record = {
        "schema_version": "bb.coordination_slice.v2",
        "slice_id": f"{session_id}_coordination_slice",
        "slice_kind": "reference",
        "generated_at_utc": generated_at,
        "participants": [{"actor_kind": "service", "actor_id": "cli_bridge.session_service"}],
        "records": {},
        "metadata": {"session_id": session_id, "runtime_source": "cli_bridge"},
    }
    return finalize_record(get_spec("bb.coordination_slice.v2"), record)


def _coordination_pack(session_id: str, *, generated_at: str, slices: list[Mapping[str, Any]]) -> dict[str, Any]:
    record = {
        "schema_version": "bb.coordination_pack.v3",
        "pack_id": f"{session_id}_coordination_pack",
        "generated_at_utc": generated_at,
        "producer": {"actor_kind": "service", "actor_id": "cli_bridge.session_service"},
        "source_dialects": ["bb.coordination_slice.v2", "bb.coordination_pack.v3"],
        "records": {"slices": list(slices)},
        "migration": {
            "v1_acceptance": "read_only_accepted_evidence",
            "v2_acceptance": "read_write",
            "v3_write_mode": "preferred",
            "deprecation_notice": "v1/v2 coordination evidence remains readable; v3 is the grouped write envelope.",
        },
        "metadata": {"session_id": session_id, "runtime_source": "cli_bridge"},
    }
    return finalize_record(get_spec("bb.coordination_pack.v3"), record)


def emit_session_start_records(
    *,
    session_id: str,
    request: SessionCreateRequest,
    repo_root: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, str]:
    """Emit validating primitive records for a session start.

    The caller is responsible for checking ``primitive_emission_enabled``. This
    function raises on malformed records so tests and explicit capture runs fail
    at the boundary instead of writing partial evidence.
    """

    root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
    generated = generated_at or _utc_now()
    config_path = _config_path(root, request.config_path)
    config = load_agent_config(str(config_path))
    if not isinstance(config, Mapping):
        config = {}

    layers = [
        {
            "layer_id": "request.config",
            "source_kind": "project",
            "scope": "session",
            "precedence": 0,
            "source_ref": str(config_path),
            "values": _json_safe(config),
        },
        {
            "layer_id": "request.metadata",
            "source_kind": "runtime",
            "scope": "session",
            "precedence": 10,
            "source_ref": f"runtime://sessions/{session_id}/metadata",
            "values": {"metadata": _json_safe(request.metadata or {}), "workspace": request.workspace, "max_steps": request.max_steps},
        },
    ]
    if request.overrides:
        layers.append(
            {
                "layer_id": "request.overrides",
                "source_kind": "cli",
                "scope": "session",
                "precedence": 20,
                "source_ref": f"runtime://sessions/{session_id}/overrides",
                "values": _json_safe(request.overrides),
            }
        )

    graph = compile_effective_config_graph(graph_id=f"{session_id}_effective_config_graph", layers=layers)
    registry = compile_capability_registry(
        registry_id=f"{session_id}_capability_registry",
        run_id=session_id,
        environment_id="cli_bridge_runtime",
        declarations=_capability_declarations(config, generated_at=generated),
        generated_at=generated,
    )
    surface = _effective_tool_surface(f"{session_id}_effective_tool_surface", registry)
    operation_policy = compile_effective_operation_policy(
        config,
        session_id=session_id,
        config_path=str(config_path),
        generated_at_utc=generated,
    )

    out_dir = default_runtime_record_root(root) / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    policy_path = out_dir / "effective_operation_policy.json"
    policy_text = json.dumps(operation_policy, indent=2, sort_keys=True) + "\n"
    policy_path.write_text(policy_text, encoding="utf-8")
    operation_policy_ref = f"effective_operation_policy.json#sha256:{hashlib.sha256(policy_text.encode('utf-8')).hexdigest()}"
    work_created = _work_item(
        session_id,
        status="queued",
        entered_at=generated,
        reason="session accepted",
        operation_policy_ref=operation_policy_ref,
    )
    work_running = _work_item(
        session_id,
        status="running",
        entered_at=generated,
        reason="runner start requested",
        operation_policy_ref=operation_policy_ref,
    )
    records = {
        "effective_operation_policy": operation_policy,
        "effective_config_graph": graph,
        "capability_registry": registry,
        "effective_tool_surface": surface,
        "work_item_queued": work_created,
        "work_item_running": work_running,
    }
    dialects = config_plane_dialects()
    coordination_slices: list[Mapping[str, Any]] = []
    if "v2" in dialects or "v3" in dialects:
        coordination_slice = _coordination_slice(session_id, generated_at=generated)
        coordination_slices.append(coordination_slice)
        if "v2" in dialects:
            records["coordination_slice"] = coordination_slice
    if "v3" in dialects:
        records["coordination_pack"] = _coordination_pack(session_id, generated_at=generated, slices=coordination_slices)
    config_plane_path = out_dir / "records" / "config_plane.jsonl"
    config_plane_path.parent.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    with config_plane_path.open("a", encoding="utf-8") as stream:
        for name, payload in records.items():
            if name == "effective_operation_policy":
                paths[name] = str(policy_path)
            else:
                path = out_dir / f"{name}.json"
                path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                paths[name] = str(path)
            envelope = {
                "stream": "config_plane",
                "name": name,
                "schema_version": payload.get("schema_version") if isinstance(payload, Mapping) else None,
                "record": payload,
                "emitted_at_utc": generated,
            }
            stream.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return paths
