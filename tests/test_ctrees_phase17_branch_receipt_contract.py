from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.branch_receipt_contract import build_branch_receipt_contract
from agentic_coder_prototype.ctrees.branch_receipt_state import (
    current_branch_receipt_allowlist,
    record_branch_receipt_tool_result,
)
from agentic_coder_prototype.ctrees.live_benchmark_adapter import build_phase17_branch_receipt_protocol_payload
from agentic_coder_prototype.state.session_state import SessionState


def _config_path(rel_path: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    direct = repo_root / rel_path
    if direct.exists():
        return str(direct)
    return str(Path(rel_path))


def test_phase17_branch_receipt_contract_builds_execution_first_variant() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_branch_receipt_executor_v1_native.yaml")
    )
    payload = build_branch_receipt_contract(config)
    assert payload["enabled"] is True
    assert payload["family"] == "execution_first_branch_receipt_executor_v1_mini_native"
    assert payload["tool_allowlists"]["branch_lock"] == ["record_branch_decision"]
    assert payload["tool_allowlists"]["prove_no_edit"] == ["shell_command", "record_proof_receipt"]


def test_phase17_branch_receipt_contract_supports_strict_receipt_forcing_variant() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_branch_receipt_executor_v1b_native.yaml")
    )
    payload = build_branch_receipt_contract(config)
    assert payload["family"] == "execution_first_branch_receipt_executor_v1b_mini_native"
    assert payload["finish_rules"]["strict_receipt_forcing"] is True
    assert payload["tool_allowlists"]["verify_evidence"] == ["shell_command"]
    assert payload["tool_allowlists"]["verify_receipt"] == ["record_verification_receipt"]


def test_phase17_protocol_payload_injects_branch_rule() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_deterministic_branch_receipt_executor_v1_native.yaml")
    )
    payload = build_phase17_branch_receipt_protocol_payload(
        config=config,
        task={
            "id": "phase17_probe_proof_branch_receipt_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "probe_kind": "proof_branch_receipt",
            "prompt": "Choose proof and record it explicitly.",
            "closure_mode": "proof_required",
            "required_branch_mode": "proof",
            "required_receipts": ["branch", "proof", "finish"],
            "allow_shell_branch_proxy": False,
        },
    )
    assert payload["applied"] is True
    assert payload["family"] == "deterministic_branch_receipt_executor_v1_mini_native"
    assert payload["task_context"]["required_branch_mode"] == "proof"
    assert "[Task branch rule]" in payload["prompt"]
    assert "required_branch_mode: proof" in payload["prompt"]


def test_phase17_state_requires_explicit_branch_then_receipt_progress(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.set_provider_metadata("phase17_closure_mode", "proof_required")
    session_state.set_provider_metadata("phase17_required_branch_mode", "proof")
    session_state.set_provider_metadata("phase17_required_receipts", ["branch", "proof", "finish"])
    contract = {"family": "execution_first_branch_receipt_executor_v1_mini_native"}

    allowlist = current_branch_receipt_allowlist(
        {"current_phase": "branch_lock"},
        {"tool_allowlists": {"branch_lock": ["record_branch_decision"]}},
    )
    assert allowlist == ["record_branch_decision"]

    record_branch_receipt_tool_result(
        session_state,
        contract,
        tool_name="record_branch_decision",
        tool_args={"branch_mode": "proof"},
        tool_result={"ok": True},
        turn_index=1,
    )
    record_branch_receipt_tool_result(
        session_state,
        contract,
        tool_name="record_proof_receipt",
        tool_args={"summary": "no edit needed"},
        tool_result={"ok": True},
        turn_index=2,
    )
    state = session_state.get_provider_metadata("phase17_branch_receipt_state")
    assert state["branch_mode"] == "proof"
    assert state["proof_receipt_observed"] is True


def test_phase17_verification_receipt_requires_post_patch_shell_evidence(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.set_provider_metadata("phase17_closure_mode", "edit_required")
    session_state.set_provider_metadata("phase17_required_branch_mode", "edit")
    session_state.set_provider_metadata("phase17_required_receipts", ["branch", "edit", "verification", "finish"])
    contract = build_branch_receipt_contract(
        load_agent_config(
            _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_branch_receipt_executor_v1b_native.yaml")
        )
    )

    record_branch_receipt_tool_result(
        session_state,
        contract,
        tool_name="record_branch_decision",
        tool_args={"branch_mode": "edit"},
        tool_result={"ok": True},
        turn_index=1,
    )
    record_branch_receipt_tool_result(
        session_state,
        contract,
        tool_name="apply_patch",
        tool_args={"input": "*** Begin Patch\n*** End Patch"},
        tool_result={"ok": True},
        turn_index=2,
    )
    record_branch_receipt_tool_result(
        session_state,
        contract,
        tool_name="shell_command",
        tool_args={"command": "pytest -q"},
        tool_result={"ok": True},
        turn_index=3,
    )
    state = session_state.get_provider_metadata("phase17_branch_receipt_state")
    assert state["post_patch_shell_seen"] is True


def test_phase17_runner_dry_run_uses_branch_receipt_payload() -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = repo_root / "tmp" / "test_phase17_runner_tasks.json"
    out_path = repo_root / "tmp" / "test_phase17_runner_results.json"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase17_probe_edit_branch_lock_v1",
                        "base_task_id": "downstream_interrupt_repair_v1",
                        "probe_kind": "edit_branch_lock",
                        "prompt": "Choose edit, patch, verify, then finish.",
                        "task_type": "coding_continuation",
                        "latency_budget_ms": 60000,
                        "max_steps": 6,
                        "overrides": {"max_iterations": 6},
                        "closure_mode": "edit_required",
                        "required_branch_mode": "edit",
                        "required_receipts": ["branch", "edit", "verification", "finish"],
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
            "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_branch_receipt_executor_v1_native.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase17_runner_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    protocol = payload["results"][0]["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase17_branch_receipt_protocol_payload_v1"
    assert protocol["family"] == "execution_first_branch_receipt_executor_v1_mini_native"
    assert protocol["task_context"]["required_branch_mode"] == "edit"
