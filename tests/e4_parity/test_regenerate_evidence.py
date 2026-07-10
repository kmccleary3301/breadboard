from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.e4_parity import regen as front_door
from scripts.e4_parity import regenerate_evidence as driver


def _synthetic_writing_stage(
    *,
    stage_id: str,
    phase: str,
    label: str,
    argv: tuple[str, ...],
    depends_on: tuple[str, ...] = (),
    blocker: str | None = None,
    allowed_exit_codes: tuple[int, ...] = (),
) -> driver.Stage:
    return driver.Stage(
        stage_id=stage_id,
        phase=phase,
        label=label,
        argv=argv,
        depends_on=depends_on,
        writes=(f"tmp/test_regenerate_evidence/{stage_id}.json",),
        blocker=blocker,
        allowed_exit_codes=allowed_exit_codes,
    )


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
        "catalog_claim_binding_snapshot",
        "catalog_full",
        "catalog_post_report_snapshot",
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
    assert positions["catalog_stable_snapshot"] < positions["catalog_claim_binding_snapshot"]
    assert positions["catalog_claim_binding_snapshot"] < positions["support_claim_generation"]
    assert positions["support_claim_generation"] < positions["catalog_full"]
    assert positions["catalog_full"] < positions["ct_scenarios"]
    assert positions["final_readiness_packet"] < positions["catalog_post_report_snapshot"] < positions["validate_e4_closure"] < positions["validate_report_hash_freshness"]
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
    assert freshness_stages[0].depends_on == ("validate_e4_closure",)
    assert positions["final_readiness_packet"] < positions["catalog_post_report_snapshot"] < positions["validate_report_hash_freshness"]
    assert driver.STAGES[-1].stage_id == "validate_report_hash_freshness"


def test_validators_phase_is_closure_then_report_hash_freshness() -> None:
    validator_stages = [stage.stage_id for stage in driver.STAGES if stage.phase == "validators"]

    assert validator_stages == ["validate_e4_closure", "validate_report_hash_freshness"]

def test_duplicate_stage_write_declarations_name_both_stage_ids() -> None:
    duplicate_path = "docs/conformance/shared_output.json"
    stages = (
        driver.Stage(
            stage_id="first_writer",
            phase="test",
            label="first writer",
            argv=(driver.PYTHON, "scripts/first_writer.py"),
            reads=("scripts/first_writer.py",),
            writes=(duplicate_path,),
        ),
        driver.Stage(
            stage_id="second_writer",
            phase="test",
            label="second writer",
            argv=(driver.PYTHON, "scripts/second_writer.py"),
            depends_on=("first_writer",),
            reads=("scripts/second_writer.py",),
            writes=(duplicate_path,),
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        driver.validate_stage_graph(stages)

    assert str(exc_info.value) == (
        "write path docs/conformance/shared_output.json is declared by both first_writer and second_writer"
    )


def test_read_only_stage_write_declarations_are_rejected() -> None:
    stages = (
        driver.Stage(
            stage_id="validator",
            phase="validators",
            label="validator",
            argv=(driver.PYTHON, "scripts/validator.py"),
            reads=("scripts/validator.py",),
            writes=("docs/conformance/validator_output.json",),
            read_only=True,
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        driver.validate_stage_graph(stages)

    assert str(exc_info.value) == "read_only stage validator must not declare writes"


def test_canonical_stages_declare_reads_and_output_contracts() -> None:
    driver.validate_stage_graph()

    stages_missing_reads = [stage.stage_id for stage in driver.STAGES if not stage.reads]
    stages_missing_output_contract = [
        stage.stage_id
        for stage in driver.STAGES
        if not stage.writes and not stage.read_only
    ]
    read_only_stages_with_writes = [
        stage.stage_id
        for stage in driver.STAGES
        if stage.read_only and stage.writes
    ]
    validator_stages = [stage for stage in driver.STAGES if stage.phase == "validators"]

    assert stages_missing_reads == []
    assert stages_missing_output_contract == []
    assert read_only_stages_with_writes == []
    assert validator_stages
    assert [stage.stage_id for stage in validator_stages] == [
        "validate_e4_closure",
        "validate_report_hash_freshness",
    ]
    assert all(stage.read_only for stage in validator_stages)
    assert all(stage.writes == () for stage in validator_stages)


def test_catalog_stage_writes_are_distinct_real_paths_with_one_default_owner() -> None:
    catalog_stages = [
        stage
        for stage in driver.STAGES
        if stage.argv[1:2] == ("scripts/e4_parity/build_artifact_catalog.py",)
    ]
    stage_writes = {stage.stage_id: stage.writes for stage in catalog_stages}

    assert stage_writes == {
        "catalog_stable_snapshot": ("docs/conformance/e4_artifact_catalog_stable_snapshot.json",),
        "catalog_claim_binding_snapshot": ("docs/conformance/e4_artifact_catalog.json",),
        "catalog_full": ("docs/conformance/e4_artifact_catalog_full_snapshot.json",),
        "catalog_post_report_snapshot": ("docs/conformance/e4_artifact_catalog_post_report_snapshot.json",),
    }

    catalog_write_owners = {
        write: stage.stage_id
        for stage in catalog_stages
        for write in stage.writes
    }

    assert len(catalog_write_owners) == sum(len(stage.writes) for stage in catalog_stages)
    assert all("*" not in write for write in catalog_write_owners)
    assert catalog_write_owners == {
        "docs/conformance/e4_artifact_catalog_stable_snapshot.json": "catalog_stable_snapshot",
        "docs/conformance/e4_artifact_catalog.json": "catalog_claim_binding_snapshot",
        "docs/conformance/e4_artifact_catalog_full_snapshot.json": "catalog_full",
        "docs/conformance/e4_artifact_catalog_post_report_snapshot.json": "catalog_post_report_snapshot",
    }


def test_lane_def_reverify_stages_refresh_their_declared_json_reports() -> None:
    lane_stages = [
        stage
        for stage in driver.STAGES
        if stage.stage_id.startswith("lane_def_reverify_")
    ]

    assert lane_stages
    for stage in lane_stages:
        assert stage.read_only is False
        json_out_index = stage.argv.index("--json-out")
        assert stage.writes == (stage.argv[json_out_index + 1],)
        assert "--check-only" not in stage.argv



def test_sync_conformance_matrix_stage_is_fail_closed_report_blocker() -> None:
    stages = {stage.stage_id: stage for stage in driver.STAGES}

    stage = stages["sync_conformance_matrix"]

    assert "--fail-on-summary-not-ok" in stage.argv
    assert stage.phase == "reports"
    assert stage.depends_on == ("ct_scenarios",)
    assert stage.allowed_exit_codes == (1,)
    assert stage.blocker == "Matrix sync remains fail-closed while its generated summary has ok=false."



def test_explain_prints_dag_with_blocker_and_no_pytest(capsys: pytest.CaptureFixture[str]) -> None:
    assert front_door.main(["--python", "PY", "explain"]) == 0

    out = capsys.readouterr().out
    assert "E4 evidence regeneration DAG" in out
    assert "catalog_stable_snapshot" in out
    assert "catalog_full" in out
    assert "BLOCKER" not in out
    assert "blocker: CT is fail-closed" in out
    assert "pytest" not in out


def test_front_door_explain_does_not_execute_subprocess(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def forbidden_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("explain must not execute subprocess.run")

    monkeypatch.setattr(driver.subprocess, "run", forbidden_run)

    assert front_door.main(["--python", "PY", "explain"]) == 0
    out = capsys.readouterr().out
    assert "E4 evidence regeneration DAG" in out
    assert "PY scripts/e4_parity/build_artifact_catalog.py" in out


def test_run_pipeline_records_stage_durations_and_prints_timing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stages = (
        _synthetic_writing_stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        _synthetic_writing_stage(
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
        _synthetic_writing_stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        _synthetic_writing_stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        _synthetic_writing_stage(
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

    assert front_door.main(["--python", "PY", "run", "--json"]) == 0

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
        _synthetic_writing_stage(
            stage_id="first",
            phase="lane_artifacts",
            label="first",
            argv=(driver.PYTHON, "first.py"),
        ),
        _synthetic_writing_stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("first",),
        ),
        _synthetic_writing_stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        _synthetic_writing_stage(
            stage_id="ct",
            phase="reports",
            label="ct",
            argv=(driver.PYTHON, "ct.py"),
            depends_on=("catalog_b",),
            blocker="known CT blocker",
        ),
        _synthetic_writing_stage(
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
        _synthetic_writing_stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        _synthetic_writing_stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        _synthetic_writing_stage(
            stage_id="ct",
            phase="reports",
            label="ct",
            argv=(driver.PYTHON, "ct.py"),
            depends_on=("catalog_b",),
            blocker="ct wrote fail-closed outputs",
            allowed_exit_codes=(1,),
        ),
        _synthetic_writing_stage(
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
        _synthetic_writing_stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        _synthetic_writing_stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        _synthetic_writing_stage(
            stage_id="final",
            phase="reports",
            label="final",
            argv=(driver.PYTHON, "final.py"),
            depends_on=("catalog_b",),
            blocker="blocked final",
            allowed_exit_codes=(1,),
        ),
        _synthetic_writing_stage(
            stage_id="catalog_after_final",
            phase="reports",
            label="catalog after final",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("final",),
        ),
        _synthetic_writing_stage(
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
        _synthetic_writing_stage(
            stage_id="catalog_a",
            phase="catalog_rev_n",
            label="catalog a",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
        ),
        _synthetic_writing_stage(
            stage_id="catalog_b",
            phase="catalog_rev_n_plus_1",
            label="catalog b",
            argv=(driver.PYTHON, "scripts/e4_parity/build_artifact_catalog.py"),
            depends_on=("catalog_a",),
        ),
        _synthetic_writing_stage(
            stage_id="ct",
            phase="reports",
            label="ct",
            argv=(driver.PYTHON, "ct.py"),
            depends_on=("catalog_b",),
            blocker="ct schema error",
            allowed_exit_codes=(1,),
        ),
        _synthetic_writing_stage(
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
