"""Unit tests for the Pi 0.57.1 engine slice (provider body, failure routing, semantics)."""
from __future__ import annotations

import dataclasses
from typing import Any, Mapping

import httpx
import pytest

from breadboard.rl.harness import native_stream_profiles
from breadboard.rl.harness.policy_provider import _pi_0_57_1_public_config
from breadboard.rl.harness.runners import pi_0_57_1_semantics as semantics
from breadboard.rl.harness.runners.base import RunnerDependencyError, RunnerProtocolError
from breadboard_engine.compilation.provider_response import (
    PI_0_57_1_RESPONSE_CONSUMER_ID, PI_RESPONSE_CONSUMER_ID,
)
from breadboard_engine.provider.contracts import (
    NativeProviderRequestFailure, OpenAICompletionsProviderProfile,
    ProviderRuntimeContext, ProviderRuntimeError,
)
from breadboard_engine.provider.native_response import (
    NativeProviderResponse, NativeStreamFragment, NativeToolCall,
)
from breadboard_engine.provider.runtimes.openai import chat_stream_decoder
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
from breadboard_engine.security import redaction
import tests.rl.harness.test_runner_conductor as rc

ZERO_COST = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
REGISTRY = {
    "provider_id": "vllm-local", "api": "openai-completions", "reasoning": True,
    "input": ["text"], "cost": ZERO_COST,
    "compat": {
        "supportsStore": False, "supportsDeveloperRole": False, "supportsReasoningEffort": False,
        "supportsUsageInStreaming": True, "maxTokensField": "max_tokens", "supportsStrictMode": True,
    },
}


def _profile() -> OpenAICompletionsProviderProfile:
    return OpenAICompletionsProviderProfile(
        model="served-model",
        scoped_credential="episode-secret",
        base_url="https://provider.example/v1",
        context_window=131_072,
        max_output_tokens=32_000,
        request_policy={"enable_thinking": None},
    )


def _state(clock: list[int] | None = None, **overrides: Any) -> semantics.Pi0571SemanticsState:
    ticks = iter(clock or range(1000, 2000))
    arguments = {
        "task": "task", "system_prompt": "system", "model_id": "served-model",
        "provider": "vllm-local", "api": "openai-completions", "cost": ZERO_COST,
        "current_date_time": "Friday, September 26, 2026 at 12:00:00 PM UTC",
        "clock": lambda: next(ticks),
    }
    arguments.update(overrides)
    return semantics.Pi0571SemanticsState(**arguments)


def _native(**fields: Any) -> NativeProviderResponse:
    base = {
        "binding_digest": "sha256:" + "a" * 64, "request_digest": "b" * 64,
        "response_id": "resp", "model": "served-model", "content": None,
        "finish_reason": "stop",
    }
    base.update(fields)
    return NativeProviderResponse(**base)


def test_chat_body_members_for_0_57_1_pass_tools_and_drop_n_without_store() -> None:
    tools = [{"type": "function", "function": {
        "name": "read", "description": "Read", "parameters": {"type": "object"}, "strict": False,
    }}]
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": [{"type": "text", "text": "t"}]}]
    runtime = object.__new__(OpenAIChatRuntime)
    body = runtime.profile_chat_request(
        _profile(), messages, tools,
        context=ProviderRuntimeContext(
            session_state=None, agent_config={},
            extra={"response_consumer_id": PI_0_57_1_RESPONSE_CONSUMER_ID},
        ),
    )
    assert set(body) == {"model", "messages", "stream", "stream_options", "max_tokens", "tools"}
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 32_000
    assert body["tools"] == tools
    assert body["messages"] == messages


def test_public_config_comes_from_model_registry() -> None:
    config = _pi_0_57_1_public_config(_profile(), {"model_registry": REGISTRY})
    assert config == {
        "id": "served-model", "name": "served-model", "api": "openai-completions",
        "provider": "vllm-local", "baseUrl": "https://provider.example/v1", "reasoning": True,
        "input": ["text"], "cost": ZERO_COST, "contextWindow": 131_072, "maxTokens": 32_000,
        "compat": REGISTRY["compat"],
    }


@pytest.mark.parametrize("missing", sorted(REGISTRY))
def test_public_config_fails_closed_on_missing_registry_key(missing: str) -> None:
    registry = {key: value for key, value in REGISTRY.items() if key != missing}
    with pytest.raises(ValueError):
        _pi_0_57_1_public_config(_profile(), {"model_registry": registry})


@pytest.mark.parametrize("override", [
    {"provider_id": "openai"},
    {"api": "openai-responses"},
    {"compat": {**REGISTRY["compat"], "supportsDeveloperRole": True}},
])
def test_public_config_rejects_forbidden_registry_values(override: Mapping[str, Any]) -> None:
    with pytest.raises(ValueError):
        _pi_0_57_1_public_config(_profile(), {"model_registry": {**REGISTRY, **override}})


class _StatusError(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"{status} status")
        self.response = httpx.Response(status, text=text, request=httpx.Request("POST", "https://p/v1"))


class _Completions:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def create(self, **_: Any) -> Any:
        raise self.error


def _create(error: Exception, *, native: bool) -> ProviderRuntimeError:
    client = type("C", (), {})()
    client.chat = type("Chat", (), {"completions": _Completions(error)})()
    decoder = chat_stream_decoder.OpenAIChatStreamDecoder(object())
    with pytest.raises(ProviderRuntimeError) as captured:
        decoder._create_stream(
            client, model="m", messages=[], tools=None, extra_body=None,
            request_options=None, native=native,
        )
    return captured.value


def test_http_status_error_details_on_native_stream() -> None:
    error = _create(_StatusError(500, "Internal Server Error"), native=True)
    assert error.kind == "provider"
    assert error.details == {
        "code": "provider_http_status", "http_status": 500,
        "response_body_text": "Internal Server Error",
    }


def test_http_status_body_omitted_when_scrubbing_changes_it_or_bound_exceeded() -> None:
    with redaction.secret_value_scope("episode-secret-value", allow_short=True):
        scrubbed = _create(_StatusError(401, "bad key episode-secret-value"), native=True)
    assert "response_body_text" not in scrubbed.details
    assert scrubbed.details["response_body_omitted"] == "redaction_required"
    large = "x" * (chat_stream_decoder.MAX_NATIVE_HTTP_ERROR_BODY_BYTES + 1)
    bounded = _create(_StatusError(502, large), native=True)
    assert "response_body_text" not in bounded.details
    assert bounded.details["response_body_omitted"] == "byte_limit"


def test_non_native_and_non_http_errors_keep_empty_details() -> None:
    assert _create(_StatusError(500, "x"), native=False).details == {}
    assert _create(RuntimeError("boom"), native=True).details == {}


def test_usage_follows_pinned_formula() -> None:
    usage = semantics.pinned_usage({
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 999,
        "prompt_tokens_details": {"cached_tokens": 30},
        "completion_tokens_details": {"reasoning_tokens": 5},
    }, {"input": 2, "output": 4, "cacheRead": 1, "cacheWrite": 0})
    assert usage == {
        "input": 70, "output": 25, "cacheRead": 30, "cacheWrite": 0, "totalTokens": 125,
        # models.js:23-27 order: (cost / 1e6) * tokens, then the left-to-right sum.
        "cost": {"input": 2 / 1e6 * 70, "output": 4 / 1e6 * 25, "cacheRead": 1 / 1e6 * 30,
                 "cacheWrite": 0, "total": 2 / 1e6 * 70 + 4 / 1e6 * 25 + 1 / 1e6 * 30 + 0 / 1e6 * 0},
    }
    zero = semantics.pinned_usage(None, ZERO_COST)
    assert zero["totalTokens"] == 0 and zero["cost"]["total"] == 0
    missing = semantics.pinned_usage({"prompt_tokens": 7, "prompt_tokens_details": None}, ZERO_COST)
    assert (missing["input"], missing["output"], missing["totalTokens"]) == (7, 0, 7)


@pytest.mark.parametrize(("finish", "stop"), [
    (None, "stop"), ("stop", "stop"), ("length", "length"),
    ("tool_calls", "toolUse"), ("function_call", "toolUse"),
])
def test_stop_mapping(finish: str | None, stop: str) -> None:
    assert semantics.pinned_stop_reason(finish) == stop


@pytest.mark.parametrize("finish", ["content_filter", "eos"])
def test_unmodeled_finish_reason_is_typed(finish: str) -> None:
    state = _state()
    assert state.begin_query() is None
    with pytest.raises(RunnerProtocolError) as captured:
        state.prepare_response(_native(finish_reason=finish), [])
    assert captured.value.code == "native_unmodeled_finish_reason"
    assert state.request_count == 0


def test_messages_follow_pinned_shapes_and_stream_order() -> None:
    state = _state()
    assert state.messages == [{"role": "user", "content": [{"type": "text", "text": "task"}], "timestamp": 1000}]
    state.begin_query()
    response = _native(
        finish_reason="tool_calls",
        tool_calls=(NativeToolCall("c1", "read", '{"path":"a"}'), NativeToolCall("c2", "ls", "{}")),
        stream_fragments=(
            NativeStreamFragment("content", 0, "look"),
            NativeStreamFragment("tool_arguments", 1, '{"path":"a"}', "c1", "read", 0),
            NativeStreamFragment("tool_arguments", 2, "{}", "c2", "ls", 1),
        ),
    )
    result = state.prepare_response(response, [{"path": "a"}, {}])
    assert result.assistant == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "look"},
            {"type": "toolCall", "id": "c1", "name": "read", "arguments": {"path": "a"}},
            {"type": "toolCall", "id": "c2", "name": "ls", "arguments": {}},
        ],
        "api": "openai-completions", "provider": "vllm-local", "model": "served-model",
        "usage": semantics.pinned_usage(None, ZERO_COST), "stopReason": "toolUse", "timestamp": 1001,
    }
    assert not state.is_exited
    committed = state.commit_tool_results(result.calls, [
        {"id": "c1", "completion_index": 0, "content": [{"type": "text", "text": "x"}], "isError": False},
        {"id": "c2", "completion_index": 1, "content": [{"type": "text", "text": "e"}], "details": {}, "isError": True},
    ])
    assert "details" not in committed[0] and committed[1]["details"] == {}
    assert list(committed[1]) == ["role", "toolCallId", "toolName", "content", "details", "isError", "timestamp"]


def test_request_cap_eight_refuses_ninth_attempt_unsent() -> None:
    state = _state()
    for _ in range(semantics.REQUEST_CAP):
        assert state.begin_query() is None
        state.prepare_response(_native(
            finish_reason="tool_calls", tool_calls=(NativeToolCall("c", "ls", "{}"),),
            stream_fragments=(NativeStreamFragment("tool_arguments", 0, "{}", "c", "ls", 0),),
        ), [{}])
        state.commit_tool_results(
            (semantics.Pi0571ToolCall("c", "ls", {}),),
            [{"id": "c", "completion_index": 0, "content": [], "isError": False}],
        )
    refused = state.begin_query()
    assert refused is not None
    assert state.request_count == 8 and state.stream_fn_issued == 9
    assert state.request_records[-1].sent is False
    assert state.exit_status == "RequestLimitExceeded" and state.native_stop_reason == "error"
    assert state.messages[-1]["stopReason"] == "error" and "errorMessage" not in state.messages[-1]
    trace = state.to_trace(requests=[], runtime_inputs={"cwd": "/w", "home": "/h", "package_dir": "/p"}, effects={})
    assert trace["version"] == "0.57.1" and trace["profile"] == "pi"
    assert trace["schema_version"] == "bb.e4.pi-replay-trace.v1"
    assert list(trace["runtime_inputs"]) == ["cwd", "home", "package_dir", "current_date_time"]


def test_profile_entry_declares_0_57_1_phases() -> None:
    profile = native_stream_profiles.NATIVE_STREAM_PROFILES[PI_0_57_1_RESPONSE_CONSUMER_ID]
    assert profile.target_id == "pi-r3@0.57.1" and profile.api_variant == "chat_completions"
    assert profile.provider_failure_phase == "project_provider_failure"
    assert profile.parse_arguments_phase == "parse_streaming_json_batch"
    assert profile.accepts_truncated_stream is False and profile.provider_failure_terminates is True
    with pytest.raises(ValueError):
        profile.state_factory("t", "s", {"model_config": {"id": "m", "provider": "p", "api": "a", "cost": ZERO_COST}})


class _Pi057Port(rc._NativeCloseTestPort):
    def __init__(self, *args: Any, request_body: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request_body = request_body
        self.failure_payloads: list[Mapping[str, Any]] = []

    async def invoke_native_phase(self, operation: str, payload: Mapping[str, Any], **kwargs: Any) -> Mapping[str, Any]:
        if operation == "project_request" and self.request_body is not None:
            self.operations.append(operation)
            return {
                "schema_version": "bb.pi-native.test.v1", "kind": "request",
                "messages": [], "tools": [], "request_body": dict(self.request_body),
            }
        if operation == "project_provider_failure":
            self.operations.append(operation)
            self.failure_payloads.append(dict(payload))
            return {"schema_version": "bb.pi-native.test.v1", "kind": "provider_failure", "message": {
                "role": "assistant", "content": [], "stopReason": "error", "errorMessage": "pinned",
            }}
        return await super().invoke_native_phase(operation, payload, **kwargs)


async def _run(monkeypatch: pytest.MonkeyPatch, port: _Pi057Port, *, error: BaseException | None = None) -> Any:
    plan, client, _ = rc._native_close_test_case(monkeypatch)
    registry = rc.conductor_module.NATIVE_STREAM_PROFILES
    registry[PI_RESPONSE_CONSUMER_ID] = dataclasses.replace(
        registry[PI_RESPONSE_CONSUMER_ID],
        provider_failure_terminates=True,
        provider_failure_phase="project_provider_failure",
        state_factory=lambda task, system_prompt, bootstrap: _state(task=task, system_prompt=system_prompt),
    )
    client.invoke_error = error
    session, _, _, _, _, _ = await rc._open(plan=plan, client=client, tools=port)
    try:
        return await session.run(rc.ConductorRunRequest({"prompt": "task"}))
    finally:
        await session.close()


def _port(**kwargs: Any) -> _Pi057Port:
    return _Pi057Port((rc._tool_binding("read-file"),), **kwargs)


def _failure(details: Mapping[str, Any]) -> RunnerDependencyError:
    failure = NativeProviderRequestFailure(
        ProviderRuntimeError("500 status", details=dict(details)),
        request_body={"model": "m", "messages": [], "tools": []},
    )
    error = RunnerDependencyError("episode provider invocation failed", code="provider_invocation_failed")
    error.__cause__ = failure
    return error


async def test_request_body_mismatch_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _port(request_body={"model": "other", "messages": [], "tools": []})
    with pytest.raises(RunnerProtocolError) as captured:
        await _run(monkeypatch, port)
    assert captured.value.code == "native_request_body_mismatch"


async def test_provider_failure_phase_routes_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    port = _port()
    result = await _run(monkeypatch, port, error=_failure({
        "code": "provider_http_status", "http_status": 500, "response_body_text": "Internal Server Error",
    }))
    assert result.termination == rc.RunnerTermination.POLICY_INCOMPLETE
    [payload] = port.failure_payloads
    assert payload["http_status"] == 500 and payload["response_body_text"] == "Internal Server Error"
    assert payload["messages"][0]["role"] == "user"
    last = result.response["replay_trace"]["messages"][-1]
    assert last["errorMessage"] == "pinned"
    assert result.response["replay_trace"]["termination"] == {"kind": "error", "native_stop_reason": "error"}


@pytest.mark.parametrize(("details", "code"), [
    ({}, "native_non_http_transport_failure"),
    ({"code": "provider_http_status", "http_status": 500, "response_body_omitted": "byte_limit"},
     "native_provider_failure_body_withheld"),
])
async def test_provider_failure_without_usable_http_details_is_typed(
    monkeypatch: pytest.MonkeyPatch, details: Mapping[str, Any], code: str,
) -> None:
    port = _port()
    with pytest.raises(RunnerProtocolError) as captured:
        await _run(monkeypatch, port, error=_failure(details))
    assert captured.value.code == code
    assert "project_provider_failure" not in port.operations
