from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.e4_parity import regenerate_evidence as driver


def test_stage_graph_is_topological_and_cataloged() -> None:
    driver.validate_stage_graph()

    positions = {stage.stage_id: index for index, stage in enumerate(driver.STAGES)}
    for stage in driver.STAGES:
        assert all(positions[dep] < positions[stage.stage_id] for dep in stage.depends_on)

    catalog_stage_ids = [
        stage.stage_id
        for stage in driver.STAGES
        if stage.argv[1:2] == ("scripts/e4_parity/build_artifact_catalog.py",)
    ]
    assert catalog_stage_ids == [
        "catalog_stable_snapshot",
        "catalog_full",
    ]
    assert all("--write-bindings" not in stage.argv for stage in driver.STAGES if stage.stage_id in catalog_stage_ids)
    claim_stage_ids = [
        stage.stage_id
        for stage in driver.STAGES
        if stage.argv[1:2] == ("scripts/e4_parity/generate_support_claims.py",)
    ]
    assert claim_stage_ids == ["support_claim_generation"]
    assert positions["ledger_seed"] < positions["catalog_stable_snapshot"]
    assert "legacy_evidence_ledger_refs" not in positions
    assert positions["catalog_stable_snapshot"] < positions["support_claim_generation"]
    assert positions["support_claim_generation"] < positions["catalog_full"]
    assert positions["catalog_full"] < positions["ct_scenarios"]
    assert positions["final_readiness_packet"] < positions["validate_report_hash_freshness"]
    lane_stages = [stage for stage in driver.STAGES if stage.stage_id.startswith("lane_def_reverify_")]
    assert lane_stages
    assert all(stage.phase == "lane_def_reverify" for stage in lane_stages)
    assert all(stage.note and "config/e4_lanes" in stage.note for stage in lane_stages)
    assert positions["catalog_full"] < positions[lane_stages[0].stage_id]
    assert positions[lane_stages[-1].stage_id] < positions["ct_scenarios"]

def test_report_hash_freshness_only_runs_at_release_boundary() -> None:
    positions = {stage.stage_id: index for index, stage in enumerate(driver.STAGES)}
    freshness_stages = [
        stage
        for stage in driver.STAGES
        if stage.argv[1:2] == ("scripts/e4_parity/validate_e4_report_hash_freshness.py",)
    ]

    assert [stage.stage_id for stage in freshness_stages] == ["validate_report_hash_freshness"]
    assert freshness_stages[0].phase == "validators"
    assert freshness_stages[0].depends_on == ("validate_primitive_readiness",)
    assert positions["final_readiness_packet"] < positions["validate_report_hash_freshness"]
    assert driver.STAGES[-1].stage_id == "validate_report_hash_freshness"




def test_explain_prints_dag_with_blocker_and_no_pytest(capsys: pytest.CaptureFixture[str]) -> None:
    assert driver.main(["--explain", "--python", "PY"]) == 0

    out = capsys.readouterr().out
    assert "E4 evidence regeneration DAG" in out
    assert "catalog_stable_snapshot" in out
    assert "catalog_full" in out
    assert "BLOCKER" not in out
    assert "blocker: CT is fail-closed" in out
    assert "pytest" not in out


def test_dry_run_does_not_execute_subprocess(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def forbidden_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("dry-run must not execute subprocess.run")

    monkeypatch.setattr(driver.subprocess, "run", forbidden_run)

    assert driver.main(["--dry-run", "--python", "PY"]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN: no regeneration commands executed." in out
    assert "PY scripts/e4_parity/build_artifact_catalog.py" in out


def test_run_pipeline_records_stage_durations_and_prints_timing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stages = (
        driver.Stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        driver.Stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
    )
    perf_counter_values = iter((10.0, 10.125, 20.0, 20.5))

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(driver.time, "perf_counter", lambda: next(perf_counter_values))
    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = driver.run_pipeline(stages, python="PY")

    captured = capsys.readouterr()
    assert code == 0
    assert [(result.stage_id, result.duration_seconds) for result in results] == [
        ("catalog_a", 0.125),
        ("catalog_b", 0.5),
    ]
    assert "<== catalog_a: exit=0 duration_seconds=0.125" in captured.out
    assert "<== catalog_b: exit=0 duration_seconds=0.500" in captured.out


def test_run_pipeline_prints_gate_error_bullets_from_failed_stage_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stages = (
        driver.Stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        driver.Stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        driver.Stage(
            stage_id="gate_report",
            phase="reports",
            label="gate report",
            argv=(driver.PYTHON, "gate_report.py"),
            depends_on=("catalog_b",),
        ),
    )
    failure_payload = json.dumps(
        {
            "gate_errors": [
                {
                    "klass": "pin_stale",
                    "code": "artifact_hash_mismatch",
                    "message": "catalog hash changed",
                    "remedy": "Regenerate and rebind support claims.",
                }
            ]
        }
    )

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[-1] == "gate_report.py":
            return subprocess.CompletedProcess(argv, 3, stdout=failure_payload, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = driver.run_pipeline(stages, python="PY")

    captured = capsys.readouterr()
    assert code == 3
    assert [result.stage_id for result in results] == ["catalog_a", "catalog_b", "gate_report"]
    assert "FAILED stage gate_report exit=3" in captured.err
    assert (
        "GATE_ERROR [PIN_STALE] artifact_hash_mismatch: catalog hash changed "
        "remedy=Regenerate and rebind support claims."
    ) in captured.err


def test_main_json_summary_includes_stage_and_total_durations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = driver.StageResult(
        stage_id="catalog_a",
        argv=["PY", "scripts/e4_parity/build_artifact_catalog.py"],
        returncode=0,
        stdout="",
        stderr="",
        duration_seconds=1.25,
    )

    def fake_run_pipeline(
        stages: tuple[driver.Stage, ...],
        *,
        python: str,
    ) -> tuple[int, list[driver.StageResult]]:
        assert stages == driver.STAGES
        assert python == "PY"
        return 0, [result]

    monkeypatch.setattr(driver, "run_pipeline", fake_run_pipeline)

    assert driver.main(["--json", "--python", "PY"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["completed_stage_count"] == 1
    assert payload["total_duration_seconds"] == 1.25
    assert payload["results"][0]["duration_seconds"] == 1.25


def test_run_pipeline_fail_fast_documents_stage_blocker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stages = (
        driver.Stage(
            stage_id="first",
            phase="lane_artifacts",
            label="first",
            argv=(driver.PYTHON, "first.py"),
        ),
        driver.Stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("first",),
        ),
        driver.Stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        driver.Stage(
            stage_id="ct",
            phase="reports",
            label="ct",
            argv=(driver.PYTHON, "ct.py"),
            depends_on=("catalog_b",),
            blocker="known CT blocker",
        ),
        driver.Stage(
            stage_id="validator",
            phase="validators",
            label="validator",
            argv=(driver.PYTHON, "validator.py"),
            depends_on=("ct",),
        ),
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "ct.py":
            return subprocess.CompletedProcess(argv, 7, stdout="ct out\n", stderr="ct err\n")
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = driver.run_pipeline(stages, python="PY")

    captured = capsys.readouterr()
    assert code == 7
    assert [result.stage_id for result in results] == ["first", "catalog_a", "catalog_b", "ct"]
    assert calls == [["PY", "first.py"], ["PY", "scripts/e4_parity/build_artifact_catalog.py"], ["PY", "scripts/e4_parity/build_artifact_catalog.py"], ["PY", "ct.py"]]
    assert "FAILED stage ct exit=7" in captured.err
    assert "BLOCKER: known CT blocker" in captured.err
    assert "validator.py" not in captured.out


def test_run_pipeline_continues_only_for_allowed_blocker_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stages = (
        driver.Stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        driver.Stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        driver.Stage(
            stage_id="ct",
            phase="reports",
            label="ct",
            argv=(driver.PYTHON, "ct.py"),
            depends_on=("catalog_b",),
            blocker="ct wrote fail-closed outputs",
            allowed_exit_codes=(1,),
        ),
        driver.Stage(
            stage_id="sync",
            phase="reports",
            label="sync",
            argv=(driver.PYTHON, "sync.py"),
            depends_on=("ct",),
        ),
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "ct.py":
            return subprocess.CompletedProcess(argv, 1, stdout="ct wrote\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="sync wrote\n", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = driver.run_pipeline(stages, python="PY")

    captured = capsys.readouterr()
    assert code == 1
    assert [result.stage_id for result in results] == ["catalog_a", "catalog_b", "ct", "sync"]
    assert calls == [["PY", "scripts/e4_parity/build_artifact_catalog.py"], ["PY", "scripts/e4_parity/build_artifact_catalog.py"], ["PY", "ct.py"], ["PY", "sync.py"]]
    assert "CONTINUING blocked stage ct exit=1" in captured.err
    assert "BLOCKER: ct wrote fail-closed outputs" in captured.err


def test_run_pipeline_refreshes_catalog_after_blocked_final_before_stopping_validators(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stages = (
        driver.Stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        driver.Stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        driver.Stage(
            stage_id="final",
            phase="reports",
            label="final",
            argv=(driver.PYTHON, "final.py"),
            depends_on=("catalog_b",),
            blocker="blocked final",
            allowed_exit_codes=(1,),
        ),
        driver.Stage(
            stage_id="catalog_after_final",
            phase="reports",
            label="catalog after final",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("final",),
        ),
        driver.Stage(
            stage_id="validator",
            phase="validators",
            label="validator",
            argv=(driver.PYTHON, "validator.py"),
            depends_on=("catalog_after_final",),
        ),
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "final.py":
            return subprocess.CompletedProcess(argv, 1, stdout="blocked final\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = driver.run_pipeline(stages, python="PY")

    captured = capsys.readouterr()
    assert code == 1
    assert [result.stage_id for result in results] == ["catalog_a", "catalog_b", "final", "catalog_after_final"]
    assert calls == [
        ["PY", "scripts/e4_parity/build_artifact_catalog.py"],
        ["PY", "scripts/e4_parity/build_artifact_catalog.py"],
        ["PY", "final.py"],
        ["PY", "scripts/e4_parity/build_artifact_catalog.py"],
    ]
    assert "CONTINUING blocked stage final exit=1" in captured.err
    assert "STOPPING before validators" in captured.err
    assert "validator.py" not in captured.out


def test_run_pipeline_stops_on_unallowed_blocker_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stages = (
        driver.Stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        driver.Stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        driver.Stage(
            stage_id="ct",
            phase="reports",
            label="ct",
            argv=(driver.PYTHON, "ct.py"),
            depends_on=("catalog_b",),
            blocker="ct schema error",
            allowed_exit_codes=(1,),
        ),
        driver.Stage(
            stage_id="sync",
            phase="reports",
            label="sync",
            argv=(driver.PYTHON, "sync.py"),
            depends_on=("ct",),
        ),
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "ct.py":
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="schema error\n")
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = driver.run_pipeline(stages, python="PY")

    captured = capsys.readouterr()
    assert code == 2
    assert [result.stage_id for result in results] == ["catalog_a", "catalog_b", "ct"]
    assert calls == [["PY", "scripts/e4_parity/build_artifact_catalog.py"], ["PY", "scripts/e4_parity/build_artifact_catalog.py"], ["PY", "ct.py"]]
    assert "FAILED stage ct exit=2" in captured.err
    assert "schema error" in captured.err
