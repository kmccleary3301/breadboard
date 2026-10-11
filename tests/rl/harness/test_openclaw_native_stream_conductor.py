import hashlib
import json
import re
from pathlib import Path
import subprocess
from typing import Any, Mapping
from breadboard.rl.harness import sandbox as sandbox_module
import pytest
from breadboard.rl.harness.runners.base import RunnerDependencyError, RunnerProtocolError, RunnerTermination, thaw_json
from breadboard.rl.harness.runners.openclaw_semantics import OpenClawSemanticsState
from typing import Any

from tests.rl.harness.openclaw_native_stream_test_support import (
    _NativeWorkerPort,
    _compile_target,
    _invoke_finalize_only,
    _run_episode,
    _supplier_sanitize_tool_call_id,
    openclaw_dist,
)

@pytest.mark.asyncio
async def test_openclaw_native_stream_cap_refuses_ninth_http_request(tmp_path: Path, openclaw_dist: Path) -> None:
    responses = [
        [(f"call-{index}", "write", {"path": f"turn-{index}.txt", "content": "ok\n"})]
        for index in range(8)
    ]
    result, requests, _, system_prompt, operations = await _run_episode(tmp_path, responses)
    assert result.termination is RunnerTermination.LIMITS_EXCEEDED
    assert len(requests) == 8
    system_on_wire = requests[0]["messages"][0]
    assert system_on_wire["role"] == "system"
    assert system_on_wire["content"].startswith("<!-- openclaw:attempt:STABLE -->\n")
    assert "Runtime: agent=" in system_prompt
    assert "Runtime: agent=" not in system_on_wire["content"]
    assert "OPENCLAW-RELOCATABLE-BOUNDARY" not in system_on_wire["content"]
    assert [tool["function"]["name"] for tool in requests[0]["tools"]] == [
        "edit", "exec", "ls", "process", "read", "write",
    ]
    assert all(request.get("stream_options") == {"include_usage": True} for request in requests)
    # Pinned buildOpenAICompletionsParams wire members (no store, n or max_tokens).
    assert all(
        sorted(request) == [
            "max_completion_tokens", "messages", "model", "stream",
            "stream_options", "tool_choice", "tools",
        ]
        and request["tool_choice"] == "auto"
        and request["max_completion_tokens"] == 2048
        for request in requests
    )
    assert operations[-5:] == ("classify_result", "close", "retire_runtime", "measure_effects", "finalize_command_result")
    replay_trace = result.response["replay_trace"]
    assert replay_trace["refusal"] == {
        "status": 429,
        "message": "bbe4 capture request cap",
        "isError": True,
    }
    assert replay_trace["isError"] is True
    assert replay_trace["termination"]["refusal"] == {
        "status": 429,
        "message": "bbe4 capture request cap",
        "isError": True,
    }
    assert replay_trace["termination"]["isError"] is True
    assert all((tmp_path / f"turn-{index}.txt").read_text() == "ok\n" for index in range(8))


@pytest.mark.asyncio
async def test_openclaw_request_members_must_match_pinned_source_builder(tmp_path: Path, openclaw_dist: Path) -> None:
    # A profile whose wire differs from buildOpenAICompletionsParams
    # (max_tokens plus store, no tool_choice) fails before any tool effect.
    divergent = {
        "request_policy": {"max_token_field": "max_tokens"},
        "capabilities": {"supports_store": True},
        "request_features": ("max_tokens",),
    }
    with pytest.raises(RunnerProtocolError) as exc:
        await _run_episode(
            tmp_path,
            [[("call-0", "write", {"path": "never.txt", "content": "x\n"})]],
            wire_policy=divergent,
        )
    assert exc.value.code == "native_request_members_mismatch"
    assert not (tmp_path / "never.txt").exists()


@pytest.mark.asyncio
async def test_openclaw_conductor_commits_poll_before_ack(tmp_path: Path, openclaw_dist: Path) -> None:
    exec_wire_id = _supplier_sanitize_tool_call_id("exec-1")
    poll_wire_id = _supplier_sanitize_tool_call_id("poll-1")

    def responses(ordinal: int, requests: list[dict[str, Any]]) -> list[tuple[str, str, Mapping[str, Any]]]:
        if ordinal == 0:
            return [("exec-1", "exec", {"command": "printf ACK_MARKER", "background": True})]
        if ordinal == 1:
            output = next(msg["content"] for msg in requests[-1]["messages"] if msg.get("tool_call_id") == exec_wire_id)
            session = re.search(r"session ([^,]+), pid ", output)
            assert session is not None
            return [("poll-1", "process", {"action": "poll", "sessionId": session[1], "timeout": 500})]
        return []

    result, requests, _, _, operations = await _run_episode(tmp_path, responses)
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert len(requests) == 3
    assert operations.index("ack") > operations.index("execute_batch", operations.index("execute_batch") + 1)
    assert any("ACK_MARKER" in str(msg.get("content")) for msg in requests[-1]["messages"] if msg.get("tool_call_id") == poll_wire_id)


@pytest.mark.asyncio
async def test_openclaw_replay_tool_call_id_sanitizes_long_id_on_wire(tmp_path: Path, openclaw_dist: Path) -> None:
    raw_tool_id = "very_long_tool_call_id_that_exceeds_forty_characters_1234567890"
    assert len(raw_tool_id) > 40
    expected_wire_id = _supplier_sanitize_tool_call_id(raw_tool_id)
    assert len(expected_wire_id) <= 40
    assert expected_wire_id != raw_tool_id

    def responses(ordinal: int, requests: list[dict[str, Any]]) -> list[tuple[str, str, Mapping[str, Any]]]:
        if ordinal == 0:
            return [(raw_tool_id, "write", {"path": "long_id.txt", "content": "verified\n"})]
        return []

    result, requests, _, _, operations = await _run_episode(tmp_path, responses)
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert len(requests) == 2

    # Wire assistant tool_calls[].id and tool tool_call_id equal supplier sanitizer output
    turn1_messages = requests[1]["messages"]
    assistant_wire = next(m for m in turn1_messages if m.get("role") == "assistant")
    assert assistant_wire["tool_calls"][0]["id"] == expected_wire_id
    tool_wire = next(m for m in turn1_messages if m.get("role") == "tool")
    assert tool_wire["tool_call_id"] == expected_wire_id

    # Internal history ids unchanged (passed as raw_tool_id to execute_batch)
    assert (tmp_path / "long_id.txt").read_text(encoding="utf-8") == "verified\n"
    assert operations.count("execute_batch") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("envelope", "cleanup_error_message", "expected_exit_code", "expected_runtime_error"),
    [
        (
            {"ok": True, "status": "ok", "sessionId": "session-1", "final": "done", "payloads": []},
            "native process scope did not reach independently observed death",
            1,
            None,
        ),
        (
            {"ok": False, "status": "timeout", "sessionId": "session-1", "error": {"message": "timed out", "kind": "timeout"}},
            "native process scope did not reach independently observed death",
            2,
            "Agent exec cleanup failed: native process scope did not reach independently observed death",
        ),
        (
            {"ok": True, "status": "ok", "sessionId": "session-1", "final": "done", "payloads": []},
            None,
            0,
            None,
        ),
    ],
)
async def test_openclaw_worker_finalizes_pinned_command_result(
    envelope: dict[str, Any],
    cleanup_error_message: str | None,
    expected_exit_code: int,
    expected_runtime_error: str | None, openclaw_dist: Path,
) -> None:
    result = await _invoke_finalize_only({
        "envelope": envelope,
        "sessionId": "session-1",
        "toolCalls": 3,
        "cleanup_error_message": cleanup_error_message,
    })

    command_result = result["command_result"]
    assert command_result["toolCalls"] == 3
    assert command_result["exitCode"] == expected_exit_code
    assert result["runtime_error"] == expected_runtime_error
    if cleanup_error_message is None or not envelope["ok"]:
        assert command_result["envelope"] == envelope
    else:
        assert command_result["envelope"] == {
            "ok": False,
            "status": "error",
            "final": "",
            "payloads": [],
            "model": None,
            "provider": None,
            "sessionId": "session-1",
            "error": {
                "message": "Agent exec cleanup failed: native process scope did not reach independently observed death",
                "kind": "exception",
            },
        }


@pytest.mark.asyncio
async def test_openclaw_native_stream_classification_and_cleanup_envelope(tmp_path: Path, openclaw_dist: Path) -> None:
    # 1. Normal run: pre-cleanup ok and post-cleanup ok envelope match
    result, requests, _, _, operations = await _run_episode(tmp_path, [[]])
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert operations[-5:] == ("classify_result", "close", "retire_runtime", "measure_effects", "finalize_command_result")
    classified = result.response["classification"]
    final_env = result.response["final_envelope"]
    assert classified["envelope"]["status"] == "ok"
    assert classified["envelope"]["ok"] is True
    assert classified["exit_code"] == 0
    assert final_env["status"] == "ok"
    assert final_env["ok"] is True
    assert result.response["command_result"] == {"envelope": final_env, "exitCode": 0, "toolCalls": 0}
    assert result.response["replay_trace"]["classification"] == classified
    assert result.response["replay_trace"]["final_envelope"] == final_env
    assert result.response["replay_trace"]["envelope"] == final_env

    # 2. Cleanup failure replaces success envelope per agent-exec.ts:500-559
    class FailingCleanupWorkerPort(_NativeWorkerPort):
        async def close_native_runtime(self) -> Mapping[str, Any]:
            self.operations.append("retire_runtime")
            process, self._process = self._process, None
            if process is not None:
                process.kill()
                await process.wait()
            # Intentionally report all_dead=False to simulate cleanup failure
            return {"kind": "closed", "cleanup": {"all_dead": False, "steps": []}}

    fail_path = tmp_path / "cleanup_fail"
    fail_path.mkdir()
    fail_result, _, _, _, _ = await _run_episode(
        fail_path,
        [[]],
        worker_factory=lambda path, bindings: FailingCleanupWorkerPort(path, bindings),
    )
    pre_env = fail_result.response["classification"]["envelope"]
    post_env = fail_result.response["final_envelope"]
    # Pre-cleanup envelope was ok
    assert pre_env["ok"] is True
    assert pre_env["status"] == "ok"
    # Post-cleanup envelope was replaced by error envelope
    assert post_env["ok"] is False
    assert post_env["status"] == "error"
    assert post_env["final"] == ""
    assert list(post_env["payloads"]) == []
    assert "exit_code" not in post_env
    assert post_env["error"]["message"] == (
        "Agent exec cleanup failed: native worker cleanup is not verified"
    )
    assert fail_result.response["command_result"] == {"envelope": post_env, "exitCode": 1, "toolCalls": 0}
    assert fail_result.response["replay_trace"]["classification"]["envelope"]["ok"] is True
    assert fail_result.response["replay_trace"]["final_envelope"]["ok"] is False


@pytest.mark.asyncio
async def test_openclaw_provider_failure_ends_episode_with_replay_trace(tmp_path: Path, openclaw_dist: Path) -> None:
    # Supplier case provider_failure_no_retry: one HTTP 500, no retry, and the
    # episode still classifies, finalizes, and records the request it sent.
    refusal = {"http_error": 500, "body": {"error": {"message": "scripted provider failure", "type": "server_error"}}}
    result, requests, _, _, operations = await _run_episode(tmp_path, [refusal])
    assert len(requests) == 1
    assert result.termination is RunnerTermination.POLICY_INCOMPLETE
    assert operations[-5:] == ("classify_result", "close", "retire_runtime", "measure_effects", "finalize_command_result")
    replay_trace = result.response["replay_trace"]
    assert thaw_json(replay_trace["requests"]) == requests
    assert replay_trace["request_count"] == 1
    assert replay_trace["termination"] == {"kind": "provider_failure", "native_stop_reason": None}
    assert result.response["final_envelope"]["ok"] is False
    assert result.response["command_result"]["exitCode"] == 1


@pytest.mark.asyncio
async def test_openclaw_failed_envelope_survives_cleanup_failure(tmp_path: Path, openclaw_dist: Path) -> None:
    class FailingCleanupWorkerPort(_NativeWorkerPort):
        async def close_native_runtime(self) -> Mapping[str, Any]:
            await super().close_native_runtime()
            return {"kind": "closed", "cleanup": {"all_dead": False, "steps": []}}

    responses = [
        [(f"call-{index}", "write", {"path": f"turn-{index}.txt", "content": "ok\n"})]
        for index in range(8)
    ]
    result, _, _, _, _ = await _run_episode(
        tmp_path,
        responses,
        worker_factory=lambda path, bindings: FailingCleanupWorkerPort(path, bindings),
    )
    before = result.response["classification"]["envelope"]
    command_result = result.response["command_result"]
    assert before["ok"] is False
    assert command_result["envelope"] == before
    assert command_result["toolCalls"] == 8
    assert result.response["runtime_error"] == (
        "Agent exec cleanup failed: native worker cleanup is not verified"
    )


@pytest.mark.asyncio
async def test_openclaw_finalization_failure_fails_closed(tmp_path: Path, openclaw_dist: Path) -> None:
    class FailedFinalizerPort(_NativeWorkerPort):
        async def invoke_native_finalization_phase(
            self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int,
        ) -> Mapping[str, Any]:
            raise RuntimeError("pinned finalizer is unavailable")

    with pytest.raises(RunnerDependencyError, match="native command finalization failed") as exc:
        await _run_episode(
            tmp_path,
            [[]],
            worker_factory=lambda path, bindings: FailedFinalizerPort(path, bindings),
        )
    assert exc.value.code == "native_finalization_failed"


@pytest.mark.asyncio
async def test_openclaw_initialization_materializes_pinned_system_prompt(tmp_path: Path, openclaw_dist: Path) -> None:
    worker = _NativeWorkerPort(tmp_path, ())
    config = json.loads(
        (Path(__file__).parents[3] / "config/e4_targets/openclaw/2026.9.4/native-config.json").read_bytes()
    )
    try:
        initialized = await worker.invoke_native_phase(
            "initialize",
            {
                "task": "inspect workspace",
                "advertisement": config["advertisement"],
                "model_config": {"id": "gpt-4o-mini", "provider": "openai"},
                "runtime_inputs": worker.native_runtime_inputs(
                    input_names=("cwd", "home", "current_date", "message_timestamp_ms", "package_dir", "session_id"),
                    package_subpath=".",
                ),
            },
            timeout_ms=15_000,
        )
        prompt = initialized["system_prompt"]
        assert prompt.startswith("<!-- openclaw:attempt:STABLE -->\n")
        # Both revisions declare isolated agent exec. Its source scope is
        # agent-exec-BAuhpelg.mjs:388, not the previous BB-only explicit scope.
        assert "session=agent:main:agent-exec:bbe4-" in prompt
        assert "## Tooling\nTools policy-filtered." in prompt
        assert "<available_skills>" in prompt
        assert "## Workspace Files (injected)" in prompt
        assert f"## {tmp_path}/BOOTSTRAP.md" not in prompt
        assert not (tmp_path / "IDENTITY.md").exists()
        assert not (tmp_path / "USER.md").exists()
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_pinned_skill_catalog_contains_supplier_linux_names_on_local_host(tmp_path: Path, openclaw_dist: Path) -> None:
    fixture = Path(__file__).parents[2] / "e4_parity/fixtures/openclaw_packet_640/receiver/http-transcript.jsonl"
    supplier = json.loads(fixture.read_text(encoding="utf-8").splitlines()[0])["body"]
    supplier_names = set(re.findall(r"<name>([^<]+)</name>", supplier["messages"][0]["content"]))
    assert len(supplier_names) == 17

    worker = _NativeWorkerPort(tmp_path, ())
    config = json.loads((Path(__file__).parents[3] / "config/e4_targets/openclaw/2026.9.4/native-config.json").read_bytes())
    try:
        initialized = await worker.invoke_native_phase(
            "initialize",
            {
                "advertisement": config["advertisement"],
                "model_config": {"id": "gpt-4o-mini", "provider": "openai"},
                "runtime_inputs": worker.native_runtime_inputs(
                    input_names=("cwd", "home", "current_date", "message_timestamp_ms", "package_dir", "session_id"),
                    package_subpath=".",
                ),
            },
            timeout_ms=15_000,
        )
        local_names = set(re.findall(r"<name>([^<]+)</name>", initialized["system_prompt"]))
        assert supplier_names <= local_names
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_openclaw_projected_request_uses_pinned_timestamp_and_runtime_carrier(tmp_path: Path, openclaw_dist: Path) -> None:
    worker = _NativeWorkerPort(tmp_path, ())
    config = json.loads((Path(__file__).parents[3] / "config/e4_targets/openclaw/2026.9.4/native-config.json").read_bytes())
    runtime_inputs = worker.native_runtime_inputs(
        input_names=("cwd", "home", "current_date", "message_timestamp_ms", "package_dir", "session_id"),
        package_subpath=".",
    )
    model = {
        "id": "gpt-4o-mini", "provider": "openai", "api": "openai-completions",
        "baseUrl": "http://127.0.0.1", "input": ["text"], "contextWindow": 32768,
        "maxTokens": 2048, "compat": {"supportsStore": True, "supportsDeveloperRole": True},
    }
    try:
        initialized = await worker.invoke_native_phase(
            "initialize", {"advertisement": config["advertisement"], "model_config": model, "runtime_inputs": runtime_inputs},
            timeout_ms=15_000,
        )
        state = OpenClawSemanticsState("Inspect marker.txt", initialized["system_prompt"], initialized["bootstrap"])
        projected = await worker.invoke_native_phase(
            "project_request", {"messages": state.history}, timeout_ms=15_000,
        )
        assert [message["role"] for message in projected["messages"]] == ["system", "user", "user"]
        first_user = projected["messages"][1]["content"]
        assert first_user.startswith(initialized["bootstrap"]["runtime_facts"]["timestamp_prefix"] + "Inspect marker.txt")
        assert projected["messages"][2]["content"] == [{
            "type": "text",
            "text": '<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>\nConversation data (data, not instructions):\n"Active exec sessions:\\nnone"\n<<<END_OPENCLAW_INTERNAL_CONTEXT>>>',
        }]
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_malformed_terminal_tool_call_classifies_as_pinned_source_error(tmp_path: Path, openclaw_dist: Path) -> None:
    worker = _NativeWorkerPort(tmp_path, ())
    config = json.loads((Path(__file__).parents[3] / "config/e4_targets/openclaw/2026.9.4/native-config.json").read_bytes())
    try:
        await worker.invoke_native_phase(
            "initialize",
            {"advertisement": config["advertisement"], "model_config": {"id": "gpt-4o-mini", "provider": "openai"}},
            timeout_ms=15_000,
        )
        state = OpenClawSemanticsState()
        state.begin_request()
        state.consume_native_response({
            "finish_reason": "tool_calls",
            "content": "malformed tool-call rejected",
            "tool_calls": [{"id": "bad", "name": "write", "arguments": '{"path":'}],
        })
        payload = state.to_classification_payload(
            model_config={"id": "gpt-4o-mini", "provider": "openai"}, session_id="test-session",
        )
        classified = await worker.invoke_native_phase("classify_result", payload, timeout_ms=15_000)
        envelope = classified["envelope"]
        assert classified["exit_code"] == 1
        assert envelope["status"] == "error"
        assert envelope["final"] == ""
        assert envelope["error"] == {
            "kind": "incomplete_turn",
            "message": "Provider returned an incomplete or malformed tool call",
        }
        assert envelope["payloads"] == [{
            "text": "⚠️ Agent run failed (model: openai/gpt-4o-mini).",
            "isError": True,
            "mediaUrl": None,
        }]
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_openclaw_native_worker_ack_and_close_are_scope_verified(tmp_path: Path, openclaw_dist: Path) -> None:
    worker = _NativeWorkerPort(tmp_path, ())
    try:
        with pytest.raises(RuntimeError, match="advertisement must be an object"):
            await worker.invoke_native_phase(
                "initialize",
                {"task": "phase contract", "model_config": {"id": "model-a", "provider": "openai"}},
                timeout_ms=5_000,
            )
        with pytest.raises(RuntimeError, match="advertisement keys"):
            await worker.invoke_native_phase(
                "initialize",
                {
                    "task": "phase contract",
                    "advertisement": {
                        "prompt_removals": [],
                        "tool_description_replacements": {},
                        "tools": {"exec": {"description": "Run shell now; background continuation supported. Use yieldMs/background, then process for logs/status/input/intervention. Process confirms completion. TTY CLI/UI: pty=true. Quote arguments containing shell metacharacters, including URL query strings with `?` or `&`.", "native_sha256": "sha256:6a41ebbc7cd1fae376a497c1bb1662c40a5faa87e5f811bff3ae37e04fb20973"}},
                        "capability_denials": {
                            "ask": {
                                "schema_version": "bb.openclaw-capability-denial.v1",
                                "capability": "ask",
                                "message": "denied",
                                "source_ref": "decision:15",
                            },
                            "node": {
                                "schema_version": "bb.openclaw-capability-denial.v1",
                                "capability": "node",
                                "message": "denied",
                                "source_ref": "decision:15",
                            }
                        },
                        "unexpected": True,
                    },
                    "model_config": {"id": "model-a", "provider": "openai"},
                },
                timeout_ms=5_000,
            )
        initialized = await worker.invoke_native_phase(
            "initialize",
            {
                "task": "phase contract",
                "advertisement": {
                    "prompt_removals": [],
                    "tool_description_replacements": {},
                    "tools": {"exec": {"description": "Run shell now; background continuation supported. Use yieldMs/background, then process for logs/status/input/intervention. Process confirms completion. TTY CLI/UI: pty=true. Quote arguments containing shell metacharacters, including URL query strings with `?` or `&`.", "native_sha256": "sha256:6a41ebbc7cd1fae376a497c1bb1662c40a5faa87e5f811bff3ae37e04fb20973"}},
                    "capability_denials": {
                        "ask": {
                            "schema_version": "bb.openclaw-capability-denial.v1",
                            "capability": "ask",
                            "message": "denied",
                            "source_ref": "decision:15",
                        },
                        "node": {
                            "schema_version": "bb.openclaw-capability-denial.v1",
                            "capability": "node",
                            "message": "denied",
                            "source_ref": "decision:15",
                        }
                    },
                },
                "model_config": {
                    "id": "model-a",
                    "provider": "openai",
                    "api": "openai-completions",
                    "baseUrl": "http://127.0.0.1",
                    "input": ["text"],
                    "contextWindow": 32_768,
                    "maxTokens": 2_048,
                    "compat": {
                        "supportsStore": True,
                        "supportsDeveloperRole": True,
                        "supportsUsageInStreaming": True,
                        "supportsStrictMode": False,
                    },
                },
            },
            timeout_ms=5_000,
        )
        assert initialized["kind"] == "initialized"
        await worker.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {
                        "id": "node-denied",
                        "name": "exec",
                        "arguments": {
                            "command": "touch node-denied.txt",
                            "node": "remote",
                        },
                    }
                ]
            },
            timeout_ms=5_000,
        )
        denied = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        assert denied["results"][0]["isError"] is True
        assert denied["results"][0]["details"]["capability_denial"]["capability"] == "node"
        assert not (tmp_path / "node-denied.txt").exists()
        await worker.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {
                        "id": "ask-denied",
                        "name": "exec",
                        "arguments": {"command": "touch ask-denied.txt", "ask": "always"},
                    }
                ]
            },
            timeout_ms=5_000,
        )
        ask_denied = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        assert ask_denied["results"][0]["isError"] is True
        assert ask_denied["results"][0]["details"]["capability_denial"]["capability"] == "ask"
        assert not (tmp_path / "ask-denied.txt").exists()
        for field, value, prefix, filename in (
            ("host", "node", "exec host not allowed", "host-denied.txt"),
            ("elevated", True, "elevated is not available", "elevated-denied.txt"),
        ):
            await worker.invoke_native_phase(
                "prepare_tools",
                {
                    "calls": [
                        {
                            "id": f"{field}-denied",
                            "name": "exec",
                            "arguments": {"command": f"touch {filename}", field: value},
                        }
                    ]
                },
                timeout_ms=5_000,
            )
            native_denied = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
            assert native_denied["results"][0]["isError"] is True
            assert native_denied["results"][0]["content"][0]["text"].startswith(prefix)
            assert not (tmp_path / filename).exists()
        await worker.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {
                        "id": "pty-background",
                        "name": "exec",
                        "arguments": {"command": "sleep 2", "background": True, "pty": True},
                    }
                ]
            },
            timeout_ms=5_000,
        )
        pty_started = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        assert pty_started["results"][0]["details"]["status"] == "running"
        await worker.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {
                        "id": "background",
                        "name": "exec",
                        "arguments": {"command": "printf done", "background": True},
                    }
                ]
            },
            timeout_ms=5_000,
        )
        launched = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        session_id = launched["results"][0]["details"]["sessionId"]
        await worker.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {
                        "id": "poll",
                        "name": "process",
                        "arguments": {"action": "poll", "sessionId": session_id, "timeout": 100},
                    }
                ]
            },
            timeout_ms=5_000,
        )
        polled = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        delivery_id = polled["results"][0]["delivery_id"]
        assert worker.operations[-1] == "execute_batch"
        acknowledged = await worker.invoke_native_phase(
            "ack",
            {"delivery_id": delivery_id, "history_digest": "sha256:" + "a" * 64},
            timeout_ms=5_000,
        )
        assert acknowledged["kind"] == "acked"
        assert worker.operations[-1] == "ack"
        closed = await worker.invoke_native_phase("close", {}, timeout_ms=5_000)
        assert closed["cleanup"]["all_dead"] is True
        assert closed["cleanup"]["marker_before"][0]["pid"] > 1
        assert closed["cleanup"]["marker_after"] == []
        assert closed["cleanup"]["process_groups"]
        assert all(group["group_probe_absent"] for group in closed["cleanup"]["process_groups"])
    finally:
        await worker.close()


@pytest.mark.parametrize("descriptor_argv", [None, "equal", "different"])
def test_installed_worker_argv_comes_from_sealed_target(
    tmp_path: Path, descriptor_argv: str | None
) -> None:
    import subprocess
    from types import SimpleNamespace

    from breadboard.rl.harness import composition
    from breadboard.rl.harness.materialization import SealedSourceManifest, SourceManifestEntry

    _, _, compiled = _compile_target(
        tmp_path, profile_digest="sha256:" + "a" * 64
    )
    target = compiled.semantic.to_canonical_obj()["metadata"]["e4_target"]
    declared = tuple(target["runtime_profile"]["native_worker"]["argv"])
    compiled_digest = "sha256:" + hashlib.sha256(compiled.canonical_bytes()).hexdigest()
    root = tmp_path / "installed"
    root.mkdir(mode=0o700)
    files = {
        "bin/node": b"#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
        declared[2].removeprefix("./"): b"// pinned loader\n",
        declared[-1]: b"// pinned worker\n",
    }
    entries: list[SourceManifestEntry] = []
    directories = {
        str(parent)
        for name in files
        for parent in Path(name).parent.parents
        if str(parent) != "."
    } | {str(Path(name).parent) for name in files}
    for directory in sorted(directories):
        (root / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        entries.append(SourceManifestEntry(directory, "directory", 0, 0o700))
    for name, content in files.items():
        path = root / name
        path.write_bytes(content)
        path.chmod(0o700 if name == "bin/node" else 0o600)
        entries.append(
            SourceManifestEntry(
                name, "file", len(content), path.stat().st_mode & 0o777,
                "sha256:" + hashlib.sha256(content).hexdigest(),
            )
        )
    source_manifest = SealedSourceManifest(
        source_digest="sha256:" + "b" * 64,
        schema_identity=composition._NATIVE_TOOL_SOURCE_SCHEMA_VERSION,
        media_identity=composition._NATIVE_TOOL_SOURCE_MEDIA_TYPE,
        entries=tuple(sorted(entries, key=lambda item: item.logical_path)),
        total_bytes=sum(len(content) for content in files.values()),
        total_files=len(files),
    )
    manifest_path = tmp_path / "native-manifest.json"
    manifest_bytes = composition._canonical_bytes(source_manifest.projection())
    manifest_path.write_bytes(manifest_bytes)
    stat = root.stat()
    descriptor = {
        "adapter_id": "openclaw.local.v2026.9.4",
        "tool_ids": sorted(["ls", "read", "edit", "write", "exec", "process"]),
        "runtime_root": {
            "authority_id": "native-runtime", "path": str(root),
            "device": str(stat.st_dev), "inode": str(stat.st_ino),
            "owner_uid": stat.st_uid, "mode": "0700",
        },
        "manifest_ref": {
            "path": str(manifest_path), "sha256": source_manifest.manifest_digest,
            "size_bytes": len(manifest_bytes),
            "media_type": "application/vnd.breadboard.native-tool-source+json;version=1",
        },
        "executable_relative_path": "bin/node",
        "entrypoint_relative_path": declared[-1],
    }
    if descriptor_argv:
        descriptor["argv"] = list(declared if descriptor_argv == "equal" else (
            declared[0], "--import", "./bin/node", declared[-1]
        ))
    installed = SimpleNamespace(
        tool_adapters=(composition.InstalledToolAdapterV1.model_validate_json(json.dumps(descriptor)),)
    )
    receipt = SimpleNamespace(
        compiled=SimpleNamespace(manifest_digest=compiled_digest),
        effective_capabilities=SimpleNamespace(
            tools=tuple(
                SimpleNamespace(tool_id=tool, implementation_digest=source_manifest.manifest_digest)
                for tool in descriptor["tool_ids"]
            )
        ),
    )
    if descriptor_argv == "different":
        with pytest.raises(ValueError, match="sealed.*argv"):
            composition._load_native_tool_bindings(
                installed, (receipt,), {compiled_digest: compiled}
            )
        return
    (binding,) = composition._load_native_tool_bindings(
        installed, (receipt,), {compiled_digest: compiled}
    )
    command = sandbox_module._native_worker_argv(binding, str(root / "bin/node"))
    assert command == (
        str(root / "bin/node"), "--import", str(root / declared[2].removeprefix("./")),
        str(root / declared[-1]),
    )
    assert subprocess.run(command, capture_output=True, text=True, check=True).stdout.splitlines() == [
        "--import", str(root / declared[2].removeprefix("./")), str(root / declared[-1]),
    ]

