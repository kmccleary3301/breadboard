from __future__ import annotations

import csv
import json
from pathlib import Path

import scripts.sync_conformance_matrix_status as sync_status


def _write_matrix(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "test_id",
                "primitive_family",
                "schema_target",
                "fixture_source",
                "gate_level",
                "owner",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_matrix(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_sync_conformance_matrix_status_updates_rows_by_exact_test_id(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    _write_matrix(
        matrix,
        [
            {
                "test_id": "CT-UNIT-001",
                "primitive_family": "Unit",
                "schema_target": "bb.unit.v1",
                "fixture_source": "fixtures/unit/pass.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
            },
            {
                "test_id": "CT-UNIT-002",
                "primitive_family": "Unit",
                "schema_target": "bb.unit.v1",
                "fixture_source": "fixtures/unit/fail.json",
                "gate_level": "P1-blocking",
                "owner": "unit",
            },
        ],
    )
    rows = tmp_path / "rows.json"
    rows.write_text(
        json.dumps(
            [
                {"test_id": "CT-UNIT-001", "status": "pass", "source": "unit-check"},
                {
                    "test_id": "CT-UNIT-002",
                    "status": "fail",
                    "source": "unit-check",
                    "failures": ["bad unit"],
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out_csv = tmp_path / "synced.csv"
    summary_json = tmp_path / "summary.json"
    summary_md = tmp_path / "summary.md"

    exit_code = sync_status.main(
        [
            "--matrix-csv",
            str(matrix),
            "--rows-json",
            str(rows),
            "--out-csv",
            str(out_csv),
            "--summary-json",
            str(summary_json),
            "--summary-md",
            str(summary_md),
        ]
    )

    assert exit_code == 0
    synced = _read_matrix(out_csv)
    assert [row["status"] for row in synced] == ["passing", "failing"]
    assert "auto-sync: [unit-check] pass" == synced[0]["notes"]
    assert "bad unit" in synced[1]["notes"]
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["mapped_rows"] == 2
    assert summary["passing_rows"] == 1
    assert summary["failing_rows"] == 1
    assert summary["planned_rows"] == 0
    assert "failing_rows" in summary_md.read_text(encoding="utf-8")


def test_sync_conformance_matrix_status_does_not_family_fallback(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    _write_matrix(
        matrix,
        [
            {
                "test_id": "CT-UNIT-001",
                "primitive_family": "Same family",
                "schema_target": "bb.unit.v1",
                "fixture_source": "fixtures/unit/pass.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
            }
        ],
    )
    rows = tmp_path / "rows.json"
    rows.write_text(
        json.dumps(
            [
                {
                    "test_id": "CT-OTHER-999",
                    "primitive_family": "Same family",
                    "status": "pass",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out_csv = tmp_path / "synced.csv"
    summary_json = tmp_path / "summary.json"
    summary_md = tmp_path / "summary.md"

    exit_code = sync_status.main(
        [
            "--matrix-csv",
            str(matrix),
            "--rows-json",
            str(rows),
            "--out-csv",
            str(out_csv),
            "--summary-json",
            str(summary_json),
            "--summary-md",
            str(summary_md),
        ]
    )

    assert exit_code == 0
    synced = _read_matrix(out_csv)
    assert synced[0]["status"] == "not_implemented"
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["mapped_rows"] == 0
    assert summary["missing_rows"] == 1
    assert summary["unexpected_rows"] == 1
    assert summary["missing_test_ids"] == ["CT-UNIT-001"]
    assert summary["unexpected_test_ids"] == ["CT-OTHER-999"]
    assert summary["not_implemented_rows"] == 1
    assert summary["blocking_not_implemented_rows"] == 1
    assert summary["ok"] is False


def test_sync_conformance_matrix_status_rejects_generated_source_columns(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    with matrix.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "test_id",
                "primitive_family",
                "schema_target",
                "fixture_source",
                "gate_level",
                "owner",
                "status",
                "notes",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(
            {
                "test_id": "CT-UNIT-001",
                "primitive_family": "Unit",
                "schema_target": "bb.unit.v1",
                "fixture_source": "fixtures/unit/pass.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
                "status": "planned",
                "notes": "old note",
            }
        )
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([{"test_id": "CT-UNIT-001", "status": "pass"}]) + "\n", encoding="utf-8")

    exit_code = sync_status.main(
        [
            "--matrix-csv",
            str(matrix),
            "--rows-json",
            str(rows),
            "--out-csv",
            str(tmp_path / "synced.csv"),
            "--summary-json",
            str(tmp_path / "summary.json"),
            "--summary-md",
            str(tmp_path / "summary.md"),
        ]
    )

    assert exit_code == 2



def test_sync_conformance_matrix_status_rejects_rows_payload_without_rows_list(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    _write_matrix(
        matrix,
        [
            {
                "test_id": "CT-UNIT-001",
                "primitive_family": "Unit",
                "schema_target": "bb.unit.v1",
                "fixture_source": "fixtures/unit/pass.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
            }
        ],
    )
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps({"rows": {"test_id": "CT-UNIT-001"}}) + "\n", encoding="utf-8")

    exit_code = sync_status.main(
        [
            "--matrix-csv",
            str(matrix),
            "--rows-json",
            str(rows),
            "--out-csv",
            str(tmp_path / "synced.csv"),
            "--summary-json",
            str(tmp_path / "summary.json"),
            "--summary-md",
            str(tmp_path / "summary.md"),
        ]
    )

    assert exit_code == 2
    assert not (tmp_path / "synced.csv").exists()


def test_sync_conformance_matrix_status_rejects_rows_without_test_id(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    _write_matrix(
        matrix,
        [
            {
                "test_id": "CT-UNIT-001",
                "primitive_family": "Unit",
                "schema_target": "bb.unit.v1",
                "fixture_source": "fixtures/unit/pass.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
            }
        ],
    )
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([{"status": "pass"}]) + "\n", encoding="utf-8")

    exit_code = sync_status.main(
        [
            "--matrix-csv",
            str(matrix),
            "--rows-json",
            str(rows),
            "--out-csv",
            str(tmp_path / "synced.csv"),
            "--summary-json",
            str(tmp_path / "summary.json"),
            "--summary-md",
            str(tmp_path / "summary.md"),
        ]
    )

    assert exit_code == 2


def test_sync_conformance_matrix_status_rejects_duplicate_result_rows(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    _write_matrix(
        matrix,
        [
            {
                "test_id": "CT-UNIT-001",
                "primitive_family": "Unit",
                "schema_target": "bb.unit.v1",
                "fixture_source": "fixtures/unit/pass.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
            }
        ],
    )
    rows = tmp_path / "rows.json"
    rows.write_text(
        json.dumps(
            [
                {"test_id": "CT-UNIT-001", "status": "pass"},
                {"test_id": "CT-UNIT-001", "status": "fail"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = sync_status.main(
        [
            "--matrix-csv",
            str(matrix),
            "--rows-json",
            str(rows),
            "--out-csv",
            str(tmp_path / "synced.csv"),
            "--summary-json",
            str(tmp_path / "summary.json"),
            "--summary-md",
            str(tmp_path / "summary.md"),
        ]
    )

    assert exit_code == 2