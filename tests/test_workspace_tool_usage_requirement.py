from __future__ import annotations

from types import SimpleNamespace

from agentic_coder_prototype.conductor.components import (
    assistant_claims_workspace_tool_usage,
    latest_real_user_prompt,
    session_requires_workspace_tool_usage,
    should_require_workspace_tool_usage,
)
from agentic_coder_prototype.guardrail.orchestrator import GuardrailOrchestrator
from agentic_coder_prototype.conductor.execution import (
    _async_result_task_id_from_activity,
    _is_allowed_async_result_followup,
    _required_final_answer_marker,
    handle_text_tool_calls,
)


class _FakeSessionState:
    def __init__(self) -> None:
        self._meta = {"require_workspace_tool_usage": True, "current_mode": "build"}
        self.messages: list[dict] = []
        self.provider_messages: list[dict] = []
        self.added_messages: list[tuple[dict, bool]] = []
        self.tool_usage_summary = {"total_calls": 0}

    def set_provider_metadata(self, key, value) -> None:
        self._meta[key] = value

    def get_provider_metadata(self, key, default=None):
        return self._meta.get(key, default)

    def add_message(self, message, to_provider=False) -> None:
        stored = dict(message)
        self.messages.append(stored)
        if to_provider:
            self.provider_messages.append(stored)
        self.added_messages.append((stored, bool(to_provider)))


class _FakeCaller:
    def parse_all(self, content, tool_defs):
        return []


class _FakeConductor:
    config = {}
    class _FakeAgentExecutor:
        def is_tool_failure(self, tool_name, result):
            return bool((result or {}).get("error"))

    agent_executor = _FakeAgentExecutor()
    hook_manager = None

    def _exec_raw(self, call):
        task_id = call["arguments"]["task_id"]
        return {
            "output": f"<task_id>{task_id}</task_id>\n<status>completed</status>\n<output>README.md\npackage.json</output>",
            "__mvi_text_output": f"<task_id>{task_id}</task_id>\n<status>completed</status>\n<output>README.md\npackage.json</output>",
            "status": "completed",
            "task_id": task_id,
        }

    def _synthesize_patch_blocks(self, content):
        return []


class _FakeMarkdownLogger:
    def __init__(self) -> None:
        self.user_messages: list[str] = []

    def log_user_message(self, message: str) -> None:
        self.user_messages.append(message)


def test_should_require_workspace_tool_usage_detects_direct_shell_request() -> None:
    assert should_require_workspace_tool_usage("Run pwd and answer with only the absolute path.") is True
    assert should_require_workspace_tool_usage("List the files in the repo and summarize the top-level layout.") is True


def test_should_require_workspace_tool_usage_allows_pure_conversation() -> None:
    assert should_require_workspace_tool_usage("Hello") is False
    assert should_require_workspace_tool_usage("How are you?") is False
    assert should_require_workspace_tool_usage("Answer in exactly three bullet points: what can you do?") is False


def test_assistant_claims_workspace_tool_usage_detects_bluffing_action_text() -> None:
    assert assistant_claims_workspace_tool_usage("I’ll run `pwd` and return only the absolute path.") is True
    assert assistant_claims_workspace_tool_usage("Running pwd now and will reply with only the absolute path.") is True
    assert assistant_claims_workspace_tool_usage("Hello! How can I help?") is False


def test_handle_text_tool_calls_relays_assistant_text_when_completion_detector_is_unavailable() -> None:
    session_state = _FakeSessionState()
    markdown_logger = _FakeMarkdownLogger()
    result = handle_text_tool_calls(
        _FakeConductor(),
        SimpleNamespace(content="The current path is /tmp/example."),
        _FakeCaller(),
        [],
        session_state,
        completion_detector=None,
        choice_finish_reason="stop",
        markdown_logger=markdown_logger,
        error_handler=None,
        stream_responses=False,
    )

    assert result is False
    assert len(session_state.added_messages) == 2
    assert session_state.added_messages[0] == ({"role": "assistant", "content": "The current path is /tmp/example."}, False)
    assert session_state.added_messages[1] == ({"role": "assistant", "content": "The current path is /tmp/example."}, True)
    assert not markdown_logger.user_messages


def test_handle_text_tool_calls_derives_tool_requirement_from_latest_user_prompt() -> None:
    session_state = _FakeSessionState()
    session_state.set_provider_metadata("require_workspace_tool_usage", False)
    session_state.messages.append(
        {"role": "user", "content": "Run pwd and answer with only the absolute path."}
    )
    markdown_logger = _FakeMarkdownLogger()

    result = handle_text_tool_calls(
        _FakeConductor(),
        SimpleNamespace(content="Running `pwd` now and will reply with only the absolute path."),
        _FakeCaller(),
        [],
        session_state,
        completion_detector=None,
        choice_finish_reason="stop",
        markdown_logger=markdown_logger,
        error_handler=None,
        stream_responses=False,
    )

    assert result is False
    assert session_state.get_provider_metadata("require_workspace_tool_usage") is True
    assert len(session_state.added_messages) == 1
    message, to_provider = session_state.added_messages[0]
    assert to_provider is True
    assert message["role"] == "user"
    assert "requires real workspace interaction" in message["content"]


def test_latest_real_user_prompt_skips_validation_messages() -> None:
    session_state = _FakeSessionState()
    session_state.messages.extend(
        [
            {"role": "user", "content": "Run pwd and answer with only the absolute path."},
            {"role": "user", "content": "<VALIDATION_ERROR>\nretry\n</VALIDATION_ERROR>"},
        ]
    )

    assert latest_real_user_prompt(session_state) == "Run pwd and answer with only the absolute path."


def test_session_requires_workspace_tool_usage_derives_from_latest_user_prompt() -> None:
    session_state = _FakeSessionState()
    session_state.set_provider_metadata("require_workspace_tool_usage", False)
    session_state.messages.append(
        {"role": "user", "content": "Run pwd and answer with only the absolute path."}
    )

    assert session_requires_workspace_tool_usage(session_state) is True
    assert session_state.get_provider_metadata("require_workspace_tool_usage") is True


def test_latest_real_user_prompt_falls_back_to_provider_messages() -> None:
    session_state = _FakeSessionState()
    session_state.messages = [{"role": "assistant", "content": "stub"}]
    session_state.provider_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Run pwd and answer with only the absolute path."},
    ]

    assert latest_real_user_prompt(session_state) == "Run pwd and answer with only the absolute path."


def test_completion_guard_blocks_zero_tool_bluff_when_assistant_claims_workspace_action() -> None:
    class _FakeCoordinator:
        def todo_config(self):
            return {"enabled": False, "strict": False, "reset_streak_on_todo": False}

    orchestrator = GuardrailOrchestrator(
        config={"completion": {"allow_zero_tool_completion": True}},
        guardrail_coordinator=_FakeCoordinator(),
        plan_bootstrapper=None,  # type: ignore[arg-type]
        completion_guard_abort_threshold=1,
        completion_guard_handler=None,
        zero_tool_warn_turns=1,
        zero_tool_abort_turns=2,
        zero_tool_warn_message="warn",
        zero_tool_abort_message="abort",
        zero_tool_emit_event=False,
    )
    session_state = _FakeSessionState()
    session_state.set_provider_metadata("require_workspace_tool_usage", False)
    session_state.messages.append(
        {"role": "user", "content": "Run pwd and answer with only the absolute path."}
    )
    session_state.set_provider_metadata(
        "assistant_text_history",
        ["I’ll run `pwd` and return only the absolute path."],
    )

    ok, reason = orchestrator.completion_guard_check(session_state)

    assert ok is False
    assert reason is not None
    assert "requires real workspace interaction" in reason


def test_exactly_once_guard_allows_taskoutput_after_async_task_launch() -> None:
    prior_tool_activity = {"tools": [{"name": "task"}], "turn": 1}
    parsed_calls = [SimpleNamespace(function="taskoutput", provider_name="TaskOutput")]

    assert _is_allowed_async_result_followup(parsed_calls, prior_tool_activity) is True


def test_exactly_once_guard_still_blocks_unrelated_tool_after_task_launch() -> None:
    prior_tool_activity = {"tools": [{"name": "task"}], "turn": 1}
    parsed_calls = [SimpleNamespace(function="shell_command", provider_name="shell_command")]

    assert _is_allowed_async_result_followup(parsed_calls, prior_tool_activity) is False


def test_async_task_id_is_preserved_from_recent_tool_activity_metadata() -> None:
    prior_tool_activity = {
        "tools": [
            {
                "name": "task",
                "meta": {"async_task_id": "abc1234", "sessionId": "child-session"},
            }
        ],
        "turn": 1,
    }

    assert _async_result_task_id_from_activity(prior_tool_activity) == "abc1234"


def test_post_async_task_final_answer_requires_taskoutput_before_marker_completion() -> None:
    session_state = _FakeSessionState()
    session_state.messages.append(
        {
            "role": "user",
            "content": (
                "Use the task tool exactly once with run_in_background true. "
                "After launching it, wait for the task result if needed, then answer with LIVE_DONE."
            ),
        }
    )
    session_state.set_provider_metadata(
        "recent_tool_activity",
        {
            "tools": [
                {
                    "name": "task",
                    "meta": {"async_task_id": "abc1234"},
                }
            ],
            "turn": 1,
        },
    )
    markdown_logger = _FakeMarkdownLogger()

    result = handle_text_tool_calls(
        _FakeConductor(),
        SimpleNamespace(content="LIVE_DONE\nThe task completed."),
        _FakeCaller(),
        [],
        session_state,
        completion_detector=None,
        choice_finish_reason="stop",
        markdown_logger=markdown_logger,
        error_handler=None,
        stream_responses=False,
    )

    assert result is False
    assert session_state.added_messages[-1][1] is True
    assert "<ASYNC_TASK_RESULT>" in session_state.added_messages[-1][0]["content"]
    assert "TaskOutput" in session_state.added_messages[-1][0]["content"]
    assert "abc1234" in session_state.added_messages[-1][0]["content"]
    assert "package.json" in session_state.added_messages[-1][0]["content"]


def test_exact_marker_phrase_is_extracted_and_enforced_after_taskoutput() -> None:
    session_state = _FakeSessionState()
    session_state.messages.append(
        {
            "role": "user",
            "content": "Use the task tool exactly once. After launching it, wait for the task result if needed, then answer with the exact marker LIVE_MULTIAGENT_REPO_SCAN_DONE and a one-sentence summary.",
        }
    )
    session_state.set_provider_metadata(
        "recent_tool_activity",
        {
            "tools": [{"name": "TaskOutput", "meta": {"async_task_id": "abc1234"}}],
            "turn": 1,
        },
    )
    markdown_logger = _FakeMarkdownLogger()

    assert _required_final_answer_marker(session_state) == "LIVE_MULTIAGENT_REPO_SCAN_DONE"
    result = handle_text_tool_calls(
        _FakeConductor(),
        SimpleNamespace(content="Repo root contains README.md and package.json."),
        _FakeCaller(),
        [],
        session_state,
        completion_detector=None,
        choice_finish_reason="stop",
        markdown_logger=markdown_logger,
        error_handler=None,
        stream_responses=False,
    )

    assert result is False
    assert session_state.added_messages[-1][1] is True
    assert "LIVE_MULTIAGENT_REPO_SCAN_DONE" in session_state.added_messages[-1][0]["content"]
