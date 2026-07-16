#!/usr/bin/env python3
"""Compile an author-owned E4 lane manifest into deterministic machine-owned files."""
from __future__ import annotations

import argparse
import json
import sys
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
try:
    from scripts.e4_parity.path_refs import (
        ReferenceResolutionError,
        resolve_declared_reference,
        workspace_root_for_checkout,
    )
    from scripts.e4_parity.promote_lane_payload_source import (
        extract_payload_source,
        validate_payload_source,
    )
    from scripts.e4_parity.tree_digest import TreeDigest, TreeDigestError, digest_directory
    from scripts.e4_parity.validators.hash_utils import sha256_bytes
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from path_refs import (
        ReferenceResolutionError,
        resolve_declared_reference,
        workspace_root_for_checkout,
    )
    from promote_lane_payload_source import extract_payload_source, validate_payload_source
    from tree_digest import TreeDigest, TreeDigestError, digest_directory
    from validators.hash_utils import sha256_bytes

SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = SOURCE_ROOT
SOURCE_FREEZE_ARCHIVE_REF = (
    "config/e4_lanes/source_freezes/"
    "oh_my_pi_main_5356713e_git_tracked.zip"
)
SOURCE_FREEZE_EXTRACTION_REF = (
    "docs_tmp/phase_20/derived/oh_my_pi_main_5356713e_extracted"
)
PILOT_LANE_ID = "oh_my_pi_p6_6_task_job_subagent"

_PILOT_ARTIFACT_ROLES = {
    "agent_config": "agent_configs/misc/oh_my_pi_p6_6_task_job_subagent_v1.yaml",
    "artifact_catalog": "docs/conformance/e4_artifact_catalog.json",
    "atomic_feature_ledger": "config/e4_lanes/evidence_inputs/oh_my_pi_p6_6_atomic_feature_ledger.v1.json",
    "capture_ref": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/raw_capture_manifest.json",
    "comparator_ref": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/comparator_report.json",
    "detached_subagent_target_capture": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/detached_subagent_target_capture.json",
    "evidence_manifest": "docs/conformance/support_claims/oh_my_pi_p6_6_task_job_subagent_v1_c4_evidence_manifest.json",
    "freeze_manifest": "config/e4_target_freeze_manifest.yaml",
    "joined_subagent_target_capture": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/joined_subagent_target_capture.json",
    "node_gate": "artifacts/conformance/node_gate/ct_p6_oh_my_pi_p66_task_job_subagent_c4_chain.json",
    "parity_results": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/parity_results.json",
    "probe_script": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/oh_my_pi_p6_6_task_job_subagent_probe.mjs",
    "replay_ref": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/bb_replay_result.json",
    "secret_scan_report": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/secret_scan_report.json",
    "support_claim_ref": "docs/conformance/support_claims/oh_my_pi_p6_6_task_job_subagent_v1_c4_support_claim.json",
    "target_probe_output": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/target_probe_output.json",
    "target_setup_report": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/target_setup_and_capture_report.json",
    "task_job_subagent_comparator": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/comparator_report.json",
    "validator_output": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/prevalidation_report.json",
    "work_item_ref": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/work_items.json",
    "work_item_replay": "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent/work_item_replay.json",
}


class ManifestError(ValueError):
    """The manifest or requested compilation mode is invalid."""


class ReferenceError(ValueError):
    """A declared file or registry reference cannot be resolved."""


def canonical_bytes(value: Any, *, newline: bool = True) -> bytes:
    suffix = "\n" if newline else ""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + suffix).encode("utf-8")


def _repo_path(
    value: str | Path,
    *,
    source: Path | None = None,
    checkout_root: Path | None = None,
) -> Path:
    label = f"declared input from {_display(source)}" if source is not None else "declared input"
    try:
        return resolve_declared_reference(
            value,
            checkout_root=checkout_root or ROOT,
            namespace="repo",
            label=label,
            must_exist=False,
        )
    except ReferenceResolutionError as exc:
        raise ReferenceError(str(exc)) from exc


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReferenceError(f"cannot read declared input {_display(path)}: {exc}") from exc
    try:
        value = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ReferenceError(f"declared input {_display(path)} is not valid JSON/YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ReferenceError(f"declared input {_display(path)} must contain an object")
    return value


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = yaml.safe_load(raw)
    except OSError as exc:
        raise ReferenceError(f"cannot read manifest {_display(path)}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest is not valid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError("manifest must contain an object")
    schema_path = _repo_path(
        "contracts/kernel/schemas/bb.e4.lane_manifest.v1.schema.json",
        checkout_root=SOURCE_ROOT,
    )
    schema = _load_mapping(schema_path)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise ManifestError(f"{pointer or '<root>'}: {error.message}")
    return value


def _input_row(path: Path, role: str, *, logical_path: str | None = None) -> dict[str, Any]:
    display = logical_path or _display(path)
    if path.is_dir():
        try:
            tree = digest_directory(path)
        except TreeDigestError as exc:
            raise ReferenceError(f"invalid declared input directory {display}: {exc}") from exc
        return {"path": display, "sha256": tree.digest, "bytes": tree.bytes, "role": role}
    if not path.is_file():
        raise ReferenceError(f"declared input is not a regular file or directory: {display}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReferenceError(f"cannot read declared input {display}: {exc}") from exc
    return {"path": display, "sha256": sha256_bytes(data), "bytes": len(data), "role": role}

def extract_source_archive(archive_path: Path, extraction_path: Path) -> TreeDigest:
    """Safely materialize a canonical regular-file-only ZIP extraction."""
    archive_path = Path(archive_path)
    extraction_path = Path(extraction_path)
    extraction_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{extraction_path.name}.",
            dir=extraction_path.parent,
        )
    )
    try:
        try:
            archive = zipfile.ZipFile(archive_path)
        except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
            raise ReferenceError(f"invalid source-freeze archive {archive_path}: {exc}") from exc
        with archive:
            seen: set[str] = set()
            for info in archive.infolist():
                try:
                    info.filename.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise ReferenceError("source-freeze archive member name is not strict UTF-8") from exc
                name = info.filename
                if not name or name.startswith(("/", "\\")) or "\\" in name:
                    raise ReferenceError(f"unsafe source-freeze archive member path: {name!r}")
                parts = Path(name).parts
                if any(part in ("", ".", "..") for part in parts):
                    raise ReferenceError(f"unsafe source-freeze archive member path: {name!r}")
                normalized = "/".join(parts)
                if normalized in seen:
                    raise ReferenceError(f"duplicate source-freeze archive member: {name!r}")
                seen.add(normalized)
                mode = info.external_attr >> 16
                entry_type = stat.S_IFMT(mode)
                if info.is_dir():
                    if entry_type not in (0, stat.S_IFDIR):
                        raise ReferenceError(f"non-directory archive metadata for {name!r}")
                    target_dir = temporary.joinpath(*parts)
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_dir.chmod(0o755)
                    continue
                if entry_type not in (0, stat.S_IFREG):
                    raise ReferenceError(f"non-regular source-freeze archive member: {name!r}")
                target = temporary.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    data = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise ReferenceError(f"cannot read source-freeze archive member {name!r}: {exc}") from exc
                target.write_bytes(data)
                target.chmod(0o644)
        for directory in sorted(
            (path for path in temporary.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            directory.chmod(0o755)
        tree = digest_directory(temporary)
        if extraction_path.exists():
            try:
                current = digest_directory(extraction_path)
            except TreeDigestError:
                current = None
            if current == tree:
                return tree
            shutil.rmtree(extraction_path, ignore_errors=True)
        temporary.replace(extraction_path)
        return tree
    except (OSError, TreeDigestError) as exc:
        raise ReferenceError(f"cannot materialize source-freeze extraction: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _freeze_row(manifest: Mapping[str, Any], manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    target = manifest["target"]
    reference = target.get("source_freeze_ref")
    if not isinstance(reference, str) or not reference:
        raise ReferenceError("/target/source_freeze_ref must name a freeze manifest")
    path = _repo_path(reference, source=manifest_path)
    freeze = _load_mapping(path)
    rows = freeze.get("e4_configs")
    config_id = str(manifest["config_id"])
    row = rows.get(config_id) if isinstance(rows, Mapping) else None
    if not isinstance(row, Mapping):
        raise ReferenceError(f"freeze manifest {_display(path)} has no e4_configs row {config_id!r}")
    preimage = canonical_bytes({"row_id": config_id, "row": row}, newline=False)
    target_freeze = {"config_id": config_id, "freeze_manifest_row_sha256": sha256_bytes(preimage)}
    return target_freeze, _input_row(path, "target_freeze_manifest", logical_path=reference)


def _registry_pins(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    registry_path = _repo_path("contracts/kernel/registries/e4_adapters.v1.json")
    registry = _load_mapping(registry_path)
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise ReferenceError(f"registry {_display(registry_path)} has no entries array")
    by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, Mapping)}
    selected = [manifest["capture"].get("adapter"), manifest["normalize"].get("translator"), manifest["compare"].get("comparator")]
    pins: list[dict[str, str]] = []
    for entry_id in selected:
        if entry_id is None:
            continue
        entry = by_id.get(entry_id)
        if not isinstance(entry, Mapping) or entry.get("status") != "active":
            raise ReferenceError(f"active e4_adapters registry entry not found: {entry_id!r}")
        pins.append({
            "registry": "e4_adapters",
            "entry_id": str(entry_id),
            "entry_sha256": sha256_bytes(canonical_bytes(entry, newline=False)),
        })
    return sorted(pins, key=lambda pin: (pin["registry"], pin["entry_id"]))




def _derived_reference_path(reference: str) -> Path:
    try:
        return resolve_declared_reference(
            reference,
            checkout_root=ROOT,
            namespace="workspace_evidence",
            label="derived extraction",
            workspace_root=workspace_root_for_checkout(ROOT),
            must_exist=False,
        )
    except ReferenceResolutionError as exc:
        raise ReferenceError(str(exc)) from exc


def _resolved_input_rows(reference: str, manifest_path: Path) -> list[dict[str, Any]]:
    path = _repo_path(reference, source=manifest_path)
    if reference.endswith(".payloads.yaml"):
        return [_input_row(path, "payload_source", logical_path=reference)]
    if reference != SOURCE_FREEZE_ARCHIVE_REF:
        return [_input_row(path, "capture_input", logical_path=reference)]
    archive_row = _input_row(path, "source_freeze_archive", logical_path=reference)
    extraction_path = _derived_reference_path(SOURCE_FREEZE_EXTRACTION_REF)
    tree = extract_source_archive(path, extraction_path)
    extraction_row = {
        "path": SOURCE_FREEZE_EXTRACTION_REF,
        "sha256": tree.digest,
        "bytes": tree.bytes,
        "role": "source_freeze_extraction",
    }
    return [archive_row, extraction_row]


def materialize_manifest_inputs(manifest_path: Path) -> None:
    """Materialize clean-checkout runtime inputs declared by a lane manifest."""
    manifest = _load_manifest(manifest_path)
    for value in manifest["capture"].get("inputs", []):
        reference = str(value)
        if reference == SOURCE_FREEZE_ARCHIVE_REF:
            _resolved_input_rows(reference, manifest_path)


def _payload_source(
    manifest: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[dict[str, Any], Path]:
    references = [
        str(value)
        for value in manifest["capture"].get("inputs", [])
        if str(value).endswith(".payloads.yaml")
    ]
    if len(references) != 1:
        raise ManifestError("manifest must declare exactly one .payloads.yaml input")
    reference = references[0]
    path = _repo_path(reference, source=manifest_path)
    try:
        value = validate_payload_source(_load_mapping(path), source=path)
    except ValueError as exc:
        raise ReferenceError(str(exc)) from exc
    return value, path


def _packet_constants(value: Mapping[str, Any]) -> Mapping[str, Any] | None:
    nested = value.get("packet_constants")
    if isinstance(nested, Mapping):
        return nested
    if "payload_templates" in value or "substitutions" in value:
        return value
    return None


def _merge_generated(
    target: dict[str, Any],
    source: Any,
    *,
    field: str,
    input_path: Path,
) -> None:
    if source is None:
        return
    if not isinstance(source, Mapping):
        raise ReferenceError(f"{_display(input_path)} {field} must be an object")
    duplicates = sorted(set(target).intersection(str(key) for key in source))
    if duplicates:
        raise ReferenceError(
            f"duplicate {field} keys in {_display(input_path)}: "
            + ", ".join(duplicates)
        )
    target.update((str(key), value) for key, value in source.items())


def _generic_compile_sidecar(
    manifest: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload_templates: dict[str, Any] = {}
    substitutions: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for reference_value in manifest["capture"].get("inputs", []):
        reference = str(reference_value)
        path = _repo_path(reference, source=manifest_path)
        rows.extend(_resolved_input_rows(reference, manifest_path))
        if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            continue
        constants = _packet_constants(_load_mapping(path))
        if constants is None:
            continue
        _merge_generated(
            payload_templates,
            constants.get("payload_templates"),
            field="payload_templates",
            input_path=path,
        )
        _merge_generated(
            substitutions,
            constants.get("substitutions"),
            field="substitutions",
            input_path=path,
        )
    return {
        "payload_templates": payload_templates,
        "substitutions": substitutions,
    }, rows


def _migrate_legacy_sidecar(
    legacy_path: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    legacy = _load_mapping(legacy_path)
    normalize = legacy.get("normalize")
    config = normalize.get("config") if isinstance(normalize, Mapping) else None
    constants = config.get("packet_constants") if isinstance(config, Mapping) else None
    if not isinstance(constants, Mapping):
        raise ReferenceError(
            "legacy descriptor has no /normalize/config/packet_constants object"
        )
    sidecar = validate_payload_source(
        {
            "payload_templates": constants.get("payload_templates"),
            "substitutions": constants.get("substitutions"),
        },
        source=legacy_path,
    )
    roles = config.get("roles", {}) if isinstance(config, Mapping) else {}
    if not isinstance(roles, Mapping) or not all(
        isinstance(value, str)
        for value in roles.values()
    ):
        raise ReferenceError(
            "legacy /normalize/config/roles must map role ids to paths"
        )
    artifact_roles = {
        str(role): {"path": str(path), "sha256": None, "bytes": None}
        for role, path in sorted(roles.items())
    }
    return sidecar, artifact_roles


def _compile_sidecar(
    manifest: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if manifest.get("lane_id") != PILOT_LANE_ID:
        return _generic_compile_sidecar(manifest, manifest_path)
    sidecar, _ = _payload_source(manifest, manifest_path)
    rows: list[dict[str, Any]] = []
    for reference_value in manifest["capture"].get("inputs", []):
        rows.extend(_resolved_input_rows(str(reference_value), manifest_path))
    return sidecar, rows


def _artifact_roles(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if manifest.get("lane_id") != PILOT_LANE_ID:
        raise ManifestError(
            "artifact-role derivation is not declared for lane "
            f"{manifest.get('lane_id')!r}"
        )
    return {
        role: {"path": path, "sha256": None, "bytes": None}
        for role, path in sorted(_PILOT_ARTIFACT_ROLES.items())
    }


def _assert_migration_equivalence(
    legacy_path: Path,
    promoted: Mapping[str, Any],
) -> None:
    try:
        legacy_payloads = extract_payload_source(legacy_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ReferenceError(str(exc)) from exc
    if legacy_payloads != promoted:
        raise ReferenceError(
            "promoted payload source does not match legacy "
            "payload_templates/substitutions"
        )


def build_outputs(
    mode: str,
    manifest_path: Path,
    *,
    legacy_path: Path | None = None,
    sidecar_path: Path,
) -> tuple[bytes, bytes]:
    manifest = _load_manifest(manifest_path)
    target_freeze, freeze_input = _freeze_row(manifest, manifest_path)
    if mode == "migrate":
        if legacy_path is None:
            raise ManifestError("migrate mode requires --legacy")
        if manifest.get("lane_id") == PILOT_LANE_ID:
            sidecar, input_rows = _compile_sidecar(manifest, manifest_path)
            artifact_roles = _artifact_roles(manifest)
            _assert_migration_equivalence(legacy_path, sidecar)
        else:
            sidecar, artifact_roles = _migrate_legacy_sidecar(legacy_path)
            input_rows = [_input_row(legacy_path, "legacy_lane_descriptor")]
            for reference_value in manifest["capture"].get("inputs", []):
                input_rows.extend(
                    _resolved_input_rows(str(reference_value), manifest_path)
                )
    elif mode == "compile":
        if legacy_path is not None:
            raise ManifestError("compile mode never accepts or reads --legacy")
        sidecar, input_rows = _compile_sidecar(manifest, manifest_path)
        artifact_roles = (
            _artifact_roles(manifest)
            if manifest.get("lane_id") == PILOT_LANE_ID
            else {}
        )
    else:
        raise ManifestError(f"unsupported mode: {mode}")
    input_rows.append(freeze_input)
    unique_rows = {row["path"]: row for row in input_rows}
    sidecar_data = canonical_bytes(sidecar)
    lock = {
        "schema_version": "bb.e4.lane_lock.v1",
        "lock_format": "canonical-json-v1",
        "lane_id": str(manifest["lane_id"]),
        "manifest_ref": _display(manifest_path),
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "target_freeze": target_freeze,
        "resolved_inputs": sorted(unique_rows.values(), key=lambda row: (row["path"], row["role"])),
        "artifact_roles": artifact_roles,
        "packet_constants_ref": {"path": _display(sidecar_path), "sha256": sha256_bytes(sidecar_data)},
        "registry_pins": _registry_pins(manifest),
    }
    lock_schema_path = _repo_path(
        "contracts/kernel/schemas/bb.e4.lane_lock.v1.schema.json",
        checkout_root=SOURCE_ROOT,
    )
    lock_schema = _load_mapping(lock_schema_path)
    errors = list(Draft202012Validator(lock_schema).iter_errors(lock))
    if errors:
        raise ManifestError(f"generated lock violates schema: {errors[0].message}")
    return canonical_bytes(lock), sidecar_data


def _default_paths(manifest_path: Path, lane_id: str) -> tuple[Path, Path]:
    return (
        manifest_path.with_name(f"{lane_id}.lock.json"),
        manifest_path.with_name(f"{lane_id}.packet_constants.v1.json"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("migrate", "compile"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        manifest_path = args.manifest if args.manifest.is_absolute() else _repo_path(args.manifest)
        manifest = _load_manifest(manifest_path)
        default_lock, default_sidecar = _default_paths(manifest_path, str(manifest["lane_id"]))
        lock_path = args.lock or default_lock
        sidecar_path = args.sidecar or default_sidecar
        legacy_path = None if args.legacy is None else (args.legacy if args.legacy.is_absolute() else _repo_path(args.legacy))
        lock_data, sidecar_data = build_outputs(
            args.mode,
            manifest_path,
            legacy_path=legacy_path,
            sidecar_path=sidecar_path,
        )
        if args.check:
            return 0 if lock_path.is_file() and sidecar_path.is_file() and lock_path.read_bytes() == lock_data and sidecar_path.read_bytes() == sidecar_data else 5
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_bytes(sidecar_data)
        lock_path.write_bytes(lock_data)
        return 0
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ReferenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
