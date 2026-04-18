from __future__ import annotations

from agentic_coder_prototype.provider import runtime_codex as runtime_codex_module
from agentic_coder_prototype.provider_routing import provider_router
from agentic_coder_prototype.provider_runtime import ProviderRuntimeContext, provider_registry

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
        return self._notifications.pop(0)


def test_codex_provider_routes_to_app_server() -> None:
    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    assert descriptor.provider_id == "codex"
    assert descriptor.runtime_id == "codex_app_server"
    assert model == "gpt-5.4-mini"
    client_config = provider_router.create_client_config("codex/gpt-5.4-mini")
    assert client_config["api_key"] == "codex"


def test_codex_runtime_streams_commentary_tool_exec_and_final_answer(monkeypatch) -> None:
    descriptor, model = provider_router.get_runtime_descriptor("codex/gpt-5.4-mini")
    runtime = provider_registry.create_runtime(descriptor)
    fake_client = _FakeClient(
        [
            {"method": "item/started", "params": {"item": {"id": "commentary-1", "type": "agentMessage", "phase": "commentary"}}},
            {"method": "item/agentMessage/delta", "params": {"item_id": "commentary-1", "delta": "Checking now."}},
            {"method": "item/completed", "params": {"item": {"id": "commentary-1", "type": "agentMessage", "phase": "commentary", "text": "Checking now."}}},
            {"method": "item/started", "params": {"item": {"id": "call-1", "type": "commandExecution", "command": "pwd", "processId": "proc-1"}}},
            {"method": "item/completed", "params": {"item": {"id": "call-1", "type": "commandExecution", "processId": "proc-1", "aggregatedOutput": "/tmp/workspace\n", "exitCode": 0}}},
            {"method": "item/started", "params": {"item": {"id": "final-1", "type": "agentMessage", "phase": "final_answer"}}},
            {"method": "item/agentMessage/delta", "params": {"item_id": "final-1", "delta": "Done."}},
            {"method": "item/completed", "params": {"item": {"id": "final-1", "type": "agentMessage", "phase": "final_answer", "text": "Done."}}},
            {"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "completed"}}},
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
    assert result.messages[0].content == "Done."
    assert result.metadata["provider_turn_completed"] is True
    assert result.metadata["provider_turn_completion_method"] == "codex_app_server"
    assert result.metadata["provider_turn_completion_reason"] == "codex_turn_completed"
    timing = result.metadata.get("provider_runtime_timing") or {}
    assert isinstance(timing, dict)
    assert timing.get("notification_count") == 9
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
            self._notifications = [
                {"method": "item/started", "params": {"item": {"id": f"final-{self.thread_starts}", "type": "agentMessage", "phase": "final_answer"}}},
                {"method": "item/agentMessage/delta", "params": {"item_id": f"final-{self.thread_starts}", "delta": f"Done {self.thread_starts}."}},
                {"method": "item/completed", "params": {"item": {"id": f"final-{self.thread_starts}", "type": "agentMessage", "phase": "final_answer", "text": f"Done {self.thread_starts}."}}},
                {"method": "turn/completed", "params": {"turn": {"id": f"turn-{self.thread_starts}", "status": "completed"}}},
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
            return self._notifications.pop(0)

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
            self._notifications = [
                {"method": "item/started", "params": {"item": {"id": f"final-{self.thread_starts}", "type": "agentMessage", "phase": "final_answer"}}},
                {"method": "item/agentMessage/delta", "params": {"item_id": f"final-{self.thread_starts}", "delta": "Done."}},
                {"method": "item/completed", "params": {"item": {"id": f"final-{self.thread_starts}", "type": "agentMessage", "phase": "final_answer", "text": "Done."}}},
                {"method": "turn/completed", "params": {"turn": {"id": f"turn-{self.thread_starts}", "status": "completed"}}},
            ]
            return {"thread": {"id": f"thread-{self.thread_starts}"}}

        def turn_start(self, thread_id: str, text: str) -> dict:
            assert text == "Say hello"
            return {"turn": {"id": f"turn-{self.thread_starts}"}}

        def next_notification(self, timeout_s=None):
            del timeout_s
            if not self._notifications:
                raise AssertionError("no more notifications")
            return self._notifications.pop(0)

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
            self._notifications = [
                {"method": "item/started", "params": {"item": {"id": "final-1", "type": "agentMessage", "phase": "final_answer"}}},
                {"method": "item/agentMessage/delta", "params": {"item_id": "final-1", "delta": "Fresh."}},
                {"method": "item/completed", "params": {"item": {"id": "final-1", "type": "agentMessage", "phase": "final_answer", "text": "Fresh."}}},
                {"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "completed"}}},
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
            return self._notifications.pop(0)

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
