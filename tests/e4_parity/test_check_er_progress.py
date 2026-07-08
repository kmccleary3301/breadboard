from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.e4_parity import check_er_progress as checker

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = ROOT.parent

PIN_POLICY_V2_DERIVATIVE_FIXTURES = (
    ("docs/conformance/support_claims/claim.json", "docs/conformance/support_claims/**"),
    ("artifacts/conformance/node_gate/ct_lane.json", "artifacts/conformance/node_gate/**"),
    ("docs/conformance/e4_artifact_catalog.json", "docs/conformance/e4_artifact_catalog.json"),
    ("artifacts/conformance/ct_scenarios_lane_alpha.json", "artifacts/conformance/ct_scenarios_*.json"),
    (
        "artifacts/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv",
        "artifacts/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv",
    ),
    (
        "artifacts/conformance/conformance_matrix_sync_summary_v1.json",
        "artifacts/conformance/conformance_matrix_sync_summary_v1.*",
    ),
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _minimal_progress(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    evidence = tmp_path / "docs_tmp" / "phase_16" / "evidence" / "unit_report.json"
    _write_json(evidence, {"ok": True})
    progress = {
        "schema_version": "bb.er.progress.v1",
        "generated_at_utc": "2026-07-04T00:00:00Z",
        "plan_ref": "docs_tmp/phase_16/BB_ER_MASTER_PLAN.md",
        "revision": 1,
        "amendments": [],
        "totals": {
            "points_total": 10,
            "points_done": 10,
            "points_blocked": 0,
        },
        "workstreams": [
            {
                "ws_id": "WS-UNIT",
                "title": "Unit workstream",
                "points_available": 10,
                "points_done": 10,
                "items": [
                    {
                        "item_id": "UNIT.1",
                        "title": "Unit item",
                        "points": 10,
                        "status": "done",
                        "notes": "covered",
                        "evidence": [
                            {
                                "path": evidence.relative_to(tmp_path).as_posix(),
                                "sha256": _sha256(evidence),
                            }
                        ],
                    }
                ],
            }
        ],
    }
    progress_path = tmp_path / "docs_tmp" / "phase_16" / "BB_ER_PROGRESS.json"
    _write_json(progress_path, progress)
    return progress_path, progress


def _minimal_progress_with_evidence_path(tmp_path: Path, relative_path: str) -> Path:
    progress_path, progress = _minimal_progress(tmp_path)
    evidence = tmp_path / relative_path
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(f"fixture for {relative_path}\n", encoding="utf-8")
    progress["workstreams"][0]["items"][0]["evidence"] = [
        {"path": relative_path, "sha256": _sha256(evidence)}
    ]
    _write_json(progress_path, progress)
    return progress_path


def _assert_gate_envelope(report: dict[str, object], *, klass: str, exit_code: int) -> None:
    assert report["ok"] is False
    gate_errors = report["gate_errors"]
    rendered_errors = report["errors"]
    assert isinstance(gate_errors, list) and gate_errors
    assert isinstance(rendered_errors, list) and rendered_errors
    assert len(gate_errors) == report["error_count"]
    assert len(rendered_errors) == report["error_count"]
    assert report[f"{klass}_count"] == report["error_count"]
    assert report["semantic_count" if klass == "pin_stale" else "pin_stale_count"] == 0
    assert all(error["klass"] == klass for error in gate_errors)
    prefix = "[PIN_STALE]" if klass == "pin_stale" else "[SEMANTIC]"
    assert all(isinstance(error, str) and error.startswith(prefix) for error in rendered_errors)
    assert checker.gate_exit_code(report) == exit_code


def test_progress_schema_is_metaschema_valid() -> None:
    schema = json.loads(checker.SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)



def test_check_er_progress_accepts_valid_minimal_progress(tmp_path: Path) -> None:
    progress_path, _ = _minimal_progress(tmp_path)

    report = checker.check_progress(progress_path, workspace_root=tmp_path)

    assert report["ok"] is True
    assert report["error_count"] == 0
    assert report["points_done"] == 10
    assert report["points_total"] == 10


def test_check_er_progress_rejects_arithmetic_mismatches(tmp_path: Path) -> None:
    progress_path, progress = _minimal_progress(tmp_path)
    mutated = copy.deepcopy(progress)
    mutated["totals"]["points_done"] = 9
    _write_json(progress_path, mutated)

    report = checker.check_progress(progress_path, workspace_root=tmp_path)

    _assert_gate_envelope(report, klass="semantic", exit_code=4)
    assert any(error["code"] == "point_total_mismatch" for error in report["gate_errors"])
    assert any("totals.points_done" in error for error in report["errors"])


def test_check_er_progress_rejects_done_item_without_evidence(tmp_path: Path) -> None:
    progress_path, progress = _minimal_progress(tmp_path)
    mutated = copy.deepcopy(progress)
    mutated["workstreams"][0]["items"][0]["evidence"] = []
    _write_json(progress_path, mutated)

    report = checker.check_progress(progress_path, workspace_root=tmp_path)

    assert report["ok"] is False
    assert any("done item must include at least one evidence entry" in error for error in report["errors"])


def test_check_er_progress_rejects_stale_evidence_hash(tmp_path: Path) -> None:
    progress_path, progress = _minimal_progress(tmp_path)
    mutated = copy.deepcopy(progress)
    mutated["workstreams"][0]["items"][0]["evidence"][0]["sha256"] = "sha256:" + "0" * 64
    _write_json(progress_path, mutated)

    report = checker.check_progress(progress_path, workspace_root=tmp_path)

    _assert_gate_envelope(report, klass="pin_stale", exit_code=3)
    assert any(error["code"] == "artifact_hash_mismatch" for error in report["gate_errors"])
    assert any("sha256" in error and "current" in error for error in report["errors"])


def test_check_er_progress_rejects_schema_invalid_status(tmp_path: Path) -> None:
    progress_path, progress = _minimal_progress(tmp_path)
    mutated = copy.deepcopy(progress)
    mutated["workstreams"][0]["items"][0]["status"] = "finished"
    _write_json(progress_path, mutated)

    report = checker.check_progress(progress_path, workspace_root=tmp_path)

    _assert_gate_envelope(report, klass="semantic", exit_code=4)
    assert any(error["code"] == "shape_invalid" for error in report["gate_errors"])
    assert any("schema.workstreams.0.items.0.status" in error for error in report["errors"])


def test_pin_policy_v1_allows_derivative_evidence_path(tmp_path: Path) -> None:
    derivative_path, _deny_glob = PIN_POLICY_V2_DERIVATIVE_FIXTURES[0]
    progress_path = _minimal_progress_with_evidence_path(tmp_path, derivative_path)

    report = checker.check_progress(progress_path, workspace_root=tmp_path, pin_policy="v1")

    assert report["ok"] is True
    assert report["error_count"] == 0
    assert report["errors"] == []


@pytest.mark.parametrize(
    ("derivative_path", "deny_glob"),
    PIN_POLICY_V2_DERIVATIVE_FIXTURES,
    ids=[
        "support-claims",
        "node-gate",
        "artifact-catalog",
        "ct-scenarios",
        "matrix-sync",
        "sync-summary",
    ],
)
def test_pin_policy_v2_rejects_seed_derivative_evidence_roots(
    tmp_path: Path,
    derivative_path: str,
    deny_glob: str,
) -> None:
    progress_path = _minimal_progress_with_evidence_path(tmp_path, derivative_path)

    report = checker.check_progress(progress_path, workspace_root=tmp_path, pin_policy="v2")

    _assert_gate_envelope(report, klass="semantic", exit_code=4)
    assert any(derivative_path in error and deny_glob in error for error in report["errors"])


def test_pin_policy_v2_cli_rejects_derivative_evidence_path_and_writes_json(tmp_path: Path) -> None:
    derivative_path = "docs/conformance/e4_artifact_catalog.json"
    progress_path = _minimal_progress_with_evidence_path(tmp_path, derivative_path)
    report_path = tmp_path / "report.json"

    exit_code = checker.main(
        [
            "--progress",
            str(progress_path),
            "--workspace-root",
            str(tmp_path),
            "--pin-policy",
            "v2",
            "--json-out",
            str(report_path),
        ]
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 4
    _assert_gate_envelope(report, klass="semantic", exit_code=4)
    assert report["semantic_count"] == 1
    assert any(derivative_path in error for error in report["errors"])


def test_live_bb_er_progress_passes_checker() -> None:
    progress_path = WORKSPACE_ROOT / "docs_tmp" / "phase_16" / "BB_ER_PROGRESS.json"

    report = checker.check_progress(progress_path, workspace_root=WORKSPACE_ROOT)

    assert report["ok"] is True
    assert report["points_total"] == 1000
    assert report["points_done"] >= 0
