from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.conductor_execution import _is_completion_action_result, summarize_execution_results
from agentic_coder_prototype.ctrees.branch_receipt_state import begin_branch_receipt_turn, record_branch_receipt_tool_result
from agentic_coder_prototype.ctrees.finish_closure_contract import build_finish_closure_contract
from agentic_coder_prototype.ctrees.finish_closure_state import (
    begin_finish_closure_turn,
    current_finish_closure_allowlist,
    get_finish_closure_state,
    validate_finish_closure_request,
)
from agentic_coder_prototype.ctrees.live_benchmark_adapter import build_phase18_finish_closure_protocol_payload
from agentic_coder_prototype.ctrees.phase18_finish_closure_audit import build_phase18_finish_closure_audit
from agentic_coder_prototype.state.session_state import SessionState


def _config_path(rel_path: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    direct = repo_root / rel_path
    if direct.exists():
        return str(direct)
    return str(Path(rel_path))


def test_phase18_finish_closure_contract_builds_execution_first_variant() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_finish_closure_executor_v1_native.yaml")
    )
    payload = build_finish_closure_contract(config)
    assert payload["enabled"] is True
    assert payload["family"] == "execution_first_closure_ready_executor_v1_mini_native"
    assert payload["tool_allowlists"]["close"] == ["request_finish_receipt"]
    assert payload["branch_contract"]["finish_rules"]["strict_receipt_forcing"] is True


def test_phase18_protocol_payload_injects_finish_rule() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_deterministic_finish_closure_executor_v1_native.yaml")
    )
    payload = build_phase18_finish_closure_protocol_payload(
        config=config,
        task={
            "id": "phase18_probe_close_after_proof_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "probe_kind": "close_after_proof",
            "prompt": "Choose proof and request finish only when closure-ready.",
            "closure_mode": "proof_required",
            "required_branch_mode": "proof",
            "required_receipts": ["branch", "proof", "finish"],
        },
    )
    assert payload["applied"] is True
    assert payload["family"] == "deterministic_closure_ready_executor_v1_mini_native"
    assert "[Task closure rule]" in payload["prompt"]
    assert "required_receipts: branch, proof, finish" in payload["prompt"]


def test_phase18_state_becomes_closure_ready_after_edit_and_verify(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.set_provider_metadata("phase17_closure_mode", "edit_required")
    session_state.set_provider_metadata("phase17_required_branch_mode", "edit")
    session_state.set_provider_metadata("phase17_required_receipts", ["branch", "edit", "verification", "finish"])
    contract = build_finish_closure_contract(
        load_agent_config(
            _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_finish_closure_executor_v1_native.yaml")
        )
    )
    branch_contract = contract["branch_contract"]

    begin_branch_receipt_turn(session_state, branch_contract, turn_index=1)
    record_branch_receipt_tool_result(
        session_state, branch_contract, tool_name="record_branch_decision", tool_args={"branch_mode": "edit"}, tool_result={"ok": True}, turn_index=1
    )
    begin_branch_receipt_turn(session_state, branch_contract, turn_index=2)
    record_branch_receipt_tool_result(
        session_state, branch_contract, tool_name="apply_patch", tool_args={"input": "*** Begin Patch\n*** End Patch"}, tool_result={"ok": True}, turn_index=2
    )
    record_branch_receipt_tool_result(
        session_state, branch_contract, tool_name="shell_command", tool_args={"command": "pytest -q"}, tool_result={"ok": True}, turn_index=2
    )
    record_branch_receipt_tool_result(
        session_state,
        branch_contract,
        tool_name="record_verification_receipt",
        tool_args={"summary": "verified"},
        tool_result={"ok": True},
        turn_index=2,
    )
    begin_branch_receipt_turn(session_state, branch_contract, turn_index=3)
    begin_finish_closure_turn(session_state, contract, turn_index=3)
    finish_state = get_finish_closure_state(session_state, contract)
    assert finish_state["closure_ready"] is True
    assert current_finish_closure_allowlist(session_state, contract) == ["request_finish_receipt"]


def test_phase18_finish_request_is_denied_before_closure_ready(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.set_provider_metadata("phase17_closure_mode", "proof_required")
    session_state.set_provider_metadata("phase17_required_branch_mode", "proof")
    session_state.set_provider_metadata("phase17_required_receipts", ["branch", "proof", "finish"])
    contract = build_finish_closure_contract(
        load_agent_config(
            _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_finish_closure_executor_v1_native.yaml")
        )
    )
    verdict = validate_finish_closure_request(session_state, contract, tool_args={"branch_mode": "proof"})
    assert verdict["allowed"] is False
    assert "finish denied" in verdict["reason"]


def test_phase18_audit_reads_phase17_reentry_summary() -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
    summary = build_phase18_finish_closure_audit(
        repo_root / "docs_tmp/c_trees/phase_17/artifacts/verification_probe_reentry_v2/phase17_verification_probe_reentry_summary_v2.json"
    )
    assert summary["schema_version"] == "phase18_finish_closure_audit_v1"
    assert summary["tied_at_real_surface"] is True
    assert "edit_ready_no_close" in summary["global_class_counts"]


def test_phase18_runner_dry_run_uses_finish_closure_payload() -> None:
    repo_root = Path("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_main_verify_20260313")
    tasks_path = repo_root / "tmp" / "test_phase18_runner_tasks.json"
    out_path = repo_root / "tmp" / "test_phase18_runner_results.json"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "phase18_probe_close_after_edit_v1",
                        "base_task_id": "downstream_interrupt_repair_v1",
                        "probe_kind": "close_after_edit",
                        "prompt": "Patch, verify, then request finish.",
                        "task_type": "coding_continuation",
                        "latency_budget_ms": 60000,
                        "max_steps": 7,
                        "overrides": {"max_iterations": 7},
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
            "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_finish_closure_executor_v1_native.yaml",
            "--tasks",
            str(tasks_path),
            "--out",
            str(out_path),
            "--workspace-root",
            "tmp/test_phase18_runner_dry_run",
            "--dry-run",
        ],
        cwd=repo_root,
        check=True,
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    protocol = payload["results"][0]["phase11_live_protocol"]
    assert protocol["schema_version"] == "phase18_finish_closure_protocol_payload_v1"
    assert protocol["family"] == "execution_first_closure_ready_executor_v1_mini_native"


def test_provider_native_finish_receipt_counts_as_completion_action() -> None:
    assert _is_completion_action_result(
        "request_finish_receipt",
        {"ok": True, "action": "complete", "accepted_finish": True},
    ) is True


def test_provider_native_tool_names_update_guardrail_usage_summary(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})

    conductor = SimpleNamespace(
        _record_diff_metrics=lambda *args, **kwargs: None,
        _is_test_command=lambda command: "pytest" in str(command),
        _is_read_only_tool=lambda _tool_name: False,
        _record_lsp_reward_metrics=lambda *args, **kwargs: None,
        _record_test_reward_metric=lambda *args, **kwargs: None,
        agent_executor=SimpleNamespace(
            is_tool_failure=lambda _tool_name, result: bool((result or {}).get("error")),
        ),
    )
    executed_results = [
        (
            SimpleNamespace(function="apply_patch", arguments={"input": "*** Begin Patch\n*** End Patch"}, call_id="call_patch"),
            {"ok": True},
        ),
        (
            SimpleNamespace(function="shell_command", arguments={"command": "pytest -q"}, call_id="call_shell"),
            {"stdout": "", "exit": 0},
        ),
    ]

    summarize_execution_results(
        conductor,
        SimpleNamespace(),
        executed_results,
        session_state,
        1,
    )

    assert session_state.tool_usage_summary["total_calls"] == 2
    assert session_state.tool_usage_summary["write_calls"] == 1
    assert session_state.tool_usage_summary["successful_writes"] == 1
    assert session_state.tool_usage_summary["run_shell_calls"] == 1
    assert session_state.tool_usage_summary["test_commands"] == 1
    assert session_state.tool_usage_summary["successful_tests"] == 1
