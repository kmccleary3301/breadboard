from __future__ import annotations

from typing import Any, Dict


def evaluate_action_budget(
    live_summary: Dict[str, Any],
    *,
    max_inspection_turns: int = 2,
    mandatory_write_by_turn: int = 4,
    escalation_after_no_progress: int = 2,
) -> Dict[str, Any]:
    controller_metrics = dict(live_summary.get("controller_metrics") or {})
    tool_counts = dict(live_summary.get("tool_counts") or {})

    first_write_step = controller_metrics.get("first_write_step")
    first_verify_step = controller_metrics.get("first_verify_step")
    no_progress_streak_max = int(controller_metrics.get("no_progress_streak_max") or 0)
    write_calls = int(tool_counts.get("write_calls") or 0)
    test_commands = int(tool_counts.get("test_commands") or 0)
    successful_tests = int(tool_counts.get("successful_tests") or 0)

    inspection_over_budget = (
        first_write_step is None
        and first_verify_step is not None
        and int(first_verify_step) <= int(max_inspection_turns)
        and no_progress_streak_max > int(max_inspection_turns)
    )
    mandatory_write_missed = first_write_step is None or int(first_write_step) > int(mandatory_write_by_turn)
    verification_missing = write_calls > 0 and test_commands == 0 and successful_tests == 0
    escalation_recommended = (
        no_progress_streak_max >= int(escalation_after_no_progress)
        and first_write_step is None
    )
    escalation_stage = "none"
    if escalation_recommended:
        escalation_stage = "write_or_verify_now"
    if no_progress_streak_max >= int(mandatory_write_by_turn) and first_write_step is None:
        escalation_stage = "forced_closure_attempt"

    return {
        "schema_version": "phase12_action_budget_v1",
        "max_inspection_turns": int(max_inspection_turns),
        "mandatory_write_by_turn": int(mandatory_write_by_turn),
        "escalation_after_no_progress": int(escalation_after_no_progress),
        "inspection_over_budget": bool(inspection_over_budget),
        "mandatory_write_missed": bool(mandatory_write_missed),
        "verification_missing": bool(verification_missing),
        "escalation_recommended": bool(escalation_recommended),
        "escalation_stage": escalation_stage,
    }
