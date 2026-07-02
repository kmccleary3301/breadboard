#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from scripts.e4_parity.lane_runtime import sha256_file
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from lane_runtime import sha256_file

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
DEFAULT_COMPARATOR_REGISTRY = ROOT / "conformance" / "comparators" / "registry.json"
EXECUTABLE_STAGES = {"capture", "normalize", "replay", "compare", "claim"}
NORTH_STAR_LANE_IDS = (
    "breadboard_self_runtime_records_v1",
    "claude_code_north_star_capture_v1",
    "opencode_north_star_capture_v1",
)

ALL_STAGES = ("capture", "normalize", "replay", "compare", "claim")


class LaneRunError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _inventory_lane(lane_id: str, inventory_path: Path) -> Mapping[str, Any] | None:
    inventory = _load_json(inventory_path)
    lanes = inventory.get("lanes")
    if not isinstance(lanes, list):
        raise LaneRunError(f"{inventory_path} inventory.lanes must be a list")
    matches = [lane for lane in lanes if isinstance(lane, Mapping) and lane.get("lane_id") == lane_id]
    if len(matches) > 1:
        raise LaneRunError(f"duplicate lane_id {lane_id!r} in {inventory_path}")
    return matches[0] if matches else None


def _command_argv(command: Any) -> list[str] | None:
    if isinstance(command, Mapping):
        argv = command.get("argv")
        if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
            return list(argv)
    return None


def _claim_argv(lane_def: Mapping[str, Any], inventory_lane: Mapping[str, Any] | None) -> list[str]:
    argv = _command_argv(lane_def.get("reverify_command"))
    if argv is not None:
        return argv
    if inventory_lane is not None:
        argv = _command_argv(inventory_lane.get("reverify_command"))
        if argv is not None:
            return argv
        ct = inventory_lane.get("ct")
        if isinstance(ct, Mapping):
            argv = _command_argv(ct.get("command"))
            if argv is not None:
                return argv
    raise LaneRunError(f"lane {lane_def['lane_id']!r} has no claim/reverify argv in lane_def or inventory")


def _json_out_location(argv: Sequence[str]) -> tuple[int, str, bool]:
    for index, arg in enumerate(argv):
        if arg == "--json-out":
            value_index = index + 1
            if value_index >= len(argv):
                raise LaneRunError("argv has --json-out without a following path")
            return value_index, str(argv[value_index]), False
        if arg.startswith("--json-out="):
            return index, arg.split("=", 1)[1], True
    raise LaneRunError("argv must include --json-out")


def _resolve_repo_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def _retarget_json_out(argv: Sequence[str], out_dir: Path | None) -> tuple[list[str], Path, Path]:
    result = list(argv)
    index, value, inline = _json_out_location(result)
    accepted_path = _resolve_repo_path(value)
    if out_dir is None:
        return result, accepted_path, accepted_path
    try:
        relative = accepted_path.resolve().relative_to(ROOT)
    except ValueError:
        relative = Path(accepted_path.name)
    scratch_path = out_dir / relative
    scratch_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_display = _display_path(scratch_path)
    result[index] = f"--json-out={scratch_display}" if inline else scratch_display
    if "--check-only" in result:
        result.remove("--check-only")
    return result, accepted_path, scratch_path


def _stage_list(stage: str) -> list[str]:
    return list(ALL_STAGES) if stage == "all" else [stage]


def _comparator_entry(lane_def: Mapping[str, Any], registry_path: Path) -> Mapping[str, Any]:
    compare = lane_def.get("compare")
    if not isinstance(compare, Mapping) or not isinstance(compare.get("comparator"), str):
        raise LaneRunError(f"lane {lane_def['lane_id']!r} has no compare.comparator")
    comparator_id = str(compare["comparator"])
    registry = _load_json(registry_path)
    comparators = registry.get("comparators")
    if not isinstance(comparators, list):
        raise LaneRunError(f"{registry_path} comparators must be a list")
    matches = [
        entry
        for entry in comparators
        if isinstance(entry, Mapping)
        and entry.get("comparator_id") == comparator_id
        and lane_def["lane_id"] in entry.get("lane_ids", [])
    ]
    if len(matches) != 1:
        raise LaneRunError(
            f"lane {lane_def['lane_id']!r} comparator {comparator_id!r} must have exactly one registry entry"
        )
    return matches[0]


def _command_stage_argv(stage_name: str, lane_def: Mapping[str, Any], inventory_lane: Mapping[str, Any] | None) -> list[str] | None:
    if stage_name == "capture":
        capture = lane_def.get("capture")
        if isinstance(capture, Mapping):
            argv = capture.get("argv")
            if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
                return list(argv)
        return None
    if stage_name == "claim":
        return _claim_argv(lane_def, inventory_lane)
    return None


def _write_accepted_compatible_output(output_path: Path, accepted_path: Path) -> bool:
    if not output_path.exists() or not accepted_path.exists() or output_path.read_bytes() == accepted_path.read_bytes():
        return False
    try:
        output_payload = json.loads(output_path.read_text(encoding="utf-8"))
        accepted_payload = json.loads(accepted_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(output_payload, dict) or not isinstance(accepted_payload, dict):
        return False
    stripped = dict(output_payload)
    for key in ("gate_errors", "pin_stale_count", "semantic_count", "error_count"):
        value = stripped.get(key)
        if value in ([], 0):
            stripped.pop(key, None)
    if stripped != accepted_payload:
        return False
    output_path.write_bytes(accepted_path.read_bytes())
    return True


def _metadata_stage_result(stage_name: str, lane_def: Mapping[str, Any], registry_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"stage": stage_name, "lane_id": lane_def["lane_id"], "returncode": 0}
    if stage_name == "capture":
        capture = lane_def.get("capture")
        result["strategy"] = capture.get("strategy") if isinstance(capture, Mapping) else None
        result["inputs"] = list(capture.get("inputs", [])) if isinstance(capture, Mapping) else []
        result["artifacts_root"] = lane_def.get("artifacts_root")
        result["skipped"] = True
        result["reason"] = "data-defined capture uses checked-in canonical artifacts; no lane-specific packet builder is executed"
        return result
    if stage_name == "normalize":
        normalize = lane_def.get("normalize")
        result["translator"] = normalize.get("translator") if isinstance(normalize, Mapping) else None
        result["skipped"] = True
        result["reason"] = "identity/data-only normalize stage has no artifact writer"
        return result
    if stage_name == "replay":
        replay = lane_def.get("replay")
        result["comparator_class"] = replay.get("comparator_class") if isinstance(replay, Mapping) else None
        result["skipped"] = True
        result["reason"] = "stored replay stage has no artifact writer"
        return result
    if stage_name == "compare":
        entry = _comparator_entry(lane_def, registry_path)
        result["comparator_id"] = entry.get("comparator_id")
        result["entrypoint"] = entry.get("entrypoint")
        result["skipped"] = True
        result["reason"] = "comparator registry resolved; C4 claim stage performs rerun/diff"
        return result
    raise LaneRunError(f"unsupported metadata stage {stage_name!r}")



def _refresh_er_progress_seed_pin(seed_path: Path) -> None:
    progress_path = ROOT.parent / "docs_tmp" / "phase_16" / "BB_ER_PROGRESS.json"
    if not progress_path.exists():
        return
    progress = _load_json(progress_path)
    changed = False
    for workstream in progress.get("workstreams", []):
        if not isinstance(workstream, dict):
            continue
        for item in workstream.get("items", []):
            if not isinstance(item, dict) or item.get("item_id") != "J.5":
                continue
            for evidence in item.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                if evidence.get("path") == "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json":
                    current_hash = sha256_file(seed_path)
                    if evidence.get("sha256") != current_hash:
                        evidence["sha256"] = current_hash
                        changed = True
    if changed:
        progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _refresh_promoted_bindings() -> dict[str, Any]:
    """Refresh deterministic artifacts whose hashes depend on promoted lane packets."""
    try:
        from scripts.e4_parity import build_artifact_catalog, build_e4_final_readiness_packet, seed_atomic_feature_ledger
        from scripts.e4_parity.generate_support_claims import generate as generate_support_claims
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        import build_artifact_catalog
        import build_e4_final_readiness_packet
        import seed_atomic_feature_ledger
        from generate_support_claims import generate as generate_support_claims

    seed_summary = seed_atomic_feature_ledger.write_ledger(
        seed_atomic_feature_ledger.DEFAULT_OUT,
        seed_atomic_feature_ledger.DEFAULT_INDEX_OUT,
    )
    _refresh_er_progress_seed_pin(seed_atomic_feature_ledger.DEFAULT_OUT)
    first_catalog = build_artifact_catalog.build_catalog(write_bindings=True, schema_version="v2")
    build_artifact_catalog.write_json(build_artifact_catalog.DEFAULT_OUTPUT_PATH, first_catalog)
    first_claims = generate_support_claims(dry_run=False)
    second_catalog = build_artifact_catalog.build_catalog(write_bindings=True, schema_version="v2")
    build_artifact_catalog.write_json(build_artifact_catalog.DEFAULT_OUTPUT_PATH, second_catalog)
    second_claims = generate_support_claims(dry_run=False)
    build_e4_final_readiness_packet.refresh_score_artifact_hashes()
    final_catalog = build_artifact_catalog.build_catalog(write_bindings=False, schema_version="v2")
    build_artifact_catalog.write_json(build_artifact_catalog.DEFAULT_OUTPUT_PATH, final_catalog)
    subledger = build_e4_final_readiness_packet.load_json(build_e4_final_readiness_packet.SCORE_SUBLEDGER_PATH)
    accepted_report = build_e4_final_readiness_packet.load_json(build_e4_final_readiness_packet.ACCEPTED_REPORT_PATH)
    return {
        "seed_row_count": seed_summary.get("row_count"),
        "catalog_entries": final_catalog.get("integrity", {}).get("entry_count"),
        "support_claim_count": second_claims.get("claim_count", first_claims.get("claim_count")),
        "score_rows": len(subledger.get("score_rows", [])),
        "accepted_support_claims": len(accepted_report.get("accepted_support_claims", [])),
        "preflight_blocked": accepted_report.get("preflight_blocked"),
    }

def run_lane(
    lane_id: str,
    *,
    stage: str,
    out_dir: Path | None,
    lane_def_dir: Path = DEFAULT_LANE_DEF_DIR,
    inventory_path: Path = DEFAULT_INVENTORY,
    comparator_registry_path: Path = DEFAULT_COMPARATOR_REGISTRY,
    promote_accepted: bool = False,
) -> dict[str, Any]:
    lane_defs = load_lane_defs(lane_def_dir)
    try:
        lane_def = lane_defs[lane_id]
    except KeyError as exc:
        raise LaneRunError(f"unknown lane {lane_id!r} in {lane_def_dir}") from exc
    inventory_lane = _inventory_lane(lane_id, inventory_path)
    if inventory_lane is not None and inventory_lane.get("config_id") != lane_def.get("config_id"):
        raise LaneRunError(f"lane {lane_id!r} config_id differs between lane_def and inventory")

    stages = _stage_list(stage)
    unsupported = [name for name in stages if name not in EXECUTABLE_STAGES]
    if unsupported:
        raise LaneRunError("unsupported stage(s): " + ", ".join(unsupported))
    if lane_def.get("status") == "accepted" and out_dir is None and not promote_accepted:
        raise LaneRunError("accepted lanes require --out unless --promote-accepted is set")

    results: list[dict[str, Any]] = []
    for stage_name in stages:
        argv = _command_stage_argv(stage_name, lane_def, inventory_lane)
        if argv is None and stage_name == "capture" and promote_accepted:
            try:
                from scripts.e4_parity.lane_acceptance_artifacts import build_lane_from_definition
            except ModuleNotFoundError:  # pragma: no cover - direct script execution
                from lane_acceptance_artifacts import build_lane_from_definition

            row = build_lane_from_definition(lane_def, inventory_lane)
            refresh_report = _refresh_promoted_bindings() if row.get("ok") else None
            results.append(
                {
                    "stage": stage_name,
                    "lane_id": lane_id,
                    "returncode": 0 if row.get("ok") else 1,
                    "artifact_writer": "run_lane",
                    "output_path": row.get("node_gate"),
                    "promotion_refresh": refresh_report,
                    "packet_report": row,
                }
            )
            if not row.get("ok"):
                break
            continue
        if argv is None:
            results.append(_metadata_stage_result(stage_name, lane_def, comparator_registry_path))
            continue
        try:
            command, accepted_path, output_path = _retarget_json_out(argv, out_dir)
        except LaneRunError:
            if stage_name == "capture":
                results.append(
                    {
                        "stage": stage_name,
                        "lane_id": lane_id,
                        "returncode": 0,
                        "skipped": True,
                        "reason": "capture argv has no --json-out contract; unsafe to run through generic lane runner",
                    }
                )
                continue
            raise
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        compatible_rewrite = _write_accepted_compatible_output(output_path, accepted_path)
        result: dict[str, Any] = {
            "stage": stage_name,
            "lane_id": lane_id,
            "command": command,
            "returncode": completed.returncode,
            "output_path": _display_path(output_path),
            "accepted_path": _display_path(accepted_path),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if compatible_rewrite:
            result["accepted_compatible_rewrite"] = True
        if output_path.exists():
            result["output_sha256"] = sha256_file(output_path)
        if accepted_path.exists():
            result["accepted_sha256"] = sha256_file(accepted_path)
            result["byte_parity"] = output_path.exists() and output_path.read_bytes() == accepted_path.read_bytes()
        results.append(result)
        if completed.returncode != 0:
            break
    ok = all(item["returncode"] == 0 for item in results)
    return {"ok": ok, "lane_id": lane_id, "stages": results}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an E4 lane from lane_def/inventory data.")
    parser.add_argument("--lane", required=True, help="lane_id from config/e4_lanes, or north-star for the WS-J lane set")
    parser.add_argument("--stage", choices=[*ALL_STAGES, "all"], default="all")
    parser.add_argument("--out", type=Path, default=None, help="scratch output root for all artifact writes")
    parser.add_argument("--promote-accepted", action="store_true", help="allow accepted-root artifact writes for promotion regeneration")
    parser.add_argument("--json", action="store_true", help="emit JSON result")
    parser.add_argument("--lane-def-dir", type=Path, default=DEFAULT_LANE_DEF_DIR)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--comparator-registry", type=Path, default=DEFAULT_COMPARATOR_REGISTRY)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    lane_ids = list(NORTH_STAR_LANE_IDS) if args.lane == "north-star" else [args.lane]
    rows: list[dict[str, Any]] = []
    exit_code = 0
    for lane_id in lane_ids:
        try:
            result = run_lane(
                lane_id,
                stage=args.stage,
                out_dir=args.out,
                lane_def_dir=args.lane_def_dir,
                inventory_path=args.inventory,
                comparator_registry_path=args.comparator_registry,
                promote_accepted=args.promote_accepted,
            )
        except LaneRunError as exc:
            payload = {"ok": False, "lane_id": lane_id, "error": str(exc)}
            rows.append(payload)
            exit_code = 2
            if not args.json:
                print(f"run_lane: {exc}", file=sys.stderr)
            break
        rows.append(result)
        if not result["ok"]:
            exit_code = 1
            break
    payload = {"ok": exit_code == 0, "lanes": rows} if len(lane_ids) > 1 else rows[0]
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for result in rows:
            if not result.get("ok"):
                continue
            for item in result["stages"]:
                output = item.get("output_path", "<metadata>")
                sha = item.get("output_sha256", "<missing>")
                print(f"{item['stage']} {item['lane_id']} rc={item['returncode']} output={output} sha={sha}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
