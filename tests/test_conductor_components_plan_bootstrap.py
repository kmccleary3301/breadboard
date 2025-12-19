from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from agentic_coder_prototype.conductor_components import maybe_run_plan_bootstrap


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


def test_maybe_run_plan_bootstrap_uses_execute_agent_calls_wrapper() -> None:
    session_state = _StubSessionState()
    orchestrator = _StubGuardrailOrchestrator()

    called: Dict[str, Any] = {"execute_agent_calls": False}

    def _exec_raw(call: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "call": call}

    def _build_exec_func(session_state: Any) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        return _exec_raw

    def _execute_agent_calls(
        parsed_calls: List[Any],
        exec_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        session_state: Any,
        *,
        transcript_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        policy_bypass: bool = False,
    ):
        called["execute_agent_calls"] = True
        assert exec_func is _exec_raw
        assert policy_bypass is False
        return [], -1, None, {}

    conductor = SimpleNamespace(
        guardrail_orchestrator=orchestrator,
        _build_exec_func=_build_exec_func,
        _execute_agent_calls=_execute_agent_calls,
    )

    maybe_run_plan_bootstrap(conductor, session_state)
    assert orchestrator.called is True
    assert called["execute_agent_calls"] is True

