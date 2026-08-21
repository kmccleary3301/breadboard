#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class CheckError(ValueError):
    pass


def _root_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_list(value: Any, pointer: str) -> list[Any]:
    if not isinstance(value, list):
        raise CheckError(f"{pointer} must be an array")
    return value


def _as_dict(value: Any, pointer: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckError(f"{pointer} must be an object")
    return value


def _violations_for_required_mapping(rows: list[Any], required_keys: set[str], pointer: str) -> list[str]:
    violations: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            violations.append(f"{pointer}/{index} must be an object")
            continue
        missing = sorted(key for key in required_keys if key not in row)
        if missing:
            violations.append(f"{pointer}/{index} missing {', '.join(missing)}")
    return violations


def check_contract_exports(payload: dict[str, Any]) -> dict[str, Any]:
    expected = [str(item) for item in _as_list(payload.get("expected_schemas"), "/expected_schemas")]
    schema_dir = ROOT / "contracts" / "kernel" / "schemas"
    existing = {path.name for path in schema_dir.glob("*.schema.json")}
    missing = [name for name in expected if name not in existing]
    return {
        "ok": not missing,
        "expected_schema_count": len(expected),
        "missing_schema_count": len(missing),
        "missing_schemas": missing,
    }


def check_envelope_variants(payload: dict[str, Any]) -> dict[str, Any]:
    variants = _as_list(payload.get("variants"), "/variants")
    required = [str(item) for item in _as_list(payload.get("required_variants"), "/required_variants")]
    names = {str(row.get("name")) for row in variants if isinstance(row, dict)}
    missing = [name for name in required if name not in names]
    violations = _violations_for_required_mapping(variants, {"name", "schema_version", "fields"}, "/variants")
    return {
        "ok": not missing and not violations,
        "variant_count": len(variants),
        "missing_count": len(missing),
        "missing_variants": missing,
        "violation_count": len(violations),
        "violations": violations,
    }


def check_minimal_payload_samples(payload: dict[str, Any]) -> dict[str, Any]:
    samples = _as_list(payload.get("samples"), "/samples")
    violations = _violations_for_required_mapping(samples, {"schema_version", "payload"}, "/samples")
    for index, sample in enumerate(samples):
        if isinstance(sample, dict) and not isinstance(sample.get("payload"), dict):
            violations.append(f"/samples/{index}/payload must be an object")
    return {"ok": not violations, "sample_count": len(samples), "violation_count": len(violations), "violations": violations}


def check_runtime_event_translation(payload: dict[str, Any]) -> dict[str, Any]:
    cases = _as_list(payload.get("cases"), "/cases")
    violations = _violations_for_required_mapping(cases, {"runtime_event", "expected_event"}, "/cases")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        runtime_event = _as_dict(case.get("runtime_event"), f"/cases/{index}/runtime_event")
        expected_event = _as_dict(case.get("expected_event"), f"/cases/{index}/expected_event")
        for key in ("session_id", "event_type"):
            if runtime_event.get(key) != expected_event.get(key):
                violations.append(f"/cases/{index}/{key} translation mismatch")
    return {"ok": not violations, "case_count": len(cases), "violation_count": len(violations), "violations": violations}


def check_status_error_taxonomy(payload: dict[str, Any]) -> dict[str, Any]:
    events = _as_list(payload.get("events"), "/events")
    allowed_statuses = set(payload.get("allowed_statuses") or [])
    error_statuses = set(payload.get("error_statuses") or [])
    violations = _violations_for_required_mapping(events, {"event_id", "status"}, "/events")
    error_count = 0
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        status = event.get("status")
        if allowed_statuses and status not in allowed_statuses:
            violations.append(f"/events/{index}/status not allowed")
        has_error = isinstance(event.get("error"), dict)
        if status in error_statuses:
            error_count += 1
            if not has_error:
                violations.append(f"/events/{index}/error required")
        elif has_error:
            violations.append(f"/events/{index}/error forbidden for non-error status")
    return {"ok": not violations, "event_count": len(events), "error_count": error_count, "violation_count": len(violations), "violations": violations}


def check_session_stream_isolation(payload: dict[str, Any]) -> dict[str, Any]:
    events = _as_list(payload.get("events"), "/events")
    expected = _as_dict(payload.get("expected"), "/expected")
    allowed_kinds = set(expected.get("allowed_kinds") or [])
    required_types = set(expected.get("required_event_types") or [])
    required_counts = dict(expected.get("required_event_type_counts") or {})
    violations = _violations_for_required_mapping(events, {"type", "session_id", "stream_id", "cursor", "kind", "origin_session_id"}, "/events")
    sessions = {event.get("session_id") for event in events if isinstance(event, dict)}
    streams = {event.get("stream_id") for event in events if isinstance(event, dict)}
    bleed = 0
    cursors: list[int] = []
    types = Counter()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        types[str(event.get("type"))] += 1
        if event.get("origin_session_id") != event.get("session_id"):
            bleed += 1
            violations.append(f"/events/{index}/origin_session_id crosses session")
        if allowed_kinds and event.get("kind") not in allowed_kinds:
            violations.append(f"/events/{index}/kind not allowed")
        cursor = event.get("cursor")
        if isinstance(cursor, int):
            cursors.append(cursor)
        else:
            violations.append(f"/events/{index}/cursor must be integer")
    missing_types = sorted(required_types - set(types))
    for event_type in missing_types:
        violations.append(f"missing required event type {event_type}")
    for event_type, count in required_counts.items():
        if types.get(str(event_type), 0) != count:
            violations.append(f"event type {event_type} expected count {count}, got {types.get(str(event_type), 0)}")
    max_step = max((b - a for a, b in zip(cursors, cursors[1:])), default=0)
    if expected.get("min_event_count") is not None and len(events) < int(expected["min_event_count"]):
        violations.append("event count below expected minimum")
    if expected.get("max_cursor_step") is not None and max_step > int(expected["max_cursor_step"]):
        violations.append("cursor step exceeds expected maximum")
    if expected.get("max_unique_sessions") is not None and len(sessions) > int(expected["max_unique_sessions"]):
        violations.append("too many sessions")
    if expected.get("max_unique_streams") is not None and len(streams) > int(expected["max_unique_streams"]):
        violations.append("too many streams")
    return {
        "ok": not violations,
        "event_count": len(events),
        "bleed_event_count": bleed,
        "max_cursor_step_observed": max_step,
        "unique_session_count": len(sessions),
        "unique_stream_count": len(streams),
        "violation_count": len(violations),
        "violations": violations,
    }


def check_operator_diagnostics_pack(payload: dict[str, Any]) -> dict[str, Any]:
    events = _as_list(payload.get("events"), "/events")
    expected = _as_dict(payload.get("expected"), "/expected")
    allowed_sources = set(expected.get("allowed_sources") or [])
    allowed_statuses = set(expected.get("allowed_statuses") or [])
    statuses_require_error = set(expected.get("statuses_require_error") or [])
    prefixes = tuple(str(prefix) for prefix in expected.get("allowed_error_prefixes") or [])
    required_sources = set(expected.get("required_sources") or [])
    required_error_sources = set(expected.get("required_error_sources") or [])
    violations = _violations_for_required_mapping(events, {"source", "status"}, "/events")
    sources = {event.get("source") for event in events if isinstance(event, dict)}
    error_sources = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        source = event.get("source")
        status = event.get("status")
        if allowed_sources and source not in allowed_sources:
            violations.append(f"/events/{index}/source not allowed")
        if allowed_statuses and status not in allowed_statuses:
            violations.append(f"/events/{index}/status not allowed")
        error = event.get("error")
        if status in statuses_require_error:
            if not isinstance(error, dict):
                violations.append(f"/events/{index}/error required")
                continue
            error_sources.add(source)
            code = str(error.get("code", ""))
            if prefixes and not code.startswith(prefixes):
                violations.append(f"/events/{index}/error/code prefix not allowed")
        elif error is not None:
            violations.append(f"/events/{index}/error forbidden for status {status}")
    missing_sources = sorted(required_sources - sources)
    missing_error_sources = sorted(required_error_sources - error_sources)
    violations.extend(f"missing source {source}" for source in missing_sources)
    violations.extend(f"missing error source {source}" for source in missing_error_sources)
    return {
        "ok": not violations,
        "event_count": len(events),
        "source_count": len(sources),
        "error_source_count": len(error_sources),
        "violation_count": len(violations),
        "violations": violations,
    }


CHECKS = {
    "contract_exports": check_contract_exports,
    "envelope_variants": check_envelope_variants,
    "minimal_payload_samples": check_minimal_payload_samples,
    "runtime_event_translation": check_runtime_event_translation,
    "status_error_taxonomy": check_status_error_taxonomy,
    "session_stream_isolation": check_session_stream_isolation,
    "operator_diagnostics_pack": check_operator_diagnostics_pack,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate protocol conformance semantics fixtures.")
    parser.add_argument("--mode", required=True, choices=sorted(CHECKS))
    parser.add_argument("--payload", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()

    payload_path = _root_path(args.payload)
    out_path = _root_path(args.json_out)
    errors: list[str] = []
    try:
        payload = _as_dict(_load_json(payload_path), "/")
        report = CHECKS[args.mode](payload)
    except Exception as exc:
        report = {"ok": False, "violation_count": 1, "violations": [str(exc)]}
        errors.append(str(exc))

    report.setdefault("ok", False)
    report["schema_version"] = "bb.ct.protocol_semantics_report.v1"
    report["mode"] = args.mode
    report["payload_path"] = args.payload
    report["errors"] = errors
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
