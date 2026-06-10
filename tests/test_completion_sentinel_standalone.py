"""Completion sentinels must match standalone declaration lines, not substrings.

Regression tests for two session-killing failure modes seen in long agentic
runs:

1. An agent `cat`s a tool script or echoes its own task instructions that
   *contain* the marker text ("... then say: task complete") and the session
   terminates on turn 1 with zero work done.
2. A bare finish_reason=stop on a turn with no tool activity (a planning
   preamble) is treated as completion.
"""

from agentic_coder_prototype.state.completion_detector import CompletionDetector


def _detector(**completion_cfg):
    return CompletionDetector(config={"completion": {"threshold": 0.6, **completion_cfg}})


def _detect(text, *, finish="stop", tool_results=None, recent_tool_activity=None, **completion_cfg):
    return _detector(**completion_cfg).detect_completion(
        text,
        finish,
        tool_results,
        agent_config={},
        recent_tool_activity=recent_tool_activity,
    )


def test_marker_alone_completes():
    out = _detect("task complete", recent_tool_activity={"tools": [{"name": "run_shell"}]})
    assert out["completed"] is True
    assert out["reason"] == "explicit_completion_marker"


def test_marker_with_decoration_completes():
    for text in ("Task complete.", "**Task complete!**", "- task complete", "Task complete - all tests pass"):
        out = _detect(text, recent_tool_activity={"tools": [{"name": "run_shell"}]})
        assert out["completed"] is True, text


def test_marker_as_final_line_completes():
    out = _detect(
        "All visible cases pass and the submission is frozen.\n\nTask complete.",
        recent_tool_activity={"tools": [{"name": "run_shell"}]},
    )
    assert out["completed"] is True


def test_marker_embedded_in_instructions_does_not_complete():
    # Echoing task instructions must not end the session (finish=None isolates
    # the sentinel path from finish_reason handling).
    out = _detect(
        "My plan: write candidate.py, run the eval, and when visible passes, "
        "submit and then say: task complete as instructed. Starting now.",
        finish=None,
    )
    assert out["completed"] is False


def test_marker_inside_catted_script_does_not_complete():
    out = _detect(
        "Here is the tool script:\n"
        "```bash\n"
        'echo "If your latest eval passed visible, run submit; otherwise say: task complete and stop."\n'
        "```\n"
        "I will now write the first candidate.",
        finish=None,
    )
    assert out["completed"] is False


def test_custom_sentinel_standalone_only():
    detector = _detector()
    embedded = detector.detect_completion(
        "the runner appends >>>>>> END RESPONSE markers to transcripts, interesting",
        None,
        None,
        agent_config={},
    )
    assert embedded["completed"] is False
    standalone = detector.detect_completion(
        "Done with the migration.\n\n>>>>>> END RESPONSE",
        "stop",
        None,
        agent_config={},
        recent_tool_activity={"tools": [{"name": "run_shell"}]},
    )
    assert standalone["completed"] is True


def test_bare_stop_without_tool_activity_guard_opt_in():
    out = _detect(
        "Here is my plan:\n1. Inspect the workspace\n2. Write the fix",
        require_tool_activity_for_finish_reason=True,
    )
    assert out["completed"] is False
    assert out["reason"] == "finish_reason_stop_without_tool_activity"


def test_bare_stop_without_tool_activity_default_unchanged():
    out = _detect("Here is my plan in prose, considered complete by default.")
    assert out["completed"] is True
    assert out["method"] == "finish_reason"


def test_bare_stop_with_tool_activity_still_completes_with_guard():
    out = _detect(
        "All requirements met.",
        recent_tool_activity={"tools": [{"name": "run_shell", "read_only": False}]},
        require_tool_activity_for_finish_reason=True,
    )
    assert out["completed"] is True
    assert out["method"] == "finish_reason"
