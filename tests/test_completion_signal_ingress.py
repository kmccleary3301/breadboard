from __future__ import annotations

from agentic_coder_prototype.orchestration.coordination import (
    build_completion_signal_proposal,
    build_tool_completion_signal_proposal,
    infer_completion_signal_source_kind,
    validate_signal_proposal,
)
from agentic_coder_prototype.state.completion_detector import CompletionDetector


def test_completion_detector_emits_text_sentinel_signal_metadata() -> None:
    detector = CompletionDetector(config={"completion": {"threshold": 0.6}})

    analysis = detector.detect_completion(
        "TASK COMPLETE",
        "stop",
        [],
        {"tools": {"mark_task_complete": True}},
    )

    assert analysis["completed"] is True
    assert analysis["signal_code"] == "complete"
    assert analysis["signal_source_kind"] == "text_sentinel"


def test_completion_detector_emits_provider_signal_metadata() -> None:
    detector = CompletionDetector(config={"completion": {"threshold": 0.6}})

    analysis = detector.detect_completion(
        "All requirements met",
        "stop",
        [],
        {"tools": {"mark_task_complete": True}},
    )

    assert analysis["completed"] is True
    assert analysis["signal_code"] == "complete"
    assert infer_completion_signal_source_kind(analysis["method"], analysis["reason"]) in {
        "provider_finish",
        "assistant_content",
    }


def test_build_completion_signal_from_analysis_round_trips_to_validation() -> None:
    analysis = {
        "completed": True,
        "method": "assistant_content",
        "reason": "explicit_completion_marker",
        "confidence": 0.9,
        "signal_code": "complete",
        "signal_source_kind": "text_sentinel",
    }

    signal = validate_signal_proposal(
        build_completion_signal_proposal(
            analysis,
            task_id="main",
        )
    )

    assert signal["status"] == "accepted"
    assert signal["source"]["kind"] == "text_sentinel"


def test_build_tool_completion_signal_accepts_mark_task_complete() -> None:
    signal = validate_signal_proposal(
        build_tool_completion_signal_proposal(
            task_id="main",
            tool_name="mark_task_complete",
            tool_result={"action": "complete"},
        )
    )

    assert signal["status"] == "accepted"
    assert signal["source"]["kind"] == "tool_call"
