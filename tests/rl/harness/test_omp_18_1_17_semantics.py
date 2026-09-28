from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess

import pytest

from conformance.comparators.oh_my_pi_18_1_17 import project_bb_trace
from breadboard_engine.provider.native_response import (
    NativeProviderResponse,
    NativeStreamTermination,
    NativeToolCall,
)
from breadboard.rl.harness.omp_native_tools import deny_declared_read_route, supplier_cli_invocation
from breadboard.rl.harness.runners.omp_semantics import (
    EditStore,
    LENGTH_SKIP_MESSAGE,
    NoRetryPolicy,
    OMPPhaseError,
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
from tests.rl.harness.test_omp_18_1_17_native_worker import _differential_bun, _differential_source_root


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



def test_omp_raw_response_is_one_wire_record_and_projects() -> None:
    state = OMPSemanticsState(task="raw response")
    assert state.begin_query() is None
    state.prepare_response(
        NativeProviderResponse(
            binding_digest="binding",
            request_digest="request",
            response_id="raw",
            model="capture",
            content="done",
            finish_reason="stop",
            raw_response={"id": "raw", "choices": [{"finish_reason": "stop"}]},
        )
    )
    assert len(state.native_responses) == 1
    trace = state.to_trace(
        requests=[{"messages": [], "tools": []}],
        runtime_inputs={
            "cwd": "/workspace",
            "home": "/scratch/home",
            "current_date": "2026-09-23",
            "package_dir": "/workspace/package",
        },
        effects={},
    )
    assert project_bb_trace(trace)["request_count"] == 1


def test_omp_raw_response_finish_reason_must_match_decoded() -> None:
    state = OMPSemanticsState(task="raw response")
    assert state.begin_query() is None
    with pytest.raises(OMPPhaseError, match="finish_reason disagrees"):
        state.prepare_response(
            NativeProviderResponse(
                binding_digest="binding",
                request_digest="request",
                response_id="raw",
                model="capture",
                content="done",
                finish_reason="stop",
                raw_response={"id": "raw", "choices": [{"finish_reason": "tool_calls"}]},
            )
        )

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


def test_omp_length_response_skips_execution_and_preserves_terminal_punctuation() -> None:
    class Worker:
        invocations = 0

        def execute_batch(self, calls: list[dict[str, object]]) -> list[dict[str, object]]:
            self.invocations += 1
            return []

    worker = Worker()
    state = OMPSemanticsState(task="do not run truncated tool", worker=worker)
    assert state.begin_query() is None
    parsed = state.prepare_response(
        NativeProviderResponse(
            binding_digest="binding",
            request_digest="request",
            response_id="length",
            model="capture",
            content=None,
            finish_reason="length",
            tool_calls=(NativeToolCall("a", "bash", '{"command":"touch marker"}'),),
        )
    )
    assert [call.id for call in parsed.calls] == ["a"]
    assert parsed.dispatch_calls == ()
    assert len(parsed.synthetic_results) == 1
    assert parsed.synthetic_results[0]["id"] == "a"
    assert parsed.synthetic_results[0]["content"].endswith("targets).")
    assert parsed.synthetic_results[0]["details"]["reason"] == "length"
    assert worker.invocations == 0
    assert state.effects == {}


def test_omp_prepare_refuses_excluded_native_capabilities() -> None:
    state = OMPSemanticsState(
        task="deny excluded routes",
        capability_denials={
            "pty": {"capability": "pty", "message": "OMP capability denied: pty"},
            "async": {"capability": "async", "message": "OMP capability denied: async"},
        },
    )
    assert state.begin_query() is None
    calls = (
        NativeToolCall("pty", "bash", '{"command":"printf x","pty":true}'),
        NativeToolCall("async", "bash", '{"command":"printf x","async":true}'),
    )
    state.prepare_response(NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="response",
        model="capture",
        content=None,
        finish_reason="tool_calls",
        tool_calls=calls,
    ))
    prepared = state.prepare_tools(calls)
    assert [item["error"] for item in prepared["calls"]] == [
        "OMP capability denied: pty",
        "OMP capability denied: async",
    ]


def test_omp_declared_route_exclusions_deny_before_worker_invocation() -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    native = json.loads((root / "native-config.json").read_text())
    policy = native["capability_denials"]
    samples = {
        "pty": ("bash", {"command": "printf x", "pty": True}),
        "async": ("bash", {"command": "printf x", "async": True}),
    }

    class Worker:
        invocations = 0

        def execute_batch(self, calls: list[dict[str, object]]) -> list[dict[str, object]]:
            self.invocations += 1
            return []

    calls = tuple(
        NativeToolCall(name, tool_name, json.dumps(arguments))
        for name, (tool_name, arguments) in samples.items()
    )
    worker = Worker()
    state = OMPSemanticsState(
        task="deny declared routes",
        worker=worker,
        capability_denials=policy,
    )
    assert state.begin_query() is None
    state.prepare_response(NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="response",
        model="capture",
        content=None,
        finish_reason="tool_calls",
        tool_calls=calls,
    ))
    prepared = state.prepare_tools(calls)
    assert [item["error"] for item in prepared["calls"]] == [
        policy[capability]["message"] for capability in samples
    ]
    results = state.execute_batch()["results"]
    assert [item["content"] for item in results] == [
        policy[capability]["message"] for capability in samples
    ]
    assert worker.invocations == 0
    assert state.effects == {}



def test_declared_read_route_preserves_literal_selector_filename(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    policy = json.loads((root / "native-config.json").read_text())["capability_denials"]
    literal = tmp_path / "bundle.zip:state.sqlite"
    literal.write_text("literal file", encoding="utf-8")
    deny_declared_read_route(
        "read", {"path": literal.name}, cwd=str(tmp_path), denial_policy=policy
    )
    literal.unlink()
    with pytest.raises(PermissionError, match="OMP capability denied: archive"):
        deny_declared_read_route(
            "read", {"path": literal.name}, cwd=str(tmp_path), denial_policy=policy
        )


def test_omp_mixed_denials_preserve_source_order_and_execute_allowed_calls() -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    policy = json.loads((root / "native-config.json").read_text())["capability_denials"]

    class Worker:
        calls: list[dict[str, object]] = []

        def execute_batch(self, calls: list[dict[str, object]]) -> list[dict[str, object]]:
            self.calls.extend(calls)
            return [
                {"id": call["id"], "completion_index": 0, "content": "allowed", "details": {}, "isError": False}
                for call in calls
            ]

    calls = (
        NativeToolCall("denied", "bash", '{"command":"printf x","pty":true}'),
        NativeToolCall("allowed", "read", '{"path":"./local.txt"}'),
    )
    worker = Worker()
    state = OMPSemanticsState(task="mixed static controls", worker=worker, capability_denials=policy)
    assert state.begin_query() is None
    state.prepare_response(NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="response",
        model="capture",
        content=None,
        finish_reason="tool_calls",
        tool_calls=calls,
    ))
    state.prepare_tools(calls)
    results = state.execute_batch()["results"]
    assert [item["content"] for item in results] == [
        "OMP capability denied: pty",
        "allowed",
    ]
    assert [call["id"] for call in worker.calls] == ["allowed"]


def test_omp_none_denied_batch_executes_every_call() -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    policy = json.loads((root / "native-config.json").read_text())["capability_denials"]

    class Worker:
        calls: list[dict[str, object]] = []

        def execute_batch(self, calls: list[dict[str, object]]) -> list[dict[str, object]]:
            self.calls.extend(calls)
            return [
                {"id": call["id"], "completion_index": index, "content": "allowed", "details": {}, "isError": False}
                for index, call in enumerate(calls)
            ]

    calls = (
        NativeToolCall("a", "read", '{"path":"./a.txt"}'),
        NativeToolCall("b", "bash", '{"command":"printf ok"}'),
    )
    worker = Worker()
    state = OMPSemanticsState(task="allowed routes", worker=worker, capability_denials=policy)
    assert state.begin_query() is None
    state.prepare_response(NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="response",
        model="capture",
        content=None,
        finish_reason="tool_calls",
        tool_calls=calls,
    ))
    state.prepare_tools(calls)
    results = state.execute_batch()["results"]
    assert [item["content"] for item in results] == ["allowed", "allowed"]
    assert [call["id"] for call in worker.calls] == ["a", "b"]

def test_omp_request_cap_refuses_before_native_query() -> None:
    state = OMPSemanticsState(task="bounded", request_cap=1)
    assert state.begin_query() is None
    state.prepare_response(NativeProviderResponse(
        binding_digest="binding",
        request_digest="request",
        response_id="response",
        model="capture",
        content=None,
        finish_reason="tool_calls",
        tool_calls=(NativeToolCall("pending", "bash", '{"command":"printf ok"}'),),
        raw_response={"choices": [{"finish_reason": "tool_calls"}]},
    ))
    refusal = state.begin_query()
    assert refusal is not None
    assert refusal["stopReason"] == "error"
    assert state.request_count == 1
    assert state.stream_fn_issued == 2
    trace = state.to_trace(
        requests=[{"model": "capture"}],
        runtime_inputs={"cwd": "/workspace", "home": "/home/capture", "current_date": "2026-09-23", "package_dir": "/packages"},
        effects={},
    )
    assert state.messages[-1] == {
        "role": "assistant",
        "content": "",
        "stopReason": "error",
        "isError": True,
    }
    assert trace["request_count"] == 1
    assert trace["exit"] == {"kind": "RequestLimitExceeded", "native_stop_reason": "tool_calls"}
    assert project_bb_trace(trace)["termination"]["native_stop_reason"] == "tool_calls"


def test_omp_initial_user_turn_is_one_text_part() -> None:
    request = OMPSemanticsState(task="do task", system_prompt="system").project_request()
    assert request["messages"][1] == {"role": "user", "content": [{"type": "text", "text": "do task"}]}


_PINNED_REMINDER_SCRIPT = """
const payload = JSON.parse(await Bun.stdin.text());
const { DateCwdReminderInjector } = await import(`${payload.root}/packages/coding-agent/src/session/date-cwd-reminder.ts`);
const context = new DateCwdReminderInjector().transform({ systemPrompt: payload.system, messages: payload.messages }, payload.date, payload.cwd);
process.stdout.write(JSON.stringify(context.messages));
"""


@pytest.mark.skipif(
    _differential_bun() is None or not _differential_source_root().is_dir(),
    reason="bun or pinned OMP source is unavailable on this host",
)
def test_omp_pinned_reminder_projection_keeps_reminder_and_task_parts(tmp_path: Path) -> None:
    source_root = _differential_source_root()
    # The reminder module's only runtime import is the pinned prompt renderer.
    shim = tmp_path / "resolve" / "@oh-my-pi" / "pi-utils"
    shim.mkdir(parents=True)
    (shim / "package.json").write_text('{"type":"module","exports":"./index.ts"}', encoding="utf-8")
    (shim / "index.ts").write_text(
        f"export * as prompt from {json.dumps(str(source_root / 'packages/utils/src/prompt.ts'))};\n",
        encoding="utf-8",
    )
    script = tmp_path / "reminder.ts"
    script.write_text(_PINNED_REMINDER_SCRIPT, encoding="utf-8")
    request = OMPSemanticsState(task="do task", system_prompt="system").project_request()
    completed = subprocess.run(
        [str(_differential_bun()), str(script)],
        input=json.dumps({
            "root": str(source_root),
            "system": [request["messages"][0]["content"]],
            "messages": request["messages"][1:],
            "date": "2026-09-23",
            "cwd": "/workspace/repo",
        }),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "NODE_PATH": str(tmp_path / "resolve")},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    [user] = json.loads(completed.stdout)
    assert user["role"] == "user"
    [reminder, task] = user["content"]
    assert reminder["type"] == "text"
    assert reminder["text"].startswith("<system-reminder>\nToday: 2026-09-23; current working directory: '/workspace/repo'.")
    assert task == {"type": "text", "text": "do task"}


_CUT_ID = "omp-capture-stream_fragments_broken-00-00"
_CUT_ARGUMENTS = "{\"command\":\"printf 'stream-"


def _cut_chunk(delta: dict) -> dict:
    return {"id": "cut", "object": "chat.completion.chunk", "created": 0, "model": "capture",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


def _terminated(reason: str, deltas: list[dict], *, content=None, calls=()) -> NativeProviderResponse:
    return NativeProviderResponse(
        binding_digest="binding", request_digest="request", response_id="cut",
        model="capture", content=content, finish_reason=None, tool_calls=tuple(calls),
        stream_termination=NativeStreamTermination(reason, tuple(_cut_chunk(d) for d in deltas)),
    )


def test_omp_truncated_stream_keeps_the_partial_call_with_an_undispatched_error_result() -> None:
    # Job-1203 stream_fragments_broken: text, then a call cut mid-arguments.
    deltas = [
        {"role": "assistant", "content": "broken stream"},
        {"content": " mid-argument"},
        {"tool_calls": [{"index": 0, "id": _CUT_ID, "type": "function",
                         "function": {"name": "bash", "arguments": ""}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": _CUT_ARGUMENTS}}]},
    ]
    state = OMPSemanticsState(task="cut")
    assert state.begin_query() is None
    result = state.prepare_response(_terminated(
        "stream_truncated", deltas, content="broken stream mid-argument",
        calls=[NativeToolCall(_CUT_ID, "bash", _CUT_ARGUMENTS)],
    ))
    assert result.dispatch_calls == ()
    assert [call.id for call in result.calls] == [_CUT_ID]
    assert result.assistant == {"role": "assistant", "stopReason": "error", "content": [
        {"type": "text", "text": "broken stream mid-argument"},
        {"type": "toolCall", "id": _CUT_ID, "name": "bash", "arguments": _CUT_ARGUMENTS},
    ]}
    [placeholder] = result.synthetic_results
    assert placeholder["content"] == (
        "Tool call was not executed because the provider stream ended with an error before "
        "the tool could run: OpenAI completions stream closed before a finish_reason was received"
    )
    assert placeholder["details"]["executed"] is False
    state.commit_tool_results(result.calls, list(result.synthetic_results))
    assert state.messages[-1]["role"] == "toolResult"
    assert state.messages[-1]["toolCallId"] == _CUT_ID
    assert state.messages[-1]["isError"] is True
    assert state.is_exited
    trace = state.to_trace(requests=[{}], runtime_inputs={}, effects={})
    assert trace["exit"] == {"kind": "Submitted", "native_stop_reason": None}
    assert trace["native_responses"] == [{"stream_termination": {
        "reason": "stream_truncated", "chunks": [_cut_chunk(d) for d in deltas],
    }}]


def test_omp_transport_error_after_text_is_a_text_only_error_stop() -> None:
    state = OMPSemanticsState(task="cut")
    assert state.begin_query() is None
    result = state.prepare_response(
        _terminated("transport_error", [{"content": "partial"}], content="partial")
    )
    assert (result.calls, result.synthetic_results) == ((), ())
    assert state.messages[-1]["stopReason"] == "error"
    assert state.exit_status == "Submitted"
    assert state.native_stop_reason is None


@pytest.mark.parametrize(("reason", "delta"), [
    ("stream_truncated", {"role": "assistant", "content": ""}),
    ("transport_error", {"tool_calls": [{"index": 0, "function": {"arguments": "{"}}]}),
    ("stream_truncated", {"content": "x", "reasoning_content": "think"}),
])
def test_omp_unreplayable_stream_terminations_fail_closed(reason: str, delta: dict) -> None:
    state = OMPSemanticsState(task="cut")
    assert state.begin_query() is None
    with pytest.raises(OMPPhaseError):
        state.prepare_response(_terminated(reason, [delta]))
