from __future__ import annotations
from types import SimpleNamespace; from pathlib import Path; import asyncio, hashlib, json, threading, pytest
from fastapi import HTTPException
from breadboard.product.runtime import ports as runtime_ports
from agentic_coder_prototype.api.cli_bridge.models import SessionCommandRequest, SessionCreateRequest, SessionInputRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.events import EventType; from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.runtime_emission import _tool_names
from agentic_coder_prototype.compilation.v2_loader import load_agent_config

CONFIG = "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml"
RUNNER = "agentic_coder_prototype.api.cli_bridge.session_runner.SessionRunner."
SERVICE = "agentic_coder_prototype.api.cli_bridge.service."
class _Failing:
    def append(self, _event) -> None: raise OSError("sink unavailable")  # type: ignore[no-untyped-def]
    def put_nowait(self, _item) -> None: raise RuntimeError("broker unavailable")  # type: ignore[no-untyped-def]
async def _stop(record) -> None:  # type: ignore[no-untyped-def]
    if record.dispatcher_task and not record.dispatcher_task.done(): await record.event_queue.put(None); await record.dispatcher_task
async def _create(monkeypatch, tmp_path, *, service=None, task="Say hi", **fields):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER + "schedule_start", lambda _runner: None); monkeypatch.setattr(RUNNER + "authorize_start", lambda _runner: None); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "events"))
    service = service or SessionService()
    response = await service.create_session(SessionCreateRequest(config_path=CONFIG, task=task, **fields))
    return service, response, await service.ensure_session(response.session_id)
@pytest.mark.asyncio
@pytest.mark.parametrize(("metadata", "task"), [({"cli_session_kind": "oneshot", "non_interactive_cli_session": True}, "Say hi"), ({"cli_session_kind": "interactive"}, "Say hi"), ({"cli_session_kind": "interactive"}, "")])
async def test_session_service_prewarms_supported_and_empty_sessions(monkeypatch, tmp_path, metadata, task) -> None:
    service, called = SessionService(), []; monkeypatch.setattr(service, "_prewarm_request_runtime_sync", lambda request, values, config: called.append((request.config_path, values["cli_session_kind"], config["providers"]["default_model"])))
    if not task: monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True); monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records"))
    service, response, record = await _create(monkeypatch, tmp_path, service=service, metadata=metadata, stream=True, task=task)
    assert called == [(CONFIG, metadata["cli_session_kind"], record.runner.current_runtime_config()["providers"]["default_model"])]
    if not task:
        title = "interactive session awaiting input"; stream = Path(record.metadata["runtime_record_dir"]) / "records" / "config_plane.jsonl"; work = [json.loads(line) for line in stream.read_text().splitlines() if '"name":"work_item_' in line]
        assert [item["name"] for item in work] == ["work_item_created", "work_item_lease_acquired", "work_item_attempt_started", "work_item_snapshot"] and [item["record"].get("kind") for item in work] == ["work_item.created", "lease.acquired", "attempt.started", None] and work[-1]["schema_version"] == "bb.work_item.v2"
        assert work[-1]["record"]["title"] == title and record.product_session.events[0].payload["task_hash"] == "sha256:" + hashlib.sha256(title.encode()).hexdigest() and record.runner.request.task == "" and record.runner._input_queue.empty()
    await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.STOPPED and type(record.product_session).restore(record.product_session.events).read_model.status == "canceled"; await _stop(record)
@pytest.mark.asyncio
async def test_effective_lock_is_exact_and_secret_free(monkeypatch, tmp_path) -> None:
    from agentic_coder_prototype.auth.store import DEFAULT_PROVIDER_AUTH_STORE
    auth = SimpleNamespace(api_key="forbidden-key", base_url="https://secret.invalid", headers={"X-Secret": "forbidden-header"})
    monkeypatch.setattr(DEFAULT_PROVIDER_AUTH_STORE, "get", lambda _: auth); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records"))
    workspace = str((tmp_path / "workspace").resolve()); service, response, record = await _create(monkeypatch, tmp_path, workspace=workspace, metadata={"model": "test-runtime-model"}, overrides={"provider_auth_runtime.openai.api_key": auth.api_key})
    config = record.runner.current_runtime_config(); original_lock = service._runtime_lock(response.session_id, config, CONFIG)
    graph = json.loads(Path(record.metadata["runtime_records"]["effective_config_graph"]).read_text(encoding="utf-8"))
    assert graph == original_lock.as_dict() and graph["graph_hash"] == record.product_session.read_model.effective_lock_hash
    config["nested"] = {"provider_auth_runtime": {"token": "nested-secret"}, "provider_auth_runtime.token": "dotted-secret", "safe": True}; lock = service._runtime_lock(response.session_id, config, CONFIG); values = {row["path"]: row["value"] for row in lock["effective_values"]}
    serialized = lock.canonical_json() + "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "records" / response.session_id).rglob("*") if path.is_file())
    assert (values["workspace.root"], values["providers.default_model"]) == (workspace, "test-runtime-model")
    assert all(secret not in serialized for secret in ("provider_auth_runtime", auth.api_key, auth.base_url, auth.headers["X-Secret"], "nested-secret", "dotted-secret"))
    assert _tool_names(load_agent_config(CONFIG)) == ["apply_patch", "shell_command", "update_plan"]
    record.runner.transition_product_session("complete"); await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.COMPLETED and type(record.product_session).restore(record.product_session.events).read_model.status == "completed"; await _stop(record)
@pytest.mark.asyncio
async def test_input_and_approval_are_durable_before_delivery(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path); sink, record.product_session._sink = record.product_session._sink, _Failing()
    with pytest.raises(OSError, match="sink unavailable"):
        await service.send_input(response.session_id, SessionInputRequest(content="next"))
    assert record.runner._input_queue.empty(); assert [event.kind for event in record.product_session.events] == ["session.started"]
    record.product_session._sink = sink; record.runner._rehydrate_pending_permissions("permission_request", {"request_id": "perm-1", "category": "shell"}); record.runner._permission_queue = _Failing(); persisted = []
    monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.session_runner.upsert_permission_rule", lambda *_args, **_kwargs: persisted.append(True))
    request = SessionCommandRequest(command="permission_decision", payload={"request_id": "perm-1", "decision": "always", "rule": "*.sh"})
    with pytest.raises(HTTPException) as error:
        await service.execute_command(response.session_id, request)
    assert error.value.status_code == 409; assert [event.kind for event in record.product_session.events][-2:] == ["approval.resolved", "session.failed"]; assert record.status.value == "failed"
    assert not persisted and "permission_rules" not in record.metadata
    await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.FAILED and type(record.product_session).restore(record.product_session.events).read_model.status == "failed"; await _stop(record)
@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "payload"), [
    ("set_model", {"model": "openrouter/openai/gpt-5-nano"}),
    ("set_skills", {"allowlist": ["test-skill"]}),
    ("set_mode", {"mode": "plan"}),
])
async def test_reconfiguration_is_durable_before_runtime_mutation(monkeypatch, tmp_path, command, payload) -> None:
    service, response, record = await _create(monkeypatch, tmp_path); runner = record.runner
    before_config, before_metadata, before_model, before_mode = runner.current_runtime_config(), dict(record.metadata), runner._model_override, runner._mode
    sink, record.product_session._sink = record.product_session._sink, _Failing()
    with pytest.raises(OSError, match="sink unavailable"):
        await service.execute_command(response.session_id, SessionCommandRequest(command=command, payload=payload))
    assert runner.current_runtime_config() == before_config; assert record.metadata == before_metadata; assert (runner._model_override, runner._mode) == (before_model, before_mode)
    assert [event.kind for event in record.product_session.events] == ["session.started"]
    if command == "set_mode":
        record.product_session._sink = sink
        await service.execute_command(response.session_id, SessionCommandRequest(command=command, payload=payload))
        assert (record.product_session.events[-1].kind, runner.current_runtime_config()["mode"], record.metadata["mode"]) == ("session.reconfigured", "plan", "plan")
    await _stop(record)
@pytest.mark.asyncio
async def test_runtime_failure_does_not_advance_registry_past_failed_sink(monkeypatch, tmp_path) -> None:
    service, _, record = await _create(monkeypatch, tmp_path); updates, original_update = [], service.registry.update_status
    async def update_status(session_id, status): updates.append(status); await original_update(session_id, status)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service.registry, "update_status", update_status); monkeypatch.setattr(record.runner, "prepare_runtime_config", lambda: (_ for _ in ()).throw(RuntimeError("runtime failed")))
    record.product_session._sink = _Failing()
    with pytest.raises(OSError, match="sink unavailable"):
        await record.runner._run()
    assert SessionStatus.FAILED not in updates; assert record.status is not SessionStatus.FAILED; assert record.product_session.read_model.status == "running"
    await _stop(record)
@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["set_model", "set_skills"])
async def test_reconfiguration_commit_failure_terminalizes_session(monkeypatch, tmp_path, command) -> None:
    class RejectingModelConfig(dict):
        def setdefault(self, *_args, **_kwargs): raise RuntimeError("model propagation failed")  # type: ignore[no-untyped-def]
    if command == "set_model": agent = SimpleNamespace(config=RejectingModelConfig()); payload = {"model": "openrouter/openai/gpt-5-nano"}
    else: agent = SimpleNamespace(config={}, apply_runtime_overrides=lambda _overrides: False); payload = {"allowlist": ["test-skill"]}
    service, response, record = await _create(monkeypatch, tmp_path); record.runner._agent = agent
    with pytest.raises(HTTPException) as error:
        await service.execute_command(response.session_id, SessionCommandRequest(command=command, payload=payload))
    assert error.value.status_code == 409; assert [event.kind for event in record.product_session.events][-2:] == ["session.reconfigured", "session.failed"]; assert record.status is SessionStatus.FAILED
    await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert (await service.registry.get(response.session_id)) is record and record.status is SessionStatus.FAILED and record.product_session.events[-1].kind == "session.failed"; await _stop(record)
@pytest.mark.asyncio
async def test_scheduling_failure_publishes_no_start_authority(monkeypatch, tmp_path) -> None:
    records_root, events_root = tmp_path / "records", tmp_path / "events"
    def fail(_runner) -> None: raise RuntimeError("runner scheduling exploded")  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER + "schedule_start", fail); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True); monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "schedule-failure")
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root)); service = SessionService()
    with pytest.raises(RuntimeError, match="runner scheduling exploded"): await service.create_session(SessionCreateRequest(config_path=CONFIG, task="task"))
    assert await service.registry.get("schedule-failure") is None and all(not root.exists() or not any(root.iterdir()) for root in (records_root, events_root))
@pytest.mark.asyncio
async def test_initial_durable_start_failure_has_no_published_lifecycle(monkeypatch, tmp_path) -> None:
    records_root, events_root, started = tmp_path / "records", tmp_path / "events", []; entered, released = threading.Event(), threading.Event()
    def start(runner) -> None: started.append(runner)  # type: ignore[no-untyped-def]
    def emit(*, session_id, request, output_root, **_): path = output_root / session_id / "start.json"; path.parent.mkdir(parents=True); path.write_text("emitted", encoding="utf-8"); entered.set(); assert released.wait(2); return {"start": str(path)}  # type: ignore[no-untyped-def]
    def fail_sync(_stream) -> None: raise OSError("initial append failed")  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER + "schedule_start", start); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True); monkeypatch.setattr(SERVICE + "emit_session_start_records", emit)
    monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "durable-start-failure"); monkeypatch.setattr(runtime_ports, "_sync", fail_sync)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root))
    service = SessionService(); request = SessionCreateRequest(config_path=CONFIG, task="task")
    pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(service.create_session(request))))
    assert await asyncio.to_thread(entered.wait, 2); assert await service.registry.get("durable-start-failure") is None
    assert not (records_root / "durable-start-failure").exists() and not (events_root / "durable-start-failure").exists()
    released.set()
    with pytest.raises(OSError, match="initial append failed"): await pending
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
    if boundary == "authority":
        hidden_event = events_root / f".{session_id}.events.starting" / "session_events.jsonl"; assert (authority / ".start.committed").is_file() and ((events_root / session_id / "session_events.jsonl").is_file() or hidden_event.is_file())
    else: assert not (records_root / session_id).exists() and not (events_root / session_id).exists() and not (records_root / session_id / "records" / "config_plane.jsonl").exists()
    released.set()
    with pytest.raises(OSError, match=f"{boundary} publication failed"): await pending
    if boundary == "authority":
        SessionService(); assert (authority / ".start.committed").is_file() and (events_root / session_id / "session_events.jsonl").is_file(); return
    assert all(not root.exists() or not any(root.iterdir()) for root in {records_root, events_root})
    response = await service.create_session(request); record = await service.ensure_session(response.session_id); authority = (records_root if primitives else events_root) / response.session_id
    assert (authority / ".start.committed").is_file() and (events_root / response.session_id / "session_events.jsonl").is_file() and (primitives or shared_root or not records_root.exists())
    await _stop(record)
@pytest.mark.parametrize("shared_root", [False, True])
def test_startup_removes_incomplete_and_recovers_committed_projection(monkeypatch, tmp_path, shared_root) -> None:
    records_root = tmp_path / "records"; events_root = records_root if shared_root else tmp_path / "events"; monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root))
    for path in (records_root / "incomplete", events_root / "incomplete", records_root / ".staged.records.starting", events_root / "staged", records_root / ".committed.records.starting", records_root / "committed", events_root / "committed", events_root / ".other.events.starting", records_root / "recoverable", events_root / ".recoverable.events.starting"): path.mkdir(parents=True, exist_ok=True)
    (records_root / "incomplete" / ".start.pending").write_text("incomplete\n"); (records_root / "committed" / ".start.pending").write_text("committed\n"); (records_root / "committed" / ".start.committed").write_text("committed\n"); (events_root / "committed" / "session_events.jsonl").write_text("{}\n"); (records_root / "recoverable" / ".start.committed").write_text("recoverable\n"); (events_root / ".recoverable.events.starting" / "session_events.jsonl").write_text('{"kind":"session.started"}\n'); SessionService()
    assert {path.name for path in records_root.iterdir()} == {"committed", "recoverable"} and {path.name for path in events_root.iterdir()} == {"committed", "recoverable"} and (records_root / "committed" / ".start.committed").is_file() and (events_root / "recoverable" / "session_events.jsonl").is_file()
@pytest.mark.asyncio
async def test_completed_dispatch_replay_is_ordered_and_finite(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path)
    for order in (1, 2): await record.runner.publish_event_async(EventType.WARNING, {"order": order})
    assert record.dispatcher_task; record.runner.transition_product_session("complete"); await service.registry.update_status(response.session_id, SessionStatus.COMPLETED); await record.event_queue.put(None); await record.dispatcher_task
    replay = service.event_stream(response.session_id, replay=True); assert [await anext(replay), await anext(replay)] == list(record.event_log)
    nonreplay = service.event_stream(response.session_id); snapshot = await asyncio.wait_for(anext(nonreplay), 0.1); assert snapshot.type is EventType.TOOL_RESULT and "todo" in snapshot.payload
    outcomes = await asyncio.wait_for(asyncio.gather(anext(replay), anext(nonreplay), return_exceptions=True), 0.1); assert len(outcomes) == 2 and all(isinstance(item, StopAsyncIteration) for item in outcomes)
