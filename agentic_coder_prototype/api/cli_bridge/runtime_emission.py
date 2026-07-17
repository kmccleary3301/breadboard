"""Flag-gated E4 primitive emission for CLI bridge sessions."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from breadboard.product.runtime.artifacts import ArtifactStore
from breadboard.product.coordination.work_items import (
    Budget,
    CancellationPolicy,
    ResumePolicy,
    RetryPolicy,
    WorkItemEvent,
    WorkItemSnapshot,
    rebuild_work_item,
)

from ...auth.enforcer import apply_dotted_overrides
from ...compilation import compile_capability_registry, compile_effective_config_graph, finalize_record, get_spec
from ...compilation.effective_operation_policy import compile_effective_operation_policy
from ...compilation.v2_loader import load_agent_config
from .models import SessionCreateRequest
DEFAULT_INTERACTIVE_SESSION_TITLE = "interactive session awaiting input"

def primitive_emission_enabled() -> bool:
    return os.environ.get("BREADBOARD_EMIT_PRIMITIVES", "").strip().lower() in {"1", "true", "yes", "on"}


def default_runtime_record_root(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[3]
    override = os.environ.get("BREADBOARD_RUNTIME_RECORD_ROOT")
    return Path(override).resolve() if override else (root / "artifacts" / "runtime_records").resolve()

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _sanitize_persisted_runtime_config(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize_persisted_runtime_config(item) for key, item in value.items()
                if str(key) != "provider_auth_runtime" and not str(key).startswith("provider_auth_runtime.")}
    if isinstance(value, (list, tuple)):
        return [_sanitize_persisted_runtime_config(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value

def _config_path(repo_root: Path, config_path: str) -> Path:
    raw = Path(config_path)
    if raw.is_absolute():
        return raw
    candidate = (repo_root / raw).resolve()
    return candidate if candidate.exists() else raw.resolve()

def compile_runtime_effective_config_graph(session_id: str, runtime_config: Mapping[str, Any], source_ref: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
    return compile_effective_config_graph(graph_id=f"{session_id}_effective_config_graph", layers=[{
        "layer_id": "runtime:effective", "source_kind": "runtime", "scope": "session", "precedence": 0,
        "source_ref": str(_config_path(root, source_ref)), "values": _sanitize_persisted_runtime_config(runtime_config),
    }])


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
            for key in ("include", "tools", "entries"):
                for entry in registry.get(key) or []:
                    if isinstance(entry, Mapping):
                        entry = entry.get("name") or entry.get("id") or entry.get("tool_id")
                    if entry:
                        names.add(str(entry))
    return sorted(names) or ["session_runtime"]

def _capability_declarations(config: Mapping[str, Any], *, generated_at: str) -> list[dict[str, Any]]:
    declarations = [{
        "capability_id": "runtime.session_service", "capability_type": "runtime", "name": "CLI bridge session service",
        "discovery_mode": "runtime_probe", "evidence_ref": "runtime://session/start", "visibility": "host_only",
        "model_visible": False, "exposure_mode": "host_only", "surface_refs": [], "metadata": {"generated_at": generated_at},
    }]
    for name in _tool_names(config):
        tool_id = "tool." + "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in name).strip("._-")
        declarations.append({"capability_id": tool_id or "tool.session_runtime", "capability_type": "tool", "name": name,
                             "discovery_mode": "runtime_probe", "evidence_ref": "runtime://session/tool-list",
                             "visibility": "model_visible", "model_visible": True,
                             "surface_refs": ["surface.runtime_session_start"], "scopes": ["session_start"]})
    return declarations

def _effective_tool_surface(surface_id: str, capability_registry: Mapping[str, Any]) -> dict[str, Any]:
    capabilities = capability_registry.get("capabilities")
    tool_ids = sorted(str(item["capability_id"]) for item in capabilities or []
                      if isinstance(item, Mapping) and item.get("capability_type") == "tool"
                      and isinstance(item.get("capability_id"), str))
    return finalize_record(get_spec("bb.effective_tool_surface.v1"), {
        "schema_version": "bb.effective_tool_surface.v1", "surface_id": surface_id,
        "tool_ids": tool_ids, "binding_ids": [f"binding.{tool_id}" for tool_id in tool_ids],
        "hidden_tool_ids": [], "projection_profile_id": "runtime_session_start", "surface_hash": None})

def _work_item_records(session_id: str, *, title: str, generated_at: str) -> dict[str, dict[str, Any]]:
    events = (
        WorkItemEvent(
            session_id,
            1,
            "work_item.created",
            generated_at,
            {
                "title": title,
                "parent_work_item_id": None,
                "dependency_refs": [],
                "retry_policy": RetryPolicy().as_dict(),
                "resume_policy": ResumePolicy().as_dict(),
                "cancellation_policy": CancellationPolicy(
                    cancellable_by=("cli_bridge.session_service",),
                ).as_dict(),
                "budget": Budget().as_dict(),
            },
        ),
        WorkItemEvent(
            session_id,
            2,
            "lease.acquired",
            generated_at,
            {
                "lease_id": f"{session_id}.lease",
                "worker_id": "session_runner",
                "expires_at": None,
            },
        ),
        WorkItemEvent(
            session_id,
            3,
            "attempt.started",
            generated_at,
            {
                "attempt_id": f"{session_id}.attempt",
                "lease_id": f"{session_id}.lease",
                "session_ref": session_id,
            },
        ),
    )
    snapshot = _work_item_snapshot_record(rebuild_work_item(events))
    return {
        "work_item_created": events[0].as_dict(),
        "work_item_lease_acquired": events[1].as_dict(),
        "work_item_attempt_started": events[2].as_dict(),
        "work_item_snapshot": finalize_record(get_spec("bb.work_item.v2"), snapshot),
    }


def _work_item_snapshot_record(snapshot: WorkItemSnapshot) -> dict[str, Any]:
    record = json.loads(json.dumps(asdict(snapshot)))
    placements = record.pop("placements")
    record["placement_refs"] = [placement["placement_id"] for placement in placements]
    record["budget"] = {
        "limits": record.pop("budget"),
        "usage": record.pop("budget_usage"),
    }
    return record

def emit_session_start_records(
    *, session_id: str, request: SessionCreateRequest, title: str | None = None, repo_root: Path | None = None, generated_at: str | None = None, output_root: Path | None = None, effective_runtime_config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Emit validating session-start records, failing before partial evidence is written."""

    root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
    generated = generated_at or _utc_now()
    config_path = _config_path(root, request.config_path)
    if effective_runtime_config is None:
        base_config = load_agent_config(str(config_path))
        base_config = dict(base_config) if isinstance(base_config, Mapping) else {}
        config = _sanitize_persisted_runtime_config(apply_dotted_overrides(base_config, dict(request.overrides or {})))
    else: config = _sanitize_persisted_runtime_config(effective_runtime_config)
    graph = compile_runtime_effective_config_graph(session_id, config, str(config_path), repo_root=root)
    registry = compile_capability_registry(
        registry_id=f"{session_id}_capability_registry", run_id=session_id,
        environment_id="cli_bridge_runtime",
        declarations=_capability_declarations(config, generated_at=generated), generated_at=generated,
    )
    surface = _effective_tool_surface(f"{session_id}_effective_tool_surface", registry)
    operation_policy = compile_effective_operation_policy(
        config, session_id=session_id, config_path=str(config_path), generated_at_utc=generated,
    )

    out_dir = (output_root or default_runtime_record_root(root)) / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_store = ArtifactStore(out_dir / "objects")
    policy_path = out_dir / "effective_operation_policy.json"
    policy_text = json.dumps(operation_policy, indent=2, sort_keys=True) + "\n"
    policy_ref = artifact_store.put(policy_text.encode("utf-8"), media_type="application/json")
    artifact_store.materialize(policy_ref, policy_path)
    operation_policy_ref = f"effective_operation_policy.json#sha256:{hashlib.sha256(policy_text.encode('utf-8')).hexdigest()}"
    records = {
        "effective_operation_policy": operation_policy,
        "effective_config_graph": graph,
        "capability_registry": registry,
        "effective_tool_surface": surface,
        **_work_item_records(session_id, title=title if title is not None else request.task if request.task.strip() else DEFAULT_INTERACTIVE_SESSION_TITLE, generated_at=generated),
    }
    config_plane_path = out_dir / "records" / "config_plane.jsonl"
    config_plane_path.parent.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    envelopes: list[str] = []
    for name, payload in records.items():
        if name == "effective_operation_policy":
            paths[name] = str(policy_path)
        else:
            path = out_dir / f"{name}.json"
            content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            artifact_store.materialize(artifact_store.put(content, media_type="application/json"), path)
            paths[name] = str(path)
        envelope = {
            "stream": "config_plane", "name": name,
            "schema_version": payload.get("schema_version") if isinstance(payload, Mapping) else None,
            "record": payload, "emitted_at_utc": generated,
        }
        if name.startswith("work_item_"):
            envelope["correlation"] = {
                "work_item_id": session_id,
                "session_id": session_id,
                "run_id": session_id,
            }
            envelope["operation_policy_ref"] = operation_policy_ref
        envelopes.append(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    prior = config_plane_path.read_bytes() if config_plane_path.exists() else b""
    stream_content = prior + "".join(envelopes).encode("utf-8")
    artifact_store.materialize(
        artifact_store.put(stream_content, media_type="application/x-ndjson"), config_plane_path,
    )
    return paths
