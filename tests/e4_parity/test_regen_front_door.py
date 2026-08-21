from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import ValidationError
import yaml

from scripts.e4_parity import regen as front_door


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
REGEN_PLAN_SCHEMA_PATH = SCHEMA_DIR / "bb.e4.regen_plan.v1.schema.json"
FIXED_POINT_SCHEMA_PATH = SCHEMA_DIR / "bb.e4.fixed_point_report.v1.schema.json"
PACKS_PATH = ROOT / "contracts" / "kernel" / "packs.v1.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator_for(schema_path: Path) -> Draft202012Validator:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    store: dict[str, dict[str, Any]] = {}
    for schema_path in SCHEMA_DIR.glob("*.json"):
        loaded = _load_json(schema_path)
        store[schema_path.name] = loaded
        store[schema_path.resolve().as_uri()] = loaded
        schema_id = loaded.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = loaded
    resolver = RefResolver(
        base_uri=SCHEMA_DIR.resolve().as_uri() + "/",
        referrer=schema,
        store=store,
    )
    return Draft202012Validator(schema, resolver=resolver)


def _regen_plan_validator() -> Draft202012Validator:
    return _validator_for(REGEN_PLAN_SCHEMA_PATH)


def _fixed_point_validator() -> Draft202012Validator:
    return _validator_for(FIXED_POINT_SCHEMA_PATH)


def _schema_errors(
    validator: Draft202012Validator, payload: dict[str, Any]
) -> list[ValidationError]:
    return sorted(
        validator.iter_errors(payload),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )


def _format_error(error: ValidationError) -> str:
    path = "/" + "/".join(str(part) for part in error.absolute_path)
    return f"{path}: {error.message}"


def test_e4_battery_workflow_runs_complete_pytest_and_fixed_point_gates() -> None:
    workflow_path = ROOT / ".github/workflows/e4-battery.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["e4-battery"]
    commands = {
        step["name"]: step["run"]
        for step in job["steps"]
        if isinstance(step, dict) and "name" in step and "run" in step
    }

    assert job["env"]["BB_WORKSPACE_ROOT"] == "${{ github.workspace }}/.."
    assert commands["Provision immutable E4 inputs"] == (
        "python scripts/e4_parity/provision_immutable_inputs.py "
        '--repo-root "$GITHUB_WORKSPACE" --workspace-root "$GITHUB_WORKSPACE/.."'
    )
    assert commands["Run full literal E4 parity battery"] == "pytest -q tests/e4_parity"
    assert commands["Verify exact 1000-point fixed-point regeneration"] == (
        "python scripts/e4_parity/regen.py fixed-point "
        "--expected-points 1000 --expected-target-claims 10 "
        "--expected-non-target-claims 8 "
        "--json artifacts/conformance/e4_regen_fixed_point.json"
    )
    assert "--ignore" not in workflow_text
    assert "--deselect" not in workflow_text
    assert "--skip" not in workflow_text
    assert "--continue-on-error" not in workflow_text
    assert "continue-on-error:" not in workflow_text


def test_product_spine_provisions_workspace_evidence() -> None:
    workflow_path = ROOT / ".github/workflows/ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["product-spine"]
    commands = {
        step["name"]: step["run"]
        for step in job["steps"]
        if isinstance(step, dict) and "name" in step and "run" in step
    }

    assert job["env"]["BB_WORKSPACE_ROOT"] == "${{ github.workspace }}/.."
    assert commands["Provision immutable E4 inputs"] == (
        "python scripts/e4_parity/provision_immutable_inputs.py "
        '--repo-root "$GITHUB_WORKSPACE" --workspace-root "$GITHUB_WORKSPACE/.."'
    )


def test_e4_battery_workflow_compares_two_independent_clean_checkouts() -> None:
    workflow_path = ROOT / ".github/workflows/e4-battery.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    clean_job = workflow["jobs"]["fixed-point-clean-checkout"]
    compare_job = workflow["jobs"]["fixed-point-clean-checkout-compare"]
    clean_commands = {
        step["name"]: step["run"]
        for step in clean_job["steps"]
        if isinstance(step, dict) and "name" in step and "run" in step
    }

    assert clean_job["strategy"]["matrix"]["pass"] == ["first", "second"]
    assert clean_job["env"]["BB_WORKSPACE_ROOT"] == "${{ github.workspace }}/.."
    assert clean_commands["Provision immutable E4 inputs"] == (
        "python scripts/e4_parity/provision_immutable_inputs.py "
        '--repo-root "$GITHUB_WORKSPACE" --workspace-root "$GITHUB_WORKSPACE/.."'
    )
    assert clean_commands["Generate fixed-point report"] == (
        "python scripts/e4_parity/regen.py fixed-point "
        "--expected-points 1000 --expected-target-claims 10 "
        "--expected-non-target-claims 8 "
        "--json artifacts/conformance/e4_regen_fixed_point_${{ matrix.pass }}.json"
    )
    upload = next(
        step
        for step in clean_job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    )
    assert upload["with"] == {
        "name": "e4-fixed-point-${{ matrix.pass }}",
        "path": "artifacts/conformance/e4_regen_fixed_point_${{ matrix.pass }}.json",
        "if-no-files-found": "error",
    }
    assert compare_job["needs"] == "fixed-point-clean-checkout"
    compare_commands = {
        step["name"]: step["run"]
        for step in compare_job["steps"]
        if isinstance(step, dict) and "name" in step and "run" in step
    }
    assert compare_commands["Compare clean-checkout reports byte-for-byte"] == (
        "cmp artifacts/first/e4_regen_fixed_point_first.json "
        "artifacts/second/e4_regen_fixed_point_second.json"
    )


def test_phase16_score_authority_matches_inventory() -> None:
    assert front_door.final_readiness.score_authority() == {
        "source_ref": "docs_tmp/phase_16/BB_ER_PROGRESS.json#totals",
        "source_sha256": front_door._sha256_path(
            front_door.final_readiness.SCORE_AUTHORITY_PATH
        ),
        "source_observed": {
            "points_total": 1000,
            "points_done": 1000,
            "points_blocked": 0,
        },
        "expected": {
            "points": 1000,
            "target_claims": 10,
            "non_target_claims": 8,
        },
        "observed": {
            "points": 1000,
            "target_claims": 10,
            "non_target_claims": 8,
        },
        "ok": True,
    }


def test_score_authority_rejects_source_total_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_path = tmp_path / "BB_ER_PROGRESS.json"
    authority_path.write_text(
        json.dumps(
            {
                "totals": {
                    "points_total": 1000,
                    "points_done": 999,
                    "points_blocked": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        front_door.final_readiness, "SCORE_AUTHORITY_PATH", authority_path
    )

    authority = front_door.final_readiness.score_authority()

    assert authority["source_observed"] == {
        "points_total": 1000,
        "points_done": 999,
        "points_blocked": 1,
    }
    assert authority["ok"] is False


def test_explain_json_emits_schema_valid_plan_from_regenerate_evidence_stages(
    tmp_path: Path,
) -> None:
    """The front door's JSON explain output is the registered regen-plan contract for the canonical stage graph."""
    out_path = tmp_path / "regen_plan.json"

    assert front_door.main(["--python", "PY", "explain", "--json", str(out_path)]) == 0

    payload = _load_json(out_path)
    errors = _schema_errors(_regen_plan_validator(), payload)
    assert errors == [], "\n".join(_format_error(error) for error in errors)
    stages = payload["stages"]

    assert payload["schema_version"] == "bb.e4.regen_plan.v1"
    assert payload["ok"] is True
    assert payload["stage_count"] == len(front_door.regen.STAGES)
    assert (
        payload["explicit_stage_count"] + payload["generated_stage_count"]
        == payload["stage_count"]
    )
    assert [stage["stage_id"] for stage in stages] == [
        stage.stage_id for stage in front_door.regen.STAGES
    ]
    assert all(
        "reads" in stage and "writes" in stage and "read_only" in stage
        for stage in stages
    )
    assert any(stage["read_only"] is True and stage["writes"] == [] for stage in stages)
    assert any(stage["stage_id"].startswith("lane_def_reverify_") for stage in stages)
    assert {
        "stage_ids_unique",
        "dependencies_precede_consumers",
        "non_read_only_stages_declare_writes",
        "read_only_stages_declare_no_writes",
    }.issubset(set(payload["invariants"]))


def test_explain_json_delegates_to_plan_dict_with_regenerate_evidence_stages(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """A JSON explain call must ask regenerate_evidence.plan_dict to render regen.STAGES, not keep a second DAG."""
    sentinel_stages = (
        front_door.regen.Stage(
            stage_id="front_door_probe",
            phase="probe",
            label="front door probe",
            argv=(front_door.regen.PYTHON, "probe.py"),
            reads=("probe.py",),
            writes=("probe.json",),
        ),
    )
    calls: dict[str, Any] = {}

    def fake_validate_stage_graph(stages: tuple[front_door.regen.Stage, ...]) -> None:
        calls["validated_stages"] = stages

    def fake_plan_dict(
        stages: tuple[front_door.regen.Stage, ...], *, python: str
    ) -> dict[str, Any]:
        calls["planned_stages"] = stages
        calls["python"] = python
        return {
            "schema_version": "bb.e4.regen_plan.v1",
            "plan_id": "front_door_probe_plan",
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "explicit_stage_count": 1,
            "generated_stage_count": 0,
            "stage_count": 1,
            "invariants": ["front_door_uses_plan_dict"],
            "stages": [
                {
                    "stage_id": "front_door_probe",
                    "phase": "probe",
                    "depends_on": [],
                    "reads": ["probe.py"],
                    "writes": ["probe.json"],
                    "read_only": False,
                    "argv": [python, "probe.py"],
                }
            ],
        }

    monkeypatch.setattr(front_door.regen, "STAGES", sentinel_stages)
    monkeypatch.setattr(
        front_door.regen, "validate_stage_graph", fake_validate_stage_graph
    )
    monkeypatch.setattr(front_door.regen, "plan_dict", fake_plan_dict)

    assert front_door.main(["--python", "PY", "explain", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert calls == {
        "validated_stages": sentinel_stages,
        "planned_stages": sentinel_stages,
        "python": "PY",
    }
    assert payload["ok"] is True
    assert payload["stages"] == [
        {
            "argv": ["PY", "probe.py"],
            "depends_on": [],
            "phase": "probe",
            "read_only": False,
            "reads": ["probe.py"],
            "stage_id": "front_door_probe",
            "writes": ["probe.json"],
        }
    ]


def test_human_explain_delegates_to_print_explain_without_running_pipeline(
    monkeypatch: Any,
) -> None:
    """Human explain is read-only: it validates and prints the regenerate_evidence plan without running stages."""
    sentinel_stages = (
        front_door.regen.Stage(
            stage_id="front_door_human_probe",
            phase="probe",
            label="front door human probe",
            argv=(front_door.regen.PYTHON, "probe.py"),
            reads=("probe.py",),
            writes=("probe.json",),
        ),
    )
    calls: dict[str, Any] = {}

    def fake_validate_stage_graph(stages: tuple[front_door.regen.Stage, ...]) -> None:
        calls["validated_stages"] = stages

    def fake_print_explain(
        stages: tuple[front_door.regen.Stage, ...], *, python: str
    ) -> None:
        calls["printed_stages"] = stages
        calls["python"] = python

    def forbidden_run_pipeline(*_args: Any, **_kwargs: Any) -> tuple[int, list[Any]]:
        raise AssertionError("explain must not execute the regeneration DAG")

    monkeypatch.setattr(front_door.regen, "STAGES", sentinel_stages)
    monkeypatch.setattr(
        front_door.regen, "validate_stage_graph", fake_validate_stage_graph
    )
    monkeypatch.setattr(front_door.regen, "print_explain", fake_print_explain)
    monkeypatch.setattr(front_door.regen, "run_pipeline", forbidden_run_pipeline)

    assert front_door.main(["--python", "PY", "explain"]) == 0

    assert calls == {
        "validated_stages": sentinel_stages,
        "printed_stages": sentinel_stages,
        "python": "PY",
    }


def test_run_json_delegates_to_run_pipeline_and_serializes_stage_results(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """The run subcommand emits regenerate_evidence.run_pipeline results instead of executing its own DAG."""
    sentinel_stages = (
        front_door.regen.Stage(
            stage_id="front_door_run_probe",
            phase="probe",
            label="front door run probe",
            argv=(front_door.regen.PYTHON, "probe.py"),
            reads=("probe.py",),
            writes=("probe.json",),
        ),
    )
    result = front_door.regen.StageResult(
        stage_id="front_door_run_probe",
        argv=["PY", "probe.py"],
        returncode=7,
        stdout="probe stdout\n",
        stderr="probe stderr\n",
        duration_seconds=0.25,
    )
    calls: dict[str, Any] = {}

    def fake_run_pipeline(
        stages: tuple[front_door.regen.Stage, ...], *, python: str
    ) -> tuple[int, list[front_door.regen.StageResult]]:
        calls["stages"] = stages
        calls["python"] = python
        return 7, [result]

    monkeypatch.setattr(front_door.regen, "STAGES", sentinel_stages)
    monkeypatch.setattr(front_door.regen, "run_pipeline", fake_run_pipeline)
    out_path = tmp_path / "run_summary.json"

    assert front_door.main(["--python", "PY", "run", "--json", str(out_path)]) == 7

    assert calls == {"stages": sentinel_stages, "python": "PY"}
    payload = _load_json(out_path)
    assert payload == {
        "schema_version": "bb.e4.regen_run.v1",
        "ok": False,
        "exit_code": 7,
        "completed_stage_count": 1,
        "total_duration_seconds": 0.25,
        "results": [
            {
                "stage_id": "front_door_run_probe",
                "argv": ["PY", "probe.py"],
                "returncode": 7,
                "stdout": "probe stdout\n",
                "stderr": "probe stderr\n",
                "duration_seconds": 0.25,
            }
        ],
    }


def test_fixed_point_runs_pipeline_twice_and_ignores_undeclared_writes(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Fixed-point equality is computed from declared stage writes, not unrelated files produced nearby."""
    sentinel_stages = (
        front_door.regen.Stage(
            stage_id="front_door_fixed_point_probe",
            phase="probe",
            label="front door fixed-point probe",
            argv=(front_door.regen.PYTHON, "probe.py"),
            reads=("probe.py",),
            writes=("watched.txt",),
        ),
    )
    watched_path = tmp_path / "watched.txt"
    undeclared_path = tmp_path / "undeclared.txt"
    calls: list[tuple[tuple[front_door.regen.Stage, ...], str]] = []

    def fake_run_pipeline(
        stages: tuple[front_door.regen.Stage, ...],
        *,
        python: str,
    ) -> tuple[int, list[front_door.regen.StageResult]]:
        calls.append((stages, python))
        watched_path.write_text("stable declared output\n", encoding="utf-8")
        undeclared_path.write_text(
            f"churn outside watch set {len(calls)}\n", encoding="utf-8"
        )
        return 0, []

    out_path = tmp_path / "fixed_point.json"
    monkeypatch.setattr(front_door, "ROOT", tmp_path)
    monkeypatch.setattr(front_door.regen, "STAGES", sentinel_stages)
    monkeypatch.setattr(front_door.regen, "run_pipeline", fake_run_pipeline)

    assert (
        front_door.main(["--python", "PY", "fixed-point", "--json", str(out_path)]) == 0
    )

    assert calls == [(sentinel_stages, "PY"), (sentinel_stages, "PY")]
    payload = _load_json(out_path)
    errors = _schema_errors(_fixed_point_validator(), payload)
    assert errors == [], "\n".join(_format_error(error) for error in errors)
    assert payload["schema_version"] == "bb.e4.fixed_point_report.v1"
    assert payload["watch_set_size"] == 2  # declared output plus score authority
    assert payload["byte_identical"] is True
    assert payload["changed_paths"] == []
    assert (
        payload["first_pass_snapshot_sha256"] == payload["second_pass_snapshot_sha256"]
    )


def test_watch_set_includes_generated_self_runtime_record_tree(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Fixed-point must recurse directory writes instead of omitting their generated files."""
    self_runtime_write = (
        "docs/conformance/e4_target_support/breadboard_self_runtime_records_v1"
    )
    north_star_write = (
        "docs/conformance/e4_target_support/claude_code_north_star_capture_v1"
    )

    target_support = tmp_path / "docs/conformance/e4_target_support"
    runtime_root = target_support / "breadboard_self_runtime_records_v1"
    replay_result = runtime_root / "bb_replay_result.json"
    nested_record = runtime_root / "sessions/session_001/runtime_record.json"
    north_star_record = tmp_path / north_star_write / "records/runtime_record.json"
    for path in (replay_result, nested_record, north_star_record):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(front_door, "ROOT", tmp_path)

    watched = set(front_door._watch_set())
    assert {
        replay_result.resolve(),
        nested_record.resolve(),
        north_star_record.resolve(),
    } <= watched


def test_p6_6_producer_declares_full_lane_root_for_fixed_point_watch_set(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Every P6.6 lane output must participate in fixed-point drift detection."""
    lane_write = "docs/conformance/e4_target_support/oh_my_pi_p6_6_task_job_subagent_v2"

    lane_root = tmp_path / lane_write
    lane_outputs = tuple(lane_root / name for name in ("work_items.json", "prevalidation_report.json"))
    for path in lane_outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(front_door, "ROOT", tmp_path)

    watched = set(front_door._watch_set())
    assert {path.resolve() for path in lane_outputs} <= watched


def test_fixed_point_reports_declared_write_drift_between_passes(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """A byte change in a declared write must fail fixed-point and name the changed path."""
    sentinel_stages = (
        front_door.regen.Stage(
            stage_id="front_door_fixed_point_drift_probe",
            phase="probe",
            label="front door fixed-point drift probe",
            argv=(front_door.regen.PYTHON, "probe.py"),
            reads=("probe.py",),
            writes=("watched.txt",),
        ),
    )
    watched_path = tmp_path / "watched.txt"
    calls: list[int] = []

    def fake_run_pipeline(
        stages: tuple[front_door.regen.Stage, ...],
        *,
        python: str,
    ) -> tuple[int, list[front_door.regen.StageResult]]:
        assert stages == sentinel_stages
        assert python == "PY"
        calls.append(len(calls) + 1)
        watched_path.write_text(f"declared output pass {calls[-1]}\n", encoding="utf-8")
        return 0, []

    out_path = tmp_path / "fixed_point_drift.json"
    monkeypatch.setattr(front_door, "ROOT", tmp_path)
    monkeypatch.setattr(front_door.regen, "STAGES", sentinel_stages)
    monkeypatch.setattr(front_door.regen, "run_pipeline", fake_run_pipeline)

    assert (
        front_door.main(["--python", "PY", "fixed-point", "--json", str(out_path)]) == 1
    )

    assert calls == [1, 2]
    payload = _load_json(out_path)
    errors = _schema_errors(_fixed_point_validator(), payload)
    assert errors == [], "\n".join(_format_error(error) for error in errors)
    assert payload["watch_set_size"] == 2  # declared output plus score authority
    assert payload["byte_identical"] is False
    assert payload["changed_paths"] == ["watched.txt"]
    assert (
        payload["first_pass_snapshot_sha256"] != payload["second_pass_snapshot_sha256"]
    )


@pytest.mark.parametrize(
    ("name", "log_payload", "expected"),
    [
        (
            "pin marker outranks semantic and io text",
            {
                "stderr": "[SEMANTIC] mismatch\n[PIN_STALE] pinned evidence is stale\nNo such file or directory"
            },
            "pin_stale",
        ),
        (
            "semantic marker outranks comparator text",
            {
                "stderr": "AssertionError from comparator\n[SEMANTIC] tool output changed meaning"
            },
            "semantic",
        ),
        (
            "missing file text",
            {"error": "OSError: [Errno 2] No such file or directory: 'artifact.json'"},
            "io_missing",
        ),
        (
            "schema invalid text",
            {
                "stdout": "jsonschema validation failed against bb.e4.artifact_catalog.v2"
            },
            "schema_invalid",
        ),
        (
            "graph invariant text",
            {"error": "validate_stage_graph: duplicate stage id lane_capture"},
            "graph_invariant",
        ),
        (
            "nested comparator failure text",
            {
                "results": [
                    {"stderr": "comparator reported AssertionError for replay rows"}
                ]
            },
            "comparator_failure",
        ),
        (
            "unmatched drift text",
            {"stderr": "unexpected byte drift without a known marker"},
            "drift_unexplained",
        ),
    ],
)
def test_classify_log_maps_failure_text_to_ordered_failure_class_enum(
    name: str,
    log_payload: dict[str, Any],
    expected: str,
    tmp_path: Path,
) -> None:
    """Classifier output is the public enum contract for known regen failure markers and text."""
    log_path = tmp_path / f"{name.replace(' ', '_')}.json"
    out_path = tmp_path / f"{name.replace(' ', '_')}_classification.json"
    log_path.write_text(json.dumps(log_payload), encoding="utf-8")

    assert (
        front_door.main(["classify", "--log", str(log_path), "--json", str(out_path)])
        == 0
    )

    payload = _load_json(out_path)
    assert payload["schema_version"] == "bb.e4.regen_failure_classification.v1"
    assert payload["ok"] is True
    assert payload["failure_class"] == expected
    assert payload["failure_classes"] == list(front_door.FAILURE_CLASSES)
    assert payload["log"] == str(log_path)


def test_classify_log_keeps_error_bearing_stderr_tail_visible(tmp_path: Path) -> None:
    """Classification evidence must show the stderr marker that explains the selected class."""
    log_path = tmp_path / "classification_with_long_stdout.json"
    out_path = tmp_path / "classification_with_long_stdout.out.json"
    log_path.write_text(
        json.dumps(
            {
                "stdout_tail": "x" * 6000,
                "stderr_tail": "FileNotFoundError: registered artifact does not exist for role_id",
            }
        ),
        encoding="utf-8",
    )

    assert (
        front_door.main(["classify", "--log", str(log_path), "--json", str(out_path)])
        == 0
    )

    payload = _load_json(out_path)
    assert payload["failure_class"] == "io_missing"
    assert "FileNotFoundError" in payload["stderr_tail"]
    assert "FileNotFoundError" in payload["raw_stderr_tail"]


def test_regen_front_door_schemas_are_registered_in_kernel_e4_pack() -> None:
    """Front-door schemas are discoverable through the checked-in kernel pack manifest."""
    packs = _load_json(PACKS_PATH)
    e4_pack = next(pack for pack in packs["entries"] if pack["id"] == "e4")

    regen_plan_schema = _load_json(REGEN_PLAN_SCHEMA_PATH)
    fixed_point_schema = _load_json(FIXED_POINT_SCHEMA_PATH)
    Draft202012Validator.check_schema(regen_plan_schema)
    Draft202012Validator.check_schema(fixed_point_schema)
    assert regen_plan_schema["properties"]["schema_version"] == {
        "const": "bb.e4.regen_plan.v1"
    }
    assert fixed_point_schema["properties"]["schema_version"] == {
        "const": "bb.e4.fixed_point_report.v1"
    }
    assert "bb.e4.regen_plan.v1.schema.json" in e4_pack["metadata"]["schemas"]
    assert "bb.e4.fixed_point_report.v1.schema.json" in e4_pack["metadata"]["schemas"]
