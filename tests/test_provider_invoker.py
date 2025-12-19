from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_coder_prototype.provider_invoker import ProviderInvoker
from agentic_coder_prototype.provider_runtime import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from agentic_coder_prototype.state.session_state import SessionState
from agentic_coder_prototype.messaging.markdown_logger import MarkdownLogger


def _make_invoker(retry_with_fallback):
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
    return SessionState(workspace=".", image="cli", config={})


def _markdown_logger():
    logger = Mock(spec=MarkdownLogger)
    return logger


def _provider_result(payload: str = "ok"):
    message = ProviderMessage(role="assistant", content=payload, tool_calls=[], finish_reason="stop", index=0)
    return ProviderResult(messages=[message], raw_response=None, metadata={})


def test_provider_invoker_stream_success():
    runtime_result = _provider_result()
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


def test_provider_invoker_stream_failure_falls_back_to_retry():
    fallback_result = _provider_result("fallback")
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
