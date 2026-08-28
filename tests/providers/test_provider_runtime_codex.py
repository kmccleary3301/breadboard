from __future__ import annotations

import io
import json
import traceback
import types

import pytest

from breadboard_engine.provider import runtime_codex as runtime_codex_module
from breadboard_engine.provider_routing import provider_router
from breadboard_engine.provider_runtime import (
    ProviderRuntimeContext,
    ProviderRuntimeError,
    provider_registry,
)
from breadboard_engine.security import build_child_environment

provider_registry.register_runtime("codex_app_server", runtime_codex_module.CodexAppServerRuntime)


class _FakeSessionState:
    def __init__(self) -> None:
        self.workspace = "/tmp/workspace"
        self._meta = {"current_turn_index": 3}
        self.emitted: list[tuple[str, dict, int | None]] = []
        self.tool_events: list[tuple[int | None, str, bool, dict]] = []

    def get_provider_metadata(self, key: str, default=None):
        return self._meta.get(key, default)

    def set_provider_metadata(self, key: str, value):
        self._meta[key] = value

    def _emit_event(self, event_type: str, payload: dict, *, turn=None):
        self.emitted.append((event_type, dict(payload), turn))

    def record_tool_event(self, turn_index, tool_name: str, *, success: bool, metadata=None):
        self.tool_events.append((turn_index, tool_name, success, dict(metadata or {})))


def _correlated_notification(
    notification: dict,
    *,
    thread_id: str,
    turn_id: str,
) -> dict:
    result = dict(notification)
    params = dict(result.get("params") or {})
    method = result.get("method")
    if method == "turn/completed":
        params.setdefault("threadId", thread_id)
    elif method == "thread/tokenUsage/updated" or method == "turn/started" or (
        isinstance(method, str) and method.startswith("item/")
    ):
        params.setdefault("threadId", thread_id)
        params.setdefault("turnId", turn_id)
    if method == "item/started":
        params.setdefault("startedAtMs", 1)
    elif method == "item/completed":
        params.setdefault("completedAtMs", 2)
    result["params"] = params
    return result


class _FakeClient:
    def __init__(self, notifications: list[dict]) -> None:
        self._notifications = list(notifications)
        self.turn_inputs: list[tuple[str, str]] = []

    def turn_start(self, thread_id: str, text: str):
        self.turn_inputs.append((thread_id, text))
        return {"turn": {"id": "turn-1"}}

    def next_notification(self):
        if not self._notifications:
            raise AssertionError("no more notifications")
        return _correlated_notification(
            self._notifications.pop(0),
            thread_id="thread-1",
            turn_id="turn-1",
        )


def _agent_item(
    item_id: str, text: str, *, phase: str = "final_answer"
) -> dict:
    return {
        "id": item_id,
        "type": "agentMessage",
        "phase": phase,
        "text": text,
    }


def _command_item(
    item_id: str,
    command: str,
    *,
    status: str,
    output: str | None = None,
    exit_code: int | None = None,
    process_id: str | None = None,
) -> dict:
    return {
        "id": item_id,
        "type": "commandExecution",
        "command": command,
        "commandActions": [],
        "cwd": "/tmp/workspace",
        "status": status,
        "aggregatedOutput": output,
        "exitCode": exit_code,
        "processId": process_id,
    }


def _completed_turn(turn_id: str, *items: dict) -> dict:
    return {
        "id": turn_id,
        "status": "completed",
        "items": list(items),
        "itemsView": "full",
    }

def test_codex_provider_routes_to_app_server() -> None:
    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    assert descriptor.provider_id == "codex"
    assert descriptor.runtime_id == "codex_app_server"
    assert model == "gpt-5.4-mini"
    client_config = provider_router.create_client_config("codex/gpt-5.4-mini")
    assert client_config["api_key"] == "codex"


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        ("item/started", {"item": {"type": "agentMessage"}}),
        ("item/completed", {"item": {"type": "commandExecution"}}),
        ("item/agentMessage/delta", {"delta": "missing identifier"}),
        (
            "item/completed",
            {
                "item": {
                    "id": "message-1",
                    "type": "agentMessage",
                    "phase": "final_answer",
                }
            },
        ),
        (
            "item/reasoning/textDelta",
            {"contentIndex": 0, "delta": "missing identifier"},
        ),
    ],
)
def test_codex_normative_notifications_require_identifiers(method, payload):
    descriptor, _ = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    runtime = provider_registry.create_runtime(descriptor)
    session_state = _FakeSessionState()
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime._handle_notification(
            method=method,
            payload=payload,
            turn_index=3,
            stream=True,
            session_state=session_state,
            context=context,
            expected_turn_id="turn-1",
        )

    assert exc_info.value.kind == "protocol"
    assert exc_info.value.details["code"] == "invalid_codex_event"


def test_codex_unknown_normative_item_fails_closed() -> None:
    descriptor, _ = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    runtime = provider_registry.create_runtime(descriptor)
    runtime._thread_id = "thread-1"
    session_state = _FakeSessionState()
    context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime._handle_notification(
            method="item/started",
            payload={
                "threadId": "thread-1",
                "turnId": "turn-1",
                "startedAtMs": 1,
                "item": {"id": "unknown-1", "type": "futureItem"},
            },
            turn_index=3,
            stream=True,
            session_state=session_state,
            context=context,
            expected_turn_id="turn-1",
        )

    assert exc_info.value.kind == "protocol"
    assert exc_info.value.details["code"] == "unknown_codex_item"


def test_codex_unknown_notification_method_fails_closed() -> None:
    descriptor, _ = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    runtime = provider_registry.create_runtime(descriptor)
    runtime._thread_id = "thread-1"
    context = ProviderRuntimeContext(
        session_state=_FakeSessionState(),
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime._handle_notification(
            method="future/semantic",
            payload={},
            turn_index=3,
            stream=True,
            session_state=context.session_state,
            context=context,
            expected_turn_id="turn-1",
        )

    assert exc_info.value.details["code"] == "unknown_codex_event"


def test_codex_notification_correlation_must_match_active_turn() -> None:
    descriptor, _ = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    runtime = provider_registry.create_runtime(descriptor)
    runtime._thread_id = "thread-1"
    context = ProviderRuntimeContext(
        session_state=_FakeSessionState(),
        agent_config={},
        stream=True,
    )

    with pytest.raises(ProviderRuntimeError) as exc_info:
        runtime._handle_notification(
            method="item/started",
            payload={
                "threadId": "thread-other",
                "turnId": "turn-1",
                "startedAtMs": 1,
                "item": _agent_item("message-1", ""),
            },
            turn_index=3,
            stream=True,
            session_state=context.session_state,
            context=context,
            expected_turn_id="turn-1",
        )

    assert exc_info.value.details["code"] == "invalid_codex_event"

def test_codex_child_environment_excludes_credentials(monkeypatch) -> None:
    canaries = {
        "OPENAI_API_KEY": "e3-codex-openai-canary",
        "ANTHROPIC_API_KEY": "e3-codex-anthropic-canary",
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON": "e3-codex-header-canary",
    }
    for key, value in canaries.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PATH", "/usr/bin")

    child_env = build_child_environment()

    assert child_env["PATH"] == "/usr/bin"
    assert not set(canaries).intersection(child_env)
    assert all(value not in json.dumps(child_env) for value in canaries.values())


def test_codex_invalid_child_output_is_secret_free() -> None:
    canary = "e3-codex-stdout-canary"
    client = runtime_codex_module._CodexJsonRpcClient(
        codex_bin="codex",
        cwd="/tmp",
        env={},
    )
    client._proc = types.SimpleNamespace(stdout=io.StringIO(f"not-json {canary}\n"))

    with pytest.raises(runtime_codex_module.ProviderRuntimeError) as exc_info:
        client._read_message()

    rendered = "".join(
        traceback.format_exception(
            exc_info.type,
            exc_info.value,
            exc_info.tb,
        )
    )
    serialized = json.dumps(exc_info.value.details)
    assert canary not in rendered
    assert canary not in serialized
    assert set(exc_info.value.details) == {"line_bytes"}

@pytest.mark.parametrize(
    "method",
    (
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    ),
)
def test_codex_app_server_fails_closed_on_approval_request(method) -> None:
    client = runtime_codex_module._CodexJsonRpcClient(
        codex_bin="codex",
        cwd="/tmp",
        env={},
    )

    assert client._handle_server_request({"method": method}) == {
        "decision": "cancel"
    }


def test_codex_app_server_uses_restricted_process_builder(
    monkeypatch,
    tmp_path,
) -> None:
    protected = tmp_path / "credentials.sqlite3"
    builder_calls: list[tuple[object, dict]] = []
    popen_calls: list[tuple[object, dict]] = []

    def _builder(command, **kwargs):
        builder_calls.append((command, kwargs))
        return (
            ("/trusted/isolation-helper", "--", "codex", "app-server"),
            {"PATH": "/trusted/bin", "HOME": str(tmp_path)},
        )

    class _Process:
        stderr = io.StringIO("")

    def _popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return _Process()

    monkeypatch.setattr(
        runtime_codex_module,
        "build_restricted_process_command",
        _builder,
    )
    monkeypatch.setattr(runtime_codex_module.subprocess, "Popen", _popen)
    client = runtime_codex_module._CodexJsonRpcClient(
        codex_bin="/trusted/bin/codex",
        cwd=str(tmp_path),
        env={"PATH": "/trusted/bin"},
        protected_paths=(str(protected),),
    )

    client.start()

    assert builder_calls == [
        (
            ["/trusted/bin/codex", "app-server", "--listen", "stdio://"],
            {
                "workspace": str(tmp_path),
                "working_directory": str(tmp_path),
                "shell": False,
                "environment": {"PATH": "/trusted/bin"},
                "protected_paths": (str(protected),),
                "allow_network": True,
            },
        )
    ]
    assert popen_calls[0][0] == (
        "/trusted/isolation-helper",
        "--",
        "codex",
        "app-server",
    )
    assert popen_calls[0][1]["env"] == {
        "PATH": "/trusted/bin",
        "HOME": str(tmp_path),
    }
    assert popen_calls[0][1]["cwd"] == str(tmp_path)
    assert popen_calls[0][1]["shell"] is False


def test_codex_app_server_isolation_failure_is_secret_free_and_never_retries(
    monkeypatch,
    tmp_path,
) -> None:
    canary = "codex-isolation-error-canary-e7"
    build_calls = 0

    def _builder(*_args, **_kwargs):
        nonlocal build_calls
        build_calls += 1
        raise runtime_codex_module.ProcessIsolationUnavailable(canary)

    monkeypatch.setattr(
        runtime_codex_module,
        "build_restricted_process_command",
        _builder,
    )
    monkeypatch.setattr(
        runtime_codex_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("unisolated process launch attempted"),
    )
    client = runtime_codex_module._CodexJsonRpcClient(
        codex_bin="codex",
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin"},
    )

    with pytest.raises(runtime_codex_module.ProviderRuntimeError) as exc_info:
        client.start()

    rendered = "".join(
        traceback.format_exception(
            exc_info.type,
            exc_info.value,
            exc_info.tb,
        )
    )
    assert build_calls == 1
    assert canary not in rendered
    assert canary not in json.dumps(exc_info.value.details)
    assert exc_info.value.details == {
        "error_type": "ProcessIsolationUnavailable"
    }


def test_codex_runtime_starts_threads_read_only_without_approvals(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BREADBOARD_CODEX_APP_SERVER_POOL", raising=False)
    runtime_codex_module._reset_codex_client_pool_for_tests()
    thread_starts: list[dict] = []

    class _ReadOnlyFakeClient:
        def __init__(self, *, codex_bin: str, cwd: str, env: dict) -> None:
            del codex_bin, cwd, env

        def start(self) -> None:
            pass

        def initialize(self) -> dict:
            return {"ok": True}

        def thread_start(self, params: dict) -> dict:
            thread_starts.append(dict(params))
            return {"thread": {"id": "thread-read-only"}}

        def close(self) -> None:
            pass

    monkeypatch.setattr(runtime_codex_module, "_CodexJsonRpcClient", _ReadOnlyFakeClient)
    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.5")
    runtime = provider_registry.create_runtime(descriptor)

    runtime._ensure_client(model=model, cwd=str(tmp_path))

    assert thread_starts == [
        {
            "model": "gpt-5.5",
            "cwd": str(tmp_path),
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": True,
            "dynamicTools": [],
            "environments": [],
        }
    ]
    runtime._release_leased_client(healthy=True)


def test_codex_runtime_streams_commentary_tool_exec_and_final_answer(monkeypatch) -> None:
    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    runtime = provider_registry.create_runtime(descriptor)
    commentary_start = _agent_item("commentary-1", "", phase="commentary")
    commentary_done = _agent_item(
        "commentary-1", "Checking now.", phase="commentary"
    )
    command_start = _command_item(
        "call-1", "pwd", status="inProgress", process_id="proc-1"
    )
    command_done = _command_item(
        "call-1",
        "pwd",
        status="completed",
        output="/tmp/workspace\n",
        exit_code=0,
        process_id="proc-1",
    )
    reasoning_start = {
        "id": "reasoning-1",
        "type": "reasoning",
        "content": [],
        "summary": [],
    }
    reasoning_done = {
        **reasoning_start,
        "content": ["private chain"],
        "summary": ["brief plan"],
    }
    final_start = _agent_item("final-1", "")
    final_done = _agent_item("final-1", "Done.")
    fake_client = _FakeClient(
        [
            {"method": "item/started", "params": {"item": commentary_start}},
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "commentary-1", "delta": "Checking now."},
            },
            {"method": "item/completed", "params": {"item": commentary_done}},
            {"method": "item/started", "params": {"item": reasoning_start}},
            {
                "method": "item/reasoning/textDelta",
                "params": {
                    "itemId": "reasoning-1",
                    "contentIndex": 0,
                    "delta": "private chain",
                },
            },
            {
                "method": "item/reasoning/summaryTextDelta",
                "params": {
                    "itemId": "reasoning-1",
                    "summaryIndex": 0,
                    "delta": "brief plan",
                },
            },
            {"method": "item/completed", "params": {"item": reasoning_done}},
            {"method": "item/started", "params": {"item": command_start}},
            {"method": "item/completed", "params": {"item": command_done}},
            {"method": "item/started", "params": {"item": final_start}},
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "final-1", "delta": "Done."},
            },
            {"method": "item/completed", "params": {"item": final_done}},
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "tokenUsage": {
                        "last": {
                            "cachedInputTokens": 2,
                            "inputTokens": 10,
                            "outputTokens": 5,
                            "reasoningOutputTokens": 3,
                            "totalTokens": 15,
                        },
                        "total": {
                            "cachedInputTokens": 4,
                            "inputTokens": 20,
                            "outputTokens": 9,
                            "reasoningOutputTokens": 6,
                            "totalTokens": 29,
                        },
                        "modelContextWindow": 128000,
                    }
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "turn": _completed_turn(
                        "turn-1",
                        commentary_done,
                        reasoning_done,
                        command_done,
                        final_done,
                    )
                },
            },
        ]
    )
    runtime._thread_id = "thread-1"
    monkeypatch.setattr(runtime, "_ensure_client", lambda **_kwargs: fake_client)
    session_state = _FakeSessionState()
    context = ProviderRuntimeContext(session_state=session_state, agent_config={}, stream=True)

    result = runtime.invoke(
        client={"api_key": "codex"},
        model=model,
        messages=[{"role": "user", "content": "Say hello"}],
        tools=None,
        stream=True,
        context=context,
    )

    assert fake_client.turn_inputs == [("thread-1", "Say hello")]
    assert len(result.messages) == 1
    assert result.messages[0].content == [
        {"type": "thinking", "text": "Checking now."},
        {"type": "thinking", "text": "private chain"},
        {"type": "thinking", "text": "brief plan"},
        {"type": "text", "text": "Done."},
    ]
    assert result.messages[0].tool_calls[0].as_dict() == {
        "call_id": "call-1",
        "name": "shell_command",
        "arguments_json": (
            '{"command":"pwd","command_actions":[],"cwd":"/tmp/workspace",'
            '"source":"agent"}'
        ),
        "arguments": {
            "command": "pwd",
            "command_actions": [],
            "cwd": "/tmp/workspace",
            "source": "agent",
        },
    }
    assert result.messages[0].tool_results == [
        {"call_id": "call-1", "result": "/tmp/workspace\n"}
    ]
    assert result.reasoning_summaries == ["brief plan"]
    assert result.usage == {
        "cache_read_tokens": 2,
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 3,
        "total_tokens": 15,
        "extensions": {
            "codex_total": {
                "cache_read_tokens": 4,
                "input_tokens": 20,
                "output_tokens": 9,
                "reasoning_tokens": 6,
                "total_tokens": 29,
            },
            "model_context_window": 128000,
        },
    }
    assert result.metadata["provider_turn_completed"] is True
    assert result.metadata["provider_turn_completion_method"] == "codex_app_server"
    assert result.metadata["provider_turn_completion_reason"] == "codex_turn_completed"
    timing = result.metadata.get("provider_runtime_timing") or {}
    assert isinstance(timing, dict)
    assert timing.get("notification_count") == 14
    assert "client_ready_seconds" in timing
    assert "turn_start_seconds" in timing
    event_types = [event_type for event_type, _payload, _turn in session_state.emitted]
    assert "assistant.thought_summary.delta" in event_types
    assert "tool.exec.start" in event_types
    assert "tool.exec.stdout.delta" in event_types
    assert "tool.exec.end" in event_types
    assert "assistant.message.delta" in event_types
    assert session_state.tool_events == [
        (3, "shell_command", True, {"is_run_shell": True, "exit_code": 0, "call_id": "call-1"})
    ]


def test_codex_nonstream_preserves_reasoning_without_session_state(
    monkeypatch,
) -> None:
    descriptor, model = provider_router.get_runtime_descriptor(
        "codex/gpt-5.4-mini"
    )
    runtime = provider_registry.create_runtime(descriptor)
    reasoning_start = {
        "id": "reasoning-1",
        "type": "reasoning",
        "content": [],
        "summary": [],
    }
    reasoning_done = {
        **reasoning_start,
        "content": ["analysis"],
        "summary": ["summary"],
    }
    final_done = _agent_item("final-1", "Done.")
    fake_client = _FakeClient(
        [
            {"method": "item/started", "params": {"item": reasoning_start}},
            {
                "method": "item/reasoning/textDelta",
                "params": {
                    "itemId": "reasoning-1",
                    "contentIndex": 0,
                    "delta": "analysis",
                },
            },
            {"method": "item/completed", "params": {"item": reasoning_done}},
            {
                "method": "item/completed",
                "params": {"item": final_done},
            },
            {
                "method": "turn/completed",
                "params": {
                    "turn": _completed_turn(
                        "turn-1", reasoning_done, final_done
                    )
                },
            },
        ]
    )
    runtime._thread_id = "thread-1"
    monkeypatch.setattr(runtime, "_ensure_client", lambda **_kwargs: fake_client)

    result = runtime.invoke(
        client={"api_key": "codex"},
        model=model,
        messages=[{"role": "user", "content": "Say hello"}],
        tools=None,
        stream=False,
        context=ProviderRuntimeContext(
            session_state=None, agent_config={}, stream=False
        ),
    )

    assert result.messages[0].content == [
        {"type": "thinking", "text": "analysis"},
        {"type": "thinking", "text": "summary"},
        {"type": "text", "text": "Done."},
    ]
    assert result.reasoning_summaries == ["summary"]


def test_codex_runtime_reuses_warm_app_server_client_across_runtime_instances(monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_CODEX_APP_SERVER_POOL", "1")
    runtime_codex_module._reset_codex_client_pool_for_tests()
    created_clients: list["_WarmFakeClient"] = []

    class _WarmFakeClient:
        def __init__(self, *, codex_bin: str, cwd: str, env: dict) -> None:
            self.codex_bin = codex_bin
            self.cwd = cwd
            self.env = env
            self.started = 0
            self.initialized = 0
            self.thread_starts = 0
            self.closed = 0
            self._notifications: list[dict] = []

        def start(self) -> None:
            self.started += 1

        def initialize(self) -> dict:
            self.initialized += 1
            return {"ok": True}

        def thread_start(self, params: dict) -> dict:
            self.thread_starts += 1
            item_id = f"final-{self.thread_starts}"
            turn_id = f"turn-{self.thread_starts}"
            text = f"Done {self.thread_starts}."
            started = _agent_item(item_id, "")
            completed = _agent_item(item_id, text)
            self._notifications = [
                {"method": "item/started", "params": {"item": started}},
                {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": item_id, "delta": text},
                },
                {"method": "item/completed", "params": {"item": completed}},
                {
                    "method": "turn/completed",
                    "params": {"turn": _completed_turn(turn_id, completed)},
                },
            ]
            return {"thread": {"id": f"thread-{self.thread_starts}"}}

        def turn_start(self, thread_id: str, text: str) -> dict:
            assert thread_id == f"thread-{self.thread_starts}"
            assert text == "Say hello"
            return {"turn": {"id": f"turn-{self.thread_starts}"}}

        def next_notification(self, timeout_s=None):
            del timeout_s
            if not self._notifications:
                raise AssertionError("no more notifications")
            return _correlated_notification(
                self._notifications.pop(0),
                thread_id=f"thread-{self.thread_starts}",
                turn_id=f"turn-{self.thread_starts}",
            )

        def close(self) -> None:
            self.closed += 1

    def _fake_client_ctor(*, codex_bin: str, cwd: str, env: dict):
        client = _WarmFakeClient(codex_bin=codex_bin, cwd=cwd, env=env)
        created_clients.append(client)
        return client

    monkeypatch.setattr(runtime_codex_module, "_CodexJsonRpcClient", _fake_client_ctor)

    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    messages = [{"role": "user", "content": "Say hello"}]

    runtime1 = provider_registry.create_runtime(descriptor)
    result1 = runtime1.invoke(
        client={"api_key": "codex"},
        model=model,
        messages=messages,
        tools=None,
        stream=False,
        context=ProviderRuntimeContext(session_state=_FakeSessionState(), agent_config={}, stream=False),
    )
    timing1 = result1.metadata.get("provider_runtime_timing") or {}
    assert timing1.get("client_cache_hit") is False

    runtime2 = provider_registry.create_runtime(descriptor)
    result2 = runtime2.invoke(
        client={"api_key": "codex"},
        model=model,
        messages=messages,
        tools=None,
        stream=False,
        context=ProviderRuntimeContext(session_state=_FakeSessionState(), agent_config={}, stream=False),
    )
    timing2 = result2.metadata.get("provider_runtime_timing") or {}
    assert timing2.get("client_cache_hit") is True

    assert len(created_clients) == 1
    assert created_clients[0].started == 1
    assert created_clients[0].initialized == 1
    assert created_clients[0].thread_starts == 2
    assert created_clients[0].closed == 0

    runtime_codex_module._reset_codex_client_pool_for_tests()
    assert created_clients[0].closed == 1


def test_codex_prewarm_populates_pool_for_first_runtime_invoke(monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_CODEX_APP_SERVER_POOL", "1")
    runtime_codex_module._reset_codex_client_pool_for_tests()
    created_clients: list["_WarmFakeClient"] = []

    class _WarmFakeClient:
        def __init__(self, *, codex_bin: str, cwd: str, env: dict) -> None:
            self.codex_bin = codex_bin
            self.cwd = cwd
            self.env = env
            self.started = 0
            self.initialized = 0
            self.thread_starts = 0
            self.closed = 0
            self._notifications: list[dict] = []

        def start(self) -> None:
            self.started += 1

        def initialize(self) -> dict:
            self.initialized += 1
            return {"ok": True}

        def thread_start(self, params: dict) -> dict:
            self.thread_starts += 1
            item_id = f"final-{self.thread_starts}"
            turn_id = f"turn-{self.thread_starts}"
            started = _agent_item(item_id, "")
            completed = _agent_item(item_id, "Done.")
            self._notifications = [
                {"method": "item/started", "params": {"item": started}},
                {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": item_id, "delta": "Done."},
                },
                {"method": "item/completed", "params": {"item": completed}},
                {
                    "method": "turn/completed",
                    "params": {"turn": _completed_turn(turn_id, completed)},
                },
            ]
            return {"thread": {"id": f"thread-{self.thread_starts}"}}

        def turn_start(self, thread_id: str, text: str) -> dict:
            assert text == "Say hello"
            return {"turn": {"id": f"turn-{self.thread_starts}"}}

        def next_notification(self, timeout_s=None):
            del timeout_s
            if not self._notifications:
                raise AssertionError("no more notifications")
            return _correlated_notification(
                self._notifications.pop(0),
                thread_id=f"thread-{self.thread_starts}",
                turn_id=f"turn-{self.thread_starts}",
            )

        def close(self) -> None:
            self.closed += 1

    def _fake_client_ctor(*, codex_bin: str, cwd: str, env: dict):
        client = _WarmFakeClient(codex_bin=codex_bin, cwd=cwd, env=env)
        created_clients.append(client)
        return client

    monkeypatch.setattr(runtime_codex_module, "_CodexJsonRpcClient", _fake_client_ctor)

    warm = runtime_codex_module.prewarm_codex_app_server(model="gpt-5.4-mini", cwd="/tmp/workspace")
    assert warm["cache_hit"] is False
    assert len(created_clients) == 1
    assert created_clients[0].started == 1
    assert created_clients[0].initialized == 1
    assert created_clients[0].thread_starts == 0

    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    runtime = provider_registry.create_runtime(descriptor)
    result = runtime.invoke(
        client={"api_key": "codex"},
        model=model,
        messages=[{"role": "user", "content": "Say hello"}],
        tools=None,
        stream=False,
        context=ProviderRuntimeContext(session_state=_FakeSessionState(), agent_config={}, stream=False),
    )
    timing = result.metadata.get("provider_runtime_timing") or {}
    assert timing.get("client_cache_hit") is True
    assert len(created_clients) == 1
    assert created_clients[0].thread_starts == 1

    runtime_codex_module._reset_codex_client_pool_for_tests()
    assert created_clients[0].closed == 1


def test_codex_runtime_does_not_pool_app_server_clients_by_default(monkeypatch) -> None:
    monkeypatch.delenv("BREADBOARD_CODEX_APP_SERVER_POOL", raising=False)
    runtime_codex_module._reset_codex_client_pool_for_tests()
    created_clients: list["_FreshFakeClient"] = []

    class _FreshFakeClient:
        def __init__(self, *, codex_bin: str, cwd: str, env: dict) -> None:
            self.codex_bin = codex_bin
            self.cwd = cwd
            self.env = env
            self.started = 0
            self.initialized = 0
            self.thread_starts = 0
            self.closed = 0
            self._notifications: list[dict] = []

        def start(self) -> None:
            self.started += 1

        def initialize(self) -> dict:
            self.initialized += 1
            return {"ok": True}

        def thread_start(self, params: dict) -> dict:
            self.thread_starts += 1
            started = _agent_item("final-1", "")
            completed = _agent_item("final-1", "Fresh.")
            self._notifications = [
                {"method": "item/started", "params": {"item": started}},
                {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": "final-1", "delta": "Fresh."},
                },
                {"method": "item/completed", "params": {"item": completed}},
                {
                    "method": "turn/completed",
                    "params": {"turn": _completed_turn("turn-1", completed)},
                },
            ]
            return {"thread": {"id": "thread-1"}}

        def turn_start(self, thread_id: str, text: str) -> dict:
            assert thread_id == "thread-1"
            assert text == "Say hello"
            return {"turn": {"id": "turn-1"}}

        def next_notification(self, timeout_s=None):
            del timeout_s
            if not self._notifications:
                raise AssertionError("no more notifications")
            return _correlated_notification(
                self._notifications.pop(0),
                thread_id="thread-1",
                turn_id="turn-1",
            )

        def close(self) -> None:
            self.closed += 1

    def _fake_client_ctor(*, codex_bin: str, cwd: str, env: dict):
        client = _FreshFakeClient(codex_bin=codex_bin, cwd=cwd, env=env)
        created_clients.append(client)
        return client

    monkeypatch.setattr(runtime_codex_module, "_CodexJsonRpcClient", _fake_client_ctor)
    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    messages = [{"role": "user", "content": "Say hello"}]

    for _ in range(2):
        runtime = provider_registry.create_runtime(descriptor)
        result = runtime.invoke(
            client={"api_key": "codex"},
            model=model,
            messages=messages,
            tools=None,
            stream=False,
            context=ProviderRuntimeContext(session_state=_FakeSessionState(), agent_config={}, stream=False),
        )
        timing = result.metadata.get("provider_runtime_timing") or {}
        assert timing.get("client_cache_hit") is False

    assert len(created_clients) == 2
    assert [client.started for client in created_clients] == [1, 1]
    assert [client.initialized for client in created_clients] == [1, 1]
    assert [client.thread_starts for client in created_clients] == [1, 1]
    assert [client.closed for client in created_clients] == [1, 1]

    warm = runtime_codex_module.prewarm_codex_app_server(model="gpt-5.4-mini", cwd="/tmp/workspace")
    assert warm["disabled"] is True
    assert len(created_clients) == 2
