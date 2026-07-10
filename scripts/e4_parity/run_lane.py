#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import importlib
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from scripts.e4_parity.lane_runtime import LANE_SHARED_READ_ONLY_PATHS, sha256_file
    from scripts.e4_parity.stage_contracts import STAGES_BY_KIND, check_stage_report
    from scripts.e4_parity.validators.registries import load_registry
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
    from scripts.e4_parity.lane_runtime import LANE_SHARED_READ_ONLY_PATHS, sha256_file
    from scripts.e4_parity.stage_contracts import STAGES_BY_KIND, check_stage_report
    from scripts.e4_parity.validators.registries import load_registry

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
_DERIVED_OUTPUT_PREFIXES = (
    "docs/conformance/support_claims/",
    "artifacts/conformance/node_gate/",
    "artifacts/conformance/e4_primitive_projection/",
)
_REGEN_SCRATCH_ROOT = ROOT / "tmp" / "e4_regen_capture"


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


def _claim_argv(lane_def: Mapping[str, Any], inventory_lane: Mapping[str, Any] | None) -> list[str] | None:
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
    return None


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

def _capture_adapter_callable(lane_def: Mapping[str, Any]) -> Any | None:
    capture = lane_def.get("capture")
    if not isinstance(capture, Mapping) or capture.get("strategy") != "adapter":
        return None
    adapter_id = capture.get("adapter")
    if not isinstance(adapter_id, str) or not adapter_id:
        raise LaneRunError(f"lane {lane_def['lane_id']!r} adapter capture requires capture.adapter")
    registry = load_registry("e4_adapters")
    matches = [
        entry
        for entry in registry.get("entries", [])
        if isinstance(entry, Mapping)
        and entry.get("id") == adapter_id
        and isinstance(entry.get("metadata"), Mapping)
        and entry["metadata"].get("kind") == "capture_adapter"
    ]
    if len(matches) != 1:
        raise LaneRunError(f"lane {lane_def['lane_id']!r} capture.adapter {adapter_id!r} must name one active capture_adapter")
    impl = matches[0]["metadata"].get("impl")
    if not isinstance(impl, str) or ":" not in impl:
        raise LaneRunError(f"capture.adapter {adapter_id!r} has invalid impl {impl!r}")
    module_name, callable_name = impl.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, callable_name)


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


def _declared_non_execution(
    stage_name: str,
    lane_def: Mapping[str, Any],
    registry_path: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {"stage": stage_name, "lane_id": lane_def["lane_id"], "returncode": 0}
    kind = lane_def.get("kind")
    applicable_stages = STAGES_BY_KIND.get(kind) if isinstance(kind, str) else None
    if applicable_stages is not None and stage_name not in applicable_stages:
        result.update(
            outcome="not_applicable",
            manifest_rule="/kind",
            reused_inputs=None,
            report_ref=None,
            lock_sha256=None,
            detail=f"{stage_name} is not part of the {kind} lane stage table",
        )
        return result

    declaration = lane_def.get(stage_name)
    if declaration is False:
        result.update(
            outcome="disabled_by_manifest",
            manifest_rule=f"/{stage_name}",
            reused_inputs=None,
            report_ref=None,
            lock_sha256=None,
            detail=f"{stage_name} is disabled by its manifest declaration",
        )
        return result
    if isinstance(declaration, Mapping):
        if declaration.get("enabled") is False:
            result.update(
                outcome="disabled_by_manifest",
                manifest_rule=f"/{stage_name}/enabled",
                reused_inputs=None,
                report_ref=None,
                lock_sha256=None,
                detail=f"{stage_name} is disabled by its manifest declaration",
            )
            return result
        if declaration.get("mode") == "off":
            result.update(
                outcome="disabled_by_manifest",
                manifest_rule=f"/{stage_name}/mode",
                reused_inputs=None,
                report_ref=None,
                lock_sha256=None,
                detail=f"{stage_name} is disabled by its manifest declaration",
            )
            return result

    reused_paths: list[str] = []
    manifest_rule: str | None = None
    if stage_name == "capture" and isinstance(declaration, Mapping):
        if declaration.get("strategy") in {"replay_dump", "runtime_records"}:
            manifest_rule = "/capture/strategy"
            reused_paths = [path for path in declaration.get("inputs", []) if isinstance(path, str)]
        result["strategy"] = declaration.get("strategy")
        result["inputs"] = list(declaration.get("inputs", []))
        result["artifacts_root"] = lane_def.get("artifacts_root")
    elif stage_name == "replay" and isinstance(declaration, Mapping):
        if declaration.get("mode") == "stored":
            manifest_rule = "/replay/mode"
            session = declaration.get("session")
            reused_paths = [session] if isinstance(session, str) and session else []
        result["comparator_class"] = declaration.get("comparator_class")
    elif stage_name == "normalize" and isinstance(declaration, Mapping):
        result["translator"] = declaration.get("translator")
    elif stage_name == "compare" and isinstance(declaration, Mapping):
        entry = _comparator_entry(lane_def, registry_path)
        result["comparator_id"] = entry.get("comparator_id")
        result["entrypoint"] = entry.get("entrypoint")

    if manifest_rule is not None:
        reused_inputs: list[dict[str, str]] = []
        missing_paths: list[str] = []
        for logical_path in reused_paths:
            artifact_path = _resolve_repo_path(logical_path)
            if artifact_path.is_file():
                reused_inputs.append({"path": logical_path, "sha256": sha256_file(artifact_path)})
            else:
                missing_paths.append(logical_path)
        if reused_inputs and not missing_paths:
            result.update(
                outcome="reused_stored_result",
                manifest_rule=manifest_rule,
                reused_inputs=reused_inputs,
                report_ref=None,
                lock_sha256=None,
                detail=f"{stage_name} reused author-declared stored artifacts",
            )
            return result
        detail = (
            f"{stage_name} declared stored reuse without reusable artifact provenance"
            if not missing_paths
            else f"{stage_name} declared stored reuse but artifacts are missing: {', '.join(missing_paths)}"
        )
    else:
        detail = f"{stage_name} did not execute and has no author declaration allowing non-execution"
    result.update(
        returncode=1,
        outcome="executed_fail",
        manifest_rule=None,
        reused_inputs=None,
        report_ref=None,
        lock_sha256=None,
        detail=detail,
    )
    return result


def _finalize_stage_result(
    result: dict[str, Any],
    lane_def: Mapping[str, Any],
    *,
    executed: bool,
) -> dict[str, Any]:
    if executed:
        returncode = int(result.get("returncode", 1))
        report_ref = result.get("output_path")
        result.update(
            outcome="executed_pass" if returncode == 0 else "executed_fail",
            manifest_rule=None,
            reused_inputs=None,
            report_ref=report_ref if isinstance(report_ref, str) and report_ref else None,
            lock_sha256=None,
            detail=(
                f"{result['stage']} executed successfully"
                if returncode == 0
                else f"{result['stage']} execution failed with return code {returncode}"
            ),
        )
    errors = check_stage_report(result, lane_def)
    result["honesty_errors"] = errors
    if errors or result.get("outcome") == "executed_fail":
        result["returncode"] = int(result.get("returncode") or 1)
    return result



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

def _capture_owned_paths(lane_def: Mapping[str, Any]) -> tuple[str, ...]:
    outputs: list[str] = []
    artifacts_root = lane_def.get("artifacts_root")
    if isinstance(artifacts_root, str):
        outputs.append(artifacts_root)
    capture = lane_def.get("capture")
    inputs = capture.get("inputs") if isinstance(capture, Mapping) else None
    declared_inputs = frozenset(item for item in inputs if isinstance(item, str)) if isinstance(inputs, list) else frozenset()
    normalize = lane_def.get("normalize")
    config = normalize.get("config") if isinstance(normalize, Mapping) else None
    roles = config.get("roles") if isinstance(config, Mapping) else None
    if isinstance(roles, Mapping):
        for value in roles.values():
            if (
                isinstance(value, str)
                and not value.startswith(_DERIVED_OUTPUT_PREFIXES)
                and value not in LANE_SHARED_READ_ONLY_PATHS
                # Role paths declared as capture inputs are preserved sources
                # (probe scripts, raw session captures, static agent configs),
                # never capture-owned outputs.
                and value not in declared_inputs
            ):
                outputs.append(value)
    return tuple(dict.fromkeys(outputs))


def _scratch_path(scratch_root: Path, logical_path: str) -> Path:
    resolved = _resolve_repo_path(logical_path)
    try:
        return scratch_root / resolved.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return scratch_root / resolved.name


def _promote_capture_outputs(lane_def: Mapping[str, Any], scratch_root: Path) -> list[str]:
    promoted: list[str] = []
    covered_directories: list[Path] = []
    for logical_path in _capture_owned_paths(lane_def):
        destination = _resolve_repo_path(logical_path)
        if any(destination == directory or directory in destination.parents for directory in covered_directories):
            continue
        source = _scratch_path(scratch_root, logical_path)
        if source.is_dir():
            # Overlay merge: scratch owns regenerated files, but preserved raw
            # evidence absent from scratch (target probes, raw run outputs,
            # session captures) MUST survive promotion.
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, dirs_exist_ok=True)
            covered_directories.append(destination)
        elif source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        else:
            raise LaneRunError(f"capture scratch output missing: {source}")
        promoted.append(logical_path)
    return promoted


def _run_isolated_capture(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any] | None,
    adapter_capture: Any | None,
) -> dict[str, Any]:
    lane_id = str(lane_def["lane_id"])
    scratch_root = _REGEN_SCRATCH_ROOT / lane_id
    if scratch_root.exists():
        shutil.rmtree(scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    if adapter_capture is not None:
        if not getattr(adapter_capture, "supports_scratch_out_dir", False):
            raise LaneRunError(
                f"lane {lane_id!r} capture adapter does not support isolated regeneration writes"
            )
        row = adapter_capture(
            lane_def,
            inventory_lane,
            promote_accepted=False,
            out_dir=scratch_root,
        )
    else:
        try:
            from scripts.e4_parity.lane_acceptance_artifacts import build_lane_from_definition
        except ModuleNotFoundError:  # pragma: no cover - direct script execution
            from lane_acceptance_artifacts import build_lane_from_definition
        row = build_lane_from_definition(lane_def, inventory_lane, output_root=scratch_root)
    promoted = _promote_capture_outputs(lane_def, scratch_root) if row.get("ok") else []
    return {
        "stage": "capture",
        "lane_id": lane_id,
        "returncode": 0 if row.get("ok") else 1,
        "artifact_writer": str(lane_def.get("capture", {}).get("adapter") or "run_lane"),
        "output_path": row.get("node_gate"),
        "promotion_refresh": {"skipped": True, "reason": "derived writes deferred to canonical DAG owners"},
        "packet_report": row,
        "promoted_paths": promoted,
        "scratch_root": _display_path(scratch_root),
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
    defer_promotion_refresh: bool = False,
    defer_derived_writes: bool = False,
):
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
    if defer_derived_writes and (
        stage != "capture"
        or not promote_accepted
        or not defer_promotion_refresh
        or out_dir is not None
    ):
        raise LaneRunError(
            "--defer-derived-writes requires --stage capture --promote-accepted "
            "--defer-promotion-refresh and no --out"
        )

    results: list[dict[str, Any]] = []
    blocked_by: str | None = None
    for stage_name in stages:
        if blocked_by is not None:
            results.append(
                _finalize_stage_result(
                    {
                        "stage": stage_name,
                        "lane_id": lane_id,
                        "returncode": 1,
                        "outcome": "executed_fail",
                        "manifest_rule": None,
                        "reused_inputs": None,
                        "report_ref": None,
                        "lock_sha256": None,
                        "detail": f"{stage_name} did not execute because {blocked_by} failed",
                    },
                    lane_def,
                    executed=False,
                )
            )
            continue
        adapter_capture = _capture_adapter_callable(lane_def) if stage_name == "capture" else None
        if defer_derived_writes:
            isolated_result = _finalize_stage_result(
                _run_isolated_capture(lane_def, inventory_lane, adapter_capture),
                lane_def,
                executed=True,
            )
            results.append(isolated_result)
            if isolated_result["returncode"] != 0:
                blocked_by = stage_name
                continue
            continue
        if adapter_capture is not None:
            if not promote_accepted and out_dir is not None and not getattr(adapter_capture, "supports_scratch_out_dir", False):
                raise LaneRunError(
                    f"lane {lane_id!r} capture.adapter {lane_def.get('capture', {}).get('adapter')!r} does not declare scratch out_dir support"
                )
            row = adapter_capture(lane_def, inventory_lane, promote_accepted=promote_accepted, out_dir=out_dir)
            refresh_report = _refresh_promoted_bindings() if row.get("ok") and promote_accepted and not defer_promotion_refresh else {"skipped": True, "reason": "deferred by --defer-promotion-refresh"} if row.get("ok") and promote_accepted else None
            adapter_result = _finalize_stage_result(
                {
                    "stage": stage_name,
                    "lane_id": lane_id,
                    "returncode": 0 if row.get("ok") else 1,
                    "artifact_writer": str(lane_def.get("capture", {}).get("adapter")),
                    "output_path": row.get("node_gate"),
                    "promotion_refresh": refresh_report,
                    "packet_report": row,
                },
                lane_def,
                executed=True,
            )
            results.append(adapter_result)
            if adapter_result["returncode"] != 0:
                blocked_by = stage_name
                continue
            continue
        argv = _command_stage_argv(stage_name, lane_def, inventory_lane)
        if argv is None and stage_name == "capture" and promote_accepted:
            try:
                from scripts.e4_parity.lane_acceptance_artifacts import build_lane_from_definition
            except ModuleNotFoundError:  # pragma: no cover - direct script execution
                from lane_acceptance_artifacts import build_lane_from_definition

            row = build_lane_from_definition(lane_def, inventory_lane)
            refresh_report = _refresh_promoted_bindings() if row.get("ok") and not defer_promotion_refresh else {"skipped": True, "reason": "deferred by --defer-promotion-refresh"} if row.get("ok") else None
            builder_result = _finalize_stage_result(
                {
                    "stage": stage_name,
                    "lane_id": lane_id,
                    "returncode": 0 if row.get("ok") else 1,
                    "artifact_writer": "run_lane",
                    "output_path": row.get("node_gate"),
                    "promotion_refresh": refresh_report,
                    "packet_report": row,
                },
                lane_def,
                executed=True,
            )
            results.append(builder_result)
            if builder_result["returncode"] != 0:
                blocked_by = stage_name
                continue
            continue
        if argv is None:
            metadata_result = _finalize_stage_result(
                _declared_non_execution(stage_name, lane_def, comparator_registry_path),
                lane_def,
                executed=False,
            )
            results.append(metadata_result)
            if metadata_result["returncode"] != 0:
                blocked_by = stage_name
            continue
        try:
            command, accepted_path, output_path = _retarget_json_out(argv, out_dir)
        except LaneRunError:
            if stage_name == "capture":
                metadata_result = _finalize_stage_result(
                    _declared_non_execution(stage_name, lane_def, comparator_registry_path),
                    lane_def,
                    executed=False,
                )
                results.append(metadata_result)
                if metadata_result["returncode"] != 0:
                    blocked_by = stage_name
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
        executed_result = _finalize_stage_result(result, lane_def, executed=True)
        results.append(executed_result)
        if executed_result["returncode"] != 0:
            blocked_by = stage_name
    ok = all(item["returncode"] == 0 for item in results)
    return {"ok": ok, "lane_id": lane_id, "stages": results}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an E4 lane from lane_def/inventory data.")
    parser.add_argument("--lane", required=True, help="lane_id from config/e4_lanes, or north-star for the WS-J lane set")
    parser.add_argument("--stage", choices=[*ALL_STAGES, "all"], default="all")
    parser.add_argument("--out", type=Path, default=None, help="scratch output root for all artifact writes")
    parser.add_argument("--promote-accepted", action="store_true", help="allow accepted-root artifact writes for promotion regeneration")
    parser.add_argument("--defer-promotion-refresh", action="store_true", help="skip immediate promoted-binding refresh; use only inside orchestrators that run explicit catalog/support refresh stages later")
    parser.add_argument(
        "--defer-derived-writes",
        action="store_true",
        help="isolate capture-derived claims/manifests/node gates and promote only lane-owned outputs",
    )
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
                defer_promotion_refresh=args.defer_promotion_refresh,
                defer_derived_writes=args.defer_derived_writes,
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
