from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.conductor_execution import (
    execute_agent_calls,
    _force_failed_write_final_answer,
    _force_failed_verification_final_answer,
    _force_post_receipt_final_answer,
    _implementation_verification_receipt_missing,
    _is_completion_action_result,
    _latest_prompt_requires_implementation_write,
    _latest_prompt_requests_verification,
    _shell_command_write_targets,
    summarize_execution_results,
)
from agentic_coder_prototype.conductor.components import (
    latest_real_user_prompt,
    session_requires_workspace_tool_usage,
    should_require_workspace_tool_usage,
)
from agentic_coder_prototype.conductor.execution import (
    _ensure_tool_completion_final_message,
    _implementation_receipts_satisfied,
    _latest_prompt_requests_read_only_answer_after_observation,
    _latest_prompt_requests_tool_stop_after_observation,
    _latest_implementation_prompt,
    _maybe_auto_verify_make_after_write_receipts,
    _maybe_block_read_only_implementation_loop,
    _maybe_force_read_only_observation_closure,
    _maybe_force_post_write_auto_verification_closure,
    _maybe_force_requested_shell_command_closure,
    _requested_write_targets,
    _run_subprocess_capture_with_group_timeout,
)
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


def test_codex_apply_patch_input_payload_counts_requested_write_receipts(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smtp_server.c, Makefile, README.md, and smoke_test.sh.",
        },
        to_provider=True,
    )

    conductor = SimpleNamespace(
        _record_diff_metrics=lambda *args, **kwargs: None,
        _is_test_command=lambda command: False,
        _is_read_only_tool=lambda _tool_name: False,
        _record_lsp_reward_metrics=lambda *args, **kwargs: None,
        _record_test_reward_metric=lambda *args, **kwargs: None,
        agent_executor=SimpleNamespace(
            is_tool_failure=lambda _tool_name, result: bool((result or {}).get("error")),
        ),
    )
    patch = """*** Begin Patch
*** Add File: smtp_server.c
+int main(void) { return 0; }
*** Add File: Makefile
+all:
+\tcc -o smtp_server smtp_server.c
*** Add File: README.md
+# SMTP
*** Add File: smoke_test.sh
+#!/usr/bin/env bash
+echo ok
*** End Patch"""

    summarize_execution_results(
        conductor,
        SimpleNamespace(),
        [
            (
                SimpleNamespace(function="apply_patch", arguments={"input": patch}, call_id="call_patch"),
                {"ok": True},
            )
        ],
        session_state,
        1,
    )

    summary = session_state.tool_usage_summary
    assert summary["successful_writes"] == 1
    assert summary["successful_user_facing_writes"] == 1
    assert summary["successful_requested_file_writes"] == 1
    assert set(summary["successful_requested_write_targets"]) == {
        "smtp_server.c",
        "Makefile",
        "README.md",
        "smoke_test.sh",
    }


def test_wrapped_provider_native_apply_patch_result_counts_successful_write(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smtp_server.c, Makefile, README.md, and smoke_test.sh.",
        },
        to_provider=True,
    )

    conductor = SimpleNamespace(
        _record_diff_metrics=lambda *args, **kwargs: None,
        _is_test_command=lambda command: False,
        _is_read_only_tool=lambda _tool_name: False,
        _record_lsp_reward_metrics=lambda *args, **kwargs: None,
        _record_test_reward_metric=lambda *args, **kwargs: None,
        agent_executor=SimpleNamespace(
            is_tool_failure=lambda _tool_name, result: bool((result or {}).get("error") or (result or {}).get("ok") is False),
        ),
    )
    patch = """*** Begin Patch
*** Add File: smtp_server.c
+int main(void) { return 0; }
*** Add File: Makefile
+all:
+\tcc -o smtp_server smtp_server.c
*** Add File: README.md
+# SMTP
*** Add File: smoke_test.sh
+#!/usr/bin/env bash
+echo ok
*** End Patch"""

    summarize_execution_results(
        conductor,
        SimpleNamespace(),
        [
            (
                SimpleNamespace(function="apply_patch", arguments={"input": patch, "patch": patch}, call_id="call_patch"),
                {
                    "fn": "apply_patch",
                    "args": {"input": patch, "patch": patch},
                    "out": {
                        "ok": True,
                        "action": "apply_patch",
                        "exit": 0,
                        "stdout": "",
                        "stderr": "",
                        "data": {
                            "manual_fallback": True,
                            "paths": ["smtp_server.c", "Makefile", "README.md", "smoke_test.sh"],
                        },
                    },
                },
            )
        ],
        session_state,
        1,
    )

    summary = session_state.tool_usage_summary
    assert summary["successful_writes"] == 1
    assert summary["successful_user_facing_writes"] == 1
    assert summary["successful_requested_file_writes"] == 1
    assert set(summary["successful_requested_write_targets"]) == {
        "smtp_server.c",
        "Makefile",
        "README.md",
        "smoke_test.sh",
    }


def test_requested_write_targets_survive_later_verify_turn(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smtp_server.c, Makefile, README.md, and smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.add_message(
        {
            "role": "user",
            "content": "Now verify the project like a normal code agent. Inspect the files you created and fix anything that fails.",
        },
        to_provider=True,
    )

    assert set(_requested_write_targets(session_state)) == {
        "smtp_server.c",
        "Makefile",
        "README.md",
        "smoke_test.sh",
    }


def test_failed_verification_forced_closure_summarizes_after_retries(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Build smtp_server.c and Makefile, then run make and smoke verification.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["smtp_server.c", "Makefile"],
            "requested_write_targets": ["smtp_server.c", "Makefile"],
            "requested_write_matches": ["smtp_server.c", "Makefile"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    for turn in range(2, 5):
        session_state.record_tool_event(
            turn,
            "run_shell",
            success=False,
            metadata={
                "is_run_shell": True,
                "is_test_command": True,
                "command": "make && ./smoke_test.sh",
                "exit_code": 2,
            },
            result={"exit": 2, "stderr": "compile failed"},
        )

    closed = _force_failed_verification_final_answer(
        session_state,
        reason="failed_verification_after_retries",
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is False
    assert session_state.completion_summary["method"] == "failed_verification_forced_closure"
    assert "verification is still failing" in session_state.messages[-1]["content"]


def test_failed_verification_forced_closure_allows_partial_test_success_when_requested_smoke_missing(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Build smtp_server.c and Makefile, then run make and timeout 20s bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_matches": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make",
            "exit_code": 0,
        },
        result={"exit": 0, "stdout": "built"},
    )
    for turn in range(3, 6):
        session_state.record_tool_event(
            turn,
            "run_shell",
            success=False,
            metadata={
                "is_run_shell": True,
                "is_test_command": True,
                "command": "timeout 20s bash smoke_test.sh",
                "exit_code": 124,
            },
            result={"exit": 124, "stderr": "timed out"},
        )

    closed = _force_failed_verification_final_answer(
        session_state,
        reason="failed_verification_after_retries",
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is False
    assert session_state.completion_summary["method"] == "failed_verification_forced_closure"
    assert session_state.completion_summary["successful_tests"] == 1
    assert "timeout 20s bash smoke_test.sh" in session_state.messages[-1]["content"]


def test_failed_requested_write_forced_closure_summarizes_after_retries(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix the src/args.ts file cleanly, then run bash smoke_test.sh.",
        },
        to_provider=True,
    )
    for turn in range(1, 4):
        session_state.record_tool_event(
            turn,
            "apply_unified_patch",
            success=False,
            metadata={
                "is_write": True,
                "is_user_facing_write": True,
                "write_targets": ["src/args.ts"],
                "requested_write_targets": ["src/args.ts"],
                "requested_write_matches": ["src/args.ts"],
                "is_requested_file_write": True,
                "call_id": f"call_patch_{turn}",
            },
            result={"error": "hunk failed"},
        )
    conductor = SimpleNamespace(config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}})

    closed = _force_failed_write_final_answer(
        conductor,
        session_state,
        reason="failed_requested_write_after_retries",
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is False
    assert session_state.completion_summary["method"] == "failed_write_forced_closure"
    assert "Failed write attempts: 3" in session_state.messages[-1]["content"]
    assert "src/args.ts" in session_state.messages[-1]["content"]


def test_failed_requested_write_forced_closure_does_not_fire_when_requested_files_exist(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smtp_server.h, README.md, and smoke_test.sh.",
        },
        to_provider=True,
    )
    for name in ("smtp_server.h", "README.md", "smoke_test.sh"):
        (tmp_path / name).write_text("ok\n", encoding="utf-8")
    for turn in range(1, 4):
        session_state.record_tool_event(
            turn,
            "apply_unified_patch",
            success=False,
            metadata={
                "is_write": True,
                "is_user_facing_write": True,
                "write_targets": ["smtp_server.h", "README.md", "smoke_test.sh"],
                "requested_write_targets": ["smtp_server.h", "README.md", "smoke_test.sh"],
                "requested_write_matches": ["smtp_server.h", "README.md", "smoke_test.sh"],
                "is_requested_file_write": True,
                "call_id": f"call_patch_{turn}",
            },
            result={"error": "stale hunk"},
        )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}},
    )

    closed = _force_failed_write_final_answer(
        conductor,
        session_state,
        reason="failed_requested_write_after_retries",
    )

    assert closed is False


def test_failed_requested_write_forced_closure_fires_for_repeated_failed_edits_to_existing_file(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "args.ts").write_text("export const existing = true;\n", encoding="utf-8")
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix the src/args.ts file cleanly, then run node --check src/args.ts and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    for turn in range(1, 4):
        session_state.record_tool_event(
            turn,
            "apply_unified_patch",
            success=False,
            metadata={
                "is_write": True,
                "is_user_facing_write": True,
                "write_targets": ["src/args.ts"],
                "requested_write_targets": ["src/args.ts"],
                "requested_write_matches": ["src/args.ts"],
                "is_requested_file_write": True,
                "call_id": f"call_patch_{turn}",
            },
            result={"ok": False, "stderr": "error: unrecognized input"},
        )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}},
    )

    closed = _force_failed_write_final_answer(
        conductor,
        session_state,
        reason="failed_requested_write_after_retries",
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is False
    assert session_state.completion_summary["method"] == "failed_write_forced_closure"
    assert "src/args.ts" in session_state.messages[-1]["content"]


def test_implementation_prompt_survives_tool_result_user_relay(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix the src/args.ts file, then run node --check src/args.ts and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.add_message(
        {
            "role": "user",
            "content": "Tool execution results:\nexport interface CliOptions { name: string; }\n",
        },
        to_provider=True,
    )

    assert _latest_implementation_prompt(session_state).startswith("Fix the src/args.ts file")
    assert _latest_prompt_requires_implementation_write(session_state) is True
    assert _latest_prompt_requests_verification(session_state) is True


def test_smoke_prompt_requires_successful_smoke_command_not_only_make(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Build smtp_server.c and Makefile, then run make and ./smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_matches": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    conductor = SimpleNamespace(config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}})

    assert _implementation_verification_receipt_missing(conductor, session_state) is True

    session_state.record_tool_event(
        3,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "./smoke_test.sh",
            "exit_code": 0,
        },
        result={"exit": 0},
    )

    assert _implementation_verification_receipt_missing(conductor, session_state) is False


def test_auto_verification_runs_requested_smoke_command_when_smoke_is_requested(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@true\nclean:\n\t@true\n.PHONY: all clean\n", encoding="utf-8")
    smoke_script = tmp_path / "smoke_test.sh"
    smoke_script.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho tiny-repair-smoke-ok\n", encoding="utf-8")
    smoke_script.chmod(0o755)
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix the calc.c file, then run make clean all and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["calc.c"],
            "requested_write_targets": ["calc.c"],
            "requested_write_matches": ["calc.c"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )

    auto_satisfied = _maybe_auto_verify_make_after_write_receipts(conductor, session_state)

    assert auto_satisfied is True
    assert _implementation_verification_receipt_missing(conductor, session_state) is False
    assert any(
        tool.get("meta", {}).get("command") == "make clean all && bash smoke_test.sh"
        for turn_payload in session_state.turn_tool_usage.values()
        for tool in turn_payload.get("tools", [])
    )
    assert not session_state.completion_summary


def test_auto_verification_runs_node_check_and_smoke_without_makefile(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "args.ts").write_text("export const ok = true;\n", encoding="utf-8")
    smoke_script = tmp_path / "smoke_test.sh"
    smoke_script.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho tiny-ts-cli-smoke-ok\n", encoding="utf-8")
    smoke_script.chmod(0o755)
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix the src/args.ts file, then run node --check src/args.ts and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["src/args.ts"],
            "requested_write_targets": ["src/args.ts"],
            "requested_write_matches": ["src/args.ts"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )

    auto_satisfied = _maybe_auto_verify_make_after_write_receipts(conductor, session_state)

    assert auto_satisfied is True
    assert _implementation_verification_receipt_missing(conductor, session_state) is False
    assert any(
        tool.get("meta", {}).get("command") == "node --check src/args.ts && bash smoke_test.sh"
        for turn_payload in session_state.turn_tool_usage.values()
        for tool in turn_payload.get("tools", [])
    )


def test_auto_verification_preserves_requested_timeout_smoke_command(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text(
        "all:\n\t@echo build-ok\nclean:\n\t@true\n",
        encoding="utf-8",
    )
    smoke_script = tmp_path / "smoke_test.sh"
    smoke_script.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho smoke-ok\n", encoding="utf-8")
    smoke_script.chmod(0o755)
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix smtp_server.c, then run make clean all and timeout 20s bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["smtp_server.c"],
            "requested_write_targets": ["smtp_server.c"],
            "requested_write_matches": ["smtp_server.c"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )

    auto_satisfied = _maybe_auto_verify_make_after_write_receipts(conductor, session_state)

    assert auto_satisfied is True
    assert any(
        tool.get("meta", {}).get("command") == "make clean all && timeout 20s bash smoke_test.sh"
        for turn_payload in session_state.turn_tool_usage.values()
        for tool in turn_payload.get("tools", [])
    )


def test_group_timeout_kills_daemon_like_grandchild(tmp_path: Path) -> None:
    marker = tmp_path / "leaked_after_timeout.txt"
    script = tmp_path / "leaky_smoke.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"(sleep 3; echo leaked > {marker.name}) &\n"
        "wait\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = _run_subprocess_capture_with_group_timeout(
        ["bash", "-lc", f"bash {script.name}"],
        cwd=str(tmp_path),
        timeout=1,
    )

    assert result["exit"] == 124
    assert result["timed_out"] is True
    time.sleep(4)
    assert not marker.exists()


def test_node_check_receipt_target_keeps_file_extension(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix the src/args.ts file, then run node --check src/args.ts and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["src/args.ts"],
            "requested_write_targets": ["src/args.ts"],
            "requested_write_matches": ["src/args.ts"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    session_state.record_tool_event(
        1,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "node --check src/args.ts && bash smoke_test.sh",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}},
    )

    assert _implementation_verification_receipt_missing(conductor, session_state) is False
    assert _implementation_receipts_satisfied(conductor, session_state) is True


def test_post_write_auto_verification_forces_closure_without_waiting_for_completion_attempt(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "args.ts").write_text("export const ok = true;\n", encoding="utf-8")
    smoke_script = tmp_path / "smoke_test.sh"
    smoke_script.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho tiny-ts-cli-smoke-ok\n", encoding="utf-8")
    smoke_script.chmod(0o755)
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix the src/args.ts file, then run node --check src/args.ts and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["src/args.ts"],
            "requested_write_targets": ["src/args.ts"],
            "requested_write_matches": ["src/args.ts"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )

    closed = _maybe_force_post_write_auto_verification_closure(
        conductor,
        session_state,
        reason="unit_test_post_write_auto_verify",
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is True
    assert session_state.completion_summary["method"] == "post_receipt_forced_closure"
    assert "node --check src/args.ts && bash smoke_test.sh" in session_state.messages[-1]["content"]


def test_requested_write_targets_ignore_negated_file_mentions(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "Fix calc.c cleanly. Do not create a replacement main.c/app project, "
                "and do not rewrite the Makefile unless absolutely necessary."
            ),
        },
        to_provider=True,
    )

    assert _requested_write_targets(session_state) == ["calc.c"]


def test_requested_write_targets_ignore_verification_command_file_mentions(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "The smoke test fails because src/args.ts parses --name incorrectly. "
                "Fix src/args.ts cleanly, then run node --check src/args.ts and bash smoke_test.sh."
            ),
        },
        to_provider=True,
    )

    assert _requested_write_targets(session_state) == ["src/args.ts"]


def test_requested_write_targets_infer_readme_usage_target(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "Do one final maintenance pass. Add or improve comments and README usage only if needed, "
                "then run make."
            ),
        },
        to_provider=True,
    )

    assert _requested_write_targets(session_state) == ["README.md"]


def test_execute_agent_calls_rejects_non_requested_write_target(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message({"role": "user", "content": "Fix calc.c cleanly."}, to_provider=True)
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: "apply_unified_patch" if name == "apply_patch" else name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="apply_patch",
            arguments={"input": "*** Begin Patch\n*** Add File: main.c\n+int main(void){return 0;}\n*** End Patch"},
            call_id="call_bad",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["unrequested_write_target"] is True
    assert "calc.c" in validation_error["error"]
    assert "main.c" in validation_error["error"]


def test_execute_agent_calls_treats_raw_apply_patch_as_write_tool(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message({"role": "user", "content": "Fix calc.c cleanly."}, to_provider=True)
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="apply_patch",
            arguments={"input": "*** Begin Patch\n*** Add File: main.c\n+int main(void){return 0;}\n*** End Patch"},
            call_id="call_raw_patch_bad_target",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["unrequested_write_target"] is True
    assert "calc.c" in validation_error["error"]
    assert "main.c" in validation_error["error"]


def test_execute_agent_calls_rejects_deleting_requested_target_without_delete_request(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {"role": "user", "content": "Create smtp_server.c, Makefile, README.md, and smoke_test.sh."},
        to_provider=True,
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: "apply_unified_patch" if name == "apply_patch" else name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="apply_patch",
            arguments={"input": "*** Begin Patch\n*** Delete File: Makefile\n*** End Patch"},
            call_id="call_delete_makefile",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["destructive_requested_target_delete"] is True
    assert "Makefile" in validation_error["error"]


def test_stop_after_observation_guard_ignores_stop_after_verification_phrase(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Run make and bash smoke_test.sh. Once make and bash smoke_test.sh pass, stop using tools.",
        },
        to_provider=True,
    )

    assert _latest_prompt_requests_tool_stop_after_observation(session_state) is False


def test_read_only_observation_prompt_detects_then_reply_marker(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "V7 endurance turn 1. Use tools to inspect README.md, then reply with "
                "marker V7_ENDURANCE_TURN_1 and one sentence."
            ),
        },
        to_provider=True,
    )

    assert _latest_prompt_requests_read_only_answer_after_observation(session_state) is True


def test_read_only_observation_prompt_detects_filename_marker_target(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "V7 endurance turn 8. Use tools to inspect v7_endurance_marker.txt, "
                "then reply with marker V7_ENDURANCE_TURN_8."
            ),
        },
        to_provider=True,
    )

    assert _latest_prompt_requests_read_only_answer_after_observation(session_state) is True


def test_read_only_observation_prompt_does_not_match_implementation(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "Create smtp_server.c, then run make and smoke_test.sh. "
                "After verification passes, reply with marker BUILD_OK."
            ),
        },
        to_provider=True,
    )

    assert _latest_prompt_requests_read_only_answer_after_observation(session_state) is False


def test_repeated_read_only_observation_forces_marker_closure(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "V7 endurance turn 1. Use tools to inspect README.md, then reply with "
                "marker V7_ENDURANCE_TURN_1 and one sentence."
            ),
        },
        to_provider=True,
    )
    session_state.begin_turn(8)
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "rg --files"},
            call_id="call_initial_read",
        )
    ]
    assert _maybe_force_read_only_observation_closure(session_state, parsed_calls) is False
    for command in ["sed -n '1,220p' README.md", "cat README.md"]:
        session_state.record_tool_event(
            8,
            "run_shell",
            success=True,
            metadata={"is_run_shell": True, "command": command, "exit_code": 0},
            result={"exit": 0, "stdout": "# Dummy"},
        )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "rg --files"},
            call_id="call_repeat_read",
        )
    ]

    closed = _maybe_force_read_only_observation_closure(session_state, parsed_calls)

    assert closed is True
    assert session_state.completion_summary["completed"] is True
    assert session_state.completion_summary["method"] == "read_only_observation_forced_closure"
    assert session_state.messages[-1]["content"].startswith("V7_ENDURANCE_TURN_1\n")
    assert "repetitive read-only inspection loop" in session_state.messages[-1]["content"]


def test_read_only_observation_closure_ignores_prior_turn_tool_history(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "V7 endurance turn 8. Use tools to inspect v7_endurance_marker.txt, "
                "then reply with marker V7_ENDURANCE_TURN_8."
            ),
        },
        to_provider=True,
    )
    for turn in range(1, 8):
        session_state.record_tool_event(
            turn,
            "run_shell",
            success=True,
            metadata={"is_run_shell": True, "command": "cat README.md", "exit_code": 0},
            result={"exit": 0, "stdout": "# Dummy"},
        )
    session_state.begin_turn(8)
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "cat v7_endurance_marker.txt"},
            call_id="call_marker_read",
        )
    ]

    closed = _maybe_force_read_only_observation_closure(session_state, parsed_calls)

    assert closed is False
    assert session_state.completion_summary == {}


def test_read_only_loop_guard_has_forced_closure_safety_net(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {"role": "user", "content": "Create report.md after inspecting README.md."},
        to_provider=True,
    )
    session_state.tool_usage_summary["run_shell_calls"] = 4
    session_state.tool_usage_summary["total_calls"] = 4
    session_state.set_provider_metadata("implementation_read_only_loop_blocks", 2)
    conductor = SimpleNamespace(
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "max_read_only_calls_before_write": 4,
                }
            }
        },
        workspace=str(tmp_path),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "cat README.md"},
            call_id="call_read_again",
        )
    ]
    markdown_logger = SimpleNamespace(log_user_message=lambda *_args, **_kwargs: None)

    closed = _maybe_block_read_only_implementation_loop(
        conductor,
        session_state,
        markdown_logger,
        parsed_calls,
        False,
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is True
    assert session_state.completion_summary["reason"] == "repeated_read_only_loop_safety_net"
    assert "repetitive read-only inspection loop" in session_state.messages[-1]["content"]


def test_read_only_loop_after_failed_requested_write_is_not_success_closure(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {"role": "user", "content": "Fix src/args.ts cleanly, then run bash smoke_test.sh."},
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=False,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["src/args.ts"],
            "requested_write_targets": ["src/args.ts"],
            "requested_write_matches": ["src/args.ts"],
            "is_requested_file_write": True,
            "call_id": "call_failed_patch",
        },
        result={"ok": False, "stderr": "patch did not apply"},
    )
    session_state.tool_usage_summary["run_shell_calls"] = 4
    session_state.tool_usage_summary["total_calls"] = 5
    session_state.set_provider_metadata("implementation_read_only_loop_blocks", 2)
    conductor = SimpleNamespace(
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "max_read_only_calls_before_write": 4,
                }
            }
        },
        workspace=str(tmp_path),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "sed -n '1,200p' src/args.ts"},
            call_id="call_read_again",
        )
    ]
    markdown_logger = SimpleNamespace(log_user_message=lambda *_args, **_kwargs: None)

    closed = _maybe_block_read_only_implementation_loop(
        conductor,
        session_state,
        markdown_logger,
        parsed_calls,
        False,
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is False
    assert session_state.completion_summary["method"] == "failed_write_forced_closure"
    assert session_state.completion_summary["reason"] == "read_only_loop_after_failed_requested_write"
    assert "repetitive read-only inspection loop" not in session_state.messages[-1]["content"]


def test_latest_real_user_prompt_strips_workspace_tool_required_block(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "Run make and bash smoke_test.sh.\n\n"
                "<WORKSPACE_TOOL_REQUIRED>\n"
                "This turn requires real workspace interaction.\n"
                "</WORKSPACE_TOOL_REQUIRED>"
            ),
        },
        to_provider=True,
    )

    assert latest_real_user_prompt(session_state) == "Run make and bash smoke_test.sh."
    assert session_requires_workspace_tool_usage(session_state) is True


def test_workspace_tool_requirement_does_not_wrap_implementation_tasks() -> None:
    assert should_require_workspace_tool_usage(
        "Build a small SMTP server in C, create Makefile, and run make and bash smoke_test.sh."
    ) is False
    assert should_require_workspace_tool_usage("Run pwd and tell me the current directory.") is True


def test_execute_agent_calls_rejects_prompt_forbidden_direct_command(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Run bash smoke_test.sh. Do not run ./smtp_server directly or with --help.",
        },
        to_provider=True,
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "printf 'QUIT\\r\\n' | ./smtp_server"},
            call_id="call_forbidden_daemon",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["forbidden_direct_command"] is True
    assert "./smtp_server" in validation_error["error"]


def test_execute_agent_calls_rejects_shell_tunneled_apply_patch(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smtp_server.c and Makefile, then run make.",
        },
        to_provider=True,
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "apply_patch <<'PATCH'\n*** Begin Patch\n*** End Patch\nPATCH"},
            call_id="call_shell_patch",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["shell_tunneled_apply_patch"] is True
    assert "native apply_patch tool" in validation_error["error"]


def test_execute_agent_calls_rejects_unbounded_smoke_test(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smoke_test.sh, then run timeout 20s bash smoke_test.sh.",
        },
        to_provider=True,
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "sh ./smoke_test.sh"},
            call_id="call_unbounded_smoke",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["unbounded_smoke_test"] is True
    assert "timeout 20s bash smoke_test.sh" in validation_error["error"]


def test_execute_agent_calls_rejects_compound_unbounded_smoke_test_without_write_targets(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Run timeout 20s bash smoke_test.sh and report the result.",
        },
        to_provider=True,
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "make && bash smoke_test.sh"},
            call_id="call_compound_unbounded_smoke",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["unbounded_smoke_test"] is True
    assert validation_error["exit"] == 1


def test_execute_agent_calls_rejects_executable_smoke_test_without_timeout(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Run timeout 20s bash smoke_test.sh before finalizing.",
        },
        to_provider=True,
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "make && chmod +x smoke_test.sh && ./smoke_test.sh"},
            call_id="call_executable_unbounded_smoke",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["unbounded_smoke_test"] is True


def test_execute_agent_calls_rejects_unbounded_smoke_after_internal_retry_prompt(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smoke_test.sh and run timeout 20s bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.add_message(
        {
            "role": "user",
            "content": "<VALIDATION_ERROR>\nPatch failed. Repair the requested files.\n</VALIDATION_ERROR>",
        },
        to_provider=True,
    )
    conductor = SimpleNamespace(
        workspace=str(tmp_path),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args, **_kwargs: None),
        agent_executor=SimpleNamespace(
            allow_multiple_bash=False,
            canonical_tool_name=lambda name: name,
            execute_parsed_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
            is_tool_failure=lambda _name, result: bool((result or {}).get("error")),
        ),
    )
    parsed_calls = [
        SimpleNamespace(
            function="shell_command",
            arguments={"command": "make && bash smoke_test.sh"},
            call_id="call_internal_retry_unbounded_smoke",
        )
    ]

    executed, failed_at, validation_error, meta = execute_agent_calls(
        conductor,
        parsed_calls,
        lambda _call: {"ok": True},
        session_state,
    )

    assert len(executed) == 1
    assert failed_at == 0
    assert meta["executed_calls"] == 0
    assert validation_error["unbounded_smoke_test"] is True


def test_agent_executor_bypasses_enhanced_executor_for_patch_primitives() -> None:
    from agentic_coder_prototype.execution.agent_executor import AgentToolExecutor

    executor = AgentToolExecutor({}, "/tmp")

    class FailingEnhancedExecutor:
        async def execute_tool_call(self, _tool_call, _exec_func):
            return {"ok": False, "stderr": "error: unrecognized input"}

    executor.set_enhanced_executor(FailingEnhancedExecutor())

    result = executor.execute_tool_call(
        {"function": "apply_patch", "arguments": {"input": "*** Begin Patch\n*** End Patch\n"}},
        lambda _tool_call: {"ok": True, "manual_fallback": True},
    )

    assert result == {"ok": True, "manual_fallback": True}


def test_post_receipt_forced_closure_names_successful_verification_commands(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Build smtp_server.c and Makefile, then run make and ./smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_matches": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "is_requested_file_write": True,
        },
        result={"ok": True},
    )
    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make clean all",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    session_state.record_tool_event(
        3,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "bash ./smoke_test.sh",
            "exit_code": 0,
        },
        result={"exit": 0},
    )

    closed = _force_post_receipt_final_answer(
        session_state,
        reason="post_receipt_loop_guard",
    )

    assert closed is True
    final_message = session_state.messages[-1]["content"]
    assert "Verification: make clean all; bash ./smoke_test.sh" in final_message


def test_mark_task_complete_after_receipts_adds_transcript_final_message(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix calc.c cleanly, then run make clean all and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["calc.c"],
            "requested_write_targets": ["calc.c"],
            "requested_write_matches": ["calc.c"],
            "is_requested_file_write": True,
        },
        result={"exit": 0},
    )
    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make clean all && bash smoke_test.sh",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    conductor = SimpleNamespace(
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )

    final_message = _ensure_tool_completion_final_message(
        conductor,
        session_state,
        reason="unit_test_mark_task_complete_after_receipts",
    )

    assert final_message is not None
    assert "Files changed: `calc.c`" in final_message
    assert "Verification: make clean all && bash smoke_test.sh" in final_message
    assert session_state.messages[-1]["role"] == "assistant"


def test_requested_make_clean_all_and_smoke_requires_exact_verification_terms(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix calc.c cleanly, then run make clean all and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["calc.c"],
            "requested_write_targets": ["calc.c"],
            "requested_write_matches": ["calc.c"],
            "is_requested_file_write": True,
        },
        result={"exit": 0},
    )
    conductor = SimpleNamespace(
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )

    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make && ./calc 2 3",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    assert _implementation_verification_receipt_missing(conductor, session_state) is True

    session_state.record_tool_event(
        3,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make clean all",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    assert _implementation_verification_receipt_missing(conductor, session_state) is True

    session_state.record_tool_event(
        4,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "bash smoke_test.sh",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    assert _implementation_verification_receipt_missing(conductor, session_state) is False
    assert _implementation_receipts_satisfied(conductor, session_state) is True


def test_post_receipt_extra_write_tool_forces_final_answer(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix calc.c cleanly, then run make clean all and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["calc.c"],
            "requested_write_targets": ["calc.c"],
            "requested_write_matches": ["calc.c"],
            "is_requested_file_write": True,
        },
        result={"exit": 0},
    )
    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make clean all && bash smoke_test.sh",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    conductor = SimpleNamespace(
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )
    parsed_calls = [SimpleNamespace(function="apply_unified_patch", arguments={"input": "noop"})]
    markdown_logger = SimpleNamespace(log_user_message=lambda *_args, **_kwargs: None)

    closed = _maybe_block_read_only_implementation_loop(
        conductor,
        session_state,
        markdown_logger,
        parsed_calls,
        False,
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is True
    assert session_state.completion_summary["reason"] == "post_receipt_extra_tool_attempt"
    assert "Verification: make clean all && bash smoke_test.sh" in session_state.messages[-1]["content"]


def test_model_run_exact_verification_forces_post_write_closure(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Fix calc.c cleanly, then run make clean all and bash smoke_test.sh.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["calc.c"],
            "requested_write_targets": ["calc.c"],
            "requested_write_matches": ["calc.c"],
            "is_requested_file_write": True,
        },
        result={"exit": 0},
    )
    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "is_test_command": True,
            "command": "make clean all && timeout 20s bash smoke_test.sh",
            "exit_code": 0,
        },
        result={"exit": 0},
    )
    conductor = SimpleNamespace(
        config={
            "workloop_guards": {
                "implementation_write_receipts": {
                    "enabled": True,
                    "auto_verify_make_after_write_receipts": True,
                }
            }
        },
    )

    closed = _maybe_force_post_write_auto_verification_closure(
        conductor,
        session_state,
        reason="unit_test_model_run_exact_verification",
    )

    assert closed is True
    assert session_state.completion_summary["completed"] is True
    assert session_state.completion_summary["reason"] == "unit_test_model_run_exact_verification"
    assert "Verification: make clean all && timeout 20s bash smoke_test.sh" in session_state.messages[-1]["content"]


def test_implementation_prompt_selection_ignores_tool_result_user_relays(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    original = "Fix calc.c cleanly, then run make clean all and bash smoke_test.sh."
    session_state.add_message({"role": "user", "content": original}, to_provider=True)
    session_state.add_message(
        {
            "role": "user",
            "content": "Tool execution results:\napply_patch succeeded for calc.c",
        },
        to_provider=True,
    )

    assert _latest_implementation_prompt(session_state) == original
    assert _latest_prompt_requires_implementation_write(session_state) is True
    assert _latest_prompt_requests_verification(session_state) is True


def test_implementation_prompt_selection_ignores_guard_validation_relays(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    original = "Fix calc.c cleanly, then run make clean all and bash smoke_test.sh."
    session_state.add_message({"role": "user", "content": original}, to_provider=True)
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "<VALIDATION_ERROR>\n"
                "This implementation task is not complete. Run the requested build/smoke verification now.\n"
                f"Original request: {original}\n"
                "</VALIDATION_ERROR>"
            ),
        },
        to_provider=True,
    )

    assert _latest_implementation_prompt(session_state) == original
    assert _latest_prompt_requires_implementation_write(session_state) is True
    assert _latest_prompt_requests_verification(session_state) is True


def test_do_not_rewrite_clause_does_not_disable_implementation_receipts(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    prompt = (
        "You are in a standalone disposable git repo with a tiny C project. "
        "The smoke test currently fails because calc.c has one arithmetic bug. "
        "Inspect only this workspace, fix calc.c cleanly, do not create a replacement main.c/app project, "
        "do not rewrite the Makefile unless absolutely necessary, then run make clean all and bash smoke_test.sh. "
        "When finished, summarize files changed and exact verification commands."
    )
    session_state.add_message({"role": "user", "content": prompt}, to_provider=True)
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "write_targets": ["calc.c"],
            "requested_write_targets": ["calc.c"],
            "requested_write_matches": ["calc.c"],
            "is_requested_file_write": True,
        },
        result={"exit": 0},
    )
    conductor = SimpleNamespace(
        config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}},
    )

    assert _latest_prompt_requires_implementation_write(session_state) is True
    assert _latest_prompt_requests_verification(session_state) is True
    assert _implementation_verification_receipt_missing(conductor, session_state) is True


def test_requested_shell_command_forced_closure_after_exact_success(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Use the shell tool to run `sleep 1 && echo STOP_RETRY_TOOL_READY`, then summarize the command result.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "command": "sleep 1 && echo STOP_RETRY_TOOL_READY",
            "exit_code": 0,
        },
        result={"exit": 0, "stdout": "STOP_RETRY_TOOL_READY\n", "stderr": ""},
    )

    closed = _maybe_force_requested_shell_command_closure(
        session_state,
        reason="requested_shell_command_observed_before_continuation",
    )

    assert closed is True
    assert session_state.completion_summary["method"] == "requested_shell_command_forced_closure"
    assert "STOP_RETRY_TOOL_READY" in session_state.messages[-1]["content"]


def test_requested_shell_command_forced_closure_ignores_implementation_prompt(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Create smoke_test.sh, then run `bash smoke_test.sh`.",
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "run_shell",
        success=True,
        metadata={"is_run_shell": True, "command": "bash smoke_test.sh", "exit_code": 0},
        result={"exit": 0, "stdout": "ok\n"},
    )

    assert _maybe_force_requested_shell_command_closure(
        session_state,
        reason="requested_shell_command_observed_before_continuation",
    ) is False


def test_implementation_receipts_satisfied_requires_requested_smoke_verification(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": "Build smtp_server.c and Makefile, then run make and timeout 20s bash smoke_test.sh.",
        },
        to_provider=True,
    )
    conductor = SimpleNamespace(workspace=str(tmp_path), config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}})
    session_state.record_tool_event(
        1,
        "apply_unified_patch",
        success=True,
        metadata={
            "is_write": True,
            "is_user_facing_write": True,
            "is_requested_file_write": True,
            "write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_targets": ["smtp_server.c", "Makefile", "smoke_test.sh"],
            "requested_write_matches": ["smtp_server.c", "Makefile", "smoke_test.sh"],
        },
        result={"ok": True},
    )
    session_state.record_tool_event(
        2,
        "run_shell",
        success=True,
        metadata={"is_run_shell": True, "is_test_command": True, "command": "make clean all", "exit_code": 0},
        result={"exit": 0},
    )

    assert _implementation_verification_receipt_missing(conductor, session_state) is True
    assert _implementation_receipts_satisfied(conductor, session_state) is False


def test_post_receipt_forced_closure_preserves_requested_final_marker(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "Write agent_notes.md. After that, answer with first line exactly "
                "HARNESS_PARITY_EDIT_OK."
            ),
        },
        to_provider=True,
    )
    session_state.record_tool_event(
        1,
        "run_shell",
        success=True,
        metadata={
            "is_run_shell": True,
            "command": "printf ok > agent_notes.md",
            "exit_code": 0,
            "is_write": True,
            "is_user_facing_write": True,
            "is_requested_file_write": True,
            "write_targets": ["agent_notes.md"],
            "requested_write_targets": ["agent_notes.md"],
            "requested_write_matches": ["agent_notes.md"],
        },
        result={"exit": 0},
    )

    closed = _force_post_receipt_final_answer(
        session_state,
        reason="post_receipt_read_only_or_inspection_attempt",
    )

    assert closed is True
    assert session_state.messages[-1]["content"].startswith("HARNESS_PARITY_EDIT_OK\n")


def test_smoke_test_filename_read_command_is_not_test_command() -> None:
    read_command = (
        "cat AGENTS.md && printf '\\n--- smoke_test.sh ---\\n' "
        "&& sed -n '1,220p' smoke_test.sh"
    )

    assert OpenAIConductor._is_test_command(read_command) is False
    assert OpenAIConductor._is_test_command("bash smoke_test.sh") is True
    assert OpenAIConductor._is_test_command("make clean all && bash ./smoke_test.sh") is True
    assert OpenAIConductor._is_test_command("timeout 20s bash smoke_test.sh") is True


def test_shell_command_cannot_tunnel_apply_patch() -> None:
    conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = conductor_cls.__new__(conductor_cls)
    conductor.config = {}

    result = conductor._exec_raw(
        {
            "function": "shell_command",
            "arguments": {
                "command": "apply_patch <<'PATCH'\n*** Begin Patch\n*** End Patch\nPATCH",
            },
        }
    )

    assert result["exit"] == 126
    assert "native apply_patch tool" in result["error"]


def test_inspection_reporting_prompt_does_not_require_write_receipt(tmp_path: Path) -> None:
    session_state = SessionState(str(tmp_path), None, {})
    session_state.add_message(
        {
            "role": "user",
            "content": (
                "This is an inspection/reporting task, not a bug-fix task. "
                "Do not edit files yet. Use shell_command once to inspect src/widget.ts."
            ),
        },
        to_provider=True,
    )
    assert _latest_prompt_requires_implementation_write(session_state) is False

    edit_state = SessionState(str(tmp_path), None, {})
    edit_state.add_message(
        {
            "role": "user",
            "content": "Now perform one small edit in this same dummy workspace and write agent_notes.md.",
        },
        to_provider=True,
    )
    assert _latest_prompt_requires_implementation_write(edit_state) is True

    verify_state = SessionState(str(tmp_path), None, {})
    verify_state.add_message(
        {
            "role": "user",
            "content": "Verify the edit by using shell_command exactly once to run: pwd && cat agent_notes.md && npm run check.",
        },
        to_provider=True,
    )
    assert _latest_prompt_requires_implementation_write(verify_state) is False


def test_read_only_inspection_prompt_variants_do_not_require_write_receipt(tmp_path: Path) -> None:
    read_only_prompts = [
        "Inspect this dummy workspace and summarize what files exist. Do not edit anything.",
        "Read-only pass: list the files in this workspace and summarize them.",
        "Inspect-only task. Do not modify anything in the repo.",
        "Summarize the code without editing files.",
        "Check the workspace layout. Do not make changes.",
    ]

    for prompt in read_only_prompts:
        session_state = SessionState(str(tmp_path), None, {})
        session_state.add_message({"role": "user", "content": prompt}, to_provider=True)
        assert _latest_prompt_requires_implementation_write(session_state) is False, prompt


def test_implementation_prompt_still_requires_write_receipt(tmp_path: Path) -> None:
    implementation_prompts = [
        "Create a tiny SMTP server in C.",
        "Fix the parser bug in src/parser.py and update the tests.",
        "Write notes.md with a short summary of this workspace.",
    ]

    for prompt in implementation_prompts:
        session_state = SessionState(str(tmp_path), None, {})
        session_state.add_message({"role": "user", "content": prompt}, to_provider=True)
        assert _latest_prompt_requires_implementation_write(session_state) is True, prompt


def test_shell_redirection_write_targets_are_detected() -> None:
    command = (
        "printf '%s\\n' HARNESS_PARITY_EDIT_FILE ALPHA_WIDGET_CONFIRMED "
        "BETA_NOTE_CONFIRMED > agent_notes.md && cat agent_notes.md"
    )

    assert _shell_command_write_targets(command) == ["agent_notes.md"]


def test_run_exact_shell_command_is_not_implicitly_verification(tmp_path: Path) -> None:
    edit_state = SessionState(str(tmp_path), None, {})
    edit_state.add_message(
        {
            "role": "user",
            "content": (
                "Use shell_command exactly once to run this exact command: "
                "printf '%s\\n' HARNESS_PARITY_EDIT_FILE > agent_notes.md && cat agent_notes.md."
            ),
        },
        to_provider=True,
    )
    assert _latest_prompt_requests_verification(edit_state) is False

    verify_state = SessionState(str(tmp_path), None, {})
    verify_state.add_message(
        {"role": "user", "content": "Verify the edit by running npm run check."},
        to_provider=True,
    )
    assert _latest_prompt_requests_verification(verify_state) is True
