#!/usr/bin/env python3
"""Compile an author-owned E4 lane manifest into deterministic machine-owned files."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = SOURCE_ROOT


class ManifestError(ValueError):
    """The manifest or requested compilation mode is invalid."""


class ReferenceError(ValueError):
    """A declared file or registry reference cannot be resolved."""


def canonical_bytes(value: Any, *, newline: bool = True) -> bytes:
    suffix = "\n" if newline else ""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + suffix).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _repo_path(value: str | Path, *, source: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    rooted = ROOT / path
    if rooted.exists() or source is None:
        return rooted
    sibling = source.parent / path
    return sibling if sibling.exists() else rooted


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
    schema = json.loads((SOURCE_ROOT / "contracts/kernel/schemas/bb.e4.lane_manifest.v1.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise ManifestError(f"{pointer or '<root>'}: {error.message}")
    return value


def _input_row(path: Path, role: str) -> dict[str, Any]:
    if not path.is_file():
        raise ReferenceError(f"declared input is not a file: {_display(path)}")
    data = path.read_bytes()
    return {"path": _display(path), "sha256": digest_bytes(data), "bytes": len(data), "role": role}


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
    target_freeze = {"config_id": config_id, "freeze_manifest_row_sha256": digest_bytes(preimage)}
    return target_freeze, _input_row(path, "target_freeze_manifest")


def _registry_pins(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    registry_path = ROOT / "contracts/kernel/registries/e4_adapters.v1.json"
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
            "entry_sha256": digest_bytes(canonical_bytes(entry, newline=False)),
        })
    return sorted(pins, key=lambda pin: (pin["registry"], pin["entry_id"]))


def _packet_constants(value: Mapping[str, Any]) -> Mapping[str, Any] | None:
    nested = value.get("packet_constants")
    if isinstance(nested, Mapping):
        return nested
    if "payload_templates" in value or "substitutions" in value:
        return value
    return None


def _merge_generated(target: dict[str, Any], source: Any, *, field: str, input_path: Path) -> None:
    if source is None:
        return
    if not isinstance(source, Mapping):
        raise ReferenceError(f"{_display(input_path)} {field} must be an object")
    duplicates = sorted(set(target).intersection(str(key) for key in source))
    if duplicates:
        raise ReferenceError(f"duplicate {field} keys in {_display(input_path)}: {', '.join(duplicates)}")
    target.update((str(key), value) for key, value in source.items())


def _compile_sidecar(manifest: Mapping[str, Any], manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload_templates: dict[str, Any] = {}
    substitutions: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for reference in manifest["capture"].get("inputs", []):
        path = _repo_path(str(reference), source=manifest_path)
        rows.append(_input_row(path, "capture_input"))
        if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            continue
        constants = _packet_constants(_load_mapping(path))
        if constants is None:
            continue
        _merge_generated(payload_templates, constants.get("payload_templates"), field="payload_templates", input_path=path)
        _merge_generated(substitutions, constants.get("substitutions"), field="substitutions", input_path=path)
    return {"payload_templates": payload_templates, "substitutions": substitutions}, rows


def _migrate_sidecar(legacy_path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    legacy = _load_mapping(legacy_path)
    normalize = legacy.get("normalize")
    config = normalize.get("config") if isinstance(normalize, Mapping) else None
    constants = config.get("packet_constants") if isinstance(config, Mapping) else None
    if not isinstance(constants, Mapping):
        raise ReferenceError("legacy descriptor has no /normalize/config/packet_constants object")
    payload_templates = constants.get("payload_templates", {})
    substitutions = constants.get("substitutions", {})
    if not isinstance(payload_templates, Mapping) or not isinstance(substitutions, Mapping):
        raise ReferenceError("legacy packet_constants payload_templates/substitutions must be objects")
    roles = config.get("roles", {}) if isinstance(config, Mapping) else {}
    if not isinstance(roles, Mapping) or not all(isinstance(value, str) for value in roles.values()):
        raise ReferenceError("legacy /normalize/config/roles must map role ids to paths")
    artifact_roles = {
        str(role): {"path": str(path), "sha256": None, "bytes": None}
        for role, path in sorted(roles.items())
    }
    sidecar = {"payload_templates": dict(payload_templates), "substitutions": dict(substitutions)}
    return sidecar, artifact_roles, [_input_row(legacy_path, "legacy_lane_descriptor")]


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
        sidecar, artifact_roles, input_rows = _migrate_sidecar(legacy_path)
    elif mode == "compile":
        if legacy_path is not None:
            raise ManifestError("compile mode never accepts or reads --legacy")
        sidecar, input_rows = _compile_sidecar(manifest, manifest_path)
        artifact_roles = {}
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
        "manifest_sha256": digest_bytes(manifest_path.read_bytes()),
        "target_freeze": target_freeze,
        "resolved_inputs": sorted(unique_rows.values(), key=lambda row: (row["path"], row["role"])),
        "artifact_roles": artifact_roles,
        "packet_constants_ref": {"path": _display(sidecar_path), "sha256": digest_bytes(sidecar_data)},
        "registry_pins": _registry_pins(manifest),
    }
    lock_schema = json.loads((SOURCE_ROOT / "contracts/kernel/schemas/bb.e4.lane_lock.v1.schema.json").read_text(encoding="utf-8"))
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
