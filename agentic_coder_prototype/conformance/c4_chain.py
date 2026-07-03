from __future__ import annotations

import argparse
import importlib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator, RefResolver
from scripts.e4_parity.validators.gate_errors import BlameEntry, GateError, apply_gate_error_envelope, gate_error_to_dict, gate_exit_code
from scripts.e4_parity.validators.registries import RegistryValidationError, assert_registered

FORBIDDEN_COMPONENTS = {"scratch", "scratch_runs", "tmp"}
SHARED_PREFIXES = ("/shared_folders", "/shared/")
CAPTURE_CLASSES = {"raw_target_capture", "replay_session_dump", "derived_capture", "real_agentic_coder_runtime_records"}
ACCEPTED_RAW_SOURCE_STATUS = {
    "canonical_raw_present",
    "replay_session_dump_declared_canonical",
    "real_agentic_coder_capture_normalized_from_kernel_events",
}
RAW_SOURCE_STATUS = ACCEPTED_RAW_SOURCE_STATUS | {"derived_from_unavailable_raw", "unknown"}
REQUIRED_SCOPE_KEYS = {"lane_id", "config_id", "run_id", "target_version", "provider_model", "sandbox_mode"}
REQUIRED_CHAIN_ROLES = {
    "freeze_manifest",
    "capture_ref",
    "replay_ref",
    "comparator_ref",
    "support_claim_ref",
    "parity_results",
    "secret_scan_report",
    "validator_output",
}
SUPPORT_CLAIM_SCHEMA_VERSIONS = {"bb.e4.support_claim.v1", "bb.e4.support_claim.v2", "bb.e4.support_claim.v3", "bb.e4.support_claim.v4"}
SUPPORT_CLAIM_SCHEMA_PATHS = {
    "bb.e4.support_claim.v1": Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "schemas" / "bb.e4.support_claim.v1.schema.json",
    "bb.e4.support_claim.v2": Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "schemas" / "bb.e4.support_claim.v2.schema.json",
    "bb.e4.support_claim.v3": Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "schemas" / "bb.e4.support_claim.v3.schema.json",
    "bb.e4.support_claim.v4": Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "schemas" / "bb.e4.support_claim.v4.schema.json",
}
COMMON_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "schemas" / "bb.kernel.common.v1.schema.json"
E4_COMMON_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "schemas" / "bb.e4.common.v1.schema.json"
from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH, catalog_stable_entries_hash, stable_entries, stable_entries_hash



class C4ChainValidationError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _try_load_json(path: Path, label: str, errors: list[str]) -> Any:
    if not path.exists():
        errors.append(f"{label}: missing path: {path}")
        return {}
    try:
        return _load_json(path)
    except Exception as exc:
        errors.append(f"{label}: unable to load JSON: {exc}")
        return {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _strip_ref_suffix(ref: str) -> str:
    return ref.split("#", 1)[0]


def _ref_hash(ref: str) -> str | None:
    if "#" not in ref:
        return None
    for part in ref.replace("#", " ").replace(";", " ").split():
        if part.startswith("sha256:") and len(part) == 71:
            return part
    return None


def _workspace_root(repo_root: Path) -> Path:
    return repo_root.parent


def _resolve_path(repo_root: Path, ref_or_path: str) -> Path:
    raw = _strip_ref_suffix(ref_or_path)
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    workspace = _workspace_root(repo_root)
    if raw.startswith("docs_tmp/") or raw.startswith("breadboard_repo_integration_main_20260326/"):
        return (workspace / raw).resolve()
    return (repo_root / raw).resolve()


def _display_path(repo_root: Path, path: Path) -> str:
    workspace = _workspace_root(repo_root).resolve()
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(workspace))
    except ValueError:
        return str(resolved)


def _canonicalish_path(repo_root: Path, path: Path) -> bool:
    resolved = path.resolve()
    workspace = _workspace_root(repo_root).resolve()
    roots = [
        workspace / "docs_tmp" / "phase_15",
        repo_root / "docs" / "conformance",
        repo_root / "artifacts" / "conformance",
        repo_root / "config",
        repo_root / "agent_configs",
    ]
    return any(resolved == root.resolve() or root.resolve() in resolved.parents for root in roots)


def _path_policy_errors(repo_root: Path, label: str, ref_or_path: str) -> list[str]:
    errors: list[str] = []
    raw = _strip_ref_suffix(ref_or_path)
    raw_path = Path(raw)
    if str(raw_path).startswith(SHARED_PREFIXES):
        errors.append(f"{label}: forbidden shared path: {raw}")
    if any(part in FORBIDDEN_COMPONENTS for part in raw_path.parts):
        errors.append(f"{label}: forbidden scratch/tmp component in path: {raw}")
    resolved = _resolve_path(repo_root, raw)
    if not _canonicalish_path(repo_root, resolved):
        errors.append(f"{label}: path is outside canonical roots: {raw}")
    return errors


def _require_mapping(value: Any, label: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        errors.append(f"{label}: must be an object")
        return {}
    return value


def _require_list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label}: must be a list")
        return []
    return value

def _format_schema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    prefix = f"schema.{path}: " if path else "schema: "
    return f"{prefix}{error.message}"


def _claim_anchor(support_claim: Mapping[str, Any], key: str) -> Any:
    if support_claim.get("schema_version") == "bb.e4.support_claim.v4":
        scope = support_claim.get("scope")
        return scope.get(key) if isinstance(scope, Mapping) else None
    return support_claim.get(key)


def collect_support_claim_schema_errors(
    support_claim: Mapping[str, Any],
    schema_path: Path | None = None,
) -> list[str]:
    schema_version = support_claim.get("schema_version")
    selected_schema_path = schema_path or SUPPORT_CLAIM_SCHEMA_PATHS.get(str(schema_version), SUPPORT_CLAIM_SCHEMA_PATHS["bb.e4.support_claim.v1"])
    schema = _load_json(selected_schema_path)
    common = _load_json(COMMON_SCHEMA_PATH)
    e4_common = _load_json(E4_COMMON_SCHEMA_PATH)
    store = {
        schema.get("$id", selected_schema_path.as_uri()): schema,
        selected_schema_path.name: schema,
        common["$id"]: common,
        COMMON_SCHEMA_PATH.name: common,
        e4_common["$id"]: e4_common,
        E4_COMMON_SCHEMA_PATH.name: e4_common,
    }
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(
        schema,
        resolver=RefResolver(base_uri=selected_schema_path.as_uri(), referrer=schema, store=store),
    )
    return [
        _format_schema_error(error)
        for error in sorted(
            validator.iter_errors(support_claim),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def _validate_artifact_hash(repo_root: Path, label: str, path_ref: str, expected_hash: str | None, errors: list[str]) -> tuple[Path | None, str | None]:
    errors.extend(_path_policy_errors(repo_root, label, path_ref))
    path = _resolve_path(repo_root, path_ref)
    if not path.exists():
        errors.append(f"{label}: missing path: {_strip_ref_suffix(path_ref)}")
        return None, None
    actual = _sha256(path)
    if expected_hash and actual != expected_hash:
        errors.append(f"{label}: hash mismatch for {_strip_ref_suffix(path_ref)}: expected {expected_hash}, got {actual}")
    ref_hash = _ref_hash(path_ref)
    if ref_hash and actual != ref_hash:
        errors.append(f"{label}: ref hash mismatch for {_strip_ref_suffix(path_ref)}: expected {ref_hash}, got {actual}")
    return path, actual

def _row_content_hash(row_id: str, row: Mapping[str, Any]) -> str:
    payload = {"row_id": row_id, "row": row}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _freeze_row_hash(freeze_manifest: Mapping[str, Any], config_id: str) -> str | None:
    configs = freeze_manifest.get("e4_configs")
    if not isinstance(configs, Mapping):
        return None
    row = configs.get(config_id)
    if not isinstance(row, Mapping):
        return None
    return _row_content_hash(config_id, row)


def _validate_freeze_row_ref(
    repo_root: Path,
    label: str,
    ref: str,
    *,
    freeze_manifest_path: Path,
    freeze_manifest: Mapping[str, Any],
    config_id: str,
    errors: list[str],
) -> tuple[Path | None, str | None]:
    errors.extend(_path_policy_errors(repo_root, label, ref))
    path = _resolve_path(repo_root, ref)
    if not path.exists():
        errors.append(f"{label}: missing path: {_strip_ref_suffix(ref)}")
        return None, None
    if path != freeze_manifest_path.resolve():
        errors.append(f"{label} must reference the selected freeze manifest")
    parts = ref.split("#")
    if len(parts) != 3 or not parts[1]:
        errors.append(f"{label} must use path#config_id#sha256:<row_hash>")
        return path, None
    ref_config_id = parts[1]
    ref_hash = _ref_hash(ref)
    expected_hash = _freeze_row_hash(freeze_manifest, config_id)
    if ref_config_id != config_id:
        errors.append(f"{label} must name the selected config_id exactly")
    if ref_hash is None:
        errors.append(f"{label} must include current sha256 row hash")
    elif expected_hash and ref_hash != expected_hash:
        errors.append(f"{label}: row hash mismatch for {config_id}: expected {expected_hash}, got {ref_hash}")
    return path, expected_hash


def _validate_freeze_manifest_artifact(
    repo_root: Path,
    label: str,
    path_ref: str,
    expected_hash: str | None,
    *,
    freeze_manifest_path: Path,
    freeze_manifest: Mapping[str, Any],
    config_id: str,
    errors: list[str],
) -> tuple[Path | None, str | None]:
    errors.extend(_path_policy_errors(repo_root, label, path_ref))
    path = _resolve_path(repo_root, path_ref)
    if not path.exists():
        errors.append(f"{label}: missing path: {_strip_ref_suffix(path_ref)}")
        return None, None
    if path != freeze_manifest_path.resolve():
        errors.append(f"{label}: path must reference the selected freeze manifest")
    actual = _freeze_row_hash(freeze_manifest, config_id)
    if actual is None:
        errors.append(f"{label}: selected freeze row missing for {config_id}")
        return path, None
    if expected_hash and actual != expected_hash:
        errors.append(f"{label}: freeze row hash mismatch for {config_id}: expected {expected_hash}, got {actual}")
    return path, actual


def _validate_ledger_row_ref(repo_root: Path, label: str, ref: str, errors: list[str]) -> str | None:
    errors.extend(_path_policy_errors(repo_root, label, ref))
    path = _resolve_path(repo_root, ref)
    if not path.exists():
        errors.append(f"{label}: missing path: {_strip_ref_suffix(ref)}")
        return None
    parts = ref.split("#")
    if len(parts) != 3 or not parts[1]:
        errors.append(f"{label} must use path#feature_id#sha256:<row_hash>")
        return None
    feature_id = parts[1]
    ref_hash = _ref_hash(ref)
    payload = _try_load_json(path, label, errors)
    rows = payload.get("rows") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        errors.append(f"{label}: ledger payload must contain rows list")
        return None
    matched = next((row for row in rows if isinstance(row, Mapping) and row.get("feature_id") == feature_id), None)
    if matched is None:
        errors.append(f"{label}: feature_id not found: {feature_id}")
        return None
    expected_hash = _row_content_hash(feature_id, matched)
    if ref_hash is None:
        errors.append(f"{label} must include current sha256 row hash")
    elif ref_hash != expected_hash:
        errors.append(f"{label}: row hash mismatch for {feature_id}: expected {expected_hash}, got {ref_hash}")
    return expected_hash


def _scope_contains(comparator_scope: Any, claim_scope: Any, prefix: str = "scope") -> list[str]:
    errors: list[str] = []
    if not isinstance(claim_scope, Mapping):
        return [f"{prefix}: support claim scope must be an object"]
    if not isinstance(comparator_scope, Mapping):
        return [f"{prefix}: comparator scope must be an object"]
    for key, claim_value in claim_scope.items():
        if key not in comparator_scope:
            errors.append(f"{prefix}.{key}: missing from comparator scope")
            continue
        comparator_value = comparator_scope[key]
        if isinstance(claim_value, Mapping):
            errors.extend(_scope_contains(comparator_value, claim_value, f"{prefix}.{key}"))
        elif comparator_value != claim_value:
            errors.append(f"{prefix}.{key}: support claim value {claim_value!r} is not proven by comparator value {comparator_value!r}")
    return errors


def _artifact_by_role(evidence_manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    artifacts = evidence_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return result
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        role = artifact.get("role")
        if isinstance(role, str):
            result[role] = artifact
    return result


def _normalize_manifest_path(repo_root: Path, value: str) -> str:
    resolved = _resolve_path(repo_root, value)
    return _display_path(repo_root, resolved)


def _load_freeze_manifest(path: Path) -> Mapping[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise C4ChainValidationError("freeze manifest root must be an object")
    return data


def _try_load_freeze_manifest(path: Path, errors: list[str]) -> Mapping[str, Any]:
    if not path.exists():
        errors.append(f"freeze_manifest: missing path: {path}")
        return {}
    try:
        return _load_freeze_manifest(path)
    except Exception as exc:
        errors.append(f"freeze_manifest: unable to load YAML: {exc}")
        return {}

def _assertion_identity(assertion: Mapping[str, Any]) -> str | None:
    value = assertion.get("assertion_id") or assertion.get("name")
    return value if isinstance(value, str) and value else None


def _validate_catalog_binding(repo_root: Path, support_claim: Mapping[str, Any], errors: list[str]) -> None:
    binding = support_claim.get("catalog_binding")
    if not isinstance(binding, Mapping):
        errors.append("support_claim.catalog_binding: must be an object")
        return
    catalog_path = binding.get("catalog_path")
    if catalog_path != CATALOG_PATH:
        errors.append(f"support_claim.catalog_binding.catalog_path must be {CATALOG_PATH}")
    catalog_hash = binding.get("catalog_hash")
    if not isinstance(catalog_hash, str) or not catalog_hash.startswith("sha256:"):
        errors.append("support_claim.catalog_binding.catalog_hash: missing")
    catalog_revision = binding.get("catalog_revision")
    if isinstance(catalog_revision, bool) or not isinstance(catalog_revision, int) or catalog_revision < 1:
        errors.append("support_claim.catalog_binding.catalog_revision must be an integer >= 1")
    catalog_file = repo_root / CATALOG_PATH
    catalog = _try_load_json(catalog_file, "support_claim.catalog_binding.catalog_path", errors)
    if not isinstance(catalog, Mapping):
        errors.append("support_claim.catalog_binding.catalog_path: catalog must be a JSON object")
        return
    live_revision = catalog.get("revision")
    if isinstance(live_revision, bool) or not isinstance(live_revision, int) or live_revision < 1:
        errors.append("support_claim.catalog_binding.catalog_revision: live catalog revision must be an integer >= 1")
    elif isinstance(catalog_revision, int) and not isinstance(catalog_revision, bool) and catalog_revision > live_revision:
        errors.append("support_claim.catalog_binding.catalog_revision must be <= live catalog revision")
    try:
        expected_hash = catalog_stable_entries_hash(catalog)
    except Exception as exc:
        errors.append(f"support_claim.catalog_binding.catalog_hash: unable to recompute stable digest: {exc}")
        return
    if isinstance(catalog_hash, str) and catalog_hash != expected_hash:
        errors.append(
            "support_claim.catalog_binding.catalog_hash mismatch: "
            f"expected {expected_hash}, got {catalog_hash}"
        )


def _catalog_entries(catalog: Mapping[str, Any], errors: list[str]) -> list[Mapping[str, Any]]:
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not all(isinstance(entry, Mapping) for entry in entries):
        errors.append("support_claim.catalog_binding.catalog_path: v1 catalog entries must be a list of objects")
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]


def _v1_pseudo_segment_hash(catalog: Mapping[str, Any], segment_id: str, errors: list[str]) -> str | None:
    entries = _catalog_entries(catalog, errors)
    if errors:
        return None
    if segment_id == "shared":
        segment_entries = [entry for entry in entries if not isinstance(entry.get("lane_id"), str) or not entry.get("lane_id")]
    else:
        segment_entries = [entry for entry in entries if entry.get("lane_id") == segment_id]
    try:
        return stable_entries_hash(segment_entries)
    except Exception as exc:
        errors.append(f"support_claim.catalog_binding.segment_hash: unable to recompute v1 pseudo-segment digest: {exc}")
        return None


def _v2_catalog_segment_hash(catalog: Mapping[str, Any], segment_id: str, errors: list[str]) -> str | None:
    segments = catalog.get("segments")
    if not isinstance(segments, list):
        errors.append("support_claim.catalog_binding.catalog_path: v2 catalog segments must be a list")
        return None
    matches = [
        segment
        for segment in segments
        if isinstance(segment, Mapping) and segment.get("segment_id") == segment_id
    ]
    if not matches:
        errors.append(f"support_claim.catalog_binding.segment_id not found in catalog: {segment_id}")
        return None
    if len(matches) > 1:
        errors.append(f"support_claim.catalog_binding.segment_id duplicated in catalog: {segment_id}")
        return None
    stable_hash = matches[0].get("stable_entries_hash")
    if not isinstance(stable_hash, str) or not stable_hash.startswith("sha256:"):
        errors.append(f"support_claim.catalog_binding.segment_id {segment_id} missing stable_entries_hash")
        return None
    return stable_hash


def _validate_segment_catalog_binding(repo_root: Path, support_claim: Mapping[str, Any], errors: list[str]) -> None:
    binding = support_claim.get("catalog_binding")
    if not isinstance(binding, Mapping):
        errors.append("support_claim.catalog_binding: must be an object")
        return
    catalog_path = binding.get("catalog_path")
    if catalog_path != CATALOG_PATH:
        errors.append(f"support_claim.catalog_binding.catalog_path must be {CATALOG_PATH}")
    segment_id = binding.get("segment_id")
    lane_id = _claim_anchor(support_claim, "lane_id")
    if not isinstance(segment_id, str) or not segment_id:
        errors.append("support_claim.catalog_binding.segment_id: missing")
    elif isinstance(lane_id, str) and segment_id != lane_id:
        errors.append("support_claim.catalog_binding.segment_id must match support_claim.scope.lane_id")
    segment_hash = binding.get("segment_hash")
    if not isinstance(segment_hash, str) or not segment_hash.startswith("sha256:"):
        errors.append("support_claim.catalog_binding.segment_hash: missing")
    shared_segment_hash = binding.get("shared_segment_hash")
    if not isinstance(shared_segment_hash, str) or not shared_segment_hash.startswith("sha256:"):
        errors.append("support_claim.catalog_binding.shared_segment_hash: missing")
    catalog_file = repo_root / CATALOG_PATH
    catalog = _try_load_json(catalog_file, "support_claim.catalog_binding.catalog_path", errors)
    if not isinstance(catalog, Mapping):
        errors.append("support_claim.catalog_binding.catalog_path: catalog must be a JSON object")
        return
    schema_version = catalog.get("schema_version")
    local_errors: list[str] = []
    if schema_version == "bb.e4.artifact_catalog.v2":
        expected_segment_hash = _v2_catalog_segment_hash(catalog, str(segment_id), local_errors) if isinstance(segment_id, str) else None
        expected_shared_hash = _v2_catalog_segment_hash(catalog, "shared", local_errors)
    else:
        expected_segment_hash = _v1_pseudo_segment_hash(catalog, str(segment_id), local_errors) if isinstance(segment_id, str) else None
        expected_shared_hash = _v1_pseudo_segment_hash(catalog, "shared", local_errors)
    errors.extend(local_errors)
    if expected_segment_hash and isinstance(segment_hash, str) and segment_hash != expected_segment_hash:
        errors.append(
            "support_claim.catalog_binding.segment_hash mismatch: "
            f"expected {expected_segment_hash}, got {segment_hash}"
        )
    if expected_shared_hash and isinstance(shared_segment_hash, str) and shared_segment_hash != expected_shared_hash:
        errors.append(
            "support_claim.catalog_binding.shared_segment_hash mismatch: "
            f"expected {expected_shared_hash}, got {shared_segment_hash}"
        )


def _catalog_blame_entries(repo_root: Path, catalog_path: str = CATALOG_PATH) -> tuple[BlameEntry, ...]:
    catalog_file = (repo_root / catalog_path).resolve()
    if not catalog_file.exists():
        return ()
    catalog = _try_load_json(catalog_file, "catalog_blame.catalog_path", [])
    entries = catalog.get("entries") if isinstance(catalog, Mapping) else None
    if not isinstance(entries, list):
        return ()
    try:
        stable_catalog_entries = stable_entries([item for item in entries if isinstance(item, Mapping)])
    except Exception:
        return ()
    blame: list[BlameEntry] = []
    for entry in stable_catalog_entries:
        role_id = entry.get("role_id")
        path_value = entry.get("path")
        prev_sha256 = entry.get("sha256")
        if not (isinstance(role_id, str) and isinstance(path_value, str) and isinstance(prev_sha256, str)):
            continue
        artifact_path = _resolve_path(repo_root, path_value)
        if not artifact_path.exists() or not artifact_path.is_file():
            continue
        cur_sha256 = _sha256(artifact_path)
        if cur_sha256 != prev_sha256:
            blame.append(
                BlameEntry(
                    role_id=role_id,
                    path=_display_path(repo_root, artifact_path),
                    prev_sha256=prev_sha256,
                    cur_sha256=cur_sha256,
                )
            )
    return tuple(blame)

def _load_comparator_registry(path: Path) -> Mapping[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, Mapping):
        raise C4ChainValidationError("comparator registry root must be an object")
    return payload


def _find_comparator_entry(registry: Mapping[str, Any], lane_id: str) -> Mapping[str, Any] | None:
    comparators = registry.get("comparators")
    if not isinstance(comparators, list):
        return None
    matches = []
    for entry in comparators:
        if not isinstance(entry, Mapping):
            continue
        lane_ids = entry.get("lane_ids")
        if isinstance(lane_ids, list) and lane_id in lane_ids:
            matches.append(entry)
    if len(matches) != 1:
        return None
    return matches[0]


def _load_comparator_callable(entry: Mapping[str, Any]) -> Any:
    entrypoint = entry.get("entrypoint")
    if not isinstance(entrypoint, Mapping):
        raise C4ChainValidationError("comparator entrypoint must be an object")
    module_name = entrypoint.get("module")
    callable_name = entrypoint.get("callable")
    if not isinstance(module_name, str) or not module_name:
        raise C4ChainValidationError("comparator entrypoint.module missing")
    if not isinstance(callable_name, str) or not callable_name:
        raise C4ChainValidationError("comparator entrypoint.callable missing")
    module = importlib.import_module(module_name)
    return getattr(module, callable_name)


def _assertion_map(report: Mapping[str, Any], label: str, errors: list[str]) -> dict[str, Mapping[str, Any]]:
    assertions = report.get("assertions")
    result: dict[str, Mapping[str, Any]] = {}
    if not isinstance(assertions, list):
        errors.append(f"{label}.assertions: must be a list")
        return result
    for index, assertion in enumerate(assertions):
        if not isinstance(assertion, Mapping):
            errors.append(f"{label}.assertions[{index}]: must be an object")
            continue
        identity = _assertion_identity(assertion)
        if identity is None:
            errors.append(f"{label}.assertions[{index}]: missing assertion_id/name")
            continue
        if identity in result:
            errors.append(f"{label}.assertions: duplicate assertion id {identity}")
            continue
        result[identity] = assertion
    return result


def diff_comparator_reports(
    *,
    stored: Mapping[str, Any],
    fresh: Mapping[str, Any],
    comparator_class: str,
    errors: list[str],
) -> dict[str, Any]:
    local_errors: list[str] = []
    stored_by_id = _assertion_map(stored, "comparator_ref", local_errors)
    fresh_by_id = _assertion_map(fresh, "comparator_rerun", local_errors)
    stored_ids = set(stored_by_id)
    fresh_ids = set(fresh_by_id)
    missing = sorted(stored_ids - fresh_ids)
    unexpected = sorted(fresh_ids - stored_ids)
    if missing:
        local_errors.append(f"comparator_rerun missing assertion ids: {', '.join(missing)}")
    if unexpected:
        local_errors.append(f"comparator_rerun unexpected assertion ids: {', '.join(unexpected)}")
    status_mismatches: list[str] = []
    value_mismatches: list[str] = []
    for identity in sorted(stored_ids & fresh_ids):
        stored_assertion = stored_by_id[identity]
        fresh_assertion = fresh_by_id[identity]
        if stored_assertion.get("status") != fresh_assertion.get("status"):
            status_mismatches.append(identity)
        if comparator_class == "deterministic_replay":
            if stored_assertion.get("observed") != fresh_assertion.get("observed") or stored_assertion.get("expected") != fresh_assertion.get("expected"):
                value_mismatches.append(identity)
    if status_mismatches:
        local_errors.append(f"comparator_rerun status mismatch assertion ids: {', '.join(status_mismatches)}")
    if value_mismatches:
        local_errors.append(f"comparator_rerun deterministic value mismatch assertion ids: {', '.join(value_mismatches)}")
    errors.extend(local_errors)
    return {
        "assertion_count": len(stored_by_id),
        "comparator_class": comparator_class,
        "missing_assertion_ids": missing,
        "unexpected_assertion_ids": unexpected,
        "status_mismatch_ids": status_mismatches,
        "value_mismatch_ids": value_mismatches,
        "ok": not local_errors,
    }


# Backward-compatible private alias for legacy imports from the script shim.
_diff_comparator_reports = diff_comparator_reports


def _rerun_comparator(
    *,
    repo_root: Path,
    registry_path: Path,
    roles: Mapping[str, Mapping[str, Any]],
    lane_id: str,
    capture: Mapping[str, Any],
    replay: Mapping[str, Any],
    comparator: Mapping[str, Any],
    claim_scope: Mapping[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    try:
        registry = _load_comparator_registry(registry_path)
    except Exception as exc:
        errors.append(f"comparator_registry: unable to load: {exc}")
        return {"ok": False, "errors": [str(exc)]}
    entry = _find_comparator_entry(registry, lane_id)
    if entry is None:
        errors.append(f"comparator_registry: exactly one comparator entry required for lane_id {lane_id}")
        return {"ok": False, "errors": [f"missing comparator for {lane_id}"]}
    comparator_class = str(entry.get("comparator_class") or "")
    if comparator_class in {"environment_bound", "hash_only_legacy"}:
        metadata = entry.get("metadata")
        if not isinstance(metadata, Mapping) or not metadata.get("reason"):
            errors.append(f"comparator_registry.{entry.get('comparator_id')}: {comparator_class} requires metadata.reason")
    artifact_paths: dict[str, Path] = {}
    for role, artifact in roles.items():
        path_value = artifact.get("path")
        repo_root_text = str(repo_root)
        if repo_root_text not in sys.path:
            sys.path.insert(0, repo_root_text)
        if isinstance(path_value, str) and path_value:
            artifact_paths[role] = _resolve_path(repo_root, path_value)
    try:
        compare = _load_comparator_callable(entry)
        fresh = compare(
            {
                "capture": dict(capture),
                "replay": dict(replay),
                "scope": dict(claim_scope),
                "artifacts": artifact_paths,
                "repo_root": repo_root,
            }
        )
    except Exception as exc:
        errors.append(f"comparator_rerun: {entry.get('comparator_id')}: {exc}")
        return {"ok": False, "comparator_id": entry.get("comparator_id"), "errors": [str(exc)]}
    if not isinstance(fresh, Mapping):
        errors.append(f"comparator_rerun: {entry.get('comparator_id')} returned non-object report")
        return {"ok": False, "comparator_id": entry.get("comparator_id"), "errors": ["non-object report"]}
    diff = diff_comparator_reports(
        stored=comparator,
        fresh=fresh,
        comparator_class=comparator_class,
        errors=errors,
    )
    diff["comparator_id"] = entry.get("comparator_id")
    diff["registry"] = _display_path(repo_root, registry_path)
    return diff


def validate_c4_chain(
    *,
    repo_root: Path,
    freeze_manifest_path: Path,
    config_id: str,
    support_claim_path: Path,
    evidence_manifest_path: Path | None = None,
    allow_unaccepted: bool = False,
    rerun_comparators: bool = True,
    comparator_registry_path: Path | None = None,
    no_rerun_reason: str | None = None,
    enforce_catalog_binding: bool = True,
    blame: bool = False,
) -> dict[str, Any]:
    """Validate one governed C4 support chain.

    Lane builders prevalidate before claim re-binding in the regeneration DAG.
    Enforcing catalog binding during that prevalidation would deadlock
    convergence; the CT stage re-validates strictly after re-binding and its
    gates are authoritative.
    """
    repo_root = repo_root.resolve()
    errors: list[str] = []
    refs: dict[str, str] = {}
    hashes: dict[str, str] = {}
    extra_gate_errors: list[GateError] = []

    freeze_manifest = _try_load_freeze_manifest(freeze_manifest_path, errors)
    configs = freeze_manifest.get("e4_configs")
    if not isinstance(configs, Mapping):
        errors.append("freeze_manifest: e4_configs must be an object")
        configs = {}
    row = configs.get(config_id)
    row = _require_mapping(row, f"freeze_manifest.e4_configs.{config_id}", errors)
    if row:
        config_path = row.get("config_path")
        if not isinstance(config_path, str) or not config_path:
            errors.append(f"{config_id}: config_path missing")
        else:
            _validate_artifact_hash(repo_root, f"{config_id}.config_path", config_path, None, errors)
        harness = _require_mapping(row.get("harness"), f"{config_id}.harness", errors)
        runtime_surface = _require_mapping(harness.get("runtime_surface"), f"{config_id}.harness.runtime_surface", errors)
        anchor = _require_mapping(row.get("calibration_anchor"), f"{config_id}.calibration_anchor", errors)
    else:
        harness = {}
        runtime_surface = {}
        anchor = {}
    freeze_row_hash = _freeze_row_hash(freeze_manifest, config_id)
    if freeze_row_hash:
        hashes["freeze_manifest_row"] = freeze_row_hash

    support_claim_path = support_claim_path.resolve()
    support_claim = _require_mapping(_try_load_json(support_claim_path, "support_claim", errors), "support_claim", errors)
    if evidence_manifest_path is None:
        manifest_ref = support_claim.get("evidence_manifest_ref")
        if isinstance(manifest_ref, str) and manifest_ref:
            evidence_manifest_path = _resolve_path(repo_root, manifest_ref)
    if evidence_manifest_path is None:
        errors.append("support_claim.evidence_manifest_ref: missing")
        evidence_manifest = {}
        evidence_manifest_path = repo_root / "__missing_evidence_manifest__.json"
    else:
        evidence_manifest_path = evidence_manifest_path.resolve()
        evidence_manifest = _require_mapping(_try_load_json(evidence_manifest_path, "evidence_manifest", errors), "evidence_manifest", errors)

    for label, path in (("freeze_manifest", freeze_manifest_path), ("support_claim", support_claim_path), ("evidence_manifest", evidence_manifest_path)):
        _validate_artifact_hash(repo_root, label, str(path), None, errors)
        refs[label] = _display_path(repo_root, path)
        if path.exists():
            hashes[label] = _sha256(path)

    support_schema_version = support_claim.get("schema_version")
    if support_schema_version not in SUPPORT_CLAIM_SCHEMA_VERSIONS:
        errors.append(f"support_claim.schema_version must be one of {sorted(SUPPORT_CLAIM_SCHEMA_VERSIONS)}")
    else:
        for error in collect_support_claim_schema_errors(support_claim):
            errors.append(f"support_claim.{error}")
    catalog_binding_pending: list[str] = []
    if support_schema_version == "bb.e4.support_claim.v2":
        catalog_binding_errors = errors if enforce_catalog_binding else catalog_binding_pending
        _validate_catalog_binding(repo_root, support_claim, catalog_binding_errors)
    elif support_schema_version in {"bb.e4.support_claim.v3", "bb.e4.support_claim.v4"}:
        target_family = _claim_anchor(support_claim, "target_family")
        if isinstance(target_family, str):
            try:
                assert_registered("target_families", target_family)
            except RegistryValidationError as exc:
                extra_gate_errors.append(exc.gate_error)
                errors.append(exc.gate_error.message)
        else:
            errors.append("support_claim.target_family: missing or not a string")
        catalog_binding_errors = errors if enforce_catalog_binding else catalog_binding_pending
        _validate_segment_catalog_binding(repo_root, support_claim, catalog_binding_errors)
    if support_schema_version == "bb.e4.support_claim.v4":
        for deprecated_field in ("config_id", "lane_id", "target_family", "target_version", "run_id", "provider_model", "sandbox_mode"):
            if deprecated_field in support_claim:
                errors.append(f"support_claim.{deprecated_field}: Additional properties are not allowed in v4; use support_claim.scope.{deprecated_field}")
    elif support_claim.get("lane_id") != support_claim.get("scope", {}).get("lane_id"):
        errors.append("support_claim.lane_id must match support_claim.scope.lane_id")
    if _claim_anchor(support_claim, "config_id") != config_id:
        errors.append("support_claim.config_id must match selected config_id")
    if support_claim.get("accepted") is not True and not allow_unaccepted:
        errors.append("support_claim.accepted must be true for promotion validation")
    required_claim_keys = ["claim_id", "scope", "exclusions", "freeze_ref", "capture_ref", "replay_ref", "comparator_ref", "evidence_manifest_ref", "validation_refs", "generated_at_utc"]
    if support_schema_version != "bb.e4.support_claim.v4":
        required_claim_keys.append("lane_id")
    for key in required_claim_keys:
        if key not in support_claim:
            errors.append(f"support_claim.{key}: missing")
    exclusions = support_claim.get("exclusions")
    exclusion_list = _require_list(exclusions, "support_claim.exclusions", errors)
    if not any(isinstance(item, str) and "broad" in item.lower() for item in exclusion_list):
        errors.append("support_claim.exclusions must explicitly reject broad support claims")

    support_freeze_hash = None
    freeze_ref = support_claim.get("freeze_ref")
    if isinstance(freeze_ref, str) and freeze_ref:
        _, support_freeze_hash = _validate_freeze_row_ref(
            repo_root,
            "support_claim.freeze_ref",
            freeze_ref,
            freeze_manifest_path=freeze_manifest_path,
            freeze_manifest=freeze_manifest,
            config_id=config_id,
            errors=errors,
        )
    else:
        errors.append("support_claim.freeze_ref: missing or not a string")

    validation_ref_list = _require_list(support_claim.get("validation_refs"), "support_claim.validation_refs", errors)
    if not validation_ref_list:
        errors.append("support_claim.validation_refs must include validator output")
    for validation_ref in validation_ref_list:
        validation_path = _resolve_path(repo_root, validation_ref)
        validation_hash = _ref_hash(validation_ref)
        if validation_hash is None:
            errors.append(f"support_claim.validation_refs missing sha256 hash: {validation_ref}")
            continue
        _validate_artifact_hash(repo_root, "support_claim.validation_refs", validation_ref, validation_hash, errors)
        validation_payload = _try_load_json(validation_path, "support_claim.validation_refs", errors)
        if isinstance(validation_payload, Mapping):
            if validation_payload.get("ok") is not True:
                errors.append("support_claim.validation_refs referenced validator output must have ok=true")
        else:
            errors.append("support_claim.validation_refs referenced validator output must be a JSON object")

    ledger_ref_list = _require_list(support_claim.get("ledger_row_refs"), "support_claim.ledger_row_refs", errors)
    if not ledger_ref_list:
        errors.append("support_claim.ledger_row_refs must include at least one atomic feature ledger row")
    for ledger_ref in ledger_ref_list:
        if not isinstance(ledger_ref, str) or not ledger_ref:
            errors.append("support_claim.ledger_row_refs contains non-string ref")
            continue
        _validate_ledger_row_ref(repo_root, "support_claim.ledger_row_refs", ledger_ref, errors)

    claim_scope = _require_mapping(support_claim.get("scope"), "support_claim.scope", errors)
    missing_scope = sorted(REQUIRED_SCOPE_KEYS - set(claim_scope))
    if missing_scope:
        errors.append(f"support_claim.scope missing semantic anchors: {', '.join(missing_scope)}")
    if claim_scope.get("config_id") != config_id:
        errors.append("support_claim.scope.config_id must match selected config_id")
    if runtime_surface.get("provider_model") and claim_scope.get("provider_model") != runtime_surface.get("provider_model"):
        errors.append("support_claim.scope.provider_model must match freeze manifest provider_model")

    if evidence_manifest.get("schema_version") != "bb.e4.evidence_manifest.v1":
        errors.append("evidence_manifest.schema_version must be bb.e4.evidence_manifest.v1")
    if evidence_manifest.get("claim_id") != support_claim.get("claim_id"):
        errors.append("evidence_manifest.claim_id must match support_claim.claim_id")
    if evidence_manifest.get("lane_id") != claim_scope.get("lane_id"):
        errors.append("evidence_manifest.lane_id must match support_claim.scope.lane_id")
    artifacts = _require_list(evidence_manifest.get("artifacts"), "evidence_manifest.artifacts", errors)
    roles = _artifact_by_role(evidence_manifest)
    missing_roles = sorted(REQUIRED_CHAIN_ROLES - set(roles))
    if missing_roles:
        errors.append(f"evidence_manifest.artifacts missing roles: {', '.join(missing_roles)}")
    checked_roots = evidence_manifest.get("forbidden_roots_checked")
    checked_root_list = _require_list(checked_roots, "evidence_manifest.forbidden_roots_checked", errors)
    for required in ["scratch", "tmp", "scratch_runs", "/shared_folders"]:
        if required not in checked_root_list:
            errors.append(f"evidence_manifest.forbidden_roots_checked missing {required}")

    evidence_hash_by_path: dict[str, str] = {}
    evidence_hash_by_role: dict[str, str] = {}
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping):
            errors.append(f"evidence_manifest.artifacts[{index}]: must be an object")
            continue
        role = artifact.get("role")
        path_value = artifact.get("path")
        expected_hash = artifact.get("sha256")
        if not isinstance(role, str) or not role:
            errors.append(f"evidence_manifest.artifacts[{index}].role: missing")
            continue
        if not isinstance(path_value, str) or not path_value:
            errors.append(f"evidence_manifest.artifacts[{index}].path: missing")
            continue
        if not isinstance(expected_hash, str) or not expected_hash.startswith("sha256:"):
            errors.append(f"evidence_manifest.artifacts[{index}].sha256: missing")
            continue
        if role == "freeze_manifest":
            artifact_path, actual_hash = _validate_freeze_manifest_artifact(
                repo_root,
                f"evidence_manifest.artifacts[{role}]",
                path_value,
                expected_hash,
                freeze_manifest_path=freeze_manifest_path,
                freeze_manifest=freeze_manifest,
                config_id=config_id,
                errors=errors,
            )
        else:
            artifact_path, actual_hash = _validate_artifact_hash(repo_root, f"evidence_manifest.artifacts[{role}]", path_value, expected_hash, errors)
        if artifact_path is not None and actual_hash is not None:
            display = _display_path(repo_root, artifact_path)
            evidence_hash_by_path[display] = actual_hash
            evidence_hash_by_role[role] = actual_hash
        derived_from = artifact.get("derived_from", [])
        if derived_from:
            for source_ref in _require_list(derived_from, f"evidence_manifest.artifacts[{role}].derived_from", errors):
                if not isinstance(source_ref, str):
                    errors.append(f"evidence_manifest.artifacts[{role}].derived_from contains non-string ref")
                    continue
                _validate_artifact_hash(repo_root, f"evidence_manifest.artifacts[{role}].derived_from", source_ref, _ref_hash(source_ref), errors)

    def artifact_ref_path(role: str) -> str | None:
        artifact = roles.get(role)
        if not artifact:
            return None
        path_value = artifact.get("path")
        return path_value if isinstance(path_value, str) else None

    freeze_role_path = artifact_ref_path("freeze_manifest")
    if isinstance(freeze_ref, str) and freeze_role_path and _normalize_manifest_path(repo_root, freeze_ref) != _normalize_manifest_path(repo_root, freeze_role_path):
        errors.append("support_claim.freeze_ref does not match evidence_manifest role freeze_manifest")

    claim_ref_roles = {
        "capture_ref": "capture_ref",
        "replay_ref": "replay_ref",
        "comparator_ref": "comparator_ref",
        "evidence_manifest_ref": None,
    }
    for claim_field, role in claim_ref_roles.items():
        claim_ref = support_claim.get(claim_field)
        if not isinstance(claim_ref, str) or not claim_ref:
            errors.append(f"support_claim.{claim_field}: missing")
            continue
        _validate_artifact_hash(repo_root, f"support_claim.{claim_field}", claim_ref, _ref_hash(claim_ref), errors)
        if role:
            role_path = artifact_ref_path(role)
            if role_path and _normalize_manifest_path(repo_root, claim_ref) != _normalize_manifest_path(repo_root, role_path):
                errors.append(f"support_claim.{claim_field} does not match evidence_manifest role {role}")

    validator_output_path = artifact_ref_path("validator_output")
    validator_output_ref = None
    if validator_output_path:
        normalized_validator_output_path = _normalize_manifest_path(repo_root, validator_output_path)
        for validation_ref in validation_ref_list:
            if isinstance(validation_ref, str) and _normalize_manifest_path(repo_root, validation_ref) == normalized_validator_output_path:
                validator_output_ref = validation_ref
                break
        if validator_output_ref is None:
            errors.append("support_claim.validation_refs does not match evidence_manifest role validator_output")

    if isinstance(support_claim.get("evidence_manifest_ref"), str):
        manifest_ref_resolved = _resolve_path(repo_root, support_claim["evidence_manifest_ref"])
        if manifest_ref_resolved != evidence_manifest_path:
            errors.append("support_claim.evidence_manifest_ref does not match selected evidence manifest path")

    capture_path = _resolve_path(repo_root, str(support_claim.get("capture_ref", "")))
    replay_path = _resolve_path(repo_root, str(support_claim.get("replay_ref", "")))
    comparator_path = _resolve_path(repo_root, str(support_claim.get("comparator_ref", "")))
    capture = _require_mapping(_try_load_json(capture_path, "capture_ref", errors), "capture_ref", errors) if capture_path.exists() else {}
    replay = _require_mapping(_try_load_json(replay_path, "replay_ref", errors), "replay_ref", errors) if replay_path.exists() else {}
    comparator = _require_mapping(_try_load_json(comparator_path, "comparator_ref", errors), "comparator_ref", errors) if comparator_path.exists() else {}

    if capture.get("schema_version") != "bb.e4.raw_capture_manifest.v1":
        errors.append("capture_ref.schema_version must be bb.e4.raw_capture_manifest.v1")
    if capture.get("capture_class") not in CAPTURE_CLASSES:
        errors.append("capture_ref.capture_class invalid")
    if capture.get("raw_source_status") not in RAW_SOURCE_STATUS:
        errors.append("capture_ref.raw_source_status invalid")
    if capture.get("accepted_as_capture_ref") is not True:
        errors.append("capture_ref.accepted_as_capture_ref must be true")
    if capture.get("accepted_as_capture_ref") is True and capture.get("raw_source_status") not in ACCEPTED_RAW_SOURCE_STATUS:
        errors.append("capture_ref accepted_as_capture_ref requires canonical raw or declared canonical replay source")
    if not capture.get("lineage_rationale"):
        errors.append("capture_ref.lineage_rationale missing")
    _require_list(capture.get("source_artifacts"), "capture_ref.source_artifacts", errors)
    source_hashes = _require_mapping(capture.get("source_hashes"), "capture_ref.source_hashes", errors)
    for key in REQUIRED_SCOPE_KEYS:
        if key in {"provider_model", "target_version", "run_id", "sandbox_mode", "lane_id", "config_id"} and capture.get(key) != claim_scope.get(key):
            errors.append(f"capture_ref.{key} must match support claim scope")
    for path_value, expected_hash in source_hashes.items():
        if isinstance(path_value, str) and isinstance(expected_hash, str):
            _validate_artifact_hash(repo_root, f"capture_ref.source_hashes[{path_value}]", path_value, expected_hash, errors)

    if replay.get("schema_version") != "bb.e4.bb_replay_result.v1":
        errors.append("replay_ref.schema_version must be bb.e4.bb_replay_result.v1")
    if replay.get("exit_status") != "passed":
        errors.append("replay_ref.exit_status must be passed")
    if replay.get("warnings"):
        errors.append("replay_ref.warnings must be empty for promotion")
    if replay.get("errors"):
        errors.append("replay_ref.errors must be empty for promotion")
    if replay.get("lane_id") != claim_scope.get("lane_id") or replay.get("config_id") != config_id:
        errors.append("replay_ref lane/config must match support claim")
    replay_hashes = _require_mapping(replay.get("input_hashes"), "replay_ref.input_hashes", errors)
    for path_value, expected_hash in replay_hashes.items():
        if isinstance(path_value, str) and isinstance(expected_hash, str):
            _validate_artifact_hash(repo_root, f"replay_ref.input_hashes[{path_value}]", path_value, expected_hash, errors)

    if comparator.get("schema_version") != "bb.e4.comparator_report.v1":
        errors.append("comparator_ref.schema_version must be bb.e4.comparator_report.v1")
    if comparator.get("failed") != 0:
        errors.append("comparator_ref.failed must be 0")
    if comparator.get("warned") != 0:
        errors.append("comparator_ref.warned must be 0")
    if not isinstance(comparator.get("details"), list) or not comparator.get("details"):
        errors.append("comparator_ref.details must be non-empty")
    assertions = comparator.get("assertions")
    assertion_list = _require_list(assertions, "comparator_ref.assertions", errors)
    if not assertion_list:
        errors.append("comparator_ref.assertions must be a non-empty list")
    for index, assertion in enumerate(assertion_list):
        if not isinstance(assertion, Mapping):
            errors.append(f"comparator_ref.assertions[{index}]: must be an object")
            continue
        if not isinstance(assertion.get("name"), str) or not assertion.get("name"):
            errors.append(f"comparator_ref.assertions[{index}].name: missing")
        if assertion.get("status") != "passed":
            errors.append(f"comparator_ref.assertions[{index}].status must be passed")
        if "observed" not in assertion:
            errors.append(f"comparator_ref.assertions[{index}].observed: missing")
        if "expected" not in assertion:
            errors.append(f"comparator_ref.assertions[{index}].expected: missing")
        if "observed" in assertion and "expected" in assertion and assertion.get("observed") != assertion.get("expected"):
            errors.append(f"comparator_ref.assertions[{index}].observed must match expected")
    if support_schema_version in {"bb.e4.support_claim.v2", "bb.e4.support_claim.v3", "bb.e4.support_claim.v4"}:
        passed_assertion_ids = {
            identity
            for assertion in assertion_list
            if isinstance(assertion, Mapping)
            for identity in (_assertion_identity(assertion),)
            if identity and assertion.get("status") == "passed"
        }
        semantics = support_claim.get("claim_semantics")
        asserted_behaviors = semantics.get("asserted_behaviors") if isinstance(semantics, Mapping) else None
        for behavior_index, behavior in enumerate(_require_list(asserted_behaviors, "support_claim.claim_semantics.asserted_behaviors", errors)):
            if not isinstance(behavior, Mapping):
                errors.append(f"support_claim.claim_semantics.asserted_behaviors[{behavior_index}]: must be an object")
                continue
            for assertion_id in _require_list(
                behavior.get("comparator_assertion_ids"),
                f"support_claim.claim_semantics.asserted_behaviors[{behavior_index}].comparator_assertion_ids",
                errors,
            ):
                if not isinstance(assertion_id, str) or not assertion_id:
                    errors.append(
                        f"support_claim.claim_semantics.asserted_behaviors[{behavior_index}].comparator_assertion_ids contains non-string id"
                    )
                elif assertion_id not in passed_assertion_ids:
                    errors.append(
                        f"support_claim.claim_semantics.asserted_behaviors[{behavior_index}] comparator_assertion_id not passed: {assertion_id}"
                    )
    if comparator.get("lane_id") != claim_scope.get("lane_id") or comparator.get("config_id") != config_id:
        errors.append("comparator_ref lane/config must match support claim")
    errors.extend(_scope_contains(comparator.get("scope"), claim_scope))
    comparator_hashes = comparator.get("input_hashes", {})
    if isinstance(comparator_hashes, Mapping):
        for path_value, expected_hash in comparator_hashes.items():
            if isinstance(path_value, str) and isinstance(expected_hash, str):
                _validate_artifact_hash(repo_root, f"comparator_ref.input_hashes[{path_value}]", path_value, expected_hash, errors)

    evidence_paths = anchor.get("evidence_paths")
    evidence_path_list = _require_list(evidence_paths, f"{config_id}.calibration_anchor.evidence_paths", errors)
    normalized_evidence_paths = {_normalize_manifest_path(repo_root, str(item)) for item in evidence_path_list if isinstance(item, str)}
    required_refs = {
        "capture_ref": support_claim.get("capture_ref"),
        "replay_ref": support_claim.get("replay_ref"),
        "comparator_ref": support_claim.get("comparator_ref"),
        "support_claim_ref": str(support_claim_path),
        "evidence_manifest_ref": str(evidence_manifest_path),
    }
    if validator_output_ref:
        required_refs["validator_output"] = validator_output_ref
    parity_path = artifact_ref_path("parity_results")
    if parity_path:
        required_refs["parity_results"] = parity_path
    for label, ref in required_refs.items():
        if isinstance(ref, str) and _normalize_manifest_path(repo_root, ref) not in normalized_evidence_paths:
            errors.append(f"{config_id}.calibration_anchor.evidence_paths missing {label}: {_normalize_manifest_path(repo_root, ref)}")

    comparator_rerun: dict[str, Any]
    if rerun_comparators:
        registry_path = comparator_registry_path or (repo_root / "conformance" / "comparators" / "registry.json")
        if not registry_path.exists():
            registry_path = Path(__file__).resolve().parents[2] / "conformance" / "comparators" / "registry.json"
        comparator_rerun = _rerun_comparator(
            repo_root=repo_root,
            registry_path=registry_path.resolve(),
            roles=roles,
            lane_id=str(claim_scope.get("lane_id", "")),
            capture=capture,
            replay=replay,
            comparator=comparator,
            claim_scope=claim_scope,
            errors=errors,
        )
    else:
        comparator_rerun = {
            "ok": None,
            "skipped": True,
            "reason": no_rerun_reason or "--no-rerun-comparators escape used",
        }

    claimed_scope = dict(claim_scope)
    metadata = support_claim.get("metadata")
    legacy_scope = metadata.get("legacy_scope") if isinstance(metadata, Mapping) else None
    if isinstance(legacy_scope, Mapping):
        claimed_scope.update({str(key): value for key, value in legacy_scope.items() if isinstance(key, str)})

    report = {
        "schema_version": "bb.e4.c4_chain_validation_report.v1",
        "ok": not errors,
        "config_id": config_id,
        "support_claim": _display_path(repo_root, support_claim_path),
        "evidence_manifest": _display_path(repo_root, evidence_manifest_path),
        "refs": refs,
        "hashes": hashes,
        "errors": errors,
        "catalog_binding_pending": catalog_binding_pending,
        "claimed_scope": claimed_scope,
        "accepted": bool(support_claim.get("accepted")) and not errors,
        "comparator_rerun": comparator_rerun,
    }
    apply_gate_error_envelope(report, "c4_chain", blame=_catalog_blame_entries(repo_root) if blame else ())
    if extra_gate_errors:
        extra_messages = {error.message for error in extra_gate_errors}
        converted = [error for error in report.get("gate_errors", []) if error.get("message") not in extra_messages]
        report["gate_errors"] = [gate_error_to_dict(error) for error in extra_gate_errors] + converted
        report["semantic_count"] = sum(1 for error in report["gate_errors"] if error.get("klass") == "semantic")
        report["pin_stale_count"] = sum(1 for error in report["gate_errors"] if error.get("klass") == "pin_stale")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one governed E4 C4 target-support chain.")
    parser.add_argument("--repo-root", default=".", help="Implementation repository root")
    parser.add_argument("--freeze-manifest", default="config/e4_target_freeze_manifest.yaml", help="E4 target freeze manifest")
    parser.add_argument("--config-id", required=True, help="Selected e4_configs row id")
    parser.add_argument("--support-claim", required=True, help="Lane-level bb.e4.support_claim.v1 JSON")
    parser.add_argument("--evidence-manifest", default="", help="Lane-level bb.e4.evidence_manifest.v1 JSON; defaults to support claim ref")
    parser.add_argument("--allow-unaccepted", action="store_true", help="Validate shape/freshness before support_claim.accepted is flipped true")
    parser.add_argument("--rerun-comparators", dest="rerun_comparators", action="store_true", default=True, help="Rerun the lane comparator and diff against the stored report (default).")
    parser.add_argument("--no-rerun-comparators", dest="rerun_comparators", action="store_false", help="Skip comparator re-execution and log the escape in JSON output.")
    parser.add_argument("--comparator-registry", default="conformance/comparators/registry.json", help="Comparator registry JSON path")
    parser.add_argument("--check-only", action="store_true", help="Validate without writing --json-out")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument("--json-out", default="", help="Write JSON report to this path")
    parser.add_argument("--blame", action="store_true", help="Attach drifted stable catalog role_ids to catalog hash errors")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    freeze_manifest = (repo_root / args.freeze_manifest).resolve()
    support_claim = _resolve_path(repo_root, args.support_claim)
    evidence_manifest = _resolve_path(repo_root, args.evidence_manifest) if args.evidence_manifest else None
    report = validate_c4_chain(
        repo_root=repo_root,
        freeze_manifest_path=freeze_manifest,
        config_id=args.config_id,
        support_claim_path=support_claim,
        evidence_manifest_path=evidence_manifest,
        allow_unaccepted=args.allow_unaccepted,
        rerun_comparators=args.rerun_comparators,
        comparator_registry_path=_resolve_path(repo_root, args.comparator_registry),
        no_rerun_reason="--no-rerun-comparators escape used" if not args.rerun_comparators else None,
        blame=args.blame,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    if args.json_out and not args.check_only:
        out_path = _resolve_path(repo_root, args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if report["ok"]:
        print(f"[e4-c4-chain] pass: {args.config_id}")
        return 0
    print(f"[e4-c4-chain] fail: {args.config_id}", file=sys.stderr)
    for error in report["errors"]:
        print(f"- {error}", file=sys.stderr)
    return gate_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
