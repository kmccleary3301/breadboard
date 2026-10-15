"""Behavior locks for profile dispatch and request-scoped compaction ports."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from breadboard_engine.compaction.controller import CompactionController, CompactionRequestContext
from breadboard_engine.compaction.state import ProjectionTarget
from breadboard_engine.execution.stock_driver import StockDriver, resolve_stock_driver
from breadboard_engine.execution.stock_tools import close_stock_sessions, register_stock_session
from breadboard_engine.state.session_state import SessionState


def _state():
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "old task"})
    state.add_message({"role": "assistant", "content": "old answer"})
    state.add_message({"role": "user", "content": "new task"})
    return state


def test_request_boundary_is_immutable_and_does_not_leak_into_next_pass():
    controller = CompactionController({})
    target = ProjectionTarget("openai", "responses", "gpt-test")
    first = CompactionRequestContext("pre_turn", 2)
    context = controller._context_for_pass(_state(), reason="threshold", target=target, request_context=first)
    assert (context.sampling_phase, context.pending_user_index) == ("pre_turn", 2)
    with pytest.raises(FrozenInstanceError):
        first.pending_user_index = 0
    second = controller._context_for_pass(_state(), reason="threshold", target=target)
    assert second.sampling_phase is None and second.pending_user_index is None


def test_turn_completion_drains_queued_summaries(monkeypatch):
    queue = [object(), object()]
    controller = CompactionController({}, stock_driver=StockDriver(
        execute=lambda *args, **kwargs: None,
        pending_compaction=lambda state: queue[0] if queue else None,
    ))
    def compact(state, **kwargs):
        queue.pop(0)
        return SimpleNamespace(compacted=True)
    monkeypatch.setattr(controller, "compact", compact)
    assert controller.finish_user_turn(
        _state(), conductor=None, runtime=None, client=None, model="gpt-test", turn_index=1,
    )
    assert not queue


def test_unconsumed_summary_raises_selected_driver_error(monkeypatch):
    class SourceError(RuntimeError):
        pass
    pending = object()
    controller = CompactionController({}, stock_driver=StockDriver(
        execute=lambda *args, **kwargs: None,
        pending_compaction=lambda state: pending,
        compaction_error=SourceError,
    ))
    monkeypatch.setattr(controller, "compact", lambda *args, **kwargs: SimpleNamespace(compacted=True))
    with pytest.raises(SourceError):
        controller._serve_native_summary_requests(
            _state(), conductor=None, runtime=None, client=None, model="gpt-test", turn_index=1,
        )


def test_handled_source_outcome_is_not_retried():
    outcomes = iter(["handled", None])
    controller = CompactionController({}, stock_driver=StockDriver(
        execute=lambda *args, **kwargs: None,
        recovery_outcome=lambda state: next(outcomes),
    ))
    args = dict(turn_index=1, conductor=None, runtime=None, client=None, model="gpt-test")
    assert not controller.recover_from_overflow(RuntimeError("overflow"), _state(), **args)
    assert not controller.recover_from_overflow(RuntimeError("unrelated"), _state(), **args)
    assert controller.request_attempt == 0



def test_selected_codex_driver_filters_tools_at_depth_limit():
    owner = SimpleNamespace(config={"tools": {"native_semantics": "codex_0_139"}, "agents": {"max_depth": 1}},
                            _codex_agent_depth=1)
    driver = resolve_stock_driver(owner)
    tools = [SimpleNamespace(name=name) for name in ["read", "multi_agent_v1", "tool_search"]]
    assert [tool.name for tool in driver.available_tools(owner, tools)] == ["read"]
    owner._codex_agent_depth = 0
    assert driver.available_tools(owner, tools) is tools


def test_driver_close_reuses_registered_sessions_and_retries_failed_close():
    closed = []
    class Session:
        def __init__(self, fail):
            self.fail = fail
        def close(self):
            if self.fail:
                self.fail = False
                raise RuntimeError("busy")
            closed.append(self)
    owner = SimpleNamespace()
    first, second = Session(False), Session(True)
    register_stock_session(owner, first)
    register_stock_session(owner, second)
    with pytest.raises(RuntimeError):
        close_stock_sessions(owner)
    assert closed == [first] and owner._stock_tool_sessions == [second]
    close_stock_sessions(owner)
    assert closed == [first, second] and owner._stock_tool_sessions == []


@pytest.mark.parametrize("semantics", [None, "pi_0_73", "unknown"])
def test_non_stock_profiles_have_no_stock_driver(semantics):
    assert resolve_stock_driver(SimpleNamespace(config={"tools": {"native_semantics": semantics}})) is None


def _conductor(tmp_path, semantics):
    from breadboard_engine.agent_llm_openai import OpenAIConductor

    workspace = tmp_path / "ws"
    workspace.mkdir()
    config = {
        "workspace": {"root": str(workspace), "mirror": {"enabled": False}, "sandbox": {"driver": "process"}},
        "logging": {"enabled": False},
        "providers": {"default_model": "gpt-5.5", "models": [{"id": "gpt-5.5", "adapter": "openai"}]},
        "tools": {"native_semantics": semantics},
        "loop": {"sequence": [{"mode": "build"}]},
        "modes": [{"name": "build", "prompt": "", "tools_enabled": ["*"], "tools_disabled": []}],
    }
    cls = OpenAIConductor.__ray_metadata__.modified_class
    return cls(workspace=str(workspace), config=config, local_mode=True)


def _run(conductor, monkeypatch, main_loop):
    monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
    monkeypatch.setattr(conductor, "_run_main_loop", lambda *args: main_loop(args[9]))
    return conductor.run_agentic_loop(
        "", "Answer", "gpt-5.5", max_steps=1, stream_responses=False, tool_prompt_mode="system_once",
        completion_sentinel=None, context={"session_id": "s", "input_id": "i", "turn_id": "t"},
    )


def test_native_finish_failure_raises_only_after_provider_client_cleanup(tmp_path, monkeypatch):
    events = []
    failure = RuntimeError("native finish failed")

    class Native:
        def finish_run(self):
            events.append("finish")
            raise failure

    class Client:
        def close(self):
            events.append("close")

    def main_loop(state):
        state._opencode_tool_session = Native()
        state._episode_provider_client = Client()
        captured.append(state)
        return {"completed": True}

    captured = []
    conductor = _conductor(tmp_path, "opencode_1_2_17")
    with pytest.raises(RuntimeError) as raised:
        _run(conductor, monkeypatch, main_loop)
    assert raised.value is failure
    assert events == ["finish", "close"]
    assert captured[0]._episode_provider_client is None
    assert conductor._active_session_state is None


def test_codex_thread_snapshot_follows_defensive_result_fallback(tmp_path, monkeypatch):
    def main_loop(state):
        state.provider_messages.append({"role": "assistant", "content": "kept"})
        captured.append(state)
        return None

    captured = []
    conductor = _conductor(tmp_path, "codex_0_139")
    conductor._codex_agent_id = "thread"
    result = _run(conductor, monkeypatch, main_loop)
    state = captured[0]
    assert result["completion_reason"] == "no_result"
    assert result["_codex_session_snapshot"] == {
        "messages": state.provider_messages,
        "compaction_state": state.compaction_state.to_list(),
    }
    assert result["_codex_session_snapshot"]["messages"] is not state.provider_messages
