from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from breadboard_engine.conductor.components import maybe_run_plan_bootstrap
from breadboard_engine.conductor.tool_executor import ToolExecutor
from breadboard_engine.conductor.turn_runtime import TurnPolicy


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
