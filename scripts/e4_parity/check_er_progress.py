#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal, Mapping

from jsonschema import Draft202012Validator

try:
    from scripts.e4_parity.validators.hash_utils import sha256_file
    from scripts.e4_parity.validators.gate_errors import apply_gate_error_envelope, gate_exit_code
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators.hash_utils import sha256_file
    from validators.gate_errors import apply_gate_error_envelope, gate_exit_code

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ROOT.parent
DEFAULT_PROGRESS = WORKSPACE_ROOT / "docs_tmp" / "phase_16" / "BB_ER_PROGRESS.json"
SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.er.progress.v1.schema.json"
DERIVATIVE_ROOTS_PATH = ROOT / "docs" / "contracts" / "e4_derivative_roots.json"
PinPolicy = Literal["v1", "v2"]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    prefix = f"schema.{path}: " if path else "schema: "
    return f"{prefix}{error.message}"


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_evidence_path(raw_path: str, *, workspace_root: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return workspace_root / path


def _load_derivative_globs() -> list[str]:
    payload = _load_json(DERIVATIVE_ROOTS_PATH)
    globs = payload.get("globs") if isinstance(payload, Mapping) else None
    if not isinstance(globs, list) or any(not isinstance(item, str) or not item for item in globs):
        raise ValueError(f"{_display(DERIVATIVE_ROOTS_PATH)} must contain non-empty string globs")
    return globs


def _derivative_glob_for_path(raw_path: str, globs: list[str]) -> str | None:
    normalized = raw_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    for pattern in globs:
        if fnmatchcase(normalized, pattern):
            return pattern
    return None

def _validate_schema(progress: Mapping[str, Any]) -> list[str]:
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    return [_format_error(error) for error in sorted(validator.iter_errors(progress), key=lambda err: (tuple(str(part) for part in err.absolute_path), err.message))]


def _validate_arithmetic(progress: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    workstreams = progress.get("workstreams", [])
    if not isinstance(workstreams, list):
        return errors

    total_available = 0
    total_done = 0
    total_blocked = 0
    seen_ws: set[str] = set()
    seen_items: set[str] = set()
    for ws_index, ws in enumerate(workstreams):
        if not isinstance(ws, Mapping):
            continue
        ws_id = str(ws.get("ws_id", f"#{ws_index}"))
        if ws_id in seen_ws:
            errors.append(f"workstreams[{ws_index}].ws_id duplicates {ws_id!r}")
        seen_ws.add(ws_id)
        items = ws.get("items", [])
        if not isinstance(items, list):
            continue
        available = sum(item.get("points", 0) for item in items if isinstance(item, Mapping) and isinstance(item.get("points"), int))
        done = sum(item.get("points", 0) for item in items if isinstance(item, Mapping) and item.get("status") == "done" and isinstance(item.get("points"), int))
        blocked = sum(item.get("points", 0) for item in items if isinstance(item, Mapping) and item.get("status") == "blocked" and isinstance(item.get("points"), int))
        if ws.get("points_available") != available:
            errors.append(f"{ws_id}: points_available {ws.get('points_available')!r} != item point sum {available}")
        if ws.get("points_done") != done:
            errors.append(f"{ws_id}: points_done {ws.get('points_done')!r} != done item point sum {done}")
        total_available += available
        total_done += done
        total_blocked += blocked
        for item_index, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("item_id")
            if isinstance(item_id, str):
                if item_id in seen_items:
                    errors.append(f"{ws_id}.items[{item_index}].item_id duplicates {item_id!r}")
                seen_items.add(item_id)
    totals = progress.get("totals", {})
    if isinstance(totals, Mapping):
        if totals.get("points_total") != total_available:
            errors.append(f"totals.points_total {totals.get('points_total')!r} != workstream point sum {total_available}")
        if totals.get("points_done") != total_done:
            errors.append(f"totals.points_done {totals.get('points_done')!r} != workstream done sum {total_done}")
        if totals.get("points_blocked") != total_blocked:
            errors.append(f"totals.points_blocked {totals.get('points_blocked')!r} != blocked item point sum {total_blocked}")
    return errors


def _validate_evidence(progress: Mapping[str, Any], *, workspace_root: Path, verify_hashes: bool, pin_policy: PinPolicy) -> list[str]:
    errors: list[str] = []
    workstreams = progress.get("workstreams", [])
    if not isinstance(workstreams, list):
        return errors
    derivative_globs = _load_derivative_globs() if pin_policy == "v2" else []
    for ws in workstreams:
        if not isinstance(ws, Mapping):
            continue
        for item in ws.get("items", []):
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("item_id", "<unknown>")
            evidence = item.get("evidence", [])
            if item.get("status") == "done" and not evidence:
                errors.append(f"{item_id}: done item must include at least one evidence entry")
            if not isinstance(evidence, list):
                continue
            for entry_index, entry in enumerate(evidence):
                if not isinstance(entry, Mapping):
                    continue
                raw_path = entry.get("path")
                expected_sha = entry.get("sha256")
                if not isinstance(raw_path, str) or not isinstance(expected_sha, str):
                    continue
                if derivative_glob := _derivative_glob_for_path(raw_path, derivative_globs):
                    errors.append(f"{item_id}.evidence[{entry_index}]: derivative evidence path {raw_path} matches pin-policy v2 deny glob {derivative_glob}")
                path = _resolve_evidence_path(raw_path, workspace_root=workspace_root)
                if not path.is_file():
                    errors.append(f"{item_id}.evidence[{entry_index}]: missing evidence file {_display(path)}")
                    continue
                if verify_hashes:
                    actual_sha = sha256_file(path)
                    if actual_sha != expected_sha:
                        errors.append(f"{item_id}.evidence[{entry_index}]: sha256 {expected_sha} != current {actual_sha}")
    return errors


def check_progress(progress_path: Path = DEFAULT_PROGRESS, *, workspace_root: Path = WORKSPACE_ROOT, verify_hashes: bool = True, pin_policy: PinPolicy = "v1") -> dict[str, Any]:
    progress = _load_json(progress_path)
    if not isinstance(progress, Mapping):
        errors = ["progress JSON must be an object"]
    else:
        errors = []
        errors.extend(_validate_schema(progress))
        errors.extend(_validate_arithmetic(progress))
        errors.extend(_validate_evidence(progress, workspace_root=workspace_root, verify_hashes=verify_hashes, pin_policy=pin_policy))
    report = {
        "schema_version": "bb.er.progress_check_report.v1",
        "progress_path": _display(progress_path),
        "schema_path": _display(SCHEMA_PATH),
        "verify_hashes": verify_hashes,
        "pin_policy": pin_policy,
        "ok": not errors,
        "error_count": len(errors),
        "errors": errors,
    }
    apply_gate_error_envelope(report, "er_progress")
    if isinstance(progress, Mapping):
        totals = progress.get("totals")
        if isinstance(totals, Mapping):
            report["points_done"] = totals.get("points_done")
            report["points_total"] = totals.get("points_total")
        report["revision"] = progress.get("revision")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate BB_ER_PROGRESS schema, arithmetic, and evidence invariants.")
    parser.add_argument("--progress", default=str(DEFAULT_PROGRESS))
    parser.add_argument("progress_path", nargs="?", help="Progress JSON path; positional form is kept for the documented G-PROG gate.")
    parser.add_argument("--workspace-root", default=str(WORKSPACE_ROOT))
    parser.add_argument("--json-out")
    parser.add_argument("--no-verify-hashes", action="store_true")
    parser.add_argument("--pin-policy", choices=("v1", "v2"), default="v1")
    args = parser.parse_args(argv)

    try:
        progress_path = Path(args.progress_path or args.progress)
        report = check_progress(
            progress_path,
            workspace_root=Path(args.workspace_root),
            verify_hashes=not args.no_verify_hashes,
            pin_policy=args.pin_policy,
        )
    except Exception as exc:
        report = {
            "schema_version": "bb.er.progress_check_report.v1",
            "progress_path": args.progress,
            "schema_path": _display(SCHEMA_PATH),
            "verify_hashes": not args.no_verify_hashes,
            "pin_policy": args.pin_policy,
            "ok": False,
            "error_count": 1,
            "errors": [str(exc)],
        }
        apply_gate_error_envelope(report, "er_progress")

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return gate_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
