from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from breadboard.rl.phase2.benchmark import source_sha256
from breadboard.rl.phase3.benchmark_campaign import BenchmarkCampaignSpec, build_benchmark_campaign_report
from breadboard.rl.phase3.evidence import PHASE3_COMMAND_LOG_MANIFEST_SCHEMA, sha256_file

TARGET = "20260623T000000Z-slurm-234555"


def manifest(evidence: Path) -> dict:
    raw = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "command_logs" / "cmd.log"
    raw.parent.mkdir(parents=True)
    raw.write_text("ok")
    return {"schema_version": PHASE3_COMMAND_LOG_MANIFEST_SCHEMA, "target_run_id": TARGET, "commands": [{"command_id": "cmd", "argv": ["x"], "raw_log_path": "command_logs/cmd.log", "raw_log_sha256": sha256_file(raw), "slurm_job_id": "1", "target_run_id": TARGET, "node": "n", "started_at": "a", "completed_at": "b", "exit_code": 0, "status": "passed"}]}


def test_benchmark_hash_mismatch_fails(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    out.mkdir(parents=True)
    summary = out / "summary.json"; summary.write_text(json.dumps({"source_payload": "actual", "metrics": {"attempted": 1}}))
    contam = out / "contam.json"; contam.write_text(json.dumps({"controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"], "train_overlap_manifest": "none", "prompt_solution_leakage_scan": "none"}))
    report = build_benchmark_campaign_report(BenchmarkCampaignSpec("bench", "v1", "fixture", "bad", "validation", 1, contam, out), run_summary_path=summary, replay_dir=out, command_log_manifest=manifest(evidence))
    assert report["passed"] is False
    assert report["errors"]

def test_benchmark_campaign_uses_docs_tmp_evidence_root(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    out.mkdir(parents=True)
    source_payload = "external-benchmark-row\n"
    summary = out / "summary.json"
    summary.write_text(json.dumps({
        "source_payload": source_payload,
        "failed_tasks": [],
        "metrics": {"attempted": 1, "accepted": 1, "rejected": 0, "quarantined": 0},
    }))
    contam = out / "contam.json"
    contam.write_text(json.dumps({
        "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
        "train_overlap_manifest": "none",
        "prompt_solution_leakage_scan": "none",
    }))
    report = build_benchmark_campaign_report(
        BenchmarkCampaignSpec("bench", "v1", "fixture", source_sha256(source_payload), "validation", 1, contam, out),
        run_summary_path=summary,
        replay_dir=out,
        command_log_manifest=manifest(evidence),
    )
    assert report["passed"] is True
    assert report["source_pin"]["benchmark_version"] == "v1"
    assert report["slice_report"]["claim_boundary"] == "phase3_named_benchmark_campaign_scope"
    assert report["slice_report"]["metadata"]["benchmark_input_kind"] == "external_jsonl"

def test_external_benchmark_all_success_does_not_require_failure_replay(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    out.mkdir(parents=True)
    source_payload = "external-benchmark-row\n"
    summary = out / "summary.json"
    summary.write_text(json.dumps({
        "source_payload": source_payload,
        "failed_tasks": [],
        "quarantined_tasks": [],
        "metrics": {"attempted": 1, "accepted": 1, "rejected": 0, "quarantined": 0},
    }))
    contam = out / "contam.json"
    contam.write_text(json.dumps({
        "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
        "train_overlap_manifest": "none",
        "prompt_solution_leakage_scan": "none",
    }))
    report = build_benchmark_campaign_report(
        BenchmarkCampaignSpec("bench", "v1", "external", source_sha256(source_payload), "validation", 1, contam, out),
        run_summary_path=summary,
        replay_dir=out,
        command_log_manifest=manifest(evidence),
    )

    assert report["passed"] is True
    assert "missing_failure_replay" not in report["errors"]
    assert "missing_failure_replay" not in report["slice_report"]["errors"]
    assert report["slice_report"]["status"] == "external_benchmark_package_accepted"



def test_benchmark_campaign_rejects_zero_accepted_external_tasks(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    out.mkdir(parents=True)
    source_payload = "external-benchmark-row\n"
    summary = out / "summary.json"
    summary.write_text(json.dumps({
        "source_payload": source_payload,
        "failed_tasks": ["task-1"],
        "quarantined_tasks": [],
        "metrics": {"attempted": 1, "accepted": 0, "rejected": 1, "quarantined": 0},
    }))
    (out / "task-1.json").write_text("{}")
    contam = out / "contam.json"
    contam.write_text(json.dumps({
        "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
        "train_overlap_manifest": "none",
        "prompt_solution_leakage_scan": {"candidate_source": "phase3_vllm_model_pipeline"},
    }))
    report = build_benchmark_campaign_report(
        BenchmarkCampaignSpec("bench", "v1", "external", source_sha256(source_payload), "validation", 1, contam, out),
        run_summary_path=summary,
        replay_dir=out,
        command_log_manifest=manifest(evidence),
    )

    assert report["passed"] is False
    assert "benchmark_no_accepted_tasks" in report["errors"]
    assert "benchmark_no_accepted_tasks" in report["slice_report"]["errors"]
    assert report["slice_report"]["status"] == "rejected_external_benchmark_controls"


def test_benchmark_campaign_rejects_unsafe_duplicate_and_mismatched_replay_ids(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    out.mkdir(parents=True)
    source_payload = "external-benchmark-row\n"
    summary = out / "summary.json"
    summary.write_text(json.dumps({
        "source_payload": source_payload,
        "failed_tasks": ["task-1", "task-1", "../secret", "task-2"],
        "quarantined_tasks": [],
        "metrics": {"attempted": 4, "accepted": 1, "rejected": 3, "quarantined": 0},
    }))
    (out / "task-1.json").write_text(json.dumps({"task_id": "task-1"}))
    (out / "task-2.json").write_text(json.dumps({"task_id": "other-task"}))
    contam = out / "contam.json"
    contam.write_text(json.dumps({
        "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
        "train_overlap_manifest": "none",
        "prompt_solution_leakage_scan": {"candidate_source": "phase3_vllm_model_pipeline"},
    }))

    report = build_benchmark_campaign_report(
        BenchmarkCampaignSpec("bench", "v1", "external", source_sha256(source_payload), "validation", 4, contam, out),
        run_summary_path=summary,
        replay_dir=out,
        command_log_manifest=manifest(evidence),
    )

    assert report["passed"] is False
    assert "benchmark_replay_task_id_duplicate:task-1" in report["errors"]
    assert "unsafe replay task_id:../secret" in report["errors"]
    assert "replay artifact task_id mismatch for task-2" in report["errors"]



def test_benchmark_campaign_accepts_humaneval_slash_replay_ids(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    replay = out / "HumanEval"
    replay.mkdir(parents=True)
    source_payload = "external-benchmark-row\n"
    summary = out / "summary.json"
    summary.write_text(json.dumps({
        "source_payload": source_payload,
        "failed_tasks": ["HumanEval/0"],
        "quarantined_tasks": [],
        "metrics": {"attempted": 1, "accepted": 1, "rejected": 1, "quarantined": 0},
    }))
    (replay / "0.json").write_text(json.dumps({"task_id": "HumanEval/0"}))
    contam = out / "contam.json"
    contam.write_text(json.dumps({
        "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
        "train_overlap_manifest": "none",
        "prompt_solution_leakage_scan": {"candidate_source": "phase3_vllm_model_pipeline"},
    }))

    report = build_benchmark_campaign_report(
        BenchmarkCampaignSpec("bench", "v1", "external", source_sha256(source_payload), "validation", 1, contam, out),
        run_summary_path=summary,
        replay_dir=out,
        command_log_manifest=manifest(evidence),
    )

    assert report["passed"] is True
    assert str(out / "HumanEval" / "0.json") in report["slice_report"]["failure_replay_refs"]


def test_benchmark_campaign_rejects_duplicate_logical_replay_artifacts(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    (out / "HumanEval").mkdir(parents=True)
    source_payload = "external-benchmark-row\n"
    summary = out / "summary.json"
    summary.write_text(json.dumps({
        "source_payload": source_payload,
        "failed_tasks": [],
        "quarantined_tasks": [],
        "metrics": {"attempted": 1, "accepted": 1, "rejected": 0, "quarantined": 0},
    }))
    (out / "HumanEval" / "0.json").write_text(json.dumps({"task_id": "HumanEval/0"}))
    (out / "HumanEval_0.json").write_text(json.dumps({"task_id": "HumanEval/0"}))
    contam = out / "contam.json"
    contam.write_text(json.dumps({
        "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
        "train_overlap_manifest": "none",
        "prompt_solution_leakage_scan": {"candidate_source": "phase3_vllm_model_pipeline"},
    }))

    report = build_benchmark_campaign_report(
        BenchmarkCampaignSpec("bench", "v1", "external", source_sha256(source_payload), "validation", 1, contam, out),
        run_summary_path=summary,
        replay_dir=out,
        command_log_manifest=manifest(evidence),
    )

    assert report["passed"] is False
    assert any(error.startswith("duplicate replay artifact for HumanEval/0:") for error in report["errors"])
def test_benchmark_campaign_rejects_hand_written_target_payload_candidates(tmp_path: Path) -> None:
    evidence = tmp_path / "docs_tmp"
    out = evidence / "ZYPHRA" / "RL_PHASE_3" / "runs" / "bench"
    out.mkdir(parents=True)
    source_payload = "external-benchmark-row\n"
    summary = out / "summary.json"
    summary.write_text(json.dumps({
        "source_payload": source_payload,
        "failed_tasks": [],
        "quarantined_tasks": [],
        "metrics": {"attempted": 1, "accepted": 1, "rejected": 0, "quarantined": 0},
    }))
    contam = out / "contam.json"
    contam.write_text(json.dumps({
        "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
        "train_overlap_manifest": "none",
        "prompt_solution_leakage_scan": {"candidate_source": "hand_written_baseline_in_target_payload"},
    }))
    report = build_benchmark_campaign_report(
        BenchmarkCampaignSpec("bench", "v1", "external", source_sha256(source_payload), "validation", 1, contam, out),
        run_summary_path=summary,
        replay_dir=out,
        command_log_manifest=manifest(evidence),
    )

    assert report["passed"] is False
    assert "benchmark_candidate_source_not_phase3_model_pipeline" in report["errors"]
    assert "benchmark_candidate_source_not_phase3_model_pipeline" in report["slice_report"]["errors"]
    assert report["slice_report"]["status"] == "rejected_external_benchmark_controls"
    assert report["slice_report"]["passed"] is False
    assert report["slice_report"]["accepted_for_claim"] is False

def test_benchmark_runner_uses_locked_fixture_without_external_jsonl(tmp_path: Path, monkeypatch) -> None:
    evidence = tmp_path / "docs_tmp"
    phase_dir = evidence / "ZYPHRA" / "RL_PHASE_3"
    command_manifest = manifest(evidence)
    (phase_dir / "runs").mkdir(parents=True, exist_ok=True)
    (phase_dir / "runs" / "phase3_command_log_manifest.json").write_text(json.dumps(command_manifest))
    monkeypatch.delenv("PHASE3_BENCHMARK_JSONL", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/run_phase3_benchmark_campaign.py",
            "--phase-dir",
            str(phase_dir),
            "--target-run-id",
            TARGET,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0
    report = json.loads((phase_dir / "runs" / "benchmark_campaign" / "p3-m7_benchmark_campaign.json").read_text())
    assert report["passed"] is True
    assert report["benchmark_report"]["slice_report"]["source_pin"]["benchmark_version"] == "fixture-v2"
    assert report["required_artifact_keys"] == ["benchmark_report", "benchmark_source", "run_summary", "contamination", "replay_manifest"]
