from __future__ import annotations

from agentic_coder_prototype.ctrees.progress_watchdog import evaluate_progress_watchdog


def test_progress_watchdog_triggers_when_streak_meets_threshold() -> None:
    payload = evaluate_progress_watchdog(
        {"controller_metrics": {"no_progress_streak_max": 3}},
        max_no_progress_turns=3,
    )
    assert payload["triggered"] is True


def test_progress_watchdog_stays_off_when_streak_below_threshold() -> None:
    payload = evaluate_progress_watchdog(
        {"controller_metrics": {"no_progress_streak_max": 2}},
        max_no_progress_turns=3,
    )
    assert payload["triggered"] is False


def test_progress_watchdog_exposes_escalation_threshold() -> None:
    payload = evaluate_progress_watchdog(
        {"controller_metrics": {"no_progress_streak_max": 2}},
        max_no_progress_turns=3,
        escalation_after_no_progress=2,
    )
    assert payload["triggered"] is False
    assert payload["escalation_recommended"] is True
