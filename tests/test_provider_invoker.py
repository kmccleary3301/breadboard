from __future__ import annotations

from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from breadboard_engine.provider import ProviderInvoker
from breadboard_engine.provider.runtime import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderToolCall,
)
from breadboard_engine.provider.contracts import ProviderContractError
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from breadboard_engine.state.session_state import SessionState
from breadboard_engine.messaging.markdown_logger import MarkdownLogger


def _make_invoker(retry_with_fallback, *, client_lease=None):
    provider_metrics = Mock()
    route_health = Mock()
    logger_v2 = SimpleNamespace(run_dir=None)
    md_writer = SimpleNamespace(system=lambda msg: msg)
    return ProviderInvoker(
        provider_metrics=provider_metrics,
        route_health=route_health,
        logger_v2=logger_v2,
        md_writer=md_writer,
        retry_with_fallback=retry_with_fallback,
        update_health_metadata=Mock(),
        set_last_latency=Mock(),
        set_html_detected=Mock(),
        client_lease=client_lease,
    )


def _mk_runtime(result=None, *, should_raise=False):
    runtime = Mock()
    runtime.descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock_chat")
    if should_raise:
        runtime.invoke.side_effect = ProviderRuntimeError("runtime_failed")
    else:
        runtime.invoke.return_value = result
    return runtime


def _session_state():
    state = SessionState(workspace=".", image="cli", config={})
    state.set_provider_metadata("session_id", "session-1")
    state.set_provider_metadata("input_id", "input-1")
    state.set_provider_metadata("turn_id", "turn-1")
    return state


def _markdown_logger():
    logger = Mock(spec=MarkdownLogger)
    return logger


def _provider_result(payload: str = "ok"):
    message = ProviderMessage(role="assistant", content=payload, tool_calls=[], finish_reason="stop", index=0)
    return ProviderResult(messages=[message], raw_response=None, metadata={})


def test_provider_invoker_stream_success():
    runtime_result = _provider_result()
    canary = "private-provider-metadata-canary"
    runtime_result.metadata.update(
        {
            "raw_finish_reason": "stop",
            "provider_runtime_timing": {
                "invoke_total_seconds": 1.25,
                "client_cache_hit": True,
                "invalid key": canary,
            },
            "provider_private_text": canary,
        }
    )
    runtime = _mk_runtime(runtime_result)
    retry_with_fallback = Mock(return_value=None)
    invoker = _make_invoker(retry_with_fallback)
    invoker.route_health.is_circuit_open.return_value = False

    session_state = _session_state()
    result, used_streaming = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[],
        tools_schema=None,
        stream_responses=True,
        runtime_context=ProviderRuntimeContext(session_state=session_state, agent_config={}),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    assert result is runtime_result
    assert used_streaming is True
    runtime.invoke.assert_called_once()
    retry_with_fallback.assert_not_called()
    invoker.route_health.record_success.assert_called_once_with("cli_mock/dev")
    assert session_state.get_provider_metadata("raw_finish_meta") == {
        "raw_finish_reason": "stop",
        "provider_runtime_timing": {
            "invoke_total_seconds": 1.25,
            "client_cache_hit": True,
        },
    }
    assert canary not in json.dumps(session_state.provider_metadata)
    exchange = result.metadata["provider_exchange"]
    assert session_state.get_provider_metadata("provider_exchange_history") == [
        exchange
    ]
    assert session_state.get_provider_metadata("last_provider_exchange") == exchange



def test_provider_invoker_records_cache_observation_as_independent_facts() -> None:
    runtime = _mk_runtime(
        ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content="ok",
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            ],
            raw_response=None,
            usage={"cache_read_tokens": 11, "cache_write_tokens": 3},
            metadata={},
        )
    )
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False
    state = _session_state()
    context = ProviderRuntimeContext(
        session_state=state,
        agent_config={},
        extra={
            "cache_prefix_digest": "sha256:" + "a" * 64,
            "cache_divergence_reason": "provider_route_changed",
        },
    )

    invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[],
        tools_schema=None,
        stream_responses=False,
        runtime_context=context,
        session_state=state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    expected = {
        "observed": True,
        "prefix_digest": "sha256:" + "a" * 64,
        "provider_tokens": {"cache_read": 11, "cache_write": 3},
        "route_id": "cli_mock/dev",
        "model": "cli_mock/dev",
        "divergence_reason": "provider_route_changed",
    }
    assert state.get_provider_metadata("last_cache_observation") == expected
    details = invoker.provider_metrics.add_call.call_args.kwargs["details"]
    assert details == {"cache_observation": expected}

def test_provider_invoker_requires_typed_operation_correlation_before_dispatch() -> None:
    runtime = _mk_runtime(_provider_result())
    invoker = _make_invoker(Mock(return_value=None))
    state = SessionState(workspace=".", image="cli", config={})

    with pytest.raises(
        ProviderContractError,
        match="requires exact session/input/turn correlation",
    ):
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model="cli_mock/dev",
            send_messages=[],
            tools_schema=None,
            stream_responses=False,
            runtime_context=ProviderRuntimeContext(
                session_state=state,
                agent_config={},
            ),
            session_state=state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="cli_mock/dev",
        )

    runtime.invoke.assert_not_called()

@pytest.mark.parametrize(
    "runtime_error",
    (
        ProviderRuntimeError("provider_failed"),
        ProviderRuntimeError("stream_failed", kind="protocol"),
    ),
)
def test_profile_bound_invocation_never_leases_or_falls_back(runtime_error):
    runtime = _mk_runtime()
    runtime.invoke.side_effect = runtime_error
    retry_with_fallback = Mock(return_value=_provider_result("must not run"))
    def forbidden_lease(*_args, **_kwargs):
        raise AssertionError("profile-bound invocation must use its bound client")

    invoker = _make_invoker(
        retry_with_fallback,
        client_lease=forbidden_lease,
    )
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()
    profile = OpenAICompletionsProviderProfile(
        model="Qwen/Qwen3.5-35B-A3B",
        scoped_credential="episode-secret",
        base_url="http://127.0.0.1:8111/v1",
        context_window=131_072,
        max_output_tokens=32_000,
    )

    with pytest.raises(ProviderRuntimeError, match=str(runtime_error)):
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model=profile.model,
            send_messages=[],
            tools_schema=None,
            stream_responses=True,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state,
                agent_config={},
                stream=True,
                provider_profile=profile,
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="openai/Qwen/Qwen3.5-35B-A3B",
        )

    runtime.invoke.assert_called_once()
    retry_with_fallback.assert_not_called()


def test_profile_bound_invocation_fails_closed_on_open_circuit():
    runtime = _mk_runtime(_provider_result())
    retry_with_fallback = Mock(return_value=_provider_result("must not run"))
    invoker = _make_invoker(retry_with_fallback)
    invoker.route_health.is_circuit_open.return_value = True
    session_state = _session_state()
    profile = OpenAICompletionsProviderProfile(
        model="Qwen/Qwen3.5-35B-A3B",
        scoped_credential="episode-secret",
        base_url="http://127.0.0.1:8111/v1",
        context_window=131_072,
        max_output_tokens=32_000,
    )

    with pytest.raises(ProviderRuntimeError, match="route unavailable"):
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model=profile.model,
            send_messages=[],
            tools_schema=None,
            stream_responses=True,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state,
                agent_config={},
                stream=True,
                provider_profile=profile,
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="openai/Qwen/Qwen3.5-35B-A3B",
        )

    runtime.invoke.assert_not_called()
    retry_with_fallback.assert_not_called()

def test_provider_invoker_counts_reasoning_only_result_as_output() -> None:
    runtime_result = ProviderResult(
        messages=[],
        raw_response=None,
        reasoning_blocks=[{"type": "thinking", "text": "plan"}],
        metadata={},
    )
    runtime = _mk_runtime(runtime_result)
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    result, _ = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[],
        tools_schema=None,
        stream_responses=False,
        runtime_context=ProviderRuntimeContext(
            session_state=session_state,
            agent_config={},
        ),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    terminal = result.metadata["provider_exchange"]["terminal"]
    assert terminal["output_emitted"] is True
    assert terminal["assistant_messages"] == [
        {
            "role": "assistant",
            "content": [{"type": "thinking", "text": "plan"}],
        }
    ]


def test_provider_invoker_strips_control_sentinels_from_public_output_only():
    runtime_result = ProviderResult(
        messages=[
            ProviderMessage(
                role="assistant",
                content="answer\nTASK COMPLETE\n",
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name="echo",
                        arguments='{"marker":"TASK COMPLETE"}',
                    )
                ],
                finish_reason="stop",
                index=0,
            )
        ],
        raw_response=None,
        metadata={},
        provider_replay=[
            {
                "provider_id": "mock",
                "schema_version": "mock.v1",
                "replay_scope": "same_provider",
                "payload": {"signature": "opaque\nTASK COMPLETE\n"},
            }
        ],
    )
    runtime = _mk_runtime(runtime_result)
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    result, _used_streaming = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[{"role": "user", "content": "TASK COMPLETE"}],
        tools_schema=None,
        stream_responses=True,
        runtime_context=ProviderRuntimeContext(
            session_state=session_state,
            agent_config={},
        ),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    exchange = result.metadata["provider_exchange"]
    assistant_content = exchange["terminal"]["assistant_messages"][0]["content"]
    assert assistant_content[0] == {"type": "text", "text": "answer"}
    assert assistant_content[1]["type"] == "tool_call"
    assert assistant_content[1]["arguments"] == {"marker": "TASK COMPLETE"}
    assert exchange["request"]["messages"][0]["content"] == [
        {"type": "text", "text": "TASK COMPLETE"}
    ]
    assert exchange["terminal"]["provider_replay"][0]["payload"]["signature"] == (
        "opaque"
    )


def test_provider_invoker_leases_by_route_id_not_resolved_model():
    runtime_result = _provider_result()
    runtime = _mk_runtime(runtime_result)
    leased_routes = []

    @contextmanager
    def client_lease(route_id, leased_runtime):
        leased_routes.append((route_id, leased_runtime))
        yield "leased-client"

    invoker = _make_invoker(Mock(return_value=None), client_lease=client_lease)
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    result, used_streaming = invoker.invoke(
        runtime=runtime,
        client=None,
        model="openai/gpt-4o-mini",
        send_messages=[],
        tools_schema=None,
        stream_responses=False,
        runtime_context=ProviderRuntimeContext(session_state=session_state, agent_config={}),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="openrouter/openai/gpt-4o-mini",
    )

    assert result is runtime_result
    assert used_streaming is False
    assert leased_routes == [("openrouter/openai/gpt-4o-mini", runtime)]
    assert runtime.invoke.call_args.kwargs["client"] == "leased-client"
    assert runtime.invoke.call_args.kwargs["model"] == "openai/gpt-4o-mini"

def test_provider_invoker_stream_failure_falls_back_to_retry():
    fallback_result = _provider_result("fallback")
    fallback_result.metadata["provider_exchange_identity"] = {
        "provider_id": "openai",
        "runtime_id": "openai_responses",
        "route_id": "openai/gpt-5.2",
        "model": "gpt-5.2",
    }
    runtime = _mk_runtime(should_raise=True)
    retry_with_fallback = Mock(return_value=fallback_result)
    invoker = _make_invoker(retry_with_fallback)
    invoker.route_health.is_circuit_open.return_value = False

    session_state = _session_state()
    result, used_streaming = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[],
        tools_schema=None,
        stream_responses=True,
        runtime_context=ProviderRuntimeContext(session_state=session_state, agent_config={}),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    assert result is fallback_result
    assert used_streaming is False
    retry_with_fallback.assert_called_once()
    invoker.route_health.record_failure.assert_called()
    assert retry_with_fallback.call_args.kwargs["route_id"] == "cli_mock/dev"
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert exchange["provider"] == fallback_result.metadata[
        "provider_exchange_identity"
    ]
    assert exchange["request"]["stream"] is False


def test_provider_invoker_resets_lifecycle_only_events_before_safe_retry():
    runtime = _mk_runtime()
    streams = []

    def invoke(**kwargs):
        streams.append(kwargs["stream"])
        kwargs["context"].record_provider_event("response_start")
        if kwargs["stream"]:
            raise ProviderRuntimeError(
                "stream unavailable",
                kind="transport",
                details={"code": "stream_unavailable"},
            )
        return _provider_result()

    runtime.invoke.side_effect = invoke
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    result, used_streaming = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[],
        tools_schema=None,
        stream_responses=True,
        runtime_context=ProviderRuntimeContext(
            session_state=session_state, agent_config={}
        ),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    assert result.messages[0].content == "ok"
    assert used_streaming is False
    assert streams == [True, False]
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert [event["kind"] for event in exchange["events"]] == ["response_start"]
    assert exchange["request"]["stream"] is False


def test_provider_invoker_never_replays_after_recorder_observes_output():
    runtime = _mk_runtime()

    def invoke(**kwargs):
        kwargs["context"].record_provider_event(
            "text_start", {"item_id": "message-1"}
        )
        kwargs["context"].record_provider_event(
            "text_delta", {"item_id": "message-1", "delta": "partial"}
        )
        raise ProviderRuntimeError(
            "transport incorrectly claimed replay safety",
            kind="transport",
            output_emitted=False,
        )

    runtime.invoke.side_effect = invoke
    retry_with_fallback = Mock(return_value=_provider_result("must not run"))
    invoker = _make_invoker(retry_with_fallback)
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    with pytest.raises(ProviderRuntimeError):
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model="cli_mock/dev",
            send_messages=[],
            tools_schema=None,
            stream_responses=True,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state, agent_config={}
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="cli_mock/dev",
        )

    runtime.invoke.assert_called_once()
    retry_with_fallback.assert_not_called()
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert [event["kind"] for event in exchange["events"]] == [
        "response_start",
        "text_start",
        "text_delta",
    ]
    assert exchange["terminal"]["output_emitted"] is True



def test_provider_invoker_terminalizes_success_with_unclosed_stream_content():
    runtime = _mk_runtime()

    def invoke(**kwargs):
        kwargs["context"].record_provider_event(
            "text_start", {"item_id": "message-1"}
        )
        kwargs["context"].record_provider_event(
            "text_delta", {"item_id": "message-1", "delta": "partial"}
        )
        return _provider_result("partial")

    runtime.invoke.side_effect = invoke
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    with pytest.raises(ProviderRuntimeError) as exc_info:
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model="cli_mock/dev",
            send_messages=[],
            tools_schema=None,
            stream_responses=True,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state, agent_config={}
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="cli_mock/dev",
        )

    assert exc_info.value.safe_code == "provider_contract_error"
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert [event["kind"] for event in exchange["events"]] == [
        "response_start",
        "text_start",
        "text_delta",
    ]
    assert exchange["terminal"] == {
        "kind": "error",
        "output_emitted": True,
        "code": "provider_contract_error",
        "category": "protocol",
        "retryable": False,
        "evidence_refs": [],
    }

def test_provider_invoker_respects_circuit_breaker():
    fallback_result = _provider_result("circuit")
    runtime = _mk_runtime(_provider_result())
    retry_with_fallback = Mock(return_value=fallback_result)
    invoker = _make_invoker(retry_with_fallback)
    invoker.route_health.is_circuit_open.return_value = True

    session_state = _session_state()
    result, used_streaming = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[],
        tools_schema=None,
        stream_responses=True,
        runtime_context=ProviderRuntimeContext(session_state=session_state, agent_config={}),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    assert result is fallback_result
    assert used_streaming is False
    runtime.invoke.assert_not_called()
    retry_with_fallback.assert_called_once()
    assert retry_with_fallback.call_args.kwargs["route_id"] == "cli_mock/dev"

def test_provider_invoker_terminalizes_fallback_route_failure():
    runtime = _mk_runtime(should_raise=True)
    fallback_error = ProviderRuntimeError(
        "fallback failed",
        kind="transport",
        details={"code": "fallback_failed"},
    )
    invoker = _make_invoker(Mock(side_effect=fallback_error))
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    with pytest.raises(ProviderRuntimeError) as exc_info:
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model="cli_mock/dev",
            send_messages=[],
            tools_schema=None,
            stream_responses=False,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state, agent_config={}
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="cli_mock/dev",
        )

    assert exc_info.value is fallback_error
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert exchange["terminal"]["kind"] == "error"
    assert exchange["terminal"]["code"] == "fallback_failed"


@pytest.mark.parametrize("circuit_open", [False, True])
def test_provider_invoker_classifies_and_terminalizes_arbitrary_fallback_error(
    circuit_open: bool,
):
    runtime = _mk_runtime(should_raise=True)
    secret = "provider-secret-canary"
    invoker = _make_invoker(Mock(side_effect=ValueError(secret)))
    invoker.route_health.is_circuit_open.return_value = circuit_open
    session_state = _session_state()

    with pytest.raises(ProviderRuntimeError) as exc_info:
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model="cli_mock/dev",
            send_messages=[],
            tools_schema=None,
            stream_responses=False,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state, agent_config={}
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="cli_mock/dev",
        )

    assert exc_info.value.safe_code == "provider_fallback_error"
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert exchange["terminal"]["kind"] == "error"
    assert exchange["terminal"]["code"] == "provider_fallback_error"
    assert secret not in json.dumps(exchange, sort_keys=True)


def test_provider_invoker_accepts_canonical_tool_use_finish_reason():
    result = _provider_result()
    result.messages[0].finish_reason = "toolUse"
    runtime = _mk_runtime(result)
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    returned, used_fallback = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[],
        tools_schema=None,
        stream_responses=False,
        runtime_context=ProviderRuntimeContext(
            session_state=session_state,
            agent_config={},
        ),
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=1,
        route_id="cli_mock/dev",
    )

    assert returned is result
    assert used_fallback is False
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert exchange["terminal"]["finish_reason"] == "toolUse"


def test_provider_invoker_rejects_unknown_finish_with_error_terminal():
    result = _provider_result()
    result.messages[0].finish_reason = "unknown_finish"
    runtime = _mk_runtime(result)
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    with pytest.raises(ProviderRuntimeError) as exc_info:
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model="cli_mock/dev",
            send_messages=[],
            tools_schema=None,
            stream_responses=False,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state, agent_config={}
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="cli_mock/dev",
        )

    assert exc_info.value.safe_code == "provider_contract_error"
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert exchange["terminal"]["kind"] == "error"
    assert exchange["terminal"]["code"] == "provider_contract_error"


def test_provider_invoker_requires_exact_correlation():
    session_state = SessionState(workspace=".", image="cli", config={})
    invoker = _make_invoker(Mock(return_value=None))
    invoker.route_health.is_circuit_open.return_value = False

    with pytest.raises(ProviderContractError):
        invoker.invoke(
            runtime=_mk_runtime(_provider_result()),
            client=object(),
            model="cli_mock/dev",
            send_messages=[],
            tools_schema=None,
            stream_responses=False,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state, agent_config={}
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="cli_mock/dev",
        )

def test_provider_invoker_rejects_conflicting_correlation_sources():
    session_state = _session_state()
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        session_id="different-session",
        input_id="input-admitted",
        turn_id="turn-admitted",
    )

    with pytest.raises(ProviderContractError, match="sources disagree"):
        ProviderInvoker._resolve_correlation(context, session_state, 1)


def test_provider_invoker_records_provider_exchange_request_and_response():
    runtime_result = _provider_result()
    runtime_result.metadata["normalized_events"] = [{"type": "untrusted"}]
    runtime = _mk_runtime(runtime_result)
    retry_with_fallback = Mock(return_value=None)
    invoker = _make_invoker(retry_with_fallback)
    invoker.route_health.is_circuit_open.return_value = False

    session_state = _session_state()
    session_state.set_provider_metadata("session_id", "session-admitted")
    session_state.set_provider_metadata("input_id", "input-admitted")
    session_state.set_provider_metadata("turn_id", "turn-admitted")
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        session_id="session-admitted",
        input_id="input-admitted",
        turn_id="turn-admitted",
    )
    result, used_streaming = invoker.invoke(
        runtime=runtime,
        client=object(),
        model="cli_mock/dev",
        send_messages=[{"role": "user", "content": "hi"}],
        tools_schema=[{"name": "bash"}],
        stream_responses=False,
        runtime_context=context,
        session_state=session_state,
        markdown_logger=_markdown_logger(),
        turn_index=2,
        route_id="cli_mock/dev",
    )

    assert result is runtime_result
    assert used_streaming is False
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert exchange["schema_version"] == "bb.provider_exchange.v2"
    assert exchange["correlation"] == {
        "session_id": "session-admitted",
        "input_id": "input-admitted",
        "turn_id": "turn-admitted",
    }
    assert exchange["provider"] == {
        "provider_id": "mock",
        "runtime_id": "mock_chat",
        "route_id": "cli_mock/dev",
        "model": "cli_mock/dev",
    }
    assert exchange["request"] == {
        "stream": False,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "tools": [{"name": "bash", "parameters": {}}],
    }
    assert exchange["events"] == [{"sequence": 0, "kind": "response_start"}]
    assert exchange["terminal"] == {
        "kind": "done",
        "output_emitted": True,
        "finish_reason": "stop",
        "raw_provider_finish": "stop",
        "assistant_messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
        ],
        "provider_replay": [],
        "evidence_refs": [],
    }
    assert result.metadata["normalized_events"][0] == {
        "type": "response_start",
        "payload": {},
    }
    assert session_state.get_provider_metadata("last_provider_exchange_request") is None
    assert (
        session_state.get_provider_metadata("last_provider_exchange_response") is None
    )


@pytest.mark.parametrize(
    ("error", "records_route_failure"),
    [
        (ProviderRuntimeError("local sdk defect", kind="adapter"), False),
        (
            ProviderRuntimeError(
                "stream failed after output", kind="provider", output_emitted=True
            ),
            True,
        ),
    ],
)
def test_provider_invoker_does_not_fallback_when_stream_replay_is_unsafe(
    error, records_route_failure
):
    runtime = _mk_runtime()
    runtime.invoke.side_effect = error
    retry_with_fallback = Mock(return_value=_provider_result("must not be used"))
    invoker = _make_invoker(retry_with_fallback)
    invoker.route_health.is_circuit_open.return_value = False
    session_state = _session_state()

    with pytest.raises(ProviderRuntimeError) as exc_info:
        invoker.invoke(
            runtime=runtime,
            client=object(),
            model="openrouter/deepseek/deepseek-v4-flash-0731",
            send_messages=[],
            tools_schema=None,
            stream_responses=True,
            runtime_context=ProviderRuntimeContext(
                session_state=session_state, agent_config={}
            ),
            session_state=session_state,
            markdown_logger=_markdown_logger(),
            turn_index=1,
            route_id="openrouter/deepseek/deepseek-v4-flash-0731",
        )

    assert exc_info.value is error
    runtime.invoke.assert_called_once()
    retry_with_fallback.assert_not_called()
    assert invoker.route_health.record_failure.called is records_route_failure
    assert session_state.get_provider_metadata("streaming_disabled") is None
    exchange = session_state.get_provider_metadata("last_provider_exchange")
    assert exchange["terminal"]["kind"] == "error"
    assert exchange["terminal"]["category"] == error.kind
    assert exchange["terminal"]["output_emitted"] is error.output_emitted
    assert str(error) not in exchange["terminal"].values()
