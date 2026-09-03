from __future__ import annotations
from types import SimpleNamespace; from pathlib import Path; import asyncio, hashlib, json, os, threading, pytest, yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from breadboard.product.harness import default_profile as harness_operations
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import events as runtime_ports; from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.runtime import session_store
from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.api.cli_bridge.models import SessionCommandRequest, SessionCreateRequest, SessionInputRequest, SessionStatus
from breadboard_engine.api.cli_bridge.events import EventType; from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.api.cli_bridge.session_runner import MAX_ATTACHMENT_BYTES
from breadboard_engine.api.cli_bridge.runtime_emission import _tool_names
from breadboard_engine.auth.enforcer import apply_dotted_overrides; from breadboard_engine.compilation.v2_loader import load_agent_config
from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.api.cli_bridge import session_artifacts
from breadboard_engine.api.cli_bridge.session_artifacts import SessionArtifactStore
from breadboard.product.harness.default_profile import DefaultProfileInvalidError, DefaultProfileUnavailableError
CONFIG = "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml"
RUNNER = "breadboard_engine.api.cli_bridge.session_runner.SessionRunner."
SERVICE = "breadboard_engine.api.cli_bridge.service."


@pytest.fixture(autouse=True)
def _clear_default_profile_cache():
    harness_operations.resolve_default_profile.cache_clear()
    yield
    harness_operations.resolve_default_profile.cache_clear()
class _Failing:
    def append(self, _event) -> None: raise OSError("sink unavailable")  # type: ignore[no-untyped-def]
    def put_nowait(self, _item) -> None: raise RuntimeError("broker unavailable")  # type: ignore[no-untyped-def]
class _Upload:
    filename, content_type, data = "proof.txt", "text/plain", b"proof"
    async def read(self, size: int = -1) -> bytes: data = self.data if size < 0 else self.data[:size]; self.data = self.data[len(data):]; return data
async def _stop(record) -> None:  # type: ignore[no-untyped-def]
    if record.dispatcher_task and not record.dispatcher_task.done(): await record.event_queue.put(None); await record.dispatcher_task
async def _create(monkeypatch, tmp_path, *, service=None, task="Say hi", **fields):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None); monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "events"))
    service = service or SessionService(); response = await service.create_session(SessionCreateRequest(config_path=CONFIG, task=task, **fields)); return service, response, await service.ensure_session(response.session_id)
@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "error"), [(None, FileNotFoundError), ("[", yaml.YAMLError)])
async def test_invalid_config_fails_before_session_publication(monkeypatch, tmp_path, content, error) -> None:
    config, records, events = tmp_path / "invalid.yaml", tmp_path / "records", tmp_path / "events"; config.write_text(content, encoding="utf-8") if content is not None else None
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events)); service = SessionService()
    with pytest.raises(error): await service.create_session(SessionCreateRequest(config_path=str(config), task="task"))
    assert service.registry._records == {} and not records.exists() and not events.exists()


def test_session_create_rejects_empty_config_path() -> None:
    for value in ("", " ", "\t\n"):
        with pytest.raises(ValueError):
            SessionCreateRequest(config_path=value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "create_request",
    [SessionCreateRequest(), SessionCreateRequest(config_path=None)],
)
async def test_default_session_create_uses_exact_profile_authority(
    monkeypatch, tmp_path, create_request
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    service = SessionService()
    response = await service.create_session(
        create_request.model_copy(
            update={
                "task": "",
                "workspace": str(workspace),
                "metadata": {
                    "config_path": "forged-config",
                    "default_profile": {"profile_id": "forged-profile"},
                    "safe": "retained",
                },
            }
        ),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)
    resolution = harness_operations.resolve_default_profile()
    identity = resolution.public_identity()
    assert record.runner.request.config_path == str(resolution.source_path)
    assert record.metadata["config_path"] == identity["definition_ref"]
    assert record.metadata["default_profile"] == identity
    assert record.metadata["safe"] == "retained"
    assert str(resolution.source_path) not in json.dumps(record.metadata)
    assert record.product_session.read_model.effective_lock_hash != (
        resolution.compilation.lock["graph_hash"]
    )
    assert record.metadata["active_model_role"] == "default"
    assert set(record.metadata["model_role_lock"]["roles"]) == {
        "default",
        "smol",
        "slow",
        "vision",
        "plan",
        "designer",
        "task",
    }
    assert record.runner.current_runtime_config()["workspace"]["root"] == str(workspace)
    skill_catalog = await service.list_skills(response.session_id)
    skill_payload = skill_catalog.model_dump(mode="json")
    assert skill_payload["sources"]["config_path"] == identity["definition_ref"]
    assert str(resolution.source_path) not in json.dumps(skill_payload)
    await service.stop_session(response.session_id)
    await _stop(record)

@pytest.mark.asyncio
async def test_create_strips_caller_internal_runtime_metadata(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    service = SessionService(state_root=tmp_path / "state")
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="reject caller manifest metadata",
            metadata={
                "artifact_manifest_ref": "caller-owned-value",
                "runtime_overrides": {"mode": "plan"},
                "skills_selection": {"mode": "allowlist", "allowlist": ["caller"]},
                "safe": "retained",
            },
        ),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)
    await service.registry.persist(record)

    assert {
        "artifact_manifest_ref",
        "runtime_overrides",
        "skills_selection",
    }.isdisjoint(record.metadata)
    assert record.metadata["safe"] == "retained"
    await service.stop_session(response.session_id)
    await _stop(record)

@pytest.mark.asyncio
async def test_default_event_root_preserves_internal_session_authority(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv(
        "BREADBOARD_SESSION_EVENT_ROOT",
        str(workspace / ".breadboard" / "sessions"),
    )
    monkeypatch.setenv(
        "BREADBOARD_RUNTIME_RECORD_ROOT",
        str(workspace / ".breadboard" / "service_records"),
    )
    service = SessionService()

    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="durable internal session",
            workspace=str(workspace),
        )
    )
    record = await service.ensure_session(response.session_id)
    await service.stop_session(response.session_id, reason="restart proof")

    restored, _ = session_store.load_session(workspace, response.session_id)
    assert restored.read_model.status == "canceled"
    await _stop(record)


@pytest.mark.asyncio
async def test_effective_config_workspace_preserves_internal_session_authority(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = tmp_path / "configured-workspace"
    workspace.mkdir()
    base_config = load_agent_config(CONFIG)
    base_config["workspace"] = {
        **dict(base_config.get("workspace") or {}),
        "root": str(workspace),
    }
    monkeypatch.setattr(RUNNER + "_load_base_config", lambda _runner: base_config)
    monkeypatch.setenv(
        "BREADBOARD_SESSION_EVENT_ROOT",
        str(workspace / ".breadboard" / "sessions"),
    )
    monkeypatch.setenv(
        "BREADBOARD_RUNTIME_RECORD_ROOT",
        str(workspace / ".breadboard" / "service_records"),
    )
    service = SessionService()

    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="configured workspace durability",
        )
    )
    record = await service.ensure_session(response.session_id)
    assert record.runner.request.workspace == str(workspace.resolve())
    await service.stop_session(response.session_id, reason="configured restart proof")

    restored, _ = session_store.load_session(workspace, response.session_id)
    assert restored.read_model.status == "canceled"
    await _stop(record)


@pytest.mark.asyncio
async def test_default_event_root_rejects_symlinked_session_directory(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = tmp_path / "workspace"
    metadata_root = workspace / ".breadboard"
    event_root = tmp_path / "configured-events"
    metadata_root.mkdir(parents=True)
    event_root.mkdir()
    (metadata_root / "sessions").symlink_to(event_root, target_is_directory=True)
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(event_root))
    monkeypatch.setenv(
        "BREADBOARD_RUNTIME_RECORD_ROOT",
        str(tmp_path / "records"),
    )
    monkeypatch.setenv(
        "BREADBOARD_SESSION_AUTHORITY_ROOT",
        str(tmp_path / "authority"),
    )
    service = SessionService()

    with pytest.raises(OSError):
        await service.create_session(
            SessionCreateRequest(
                config_path=CONFIG,
                task="symlink rejection proof",
                workspace=str(workspace),
            )
        )
    record = next(iter(service.registry._records.values()))
    assert record.status is SessionStatus.FAILED
    assert not (tmp_path / "authority").exists()
    await _stop(record)


@pytest.mark.asyncio
async def test_terminal_authority_rejects_replaced_session_directory(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_root = workspace / ".breadboard" / "sessions"
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(session_root))
    monkeypatch.setenv(
        "BREADBOARD_RUNTIME_RECORD_ROOT",
        str(workspace / ".breadboard" / "service_records"),
    )
    monkeypatch.setenv(
        "BREADBOARD_SESSION_AUTHORITY_ROOT",
        str(tmp_path / "authority"),
    )
    service = SessionService()

    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="directory identity proof",
            workspace=str(workspace),
        )
    )
    record = await service.ensure_session(response.session_id)
    original_root = tmp_path / "original-sessions"
    session_root.rename(original_root)
    session_root.mkdir()

    with pytest.raises(OSError, match="directory identity changed"):
        record.runner._commit_terminal_product_session_locked()
    with pytest.raises(FileNotFoundError):
        session_store.load_session(workspace, response.session_id)
    assert not any(session_root.iterdir())
    await _stop(record)


@pytest.mark.asyncio
async def test_default_profile_overrides_define_product_runtime_lock(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = SessionService()

    response = await service.create_session(
        SessionCreateRequest(
            workspace=str(workspace),
            overrides={"providers.default_model": "mock/overridden"},
        ),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)
    default_lock = harness_operations.resolve_default_profile().compilation.lock

    assert (
        record.runner.current_runtime_config()["providers"]["default_model"]
        == "mock/overridden"
    )
    assert (
        record.product_session.read_model.effective_lock_hash
        != default_lock["graph_hash"]
    )
    await service.stop_session(response.session_id)
    await _stop(record)

@pytest.mark.asyncio
async def test_default_profile_non_provider_override_preserves_model_roles(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = SessionService()

    response = await service.create_session(
        SessionCreateRequest(
            workspace=str(workspace),
            overrides={"completion.natural_finish.idle_turn_limit": 3},
        ),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)

    assert (
        record.runner.current_runtime_config()["completion"]["natural_finish"][
            "idle_turn_limit"
        ]
        == 3
    )
    assert record.metadata["active_model_role"] == "default"
    assert set(record.metadata["model_role_lock"]["roles"]) == {
        "default",
        "smol",
        "slow",
        "vision",
        "plan",
        "designer",
        "task",
    }
    await service.stop_session(response.session_id)
    await _stop(record)


def test_prewarm_sync_invokes_codex_runtime_module(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setattr(
        SERVICE + "runtime_codex_module.prewarm_codex_app_server",
        lambda **kwargs: calls.append(kwargs),
    )

    SessionService()._prewarm_request_runtime_sync(
        SessionCreateRequest(workspace=str(tmp_path)),
        {"model": "codex/gpt-5.4-mini"},
        {},
    )

    assert calls == [{"model": "gpt-5.4-mini", "cwd": str(tmp_path)}]


@pytest.mark.asyncio
async def test_default_profile_role_metadata_defines_product_runtime_lock(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    role_document = {
        "schema_version": "bb.model_roles.v1",
        "defaults": {
            "role": "default",
            "known_but_unbound_role": "error",
            "unknown_role": "error",
        },
        "roles": {
            "default": {
                "primary": {
                    "provider_id": "mock",
                    "model_id": "reference",
                },
                "fallbacks": [],
                "fallback_on": [],
            }
        },
        "dispatch": {"subagents": {}, "lanes": {"main": "default"}},
        "policy": {
            "allow_environment_overrides": False,
            "cross_provider_fallback": "forbidden",
            "account_failover": "forbidden",
        },
    }
    service = SessionService()

    response = await service.create_session(
        SessionCreateRequest(
            workspace=str(workspace),
            metadata={"bb.model_roles.v1": role_document},
        ),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)
    default_lock = harness_operations.resolve_default_profile().compilation.lock

    assert record.runner.current_runtime_config()["model_role_lock"]["lock_hash"]
    assert (
        record.product_session.read_model.effective_lock_hash
        != default_lock["graph_hash"]
    )
    await service.stop_session(response.session_id)
    await _stop(record)


@pytest.mark.asyncio
async def test_explicit_config_metadata_remains_custom(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(
        monkeypatch,
        tmp_path,
        metadata={"default_profile": {"profile_id": "forged"}},
    )
    assert record.metadata["config_path"] == str(Path(CONFIG).resolve())
    assert "default_profile" not in record.metadata
    await service.stop_session(response.session_id)
    await _stop(record)


@pytest.mark.asyncio
async def test_default_session_rejects_conflicting_supplied_lock(tmp_path) -> None:
    service = SessionService(state_root=tmp_path / "state")
    conflicting = EffectiveHarnessLock._from_record(
        {"graph_hash": "sha256:" + "f" * 64}
    )
    with pytest.raises(ValueError, match="conflicts"):
        await service.create_session(
            SessionCreateRequest(),
            effective_lock=conflicting,
            event_root=tmp_path / "events",
            runtime_root=tmp_path / "records",
        )
    assert service.registry._records == {}


@pytest.mark.asyncio
async def test_missing_or_corrupt_default_profile_fails_before_publication(
    monkeypatch, tmp_path
) -> None:
    profile = tmp_path / "package" / "agent_configs" / "templates" / "daily_driver.v1.yaml"
    monkeypatch.setattr(
        harness_operations, "daily_driver_template_path", lambda: profile
    )
    service = SessionService(state_root=tmp_path / "missing-state")
    with pytest.raises(DefaultProfileUnavailableError):
        await service.create_session(SessionCreateRequest())
    assert service.registry._records == {}
    assert not (tmp_path / "records").exists()
    assert not (tmp_path / "events").exists()

    profile.parent.mkdir(parents=True)
    profile.write_text("schema_version: bb.harness_definition.v1\n", encoding="utf-8")
    harness_operations.resolve_default_profile.cache_clear()
    service = SessionService(state_root=tmp_path / "corrupt-state")
    with pytest.raises(DefaultProfileInvalidError):
        await service.create_session(SessionCreateRequest())
    assert service.registry._records == {}
    assert not (tmp_path / "records").exists()
    assert not (tmp_path / "events").exists()


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (DefaultProfileUnavailableError, 503),
        (DefaultProfileInvalidError, 500),
    ],
)
def test_default_profile_route_errors_are_typed_and_secret_safe(
    monkeypatch, tmp_path, error, status_code
) -> None:
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", "1")
    service = SessionService(state_root=tmp_path / "state")

    async def fail(_request):
        raise error("internal profile path must not escape")

    monkeypatch.setattr(service, "create_session", fail)
    response = TestClient(create_app(service=service)).post(
        "/v1/sessions",
        json={},
    )
    assert response.status_code == status_code
    assert response.json() == {
        "error": error.error_code,
        "detail": error.hint,
        "path": None,
    }
    assert "internal profile path" not in response.text
@pytest.mark.asyncio
@pytest.mark.parametrize(("metadata", "task"), [({"cli_session_kind": "oneshot", "non_interactive_cli_session": True}, "Say hi"), ({"cli_session_kind": "interactive"}, "Say hi"), ({"cli_session_kind": "interactive"}, "")])
async def test_session_service_prewarms_supported_and_empty_sessions(monkeypatch, tmp_path, metadata, task) -> None:
    service, called = SessionService(), []; monkeypatch.setattr(service, "_prewarm_request_runtime_sync", lambda request, values, config: called.append((request.config_path, values["cli_session_kind"], config["providers"]["default_model"])))
    if not task: monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True); monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records"))
    service, response, record = await _create(monkeypatch, tmp_path, service=service, metadata=metadata, stream=True, task=task)
    assert called == [(str(Path(CONFIG).resolve()), metadata["cli_session_kind"], record.runner.current_runtime_config()["providers"]["default_model"])]
    if not task:
        title = "interactive session awaiting input"; stream = Path(record.metadata["runtime_record_dir"]) / "records" / "config_plane.jsonl"; work = [json.loads(line) for line in stream.read_text().splitlines() if '"name":"work_item_' in line]
        assert [item["name"] for item in work] == ["work_item_created", "work_item_lease_acquired", "work_item_attempt_started", "work_item_snapshot"] and [item["record"].get("kind") for item in work] == ["work_item.created", "lease.acquired", "attempt.started", None] and work[-1]["schema_version"] == "bb.work_item.v2"
        assert work[-1]["record"]["title"] == title and record.product_session.events[0].payload["task_hash"] == "sha256:" + hashlib.sha256(title.encode()).hexdigest() and record.runner.request.task == "" and record.runner._input_queue.empty()
    await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.STOPPED; await _stop(record)


@pytest.mark.asyncio
async def test_one_shot_completion_terminalizes_queued_turns(
    monkeypatch, tmp_path
) -> None:
    service, response, record = await _create(
        monkeypatch,
        tmp_path,
        metadata={
            "cli_session_kind": "oneshot",
            "non_interactive_cli_session": True,
        },
    )
    initial_turn = next(iter(record.turns_by_id.values()))
    accepted = []

    async def initialize_lifecycle() -> None:
        record.runner._input_queue.put_nowait(
            {
                "content": initial_turn.content,
                "attachments": [],
                "input_id": initial_turn.input_id,
                "turn_id": initial_turn.turn_id,
            }
        )
        accepted.append(
            await service.send_input(
                response.session_id,
                SessionInputRequest(content="second task"),
            )
        )

    async def initialize_agent() -> None:
        return None

    monkeypatch.setattr(
        record.runner._lifecycle_owner,
        "_initialize",
        initialize_lifecycle,
    )
    monkeypatch.setattr(record.runner, "_ensure_agent_initialized", initialize_agent)
    monkeypatch.setattr(
        record.runner._task_execution,
        "execute_task",
        lambda *_args, **_kwargs: {
            "completion_summary": {"completed": True, "reason": "completed"},
            "reward_metrics": {},
            "logging_dir": None,
        },
    )

    await record.runner._run()

    queued_turn = next(
        turn
        for turn in record.turns_by_id.values()
        if turn.turn_id != initial_turn.turn_id
    )
    assert accepted[0].disposition == "queued"
    assert initial_turn.terminal_outcome == "completed"
    assert queued_turn.terminal_outcome == "cancelled"
    assert record.terminal_event_envelopes[-1]["payload"]["reason"] == "superseded"
    assert record.product_session.read_model.status == "completed"
    assert record.status is SessionStatus.COMPLETED
    assert record.active_turn_id is None
    assert not record.queued_turn_ids
    assert record.runner._input_queue.empty()
    await _stop(record)


@pytest.mark.asyncio
async def test_incomplete_execution_fails_product_session(
    monkeypatch, tmp_path
) -> None:
    service, response, record = await _create(
        monkeypatch,
        tmp_path,
        metadata={
            "cli_session_kind": "oneshot",
            "non_interactive_cli_session": True,
        },
    )
    turn = next(iter(record.turns_by_id.values()))

    async def initialize_agent() -> None:
        return None

    monkeypatch.setattr(record.runner, "_ensure_agent_initialized", initialize_agent)
    monkeypatch.setattr(
        record.runner._task_execution,
        "execute_task",
        lambda *_args, **_kwargs: {
            "completion_summary": {
                "completed": False,
                "reason": "provider_error",
            },
            "reward_metrics": {},
            "logging_dir": None,
        },
    )

    await record.runner._run()

    assert record.product_session.read_model.status == "failed"
    assert record.status is SessionStatus.FAILED
    assert turn.state == "failed"
    assert turn.terminal_outcome == "failed"
    assert record.product_session.read_model.terminal_outcome == {
        "outcome": "failed",
        "error": "runtime_failure",
        "detail": "provider_error",
    }
    await _stop(record)


@pytest.mark.asyncio
async def test_failure_recovery_preserves_terminal_product_session(
    monkeypatch, tmp_path
) -> None:
    service, _, record = await _create(
        monkeypatch,
        tmp_path,
        metadata={
            "cli_session_kind": "oneshot",
            "non_interactive_cli_session": True,
        },
    )
    turn = next(iter(record.turns_by_id.values()))

    async def initialize_agent() -> None:
        return None

    real_finish_turn = record.runner._task_execution.finish_turn
    finish_outcomes = []

    async def fail_first_finish(turn_record, outcome, **kwargs):
        finish_outcomes.append(outcome)
        if len(finish_outcomes) == 1:
            raise OSError("terminal projection unavailable")
        return await real_finish_turn(turn_record, outcome, **kwargs)

    monkeypatch.setattr(record.runner, "_ensure_agent_initialized", initialize_agent)
    monkeypatch.setattr(
        record.runner._task_execution,
        "execute_task",
        lambda *_args, **_kwargs: {
            "completion_summary": {
                "completed": False,
                "reason": "provider_error",
            },
            "reward_metrics": {},
            "logging_dir": None,
        },
    )
    monkeypatch.setattr(
        record.runner._task_execution,
        "finish_turn",
        fail_first_finish,
    )

    await record.runner._run()

    assert finish_outcomes == ["failed", "failed"]
    assert record.product_session.read_model.status == "failed"
    assert record.product_session.read_model.terminal_outcome["detail"] == "provider_error"
    assert record.status is SessionStatus.FAILED
    assert turn.terminal_outcome == "failed"
    await _stop(record)


@pytest.mark.asyncio
async def test_recovered_admission_is_present_in_logical_session_journal(
    monkeypatch, tmp_path
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records"))
    initial_service = SessionService(state_root=state_root)
    _, response, initial = await _create(
        monkeypatch, tmp_path, service=initial_service, task=""
    )
    initial_scheduled = []
    initial_receipt = await initial_service.send_input(
        response.session_id,
        SessionInputRequest(
            content="Create and validate the deterministic bubble sort fixture.",
            client_message_id="ft01-initial-test",
        ),
        defer_execution=initial_scheduled.append,
    )
    await initial_scheduled[0]()
    await initial.runner._finish_turn(
        initial.turns_by_id[initial_receipt.turn_id], "completed"
    )
    await _stop(initial)

    recovered_service = SessionService(state_root=state_root)
    recovered = await recovered_service.ensure_session(response.session_id)
    scheduled = []
    receipt = await recovered_service.send_input(
        response.session_id,
        SessionInputRequest(
            content="Prove the recovered engine can execute the deterministic validation.",
            client_message_id="ft01-recovery-test",
        ),
        defer_execution=scheduled.append,
    )
    await scheduled[0]()
    try:
        durable_turns = list(recovered.turns_by_id)
        logical_events = [
            json.loads(line)
            for line in (
                tmp_path
                / "events"
                / response.session_id
                / "session_events.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        accepted_events = [
            event for event in logical_events if event["kind"] == "input.accepted"
        ]

        assert receipt.disposition == "started"
        assert receipt.turn_id in durable_turns
        assert len(scheduled) == 1
        assert len(accepted_events) == len(durable_turns) == 2
    finally:
        await _stop(recovered)


@pytest.mark.asyncio
async def test_recovery_reconciles_terminal_logical_journal_before_runner_start(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.runtime import Session as ProductSession
    from breadboard.product.runtime.events import JsonlEventSink
    from breadboard_engine.api.cli_bridge.registry import (
        SessionRecord,
        SessionRegistry,
        TurnRecord,
        submission_body_digest,
    )

    state_root = tmp_path / "state"
    event_root = tmp_path / "events"
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(event_root))
    registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id="terminal-before-registry-update",
        status=SessionStatus.RUNNING,
    )
    record.product_session = ProductSession.start(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "a" * 64}
        ),
        "retained terminal session",
        session_id=record.session_id,
        sink=JsonlEventSink(event_root / record.session_id / "session_events.jsonl"),
    )
    turn = TurnRecord(
        input_id="input-before-terminal-crash",
        turn_id="turn-before-terminal-crash",
        client_message_id="client-before-terminal-crash",
        content="work",
        attachments=(),
        original_disposition="started",
        state="active",
        body_digest=submission_body_digest("work", ()),
    )
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    record.product_session.input("work", [])
    await registry.create(record)
    record.product_session.complete()

    recovered_service = SessionService(state_root=state_root)
    recovered = await recovered_service.ensure_session(record.session_id)
    persisted = await SessionRegistry(state_root=state_root).get(record.session_id)

    assert recovered.status is SessionStatus.COMPLETED
    assert recovered.projected_status() is SessionStatus.COMPLETED
    assert recovered.runner is None
    assert recovered.loaded_from_retained_state is False
    assert persisted is not None
    assert persisted.status is SessionStatus.COMPLETED
    persisted_turn = persisted.turns_by_id[turn.turn_id]
    assert persisted_turn.terminal_outcome == "completed"
    assert persisted_turn.terminal_resolution_committed is True
    assert persisted.active_turn_id is None



def test_restore_wraps_invalid_event_transaction_as_typed_replay_error(tmp_path) -> None:
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    event_root = tmp_path / "events"
    session_id = "invalid-transaction"
    session_dir = event_root / session_id
    session_dir.mkdir(parents=True)
    (session_dir / ".session_events.jsonl.txn").write_text(
        "not-an-offset", encoding="ascii"
    )

    with pytest.raises(runtime_ports.ReplayError) as raised:
        _restore_product_session(session_id, event_root=event_root)

    assert raised.value.code == "invalid_event_record"



@pytest.mark.asyncio
async def test_restore_accepts_identity_stable_event_transaction_recovery(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    original = journal.read_bytes()
    journal.write_bytes(original + b'{"partial":')
    journal.with_name(".session_events.jsonl.txn").write_text(
        str(len(original)),
        encoding="ascii",
    )

    restored = _restore_product_session(response.session_id, event_root=event_root)

    assert restored.read_model.session_id == response.session_id
    assert journal.read_bytes() == original


@pytest.mark.asyncio
async def test_restore_recovery_never_mutates_a_swapped_outside_journal(
    monkeypatch, tmp_path
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX no-follow descriptor regression")
    import breadboard_engine.api.cli_bridge.service as service_module
    from breadboard.product.runtime import ReplayError

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    session_dir = event_root / response.session_id
    journal = session_dir / "session_events.jsonl"
    original = journal.read_bytes()

    outside = tmp_path / "outside-session"
    outside.mkdir()
    outside_journal = outside / "session_events.jsonl"
    outside_payload = original + b'{"outside-partial":'
    outside_journal.write_bytes(outside_payload)
    outside_transaction = outside / ".session_events.jsonl.txn"
    outside_transaction.write_text(str(len(original)), encoding="ascii")

    retained = event_root / f"{response.session_id}-retained"
    real_read = service_module._read_retained_event_journal
    reads = 0

    def swap_after_initial_read(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal reads
        result = real_read(*args, **kwargs)
        reads += 1
        if reads == 1:
            session_dir.rename(retained)
            session_dir.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(
        service_module,
        "_read_retained_event_journal",
        swap_after_initial_read,
    )

    with pytest.raises(ReplayError, match="unsafe logical event journal"):
        service_module._restore_product_session(
            response.session_id,
            event_root=event_root,
        )

    assert outside_journal.read_bytes() == outside_payload
    assert outside_transaction.is_file()


@pytest.mark.asyncio
async def test_restore_reads_under_the_existing_process_lock(
    monkeypatch, tmp_path
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX advisory-lock regression")
    import fcntl
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    lock_path = (
        event_root
        / response.session_id
        / ".session_events.jsonl.lock"
    )
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_process_lock() -> None:
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            lock_acquired.set()
            release_lock.wait()
        finally:
            os.close(descriptor)

    holder = threading.Thread(target=hold_process_lock)
    holder.start()
    assert lock_acquired.wait(timeout=2)
    restore_task = asyncio.create_task(
        asyncio.to_thread(
            _restore_product_session,
            response.session_id,
            event_root=event_root,
        )
    )
    await asyncio.sleep(0.05)
    assert not restore_task.done()
    release_lock.set()
    restored = await restore_task
    holder.join(timeout=2)
    assert restored.read_model.session_id == response.session_id


@pytest.mark.asyncio
async def test_retained_append_uses_standard_process_lock(
    monkeypatch, tmp_path
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX advisory-lock regression")
    import fcntl
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    restored = _restore_product_session(response.session_id, event_root=event_root)
    lock_path = (
        event_root
        / response.session_id
        / ".session_events.jsonl.lock"
    )
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_process_lock() -> None:
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            lock_acquired.set()
            release_lock.wait()
        finally:
            os.close(descriptor)

    holder = threading.Thread(target=hold_process_lock)
    holder.start()
    assert lock_acquired.wait(timeout=2)
    append_task = asyncio.create_task(asyncio.to_thread(restored.complete))
    await asyncio.sleep(0.05)
    assert not append_task.done()
    release_lock.set()
    await append_task
    holder.join(timeout=2)
    assert restored.read_model.status == "completed"


@pytest.mark.asyncio
async def test_retained_append_preserves_wal_when_rollback_fsync_fails(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge.service import _RetainedEventSink
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    restored = _restore_product_session(response.session_id, event_root=event_root)

    real_write_all = _RetainedEventSink._write_all
    writes = 0

    def fail_event_write(descriptor, payload):  # type: ignore[no-untyped-def]
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("event write failed")
        real_write_all(descriptor, payload)

    real_ftruncate = os.ftruncate
    rollback_started = False

    def mark_rollback(descriptor, length):  # type: ignore[no-untyped-def]
        nonlocal rollback_started
        rollback_started = True
        return real_ftruncate(descriptor, length)

    real_fsync = os.fsync

    def fail_rollback_sync(descriptor):  # type: ignore[no-untyped-def]
        if rollback_started:
            raise OSError("rollback sync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr(_RetainedEventSink, "_write_all", staticmethod(fail_event_write))
    monkeypatch.setattr(os, "ftruncate", mark_rollback)
    monkeypatch.setattr(os, "fsync", fail_rollback_sync)

    with pytest.raises(RuntimeError, match="event journal identity changed"):
        restored.complete()

    assert journal.with_name(".session_events.jsonl.txn").is_file()


def test_restore_does_not_misclassify_event_sink_infrastructure_failure(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    def fail_sink(_path):
        try:
            raise OSError("event storage unavailable")
        except OSError as cause:
            raise RuntimeError("event sink recovery failed") from cause

    monkeypatch.setattr(SERVICE + "JsonlEventSink", fail_sink)

    with pytest.raises(RuntimeError, match="event sink recovery failed"):
        _restore_product_session("infrastructure-failure", event_root=tmp_path)

@pytest.mark.asyncio
async def test_recovery_uses_retained_event_root_and_rebinds_durable_workspace(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    event_root = workspace / ".breadboard" / "sessions"
    state_root = tmp_path / "state"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)

    initial_service = SessionService(state_root=state_root)
    response = await initial_service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="finish before the registry status commit",
            workspace=str(workspace),
        ),
        session_id="retained-custom-root",
        event_root=event_root,
        runtime_root=workspace / ".breadboard" / "service_records",
    )
    initial = await initial_service.ensure_session(response.session_id)
    uploaded = await initial_service.upload_attachments(
        response.session_id,
        [_Upload()],
    )
    initial.product_session.complete()
    await _stop(initial)
    await initial_service.registry.persist(initial)
    monkeypatch.setenv(
        "BREADBOARD_SESSION_EVENT_ROOT",
        str(tmp_path / "unrelated-default-events"),
    )

    from breadboard_engine.api.cli_bridge.session_runner import SessionRunner

    recovered_service = SessionService(state_root=state_root)
    real_commit = SessionRunner._commit_terminal_product_session_locked
    commit_attempts = 0

    def fail_first_commit(runner) -> None:
        nonlocal commit_attempts
        commit_attempts += 1
        if commit_attempts == 1:
            raise OSError("durable product projection unavailable")
        real_commit(runner)

    monkeypatch.setattr(
        SessionRunner,
        "_commit_terminal_product_session_locked",
        fail_first_commit,
    )
    with pytest.raises(OSError, match="durable product projection unavailable"):
        await recovered_service.ensure_session(response.session_id)
    retained = await recovered_service.registry.get(response.session_id)
    assert retained is not None
    assert retained.status not in {
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.STOPPED,
    }
    assert retained.loaded_from_retained_state is True

    recovered = await recovered_service.ensure_session(response.session_id)

    assert recovered.projected_status() is SessionStatus.COMPLETED
    assert session_store.session_metadata_path(
        workspace, response.session_id
    ).is_file()
    artifact_rows = session_store.session_artifact_rows(
        workspace,
        response.session_id,
    )
    assert [row["name"] for row in artifact_rows] == [
        uploaded.attachments[0].id
    ]

    async def collect_replay() -> list:
        return [
            event
            async for event in recovered_service.event_stream(
                response.session_id,
                replay=True,
            )
        ]

    replay = await asyncio.wait_for(collect_replay(), timeout=1)
    assert replay
    assert getattr(recovered, "_dispatcher_complete", False) is True


@pytest.mark.asyncio
async def test_managed_session_persists_actual_journal_root(monkeypatch, tmp_path) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", "managed-root-test")
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(managed_root))
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)

    service = SessionService()
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="retain the actual managed journal root",
            workspace=str(workspace),
        ),
        session_id="managed-event-root",
        event_root=workspace / ".breadboard" / "sessions",
    )
    record = await service.ensure_session(response.session_id)
    await service.registry.persist(record)
    first_upload = await service.upload_attachments(
        response.session_id,
        [_Upload()],
    )

    expected_root = (managed_root / "session-events").resolve()
    assert record.metadata["session_event_root"] == str(expected_root)
    assert (
        expected_root / response.session_id / "session_events.jsonl"
    ).is_file()
    await _stop(record)

    recovered_service = SessionService()
    recovered = await recovered_service.ensure_session(response.session_id)
    second_file = _Upload()
    second_file.filename = "second-proof.txt"
    second_upload = await recovered_service.upload_attachments(
        response.session_id,
        [second_file],
    )

    assert recovered.metadata["session_event_root"] == str(expected_root)
    assert recovered.product_session.events[0].kind == "session.started"
    expected_attachment_ids = {
        first_upload.attachments[0].id,
        second_upload.attachments[0].id,
    }
    assert set(recovered.runner.artifacts.artifact_refs()) == expected_attachment_ids
    recovered.product_session.complete()
    recovered.runner._commit_terminal_product_session_locked()
    rows = session_store.session_artifact_rows(workspace, response.session_id)
    assert {row["name"] for row in rows} == expected_attachment_ids
    await _stop(recovered)


@pytest.mark.asyncio
async def test_recovery_denies_orphaned_approval_and_reopens_admission(
    monkeypatch, tmp_path
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records"))
    initial_service = SessionService(state_root=state_root)
    _, response, initial = await _create(
        monkeypatch,
        tmp_path,
        service=initial_service,
        task="request approval before a process crash",
    )
    initial.product_session.request_approval("approval-before-crash", "write")
    import hashlib
    from breadboard_engine.api.cli_bridge.registry import TurnRecord
    from breadboard_engine.api.cli_bridge.registry import submission_body_digest
    from breadboard_engine.api.cli_bridge.registry import identity_digest

    admitted_content = "must not consume the approval journal slot"
    retained_turn = TurnRecord(
        input_id="input-approval-slot",
        turn_id="turn-approval-slot",
        client_message_id="approval-slot",
        content=admitted_content,
        attachments=(),
        original_disposition="started",
        state="active",
        body_digest=submission_body_digest(admitted_content, ()),
        logical_event_count_before_admission=(
            initial.product_session.read_model.event_count
        ),
        logical_input_content_hash=(
            "sha256:" + hashlib.sha256(admitted_content.encode()).hexdigest()
        ),
        logical_input_session_status_before_admission="awaiting_approval",
    )
    initial.turns_by_id[retained_turn.turn_id] = retained_turn
    initial.submissions_by_key[retained_turn.client_message_id] = retained_turn
    initial.submissions_by_key_digest[
        identity_digest(retained_turn.client_message_id)
    ] = retained_turn
    initial.active_turn_id = retained_turn.turn_id
    initial.turn_admission = initial.turn_admission.__class__.ACTIVE
    await initial_service.registry.persist(initial)
    await _stop(initial)

    recovered_service = SessionService(state_root=state_root)
    recovered = await recovered_service.ensure_session(response.session_id)
    assert [
        event
        for event in recovered.product_session.events
        if event.kind == "input.accepted"
    ] == []
    scheduled = []
    receipt = await recovered_service.send_input(
        response.session_id,
        SessionInputRequest(
            content="continue after orphaned approval reconciliation",
            client_message_id="after-approval-recovery",
        ),
        defer_execution=scheduled.append,
    )

    assert recovered.product_session.read_model.status == "running"
    assert recovered.product_session.read_model.pending_approval is None
    assert {
        event.kind
        for event in recovered.product_session.events
    } >= {"approval.requested", "approval.resolved"}
    recovered_turn = recovered.turns_by_id[retained_turn.turn_id]
    assert recovered_turn.cancellation_requested is True
    assert recovered_turn.terminal_outcome == "cancelled"
    assert receipt.disposition == "started"
    assert len(scheduled) == 1
    await _stop(recovered)

@pytest.mark.asyncio
async def test_terminalization_closes_admission_before_async_resolution(
    monkeypatch, tmp_path
) -> None:
    service, response, record = await _create(monkeypatch, tmp_path)
    turn = next(iter(record.turns_by_id.values()))
    finish_started = asyncio.Event()
    finish_release = asyncio.Event()
    real_finish_turn = record.runner._task_execution.finish_turn

    async def blocking_finish(turn_record, outcome, **kwargs):
        finish_started.set()
        await finish_release.wait()
        return await real_finish_turn(turn_record, outcome, **kwargs)

    monkeypatch.setattr(
        record.runner._task_execution,
        "finish_turn",
        blocking_finish,
    )
    terminalizing = asyncio.create_task(
        record.runner._lifecycle_owner.terminalize_admitted_turns(
            outcome="failed",
            reason="worker_crash",
            error_code="worker_crash",
        )
    )
    await finish_started.wait()

    with pytest.raises(HTTPException) as rejected:
        await service.send_input(
            response.session_id,
            SessionInputRequest(content="racing task"),
        )

    assert rejected.value.status_code == 409
    assert rejected.value.detail == "session is closed"
    finish_release.set()
    await terminalizing

    assert set(record.turns_by_id) == {turn.turn_id}
    assert turn.terminal_outcome == "failed"
    assert record.active_turn_id is None
    assert not record.queued_turn_ids
    assert record.runner._input_queue.empty()
    await _stop(record)
@pytest.mark.asyncio
async def test_session_summary_projects_effective_model(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path)
    assert record.to_summary().model == record.runner.current_runtime_config()["providers"]["default_model"]
    await service.stop_session(response.session_id)
    await _stop(record)
@pytest.mark.asyncio
async def test_product_session_projects_bridge_status_and_exact_supplied_lock(monkeypatch, tmp_path) -> None:
    supplied_lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "b" * 64})
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    service = SessionService()
    response = await service.create_session(
        SessionCreateRequest(config_path=CONFIG, task="project product state"),
        event_root=tmp_path / "events",
        effective_lock=supplied_lock,
    )
    record = await service.ensure_session(response.session_id)
    assert response.status is SessionStatus.RUNNING
    assert record.status is SessionStatus.STARTING
    assert record.to_summary().status is SessionStatus.RUNNING
    assert record.product_session.read_model.effective_lock_hash == supplied_lock["graph_hash"]
    product_session = record.product_session
    record.product_session = SimpleNamespace(read_model=SimpleNamespace(status="unknown"))
    with pytest.raises(RuntimeError, match="unknown product Session status"):
        record.projected_status()
    record.product_session = product_session
    with pytest.raises(RuntimeError, match="disagrees with product Session"):
        await service.registry.update_status(response.session_id, SessionStatus.COMPLETED)
    await service.stop_session(response.session_id)
    assert record.status is SessionStatus.STOPPED
    await _stop(record)
@pytest.mark.asyncio
async def test_session_service_authorizes_runner_after_prewarm(monkeypatch, tmp_path) -> None:
    service, order = SessionService(), []
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: order.append("schedule"))
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: order.append("authorize"))
    monkeypatch.setattr(
        service,
        "_prewarm_request_runtime_sync",
        lambda _request, _metadata, _config: order.append("prewarm"),
    )
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "events"))

    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="Say hi",
            metadata={"cli_session_kind": "oneshot", "non_interactive_cli_session": True},
        )
    )

    assert order == ["schedule", "prewarm", "authorize"]
    await service.stop_session(response.session_id)
    await _stop(await service.ensure_session(response.session_id))

@pytest.mark.asyncio
async def test_effective_lock_is_exact_and_secret_free(monkeypatch, tmp_path) -> None:
    auth = SimpleNamespace(
        api_key="forbidden-key",
        base_url="https://secret.invalid",
        headers={"X-Secret": "forbidden-header"},
    )
    monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True)
    monkeypatch.setenv(
        "BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records")
    )
    workspace = str((tmp_path / "workspace").resolve()); service, response, record = await _create(monkeypatch, tmp_path, workspace=workspace, metadata={"model": "test-runtime-model"}, overrides={"provider_auth_runtime.openai.api_key": auth.api_key})
    config = record.runner.current_runtime_config(); original_lock = service._runtime_lock(response.session_id, config, CONFIG); graph = json.loads(Path(record.metadata["runtime_records"]["effective_config_graph"]).read_text(encoding="utf-8")); assert graph == original_lock.as_dict() and graph["graph_hash"] == record.product_session.read_model.effective_lock_hash
    config["nested"] = {"provider_auth_runtime": {"token": "nested-secret"}, "provider_auth_runtime.token": "dotted-secret", "safe": True}; lock = service._runtime_lock(response.session_id, config, CONFIG); values = {row["path"]: row["value"] for row in lock["effective_values"]}
    serialized = lock.canonical_json() + "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "records" / response.session_id).rglob("*") if path.is_file()); assert (values["workspace.root"], values["providers.default_model"]) == (workspace, "test-runtime-model"); assert all(secret not in serialized for secret in ("provider_auth_runtime", auth.api_key, auth.base_url, auth.headers["X-Secret"], "nested-secret", "dotted-secret")); assert _tool_names(load_agent_config(CONFIG)) == ["apply_patch", "shell_command", "update_plan"]; assert _tool_names({"tools": {"defs_dir": "implementations/tools/defs_oc", "enabled": {"list": True}}}) == ["list"]
    record.runner.transition_product_session("complete"); await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.COMPLETED; await _stop(record)
@pytest.mark.asyncio
async def test_input_and_approval_are_durable_before_delivery(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path); sink, record.product_session._sink = record.product_session._sink, _Failing()
    with pytest.raises(OSError, match="sink unavailable"): await service.send_input(response.session_id, SessionInputRequest(content="next"))
    assert record.runner._input_queue.empty(); assert [event.kind for event in record.product_session.events] == ["session.started"]
    record.product_session._sink = sink; record.runner._rehydrate_pending_permissions("permission_request", {"request_id": "perm-1", "category": "shell"}); record.runner._permission_queue = _Failing(); persisted = []
    monkeypatch.setattr(record.runner.permission_authority, "update_rule", lambda *_args, **_kwargs: persisted.append(True) or True); request = SessionCommandRequest(command="permission_decision", payload={"request_id": "perm-1", "decision": "always", "rule": "*.sh"})
    with pytest.raises(HTTPException) as error: await service.execute_command(response.session_id, request)
    assert error.value.status_code == 409; assert [event.kind for event in record.product_session.events][-2:] == ["approval.resolved", "session.failed"]; assert record.status.value == "failed"
    assert persisted == [True] and record.metadata["permission_rules"][0]["rule"] == "*.sh"
    await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.FAILED; await _stop(record)
@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "payload"), [("set_model", {"model": "openrouter/openai/gpt-5-nano"}), ("set_skills", {"allowlist": ["test-skill"]}), ("set_mode", {"mode": "plan"})])
async def test_failed_durable_reconfigure_rolls_back_runtime_mutation(monkeypatch, tmp_path, command, payload) -> None:
    service, response, record = await _create(monkeypatch, tmp_path, task=""); runner = record.runner
    calls = []; model_config = runner.current_runtime_config(); model_config["providers"].pop("default_model", None); model_config.pop("mode", None); runtime_config = model_config; runner._agent = SimpleNamespace(config=runtime_config) if command == "set_model" else SimpleNamespace(config=runtime_config, apply_runtime_overrides=lambda overrides: calls.append(overrides) or runtime_config.update(apply_dotted_overrides(runtime_config, overrides)) or True)
    before_config, before_metadata, before_model, before_mode = runner.current_runtime_config(), dict(record.metadata), runner._model_override, runner._mode; sink, record.product_session._sink = record.product_session._sink, _Failing()
    with pytest.raises(OSError, match="sink unavailable"): await service.execute_command(response.session_id, SessionCommandRequest(command=command, payload=payload))
    assert runner.current_runtime_config() == before_config; assert record.metadata == before_metadata; assert (runner._model_override, runner._mode) == (before_model, before_mode)
    assert [event.kind for event in record.product_session.events] == ["session.started"]; assert "default_model" not in runner._agent.config["providers"] if command == "set_model" else len(calls) == 2
    if command == "set_mode":
        record.product_session._sink = sink; await service.execute_command(response.session_id, SessionCommandRequest(command=command, payload=payload)); assert (record.product_session.events[-1].kind, runner.current_runtime_config()["mode"], record.metadata["mode"]) == ("session.reconfigured", "plan", "plan")
    await _stop(record)
@pytest.mark.asyncio
async def test_generation_adoption_rejects_non_quiescent_session_and_rolls_back(
    monkeypatch, tmp_path
) -> None:
    service, response, record = await _create(monkeypatch, tmp_path)
    before = (
        record.runner.current_runtime_config(),
        dict(record.metadata),
        record.product_session.events,
    )

    with pytest.raises(HTTPException) as captured:
        await service.execute_command(
            response.session_id,
            SessionCommandRequest(command="set_mode", payload={"mode": "plan"}),
        )

    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == "non_quiescent"
    assert (
        record.runner.current_runtime_config(),
        record.metadata,
        record.product_session.events,
    ) == before
    await _stop(record)


@pytest.mark.asyncio
async def test_stop_command_closes_admission_when_cancel_append_fails(
    monkeypatch, tmp_path
) -> None:
    service, response, record = await _create(monkeypatch, tmp_path, task="")
    record.product_session._sink = _Failing()

    with pytest.raises(OSError, match="sink unavailable"):
        await service.execute_command(
            response.session_id,
            SessionCommandRequest(command="stop"),
        )
    assert record.admission_closed is True
    with pytest.raises(HTTPException) as late:
        await service.send_input(
            response.session_id,
            SessionInputRequest(content="late admission"),
        )
    assert late.value.status_code == 409
    assert late.value.detail == "session admission is closed"
    record.product_session._sink = runtime_ports.NullEventSink()
    await _stop(record)


@pytest.mark.asyncio
async def test_stop_closes_session_admission_before_teardown(
    monkeypatch, tmp_path
) -> None:
    service, response, record = await _create(monkeypatch, tmp_path, task="")
    entered = asyncio.Event()
    release = asyncio.Event()
    terminalize = record.runner._terminalize_admitted_turns

    async def blocked_terminalize(**kwargs):
        entered.set()
        await release.wait()
        await terminalize(**kwargs)

    monkeypatch.setattr(
        record.runner, "_terminalize_admitted_turns", blocked_terminalize
    )
    stopping = asyncio.create_task(record.runner.stop())
    await entered.wait()

    with pytest.raises(HTTPException) as captured:
        await service.send_input(
            response.session_id,
            SessionInputRequest(content="late admission"),
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == "session admission is closed"
    before = record.product_session.events
    with pytest.raises(HTTPException) as reconfigure:
        await service.execute_command(
            response.session_id,
            SessionCommandRequest(command="set_mode", payload={"mode": "plan"}),
        )
    assert reconfigure.value.status_code == 409
    assert reconfigure.value.detail["code"] == "admission_closed"
    assert record.product_session.events == before
    release.set()
    await stopping
    assert record.status is SessionStatus.STOPPED
    await _stop(record)
@pytest.mark.asyncio
async def test_runtime_failure_does_not_advance_registry_past_failed_sink(monkeypatch, tmp_path) -> None:
    service, _, record = await _create(monkeypatch, tmp_path); updates, original_update = [], service.registry.update_status
    async def update_status(session_id, status): updates.append(status); await original_update(session_id, status)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service.registry, "update_status", update_status); monkeypatch.setattr(record.runner, "prepare_runtime_config", lambda: (_ for _ in ()).throw(RuntimeError("runtime failed")))
    record.product_session._sink = _Failing()
    with pytest.raises(OSError, match="sink unavailable"): await record.runner._run()
    assert SessionStatus.FAILED not in updates; assert record.status is not SessionStatus.FAILED; assert record.product_session.read_model.status == "running"; await _stop(record)
@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["set_model", "set_skills"])
async def test_runtime_reconfigure_failure_never_claims_effective_config(monkeypatch, tmp_path, command) -> None:
    RejectingModelConfig = type("RejectingModelConfig", (dict,), {"setdefault": lambda self, *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model propagation failed"))})
    if command == "set_model": agent = SimpleNamespace(config=RejectingModelConfig()); payload = {"model": "openrouter/openai/gpt-5-nano"}
    else: agent = SimpleNamespace(config={}, apply_runtime_overrides=lambda _overrides: False); payload = {"allowlist": ["test-skill"]}
    service, response, record = await _create(monkeypatch, tmp_path, task=""); record.runner._agent = agent
    with pytest.raises(HTTPException) as error: await service.execute_command(response.session_id, SessionCommandRequest(command=command, payload=payload))
    assert error.value.status_code == 409; assert [event.kind for event in record.product_session.events][-2:] == ["session.started", "session.failed"]; assert record.status is SessionStatus.FAILED
    await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.FAILED and record.product_session.events[-1].kind == "session.failed"; await _stop(record)
@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["pause", "resume"])
async def test_control_delivery_failure_terminalizes_and_stops_runner(monkeypatch, tmp_path, command) -> None:
    service, response, record = await _create(monkeypatch, tmp_path); runner = record.runner
    if command == "resume": await runner.handle_command("pause", {})
    monkeypatch.setattr(runner, "_signal_control", lambda _kind: (_ for _ in ()).throw(RuntimeError("control unavailable")))
    with pytest.raises(HTTPException) as error: await service.execute_command(response.session_id, SessionCommandRequest(command=command))
    assert error.value.status_code == 409 and record.status is SessionStatus.FAILED and record.product_session.read_model.status == "failed" and record.product_session.read_model.terminal_outcome["error"] == f"{command}_control_failed" and runner._stop_event.is_set(); await _stop(record)
@pytest.mark.asyncio
async def test_pause_append_failure_restores_running_gate(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path); runner, sink = record.runner, record.product_session._sink; original = sink.append; monkeypatch.setattr(sink, "append", lambda _event: (_ for _ in ()).throw(OSError("append unavailable")))
    with pytest.raises(OSError, match="append unavailable"): await runner.handle_command("pause", {})
    assert record.product_session.read_model.status == "running" and runner._resume_event.is_set() and not runner._stop_event.is_set(); monkeypatch.setattr(sink, "append", original); await service.delete_session(response.session_id)
@pytest.mark.asyncio
async def test_setup_failure_terminalizes_registered_session(monkeypatch, tmp_path) -> None:
    def fail(_runner) -> None: raise RuntimeError("runner setup exploded")  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER + "authorize_start", fail); monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "setup-failure"); monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records")); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "events")); service = SessionService()
    with pytest.raises(RuntimeError, match="runner setup exploded"): await service.create_session(SessionCreateRequest(config_path=CONFIG, task="task"))
    record = await service.ensure_session("setup-failure"); assert (record.status.value, record.product_session.events[-1].kind) == ("failed", "session.failed"); assert record.product_session.read_model.terminal_outcome["error"] == "session_setup_failed"; assert record.runner._stop_event.is_set() and record.dispatcher_task.done()
@pytest.mark.asyncio
async def test_scheduling_failure_publishes_no_start_authority(monkeypatch, tmp_path) -> None:
    records_root, events_root = tmp_path / "records", tmp_path / "events"
    def fail(_runner) -> None: raise RuntimeError("runner scheduling exploded")  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER + "schedule_start", fail); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True); monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "schedule-failure")
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root)); service = SessionService()
    with pytest.raises(RuntimeError, match="runner scheduling exploded"): await service.create_session(SessionCreateRequest(config_path=CONFIG, task="task"))
    assert await service.registry.get("schedule-failure") is None and all(not root.exists() or not any(root.iterdir()) for root in (records_root, events_root))
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_initial_durable_start_failure_has_no_published_lifecycle(monkeypatch, tmp_path, failure) -> None:
    records_root, events_root, started = tmp_path / "records", tmp_path / "events", []; entered, released = threading.Event(), threading.Event()
    def start(runner) -> None: started.append(runner)  # type: ignore[no-untyped-def]
    def emit(*, session_id, request, output_root, **_): path = output_root / session_id / "start.json"; path.parent.mkdir(parents=True); path.write_text("emitted", encoding="utf-8"); entered.set(); assert released.wait(2); return {"start": str(path)}  # type: ignore[no-untyped-def]
    def fail_sync(_stream) -> None: raise failure("initial append failed")  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER + "schedule_start", start); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True); monkeypatch.setattr(SERVICE + "emit_session_start_records", emit); monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "durable-start-failure"); monkeypatch.setattr(runtime_ports, "_sync", fail_sync); monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root)); service = SessionService(); request = SessionCreateRequest(config_path=CONFIG, task="task"); real_write, owners = Path.write_text, [0]
    def owner_write(path, *args, **kwargs): return (_ for _ in ()).throw(OSError("owner write failed")) if path.name == ".start.owner" and (owners.__setitem__(0, owners[0] + 1) or owners[0] == 2) else real_write(path, *args, **kwargs)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(Path, "write_text", owner_write)
    with pytest.raises(OSError, match="owner write failed"): await service.create_session(request)
    assert all(not root.exists() or not any(root.iterdir()) for root in (records_root, events_root)); monkeypatch.setattr(Path, "write_text", real_write)
    pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(service.create_session(request))))
    assert await asyncio.to_thread(entered.wait, 2); assert await service.registry.get("durable-start-failure") is None; assert not (records_root / "durable-start-failure").exists() and not (events_root / "durable-start-failure").exists(); released.set()
    with pytest.raises(failure, match="initial append failed"): await pending
    assert await service.registry.get("durable-start-failure") is None and started == []; assert not any(records_root.iterdir()) and not any(events_root.iterdir())
@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["records", "events", "commit", "authority"])
@pytest.mark.parametrize("shared_root", [False, True])
@pytest.mark.parametrize("primitives", [False, True])
async def test_start_publication_boundaries_are_invisible_and_retryable(monkeypatch, tmp_path, boundary, shared_root, primitives) -> None:
    records_root = tmp_path / "records"; events_root = records_root if shared_root else tmp_path / "events"; entered, released = threading.Event(), threading.Event(); order = []
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: order.append("schedule")); monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: (order[-1] == "authority") or (_ for _ in ()).throw(AssertionError(order)))
    monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "publication-failure"); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: primitives)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root)); service, armed = SessionService(), True
    def failpoint(name) -> None:  # type: ignore[no-untyped-def]
        nonlocal armed
        if name == "records": assert order[-1] == "schedule"
        order.append(name)
        if armed and name == boundary: entered.set(); assert released.wait(2); armed = False; raise OSError(f"{name} publication failed")
    monkeypatch.setattr(service, "_publication_boundary", failpoint); request = SessionCreateRequest(config_path=CONFIG, task="task")
    pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(service.create_session(request)))); assert await asyncio.to_thread(entered.wait, 2); session_id = "publication-failure"; authority = (records_root if primitives else events_root) / session_id; assert session_id not in service.registry._records
    active_paths = [path for path in (records_root / f".{session_id}.records.starting", events_root / f".{session_id}.events.starting", authority) if path.exists()]; SessionService(); assert active_paths and all(path.exists() for path in active_paths)
    if boundary == "authority":
        hidden_event = events_root / f".{session_id}.events.starting" / "session_events.jsonl"; assert (authority / ".start.committed").is_file() and ((events_root / session_id / "session_events.jsonl").is_file() or hidden_event.is_file())
    else: assert not (records_root / session_id).exists() and not (events_root / session_id).exists() and not (records_root / session_id / "records" / "config_plane.jsonl").exists()
    released.set()
    with pytest.raises(OSError, match=f"{boundary} publication failed"): await pending
    if boundary == "authority":
        SessionService(); event_log = (events_root / session_id / "session_events.jsonl"); assert (authority / ".start.committed").is_file() and event_log.is_file() and "session.failed" in event_log.read_text(); return
    assert all(not root.exists() or not any(root.iterdir()) for root in {records_root, events_root}); response = await service.create_session(request); record = await service.ensure_session(response.session_id); authority = (records_root if primitives else events_root) / response.session_id; assert (authority / ".start.committed").is_file() and (events_root / response.session_id / "session_events.jsonl").is_file() and (primitives or shared_root or not records_root.exists()); await _stop(record)
@pytest.mark.parametrize("shared_root", [False, True])
def test_startup_removes_incomplete_and_recovers_committed_projection(monkeypatch, tmp_path, shared_root) -> None:
    records_root = tmp_path / "records"; events_root = records_root if shared_root else tmp_path / "events"; monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root))
    for path in (records_root / "incomplete", events_root / "incomplete", records_root / ".staged.records.starting", events_root / "staged", records_root / "..crash.records.starting.dead.start-owner", events_root / "..crash.events.starting.dead.start-owner", records_root / ".committed.records.starting", records_root / "committed", events_root / "committed", events_root / ".other.events.starting", records_root / "recoverable", events_root / ".recoverable.events.starting"): path.mkdir(parents=True, exist_ok=True)
    (records_root / "incomplete" / ".start.pending").write_text("incomplete\n"); (records_root / "committed" / ".start.pending").write_text("committed\n"); (records_root / "committed" / ".start.committed").write_text("committed\n"); (events_root / "committed" / "session_events.jsonl").write_text("{}\n"); (records_root / "recoverable" / ".start.committed").write_text("recoverable\n"); (events_root / ".recoverable.events.starting" / "session_events.jsonl").write_text('{"kind":"session.started"}\n'); SessionService()
    assert {path.name for path in records_root.iterdir()} == {"committed", "recoverable"} and {path.name for path in events_root.iterdir()} == {"committed", "recoverable"} and (records_root / "committed" / ".start.committed").is_file() and (events_root / "recoverable" / "session_events.jsonl").is_file()
@pytest.mark.asyncio
async def test_attachment_manifest_survives_delete_and_unknown_ids_are_rejected(monkeypatch, tmp_path) -> None:
    Upload = _Upload
    workspace = tmp_path / "workspace"; service, response, record = await _create(monkeypatch, tmp_path, workspace=str(workspace))
    upload = Upload(); upload.filename = "résumé.txt"; uploaded = await service.upload_attachments(response.session_id, [upload]); attachment_id = uploaded.attachments[0].id
    digest = record.metadata["artifact_manifest_ref"]["digest"].removeprefix("sha256:"); manifest_path = workspace / ".breadboard" / "artifacts" / "manifests" / f"{response.session_id}.{digest}.json"; manifest = json.loads(manifest_path.read_text()); assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == digest; empty = Upload(); empty.data = b""; attachment_root = workspace / ".breadboard" / "attachments"; before = (manifest_path.read_bytes(), dict(record.runner.artifacts.artifact_refs()), dict(record.metadata), {path.name for path in attachment_root.iterdir()})
    attachment_path = next((attachment_root / attachment_id).iterdir()); attachment_path.write_bytes(b"tampered")
    helper = record.runner._format_attachment_helper([attachment_id, attachment_id]); attachment_path.write_bytes(b"raced"); uri = f"attachment://{record.runner.artifacts.artifact_refs()[attachment_id].digest}"
    conductor_class = OpenAIConductor.__ray_metadata__.modified_class; conductor = object.__new__(conductor_class); conductor.config, conductor.workspace, conductor.read_file, conductor._ray_get = {}, str(workspace), lambda _path: {"content": "bypass"}, lambda value: value; conductor._active_session_state = SimpleNamespace(get_provider_metadata=lambda key, default=None: record.runner._active_attachment_capabilities if key == "attachment_capabilities" else default); conductor.sandbox = SimpleNamespace(grep=SimpleNamespace(remote=lambda *args: {"matches": [{"path": str(attachment_path.relative_to(workspace)), "text": "secret"}, {"path": "README.md", "text": "public"}]} if args[3] == 0 else (_ for _ in ()).throw(AssertionError("grep limit applied before privacy filter"))), glob=SimpleNamespace(remote=lambda *args: [str(attachment_path.relative_to(workspace)), "README.md"] if args[2] is None else (_ for _ in ()).throw(AssertionError("glob limit applied before privacy filter"))), ls=SimpleNamespace(remote=lambda *_: {"items": [{"path": str(attachment_path.relative_to(workspace))}, {"path": "README.md"}]}))
    read_result = conductor._exec_raw({"function": "read_file", "arguments": {"path": uri}}); denied_result = conductor._exec_raw({"function": "read_file", "arguments": {"path": "attachment://sha256:" + "0" * 64}}); digest_path = record.runner.artifacts.artifact_refs()[attachment_id].digest[7:]; direct_result = conductor._exec_raw({"function": "read_file", "arguments": {"path": str(workspace / ".breadboard" / "artifacts" / "sha256" / digest_path[:2] / digest_path)}}); legacy_result = conductor._exec_raw({"function": "read_file", "arguments": {"path": str(attachment_path)}}); grep_result = conductor._exec_raw({"function": "grep", "arguments": {"pattern": ".*", "path": "."}}); glob_result = conductor._exec_raw({"function": "glob", "arguments": {"pattern": "**/*", "path": "."}}); list_result = conductor._exec_raw({"function": "list_dir", "arguments": {"path": ".", "depth": 5}}); shell_result = {"stdout": "preserved"}; blob_result = conductor._exec_raw({"function": "blob.put_file_slice", "arguments": {"path": str(attachment_path)}})
    assert uri in helper and "content=" not in helper and read_result["content"] == "proof" and "not authorized" in denied_result["error"] and all("model tools" in result["error"] or "attachment URI" in result["error"] for result in (direct_result, legacy_result, blob_result)) and [row["path"] for row in grep_result["matches"]] == glob_result == [row["path"] for row in list_result["items"]] == ["README.md"] and shell_result["stdout"] == "preserved" and attachment_path.read_bytes() == b"raced" and helper.count("Attachment ") == 1
    cas_path = workspace / ".breadboard" / "artifacts" / "sha256" / digest_path[:2] / digest_path; edit_result = conductor._exec_raw({"function": "apply_search_replace", "arguments": {"file_name": str(cas_path), "search": "proof", "replace": "corrupt"}}); patch_result = conductor._exec_raw({"function": "apply_unified_patch", "arguments": {"patch": f"*** Begin Patch\n*** Update File: {cas_path}\n@@\n-proof\n+corrupt\n*** End Patch\n"}})
    assert all("private workspace storage" in result["error"] for result in (edit_result, patch_result)) and conductor._exec_raw({"function": "read_file", "arguments": {"path": uri}})["content"] == "proof" and cas_path.read_bytes() == b"proof"
    empty_error, missing_error = await asyncio.gather(service.upload_attachments(response.session_id, [empty]), service.send_input(response.session_id, SessionInputRequest(content="use it", attachments=["missing"])), return_exceptions=True)
    assert isinstance(empty_error, HTTPException) and empty_error.status_code == 400 and isinstance(missing_error, HTTPException) and missing_error.status_code == 400 and record.runner._input_queue.empty(); assert before == (manifest_path.read_bytes(), record.runner.artifacts.artifact_refs(), record.metadata, {path.name for path in attachment_root.iterdir()}) and manifest["schema_version"] == "bb.artifact_manifest.v1" and manifest["artifacts"][0]["name"] == attachment_id
    cas_root = workspace / ".breadboard" / "artifacts" / "sha256"; cas_before = {path.relative_to(cas_root): path.read_bytes() for path in cas_root.rglob("*") if path.is_file()}
    real_put, calls = ArtifactStore.put, []; monkeypatch.setattr(ArtifactStore, "put", lambda store, *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")) if (calls.append(1) or len(calls) == 2) else real_put(store, *args, **kwargs))
    with pytest.raises(OSError, match="write failed"): await service.upload_attachments(response.session_id, [Upload(), Upload()])
    assert before == (manifest_path.read_bytes(), record.runner.artifacts.artifact_refs(), record.metadata, {path.name for path in attachment_root.iterdir()})
    assert cas_before == {path.relative_to(cas_root): path.read_bytes() for path in cas_root.rglob("*") if path.is_file()}
    if os.name != "nt":
        outside, attachment_dir = tmp_path / "outside-helper", attachment_path.parent; outside.mkdir(); attachment_path.unlink(); attachment_dir.rmdir(); attachment_dir.symlink_to(outside, target_is_directory=True)
        assert uri in record.runner._format_attachment_helper([attachment_id]) and not list(outside.iterdir())
    monkeypatch.setattr(ArtifactStore, "put", real_put); entered, release = asyncio.Event(), asyncio.Event()
    class BlockingUpload(Upload):
        async def read(self, size: int = -1) -> bytes: entered.set(); await release.wait(); return await super().read(size)
    upload_task = asyncio.create_task(service.upload_attachments(response.session_id, [BlockingUpload()])); await entered.wait()
    delete_task = asyncio.create_task(service.delete_session(response.session_id)); await asyncio.sleep(0); assert not delete_task.done()
    release.set(); raced_upload = await upload_task; await delete_task
    assert raced_upload.attachments and await service.registry.get(response.session_id) is None and not manifest_path.exists() and record.dispatcher_task.done()
    with pytest.raises(HTTPException) as missing: await service.ensure_session(response.session_id)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_successive_uploads_rotate_manifest_history_before_recovery_cap(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.session_artifacts._MAX_ARTIFACT_MANIFESTS",
        2,
    )
    workspace = tmp_path / "workspace"
    service, response, record = await _create(
        monkeypatch, tmp_path, workspace=str(workspace)
    )
    first = _Upload()
    first.filename = "first.txt"
    second = _Upload()
    second.filename = "second.txt"

    await service.upload_attachments(response.session_id, [first])
    await service.upload_attachments(response.session_id, [second])

    manifests = list(
        (workspace / ".breadboard" / "artifacts" / "manifests").iterdir()
    )
    assert len(manifests) == 1
    restored = SessionArtifactStore(
        session_id=response.session_id,
        metadata=dict(record.metadata),
    )
    restored.restore_manifest(workspace)
    assert len(restored.artifact_refs()) == 2
    await service.delete_session(response.session_id)


@pytest.mark.asyncio
async def test_upload_rejects_excess_manifest_history_before_commit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.session_artifacts._MAX_ARTIFACT_MANIFESTS",
        2,
    )
    session_id = "bounded-upload"
    manifest_root = tmp_path / ".breadboard" / "artifacts" / "manifests"
    manifest_root.mkdir(parents=True)
    for index in range(3):
        (manifest_root / f"{session_id}.{index:064x}.json").write_text("{}")
    owner = SessionArtifactStore(session_id=session_id, metadata={})

    with pytest.raises(HTTPException) as rejected:
        await owner.upload([_Upload()], workspace_dir=tmp_path)

    assert rejected.value.status_code == 409
    assert len(list(manifest_root.iterdir())) == 3
    assert owner.artifact_refs() == {}


@pytest.mark.asyncio
async def test_manifest_prune_failure_rejects_before_recovery_cap(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.session_artifacts._MAX_ARTIFACT_MANIFESTS",
        2,
    )
    workspace = tmp_path / "workspace"
    service, response, record = await _create(
        monkeypatch, tmp_path, workspace=str(workspace)
    )

    def fail_prune(*_args, **_kwargs) -> None:
        raise HTTPException(status_code=400, detail="workspace changed")

    monkeypatch.setattr(record.runner.artifacts, "_discard_manifest_names", fail_prune)
    await service.upload_attachments(response.session_id, [_Upload()])
    await service.upload_attachments(response.session_id, [_Upload()])
    before = (
        dict(record.metadata),
        dict(record.runner.artifacts.artifact_refs()),
    )

    with pytest.raises(HTTPException) as rejected:
        await service.upload_attachments(response.session_id, [_Upload()])

    assert rejected.value.status_code == 409
    assert before == (
        record.metadata,
        record.runner.artifacts.artifact_refs(),
    )
    await service.delete_session(response.session_id)


@pytest.mark.asyncio
async def test_shared_workspace_uploads_serialize_before_persistence(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "shared-workspace"
    service, first_response, first_record = await _create(
        monkeypatch,
        tmp_path / "first",
        workspace=str(workspace),
    )
    _, second_response, second_record = await _create(
        monkeypatch,
        tmp_path / "second",
        service=service,
        workspace=str(workspace),
    )
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_upload(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        first_entered.set()
        await release_first.wait()
        return SimpleNamespace(attachments=[])

    async def second_upload(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        second_entered.set()
        return SimpleNamespace(attachments=[])

    monkeypatch.setattr(first_record.runner.artifacts, "upload", first_upload)
    monkeypatch.setattr(second_record.runner.artifacts, "upload", second_upload)

    first_task = asyncio.create_task(
        service.upload_attachments(first_response.session_id, [_Upload()])
    )
    await first_entered.wait()
    second_task = asyncio.create_task(
        service.upload_attachments(second_response.session_id, [_Upload()])
    )
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered.is_set()
    await service.delete_session(first_response.session_id)
    await service.delete_session(second_response.session_id)


@pytest.mark.asyncio
async def test_image_attachment_becomes_first_class_model_input(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    service, response, record = await _create(
        monkeypatch, tmp_path, workspace=str(workspace)
    )
    upload = _Upload()
    upload.filename = "pixel.png"
    upload.content_type = "image/png"
    upload.data = b"\x89PNG\r\n\x1a\nmodel-input"
    attachment_id = (
        await service.upload_attachments(response.session_id, [upload])
    ).attachments[0].id

    helper = record.runner._format_attachment_helper([attachment_id])
    artifact = record.runner.artifacts.artifact_refs()[attachment_id]
    assert "read with read_file" in helper
    assert record.runner._active_input_media == [
        {
            "type": "media",
            "kind": "image",
            "uri": f"attachment://{artifact.digest}",
            "mime": "image/png",
        }
    ]

    await service.stop_session(response.session_id)
    await _stop(record)

@pytest.mark.asyncio
async def test_terminal_recovery_accepts_identical_projection_and_rejects_divergence(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime import Session as ProductSession

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    event_root = workspace / ".breadboard" / "sessions"
    state_root = tmp_path / "state"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    initial_service = SessionService(state_root=state_root)
    response = await initial_service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="publish the terminal projection",
            workspace=str(workspace),
        ),
        session_id="idempotent-terminal-publication",
        event_root=event_root,
    )
    initial = await initial_service.ensure_session(response.session_id)
    initial.product_session.complete()
    initial.runner._commit_terminal_product_session_locked()
    await initial_service.registry.persist(initial)

    recovered_service = SessionService(state_root=state_root)
    recovered = await recovered_service.ensure_session(response.session_id)

    assert recovered.status is SessionStatus.COMPLETED
    durable, _ = session_store.load_session(workspace, response.session_id)
    assert durable.read_model.status == "completed"
    divergent = ProductSession.start(
        EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
        "different task",
        session_id=response.session_id,
    )
    divergent.complete("different summary")
    with pytest.raises(ValueError, match="durable session projection diverges"):
        session_store.create_session(
            workspace,
            divergent,
            allow_existing=True,
        )


@pytest.mark.asyncio
async def test_legacy_retained_record_derives_workspace_event_root(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    event_root = workspace / ".breadboard" / "sessions"
    state_root = tmp_path / "state"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    initial_service = SessionService(state_root=state_root)
    response = await initial_service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="legacy workspace journal",
            workspace=str(workspace),
        ),
        session_id="legacy-workspace-root",
        event_root=event_root,
    )
    initial = await initial_service.ensure_session(response.session_id)
    initial.metadata.pop("session_event_root", None)
    await initial_service.registry.persist(initial)
    await _stop(initial)
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "wrong-root"))

    recovered = await SessionService(state_root=state_root).ensure_session(
        response.session_id
    )

    assert recovered.product_session.read_model.status == "running"
    assert recovered.loaded_from_retained_state is False
    await _stop(recovered)


@pytest.mark.asyncio
async def test_recovered_attachment_restores_descriptor_and_media(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    event_root = workspace / ".breadboard" / "sessions"
    state_root = tmp_path / "state"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    initial_service = SessionService(state_root=state_root)
    response = await initial_service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="retain attachment descriptor",
            workspace=str(workspace),
        ),
        session_id="retained-attachment-descriptor",
        event_root=event_root,
    )
    initial = await initial_service.ensure_session(response.session_id)
    upload = _Upload()
    upload.filename = "retained.png"
    upload.content_type = "image/png"
    upload.data = b"\x89PNG\r\n\x1a\nretained"
    uploaded = await initial_service.upload_attachments(
        response.session_id,
        [upload],
    )
    await initial_service.registry.persist(initial)
    await _stop(initial)

    recovered = await SessionService(state_root=state_root).ensure_session(
        response.session_id
    )
    attachment_id = uploaded.attachments[0].id
    helper = recovered.runner._format_attachment_helper([attachment_id])

    assert "retained.png" in helper
    assert recovered.runner._active_input_media == [
        {
            "type": "media",
            "kind": "image",
            "uri": (
                f"attachment://"
                f"{recovered.runner.artifacts.artifact_refs()[attachment_id].digest}"
            ),
            "mime": "image/png",
        }
    ]


@pytest.mark.asyncio
async def test_upload_rolls_back_when_registry_persistence_fails(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    event_root = tmp_path / "events"
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(event_root))
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    service = SessionService(state_root=state_root)
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="rollback failed upload",
            workspace=str(workspace),
        ),
        session_id="rollback-upload",
        event_root=event_root,
    )
    record = await service.ensure_session(response.session_id)
    metadata_before = dict(record.metadata)
    real_persist = service.registry.persist

    async def fail_persist(_record) -> None:
        raise OSError("registry persistence unavailable")

    monkeypatch.setattr(service.registry, "persist", fail_persist)
    with pytest.raises(OSError, match="registry persistence unavailable"):
        await service.upload_attachments(response.session_id, [_Upload()])

    attachments_root = workspace / ".breadboard" / "attachments"
    manifests_root = workspace / ".breadboard" / "artifacts" / "manifests"
    assert record.metadata == metadata_before
    assert dict(record.runner.artifacts.artifact_refs()) == {}
    assert not list(attachments_root.iterdir()) if attachments_root.exists() else True
    assert not list(manifests_root.iterdir()) if manifests_root.exists() else True
    restored = await service.registry.get(response.session_id)
    assert restored is not None
    assert "artifact_manifest_ref" not in restored.metadata
    monkeypatch.setattr(service.registry, "persist", real_persist)

    retry = await service.upload_attachments(response.session_id, [_Upload()])
    assert len(retry.attachments) == 1
    assert len(record.runner.artifacts.artifact_refs()) == 1
    await _stop(record)
@pytest.mark.asyncio
async def test_attachment_size_limit_is_rejected_before_durable_input(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path, workspace=str(tmp_path / "workspace"))
    class Oversized(_Upload):
        async def read(self, size: int = -1) -> bytes: assert 0 < size <= MAX_ATTACHMENT_BYTES + 1; return b"x" * size
    before = (record.product_session.events, dict(record.runner.artifacts.artifact_refs()), record.runner._input_queue.qsize())
    with pytest.raises(HTTPException) as upload_error: await service.upload_attachments(response.session_id, [Oversized()])
    assert upload_error.value.status_code == 413 and before == (record.product_session.events, record.runner.artifacts.artifact_refs(), record.runner._input_queue.qsize())
    first = _Upload(); first.data = b"a" * (MAX_ATTACHMENT_BYTES // 2 + 1); second = _Upload(); second.data = b"b" * (MAX_ATTACHMENT_BYTES // 2 + 1)
    first_id = (await service.upload_attachments(response.session_id, [first])).attachments[0].id; second_id = (await service.upload_attachments(response.session_id, [second])).attachments[0].id; events = record.product_session.events; metadata = json.loads(json.dumps(record.metadata))
    with pytest.raises(HTTPException) as selection_error: await service.send_input(response.session_id, SessionInputRequest(content="Say hi inspect", attachments=[first_id, second_id]))
    assert selection_error.value.status_code == 400 and record.product_session.events == events and record.runner._input_queue.empty() and record.metadata == metadata
    await _stop(record)
@pytest.mark.asyncio
async def test_attachment_storage_rejects_workspace_symlink_escape(monkeypatch, tmp_path) -> None:
    if os.name == "nt": pytest.skip("symlink privilege is not portable on Windows")
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"; workspace.mkdir(); outside.mkdir(); (workspace / ".breadboard").symlink_to(outside, target_is_directory=True); service, response, record = await _create(monkeypatch, tmp_path, workspace=str(workspace))
    with pytest.raises(HTTPException) as error: await service.upload_attachments(response.session_id, [_Upload()])
    assert error.value.status_code == 400 and not list(outside.iterdir())
    async def fail_stop() -> None: raise OSError("sink unavailable")
    monkeypatch.setattr(record.runner, "stop", fail_stop)
    with pytest.raises(OSError, match="sink unavailable"): await service.stop_session(response.session_id)
    assert record.dispatcher_task.done(); await service.registry.delete(response.session_id)
    (workspace / ".breadboard").unlink(); (workspace / ".breadboard").mkdir(); service, response, record = await _create(monkeypatch, tmp_path, service=service, workspace=str(workspace)); real_open, moved, swapped_root = os.open, tmp_path / "workspace-old", [False]
    def swap_root(path, flags, *args, **kwargs): (workspace.rename(moved), workspace.symlink_to(outside, target_is_directory=True), swapped_root.__setitem__(0, True)) if Path(path) == workspace and not swapped_root[0] else None; return real_open(path, flags, *args, **kwargs)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(os, "open", swap_root)
    with pytest.raises(HTTPException) as root_error: await service.upload_attachments(response.session_id, [_Upload()])
    assert root_error.value.status_code == 400 and not list(outside.iterdir()); monkeypatch.setattr(os, "open", real_open); workspace.unlink(); moved.rename(workspace); await service.delete_session(response.session_id)
    service, response, record = await _create(monkeypatch, tmp_path, service=service, workspace=str(workspace)); original, swapped = ArtifactStore.put, [False]
    def swap(store, *args, **kwargs): ((workspace / ".breadboard").rename(workspace / ".breadboard-old"), (workspace / ".breadboard").symlink_to(outside, target_is_directory=True), swapped.__setitem__(0, True)) if not swapped[0] else None; return original(store, *args, **kwargs)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ArtifactStore, "put", swap)
    with pytest.raises(HTTPException, match="metadata path changed"): await service.upload_attachments(response.session_id, [_Upload()])
    assert not list(outside.iterdir()) and not list((workspace / ".breadboard-old" / "attachments").iterdir()); (workspace / ".breadboard").unlink(); (workspace / ".breadboard-old").rename(workspace / ".breadboard"); await service.delete_session(response.session_id)
@pytest.mark.asyncio
async def test_completed_dispatch_replay_is_ordered_and_finite(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path)
    for order in (1, 2): await record.runner.publish_event_async(EventType.WARNING, {"order": order})
    assert record.dispatcher_task; record.runner.transition_product_session("complete"); await service.stop_session(response.session_id); assert record.dispatcher_task.done()
    replay = service.event_stream(response.session_id, replay=True); replayed = [event async for event in replay]; assert replayed == list(record.event_log); assert [event.type for event in replayed] == [EventType.WARNING, EventType.WARNING, EventType.TURN_COMPLETED]
    nonreplay = service.event_stream(response.session_id); snapshot = await asyncio.wait_for(anext(nonreplay), 0.1); assert snapshot.type is EventType.TOOL_RESULT and "todo" in snapshot.payload
    outcomes = await asyncio.wait_for(asyncio.gather(anext(replay), anext(nonreplay), return_exceptions=True), 0.1); assert len(outcomes) == 2 and all(isinstance(item, StopAsyncIteration) for item in outcomes)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "payload", "path", "expected"),
    [
        (
            "set_model",
            {"model": "openrouter/openai/gpt-5-nano"},
            ("providers", "default_model"),
            "openrouter/openai/gpt-5-nano",
        ),
        ("set_mode", {"mode": "plan"}, ("mode",), "plan"),
        (
            "set_skills",
            {"allowlist": ["test-skill"]},
            ("skills", "allowlist"),
            ["test-skill"],
        ),
    ],
)
async def test_runtime_reconfigure_survives_fresh_retained_resume(
    monkeypatch, tmp_path, command, payload, path, expected
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    state_root = tmp_path / "state"
    service = SessionService(state_root=state_root)
    response = await service.create_session(
        SessionCreateRequest(config_path=CONFIG, task=""),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)
    await service.execute_command(
        response.session_id,
        SessionCommandRequest(command=command, payload=payload),
    )
    pinned_generation = record.product_session.pinned_generation_id
    await _stop(record)

    fresh = SessionService(state_root=state_root)
    restored = await fresh.ensure_session(response.session_id)
    config = restored.runner.current_runtime_config()
    selected = config
    for key in path:
        selected = selected[key]
    assert selected == expected
    rebuilt_lock = fresh._runtime_lock(
        response.session_id, config, restored.runner.request.config_path
    )
    assert rebuilt_lock["graph_hash"] == pinned_generation
    await _stop(restored)


@pytest.mark.asyncio
async def test_retained_resume_refuses_runtime_generation_drift(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    state_root = tmp_path / "state"
    service = SessionService(state_root=state_root)
    response = await service.create_session(
        SessionCreateRequest(config_path=CONFIG, task=""),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)
    durable_events = tuple(record.product_session.events)
    await service.registry.persist(record)
    await _stop(record)
    real_load = load_agent_config

    def load_changed_config(path):
        config = real_load(path)
        config["mode"] = "plan" if config.get("mode") != "plan" else "implementation"
        return config

    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.session_runner.load_agent_config",
        load_changed_config,
    )
    fresh = SessionService(state_root=state_root)
    with pytest.raises(runtime_ports.ReplayError) as error:
        await fresh.ensure_session(response.session_id)
    assert error.value.code == "generation_mismatch"
    await fresh.delete_session(response.session_id)
    with pytest.raises(HTTPException) as deleted:
        await fresh.ensure_session(response.session_id)
    assert deleted.value.status_code == 404
    assert tuple(record.product_session.events) == durable_events


@pytest.mark.parametrize("failure", [FileNotFoundError, yaml.YAMLError])
@pytest.mark.asyncio
async def test_retained_resume_wraps_unavailable_generation_and_remains_deletable(
    monkeypatch, tmp_path, failure
) -> None:
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    state_root = tmp_path / "state"
    service = SessionService(state_root=state_root)
    response = await service.create_session(
        SessionCreateRequest(config_path=CONFIG, task=""),
        event_root=tmp_path / "events",
        runtime_root=tmp_path / "records",
    )
    record = await service.ensure_session(response.session_id)
    await service.registry.persist(record)
    await _stop(record)

    monkeypatch.setattr(
        RUNNER + "prepare_runtime_config",
        lambda _runner: (_ for _ in ()).throw(failure("config unavailable")),
    )
    fresh = SessionService(state_root=state_root)
    with pytest.raises(runtime_ports.ReplayError) as error:
        await fresh.ensure_session(response.session_id)
    assert error.value.code == "generation_unavailable"
    await fresh.delete_session(response.session_id)
    with pytest.raises(HTTPException) as deleted:
        await fresh.ensure_session(response.session_id)
    assert deleted.value.status_code == 404

@pytest.mark.asyncio
async def test_managed_retained_workspace_restores_attachments_without_binding(
    monkeypatch, tmp_path
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", "managed-attachment-test")
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(managed_root))
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    service = SessionService()
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="restore managed workspace attachment",
            workspace=str(workspace),
        ),
        session_id="managed-retained-attachment",
        event_root=workspace / ".breadboard" / "sessions",
    )
    record = await service.ensure_session(response.session_id)
    uploaded = await service.upload_attachments(response.session_id, [_Upload()])
    record.metadata.pop("durable_product_workspace", None)
    await service.registry.persist(record)
    await _stop(record)

    recovered = await SessionService().ensure_session(response.session_id)
    attachment_id = uploaded.attachments[0].id

    assert set(recovered.runner.artifacts.artifact_refs()) == {attachment_id}
    assert "proof.txt" in recovered.runner._format_attachment_helper([attachment_id])
    await _stop(recovered)


@pytest.mark.asyncio
async def test_retained_event_journal_rejects_symlinked_directory_and_file(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.runtime import ReplayError
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    session_directory = event_root / response.session_id
    journal = session_directory / "session_events.jsonl"
    retained_directory = event_root / "retained-directory"
    session_directory.rename(retained_directory)
    session_directory.symlink_to(retained_directory, target_is_directory=True)

    with pytest.raises(ReplayError, match="unsafe logical event journal"):
        _restore_product_session(response.session_id, event_root=event_root)

    session_directory.unlink()
    retained_directory.rename(session_directory)
    retained_journal = session_directory / "retained-events.jsonl"
    journal.rename(retained_journal)
    journal.symlink_to(retained_journal)

    with pytest.raises(ReplayError, match="unsafe logical event journal"):
        _restore_product_session(response.session_id, event_root=event_root)


@pytest.mark.asyncio
async def test_recovery_rejects_replaced_recorded_event_root(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.runtime import ReplayError

    state_root = tmp_path / "state"
    event_root = tmp_path / "recorded-events"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    initial_service = SessionService(state_root=state_root)
    response = await initial_service.create_session(
        SessionCreateRequest(config_path=CONFIG, task="retain event root"),
        session_id="replaced-recorded-event-root",
        event_root=event_root,
        runtime_root=tmp_path / "records",
    )
    initial = await initial_service.ensure_session(response.session_id)
    await _stop(initial)
    await initial_service.registry.persist(initial)
    retained_root = tmp_path / "retained-events"
    event_root.rename(retained_root)
    event_root.symlink_to(retained_root, target_is_directory=True)

    with pytest.raises(ReplayError, match="unsafe logical event journal"):
        await SessionService(state_root=state_root).ensure_session(
            response.session_id
        )


@pytest.mark.asyncio
async def test_retained_event_journal_rejects_oversized_file_before_read(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.runtime import ReplayError
    from breadboard_engine.api.cli_bridge.service import (
        _MAX_RETAINED_EVENT_JOURNAL_BYTES,
        _restore_product_session,
    )

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    with journal.open("r+b") as stream:
        stream.truncate(_MAX_RETAINED_EVENT_JOURNAL_BYTES + 1)

    with pytest.raises(ReplayError, match="unsafe logical event journal") as error:
        _restore_product_session(response.session_id, event_root=event_root)

    assert error.value.__cause__ is not None
    assert "exceeds byte limit" in str(error.value.__cause__)


def test_windows_retained_process_lock_rejects_replaced_lock_identity(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge import service as service_module
    from breadboard_engine.api.cli_bridge.service import _RetainedProcessLock

    event_path = tmp_path / ".session.events.starting" / "session_events.jsonl"
    event_path.parent.mkdir()
    stable_root = tmp_path / "events"
    stable_root.mkdir()
    lock_path = service_module._retained_event_lock_path(stable_root, "session")
    lock_path.write_bytes(b"\0")
    replacement = tmp_path / "replacement.lock"
    os.link(lock_path, replacement)
    opened_paths: list[Path] = []

    def open_windows_file_descriptor(path, *, create=True):
        opened_paths.append(Path(path))
        return os.open(replacement, os.O_RDWR)

    monkeypatch.setattr(
        service_module,
        "os",
        SimpleNamespace(
            name="nt",
            fdopen=os.fdopen,
            fstat=os.fstat,
            close=os.close,
        ),
    )
    monkeypatch.setattr(
        service_module.AnchoredStorage,
        "windows_file_descriptor",
        staticmethod(open_windows_file_descriptor),
    )
    with pytest.raises(OSError, match="unsafe retained event process lock"):
        with _RetainedProcessLock(event_path, lock_path=lock_path):
            raise AssertionError("unreachable")
    assert opened_paths == [lock_path]
    assert lock_path.parent != event_path.parent


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO semantics")
async def test_retained_event_journal_rejects_fifo_without_blocking(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.runtime import ReplayError
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    journal.unlink()
    os.mkfifo(journal)

    with pytest.raises(ReplayError, match="unsafe logical event journal"):
        _restore_product_session(response.session_id, event_root=event_root)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX hard-link containment")
async def test_retained_event_journal_rejects_hard_link(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.runtime import ReplayError
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    outside = tmp_path / "outside-events.jsonl"
    outside.write_bytes(journal.read_bytes())
    outside_before = outside.read_bytes()
    journal.unlink()
    os.link(outside, journal)

    with pytest.raises(ReplayError, match="unsafe logical event journal"):
        _restore_product_session(response.session_id, event_root=event_root)

    assert outside.read_bytes() == outside_before


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor recovery")
async def test_retained_recovery_preserves_journal_and_wal_for_hard_link(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge.service import _RetainedEventSink

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    session_directory = event_root / response.session_id
    journal = session_directory / "session_events.jsonl"
    before = journal.read_bytes()
    transaction = session_directory / ".session_events.jsonl.txn"
    transaction.write_text("0", encoding="ascii")
    outside = tmp_path / "outside-events.jsonl"
    os.link(journal, outside)
    directory_stat = session_directory.stat(follow_symlinks=False)
    journal_stat = journal.stat(follow_symlinks=False)
    sink = _RetainedEventSink(
        None,
        event_root,
        response.session_id,
        (
            (directory_stat.st_dev, directory_stat.st_ino),
            (journal_stat.st_dev, journal_stat.st_ino),
        ),
    )
    session_descriptor = os.open(session_directory, os.O_RDONLY)
    event_descriptor = os.open(journal, os.O_RDWR)
    try:
        with pytest.raises(OSError, match="exactly one hard link"):
            sink._recover_transaction_posix(
                session_descriptor,
                event_descriptor,
            )
    finally:
        os.close(event_descriptor)
        os.close(session_descriptor)

    assert journal.read_bytes() == before
    assert outside.read_bytes() == before
    assert transaction.read_text(encoding="ascii") == "0"


@pytest.mark.asyncio
async def test_restored_event_sink_rejects_append_past_byte_limit(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge import service as service_module

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    restored = service_module._restore_product_session(
        response.session_id,
        event_root=event_root,
    )
    before = journal.read_bytes()
    monkeypatch.setattr(
        service_module,
        "_MAX_RETAINED_EVENT_JOURNAL_BYTES",
        len(before),
    )

    with pytest.raises(RuntimeError, match="exceeds byte limit"):
        restored.complete()

    assert journal.read_bytes() == before


@pytest.mark.asyncio
async def test_restored_event_sink_revalidates_identity_before_every_append(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    restored = _restore_product_session(response.session_id, event_root=event_root)
    retained_journal = journal.with_name("retained-events.jsonl")
    journal.rename(retained_journal)
    outside = tmp_path / "outside-events.jsonl"
    outside.write_bytes(retained_journal.read_bytes())
    outside_before = outside.read_bytes()
    journal.symlink_to(outside)

    with pytest.raises(RuntimeError, match="event journal identity changed"):
        restored.complete()

    assert outside.read_bytes() == outside_before


@pytest.mark.asyncio
async def test_restored_event_sink_rejects_stale_writer_head_advance(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge import service as service_module

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    restored = service_module._restore_product_session(
        response.session_id,
        event_root=event_root,
    )

    record.product_session.pause("stale writer advanced the journal head")
    with pytest.raises(
        RuntimeError,
        match="event journal advanced since session recovery",
    ):
        restored.pause("stale restored writer")

    replayed = service_module._restore_product_session(
        response.session_id,
        event_root=event_root,
    )
    assert [event.sequence for event in replayed.events] == [1, 2]
    assert [event.kind for event in replayed.events] == [
        "session.started",
        "session.paused",
    ]
    assert journal.read_bytes().count(b"\n") == 2


@pytest.mark.asyncio
async def test_live_event_sink_rejects_append_past_restored_writer_head(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge import service as service_module

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    restored = service_module._restore_product_session(
        response.session_id,
        event_root=event_root,
    )

    restored.pause("restored writer advanced the journal head")
    with pytest.raises(
        RuntimeError,
        match="event journal advanced since",
    ):
        record.product_session.pause("stale original writer")

    replayed = service_module._restore_product_session(
        response.session_id,
        event_root=event_root,
    )
    assert [event.sequence for event in replayed.events] == [1, 2]
    assert [event.kind for event in replayed.events] == [
        "session.started",
        "session.paused",
    ]
    assert journal.read_bytes().count(b"\n") == 2


@pytest.mark.asyncio
async def test_start_publication_serializes_writer_before_sink_binding(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge import service as service_module

    event_root = tmp_path / "events"
    state_root = tmp_path / "state"
    runtime_root = tmp_path / "records"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    service = SessionService(state_root=state_root)
    recovery_started = threading.Event()
    writer_done = threading.Event()
    writer_errors: list[BaseException] = []
    real_recover = service_module._RetainedEventSink.recover

    def signal_recover(sink):
        recovery_started.set()
        return real_recover(sink)

    monkeypatch.setattr(
        service_module._RetainedEventSink,
        "recover",
        signal_recover,
    )
    real_publish = service._publish_start_bundle
    writer_thread: threading.Thread | None = None

    def interleaved_writer(session_id, event_dir):
        try:
            retained = service_module._restore_product_session(
                session_id,
                event_root=event_dir.parent,
            )
            retained.pause("writer raced live sink binding")
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_done.set()

    def publish_with_interleaved_writer(
        session_id,
        staged_record_dir,
        staging_record_root,
        runtime_record_dir,
        staged_event_dir,
        event_dir,
        publish_records,
    ):
        nonlocal writer_thread
        real_publish(
            session_id,
            staged_record_dir,
            staging_record_root,
            runtime_record_dir,
            staged_event_dir,
            event_dir,
            publish_records,
        )
        writer_thread = threading.Thread(
            target=interleaved_writer,
            args=(session_id, event_dir),
        )
        writer_thread.start()
        if not recovery_started.wait(2):
            raise RuntimeError("interleaved writer did not reach retained recovery")

    monkeypatch.setattr(
        service,
        "_publish_start_bundle",
        publish_with_interleaved_writer,
    )
    session_id = "publication-binding-race"
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="serialize publication binding",
        ),
        session_id=session_id,
        event_root=event_root,
        runtime_root=runtime_root,
    )
    assert response.session_id == session_id
    assert writer_thread is not None
    writer_thread.join(timeout=2)
    assert writer_done.is_set()
    assert writer_errors == []

    record = await service.ensure_session(session_id)
    retained = await service.registry.get(session_id)
    assert retained is record
    assert retained.status is SessionStatus.STARTING
    assert [event.sequence for event in record.product_session.events] == [1]
    with pytest.raises(
        RuntimeError,
        match="event journal advanced since session recovery",
    ):
        record.product_session.pause("stale live writer")

    replayed = service_module._restore_product_session(
        session_id,
        event_root=event_root,
    )
    assert [event.sequence for event in replayed.events] == [1, 2]
    assert [event.kind for event in replayed.events] == [
        "session.started",
        "session.paused",
    ]
    await _stop(record)
@pytest.mark.asyncio
async def test_start_refreshes_retained_writer_before_authorize(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge import service as service_module

    event_root = tmp_path / "events"
    state_root = tmp_path / "state"
    runtime_root = tmp_path / "records"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    service = SessionService(state_root=state_root)
    prewarm_started = threading.Event()
    release_prewarm = threading.Event()
    writer_done = threading.Event()
    writer_errors: list[BaseException] = []

    async def gated_prewarm(*_args) -> None:
        prewarm_started.set()
        await asyncio.to_thread(release_prewarm.wait)

    monkeypatch.setattr(service, "_maybe_prewarm_request_runtime", gated_prewarm)
    session_id = "preauthorize-retained-refresh"
    create_task = asyncio.create_task(
        service.create_session(
            SessionCreateRequest(
                config_path=CONFIG,
                task="refresh before authorize",
            ),
            session_id=session_id,
            event_root=event_root,
            runtime_root=runtime_root,
        )
    )
    assert await asyncio.to_thread(prewarm_started.wait, 2)

    def interleaved_writer() -> None:
        try:
            retained = service_module._restore_product_session(
                session_id,
                event_root=event_root,
            )
            retained.pause("writer raced before authorize")
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_done.set()

    writer = threading.Thread(target=interleaved_writer)
    writer.start()
    writer.join(timeout=2)
    assert writer_done.is_set()
    assert writer_errors == []
    release_prewarm.set()
    response = await create_task

    record = await service.ensure_session(response.session_id)
    assert await service.registry.get(response.session_id) is record
    assert record.status is SessionStatus.STARTING
    assert [event.sequence for event in record.product_session.events] == [1, 2]
    record.product_session.resume()
    assert [event.sequence for event in record.product_session.events] == [1, 2, 3]
    replayed = service_module._restore_product_session(
        session_id,
        event_root=event_root,
    )
    assert [event.sequence for event in replayed.events] == [1, 2, 3]
    await _stop(record)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "payload"),
    [
        ("set_model", {"model": "openrouter/openai/gpt-5-nano"}),
        ("set_mode", {"mode": "plan"}),
        ("set_skills", {"allowlist": ["test-skill"]}),
    ],
)
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_runtime_reconfigure_metadata_failure_restores_authority(
    monkeypatch, tmp_path, command, payload, failure
) -> None:
    service, response, record = await _create(
        monkeypatch,
        tmp_path,
        task="",
        service=SessionService(state_root=tmp_path / "state"),
    )
    runner = record.runner
    before = (
        runner.current_runtime_config(),
        json.loads(json.dumps(record.metadata)),
        runner._prepared_runtime_config,
        record.product_session.pinned_generation_id,
    )

    async def fail_update_metadata(*_args, **_kwargs):
        raise failure("metadata persistence unavailable")

    monkeypatch.setattr(service.registry, "update_metadata", fail_update_metadata)
    with pytest.raises(failure):
        await service.execute_command(
            response.session_id,
            SessionCommandRequest(command=command, payload=payload),
        )
    assert runner.current_runtime_config() == before[0]
    assert record.metadata == before[1]
    assert runner._prepared_runtime_config == before[2]
    assert record.product_session.pinned_generation_id == before[3]
    await _stop(record)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_sequence",
    [
        pytest.param(True, id="boolean"),
        pytest.param(1.0, id="float"),
        pytest.param("1", id="string"),
        pytest.param(float("inf"), id="infinity"),
    ],
)
async def test_restore_rejects_non_exact_committed_event_sequence(
    monkeypatch, tmp_path, invalid_sequence
) -> None:
    from breadboard_engine.api.cli_bridge.service import _restore_product_session

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    original_record = json.loads(journal.read_text(encoding="utf-8"))
    original_record["sequence"] = invalid_sequence
    corrupted = (
        json.dumps(original_record, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    journal.write_bytes(corrupted)

    with pytest.raises(runtime_ports.ReplayError) as raised:
        _restore_product_session(response.session_id, event_root=event_root)

    assert raised.value.code == "invalid_event_record"
    assert journal.read_bytes() == corrupted

@pytest.mark.asyncio
async def test_live_event_sink_delegates_with_retained_journal_cap(
    monkeypatch, tmp_path
) -> None:
    from breadboard.product.runtime.events import KernelEvent
    from breadboard_engine.api.cli_bridge import service as service_module
    from breadboard_engine.api.cli_bridge.service import _RetainedEventSink

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    sink = record.product_session._sink
    assert isinstance(sink, _RetainedEventSink)
    assert sink._delegate is not None
    assert sink._delegate._max_bytes == (
        service_module._MAX_RETAINED_EVENT_JOURNAL_BYTES
    )
    before = journal.read_bytes()
    sink._delegate._max_bytes = len(before)
    oversized = KernelEvent.create(
        response.session_id,
        2,
        "session.paused",
        "2026-09-03T00:00:00Z",
        {"reason": "x" * len(before)},
    )
    with pytest.raises(RuntimeError, match="event journal exceeds byte limit"):
        sink._delegate.append(oversized)
    assert journal.read_bytes() == before


@pytest.mark.asyncio
async def test_restored_event_sink_anchors_open_file_across_path_swap(
    monkeypatch, tmp_path
) -> None:
    from breadboard_engine.api.cli_bridge.service import (
        _RetainedEventSink,
        _restore_product_session,
    )

    service, response, record = await _create(monkeypatch, tmp_path)
    await _stop(record)
    event_root = Path(record.metadata["session_event_root"])
    journal = event_root / response.session_id / "session_events.jsonl"
    original = journal.read_bytes()
    restored = _restore_product_session(response.session_id, event_root=event_root)
    retained_journal = journal.with_name("retained-events.jsonl")
    outside = tmp_path / "outside-events.jsonl"
    outside.write_bytes(original)
    real_verify = _RetainedEventSink._verify_identity
    verifications = 0

    def swap_after_open(sink, directory_stat, file_stat):  # type: ignore[no-untyped-def]
        nonlocal verifications
        real_verify(sink, directory_stat, file_stat)
        verifications += 1
        if verifications == 1:
            journal.rename(retained_journal)
            journal.symlink_to(outside)

    monkeypatch.setattr(_RetainedEventSink, "_verify_identity", swap_after_open)

    with pytest.raises(RuntimeError, match="event journal identity changed"):
        restored.complete()

    assert outside.read_bytes() == original
    assert retained_journal.read_bytes() == original


def test_retained_manifest_history_rejects_excess_count_before_reads(
    monkeypatch, tmp_path
) -> None:
    owner = SessionArtifactStore(session_id="bounded-history", metadata={})
    names = [
        f"bounded-history.{index:064x}.json"
        for index in range(257)
    ]
    reads: list[ArtifactRef] = []
    monkeypatch.setattr(owner, "_manifest_names", lambda _workspace: names)
    monkeypatch.setattr(
        owner,
        "_read_manifest",
        lambda _workspace, ref: reads.append(ref),
    )

    with pytest.raises(ValueError, match="too many retained attachment manifests"):
        owner.restore_manifest(tmp_path)

    assert reads == []


@pytest.mark.asyncio
async def test_retained_attachment_materialization_reads_at_most_two_children(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service, response, record = await _create(
        monkeypatch,
        tmp_path,
        workspace=str(workspace),
    )
    uploaded = await service.upload_attachments(response.session_id, [_Upload()])
    attachment_directory = (
        workspace
        / ".breadboard"
        / "attachments"
        / uploaded.attachments[0].id
    )
    (attachment_directory / "extra-a").write_bytes(b"a")
    (attachment_directory / "extra-b").write_bytes(b"b")
    owner = SessionArtifactStore(
        session_id=response.session_id,
        metadata=dict(record.metadata),
    )
    await _stop(record)
    real_iterdir = Path.iterdir

    def fail_after_two(path: Path):
        for index, child in enumerate(real_iterdir(path)):
            if path == attachment_directory and index == 2:
                raise AssertionError("enumerated a third attachment child")
            yield child

    monkeypatch.setattr(Path, "iterdir", fail_after_two)
    with pytest.raises(ValueError, match="invalid retained attachment materialization"):
        owner.restore_manifest(workspace)


def test_retained_manifest_history_rejects_oversized_entry_before_read(
    monkeypatch, tmp_path
) -> None:
    digest = "0" * 64
    owner = SessionArtifactStore(session_id="bounded-history", metadata={})
    reads: list[ArtifactRef] = []
    monkeypatch.setattr(
        owner,
        "_manifest_names",
        lambda _workspace: [f"bounded-history.{digest}.json"],
    )
    monkeypatch.setattr(
        session_artifacts,
        "workspace_artifact_ref",
        lambda _workspace, value, *, media_type: ArtifactRef(
            digest=value,
            size_bytes=1024 * 1024 + 1,
            media_type=media_type,
        ),
    )
    monkeypatch.setattr(
        owner,
        "_read_manifest",
        lambda _workspace, ref: reads.append(ref),
    )

    with pytest.raises(ValueError, match="retained attachment manifest is oversized"):
        owner.restore_manifest(tmp_path)

    assert reads == []


def test_retained_manifest_history_rejects_aggregate_size_before_reads(
    monkeypatch, tmp_path
) -> None:
    owner = SessionArtifactStore(session_id="bounded-history", metadata={})
    names = [
        f"bounded-history.{index:064x}.json"
        for index in range(65)
    ]
    reads: list[ArtifactRef] = []
    monkeypatch.setattr(owner, "_manifest_names", lambda _workspace: names)
    monkeypatch.setattr(
        session_artifacts,
        "workspace_artifact_ref",
        lambda _workspace, digest, *, media_type: ArtifactRef(
            digest=digest,
            size_bytes=1024 * 1024,
            media_type=media_type,
        ),
    )
    monkeypatch.setattr(
        owner,
        "_read_manifest",
        lambda _workspace, ref: reads.append(ref),
    )

    with pytest.raises(ValueError, match="retained attachment manifests are oversized"):
        owner.restore_manifest(tmp_path)

    assert reads == []


@pytest.mark.asyncio
async def test_retained_manifest_ref_wins_over_uncommitted_newer_manifest(
    monkeypatch, tmp_path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    event_root = workspace / ".breadboard" / "sessions"
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)
    initial_service = SessionService(state_root=state_root)
    response = await initial_service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="ignore an uncommitted newer manifest",
            workspace=str(workspace),
        ),
        session_id="stale-newer-manifest",
        event_root=event_root,
    )
    initial = await initial_service.ensure_session(response.session_id)
    first = await initial_service.upload_attachments(response.session_id, [_Upload()])
    retained_ref = dict(initial.metadata["artifact_manifest_ref"])
    second = _Upload()
    second.filename = "uncommitted.txt"
    await initial.runner.artifacts.upload([second], workspace_dir=workspace)
    initial.metadata["artifact_manifest_ref"] = retained_ref
    await initial_service.registry.persist(initial)
    await _stop(initial)

    recovered_service = SessionService(state_root=state_root)
    recovered = await recovered_service.ensure_session(response.session_id)
    first_id = first.attachments[0].id
    second_id = next(
        attachment_id
        for attachment_id in (
            set(path.name for path in (workspace / ".breadboard" / "attachments").iterdir())
            - {first_id}
        )
    )

    assert set(recovered.runner.artifacts.artifact_refs()) == {first_id}
    with pytest.raises(ValueError, match=f"unknown attachment IDs: {second_id}"):
        recovered.runner.artifacts.selected_artifacts([second_id])
    third = _Upload()
    third.filename = "committed-after-recovery.txt"
    third_upload = await recovered_service.upload_attachments(
        response.session_id,
        [third],
    )
    third_id = third_upload.attachments[0].id
    await _stop(recovered)
    restarted = await SessionService(state_root=state_root).ensure_session(
        response.session_id
    )
    assert set(restarted.runner.artifacts.artifact_refs()) == {first_id, third_id}
    await _stop(restarted)


@pytest.mark.asyncio
async def test_legacy_managed_retained_session_rebinds_durable_workspace(
    monkeypatch, tmp_path
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", "legacy-managed-binding")
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(managed_root))
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)

    service = SessionService()
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="restore legacy managed binding",
            workspace=str(workspace),
        ),
        session_id="legacy-managed-binding",
        event_root=workspace / ".breadboard" / "sessions",
    )
    record = await service.ensure_session(response.session_id)
    workspace_journal = (
        workspace
        / ".breadboard"
        / "sessions"
        / response.session_id
        / "session_events.jsonl"
    )
    workspace_sink = runtime_ports.JsonlEventSink(workspace_journal)
    for event in record.product_session.events:
        workspace_sink.append(event)
    record.metadata.pop("session_event_root", None)
    record.metadata.pop("durable_product_workspace", None)
    await service.registry.persist(record)
    await _stop(record)

    recovered = await SessionService().ensure_session(response.session_id)
    assert recovered.metadata["durable_product_workspace"] == str(
        workspace.resolve()
    )
    assert recovered.runner is not None
    assert recovered.runner._durable_product_session is not None

    recovered.product_session.complete()
    recovered.runner._commit_terminal_product_session_locked()
    assert session_store.session_metadata_path(
        workspace, response.session_id
    ).is_file()
    await _stop(recovered)


@pytest.mark.asyncio
async def test_legacy_internal_managed_session_does_not_gain_public_binding(
    monkeypatch, tmp_path
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", "legacy-internal-binding")
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(managed_root))
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)

    service = SessionService()
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="restore legacy internal session",
            workspace=str(workspace),
        ),
        session_id="legacy-internal-binding",
    )
    record = await service.ensure_session(response.session_id)
    record.metadata.pop("session_event_root", None)
    record.metadata.pop("durable_product_workspace", None)
    await service.registry.persist(record)
    await _stop(record)

    recovered = await SessionService().ensure_session(response.session_id)
    assert "durable_product_workspace" not in recovered.metadata
    assert recovered.runner is not None
    assert recovered.runner._durable_product_session is None
    await _stop(recovered)

@pytest.mark.asyncio
async def test_legacy_workspace_symlink_does_not_gain_public_binding(
    monkeypatch, tmp_path
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", "legacy-symlink-binding")
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(managed_root))
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)

    service = SessionService()
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="restore legacy symlink session",
            workspace=str(workspace),
        ),
        session_id="legacy-symlink-binding",
    )
    record = await service.ensure_session(response.session_id)
    managed_journal = (
        Path(record.metadata["session_event_root"])
        / response.session_id
        / "session_events.jsonl"
    )
    assert managed_journal.is_file()
    workspace_journal = (
        workspace
        / ".breadboard"
        / "sessions"
        / response.session_id
        / "session_events.jsonl"
    )
    workspace_journal.parent.mkdir(parents=True, exist_ok=True)
    workspace_journal.symlink_to(managed_journal)
    record.metadata.pop("session_event_root", None)
    record.metadata.pop("durable_product_workspace", None)
    await service.registry.persist(record)
    await _stop(record)

    recovered = await SessionService().ensure_session(response.session_id)
    assert "durable_product_workspace" not in recovered.metadata
    assert recovered.runner is not None
    assert recovered.runner._durable_product_session is None
    await _stop(recovered)



@pytest.mark.asyncio
async def test_recovery_creates_missing_durable_workspace_session_directory(
    monkeypatch, tmp_path
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", "recovery-creates-binding")
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(managed_root))
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None)
    monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None)

    service = SessionService()
    response = await service.create_session(
        SessionCreateRequest(
            config_path=CONFIG,
            task="recover missing durable session directory",
            workspace=str(workspace),
        ),
        session_id="recovery-creates-binding",
        event_root=workspace / ".breadboard" / "sessions",
    )
    record = await service.ensure_session(response.session_id)
    record.metadata["durable_product_workspace"] = str(workspace.resolve())
    await service.registry.persist(record)
    await _stop(record)

    session_directory = workspace / ".breadboard" / "sessions"
    assert session_directory.is_dir()
    session_directory.rmdir()

    recovered = await SessionService().ensure_session(response.session_id)
    assert session_directory.is_dir()
    assert recovered.runner is not None
    assert recovered.runner._durable_product_session is not None
    await _stop(recovered)
