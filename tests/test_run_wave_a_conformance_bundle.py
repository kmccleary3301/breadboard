from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path


def test_run_wave_a_conformance_bundle_emits_reports(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    manifest = tmp_path / "ct_scenarios_v1.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "ct_scenarios_manifest_v1",
                "suite_id": "bundle-unit",
                "target": {"kind": "unit", "id": "bundle"},
                "scenarios": [
                    {
                        "test_id": "CT-BUNDLE-001",
                        "description": "bundle scenario",
                        "command": [
                            "python",
                            "-c",
                            "import argparse,json; from pathlib import Path; parser=argparse.ArgumentParser(); parser.add_argument('--json-out', required=True); args=parser.parse_args(); out=Path(args.json_out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps({'ok': True, 'count': 1, 'violations': []}) + '\\n', encoding='utf-8')",
                            "--json-out",
                            "artifacts/conformance/node_gate/ct_bundle_001.json",
                        ],
                        "timeout_seconds": 30,
                        "assertions": {
                            "json_files": [
                                {
                                    "path": "artifacts/conformance/node_gate/ct_bundle_001.json",
                                    "checks": [
                                        {"path": "ok", "equals": True},
                                        {"path": "count", "min": 1},
                                        {"path": "violations", "length_equals": 0},
                                    ],
                                }
                            ]
                        },
                        "gate_level": "P0-blocking",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
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
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(
            {
                "test_id": "CT-BUNDLE-001",
                "primitive_family": "Bundle",
                "schema_target": "bb.bundle.v1",
                "fixture_source": "fixtures/bundle.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
            }
        )
    artifact_dir = tmp_path / "artifact-root"
    env = dict(os.environ)
    env["CT_SCENARIOS_MANIFEST"] = str(manifest)
    env["CONFORMANCE_MATRIX_CSV"] = str(matrix)

    completed = subprocess.run(
        ["bash", "scripts/run_wave_a_conformance_bundle.sh", str(artifact_dir)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads((artifact_dir / "ct_scenarios_result_v1.json").read_text(encoding="utf-8"))
    summary = json.loads((artifact_dir / "conformance_matrix_sync_summary_v1.json").read_text(encoding="utf-8"))
    assert result["status"] == "pass"
    assert summary["passing_rows"] == 1
    assert (artifact_dir / "CONFORMANCE_TEST_MATRIX_V1.synced.csv").is_file()


def test_run_wave_a_conformance_bundle_syncs_before_failing_not_implemented(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    manifest = tmp_path / "ct_scenarios_v1.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "ct_scenarios_manifest_v1",
                "suite_id": "bundle-unit-red",
                "target": {"kind": "unit", "id": "bundle-red"},
                "scenarios": [
                    {
                        "test_id": "CT-BUNDLE-MISSING",
                        "description": "bundle missing scenario",
                        "command": [
                            "python",
                            "scripts/not_a_real_ct_checker.py",
                            "--json-out",
                            "artifacts/conformance/node_gate/ct_bundle_missing.json",
                        ],
                        "timeout_seconds": 30,
                        "assertions": {
                            "json_files": [
                                {
                                    "path": "artifacts/conformance/node_gate/ct_bundle_missing.json",
                                    "checks": [{"path": "ok", "equals": True}],
                                }
                            ]
                        },
                        "gate_level": "P0-blocking",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
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
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(
            {
                "test_id": "CT-BUNDLE-MISSING",
                "primitive_family": "Bundle",
                "schema_target": "bb.bundle.v1",
                "fixture_source": "fixtures/bundle_missing.json",
                "gate_level": "P0-blocking",
                "owner": "unit",
            }
        )
    artifact_dir = tmp_path / "artifact-root"
    env = dict(os.environ)
    env["CT_SCENARIOS_MANIFEST"] = str(manifest)
    env["CONFORMANCE_MATRIX_CSV"] = str(matrix)

    completed = subprocess.run(
        ["bash", "scripts/run_wave_a_conformance_bundle.sh", str(artifact_dir)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    result = json.loads((artifact_dir / "ct_scenarios_result_v1.json").read_text(encoding="utf-8"))
    summary = json.loads((artifact_dir / "conformance_matrix_sync_summary_v1.json").read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["not_implemented_count"] == 1
    assert summary["ok"] is False
    assert summary["not_implemented_rows"] == 1
    assert summary["blocking_not_implemented_rows"] == 1
    assert (artifact_dir / "CONFORMANCE_TEST_MATRIX_V1.synced.csv").is_file()
