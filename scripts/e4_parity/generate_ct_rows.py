#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
DEFAULT_MANIFEST = ROOT / "docs" / "conformance" / "ct_scenarios_v1.json"
DEFAULT_LANE_DEFS = DEFAULT_LANE_DEF_DIR


class CtRowGenerationError(ValueError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json_out_from_argv(argv: Sequence[str]) -> str:
    for index, arg in enumerate(argv):
        if arg == "--json-out" and index + 1 < len(argv):
            return str(argv[index + 1])
        if arg.startswith("--json-out="):
            return arg.split("=", 1)[1]
    raise CtRowGenerationError("ct command argv must include --json-out")


def _normalize_command(argv: Sequence[str]) -> list[str]:
    if not argv:
        raise CtRowGenerationError("ct command argv must not be empty")
    command = [str(part) for part in argv]
    first = Path(command[0]).as_posix()
    if command[0] == sys.executable or first.endswith("/.venv/bin/python") or first.endswith("/venv/bin/python") or command[0] == ".venv/bin/python":
        command[0] = "python"
    return command


def _phase_gate_level(lane: Mapping[str, Any]) -> str:
    phase = lane.get("phase")
    if not isinstance(phase, str) or not phase:
        raise CtRowGenerationError(f"lane {lane.get('lane_id', '<unknown>')} missing phase")
    return f"{phase}-support"


def _lane_def_for(lane: Mapping[str, Any], lane_defs: Mapping[str, Mapping[str, Any]] | None) -> Mapping[str, Any] | None:
    if lane_defs is None:
        return None
    lane_def = lane_defs.get(str(lane["lane_id"]))
    if lane_def is None:
        return None
    if lane_def.get("config_id") not in (None, lane.get("config_id")):
        raise CtRowGenerationError(f"lane_def {lane['lane_id']} config_id does not match inventory")
    return lane_def


def _description(lane: Mapping[str, Any], lane_def: Mapping[str, Any] | None) -> str:
    if lane_def is None:
        ct = lane.get("ct")
        if isinstance(ct, Mapping) and isinstance(ct.get("description"), str):
            return ct["description"]
        raise CtRowGenerationError(f"lane {lane.get('lane_id', '<unknown>')} missing lane_def for CT description")
    ct = lane_def.get("ct")
    if isinstance(ct, Mapping) and isinstance(ct.get("description"), str):
        return ct["description"]
    compare = lane_def.get("compare")
    capture = lane_def.get("capture")
    if not isinstance(compare, Mapping) or not isinstance(capture, Mapping):
        raise CtRowGenerationError(f"lane {lane['lane_id']} lane_def compare/capture must be objects")
    comparator = compare.get("comparator")
    strategy = capture.get("strategy")
    if not isinstance(comparator, str) or not isinstance(strategy, str):
        raise CtRowGenerationError(f"lane {lane['lane_id']} lane_def compare.comparator and capture.strategy must be strings")
    return f"{lane['lane_id']}: {comparator} over {strategy} capture"

def _base_checks(
    lane: Mapping[str, Any],
    output_path: str,
    lane_def: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if lane_def is not None:
        compare = lane_def.get("compare")
        if isinstance(compare, Mapping):
            config = compare.get("config")
            if isinstance(config, Mapping) and isinstance(config.get("assertions"), list):
                return copy.deepcopy(config["assertions"])
    lane_id = str(lane["lane_id"])
    config_id = str(lane["config_id"])
    provider_model = str(lane["provider_model"])
    sandbox_mode = str(lane["sandbox_mode"])
    return [
        {"equals": True, "path": "ok"},
        {"length_equals": 0, "path": "errors"},
        {"equals": config_id, "path": "claimed_scope.config_id"},
        {"equals": lane_id, "path": "claimed_scope.lane_id"},
        {"equals": provider_model, "path": "claimed_scope.provider_model"},
        {"equals": sandbox_mode, "path": "claimed_scope.sandbox_mode"},
    ]


def _timeout_seconds(lane: Mapping[str, Any], lane_def: Mapping[str, Any] | None) -> int:
    if lane_def is not None:
        ct = lane_def.get("ct")
        if isinstance(ct, Mapping) and isinstance(ct.get("timeout_seconds"), int):
            return ct["timeout_seconds"]
    return 240 if lane.get("phase") == "P5" else 180


def generate_inventory_scenarios(
    inventory: Mapping[str, Any],
    *,
    lane_defs: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    lanes = inventory.get("lanes")
    if not isinstance(lanes, list):
        raise CtRowGenerationError("inventory.lanes must be a list")
    if lane_defs is None:
        lane_defs = load_lane_defs(DEFAULT_LANE_DEFS)
    scenarios: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise CtRowGenerationError("inventory lane must be an object")
        ct = lane.get("ct")
        if ct is None:
            continue
        if lane.get("status") != "accepted":
            continue
        if not isinstance(ct, Mapping):
            raise CtRowGenerationError(f"lane {lane.get('lane_id', '<unknown>')} ct must be object or null")
        command_spec = ct.get("command")
        if not isinstance(command_spec, Mapping) or not isinstance(command_spec.get("argv"), list):
            raise CtRowGenerationError(f"lane {lane.get('lane_id', '<unknown>')} ct.command.argv must be a list")
        command = _normalize_command(command_spec["argv"])
        output_path = _json_out_from_argv(command)
        lane_def = _lane_def_for(lane, lane_defs)
        scenarios.append(
            {
                "assertions": {
                    "json_files": [
                        {
                            "checks": _base_checks(lane, output_path, lane_def),
                            "path": output_path,
                        }
                    ]
                },
                "command": command,
                "description": _description(lane, lane_def),
                "gate_level": _phase_gate_level(lane),
                "test_id": str(ct["test_id"]),
                "timeout_seconds": _timeout_seconds(lane, lane_def),
            }
        )
    return sorted(scenarios, key=lambda item: item["test_id"])


def merge_inventory_scenarios(
    manifest: Mapping[str, Any], generated_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(manifest))
    existing = payload.get("scenarios")
    if not isinstance(existing, list):
        raise CtRowGenerationError("manifest.scenarios must be a list")
    generated_by_id = {str(row["test_id"]): dict(row) for row in generated_rows}
    merged = [row for row in existing if not (isinstance(row, Mapping) and row.get("test_id") in generated_by_id)]
    merged.extend(generated_by_id[test_id] for test_id in sorted(generated_by_id))
    payload["scenarios"] = merged
    return payload


def upsert_inventory_scenarios(
    manifest_path: Path = DEFAULT_MANIFEST,
    inventory_path: Path = DEFAULT_INVENTORY,
    lane_defs_path: Path = DEFAULT_LANE_DEFS,
) -> list[dict[str, Any]]:
    inventory = load_json(inventory_path)
    manifest = load_json(manifest_path)
    rows = generate_inventory_scenarios(inventory, lane_defs=load_lane_defs(lane_defs_path))
    write_json(manifest_path, merge_inventory_scenarios(manifest, rows))
    return rows


def _row_diff_fields(expected: Mapping[str, Any], actual: Mapping[str, Any], *, prefix: str = "") -> list[str]:
    fields: list[str] = []
    keys = sorted(set(expected) | set(actual))
    for key in keys:
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in expected or key not in actual:
            fields.append(path)
            continue
        expected_value = expected[key]
        actual_value = actual[key]
        if isinstance(expected_value, Mapping) and isinstance(actual_value, Mapping):
            fields.extend(_row_diff_fields(expected_value, actual_value, prefix=path))
        elif expected_value != actual_value:
            fields.append(path)
    return fields


def field_level_diffs(
    manifest: Mapping[str, Any],
    generated_rows: Sequence[Mapping[str, Any]],
    *,
    ignored_fields: set[str] | None = None,
) -> list[dict[str, Any]]:
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        return [{"test_id": "<manifest>", "fields": ["scenarios"], "non_ignored_fields": ["scenarios"]}]
    ignored = ignored_fields or set()
    by_id = {str(row.get("test_id")): row for row in scenarios if isinstance(row, Mapping)}
    diffs: list[dict[str, Any]] = []
    for row in generated_rows:
        test_id = str(row["test_id"])
        actual = by_id.get(test_id)
        if actual is None:
            diffs.append({"test_id": test_id, "fields": ["<missing>"], "non_ignored_fields": ["<missing>"]})
            continue
        fields = _row_diff_fields(row, actual)
        if fields:
            diffs.append(
                {
                    "test_id": test_id,
                    "fields": fields,
                    "non_ignored_fields": [field for field in fields if field not in ignored],
                }
            )
    return diffs


def mismatches(manifest: Mapping[str, Any], generated_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    diffs = field_level_diffs(manifest, generated_rows)
    errors: list[str] = []
    for diff in diffs:
        fields = diff["fields"]
        if fields:
            errors.append(f"scenario mismatch: {diff['test_id']} fields={','.join(fields)}")
    return errors


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate inventory-backed C4 CT scenario rows.")
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out", default=None, help="write merged manifest to this path")
    parser.add_argument("--rows-out", default=None, help="write generated inventory rows to this path")
    parser.add_argument("--lane-defs", default=str(DEFAULT_LANE_DEFS), help="directory of bb.e4.lane_def.v1 YAML files")
    parser.add_argument("--check", action="store_true", help="fail if generated inventory rows differ from manifest rows")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    inventory_path = Path(args.inventory)
    manifest_path = Path(args.manifest)
    inventory = load_json(inventory_path)
    manifest = load_json(manifest_path)
    rows = generate_inventory_scenarios(inventory, lane_defs=load_lane_defs(Path(args.lane_defs)))

    if args.rows_out:
        write_json(Path(args.rows_out), rows)
    if args.out:
        write_json(Path(args.out), merge_inventory_scenarios(manifest, rows))

    diffs = field_level_diffs(manifest, rows, ignored_fields={"description"})
    errors = mismatches(manifest, rows) if args.check else []
    print(
        json.dumps(
            {
                "description_diff_count": sum(
                    1 for diff in diffs if diff["fields"] and not diff["non_ignored_fields"]
                ),
                "field_diff_count": len(diffs),
                "generated_row_count": len(rows),
                "inventory": str(inventory_path),
                "manifest": str(manifest_path),
                "mismatch_count": len(errors),
                "ok": not errors,
                "errors": errors[:20],
            },
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
