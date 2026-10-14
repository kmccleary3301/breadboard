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
def test_route_requests_stay_within_the_provider_image_contract(entry: Dict[str, Any]) -> None:
    from breadboard_engine.provider.contract_requests import normalize_request_messages

    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "omp@18.4.5", "snapcompact": {"system_prompt": "all", "inline_min_tokens": 50},
    }})
    conductor = SimpleNamespace(config={"providers": {"models": [{"id": "gemini-test", **entry}]}}, model="gemini-test")
    runtime = SimpleNamespace(descriptor=SimpleNamespace(provider_id="google", default_api_variant="google-generative-ai"))
    state = SessionState("ws", "image", {})
    state.add_message({"role": "system", "content": "You are an engineering agent with extensive guidelines. " * 250})
    state.add_message({"role": "user", "content": "Implement feature X."})

    view = controller.prepare_request(
        state, conductor=conductor, runtime=runtime, client=object(), model="gemini-test", turn_index=1
    )

    normalize_request_messages(view)


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


def test_routed_summary_tools_use_the_summary_providers_schema() -> None:
    from breadboard_engine.compaction.methods import SummaryRequest
    from breadboard_engine.compaction.summary_model import ConductorSummaryModel

    sent: List[Any] = []

    class SummaryRuntime:
        descriptor = SimpleNamespace(provider_id="openai", runtime_id="openai_responses")

        def invoke(self, **kwargs: Any) -> ProviderResult:
            sent.append(kwargs["tools"])
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="summary")], raw_response=None)

    schemas = {"anthropic": {"name": "Read", "input_schema": {}}, "openai": {"type": "function", "name": "Read", "parameters": {}}}
    summary = ConductorSummaryModel(
        runtime=SimpleNamespace(descriptor=SimpleNamespace(provider_id="anthropic")), client="client", model="claude-test",
        session_state=SessionState("ws", "image", {}), agent_config={},
        tool_schema_provider=lambda provider: [schemas[provider]],
        route_resolver=lambda route_id: (SummaryRuntime(), "gpt-5.1-codex-mini"),
    )
    summary.complete(SummaryRequest(None, (), 20, "summary", model="openai/gpt-5.1-codex-mini",
                                    tool_names=("Read",), request_params={}))
    assert sent == [[schemas["openai"]]]


def test_local_summary_after_a_native_boundary_covers_the_history_it_replaced() -> None:
    controller = _controller()
    state = _session(turns=12)
    context = CompactionContext(
        messages=state.provider_messages, state=state.compaction_state, settings=controller.settings,
        reason="manual", target=_target(), context_window=8000, tokens_before=1,
    )
    native = NativeCompaction(provider="openai", api="chat", model="gpt-test", items=({"type": "compaction"},))
    state.compaction_state.append(context.new_record(
        method="remote", first_kept_index=len(state.provider_messages) - 6, native=native,
    ), state.provider_messages)
    runtime = FakeRuntime()
    # The remote pass failed, so a local method runs; its boundary supersedes the native one.
    assert controller.compact_now(state, target=_target(), runtime=runtime, client=object()).compacted
    assert "f0.py" in str(runtime.summary_requests)


def test_final_product_context_never_carries_the_native_request_surface() -> None:
    controller = _controller()
    state = _session()
    context = CompactionContext(
        messages=state.provider_messages, state=state.compaction_state, settings=controller.settings,
        reason="manual", target=_target(), context_window=8000, tokens_before=1,
    )
    native = NativeCompaction(provider="openai", api="chat", model="gpt-test", items=({"type": "compaction"},))
    state.compaction_state.append(context.new_record(
        method="remote", first_kept_index=len(state.provider_messages) - 2, native=native,
    ), state.provider_messages)
    sent = state.compaction_state.project(state.provider_messages, _target())
    assert any(NATIVE_MARKER_KEY in message for message in sent)
    state.record_provider_request_surface(sent)
    # The boundary predates the last request, yet a resumed session may switch models.
    assert state.final_provider_context() == state.provider_messages


def test_summary_routed_to_another_provider_sends_messages_it_accepts() -> None:
    from breadboard_engine.compaction.methods import SummaryRequest
    from breadboard_engine.compaction.summary_model import ConductorSummaryModel
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message
    from breadboard_engine.provider.routing import provider_router
    from breadboard_engine.provider.runtime import provider_registry
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
    from breadboard_engine.provider.anthropic_replay import TOOL_RESULT_SCHEMA
    from breadboard_engine.provider.contract_requests import ProviderRequest

    anthropic = provider_registry.create_runtime(provider_router.get_runtime_descriptor("anthropic/claude-sonnet-4-6")[0])
    same_api_sent: List[Any] = []
    sent: List[Any] = []

    class SourceRuntime:
        descriptor = SimpleNamespace(provider_id="openai", default_api_variant="responses", runtime_id="openai_responses")

        def invoke(self, **kwargs: Any) -> ProviderResult:
            same_api_sent.append(copy.deepcopy(kwargs["messages"]))
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="summary")], raw_response=None)

    class SummaryRuntime:
        descriptor = anthropic.descriptor

        def invoke(self, **kwargs: Any) -> ProviderResult:
            sent.append(anthropic._convert_messages(kwargs["messages"], context=kwargs["context"])[1])
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="summary")], raw_response=None)

    legacy_raw = '{ "legacy": }'
    canonical_raw = '{"canonical": "unterminated}'
    history = (
        {"role": "user", "content": [{"type": "text", "text": "look at the screen"},
                                     {"type": "input_image", "image_url": "data:image/png;base64,AA=="}]},
        {"role": "assistant", "content": [{"type": "provider_replay", "provider_id": "openai",
                                           "schema_version": "openai.responses.tools.v1", "replay_scope": "same_provider",
                                           "payload": {"type": "local_shell_call", "call_id": "ignored"}},
                                          {"type": "tool_call", "call_id": "raw-canonical",
                                           "name": "canonical_tool", "raw_arguments": canonical_raw}],
         "tool_calls": [
             {"id": "raw-legacy", "type": "function", "name": "legacy_tool", "raw_arguments": legacy_raw},
             {"id": "c1", "type": "function", "function": {"name": "view_image", "arguments": "{}"}},
             {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
         ]},
        responses_image_tool_result_message("raw-legacy", [{"type": "input_text", "text": "LEGACY_RAW_RESULT"},
                                                           {"type": "input_image", "image_url": "data:image/png;base64,AA=="}]),
        {"role": "tool_result", "content": [
            {"type": "tool_result", "call_id": "raw-canonical", "content": [
                {"type": "input_text", "text": "CANONICAL_RAW_RESULT"},
                {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
            ]},
            {"type": "tool_result", "call_id": "c1", "content": "ORDINARY_RESULT_1"},
        ]},
        {"role": "tool", "tool_call_id": "c2", "content": "ORDINARY_RESULT_2"},
        {"role": "assistant", "content": "done"},
    )
    original_history = copy.deepcopy(history)
    summary = ConductorSummaryModel(
        runtime=SourceRuntime(),
        client="client", model="gpt-5.5", session_state=SessionState("ws", "image", {}), agent_config={},
        route_resolver=lambda route_id: (SummaryRuntime(), "claude-sonnet-4-6"),
    )
    summary.complete(SummaryRequest(None, history, 20, "summary"))
    assert same_api_sent == [list(history)]

    summary.complete(SummaryRequest(None, history, 20, "summary", model="anthropic/claude-sonnet-4-6"))

    assert history == original_history
    converted = sent[0]
    assistant_index = next(
        index for index, message in enumerate(converted)
        if message["role"] == "assistant"
        and any(block.get("type") == "tool_use" for block in message["content"])
    )
    assistant_blocks = converted[assistant_index]["content"]
    assistant_text = "\n".join(block["text"] for block in assistant_blocks if block.get("type") == "text")
    assert "legacy_tool" in assistant_text and "call ID raw-legacy" in assistant_text
    assert "canonical_tool" in assistant_text and "call ID raw-canonical" in assistant_text
    assert legacy_raw in assistant_text
    assert canonical_raw in assistant_text
    assert "LEGACY_RAW_RESULT[image removed]" in assistant_text
    assert "CANONICAL_RAW_RESULT[image removed]" in assistant_text
    assert "Tool result for call ID raw-legacy" in assistant_text
    assert "Tool result for call ID raw-canonical" in assistant_text

    tool_uses = {
        block["id"] for block in assistant_blocks if block.get("type") == "tool_use"
    }
    assert tool_uses == {"c1", "c2"}
    results = {block["tool_use_id"]: block["content"] for message in converted
               for block in message["content"] if block.get("type") == "tool_result"}
    assert results == {"c1": "ORDINARY_RESULT_1", "c2": "ORDINARY_RESULT_2"}
    result_message_indices = [
        index for index, message in enumerate(converted)
        if any(block.get("type") == "tool_result" for block in message["content"])
    ]
    assert result_message_indices == list(range(assistant_index + 1, assistant_index + 3))
    assert "openai" not in str(sent)

    responses = OpenAIResponsesRuntime(SimpleNamespace(
        provider_id="openai", runtime_id="openai_responses", default_api_variant="responses"
    ))
    responses_sent: List[Any] = []

    class ResponsesSummaryRuntime:
        descriptor = responses.descriptor

        def invoke(self, **kwargs: Any) -> ProviderResult:
            responses_sent.append(responses._convert_messages_to_input(
                kwargs["messages"], include_tool_calls=True, context=kwargs["context"]
            ))
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="summary")], raw_response=None)

    claude_history = (
        {"role": "assistant", "content": [{
            "type": "tool_call", "call_id": "claude-image", "name": "view_image", "arguments": {},
        }]},
        AnthropicAdapter().create_tool_result_batch_message([{
            "role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "claude-image", "content": [{
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "AA=="},
                }],
            }],
        }]),
    )
    claude_summary = ConductorSummaryModel(
        runtime=anthropic,
        client="client", model="claude-sonnet-4-6", session_state=SessionState("ws", "image", {}),
        agent_config={},
        route_resolver=lambda route_id: (ResponsesSummaryRuntime(), "gpt-5.1-codex-mini"),
    )
    claude_summary.complete(SummaryRequest(
        None, claude_history, 20, "summary", model="openai/gpt-5.1-codex-mini"
    ))
    assert {"type": "function_call_output", "call_id": "claude-image", "output": "[image removed]"} in responses_sent[0]
    assert "AA==" not in str(responses_sent[0])

    for case, include_content in (("missing", False), ("none", True)):
        call_id = f"claude-{case}-content"
        native_result = {"type": "tool_result", "tool_use_id": call_id}
        if include_content:
            native_result["content"] = None
        request = ProviderRequest(
            stream=False,
            messages=[
                {"role": "assistant", "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "view_image", "arguments": "{}"},
                }]},
                {"role": "tool", "content": [
                    {"type": "tool_result", "call_id": call_id, "content": "null"},
                    {"type": "provider_replay", "provider_id": "anthropic",
                     "schema_version": TOOL_RESULT_SCHEMA, "replay_scope": "same_provider",
                     "payload": {"messages": [{"role": "user", "content": [native_result]}]}},
                ]},
            ],
            tools=[],
        )
        assert request.messages[1]["content"][0]["content"] == "null"
        claude_summary.complete(SummaryRequest(
            None, request.messages, 20, "summary", model="openai/gpt-5.1-codex-mini"
        ))
        assert {
            "type": "function_call_output", "call_id": call_id, "output": "null"
        } in responses_sent[-1]
