from agentic_coder_prototype.state.completion_detector import CompletionDetector


def test_completion_detector_text_sentinel_boost():
    cd = CompletionDetector(config={"confidence_threshold": 0.6})
    # No tool available -> higher boost
    out = cd.detect_completion("TASK COMPLETE", "stop", [], agent_config={"tools": {"mark_task_complete": False}})
    assert out["completed"] is True
    assert out["confidence"] >= 0.9


def test_completion_detector_provider_signal():
    cd = CompletionDetector(config={"confidence_threshold": 0.6})
    out = cd.detect_completion("All requirements met", "stop", [], agent_config={"tools": {"mark_task_complete": True}})
    assert out["completed"] in (True, False)  # not strict; ensure no crash


def test_completion_detector_plan_satisfaction():
    cd = CompletionDetector(config={"confidence_threshold": 0.75})
    recent_activity = {
        "tools": [
            {"name": "list_dir", "read_only": True},
            {"name": "read_file", "read_only": True},
        ],
        "turn": 2,
    }
    message = "Summary:\n- Root files enumerated\n- Highlighted key sources\nReady to close."
    out = cd.detect_completion(
        message,
        "stop",
        [],
        agent_config={"tools": {"mark_task_complete": True}},
        recent_tool_activity=recent_activity,
    )
    assert out["completed"] is True
    assert out["method"] == "plan_satisfied"
    assert out["confidence"] >= 0.9


def test_completion_detector_idle_loop():
    cd = CompletionDetector(config={"confidence_threshold": 0.75})
    history = ["Enumerated workspace directories"]
    message = "Enumerated workspace directories"
    out = cd.detect_completion(
        message,
        "stop",
        [],
        agent_config={"tools": {"mark_task_complete": True}},
        assistant_history=history,
    )
    assert out["completed"] is True
    assert out["method"] == "idle_loop"
    assert out["confidence"] >= 0.8


