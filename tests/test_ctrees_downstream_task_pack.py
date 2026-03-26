from __future__ import annotations

from agentic_coder_prototype.ctrees.downstream_task_pack import (
    build_phase11_downstream_benchmark_tasks,
    build_phase11_downstream_pilot_tasks,
    build_phase11_model_axis_matrix,
    build_phase12_calibration_tasks,
    build_phase13_closure_floor_tasks,
    build_phase14_executor_probe_tasks,
    build_phase15_verification_receipt_probe_tasks,
    build_phase17_branch_receipt_probe_tasks,
    build_phase18_finish_closure_probe_tasks,
    build_phase19_anchor_slice_holdout_tasks,
    build_phase19_anchor_slice_tasks,
)


def test_phase11_downstream_pilot_pack_has_four_families() -> None:
    tasks = build_phase11_downstream_pilot_tasks()

    assert len(tasks) == 4
    assert {task["family"] for task in tasks} == {
        "interrupted_continuation_repair",
        "dependency_aware_continuation",
        "semantic_pivot",
        "subtree_salience",
    }


def test_phase11_model_axis_matrix_orders_mini_before_nano_helper_cells() -> None:
    matrix = build_phase11_model_axis_matrix()

    assert matrix["schema_version"] == "phase11_model_axis_matrix_v1"
    assert [cell["model_tier"] for cell in matrix["mainline_cells"]] == ["flagship", "gpt-5.4-mini"]
    assert matrix["helper_cells"][0]["model_tier"] == "gpt-5.4-nano"
    assert matrix["helper_cells"][0]["requires_acceptance_gate"] is True


def test_phase11_benchmark_tasks_expand_repeats_and_prompts() -> None:
    tasks = build_phase11_downstream_benchmark_tasks(expand_repeats=True)

    assert len(tasks) == 8
    assert tasks[0]["base_task_id"] == "downstream_interrupt_repair_v1"
    assert tasks[0]["id"] == "downstream_interrupt_repair_v1__r1"
    assert tasks[1]["id"] == "downstream_interrupt_repair_v1__r2"
    assert all(isinstance(task["prompt"], str) and task["prompt"] for task in tasks)


def test_phase12_calibration_pack_has_four_single_run_tasks() -> None:
    tasks = build_phase12_calibration_tasks()

    assert len(tasks) == 4
    assert {task["base_task_id"] for task in tasks} == {
        "downstream_interrupt_repair_v1",
        "downstream_dependency_shift_v1",
        "downstream_semantic_pivot_v1",
        "downstream_subtree_pressure_v1",
    }
    assert all(int(task["max_steps"]) == 12 for task in tasks)


def test_phase13_closure_floor_pack_has_four_low_ambiguity_tasks() -> None:
    tasks = build_phase13_closure_floor_tasks()

    assert len(tasks) == 4
    assert all(task["edit_scope"] == "single_file" for task in tasks)
    assert all(task["verification_scope"] == "single_command" for task in tasks)
    assert all(task["ambiguity_level"] == "low" for task in tasks)
    assert all(int(task["max_steps"]) == 8 for task in tasks)


def test_phase14_executor_probe_pack_has_four_ultra_narrow_tasks() -> None:
    tasks = build_phase14_executor_probe_tasks()

    assert len(tasks) == 4
    assert {task["probe_kind"] for task in tasks} == {
        "single_edit_commit",
        "no_edit_proof",
        "edit_then_verify",
        "premature_finish_trap",
    }
    assert all(task["edit_scope"] == "single_file" for task in tasks)
    assert all(task["verification_scope"] == "single_command" for task in tasks)
    assert all(task["ambiguity_level"] == "very_low" for task in tasks)
    assert all(int(task["max_steps"]) == 6 for task in tasks)
    assert any(task.get("requires_no_edit_proof") is True for task in tasks)
    assert any(task.get("invalid_finish_trap") is True for task in tasks)


def test_phase15_verification_receipt_probe_pack_has_task_aware_receipt_rules() -> None:
    tasks = build_phase15_verification_receipt_probe_tasks()

    assert len(tasks) == 4
    assert {task["probe_kind"] for task in tasks} == {
        "edit_receipt_required",
        "explicit_proof_receipt",
        "verify_reentry",
        "invalid_finish_rejection",
    }
    assert all(task["ambiguity_level"] == "very_low" for task in tasks)
    assert any(task.get("closure_mode") == "proof_required" for task in tasks)
    assert any(task.get("closure_mode") == "edit_or_proof" for task in tasks)
    assert all(isinstance(task.get("required_receipts"), list) and task["required_receipts"] for task in tasks)


def test_phase17_branch_receipt_probe_pack_has_five_branch_receipt_tasks() -> None:
    tasks = build_phase17_branch_receipt_probe_tasks()

    assert len(tasks) == 5
    assert {task["probe_kind"] for task in tasks} == {
        "edit_branch_lock",
        "proof_branch_receipt",
        "edit_verify_receipt",
        "shell_proxy_rejection",
        "verification_reentry_receipt",
    }
    assert all(isinstance(task.get("required_receipts"), list) and task["required_receipts"] for task in tasks)
    assert all(task.get("allow_shell_branch_proxy") is False for task in tasks)


def test_phase18_finish_closure_probe_pack_has_five_closure_tasks() -> None:
    tasks = build_phase18_finish_closure_probe_tasks()

    assert len(tasks) == 5
    assert {task["probe_kind"] for task in tasks} == {
        "close_after_edit",
        "close_after_proof",
        "close_after_verify_reentry",
        "invalid_finish_nearly_ready",
        "closure_ready_stall_trap",
    }
    assert all(isinstance(task.get("required_receipts"), list) and task["required_receipts"] for task in tasks)
    assert any(task.get("invalid_finish_trap") is True for task in tasks)


def test_phase19_anchor_slice_and_holdout_cover_expected_rows() -> None:
    slice_tasks = build_phase19_anchor_slice_tasks()
    holdout_tasks = build_phase19_anchor_slice_holdout_tasks()

    assert [task["id"] for task in slice_tasks] == [
        "downstream_interrupt_repair_v1__r1",
        "downstream_interrupt_repair_v1__r2",
        "downstream_dependency_shift_v1__r1",
        "downstream_dependency_shift_v1__r2",
        "downstream_semantic_pivot_v1__r2",
        "downstream_subtree_pressure_v1__r1",
    ]
    assert [task["id"] for task in holdout_tasks] == [
        "downstream_semantic_pivot_v1__r1",
        "downstream_subtree_pressure_v1__r2",
    ]
