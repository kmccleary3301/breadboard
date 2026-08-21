from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import scripts.run_ct_scenarios as ct_runner
import scripts.sync_conformance_matrix_status as sync_status


SOURCE_MATRIX_FIELDS = [
    "test_id",
    "primitive_family",
    "schema_target",
    "fixture_source",
    "gate_level",
    "owner",
]


def _run_ct_main(argv: list[str]) -> int:
    try:
        return int(ct_runner.main(argv))
    except SystemExit as exc:
        return int(exc.code or 0)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _missing_command(test_id: str) -> list[str]:
    return [
        "python",
        f"scripts/missing_{test_id.lower().replace('-', '_')}.py",
        "--json-out",
        f"artifacts/conformance/node_gate/{test_id.lower().replace('-', '_')}.json",
    ]


def _scenario(
    test_id: str,
    *,
    gate_level: str = "P0-blocking",
    command: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "test_id": test_id,
        "description": f"{test_id} honesty fixture",
        "gate_level": gate_level,
        "timeout_seconds": 30,
        "command": command if command is not None else _missing_command(test_id),
        "assertions": {"json_files": []},
    }
    row.update(extra)
    return row


def _write_manifest(path: Path, scenarios: list[dict[str, Any]]) -> None:
    _write_json(
        path,
        {
            "schema_version": "ct_scenarios_manifest_v1",
            "suite_id": "ct-honesty-unit",
            "target": {"kind": "unit", "id": "ct-honesty"},
            "scenarios": scenarios,
        },
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_report_helper(repo_root: Path) -> None:
    helper = repo_root / "scripts" / "write_ct_report.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(
        "from __future__ import annotations\n"
        "import argparse, json\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--json-out', required=True)\n"
        "args = parser.parse_args()\n"
        "out = Path(args.json_out)\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_text(json.dumps({'ok': True, 'violations': []}) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )


def _write_source_matrix(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_MATRIX_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        return list(reader.fieldnames), list(reader)


def test_p0_p1_missing_commands_are_not_implemented_and_fail_by_default(tmp_path: Path) -> None:
    """Blocking CT rows with missing commands must fail the suite instead of passing as planned work."""
    manifest = tmp_path / "ct_manifest.json"
    _write_manifest(
        manifest,
        [
            _scenario("CT-HONEST-P0", gate_level="P0-blocking"),
            _scenario("CT-HONEST-P1", gate_level="P1-blocking"),
        ],
    )
    result = tmp_path / "ct_result.json"
    rows = tmp_path / "ct_rows.json"

    exit_code = _run_ct_main(["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)])

    assert exit_code == 1
    report = _read_json(result)
    row_payload = _read_json(rows)
    assert report["ok"] is False
    assert report["status"] == "fail"
    assert report["status_counts"] == {"not_implemented": 2}
    assert [row["status"] for row in row_payload] == ["not_implemented", "not_implemented"]
    assert all("command script missing" in row["errors"][0] for row in row_payload)


def test_legacy_planned_ok_preserves_not_implemented_rows_but_allows_exit_zero(tmp_path: Path) -> None:
    """The legacy escape hatch may make old dashboards green, but it must not relabel missing CT commands."""
    manifest = tmp_path / "ct_manifest.json"
    _write_manifest(manifest, [_scenario("CT-HONEST-LEGACY", gate_level="P0-blocking")])
    result = tmp_path / "ct_result.json"
    rows = tmp_path / "ct_rows.json"

    exit_code = _run_ct_main(
        [
            "--manifest",
            str(manifest),
            "--json-out",
            str(result),
            "--rows-out",
            str(rows),
            "--legacy-planned-ok",
        ]
    )

    assert exit_code == 0
    report = _read_json(result)
    row_payload = _read_json(rows)
    assert report["ok"] is True
    assert report["status"] == "partial"
    assert report["status_counts"] == {"not_implemented": 1}
    assert row_payload[0]["status"] == "not_implemented"
    assert "command script missing" in row_payload[0]["errors"][0]


def test_explicit_skipped_row_requires_allowlist_reason_and_date(tmp_path: Path) -> None:
    """A skipped CT row is honest only when it carries a reviewable reason and expiry date."""
    manifest = tmp_path / "ct_manifest.json"
    _write_manifest(
        manifest,
        [
            _scenario(
                "CT-HONEST-SKIP-BAD",
                gate_level="P0-blocking",
                status="skipped",
                skip_allowlist={"reason": "live provider credentials are intentionally absent in hermetic CI"},
            )
        ],
    )
    result = tmp_path / "ct_result.json"
    rows = tmp_path / "ct_rows.json"

    exit_code = _run_ct_main(["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)])

    assert exit_code == 2
    assert not result.exists()
    assert not rows.exists()


def test_explicit_skipped_row_with_allowlist_does_not_fail_suite(tmp_path: Path) -> None:
    """A fully justified skip remains visible as skipped and is not counted as an implementation failure."""
    manifest = tmp_path / "ct_manifest.json"
    _write_manifest(
        manifest,
        [
            _scenario(
                "CT-HONEST-SKIP-OK",
                gate_level="P0-blocking",
                status="skipped",
                skip_allowlist={
                    "reason": "live provider credentials are intentionally absent in hermetic CI",
                    "expires_on": "2026-12-31",
                },
            )
        ],
    )
    result = tmp_path / "ct_result.json"
    rows = tmp_path / "ct_rows.json"

    exit_code = _run_ct_main(["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)])

    assert exit_code == 0
    report = _read_json(result)
    row_payload = _read_json(rows)
    assert report["ok"] is True
    assert report["status_counts"] == {"skipped": 1}
    assert row_payload[0]["status"] == "skipped"
    assert row_payload[0]["exit_code"] is None
    assert row_payload[0]["skip_allowlist"]["expires_on"] == "2026-12-31"


def test_sync_accepts_source_matrix_without_status_notes_and_generates_them_from_ct_rows(tmp_path: Path) -> None:
    """The checked-in matrix is source data; status and notes are generated solely from CT row results."""
    matrix = tmp_path / "CONFORMANCE_TEST_MATRIX_V1.csv"
    _write_source_matrix(
        matrix,
        [
            {
                "test_id": "CT-HONEST-PASS",
                "primitive_family": "Honesty",
                "schema_target": "bb.honesty.v1",
                "fixture_source": "fixtures/honesty/pass.json",
                "gate_level": "P0-blocking",
                "owner": "ct",
            },
            {
                "test_id": "CT-HONEST-FAIL",
                "primitive_family": "Honesty",
                "schema_target": "bb.honesty.v1",
                "fixture_source": "fixtures/honesty/fail.json",
                "gate_level": "P1-blocking",
                "owner": "ct",
            },
            {
                "test_id": "CT-HONEST-NOT-IMPLEMENTED",
                "primitive_family": "Honesty",
                "schema_target": "bb.honesty.v1",
                "fixture_source": "fixtures/honesty/missing.json",
                "gate_level": "P1-blocking",
                "owner": "ct",
            },
            {
                "test_id": "CT-HONEST-SKIPPED",
                "primitive_family": "Honesty",
                "schema_target": "bb.honesty.v1",
                "fixture_source": "fixtures/honesty/skipped.json",
                "gate_level": "P6-support",
                "owner": "ct",
            },
        ],
    )
    rows_json = tmp_path / "ct_rows.json"
    _write_json(
        rows_json,
        [
            {"test_id": "CT-HONEST-PASS", "status": "pass", "source": "ct-honesty"},
            {
                "test_id": "CT-HONEST-FAIL",
                "status": "fail",
                "source": "ct-honesty",
                "failures": ["observable mismatch"],
            },
            {
                "test_id": "CT-HONEST-NOT-IMPLEMENTED",
                "status": "not_implemented",
                "source": "ct-honesty",
                "errors": ["command script missing: scripts/missing_ct.py"],
            },
            {
                "test_id": "CT-HONEST-SKIPPED",
                "status": "skipped",
                "source": "ct-honesty",
                "skip_allowlist": {
                    "reason": "non-target provider lane",
                    "expires_on": "2026-12-31",
                },
            },
        ],
    )
    out_csv = tmp_path / "CONFORMANCE_TEST_MATRIX_V1.synced.csv"
    summary_json = tmp_path / "summary.json"
    summary_md = tmp_path / "summary.md"

    exit_code = sync_status.main(
        [
            "--matrix-csv",
            str(matrix),
            "--rows-json",
            str(rows_json),
            "--out-csv",
            str(out_csv),
            "--summary-json",
            str(summary_json),
            "--summary-md",
            str(summary_md),
        ]
    )

    assert exit_code == 0
    fieldnames, synced = _read_csv(out_csv)
    assert fieldnames == SOURCE_MATRIX_FIELDS + ["status", "notes"]
    assert [row["status"] for row in synced] == ["passing", "failing", "not_implemented", "skipped"]
    assert synced[0]["notes"] == "auto-sync: [ct-honesty] pass"
    assert synced[1]["notes"] == "auto-sync: [ct-honesty] fail: observable mismatch"
    assert synced[2]["notes"] == "auto-sync: [ct-honesty] not_implemented: command script missing: scripts/missing_ct.py"
    assert synced[3]["notes"] == "auto-sync: [ct-honesty] skipped: non-target provider lane; expires_on=2026-12-31"
    summary = _read_json(summary_json)
    assert summary["status_counts"] == {
        "passing": 1,
        "failing": 1,
        "not_implemented": 1,
        "skipped": 1,
    }


def test_ct_rows_emit_repo_relative_artifact_paths(tmp_path: Path, monkeypatch: Any) -> None:
    """CT row artifacts must be portable repo-relative refs, not machine-local absolute temp paths."""
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(ct_runner, "ROOT", repo_root)
    _write_report_helper(repo_root)
    manifest = repo_root / "ct_manifest.json"
    _write_manifest(
        manifest,
        [
            _scenario(
                "CT-HONEST-PATHS",
                command=[
                    "python",
                    "scripts/write_ct_report.py",
                    "--json-out",
                    "artifacts/conformance/node_gate/ct_honest_paths.json",
                ],
                assertions={
                    "json_files": [
                        {
                            "path": "artifacts/conformance/node_gate/ct_honest_paths.json",
                            "checks": [
                                {"path": "ok", "equals": True},
                                {"path": "violations", "length_equals": 0},
                            ],
                        }
                    ]
                },
            )
        ],
    )
    result = repo_root / "artifacts" / "conformance" / "ct_result.json"
    rows = repo_root / "artifacts" / "conformance" / "ct_rows.json"

    exit_code = _run_ct_main(["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)])

    assert exit_code == 0
    row_payload = _read_json(rows)
    assert row_payload[0]["artifact_paths"] == ["artifacts/conformance/node_gate/ct_honest_paths.json"]
    assert all(not Path(path).is_absolute() for path in row_payload[0]["artifact_paths"])


def test_absolute_ct_artifact_paths_are_rejected_without_debug(tmp_path: Path, monkeypatch: Any) -> None:
    """Machine-local CT artifact paths are an explicit debug-only escape hatch, never default row output."""
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(ct_runner, "ROOT", repo_root)
    _write_report_helper(repo_root)
    absolute_artifact = tmp_path / "outside" / "absolute_ct_report.json"
    manifest = repo_root / "ct_manifest.json"
    _write_manifest(
        manifest,
        [
            _scenario(
                "CT-HONEST-ABSOLUTE",
                command=["python", "scripts/write_ct_report.py", "--json-out", str(absolute_artifact)],
                assertions={
                    "json_files": [
                        {
                            "path": str(absolute_artifact),
                            "checks": [{"path": "ok", "equals": True}],
                        }
                    ]
                },
            )
        ],
    )
    result = repo_root / "artifacts" / "conformance" / "ct_result.json"
    rows = repo_root / "artifacts" / "conformance" / "ct_rows.json"

    exit_code = _run_ct_main(["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)])

    assert exit_code == 2
    assert not rows.exists()
