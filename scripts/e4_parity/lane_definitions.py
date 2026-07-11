from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator, RefResolver
from scripts.e4_parity.validators.registries import RegistryValidationError, assert_registered
from scripts.e4_parity.path_refs import (
    ReferenceResolutionError,
    resolve_declared_reference,
)
from scripts.e4_parity.compile_lane_lock import (
    SOURCE_FREEZE_ARCHIVE_REF,
    SOURCE_FREEZE_EXTRACTION_REF,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LANE_DEF_DIR = ROOT / "config" / "e4_lanes"
SCHEMA_VERSION_V1 = "bb.e4.lane_def.v1"
SCHEMA_VERSION_V2 = "bb.e4.lane_def.v2"
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2)
MANIFEST_SCHEMA_VERSION = "bb.e4.lane_manifest.v1"
SCHEMA_DIR_REF = "contracts/kernel/schemas"
COMMON_SCHEMA_REF = f"{SCHEMA_DIR_REF}/bb.kernel.common.v1.schema.json"
E4_COMMON_SCHEMA_REF = f"{SCHEMA_DIR_REF}/bb.e4.common.v1.schema.json"
_PILOT_LEGACY_CAPTURE_INPUTS = (
    "docs/conformance/support_claims/oh_my_pi_p6_6_task_job_subagent_v1_c4_support_claim.json",
    "artifacts/conformance/node_gate/ct_p6_oh_my_pi_p66_task_job_subagent_c4_chain.json",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/target_probe_output.json",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/target_setup_and_capture_report.json",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/joined_subagent_target_capture.json",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/detached_subagent_target_capture.json",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/oh_my_pi_p6_6_task_job_subagent_probe.mjs",
    "agent_configs/misc/oh_my_pi_p6_6_task_job_subagent_v1.yaml",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/raw",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/joined_sessions",
    "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/detached_sessions",
    "docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_freeze_provenance.json",
    "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest",
    "config/e4_target_freeze_manifest.yaml",
    "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
    "docs/conformance/e4_artifact_catalog.json",
    "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py",
    "scripts/e4_parity/adapters/oh_my_pi_projection_packet.py",
    "scripts/e4_parity/adapters/oh_my_pi_p6_6_work_item_projection.py",
    "agentic_coder_prototype/compilation/primitive_records.py",
    "contracts/kernel/schemas/bb.work_item.v1.schema.json",
)


class LaneDefValidationError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LaneDefValidationError(f"{path} must contain a JSON object")
    return payload


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LaneDefValidationError(f"{path} must contain a YAML object")
    return payload


def _resolve_repo_reference(
    reference: str | Path,
    *,
    label: str = "declared repo reference",
) -> Path:
    try:
        return resolve_declared_reference(
            reference,
            checkout_root=ROOT,
            namespace="repo",
            label=label,
        )
    except ReferenceResolutionError as exc:
        raise LaneDefValidationError(str(exc)) from exc


def _resolve_checkout_path(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        return _resolve_repo_reference(path, label=label)
    try:
        reference = path.resolve().relative_to(ROOT.resolve())
    except ValueError as exc:
        raise LaneDefValidationError(
            f"{label} must stay within checkout {ROOT.resolve()}: {path}"
        ) from exc
    return _resolve_repo_reference(reference, label=label)


def _schema_path(schema_version: str) -> Path:
    return _resolve_repo_reference(
        f"{SCHEMA_DIR_REF}/{schema_version}.schema.json",
        label=f"schema {schema_version}",
    )


@lru_cache(maxsize=None)
def _validator(schema_version: str) -> Draft202012Validator:
    schema_path = _schema_path(schema_version)
    schema = _load_json(schema_path)
    common_path = _resolve_repo_reference(COMMON_SCHEMA_REF, label="common schema")
    e4_common_path = _resolve_repo_reference(E4_COMMON_SCHEMA_REF, label="E4 common schema")
    common = _load_json(common_path)
    e4_common = _load_json(e4_common_path)
    store = {
        schema.get("$id", schema_path.as_uri()): schema,
        schema_path.name: schema,
        common.get("$id", common_path.as_uri()): common,
        common_path.name: common,
        e4_common.get("$id", e4_common_path.as_uri()): e4_common,
        e4_common_path.name: e4_common,
    }
    return Draft202012Validator(
        schema,
        resolver=RefResolver(base_uri=schema_path.as_uri(), referrer=schema, store=store),
    )


def _format_error(error: Any) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path]
    if error.validator == "required":
        quoted = error.message.split("'")
        if len(quoted) >= 3:
            parts.append(quoted[1].replace("~", "~0").replace("/", "~1"))
    pointer = "/" + "/".join(parts) if parts else ""
    return f"{pointer or '<root>'}: {error.message}"


def _schema_version(payload: Mapping[str, Any], *, source: Path | None = None) -> str:
    value = payload.get("schema_version")
    if value not in SUPPORTED_SCHEMA_VERSIONS:
        prefix = f"{source}: " if source is not None else ""
        supported = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
        raise LaneDefValidationError(f"{prefix}unknown schema_version {value!r}; expected one of: {supported}")
    return str(value)


def _normalize_v1_lane(lane_def: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(lane_def)
    normalized["_lane_def_version"] = 1
    metadata = normalized.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    acceptance_packet = metadata.get("acceptance_packet") or {}
    if not isinstance(acceptance_packet, Mapping):
        acceptance_packet = {}

    normalized.setdefault(
        "run",
        {
            "run_id": metadata.get("run_id"),
            "provider_model": metadata.get("provider_model"),
            "sandbox_mode": metadata.get("sandbox_mode"),
        },
    )
    if normalized["run"] == {"run_id": None, "provider_model": None, "sandbox_mode": None}:
        normalized["run"] = None

    if normalized.get("provenance") is None:
        if normalized.get("target_family") == "breadboard" and not acceptance_packet:
            normalized["provenance"] = None
        else:
            normalized["provenance"] = {
                "upstream_repo": acceptance_packet.get("upstream_repo"),
                "upstream_commit": acceptance_packet.get("upstream_commit"),
                "upstream_commit_date": acceptance_packet.get("upstream_commit_date"),
                "upstream_release_label": acceptance_packet.get("upstream_release_label"),
                "source_paths": list(acceptance_packet.get("source_paths") or []),
            }

    if normalized.get("acceptance") is None:
        normalized["acceptance"] = {
            "behavior_family": acceptance_packet.get("behavior_family"),
            "semantic_key": acceptance_packet.get("semantic_key"),
            "target": acceptance_packet.get("target"),
            "assertions": list(acceptance_packet.get("assertions") or []),
        }

    ct = normalized.get("ct")
    if isinstance(ct, Mapping):
        ct = dict(ct)
        ct.setdefault("test_id", metadata.get("legacy_inventory_ct_test_id"))
        normalized["ct"] = ct

    return normalized


def _normalize_v2_lane(lane_def: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(lane_def)
    normalized["_lane_def_version"] = 2
    return normalized


def _validate_v2_adapter_references(lane_def: Mapping[str, Any], *, source: Path | None = None) -> None:
    checks: list[tuple[str, str, str]] = []
    normalize = lane_def.get("normalize")
    if isinstance(normalize, Mapping):
        translator = normalize.get("translator")
        if isinstance(translator, str):
            checks.append(("normalize.translator", translator, "translator"))
    compare = lane_def.get("compare")
    if isinstance(compare, Mapping):
        comparator = compare.get("comparator")
        if isinstance(comparator, str):
            checks.append(("compare.comparator", comparator, "comparator"))
    capture = lane_def.get("capture")
    if isinstance(capture, Mapping):
        adapter = capture.get("adapter")
        if isinstance(adapter, str):
            checks.append(("capture.adapter", adapter, "capture_adapter"))
    for field, value, expected_kind in checks:
        try:
            assert_registered("e4_adapters", value, expected_kind=expected_kind)
        except RegistryValidationError as exc:
            prefix = f"{source}: " if source is not None else ""
            raise LaneDefValidationError(f"{prefix}{field}: {exc}") from exc


def _validate_replay_mode(lane_def: Mapping[str, Any], *, source: Path | None = None) -> None:
    replay = lane_def.get("replay")
    if not isinstance(replay, Mapping) or "mode" not in replay:
        return
    if replay.get("mode") != "stored":
        prefix = f"{source}: " if source is not None else ""
        raise LaneDefValidationError(
            f"{prefix}/replay/mode: executed replay is deferred; "
            "no executable replay provider exists in Phase 20"
        )


def validate_lane_def(payload: Mapping[str, Any], *, source: Path | None = None) -> dict[str, Any]:
    schema_version = _schema_version(payload, source=source)
    lane_def = dict(payload)
    _validate_replay_mode(lane_def, source=source)
    errors = sorted((_format_error(error) for error in _validator(schema_version).iter_errors(lane_def)))
    if errors:
        prefix = f"{source}: " if source is not None else ""
        raise LaneDefValidationError(prefix + "; ".join(errors))
    if schema_version == SCHEMA_VERSION_V1:
        return _normalize_v1_lane(lane_def)
    _validate_v2_adapter_references(lane_def, source=source)
    return _normalize_v2_lane(lane_def)


def load_lane_def(path: Path) -> dict[str, Any]:
    return validate_lane_def(_load_yaml(path), source=path)

def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()




def _validate_payload(payload: Mapping[str, Any], schema_version: str, source: Path) -> None:
    errors = sorted((_format_error(error) for error in _validator(schema_version).iter_errors(dict(payload))))
    if errors:
        raise LaneDefValidationError(f"{source}: " + "; ".join(errors))


def _utc_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise LaneDefValidationError("target freeze row has no upstream_commit_date")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneDefValidationError(f"invalid upstream_commit_date {value!r}") from exc
    if parsed.tzinfo is None:
        raise LaneDefValidationError("upstream_commit_date must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_PILOT_PROVENANCE_ARTIFACTS = {
    "target_probe_output.json",
    "target_setup_and_capture_report.json",
    "joined_subagent_target_capture.json",
    "detached_subagent_target_capture.json",
}


def _provenance_source_paths(inputs: list[str]) -> list[str]:
    """AM8 pilot-exact mapping from declared inputs to source provenance.

    Preserve manifest order and select target observation artifacts, the source
    freeze provenance record, adapter implementations, and kernel schemas.
    Runtime outputs, catalogs, ledgers, configs, and directories are excluded.
    """
    selected: list[str] = []
    for reference in inputs:
        name = Path(reference).name
        if (
            name in _PILOT_PROVENANCE_ARTIFACTS
            or ("/source_freezes/" in reference and reference.endswith("_freeze_provenance.json"))
            or reference.startswith("scripts/e4_parity/adapters/")
            or reference.startswith("contracts/kernel/schemas/")
        ):
            selected.append(reference)
    return selected


def _derived_run(sidecar: Mapping[str, Any]) -> dict[str, str]:
    templates = sidecar.get("payload_templates")
    probe = templates.get("target_probe_output") if isinstance(templates, Mapping) else None
    if not isinstance(probe, Mapping):
        raise LaneDefValidationError(
            "sidecar target_probe_output payload is required"
        )
    fields = ("run_id", "provider_model", "sandbox_mode")
    if not all(
        isinstance(probe.get(field), str) and probe.get(field)
        for field in fields
    ):
        raise LaneDefValidationError(
            "sidecar target probe lacks run_id/provider_model/sandbox_mode"
        )
    return {field: str(probe[field]) for field in fields}


def _derived_provenance(manifest: Mapping[str, Any], inputs: list[str]) -> dict[str, Any]:
    target = manifest["target"]
    freeze_ref = target.get("source_freeze_ref")
    if not isinstance(freeze_ref, str):
        raise LaneDefValidationError("manifest target.source_freeze_ref is required")
    freeze = _load_yaml(
        _resolve_repo_reference(freeze_ref, label="target freeze reference")
    )
    rows = freeze.get("e4_configs")
    config_id = str(manifest["config_id"])
    row = rows.get(config_id) if isinstance(rows, Mapping) else None
    harness = row.get("harness") if isinstance(row, Mapping) else None
    if not isinstance(harness, Mapping):
        raise LaneDefValidationError(f"target freeze has no harness row for {config_id!r}")
    return {
        "provenance_kind": "git_commit",
        "upstream_repo": harness.get("upstream_repo"),
        "upstream_commit": harness.get("upstream_commit"),
        "upstream_commit_date": _utc_timestamp(harness.get("upstream_commit_date")),
        "upstream_release_label": harness.get("upstream_release_label"),
        "source_paths": _provenance_source_paths(inputs),
    }


def _assert_lock_matches_manifest(manifest_path: Path, manifest: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    lane_id = str(manifest["lane_id"])
    manifest_reference = manifest_path.resolve().relative_to(ROOT.resolve())
    lock_reference = manifest_reference.with_name(f"{lane_id}.lock.json")
    lock_path = _resolve_repo_reference(lock_reference, label="lane lock sibling")
    lock = _load_json(lock_path)
    _validate_payload(lock, "bb.e4.lane_lock.v1", lock_path)
    if lock.get("lane_id") != lane_id:
        raise LaneDefValidationError(f"{lock_path}: lane_id does not match manifest")
    if lock.get("manifest_sha256") != _sha256(manifest_path):
        raise LaneDefValidationError(f"{lock_path}: manifest_sha256 does not match manifest bytes")
    manifest_ref = manifest_path.resolve().relative_to(ROOT.resolve()).as_posix()
    if lock.get("manifest_ref") != manifest_ref:
        raise LaneDefValidationError(f"{lock_path}: manifest_ref does not name {manifest_ref}")
    sidecar_ref = lock.get("packet_constants_ref")
    if not isinstance(sidecar_ref, Mapping) or not isinstance(sidecar_ref.get("path"), str):
        raise LaneDefValidationError(f"{lock_path}: packet_constants_ref is required")
    sidecar_path = _resolve_repo_reference(
        str(sidecar_ref["path"]),
        label="packet constants sidecar reference",
    )
    sidecar = _load_json(sidecar_path)
    if sidecar_ref.get("sha256") != _sha256(sidecar_path):
        raise LaneDefValidationError(f"{lock_path}: packet_constants_ref sha256 drift")
    if set(sidecar) != {"payload_templates", "substitutions"}:
        raise LaneDefValidationError(f"{sidecar_path}: sidecar must contain exactly payload_templates/substitutions")
    return lock, sidecar


def _manifest_assertion(assertion: Mapping[str, Any]) -> dict[str, Any]:
    selector = assertion.get("record_selector")
    path = selector.get("path") if isinstance(selector, Mapping) else None
    return {
        "id": assertion.get("assertion_id"),
        "path": path,
        "op": assertion.get("kind"),
        "value": assertion.get("expect"),
        "description": assertion.get("description"),
    }


def _runtime_inputs(inputs: list[str], *, parity_legacy: bool) -> list[str]:
    if parity_legacy:
        return list(_PILOT_LEGACY_CAPTURE_INPUTS)
    return [
        SOURCE_FREEZE_EXTRACTION_REF
        if value == SOURCE_FREEZE_ARCHIVE_REF
        else value
        for value in inputs
    ]


def _runtime_payload_inputs(
    sidecar: Mapping[str, Any],
    roles: Mapping[str, str],
) -> dict[str, Any]:
    templates = sidecar.get("payload_templates")
    if not isinstance(templates, Mapping):
        raise LaneDefValidationError("sidecar payload_templates must be an object")
    return {
        roles[str(role)]: value
        for role, value in templates.items()
        if str(role) in roles
    }


def load_manifest_lane_def(path: Path, *, parity_legacy: bool = False) -> dict[str, Any]:
    """Normalize manifest+lock+sidecar to the v2 runtime or pilot-parity contract."""
    path = _resolve_checkout_path(path, label="lane manifest")
    manifest = _load_yaml(path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise LaneDefValidationError(f"{path}: expected {MANIFEST_SCHEMA_VERSION}")
    _validate_payload(manifest, MANIFEST_SCHEMA_VERSION, path)
    from scripts.e4_parity import compile_lane_lock

    try:
        compile_lane_lock.materialize_manifest_inputs(path)
    except (OSError, ValueError) as exc:
        raise LaneDefValidationError(
            f"{path}: cannot materialize declared runtime inputs: {exc}"
        ) from exc
    lock, sidecar = _assert_lock_matches_manifest(path, manifest)
    inputs = [str(value) for value in manifest["capture"].get("inputs", [])]
    normalize = manifest["normalize"]
    packet_constants = {
        "required_records": list(normalize.get("required_records", [])),
        "record_roles": dict(normalize.get("record_roles", {})),
        "record_envelopes": dict(normalize.get("record_envelopes", {})),
        "role_aliases": dict(normalize.get("role_aliases", {})),
        "auto_bind_role_refs": bool(normalize.get("auto_bind_role_refs", False)),
        "scope_observation_labels": list(normalize.get("scope_observation_labels", [])),
        "payload_templates": sidecar["payload_templates"],
        "substitutions": sidecar["substitutions"],
        "required_roles": list(normalize.get("required_roles", [])),
    }
    roles = {
        str(role): str(entry["path"])
        for role, entry in lock["artifact_roles"].items()
    }
    run = _derived_run(sidecar)
    ct = dict(manifest.get("ct", {}))
    runtime_inputs = _runtime_inputs(inputs, parity_legacy=parity_legacy)
    normalize_config = {
        "record_builders": list(normalize.get("record_builders", [])),
        "projection_constants": dict(normalize.get("projection_constants", {})),
        "roles": roles,
        "packet_constants": packet_constants,
    }
    if not parity_legacy:
        normalize_config["runtime_payload_inputs"] = _runtime_payload_inputs(
            sidecar,
            roles,
        )
    reverify_command = dict(manifest.get("reverify_command", {})) or None
    if parity_legacy and reverify_command is not None:
        argv = list(reverify_command.get("argv", []))
        if argv and argv[0] == "python":
            argv[0] = ".venv/bin/python"
            reverify_command["argv"] = argv
    lane_def = {
        "schema_version": SCHEMA_VERSION_V2,
        "lane_id": manifest["lane_id"],
        "config_id": manifest["config_id"],
        "target_family": manifest["target"]["family"],
        "target_version": manifest["target"]["version"],
        "package_ref": manifest["target"].get("package_ref"),
        "kind": manifest["kind"],
        "status": {"accepted": "accepted", "candidate": "captured", "draft": "planned"}[manifest.get("status", "draft")],
        "points": manifest.get("points", 0),
        "capture": {
            "strategy": manifest["capture"]["strategy"],
            "argv": manifest["capture"].get("argv"),
            "inputs": runtime_inputs,
            "workspace_template": manifest["capture"].get("workspace_template"),
            "adapter": manifest["capture"].get("adapter"),
        },
        "normalize": {
            "translator": manifest["normalize"].get("translator") or "identity",
            "mode": manifest["normalize"]["mode"],
            "config": normalize_config,
        },
        "replay": {
            "mode": manifest["replay"]["mode"],
            "artifacts": [roles["replay_ref"]],
            "session": None,
            "comparator_class": manifest["replay"]["comparator_class"],
        },
        "compare": {
            "comparator": manifest["compare"]["comparator"],
            "config": {"assertions": [_manifest_assertion(value) for value in manifest["compare"].get("assertions", [])]},
        },
        "claim": {
            "scope": dict(manifest["claim"]["scope"]),
            "exclusions": [str(value["reason"]) for value in manifest["claim"]["exclusions"]],
        },
        "ct": ct,
        "artifacts_root": manifest["artifacts_root"],
        "reverify_command": reverify_command,
        "metadata": {
            "legacy_inventory_ct_test_id": ct.get("test_id"),
            "provider_model": run["provider_model"],
            "run_id": run["run_id"],
            "sandbox_mode": run["sandbox_mode"],
        },
        "run": run,
        "provenance": _derived_provenance(manifest, runtime_inputs),
        "acceptance": dict(manifest["acceptance"]),
    }
    return validate_lane_def(lane_def, source=path)


def lane_lock_sha256(lane_id: str, directory: Path = DEFAULT_LANE_DEF_DIR) -> str | None:
    lock_path = directory / f"{lane_id}.lock.json"
    return _sha256(lock_path) if lock_path.is_file() else None


def inventory_lane_sources(
    directory: Path = DEFAULT_LANE_DEF_DIR,
) -> list[dict[str, str | None]]:
    """Classify and validate every YAML source consumed by the lane loader."""
    if not directory.exists():
        return []
    inventory: list[dict[str, str | None]] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.endswith(".manifest.yaml"):
            load_manifest_lane_def(path)
            kind = "lane_manifest"
            reason = None
        elif path.name.endswith(".payloads.yaml"):
            payload = _load_yaml(path)
            if set(payload) != {"payload_templates", "substitutions"}:
                raise LaneDefValidationError(
                    f"{path}: payload source must contain exactly "
                    "payload_templates and substitutions"
                )
            if not all(
                isinstance(payload[field], Mapping)
                for field in ("payload_templates", "substitutions")
            ):
                raise LaneDefValidationError(
                    f"{path}: payload_templates and substitutions must be objects"
                )
            kind = "payload_source"
            reason = None
        else:
            payload = _load_yaml(path)
            if payload.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS:
                validate_lane_def(payload, source=path)
                kind = "lane_def_legacy"
                reason = None
            else:
                kind = "excluded"
                reason = "unsupported or missing lane schema_version"
        inventory.append({"path": path.name, "kind": kind, "reason": reason})
    return inventory


def load_lane_defs(directory: Path = DEFAULT_LANE_DEF_DIR) -> dict[str, dict[str, Any]]:
    if not directory.exists():
        return {}
    inventory = inventory_lane_sources(directory)
    lane_defs: dict[str, dict[str, Any]] = {}
    for row in inventory:
        if row["kind"] != "lane_manifest":
            continue
        path = directory / str(row["path"])
        lane_def = load_manifest_lane_def(path)
        lane_id = str(lane_def["lane_id"])
        if lane_id in lane_defs:
            raise LaneDefValidationError(f"duplicate lane_id {lane_id!r} in {directory}")
        lane_defs[lane_id] = lane_def
    for row in inventory:
        if row["kind"] != "lane_def_legacy":
            continue
        path = directory / str(row["path"])
        if path.stem in lane_defs:
            continue
        lane_def = load_lane_def(path)
        lane_id = str(lane_def["lane_id"])
        if lane_id in lane_defs:
            raise LaneDefValidationError(f"duplicate lane_id {lane_id!r} in {directory}")
        lane_defs[lane_id] = lane_def
    return lane_defs
