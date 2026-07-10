#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from jsonschema import Draft202012Validator

try:
    from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec
    from agentic_coder_prototype.conformance.c4_chain import validate_c4_chain
    from scripts.e4_parity import generate_lane_inventory
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, LaneDefValidationError, load_lane_def
    from scripts.e4_parity.lane_inventory_utils import DEFAULT_INVENTORY_PATH, load_inventory
    from scripts.e4_parity.metadata_non_normative import assert_lane_metadata_non_normative
    from scripts.e4_parity.validators.registries import RegistryValidationError, assert_registered
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec
    from agentic_coder_prototype.conformance.c4_chain import validate_c4_chain
    from scripts.e4_parity import generate_lane_inventory
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, LaneDefValidationError, load_lane_def
    from scripts.e4_parity.lane_inventory_utils import DEFAULT_INVENTORY_PATH, load_inventory
    from scripts.e4_parity.metadata_non_normative import assert_lane_metadata_non_normative
    from scripts.e4_parity.validators.registries import RegistryValidationError, assert_registered

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
SCHEMA_VERSION = "bb.lane_validation_report.v1"
GENERATED_AT_UTC = "2026-07-09T00:00:00Z"
LANE_MANIFEST_SCHEMA_PATH = (
    ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.lane_manifest.v1.schema.json"
)
MANIFEST_REVIEW_LINE_LIMIT = 150
MANIFEST_HARD_LINE_LIMIT = 300
MANIFEST_MAX_LINE_LENGTH = 120
CHECK_IDS = (
    "lane_def_schema_valid",
    "adapter_registered",
    "translator_registered",
    "comparator_registered",
    "capture_inputs_exist",
    "artifacts_root_valid",
    "inventory_row_consistent",
    "assertion_ids_unique",
    "claim_scope_complete",
    "reverify_command_executable",
    "accepted_artifact_hashes_fresh",
    "metadata_non_normative",
)
REQUIRED_SCOPE_KEYS = {"config_id", "lane_id", "run_id", "target_version", "provider_model", "sandbox_mode"}
INVENTORY_COMPARE_FIELDS = (
    "config_id",
    "claim_id",
    "kind",
    "status",
    "points",
    "target_family",
    "target_version",
    "run_id",
    "provider_model",
    "sandbox_mode",
    "builder",
    "ct",
    "reverify_command",
    "artifact_roles",
    "ledger_feature_ids",
    "score_row_id",
    "artifacts_root",
)


def _manifest_flow_style_errors(node: yaml.Node, pointer: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(node, yaml.MappingNode):
        if node.flow_style and len(node.value) > 1:
            errors.append(
                f"{pointer or '<root>'}: flow-style mapping has {len(node.value)} keys; "
                "lane manifests require block style"
            )
        for key_node, value_node in node.value:
            key = str(key_node.value)
            child_pointer = f"{pointer}/{key}" if pointer else f"/{key}"
            errors.extend(_manifest_flow_style_errors(value_node, child_pointer))
    elif isinstance(node, yaml.SequenceNode):
        for index, item_node in enumerate(node.value):
            child_pointer = f"{pointer}/{index}" if pointer else f"/{index}"
            errors.extend(_manifest_flow_style_errors(item_node, child_pointer))
    return errors


def load_lane_manifest(path: Path) -> dict[str, Any]:
    """Load and validate an author-owned lane manifest before lock resolution."""

    text = path.read_text(encoding="utf-8")
    if "sha256:" in text:
        raise LaneDefValidationError(
            f"{path}: manifest contains forbidden 'sha256:' substring; digests belong in the lane lock"
        )

    overlong = [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if len(line) > MANIFEST_MAX_LINE_LENGTH
    ]
    if overlong:
        raise LaneDefValidationError(
            f"{path}: manifest lines exceed {MANIFEST_MAX_LINE_LENGTH} characters: "
            + ", ".join(str(number) for number in overlong)
        )

    canonical_lines = sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if canonical_lines > MANIFEST_HARD_LINE_LIMIT:
        raise LaneDefValidationError(
            f"{path}: manifest has {canonical_lines} canonical lines; "
            f"maximum is {MANIFEST_HARD_LINE_LIMIT}"
        )
    if canonical_lines > MANIFEST_REVIEW_LINE_LIMIT:
        warnings.warn(
            f"{path}: manifest has {canonical_lines} canonical lines; "
            f"review budget is {MANIFEST_REVIEW_LINE_LIMIT}",
            UserWarning,
            stacklevel=2,
        )

    try:
        node = yaml.compose(text)
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LaneDefValidationError(f"{path}: invalid YAML: {exc}") from exc
    if node is None or not isinstance(payload, dict):
        raise LaneDefValidationError(f"{path}: manifest must contain a YAML mapping")
    flow_errors = _manifest_flow_style_errors(node)
    if flow_errors:
        raise LaneDefValidationError(f"{path}: " + "; ".join(flow_errors))

    schema = json.loads(LANE_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        (
            (
                "/" + "/".join(str(part) for part in error.absolute_path)
                if error.absolute_path
                else "<root>"
            )
            + f": {error.message}"
            for error in Draft202012Validator(schema).iter_errors(payload)
        )
    )
    if errors:
        raise LaneDefValidationError(f"{path}: " + "; ".join(errors))
    return payload


def _load_lane_source_for_validation(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    is_manifest = path.name.endswith((".manifest.yaml", ".manifest.yml")) or any(
        line.lstrip().startswith("schema_version:")
        and "bb.e4.lane_manifest." in line
        for line in text.splitlines()
    )
    if is_manifest:
        return load_lane_manifest(path)
    return load_lane_def(path)


def _repo_rel(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        try:
            return resolved.relative_to(WORKSPACE).as_posix()
        except ValueError:
            return resolved.as_posix()


def _resolve_ref(path_text: str) -> Path:
    raw = path_text.split("#", 1)[0]
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    base = WORKSPACE if raw.startswith("docs_tmp/") else ROOT
    return (base / raw).resolve()


def _flag_value(argv: Sequence[Any], flag: str) -> str | None:
    for index, item in enumerate(argv[:-1]):
        if item == flag and isinstance(argv[index + 1], str):
            return str(argv[index + 1])
    return None


def _command_argv(command: Any) -> list[str]:
    if isinstance(command, Mapping):
        argv = command.get("argv")
        if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
            return list(argv)
    return []


def _check(check_id: str, status: str, detail: str) -> dict[str, str]:
    return {"check_id": check_id, "status": status, "detail": detail}


def _pass(check_id: str, detail: str) -> dict[str, str]:
    return _check(check_id, "passed", detail)


def _fail(check_id: str, detail: str) -> dict[str, str]:
    return _check(check_id, "failed", detail)


def _skip(check_id: str, detail: str) -> dict[str, str]:
    return _check(check_id, "skipped", detail)


def _find_lane_path(lane_arg: str) -> Path:
    candidate = Path(lane_arg)
    if not candidate.is_absolute():
        direct = (ROOT / candidate).resolve()
        if direct.is_file():
            return direct
        candidate_path = (Path.cwd() / candidate).resolve()
        if candidate_path.is_file():
            return candidate_path
    elif candidate.is_file():
        return candidate.resolve()
    matches: list[Path] = []
    for path in sorted(DEFAULT_LANE_DEF_DIR.glob("*.yaml")):
        try:
            lane_def = load_lane_def(path)
        except Exception:
            continue
        if lane_def.get("lane_id") == lane_arg:
            matches.append(path.resolve())
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"lane id or path not found: {lane_arg}")
    raise ValueError(f"lane id {lane_arg!r} matched multiple lane definitions")


def _inventory_lane(lane_id: str) -> Mapping[str, Any] | None:
    inventory = load_inventory(DEFAULT_INVENTORY_PATH)
    matches = [lane for lane in inventory.get("lanes", []) if isinstance(lane, Mapping) and lane.get("lane_id") == lane_id]
    if len(matches) == 1:
        return matches[0]
    return None


def _registry_check(lane_def: Mapping[str, Any], check_id: str, value: str | None, expected_kind: str, label: str) -> dict[str, str]:
    if not value:
        if check_id == "adapter_registered":
            return _pass(check_id, "capture strategy does not require a capture adapter")
        return _fail(check_id, f"{label} is missing")
    try:
        assert_registered("e4_adapters", value, expected_kind=expected_kind)
    except RegistryValidationError as exc:
        return _fail(check_id, str(exc))
    return _pass(check_id, f"{label} {value!r} is registered as {expected_kind}")


def _capture_inputs_check(lane_def: Mapping[str, Any]) -> dict[str, str]:
    capture = lane_def.get("capture")
    inputs = capture.get("inputs") if isinstance(capture, Mapping) else None
    if not isinstance(inputs, list):
        return _fail("capture_inputs_exist", "capture.inputs must be a list")
    missing = [str(item) for item in inputs if not isinstance(item, str) or not _resolve_ref(item).exists()]
    if missing:
        return _fail("capture_inputs_exist", "missing capture inputs: " + ", ".join(missing[:8]))
    return _pass("capture_inputs_exist", f"{len(inputs)} capture input(s) exist")


def _artifacts_root_check(lane_def: Mapping[str, Any]) -> dict[str, str]:
    root = lane_def.get("artifacts_root")
    if not isinstance(root, str) or not root:
        return _fail("artifacts_root_valid", "artifacts_root is missing")
    resolved = _resolve_ref(root)
    if not resolved.exists() or not resolved.is_dir():
        return _fail("artifacts_root_valid", f"artifacts_root does not exist as directory: {root}")
    return _pass("artifacts_root_valid", f"artifacts_root exists: {_repo_rel(resolved)}")


def _inventory_check(lane_def: Mapping[str, Any], inventory_lane: Mapping[str, Any] | None) -> dict[str, str]:
    if inventory_lane is None:
        return _fail("inventory_row_consistent", "canonical inventory row is missing")
    try:
        generated = generate_lane_inventory.lane_inventory_row(lane_def)
    except Exception as exc:
        return _fail("inventory_row_consistent", f"unable to generate inventory row: {exc}")
    mismatches = [field for field in INVENTORY_COMPARE_FIELDS if generated.get(field) != inventory_lane.get(field)]
    if mismatches:
        return _fail("inventory_row_consistent", "inventory mismatches: " + ", ".join(mismatches))
    return _pass("inventory_row_consistent", "generated lane inventory fields match canonical inventory row")


def _assertion_ids_check(lane_def: Mapping[str, Any]) -> dict[str, str]:
    containers = (
        (
            "acceptance",
            lane_def.get("acceptance", {}).get("assertions")
            if isinstance(lane_def.get("acceptance"), Mapping)
            else None,
        ),
        (
            "compare",
            lane_def.get("compare", {}).get("config", {}).get("assertions")
            if isinstance(lane_def.get("compare"), Mapping)
            and isinstance(lane_def.get("compare", {}).get("config"), Mapping)
            else None,
        ),
    )
    duplicate_ids: list[str] = []
    assertion_count = 0
    for namespace, container in containers:
        if not isinstance(container, list):
            continue
        ids = [
            str(row["id"])
            for row in container
            if isinstance(row, Mapping) and isinstance(row.get("id"), str) and row["id"]
        ]
        assertion_count += len(ids)
        duplicate_ids.extend(f"{namespace}.{item}" for item in sorted({item for item in ids if ids.count(item) > 1}))
    if duplicate_ids:
        return _fail("assertion_ids_unique", "duplicate assertion ids: " + ", ".join(duplicate_ids))
    return _pass(
        "assertion_ids_unique",
        f"{assertion_count} explicit assertion id(s) are unique",
    )


def _claim_paths(lane_def: Mapping[str, Any]) -> tuple[Path | None, Path | None, str | None]:
    argv = _command_argv(lane_def.get("reverify_command"))
    support = _flag_value(argv, "--support-claim")
    evidence = _flag_value(argv, "--evidence-manifest")
    config_id = str(lane_def.get("config_id")) if lane_def.get("config_id") else None
    return (_resolve_ref(support) if support else None, _resolve_ref(evidence) if evidence else None, config_id)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _claim_scope_check(lane_def: Mapping[str, Any]) -> dict[str, str]:
    support_path, _evidence_path, _config_id = _claim_paths(lane_def)
    if support_path is None or not support_path.is_file():
        return _fail("claim_scope_complete", "support claim path from reverify_command is missing")
    try:
        support = _load_json(support_path)
    except Exception as exc:
        return _fail("claim_scope_complete", f"unable to load support claim: {exc}")
    scope = support.get("scope") if isinstance(support, Mapping) else None
    if not isinstance(scope, Mapping):
        return _fail("claim_scope_complete", "support claim scope is missing")
    missing = sorted(REQUIRED_SCOPE_KEYS - set(scope))
    if missing:
        return _fail("claim_scope_complete", "support claim scope missing: " + ", ".join(missing))
    mismatches: list[str] = []
    expected = {
        "config_id": lane_def.get("config_id"),
        "lane_id": lane_def.get("lane_id"),
        "target_version": lane_def.get("target_version"),
    }
    run = lane_def.get("run") if isinstance(lane_def.get("run"), Mapping) else {}
    expected.update({key: run.get(key) for key in ("run_id", "provider_model", "sandbox_mode")})
    for key, value in expected.items():
        if value and scope.get(key) != value:
            mismatches.append(key)
    if mismatches:
        return _fail("claim_scope_complete", "support claim scope mismatches lane definition: " + ", ".join(mismatches))
    return _pass("claim_scope_complete", "support claim scope contains required anchors and matches lane definition")


def _reverify_check(lane_def: Mapping[str, Any]) -> dict[str, str]:
    argv = _command_argv(lane_def.get("reverify_command"))
    if not argv:
        return _fail("reverify_command_executable", "reverify_command.argv is missing")
    json_out = _flag_value(argv, "--json-out")
    if not json_out:
        return _fail("reverify_command_executable", "reverify_command.argv must include --json-out")
    script = next((item for item in argv if isinstance(item, str) and item.endswith(".py")), None)
    if script is None:
        return _fail("reverify_command_executable", "reverify_command.argv has no Python script")
    script_path = _resolve_ref(script)
    if not script_path.is_file():
        return _fail("reverify_command_executable", f"reverify script is missing: {script}")
    return _pass("reverify_command_executable", f"reverify script exists and declares --json-out: {_repo_rel(script_path)}")


def _accepted_hashes_check(lane_def: Mapping[str, Any]) -> dict[str, str]:
    if lane_def.get("status") != "accepted":
        return _skip("accepted_artifact_hashes_fresh", "lane is not accepted")
    support_path, evidence_path, config_id = _claim_paths(lane_def)
    if support_path is None or evidence_path is None or config_id is None:
        return _fail("accepted_artifact_hashes_fresh", "accepted lane reverify command must name support claim, evidence manifest, and config_id")
    try:
        report = validate_c4_chain(
            repo_root=ROOT,
            freeze_manifest_path=ROOT / "config/e4_target_freeze_manifest.yaml",
            config_id=config_id,
            support_claim_path=support_path,
            evidence_manifest_path=evidence_path,
            rerun_comparators=False,
            no_rerun_reason="validate_lane accepted_artifact_hashes_fresh checks stored artifact refs only",
        )
    except Exception as exc:
        return _fail("accepted_artifact_hashes_fresh", f"C4 chain freshness validation raised: {exc}")
    if report.get("ok") is not True:
        errors = report.get("errors") if isinstance(report.get("errors"), list) else []
        return _fail("accepted_artifact_hashes_fresh", "; ".join(str(error) for error in errors[:4]) or "C4 chain freshness validation failed")
    return _pass("accepted_artifact_hashes_fresh", "stored accepted artifact refs and hashes are fresh")


def _metadata_check() -> dict[str, str]:
    try:
        result = assert_lane_metadata_non_normative()
    except Exception as exc:
        return _fail("metadata_non_normative", f"metadata sentinel failed: {exc}")
    return _pass("metadata_non_normative", f"metadata sentinel passed for {result['lane_id']}")


def validate_lane(lane_arg: str) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    lane_path: Path
    lane_def: dict[str, Any] | None = None
    lane_id = "unknown_lane"
    try:
        lane_path = _find_lane_path(lane_arg)
    except Exception as exc:
        lane_path = (ROOT / lane_arg).resolve() if not Path(lane_arg).is_absolute() else Path(lane_arg).resolve()
        checks.append(_fail("lane_def_schema_valid", str(exc)))
        for check_id in CHECK_IDS[1:-1]:
            checks.append(_skip(check_id, "lane definition did not load"))
        checks.append(_metadata_check())
    else:
        try:
            lane_def = _load_lane_source_for_validation(lane_path)
            lane_id = str(lane_def["lane_id"])
            checks.append(_pass("lane_def_schema_valid", "lane definition schema is valid"))
        except LaneDefValidationError as exc:
            lane_id = lane_path.stem
            checks.append(_fail("lane_def_schema_valid", str(exc)))
            lane_def = None
        except Exception as exc:
            lane_id = lane_path.stem
            checks.append(_fail("lane_def_schema_valid", str(exc)))
            lane_def = None
        if lane_def is None:
            for check_id in CHECK_IDS[1:-1]:
                checks.append(_skip(check_id, "lane definition did not load"))
            checks.append(_metadata_check())
        else:
            capture = lane_def.get("capture") if isinstance(lane_def.get("capture"), Mapping) else {}
            normalize = lane_def.get("normalize") if isinstance(lane_def.get("normalize"), Mapping) else {}
            compare = lane_def.get("compare") if isinstance(lane_def.get("compare"), Mapping) else {}
            inventory_lane = _inventory_lane(lane_id)
            checks.extend(
                [
                    _registry_check(lane_def, "adapter_registered", str(capture.get("adapter")) if capture.get("strategy") == "adapter" and capture.get("adapter") else None, "capture_adapter", "capture.adapter"),
                    _registry_check(lane_def, "translator_registered", str(normalize.get("translator")) if normalize.get("translator") else None, "translator", "normalize.translator"),
                    _registry_check(lane_def, "comparator_registered", str(compare.get("comparator")) if compare.get("comparator") else None, "comparator", "compare.comparator"),
                    _capture_inputs_check(lane_def),
                    _artifacts_root_check(lane_def),
                    _inventory_check(lane_def, inventory_lane),
                    _assertion_ids_check(lane_def),
                    _claim_scope_check(lane_def),
                    _reverify_check(lane_def),
                    _accepted_hashes_check(lane_def),
                    _metadata_check(),
                ]
            )
    by_id = {check["check_id"]: check for check in checks}
    ordered = [by_id[check_id] for check_id in CHECK_IDS]
    failed = any(check["status"] == "failed" for check in ordered)
    report = {
        "schema_version": SCHEMA_VERSION,
        "lane_id": lane_id,
        "lane_def_path": _repo_rel(lane_path),
        "generated_at_utc": GENERATED_AT_UTC,
        "checks": ordered,
        "ok": not failed,
    }
    return finalize_record(get_spec(SCHEMA_VERSION), report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one E4 lane definition and its accepted evidence bindings.")
    parser.add_argument("--lane", required=True, help="Lane id or lane definition path.")
    parser.add_argument("--json", dest="json_out", help="Optional output JSON path.")
    args = parser.parse_args(argv)
    report = validate_lane(args.lane)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
