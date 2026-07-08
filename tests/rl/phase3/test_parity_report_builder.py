from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.phase3.parity import PHASE3_PARITY_CLAIM_BOUNDARY, PHASE3_PARITY_REPORT_ID, PHASE3_PARITY_SCHEMA, build_phase3_parity_report, validate_phase3_parity_report
from scripts.rl_phase3.build_phase3_parity_report import MILESTONE_FILES, _attach_parity, _runtime_evidence


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def test_update_milestones_attaches_same_parity_metadata(tmp_path: Path) -> None:
    evidence_root = tmp_path / "docs_tmp"
    reports_dir = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "milestone_reports"
    parity_path = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "parity" / "phase3_parity_report.json"
    _write_json(parity_path, {"schema_version": "bb.rl.phase3.parity_report.v1"})
    parity_sha = "sha256:" + "a" * 64

    for filename in (MILESTONE_FILES["P3-M2"], MILESTONE_FILES["P3-M3"], MILESTONE_FILES["P3-M4"]):
        _write_json(reports_dir / filename, {"artifact_paths": {}, "input_hashes": {}, "required_artifact_keys": []})
        _attach_parity(reports_dir / filename, parity_path=parity_path, parity_sha256=parity_sha, evidence_root=evidence_root)

    attached = [json.loads((reports_dir / filename).read_text()) for filename in (MILESTONE_FILES["P3-M2"], MILESTONE_FILES["P3-M3"], MILESTONE_FILES["P3-M4"])]
    parity_paths = {report["artifact_paths"]["parity_report"] for report in attached}
    parity_hashes = {report["input_hashes"]["parity_report"] for report in attached}
    parity_ids = {report["parity_report_id"] for report in attached}

    assert parity_paths == {"ZYPHRA/RL_PHASE_3/runs/parity/phase3_parity_report.json"}
    assert parity_hashes == {parity_sha}
    assert parity_ids == {"phase3_parity_report"}
    assert all("parity_report" in report["required_artifact_keys"] for report in attached)


def test_infrastructure_parity_rejects_same_basename_different_runtime_roots(tmp_path: Path) -> None:
    evidence_root = tmp_path / "docs_tmp"
    runtime_a = "/shared/runtime/phase3_vllm_verl_py312"
    runtime_b = "/alternate/runtime/phase3_vllm_verl_py312"
    command_log = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "command_logs" / "cmd.log"
    command_log.parent.mkdir(parents=True, exist_ok=True)
    command_log.write_text("Digest: sha256:" + "b" * 64 + "\n")

    closed_stdout = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "closed" / "trainer_stdout.log"
    closed_stdout.parent.mkdir(parents=True, exist_ok=True)
    closed_stdout.write_text("'tensor_model_parallel_size': 2\n'memory_limit_mb': 1024\n'default_local_dir': '/tmp/x'\n'storage_backend': 'SimpleStorage'\n")
    closed_stderr = closed_stdout.with_name("trainer_stderr.log")
    closed_stderr.write_text("Started a local Ray instance\n")
    reward = closed_stdout.with_name("reward.py")
    projection = closed_stdout.with_name("accepted_projection_rows.jsonl")
    manifest = closed_stdout.with_name("evidence_manifest.json")
    metrics = closed_stdout.with_name("metrics.json")
    for artifact in (reward, projection, metrics):
        artifact.write_text("{}\n")
    _write_json(manifest, {"checkpoint_dir": "/remote/checkpoint", "files": {"train.parquet": {"bytes": 1}}})

    base_report = {
        "report_id": "report",
        "trainer_backend": "verl_ppo",
        "rollout_name": "vllm",
        "model_ref": "Qwen/Qwen2.5-0.5B-Instruct",
        "n_gpus_per_node": 8,
        "device_count": 8,
        "optimizer_step_count": 1,
        "checkpoint_before_sha256": "sha256:" + "0" * 64,
        "checkpoint_after_sha256": "sha256:" + "1" * 64,
        "checkpoint_changed": True,
        "artifact_paths": {"target_command_log": "ZYPHRA/RL_PHASE_3/runs/command_logs/cmd.log"},
    }
    closed_loop = {
        **base_report,
        "accepted_count": 1,
        "quarantined_count": 0,
        "rejected_count": 0,
        "dataproto_ok": True,
        "artifact_paths": {
            "target_command_log": "ZYPHRA/RL_PHASE_3/runs/command_logs/cmd.log",
            "trainer_stdout": "ZYPHRA/RL_PHASE_3/runs/closed/trainer_stdout.log",
            "trainer_stderr": "ZYPHRA/RL_PHASE_3/runs/closed/trainer_stderr.log",
            "reward_function": "ZYPHRA/RL_PHASE_3/runs/closed/reward.py",
            "accepted_projection_rows": "ZYPHRA/RL_PHASE_3/runs/closed/accepted_projection_rows.jsonl",
            "evidence_manifest": "ZYPHRA/RL_PHASE_3/runs/closed/evidence_manifest.json",
            "metrics": "ZYPHRA/RL_PHASE_3/runs/closed/metrics.json",
        },
    }
    introspection = {
        "torch": {"version": "2.9.1+rocm6.4", "device_count": 8, "devices": ["AMD Instinct MI300X"] * 8},
        "symbols": {"verl.__version__": "0.8.0"},
    }

    report = build_phase3_parity_report(
        target_run_id="20260624T040000Z-slurm-243958",
        ppo_report=base_report,
        grpo_report=base_report,
        closed_loop_report=closed_loop,
        introspection_report=introspection,
        runtime_evidence={
            "container_image": "vllm/vllm-openai-rocm:nightly",
            "runtime_path": runtime_a,
            "runtime_install_runtime": runtime_b,
            "runtime_install_report_path": "/tmp/install.json",
            "runtime_install_passed": True,
            "vllm_version": "0.23.1",
        },
        evidence_root=evidence_root,
    )

    missing = report["checklist"]["C10_infrastructure_parity"]["missing"]
    assert "single_runtime_install_for_trainer_runtime" in missing
    assert report["passed"] is False



def test_infrastructure_parity_rejects_scratch_runtime_install_evidence(tmp_path: Path) -> None:
    evidence_root = tmp_path / "docs_tmp"
    command_log = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "command_logs" / "cmd.log"
    command_log.parent.mkdir(parents=True, exist_ok=True)
    command_log.write_text("Digest: sha256:" + "b" * 64 + "\n")

    closed_stdout = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "closed" / "trainer_stdout.log"
    closed_stdout.parent.mkdir(parents=True, exist_ok=True)
    closed_stdout.write_text("'tensor_model_parallel_size': 2\n'memory_limit_mb': 1024\n'default_local_dir': '/tmp/x'\n'storage_backend': 'SimpleStorage'\n")
    closed_stderr = closed_stdout.with_name("trainer_stderr.log")
    closed_stderr.write_text("Started a local Ray instance\n")
    for name in ("reward.py", "accepted_projection_rows.jsonl", "metrics.json"):
        closed_stdout.with_name(name).write_text("{}\n")
    _write_json(closed_stdout.with_name("evidence_manifest.json"), {"checkpoint_dir": "/tmp/x", "files": {"train.parquet": {"bytes": 1}}})

    base_report = {
        "report_id": "report",
        "trainer_backend": "verl_ppo",
        "rollout_name": "vllm",
        "model_ref": "Qwen/Qwen2.5-0.5B-Instruct",
        "n_gpus_per_node": 8,
        "device_count": 8,
        "optimizer_step_count": 1,
        "checkpoint_before_sha256": "sha256:" + "0" * 64,
        "checkpoint_after_sha256": "sha256:" + "1" * 64,
        "checkpoint_changed": True,
        "artifact_paths": {"target_command_log": "ZYPHRA/RL_PHASE_3/runs/command_logs/cmd.log"},
    }
    closed_loop = {
        **base_report,
        "accepted_count": 1,
        "quarantined_count": 0,
        "rejected_count": 0,
        "dataproto_ok": True,
        "artifact_paths": {
            "target_command_log": "ZYPHRA/RL_PHASE_3/runs/command_logs/cmd.log",
            "trainer_stdout": "ZYPHRA/RL_PHASE_3/runs/closed/trainer_stdout.log",
            "trainer_stderr": "ZYPHRA/RL_PHASE_3/runs/closed/trainer_stderr.log",
            "reward_function": "ZYPHRA/RL_PHASE_3/runs/closed/reward.py",
            "accepted_projection_rows": "ZYPHRA/RL_PHASE_3/runs/closed/accepted_projection_rows.jsonl",
            "evidence_manifest": "ZYPHRA/RL_PHASE_3/runs/closed/evidence_manifest.json",
            "metrics": "ZYPHRA/RL_PHASE_3/runs/closed/metrics.json",
        },
    }
    report = build_phase3_parity_report(
        target_run_id="20260624T040000Z-slurm-243958",
        ppo_report=base_report,
        grpo_report=base_report,
        closed_loop_report=closed_loop,
        introspection_report={"torch": {"version": "2.9.1+rocm6.4", "device_count": 8, "devices": ["AMD Instinct MI300X"] * 8}, "symbols": {"verl.__version__": "0.8.0"}},
        runtime_evidence={
            "container_image": "vllm/vllm-openai-rocm:nightly",
            "runtime_path": "/shared/bb-p3-root/phase3_vllm_verl_py312",
            "runtime_install_runtime": "/shared/bb-p3-root/phase3_vllm_verl_py312",
            "runtime_install_report_path": str(evidence_root / "ZYPHRA" / "RL_PHASE_3" / "scratch_runs" / "phase3_verl_vllm_container_install" / "phase3_verl_vllm_container_install.json"),
            "runtime_install_passed": True,
            "vllm_version": "0.23.1",
        },
        evidence_root=evidence_root,
    )

    checklist = report["checklist"]["C10_infrastructure_parity"]
    assert checklist["status"] == "open"
    assert "target_run_bound_runtime_install" in checklist["missing"]
    assert report["passed"] is False


def test_runtime_evidence_prefers_target_bound_probe_over_legacy_scratch(tmp_path: Path) -> None:
    evidence_root = tmp_path / "docs_tmp"
    runs = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs"
    scratch = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "scratch_runs"
    introspection = runs / "phase3_verl_api_introspection" / "phase3_verl_api_introspection.json"
    introspection.parent.mkdir(parents=True, exist_ok=True)
    introspection.write_text("{}\n")
    for rel in (
        "payloads/phase3_container_ppo_8gpu_stage/run.sh",
        "payloads/phase3_container_grpo_8gpu_stage/run.sh",
        "payloads/closed_loop_verl_train_stage/run.sh",
    ):
        script = runs / rel
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('IMAGE="vllm/vllm-openai-rocm:nightly"\nsource /shared/bb-p3-root/phase3_vllm_verl_py312/bin/activate\n')
    target_report = runs / "phase3_vllm_runtime_parity_probe" / "phase3_vllm_runtime_parity_probe.json"
    _write_json(target_report, {"target_run_id": "target-run", "passed": True, "runtime": "/shared/bb-p3-root/phase3_vllm_verl_py312", "imports": {"vllm": "target-version"}})
    legacy_report = scratch / "phase3_verl_vllm_container_install" / "phase3_verl_vllm_container_install.json"
    _write_json(legacy_report, {"passed": True, "runtime": "/scratch/legacy", "imports": {"vllm": "legacy-version"}})

    evidence = _runtime_evidence(
        evidence_root=evidence_root,
        p1_report={"artifact_paths": {"introspection_report": "ZYPHRA/RL_PHASE_3/runs/phase3_verl_api_introspection/phase3_verl_api_introspection.json"}},
        target_run_id="target-run",
    )

    assert evidence["runtime_install_report_artifact"] == "ZYPHRA/RL_PHASE_3/runs/phase3_vllm_runtime_parity_probe/phase3_vllm_runtime_parity_probe.json"
    assert evidence["runtime_install_runtime"] == "/shared/bb-p3-root/phase3_vllm_verl_py312"
    assert evidence["vllm_version"] == "target-version"


def test_runtime_evidence_ignores_mismatched_target_probe_and_scratch(tmp_path: Path) -> None:
    evidence_root = tmp_path / "docs_tmp"
    runs = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs"
    scratch = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "scratch_runs"
    introspection = runs / "phase3_verl_api_introspection" / "phase3_verl_api_introspection.json"
    introspection.parent.mkdir(parents=True, exist_ok=True)
    introspection.write_text("{}\n")
    for rel in (
        "payloads/phase3_container_ppo_8gpu_stage/run.sh",
        "payloads/phase3_container_grpo_8gpu_stage/run.sh",
        "payloads/closed_loop_verl_train_stage/run.sh",
    ):
        script = runs / rel
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('IMAGE="vllm/vllm-openai-rocm:nightly"\nsource /shared/bb-p3-root/phase3_vllm_verl_py312/bin/activate\n')
    target_report = runs / "phase3_vllm_runtime_parity_probe" / "phase3_vllm_runtime_parity_probe.json"
    _write_json(target_report, {"target_run_id": "other-run", "passed": True, "runtime": "/shared/bb-p3-root/phase3_vllm_verl_py312", "imports": {"vllm": "target-version"}})
    legacy_report = scratch / "phase3_verl_vllm_container_install" / "phase3_verl_vllm_container_install.json"
    _write_json(legacy_report, {"target_run_id": "target-run", "passed": True, "runtime": "/scratch/legacy", "imports": {"vllm": "legacy-version"}})

    evidence = _runtime_evidence(
        evidence_root=evidence_root,
        p1_report={"artifact_paths": {"introspection_report": "ZYPHRA/RL_PHASE_3/runs/phase3_verl_api_introspection/phase3_verl_api_introspection.json"}},
        target_run_id="target-run",
    )

    assert evidence["runtime_install_report_artifact"] == ""
    assert evidence["runtime_install_runtime"] == ""
    assert evidence["vllm_version"] == ""

def _minimal_valid_parity_report(evidence_root: Path) -> dict:
    artifact_paths = {
        "reward_function": "ZYPHRA/RL_PHASE_3/runs/parity/reward.py",
        "accepted_projection_rows": "ZYPHRA/RL_PHASE_3/runs/parity/accepted_projection_rows.jsonl",
        "evidence_manifest": "ZYPHRA/RL_PHASE_3/runs/parity/evidence_manifest.json",
        "metrics": "ZYPHRA/RL_PHASE_3/runs/parity/metrics.json",
        "introspection_report": "ZYPHRA/RL_PHASE_3/runs/parity/introspection.json",
        "runtime_ppo_script": "ZYPHRA/RL_PHASE_3/runs/parity/run_ppo.sh",
        "runtime_grpo_script": "ZYPHRA/RL_PHASE_3/runs/parity/run_grpo.sh",
        "runtime_closed_loop_script": "ZYPHRA/RL_PHASE_3/runs/parity/run_closed_loop.sh",
        "runtime_install_report": "ZYPHRA/RL_PHASE_3/runs/parity/runtime_install.json",
    }
    for rel in artifact_paths.values():
        path = evidence_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    return {
        "schema_version": PHASE3_PARITY_SCHEMA,
        "report_id": PHASE3_PARITY_REPORT_ID,
        "claim_boundary": PHASE3_PARITY_CLAIM_BOUNDARY,
        "target_run_id": "20260624T040000Z-slurm-243958",
        "scorecard_update_allowed": False,
        "passed": True,
        "scorer": {"reward_function_sha256": "sha256:" + "1" * 64},
        "rollout": {"accepted_count": 1},
        "token_logprob": {"available": False},
        "checkpoint": {
            "ppo": {
                "optimizer_step_count": 1,
                "checkpoint_changed": True,
                "checkpoint_before_sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "checkpoint_after_sha256": "sha256:" + "2" * 64,
            },
            "grpo": {
                "optimizer_step_count": 1,
                "checkpoint_changed": True,
                "checkpoint_before_sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "checkpoint_after_sha256": "sha256:" + "3" * 64,
            },
            "closed_loop": {
                "optimizer_step_count": 1,
                "checkpoint_changed": True,
                "checkpoint_before_sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "checkpoint_after_sha256": "sha256:" + "4" * 64,
            },
        },
        "model_merge": {"required": False},
        "infra": {
            "introspection": {
                "torch": {
                    "cuda_available": True,
                    "device_count": 8,
                    "devices": ["AMD Instinct MI300X"] * 8,
                },
                "symbols": {"verl.__version__": "0.8.0"},
            },
        },
        "dataproto": {"dataproto_ok": True, "evidence_manifest_sha256": "sha256:" + "5" * 64},
        "limitations": [],
        "checklist": {
            "C7_checkpoint_parity": {"status": "satisfied"},
            "C10_infrastructure_parity": {"status": "satisfied"},
        },
        "artifact_paths": artifact_paths,
        "errors": [],
    }


def test_parity_validator_rejects_open_checkpoint_checklist(tmp_path: Path) -> None:
    evidence_root = tmp_path / "docs_tmp"
    report = _minimal_valid_parity_report(evidence_root)
    report["checklist"]["C7_checkpoint_parity"]["status"] = "open"

    errors = validate_phase3_parity_report(report, target_run_id=report["target_run_id"], evidence_root=evidence_root)

    assert "checklist.C7_checkpoint_parity.status must be satisfied" in errors


def test_parity_validator_rejects_open_infrastructure_checklist(tmp_path: Path) -> None:
    evidence_root = tmp_path / "docs_tmp"
    report = _minimal_valid_parity_report(evidence_root)
    report["checklist"]["C10_infrastructure_parity"]["status"] = "open"

    errors = validate_phase3_parity_report(report, target_run_id=report["target_run_id"], evidence_root=evidence_root)

    assert "checklist.C10_infrastructure_parity.status must be satisfied" in errors
