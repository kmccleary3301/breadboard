from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from breadboard_engine.security import WorkspacePathError

from breadboard_engine.artifact_tasks import (
    ArtifactContract,
    ArtifactRequirement,
    ArtifactTaskSpec,
    CampaignCandidateSpec,
    CampaignSpec,
    EvaluatorSpec,
    MaterializationSpec,
    WorkspaceBridgeSpec,
    artifact_task_result_to_candidate_packet,
    artifact_task_result_to_optimize_evaluation_record,
    artifact_task_result_to_rl_episode,
    hash_file,
    materialize_response_artifact,
    prepare_workspace_bridge,
    run_artifact_task,
    run_campaign,
    run_evaluator,
    safe_relative_path,
    validate_artifact_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def test_safe_relative_path_rejects_absolute_and_traversal() -> None:
    with pytest.raises(ValueError, match="relative"):
        safe_relative_path("/tmp/evil.py")
    with pytest.raises(ValueError, match="parent traversal"):
        safe_relative_path("../evil.py")


def test_artifact_contract_validates_missing_undersized_hash_and_success(
    tmp_path: Path,
) -> None:
    contract = ArtifactContract(
        requirements=[ArtifactRequirement(path="candidate.py", min_bytes=4)]
    )
    missing = validate_artifact_contract(contract, root=tmp_path)
    assert not missing.ok
    assert "candidate.py:missing_required_artifact" in missing.failure_reasons

    target = tmp_path / "candidate.py"
    target.write_text("x\n", encoding="utf-8")
    undersized = validate_artifact_contract(contract, root=tmp_path)
    assert not undersized.ok
    assert "artifact_below_min_bytes" in undersized.checks[0].failure_reasons

    wrong_hash = ArtifactContract(
        requirements=[ArtifactRequirement(path="candidate.py", sha256="0" * 64)]
    )
    mismatch = validate_artifact_contract(wrong_hash, root=tmp_path)
    assert not mismatch.ok
    assert "artifact_sha256_mismatch" in mismatch.checks[0].failure_reasons

    digest = hash_file(target)
    valid = validate_artifact_contract(
        ArtifactContract(
            requirements=[
                ArtifactRequirement(path="candidate.py", min_bytes=1, sha256=digest)
            ]
        ),
        root=tmp_path,
    )
    assert valid.ok
    assert valid.checks[0].sha256 == digest


def test_artifact_contract_rejects_duplicate_and_unsafe_paths() -> None:
    with pytest.raises(ValueError, match="unique"):
        ArtifactContract(
            requirements=[
                ArtifactRequirement(path="candidate.py"),
                ArtifactRequirement(path="candidate.py"),
            ]
        )
    with pytest.raises(ValueError, match="relative"):
        ArtifactRequirement(path="/abs/candidate.py")
    with pytest.raises(ValueError, match="parent traversal"):
        ArtifactRequirement(path="../candidate.py")


def test_materialize_response_artifact_success_and_failure_modes(
    tmp_path: Path,
) -> None:
    spec = MaterializationSpec(language="python", output_path="candidate.py")
    missing = materialize_response_artifact("done", spec, root=tmp_path)
    assert not missing.ok
    assert missing.failure_reasons == ("missing_fenced_block",)

    wrong_language = materialize_response_artifact(
        "```javascript\n1\n```", spec, root=tmp_path
    )
    assert not wrong_language.ok
    assert wrong_language.failure_reasons == ("missing_fenced_block",)

    multiple = materialize_response_artifact(
        "```python\n1\n```\n```python\n2\n```", spec, root=tmp_path
    )
    assert not multiple.ok
    assert multiple.failure_reasons == ("multiple_fenced_blocks",)

    ok = materialize_response_artifact(
        "```python\nprint('ok')\n```", spec, root=tmp_path
    )
    assert ok.ok
    assert (tmp_path / "candidate.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert ok.sha256 == hash_file(tmp_path / "candidate.py")


def test_text_only_response_claiming_success_fails_artifact_task(
    tmp_path: Path,
) -> None:
    result = run_artifact_task(
        ArtifactTaskSpec(
            task_id="task",
            candidate_id="cand_text_only",
            task_text="make a file",
            response_text="Done. The candidate is ready.",
            artifact_contract=ArtifactContract(
                requirements=[ArtifactRequirement(path="candidate.py", min_bytes=1)]
            ),
            materialization=MaterializationSpec(
                language="python", output_path="candidate.py"
            ),
            out_dir=str(tmp_path / "out"),
        )
    )
    assert not result.ok
    assert result.status == "failed"
    assert any(
        reason.startswith("materialization:missing_fenced_block")
        for reason in result.failure_reasons
    )
    assert Path(result.evidence_manifest.manifest_path).exists()


def test_artifact_task_success_bundle_contains_manifest_artifact_and_hashes(
    tmp_path: Path,
) -> None:
    result = run_artifact_task(
        ArtifactTaskSpec(
            task_id="task",
            candidate_id="cand_success",
            task_text="make a file",
            response_text="```python\nprint('ok')\n```",
            artifact_contract=ArtifactContract(
                requirements=[ArtifactRequirement(path="candidate.py", min_bytes=1)]
            ),
            materialization=MaterializationSpec(
                language="python", output_path="candidate.py"
            ),
            evaluators=[
                EvaluatorSpec(
                    name="syntax",
                    command=[sys.executable, "-m", "py_compile", "candidate.py"],
                )
            ],
            out_dir=str(tmp_path / "out"),
        )
    )
    assert result.ok
    manifest_path = Path(result.evidence_manifest.manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "passed"
    assert (manifest_path.parent / "inputs" / "task.md").exists()
    assert (manifest_path.parent / "responses" / "raw_response.md").exists()
    assert (manifest_path.parent / "artifacts" / "candidate.py").exists()
    assert (manifest_path.parent / "artifacts" / "artifact_manifest.json").exists()
    assert (manifest_path.parent / "evaluators" / "syntax" / "result.json").exists()
    assert (manifest_path.parent / "hashes" / "sha256_manifest.json").exists()


def test_evidence_bundle_symlink_is_rejected_without_deleting_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "candidate.py").write_text("print('ok')\n", encoding="utf-8")
    protected = tmp_path / "protected"
    protected.mkdir()
    canary = protected / "credential-canary"
    canary.write_text("must-survive", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "evidence_bundle").symlink_to(
        protected,
        target_is_directory=True,
    )

    with pytest.raises(WorkspacePathError):
        run_artifact_task(
            ArtifactTaskSpec(
                task_id="task",
                candidate_id="cand_symlink",
                task_text="inspect candidate",
                response_text="done",
                artifact_contract=ArtifactContract(
                    mode="file_edit",
                    requirements=[
                        ArtifactRequirement(
                            path="candidate.py",
                            min_bytes=1,
                        )
                    ],
                ),
                workspace_root=str(workspace),
                out_dir=str(out_dir),
            )
        )

    assert canary.read_text(encoding="utf-8") == "must-survive"


def test_artifact_task_rejects_output_inside_model_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "candidate.py").write_text("print('ok')\n", encoding="utf-8")

    with pytest.raises(
        WorkspacePathError,
        match="output_path_overlaps_trusted_boundary",
    ):
        run_artifact_task(
            ArtifactTaskSpec(
                task_id="task",
                candidate_id="cand_overlap",
                task_text="inspect candidate",
                response_text="done",
                artifact_contract=ArtifactContract(
                    requirements=[ArtifactRequirement(path="candidate.py")]
                ),
                workspace_root=str(workspace),
                out_dir=str(workspace / "evidence"),
            )
        )


def test_evaluator_nonzero_failure_and_timeout_are_structured(tmp_path: Path) -> None:
    fail = run_evaluator(
        EvaluatorSpec(
            name="fail", command=[sys.executable, "-c", "import sys; sys.exit(7)"]
        ),
        root=tmp_path,
        output_dir=tmp_path / "fail_out",
    )
    assert fail.status == "failed"
    assert fail.exit_code == 7
    assert "nonzero_exit" in fail.failure_reasons

    timeout = run_evaluator(
        EvaluatorSpec(
            name="timeout",
            command=[sys.executable, "-c", "import time; time.sleep(1)"],
            timeout_seconds=0.01,
        ),
        root=tmp_path,
        output_dir=tmp_path / "timeout_out",
    )
    assert timeout.status == "timeout"
    assert "timeout" in timeout.failure_reasons


def test_evaluator_strips_provider_credentials_from_child_and_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    canary = "evaluator-environment-canary-42ba"
    monkeypatch.setenv("OPENAI_API_KEY", canary)
    marker = tmp_path / "must-not-run"
    output_dir = tmp_path / "env_out"
    spec = EvaluatorSpec(
        name="environment",
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('must-not-run').write_text('ran')",
        ],
        env={"CI": canary},
    )

    result = run_evaluator(
        spec,
        root=tmp_path,
        output_dir=output_dir,
    )

    assert result.status == "error"
    assert result.failure_reasons == ("credential_in_environment",)
    assert not marker.exists()
    assert canary not in json.dumps(spec.to_dict())
    assert canary not in json.dumps(result.to_dict())
    for path in output_dir.rglob("*"):
        if path.is_file():
            assert canary not in path.read_text(
                encoding="utf-8",
                errors="replace",
            )


def test_evaluator_rejects_provider_credential_in_argv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    canary = "evaluator-argv-canary-7c19"
    monkeypatch.setenv("OPENAI_API_KEY", canary)
    marker = tmp_path / "must-not-run"
    output_dir = tmp_path / "argv_out"
    spec = EvaluatorSpec(
        name="argv",
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('must-not-run').write_text('ran')",
            canary,
        ],
    )

    result = run_evaluator(
        spec,
        root=tmp_path,
        output_dir=output_dir,
    )

    assert result.status == "error"
    assert result.failure_reasons == ("credential_in_argv",)
    assert not marker.exists()
    assert canary not in json.dumps(spec.to_dict())
    assert canary not in json.dumps(result.to_dict())
    for path in output_dir.rglob("*"):
        if path.is_file():
            assert canary not in path.read_text(
                encoding="utf-8",
                errors="replace",
            )


def test_artifact_task_captures_required_evaluator_failure(tmp_path: Path) -> None:
    result = run_artifact_task(
        ArtifactTaskSpec(
            task_id="task",
            candidate_id="cand_eval_fail",
            task_text="make a file",
            response_text="```python\nprint('ok')\n```",
            artifact_contract=ArtifactContract(
                requirements=[ArtifactRequirement(path="candidate.py", min_bytes=1)]
            ),
            materialization=MaterializationSpec(
                language="python", output_path="candidate.py"
            ),
            evaluators=[
                EvaluatorSpec(
                    name="fail",
                    command=[sys.executable, "-c", "import sys; sys.exit(3)"],
                )
            ],
            out_dir=str(tmp_path / "out"),
        )
    )
    assert not result.ok
    assert any(
        reason.startswith("evaluator:fail:nonzero_exit")
        for reason in result.failure_reasons
    )
    assert (
        Path(result.evidence_manifest.manifest_path).parent
        / "evaluators"
        / "fail"
        / "stderr.txt"
    ).exists()


def test_workspace_bridge_import_export_and_protected_rejection(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "README.md").write_text("template", encoding="utf-8")
    executable = template / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    workspace = tmp_path / "workspace"
    export = tmp_path / "export"
    result = prepare_workspace_bridge(
        WorkspaceBridgeSpec(
            workspace_template=str(template),
            disposable_workspace=str(workspace),
            export_dir=str(export),
        )
    )
    assert result.copied
    assert (workspace / "README.md").read_text(encoding="utf-8") == "template"
    assert (workspace / "run.sh").stat().st_mode & 0o111
    assert export.exists()
    (workspace / "stale.txt").write_text("stale", encoding="utf-8")
    replaced = prepare_workspace_bridge(
        WorkspaceBridgeSpec(
            workspace_template=str(template),
            disposable_workspace=str(workspace),
            overwrite_disposable=True,
        )
    )
    assert replaced.copied
    assert not (workspace / "stale.txt").exists()

    with pytest.raises(ValueError, match="protected root|too broad"):
        prepare_workspace_bridge(
            WorkspaceBridgeSpec(workspace_template=None, disposable_workspace="/tmp")
        )


def test_workspace_bridge_refuses_to_overwrite_unowned_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "unowned"
    workspace.mkdir()
    sentinel = workspace / "preserve.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="unowned workspace"):
        prepare_workspace_bridge(
            WorkspaceBridgeSpec(
                workspace_template=None,
                disposable_workspace=str(workspace),
                overwrite_disposable=True,
            )
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_workspace_bridge_rejects_registered_credential_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    template = tmp_path / "protected-template"
    template.mkdir()
    credential = template / "credentials.sqlite3"
    credential.write_text("credential-canary", encoding="utf-8")
    workspace = tmp_path / "workspace"
    monkeypatch.setenv(
        "BREADBOARD_CREDENTIAL_STORE_PATH",
        str(credential),
    )

    with pytest.raises(
        ValueError,
        match="overlaps a protected credential path",
    ):
        prepare_workspace_bridge(
            WorkspaceBridgeSpec(
                workspace_template=str(template),
                disposable_workspace=str(workspace),
            )
        )

    assert not workspace.exists()


def test_workspace_bridge_rejects_template_links_without_copying_secret(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "credential.sqlite3"
    secret.write_text("credential-canary", encoding="utf-8")
    template = tmp_path / "template"
    template.mkdir()

    symlink = template / "credential-link"
    symlink.symlink_to(secret)
    workspace = tmp_path / "workspace-symlink"
    with pytest.raises(ValueError, match="workspace_symlink_rejected"):
        prepare_workspace_bridge(
            WorkspaceBridgeSpec(
                workspace_template=str(template),
                disposable_workspace=str(workspace),
            )
        )
    assert not workspace.exists()
    assert secret.read_text(encoding="utf-8") == "credential-canary"

    symlink.unlink()
    hardlink = template / "credential-hardlink"
    hardlink.hardlink_to(secret)
    workspace = tmp_path / "workspace-hardlink"
    with pytest.raises(
        ValueError,
        match="workspace_regular_single_link_required",
    ):
        prepare_workspace_bridge(
            WorkspaceBridgeSpec(
                workspace_template=str(template),
                disposable_workspace=str(workspace),
            )
        )
    assert not workspace.exists()
    assert secret.read_text(encoding="utf-8") == "credential-canary"


def test_workspace_bridge_rejects_overlapping_roots(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    with pytest.raises(ValueError, match="must not overlap"):
        prepare_workspace_bridge(
            WorkspaceBridgeSpec(
                workspace_template=str(template),
                disposable_workspace=str(template / "workspace"),
            )
        )


def test_campaign_runner_creates_ledger_and_resumes_completed_candidate(
    tmp_path: Path,
) -> None:
    spec = CampaignSpec(
        campaign_id="campaign",
        task_id="task",
        out_dir=str(tmp_path / "campaign"),
        artifact_contract=ArtifactContract(
            requirements=[ArtifactRequirement(path="candidate.py", min_bytes=1)]
        ).to_dict(),
        materialization=MaterializationSpec(
            language="python", output_path="candidate.py"
        ).to_dict(),
        candidates=[
            CampaignCandidateSpec(
                candidate_id="cand_1",
                task_text="task",
                response_text="```python\nprint(1)\n```",
            ),
        ],
    )
    first = run_campaign(spec)
    assert first.status == "passed"
    assert Path(first.ledger_path).exists()
    second = run_campaign(spec)
    assert second.skipped_count >= 1
    assert any(
        row["campaign_action"] == "skipped_existing_passed" for row in second.results
    )


def test_campaign_runner_bounded_parallel_candidates(tmp_path: Path) -> None:
    spec = CampaignSpec(
        campaign_id="campaign_parallel",
        task_id="task",
        out_dir=str(tmp_path / "campaign_parallel"),
        artifact_contract=ArtifactContract(
            requirements=[ArtifactRequirement(path="candidate.py", min_bytes=1)]
        ).to_dict(),
        materialization=MaterializationSpec(
            language="python", output_path="candidate.py"
        ).to_dict(),
        candidates=[
            CampaignCandidateSpec(
                candidate_id="cand_1",
                task_text="task",
                response_text="```python\nprint(1)\n```",
            ),
            CampaignCandidateSpec(
                candidate_id="cand_2",
                task_text="task",
                response_text="```python\nprint(2)\n```",
            ),
        ],
        max_parallel=2,
    )
    summary = run_campaign(spec)
    assert summary.status == "passed"
    assert summary.passed_count == 2
    assert (
        Path(summary.out_dir) / "cand_1" / "evidence_bundle" / "manifest.json"
    ).exists()
    assert (
        Path(summary.out_dir) / "cand_2" / "evidence_bundle" / "manifest.json"
    ).exists()


def test_downstream_adapters_emit_candidate_optimize_and_rl_records(
    tmp_path: Path,
) -> None:
    result = run_artifact_task(
        ArtifactTaskSpec(
            task_id="task",
            candidate_id="cand_adapter",
            task_text="make a file",
            response_text="```python\nprint('ok')\n```",
            artifact_contract=ArtifactContract(
                requirements=[ArtifactRequirement(path="candidate.py", min_bytes=1)]
            ),
            materialization=MaterializationSpec(
                language="python", output_path="candidate.py"
            ),
            out_dir=str(tmp_path / "out"),
        )
    )
    packet = artifact_task_result_to_candidate_packet(result)
    assert packet["packet_kind"] == "artifact_task_candidate.v1"
    evaluation = artifact_task_result_to_optimize_evaluation_record(result)
    assert evaluation.candidate_id == "cand_adapter"
    assert evaluation.aggregate_outcome() == "passed"
    episode = artifact_task_result_to_rl_episode(result)
    assert episode.run_id.endswith("cand_adapter")
    assert episode.steps[0].reward == 1.0


def test_operator_scripts_emit_json(tmp_path: Path) -> None:
    smoke = subprocess.run(
        [
            sys.executable,
            "scripts/dev/artifact_task_smoke.py",
            "--out-dir",
            str(tmp_path / "smoke"),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert smoke.returncode == 0, smoke.stderr
    assert json.loads(smoke.stdout)["status"] == "passed"

    explain = subprocess.run(
        [sys.executable, "scripts/dev/config_explain.py", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert explain.returncode == 0, explain.stderr
    assert (
        json.loads(explain.stdout)["preset_id"]
        == "artifact_single_response_materialize_v1"
    )


def test_doctor_reports_artifact_task_checks() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/dev/first_time_doctor.py",
            "--profile",
            "engine",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    names = {row["name"] for row in payload["checks"]}
    assert "artifact_tasks_package_present" in names
    assert "artifact_task_smoke_present" in names
    assert "config_explain_present" in names
