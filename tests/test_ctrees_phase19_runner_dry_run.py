from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_phase19_runner_dry_run_uses_calibrated_family_payload(tmp_path: Path) -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = tmp_path / "tasks.json"
    out_path = tmp_path / "results.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "downstream_semantic_pivot_v1__r2",
                        "base_task_id": "downstream_semantic_pivot_v1",
                        "prompt": "Pivot cleanly to the active target.",
                        "task_type": "coding_continuation",
                        "latency_budget_ms": 180000,
                        "max_steps": 10,
                        "overrides": {"max_iterations": 10},
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
            "agent_configs/misc/codex_cli_gpt54mini_e4_live_continuation_calibrated_finish_closure_executor_v1_native.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase19_runner_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    row = payload["results"][0]
    protocol = row["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase18_finish_closure_protocol_payload_v1"
    assert protocol["family"] == "continuation_calibrated_finish_closure_executor_v1_mini_native"
    assert protocol["support_strategy"] == "calibrated_continuation"
    assert protocol["support_payload"]["base_strategy"] == "deterministic_reranker"
    assert protocol["support_payload"]["continuation_policy"]["policy_id"] == "semantic_pivot_sparse_v1"
