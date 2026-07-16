#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e4_parity import regenerate_evidence as regen  # noqa: E402
from scripts.e4_parity.validators.hash_utils import sha256_file, sha256_json  # noqa: E402

FAILURE_CLASSES = (
    "pin_stale",
    "semantic",
    "io_missing",
    "schema_invalid",
    "graph_invariant",
    "comparator_failure",
    "drift_unexplained",
)


def _final_readiness_module() -> Any:
    from scripts.e4_parity import build_e4_final_readiness_packet

    return build_e4_final_readiness_packet


def __getattr__(name: str) -> Any:
    if name == "final_readiness":
        return _final_readiness_module()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _sha256_path(path: Path) -> str:
    return sha256_file(path)


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        try:
            return "../" + resolved.relative_to(ROOT.parent.resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()


def _is_glob(value: str) -> bool:
    return any(char in value for char in "*?[")


def _add_watch_path(paths: set[Path], path: Path) -> None:
    resolved = path.resolve()
    if resolved.is_file():
        paths.add(resolved)
    elif resolved.is_dir():
        paths.update(
            candidate.resolve()
            for candidate in resolved.rglob("*")
            if candidate.is_file()
        )


def _watch_set() -> list[Path]:
    paths: set[Path] = set()
    _add_watch_path(paths, _final_readiness_module().SCORE_AUTHORITY_PATH)
    declared: list[str] = [value for stage in regen.STAGES for value in stage.writes]
    for stage in regen.STAGES:
        for lane_def in regen._lane_defs_for_stage(stage):
            artifacts_root = lane_def.get("artifacts_root")
            if isinstance(artifacts_root, str):
                declared.append(artifacts_root)
    for value in dict.fromkeys(declared):
        base = ROOT.parent if value.startswith("../") else ROOT
        pattern = value[3:] if value.startswith("../") else value
        if _is_glob(value):
            for path in base.glob(pattern):
                _add_watch_path(paths, path)
        else:
            _add_watch_path(paths, base / pattern)
    return sorted(paths, key=_display_path)


def _snapshot_watch_set() -> dict[str, Any]:
    entries = [
        {"path": _display_path(path), "sha256": _sha256_path(path)}
        for path in _watch_set()
    ]
    return {
        "watch_set_size": len(entries),
        "snapshot_sha256": sha256_json(entries),
        "entries": entries,
    }


def _changed_paths(first: Mapping[str, Any], second: Mapping[str, Any]) -> list[str]:
    first_entries = {
        str(item["path"]): str(item["sha256"])
        for item in first.get("entries", [])
        if isinstance(item, Mapping)
    }
    second_entries = {
        str(item["path"]): str(item["sha256"])
        for item in second.get("entries", [])
        if isinstance(item, Mapping)
    }
    all_paths = sorted(first_entries.keys() | second_entries.keys())
    return [
        path
        for path in all_paths
        if first_entries.get(path) != second_entries.get(path)
    ]


def _classify_text(text: str) -> str:
    classifier_text = "\n".join(
        line
        for line in text.splitlines()
        if "DeprecationWarning: jsonschema.RefResolver is deprecated" not in line
        and "from jsonschema import Draft202012Validator, RefResolver" not in line
    )
    if "[PIN_STALE]" in classifier_text:
        return "pin_stale"
    if "[SEMANTIC]" in classifier_text:
        return "semantic"
    if re.search(
        r"no such file or directory|filenotfounderror|errno\\s*2|enoent|missing (file|dir|directory|path)",
        classifier_text,
        re.IGNORECASE,
    ):
        return "io_missing"
    if re.search(
        r"jsonschema|primitivecompileerror|schema (error|invalid|validation)|failed .* schema",
        classifier_text,
        re.IGNORECASE,
    ):
        return "schema_invalid"
    if re.search(
        r"validate_stage_graph|duplicate stage id|depends on missing|declares no writes|read_only stage|write path .* declared by both",
        classifier_text,
        re.IGNORECASE,
    ):
        return "graph_invariant"
    if re.search(r"comparator|assertionerror", classifier_text, re.IGNORECASE):
        return "comparator_failure"
    return "drift_unexplained"


def _write_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        metavar="OUT",
        help="write JSON to OUT, or stdout when OUT is omitted",
    )


def _emit_json(payload: Mapping[str, Any], destination: str | None) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if destination in (None, "-"):
        print(encoded, end="")
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
    print(f"wrote {path}")


def _run(args: argparse.Namespace) -> int:
    try:
        code, results = regen.run_pipeline(regen.STAGES, python=args.python)
    except ValueError as exc:
        payload = {
            "schema_version": "bb.e4.regen_run.v1",
            "ok": False,
            "error": str(exc),
            "exit_code": 2,
            "results": [],
        }
        _emit_json(payload, args.json) if args.json is not None else print(
            f"plan invalid: {exc}", file=sys.stderr
        )
        return 2

    payload = {
        "schema_version": "bb.e4.regen_run.v1",
        "ok": code == 0,
        "exit_code": code,
        "completed_stage_count": len(results),
        "total_duration_seconds": round(
            sum(result.duration_seconds for result in results), 6
        ),
        "results": [asdict(result) for result in results],
    }
    if args.json is not None:
        _emit_json(payload, args.json)
    return code


def _explain(args: argparse.Namespace) -> int:
    try:
        regen.validate_stage_graph(regen.STAGES)
    except ValueError as exc:
        payload = {
            "schema_version": "bb.e4.regen_plan.v1",
            "ok": False,
            "error": str(exc),
        }
        _emit_json(payload, args.json) if args.json is not None else print(
            f"plan invalid: {exc}", file=sys.stderr
        )
        return 2

    if args.json is not None:
        payload = regen.plan_dict(regen.STAGES, python=args.python)
        payload["ok"] = True
        _emit_json(payload, args.json)
    else:
        regen.print_explain(regen.STAGES, python=args.python)
    return 0


def _score_authority(args: argparse.Namespace) -> dict[str, Any]:
    return _final_readiness_module().score_authority(
        expected_points_value=args.expected_points,
        expected_target_claims_value=args.expected_target_claims,
        expected_non_target_claims_value=args.expected_non_target_claims,
    )


def _fixed_point(args: argparse.Namespace) -> int:
    authority = _score_authority(args)
    if not authority["ok"]:
        payload = {
            "schema_version": "bb.e4.fixed_point_report.v1",
            "report_id": "e4_regen_fixed_point",
            "generated_at_utc": regen.PINNED_GENERATED_AT_UTC,
            "score_authority": authority,
            "watch_set_size": 0,
            "first_pass_snapshot_sha256": "sha256:" + ("0" * 64),
            "second_pass_snapshot_sha256": "sha256:" + ("0" * 64),
            "byte_identical": False,
            "changed_paths": [],
            "first_exit_code": 2,
            "second_exit_code": 2,
        }
        _emit_json(payload, args.json)
        return 1

    first_code, _first_results = regen.run_pipeline(regen.STAGES, python=args.python)
    first_snapshot = _snapshot_watch_set()
    second_code, _second_results = regen.run_pipeline(regen.STAGES, python=args.python)
    second_snapshot = _snapshot_watch_set()
    changed = _changed_paths(first_snapshot, second_snapshot)
    byte_identical = (
        first_snapshot["snapshot_sha256"] == second_snapshot["snapshot_sha256"]
        and not changed
    )
    payload = {
        "schema_version": "bb.e4.fixed_point_report.v1",
        "report_id": "e4_regen_fixed_point",
        "generated_at_utc": regen.PINNED_GENERATED_AT_UTC,
        "score_authority": authority,
        "watch_set_size": second_snapshot["watch_set_size"],
        "first_pass_snapshot_sha256": first_snapshot["snapshot_sha256"],
        "second_pass_snapshot_sha256": second_snapshot["snapshot_sha256"],
        "byte_identical": byte_identical,
        "changed_paths": changed,
        "first_exit_code": first_code,
        "second_exit_code": second_code,
    }
    if args.json is not None:
        _emit_json(payload, args.json)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if byte_identical and first_code == 0 and second_code == 0 else 1


def _classify(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.log).read_text(encoding="utf-8"))
    text_parts: list[str] = []
    raw_stderr_parts: list[str] = []
    if isinstance(payload, Mapping):
        for key in ("stdout", "stdout_tail", "error", "stderr", "stderr_tail"):
            value = payload.get(key)
            if isinstance(value, str):
                text_parts.append(value)
                if key in {"error", "stderr", "stderr_tail"}:
                    raw_stderr_parts.append(value)
        results = payload.get("results")
        if isinstance(results, list):
            for result in results:
                if isinstance(result, Mapping):
                    for key in ("stdout", "stderr"):
                        value = result.get(key)
                        if isinstance(value, str):
                            text_parts.append(value)
                            if key == "stderr":
                                raw_stderr_parts.append(value)
    text = "\n".join(text_parts)
    failure_class = _classify_text(text)
    report = {
        "schema_version": "bb.e4.regen_failure_classification.v1",
        "ok": True,
        "failure_class": failure_class,
        "failure_classes": list(FAILURE_CLASSES),
        "log": str(args.log),
        "stderr_tail": text[-4000:],
        "raw_stderr_tail": "\n".join(raw_stderr_parts)[-4000:],
    }
    _emit_json(report, args.json)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Single front door for E4 evidence regeneration."
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used for stage commands",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the full regeneration DAG")
    _write_json_arg(run_parser)
    run_parser.set_defaults(func=_run)

    explain_parser = subparsers.add_parser(
        "explain", help="emit or print the regeneration plan"
    )
    _write_json_arg(explain_parser)
    explain_parser.set_defaults(func=_explain)

    fixed_point_parser = subparsers.add_parser(
        "fixed-point", help="run twice and report fixed-point watch-set equality"
    )
    _write_json_arg(fixed_point_parser)
    fixed_point_parser.add_argument("--expected-points", type=int, default=1000)
    fixed_point_parser.add_argument("--expected-target-claims", type=int, default=10)
    fixed_point_parser.add_argument("--expected-non-target-claims", type=int, default=8)
    fixed_point_parser.set_defaults(func=_fixed_point)

    classify_parser = subparsers.add_parser(
        "classify", help="classify a failed regen run JSON log"
    )
    classify_parser.add_argument("--log", required=True, type=Path)
    _write_json_arg(classify_parser)
    classify_parser.set_defaults(func=_classify)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
