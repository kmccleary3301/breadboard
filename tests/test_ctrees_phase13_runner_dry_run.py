from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.phase11_benchmark_runner_stub import _seed_workspace_from_task


def test_phase13_runner_dry_run_uses_runtime_payload(tmp_path: Path) -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = tmp_path / "tasks.json"
    out_path = tmp_path / "results.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase13_floor_interrupt_single_edit_v1",
                        "base_task_id": "downstream_interrupt_repair_v1",
                        "prompt": "Make one minimal grounded repair and verify it.",
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
            "agent_configs/misc/codex_cli_gpt5_e4_live_candidate_a_runtime_v1.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase13_runner_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    row = payload["results"][0]
    protocol = row["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase13_runtime_protocol_payload_v1"
    assert protocol["family"] == "candidate_a_runtime_v1"
    assert protocol["runtime_contract"]["enabled"] is True


def test_seed_workspace_from_task_copies_repo_subset(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    (seed / "agentic_coder_prototype").mkdir(parents=True)
    (seed / "agentic_coder_prototype" / "sample.py").write_text("print('ok')\n", encoding="utf-8")
    (seed / "logging").mkdir()
    (seed / "logging" / "drop.txt").write_text("ignore\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    seeded = _seed_workspace_from_task(workspace, {"workspace_seed_path": str(seed)})

    assert seeded is True
    assert (workspace / "agentic_coder_prototype" / "sample.py").exists()
    assert not (workspace / "logging").exists()


def test_phase13_runner_dry_run_uses_minimal_support_challenger_payload(tmp_path: Path) -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = tmp_path / "tasks.json"
    out_path = tmp_path / "results.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase13_floor_semantic_pivot_v1",
                        "base_task_id": "downstream_semantic_pivot_v1",
                        "prompt": "Make the smallest grounded fix and verify it.",
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
            "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_runtime_v1.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase13_runner_challenger_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    row = payload["results"][0]
    protocol = row["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase13_runtime_protocol_payload_v1"
    assert protocol["family"] == "execution_first_runtime_v1_mini"
    assert protocol["support_strategy"] == "minimal_support"
    assert protocol["support_payload"]["base_strategy"] == "deterministic_reranker"
