from agentic_coder_prototype.ctrees.closure_rule_registry import build_phase15_task_closure_rule_map
from agentic_coder_prototype.ctrees.receipt_scoring import score_row_with_receipts


def test_phase15_probe_task_rules_are_present_in_registry() -> None:
    rules = build_phase15_task_closure_rule_map()

    assert rules["phase15_probe_edit_receipt_required_v1"]["closure_mode"] == "edit_required"
    assert rules["phase15_probe_explicit_proof_receipt_v1"]["closure_mode"] == "proof_required"
    assert rules["phase15_probe_invalid_finish_rejection_v1"]["invalid_finish_trap"] is True


def test_phase11_anchor_task_rules_are_present_in_registry() -> None:
    rules = build_phase15_task_closure_rule_map()

    assert rules["downstream_interrupt_repair_v1__r1"]["closure_mode"] == "edit_required"
    assert rules["downstream_dependency_shift_v1__r1"]["closure_mode"] == "edit_or_proof"
    assert rules["downstream_semantic_pivot_v1__r2"]["closure_mode"] == "edit_required"
    assert rules["downstream_subtree_pressure_v1__r2"]["closure_mode"] == "edit_required"


def test_edit_required_row_needs_patch_for_receipt_valid_completion() -> None:
    rules = build_phase15_task_closure_rule_map()
    row = {
        "id": "phase14_probe_single_edit_commit_v1",
        "completion_summary": {"completed": True, "reason": "explicit_completion_marker"},
        "grounded_summary": {
            "tool_counts": {
                "successful_writes": 0,
                "successful_tests": 0,
                "test_commands": 0,
                "run_shell_calls": 1,
            },
            "controller_metrics": {"verified_completion": False},
            "grounded": {"grounded_completion": True},
            "classification": {"failure_family": "grounded_completion"},
        },
    }

    scored = score_row_with_receipts(row, closure_rule_map=rules)

    assert scored["legacy_grounded_completion"] is True
    assert scored["receipt_outcome"]["receipt_valid_completion"] is False
    assert scored["receipt_outcome"]["verified_completion_v2"] is False


def test_edit_required_row_with_patch_and_test_is_verified() -> None:
    rules = build_phase15_task_closure_rule_map()
    row = {
        "id": "phase14_probe_edit_then_verify_v1",
        "completion_summary": {"completed": True, "reason": "mark_task_complete"},
        "grounded_summary": {
            "tool_counts": {
                "successful_writes": 1,
                "successful_tests": 1,
                "test_commands": 1,
                "run_shell_calls": 1,
            },
            "controller_metrics": {"verified_completion": True, "first_write_step": 1, "first_verify_step": 2},
            "grounded": {"grounded_completion": True},
            "classification": {"failure_family": "grounded_completion"},
        },
    }

    scored = score_row_with_receipts(row, closure_rule_map=rules)

    assert scored["receipt_outcome"]["receipt_valid_completion"] is True
    assert scored["receipt_outcome"]["verified_completion_v2"] is True


def test_proof_required_row_without_explicit_receipt_collapses_under_v2() -> None:
    rules = build_phase15_task_closure_rule_map()
    row = {
        "id": "phase14_probe_no_edit_proof_v1",
        "completion_summary": {"completed": True, "reason": "mark_task_complete"},
        "grounded_summary": {
            "tool_counts": {
                "successful_writes": 0,
                "successful_tests": 0,
                "test_commands": 0,
                "run_shell_calls": 1,
            },
            "controller_metrics": {"verified_completion": False, "first_write_step": None, "first_verify_step": 1},
            "grounded": {"grounded_completion": True},
            "classification": {"failure_family": "grounded_completion"},
        },
    }

    scored = score_row_with_receipts(row, closure_rule_map=rules)

    assert scored["legacy_grounded_completion"] is True
    assert scored["receipt_signals"]["proof_like_evidence_observed"] is True
    assert scored["receipt_signals"]["explicit_proof_receipt_observed"] is False
    assert scored["receipt_outcome"]["receipt_valid_completion"] is False


def test_invalid_finish_trap_marks_finish_without_receipts_invalid() -> None:
    rules = build_phase15_task_closure_rule_map()
    row = {
        "id": "phase14_probe_premature_finish_trap_v1",
        "completion_summary": {"completed": True, "reason": "explicit_completion_marker"},
        "grounded_summary": {
            "tool_counts": {
                "successful_writes": 0,
                "successful_tests": 0,
                "test_commands": 0,
                "run_shell_calls": 0,
            },
            "controller_metrics": {"verified_completion": False},
            "grounded": {"grounded_completion": False, "ungrounded_stop": True},
            "classification": {"failure_family": "premature_natural_language_stop"},
        },
    }

    scored = score_row_with_receipts(row, closure_rule_map=rules)

    assert scored["receipt_outcome"]["accepted_invalid_finish"] is True


def test_phase15_proof_required_row_needs_explicit_receipt_even_when_grounded() -> None:
    rules = build_phase15_task_closure_rule_map()
    row = {
        "id": "phase15_probe_explicit_proof_receipt_v1",
        "completion_summary": {"completed": True, "reason": "mark_task_complete"},
        "grounded_summary": {
            "tool_counts": {
                "successful_writes": 0,
                "successful_tests": 0,
                "test_commands": 0,
                "run_shell_calls": 1,
            },
            "controller_metrics": {"verified_completion": False, "first_write_step": None, "first_verify_step": 1},
            "grounded": {"grounded_completion": True},
            "classification": {"failure_family": "grounded_completion"},
        },
    }

    scored = score_row_with_receipts(row, closure_rule_map=rules)

    assert scored["closure_rule"]["closure_mode"] == "proof_required"
    assert scored["legacy_grounded_completion"] is True
    assert scored["receipt_outcome"]["receipt_valid_completion"] is False
    assert scored["receipt_outcome"]["verified_completion_v2"] is False
