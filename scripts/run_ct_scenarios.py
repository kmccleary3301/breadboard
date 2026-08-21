from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "conformance" / "ct_scenarios_v1.json"
DEFAULT_JSON_OUT = ROOT / "artifacts" / "conformance" / "ct_scenarios_result_v1.json"
DEFAULT_ROWS_OUT = ROOT / "artifacts" / "conformance" / "ct_scenarios_rows_v1.json"
ARTIFACT_PREFIX = ("artifacts", "conformance")
DEFAULT_DEFERRAL_ALLOWLIST = ROOT / "docs" / "conformance" / "ct_deferral_allowlist.json"
BLOCKING_GATE_LEVELS = ("P0-blocking", "P1-blocking")


class ScenarioError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _root_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def _repo_relative(path: Path) -> str:
    try:
        return path.absolute().relative_to(ROOT.absolute()).as_posix()
    except ValueError:
        return path.as_posix()


def _repo_relative_value(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return _repo_relative(path)
    return value


def _portable_excerpt(text: str) -> str:
    return _excerpt(text.replace(str(ROOT.absolute()), ".").replace(str(ROOT.resolve()), "."))


def _is_blocking_gate(gate_level: Any) -> bool:
    text = str(gate_level or "")
    return any(level in text for level in BLOCKING_GATE_LEVELS)


def _allows_absolute_debug_paths(scenario: dict[str, Any]) -> bool:
    debug = scenario.get("debug")
    return isinstance(debug, dict) and debug.get("allow_absolute_paths") is True


def _validate_repo_relative_manifest_paths(scenario: dict[str, Any]) -> None:
    if _allows_absolute_debug_paths(scenario):
        return
    raw_command = scenario.get("command")
    if isinstance(raw_command, list):
        for index, value in enumerate(raw_command):
            if index == 0:
                continue
            text = str(value)
            path_text = text.split("=", 1)[1] if "=" in text else text
            if Path(path_text).is_absolute():
                test_id = scenario.get("test_id", "<unknown>")
                raise ScenarioError(f"{test_id}: command path must be repo-relative: {text}")
    assertions = scenario.get("assertions") or {}
    if isinstance(assertions, dict):
        json_files = assertions.get("json_files") or []
        if isinstance(json_files, list):
            for item in json_files:
                if not isinstance(item, dict):
                    continue
                path = item.get("path")
                if isinstance(path, str) and Path(path).is_absolute():
                    test_id = scenario.get("test_id", "<unknown>")
                    raise ScenarioError(f"{test_id}: assertion path must be repo-relative: {path}")


def _load_deferral_allowlist(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = _load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("skipped"), dict):
        payload = payload["skipped"]
    if isinstance(payload, list):
        by_id: dict[str, dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                raise ScenarioError("ct deferral allowlist entries must be objects")
            test_id = item.get("test_id")
            if not isinstance(test_id, str) or not test_id:
                raise ScenarioError("ct deferral allowlist entries must have a non-empty test_id")
            by_id[test_id] = item
        return by_id
    if not isinstance(payload, dict):
        raise ScenarioError("ct deferral allowlist must be an object or a list")
    result: dict[str, dict[str, Any]] = {}
    for test_id, item in payload.items():
        if not isinstance(item, dict):
            raise ScenarioError(f"ct deferral allowlist entry for {test_id!r} must be an object")
        result[str(test_id)] = item
    return result


def _skip_allowlist_entry(scenario: dict[str, Any], allowlist: dict[str, dict[str, Any]]) -> dict[str, Any]:
    test_id = str(scenario.get("test_id", ""))
    entry: dict[str, Any] = {}
    if test_id in allowlist:
        entry.update(allowlist[test_id])
    inline = scenario.get("skip_allowlist")
    if isinstance(inline, dict):
        entry.update(inline)
    reason = entry.get("reason")
    expires_on = entry.get("expires_on") or entry.get("date")
    if not isinstance(reason, str) or not reason.strip():
        raise ScenarioError(f"{test_id}: skipped CT row requires skip allowlist reason")
    if not isinstance(expires_on, str) or not expires_on.strip():
        raise ScenarioError(f"{test_id}: skipped CT row requires skip allowlist expires_on/date")
    entry["reason"] = reason.strip()
    entry["expires_on"] = expires_on.strip()
    return entry


def _excerpt(text: str, *, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 15] + "\n...<truncated>"


def _artifact_root(json_out: Path) -> Path:
    # The canonical result path is <artifact-root>/ct_scenarios_result_v1.json.
    return json_out.parent


def _rewrite_artifact_path(path: str, artifact_root: Path) -> str:
    normalized = path.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part)
    if len(parts) >= 2 and parts[:2] == ARTIFACT_PREFIX:
        return str(artifact_root.joinpath(*parts[2:]))
    return path


def _rewrite_command(command: list[str], artifact_root: Path) -> list[str]:
    rewritten: list[str] = []
    for idx, arg in enumerate(command):
        value = str(arg)
        if idx == 0 and value == "python":
            rewritten.append(sys.executable)
            continue
        if "=" in value:
            flag, raw = value.split("=", 1)
            rewritten.append(f"{flag}={_rewrite_artifact_path(raw, artifact_root)}")
            continue
        rewritten.append(_rewrite_artifact_path(value, artifact_root))
    return rewritten


def _command_available(command: list[str]) -> tuple[bool, str | None]:
    if not command:
        return False, "empty command"

    executable = command[0]
    if executable in {"python", sys.executable}:
        if len(command) >= 2 and command[1] != "-m":
            script = Path(command[1])
            if script.suffix in {".py", ".mjs", ".js", ".ts", ".sh"}:
                script_path = script if script.is_absolute() else ROOT / script
                if not script_path.exists():
                    return False, f"command script missing: {command[1]}"
        return True, None

    if "/" in executable or "\\" in executable:
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            executable_path = ROOT / executable_path
        if not executable_path.exists():
            return False, f"command executable missing: {command[0]}"
        return True, None

    if shutil.which(executable) is None:
        return False, f"command executable not found on PATH: {command[0]}"
    return True, None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _value_at_path(payload: Any, dotted_path: str) -> Any:
    value = payload
    for segment in dotted_path.split("."):
        if isinstance(value, dict) and segment in value:
            value = value[segment]
            continue
        raise ScenarioError(f"missing JSON path {dotted_path!r}")
    return value


def _check_json_file(path: Path, checks: Iterable[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if not path.is_file():
        return [f"missing JSON assertion file: {path}"]

    try:
        payload = _load_json(path)
    except Exception as exc:  # pragma: no cover - surfaced in report
        return [f"failed to parse JSON assertion file {path}: {exc}"]

    for check in checks:
        check_path = check.get("path")
        if not isinstance(check_path, str) or not check_path:
            failures.append(f"invalid assertion without non-empty path in {path}")
            continue
        try:
            value = _value_at_path(payload, check_path)
        except ScenarioError as exc:
            failures.append(str(exc))
            continue

        if "equals" in check and value != check["equals"]:
            failures.append(
                f"{path}:{check_path} expected {check['equals']!r}, got {value!r}"
            )
        if "min" in check:
            minimum = check["min"]
            if not isinstance(value, (int, float)) or value < minimum:
                failures.append(f"{path}:{check_path} expected >= {minimum!r}, got {value!r}")
        if "max" in check:
            maximum = check["max"]
            if not isinstance(value, (int, float)) or value > maximum:
                failures.append(f"{path}:{check_path} expected <= {maximum!r}, got {value!r}")
        if "length_equals" in check:
            expected_len = check["length_equals"]
            try:
                actual_len = len(value)
            except TypeError:
                failures.append(f"{path}:{check_path} has no length")
                continue
            if actual_len != expected_len:
                failures.append(
                    f"{path}:{check_path} expected length {expected_len!r}, got {actual_len!r}"
                )
    return failures


def _assertion_paths(scenario: dict[str, Any], artifact_root: Path) -> list[Path]:
    paths: list[Path] = []
    assertions = scenario.get("assertions", {})
    json_files = assertions.get("json_files", []) if isinstance(assertions, dict) else []
    if not isinstance(json_files, list):
        return paths
    for json_file in json_files:
        if not isinstance(json_file, dict):
            continue
        raw_path = json_file.get("path")
        if isinstance(raw_path, str):
            paths.append(_root_path(_rewrite_artifact_path(raw_path, artifact_root)))
    return paths


def _run_assertions(scenario: dict[str, Any], artifact_root: Path) -> list[str]:
    failures: list[str] = []
    assertions = scenario.get("assertions", {})
    json_files = assertions.get("json_files", [])
    if not isinstance(json_files, list):
        return ["assertions.json_files must be a list"]
    for json_file in json_files:
        if not isinstance(json_file, dict):
            failures.append("json file assertion must be an object")
            continue
        raw_path = json_file.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            failures.append("json file assertion missing path")
            continue
        checks = json_file.get("checks", [])
        if not isinstance(checks, list):
            failures.append(f"{raw_path} checks must be a list")
            continue
        path = _root_path(_rewrite_artifact_path(raw_path, artifact_root))
        failures.extend(_check_json_file(path, checks))
    return failures


def _normalize_test_id(test_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", test_id.lower()).strip("_")


def _scenario_row(
    scenario: dict[str, Any],
    *,
    status: str,
    command: list[str],
    rewritten_command: list[str],
    artifact_paths: list[Path],
    duration_seconds: float = 0.0,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    errors: list[str] | None = None,
    failures: list[str] | None = None,
    skip_allowlist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    test_id = str(scenario.get("test_id", ""))
    row = {
        "test_id": test_id,
        "normalized_test_id": _normalize_test_id(test_id),
        "description": scenario.get("description"),
        "gate_level": scenario.get("gate_level"),
        "status": status,
        "command": command,
        "rewritten_command": [_repo_relative_value(value) for value in rewritten_command],
        "duration_seconds": round(duration_seconds, 6),
        "exit_code": exit_code,
        "artifact_paths": [_repo_relative(path) for path in artifact_paths],
        "stdout_excerpt": _portable_excerpt(stdout),
        "stderr_excerpt": _portable_excerpt(stderr),
        "errors": errors or [],
        "failures": failures or [],
    }
    if skip_allowlist is not None:
        row["skip_allowlist"] = skip_allowlist
    return row


def _run_scenario(
    scenario: dict[str, Any],
    *,
    artifact_root: Path,
    fail_on_unimplemented_all: bool,
    deferral_allowlist: dict[str, dict[str, Any]],
    zero_durations: bool = False,
) -> dict[str, Any]:
    _validate_repo_relative_manifest_paths(scenario)
    raw_status = scenario.get("status")
    raw_command = scenario.get("command")
    command = [str(part) for part in raw_command] if isinstance(raw_command, list) else []
    rewritten_command = _rewrite_command(command, artifact_root)
    artifact_paths = _assertion_paths(scenario, artifact_root)

    if raw_status is not None and raw_status != "skipped":
        test_id = scenario.get("test_id", "<unknown>")
        raise ScenarioError(f"{test_id}: manifest status is mutable; remove it or use skipped")
    if raw_status == "skipped":
        skip_allowlist = _skip_allowlist_entry(scenario, deferral_allowlist)
        return _scenario_row(
            scenario,
            status="skipped",
            command=command,
            rewritten_command=rewritten_command,
            artifact_paths=artifact_paths,
            exit_code=None,
            skip_allowlist=skip_allowlist,
        )

    available, unavailable_reason = _command_available(command)
    if not available:
        if fail_on_unimplemented_all:
            return _scenario_row(
                scenario,
                status="fail",
                command=command,
                rewritten_command=rewritten_command,
                artifact_paths=artifact_paths,
                exit_code=127,
                failures=[unavailable_reason or "command unavailable"],
            )
        return _scenario_row(
            scenario,
            status="not_implemented",
            command=command,
            rewritten_command=rewritten_command,
            artifact_paths=artifact_paths,
            exit_code=None,
            errors=[unavailable_reason or "command unavailable"],
        )

    start = time.monotonic()
    try:
        completed = subprocess.run(
            rewritten_command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=int(scenario.get("timeout_seconds", 180)),
            check=False,
        )
        duration = time.monotonic() - start
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return _scenario_row(
            scenario,
            status="fail",
            command=command,
            rewritten_command=rewritten_command,
            artifact_paths=artifact_paths,
            duration_seconds=0.0 if zero_durations else duration,
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            failures=[f"command timed out after {scenario.get('timeout_seconds', 180)} seconds"],
        )

    failures: list[str] = []
    if completed.returncode != 0:
        failures.append(f"command exited with {completed.returncode}")
    failures.extend(_run_assertions(scenario, artifact_root))
    return _scenario_row(
        scenario,
        status="fail" if failures else "pass",
        command=command,
        rewritten_command=rewritten_command,
        artifact_paths=artifact_paths,
        duration_seconds=0.0 if zero_durations else duration,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        failures=failures,
    )


def build_report(
    *,
    manifest_path: Path,
    artifact_root: Path,
    test_ids: set[str] | None = None,
    fail_on_unimplemented_all: bool = False,
    legacy_planned_ok: bool = False,
    deferral_allowlist_path: Path = DEFAULT_DEFERRAL_ALLOWLIST,
    generated_at_utc: str | None = None,
    zero_durations: bool = False,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    scenarios = manifest.get("scenarios", [])
    if not isinstance(scenarios, list):
        raise ScenarioError("manifest.scenarios must be a list")

    selected = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ScenarioError("each scenario must be an object")
        test_id = scenario.get("test_id")
        if not isinstance(test_id, str) or not test_id:
            raise ScenarioError("each scenario must have a non-empty test_id")
        if test_ids is None or test_id in test_ids:
            selected.append(scenario)

    seen: set[str] = set()
    duplicates: list[str] = []
    for scenario in selected:
        test_id = scenario["test_id"]
        if test_id in seen:
            duplicates.append(test_id)
        seen.add(test_id)
    if duplicates:
        raise ScenarioError(f"duplicate scenario test_id values: {', '.join(sorted(duplicates))}")

    missing_selected = sorted((test_ids or set()) - seen)
    if missing_selected:
        raise ScenarioError(f"requested test_id values not found: {', '.join(missing_selected)}")

    deferral_allowlist = _load_deferral_allowlist(deferral_allowlist_path)
    rows = [
        _run_scenario(
            scenario,
            artifact_root=artifact_root,
            fail_on_unimplemented_all=fail_on_unimplemented_all,
            deferral_allowlist=deferral_allowlist,
            zero_durations=zero_durations,
        )
        for scenario in selected
    ]
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    failing = status_counts.get("fail", 0)
    not_implemented = status_counts.get("not_implemented", 0)
    skipped = status_counts.get("skipped", 0)
    blocking_not_implemented = sum(
        1 for row in rows if row["status"] == "not_implemented" and _is_blocking_gate(row.get("gate_level"))
    )
    ok = failing == 0 and (blocking_not_implemented == 0 or legacy_planned_ok)
    if not ok:
        status = "fail"
    elif not_implemented or skipped:
        status = "partial"
    else:
        status = "pass"
    return {
        "schema_version": "bb.ct_scenarios_result.v1",
        "suite_id": manifest.get("suite_id"),
        "manifest_path": _repo_relative(manifest_path),
        "artifact_root": _repo_relative(artifact_root),
        "generated_at_utc": generated_at_utc or _utc_now(),
        "ok": ok,
        "status": status,
        "scenario_count": len(rows),
        "passing_count": status_counts.get("pass", 0),
        "failing_count": failing,
        "not_implemented_count": not_implemented,
        "skipped_count": skipped,
        "planned_count": 0,
        "blocking_not_implemented_count": blocking_not_implemented,
        "status_counts": status_counts,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run CT scenario manifest commands.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--json-out", default=str(DEFAULT_JSON_OUT))
    parser.add_argument("--rows-out", default=str(DEFAULT_ROWS_OUT))
    parser.add_argument(
        "--test-id",
        action="append",
        default=[],
        help="Run only the named CT test_id. Repeatable.",
    )
    parser.add_argument(
        "--fail-on-unimplemented-all",
        action="store_true",
        help="Treat missing command scripts/executables as failures for every gate level.",
    )
    parser.add_argument(
        "--legacy-planned-ok",
        action="store_true",
        help="CI-forbidden compatibility mode: permit not_implemented rows in suite ok while keeping their row status.",
    )
    parser.add_argument("--deferral-allowlist", default=str(DEFAULT_DEFERRAL_ALLOWLIST))
    parser.add_argument("--generated-at-utc", default=None)
    parser.add_argument(
        "--zero-durations",
        action="store_true",
        help="Emit 0.0 for every row duration while still running commands and recording command results.",
    )
    args = parser.parse_args(argv)

    manifest_path = _root_path(args.manifest)
    json_out = _root_path(args.json_out)
    rows_out = _root_path(args.rows_out)
    artifact_root = _artifact_root(json_out)
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "node_gate").mkdir(parents=True, exist_ok=True)
    (artifact_root / "junit").mkdir(parents=True, exist_ok=True)

    try:
        report = build_report(
            manifest_path=manifest_path,
            artifact_root=artifact_root,
            test_ids=set(args.test_id) if args.test_id else None,
            fail_on_unimplemented_all=args.fail_on_unimplemented_all,
            legacy_planned_ok=args.legacy_planned_ok,
            deferral_allowlist_path=_root_path(args.deferral_allowlist),
            generated_at_utc=args.generated_at_utc,
            zero_durations=args.zero_durations,
        )
    except ScenarioError as exc:
        print(f"run_ct_scenarios: {exc}", file=sys.stderr)
        return 2

    json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows_out.parent.mkdir(parents=True, exist_ok=True)
    rows_out.write_text(json.dumps(report["rows"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "json_out": _repo_relative(json_out),
                "not_implemented": report["not_implemented_count"],
                "ok": report["ok"],
                "scenarios": report["scenario_count"],
                "skipped": report["skipped_count"],
                "status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
