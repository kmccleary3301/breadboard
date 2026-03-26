from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_phase14_runner_dry_run_uses_executor_payload(tmp_path: Path) -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = tmp_path / "tasks.json"
    out_path = tmp_path / "results.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase14_probe_single_edit_commit_v1",
                        "base_task_id": "downstream_interrupt_repair_v1",
                        "prompt": "Make one minimal repair and finish only after verification.",
                        "task_type": "coding_continuation",
                        "latency_budget_ms": 90000,
                        "max_steps": 8,
                        "overrides": {"max_iterations": 8},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/phase11_benchmark_runner_stub.py",
            "--config",
            "agent_configs/misc/codex_cli_gpt54_e4_live_candidate_a_executor_v1.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase14_runner_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    row = payload["results"][0]
    protocol = row["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase14_executor_protocol_payload_v1"
    assert protocol["family"] == "candidate_a_executor_v1"
    assert protocol["executor_contract"]["enabled"] is True


def test_phase14_runner_dry_run_supports_execution_first_executor(tmp_path: Path) -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = tmp_path / "tasks.json"
    out_path = tmp_path / "results.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase14_probe_premature_finish_trap_v1",
                        "base_task_id": "downstream_semantic_pivot_v1",
                        "prompt": "Only finish if the runner would accept completion.",
                        "task_type": "coding_continuation",
                        "latency_budget_ms": 90000,
                        "max_steps": 8,
                        "overrides": {"max_iterations": 8},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/phase11_benchmark_runner_stub.py",
            "--config",
            "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_executor_v1.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase14_runner_execution_first_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    row = payload["results"][0]
    protocol = row["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase14_executor_protocol_payload_v1"
    assert protocol["family"] == "execution_first_executor_v1_mini"
    assert protocol["support_strategy"] == "minimal_support"
