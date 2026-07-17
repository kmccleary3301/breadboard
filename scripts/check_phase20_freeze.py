#!/usr/bin/env python3
"""Reject additions to the Phase 20 frozen semantic surfaces."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Mapping

import yaml
try:
    from scripts.check_contract_tiers import validate_contract_tiers
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from check_contract_tiers import validate_contract_tiers



ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs/plans/phase_20_right_shape/PHASE20_FREEZE_BASELINE.json"
MASTER_PLAN_PATH = ROOT / "docs/plans/phase_20_right_shape/BB_RS_MASTER_PLAN.md"
SPEC_AMENDMENTS_PATH = ROOT / "docs/plans/phase_20_right_shape/SPEC_AMENDMENTS.md"
BASELINE_SHA = "3b8d862f62ee9c2c421fe07758606b6973902c67"
SCHEMA_ROOTS = (
    ROOT / "contracts/kernel/schemas",
    ROOT / "docs/conformance/schemas",
)
SDK_ROOTS = (ROOT / "sdk", ROOT / "breadboard_sdk")
LANE_ROOT = ROOT / "config/e4_lanes"
LANE_LOCK_SCHEMA_PATH = (
    ROOT / "contracts/kernel/schemas/bb.e4.lane_lock.v1.schema.json"
)

# Phase 20 plan §4, packets F1/F2/E1. SP3 (0fc11917, reviewed) canonicalized
# schema $id values to full https://breadboard.dev URLs; both spellings name
# the same plan-sanctioned additions.
ALLOWED_SCHEMA_IDS = {
    "bb.e4.lane_manifest.v1",
    "bb.e4.lane_lock.v1",
    "bb.contract_tiers.v1",
    "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_manifest.v1.schema.json",
    "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_lock.v1.schema.json",
    "https://breadboard.dev/contracts/kernel/schemas/bb.contract_tiers.v1.schema.json",
    "https://breadboard.dev/contracts/kernel/schemas/bb.coordination_view.v1.schema.json",  # NS05A / AM24
    "https://breadboard.dev/contracts/kernel/schemas/bb.work_item.v2.schema.json",  # NS05A / AM24
    "https://breadboard.dev/contracts/kernel/schemas/bb.work_placement.v1.schema.json",  # NS05A / AM24
}

# FREEZE_POLICY.md permits plan-required tightening of an existing schema only
# when the same commit pins the expected post-change content hash and owning packet.
TIGHTENING_ALLOWLIST: dict[str, dict[str, str]] = {
    "https://breadboard.dev/contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json": {
        "packet": "I2",
        "sha256": "fbb7d1492c4f98ac8b38b2923d971b3faa51c921f70cee012d3903fcc29491ad",
    },
    "bb.e4.lane_manifest.v1": {
        "packet": "F4",
        "sha256": "06667d867257949a5cd51af4b6d92ffe93af34a0656c6958fc59900eeb64613b",
        "class": "plan_mandated_evolution",
        "ref": "plan §3 F4 + AM8",
    },
    "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_def.v1.schema.json": {
        "packet": "H3",
        "sha256": "bde8357bb29a8e25120385d6e7196607c3fb67745815b83aa3185d9f4d429dc6",
        "class": "plan_mandated_evolution",
        "ref": "plan §3 H2/H3 + AM17a/AM17b-r",
    },
    "https://breadboard.dev/contracts/kernel/schemas/bb.e4.lane_def.v2.schema.json": {
        "packet": "H3",
        "sha256": "0da3ccca218daaba4ff0f3964f7070ae146815a912ea2379cbfae88a5b5475aa",
        "class": "plan_mandated_evolution",
        "ref": "plan §3 H2/H3 + AM17a/AM17b-r",
    },
    "https://breadboard.dev/contracts/kernel/schemas/bb.e4.fixed_point_report.v1.schema.json": {
        "packet": "bb-31n",
        "sha256": "64bff2f1f55bba6216ecc71f0669a3eb4900253d07dfe3db3070c3217a325c02",
        "class": "tightening",
    },
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


class AllowlistConfigError(ValueError):
    """The tightening allowlist is malformed and cannot authorize drift."""


class InventoryError(ValueError):
    """The current tree cannot be inventoried safely."""


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _tracked_files() -> set[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InventoryError(f"cannot enumerate git-tracked files: {exc}") from exc
    return {ROOT / path for path in result.stdout.split("\0") if path}


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"{_relative(path)}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InventoryError(f"{_relative(path)}: expected a JSON object")
    return value


def _schema_inventory(tracked_files: set[Path]) -> tuple[set[str], dict[str, str]]:
    ids: set[str] = set()
    hashes: dict[str, str] = {}
    owners: dict[str, str] = {}
    schema_paths = (
        path
        for path in tracked_files
        if path.suffix == ".json"
        and any(path.is_relative_to(schema_root) for schema_root in SCHEMA_ROOTS)
    )
    for path in sorted(schema_paths):
        try:
            content = path.read_bytes()
            value = json.loads(content)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InventoryError(f"{_relative(path)}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise InventoryError(f"{_relative(path)}: expected a JSON object")
        schema_id = value.get("$id")
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
        hashes[schema_id] = hashlib.sha256(content).hexdigest()
    return ids, hashes


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


def _sdk_packages(tracked_files: set[Path]) -> set[tuple[str, str]]:
    packages: set[tuple[str, str]] = set()
    paths = (
        path
        for path in tracked_files
        if path.name in {"package.json", "pyproject.toml"}
        and any(path.is_relative_to(sdk_root) for sdk_root in SDK_ROOTS)
    )
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


def _lane_inventory(tracked_files: set[Path]) -> tuple[set[str], set[str]]:
    lane_ids: set[str] = set()
    lane_kinds: set[str] = set()
    lane_paths = (
        path
        for path in tracked_files
        if path.parent == LANE_ROOT and path.suffix in {".json", ".yaml", ".yml"}
    )
    for path in sorted(lane_paths):
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


def _governance_files(tracked_files: set[Path]) -> set[str]:
    return {
        path.name
        for path in tracked_files
        if path.parent == ROOT and GOVERNANCE_FILE.search(path.name)
    }


def build_inventory(tracked_files: set[Path] | None = None) -> dict[str, Any]:
    tracked_files = tracked_files if tracked_files is not None else _tracked_files()
    schema_ids, schema_hashes = _schema_inventory(tracked_files)
    lane_ids, lane_kinds = _lane_inventory(tracked_files)
    return {
        "schema_ids": sorted(schema_ids),
        "schema_content_sha256": dict(sorted(schema_hashes.items())),
        "sdk_packages": [
            {"name": name, "path": path}
            for name, path in sorted(_sdk_packages(tracked_files))
        ],
        "lane_ids": sorted(lane_ids),
        "lane_kinds": sorted(lane_kinds),
        "top_level_governance_files": sorted(_governance_files(tracked_files)),
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


def _string_map(payload: Any, label: str) -> dict[str, str]:
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise InventoryError(f"{label} must be an object with string keys and values")
    return dict(payload)


def _tracked_reference_text(path: Path, tracked_files: set[Path]) -> str:
    if path not in tracked_files:
        raise AllowlistConfigError(f"allowlist reference file is not tracked: {_relative(path)}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AllowlistConfigError(
            f"cannot read allowlist reference file {_relative(path)}: {exc}"
        ) from exc


def _amendment_exists(amendment_id: str, amendments_text: str) -> bool:
    number_match = re.fullmatch(r"AM(\d+)", amendment_id)
    numbered_heading = (
        re.compile(rf"^## Amendment {number_match.group(1)}\b")
        if number_match is not None
        else None
    )
    literal = re.compile(rf"(?<![A-Za-z0-9]){re.escape(amendment_id)}(?![A-Za-z0-9-])")
    for line in amendments_text.splitlines():
        if numbered_heading is not None and numbered_heading.search(line):
            return True
        if (
            ("Amendment" in line or "revision" in line.lower())
            and literal.search(line)
        ):
            return True
    return False


def _validate_evolution_ref(ref: str, tracked_files: set[Path]) -> None:
    plan_text: str | None = None
    amendments_text: str | None = None
    item_pattern = r"[A-L]\d+[a-z]?"
    amendment_pattern = r"AM\d+[a-z]?(?:-r)?"
    for segment in ref.split(" + "):
        plan_match = re.fullmatch(
            rf"plan §\d+ ({item_pattern}(?:/{item_pattern})*)", segment
        )
        amendment_match = re.fullmatch(
            rf"{amendment_pattern}(?:/{amendment_pattern})*", segment
        )
        if plan_match is not None:
            if plan_text is None:
                plan_text = _tracked_reference_text(MASTER_PLAN_PATH, tracked_files)
            item_ids = plan_match.group(1).split("/")
            if any(f"[{item_id} |" not in plan_text for item_id in item_ids):
                raise AllowlistConfigError(
                    f"invalid evolution ref segment {segment!r}: unknown plan item"
                )
            continue
        if amendment_match is not None:
            if amendments_text is None:
                amendments_text = _tracked_reference_text(
                    SPEC_AMENDMENTS_PATH, tracked_files
                )
            amendment_ids = segment.split("/")
            if any(
                not _amendment_exists(amendment_id, amendments_text)
                for amendment_id in amendment_ids
            ):
                raise AllowlistConfigError(
                    f"invalid evolution ref segment {segment!r}: unknown amendment"
                )
            continue
        raise AllowlistConfigError(f"invalid evolution ref segment {segment!r}")



def _validated_tightening_allowlist(tracked_files: set[Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    required_fields = {"packet", "sha256"}
    allowed_fields = required_fields | {"class", "ref"}
    allowed_classes = {"tightening", "plan_mandated_evolution"}
    for schema_id, entry in TIGHTENING_ALLOWLIST.items():
        if not isinstance(schema_id, str) or not schema_id:
            raise AllowlistConfigError("schema IDs must be non-empty strings")
        if not isinstance(entry, dict):
            raise AllowlistConfigError(
                f"{schema_id}: expected an object with packet and sha256 fields"
            )
        fields = set(entry)
        missing = required_fields - fields
        extra = fields - allowed_fields
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(sorted(missing)))
            if extra:
                details.append("unknown " + ", ".join(sorted(extra)))
            raise AllowlistConfigError(f"{schema_id}: " + "; ".join(details))
        packet = entry.get("packet")
        content_hash = entry.get("sha256")
        entry_class = entry.get("class", "tightening")
        ref = entry.get("ref")
        if not isinstance(packet, str) or not packet:
            raise AllowlistConfigError(f"{schema_id}: packet must be a non-empty string")
        if not isinstance(content_hash, str) or re.fullmatch(
            r"[0-9a-f]{64}", content_hash
        ) is None:
            raise AllowlistConfigError(
                f"{schema_id}: sha256 must be 64 lowercase hexadecimal characters"
            )
        if entry_class not in allowed_classes:
            raise AllowlistConfigError(
                f"{schema_id}: class must be tightening or plan_mandated_evolution"
            )
        if ref is not None and (not isinstance(ref, str) or not ref):
            raise AllowlistConfigError(f"{schema_id}: ref must be a non-empty string")
        if entry_class == "plan_mandated_evolution" and ref is None:
            raise AllowlistConfigError(
                f"{schema_id}: ref is required for plan_mandated_evolution"
            )
        if entry_class == "plan_mandated_evolution":
            assert isinstance(ref, str)
            _validate_evolution_ref(ref, tracked_files)
        hashes[schema_id] = content_hash
    return hashes


def _added_values(
    baseline: dict[str, Any],
    current: dict[str, Any],
    tracked_files: set[Path],
) -> dict[str, list[str]]:
    baseline_schema_ids = _string_set(baseline.get("schema_ids"), "schema_ids")
    current_schema_ids = _string_set(current.get("schema_ids"), "schema_ids")
    schema_additions = current_schema_ids - baseline_schema_ids - ALLOWED_SCHEMA_IDS
    baseline_hashes = _string_map(
        baseline.get("schema_content_sha256"), "schema_content_sha256"
    )
    current_hashes = _string_map(
        current.get("schema_content_sha256"), "schema_content_sha256"
    )
    tightening_hashes = _validated_tightening_allowlist(tracked_files)
    schema_content_drift: set[str] = set()
    for schema_id, baseline_hash in baseline_hashes.items():
        current_hash = current_hashes.get(schema_id)
        if current_hash is None or current_hash == baseline_hash:
            continue
        if tightening_hashes.get(schema_id) == current_hash:
            continue
        remedy = json.dumps(
            {
                schema_id: {
                    "class": "tightening",
                    "packet": "<packet-id>",
                    "sha256": current_hash,
                }
            },
            sort_keys=True,
        )
        schema_content_drift.add(
            f"{schema_id} (not authorized by TIGHTENING_ALLOWLIST; add/update "
            f"{remedy} per FREEZE_POLICY/AM10 in the same commit, or revert the change; "
            'for AM17a plan-mandated mixed evolution use class '
            '"plan_mandated_evolution" with a mandating-plan "ref")'
        )

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
        "schema_content_drift": sorted(schema_content_drift),
        "sdk_packages": sorted(f"{name} ({path})" for name, path in package_additions),
        "lane_ids": sorted(lane_id_additions),
        "lane_kinds": sorted(lane_kind_additions),
        "top_level_governance_files": sorted(governance_additions),
    }


def _is_volatile_lock_field(name: str) -> bool:
    if name.endswith("_at_utc"):
        return True
    parts = name.split("_")
    return "duration" in parts or "elapsed" in parts


def validate_lane_lock_schema(schema: Mapping[str, Any]) -> list[str]:
    """Return schema pointers for lock properties that encode volatile run data."""

    violations: list[str] = []

    def walk(value: Any, pointer: str) -> None:
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                for name in sorted(properties):
                    if isinstance(name, str) and _is_volatile_lock_field(name):
                        violations.append(f"{pointer}/properties/{name}")
            for key, child in value.items():
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                walk(child, f"{pointer}/{escaped}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{pointer}/{index}")

    walk(schema, "")
    return violations


def main() -> int:
    try:
        tracked_files = _tracked_files()
        baseline_payload = _load_json_object(BASELINE_PATH)
        if baseline_payload.get("baseline_sha") != BASELINE_SHA:
            raise InventoryError(
                f"{_relative(BASELINE_PATH)}: baseline_sha does not match script BASELINE_SHA"
            )
        baseline = baseline_payload.get("inventory")
        if not isinstance(baseline, dict):
            raise InventoryError(f"{_relative(BASELINE_PATH)}: inventory must be an object")
        volatile_fields = validate_lane_lock_schema(
            _load_json_object(LANE_LOCK_SCHEMA_PATH)
        )
        if volatile_fields:
            raise InventoryError(
                "lane lock schema contains volatile fields: "
                + ", ".join(volatile_fields)
            )
        contract_tier_errors = validate_contract_tiers(tracked_files=tracked_files)
        if contract_tier_errors:
            raise InventoryError(
                "contract tier registry invalid: "
                + "; ".join(contract_tier_errors)
            )
        additions = _added_values(
            baseline, build_inventory(tracked_files), tracked_files
        )
    except AllowlistConfigError as exc:
        print(f"phase20-freeze: allowlist config error: {exc}", file=sys.stderr)
        return 2
    except InventoryError as exc:
        print(f"phase20-freeze: inventory error: {exc}", file=sys.stderr)
        return 2

    violations = {name: values for name, values in additions.items() if values}
    if violations:
        print("phase20-freeze: frozen semantic surface violations detected", file=sys.stderr)
        for category, values in violations.items():
            for value in values:
                print(f"  {category}: {value}", file=sys.stderr)
        return 1

    print(f"phase20-freeze: PASS (baseline {BASELINE_SHA})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
