#!/usr/bin/env python3
"""Reject additions to the Phase 20 frozen semantic surfaces."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs/plans/phase_20_right_shape/PHASE20_FREEZE_BASELINE.json"
BASELINE_SHA = "3b8d862f62ee9c2c421fe07758606b6973902c67"
SCHEMA_ROOTS = (
    ROOT / "contracts/kernel/schemas",
    ROOT / "docs/conformance/schemas",
)
SDK_ROOTS = (ROOT / "sdk", ROOT / "breadboard_sdk")
LANE_ROOT = ROOT / "config/e4_lanes"

# Phase 20 plan §4, packets F1/F2/E1.
ALLOWED_SCHEMA_IDS = {
    "bb.e4.lane_manifest.v1",
    "bb.e4.lane_lock.v1",
    "bb.contract_tiers.v1",
}

# Phase 20 packet F4. These generated sources are known before the freeze begins.
ALLOWED_LANE_SOURCE_PATHS = {
    "config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml",
    "config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.lock.json",
}

# Packet M1 owns closeout files. The pattern is deliberately restricted to root-level
# files that identify both the M track and the otherwise-frozen governance surface.
M_TRACK_GOVERNANCE_FILE = re.compile(
    r"^M(?:1)?[-_].*(?:ledger|scorecard)", re.IGNORECASE
)
GOVERNANCE_FILE = re.compile(r"(?:ledger|scorecard)", re.IGNORECASE)


class InventoryError(ValueError):
    """The current tree cannot be inventoried safely."""


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"{_relative(path)}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InventoryError(f"{_relative(path)}: expected a JSON object")
    return value


def _schema_ids() -> set[str]:
    ids: set[str] = set()
    owners: dict[str, str] = {}
    for schema_root in SCHEMA_ROOTS:
        for path in sorted(schema_root.rglob("*.json")):
            schema_id = _load_json_object(path).get("$id")
            if schema_id is None:
                continue
            if not isinstance(schema_id, str) or not schema_id:
                raise InventoryError(f"{_relative(path)}: $id must be a non-empty string")
            previous = owners.get(schema_id)
            if previous is not None:
                raise InventoryError(
                    f"duplicate schema $id {schema_id!r}: {previous}, {_relative(path)}"
                )
            owners[schema_id] = _relative(path)
            ids.add(schema_id)
    return ids


def _package_name(path: Path) -> str:
    if path.name == "package.json":
        name = _load_json_object(path).get("name")
    else:
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise InventoryError(f"{_relative(path)}: invalid TOML: {exc}") from exc
        project = payload.get("project")
        poetry = payload.get("tool", {}).get("poetry")
        name = project.get("name") if isinstance(project, dict) else None
        if name is None and isinstance(poetry, dict):
            name = poetry.get("name")
    if not isinstance(name, str) or not name:
        raise InventoryError(f"{_relative(path)}: package name must be a non-empty string")
    return name


def _sdk_packages() -> set[tuple[str, str]]:
    packages: set[tuple[str, str]] = set()
    for sdk_root in SDK_ROOTS:
        if not sdk_root.exists():
            continue
        paths = set(sdk_root.rglob("package.json"))
        paths.update(sdk_root.rglob("pyproject.toml"))
        for path in sorted(paths):
            packages.add((_package_name(path), _relative(path)))
    return packages


def _lane_document(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise InventoryError(f"{_relative(path)}: invalid lane document: {exc}") from exc
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InventoryError(f"{_relative(path)}: expected a mapping lane document")
    return value


def _lane_inventory() -> tuple[set[str], set[str]]:
    lane_ids: set[str] = set()
    lane_kinds: set[str] = set()
    for path in sorted(LANE_ROOT.iterdir()):
        if not path.is_file() or path.suffix not in {".json", ".yaml", ".yml"}:
            continue
        document = _lane_document(path)
        if document is None:
            continue
        relative_path = _relative(path)
        if relative_path in ALLOWED_LANE_SOURCE_PATHS:
            continue
        for key, target in (("lane_id", lane_ids), ("kind", lane_kinds)):
            value = document.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                raise InventoryError(f"{relative_path}: {key} must be a non-empty string")
            target.add(value)
    return lane_ids, lane_kinds


def _governance_files() -> set[str]:
    return {
        path.name
        for path in ROOT.iterdir()
        if path.is_file() and GOVERNANCE_FILE.search(path.name)
    }


def build_inventory() -> dict[str, Any]:
    lane_ids, lane_kinds = _lane_inventory()
    return {
        "schema_ids": sorted(_schema_ids()),
        "sdk_packages": [
            {"name": name, "path": path} for name, path in sorted(_sdk_packages())
        ],
        "lane_ids": sorted(lane_ids),
        "lane_kinds": sorted(lane_kinds),
        "top_level_governance_files": sorted(_governance_files()),
    }


def _package_set(payload: Any, label: str) -> set[tuple[str, str]]:
    if not isinstance(payload, list):
        raise InventoryError(f"{label} must be a list")
    result: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise InventoryError(f"{label} entries must be objects")
        name, path = item.get("name"), item.get("path")
        if not isinstance(name, str) or not isinstance(path, str):
            raise InventoryError(f"{label} entries require string name and path")
        result.add((name, path))
    return result


def _string_set(payload: Any, label: str) -> set[str]:
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise InventoryError(f"{label} must be a list of strings")
    return set(payload)


def _added_values(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, list[str]]:
    baseline_schema_ids = _string_set(baseline.get("schema_ids"), "schema_ids")
    current_schema_ids = _string_set(current.get("schema_ids"), "schema_ids")
    schema_additions = current_schema_ids - baseline_schema_ids - ALLOWED_SCHEMA_IDS

    package_additions = _package_set(current.get("sdk_packages"), "sdk_packages") - _package_set(
        baseline.get("sdk_packages"), "sdk_packages"
    )

    lane_id_additions = _string_set(current.get("lane_ids"), "lane_ids") - _string_set(
        baseline.get("lane_ids"), "lane_ids"
    )
    lane_kind_additions = _string_set(current.get("lane_kinds"), "lane_kinds") - _string_set(
        baseline.get("lane_kinds"), "lane_kinds"
    )

    governance_additions = _string_set(
        current.get("top_level_governance_files"), "top_level_governance_files"
    ) - _string_set(
        baseline.get("top_level_governance_files"), "top_level_governance_files"
    )
    governance_additions = {
        name for name in governance_additions if not M_TRACK_GOVERNANCE_FILE.search(name)
    }

    return {
        "schema_ids": sorted(schema_additions),
        "sdk_packages": sorted(f"{name} ({path})" for name, path in package_additions),
        "lane_ids": sorted(lane_id_additions),
        "lane_kinds": sorted(lane_kind_additions),
        "top_level_governance_files": sorted(governance_additions),
    }


def main() -> int:
    try:
        baseline_payload = _load_json_object(BASELINE_PATH)
        if baseline_payload.get("baseline_sha") != BASELINE_SHA:
            raise InventoryError(
                f"{_relative(BASELINE_PATH)}: baseline_sha does not match script BASELINE_SHA"
            )
        baseline = baseline_payload.get("inventory")
        if not isinstance(baseline, dict):
            raise InventoryError(f"{_relative(BASELINE_PATH)}: inventory must be an object")
        additions = _added_values(baseline, build_inventory())
    except InventoryError as exc:
        print(f"phase20-freeze: inventory error: {exc}", file=sys.stderr)
        return 2

    violations = {name: values for name, values in additions.items() if values}
    if violations:
        print("phase20-freeze: frozen semantic surface additions detected", file=sys.stderr)
        for category, values in violations.items():
            for value in values:
                print(f"  {category}: {value}", file=sys.stderr)
        return 1

    print(f"phase20-freeze: PASS (baseline {BASELINE_SHA})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
