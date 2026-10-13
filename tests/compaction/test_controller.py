"""CompactionController: request views, triggers, and overflow recovery."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from breadboard_engine.compaction.controller import CompactionController
from breadboard_engine.compaction.methods import CompactionContext
from breadboard_engine.compaction.state import NATIVE_MARKER_KEY, NativeCompaction, ProjectionTarget
from breadboard_engine.compaction.transcript import check_tool_pairing
from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult
from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
from breadboard_engine.state.session_state import SessionState

OVERFLOW = ProviderRuntimeError(
    "provider operation failed (BadRequestError)",
    details={"code": "context_length_exceeded", "status_code": 400},
)


class FakeRuntime:
    descriptor = SimpleNamespace(provider_id="openai", default_api_variant="chat", runtime_id="openai_chat")

    def __init__(self) -> None:
        self.summary_requests: List[List[Dict[str, Any]]] = []

    def invoke(self, *, client, model, messages, tools, stream, context):
        assert tools is None and stream is False
        assert context.extra["compaction_summary"] is True
        self.summary_requests.append(copy.deepcopy(messages))
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="## Goal\nport the parser")],
            raw_response=None,
            model=model,
        )


class FakeRecorder:
    def __init__(self) -> None:
        self.labels: List[str] = []

    def record_request(self, turn_index, **kwargs):
        self.labels.append(kwargs.get("label"))
        return ""


def _session(turns: int = 6) -> SessionState:
    state = SessionState("ws", "image", {})
    state.add_message({"role": "system", "content": "You are a coding agent."})
    state.add_message({"role": "user", "content": "Port the parser."})
    for index in range(turns):
        call_id = f"call_{index}"
        state.add_message(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "read", "arguments": f'{{"path": "f{index}.py"}}'},
                    }
                ],
            }
        )
        state.add_message({"role": "tool", "tool_call_id": call_id, "content": "x" * 4000})
    return state


def _controller(**overrides: Any) -> CompactionController:
    config = {
        "enabled": True,
        "contextWindow": 8000,
        "reserveTokens": 1000,
        "keepRecentTokens": 1500,
        "methodOrder": ["soft"],
        **overrides,
    }
    return CompactionController({"compaction": config})


def _conductor(controller: CompactionController, recorder: Any = None) -> Any:
    return SimpleNamespace(config={}, model="gpt-test", structured_request_recorder=recorder)


def test_disabled_view_is_exact_history_and_overflow_is_not_recovered() -> None:
    controller = CompactionController({})
    state = _session()
    runtime = FakeRuntime()

    view = controller.prepare_request(
        state, conductor=_conductor(controller), runtime=runtime, client=object(), model="gpt-test", turn_index=1
    )

    assert view == state.provider_messages
    assert view is not state.provider_messages
    assert not controller.recover_from_overflow(
        OVERFLOW, state, turn_index=1, conductor=None, runtime=runtime, client=object(), model="gpt-test"
    )
    assert state.compaction_state.records == ()
    assert runtime.summary_requests == []


def test_overflow_recovery_shrinks_view_keeps_pairs_and_leaves_history() -> None:
    controller = _controller()
    state = _session()
    history = copy.deepcopy(state.provider_messages)
    runtime = FakeRuntime()
    recorder = FakeRecorder()

    recovered = controller.recover_from_overflow(
        OVERFLOW,
        state,
        turn_index=3,
        conductor=_conductor(controller, recorder),
        runtime=runtime,
        client=object(),
        model="gpt-test",
    )

    assert recovered
    assert controller.request_attempt == 1
    assert state.provider_messages == history  # records project; history is append-only
    view = controller.build_request_view(state, target=_target())
    assert len(view) < len(history)
    assert view[0] == history[0]
    assert check_tool_pairing(view) is None
    assert any("port the parser" in str(message.get("content")) for message in view)
    assert runtime.summary_requests
    assert recorder.labels and all(label.startswith("compaction_") for label in recorder.labels)


def test_overflow_recovery_is_bounded_per_turn() -> None:
    controller = _controller(maxPassesPerTurn=1)
    state = _session()
    kwargs = dict(conductor=None, runtime=FakeRuntime(), client=object(), model="gpt-test")

    assert controller.recover_from_overflow(OVERFLOW, state, turn_index=4, **kwargs)
    assert not controller.recover_from_overflow(OVERFLOW, state, turn_index=4, **kwargs)
    assert len(state.compaction_state.records) == 1


def test_terminal_policy_and_non_overflow_errors_are_not_recovered() -> None:
    state = _session()
    kwargs = dict(conductor=None, runtime=FakeRuntime(), client=object(), model="gpt-test")

    terminal = _controller(overflowPolicy="terminal")
    assert not terminal.recover_from_overflow(OVERFLOW, state, turn_index=1, **kwargs)

    compacting = _controller()
    rate_limited = ProviderRuntimeError("rate limited", details={"code": "rate_limited", "status_code": 429})
    assert not compacting.recover_from_overflow(rate_limited, state, turn_index=1, **kwargs)
    assert state.compaction_state.records == ()


def test_threshold_compacts_once_and_ignores_usage_from_before_the_record() -> None:
    controller = _controller(contextWindow=20000, reserveTokens=2000)
    state = _session(turns=6)
    state.add_message({"role": "user", "content": "continue"})
    # Last request reported a window-filling prompt.
    state.set_provider_metadata("usage", {"prompt_tokens": 19000, "completion_tokens": 10})
    runtime = FakeRuntime()
    conductor = _conductor(controller)

    first = controller.prepare_request(
        state, conductor=conductor, runtime=runtime, client=object(), model="gpt-test", turn_index=5
    )
    second = controller.prepare_request(
        state, conductor=conductor, runtime=runtime, client=object(), model="gpt-test", turn_index=5
    )

    assert len(state.compaction_state.records) == 1
    assert first == second
    assert len(first) < len(state.provider_messages)


def test_native_marker_is_stripped_for_runtime_without_compaction_port() -> None:
    controller = _controller()
    state = _session()
    native = NativeCompaction(provider="openai", api="chat", model="gpt-test", items=({"type": "compaction"},))
    context = CompactionContext(
        messages=state.provider_messages,
        state=state.compaction_state,
        settings=controller.settings,
        reason="manual",
        target=_target(),
        context_window=8000,
        tokens_before=1,
    )
    boundary = context.new_record(
        method="remote",
        first_kept_index=len(state.provider_messages) - 2,
        summary="native summary",
        summary_messages=({"role": "user", "content": "native summary"},),
        native=native,
    )
    state.compaction_state.append(boundary, state.provider_messages)

    plain = controller.build_request_view(state, target=_target(), runtime=FakeRuntime())
    assert all(NATIVE_MARKER_KEY not in message for message in plain)

    class NativeRuntime(FakeRuntime):
        def compaction_port(self, *, client, model, context=None):
            return None

    native_view = controller.build_request_view(state, target=_target(), runtime=NativeRuntime())
    assert any(NATIVE_MARKER_KEY in message for message in native_view)


def _target() -> ProjectionTarget:
    return ProjectionTarget(provider="openai", api="chat", model="gpt-test")


@pytest.mark.parametrize("entry", [{"supports_images": True}, {"input": ["text", "image"]}])
def test_image_capability_comes_from_model_config(entry: Dict[str, Any]) -> None:
    controller = _controller()
    conductor = SimpleNamespace(
        config={"providers": {"models": [{"id": "vision-model", **entry}]}}, model="vision-model"
    )
    assert controller.resolve_supports_images(conductor=conductor, model="vision-model")
    assert not controller.resolve_supports_images(conductor=conductor, model="other-model")


def test_context_window_resolves_from_id_shaped_model_entry() -> None:
    controller = CompactionController({})
    conductor = SimpleNamespace(
        config={
            "providers": {
                "models": [
                    {"id": "test-model", "context_length": 65536},
                ]
            }
        },
        model="test-model",
    )
    assert controller.resolve_context_window(conductor=conductor, model="test-model") == 65536


def test_model_limits_resolve_from_id_shaped_model_entry() -> None:
    controller = CompactionController({})
    conductor = SimpleNamespace(
        config={"providers": {"models": [
            {"id": "other-model", "max_input_tokens": 1000, "max_output_tokens": 100},
            {"id": "test-model", "max_input_tokens": 272000, "max_output_tokens": 32000},
        ]}},
        model="test-model",
    )
    session = SimpleNamespace(get_provider_metadata=lambda key: None)
    assert controller.resolve_model_limits(session, conductor, "test-model") == (272000, 32000)
    assert controller.resolve_model_limits(session, conductor) == (272000, 32000)


def test_model_limits_match_the_routed_id_after_the_conductor_resolves_the_api_model() -> None:
    conductor = SimpleNamespace(
        config={"providers": {"models": [{"id": "openai/gpt-5.1-codex-mini", "max_input_tokens": 272000}]}},
        model="gpt-5.1-codex-mini", _current_route_id="openai/gpt-5.1-codex-mini",
    )
    session = SimpleNamespace(get_provider_metadata=lambda key: None)
    assert CompactionController({}).resolve_model_limits(session, conductor, "gpt-5.1-codex-mini") == (272000, None)


def test_overflow_budget_is_scoped_to_session_state() -> None:
    controller = CompactionController({"compaction": {
        "enabled": True, "contextWindow": 8000, "reserveTokens": 1000,
        "keepRecentTokens": 1500, "methodOrder": ["soft"], "maxPassesPerTurn": 1,
    }})
    runtime = FakeRuntime()
    kwargs = dict(conductor=SimpleNamespace(config={}, model="gpt-test"), runtime=runtime,
                  client=object(), model="gpt-test", turn_index=1)
    session1 = _session()
    assert controller.recover_from_overflow(OVERFLOW, session1, **kwargs)
    session2 = _session()
    assert controller.recover_from_overflow(OVERFLOW, session2, **kwargs)


def test_usage_freshness_is_scoped_to_session_state() -> None:
    from breadboard_engine.compaction.state import CompactionRecord
    controller = CompactionController({"compaction": {"enabled": True, "preset": "pi@0.73.1"}})
    previous = SessionState("ws", "image", {})
    previous.add_message({"role": "system", "content": "system"})
    previous.add_message({"role": "user", "content": "old input"})
    prior_record = CompactionRecord(
        record_id="prior", sequence=0, method="summary", reason="threshold",
        created_at="2026-10-12T00:00:00Z", tokens_before=5000,
        history_length=len(previous.provider_messages), first_kept_index=1,
        summary="prior summary", summary_messages=({"role": "user", "content": "prior summary"},),
    )
    controller._after_record_appended(previous, prior_record)

    resumed = SessionState("ws", "image", {})
    resumed.add_message({"role": "system", "content": "system"})
    resumed.add_message({"role": "user", "content": "restored input"})
    resumed.set_provider_metadata("usage", {"total_tokens": 4000})
    assert controller.should_trigger_threshold(
        resumed, context_window=20000,
        last_usage=resumed.get_provider_metadata("usage"), checkpoint="every_request",
    )


def test_native_port_leases_the_broker_client_for_each_compaction() -> None:
    leases: List[str] = []

    class Port:
        provider, api = "openai", "responses"

        def __init__(self, client: Any) -> None:
            self.client = client

        def supports(self, model: str) -> bool:
            return True

        def compact(self, context: Any) -> Any:
            return self.client

    class LeaseRuntime:
        def compaction_port(self, *, client: Any, model: str) -> Port:
            return Port(client)

    @contextmanager
    def lease(route_id: str, runtime: Any):
        leases.append(route_id)
        yield "leased-client"

    # Production conductors pass no client; the invoker leases one per provider call.
    conductor = SimpleNamespace(_provider_client_lease=lease, _current_route_id="route-a")
    [port] = CompactionController({"compaction": {"enabled": True}}).remote_ports_for(
        LeaseRuntime(), None, "gpt-test", conductor
    )
    assert (port.provider, port.api, port.supports("gpt-test"), leases) == ("openai", "responses", True, [])
    assert port.compact(None) == "leased-client"
    assert leases == ["route-a"]


def test_native_boundary_for_the_route_counts_toward_threshold_pressure() -> None:
    controller = _controller()
    state = _session(turns=12)
    assert controller.should_trigger_threshold(state, context_window=8000)
    context = CompactionContext(
        messages=state.provider_messages, state=state.compaction_state, settings=controller.settings,
        reason="manual", target=_target(), context_window=8000, tokens_before=1,
    )
    # OpenAI native records carry no readable summary; only their own route can replay them.
    native = NativeCompaction(provider="openai", api="chat", model="gpt-test", items=({"type": "compaction"},))
    state.compaction_state.append(context.new_record(
        method="remote", first_kept_index=len(state.provider_messages) - 2, native=native,
    ), state.provider_messages)
    controller.prepare_request(state, conductor=_conductor(controller), runtime=FakeRuntime(), client=object(),
                               model="gpt-test", turn_index=1)
    assert not [event for event in state.lifecycle_events
                if event["type"] == "compaction_finished" and event["payload"]["reason"] == "threshold"]


def test_persisted_snapshot_never_carries_a_native_boundary() -> None:
    controller = _controller()
    state = _session()
    persisted: List[Any] = []
    state.can_persist_compaction = lambda: True
    state.persist_compaction_snapshot = persisted.append
    context = CompactionContext(
        messages=state.provider_messages, state=state.compaction_state, settings=controller.settings,
        reason="manual", target=_target(), context_window=8000, tokens_before=1,
    )
    native = NativeCompaction(provider="openai", api="chat", model="gpt-test", items=({"type": "compaction"},))
    record = context.new_record(method="remote", first_kept_index=len(state.provider_messages) - 2, native=native)
    state.compaction_state.append(record, state.provider_messages)
    controller._after_record_appended(state, record, _target())
    # A resumed session may switch models, so it must get the history, not another route's opaque token.
    assert persisted == [state.provider_messages]


def test_routed_summary_model_uses_its_own_runtime_and_api_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from breadboard_engine.provider.runtime import provider_registry

    summary_runtime = FakeRuntime()
    created: List[Any] = []
    monkeypatch.setattr(provider_registry, "create_runtime",
                        lambda descriptor: created.append(descriptor.provider_id) or summary_runtime)
    calls: List[Any] = []
    summary_runtime.invoke = lambda **kwargs: calls.append(kwargs["model"]) or FakeRuntime.invoke(summary_runtime, **kwargs)

    @contextmanager
    def lease(route_id: str, runtime: Any):
        calls.append((route_id, runtime is summary_runtime))
        yield "leased-client"

    turn_runtime = FakeRuntime()
    turn_runtime.descriptor = SimpleNamespace(provider_id="anthropic", default_api_variant="messages",
                                              runtime_id="anthropic_messages")
    controller = _controller(summaryModel="openai/gpt-5.1-codex-mini")
    conductor = SimpleNamespace(config={}, model="claude-test", structured_request_recorder=None,
                                _provider_client_lease=lease, _current_route_id="anthropic/claude-test")
    controller.prepare_request(_session(turns=12), conductor=conductor, runtime=turn_runtime, client=None,
                               model="claude-test", turn_index=1)
    assert created and set(created) == {"openai"}
    assert set(calls) == {("openai/gpt-5.1-codex-mini", True), "gpt-5.1-codex-mini"}
    assert turn_runtime.summary_requests == []
