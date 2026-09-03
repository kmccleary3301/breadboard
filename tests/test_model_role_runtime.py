from __future__ import annotations

import asyncio
from contextlib import contextmanager
import copy
from types import SimpleNamespace
from typing import Any

import pytest
from breadboard.product.runtime import Session as ProductSession
from breadboard.product.runtime.events import rebuild

from breadboard_engine import agent_llm_openai as agent_llm_openai_module
from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.api.cli_bridge.models import (
    SessionCommandRequest,
    SessionCreateRequest,
    SessionInputRequest,
    SessionStatus,
)
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.messaging.markdown_logger import MarkdownLogger
from breadboard_engine.model_roles import (
    ModelRoleResolutionError,
    compile_model_roles,
    execution_model_role,
    resolve_role_name,
)
from breadboard_engine.provider.contracts import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from breadboard_engine.provider.health import RouteHealthManager
from breadboard_engine.provider.invoker import ProviderInvoker
from breadboard_engine.provider.metrics import ProviderMetricsCollector
from breadboard_engine.provider.model_role_options import (
    anthropic_role_options,
    codex_role_options,
    openai_chat_role_options,
    openai_responses_role_options,
)
from breadboard_engine.state.session_state import SessionState


def _document() -> dict:
    def role(model: str) -> dict:
        return {
            "primary": {"provider_id": "mock", "model_id": model},
            "fallbacks": [],
            "fallback_on": [],
        }

    return {
        "schema_version": "bb.model_roles.v1",
        "defaults": {
            "role": "default",
            "known_but_unbound_role": "use_default",
            "unknown_role": "error",
        },
        "roles": {
            "default": role("primary"),
            "slow": role("slow"),
            "task": role("task"),
        },
        "dispatch": {
            "subagents": {"reviewer": "slow"},
            "lanes": {"main": "default"},
        },
        "policy": {
            "allow_environment_overrides": False,
            "cross_provider_fallback": "forbidden",
            "account_failover": "forbidden",
        },
    }


def _conductor(config: dict) -> object:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    instance = object.__new__(cls)
    instance.config = config
    instance._model_role_lock = config.get("model_role_lock")
    instance._active_model_role = config.get("active_model_role", "")
    return instance


def _role_policy_context(binding: dict) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        session_state=SimpleNamespace(set_provider_metadata=lambda *_args: None),
        agent_config={
            "active_model_role": "default",
            "model_role_lock": {
                "defaults": {"role": "default"},
                "roles": {"default": binding},
            },
        },
    )


def test_role_policy_translates_provider_native_request_options() -> None:
    openai = openai_responses_role_options(
        _role_policy_context(
            {
                "reasoning": {
                    "mode": "effort",
                    "effort": "xhigh",
                    "expose_summary": True,
                },
                "generation": {
                    "max_output_tokens": 2048,
                },
                "service_tier": "priority",
            }
        )
    )
    assert openai == {
        "max_output_tokens": 2048,
        "service_tier": "priority",
        "reasoning": {"effort": "xhigh", "summary": "auto"},
    }

    chat_request, chat_extra = openai_chat_role_options(
        _role_policy_context(
            {
                "reasoning": {"mode": "effort", "effort": "high"},
                "generation": {"max_output_tokens": 1024, "seed": 7},
                "service_tier": "priority",
            }
        ),
        provider_id="openrouter",
    )
    assert chat_request == {
        "max_completion_tokens": 1024,
        "seed": 7,
        "service_tier": "priority",
    }
    assert chat_extra == {"reasoning": {"effort": "high"}}

    anthropic = anthropic_role_options(
        _role_policy_context(
            {
                "reasoning": {"mode": "budget", "budget_tokens": 4096},
                "generation": {"max_output_tokens": 8192},
            }
        )
    )
    assert anthropic == {
        "max_tokens": 8192,
        "thinking": {"type": "enabled", "budget_tokens": 4096},
    }

    codex = codex_role_options(
        _role_policy_context(
            {
                "reasoning": {
                    "mode": "effort",
                    "effort": "high",
                    "expose_summary": False,
                },
                "service_tier": "priority",
            }
        )
    )
    assert codex == {
        "serviceTier": "priority",
        "effort": "high",
        "summary": "none",
    }


@pytest.mark.parametrize(
    ("translator", "binding", "error_code"),
    [
        (
            openai_responses_role_options,
            {"generation": {"seed": 7}},
            "unsupported_role_generation_seed",
        ),
        (
            openai_responses_role_options,
            {
                "reasoning": {"mode": "effort", "effort": "high"},
                "generation": {"temperature": 0.2},
            },
            "unsupported_role_generation_sampling_with_reasoning",
        ),
        (
            anthropic_role_options,
            {
                "reasoning": {"mode": "budget", "budget_tokens": 4096},
                "generation": {"temperature": 0.2},
            },
            "unsupported_role_generation_sampling_with_thinking",
        ),
        (
            anthropic_role_options,
            {
                "reasoning": {"mode": "effort", "effort": "high"},
                "generation": {"top_p": 0.8},
            },
            "unsupported_role_generation_sampling_with_thinking",
        ),
        (
            anthropic_role_options,
            {
                "reasoning": {"mode": "budget", "budget_tokens": 1},
                "generation": {"max_output_tokens": 8192},
            },
            "unsupported_role_reasoning_budget",
        ),
        (
            anthropic_role_options,
            {
                "reasoning": {"mode": "budget", "budget_tokens": 4096},
                "generation": {"max_output_tokens": 4096},
            },
            "unsupported_role_reasoning_budget",
        ),
    ],
)
def test_role_policy_rejects_provider_incompatible_generation_options(
    translator,
    binding: dict,
    error_code: str,
) -> None:
    with pytest.raises(ProviderRuntimeError) as error:
        translator(_role_policy_context(binding))

    assert error.value.details["code"] == error_code


def test_service_compiles_configured_catalog_into_session_lock() -> None:
    lock = SessionService._compile_session_role_lock(
        {
            "providers": {
                "default_model": "mock/primary",
                "models": ["mock/primary", "mock/slow", "mock/task"],
            }
        },
        session_id="service-role-session",
        role_document=_document(),
    )

    assert lock is not None
    assert lock["roles"]["default"]["primary"]["route_id"] == "mock/primary"
    assert lock["roles"]["slow"]["primary"]["route_id"] == "mock/slow"
    assert lock["dispatch"]["subagents"]["reviewer"] == "slow"


def test_session_role_switch_updates_live_agent_and_rejects_direct_model_mutation() -> (
    None
):
    lock = compile_model_roles(_document())
    record = SessionRecord(
        session_id="role-switch",
        status=SessionStatus.STARTING,
        metadata={},
    )
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="unused.json"),
    )
    runner._prepared_runtime_config = {"providers": {"default_model": "mock/pre-lock"}}
    runner.install_model_role_lock(lock)

    class ActiveAgent:
        def __init__(self, config: dict) -> None:
            self.config = copy.deepcopy(config)
            self.calls: list[dict] = []

        def apply_runtime_overrides(self, overrides: dict) -> bool:
            self.calls.append(dict(overrides))
            self.config.setdefault("providers", {})["default_model"] = overrides[
                "providers.default_model"
            ]
            self.config["active_model_role"] = overrides["active_model_role"]
            self.config["model_role_lock"] = overrides["model_role_lock"]
            return True

    active = ActiveAgent(runner.current_runtime_config())
    runner._agent = active
    durable: list[tuple[str, object]] = []

    async def persist_metadata(session_id: str, *, metadata: dict) -> SessionRecord:
        assert session_id == record.session_id
        durable.append(("metadata", metadata["active_model_role"]))
        return record

    runner.registry.update_metadata = persist_metadata  # type: ignore[method-assign]
    response = asyncio.run(
        runner.handle_command(
            "set_role",
            {"role": "slow"},
            durable_reconfigure=lambda config: durable.append(
                ("lock", config["active_model_role"])
            ),
        )
    )

    assert response["model"] == "mock/slow"
    assert record.metadata["active_model_role"] == "slow"
    assert runner.current_runtime_config()["providers"]["default_model"] == "mock/slow"
    assert active.calls[-1]["providers.default_model"] == "mock/slow"
    assert active.calls[-1]["active_model_role"] == "slow"
    assert durable == [("lock", "slow"), ("metadata", "slow")]

    with pytest.raises(ModelRoleResolutionError) as error:
        asyncio.run(runner.handle_command("set_model", {"model": "mock/outside"}))
    assert error.value.problem.code == "lock_immutable"


@pytest.mark.asyncio
async def test_service_routes_role_commands_through_durable_reconfiguration() -> None:
    registry = SessionRegistry()
    record = SessionRecord(
        session_id="service-role-transition",
        status=SessionStatus.RUNNING,
        metadata={},
    )
    transitions: list[tuple[str, Any, str]] = []
    role_lock = compile_model_roles(_document()).as_dict()

    class DurableRunner:
        request = SimpleNamespace(config_path="unused.json")

        async def handle_command(
            self,
            command: str,
            payload: dict,
            *,
            durable_reconfigure=None,
        ) -> dict:
            assert command == "set_role"
            assert payload == {"role": "slow"}
            assert durable_reconfigure is not None
            durable_reconfigure(
                {
                    "version": 2,
                    "providers": {
                        "default_model": "mock/slow",
                        "models": ["mock/primary", "mock/slow", "mock/task"],
                    },
                    "model_role_lock": role_lock,
                    "active_model_role": "slow",
                }
            )
            return {"status": "ok", "role": "slow"}

        def transition_product_session(
            self, transition: str, runtime_lock: Any, reason: str
        ) -> None:
            transitions.append((transition, runtime_lock, reason))

    record.runner = DurableRunner()
    await registry.create(record)
    service = SessionService(registry=registry)

    result = await service.execute_command(
        record.session_id,
        SessionCommandRequest(command="set_role", payload={"role": "slow"}),
    )

    assert result.detail == {"status": "ok", "role": "slow"}
    assert len(transitions) == 1
    transition, runtime_lock, reason = transitions[0]
    assert transition == "reconfigure"
    assert reason == "set_role"
    assert runtime_lock["model_role_lock"]["lock_hash"] == role_lock["lock_hash"]


@pytest.mark.asyncio
async def test_role_switch_survives_product_session_rebuild() -> None:
    session_id = "durable-role-rebuild"
    role_lock = compile_model_roles(_document())
    runtime_config = {
        "version": 2,
        "providers": {
            "default_model": "mock/primary",
            "models": ["mock/primary", "mock/slow", "mock/task"],
        },
        "model_role_lock": role_lock.as_dict(),
        "active_model_role": "default",
    }
    registry = SessionRegistry()
    record = SessionRecord(
        session_id=session_id,
        status=SessionStatus.RUNNING,
        metadata={},
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="unused.json"),
    )
    runner._prepared_runtime_config = copy.deepcopy(runtime_config)
    runner.install_model_role_lock(role_lock)

    class ActiveAgent:
        def __init__(self) -> None:
            self.config = copy.deepcopy(runtime_config)

        def apply_runtime_overrides(self, overrides: dict) -> bool:
            self.config.setdefault("providers", {})["default_model"] = overrides[
                "providers.default_model"
            ]
            self.config["active_model_role"] = overrides["active_model_role"]
            self.config["model_role_lock"] = overrides["model_role_lock"]
            return True

    runner._agent = ActiveAgent()
    initial_lock = SessionService._runtime_lock(
        session_id, runtime_config, "unused.json"
    )
    product_session = ProductSession.start(
        initial_lock,
        "task",
        session_id=session_id,
    )
    record.product_session = product_session
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)

    await service.execute_command(
        session_id,
        SessionCommandRequest(
            command="set_role",
            payload={"role": "slow"},
        ),
    )

    effective = SessionService._runtime_lock(
        session_id,
        runner.current_runtime_config(),
        "unused.json",
    )
    assert record.metadata["active_model_role"] == "slow"
    assert product_session.read_model.effective_lock_hash == effective["graph_hash"]
    assert (
        rebuild(product_session.events).effective_lock_hash == effective["graph_hash"]
    )


@pytest.mark.asyncio
async def test_role_switch_serializes_racing_input_without_deadlock() -> None:
    session_id = "durable-role-race"
    role_lock = compile_model_roles(_document())
    runtime_config = {
        "version": 2,
        "providers": {
            "default_model": "mock/primary",
            "models": ["mock/primary", "mock/slow", "mock/task"],
        },
        "model_role_lock": role_lock.as_dict(),
        "active_model_role": "default",
    }
    registry = SessionRegistry()
    record = SessionRecord(
        session_id=session_id,
        status=SessionStatus.RUNNING,
        metadata={},
    )
    runner = SessionRunner(
        session=record,
        registry=registry,
        request=SessionCreateRequest(config_path="unused.json"),
    )
    runner._prepared_runtime_config = copy.deepcopy(runtime_config)
    runner.install_model_role_lock(role_lock)

    class ActiveAgent:
        def __init__(self) -> None:
            self.config = copy.deepcopy(runtime_config)

        def apply_runtime_overrides(self, overrides: dict) -> bool:
            self.config.setdefault("providers", {})["default_model"] = overrides[
                "providers.default_model"
            ]
            self.config["active_model_role"] = overrides["active_model_role"]
            self.config["model_role_lock"] = overrides["model_role_lock"]
            return True

    runner._agent = ActiveAgent()
    initial_lock = SessionService._runtime_lock(
        session_id, runtime_config, "unused.json"
    )
    record.product_session = ProductSession.start(
        initial_lock,
        "task",
        session_id=session_id,
    )
    record.runner = runner
    await registry.create(record)
    service = SessionService(registry=registry)

    entered = asyncio.Event()
    release = asyncio.Event()
    real_handle_command = runner.handle_command

    async def blocked_handle_command(
        command: str,
        payload: dict,
        *,
        durable_reconfigure=None,
    ) -> dict:
        if command == "set_role":
            entered.set()
            await release.wait()
        return await real_handle_command(
            command,
            payload,
            durable_reconfigure=durable_reconfigure,
        )

    runner.handle_command = blocked_handle_command  # type: ignore[method-assign]
    role_switch = asyncio.create_task(
        service.execute_command(
            session_id,
            SessionCommandRequest(
                command="set_role",
                payload={"role": "slow"},
            ),
        )
    )
    await entered.wait()

    input_request = asyncio.create_task(
        service.send_input(
            session_id,
            SessionInputRequest(content="racing input"),
            defer_execution=lambda _operation: None,
        )
    )
    await asyncio.sleep(0)
    assert not input_request.done()

    release.set()
    role_result, input_result = await asyncio.wait_for(
        asyncio.gather(role_switch, input_request),
        timeout=2,
    )

    assert role_result.detail["status"] == "ok"
    assert role_result.detail["role"] == "slow"
    assert input_result.disposition == "started"
    assert runner._admission_lock_owner is None


def test_session_role_switch_rejects_active_turn() -> None:
    lock = compile_model_roles(_document())
    record = SessionRecord(
        session_id="role-active-turn",
        status=SessionStatus.RUNNING,
        metadata={},
    )
    record.active_turn_id = "turn-active"
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="unused.json"),
    )
    runner._prepared_runtime_config = {"providers": {"default_model": "mock/pre-lock"}}
    runner.install_model_role_lock(lock)

    with pytest.raises(ModelRoleResolutionError) as error:
        asyncio.run(runner.handle_command("set_role", {"role": "slow"}))

    assert error.value.problem.code == "model_role_transition_active_turn"
    assert record.metadata["active_model_role"] == "default"


def test_failed_live_role_switch_restores_prior_role_and_model() -> None:
    lock = compile_model_roles(_document())
    record = SessionRecord(
        session_id="role-rollback",
        status=SessionStatus.STARTING,
        metadata={},
    )
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="unused.json"),
    )
    runner._prepared_runtime_config = {"providers": {"default_model": "mock/pre-lock"}}
    runner.install_model_role_lock(lock)

    class RejectingAgent:
        def __init__(self, config: dict) -> None:
            self.config = copy.deepcopy(config)

        def apply_runtime_overrides(self, overrides: dict) -> bool:
            return overrides.get("active_model_role") != "slow"

    runner._agent = RejectingAgent(runner.current_runtime_config())
    with pytest.raises(RuntimeError, match="failed to apply locked model role"):
        asyncio.run(runner.handle_command("set_role", {"role": "slow"}))

    assert record.metadata["active_model_role"] == "default"
    assert record.metadata["model"] == "mock/primary"
    assert (
        runner.current_runtime_config()["providers"]["default_model"] == "mock/primary"
    )


@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
def test_role_switch_rolls_back_when_registry_persistence_fails(failure) -> None:
    lock = compile_model_roles(_document())
    record = SessionRecord(
        session_id="role-persistence-rollback",
        status=SessionStatus.STARTING,
        metadata={},
    )
    runner = SessionRunner(
        session=record,
        registry=SessionRegistry(),
        request=SessionCreateRequest(config_path="unused.json"),
    )
    runner._prepared_runtime_config = {"providers": {"default_model": "mock/pre-lock"}}
    runner.install_model_role_lock(lock)

    class ActiveAgent:
        def __init__(self, config: dict) -> None:
            self.config = copy.deepcopy(config)

        def apply_runtime_overrides(self, overrides: dict) -> bool:
            self.config.setdefault("providers", {})["default_model"] = overrides[
                "providers.default_model"
            ]
            self.config["active_model_role"] = overrides["active_model_role"]
            self.config["model_role_lock"] = overrides["model_role_lock"]
            return True

    async def fail_persistence(_session_id: str, *, metadata: dict) -> None:
        record.metadata = metadata
        raise failure("state volume unavailable")

    runner._agent = ActiveAgent(runner.current_runtime_config())
    runner.registry.update_metadata = fail_persistence  # type: ignore[method-assign]
    durable_roles: list[str] = []

    with pytest.raises(failure):
        asyncio.run(
            runner.handle_command(
                "set_role",
                {"role": "slow"},
                durable_reconfigure=lambda config: durable_roles.append(
                    config["active_model_role"]
                ),
            )
        )

    assert record.metadata["active_model_role"] == "default"
    assert record.metadata["model"] == "mock/primary"
    assert (
        runner.current_runtime_config()["providers"]["default_model"] == "mock/primary"
    )
    assert runner._agent.config["providers"]["default_model"] == "mock/primary"
    assert durable_roles == ["slow", "default"]


def test_conductor_refreshes_cached_lock_and_role_on_runtime_override() -> None:
    first = compile_model_roles(_document()).as_dict()
    second_document = _document()
    second_document["roles"]["slow"]["primary"]["model_id"] = "slow-new"
    second = compile_model_roles(second_document).as_dict()
    conductor = _conductor(
        {
            "model_role_lock": first,
            "active_model_role": "default",
            "providers": {"default_model": "mock/primary"},
        }
    )

    assert conductor.apply_config_overrides(
        {
            "model_role_lock": second,
            "active_model_role": "slow",
            "providers.default_model": "mock/slow-new",
        }
    )
    assert conductor._model_role_lock["lock_hash"] == second["lock_hash"]
    assert conductor._active_model_role == "slow"
    assert conductor._locked_target_for_role()["route_id"] == "mock/slow-new"

def test_conductor_runtime_config_replacement_removes_absent_keys() -> None:
    conductor = _conductor(
        {
            "mode": "plan",
            "providers": {"default_model": "mock/new"},
            "active_model_role": "slow",
        }
    )
    replacement = {"providers": {}}

    assert conductor.replace_config(replacement) is True
    assert conductor.config == replacement
    assert conductor._model_role_lock is None
    assert conductor._active_model_role == ""


def test_subagent_dispatch_uses_explicit_task_or_inherits_active_role() -> None:
    inherited_document = _document()
    inherited_document["dispatch"]["subagents"] = {"*": "task"}
    inherited_document["roles"]["task"]["metadata"] = {"source_provenance": "inherited"}
    inherited = compile_model_roles(inherited_document).as_dict()
    conductor = _conductor({"model_role_lock": inherited, "active_model_role": "slow"})

    assert conductor._locked_subagent_role("reviewer") == "slow"

    explicit_document = _document()
    explicit_document["dispatch"]["subagents"] = {"*": "task"}
    explicit_document["roles"]["task"]["metadata"] = {"source_provenance": "explicit"}
    explicit = compile_model_roles(explicit_document).as_dict()
    conductor = _conductor({"model_role_lock": explicit, "active_model_role": "slow"})

    assert conductor._locked_subagent_role("reviewer") == "task"


def test_unbound_vision_never_resolves_to_text_default() -> None:
    lock = compile_model_roles(_document()).as_dict()

    with pytest.raises(ModelRoleResolutionError) as error:
        resolve_role_name(lock, "vision")

    assert error.value.problem.code == "known_role_unbound"



def test_provider_lease_preserves_provider_specific_credential_material(
    monkeypatch,
) -> None:
    conductor = _conductor({})
    conductor._active_session_state = SimpleNamespace(
        get_provider_metadata=lambda _key, default=None: default
    )

    @contextmanager
    def execution_client_config(_model_id, **_kwargs):
        yield {
            "api_key": "codex-oauth-token",
            "access_token": "codex-oauth-token",
            "base_url": None,
            "default_headers": {},
        }

    monkeypatch.setattr(
        agent_llm_openai_module.provider_router,
        "execution_client_config",
        execution_client_config,
    )

    class Runtime:
        def create_client_from_config(self, config):
            return dict(config)

    with conductor._provider_client_lease("codex/gpt-5.5", Runtime()) as client:
        assert client["access_token"] == "codex-oauth-token"

def test_provider_lease_enforces_exact_locked_route_and_credential_origin() -> None:
    lock = compile_model_roles(_document()).as_dict()
    conductor = _conductor({"model_role_lock": lock, "active_model_role": "default"})
    conductor._active_session_state = SimpleNamespace(
        get_provider_metadata=lambda key, default=None: (
            "lease-session" if key == "session_id" else default
        )
    )

    class Runtime:
        def create_client(self, api_key, *, base_url=None, default_headers=None):
            return {
                "api_key": api_key,
                "base_url": base_url,
                "headers": default_headers,
            }

        def create_client_from_config(self, config):
            return self.create_client(
                config.get("api_key"),
                base_url=config.get("base_url"),
                default_headers=config.get("default_headers"),
            )

    with conductor._provider_client_lease("mock/primary", Runtime()) as client:
        assert client["api_key"] == "mock"

    with pytest.raises(ProviderRuntimeError) as error:
        with conductor._provider_client_lease("mock/outside", Runtime()):
            pass
    assert error.value.safe_code == "policy_rejection"

    with pytest.raises(ProviderRuntimeError) as inactive_error:
        with conductor._provider_client_lease("mock/slow", Runtime()):
            pass
    assert inactive_error.value.safe_code == "policy_rejection"

    with execution_model_role("slow"):
        with conductor._provider_client_lease("mock/slow", Runtime()) as client:
            assert client["api_key"] == "mock"

    with pytest.raises(ProviderRuntimeError) as restored_error:
        with conductor._provider_client_lease("mock/slow", Runtime()):
            pass
    assert restored_error.value.safe_code == "policy_rejection"

    conductor._model_role_lock["roles"]["default"]["primary"]["account_binding"] = {
        "kind": "provider_managed",
        "pin": "session",
    }
    with pytest.raises(ProviderRuntimeError) as error:
        with conductor._provider_client_lease("mock/primary", Runtime()):
            pass
    assert error.value.safe_code == "policy_rejection"


def test_provider_error_fallback_reason_is_structured_and_never_auth_or_output() -> (
    None
):
    assert (
        ProviderRuntimeError(
            "limited",
            details={"classification": "rate_limited"},
            kind="provider",
        ).model_fallback_reason
        == "rate_limited"
    )
    assert (
        ProviderRuntimeError(
            "timeout",
            details={"classification": "timeout"},
            kind="transport",
        ).model_fallback_reason
        == "timeout_before_output"
    )
    assert (
        ProviderRuntimeError(
            "auth",
            details={"status_code": 401},
            kind="provider",
            model_fallback_reason="provider_unavailable",
        ).model_fallback_reason
        is None
    )
    assert (
        ProviderRuntimeError(
            "output",
            details={"code": "rate_limited"},
            kind="provider",
            output_emitted=True,
            model_fallback_reason="rate_limited",
        ).model_fallback_reason
        is None
    )
    assert (
        ProviderRuntimeError(
            "protocol",
            details={"code": "stream_protocol_error"},
            kind="protocol",
            model_fallback_reason="provider_unavailable",
        ).model_fallback_reason
        is None
    )


def _invocation_state() -> tuple[SessionState, ProviderRuntimeContext]:
    state = SessionState(workspace=".", image="test")
    for key, value in {
        "session_id": "session",
        "input_id": "input",
        "turn_id": "turn",
    }.items():
        state.set_provider_metadata(key, value)
    context = ProviderRuntimeContext(
        session_state=state,
        agent_config={},
        session_id="session",
        input_id="input",
        turn_id="turn",
    )
    return state, context


def _result() -> ProviderResult:
    return ProviderResult(
        messages=[ProviderMessage(role="assistant", content="ok", tool_calls=[])],
        raw_response={},
        usage={},
        metadata={},
    )


def _invoker(callback) -> ProviderInvoker:
    return ProviderInvoker(
        provider_metrics=ProviderMetricsCollector(),
        route_health=RouteHealthManager(),
        logger_v2=SimpleNamespace(
            run_dir=None, append_text=lambda *args, **kwargs: None
        ),
        md_writer=SimpleNamespace(system=lambda text: text),
        retry_with_fallback=callback,
        update_health_metadata=lambda state: None,
        set_last_latency=lambda value: None,
        set_html_detected=lambda value: None,
    )


def test_stream_protocol_retry_is_same_model_but_rate_limit_uses_declared_model_fallback() -> (
    None
):
    state, context = _invocation_state()

    class ProtocolRuntime:
        descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock")

        def __init__(self) -> None:
            self.calls: list[bool] = []

        def invoke(self, **kwargs):
            self.calls.append(bool(kwargs["stream"]))
            if kwargs["stream"]:
                raise ProviderRuntimeError(
                    "stream failed",
                    details={"code": "stream_protocol_error"},
                    kind="protocol",
                )
            return _result()

    protocol = ProtocolRuntime()
    result, used_streaming = _invoker(lambda *args, **kwargs: None).invoke(
        runtime=protocol,
        client=object(),
        model="primary",
        send_messages=[{"role": "user", "content": "hello"}],
        tools_schema=None,
        stream_responses=True,
        runtime_context=context,
        session_state=state,
        markdown_logger=MarkdownLogger(None),
        turn_index=0,
        route_id="mock/primary",
    )
    assert result.messages[0].content == "ok"
    assert used_streaming is False
    assert protocol.calls == [True, False]

    state, context = _invocation_state()
    context.agent_config = {
        "active_model_role": "default",
        "model_role_lock": {
            "defaults": {"role": "default"},
            "roles": {
                "default": {
                    "fallback_on": ["rate_limited"],
                }
            },
        },
    }
    fallback_calls: list[ProviderRuntimeError] = []

    class LimitedRuntime:
        descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock")

        def __init__(self) -> None:
            self.calls: list[bool] = []

        def invoke(self, **kwargs):
            self.calls.append(bool(kwargs["stream"]))
            raise ProviderRuntimeError(
                "limited",
                details={"status_code": 429},
                kind="provider",
            )

    limited = LimitedRuntime()

    def fallback(*args, **kwargs):
        fallback_calls.append(kwargs["last_error"])
        return _result()

    result, used_streaming = _invoker(fallback).invoke(
        runtime=limited,
        client=object(),
        model="primary",
        send_messages=[{"role": "user", "content": "hello"}],
        tools_schema=None,
        stream_responses=True,
        runtime_context=context,
        session_state=state,
        markdown_logger=MarkdownLogger(None),
        turn_index=0,
        route_id="mock/primary",
    )
    assert result.messages[0].content == "ok"
    assert used_streaming is False
    assert limited.calls == [True]
    assert fallback_calls[0].model_fallback_reason == "rate_limited"


def test_auth_error_never_retries_or_enters_model_fallback() -> None:
    state, context = _invocation_state()
    context.agent_config = {
        "active_model_role": "default",
        "model_role_lock": {
            "defaults": {"role": "default"},
            "roles": {
                "default": {
                    "fallback_on": ["provider_unavailable"],
                }
            },
        },
    }
    callback_calls: list[object] = []

    class Runtime:
        descriptor = SimpleNamespace(provider_id="mock", runtime_id="mock")

        def invoke(self, **kwargs):
            raise ProviderRuntimeError(
                "unauthorized",
                details={"status_code": 401},
                kind="provider",
                model_fallback_reason="provider_unavailable",
            )

    with pytest.raises(ProviderRuntimeError) as error:
        _invoker(lambda *args, **kwargs: callback_calls.append(object())).invoke(
            runtime=Runtime(),
            client=object(),
            model="primary",
            send_messages=[{"role": "user", "content": "hello"}],
            tools_schema=None,
            stream_responses=True,
            runtime_context=context,
            session_state=state,
            markdown_logger=MarkdownLogger(None),
            turn_index=0,
            route_id="mock/primary",
        )
    assert error.value.model_fallback_reason is None
    assert callback_calls == []


def test_persistence_restores_active_role_and_exact_model(tmp_path) -> None:
    lock = compile_model_roles(_document())
    registry = SessionRegistry(tmp_path)
    record = SessionRecord(
        session_id="persisted-role",
        status=SessionStatus.STOPPED,
        metadata={
            "model": "mock/slow",
            "model_role_lock": lock.as_dict(),
            "model_role_lock_hash": lock.lock_hash,
            "active_model_role": "slow",
        },
    )
    asyncio.run(registry.create(record))

    restored_registry = SessionRegistry(tmp_path)
    restored = asyncio.run(restored_registry.get("persisted-role"))
    assert restored is not None
    assert restored.metadata["active_model_role"] == "slow"
    assert restored.metadata["model"] == "mock/slow"
    assert restored.metadata["model_role_lock_hash"] == lock.lock_hash
