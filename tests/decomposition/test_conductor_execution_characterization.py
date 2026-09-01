from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from breadboard_engine.conductor import (
    implementation_receipts,
    model_output,
    replay_compare,
)
from breadboard_engine.conductor.completion_guards import (
    _force_failed_verification_final_answer,
    _force_failed_write_final_answer,
    _force_post_receipt_final_answer,
)
from breadboard_engine.conductor.execution_records import ReplayToolOutputMismatchError
from breadboard_engine.conductor.implementation_receipts import (
    _implementation_verification_receipt_missing,
    _latest_prompt_requires_implementation_write,
    _latest_prompt_requests_verification,
    _shell_command_write_targets,
)
from breadboard_engine.conductor.tool_executor import _is_completion_action_result
from breadboard_engine.provider.runtime import ProviderMessage, ProviderResult, ProviderRuntimeContext, ProviderRuntimeError, ProviderToolCall
from breadboard_engine.state.session_state import SessionState





def _session(prompt: str = "") -> SessionState:
    state = SessionState(workspace=".", image="test", config={})
    if prompt:
        state.add_message({"role": "user", "content": prompt})
    return state


def _logger() -> Any:
    events: list[tuple[str, Any]] = []
    return SimpleNamespace(
        events=events,
        log_assistant_message=lambda message: events.append(("assistant", message)),
        log_system_message=lambda message: events.append(("system", message)),
    )






def test_process_model_output_preserves_text_and_native_dispatch_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(model_output, "handle_text_tool_calls", lambda *args: events.append(("text", args[1].content)) or False)
    monkeypatch.setattr(model_output, "handle_native_tool_calls", lambda *args: events.append(("native", args[1].tool_calls[0].function.name)) or False)
    conductor = SimpleNamespace(config={})
    state = _session()
    text_message = ProviderMessage(role="assistant", content="describe", tool_calls=[], finish_reason="stop")
    native_message = ProviderMessage(role="assistant", content=None, tool_calls=[ProviderToolCall(id="c1", name="lookup", arguments="{}")], finish_reason="tool_calls")
    assert model_output.process_model_output(conductor, text_message, object(), [], state, object(), _logger(), object(), False, "mock/model") is False
    assert model_output.process_model_output(conductor, native_message, object(), [], state, object(), _logger(), object(), False, "mock/model") is False
    assert events == [("text", "describe"), ("native", "lookup")]


def test_receipt_outcomes_and_closure_guards_are_stable(tmp_path: Path) -> None:
    conductor = SimpleNamespace(config={"workloop_guards": {"implementation_write_receipts": {"enabled": True}}}, workspace=str(tmp_path))
    state = _session("Implement app.py and verify it with a smoke test")
    assert _latest_prompt_requires_implementation_write(state)
    assert _latest_prompt_requests_verification(state)
    assert implementation_receipts._implementation_write_receipt_missing(conductor, state)
    assert _implementation_verification_receipt_missing(conductor, state) is False
    state.tool_usage_summary["successful_requested_write_targets"] = ["app.py"]
    state.tool_usage_summary["successful_user_facing_writes"] = 1
    assert implementation_receipts._implementation_write_receipt_missing(conductor, state) is False
    assert _implementation_verification_receipt_missing(conductor, state)
    state.tool_usage_summary["successful_tests"] = 1
    state.turn_tool_usage[0] = {"tools": [{"success": True, "meta": {"is_test_command": True, "exit_code": 0, "command": "smoke_test.sh"}}]}
    assert _implementation_verification_receipt_missing(conductor, state) is False
    assert _shell_command_write_targets("printf ok > app.py") == ["app.py"]


def test_retry_fallback_sequence_and_provider_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    state = _session()
    state.set_provider_metadata("current_turn_index", 4)
    class Health:
        def record_failure(self, *args): events.append("health.failure")
        def record_success(self, *args): events.append("health.success")
    class Metrics:
        def add_fallback(self, **kwargs): events.append("metrics.fallback")
        def add_call(self, *args, **kwargs): events.append("metrics.call")
    class Primary:
        descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock")
        def invoke(self, **kwargs): events.append("primary.invoke"); raise ProviderRuntimeError("primary failed")
    class Fallback:
        descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock")
        def create_client_from_config(self, config): events.append("fallback.client"); return object()
        def invoke(self, **kwargs): events.append("fallback.invoke"); return ProviderResult(messages=[ProviderMessage(role="assistant", content="ok", tool_calls=[])], raw_response={}, usage={}, metadata={})
    fallback = Fallback()
    class Router:
        def get_runtime_descriptor(self, model): return fallback.descriptor, model
        def create_client_config(self, model): return {"api_key": "test"}
        @contextmanager
        def execution_client_config(self, model, **_kwargs):
            config = self.create_client_config(model)
            try:
                yield config
            finally:
                config.clear()
    class Registry:
        def create_runtime(self, descriptor): return fallback
    conductor = SimpleNamespace(
        logger_v2=SimpleNamespace(run_dir=None), md_writer=SimpleNamespace(system=lambda text: text), route_health=Health(),
        provider_metrics=Metrics(), _update_health_metadata=lambda *_: None,
        _get_model_routing_preferences=lambda route: {"fallback_models": ["mock/fallback"]},
        _select_fallback_route=lambda *args, **kwargs: ("mock/fallback", {"selected": True}),
        _log_routing_event=lambda *args, **kwargs: events.append("routing.event"),
    )
    logger = _logger()
    monkeypatch.setattr(model_output.time, "sleep", lambda _: events.append("sleep"))
    result = model_output.retry_with_fallback(
        conductor, Primary(), object(), "mock/primary", [{"role": "user", "content": "hi"}], None,
        ProviderRuntimeContext(session_state=state, agent_config={}, stream=False), stream_responses=False,
        session_state=state, markdown_logger=logger, attempted=[], last_error=ProviderRuntimeError("initial"),
        provider_router_override=Router(), provider_registry_override=Registry(),
    )
    assert result is not None and result.messages[0].content == "ok"
    assert events[:4] == ["health.failure", "sleep", "primary.invoke", "health.failure"]
    assert "fallback.invoke" in events
    assert state.get_provider_metadata("fallback_route")["to"] == "mock/fallback"

    provider_message = ProviderMessage(role="assistant", content="hello", tool_calls=[], finish_reason="stop")
    session_events: list[Any] = []
    fake_state = SimpleNamespace(add_transcript_entry=lambda payload: session_events.append(("transcript", payload)))
    fake_conductor = SimpleNamespace(logger_v2=SimpleNamespace(run_dir=None))
    markdown = _logger()
    model_output.log_provider_message(fake_conductor, provider_message, fake_state, markdown, False)
    assert session_events and session_events[0][0] == "transcript"
    assert markdown.events == [("assistant", "hello")]


def test_replay_mismatch_output_is_recorded_and_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _session()
    state.set_provider_metadata("replay_mode", True)
    guard_events: list[Any] = []
    state.record_guardrail_event = lambda name, payload: guard_events.append((name, payload))
    adapter = SimpleNamespace(create_tool_result_message=lambda call_id, name, out: {"content": "actual"})
    monkeypatch.setattr(replay_compare.provider_router, "parse_model_id", lambda model: ("mock", "model"))
    monkeypatch.setattr(replay_compare.provider_adapter_manager, "get_adapter", lambda provider: adapter)
    conductor = SimpleNamespace(config={"replay": {"compare_tool_outputs": True, "fail_on_tool_output_mismatch": True}}, workspace=".")
    parsed = SimpleNamespace(expected_output="expected", provider_name="shell_command", function="shell_command", call_id="c1")
    with pytest.raises(ReplayToolOutputMismatchError, match="Replay tool output mismatch") as exc_info:
        replay_compare.record_replay_tool_output_mismatches(conductor, state, [(parsed, {"ok": True})], model="mock/model")
    assert "EXPECTED:" in str(exc_info.value) and "ACTUAL:" in str(exc_info.value)
    assert guard_events[0][0] == "mvi_tool_output_mismatch"


