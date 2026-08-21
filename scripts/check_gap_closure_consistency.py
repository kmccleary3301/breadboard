#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV_PATH = ROOT / "docs" / "conformance" / "GAP_TO_CT_COVERAGE_MAP_V1.csv"
DEFAULT_RUBRIC_PATH = ROOT / "docs" / "conformance" / "GAP_CLOSURE_RUBRIC_V1.json"
DEFAULT_CT_SCENARIOS_PATH = ROOT / "docs" / "conformance" / "ct_scenarios_v1.json"
CT_TOKEN_RE = re.compile(r"\b(CT-[A-Z]+-\d+(?:/\d+)*)\b")


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def _load_csv(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "gap_id" not in reader.fieldnames or "ct_coverage" not in reader.fieldnames:
            raise ValueError(f"{path} must contain gap_id and ct_coverage columns")
        rows: dict[str, str] = {}
        for row in reader:
            gap_id = row.get("gap_id", "").strip()
            if not gap_id:
                raise ValueError(f"{path} contains a row with empty gap_id")
            if gap_id in rows:
                raise ValueError(f"{path} contains duplicate gap_id {gap_id}")
            rows[gap_id] = row.get("ct_coverage", "")
        return rows


def _load_rubric(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    gaps = payload.get("gaps") if isinstance(payload, dict) else None
    if not isinstance(gaps, list):
        raise ValueError(f"{path} must contain a gaps array")
    rows: dict[str, str] = {}
    for gap in gaps:
        if not isinstance(gap, dict):
            raise ValueError(f"{path} gaps entries must be objects")
        gap_id = gap.get("gap_id")
        if not isinstance(gap_id, str) or not gap_id.strip():
            raise ValueError(f"{path} contains a gap with empty gap_id")
        if gap_id in rows:
            raise ValueError(f"{path} contains duplicate gap_id {gap_id}")
        required_ct_ids = gap.get("required_ct_ids", "")
        if not isinstance(required_ct_ids, str):
            raise ValueError(f"{path} gap {gap_id} required_ct_ids must be a string")
        rows[gap_id] = required_ct_ids
    return rows


def _load_ct_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
    if not isinstance(scenarios, list):
        raise ValueError(f"{path} must contain a scenarios array")
    ids: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError(f"{path} scenarios entries must be objects")
        test_id = scenario.get("test_id")
        if not isinstance(test_id, str) or not test_id.strip():
            raise ValueError(f"{path} contains a scenario with empty test_id")
        if test_id in ids:
            raise ValueError(f"{path} contains duplicate test_id {test_id}")
        ids.add(test_id)
    return ids


def _expand_ct_token(token: str) -> list[str]:
    head, *suffixes = token.split("/")
    parts = head.rsplit("-", 1)
    if len(parts) != 2:
        return [token]
    prefix, first_number = parts
    width = len(first_number)
    expanded = [head]
    for suffix in suffixes:
        if suffix.isdigit():
            expanded.append(f"{prefix}-{suffix.zfill(width)}")
    return expanded


def normalize_ct_ids(value: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for match in CT_TOKEN_RE.finditer(value):
        for ct_id in _expand_ct_token(match.group(1)):
            if ct_id not in seen:
                ids.append(ct_id)
                seen.add(ct_id)
    return ids


def _canonical(value: str) -> str:
    return ";".join(normalize_ct_ids(value))


def check_gap_closure_consistency(csv_path: Path, rubric_path: Path, ct_scenarios_path: Path) -> dict[str, Any]:
    csv_rows = _load_csv(csv_path)
    rubric_rows = _load_rubric(rubric_path)
    ct_ids = _load_ct_ids(ct_scenarios_path)

    mismatches: list[dict[str, str]] = []
    unknown_ct_ids: list[str] = []
    unknown_seen: set[str] = set()

    for gap_id in sorted(set(csv_rows) | set(rubric_rows)):
        csv_value = _canonical(csv_rows.get(gap_id, ""))
        rubric_value = _canonical(rubric_rows.get(gap_id, ""))
        if csv_value != rubric_value:
            mismatches.append({"gap_id": gap_id, "csv": csv_value, "rubric": rubric_value})
        for ct_id in normalize_ct_ids(csv_rows.get(gap_id, "")) + normalize_ct_ids(rubric_rows.get(gap_id, "")):
            if ct_id not in ct_ids and ct_id not in unknown_seen:
                unknown_ct_ids.append(ct_id)
                unknown_seen.add(ct_id)

    unknown_ct_ids.sort()
    ok = not mismatches and not unknown_ct_ids
    return {"ok": ok, "mismatches": mismatches, "unknown_ct_ids": unknown_ct_ids}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check GAP_TO_CT_COVERAGE_MAP_V1.csv against GAP_CLOSURE_RUBRIC_V1.json")
    parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH), help="Path to GAP_TO_CT_COVERAGE_MAP_V1.csv")
    parser.add_argument("--rubric", default=str(DEFAULT_RUBRIC_PATH), help="Path to GAP_CLOSURE_RUBRIC_V1.json")
    parser.add_argument("--ct-scenarios", default=str(DEFAULT_CT_SCENARIOS_PATH), help="Path to ct_scenarios_v1.json")
    parser.add_argument("--json-out", help="Write machine-readable check output to this path")
    args = parser.parse_args(argv)

    result = check_gap_closure_consistency(
        _resolve_path(args.csv),
        _resolve_path(args.rubric),
        _resolve_path(args.ct_scenarios),
    )
    if args.json_out:
        _write_json(_resolve_path(args.json_out), result)
    if not result["ok"]:
        for mismatch in result["mismatches"]:
            print(
                f"{mismatch['gap_id']}: csv={mismatch['csv']!r} rubric={mismatch['rubric']!r}",
                file=sys.stderr,
            )
        if result["unknown_ct_ids"]:
            print("unknown_ct_ids: " + ", ".join(result["unknown_ct_ids"]), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
