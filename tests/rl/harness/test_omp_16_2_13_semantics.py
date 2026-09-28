"""Unit tests for OMP 16.2.13 semantics state and profile.

Proves state transitions against packet facts (o0, o3, o5, o8, o9, o10, o11).
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from breadboard_engine.compilation.provider_response import OMP_16_2_13_RESPONSE_CONSUMER_ID
from breadboard_engine.provider.native_response import (
    NativeProviderResponse,
    NativeStreamFragment,
    NativeStreamTermination,
    NativeToolCall,
)
from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES, _omp_16_2_13_state
from breadboard.rl.harness.runners.omp_16_2_13_semantics import (
    CONSUMER_ID,
    LOCAL_ADAPTER_ID,
    PHASE_SCHEMA_VERSION,
    PROFILE_NAME,
    PROFILE_VERSION,
    REQUEST_CAP,
    TARGET_ID,
    TOOL_NAMES,
    TRACE_SCHEMA_VERSION,
    Omp16213RequestRecord,
    Omp16213ResponseResult,
    Omp16213SemanticsError,
    Omp16213SemanticsState,
    Omp16213ToolCall,
    Omp16213UnmodeledFinishReason,
    pinned_stop_reason,
    pinned_usage,
)

SAMPLE_ABORTED_TEXT = (
    "Tool call was not executed because the assistant hit its output token limit "
    "(stop_reason: length) before the arguments could complete; the recorded "
    "arguments are truncated and unsafe to run."
)


def _make_state(
    task: str = "Test task",
    cap: int = 8,
    clock_val: int = 1000,
    length_aborted_message: str = SAMPLE_ABORTED_TEXT,
) -> Omp16213SemanticsState:
    return Omp16213SemanticsState(
        task=task,
        system_prompt="System prompt",
        model_id="Qwen/Qwen3.5-35B-A3B",
        provider="vllm-local",
        api="openai-completions",
        cost={"input": 0.14, "output": 1.0, "cacheRead": 0.049999999999999996, "cacheWrite": 0.0},
        current_date_time="2026-09-26",
        length_aborted_message=length_aborted_message,
        request_cap=cap,
        case_id="test_case",
        runtime_inputs={
            "cwd": "/testbed",
            "home": "/capture/home",
            "current_date": "2026-09-26",
            "package_dir": "/opt/omp/node_modules/@oh-my-pi/pi-coding-agent",
        },
        clock=lambda: clock_val,
    )


def _make_native(
    *,
    content: str | None = None,
    finish_reason: str | None = None,
    tool_calls: tuple[NativeToolCall, ...] = (),
    usage: Mapping[str, Any] | None = None,
    stream_fragments: tuple[NativeStreamFragment, ...] = (),
    stream_termination: NativeStreamTermination | None = None,
) -> NativeProviderResponse:
    return NativeProviderResponse(
        binding_digest="b" * 64,
        request_digest="r" * 64,
        response_id="resp-001",
        model="Qwen/Qwen3.5-35B-A3B",
        content=content,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        usage=usage,
        stream_fragments=stream_fragments,
        stream_termination=stream_termination,
    )


def test_initial_state() -> None:
    state = _make_state()
    assert state.task == "Test task"
    assert state.request_count == 0
    assert state.stream_fn_issued == 0
    assert state.tool_admissions == 0
    assert not state.is_exited
    assert state.exit_status is None
    assert state.native_stop_reason is None
    assert len(state.messages) == 1
    assert state.messages[0]["role"] == "user"
    assert state.messages[0]["content"] == [{"type": "text", "text": "Test task"}]
    assert state.messages[0]["timestamp"] == 1000


def test_state_construction_validations() -> None:
    # Missing required cost keys
    with pytest.raises(ValueError, match="cost must carry"):
        Omp16213SemanticsState(
            task="Task",
            system_prompt="Sys",
            model_id="m",
            provider="p",
            api="a",
            cost={"input": 0.1},
            current_date_time="2026-09-26",
            length_aborted_message="msg",
            runtime_inputs={"cwd": "c", "home": "h", "current_date": "d", "package_dir": "p"},
        )

    # Empty current_date_time
    with pytest.raises(ValueError, match="current_date_time must be non-empty text"):
        Omp16213SemanticsState(
            task="Task",
            system_prompt="Sys",
            model_id="m",
            provider="p",
            api="a",
            cost={"input": 0.1, "output": 1.0, "cacheRead": 0, "cacheWrite": 0},
            current_date_time="",
            length_aborted_message="msg",
            runtime_inputs={"cwd": "c", "home": "h", "current_date": "d", "package_dir": "p"},
        )

    # Empty length_aborted_message
    with pytest.raises(ValueError, match="length_aborted_message must be non-empty text"):
        Omp16213SemanticsState(
            task="Task",
            system_prompt="Sys",
            model_id="m",
            provider="p",
            api="a",
            cost={"input": 0.1, "output": 1.0, "cacheRead": 0, "cacheWrite": 0},
            current_date_time="2026-09-26",
            length_aborted_message="",
            runtime_inputs={"cwd": "c", "home": "h", "current_date": "d", "package_dir": "p"},
        )


def test_begin_query_and_cap_guard_o9() -> None:
    state = _make_state(cap=2)
    # Turn 0: admit query 1
    res1 = state.begin_query()
    assert res1 is None
    assert state.stream_fn_issued == 1

    # Simulate response 1
    state.prepare_response(_make_native(content="Response 1", finish_reason="stop"))
    assert state.request_count == 1
    assert state.exit_status == "Submitted"
    assert state.is_exited

    # Re-opening an exited state raises
    with pytest.raises(Omp16213SemanticsError, match="episode already exited"):
        state.begin_query()

    # Test cap 8 guard
    state2 = _make_state(cap=2)
    state2.begin_query()
    state2.prepare_response(
        _make_native(
            tool_calls=(NativeToolCall("call_1", "bash", '{"command":"ls"}'),),
            finish_reason="tool_calls",
        ),
        parsed_arguments=[{"command": "ls"}],
    )
    assert not state2.is_exited
    assert state2.request_count == 1

    # Turn 1: admit query 2
    state2.begin_query()
    state2.prepare_response(
        _make_native(
            tool_calls=(NativeToolCall("call_2", "bash", '{"command":"ls"}'),),
            finish_reason="tool_calls",
        ),
        parsed_arguments=[{"command": "ls"}],
    )
    assert state2.request_count == 2
    assert not state2.is_exited

    # Turn 2: query 3 exceeds cap 2 -> refused unsent before any HTTP
    res3 = state2.begin_query()
    assert res3 is not None
    assert res3.quiescent is True
    assert res3.stop_reason == "error"
    assert state2.exit_status == "RequestLimitExceeded"
    assert state2.native_stop_reason == "error"
    assert state2.is_exited is True
    assert len(state2.request_records) == 3
    assert state2.request_records[-1].sent is False


def test_prepare_response_text_stop_o0() -> None:
    state = _make_state()
    state.begin_query()
    native = _make_native(
        content="READY",
        finish_reason="stop",
        usage={"prompt_tokens": 1200, "completion_tokens": 1, "total_tokens": 1201},
    )
    res = state.prepare_response(native)
    assert res.stop_reason == "stop"
    assert res.quiescent is True
    assert res.calls == ()
    assert res.dispatch_calls == ()
    assert res.synthetic_results == ()
    assert state.exit_status == "Submitted"
    assert state.native_stop_reason == "stop"
    assert state.is_exited is True

    # Assistant message inspection
    msg = state.messages[-1]
    assert msg["role"] == "assistant"
    assert msg["content"] == [{"type": "text", "text": "READY"}]
    assert msg["stopReason"] == "stop"
    assert msg["usage"]["input"] == 1200
    assert msg["usage"]["output"] == 1
    assert msg["usage"]["totalTokens"] == 1201


def test_prepare_response_tool_dispatch_o1() -> None:
    state = _make_state()
    state.begin_query()
    native = _make_native(
        content="Running tool",
        tool_calls=(
            NativeToolCall("c1", "read", '{"path":"file.txt"}'),
            NativeToolCall("c2", "bash", '{"command":"echo 1"}'),
        ),
        finish_reason="tool_calls",
        stream_fragments=(
            NativeStreamFragment("content", 0, "Running tool"),
            NativeStreamFragment("tool_arguments", 0, '{"path":', tool_index=0),
            NativeStreamFragment("tool_arguments", 1, '{"command":', tool_index=1),
        ),
    )
    res = state.prepare_response(
        native,
        parsed_arguments=[{"path": "file.txt"}, {"command": "echo 1"}],
    )
    assert res.stop_reason == "toolUse"
    assert res.quiescent is False
    assert len(res.calls) == 2
    assert len(res.dispatch_calls) == 2
    assert res.synthetic_results == ()
    assert not state.is_exited
    assert state.exit_status is None
    assert state.native_stop_reason == "toolUse"

    # Commit results
    committed = state.commit_tool_results(
        res.calls,
        [
            {"id": "c1", "completion_index": 0, "content": [{"type": "text", "text": "hello"}], "isError": False},
            {"id": "c2", "completion_index": 1, "content": [{"type": "text", "text": "1"}], "isError": False},
        ],
    )
    assert len(committed) == 2
    assert state.tool_admissions == 2
    assert state.messages[-2]["toolCallId"] == "c1"
    assert state.messages[-1]["toolCallId"] == "c2"


def test_prepare_response_length_cutoff_o11() -> None:
    state = _make_state(length_aborted_message=SAMPLE_ABORTED_TEXT)
    state.begin_query()
    native = _make_native(
        content="cut off before tool execution",
        tool_calls=(
            NativeToolCall("c_cut", "bash", '{"command":"printf forbidden > cutoff_marker.txt"}'),
        ),
        finish_reason="length",
    )
    res = state.prepare_response(
        native,
        parsed_arguments=[{"command": "printf forbidden > cutoff_marker.txt"}],
    )
    assert res.stop_reason == "length"
    assert res.quiescent is False
    assert len(res.calls) == 1
    assert res.dispatch_calls == ()  # Tool call is not dispatched
    assert len(res.synthetic_results) == 1
    syn = res.synthetic_results[0]
    assert syn["id"] == "c_cut"
    assert syn["completion_index"] == 0
    assert syn["isError"] is True
    assert syn["content"][0]["text"] == SAMPLE_ABORTED_TEXT
    assert not state.is_exited
    assert state.exit_status is None
    assert state.native_stop_reason == "length"


def test_prepare_response_severed_stream_o10() -> None:
    state = _make_state()
    state.begin_query()
    # Stream severed mid-arguments without finish_reason
    native = _make_native(
        content="broken stream mid-argument",
        tool_calls=(
            NativeToolCall("c_sev", "bash", '{"command":"printf \'"}'),
        ),
        finish_reason=None,
        stream_termination=NativeStreamTermination("stream_truncated", ({"choices": []},)),
    )
    res = state.prepare_response(
        native,
        parsed_arguments=[{"command": "printf '"}],
    )
    # stopReason is promoted to toolUse so the tool is dispatched and executed
    assert res.stop_reason == "toolUse"
    assert res.quiescent is False
    assert len(res.calls) == 1
    assert len(res.dispatch_calls) == 1
    assert res.synthetic_results == ()
    assert not state.is_exited
    assert state.native_stop_reason == "toolUse"


def test_commit_provider_failure_o5() -> None:
    state = _make_state()
    state.begin_query()
    err_msg = {
        "role": "assistant",
        "content": [],
        "api": "openai-completions",
        "provider": "vllm-local",
        "model": "Qwen/Qwen3.5-35B-A3B",
        "usage": pinned_usage(None, state.cost),
        "stopReason": "error",
        "errorMessage": "HTTP 500: Internal Server Error",
    }
    state.commit_provider_failure(err_msg)
    assert state.is_exited is True
    assert state.exit_status == "error"
    assert state.native_stop_reason == "error"
    assert state.messages[-1]["errorMessage"] == "HTTP 500: Internal Server Error"


def test_to_trace_keys() -> None:
    state = _make_state()
    state.begin_query()
    state.prepare_response(_make_native(content="Final text", finish_reason="stop"))
    trace = state.to_trace(
        requests=[{"model": "Qwen/Qwen3.5-35B-A3B", "messages": []}],
        runtime_inputs={
            "cwd": "/testbed",
            "home": "/capture/home",
            "current_date": "2026-09-26",
            "package_dir": "/opt/omp/node_modules/@oh-my-pi/pi-coding-agent",
        },
        effects={"files_written": []},
    )
    expected_keys = {
        "schema_version",
        "role",
        "profile",
        "version",
        "case_id",
        "request_count",
        "stream_fn_issued",
        "messages",
        "effects",
        "termination",
        "requests",
        "runtime_inputs",
    }
    assert set(trace.keys()) == expected_keys
    assert len(trace) == 12
    assert trace["schema_version"] == TRACE_SCHEMA_VERSION
    assert trace["role"] == "replay"
    assert trace["profile"] == PROFILE_NAME
    assert trace["version"] == PROFILE_VERSION
    assert trace["termination"]["kind"] == "Submitted"
    assert trace["termination"]["native_stop_reason"] == "stop"
    assert set(trace["runtime_inputs"].keys()) == {"cwd", "home", "current_date", "package_dir"}


def test_profile_registration_and_state_factory() -> None:
    assert OMP_16_2_13_RESPONSE_CONSUMER_ID in NATIVE_STREAM_PROFILES
    profile = NATIVE_STREAM_PROFILES[OMP_16_2_13_RESPONSE_CONSUMER_ID]
    assert profile.consumer_id == CONSUMER_ID
    assert profile.target_id == TARGET_ID
    assert profile.target_version == 3
    assert profile.api_variant == "chat_completions"
    assert profile.phase_schema_version == PHASE_SCHEMA_VERSION
    assert profile.tool_order == TOOL_NAMES
    assert profile.max_turns == REQUEST_CAP
    assert profile.action_timeout_ms == 40_000
    assert profile.episode_timeout_seconds == 120
    assert profile.ack_policy == "none"
    assert profile.incomplete_stop_reasons == frozenset({"error", "aborted"})
    assert profile.runtime_input_names == ("cwd", "home", "current_date", "package_dir")
    assert profile.accepts_truncated_stream is True
    assert profile.provider_failure_terminates is True
    assert profile.provider_failure_phase == "project_provider_failure"
    assert profile.parse_arguments_phase == "parse_streaming_json_batch"

    # State factory validation tests
    valid_bootstrap = {
        "model_config": {
            "id": "Qwen/Qwen3.5-35B-A3B",
            "provider": "vllm-local",
            "api": "openai-completions",
            "cost": {"input": 0.14, "output": 1.0, "cacheRead": 0.049999999999999996, "cacheWrite": 0.0},
        },
        "runtime_inputs": {
            "cwd": "/testbed",
            "home": "/capture/home",
            "current_date": "2026-09-26",
            "package_dir": "/opt/omp/node_modules/@oh-my-pi/pi-coding-agent",
        },
        "current_date_time": "2026-09-26",
        "length_aborted_message": SAMPLE_ABORTED_TEXT,
    }
    state = _omp_16_2_13_state("Task", "Prompt", valid_bootstrap)
    assert isinstance(state, Omp16213SemanticsState)
    assert state.model_id == "Qwen/Qwen3.5-35B-A3B"
    assert state.length_aborted_message == SAMPLE_ABORTED_TEXT

    # Malformed bootstrap: missing length_aborted_message
    with pytest.raises(ValueError, match="missing length_aborted_message"):
        invalid = dict(valid_bootstrap)
        del invalid["length_aborted_message"]
        _omp_16_2_13_state("Task", "Prompt", invalid)

    # Malformed bootstrap: incomplete model_config
    with pytest.raises(ValueError, match="model_config is malformed"):
        invalid = dict(valid_bootstrap)
        invalid["model_config"] = {"id": "m"}
        _omp_16_2_13_state("Task", "Prompt", invalid)
