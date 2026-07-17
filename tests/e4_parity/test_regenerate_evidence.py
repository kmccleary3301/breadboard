from __future__ import annotations

import json
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity import regen as front_door
from scripts.e4_parity import build_source_index
from scripts.e4_parity import regenerate_evidence as driver


def test_stage_expansion_reloads_readiness_for_each_candidate_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_a = tmp_path / "candidate-a"
    candidate_b = tmp_path / "candidate-b"
    observed_workspaces: list[str] = []

    class FakeReadiness:
        @staticmethod
        def expected_points() -> int:
            return 100 if Path(driver.os.environ["BB_WORKSPACE_ROOT"]) == candidate_a else 200

        @staticmethod
        def _expected_target_support_claims() -> int:
            return 10 if Path(driver.os.environ["BB_WORKSPACE_ROOT"]) == candidate_a else 20

    fake_readiness = FakeReadiness()

    def fake_reload(module: object) -> object:
        assert module is fake_readiness
        observed_workspaces.append(driver.os.environ["BB_WORKSPACE_ROOT"])
        return module

    monkeypatch.setenv("BB_WORKSPACE_ROOT", "original-workspace")
    monkeypatch.setattr(driver.importlib, "import_module", lambda _name: fake_readiness)
    monkeypatch.setattr(driver.importlib, "reload", fake_reload)
    stage = driver.Stage(
        stage_id="execution_values",
        phase="validators",
        label="resolve candidate-local expected values",
        argv=(driver.PYTHON, driver.EXPECTED_POINTS, driver.EXPECTED_CLAIMS),
    )

    assert stage.expanded_argv(
        "PY", resolve_execution_values=True, workspace_root=candidate_a
    ) == ["PY", "100", "10"]
    assert stage.expanded_argv(
        "PY", resolve_execution_values=True, workspace_root=candidate_b
    ) == ["PY", "200", "20"]
    assert observed_workspaces == [str(candidate_a), str(candidate_b)]
    assert driver.os.environ["BB_WORKSPACE_ROOT"] == "original-workspace"


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

@pytest.fixture(autouse=True)
def _stub_candidate_immutable_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic transaction tests isolate candidate mechanics from the real 25 MB bundle."""

    monkeypatch.setattr(driver, "provision_immutable_inputs", lambda *args, **kwargs: None)


def test_support_claim_generator_prefers_checkout_modules_over_pythonpath(
    tmp_path: Path,
) -> None:
    poison_root = tmp_path / "poison"
    for relative in (
        "scripts/__init__.py",
        "scripts/e4_parity/__init__.py",
        "scripts/e4_parity/validators/__init__.py",
        "scripts/e4_parity/validators/hash_utils.py",
    ):
        path = poison_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(poison_root)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/e4_parity/generate_support_claims.py",
            "--help",
        ],
        cwd=driver.ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


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
    assert positions["support_claim_generation"] < positions[lane_stages[0].stage_id]
    assert positions[lane_stages[-1].stage_id] < positions["catalog_full"]
    assert positions["catalog_full"] < positions["ct_scenarios"]
    north_star = next(
        stage for stage in driver.STAGES if stage.stage_id == "north_star_proof_packets"
    )
    north_star_writes = driver._declared_stage_writes(north_star)
    assert (
        "docs/conformance/e4_target_support/claude_code_north_star_capture_v1"
        in north_star_writes
    )
    assert all(
        not write.startswith("tmp/e4_regen_capture/")
        for stage in driver.STAGES
        for write in stage.writes
    )
    pi_capture = next(
        stage
        for stage in driver.STAGES
        if stage.stage_id == "pi_p5_cli_config_context_tool_surface"
    )
    assert (
        "docs/conformance/e4_target_support/pi_p5_l1_cli_config_context_tool_surface"
        not in pi_capture.writes
    )
    assert (
        "docs/conformance/e4_target_support/pi_p5_l1_cli_config_context_tool_surface/raw_capture_manifest.json"
        in pi_capture.writes
    )
    p66_capture = next(
        stage
        for stage in driver.STAGES
        if stage.stage_id == "oh_my_pi_p66_task_job_subagent"
    )
    assert (
        "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent"
        not in p66_capture.writes
    )


def test_canonical_transaction_groups_isolate_candidate_rebind_and_final_c4() -> None:
    """The canonical DAG is first validated as one scratch candidate, then rebound and finally C4-validated."""
    candidate_stages, canonical_rebind_stages, final_c4_stages = (
        driver._canonical_transaction_groups(driver.STAGES)
    )

    assert [stage.stage_id for stage in candidate_stages] == [
        stage.stage_id for stage in driver.STAGES
    ]
    for stage_id in ("source_index", "ledger_seed"):
        source_stage = next(stage for stage in driver.STAGES if stage.stage_id == stage_id)
        candidate_stage = next(stage for stage in candidate_stages if stage.stage_id == stage_id)
        assert candidate_stage.argv == source_stage.argv
        assert candidate_stage.reads == source_stage.reads
        assert candidate_stage.writes == source_stage.writes
    source_index = next(stage for stage in candidate_stages if stage.stage_id == "source_index")
    ledger_seed = next(stage for stage in candidate_stages if stage.stage_id == "ledger_seed")
    assert source_index.depends_on == ()
    assert ledger_seed.depends_on == ("oh_my_pi_p66_task_job_subagent",)
    ledger_report = next(stage for stage in candidate_stages if stage.stage_id == "ledger_report")
    catalog_stable = next(
        stage for stage in candidate_stages if stage.stage_id == "catalog_stable_snapshot"
    )
    assert ledger_report.depends_on == ("ledger_seed",)
    assert ledger_report.writes == (
        "../docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_REPORT.json",
    )
    assert catalog_stable.depends_on == ("ledger_report",)
    final_readiness = next(
        stage for stage in candidate_stages if stage.stage_id == "final_readiness_packet"
    )
    assert set(final_readiness.writes) == {
        "../docs_tmp/phase_15/BB_E4_COMPATIBILITY_MIGRATION_NOTES.md",
        "../docs_tmp/phase_15/BB_E4_SCORE_SUBLEDGER.json",
        "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json",
        "../docs_tmp/phase_15/BB_E4_CURRENT_BASELINE.json",
        "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_PROGRESS.json",
        "../docs_tmp/phase_15/BB_E4_PRIMITIVE_FAMILY_READINESS_REPORT.json",
        "../docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_VALIDATION_REPORT.json",
        "../docs_tmp/phase_15/BB_E4_FINAL_READINESS_REPORT.md",
        "../docs_tmp/phase_15/oh_my_pi_p6/BB_E4_OH_MY_PI_P6_TERMINAL_HASH_MANIFEST.json",
        "../docs_tmp/phase_15/BB_E4_PRIMITIVE_PARITY_SCORECARD.json",
        "../docs_tmp/phase_15/BB_E4_FINAL_ARTIFACT_FRESHNESS_MANIFEST.json",
    }
    source_freeze = next(
        stage
        for stage in candidate_stages
        if stage.stage_id == "materialize_oh_my_pi_source_freeze"
    )
    pilot_capture = next(
        stage
        for stage in candidate_stages
        if stage.stage_id == "oh_my_pi_p66_task_job_subagent"
    )
    assert source_freeze.writes == (
        "../docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest",
    )
    assert "materialize_oh_my_pi_p66_lane_lock" not in {stage.stage_id for stage in candidate_stages}
    assert pilot_capture.depends_on == ("oh_my_pi_l6_tui_projection",) and pilot_capture.argv[pilot_capture.argv.index("--lane") + 1] == "oh_my_pi_p6_6_task_job_subagent_v2"
    source_north_star = next(
        stage for stage in driver.STAGES if stage.stage_id == "north_star_proof_packets"
    )
    candidate_north_star = next(
        stage for stage in candidate_stages if stage.stage_id == "north_star_proof_packets"
    )
    source_stage_value = source_north_star.argv[source_north_star.argv.index("--stage") + 1]
    candidate_stage_value = candidate_north_star.argv[
        candidate_north_star.argv.index("--stage") + 1
    ]

    assert source_stage_value == "capture"
    assert source_north_star.argv.count("--defer-derived-writes") == 1
    assert candidate_stage_value == "capture"
    assert candidate_north_star.argv.count("--defer-derived-writes") == 1
    assert canonical_rebind_stages[0].stage_id == "catalog_claim_binding_snapshot"
    assert all(stage.phase != "validators" for stage in canonical_rebind_stages)
    assert final_c4_stages
    assert all(stage.phase == "validators" for stage in final_c4_stages)


def test_every_workspace_static_report_is_bundled_or_regenerated() -> None:
    role_registry = json.loads(
        Path("docs/conformance/e4_report_roles.json").read_text(encoding="utf-8")
    )
    bundle_manifest = json.loads(
        Path(
            "config/e4_lanes/evidence_inputs/e4_immutable_inputs.v1.manifest.json"
        ).read_text(encoding="utf-8")
    )
    bundled = {
        str(row["destination"])
        for row in bundle_manifest["members"]
        if row["namespace"] == "workspace"
    }
    regenerated = {
        path.removeprefix("../")
        for stage in driver.STAGES
        for path in driver._declared_stage_writes(stage)
        if path.startswith("../docs_tmp/") and "*" not in path
    }
    workspace_static_reports = {
        str(row["path"])
        for row in role_registry["static_artifact_roles"]
        if str(row["path"]).startswith("docs_tmp/")
    }

    assert workspace_static_reports <= bundled | regenerated
    unbundled_generated_names = {
        Path(path.removeprefix("../")).name
        for stage in driver.STAGES
        for path in driver._declared_stage_writes(stage)
        if path.startswith("../docs_tmp/phase_15/")
        and "*" not in path
        and Path(path).suffix in {".json", ".md"}
        and path.removeprefix("../") not in bundled
    }
    assert unbundled_generated_names <= build_source_index.GENERATED_NAMES

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

def test_capture_outputs_precede_catalog_and_no_capture_consumes_its_own_output() -> None:
    positions = {stage.stage_id: index for index, stage in enumerate(driver.STAGES)}
    capture_stages = [
        stage
        for stage in driver.STAGES
        if stage.argv[1:2] == ("scripts/e4_parity/run_lane.py",)
        and driver._arg_value(stage.argv, "--stage") == "capture"
    ]
    assert capture_stages
    assert all(positions[stage.stage_id] < positions["catalog_stable_snapshot"] for stage in capture_stages)
    for stage in capture_stages:
        overlaps = [
            (read, write)
            for read in stage.reads
            for write in stage.writes
            if driver._write_patterns_overlap(read, write)
        ]
        for lane_def in driver._lane_defs_for_stage(stage):
            for source in driver._lane_def_projection_sources(lane_def):
                assert source in stage.reads
                assert all(
                    not driver._write_patterns_overlap(source, write)
                    for write in stage.writes
                )
        assert overlaps == [], f"{stage.stage_id} consumes its own output: {overlaps}"

    stable_catalog = next(
        stage for stage in driver.STAGES if stage.stage_id == "catalog_stable_snapshot"
    )
    assert "--referenced-static-only" in stable_catalog.argv
    excluded = {
        stable_catalog.argv[index + 1]
        for index, value in enumerate(stable_catalog.argv[:-1])
        if value == "--exclude-lane-role"
    }
    assert excluded == {
        "capability_registry",
        "effective_config_graph",
        "effective_tool_surface",
        "evidence_manifest",
        "node_gate",
        "primitive_projection_manifest",
        "support_claim",
        "validator_output",
    }



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


def test_promotion_preserves_logical_paths_for_symlinked_lane_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    accepted_workspace = tmp_path / "accepted"
    accepted_root = accepted_workspace / "repo"
    candidate_workspace = tmp_path / "candidate"
    candidate_root = candidate_workspace / "repo"
    scratch = candidate_root / "tmp/e4_regen_capture/claude_code_north_star_capture_v1"
    scratch.mkdir(parents=True)
    (scratch / "raw_capture_manifest.json").write_text("{}\n", encoding="utf-8")
    logical = (
        candidate_root
        / "docs/conformance/e4_target_support/claude_code_north_star_capture_v1"
    )
    logical.parent.mkdir(parents=True)
    logical.symlink_to(driver.os.path.relpath(scratch, logical.parent))
    stage = next(
        stage
        for stage in driver.STAGES
        if stage.stage_id == "north_star_proof_packets"
    )
    assert all(not write.startswith("tmp/e4_regen_capture/") for write in stage.writes)
    monkeypatch.setattr(driver, "ROOT", accepted_root)
    monkeypatch.setattr(driver, "WORKSPACE", accepted_workspace)

    applied = driver._promote_write_set(
        candidate_root,
        (stage,),
        tmp_path / "transaction",
    )

    accepted_logical = (
        accepted_root
        / "docs/conformance/e4_target_support/claude_code_north_star_capture_v1"
    )
    assert accepted_logical.is_dir()
    assert not accepted_logical.is_symlink()
    driver.shutil.rmtree(candidate_workspace)
    assert (accepted_logical / "raw_capture_manifest.json").read_text(
        encoding="utf-8"
    ) == "{}\n"
    promoted = {
        destination.relative_to(accepted_root)
        for destination, _backup in applied
    }
    assert Path("docs/conformance/e4_target_support/claude_code_north_star_capture_v1") in promoted
    assert all(not path.as_posix().startswith("tmp/e4_regen_capture/") for path in promoted)


def test_regeneration_transaction_validation_failure_preserves_accepted_write_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A rejected complete candidate cannot change accepted bytes or start canonical post-promotion work."""
    accepted_a = tmp_path / "accepted" / "a.txt"
    accepted_b = tmp_path / "accepted" / "b.txt"
    accepted_a.parent.mkdir()
    accepted_a.write_bytes(b"accepted-a\n")
    accepted_b.write_bytes(b"accepted-b\n")
    accepted_before = {accepted_a: accepted_a.read_bytes(), accepted_b: accepted_b.read_bytes()}
    candidate_stages = (
        driver.Stage(
            stage_id="candidate_a",
            phase="lane_artifacts",
            label="candidate a",
            argv=(driver.PYTHON, "candidate_a.py"),
            writes=("accepted/a.txt",),
        ),
        driver.Stage(
            stage_id="candidate_b",
            phase="lane_artifacts",
            label="candidate b",
            argv=(driver.PYTHON, "candidate_b.py"),
            depends_on=("candidate_a",),
            writes=("accepted/b.txt",),
        ),
        driver.Stage(
            stage_id="candidate_validate",
            phase="reports",
            label="validate complete candidate",
            argv=(driver.PYTHON, "candidate_validate.py"),
            depends_on=("candidate_b",),
            read_only=True,
        ),
    )
    post_promotion_stages = (
        driver.Stage(
            stage_id="canonical_rebind",
            phase="reports",
            label="canonical rebind",
            argv=(driver.PYTHON, "canonical_rebind.py"),
            read_only=True,
        ),
    )
    final_c4_stages = (
        driver.Stage(
            stage_id="final_c4_validate",
            phase="validators",
            label="final C4",
            argv=(driver.PYTHON, "final_c4_validate.py"),
            read_only=True,
        ),
    )
    calls: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = argv[-1]
        calls.append(command)
        execution_root = Path(kwargs["cwd"])
        if command == "candidate_a.py":
            path = execution_root / "accepted" / "a.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"candidate-a\n")
        elif command == "candidate_b.py":
            path = execution_root / "accepted" / "b.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"candidate-b\n")
        elif command == "candidate_validate.py":
            assert (execution_root / "accepted" / "a.txt").read_bytes() == b"candidate-a\n"
            assert (execution_root / "accepted" / "b.txt").read_bytes() == b"candidate-b\n"
            return subprocess.CompletedProcess(argv, 3, stdout="", stderr="candidate invalid\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = getattr(driver, "_run_regeneration_transaction", None)
    assert runner is not None, "regeneration must expose one scratch-to-accepted transaction"
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path.parent)
    monkeypatch.setattr(driver, "_tracked_checkout_paths", lambda: ())
    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = runner(
        candidate_stages,
        post_promotion_stages,
        final_c4_stages,
        python="PY",
    )

    assert code == 3
    assert [result.stage_id for result in results] == [
        "candidate_a",
        "candidate_b",
        "candidate_validate",
    ]
    assert calls == ["candidate_a.py", "candidate_b.py", "candidate_validate.py"]
    assert {path: path.read_bytes() for path in accepted_before} == accepted_before


def test_regeneration_transaction_rolls_back_all_paths_when_promotion_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed multi-path promotion restores old files and removes newly created accepted destinations."""
    accepted_a = tmp_path / "accepted" / "a.txt"
    accepted_new = tmp_path / "accepted" / "new.txt"
    blocking_path = tmp_path / "accepted" / "zz_blocked"
    accepted_a.parent.mkdir()
    accepted_a.write_bytes(b"accepted-a\n")
    blocking_path.write_bytes(b"accepted-blocker\n")
    candidate_stages = (
        driver.Stage(
            stage_id="candidate_write_set",
            phase="lane_artifacts",
            label="complete candidate write set",
            argv=(driver.PYTHON, "candidate_write_set.py"),
            writes=(
                "accepted/a.txt",
                "accepted/new.txt",
                "accepted/zz_blocked/b.txt",
            ),
        ),
        driver.Stage(
            stage_id="candidate_validate",
            phase="reports",
            label="validate complete candidate",
            argv=(driver.PYTHON, "candidate_validate.py"),
            depends_on=("candidate_write_set",),
            read_only=True,
        ),
    )
    post_promotion_stages = (
        driver.Stage(
            stage_id="canonical_rebind",
            phase="reports",
            label="canonical rebind",
            argv=(driver.PYTHON, "canonical_rebind.py"),
            read_only=True,
        ),
    )
    final_c4_stages = (
        driver.Stage(
            stage_id="final_c4_validate",
            phase="validators",
            label="final C4",
            argv=(driver.PYTHON, "final_c4_validate.py"),
            read_only=True,
        ),
    )
    calls: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = argv[-1]
        calls.append(command)
        execution_root = Path(kwargs["cwd"])
        if command == "candidate_write_set.py":
            accepted_dir = execution_root / "accepted"
            accepted_dir.mkdir(parents=True, exist_ok=True)
            (accepted_dir / "a.txt").write_bytes(b"candidate-a\n")
            (accepted_dir / "new.txt").write_bytes(b"candidate-new\n")
            scratch_blocker = accepted_dir / "zz_blocked"
            if scratch_blocker.is_file():
                scratch_blocker.unlink()
            scratch_blocker.mkdir(exist_ok=True)
            (scratch_blocker / "b.txt").write_bytes(b"candidate-b\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = getattr(driver, "_run_regeneration_transaction", None)
    assert runner is not None, "regeneration must expose one scratch-to-accepted transaction"
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path.parent)
    monkeypatch.setattr(driver, "_tracked_checkout_paths", lambda: ())
    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = runner(
        candidate_stages,
        post_promotion_stages,
        final_c4_stages,
        python="PY",
    )

    assert code != 0
    assert [result.stage_id for result in results] == [
        "candidate_write_set",
        "candidate_validate",
    ]
    assert calls == ["candidate_write_set.py", "candidate_validate.py"]
    assert accepted_a.read_bytes() == b"accepted-a\n"
    assert not accepted_new.exists()
    assert blocking_path.read_bytes() == b"accepted-blocker\n"


def test_regeneration_transaction_promotes_before_rebind_then_runs_final_c4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Validation sees a complete scratch candidate while rebind and final C4 see only the promoted write set."""
    accepted_a = tmp_path / "accepted" / "a.txt"
    accepted_b = tmp_path / "accepted" / "b.txt"
    accepted_a.parent.mkdir()
    accepted_a.write_bytes(b"accepted-a\n")
    accepted_b.write_bytes(b"accepted-b\n")
    candidate_stages = (
        driver.Stage(
            stage_id="candidate_write_set",
            phase="lane_artifacts",
            label="complete candidate write set",
            argv=(driver.PYTHON, "candidate_write_set.py"),
            writes=("accepted/a.txt", "accepted/b.txt"),
        ),
        driver.Stage(
            stage_id="candidate_validate",
            phase="reports",
            label="validate complete candidate",
            argv=(driver.PYTHON, "candidate_validate.py"),
            depends_on=("candidate_write_set",),
            read_only=True,
        ),
    )
    post_promotion_stages = (
        driver.Stage(
            stage_id="canonical_rebind",
            phase="reports",
            label="canonical rebind",
            argv=(driver.PYTHON, "canonical_rebind.py"),
            read_only=True,
        ),
    )
    final_c4_stages = (
        driver.Stage(
            stage_id="final_c4_validate",
            phase="validators",
            label="final C4",
            argv=(driver.PYTHON, "final_c4_validate.py"),
            read_only=True,
        ),
    )
    observations: list[tuple[str, bytes, bytes]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = argv[-1]
        execution_root = Path(kwargs["cwd"])
        if command == "candidate_write_set.py":
            candidate_dir = execution_root / "accepted"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            (candidate_dir / "a.txt").write_bytes(b"candidate-a\n")
            (candidate_dir / "b.txt").write_bytes(b"candidate-b\n")
        elif command == "candidate_validate.py":
            assert Path(kwargs["env"]["BB_WORKSPACE_ROOT"]) == execution_root.parent
            assert (execution_root / "accepted" / "a.txt").read_bytes() == b"candidate-a\n"
            assert (execution_root / "accepted" / "b.txt").read_bytes() == b"candidate-b\n"
            observations.append((command, accepted_a.read_bytes(), accepted_b.read_bytes()))
        else:
            assert execution_root == tmp_path
            observations.append((command, accepted_a.read_bytes(), accepted_b.read_bytes()))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = getattr(driver, "_run_regeneration_transaction", None)
    assert runner is not None, "regeneration must expose one scratch-to-accepted transaction"
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path.parent)
    monkeypatch.setattr(driver, "_tracked_checkout_paths", lambda: ())
    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = runner(
        candidate_stages,
        post_promotion_stages,
        final_c4_stages,
        python="PY",
    )

    assert code == 0
    assert [result.stage_id for result in results] == [
        "candidate_write_set",
        "candidate_validate",
        "canonical_rebind",
        "final_c4_validate",
    ]
    assert observations == [
        ("candidate_validate.py", b"accepted-a\n", b"accepted-b\n"),
        ("canonical_rebind.py", b"candidate-a\n", b"candidate-b\n"),
        ("final_c4_validate.py", b"candidate-a\n", b"candidate-b\n"),
    ]


def test_regeneration_transaction_rebind_failure_restores_accepted_write_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A canonical-rebind failure rolls back the promoted candidate and never starts final C4."""
    accepted_a = tmp_path / "accepted" / "a.txt"
    accepted_b = tmp_path / "accepted" / "b.txt"
    accepted_a.parent.mkdir()
    accepted_a.write_bytes(b"accepted-a\n")
    accepted_b.write_bytes(b"accepted-b\n")
    accepted_before = {accepted_a: accepted_a.read_bytes(), accepted_b: accepted_b.read_bytes()}
    candidate_stages = (
        driver.Stage(
            stage_id="candidate_write_set",
            phase="lane_artifacts",
            label="complete candidate write set",
            argv=(driver.PYTHON, "candidate_write_set.py"),
            writes=("accepted/a.txt", "accepted/b.txt"),
        ),
        driver.Stage(
            stage_id="candidate_validate",
            phase="reports",
            label="validate complete candidate",
            argv=(driver.PYTHON, "candidate_validate.py"),
            depends_on=("candidate_write_set",),
            read_only=True,
        ),
    )
    canonical_rebind_stages = (
        driver.Stage(
            stage_id="canonical_rebind",
            phase="reports",
            label="canonical rebind",
            argv=(driver.PYTHON, "canonical_rebind.py"),
            read_only=True,
        ),
    )
    final_c4_stages = (
        driver.Stage(
            stage_id="final_c4_validate",
            phase="validators",
            label="final C4",
            argv=(driver.PYTHON, "final_c4_validate.py"),
            read_only=True,
        ),
    )
    calls: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = argv[-1]
        calls.append(command)
        execution_root = Path(kwargs["cwd"])
        if command == "candidate_write_set.py":
            candidate_dir = execution_root / "accepted"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            (candidate_dir / "a.txt").write_bytes(b"candidate-a\n")
            (candidate_dir / "b.txt").write_bytes(b"candidate-b\n")
        elif command == "canonical_rebind.py":
            assert accepted_a.read_bytes() == b"candidate-a\n"
            assert accepted_b.read_bytes() == b"candidate-b\n"
            return subprocess.CompletedProcess(argv, 4, stdout="", stderr="rebind failed\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = getattr(driver, "_run_regeneration_transaction", None)
    assert runner is not None, "regeneration must expose one scratch-to-accepted transaction"
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path.parent)
    monkeypatch.setattr(driver, "_tracked_checkout_paths", lambda: ())
    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = runner(
        candidate_stages,
        canonical_rebind_stages,
        final_c4_stages,
        python="PY",
    )

    assert code == 4
    assert [result.stage_id for result in results] == [
        "candidate_write_set",
        "candidate_validate",
        "canonical_rebind",
    ]
    assert calls == [
        "candidate_write_set.py",
        "candidate_validate.py",
        "canonical_rebind.py",
    ]
    assert {path: path.read_bytes() for path in accepted_before} == accepted_before


def test_regeneration_transaction_final_c4_failure_restores_rebind_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A final-C4 failure restores the pre-transaction bytes, including paths mutated during rebinding."""
    accepted_a = tmp_path / "accepted" / "a.txt"
    accepted_b = tmp_path / "accepted" / "b.txt"
    accepted_a.parent.mkdir()
    accepted_a.write_bytes(b"accepted-a\n")
    accepted_b.write_bytes(b"accepted-b\n")
    accepted_before = {accepted_a: accepted_a.read_bytes(), accepted_b: accepted_b.read_bytes()}
    candidate_stages = (
        driver.Stage(
            stage_id="candidate_write_set",
            phase="lane_artifacts",
            label="complete candidate write set",
            argv=(driver.PYTHON, "candidate_write_set.py"),
            writes=("accepted/a.txt", "accepted/b.txt"),
        ),
        driver.Stage(
            stage_id="candidate_validate",
            phase="reports",
            label="validate complete candidate",
            argv=(driver.PYTHON, "candidate_validate.py"),
            depends_on=("candidate_write_set",),
            read_only=True,
        ),
    )
    canonical_rebind_stages = (
        driver.Stage(
            stage_id="canonical_rebind",
            phase="reports",
            label="canonical rebind",
            argv=(driver.PYTHON, "canonical_rebind.py"),
            writes=("accepted/a.txt",),
        ),
    )
    final_c4_stages = (
        driver.Stage(
            stage_id="final_c4_validate",
            phase="validators",
            label="final C4",
            argv=(driver.PYTHON, "final_c4_validate.py"),
            read_only=True,
        ),
    )
    calls: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = argv[-1]
        calls.append(command)
        execution_root = Path(kwargs["cwd"])
        if command == "candidate_write_set.py":
            candidate_dir = execution_root / "accepted"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            (candidate_dir / "a.txt").write_bytes(b"candidate-a\n")
            (candidate_dir / "b.txt").write_bytes(b"candidate-b\n")
        elif command == "canonical_rebind.py":
            assert accepted_a.read_bytes() == b"candidate-a\n"
            assert accepted_b.read_bytes() == b"candidate-b\n"
            accepted_a.write_bytes(b"rebound-a\n")
        elif command == "final_c4_validate.py":
            assert accepted_a.read_bytes() == b"rebound-a\n"
            assert accepted_b.read_bytes() == b"candidate-b\n"
            return subprocess.CompletedProcess(argv, 5, stdout="", stderr="final C4 failed\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = getattr(driver, "_run_regeneration_transaction", None)
    assert runner is not None, "regeneration must expose one scratch-to-accepted transaction"
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path.parent)
    monkeypatch.setattr(driver, "_tracked_checkout_paths", lambda: ())
    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    code, results = runner(
        candidate_stages,
        canonical_rebind_stages,
        final_c4_stages,
        python="PY",
    )

    assert code == 5
    assert [result.stage_id for result in results] == [
        "candidate_write_set",
        "candidate_validate",
        "canonical_rebind",
        "final_c4_validate",
    ]
    assert calls == [
        "candidate_write_set.py",
        "candidate_validate.py",
        "canonical_rebind.py",
        "final_c4_validate.py",
    ]
    assert {path: path.read_bytes() for path in accepted_before} == accepted_before


def test_prepare_candidate_root_preserves_dangling_symlinks_and_excludes_untracked_local_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    ordinary_file = source_root / "scripts" / "required.py"
    ordinary_file.parent.mkdir(parents=True)
    ordinary_file.write_text("print('required input')\n")
    dangling_link = source_root / "dangling-link"
    dangling_target = Path("missing-target")
    dangling_link.symlink_to(dangling_target)
    subprocess.run(["git", "init", "-q"], cwd=source_root, check=True)
    subprocess.run(
        ["git", "add", "scripts/required.py", dangling_link.name],
        cwd=source_root,
        check=True,
    )

    excluded_sentinel = (
        source_root / ".venv_linux_import_20260618" / "must-not-copy"
    )
    excluded_sentinel.parent.mkdir()
    excluded_sentinel.write_text("untracked local state\n")

    candidate_root = tmp_path / "candidate"
    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path)

    driver._prepare_candidate_root(candidate_root, ())

    assert (candidate_root / "scripts" / "required.py").read_text() == (
        "print('required input')\n"
    )
    copied_link = candidate_root / dangling_link.name
    assert copied_link.is_symlink()
    assert copied_link.readlink() == dangling_target
    assert not (
        candidate_root / ".venv_linux_import_20260618" / "must-not-copy"
    ).exists()


def test_prepare_candidate_root_provisions_bundle_and_ignores_live_declared_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    workspace = tmp_path / "live-workspace"
    ambient = workspace / "docs_tmp" / "phase_15" / "ambient.json"
    ambient.parent.mkdir(parents=True)
    ambient.write_text('{"must_not_copy": true}\n')
    candidate_root = tmp_path / "scratch" / "candidate"
    stage = driver.Stage(
        stage_id="ambient_read",
        phase="test",
        label="ambient read must not be seeded",
        argv=("PY",),
        reads=("docs_tmp/phase_15/ambient.json",),
    )
    calls: list[tuple[Path, Path, Path, Path]] = []

    def fake_provision(
        archive: Path,
        manifest: Path,
        *,
        repo_root: Path,
        workspace_root: Path,
    ) -> None:
        calls.append((archive, manifest, repo_root, workspace_root))
        repo_input = repo_root / "provisioned" / "repo-input.json"
        workspace_input = workspace_root / "docs_tmp" / "phase_15" / "workspace-input.json"
        repo_input.parent.mkdir(parents=True)
        workspace_input.parent.mkdir(parents=True)
        repo_input.write_text('{"source": "bundle"}\n')
        workspace_input.write_text('{"source": "bundle"}\n')

    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", workspace)
    monkeypatch.setattr(driver, "_tracked_checkout_paths", lambda: ())
    monkeypatch.setattr(driver, "provision_immutable_inputs", fake_provision)

    driver._prepare_candidate_root(candidate_root, (stage,))

    bundle_root = candidate_root / "config/e4_lanes/evidence_inputs"
    assert calls == [
        (
            bundle_root / "e4_immutable_inputs.v1.zip",
            bundle_root / "e4_immutable_inputs.v1.manifest.json",
            candidate_root,
            candidate_root.parent,
        )
    ]
    assert (candidate_root / "provisioned/repo-input.json").read_text() == '{"source": "bundle"}\n'
    assert (candidate_root.parent / "docs_tmp/phase_15/workspace-input.json").read_text() == (
        '{"source": "bundle"}\n'
    )
    assert not (candidate_root / "docs_tmp/phase_15/ambient.json").exists()


def test_prepare_candidate_root_installs_ledger_bootstrap_in_candidate_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    candidate_root = tmp_path / "scratch" / "candidate"
    lane_stage = next(
        stage
        for stage in driver.STAGES
        if stage.stage_id == "oh_my_pi_p66_task_job_subagent"
    )
    bootstrap_payload = b'{"bootstrap": true}\n'

    def fake_provision(
        _archive: Path,
        _manifest: Path,
        *,
        repo_root: Path,
        workspace_root: Path,
    ) -> None:
        assert workspace_root == candidate_root.parent
        bootstrap = (
            repo_root
            / "config/e4_lanes/evidence_inputs/e4_regen_bootstrap"
            / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
        )
        bootstrap.parent.mkdir(parents=True)
        bootstrap.write_bytes(bootstrap_payload)

    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path)
    monkeypatch.setattr(driver, "_tracked_checkout_paths", lambda: ())
    monkeypatch.setattr(driver, "provision_immutable_inputs", fake_provision)

    driver._prepare_candidate_root(candidate_root, (lane_stage,))

    workspace_seed = (
        candidate_root.parent
        / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    )
    assert workspace_seed.read_bytes() == bootstrap_payload
    assert not (
        candidate_root
        / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    ).exists()


def test_prepare_candidate_root_rejects_gitlink_without_copying_its_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    untracked_gitlink_file = source_root / "vendor" / "module" / "local-only.txt"
    untracked_gitlink_file.parent.mkdir(parents=True)
    untracked_gitlink_file.write_text("must not enter candidate\n")
    candidate_root = tmp_path / "scratch" / "candidate"
    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path)
    monkeypatch.setattr(
        driver,
        "_tracked_checkout_paths",
        lambda: (("160000", "0" * 40, Path("vendor/module")),),
    )

    with pytest.raises(RuntimeError, match="cannot materialize tracked gitlink"):
        driver._prepare_candidate_root(candidate_root, ())

    assert not (candidate_root / "vendor" / "module").exists()


def test_prepare_candidate_root_rejects_tracked_symlink_escaping_scratch_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    escaping_link = source_root / "escape"
    escaping_link.symlink_to(Path("../..") / "host-state")
    subprocess.run(["git", "init", "-q"], cwd=source_root, check=True)
    subprocess.run(["git", "add", escaping_link.name], cwd=source_root, check=True)
    candidate_root = tmp_path / "scratch" / "candidate"
    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path)

    with pytest.raises(ValueError, match="candidate symlink escapes scratch workspace"):
        driver._prepare_candidate_root(candidate_root, ())

    assert not (candidate_root / escaping_link.name).is_symlink()


def test_prepare_candidate_root_materializes_index_content_when_checkout_file_deleted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    tracked = source_root / "tracked.py"
    tracked.write_text("print('index truth')\n")
    script = source_root / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=source_root, check=True)
    subprocess.run(
        ["git", "add", "tracked.py", "run.sh"], cwd=source_root, check=True
    )
    tracked.unlink()
    script.unlink()

    candidate_root = tmp_path / "scratch" / "candidate"
    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path)

    driver._prepare_candidate_root(candidate_root, ())

    assert (candidate_root / "tracked.py").read_text() == "print('index truth')\n"
    materialized_script = candidate_root / "run.sh"
    assert materialized_script.read_text() == "#!/bin/sh\n"
    assert materialized_script.stat().st_mode & 0o111


def test_prepare_candidate_root_ignores_untracked_directory_shadowing_tracked_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    tracked = source_root / "tracked-file"
    tracked.write_text("index truth\n")
    subprocess.run(["git", "init", "-q"], cwd=source_root, check=True)
    subprocess.run(["git", "add", "tracked-file"], cwd=source_root, check=True)
    tracked.unlink()
    shadow = source_root / "tracked-file"
    shadow.mkdir()
    (shadow / "untracked-leak.txt").write_text("must not enter candidate\n")

    candidate_root = tmp_path / "scratch" / "candidate"
    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path)

    driver._prepare_candidate_root(candidate_root, ())

    materialized = candidate_root / "tracked-file"
    assert materialized.is_file()
    assert not materialized.is_symlink()
    assert materialized.read_text() == "index truth\n"


def test_prepare_candidate_root_reads_index_not_symlinked_parent_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside" / "scripts"
    outside.mkdir(parents=True)
    (outside / "required.py").write_text("print('outside impostor')\n")
    source_root = tmp_path / "source"
    tracked = source_root / "scripts" / "required.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("print('index truth')\n")
    subprocess.run(["git", "init", "-q"], cwd=source_root, check=True)
    subprocess.run(
        ["git", "add", "scripts/required.py"], cwd=source_root, check=True
    )
    shutil.rmtree(source_root / "scripts")
    (source_root / "scripts").symlink_to(outside)

    candidate_root = tmp_path / "scratch" / "candidate"
    monkeypatch.setattr(driver, "ROOT", source_root)
    monkeypatch.setattr(driver, "WORKSPACE", tmp_path)

    driver._prepare_candidate_root(candidate_root, ())

    assert not (candidate_root / "scripts").is_symlink()
    materialized = candidate_root / "scripts" / "required.py"
    assert materialized.read_text() == "print('index truth')\n"
