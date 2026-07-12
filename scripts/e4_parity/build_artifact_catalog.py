#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Collection, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.compilation.primitive_records import (  # noqa: E402
    canonical_record_bytes,
    finalize_record,
    get_spec,
    sha256_ref,
)
from agentic_coder_prototype.conformance.catalog_binding import catalog_segments, stable_entries_hash  # noqa: E402

DEFAULT_INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
DEFAULT_REPORT_ROLES_PATH = ROOT / "docs" / "conformance" / "e4_report_roles.json"
DEFAULT_OUTPUT_PATH = ROOT / "docs" / "conformance" / "e4_artifact_catalog.json"
DEFAULT_TOOLING_MANIFEST_PATH = ROOT / "docs" / "conformance" / "e4_tooling_manifest.json"
DEFAULT_GENERATED_AT_UTC = "2026-07-03T00:00:00Z"
CATALOG_V2_ID = "e4_artifact_catalog_v2"
CHECKOUT_PREFIX = f"{ROOT.name}/"
DEFAULT_ATOMIC_LEDGER_ROLE_ID = "e4_static:report/bb_e4_atomic_feature_ledger_seed_json"

_ROLE_ALIASES: dict[str, str] = {
    "capture": "capture_ref",
    "comparator": "comparator_ref",
    "replay": "replay_ref",
    "secret_scan": "secret_scan_report",
    "support_claim": "support_claim_ref",
    "work_item": "work_item_ref",
}

_ARTIFACT_KIND_BY_ROLE: dict[str, str] = {
    "agent_config": "config",
    "atomic_feature_ledger": "ledger",
    "capture": "capture",
    "capture_ref": "capture",
    "comparator": "comparator",
    "comparator_ref": "comparator",
    "compiled_records": "schema",
    "detached_subagent_target_capture": "capture",
    "effective_config_graph": "schema",
    "evidence_ledger": "ledger",
    "evidence_manifest": "evidence_manifest",
    "freeze_manifest": "freeze",
    "joined_subagent_target_capture": "capture",
    "live_endpoint_probe": "node_gate",
    "memory_compaction_plan": "config",
    "node_gate": "node_gate",
    "parity_results": "parity_results",
    "primitive_projection_manifest": "evidence_manifest",
    "projection_events": "other",
    "replay": "replay",
    "replay_ref": "replay",
    "schema_validation": "node_gate",
    "secret_scan": "secret_scan",
    "secret_scan_report": "secret_scan",
    "session_transcript": "other",
    "source_archive": "freeze",
    "source_freeze": "freeze",
    "support_claim": "support_claim",
    "support_claim_ref": "support_claim",
    "target_config": "config",
    "target_probe_output": "node_gate",
    "target_probe_script": "script",
    "target_setup_report": "report",
    "task_job_subagent_comparator": "comparator",
    "transcript_continuation_patch": "other",
    "validator_output": "node_gate",
    "work_item": "config",
    "work_item_ref": "config",
    "work_item_replay": "replay",
}


def load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _split_ref(value: str) -> str:
    return value.split("#", 1)[0]


def display_path(path: Path | str) -> str:
    raw = Path(path)
    resolved = raw.resolve() if raw.is_absolute() else raw
    if raw.is_absolute():
        try:
            return resolved.relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            try:
                return resolved.relative_to(WORKSPACE.resolve()).as_posix()
            except ValueError:
                return resolved.as_posix()

    text = raw.as_posix()
    if text.startswith(CHECKOUT_PREFIX):
        return text[len(CHECKOUT_PREFIX) :]
    return text


def resolve_registered_path(ref: str | Path) -> Path:
    text = _split_ref(str(ref))
    path = Path(text)
    if path.is_absolute():
        return path.resolve()
    if text.startswith(CHECKOUT_PREFIX):
        return (WORKSPACE / text).resolve()
    if text.startswith("docs_tmp/"):
        return (WORKSPACE / text).resolve()
    return (ROOT / text).resolve()



def _row_content_hash(row_id: str, row: Mapping[str, Any]) -> str:
    encoded = json.dumps({"row_id": row_id, "row": row}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _artifact_by_role(evidence_manifest: Mapping[str, Any], role: str) -> dict[str, Any] | None:
    artifacts = evidence_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if isinstance(artifact, dict) and artifact.get("role") == role:
            return artifact
    return None

def _empty_sync_result() -> dict[str, int]:
    return {
        "support_claims_checked": 0,
        "support_claims_changed": 0,
        "evidence_manifests_changed": 0,
        "node_gates_changed": 0,
        "accepted_p0_lanes_skipped_missing_lock": 0,
    }


def _node_gate_paths_by_evidence_manifest(inventory_path: Path | str) -> dict[str, Path]:
    try:
        inventory = load_json(inventory_path)
    except (OSError, json.JSONDecodeError):
        return {}
    lanes = inventory.get("lanes") if isinstance(inventory, Mapping) else None
    if not isinstance(lanes, list):
        return {}
    result: dict[str, Path] = {}
    for lane in lanes:
        if not isinstance(lane, Mapping) or not isinstance(lane.get("ct"), Mapping):
            continue
        evidence_key = display_path(_evidence_manifest_path(lane))
        node_gate_path = _sync_artifact_path(_node_gate_path(lane), role="node_gate")
        result[evidence_key] = node_gate_path
    return result


def _refresh_node_gate_hashes(node_gate_path: Path, support_path: Path, evidence_path: Path) -> bool:
    if not node_gate_path.exists():
        return False
    node_gate = load_json(node_gate_path)
    if not isinstance(node_gate, dict):
        raise ValueError(f"node gate must be an object: {display_path(node_gate_path)}")
    hashes = node_gate.setdefault("hashes", {})
    if not isinstance(hashes, dict):
        raise ValueError(f"{display_path(node_gate_path)} hashes must be an object")
    expected_hashes = {
        "support_claim": sha256_path(support_path),
        "evidence_manifest": sha256_path(evidence_path),
    }
    changed = False
    for key, value in expected_hashes.items():
        if hashes.get(key) != value:
            hashes[key] = value
            changed = True
    if changed:
        write_json(node_gate_path, node_gate)
    return changed





def _sync_artifact_path(ref: str, *, role: str) -> Path:
    if not ref or "#" in ref:
        raise ValueError(f"{role} path must be a non-empty artifact path without a fragment")
    text = str(ref)
    if Path(text).is_absolute():
        raise ValueError(f"{role} path must be repository/workspace-relative: {ref}")
    resolved = resolve_registered_path(text)
    if text.startswith("docs_tmp/"):
        allowed_root = (WORKSPACE / "docs_tmp").resolve()
    else:
        allowed_root = ROOT.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(f"{role} path escapes its allowed root: {ref}") from exc
    return resolved


def _required_artifact_by_role(evidence_manifest: Mapping[str, Any], role: str) -> dict[str, Any]:
    artifacts = evidence_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("evidence manifest artifacts must be a list")
    matches = [artifact for artifact in artifacts if isinstance(artifact, dict) and artifact.get("role") == role]
    if len(matches) != 1:
        raise ValueError(f"evidence manifest must declare exactly one {role!r} artifact")
    return matches[0]


def _declared_ledger_ref(lane: Mapping[str, Any], manifest_ref: str) -> str | None:
    lane_id = lane.get("lane_id")
    if not isinstance(lane_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", lane_id) is None:
        raise ValueError("lane_id must be a filename-safe identifier")
    locks_root = (ROOT / "config" / "e4_lanes").resolve()
    lock_path = (locks_root / f"{lane_id}.lock.json").resolve()
    try:
        lock_path.relative_to(locks_root)
    except ValueError as exc:
        raise ValueError(f"lane {lane_id!r} lock path escapes the lane-lock directory") from exc
    if not lock_path.exists():
        if lane.get("phase") == "P0" and lane.get("status") == "accepted":
            return None
        if lane.get("phase") == "P0" and manifest_ref.startswith("docs_tmp/"):
            raise ValueError(
                f"lane {lane_id!r} has no tracked lane-lock authority for its atomic_feature_ledger"
            )
        return manifest_ref
    lock = load_json(lock_path)
    if not isinstance(lock, Mapping) or lock.get("lane_id") != lane_id:
        raise ValueError(f"lane lock {display_path(lock_path)} lane_id does not match {lane_id!r}")
    artifact_roles = lock.get("artifact_roles")
    ledger_binding = artifact_roles.get("atomic_feature_ledger") if isinstance(artifact_roles, Mapping) else None
    lock_ref = ledger_binding.get("path") if isinstance(ledger_binding, Mapping) else None
    if not isinstance(lock_ref, str) or not lock_ref:
        raise ValueError(f"lane lock {display_path(lock_path)} has no atomic_feature_ledger path")
    lock_ledger_path = _sync_artifact_path(lock_ref, role="lane lock atomic_feature_ledger")
    if lock_ledger_path != _sync_artifact_path(manifest_ref, role="evidence manifest atomic_feature_ledger"):
        raise ValueError(
            f"lane {lane_id!r} evidence manifest atomic_feature_ledger path does not match its lane lock"
        )
    resolved_inputs = lock.get("resolved_inputs")
    if not isinstance(resolved_inputs, list):
        raise ValueError(f"lane lock {display_path(lock_path)} resolved_inputs must be a list")
    matching_inputs = [
        item
        for item in resolved_inputs
        if isinstance(item, Mapping)
        and isinstance(item.get("path"), str)
        and _sync_artifact_path(str(item["path"]), role="lane lock resolved input") == lock_ledger_path
    ]
    if len(matching_inputs) != 1:
        raise ValueError(
            f"lane lock {display_path(lock_path)} must contain exactly one resolved input for atomic_feature_ledger"
        )
    resolved_input = matching_inputs[0]
    if not lock_ledger_path.is_file():
        raise FileNotFoundError(f"atomic_feature_ledger does not exist: {display_path(lock_ledger_path)}")
    if resolved_input.get("sha256") != sha256_path(lock_ledger_path):
        raise ValueError(f"lane lock {display_path(lock_path)} atomic_feature_ledger sha256 is inconsistent")
    if resolved_input.get("bytes") != lock_ledger_path.stat().st_size:
        raise ValueError(f"lane lock {display_path(lock_path)} atomic_feature_ledger bytes are inconsistent")
    return lock_ref


def _ledger_rows_by_feature(ledger_path: Path) -> dict[str, Mapping[str, Any]]:
    if not ledger_path.is_file():
        raise FileNotFoundError(f"atomic_feature_ledger does not exist: {display_path(ledger_path)}")
    ledger = load_json(ledger_path)
    rows = ledger.get("rows") if isinstance(ledger, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError(f"atomic_feature_ledger rows must be a list: {display_path(ledger_path)}")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("feature_id"), str) or not row["feature_id"]:
            raise ValueError(f"atomic_feature_ledger contains an invalid row: {display_path(ledger_path)}")
        feature_id = str(row["feature_id"])
        if feature_id in result:
            raise ValueError(f"atomic_feature_ledger contains duplicate feature_id {feature_id!r}")
        result[feature_id] = row
    return result


def _ledger_feature_ids(refs: Any, *, ledger_path: Path) -> list[str]:
    if not isinstance(refs, list) or not refs:
        raise ValueError("support claim ledger_row_refs must be a non-empty list")
    feature_ids: list[str] = []
    for ref in refs:
        if not isinstance(ref, str):
            raise ValueError("support claim ledger_row_refs entries must be strings")
        parts = ref.split("#")
        if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2].startswith("sha256:"):
            raise ValueError(f"invalid support claim ledger row ref: {ref!r}")
        ref_path = _sync_artifact_path(parts[0], role="ledger_row_ref")
        if ref_path != ledger_path:
            raise ValueError(
                "support claim ledger row ref path does not match its atomic_feature_ledger artifact: "
                f"{parts[0]}"
            )
        feature_ids.append(parts[1])
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError("support claim ledger_row_refs contain duplicate feature ids")
    return feature_ids


def _json_payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_inventory_artifact_role_ids(inventory: Mapping[str, Any]) -> None:
    lanes = inventory.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError("lane inventory lanes must be a list")
    owners: dict[str, str] = {}
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ValueError("lane inventory entry must be an object")
        lane_id = lane.get("lane_id")
        artifact_roles = lane.get("artifact_roles")
        if not isinstance(artifact_roles, Mapping):
            raise ValueError(f"lane {lane_id!r} artifact_roles must be an object")
        for role_key, role_id in artifact_roles.items():
            if not isinstance(role_key, str) or not role_key or not isinstance(role_id, str) or not role_id:
                raise ValueError(f"lane {lane_id!r} artifact_roles entries must be non-empty strings")
            owner = f"{lane_id}:{role_key}"
            previous_owner = owners.get(role_id)
            if previous_owner is not None:
                raise ValueError(
                    f"duplicate inventory artifact role_id {role_id!r}: "
                    f"claimed by {previous_owner} and {owner}"
                )
            owners[role_id] = owner


def _sync_support_claim_hash_bindings(inventory_path: Path | str = DEFAULT_INVENTORY_PATH) -> dict[str, int]:
    """Keep each lane's support-claim, evidence-manifest, and node-gate hash refs fresh."""

    inventory = load_json(inventory_path)
    lanes = inventory.get("lanes") if isinstance(inventory, Mapping) else None
    if not isinstance(lanes, list):
        raise ValueError("lane inventory lanes must be a list")
    _validate_inventory_artifact_role_ids(inventory)
    accepted_p0_lanes_skipped_missing_lock = 0
    node_gates_by_manifest = _node_gate_paths_by_evidence_manifest(inventory_path)
    claimed_output_paths: dict[Path, str] = {}
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ValueError("lane inventory entry must be an object")
        artifact_roles = lane.get("artifact_roles")
        if not isinstance(artifact_roles, Mapping):
            raise ValueError(f"lane {lane.get('lane_id')!r} artifact_roles must be an object")
        if artifact_roles.get("atomic_feature_ledger") is None:
            continue
        support_path = _support_claim_path(lane).resolve()
        evidence_path = _evidence_manifest_path(lane).resolve()
        outputs = [
            (support_path, f"{lane.get('lane_id')}:support_claim"),
            (evidence_path, f"{lane.get('lane_id')}:evidence_manifest"),
        ]
        node_gate_path = node_gates_by_manifest.get(display_path(evidence_path))
        if node_gate_path is not None:
            outputs.append((node_gate_path.resolve(), f"{lane.get('lane_id')}:node_gate"))
        for output_path, owner in outputs:
            previous_owner = claimed_output_paths.get(output_path)
            if previous_owner is not None:
                raise ValueError(
                    f"ambiguous binding output path {display_path(output_path)!r}: "
                    f"claimed by {previous_owner} and {owner}"
                )
            claimed_output_paths[output_path] = owner
    plans: list[dict[str, Any]] = []

    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ValueError("lane inventory entry must be an object")
        artifact_roles = lane.get("artifact_roles")
        if not isinstance(artifact_roles, Mapping):
            raise ValueError(f"lane {lane.get('lane_id')!r} artifact_roles must be an object")
        ledger_role_id = artifact_roles.get("atomic_feature_ledger")
        if ledger_role_id is None:
            continue
        if not isinstance(ledger_role_id, str) or not ledger_role_id:
            raise ValueError(f"lane {lane.get('lane_id')!r} atomic_feature_ledger role id is invalid")

        support_path = _support_claim_path(lane).resolve()
        evidence_path = _evidence_manifest_path(lane).resolve()
        # Global output ownership was validated before any lane payload was loaded or mutated.
        if not support_path.is_file():
            raise FileNotFoundError(f"support claim does not exist: {display_path(support_path)}")
        if not evidence_path.is_file():
            raise FileNotFoundError(f"evidence manifest does not exist: {display_path(evidence_path)}")

        support_claim = load_json(support_path)
        evidence_manifest = load_json(evidence_path)
        if not isinstance(support_claim, dict):
            raise ValueError(f"support claim must be an object: {display_path(support_path)}")
        if not isinstance(evidence_manifest, dict):
            raise ValueError(f"evidence manifest must be an object: {display_path(evidence_path)}")
        lane_id = lane.get("lane_id")
        claim_id = lane.get("claim_id")
        config_id = lane.get("config_id")
        if support_claim.get("claim_id") != claim_id or evidence_manifest.get("claim_id") != claim_id:
            raise ValueError(f"lane {lane_id!r} claim_id bindings are inconsistent")
        if evidence_manifest.get("config_id") != config_id:
            raise ValueError(f"lane {lane_id!r} config_id binding is inconsistent")
        if evidence_manifest.get("lane_id") != lane_id:
            raise ValueError(f"lane {lane_id!r} evidence manifest lane_id binding is inconsistent")

        evidence_ref = support_claim.get("evidence_manifest_ref")
        if not isinstance(evidence_ref, str):
            raise ValueError(f"support claim {claim_id!r} evidence_manifest_ref is invalid")
        if _sync_artifact_path(evidence_ref, role="evidence_manifest_ref") != evidence_path:
            raise ValueError(f"support claim {claim_id!r} evidence_manifest_ref does not match its lane")

        support_artifact = _required_artifact_by_role(evidence_manifest, "support_claim_ref")
        support_artifact_path = support_artifact.get("path")
        if not isinstance(support_artifact_path, str) or (
            _sync_artifact_path(support_artifact_path, role="support_claim_ref") != support_path
        ):
            raise ValueError(f"evidence manifest support_claim_ref does not match lane {lane_id!r}")

        ledger_artifact = _required_artifact_by_role(evidence_manifest, "atomic_feature_ledger")
        ledger_ref = ledger_artifact.get("path")
        if not isinstance(ledger_ref, str):
            raise ValueError(f"lane {lane_id!r} atomic_feature_ledger path is invalid")
        ledger_ref = _declared_ledger_ref(lane, ledger_ref)
        if ledger_ref is None:
            accepted_p0_lanes_skipped_missing_lock += 1
            continue
        ledger_path = _sync_artifact_path(ledger_ref, role="atomic_feature_ledger")
        rows_by_feature = _ledger_rows_by_feature(ledger_path)
        feature_ids = _ledger_feature_ids(support_claim.get("ledger_row_refs"), ledger_path=ledger_path)
        declared_feature_ids = lane.get("ledger_feature_ids")
        if not isinstance(declared_feature_ids, list) or feature_ids != declared_feature_ids:
            raise ValueError(f"lane {lane_id!r} ledger feature bindings are inconsistent")
        missing_feature_ids = [feature_id for feature_id in feature_ids if feature_id not in rows_by_feature]
        if missing_feature_ids:
            raise ValueError(f"lane {lane_id!r} ledger is missing declared feature ids: {missing_feature_ids}")

        expected_refs = [
            f"{display_path(ledger_path)}#{feature_id}#{_row_content_hash(feature_id, rows_by_feature[feature_id])}"
            for feature_id in feature_ids
        ]
        support_changed = support_claim["ledger_row_refs"] != expected_refs
        if support_changed:
            support_claim["ledger_row_refs"] = expected_refs
        support_hash = _json_payload_sha256(support_claim) if support_changed else sha256_path(support_path)

        expected_ledger_fields = {
            "sha256": sha256_path(ledger_path),
            "bytes": ledger_path.stat().st_size,
            "exists": True,
        }
        evidence_changed = False
        if support_artifact.get("sha256") != support_hash:
            support_artifact["sha256"] = support_hash
            evidence_changed = True
        for key, value in expected_ledger_fields.items():
            if ledger_artifact.get(key) != value:
                ledger_artifact[key] = value
                evidence_changed = True
        evidence_hash = _json_payload_sha256(evidence_manifest) if evidence_changed else sha256_path(evidence_path)

        node_gate_path = node_gates_by_manifest.get(display_path(evidence_path))
        node_gate: dict[str, Any] | None = None
        node_gate_changed = False
        if node_gate_path is not None and node_gate_path.exists():
            node_gate_payload = load_json(node_gate_path)
            if not isinstance(node_gate_payload, dict):
                raise ValueError(f"node gate must be an object: {display_path(node_gate_path)}")
            hashes = node_gate_payload.setdefault("hashes", {})
            if not isinstance(hashes, dict):
                raise ValueError(f"{display_path(node_gate_path)} hashes must be an object")
            expected_hashes = {"support_claim": support_hash, "evidence_manifest": evidence_hash}
            for key, value in expected_hashes.items():
                if hashes.get(key) != value:
                    hashes[key] = value
                    node_gate_changed = True
            node_gate = node_gate_payload

        plans.append(
            {
                "support_path": support_path,
                "support_claim": support_claim,
                "support_changed": support_changed,
                "evidence_path": evidence_path,
                "evidence_manifest": evidence_manifest,
                "evidence_changed": evidence_changed,
                "node_gate_path": node_gate_path,
                "node_gate": node_gate,
                "node_gate_changed": node_gate_changed,
            }
        )

    for plan in plans:
        if plan["support_changed"]:
            write_json(plan["support_path"], plan["support_claim"])
        if plan["evidence_changed"]:
            write_json(plan["evidence_path"], plan["evidence_manifest"])
        if plan["node_gate_changed"]:
            write_json(plan["node_gate_path"], plan["node_gate"])
    result = _empty_sync_result()
    result.update(
        {
            "support_claims_checked": len(plans),
            "support_claims_changed": sum(bool(plan["support_changed"]) for plan in plans),
            "evidence_manifests_changed": sum(bool(plan["evidence_changed"]) for plan in plans),
            "node_gates_changed": sum(bool(plan["node_gate_changed"]) for plan in plans),
            "accepted_p0_lanes_skipped_missing_lock": accepted_p0_lanes_skipped_missing_lock,
        }
    )
    return result

def _node_gate_path(lane: Mapping[str, Any]) -> str:
    ct = lane.get("ct") if isinstance(lane.get("ct"), Mapping) else {}
    command = ct.get("command") if isinstance(ct.get("command"), Mapping) else {}
    argv = command.get("argv", [])
    if not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)):
        raise ValueError(f"lane {lane.get('lane_id')!r} has no ct.command.argv")
    try:
        index = list(argv).index("--json-out")
    except ValueError as exc:
        raise ValueError(f"lane {lane.get('lane_id')!r} has no --json-out node gate path") from exc
    try:
        value = argv[index + 1]
    except IndexError as exc:
        raise ValueError(f"lane {lane.get('lane_id')!r} --json-out has no value") from exc
    if not isinstance(value, str) or not value:
        raise ValueError(f"lane {lane.get('lane_id')!r} --json-out value is invalid")
    return value


def _support_claims_output_path(
    lane: Mapping[str, Any],
    *,
    identifier_field: str,
    suffix: str,
) -> Path:
    identifier = lane.get(identifier_field)
    if not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", identifier) is None:
        raise ValueError(
            f"lane {lane.get('lane_id')!r} {identifier_field} must be a filename-safe identifier"
        )
    claims_root = (ROOT / "docs" / "conformance" / "support_claims").resolve()
    output_path = (claims_root / f"{identifier}{suffix}").resolve()
    try:
        output_path.relative_to(claims_root)
    except ValueError as exc:
        raise ValueError(
            f"lane {lane.get('lane_id')!r} {identifier_field} escapes the support claims directory"
        ) from exc
    return output_path


def _evidence_manifest_path(lane: Mapping[str, Any]) -> Path:
    return _support_claims_output_path(
        lane,
        identifier_field="config_id",
        suffix="_c4_evidence_manifest.json",
    )


def _support_claim_path(lane: Mapping[str, Any]) -> Path:
    return _support_claims_output_path(lane, identifier_field="claim_id", suffix=".json")


def _manifest_artifact_by_role(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("evidence manifest artifacts must be a list")
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ValueError("evidence manifest artifact must be an object")
        role = artifact.get("role")
        path = artifact.get("path")
        if not isinstance(role, str) or not role:
            raise ValueError("evidence manifest artifact role must be a non-empty string")
        if not isinstance(path, str) or not path:
            raise ValueError(f"evidence manifest artifact {role!r} path must be a non-empty string")
        result[role] = artifact
    return result


def _path_to_role_id(lane: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, str]:
    artifact_roles = lane.get("artifact_roles", {})
    if not isinstance(artifact_roles, Mapping):
        raise ValueError(f"lane {lane.get('lane_id')!r} artifact_roles must be an object")
    by_role = _manifest_artifact_by_role(manifest)
    result: dict[str, str] = {}
    for role_key, role_id in artifact_roles.items():
        if not isinstance(role_key, str) or not isinstance(role_id, str):
            continue
        if role_key == "evidence_manifest":
            result[display_path(_evidence_manifest_path(lane))] = role_id
            continue
        if role_key == "support_claim":
            result[display_path(_support_claim_path(lane))] = role_id
            continue
        if role_key == "node_gate":
            result[display_path(_node_gate_path(lane))] = role_id
            continue
        manifest_role = _ROLE_ALIASES.get(role_key, role_key)
        artifact = by_role.get(manifest_role)
        if artifact is not None:
            result[display_path(_split_ref(str(artifact["path"]))) ] = role_id
    return result


def _derived_from_role_ids(
    artifact: Mapping[str, Any],
    path_role_ids: Mapping[str, str],
) -> list[str]:
    derived_from = artifact.get("derived_from", [])
    if derived_from is None:
        return []
    if not isinstance(derived_from, list):
        raise ValueError(f"derived_from for {artifact.get('path')!r} must be a list")
    result: list[str] = []
    for item in derived_from:
        if not isinstance(item, str) or not item:
            raise ValueError(f"derived_from item for {artifact.get('path')!r} must be a non-empty string")
        display = display_path(_split_ref(item))
        result.append(path_role_ids.get(display, display))
    return sorted(dict.fromkeys(result))


def _entry_from_registered(
    *,
    role_id: str,
    path: str | Path,
    artifact_kind: str,
    lane_id: str | None,
    media_type: str | None,
    derived_from: Iterable[str],
    generated_by: str,
) -> dict[str, Any]:
    display = display_path(_split_ref(str(path)))
    if display.startswith(CHECKOUT_PREFIX):
        raise ValueError(f"catalog path retained checkout prefix: {display}")
    actual = resolve_registered_path(display)
    if not actual.exists():
        raise FileNotFoundError(f"registered artifact does not exist for {role_id}: {display} -> {actual}")
    return {
        "role_id": role_id,
        "path": display,
        "sha256": sha256_path(actual),
        "bytes": actual.stat().st_size,
        "exists": True,
        "artifact_kind": artifact_kind,
        "lane_id": lane_id,
        "media_type": media_type,
        "derived_from": sorted(dict.fromkeys(str(item) for item in derived_from)),
        "generated_by": generated_by,
    }


def _lane_entry(
    *,
    lane: Mapping[str, Any],
    role_key: str,
    role_id: str,
    artifact: Mapping[str, Any],
    path_role_ids: Mapping[str, str],
) -> dict[str, Any]:
    lane_id = lane.get("lane_id")
    if not isinstance(lane_id, str) or not lane_id:
        raise ValueError("lane_id must be a non-empty string")
    builder = lane.get("builder")
    builder_argv = builder.get("argv", []) if isinstance(builder, Mapping) else []
    generated_by = "manual"
    if isinstance(builder_argv, Sequence) and len(builder_argv) > 1 and isinstance(builder_argv[1], str):
        generated_by = display_path(builder_argv[1])
    manifest_role = str(artifact.get("role", _ROLE_ALIASES.get(role_key, role_key)))
    return _entry_from_registered(
        role_id=role_id,
        path=str(artifact["path"]),
        artifact_kind=_ARTIFACT_KIND_BY_ROLE.get(role_key, _ARTIFACT_KIND_BY_ROLE.get(manifest_role, "other")),
        lane_id=lane_id,
        media_type=artifact.get("media_type") if isinstance(artifact.get("media_type"), str) else None,
        derived_from=_derived_from_role_ids(artifact, path_role_ids),
        generated_by=generated_by,
    )


def _lane_entries(
    inventory: Mapping[str, Any],
    external_path_role_ids: Mapping[str, str],
    *,
    excluded_role_keys: Collection[str] = (),
) -> list[dict[str, Any]]:
    lanes = inventory.get("lanes", [])
    if not isinstance(lanes, list):
        raise ValueError("inventory lanes must be a list")
    entries: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ValueError("inventory lane must be an object")
        lane_id = lane.get("lane_id")
        artifact_roles = lane.get("artifact_roles", {})
        if not isinstance(lane_id, str) or not lane_id:
            raise ValueError("inventory lane_id must be a non-empty string")
        if not isinstance(artifact_roles, Mapping):
            raise ValueError(f"lane {lane_id} artifact_roles must be an object")
        manifest_path = _evidence_manifest_path(lane)
        manifest = load_json(manifest_path)
        if not isinstance(manifest, Mapping):
            raise ValueError(f"evidence manifest must be an object: {manifest_path}")
        by_role = _manifest_artifact_by_role(manifest)
        path_role_ids = {**external_path_role_ids, **_path_to_role_id(lane, manifest)}
        for role_key, role_id in artifact_roles.items():
            if role_key in excluded_role_keys:
                continue
            if not isinstance(role_key, str) or not isinstance(role_id, str):
                raise ValueError(f"lane {lane_id} artifact_roles entries must be strings")
            if role_key == "evidence_manifest":
                artifact: Mapping[str, Any] = {
                    "path": display_path(manifest_path),
                    "role": "evidence_manifest",
                    "derived_from": [str(artifact_roles.get("support_claim", ""))] if artifact_roles.get("support_claim") else [],
                }
            elif role_key == "support_claim":
                artifact = {
                    "path": display_path(_support_claim_path(lane)),
                    "role": "support_claim_ref",
                    "derived_from": [],
                }
            elif role_key == "node_gate":
                artifact = {
                    "path": _node_gate_path(lane),
                    "role": "node_gate",
                    "derived_from": [str(artifact_roles.get("evidence_manifest", ""))] if artifact_roles.get("evidence_manifest") else [],
                }
            else:
                manifest_role = _ROLE_ALIASES.get(role_key, role_key)
                artifact = by_role.get(manifest_role)
                if artifact is None:
                    raise ValueError(f"lane {lane_id} role {role_key!r} not found in {manifest_path}")
            entries.append(
                _lane_entry(
                    lane=lane,
                    role_key=role_key,
                    role_id=role_id,
                    artifact=artifact,
                    path_role_ids=path_role_ids,
                )
            )
    return entries


def _excluded_lane_role_ids(
    inventory: Mapping[str, Any],
    excluded_role_keys: Collection[str],
) -> frozenset[str]:
    excluded_keys = set(excluded_role_keys)
    if not excluded_keys:
        return frozenset()
    role_ids: set[str] = set()
    lanes = inventory.get("lanes", [])
    if not isinstance(lanes, list):
        raise ValueError("inventory lanes must be a list")
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ValueError("inventory lane must be an object")
        artifact_roles = lane.get("artifact_roles", {})
        if not isinstance(artifact_roles, Mapping):
            raise ValueError("inventory lane artifact_roles must be an object")
        role_ids.update(
            str(role_id)
            for role_key, role_id in artifact_roles.items()
            if role_key in excluded_keys and isinstance(role_id, str)
        )
    return frozenset(role_ids)


def _prune_entries_derived_from_excluded_roles(
    entries: Sequence[Mapping[str, Any]],
    excluded_role_ids: Collection[str],
) -> list[Mapping[str, Any]]:
    removed = set(excluded_role_ids)
    retained = list(entries)
    while True:
        newly_removed = {
            str(entry["role_id"])
            for entry in retained
            if any(upstream in removed for upstream in entry.get("derived_from", []))
        }
        if not newly_removed:
            return retained
        removed.update(newly_removed)
        retained = [entry for entry in retained if str(entry["role_id"]) not in newly_removed]


def _support_claim_static_role_ids(inventory: Mapping[str, Any]) -> frozenset[str]:
    lanes = inventory.get("lanes", [])
    if not isinstance(lanes, list):
        raise ValueError("inventory lanes must be a list")
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ValueError("inventory lane must be an object")
        artifact_roles = lane.get("artifact_roles", {})
        if not isinstance(artifact_roles, Mapping):
            raise ValueError("inventory lane artifact_roles must be an object")
        feature_ids = lane.get("ledger_feature_ids")
        if (
            isinstance(feature_ids, list)
            and feature_ids
            and "atomic_feature_ledger" not in artifact_roles
        ):
            return frozenset({DEFAULT_ATOMIC_LEDGER_ROLE_ID})
    return frozenset()


def _load_static_roles(report_roles: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    roles = report_roles.get("static_artifact_roles", report_roles.get("roles", []))
    if not isinstance(roles, list):
        raise ValueError("report roles static_artifact_roles must be a list")
    result: list[Mapping[str, Any]] = []
    for role in roles:
        if not isinstance(role, Mapping):
            raise ValueError("static artifact role must be an object")
        result.append(role)
    return result


def _validate_lane_role_mirror(inventory: Mapping[str, Any], report_roles: Mapping[str, Any]) -> None:
    mirrored = report_roles.get("lane_artifact_roles")
    if mirrored is None:
        return
    if not isinstance(mirrored, list):
        raise ValueError("report roles lane_artifact_roles must be a list")
    expected = sorted(
        {
            (str(lane["lane_id"]), str(role_key), str(role_id))
            for lane in inventory.get("lanes", [])
            if isinstance(lane, Mapping) and isinstance(lane.get("artifact_roles"), Mapping)
            for role_key, role_id in lane["artifact_roles"].items()
        }
    )
    actual = sorted(
        {
            (str(item.get("lane_id")), str(item.get("role_key")), str(item.get("role_id")))
            for item in mirrored
            if isinstance(item, Mapping)
        }
    )
    if actual != expected:
        raise ValueError("report roles lane_artifact_roles does not match inventory artifact_roles")


def _static_path_role_ids(report_roles: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for role in _load_static_roles(report_roles):
        role_id = role.get("role_id")
        path = role.get("path")
        if isinstance(role_id, str) and role_id and isinstance(path, str) and path:
            result[display_path(path)] = role_id
    return result


def _static_entries(
    report_roles: Mapping[str, Any],
    *,
    included_role_ids: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for role in _load_static_roles(report_roles):
        if included_role_ids is not None and role.get("role_id") not in included_role_ids:
            continue
        role_id = role.get("role_id")
        path = role.get("path")
        if not isinstance(role_id, str) or not role_id:
            raise ValueError("static artifact role_id must be a non-empty string")
        if not isinstance(path, str) or not path:
            raise ValueError(f"static artifact {role_id!r} path must be a non-empty string")
        derived_from = role.get("derived_from", [])
        if not isinstance(derived_from, list):
            raise ValueError(f"static artifact {role_id!r} derived_from must be a list")
        generated_by = role.get("generated_by", "manual")
        if not isinstance(generated_by, str) or not generated_by:
            raise ValueError(f"static artifact {role_id!r} generated_by must be a non-empty string")
        artifact_kind = role.get("artifact_kind", "report")
        if not isinstance(artifact_kind, str) or not artifact_kind:
            raise ValueError(f"static artifact {role_id!r} artifact_kind must be a non-empty string")
        lane_id = role.get("lane_id")
        if lane_id is not None and not isinstance(lane_id, str):
            raise ValueError(f"static artifact {role_id!r} lane_id must be null or string")
        media_type = role.get("media_type")
        if media_type is not None and not isinstance(media_type, str):
            raise ValueError(f"static artifact {role_id!r} media_type must be null or string")
        entries.append(
            _entry_from_registered(
                role_id=role_id,
                path=path,
                artifact_kind=artifact_kind,
                lane_id=lane_id,
                media_type=media_type,
                derived_from=derived_from,
                generated_by=generated_by,
            )
        )
    return entries

def _referenced_static_role_ids(
    report_roles: Mapping[str, Any],
    lane_entries: Sequence[Mapping[str, Any]],
) -> frozenset[str]:
    """Return the static-role closure required by the selected lane entries.

    Bootstrap catalogs deliberately omit reports produced later in the
    regeneration DAG. They retain only static source/config roles reached
    from the selected lane artifacts, plus the catalog's two governing
    configuration records.
    """

    static_roles = {
        str(role["role_id"]): role
        for role in _load_static_roles(report_roles)
        if isinstance(role.get("role_id"), str) and role["role_id"]
    }
    required = {
        "e4_static:config/e4_lane_inventory",
        "e4_static:config/e4_report_roles",
    } & static_roles.keys()
    required.update(
        str(upstream)
        for entry in lane_entries
        for upstream in entry.get("derived_from", [])
        if upstream in static_roles
    )

    pending = list(required)
    while pending:
        role_id = pending.pop()
        derived_from = static_roles[role_id].get("derived_from", [])
        if not isinstance(derived_from, list):
            raise ValueError(f"static artifact {role_id!r} derived_from must be a list")
        for upstream in derived_from:
            if upstream in static_roles and upstream not in required:
                required.add(str(upstream))
                pending.append(str(upstream))
    return frozenset(required)

def _static_script_roles(report_roles: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return sorted(
        (
            role
            for role in _load_static_roles(report_roles)
            if isinstance(role.get("role_id"), str) and str(role["role_id"]).startswith("e4_static:script/")
        ),
        key=lambda role: str(role["role_id"]),
    )


def _tooling_manifest_entry(
    report_roles: Mapping[str, Any],
    *,
    generated_at_utc: str,
    output_path: Path | None = None,
) -> dict[str, Any] | None:
    output_path = output_path or (ROOT / "docs" / "conformance" / "e4_tooling_manifest.json")
    script_roles = _static_script_roles(report_roles)
    if not script_roles:
        return None

    scripts: list[dict[str, Any]] = []
    for role in script_roles:
        role_id = role.get("role_id")
        path = role.get("path")
        if not isinstance(role_id, str) or not role_id or not isinstance(path, str) or not path:
            raise ValueError("static script role must have non-empty role_id and path")
        actual = resolve_registered_path(path)
        if not actual.exists():
            raise FileNotFoundError(f"registered script artifact does not exist for {role_id}: {display_path(path)} -> {actual}")
        scripts.append(
            {
                "role_id": role_id,
                "path": display_path(path),
                "sha256": sha256_path(actual),
                "bytes": actual.stat().st_size,
            }
        )

    write_json(
        output_path,
        {
            "schema_version": "bb.e4.tooling_manifest.v1",
            "generated_at_utc": generated_at_utc,
            "scripts": scripts,
        },
    )
    return _entry_from_registered(
        role_id="e4_static:report/tooling_manifest_json",
        path=display_path(output_path),
        artifact_kind="report",
        lane_id=None,
        media_type="application/json",
        derived_from=[str(role["role_id"]) for role in script_roles],
        generated_by="scripts/e4_parity/build_artifact_catalog.py",
    )



def _validate_entries(entries: Sequence[Mapping[str, Any]]) -> None:
    seen_role_ids: set[str] = set()
    duplicates: set[str] = set()
    for entry in entries:
        role_id = str(entry.get("role_id"))
        if role_id in seen_role_ids:
            duplicates.add(role_id)
        seen_role_ids.add(role_id)
        path = str(entry.get("path", ""))
        if path.startswith(CHECKOUT_PREFIX):
            raise ValueError(f"catalog path retained checkout prefix: {path}")
    if duplicates:
        raise ValueError(f"duplicate catalog role_id values: {', '.join(sorted(duplicates))}")
    unresolved: list[str] = []
    for entry in entries:
        for upstream in entry.get("derived_from", []):
            if upstream not in seen_role_ids:
                unresolved.append(f"{entry['role_id']}<-{upstream}")
    if unresolved:
        sample = ", ".join(sorted(unresolved)[:5])
        suffix = "" if len(unresolved) <= 5 else f", ... +{len(unresolved) - 5} more"
        raise ValueError(f"catalog derived_from values must be role_ids: {sample}{suffix}")


def _revision_and_timestamp(
    *,
    output_path: Path,
    entries: Sequence[Mapping[str, Any]],
    generated_at_utc: str,
) -> tuple[int, str]:
    """Return the catalog revision and timestamp for the next catalog build.

    The revision is keyed to the stable evidence subset, not the full entries
    array: support claims bind ``catalog_binding.catalog_revision``, so a
    full-entries-keyed counter would recreate the claim<->catalog cycle through
    the revision integer whenever claim-derived entries churn.
    """
    if not output_path.exists():
        return 1, generated_at_utc
    try:
        existing = load_json(output_path)
    except (OSError, json.JSONDecodeError):
        return 1, generated_at_utc
    if not isinstance(existing, Mapping):
        return 1, generated_at_utc

    existing_entries = existing.get("entries")
    if existing_entries == list(entries):
        revision = existing.get("revision", 1)
        timestamp = existing.get("generated_at_utc", generated_at_utc)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            revision = 1
        return revision, str(timestamp)

    revision = existing.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        return 1, generated_at_utc
    if not isinstance(existing_entries, list) or not all(isinstance(entry, Mapping) for entry in existing_entries):
        return 1, generated_at_utc
    try:
        stable_changed = stable_entries_hash(existing_entries) != stable_entries_hash(entries)
    except ValueError:
        return 1, generated_at_utc
    return (revision + 1 if stable_changed else revision), generated_at_utc


def _catalog_schema_version(value: str) -> str:
    if value in {"v2", "bb.e4.artifact_catalog.v2"}:
        return "bb.e4.artifact_catalog.v2"
    raise ValueError(f"artifact catalog generation requires bb.e4.artifact_catalog.v2, got {value!r}")


def _catalog_record(
    *,
    schema_version: str,
    generated_at_utc: str,
    revision: int,
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    entries_list = [dict(entry) for entry in entries]
    entries_hash = sha256_ref(canonical_record_bytes(entries_list))
    stable_hash = stable_entries_hash(entries_list)
    record: dict[str, Any] = {
        "catalog_id": CATALOG_V2_ID,
        "generated_at_utc": generated_at_utc,
        "revision": revision,
        "entries": entries_list,
        "integrity": {
            "entry_count": len(entries_list),
            "entries_hash": entries_hash,
            "stable_entries_hash": stable_hash,
        },
    }
    segments = catalog_segments(entries_list)
    record["segments"] = segments
    record["integrity"]["segments_hash"] = sha256_ref(canonical_record_bytes(segments))
    return record


def build_catalog(
    inventory_path: Path | str = DEFAULT_INVENTORY_PATH,
    report_roles_path: Path | str = DEFAULT_REPORT_ROLES_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    generated_at_utc: str | None = None,
    write_bindings: bool = False,
    schema_version: str = "bb.e4.artifact_catalog.v2",
    excluded_lane_roles: Collection[str] = (),
    referenced_static_only: bool = False,
) -> dict[str, Any]:
    schema_version = _catalog_schema_version(schema_version)
    inventory_file = Path(inventory_path)
    report_roles_file = Path(report_roles_path)
    output_file = Path(output_path)
    inventory = load_json(inventory_file)
    report_roles = load_json(report_roles_file)
    if not isinstance(inventory, Mapping):
        raise ValueError("lane inventory must be an object")
    if not isinstance(report_roles, Mapping):
        raise ValueError("report roles must be an object")
    _validate_inventory_artifact_role_ids(inventory)
    _validate_lane_role_mirror(inventory, report_roles)
    if write_bindings:
        _sync_support_claim_hash_bindings(inventory_path=inventory_file)

    timestamp = generated_at_utc or DEFAULT_GENERATED_AT_UTC
    lane_entries = _lane_entries(
        inventory,
        _static_path_role_ids(report_roles),
        excluded_role_keys=excluded_lane_roles,
    )
    lane_entries = _prune_entries_derived_from_excluded_roles(
        lane_entries,
        _excluded_lane_role_ids(inventory, excluded_lane_roles),
    )
    included_static_role_ids = (
        _referenced_static_role_ids(report_roles, lane_entries)
        | _support_claim_static_role_ids(inventory)
        if referenced_static_only
        else None
    )
    tooling_entry = (
        None if referenced_static_only else _tooling_manifest_entry(report_roles, generated_at_utc=timestamp)
    )
    entries = [
        *lane_entries,
        *_static_entries(report_roles, included_role_ids=included_static_role_ids),
    ]
    if tooling_entry is not None:
        entries.append(tooling_entry)
    entries = sorted(entries, key=lambda entry: str(entry["role_id"]))
    _validate_entries(entries)
    revision, timestamp = _revision_and_timestamp(
        output_path=output_file,
        entries=entries,
        generated_at_utc=timestamp,
    )
    record = _catalog_record(
        schema_version=schema_version,
        generated_at_utc=timestamp,
        revision=revision,
        entries=entries,
    )
    finalized = finalize_record(get_spec(schema_version), record)
    for entry in finalized["entries"]:
        path = entry["path"]
        if path.startswith(CHECKOUT_PREFIX):
            raise ValueError(f"catalog path retained checkout prefix: {path}")
    return finalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the deterministic BreadBoard E4 artifact catalog.")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--report-roles", type=Path, default=DEFAULT_REPORT_ROLES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--generated-at-utc", default=None)
    parser.add_argument("--write-bindings", action="store_true", help="refresh support-claim and evidence-manifest hash refs before cataloging")
    parser.add_argument(
        "--exclude-lane-role",
        action="append",
        default=[],
        help="omit one lane artifact role key; repeat for bootstrap catalogs",
    )
    parser.add_argument(
        "--referenced-static-only",
        action="store_true",
        help="include only static roles reached from selected lane entries and governing catalog configs",
    )
    parser.add_argument("--schema-version", choices=("v2", "bb.e4.artifact_catalog.v2"), default="bb.e4.artifact_catalog.v2")
    parser.add_argument("--json", action="store_true", help="print catalog JSON to stdout instead of writing --output")
    args = parser.parse_args(argv)

    catalog = build_catalog(
        inventory_path=args.inventory,
        report_roles_path=args.report_roles,
        output_path=args.output,
        generated_at_utc=args.generated_at_utc,
        write_bindings=args.write_bindings,
        schema_version=args.schema_version,
        excluded_lane_roles=args.exclude_lane_role,
        referenced_static_only=args.referenced_static_only,
    )
    if args.json:
        print(json.dumps(catalog, indent=2, sort_keys=True))
    else:
        write_json(args.output, catalog)
        print(f"wrote {display_path(args.output)} entries={catalog['integrity']['entry_count']} hash={catalog['integrity']['entries_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
