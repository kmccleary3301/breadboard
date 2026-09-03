from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import breadboard_engine.conductor.turn_runtime as turn_runtime
from breadboard_engine.conductor.components import maybe_run_plan_bootstrap
from breadboard_engine.conductor.tool_executor import ToolExecutor
from breadboard_engine.conductor.turn_runtime import AgentRuntime, PreparedProviderExchange, TurnPolicy


class _StubSessionState:
    def __init__(self) -> None:
        self._meta: Dict[str, Any] = {}

    def get_provider_metadata(self, key: str, default: Any = None) -> Any:
        return self._meta.get(key, default)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self._meta[key] = value


class _StubGuardrailOrchestrator:
    def __init__(self) -> None:
        self.called = False

    def maybe_run_plan_bootstrap(self, session_state: Any, markdown_logger: Any, exec_func: Callable[..., Any]) -> None:
        self.called = True
        exec_func(
            [SimpleNamespace(function="todo.write_board", arguments={"todos": []})],
            transcript_callback=lambda _: None,
            policy_bypass=False,
        )


def test_maybe_run_plan_bootstrap_uses_tool_executor_owner() -> None:
    session_state = _StubSessionState()
    orchestrator = _StubGuardrailOrchestrator()

    called: Dict[str, Any] = {"execute_parsed_calls": False}

    class _StubAgentExecutor:
        allow_multiple_bash = False

        def execute_parsed_calls(
            self,
            parsed_calls: List[Any],
            exec_func: Callable[[Dict[str, Any]], Dict[str, Any]],
            *,
            transcript_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
            policy_bypass: bool = False,
        ):
            called["execute_parsed_calls"] = True
            assert len(parsed_calls) == 1
            assert callable(exec_func)
            assert policy_bypass is False
            return [], -1, None, {}

        @staticmethod
        def canonical_tool_name(name: str) -> str:
            return name

        @staticmethod
        def is_tool_failure(_name: str, _result: Dict[str, Any]) -> bool:
            return False

    conductor = SimpleNamespace(
        guardrail_orchestrator=orchestrator,
        agent_executor=_StubAgentExecutor(),
        permission_broker=SimpleNamespace(ensure_allowed=lambda *_args: None),
        workspace=".",
    )

    maybe_run_plan_bootstrap(conductor, session_state)
    assert orchestrator.called is True
    assert called["execute_parsed_calls"] is True


def test_turn_policy_snapshots_completion_and_relay_decisions() -> None:
    policy = TurnPolicy.from_config(
        {
            "completion": {"allow_zero_tool_completion": True},
            "turn_strategy": {
                "flow": "tool_role",
                "relay": "provider_native",
            },
        }
    )

    assert policy.allows_zero_tool_completion() is True
    assert policy.relay_flow() == "tool_role"
    assert policy.relay_strategy() == "provider_native"
    assert policy.is_completion_action("mark_task_complete", {}) is True
    assert policy.is_completion_action("read_file", {"action": "complete"}) is True
    assert policy.completion_method("native") == "tool_completion_action"
    assert policy.completion_reason("text", "mark_task_complete") == "mark_task_complete"

def test_tool_executor_shapes_results_at_the_owner_seam() -> None:
    conductor = SimpleNamespace(
        agent_executor=SimpleNamespace(
            is_tool_failure=lambda _name, result: bool(result.get("error"))
        )
    )
    executor = ToolExecutor(
        conductor=conductor,
        session_state=SimpleNamespace(),
        exec_func=lambda _call: {"ok": True},
        execute_calls=lambda *_args, **_kwargs: ([], -1, None, {}),
    )
    parsed = SimpleNamespace(
        function="read_file",
        provider_name="read",
        arguments={"path": "README.md"},
        call_id="call-1",
    )

    assert executor.shape_results([(parsed, {"ok": True})]) == [
        {
            "fn": "read_file",
            "provider_fn": "read",
            "out": {"ok": True},
            "args": {"path": "README.md"},
            "call_id": "call-1",
            "failed": False,
        }
    ]


class _RuntimeSessionState:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.provider_metadata: dict[str, Any] = {"current_turn_index": 1}
        self.completion_summary: dict[str, Any] = {}
        self.tool_usage_summary: dict[str, Any] = {"total_calls": 0}
        self.turn_tool_usage: dict[int, dict[str, Any]] = {1: {"tools": []}}
        self.reward_metrics: dict[str, Any] = {}

    def add_message(self, message: dict[str, Any], *, to_provider: bool = True) -> None:
        self.messages.append({**message, "to_provider": to_provider})

    def get_provider_metadata(self, key: str, default: Any = None) -> Any:
        return self.provider_metadata.get(key, default)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self.provider_metadata[key] = value

    def add_reward_metric(self, _turn: int, name: str, value: Any) -> None:
        self.reward_metrics[name] = value


def _runtime_exchange(calls: list[Any]) -> PreparedProviderExchange:
    return PreparedProviderExchange(
        provider_message={},
        parsed_calls=calls,
        assistant_message={"role": "assistant", "content": "calling tool"},
        provider_assistant_message={"role": "assistant", "content": "provider call"},
        model="test",
        dialect_selection=("custom-pythonic",),
        input_kind="text",
        transcript_entry={"role": "assistant", "content": "calling tool"},
    )


def _neutralize_runtime_branches(
    monkeypatch: Any,
    turn_context: Any,
    *,
    guarded_calls: list[Any] | None = None,
    recent_tools: list[dict[str, Any]] | None = None,
    test_success: float | None = None,
) -> None:
    monkeypatch.setattr(
        turn_runtime, "_maybe_block_read_only_implementation_loop", lambda *_args: False
    )
    monkeypatch.setattr(
        turn_runtime, "_maybe_force_read_only_observation_closure", lambda *_args: False
    )
    monkeypatch.setattr(turn_runtime, "build_turn_context", lambda *_args: turn_context)
    monkeypatch.setattr(
        turn_runtime,
        "apply_turn_guards",
        lambda _conductor, _context, _state: (
            list(_context.parsed_calls)
            if guarded_calls is None
            else list(guarded_calls)
        ),
    )
    monkeypatch.setattr(turn_runtime, "handle_blocked_calls", lambda *_args: None)
    monkeypatch.setattr(
        turn_runtime,
        "summarize_execution_results",
        lambda *_args: (list(recent_tools or []), test_success),
    )
    for name in (
        "_maybe_force_post_write_auto_verification_closure",
        "_maybe_force_requested_shell_command_closure",
        "_force_failed_verification_final_answer",
        "_force_failed_write_final_answer",
    ):
        monkeypatch.setattr(turn_runtime, name, lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        turn_runtime, "finalize_turn_context_snapshot", lambda *_args: None
    )
    monkeypatch.setattr(turn_runtime, "maybe_transition_plan_mode", lambda *_args: None)


def _agent_runtime(
    *,
    result_entry: dict[str, Any],
    execution_error: dict[str, Any] | None = None,
) -> AgentRuntime:
    parsed_call = SimpleNamespace(function=result_entry["fn"])
    batch = SimpleNamespace(
        executed_results=[(parsed_call, result_entry["out"])],
        failed_at_index=-1,
        execution_error=execution_error,
        plan_metadata={"total_calls": 1, "executed_calls": 1},
    )
    executor = SimpleNamespace(
        execute=lambda *_args, **_kwargs: batch,
        shape_results=lambda _results: [result_entry],
    )
    conductor = SimpleNamespace(
        _expand_multi_file_patches=lambda calls, *_args: calls,
        provider_metrics=SimpleNamespace(add_concurrency_sample=lambda **_kwargs: None),
        _record_lsp_reward_metrics=lambda *_args: None,
        _record_test_reward_metric=lambda state, turn, value: state.add_reward_metric(
            turn, "TPF_DELTA", value
        ),
        _emit_todo_guard_violation=lambda *_args, **_kwargs: None,
    )
    return AgentRuntime(
        conductor=conductor,
        policy=TurnPolicy.from_config({}),
        tool_executor=executor,
        event_sink=lambda _event: None,
        log_sink=SimpleNamespace(),
    )


def test_agent_runtime_retains_assistant_before_block_feedback(monkeypatch: Any) -> None:
    call = SimpleNamespace(function="read_file")
    context = SimpleNamespace(parsed_calls=[call], blocked_calls=[])
    _neutralize_runtime_branches(monkeypatch, context, guarded_calls=[])
    state = _RuntimeSessionState()
    observed_messages: list[dict[str, Any]] = []

    def observe_blocked(*_args: Any) -> None:
        observed_messages.extend(state.messages)

    monkeypatch.setattr(turn_runtime, "handle_blocked_calls", observe_blocked)
    runtime = _agent_runtime(result_entry={"fn": "read_file", "out": {"ok": True}})

    assert runtime.run(
        _runtime_exchange([call]),
        session_state=state,
        error_handler=SimpleNamespace(),
        stream_responses=False,
        relay_results=lambda **_kwargs: None,
    ) is False
    assert [message["content"] for message in observed_messages] == [
        "calling tool",
        "provider call",
    ]


def test_agent_runtime_records_rewards_before_accepted_completion(
    monkeypatch: Any,
) -> None:
    call = SimpleNamespace(function="mark_task_complete")
    context = SimpleNamespace(parsed_calls=[call], blocked_calls=[])
    _neutralize_runtime_branches(monkeypatch, context, test_success=1.0)
    state = _RuntimeSessionState()
    state.tool_usage_summary["total_calls"] = 1
    events: list[str] = []
    runtime = _agent_runtime(
        result_entry={"fn": "mark_task_complete", "out": {"ok": True}}
    )
    monkeypatch.setattr(
        runtime,
        "_record_rewards",
        lambda *_args, **_kwargs: events.append("rewards"),
    )
    monkeypatch.setattr(
        runtime,
        "_handle_completion_action",
        lambda *_args, **_kwargs: events.append("completion") or True,
    )

    assert runtime.run(
        _runtime_exchange([call]),
        session_state=state,
        error_handler=SimpleNamespace(),
        stream_responses=False,
        relay_results=lambda **_kwargs: None,
    ) is True
    assert events == ["rewards", "completion"]


def test_failed_agent_runtime_reward_zeros_test_metric() -> None:
    state = _RuntimeSessionState()
    runtime = _agent_runtime(
        result_entry={"fn": "run_shell", "out": {"error": "failed"}}
    )

    runtime._record_rewards(
        state,
        1,
        {"total_calls": 1, "executed_calls": 1},
        [(SimpleNamespace(function="run_shell"), {"error": "failed"})],
        0,
        1.0,
        failed=True,
    )

    assert state.reward_metrics["TPF_DELTA"] == 0.0


def test_rejected_completion_is_removed_from_current_turn_usage(
    monkeypatch: Any,
) -> None:
    call = SimpleNamespace(function="mark_task_complete")
    result_entry = {"fn": "mark_task_complete", "out": {"ok": True}}
    context = SimpleNamespace(parsed_calls=[call], blocked_calls=[])
    _neutralize_runtime_branches(
        monkeypatch,
        context,
        recent_tools=[{"name": "mark_task_complete"}],
    )
    state = _RuntimeSessionState()
    state.tool_usage_summary["total_calls"] = 3
    state.turn_tool_usage[1]["tools"] = [
        {"name": "mark_task_complete"},
        {"name": "read_file"},
        {"name": "mark_task_complete"},
    ]
    runtime = _agent_runtime(result_entry=result_entry)

    def reject_completion(
        _exchange: Any,
        entry: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> bool:
        entry["_completion_guard_blocked"] = True
        return False

    monkeypatch.setattr(runtime, "_handle_completion_action", reject_completion)

    assert runtime.run(
        _runtime_exchange([call]),
        session_state=state,
        error_handler=SimpleNamespace(),
        stream_responses=False,
        relay_results=lambda **_kwargs: None,
    ) is False
    assert state.tool_usage_summary["total_calls"] == 2
    assert state.turn_tool_usage[1]["tools"] == [
        {"name": "mark_task_complete"},
        {"name": "read_file"},
    ]
