#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, RefResolver
try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils


if str(ROOT := Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(ROOT))
from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec
from agentic_coder_prototype.conformance.catalog_binding import (
    CATALOG_PATH as CATALOG_BINDING_PATH,
    catalog_segment_hash,
    stable_entries_hash,
)

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
CATALOG_PATH = ROOT / "docs" / "conformance" / "e4_artifact_catalog.json"
CLAIM_SCHEMA_PATHS = {
    "bb.e4.support_claim.v2": ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.support_claim.v2.schema.json",
    "bb.e4.support_claim.v3": ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.support_claim.v3.schema.json",
    "bb.e4.support_claim.v4": ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.support_claim.v4.schema.json",
}
CLAIM_SCHEMA_PATH = CLAIM_SCHEMA_PATHS["bb.e4.support_claim.v2"]
CLAIM_SCHEMA_VERSION = "bb.e4.support_claim.v2"
CLAIM_SCHEMA_VERSION_V3 = "bb.e4.support_claim.v3"
CLAIM_SCHEMA_VERSION_V4 = "bb.e4.support_claim.v4"
WSJ_PHASE_LABEL = "WS-J"
WSJ_LANE_IDS = {
    "breadboard_self_runtime_records_v1",
    "claude_code_north_star_capture_v1",
    "opencode_north_star_capture_v1",
}
COMMON_SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.kernel.common.v1.schema.json"
E4_COMMON_SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.common.v1.schema.json"
SUPPORT_CLAIMS_DIR = ROOT / "docs" / "conformance" / "support_claims"
ARCHIVE_DIR = SUPPORT_CLAIMS_DIR / "v1_archive"
GENERATED_AT = "2026-07-04T00:00:00Z"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_path(path: Path) -> str:
    return _hash_utils.sha256_path(path)


def display(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        try:
            return str(resolved.relative_to(WORKSPACE))
        except ValueError:
            return str(resolved)


def resolve_ref(ref: str) -> Path:
    raw = ref.split("#", 1)[0]
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    if raw.startswith("docs_tmp/") or raw.startswith(f"{ROOT.name}/"):
        return (WORKSPACE / raw).resolve()
    return (ROOT / raw).resolve()


def ref(path: Path) -> str:
    return f"{display(path)}#{sha256_path(path)}"


def row_ref(path: Path, row_id: str, digest: str) -> str:
    return f"{display(path)}#{row_id}#{digest}"

def _ref_digest(ref_text: str) -> str | None:
    for part in ref_text.split("#"):
        if part.startswith("sha256:"):
            return part
    return None



def _argv_value(argv: Sequence[Any], flag: str) -> str:
    for index, item in enumerate(argv[:-1]):
        if item == flag and isinstance(argv[index + 1], str):
            return argv[index + 1]
    raise ValueError(f"missing {flag} in argv")


def support_paths(lane: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    command = lane.get("ct", {}).get("command", {}) if isinstance(lane.get("ct"), Mapping) else {}
    argv = command.get("argv") if isinstance(command, Mapping) else None
    if not isinstance(argv, list):
        raise ValueError(f"lane {lane.get('lane_id')} missing ct command argv")
    support_claim_path = resolve_ref(_argv_value(argv, "--support-claim"))
    evidence_manifest_path = resolve_ref(_argv_value(argv, "--evidence-manifest"))
    node_gate_path = resolve_ref(_argv_value(argv, "--json-out"))
    return support_claim_path, evidence_manifest_path, node_gate_path


def _claim_generation_key(lane: Mapping[str, Any]) -> tuple[str, str, str, str]:
    claim_path, manifest_path, node_gate_path = support_paths(lane)
    return (display(manifest_path), display(claim_path), display(node_gate_path), str(lane.get("lane_id", "")))


def _catalog_binding_v2(catalog: Mapping[str, Any]) -> dict[str, Any]:
    revision = catalog.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("artifact catalog revision must be an int >= 1 for support-claim catalog_binding")
    integrity = catalog.get("integrity")
    stable_hash = integrity.get("stable_entries_hash") if isinstance(integrity, Mapping) else None
    if not isinstance(stable_hash, str) or not stable_hash.startswith("sha256:"):
        raise ValueError("artifact catalog integrity.stable_entries_hash is required for support-claim catalog_binding")
    return {
        "catalog_path": CATALOG_BINDING_PATH,
        "catalog_revision": revision,
        "catalog_hash": stable_hash,
    }


def _catalog_binding_v3(catalog: Mapping[str, Any], lane_id: str) -> dict[str, Any]:
    schema_version = catalog.get("schema_version")
    if schema_version == "bb.e4.artifact_catalog.v2":
        segment_hash = catalog_segment_hash(catalog, lane_id)
        shared_segment_hash = catalog_segment_hash(catalog, "shared")
    else:
        entries = catalog.get("entries")
        if not isinstance(entries, list):
            raise ValueError("artifact catalog entries must be a list for support-claim v3 segment binding")
        segment_hash = stable_entries_hash(
            [entry for entry in entries if isinstance(entry, Mapping) and entry.get("lane_id") == lane_id]
        )
        shared_segment_hash = stable_entries_hash(
            [entry for entry in entries if isinstance(entry, Mapping) and not entry.get("lane_id")]
        )
    return {
        "catalog_path": CATALOG_BINDING_PATH,
        "segment_id": lane_id,
        "segment_hash": segment_hash,
        "shared_segment_hash": shared_segment_hash,
    }


def _claim_schema_version(lane: Mapping[str, Any]) -> str:
    requested = lane.get("support_claim_schema_version")
    if isinstance(requested, str) and requested:
        return requested
    if lane.get("phase") == WSJ_PHASE_LABEL or lane.get("lane_id") in WSJ_LANE_IDS:
        return CLAIM_SCHEMA_VERSION_V3
    return CLAIM_SCHEMA_VERSION


def _catalog_binding(lane: Mapping[str, Any] | None = None) -> dict[str, Any]:
    catalog = load_json(Path(CATALOG_PATH))
    if lane is not None and _claim_schema_version(lane) in {CLAIM_SCHEMA_VERSION_V3, CLAIM_SCHEMA_VERSION_V4}:
        binding = _catalog_binding_v3(catalog, str(lane["lane_id"]))
        if _claim_schema_version(lane) == CLAIM_SCHEMA_VERSION_V4:
            revision = catalog.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise ValueError("artifact catalog revision must be an int >= 1 for support-claim v4 catalog_binding")
            binding["catalog_revision"] = revision
        return binding
    return _catalog_binding_v2(catalog)


def _schema_validators() -> dict[str, Draft202012Validator]:
    common = load_json(COMMON_SCHEMA_PATH)
    e4_common = load_json(E4_COMMON_SCHEMA_PATH)
    validators: dict[str, Draft202012Validator] = {}
    for version, schema_path in CLAIM_SCHEMA_PATHS.items():
        schema = load_json(schema_path)
        store = {
            schema["$id"]: schema,
            schema_path.name: schema,
            common["$id"]: common,
            COMMON_SCHEMA_PATH.name: common,
            e4_common["$id"]: e4_common,
            E4_COMMON_SCHEMA_PATH.name: e4_common,
        }
        validators[version] = Draft202012Validator(schema, resolver=RefResolver(base_uri=schema_path.as_uri(), referrer=schema, store=store))
    return validators


def _assertion_identity(assertion: Mapping[str, Any]) -> str:
    value = assertion.get("assertion_id") or assertion.get("name")
    return str(value) if value else ""


def _identifier(value: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    ident = re.sub(r"_+", "_", ident).strip("_")
    return ident or "behavior"


def _behavior_family(lane: Mapping[str, Any]) -> str:
    primitive = " ".join(str(item) for item in lane.get("primitives", []))
    lane_id = str(lane.get("lane_id", ""))
    text = f"{primitive} {lane_id}"
    if "effective_config_graph" in text:
        return "config_graph"
    if "context_resource_pack" in text or "context" in text:
        return "context_pack"
    if "capability_registry" in text or "tool_surface" in text:
        return "capability_surface"
    if "tool_execution" in text:
        return "tool_execution"
    if "command_network" in text or "provider_policy" in text:
        return "command_network_policy"
    if "protocol" in text:
        return "protocol_sessions"
    if "resource" in text:
        return "resource_access"
    if "provider" in text:
        return "provider_routing"
    if "memory" in text:
        return "memory_compaction"
    if "work_item" in text or "task_job" in text:
        return "work_items"
    if "extension_hook" in text:
        return "side_effects"
    if "projection" in text or "tui" in text:
        return "projection_ui"
    if "runtime_records" in text:
        return "replay_session_from_records"
    if "capture" in text or "replay" in text:
        return "replay_capture"
    if "session" in text:
        return "session_persistence"
    return "other"


def _exclusion_facets(exclusions: Sequence[Any], target_family: str) -> dict[str, Any]:
    text = "\n".join(str(item).lower() for item in exclusions)
    classes = {"broad_target_parity"}
    if "write" in text:
        classes.add("write_enabled")
    if "provider" in text or "authenticated" in text:
        classes.add("provider_authenticated")
    if "network" in text:
        classes.add("network")
    if "browser" in text:
        classes.add("browser")
    if "mcp" in text:
        classes.add("mcp")
    if "model" in text or "inference" in text:
        classes.add("model_inference")
    if "ui" in text or "tui" in text:
        classes.add("ui_parity")
    if "danger" in text or "full-access" in text:
        classes.add("danger_full_access")
    if "final-readiness" in text or "final readiness" in text:
        classes.add("final_readiness")
    excluded_families = ["all_other_families"]
    for family in ("pi", "oh_my_pi", "claude_code", "codex", "opencode", "oh_my_opencode", "breadboard"):
        if family != target_family and family in text:
            excluded_families.append(family)
    return {
        "excluded_families": sorted(set(excluded_families)),
        "excluded_behavior_classes": sorted(classes),
    }


def _asserted_behaviors(lane: Mapping[str, Any], comparator: Mapping[str, Any]) -> list[dict[str, Any]]:
    behavior_family = _behavior_family(lane)
    behaviors: list[dict[str, Any]] = []
    for assertion in comparator.get("assertions", []):
        if not isinstance(assertion, Mapping) or assertion.get("status") != "passed":
            continue
        identity = _assertion_identity(assertion)
        if not identity:
            continue
        detail = assertion.get("detail") or assertion.get("description") or identity.replace("_", " ")
        behaviors.append(
            {
                "behavior_id": _identifier(f"{behavior_family}_{identity}"),
                "description": str(detail),
                "comparator_assertion_ids": [identity],
            }
        )
    if not behaviors:
        raise ValueError(f"lane {lane.get('lane_id')} has no passed comparator assertions")
    return behaviors


def _archive_v1(path: Path, payload: Mapping[str, Any]) -> str | None:
    archive_path = ARCHIVE_DIR / path.name
    if archive_path.exists():
        return display(archive_path)
    if payload.get("schema_version") != "bb.e4.support_claim.v1":
        return None
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, archive_path)
    return display(archive_path)


def _updated_manifest(path: Path, claim_path: Path, claim: Mapping[str, Any]) -> None:
    manifest = load_json(path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError(f"{display(path)} artifacts must be a list")
    found = False
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        role = artifact.get("role")
        if role == "freeze_manifest":
            artifact_path = artifact.get("path")
            if not isinstance(artifact_path, str) or not artifact_path:
                raise ValueError(f"{display(path)} freeze_manifest artifact missing path")
            resolved = resolve_ref(artifact_path)
            if not resolved.is_file():
                raise ValueError(f"{display(path)} freeze_manifest artifact missing file: {artifact_path}")
            freeze_ref = str(claim.get("freeze_ref", ""))
            freeze_ref_path = resolve_ref(freeze_ref)
            if freeze_ref_path != resolved:
                raise ValueError(f"{display(path)} freeze_manifest artifact path does not match claim freeze_ref")
            freeze_digest = _ref_digest(freeze_ref)
            if freeze_digest is None:
                raise ValueError(f"{display(path)} claim freeze_ref missing sha256 row digest")
            artifact["sha256"] = freeze_digest
            continue
        artifact_path = artifact.get("path")
        if isinstance(artifact_path, str) and artifact_path:
            resolved = resolve_ref(artifact_path)
            if resolved.is_file():
                artifact["sha256"] = sha256_path(resolved)
        if role == "support_claim_ref":
            artifact["path"] = display(claim_path)
            artifact["sha256"] = sha256_path(claim_path)
            found = True
    if not found:
        artifacts.append({"path": display(claim_path), "role": "support_claim_ref", "sha256": sha256_path(claim_path)})
    write_json(path, manifest)

def _updated_node_gate(path: Path, claim_path: Path, manifest_path: Path) -> None:
    node_gate = load_json(path)
    hashes = node_gate.setdefault("hashes", {})
    if not isinstance(hashes, dict):
        raise ValueError(f"{display(path)} hashes must be an object")
    hashes["support_claim"] = sha256_path(claim_path)
    hashes["evidence_manifest"] = sha256_path(manifest_path)
    refs = node_gate.setdefault("refs", {})
    if isinstance(refs, dict):
        refs["support_claim"] = display(claim_path)
        refs["evidence_manifest"] = display(manifest_path)
    node_gate["support_claim"] = display(claim_path)
    node_gate["evidence_manifest"] = display(manifest_path)
    write_json(path, node_gate)



def _claim_for_lane(lane: Mapping[str, Any], claim_path: Path, manifest_path: Path) -> tuple[dict[str, Any], str | None]:
    prior = load_json(claim_path)
    legacy_prior = prior
    archive_candidate = ARCHIVE_DIR / claim_path.name
    if prior.get("schema_version") == "bb.e4.support_claim.v2" and archive_candidate.exists():
        legacy_prior = load_json(archive_candidate)
    archive_ref = _archive_v1(claim_path, prior)
    comparator_path = resolve_ref(str(prior["comparator_ref"]))
    comparator = load_json(comparator_path)
    exclusions = list(prior.get("exclusions") or ["No broad target-parity claim is made by this exact lane claim."])
    target_family = str(lane["target_family"])
    claim_id = str(lane["claim_id"])
    schema_version = _claim_schema_version(lane)
    scope = {
        "config_id": str(lane["config_id"]),
        "lane_id": str(lane["lane_id"]),
        "run_id": str(lane["run_id"]),
        "target_version": str(lane["target_version"]),
        "provider_model": str(lane["provider_model"]),
        "sandbox_mode": str(lane["sandbox_mode"]),
    }
    if schema_version == CLAIM_SCHEMA_VERSION_V4:
        scope["target_family"] = target_family
    claim: dict[str, Any] = {
        "schema_version": schema_version,
        "claim_id": claim_id,
        "kind": str(lane["kind"]),
        "accepted": bool(prior.get("accepted", True)),
        "summary": str(prior.get("summary") or f"{claim_id} is accepted for its exact captured lane only."),
        "acceptance_rationale": str(prior.get("acceptance_rationale") or "Generated from accepted v1 support claim, inventory scope, catalog refs, and passed comparator assertions."),
        "phase_label": str(prior.get("phase_label") or prior.get("phase") or lane.get("phase") or "E4"),
        "scope": scope,
        "exclusions": exclusions,
        "exclusion_facets": _exclusion_facets(exclusions, target_family),
        "claim_semantics": {
            "asserted_behaviors": _asserted_behaviors(lane, comparator),
            "excluded_behaviors": [
                {"behavior_id": "broad_target_parity", "description": "Broad target-family parity remains outside this exact C4 lane claim."}
            ],
        },
        "freeze_ref": str(prior["freeze_ref"]),
        "capture_ref": str(prior["capture_ref"]),
        "replay_ref": str(prior["replay_ref"]),
        "comparator_ref": str(prior["comparator_ref"]),
        "evidence_manifest_ref": display(manifest_path),
        "ledger_row_refs": list(prior["ledger_row_refs"]),
        "validation_refs": list(prior["validation_refs"]),
        "catalog_binding": _catalog_binding(lane),
        "reverify_command": lane.get("reverify_command") or lane.get("ct", {}).get("command"),
        "generated_at_utc": GENERATED_AT,
    }
    if schema_version != CLAIM_SCHEMA_VERSION_V4:
        claim.update(
            {
                "config_id": str(lane["config_id"]),
                "lane_id": str(lane["lane_id"]),
                "target_family": target_family,
                "target_version": str(lane["target_version"]),
                "run_id": str(lane["run_id"]),
                "provider_model": str(lane["provider_model"]),
                "sandbox_mode": str(lane["sandbox_mode"]),
            }
        )
    if prior.get("raw_source_ref"):
        claim["raw_source_ref"] = prior["raw_source_ref"]
    if prior.get("source_freeze_ref"):
        claim["source_freeze_ref"] = prior["source_freeze_ref"]
    if prior.get("parity_results_ref"):
        claim["parity_results_ref"] = prior["parity_results_ref"]
    if prior.get("secret_scan_ref"):
        claim["secret_scan_ref"] = prior["secret_scan_ref"]
    prior_scope = legacy_prior.get("scope") if isinstance(legacy_prior.get("scope"), Mapping) else {}
    metadata = {"generated_from_schema_version": str(prior.get("schema_version", "unknown"))}
    legacy_scope = {
        str(key): value
        for key, value in prior_scope.items()
        if key not in scope and isinstance(key, str)
    }
    if legacy_scope:
        metadata["legacy_scope"] = legacy_scope
    if archive_ref:
        metadata["v1_archive_ref"] = archive_ref
    claim["metadata"] = metadata
    claim = finalize_record(get_spec(schema_version), claim)
    return claim, archive_ref


def generate(*, dry_run: bool = False) -> dict[str, Any]:
    inventory = load_json(INVENTORY_PATH)
    lanes = sorted(
        (lane for lane in inventory.get("lanes", []) if isinstance(lane, Mapping) and lane.get("status") == "accepted"),
        key=_claim_generation_key,
    )
    validators = _schema_validators()
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        claim_path, manifest_path, node_gate_path = support_paths(lane)
        claim, archive_ref = _claim_for_lane(lane, claim_path, manifest_path)
        validator = validators.get(str(claim["schema_version"]))
        if validator is None:
            raise ValueError(f"claim {claim['claim_id']} requested unsupported schema {claim['schema_version']}")
        errors = sorted(validator.iter_errors(claim), key=lambda error: (list(error.absolute_path), error.message))
        if errors:
            joined = "; ".join(f"{list(error.absolute_path)}: {error.message}" for error in errors)
            raise ValueError(f"claim {claim['claim_id']} failed {claim['schema_version']} schema: {joined}")
        if not dry_run:
            write_json(claim_path, claim)
            _updated_manifest(manifest_path, claim_path, claim)
            _updated_node_gate(node_gate_path, claim_path, manifest_path)
        rows.append(
            {
                "claim_id": claim["claim_id"],
                "claim_path": display(claim_path),
                "evidence_manifest_path": display(manifest_path),
                "node_gate_path": display(node_gate_path),
                "asserted_behavior_count": len(claim["claim_semantics"]["asserted_behaviors"]),
                "archive_ref": archive_ref,
            }
        )
    return {
        "schema_version": "bb.e4.support_claim_generation_report.v1",
        "generated_at_utc": GENERATED_AT,
        "claim_count": len(rows),
        "rows": rows,
        "dry_run": dry_run,
        "ok": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate E4 support_claim.v2/v3 files from inventory, catalog, and comparator reports.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = generate(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"generated {report['claim_count']} support_claim records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
