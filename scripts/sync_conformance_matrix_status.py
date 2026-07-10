from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "docs" / "conformance" / "CONFORMANCE_TEST_MATRIX_V1.csv"
DEFAULT_ROWS = ROOT / "artifacts" / "conformance" / "ct_scenarios_rows_v1.json"
DEFAULT_OUT_CSV = ROOT / "artifacts" / "conformance" / "CONFORMANCE_TEST_MATRIX_V1.synced.csv"
DEFAULT_SUMMARY_JSON = ROOT / "artifacts" / "conformance" / "conformance_matrix_sync_summary_v1.json"
DEFAULT_SUMMARY_MD = ROOT / "artifacts" / "conformance" / "conformance_matrix_sync_summary_v1.md"


class SyncError(ValueError):
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
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("rows", [])
    if not isinstance(payload, list):
        raise SyncError("rows JSON must be a list or object with a rows list")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise SyncError("each CT row result must be an object")
        test_id = item.get("test_id")
        if not isinstance(test_id, str) or not test_id:
            raise SyncError("each CT row result must have a non-empty test_id")
        rows.append(item)
    return rows


def _read_matrix(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SyncError("matrix CSV missing header")
        fieldnames = list(reader.fieldnames)
        required = {"test_id"}
        missing = sorted(required - set(fieldnames))
        if missing:
            raise SyncError(f"matrix CSV missing required columns: {', '.join(missing)}")
        mutable = sorted({"status", "notes"} & set(fieldnames))
        if mutable:
            raise SyncError(f"source matrix CSV contains generated columns: {', '.join(mutable)}")
        rows = [dict(row) for row in reader]
    return fieldnames, rows


def _canonical_status(status: Any) -> str:
    text = str(status or "").strip().lower()
    if text in {"pass", "passing", "passed", "ok", "success"}:
        return "passing"
    if text in {"fail", "failing", "failed", "error", "timeout"}:
        return "failing"
    if text in {"not_implemented", "unimplemented", "missing"}:
        return "not_implemented"
    if text in {"skip", "skipped"}:
        return "skipped"
    return "not_implemented"


def _status_note(row: dict[str, Any], status: str) -> str:
    source = row.get("source") or "ct-scenarios"
    if status == "passing":
        return f"auto-sync: [{source}] pass"
    if status == "failing":
        failures = row.get("failures") or []
        if isinstance(failures, list) and failures:
            return f"auto-sync: [{source}] fail: {str(failures[0])[:160]}"
        return f"auto-sync: [{source}] fail"
    if status == "skipped":
        skip_allowlist = row.get("skip_allowlist") or {}
        reason = skip_allowlist.get("reason") if isinstance(skip_allowlist, dict) else None
        expires_on = skip_allowlist.get("expires_on") if isinstance(skip_allowlist, dict) else None
        if isinstance(reason, str) and reason:
            note = f"auto-sync: [{source}] skipped: {reason[:160]}"
            if isinstance(expires_on, str) and expires_on:
                note += f"; expires_on={expires_on}"
            return note
        return f"auto-sync: [{source}] skipped"
    errors = row.get("errors") or []
    if isinstance(errors, list) and errors:
        return f"auto-sync: [{source}] not_implemented: {str(errors[0])[:160]}"
    return f"auto-sync: [{source}] not_implemented"




def sync_matrix(
    *,
    matrix_csv: Path,
    rows_json: Path,
    generated_at_utc: str | None = None,
) -> tuple[list[str], list[dict[str, str]], dict[str, Any]]:
    source_fieldnames, matrix_rows = _read_matrix(matrix_csv)
    ct_rows = _load_rows(rows_json)

    by_id: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in ct_rows:
        test_id = row["test_id"]
        if test_id in by_id:
            duplicates.append(test_id)
        by_id[test_id] = row
    if duplicates:
        raise SyncError(f"duplicate CT result rows: {', '.join(sorted(duplicates))}")

    matrix_ids = {row["test_id"] for row in matrix_rows}
    unexpected = sorted(set(by_id) - matrix_ids)
    missing = sorted(matrix_ids - set(by_id))

    status_counts = {"passing": 0, "failing": 0, "not_implemented": 0, "skipped": 0}
    synced_rows: list[dict[str, str]] = []
    blocking_not_implemented = 0
    for matrix_row in matrix_rows:
        row = dict(matrix_row)
        test_id = row["test_id"]
        ct_row = by_id.get(test_id)
        if ct_row is None:
            status = "not_implemented"
            sync_note = "auto-sync: [ct-scenarios] not_implemented: missing CT scenario result"
        else:
            status = _canonical_status(ct_row.get("status"))
            sync_note = _status_note(ct_row, status)
        status_counts[status] += 1
        if status == "not_implemented" and any(level in str(row.get("gate_level", "")) for level in ("P0-blocking", "P1-blocking")):
            blocking_not_implemented += 1
        row["status"] = status
        row["notes"] = sync_note
        synced_rows.append(row)

    summary = {
        "schema_version": "bb.conformance_matrix_sync_summary.v1",
        "generated_at_utc": generated_at_utc or _utc_now(),
        "matrix_csv": _repo_relative(matrix_csv),
        "rows_json": _repo_relative(rows_json),
        "matrix_row_count": len(matrix_rows),
        "ct_row_count": len(ct_rows),
        "mapped_rows": len(matrix_ids & set(by_id)),
        "missing_rows": len(missing),
        "missing_test_ids": missing,
        "unexpected_rows": len(unexpected),
        "unexpected_test_ids": unexpected,
        "passing_rows": status_counts["passing"],
        "failing_rows": status_counts["failing"],
        "not_implemented_rows": status_counts["not_implemented"],
        "skipped_rows": status_counts["skipped"],
        "planned_rows": 0,
        "blocking_not_implemented_rows": blocking_not_implemented,
        "status_counts": status_counts,
        "ok": status_counts["failing"] == 0 and blocking_not_implemented == 0,
    }
    return source_fieldnames + ["status", "notes"], synced_rows, summary


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Conformance matrix sync summary",
        "",
        "Status and notes in the synced CSV are generated from CT row JSON. The source matrix CSV is static metadata and must not carry status or notes.",
        "",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- matrix_rows: `{summary['matrix_row_count']}`",
        f"- ct_rows: `{summary['ct_row_count']}`",
        f"- mapped_rows: `{summary['mapped_rows']}`",
        f"- passing_rows: `{summary['passing_rows']}`",
        f"- failing_rows: `{summary['failing_rows']}`",
        f"- not_implemented_rows: `{summary['not_implemented_rows']}`",
        f"- skipped_rows: `{summary['skipped_rows']}`",
        f"- blocking_not_implemented_rows: `{summary['blocking_not_implemented_rows']}`",
        f"- missing_rows: `{summary['missing_rows']}`",
        f"- unexpected_rows: `{summary['unexpected_rows']}`",
        f"- ok: `{summary['ok']}`",
        "",
    ]
    if summary["missing_test_ids"]:
        lines.append("## Missing matrix rows")
        lines.extend(f"- `{test_id}`" for test_id in summary["missing_test_ids"])
        lines.append("")
    if summary["unexpected_test_ids"]:
        lines.append("## Unexpected CT rows")
        lines.extend(f"- `{test_id}`" for test_id in summary["unexpected_test_ids"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync CONFORMANCE_TEST_MATRIX_V1.csv statuses from CT row results.")
    parser.add_argument("--matrix-csv", default=str(DEFAULT_MATRIX))
    parser.add_argument("--rows-json", default=str(DEFAULT_ROWS))
    parser.add_argument("--out-csv", default=str(DEFAULT_OUT_CSV))
    parser.add_argument("--summary-json", default=str(DEFAULT_SUMMARY_JSON))
    parser.add_argument("--summary-md", default=str(DEFAULT_SUMMARY_MD))
    parser.add_argument("--fail-on-failing", action="store_true")
    parser.add_argument(
        "--fail-on-summary-not-ok",
        action="store_true",
        help="Return 1 after writing outputs when the generated summary is fail-closed.",
    )
    parser.add_argument("--generated-at-utc", default=None)
    args = parser.parse_args(argv)

    try:
        fieldnames, synced_rows, summary = sync_matrix(
            matrix_csv=_root_path(args.matrix_csv),
            rows_json=_root_path(args.rows_json),
            generated_at_utc=args.generated_at_utc,
        )
    except SyncError as exc:
        print(f"sync_conformance_matrix_status: {exc}", file=sys.stderr)
        return 2

    out_csv = _root_path(args.out_csv)
    summary_json = _root_path(args.summary_json)
    summary_md = _root_path(args.summary_md)
    _write_csv(out_csv, fieldnames, synced_rows)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary_md(summary_md, summary)
    print(
        json.dumps(
            {
                "failing": summary["failing_rows"],
                "mapped": summary["mapped_rows"],
                "matrix_rows": summary["matrix_row_count"],
                "not_implemented": summary["not_implemented_rows"],
                "out_csv": _repo_relative(out_csv),
                "skipped": summary["skipped_rows"],
            },
            sort_keys=True,
        )
    )
    if args.fail_on_summary_not_ok and summary["ok"] is not True:
        return 1
    if args.fail_on_failing and summary["failing_rows"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
