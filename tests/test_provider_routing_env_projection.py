from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import pytest

import breadboard_engine.provider.routing as routing_module
import breadboard_engine.provider_broker as broker_module
import breadboard_engine.provider_broker.broker as broker_impl
from breadboard_engine.provider.routing import ProviderRouter


class _StubBroker:
    def __init__(
        self,
        material: dict[str, Any] | None,
        *,
        store_path: str = "/tmp/stub-credentials.sqlite3",
    ) -> None:
        self.material = material
        self.store = SimpleNamespace(path=store_path)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.redeem_calls: list[tuple[str, dict[str, Any]]] = []
        self.release_calls: list[str] = []

    def issue_execution_material(self, provider_id: str, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append((provider_id, dict(kwargs)))
        return dict(self.material) if self.material is not None else None

    def redeem_execution_material(self, lease_id: str, **kwargs: Any) -> dict[str, Any] | None:
        self.redeem_calls.append((lease_id, dict(kwargs)))
        if lease_id in self.release_calls:
            return None
        if self.material is None or self.material.get("lease_id") != lease_id:
            return None
        return dict(self.material)

    def release_execution_material(self, lease_id: str) -> bool:
        self.release_calls.append(lease_id)
        return True


class _StubLeaseChannel:
    def __init__(self, material: dict[str, Any] | None) -> None:
        self.material = material
        self.calls: list[dict[str, Any]] = []

    def redeem(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(dict(kwargs))
        return dict(self.material) if self.material is not None else None


def _poison_local_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps({"OPENAI_API_KEY": "local-auth-poison"}), encoding="utf-8")
    monkeypatch.setattr(routing_module, "_CODEX_AUTH_PATH", auth_path, raising=False)


def test_openai_client_config_uses_only_broker_execution_material(monkeypatch, tmp_path: Path) -> None:
    _poison_local_auth(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-poison")
    monkeypatch.setenv("BREADBOARD_OPENAI_AUTH_BASE_URL", "https://environment.invalid/v1")
    monkeypatch.setenv(
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
        json.dumps({"Authorization": "Bearer environment-poison", "X-Environment": "poison"}),
    )
    broker = _StubBroker(
        {
            "api_key": "broker-token",
            "base_url": "https://broker.example.test/v1",
            "headers": {"Authorization": "Bearer broker-token", "X-Broker": "1"},
            "lease_id": "bblease-test",
        }
    )
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    client_config = ProviderRouter().create_client_config("openai/gpt-5.4-mini")

    assert client_config == {
        "model": "gpt-5.4-mini",
        "api_key": "broker-token",
        "base_url": "https://broker.example.test/v1",
        "default_headers": {"Authorization": "Bearer broker-token", "X-Broker": "1"},
        "credential_source": "broker_lease",
        "lease_id": "bblease-test",
    }
    assert broker.calls == [
        (
            "openai",
            {
                "endpoint_id": "openai/gpt-5.4-mini",
                "minimum_validity_ms": 0,
            },
        )
    ]


def test_provider_router_redeems_exact_capability_without_new_issuance(monkeypatch) -> None:
    broker = _StubBroker(None)
    channel = _StubLeaseChannel(
        {
            "api_key": "broker-token",
            "lease_id": "bblease-channel",
        }
    )
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    client_config = ProviderRouter().create_client_config(
        "openai/gpt-5.4-mini",
        lease_channel=channel,
    )

    assert client_config["api_key"] == "broker-token"
    assert client_config["lease_id"] == "bblease-channel"
    assert broker.calls == []
    assert channel.calls == [
        {
            "provider_id": "openai",
            "endpoint_id": "openai/gpt-5.4-mini",
        }
    ]


def test_invalid_capability_fails_closed_without_new_issuance(monkeypatch) -> None:
    broker = _StubBroker(None)
    channel = _StubLeaseChannel(None)
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    client_config = ProviderRouter().create_client_config(
        "openai/gpt-5.4-mini",
        lease_channel=channel,
    )

    assert client_config["api_key"] is None
    assert broker.calls == []
    assert channel.calls == [
        {
            "provider_id": "openai",
            "endpoint_id": "openai/gpt-5.4-mini",
        }
    ]


@pytest.mark.parametrize(
    ("model_id", "credential_env"),
    [
        ("openai/gpt-5.4-mini", "OPENAI_API_KEY"),
        ("openrouter/openai/gpt-4o-mini", "OPENROUTER_API_KEY"),
        ("anthropic/claude-sonnet-4", "ANTHROPIC_API_KEY"),
    ],
)
def test_provider_environment_and_local_auth_are_not_execution_fallbacks(
    monkeypatch,
    tmp_path: Path,
    model_id: str,
    credential_env: str,
) -> None:
    _poison_local_auth(monkeypatch, tmp_path)
    monkeypatch.setenv(credential_env, "environment-poison")
    monkeypatch.setenv("BREADBOARD_OPENAI_AUTH_BASE_URL", "https://environment.invalid/v1")
    monkeypatch.setenv("BREADBOARD_OPENAI_AUTH_HEADERS_JSON", '{"X-Environment":"poison"}')
    broker = _StubBroker(None)
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    client_config = ProviderRouter().create_client_config(model_id)

    assert client_config["api_key"] is None
    assert client_config.get("credential_source") is None
    assert "environment-poison" not in json.dumps(client_config)
    assert "local-auth-poison" not in json.dumps(client_config)


def test_conductor_startup_does_not_project_runtime_auth(monkeypatch, tmp_path: Path) -> None:
    import breadboard_engine.agent_llm_openai as conductor_module

    for key in (
        "OPENAI_API_KEY",
        "BREADBOARD_OPENAI_AUTH_BASE_URL",
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
    ):
        monkeypatch.delenv(key, raising=False)

    def bootstrap_without_side_effects(instance: Any, **kwargs: Any) -> None:
        instance.config = kwargs["config"]

    monkeypatch.setattr(conductor_module, "bootstrap_conductor", bootstrap_without_side_effects)
    conductor_class = conductor_module.OpenAIConductor.__ray_metadata__.modified_class
    conductor_class(
        workspace=str(tmp_path),
        config={
            "provider_auth_runtime": {
                "openai": {
                    "api_key": "runtime-config-poison",
                    "base_url": "https://runtime.invalid/v1",
                    "headers": {"Authorization": "Bearer runtime-config-poison"},
                }
            }
        },
        local_mode=True,
    )

    assert all(
        key not in os.environ
        for key in (
            "OPENAI_API_KEY",
            "BREADBOARD_OPENAI_AUTH_BASE_URL",
            "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
        )
    )


def test_ray_child_receives_only_confined_lease_capability_and_releases_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import breadboard_engine.agent as agent_module

    captured: dict[str, Any] = {}
    channel = object()

    class FakeServer:
        stop_calls = 0

        def stop(self) -> None:
            self.stop_calls += 1

    server = FakeServer()

    def fake_start_channel(broker_arg: Any, **kwargs: Any) -> tuple[Any, Any]:
        captured["channel_broker"] = broker_arg
        captured["channel_kwargs"] = kwargs
        return channel, server

    class FakeRun:
        @staticmethod
        def remote(*args: Any, **kwargs: Any) -> str:
            captured["run_args"] = args
            captured["run_kwargs"] = kwargs
            return "run-ref"

    class FakeAgentHandle:
        run_agentic_loop = FakeRun()

    class FakeRemote:
        @staticmethod
        def remote(**kwargs: Any) -> FakeAgentHandle:
            captured["remote_kwargs"] = kwargs
            return FakeAgentHandle()

    class FakeConductor:
        @staticmethod
        def options(**kwargs: Any) -> type[FakeRemote]:
            captured["options"] = kwargs
            return FakeRemote

    class FakeRay:
        @staticmethod
        def is_initialized() -> bool:
            return True

        @staticmethod
        def get(reference: Any) -> dict[str, Any]:
            assert reference == "run-ref"
            return {"ok": True}

    for key in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "BREADBOARD_OPENAI_AUTH_BASE_URL",
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
    ):
        monkeypatch.setenv(key, f"credential-poison:{key}")
    monkeypatch.setattr(agent_module, "_get_ray", lambda: FakeRay())
    monkeypatch.setattr(agent_module, "OpenAIConductor", FakeConductor)
    monkeypatch.setattr(broker_impl, "start_lease_capability_channel", fake_start_channel)
    broker = _StubBroker(
        {
            "api_key": "broker-token",
            "lease_id": "bblease-child-channel",
        },
        store_path=str(tmp_path / "credentials.sqlite3"),
    )
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    coder = object.__new__(agent_module.AgenticCoder)
    coder.config_path = str(tmp_path / "config.json")
    coder.config = {"providers": {"default_model": "openai/gpt-5.4-mini"}, "workspace": {}}
    coder.workspace_dir = str(tmp_path / "workspace")
    coder.agent = None
    coder._local_mode = False
    coder._provider_lease_id = None
    coder._provider_lease_channel = None
    coder._provider_lease_server = None
    coder._provider_worker_state_dir = None
    monkeypatch.setattr(coder, "_resolve_workspace_path", lambda: tmp_path / "workspace")

    coder.initialize()
    worker_state_dir = Path(coder._provider_worker_state_dir or "")

    assert captured["channel_broker"] is broker
    assert captured["channel_kwargs"] == {
        "lease_id": "bblease-child-channel",
        "provider_id": "openai",
        "endpoint_id": "openai/gpt-5.4-mini",
        "worker_state_dir": str(worker_state_dir),
    }
    env_vars = captured["options"]["runtime_env"]["env_vars"]
    assert env_vars["BREADBOARD_CREDENTIAL_STORE_PATH"] == ""
    assert env_vars["BREADBOARD_CREDENTIAL_DB"] == ""
    assert env_vars["BREADBOARD_STATE_DIR"] == str(worker_state_dir)
    assert captured["remote_kwargs"]["provider_lease_channel"] is channel
    assert broker.calls == [
        (
            "openai",
            {
                "session_id": "agentic-coder",
                "endpoint_id": "openai/gpt-5.4-mini",
                "minimum_validity_ms": 0,
            },
        )
    ]

    assert coder.run_task("hello") == {"ok": True}

    assert broker.release_calls == ["bblease-child-channel"]
    assert server.stop_calls == 1
    assert coder._provider_lease_id is None
    assert coder._provider_lease_channel is None
    assert coder._provider_lease_server is None
    assert not worker_state_dir.exists()
    serialized = repr(captured)
    assert "broker-token" not in serialized
    assert "credential-poison" not in serialized


def _active_remote_coder(
    agent_module: Any,
    tmp_path: Path,
    remote_call: Any,
) -> tuple[Any, Path, Any]:
    worker_state_dir = tmp_path / "worker-state"
    worker_state_dir.mkdir()

    class FakeServer:
        stop_calls = 0

        def stop(self) -> None:
            self.stop_calls += 1

    server = FakeServer()
    coder = object.__new__(agent_module.AgenticCoder)
    coder.config = {"providers": {"default_model": "openai/gpt-5.4-mini"}}
    coder.agent = SimpleNamespace(run_agentic_loop=SimpleNamespace(remote=remote_call))
    coder._local_mode = False
    coder._provider_lease_id = "bblease-lifecycle"
    coder._provider_lease_channel = object()
    coder._provider_lease_server = server
    coder._provider_worker_state_dir = str(worker_state_dir)
    return coder, worker_state_dir, server


def _assert_exact_lease_released(
    coder: Any,
    worker_state_dir: Path,
    broker: _StubBroker,
    server: Any,
) -> None:
    assert broker.release_calls == ["bblease-lifecycle"]
    assert broker.redeem_execution_material("bblease-lifecycle") is None
    assert server.stop_calls == 1
    assert coder._provider_lease_id is None
    assert coder._provider_lease_channel is None
    assert coder._provider_lease_server is None
    assert coder._provider_worker_state_dir is None
    assert not worker_state_dir.exists()
    coder._release_provider_lease()
    assert broker.release_calls == ["bblease-lifecycle"]
    assert server.stop_calls == 1


@pytest.mark.parametrize(
    "dispatch_error",
    [RuntimeError("dispatch failed")],
)
def test_run_task_releases_exact_lease_when_remote_submission_fails(
    monkeypatch,
    tmp_path: Path,
    dispatch_error: BaseException,
) -> None:
    import breadboard_engine.agent as agent_module

    broker = _StubBroker(
        {"api_key": "broker-token", "lease_id": "bblease-lifecycle"},
        store_path=str(tmp_path / "credentials.sqlite3"),
    )

    def fail_dispatch(*args: Any, **kwargs: Any) -> None:
        raise dispatch_error

    coder, worker_state_dir, server = _active_remote_coder(
        agent_module,
        tmp_path,
        fail_dispatch,
    )
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    with pytest.raises(type(dispatch_error), match="dispatch failed"):
        coder.run_task("hello")

    _assert_exact_lease_released(coder, worker_state_dir, broker, server)


def test_run_task_releases_exact_lease_when_preflight_validation_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import breadboard_engine.agent as agent_module

    broker = _StubBroker(
        {"api_key": "broker-token", "lease_id": "bblease-lifecycle"},
        store_path=str(tmp_path / "credentials.sqlite3"),
    )
    remote_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    coder, worker_state_dir, server = _active_remote_coder(
        agent_module,
        tmp_path,
        lambda *args, **kwargs: remote_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    with pytest.raises(ValueError):
        coder.run_task("hello", max_iterations="not-an-integer")  # type: ignore[arg-type]

    assert remote_calls == []
    _assert_exact_lease_released(coder, worker_state_dir, broker, server)


@pytest.mark.parametrize(
    "get_error",
    [RuntimeError("get failed"), KeyboardInterrupt()],
    ids=["ray-get-error", "ray-get-cancellation"],
)
def test_run_task_releases_exact_lease_when_remote_result_fails(
    monkeypatch,
    tmp_path: Path,
    get_error: BaseException,
) -> None:
    import breadboard_engine.agent as agent_module

    broker = _StubBroker(
        {"api_key": "broker-token", "lease_id": "bblease-lifecycle"},
        store_path=str(tmp_path / "credentials.sqlite3"),
    )

    class FakeRay:
        @staticmethod
        def get(reference: Any) -> None:
            assert reference == "run-ref"
            raise get_error

    coder, worker_state_dir, server = _active_remote_coder(
        agent_module,
        tmp_path,
        lambda *args, **kwargs: "run-ref",
    )
    monkeypatch.setattr(agent_module, "_get_ray", lambda: FakeRay())
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)

    with pytest.raises(
        type(get_error),
        match="get failed" if isinstance(get_error, RuntimeError) else None,
    ):
        coder.run_task("hello")

    _assert_exact_lease_released(coder, worker_state_dir, broker, server)


@pytest.mark.parametrize(
    "scenario",
    ["normal-exit", "keyboard-interrupt", "handled-remote-error"],
)
def test_interactive_session_releases_exact_lease_on_every_exit(
    monkeypatch,
    tmp_path: Path,
    scenario: str,
) -> None:
    import breadboard_engine.agent as agent_module

    broker = _StubBroker(
        {"api_key": "broker-token", "lease_id": "bblease-lifecycle"},
        store_path=str(tmp_path / "credentials.sqlite3"),
    )

    class FakeRay:
        @staticmethod
        def get(reference: Any) -> dict[str, Any]:
            assert reference == "run-ref"
            if scenario == "handled-remote-error":
                raise RuntimeError("remote failed")
            return {"completion_reason": "done"}

    coder, worker_state_dir, server = _active_remote_coder(
        agent_module,
        tmp_path,
        lambda *args, **kwargs: "run-ref",
    )
    coder.workspace_dir = str(tmp_path / "workspace")
    monkeypatch.setattr(agent_module, "_get_ray", lambda: FakeRay())
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)
    if scenario == "normal-exit":
        responses = iter(["exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(responses))
    elif scenario == "keyboard-interrupt":
        def interrupt(prompt: str) -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", interrupt)
    else:
        responses = iter(["hello", "exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(responses))

    coder.interactive_session()

    _assert_exact_lease_released(coder, worker_state_dir, broker, server)

@pytest.mark.asyncio
async def test_session_runner_startup_observes_broker_source_and_secret_free_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
    from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry
    from breadboard_engine.api.cli_bridge.session_runner import SessionRunner

    credential_keys = (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "BREADBOARD_OPENAI_AUTH_BASE_URL",
        "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
    )
    for key in credential_keys:
        monkeypatch.delenv(key, raising=False)

    broker = _StubBroker(
        {
            "api_key": "broker-session-token",
            "headers": {"Authorization": "Bearer broker-session-token"},
            "lease_id": "bblease-session-start",
        }
    )
    monkeypatch.setattr(broker_module, "get_provider_broker", lambda: broker)
    observed: dict[str, Any] = {}

    class StartupAgent:
        workspace_dir = str(tmp_path / "workspace")
        config_path = ""

        def initialize(self) -> None:
            observed["environment"] = {key: os.environ.get(key) for key in credential_keys}
            observed["client_config"] = ProviderRouter().create_client_config(
                "openai/gpt-5.4-mini"
            )

    def agent_factory(snapshot: str, _workspace: str | None, _overrides: dict[str, Any] | None) -> StartupAgent:
        observed["snapshot"] = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        return StartupAgent()

    request = SessionCreateRequest(
        config_path=str(tmp_path / "agent.json"),
        task="credential boundary",
        workspace=str(tmp_path / "workspace"),
        stream=False,
    )
    runner = SessionRunner(
        session=SessionRecord(session_id="sec123-startup", status=SessionStatus.RUNNING),
        registry=SessionRegistry(),
        request=request,
        agent_factory=agent_factory,
    )
    runner._base_config_cache = {
        "providers": {"default_model": "openai/gpt-5.4-mini"},
        "workspace": {"root": str(tmp_path / "workspace")},
    }

    await runner._ensure_agent_initialized()

    assert observed["environment"] == {key: None for key in credential_keys}
    assert observed["client_config"]["credential_source"] == "broker_lease"
    assert observed["client_config"]["lease_id"] == "bblease-session-start"
    assert observed["client_config"]["api_key"] == "broker-session-token"
    assert "provider_auth_runtime" not in json.dumps(observed["snapshot"])
