from agentic_coder_prototype.ctrees.phase13_runtime_audit import (
    build_phase13_runtime_surface_audit,
    classify_phase13_loop_subfamily,
)


def test_classify_phase13_loop_subfamily_marks_early_verify_no_edit() -> None:
    payload = classify_phase13_loop_subfamily(
        {
            "failure_family": "read_heavy_no_closure_loop",
            "completed": False,
            "grounded_completion": False,
            "verified_completion": False,
            "ungrounded_stop": False,
            "first_write_step": None,
            "first_verify_step": 1,
            "watchdog_triggered": True,
            "reason": "max_steps_exhausted",
        }
    )
    assert payload["loop_subfamily"] == "early_verify_no_edit"


def test_classify_phase13_loop_subfamily_marks_premature_stop() -> None:
    payload = classify_phase13_loop_subfamily(
        {
            "failure_family": "premature_natural_language_stop",
            "completed": True,
            "grounded_completion": False,
            "verified_completion": False,
            "ungrounded_stop": True,
            "first_write_step": None,
            "first_verify_step": None,
            "watchdog_triggered": False,
            "reason": "finish_reason:stop",
        }
    )
    assert payload["loop_subfamily"] == "premature_natural_language_stop"


def test_build_phase13_runtime_surface_audit_flags_zero_grounded_anchor() -> None:
    payload = build_phase13_runtime_surface_audit()
    assert payload["runner_adequacy_summary"]["anchor_all_systems_zero_grounded"] is True
    assert payload["runner_adequacy_summary"]["calibration_all_runtime_variants_zero_grounded"] is True
    candidate_v2 = payload["anchor_system_summaries"]["candidate_a_control_v2"]
    assert candidate_v2["early_verify_no_edit_count"] >= 1
    assert candidate_v2["first_write_missing_count"] >= 1
