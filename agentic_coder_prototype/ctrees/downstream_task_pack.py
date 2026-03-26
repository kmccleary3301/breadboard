from __future__ import annotations

import copy
from typing import Any, Dict, List


_DOWNSTREAM_SCENARIO_MAP = {
    "downstream_interrupt_repair_v1": "continuation_workspace_scope_v1",
    "downstream_dependency_shift_v1": "pilot_dependency_noise_v1",
    "downstream_semantic_pivot_v1": "pilot_semantic_pivot_v1",
    "downstream_subtree_pressure_v1": "pilot_subtree_salience_v1",
}


def build_phase11_downstream_pilot_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "task_id": "downstream_interrupt_repair_v1",
            "family": "interrupted_continuation_repair",
            "description": "Resume a partially completed compiler/runtime repair after interruption and recover the active code path cleanly.",
            "required_signals": ["continuation", "support_selection", "cost_logging"],
            "repeat_count": 2,
            "prompt": (
                "Resume a partially completed compiler/runtime repair in the current workspace. "
                "Recover the active code path quickly, make the minimal correct edit set, and verify the repair cleanly. "
                "Do not restart from scratch or widen scope unnecessarily."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 180000,
            "max_steps": 10,
            "overrides": {"max_iterations": 10},
        },
        {
            "task_id": "downstream_dependency_shift_v1",
            "family": "dependency_aware_continuation",
            "description": "Handle a continuation task where the blocking dependency changes and correct validation evidence must be recovered.",
            "required_signals": ["dependency_shift", "validation_recovery", "blocker_tracking"],
            "repeat_count": 2,
            "prompt": (
                "Continue a coding task where the blocking dependency has changed. "
                "Recover the right validation evidence, update the implementation plan if needed, "
                "and avoid relying on stale dependency assumptions."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 180000,
            "max_steps": 10,
            "overrides": {"max_iterations": 10},
        },
        {
            "task_id": "downstream_semantic_pivot_v1",
            "family": "semantic_pivot",
            "description": "Pivot from one codebase target to another related target without carrying stale support.",
            "required_signals": ["pivot", "wrong_neighbor_suppression", "cost_logging"],
            "repeat_count": 2,
            "prompt": (
                "Pivot from the prior code target to the newly requested related target. "
                "Carry over only the support that still applies, suppress stale neighboring context, "
                "and complete the requested update cleanly."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 180000,
            "max_steps": 10,
            "overrides": {"max_iterations": 10},
        },
        {
            "task_id": "downstream_subtree_pressure_v1",
            "family": "subtree_salience",
            "description": "Continue a task under sibling-context pressure where multiple child nodes compete for support budget.",
            "required_signals": ["subtree_salience", "budget_pressure", "support_selection"],
            "repeat_count": 2,
            "prompt": (
                "Continue the active coding task under heavy sibling-context pressure. "
                "Select the most relevant child context, stay within budget, and finish the targeted change "
                "without dragging in irrelevant sibling work."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 180000,
            "max_steps": 10,
            "overrides": {"max_iterations": 10},
        },
    ]


def build_phase11_downstream_benchmark_tasks(*, expand_repeats: bool = True) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    for task in build_phase11_downstream_pilot_tasks():
        repeat_count = int(task.get("repeat_count") or 1)
        effective_repeats = repeat_count if expand_repeats else 1
        for repeat_index in range(effective_repeats):
            task_id = str(task.get("task_id") or "")
            repeated_id = task_id if not expand_repeats else f"{task_id}__r{repeat_index + 1}"
            payload = {
                "id": repeated_id,
                "base_task_id": task_id,
                "family": str(task.get("family") or ""),
                "prompt": str(task.get("prompt") or task.get("description") or ""),
                "task_type": str(task.get("task_type") or "coding_continuation"),
                "latency_budget_ms": int(task.get("latency_budget_ms") or 180000),
                "max_steps": int(task.get("max_steps") or 8),
                "overrides": copy.deepcopy(task.get("overrides") or {}),
                "repeat_index": repeat_index + 1,
                "required_signals": list(task.get("required_signals") or []),
            }
            tasks.append(payload)
    return tasks


def build_phase12_calibration_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "phase12_calibration_interrupt_focus_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "calibration_interrupt_focus",
            "prompt": (
                "Inspect the current workspace, identify the single active compiler continuity issue, "
                "apply one minimal fix if needed, and verify it with one focused command."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 120000,
            "max_steps": 12,
            "overrides": {"max_iterations": 12},
            "repeat_index": 1,
            "required_signals": ["focused_write", "focused_verify"],
        },
        {
            "id": "phase12_calibration_dependency_focus_v1",
            "base_task_id": "downstream_dependency_shift_v1",
            "family": "calibration_dependency_focus",
            "prompt": (
                "Inspect the dependency-sensitive continuation state, recover the current blocker or validation fact, "
                "and verify the next action with one grounded command."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 120000,
            "max_steps": 12,
            "overrides": {"max_iterations": 12},
            "repeat_index": 1,
            "required_signals": ["dependency_recovery", "grounded_verify"],
        },
        {
            "id": "phase12_calibration_pivot_focus_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "family": "calibration_pivot_focus",
            "prompt": (
                "Inspect the current target pivot request, suppress stale neighboring context, "
                "make the minimal relevant edit or confirm no edit is needed, and ground the decision."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 120000,
            "max_steps": 12,
            "overrides": {"max_iterations": 12},
            "repeat_index": 1,
            "required_signals": ["pivot", "grounded_decision"],
        },
        {
            "id": "phase12_calibration_subtree_focus_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "calibration_subtree_focus",
            "prompt": (
                "Inspect the active subtree pressure case, select only the most relevant child context, "
                "perform one bounded repair step if justified, and verify or explain the result with workspace evidence."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 120000,
            "max_steps": 12,
            "overrides": {"max_iterations": 12},
            "repeat_index": 1,
            "required_signals": ["subtree_focus", "grounded_verify"],
        },
    ]


def build_phase13_closure_floor_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "phase13_floor_interrupt_single_edit_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "closure_floor_interrupt_single_edit",
            "prompt": (
                "Identify the single active interrupted repair target in the current workspace, commit to one edit hypothesis, "
                "make one minimal edit if needed, and verify it with one focused command before finishing."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 90000,
            "max_steps": 8,
            "overrides": {"max_iterations": 8},
            "repeat_index": 1,
            "required_signals": ["single_edit_target", "focused_verify", "grounded_close"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "low",
        },
        {
            "id": "phase13_floor_dependency_single_fact_v1",
            "base_task_id": "downstream_dependency_shift_v1",
            "family": "closure_floor_dependency_single_fact",
            "prompt": (
                "Recover the current dependency-sensitive fact that blocks the next edit, commit to one edit target, "
                "make the smallest justified change, and verify it with one grounded command."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 90000,
            "max_steps": 8,
            "overrides": {"max_iterations": 8},
            "repeat_index": 1,
            "required_signals": ["single_dependency_fact", "focused_verify", "grounded_close"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "low",
        },
        {
            "id": "phase13_floor_pivot_single_target_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "family": "closure_floor_pivot_single_target",
            "prompt": (
                "Discard stale neighboring context, commit to the single active pivot target, make one bounded update if needed, "
                "and ground the result with one focused verification command."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 90000,
            "max_steps": 8,
            "overrides": {"max_iterations": 8},
            "repeat_index": 1,
            "required_signals": ["single_pivot_target", "focused_verify", "grounded_close"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "low",
        },
        {
            "id": "phase13_floor_subtree_single_child_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "closure_floor_subtree_single_child",
            "prompt": (
                "Choose the single most relevant child context, commit to one edit hypothesis, perform one bounded repair step if needed, "
                "and verify or ground the no-change decision with one focused command."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 90000,
            "max_steps": 8,
            "overrides": {"max_iterations": 8},
            "repeat_index": 1,
            "required_signals": ["single_child_focus", "focused_verify", "grounded_close"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "low",
        },
    ]


def build_phase14_executor_probe_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "phase14_probe_single_edit_commit_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "executor_probe_single_edit_commit",
            "probe_kind": "single_edit_commit",
            "prompt": (
                "Identify the one active target file, commit to exactly one edit hypothesis before verification, "
                "make the smallest justified patch, and then verify it with one focused command."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "required_signals": ["commit_edit", "write_before_verify", "focused_verify"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "requires_edit_commitment": True,
        },
        {
            "id": "phase14_probe_no_edit_proof_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "family": "executor_probe_no_edit_proof",
            "probe_kind": "no_edit_proof",
            "prompt": (
                "Inspect the single active target and decide whether a code change is actually required. "
                "If no change is needed, use an explicit no-edit proof path with one grounding command and finish only through that path."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "required_signals": ["no_edit_proof", "grounded_finish"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "requires_no_edit_proof": True,
        },
        {
            "id": "phase14_probe_edit_then_verify_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "executor_probe_edit_then_verify",
            "probe_kind": "edit_then_verify",
            "prompt": (
                "Commit to one bounded edit target, apply one minimal patch, and verify the patch before attempting any finish. "
                "Do not treat inspection alone as completion."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "required_signals": ["commit_edit", "patch_then_verify", "grounded_finish"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "requires_edit_commitment": True,
        },
        {
            "id": "phase14_probe_premature_finish_trap_v1",
            "base_task_id": "downstream_dependency_shift_v1",
            "family": "executor_probe_premature_finish_trap",
            "probe_kind": "premature_finish_trap",
            "prompt": (
                "Do not finish with explanation only. "
                "Either commit to the next concrete edit target and act, or use an explicit no-edit proof path with one grounding command. "
                "A natural-language stop without grounded evidence should be treated as invalid."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "required_signals": ["invalid_finish_rejection", "commit_or_prove", "grounded_finish"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "invalid_finish_trap": True,
        },
    ]


def build_phase15_verification_receipt_probe_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "phase15_probe_edit_receipt_required_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "verification_probe_edit_receipt_required",
            "probe_kind": "edit_receipt_required",
            "prompt": (
                "Commit to one concrete edit target, apply one minimal patch, and only finish after runner-visible verification evidence exists. "
                "Natural-language finish without a patch and verification receipt should be invalid."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "required_signals": ["edit_receipt", "verification_receipt", "finish_receipt"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "closure_mode": "edit_required",
            "requires_edit_commitment": True,
            "required_receipts": ["edit", "verification", "finish"],
        },
        {
            "id": "phase15_probe_explicit_proof_receipt_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "family": "verification_probe_explicit_proof_receipt",
            "probe_kind": "explicit_proof_receipt",
            "prompt": (
                "If no code change is needed, use an explicit no-edit proof path and finish only after runner-visible proof evidence exists. "
                "Shell-only grounding without an accepted proof receipt should not count."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "required_signals": ["proof_receipt", "finish_receipt"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "closure_mode": "proof_required",
            "requires_no_edit_proof": True,
            "required_receipts": ["proof", "finish"],
        },
        {
            "id": "phase15_probe_verify_reentry_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "verification_probe_verify_reentry",
            "probe_kind": "verify_reentry",
            "prompt": (
                "Make one bounded edit, attempt verification, and if the verification evidence is insufficient, re-enter the task instead of finishing. "
                "The run should only close after a valid verification receipt exists."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 7,
            "overrides": {"max_iterations": 7},
            "repeat_index": 1,
            "required_signals": ["edit_receipt", "verification_reentry", "verification_receipt"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "closure_mode": "edit_required",
            "requires_edit_commitment": True,
            "required_receipts": ["edit", "verification", "finish"],
        },
        {
            "id": "phase15_probe_invalid_finish_rejection_v1",
            "base_task_id": "downstream_dependency_shift_v1",
            "family": "verification_probe_invalid_finish_rejection",
            "probe_kind": "invalid_finish_rejection",
            "prompt": (
                "Do not finish with explanation only. Either produce an edit receipt or an explicit proof receipt. "
                "A grounded-looking but receipt-less finish attempt should be rejected by the runner."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "required_signals": ["invalid_finish_rejection", "edit_or_proof_branch", "finish_receipt"],
            "edit_scope": "single_file",
            "verification_scope": "single_command",
            "ambiguity_level": "very_low",
            "closure_mode": "edit_or_proof",
            "invalid_finish_trap": True,
            "required_receipts": ["finish"],
        },
    ]


def build_phase16_action_invocation_probe_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "phase16_probe_single_required_shell_v1",
            "base_task_id": "downstream_dependency_shift_v1",
            "family": "action_invocation_probe_single_required_shell",
            "probe_kind": "single_required_shell",
            "prompt": (
                "Use the available shell tool immediately to inspect the target, then stop. "
                "Do not narrate intent before the first tool call."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 45000,
            "max_steps": 3,
            "overrides": {"max_iterations": 3},
            "repeat_index": 1,
            "required_signals": ["tool_invocation"],
            "allowed_tool_mode": "required",
            "expected_first_tool_family": "shell",
            "edit_scope": "none",
            "verification_scope": "none",
            "ambiguity_level": "very_low",
        },
        {
            "id": "phase16_probe_single_required_patch_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "action_invocation_probe_single_required_patch",
            "probe_kind": "single_required_patch",
            "prompt": (
                "Apply one minimal patch immediately. Do not explain first. "
                "The point of this probe is whether a write tool call occurs at all."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 45000,
            "max_steps": 3,
            "overrides": {"max_iterations": 3},
            "repeat_index": 1,
            "required_signals": ["tool_invocation", "write_attempt"],
            "allowed_tool_mode": "required",
            "expected_first_tool_family": "write",
            "edit_scope": "single_file",
            "verification_scope": "none",
            "ambiguity_level": "very_low",
            "requires_edit_commitment": True,
        },
        {
            "id": "phase16_probe_shell_then_patch_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "action_invocation_probe_shell_then_patch",
            "probe_kind": "shell_then_patch",
            "prompt": (
                "Inspect once with shell, then make one bounded patch. "
                "Do not finish before both tool calls have happened."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 5,
            "overrides": {"max_iterations": 5},
            "repeat_index": 1,
            "required_signals": ["tool_invocation", "write_attempt", "multi_tool_progress"],
            "allowed_tool_mode": "required",
            "expected_first_tool_family": "shell",
            "edit_scope": "single_file",
            "verification_scope": "none",
            "ambiguity_level": "very_low",
            "requires_edit_commitment": True,
        },
        {
            "id": "phase16_probe_invalid_finish_recovery_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "family": "action_invocation_probe_invalid_finish_recovery",
            "probe_kind": "invalid_finish_recovery",
            "prompt": (
                "An explanation-only finish is invalid here. If the first attempt does not use a tool, "
                "recover by calling an available tool before trying to close the task."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 5,
            "overrides": {"max_iterations": 5},
            "repeat_index": 1,
            "required_signals": ["tool_invocation", "invalid_finish_recovery"],
            "allowed_tool_mode": "required",
            "expected_first_tool_family": "shell",
            "edit_scope": "single_file",
            "verification_scope": "none",
            "ambiguity_level": "very_low",
            "invalid_finish_trap": True,
        },
    ]


def build_phase17_branch_receipt_probe_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "phase17_probe_edit_branch_lock_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "branch_receipt_probe_edit_branch_lock",
            "probe_kind": "edit_branch_lock",
            "prompt": (
                "Inspect briefly, choose the edit branch explicitly, make one bounded patch, "
                "record verification, and only then finish."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "closure_mode": "edit_required",
            "required_branch_mode": "edit",
            "required_receipts": ["branch", "edit", "verification", "finish"],
            "requires_edit_commitment": True,
            "allow_shell_branch_proxy": False,
        },
        {
            "id": "phase17_probe_proof_branch_receipt_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "family": "branch_receipt_probe_proof_branch_receipt",
            "probe_kind": "proof_branch_receipt",
            "prompt": (
                "If no code change is needed, choose the proof branch explicitly, gather runner-visible evidence, "
                "record an explicit proof receipt, and finish only through that path."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "closure_mode": "proof_required",
            "required_branch_mode": "proof",
            "required_receipts": ["branch", "proof", "finish"],
            "requires_no_edit_proof": True,
            "allow_shell_branch_proxy": False,
        },
        {
            "id": "phase17_probe_edit_verify_receipt_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "branch_receipt_probe_edit_verify_receipt",
            "probe_kind": "edit_verify_receipt",
            "prompt": (
                "Choose the edit branch explicitly, apply one bounded patch, run a focused verification step, "
                "record the verification receipt, and finish only after that receipt exists."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 7,
            "overrides": {"max_iterations": 7},
            "repeat_index": 1,
            "closure_mode": "edit_required",
            "required_branch_mode": "edit",
            "required_receipts": ["branch", "edit", "verification", "finish"],
            "requires_edit_commitment": True,
            "allow_shell_branch_proxy": False,
        },
        {
            "id": "phase17_probe_shell_proxy_rejection_v1",
            "base_task_id": "downstream_dependency_shift_v1",
            "family": "branch_receipt_probe_shell_proxy_rejection",
            "probe_kind": "shell_proxy_rejection",
            "prompt": (
                "Do not let shell-only evidence stand in for branch choice. "
                "Choose either edit or proof explicitly, then satisfy the matching receipt path before finishing."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 6,
            "overrides": {"max_iterations": 6},
            "repeat_index": 1,
            "closure_mode": "edit_or_proof",
            "required_receipts": ["branch", "finish"],
            "invalid_finish_trap": True,
            "allow_shell_branch_proxy": False,
        },
        {
            "id": "phase17_probe_verification_reentry_receipt_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "branch_receipt_probe_verification_reentry_receipt",
            "probe_kind": "verification_reentry_receipt",
            "prompt": (
                "Choose the edit branch explicitly, make one bounded patch, and if verification is weak, re-enter rather than finish. "
                "Only close once a verification receipt is explicitly recorded."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 75000,
            "max_steps": 8,
            "overrides": {"max_iterations": 8},
            "repeat_index": 1,
            "closure_mode": "edit_required",
            "required_branch_mode": "edit",
            "required_receipts": ["branch", "edit", "verification", "finish"],
            "requires_edit_commitment": True,
            "allow_shell_branch_proxy": False,
        },
    ]


def build_phase18_finish_closure_probe_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "phase18_probe_close_after_edit_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "finish_closure_close_after_edit",
            "probe_kind": "close_after_edit",
            "prompt": (
                "Commit to the edit branch, apply one minimal patch, verify it with one focused command, "
                "and request finish only when the runner-visible closure path is complete."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 7,
            "overrides": {"max_iterations": 7},
            "repeat_index": 1,
            "closure_mode": "edit_required",
            "required_branch_mode": "edit",
            "required_receipts": ["branch", "edit", "verification", "finish"],
            "requires_edit_commitment": True,
        },
        {
            "id": "phase18_probe_close_after_proof_v1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "family": "finish_closure_close_after_proof",
            "probe_kind": "close_after_proof",
            "prompt": (
                "Commit to the proof branch, gather one shell-grounded proof step, record the proof receipt, "
                "and request finish only when the proof path is closure-ready."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 7,
            "overrides": {"max_iterations": 7},
            "repeat_index": 1,
            "closure_mode": "proof_required",
            "required_branch_mode": "proof",
            "required_receipts": ["branch", "proof", "finish"],
            "requires_no_edit_proof": True,
        },
        {
            "id": "phase18_probe_close_after_verify_reentry_v1",
            "base_task_id": "downstream_subtree_pressure_v1",
            "family": "finish_closure_close_after_verify_reentry",
            "probe_kind": "close_after_verify_reentry",
            "prompt": (
                "Commit to one bounded edit, recover cleanly if the first verification is insufficient, "
                "record the needed verification receipt, and then request finish."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 8,
            "overrides": {"max_iterations": 8},
            "repeat_index": 1,
            "closure_mode": "edit_required",
            "required_branch_mode": "edit",
            "required_receipts": ["branch", "edit", "verification", "finish"],
            "requires_edit_commitment": True,
        },
        {
            "id": "phase18_probe_invalid_finish_nearly_ready_v1",
            "base_task_id": "downstream_dependency_shift_v1",
            "family": "finish_closure_invalid_finish_nearly_ready",
            "probe_kind": "invalid_finish_nearly_ready",
            "prompt": (
                "If an early finish is denied, consume the missing-prerequisite signal, satisfy it, "
                "and then request finish through the valid closure path."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 8,
            "overrides": {"max_iterations": 8},
            "repeat_index": 1,
            "closure_mode": "edit_or_proof",
            "required_receipts": ["branch", "finish"],
            "invalid_finish_trap": True,
        },
        {
            "id": "phase18_probe_closure_ready_stall_trap_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "family": "finish_closure_closure_ready_stall_trap",
            "probe_kind": "closure_ready_stall_trap",
            "prompt": (
                "Reach the correct branch-and-receipt state quickly and request finish immediately once the task becomes closure-ready. "
                "Do not narrate through the remaining budget."
            ),
            "task_type": "coding_continuation",
            "latency_budget_ms": 60000,
            "max_steps": 7,
            "overrides": {"max_iterations": 7},
            "repeat_index": 1,
            "closure_mode": "edit_or_proof",
            "required_receipts": ["branch", "finish"],
        },
    ]


def build_phase19_anchor_slice_tasks() -> List[Dict[str, Any]]:
    benchmark = {task["id"]: dict(task) for task in build_phase11_downstream_benchmark_tasks(expand_repeats=True)}
    selected_ids = [
        "downstream_interrupt_repair_v1__r1",
        "downstream_interrupt_repair_v1__r2",
        "downstream_dependency_shift_v1__r1",
        "downstream_dependency_shift_v1__r2",
        "downstream_semantic_pivot_v1__r2",
        "downstream_subtree_pressure_v1__r1",
    ]
    return [benchmark[item_id] for item_id in selected_ids]


def build_phase19_anchor_slice_holdout_tasks() -> List[Dict[str, Any]]:
    benchmark = {task["id"]: dict(task) for task in build_phase11_downstream_benchmark_tasks(expand_repeats=True)}
    selected_ids = [
        "downstream_semantic_pivot_v1__r1",
        "downstream_subtree_pressure_v1__r2",
    ]
    return [benchmark[item_id] for item_id in selected_ids]


def build_phase11_model_axis_matrix() -> Dict[str, Any]:
    return {
        "schema_version": "phase11_model_axis_matrix_v1",
        "mainline_cells": [
            {
                "cell_id": "flagship_downstream_pilot",
                "model_tier": "flagship",
                "systems": ["practical_baseline", "deterministic_reranker", "candidate_a"],
                "task_pack": "phase11_downstream_pilot_v1",
            },
            {
                "cell_id": "gpt54_mini_downstream_pilot",
                "model_tier": "gpt-5.4-mini",
                "systems": ["practical_baseline", "deterministic_reranker", "candidate_a"],
                "task_pack": "phase11_downstream_pilot_v1",
            },
        ],
        "helper_cells": [
            {
                "cell_id": "gpt54_nano_rehydration_helper_pilot",
                "model_tier": "gpt-5.4-nano",
                "systems": ["candidate_a_with_helper_gate"],
                "helper_role": "structured_rehydration_proposal",
                "task_pack": "phase11_downstream_pilot_v1",
                "requires_acceptance_gate": True,
            }
        ],
    }


def resolve_phase11_downstream_base_scenario_id(base_task_id: str) -> str:
    task_id = str(base_task_id or "").strip()
    if task_id not in _DOWNSTREAM_SCENARIO_MAP:
        raise KeyError(f"unknown downstream base task id {task_id}")
    return _DOWNSTREAM_SCENARIO_MAP[task_id]
