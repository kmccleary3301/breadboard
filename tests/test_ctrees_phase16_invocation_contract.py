from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.invocation_first_contract import build_invocation_first_contract
from agentic_coder_prototype.ctrees.invocation_first_state import (
    current_invocation_tool_allowlist,
    record_invocation_tool_result,
)
from agentic_coder_prototype.ctrees.live_benchmark_adapter import build_phase16_invocation_first_protocol_payload
from agentic_coder_prototype.state.session_state import SessionState


def test_phase16_invocation_first_contract_builds_execution_first_family() -> None:
    config = load_agent_config(
        "breadboard_main_verify_20260313/agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_invocation_first_executor_v1_native.yaml"
    )
    payload = build_invocation_first_contract(config)

    assert payload["enabled"] is True
    assert payload["family"] == "execution_first_invocation_first_executor_v1_mini_native"
    assert payload["tool_choice"] == "required"
    assert payload["parallel_tool_calls"] is False


def test_phase16_protocol_payload_marks_probe_kind_and_support_strategy() -> None:
    config = load_agent_config(
        "breadboard_main_verify_20260313/agent_configs/misc/codex_cli_gpt54mini_e4_live_deterministic_invocation_first_executor_v1_native.yaml"
    )
    payload = build_phase16_invocation_first_protocol_payload(
        config=config,
        task={
            "id": "phase16_probe_shell_then_patch_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "probe_kind": "shell_then_patch",
            "allowed_tool_mode": "required",
            "expected_first_tool_family": "shell",
            "prompt": "Inspect once, then patch.",
        },
    )

    assert payload["applied"] is True
    assert payload["family"] == "deterministic_invocation_first_executor_v1_mini_native"
    assert payload["support_strategy"] == "deterministic_reranker"
    assert payload["task_context"]["probe_kind"] == "shell_then_patch"
    assert payload["task_context"]["expected_first_tool_family"] == "shell"


def test_phase16_state_advances_allowlist_after_observed_tool_calls(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.set_provider_metadata("phase16_probe_kind", "shell_then_patch")
    contract = {"family": "execution_first_invocation_first_executor_v1_mini_native"}

    assert current_invocation_tool_allowlist(session_state, contract) == ["shell_command"]
    record_invocation_tool_result(session_state, contract, tool_name="shell_command")
    assert current_invocation_tool_allowlist(session_state, contract) == ["apply_patch"]
    record_invocation_tool_result(session_state, contract, tool_name="apply_patch")
    assert current_invocation_tool_allowlist(session_state, contract) == ["mark_task_complete"]


def test_phase16_runner_dry_run_uses_invocation_first_payload(tmp_path: Path) -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = tmp_path / "tasks.json"
    out_path = tmp_path / "results.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase16_probe_single_required_shell_v1",
                        "base_task_id": "downstream_dependency_shift_v1",
                        "probe_kind": "single_required_shell",
                        "allowed_tool_mode": "required",
                        "expected_first_tool_family": "shell",
                        "prompt": "Use shell immediately.",
                        "task_type": "coding_continuation",
                        "latency_budget_ms": 45000,
                        "max_steps": 3,
                        "overrides": {"max_iterations": 3},
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
            "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_invocation_first_executor_v1_native.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase16_runner_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    row = payload["results"][0]
    protocol = row["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase16_invocation_first_protocol_payload_v1"
    assert protocol["family"] == "execution_first_invocation_first_executor_v1_mini_native"
    assert protocol["invocation_first_contract"]["tool_choice"] == "required"
