from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from conformance.comparators.protocol import ComparatorInput

COMPARATOR_ID = "mini_swe_agent_trace_v1"
LANE_ID = "mini_swe_agent_2_4_6_replay"
CONFIG_ID = "mini_swe_agent_2_4_6_replay_v1"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
MANIFEST_SCHEMA_VERSION = "bb.e4.mini-trace-manifest.v1"
TRACE_SCHEMA_VERSION = "bb.e4.mini-trace.v1"
TRACE_FIELDS = ("requests", "history", "exit", "effects", "counters")
TRACE_REQUIRED_FIELDS = (
    "schema_version",
    "role",
    "case_id",
    "scenario_sha256",
    "requests",
    "history",
    "exit",
    "effects",
    "counters",
    "normalizations",
)
# BreadBoard-only oracles read only the operator-recorded controls, never a
# supplier-comparable trace field.
ORACLE_ROOT = "controls"
# Volatile source values: a placeholder may replace only the named history extra field.
FIELD_NORMALIZATIONS = {
    "timestamp:<TIMESTAMP>": ("<TIMESTAMP>", "timestamp"),
    "traceback:<TRACEBACK>": ("<TRACEBACK>", "traceback"),
}
ALLOWED_NORMALIZATIONS = frozenset(FIELD_NORMALIZATIONS)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _json_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _is_json_number(value: Any) -> bool:
    return type(value) in (int, float)


def _first_difference(expected: Any, observed: Any, path: str = "$") -> str | None:
    # JSON Schema instance equality: numbers are equal when mathematically equal
    # (0 == 0.0); booleans are never numbers.
    if _is_json_number(expected) and _is_json_number(observed):
        if expected == observed:
            return None
        return (
            f"first difference at {path}: expected {_json_value(expected)}, "
            f"observed {_json_value(observed)}"
        )
    if type(expected) is not type(observed):
        return (
            f"first difference at {path}: expected {_json_value(expected)}, "
            f"observed {_json_value(observed)} (types differ)"
        )
    if isinstance(expected, Mapping):
        expected_keys = set(expected)
        observed_keys = set(observed)
        for key in sorted(expected_keys | observed_keys, key=str):
            child_path = f"{path}.{key}"
            if key not in expected:
                return f"first difference at {child_path}: expected <missing>, observed {_json_value(observed[key])}"
            if key not in observed:
                return f"first difference at {child_path}: expected {_json_value(expected[key])}, observed <missing>"
            difference = _first_difference(expected[key], observed[key], child_path)
            if difference:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(observed):
            return f"first difference at {path}: expected list length {len(expected)}, observed {len(observed)}"
        for index, (expected_item, observed_item) in enumerate(zip(expected, observed)):
            difference = _first_difference(expected_item, observed_item, f"{path}[{index}]")
            if difference:
                return difference
        return None
    if expected != observed:
        return (
            f"first difference at {path}: expected {_json_value(expected)}, "
            f"observed {_json_value(observed)}"
        )
    return None


def _assertion(assertion_id: str, expected: Any, observed: Any, detail: str | None = None) -> dict[str, Any]:
    passed = detail is None
    return {
        "assertion_id": assertion_id,
        "name": assertion_id,
        "status": "passed" if passed else "failed",
        "observed": observed,
        "expected": expected,
        "detail": detail or "observed value equals expected value",
    }


def _resolve_trace_path(raw_path: str, manifest_path: Path, repo_root: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        raise ValueError("trace path must be relative")
    evidence_path = (manifest_path.parent / path).resolve()
    if evidence_path.is_file():
        return evidence_path
    repo_path = (repo_root / path).resolve()
    if repo_path.is_file():
        return repo_path
    return evidence_path


def _manifest_role_path(inp: ComparatorInput, role: str, repo_root: Path) -> Path:
    artifact = inp.get("artifacts", {}).get(role)
    if artifact is None:
        raise ValueError(f"missing {role} artifact")
    path = Path(artifact)
    return path if path.is_absolute() else (repo_root / path)


def _manifest_cases(manifest: Mapping[str, Any], label: str, errors: list[str]) -> Mapping[str, Any]:
    cases = manifest.get("cases")
    if not isinstance(cases, Mapping):
        errors.append(f"{label}.cases must be an object")
        return {}
    return cases


def _load_trace(
    *,
    case_id: str,
    ref: Any,
    manifest_path: Path,
    repo_root: Path,
    expected_role: str,
    errors: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(ref, Mapping):
        errors.append(f"{expected_role} case {case_id!r} reference must be an object")
        return None, None
    raw_path = ref.get("path")
    expected_hash = ref.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"{expected_role} case {case_id!r} reference path is required")
        return None, None
    if not isinstance(expected_hash, str) or not expected_hash:
        errors.append(f"{expected_role} case {case_id!r} reference sha256 is required")
        expected_hash = None
    try:
        path = _resolve_trace_path(raw_path, manifest_path, repo_root)
    except ValueError as exc:
        errors.append(f"{expected_role} case {case_id!r}: {exc}")
        return None, None
    actual_hash: str | None = None
    hash_error: str | None = None
    if not path.is_file():
        hash_error = f"{case_id}: missing trace {raw_path}"
    else:
        try:
            actual_hash = _sha256(path)
        except OSError as exc:
            hash_error = f"{case_id}: cannot hash {raw_path}: {exc}"
    if expected_hash is not None and actual_hash is not None and expected_hash != actual_hash:
        hash_error = f"{case_id}: {raw_path}: expected {expected_hash}, got {actual_hash}"
    if hash_error:
        # Hash mismatches are semantic assertions, not parser errors. The caller
        # retains this detail while still loading the trace for useful diagnostics.
        pass
    if not path.is_file():
        return None, hash_error
    try:
        trace_value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{expected_role} case {case_id!r} trace cannot be loaded: {exc}")
        return None, hash_error
    if not isinstance(trace_value, Mapping):
        errors.append(f"{expected_role} case {case_id!r} trace must be an object")
        return None, hash_error
    trace = dict(trace_value)
    missing = [key for key in TRACE_REQUIRED_FIELDS if key not in trace]
    if missing:
        errors.append(f"{expected_role} case {case_id!r} trace missing keys: {', '.join(missing)}")
    if trace.get("schema_version") != TRACE_SCHEMA_VERSION:
        errors.append(f"{expected_role} case {case_id!r} trace schema_version must be {TRACE_SCHEMA_VERSION}")
    if trace.get("role") != expected_role:
        errors.append(f"{expected_role} case {case_id!r} trace role must be {expected_role}")
    if trace.get("case_id") != case_id:
        errors.append(f"{expected_role} case {case_id!r} trace case_id must be {case_id}")
    controls = trace.get("controls")
    if controls is not None and not isinstance(controls, Mapping):
        errors.append(f"{expected_role} case {case_id!r} trace controls must be an object")
    errors.extend(
        f"{expected_role} case {case_id!r} {problem}" for problem in _normalization_problems(trace)
    )
    return trace, hash_error


def _placeholder_sites(value: Any, path: tuple[Any, ...], sites: list[tuple[tuple[Any, ...], str]]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _placeholder_sites(item, (*path, key), sites)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _placeholder_sites(item, (*path, index), sites)
    elif isinstance(value, str):
        sites.append((path, value))


def _normalization_problems(trace: Mapping[str, Any]) -> list[str]:
    """Reject undeclared, unapplied or misplaced normalization placeholders.

    A placeholder hides a source value, so it is admitted only where its declared
    rule allows it; it can never mask an arbitrary protocol field.
    """
    declared = trace.get("normalizations")
    if not isinstance(declared, list) or any(type(item) is not str for item in declared):
        return ["normalizations must be a list of strings"]
    problems: list[str] = []
    if len(set(declared)) != len(declared):
        problems.append("normalizations must be unique")
    unknown = sorted(set(declared) - ALLOWED_NORMALIZATIONS)
    if unknown:
        problems.append(f"normalizations not admitted: {', '.join(unknown)}")
    sites: list[tuple[tuple[Any, ...], str]] = []
    for field in (*TRACE_FIELDS, "controls"):
        _placeholder_sites(trace.get(field), (field,), sites)
    applied: set[str] = set()
    for normalization, (placeholder, extra_field) in FIELD_NORMALIZATIONS.items():
        for site, text in sites:
            if placeholder not in text:
                continue
            allowed = (
                text == placeholder
                and len(site) == 4
                and site[0] == "history"
                and type(site[1]) is int
                and site[2] == "extra"
                and site[3] == extra_field
            )
            if not allowed:
                problems.append(f"placeholder {placeholder} is not admitted at {site!r}")
            applied.add(normalization)
    for normalization in sorted(applied - set(declared)):
        problems.append(f"normalization {normalization} is applied but undeclared")
    for normalization in sorted((set(declared) & ALLOWED_NORMALIZATIONS) - applied):
        problems.append(f"normalization {normalization} is declared but not applied")
    return problems


def _oracle_problem(oracle: Any) -> str | None:
    if not isinstance(oracle, list) or not oracle:
        return "oracle must be a non-empty list of {path, equals} expectations"
    for expectation in oracle:
        if not isinstance(expectation, Mapping) or set(expectation) != {"path", "equals"}:
            return "each oracle expectation must contain exactly path and equals"
        path = expectation["path"]
        if (
            not isinstance(path, list)
            or not path
            or path[0] != ORACLE_ROOT
            or any(type(part) not in (str, int) for part in path)
        ):
            return f"oracle path {path!r} must be a list rooted at {ORACLE_ROOT}"
    return None


def _resolve_path(value: Any, path: list[Any]) -> tuple[bool, Any]:
    for part in path:
        if isinstance(value, Mapping) and type(part) is str and part in value:
            value = value[part]
        elif isinstance(value, list) and type(part) is int and 0 <= part < len(value):
            value = value[part]
        else:
            return False, None
    return True, value


def _manifest_data(
    *,
    inp: ComparatorInput,
    role: str,
    expected_role: str,
    repo_root: Path,
    errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], list[str]]:
    value = inp.get("capture" if role == "capture_ref" else "replay")
    label = "capture_ref" if role == "capture_ref" else "replay_ref"
    manifest = dict(value) if isinstance(value, Mapping) else {}
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be a manifest object")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"{label}.schema_version must be {MANIFEST_SCHEMA_VERSION}")
    if manifest.get("role") != expected_role:
        errors.append(f"{label}.role must be {expected_role}")
    try:
        manifest_path = _manifest_role_path(inp, role, repo_root)
    except ValueError as exc:
        errors.append(str(exc))
        manifest_path = repo_root
    cases = _manifest_cases(manifest, label, errors)
    traces: dict[str, Any] = {}
    hash_errors: dict[str, str] = {}
    for case_id, ref in cases.items():
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{label}.cases keys must be non-empty strings")
            continue
        trace, hash_error = _load_trace(
            case_id=case_id,
            ref=ref,
            manifest_path=manifest_path,
            repo_root=repo_root,
            expected_role=expected_role,
            errors=errors,
        )
        if trace is not None:
            traces[case_id] = trace
        if hash_error:
            hash_errors[case_id] = hash_error
    breadboard_only = manifest.get("breadboard_only", {})
    if breadboard_only is None:
        breadboard_only = {}
    if not isinstance(breadboard_only, Mapping):
        errors.append(f"{label}.breadboard_only must be an object")
        breadboard_only = {}
    return manifest, traces, hash_errors, dict(breadboard_only)


def _compare_case(
    case_id: str,
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    # The shared scenario hash binds both roles to the same scripted case.
    for field in ("scenario_sha256", *TRACE_FIELDS, "normalizations"):
        expected_value = expected.get(field)
        observed_value = observed.get(field)
        difference = _first_difference(expected_value, observed_value, f"$.{field}")
        assertions.append(
            _assertion(f"{case_id}.{field}_equal", expected_value, observed_value, difference)
        )
    return assertions


def compare(inp: ComparatorInput) -> dict[str, Any]:
    """Compare MiniComplete supplier and BreadBoard trace manifests exactly.

    Trace fields are compared as decoded JSON values under JSON Schema instance
    equality. The comparator applies no normalization itself: operators apply only
    the admitted rules, each trace declares exactly the rules it applied, and a
    placeholder outside its admitted field invalidates the manifest.
    BreadBoard-only control cases are judged against concrete path expectations.
    """
    repo_root_value = inp.get("repo_root")
    repo_root = Path(repo_root_value) if repo_root_value is not None else _repo_root()
    errors: list[str] = []
    capture_manifest, capture_traces, capture_hash_errors, breadboard_only = _manifest_data(
        inp=inp,
        role="capture_ref",
        expected_role="supplier",
        repo_root=repo_root,
        errors=errors,
    )
    replay_manifest, replay_traces, replay_hash_errors, _replay_only = _manifest_data(
        inp=inp,
        role="replay_ref",
        expected_role="breadboard",
        repo_root=repo_root,
        errors=errors,
    )

    assertions: list[dict[str, Any]] = []
    capture_case_ids = set(_manifest_cases(capture_manifest, "capture_ref", errors))
    replay_case_ids = set(_manifest_cases(replay_manifest, "replay_ref", errors))
    breadboard_only_ids = set(breadboard_only)
    if capture_case_ids & breadboard_only_ids:
        errors.append("capture_ref cases and breadboard_only must be disjoint")
    expected_replay_ids = capture_case_ids | breadboard_only_ids
    case_sets_detail = _first_difference(
        sorted(expected_replay_ids), sorted(replay_case_ids), "$.case_ids"
    )
    assertions.append(
        _assertion(
            "case_sets_equal",
            sorted(expected_replay_ids),
            sorted(replay_case_ids),
            case_sets_detail,
        )
    )
    assertions.append(
        _assertion(
            "capture_hashes_valid",
            [],
            sorted(capture_hash_errors.values()),
            "; ".join(capture_hash_errors.values()) if capture_hash_errors else None,
        )
    )
    assertions.append(
        _assertion(
            "replay_hashes_valid",
            [],
            sorted(replay_hash_errors.values()),
            "; ".join(replay_hash_errors.values()) if replay_hash_errors else None,
        )
    )
    assertions.append(
        _assertion(
            "capture_manifest_valid",
            [],
            errors,
            "; ".join(errors) if errors else None,
        )
    )
    # Reuse the same structural errors for the replay validity assertion only when
    # the replay itself is malformed; hash issues remain role-specific assertions.
    replay_errors = [error for error in errors if error.startswith("replay_ref") or error.startswith("breadboard")]
    assertions.append(
        _assertion(
            "replay_manifest_valid",
            [],
            replay_errors,
            "; ".join(replay_errors) if replay_errors else None,
        )
    )

    for case_id in sorted(capture_case_ids & replay_case_ids):
        expected = capture_traces.get(case_id)
        observed = replay_traces.get(case_id)
        if expected is None or observed is None:
            continue
        assertions.extend(_compare_case(case_id, expected, observed))

    for case_id in sorted(breadboard_only_ids & replay_case_ids):
        declared = breadboard_only.get(case_id)
        oracle = declared.get("oracle") if isinstance(declared, Mapping) else None
        problem = _oracle_problem(oracle)
        if problem is not None:
            assertions.append(
                _assertion(f"{case_id}.oracle_valid", "declared oracle expectations", oracle, problem)
            )
            continue
        observed = replay_traces.get(case_id)
        if observed is None:
            continue
        for expectation in oracle:
            path = expectation["path"]
            label = "$." + ".".join(str(part) for part in path)
            found, observed_value = _resolve_path(observed, path)
            detail = (
                _first_difference(expectation["equals"], observed_value, label)
                if found
                else f"first difference at {label}: expected {_json_value(expectation['equals'])}, observed <missing>"
            )
            assertions.append(
                _assertion(
                    f"{case_id}.oracle{label[1:]}",
                    expectation["equals"],
                    observed_value if found else "<missing>",
                    detail,
                )
            )

    failed = sum(assertion["status"] == "failed" for assertion in assertions)
    warned = sum(assertion["status"] == "warned" for assertion in assertions)
    passed = len(assertions) - failed - warned
    scope = inp.get("scope") if isinstance(inp.get("scope"), Mapping) else {}
    lane_id = str(scope.get("lane_id") or LANE_ID)
    config_id = str(scope.get("config_id") or CONFIG_ID)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "comparator_id": COMPARATOR_ID,
        "lane_id": lane_id,
        "config_id": config_id,
        "scope": dict(scope),
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "assertions": assertions,
        "details": [
            {"assertion_id": assertion["assertion_id"], "status": assertion["status"], "detail": assertion["detail"]}
            for assertion in assertions
        ],
        "errors": errors,
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "ok": failed == 0 and warned == 0 and not errors,
    }
    return report
