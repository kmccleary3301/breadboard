from __future__ import annotations

import asyncio

import pytest

from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall
from breadboard.rl.harness.omp_native_tools import supplier_cli_invocation
from breadboard.rl.harness.runners.omp_semantics import (
    EditStore,
    NoRetryPolicy,
    OMPSemanticsState,
    SeenAnchorError,
    ToolCall,
    ToolResult,
    TurnRecovery,
    append_wall_time_notice,
    enforce_seen_lines,
    hashline_tag,
    run_tool_batch,
    schedule_tool_calls,
)


def test_hashline_tag_is_xxh32_low16_and_trailing_display_space_insensitive() -> None:
    assert hashline_tag("one  \n two\t\r\n") == hashline_tag("one\n two\n")
    assert hashline_tag("one\n") != hashline_tag("two\n")


def test_seen_gate_checks_only_collected_anchors_and_empty_set_bypasses() -> None:
    store = EditStore()
    store.record("f.txt", "one\ntwo\nthree\n", seen_lines=[1])
    with pytest.raises(SeenAnchorError) as raised:
        enforce_seen_lines(store, "f.txt", "one\ntwo\nthree\n", [1, 2])
    assert raised.value.unseen == (2,)

    empty = EditStore()
    empty.record("f.txt", "one\ntwo\n", seen_lines=[])
    enforce_seen_lines(empty, "f.txt", "one\ntwo\n", [99])


def test_length_cutoff_synthetic_results_never_invoke_executor() -> None:
    calls = [ToolCall("a", "bash", {"command": "touch a"})]
    invoked: list[str] = []

    async def execute(call: ToolCall) -> str:
        invoked.append(call.id)
        return "executed"

    result = asyncio.run(run_tool_batch(calls, "length", execute))
    assert invoked == []
    assert result == [ToolResult.skipped_result(calls[0])]


def test_shared_exclusive_barriers_and_completion_order() -> None:
    calls = [ToolCall("read-slow", "read"), ToolCall("bash-fast", "bash"), ToolCall("write", "write"), ToolCall("read-last", "read")]
    started: list[str] = []

    async def execute(call: ToolCall) -> str:
        started.append(call.id)
        if call.id == "read-slow":
            await asyncio.sleep(0.03)
        elif call.id == "bash-fast":
            await asyncio.sleep(0.005)
        return call.id

    result = asyncio.run(schedule_tool_calls(calls, execute))
    assert [item for item in result if isinstance(item, str)] == ["bash-fast", "read-slow", "write", "read-last"]
    assert started.index("write") > started.index("read-slow")
    assert started.index("read-last") > started.index("write")


def test_no_retry_and_turn_recovery_are_separate() -> None:
    policy = NoRetryPolicy()
    assert policy.begin_attempt() == 1
    assert policy.should_retry("provider_error") is False
    recovery = TurnRecovery()
    empty = {"role": "assistant", "content": "", "finish_reason": "stop"}
    assert [recovery.recover_empty_turn(empty) for _ in range(4)] == [True, True, True, False]
    assert recovery.corrective_continuations == 3
    assert policy.attempts == 1


def test_bash_wall_time_notice_keeps_two_decimal_source_slot() -> None:
    assert append_wall_time_notice("output", 8123.4).endswith("Wall time: 8.12 seconds")


def test_pinned_cli_invocation_is_exact_and_not_a_shell_fallback() -> None:
    invocation = supplier_cli_invocation(cwd="/workspace/repo", model="capture/capture", task="do task")
    assert invocation.command[:3] == ("/opt/omp/runtime/bun-linux-x64-baseline/bun", "/opt/omp/source/oh-my-pi-3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec/packages/coding-agent/src/cli.ts", "-p")
    assert "/bin/sh" not in invocation.command


def test_retention_counts_utf16_units_and_broken_stream_does_not_dispatch() -> None:
    store = EditStore(max_total_units=2)
    store.record("emoji.txt", "😀")
    store.record("ascii.txt", "a")
    assert store.head("emoji.txt") is None
    assert store.head("ascii.txt") is not None

    calls = [ToolCall("broken", "bash", {"command": "printf forbidden"})]
    invoked: list[str] = []

    async def execute(call: ToolCall) -> str:
        invoked.append(call.id)
        return "executed"

    result = asyncio.run(run_tool_batch(calls, "error", execute))
    assert result == []
    assert invoked == []


def test_source_exact_length_and_empty_bash_text() -> None:
    result = asyncio.run(run_tool_batch([ToolCall("length", "bash")], "length", lambda call: "bad"))[0]
    assert result.output.startswith("Tool call was not executed because the assistant hit its output token limit (stop_reason: length)")
    assert append_wall_time_notice("", 1000) == "(no output)\n\nWall time: 1.00 seconds"


def test_rerun4_six_case_replay_policy_matrix() -> None:
    # Offline replay of the six rerun4 oracle decisions: only length receives
    # synthetic results; broken streams dispatch nothing; ordinary tool turns
    # dispatch once and preserve the workspace-effect command.
    cases = {
        "normal_multiturn": ("tool_calls", True),
        "malformed_tool_call": ("tool_calls", True),
        "budget_limit_stop": ("tool_calls", True),
        "stream_fragments_broken": ("error", False),
        "length_cutoff_skips_tool": ("length", False),
        "process_lifecycle": ("tool_calls", True),
    }

    async def execute(call: ToolCall) -> str:
        return str(call.arguments.get("command", ""))

    for case_id, (reason, should_dispatch) in cases.items():
        calls = [ToolCall(case_id, "bash", {"command": f"printf {case_id}"})]
        result = asyncio.run(run_tool_batch(calls, reason, execute))
        assert bool(result) is (reason == "length" or should_dispatch)


def test_omp_empty_stop_uses_bounded_native_recovery() -> None:
    state = OMPSemanticsState(task="continue")
    assert state.begin_query() is None
    response = NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="empty",
        model="capture",
        content=None,
        finish_reason="stop",
    )
    result = state.prepare_response(response)
    assert result.quiescent is False
    assert state.is_exited is False
    assert state.messages[-1]["role"] == "developer"
    assert "Attempt #1/3" in state.messages[-1]["content"]


def test_omp_phase_state_commits_native_completion_order() -> None:
    class Worker:
        def execute_batch(self, calls: list[dict[str, object]]) -> list[dict[str, object]]:
            return [
                {"id": calls[0]["id"], "completion_index": 1, "content": "slow", "details": {}, "isError": False},
                {"id": calls[1]["id"], "completion_index": 0, "content": "fast", "details": {}, "isError": False},
            ]

    state = OMPSemanticsState(task="do work", worker=Worker())
    assert state.begin_query() is None
    response = NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="response",
        model="capture",
        content=None,
        finish_reason="tool_calls",
        tool_calls=(
            NativeToolCall("a", "bash", '{"command":"slow"}'),
            NativeToolCall("b", "read", '{"path":"fast"}'),
        ),
    )
    prepared_response = state.prepare_response(response)
    assert [call.id for call in prepared_response.calls] == ["a", "b"]
    state.prepare_tools(prepared_response.calls)
    batch = state.execute_batch()
    assert [item["id"] for item in batch["results"]] == ["a", "b"]
    assert [item["completion_index"] for item in batch["results"]] == [1, 0]
    state.commit_tool_results(prepared_response.calls, batch["results"])
    assert [item["toolCallId"] for item in state.messages[-2:]] == ["b", "a"]


def test_omp_request_cap_refuses_before_native_query() -> None:
    state = OMPSemanticsState(task="bounded", request_cap=1)
    assert state.begin_query() is None
    state.prepare_response(NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="response",
        model="capture",
        content="done",
        finish_reason="stop",
    ))
    refusal = state.begin_query()
    assert refusal is not None
    assert refusal["stopReason"] == "error"
    assert state.request_count == 1
    assert state.stream_fn_issued == 2
