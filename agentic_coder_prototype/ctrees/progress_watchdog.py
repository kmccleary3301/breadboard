from __future__ import annotations

from typing import Any, Dict


def evaluate_progress_watchdog(
    live_summary: Dict[str, Any],
    *,
    max_no_progress_turns: int = 3,
    escalation_after_no_progress: int | None = None,
) -> Dict[str, Any]:
    controller_metrics = dict(live_summary.get("controller_metrics") or {})
    no_progress_streak_max = int(controller_metrics.get("no_progress_streak_max") or 0)
    triggered = no_progress_streak_max >= int(max_no_progress_turns)
    escalation_threshold = int(escalation_after_no_progress or max_no_progress_turns)
    escalation_recommended = no_progress_streak_max >= escalation_threshold
    return {
        "schema_version": "phase12_progress_watchdog_v1",
        "max_no_progress_turns": int(max_no_progress_turns),
        "observed_no_progress_streak_max": no_progress_streak_max,
        "triggered": triggered,
        "escalation_after_no_progress": escalation_threshold,
        "escalation_recommended": escalation_recommended,
    }
