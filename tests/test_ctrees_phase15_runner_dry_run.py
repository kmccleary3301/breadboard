from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_phase15_runner_dry_run_uses_verifier_executor_payload() -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = repo_root / "tmp" / "test_phase15_runner_tasks.json"
    out_path = repo_root / "tmp" / "test_phase15_runner_results.json"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase15_probe_edit_receipt_required_v1",
                        "base_task_id": "downstream_interrupt_repair_v1",
                        "prompt": "Patch once and verify before finishing.",
                        "task_type": "coding_continuation",
                        "latency_budget_ms": 60000,
                        "max_steps": 6,
                        "overrides": {"max_iterations": 6},
                        "closure_mode": "edit_required",
                        "required_receipts": ["edit", "verification", "finish"],
                        "requires_edit_commitment": True,
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
            "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_verifier_owned_executor_v2_native.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase15_runner_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    row = payload["results"][0]
    protocol = row["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase15_verifier_executor_protocol_payload_v1"
    assert protocol["family"] == "execution_first_verifier_owned_executor_v2_mini_native"
    assert protocol["task_context"]["closure_mode"] == "edit_required"
    assert protocol["verifier_executor_contract"]["enabled"] is True
