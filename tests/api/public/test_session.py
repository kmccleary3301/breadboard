from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from threading import Barrier, Lock, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from starlette.formparsers import MultiPartParser

from breadboard_engine.api.cli_bridge.app import create_app
import breadboard_engine.api.cli_bridge.app as app_module
from breadboard_engine.api.cli_bridge.models import SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry, TurnRecord
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.api.public import models as public_models
from breadboard_engine.api.public import session as public_session_api
import breadboard_engine.provider_broker as provider_broker
from breadboard_engine.provider.runtimes.testing import MockRuntime
from breadboard.product.cli import session as session_operations
from breadboard.product.runtime import session_store
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.events import KernelEvent, ReplayError, Session


@pytest.fixture(autouse=True)
def _isolated_projection_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    authority_root = tmp_path.parent / f".session-authority-{tmp_path.name}"
    monkeypatch.setenv(
        "BREADBOARD_SESSION_AUTHORITY_ROOT",
        str(authority_root),
    )


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_SESSION_STATE_ROOT", str(tmp_path / "session-state"))
    monkeypatch.setenv(
        "BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "session-events")
    )
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    with TestClient(create_app(include_atp_routes=False)) as test_client:
        yield test_client


def _locked_harness(client: TestClient) -> str:
    assert client.post("/v1/harnesses", json={}).json()["ok"] is True
    result = client.post("/v1/harnesses/daily_driver.v1.yaml/lock").json()
    assert result["ok"] is True
    return result["data"]["path"]


def _stream_records(response) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_session_restore_raises_typed_replay_error_for_invalid_history() -> None:
    digest = "sha256:" + "a" * 64
    events = (
        KernelEvent(
            "typed-replay",
            1,
            "session.started",
            "2026-09-02T00:00:00Z",
            {"effective_lock_hash": digest, "task_hash": digest},
        ),
        KernelEvent(
            "typed-replay",
            3,
            "input.accepted",
            "2026-09-02T00:00:01Z",
            {"content_hash": digest, "attachments": []},
        ),
    )

    with pytest.raises(ReplayError) as captured:
        Session.restore(events)

    assert captured.value.code == "invalid_event_stream"


def test_replay_differential_uses_durable_projection_as_expected_side() -> None:
    lock = EffectiveHarnessLock._from_record(
        {"graph_hash": "sha256:" + "b" * 64}
    )
    session = Session.start(lock, "replay task", session_id="replay-differential")
    session.input("next")
    session.assistant_message("answer")
    session.tool_called("run")
    session.tool_completed("run", False)
    session.request_approval("approval-1", "run command")
    session.resolve_approval("approval-1", "allow")
    session.reconfigure(
        EffectiveHarnessLock._from_record(
            {"graph_hash": "sha256:" + "c" * 64}
        ),
        "operator update",
    )
    session.pause("checkpoint")
    session.resume()
    session.complete("done")
    expected = {
        "schema_version": "bb.session.v1",
        "session_id": "replay-differential",
        "status": "completed",
        "effective_lock_hash": "sha256:" + "c" * 64,
        "task_hash": "sha256:"
        + hashlib.sha256(b"replay task").hexdigest(),
        "event_count": 11,
        "pending_approval": None,
        "terminal_outcome": {"outcome": "completed", "summary": "done"},
    }
    calls = 0

    def build() -> Session:
        nonlocal calls
        calls += 1
        return Session.restore(session.events)

    matched = session_store.replay_differential(expected, build)
    drifted = session_store.replay_differential(
        {**expected, "event_count": 12},
        build,
    )

    assert matched.matches is True
    assert matched.mismatches == ()
    assert matched.session.read_model.as_dict() == expected
    assert drifted.matches is False
    assert drifted.mismatches == ("event_count",)
    assert calls == 2

    with pytest.raises(ReplayError) as captured:
        session_store._session_from_payloads(
            session_store._event_bytes(session),
            json.dumps({**expected, "event_count": 12}).encode(),
            session_id="replay-differential",
        )
    assert captured.value.code == "projection_mismatch"


def test_workspace_identity_uses_physical_directory_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "Workspace"
    second = tmp_path / "workspace"
    first.mkdir()
    if not second.exists():
        second.mkdir()
    physical_metadata = SimpleNamespace(st_dev=41, st_ino=73)
    monkeypatch.setattr(
        session_store.os,
        "stat",
        lambda _path, *, follow_symlinks: physical_metadata,
    )

    assert session_store._workspace_identity(first) == (
        session_store._workspace_identity(second)
    )


def test_session_start_rejects_nonportable_ids_before_storage(
    client: TestClient,
    tmp_path: Path,
) -> None:
    lock_id = _locked_harness(client)
    invalid_ids = ("CON", "a:b", "name.", "name ", "a/b", "a\\b")

    for index, session_id in enumerate(invalid_ids):
        response = client.post(
            "/v1/sessions",
            json={
                "lock_id": lock_id,
                "task": "must reject invalid session identifier",
                "session_id": session_id,
            },
            headers={"Idempotency-Key": f"invalid-session-{index}"},
        )
        assert response.status_code == 422, response.text

    session_root = session_store.session_directory(tmp_path)
    assert all(not (session_root / session_id).exists() for session_id in invalid_ids)


def test_concurrent_case_variant_public_starts_have_one_owner(
    client: TestClient,
) -> None:
    lock_id = _locked_harness(client)
    barrier = Barrier(2)
    result_guard = Lock()
    responses = []

    def start(session_id: str) -> None:
        barrier.wait(timeout=5)
        response = client.post(
            "/v1/sessions",
            json={
                "lock_id": lock_id,
                "task": "case-variant admission",
                "session_id": session_id,
            },
            headers={"Idempotency-Key": f"start-{session_id}"},
        )
        with result_guard:
            responses.append(response)

    workers = [
        Thread(target=start, args=(session_id,))
        for session_id in ("Case-Collision", "case-collision")
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert sorted(response.status_code for response in responses) == [202, 422]
    accepted = next(response for response in responses if response.status_code == 202)
    rejected = next(response for response in responses if response.status_code == 422)
    assert rejected.json()["error"]["error_code"] == "invalid_state"
    session_id = accepted.json()["data"]["session"]["session_id"]
    cancelled = client.post(
        f"/v1/sessions/{session_id}/cancel",
        json={},
        headers={"Idempotency-Key": "cancel-case-collision"},
    )
    assert cancelled.status_code == 202


def test_session_lifecycle_and_resumable_event_stream(
    client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    lock_id = _locked_harness(client)
    started = client.post(
        "/v1/sessions",
        json={
            "lock_id": lock_id,
            "task": "exercise public session",
            "session_id": "session-fixture",
        },
        headers={"Idempotency-Key": "start-fixture"},
    )
    assert started.status_code == 202, started.text
    assert started.json()["data"]["session"]["status"] == "running"
    lock = json.loads((tmp_path / lock_id).read_text(encoding="utf-8"))
    record = client.portal.call(
        client.app.state.session_service.ensure_session,
        "session-fixture",
    )
    assert (
        started.json()["hashes"]["lock"]
        == record.product_session.read_model.effective_lock_hash
    )
    assert started.json()["hashes"]["lock"] != lock["graph_hash"]
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
    monkeypatch.setenv("SESSION_TOKEN", "abc")

    def finish() -> None:
        sleep(0.05)
        headers = {"Idempotency-Key": "input-fixture"}
        assert (
            client.post(
                "/v1/sessions/session-fixture/input",
                json={"content": "continue"},
                headers=headers,
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/v1/sessions/session-fixture/input",
                json={"content": "continue"},
                headers=headers,
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/v1/sessions/session-fixture/cancel",
                json={"reason": "abc"},
                headers={"Idempotency-Key": "cancel-fixture"},
            ).status_code
            == 202
        )

    worker = Thread(target=finish)
    worker.start()
    streamed = client.get("/v1/sessions/session-fixture/events")
    worker.join()
    first = _stream_records(streamed)
    assert '"reason":"abc"' not in streamed.text
    sequences = [event["seq"] for event in first]
    assert len(sequences) >= 2 and sequences == list(range(1, len(sequences) + 1))
    assert all(
        event["schema_version"] == "bb.public_session_event.v1" for event in first
    )
    contract_root = Path(__file__).resolve().parents[3]
    event_schema = json.loads(
        (
            contract_root
            / "contracts/public/schemas/bb.public_session_event.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    lifecycle_schema = json.loads(
        (
            contract_root
            / "contracts/public/schemas/bb.payload.product_session.lifecycle.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    event_registry = Registry().with_resource(
        lifecycle_schema["$id"], Resource.from_contents(lifecycle_schema)
    )
    event_validator = Draft202012Validator(event_schema, registry=event_registry)
    payload_roots = {
        "bb.payload.product_session.lifecycle.v1": (
            contract_root / "contracts/public/schemas"
        ),
    }
    for event in first:
        event_validator.validate(event)
        schema_id = event["payload_schema_version"]
        schema_root = payload_roots.get(
            schema_id,
            Path(__file__).resolve().parents[3] / "contracts/kernel/schemas/payloads",
        )
        payload_schema = json.loads(
            (schema_root / f"{schema_id}.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(payload_schema).validate(event["payload"])
    forged_terminal = {**first[-1], "kind": "session.completed", "payload": {}}
    assert not event_validator.is_valid(forged_terminal)
    assert sum(event["kind"] == "input.accepted" for event in first) == 1
    assert first[-1]["kind"] == "session.canceled"
    assert first[-1]["payload"]["reason"] == "<redacted>"
    assert first[-1]["visibility"]["redaction_state"] == "redacted"
    resumed = _stream_records(
        client.get(
            "/v1/sessions/session-fixture/events",
            headers={"Last-Event-ID": str(first[-2]["seq"])},
        )
    )
    assert resumed == [first[-1]]
    assert (
        client.get("/v1/sessions/session-fixture").json()["data"]["session"]["status"]
        == "canceled"
    )
    assert client.get("/v1/sessions/session-fixture/artifacts").json()["ok"] is True


def test_public_session_events_snapshot_closes_without_live_follow(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    lock_id = _locked_harness(client)
    started = client.post(
        "/v1/sessions",
        json={
            "lock_id": lock_id,
            "task": "snapshot public events",
            "session_id": "snapshot-fixture",
        },
        headers={"Idempotency-Key": "start-snapshot"},
    )
    assert started.status_code == 202, started.text
    batch_reads = 0
    original_read_batch = (
        public_session_api.session_operations.SessionRuntime.read_session_event_batch
    )

    async def read_snapshot_batch(runtime, *args, **kwargs):
        nonlocal batch_reads
        batch_reads += 1
        if batch_reads > 1:
            raise AssertionError("follow=false reread the live event buffer")
        return await original_read_batch(runtime, *args, **kwargs)

    monkeypatch.setattr(
        public_session_api.session_operations.SessionRuntime,
        "read_session_event_batch",
        read_snapshot_batch,
    )
    assert started.json()["data"]["session"]["status"] == "running"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.get,
            "/v1/sessions/snapshot-fixture/events?follow=false",
        )
        streamed = future.result(timeout=2)
    assert streamed.status_code == 200
    records = _stream_records(streamed)
    assert records
    assert records[0]["kind"] == "session.started"
    assert all(event["kind"] != "session.canceled" for event in records)
    assert batch_reads == 1
    cancelled = client.post(
        "/v1/sessions/snapshot-fixture/cancel",
        json={},
        headers={"Idempotency-Key": "cancel-snapshot"},
    )
    assert cancelled.status_code == 202


def test_public_session_event_redaction_preserves_structural_contract(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_id = _locked_harness(client)
    session_id = "secret-session-fixture"
    started = client.post(
        "/v1/sessions",
        json={
            "lock_id": lock_id,
            "task": "redaction contract",
            "session_id": session_id,
        },
        headers={"Idempotency-Key": "redaction-contract"},
    )
    assert started.status_code == 202, started.text
    baseline = _stream_records(
        client.get(f"/v1/sessions/{session_id}/events?follow=false")
    )
    task_hash = baseline[0]["payload"]["task_hash"]
    monkeypatch.setenv("SESSION_TOKEN", "secret")
    monkeypatch.setenv("STRUCTURAL_TOKEN", task_hash[7:11])

    streamed = client.get(f"/v1/sessions/{session_id}/events?follow=false")
    records = _stream_records(streamed)

    assert streamed.status_code == 200
    assert records[0]["session_id"] == session_id
    assert records[0]["kind"] == "session.started"
    assert records[0]["payload"]["task_hash"] == "sha256:" + "0" * 64
    assert records[0]["visibility"]["redaction_state"] == "redacted"
    assert (
        client.post(
            f"/v1/sessions/{session_id}/cancel",
            json={},
            headers={"Idempotency-Key": "cancel-redaction-contract"},
        ).status_code
        == 202
    )


def test_managed_state_terminal_public_session_survives_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    managed_root = tmp_path / "managed-state"
    workspace.mkdir()
    managed_root.mkdir(mode=0o700)
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(managed_root))
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", "managed-public-session-test")
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")

    with TestClient(create_app(include_atp_routes=False)) as first:
        lock_id = _locked_harness(first)
        started = first.post(
            "/v1/sessions",
            json={
                "lock_id": lock_id,
                "task": "persist managed public session",
                "session_id": "managed-session",
            },
            headers={"Idempotency-Key": "start-managed-session"},
        )
        assert started.status_code == 202, started.text
        paused = first.post("/v1/sessions/managed-session/pause")
        assert paused.status_code == 202, paused.text
        uploaded = first.post(
            "/v1/sessions/managed-session/attachments",
            files={"files": ("restart-proof.txt", b"restart artifact bytes\n", "text/plain")},
        )
        assert uploaded.status_code == 200, uploaded.text
        artifact_before = first.get("/v1/sessions/managed-session/artifacts")
        assert artifact_before.status_code == 200, artifact_before.text
        artifact_rows = artifact_before.json()["data"]["artifacts"]
        assert len(artifact_rows) == 1
        artifact_digest = artifact_rows[0]["digest"]
        artifact_bytes_before = first.get(f"/v1/artifacts/{artifact_digest}")
        assert artifact_bytes_before.status_code == 200, artifact_bytes_before.text
        assert artifact_bytes_before.json()["data"]["bytes"] == len(
            b"restart artifact bytes\n"
        )
        resumed = first.post(
            "/v1/sessions/managed-session/resume",
            headers={"Idempotency-Key": "resume-managed-session"},
        )
        assert resumed.status_code == 202, resumed.text
        cancelled = first.post(
            "/v1/sessions/managed-session/cancel",
            json={"reason": "restart persistence test"},
            headers={"Idempotency-Key": "cancel-managed-session"},
        )
        assert cancelled.status_code == 202, cancelled.text
        events_before = _stream_records(
            first.get("/v1/sessions/managed-session/events?follow=false")
        )
        assert events_before and events_before[-1]["kind"] == "session.canceled"

    durable, _metadata = session_store.load_session(workspace, "managed-session")
    assert durable.read_model.status == "canceled"
    with TestClient(create_app(include_atp_routes=False)) as restarted:
        restored = restarted.get("/v1/sessions/managed-session")
        assert restored.status_code == 200, restored.text
        assert restored.json()["data"]["session"]["status"] == "canceled"
        restored_events = _stream_records(
            restarted.get("/v1/sessions/managed-session/events?follow=false")
        )
        assert restored_events == events_before
        restored_artifacts = restarted.get("/v1/sessions/managed-session/artifacts")
        assert restored_artifacts.status_code == 200, restored_artifacts.text
        assert restored_artifacts.json()["data"]["artifacts"] == artifact_rows
        restored_bytes = restarted.get(f"/v1/artifacts/{artifact_digest}")
        assert restored_bytes.status_code == 200, restored_bytes.text
        assert restored_bytes.json()["data"]["bytes"] == len(
            b"restart artifact bytes\n"
        )
def test_public_session_restart_terminalizes_each_unfinished_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "retained-unfinished"
    config = tmp_path / "retained.yaml"
    config.write_text("{}\n", encoding="utf-8")
    _new_durable_session(tmp_path, session_id)
    state_root = tmp_path / "session-state"
    first_registry = SessionRegistry(state_root=state_root)
    record = SessionRecord(
        session_id=session_id,
        status=SessionStatus.RUNNING,
        metadata={
            "config_path": str(config),
            "workspace": str(tmp_path),
            "permission_mode": "configured",
        },
    )
    failed_turn = TurnRecord(
        input_id="input-failed",
        turn_id="turn-failed",
        client_message_id="client-failed",
        content="not retained",
        attachments=(),
        original_disposition="started",
        state="active",
    )
    cancelled_turn = TurnRecord(
        input_id="input-cancelled",
        turn_id="turn-cancelled",
        client_message_id="client-cancelled",
        content="not retained",
        attachments=(),
        original_disposition="started",
        state="active",
        cancellation_requested=True,
        cancellation_reason="user_requested",
    )
    record.turns_by_id = {
        failed_turn.turn_id: failed_turn,
        cancelled_turn.turn_id: cancelled_turn,
    }
    record.active_turn_id = failed_turn.turn_id
    record.event_seq = 10
    record.replay_head_sequence = 10
    record.replay_head_event_id = "event-before-restart"
    first_service = SessionService(registry=first_registry)
    with TestClient(create_app(service=first_service, include_atp_routes=False)) as first:
        first.portal.call(first_registry.create, record)

    monkeypatch.setattr(SessionRunner, "prepare_runtime_config", lambda self: {})
    monkeypatch.setattr(SessionRunner, "schedule_start", lambda self: None)
    monkeypatch.setattr(SessionRunner, "authorize_start", lambda self: None)
    restarted_registry = SessionRegistry(state_root=state_root)
    restarted_service = SessionService(registry=restarted_registry)
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    with TestClient(
        create_app(service=restarted_service, include_atp_routes=False)
    ) as restarted:
        public_get = restarted.get(f"/v1/sessions/{session_id}")
        assert public_get.status_code == 409, public_get.text
        assert public_get.json()["error"]["error_code"] == "invalid_state"
        summary = restarted.get(f"/v1/internal/sessions/{session_id}")
        assert summary.status_code == 200, summary.text
        assert summary.json()["active_turn_id"] is None
        assert summary.json()["turn_admission"] == "idle"
        recovered = restarted.portal.call(restarted_registry.get, session_id)
        assert recovered is not None
        assert recovered.active_turn_id is None
        assert recovered.turns_by_id["turn-cancelled"].state == "cancelled"
        assert (
            recovered.turns_by_id["turn-cancelled"].terminal_outcome
            == "cancelled"
        )
        assert recovered.turns_by_id["turn-failed"].state == "failed"
        assert recovered.turns_by_id["turn-failed"].terminal_outcome == "failed"


def test_c4_daily_driver_completes_with_stable_observations_and_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("BREADBOARD_LEGACY_ROUTES", raising=False)
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_SESSION_STATE_ROOT", str(tmp_path / "session-state"))
    monkeypatch.setenv(
        "BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "session-events")
    )
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    for name in (
        "CODEX_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "MOCK_API_KEY",
    ):
        monkeypatch.setenv(name, f"C4_SENTINEL_{name}")

    def forbidden_provider_broker() -> None:
        raise AssertionError(
            "provider-free profile must not consult the credential broker"
        )

    observed_api_keys: list[str] = []
    create_client = MockRuntime.create_client

    def audited_create_client(
        runtime: MockRuntime,
        api_key: str,
        *,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
    ):
        observed_api_keys.append(api_key)
        return create_client(
            runtime,
            api_key,
            base_url=base_url,
            default_headers=default_headers,
        )

    monkeypatch.setattr(
        provider_broker, "get_provider_broker", forbidden_provider_broker
    )
    monkeypatch.setattr(MockRuntime, "create_client", audited_create_client)

    session_id = "c4-provider-free"
    with TestClient(create_app(include_atp_routes=False)) as first_service:
        lock_id = _locked_harness(first_service)
        started = first_service.post(
            "/v1/sessions",
            json={
                "lock_id": lock_id,
                "task": "Inspect this workspace using a tool, then finish.",
                "session_id": session_id,
            },
            headers={"Idempotency-Key": "c4-start"},
        )
        assert (
            started.status_code == 202
            and started.json()["data"]["session"]["status"] == "running"
        )
        sent = first_service.post(
            f"/v1/sessions/{session_id}/input",
            json={"content": "Continue deterministically."},
            headers={"Idempotency-Key": "c4-input"},
        )
        assert sent.status_code == 202 and sent.json()["ok"] is True
        streamed = first_service.get(f"/v1/sessions/{session_id}/events")
        events = _stream_records(streamed)
        current = first_service.get(f"/v1/sessions/{session_id}")
        artifacts = first_service.get(f"/v1/sessions/{session_id}/artifacts")

    assert current.json()["data"]["session"]["status"] == "completed"
    assert artifacts.status_code == 200
    assert artifacts.json()["data"] == {"session_id": session_id, "artifacts": []}
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert events[0]["kind"] == "session.started"
    assert events[-1]["kind"] == "session.completed"
    kinds = [event["kind"] for event in events]
    assert "input.accepted" in kinds
    assert [
        event["payload"]["tool"] for event in events if event["kind"] == "tool_call"
    ] == [
        "list_dir",
        "apply_unified_patch",
    ]
    assistant_events = [
        event for event in events if event["kind"] == "assistant_message"
    ]
    assert assistant_events
    assert any(
        event["payload"] == {"metadata": {"has_content": True}}
        for event in assistant_events
    )
    assert all(
        set(event["payload"]) == {"metadata"}
        and set(event["payload"]["metadata"]) == {"has_content"}
        and type(event["payload"]["metadata"]["has_content"]) is bool
        for event in assistant_events
    )
    tool_completions = [
        event["payload"]["tool"] for event in events if event["kind"] == "tool_result"
    ]
    assert tool_completions == [
        "list_dir",
        "apply_unified_patch",
        "apply_unified_patch",
        "apply_unified_patch",
        "apply_unified_patch",
    ]
    assert all(
        set(event["payload"]) == {"tool", "error"}
        and type(event["payload"]["error"]) is bool
        for event in events
        if event["kind"] == "tool_result"
    )
    observation_schemas = {
        "assistant_message": "bb.payload.message.assistant.v1",
        "tool_call": "bb.payload.tool.called.v1",
        "tool_result": "bb.payload.tool.completed.v1",
    }
    registry_path = (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / "kernel"
        / "registries"
        / "kernel_event_kinds.v1.json"
    )
    registered = {
        entry["id"]: entry["metadata"]["payload_schema_version"]
        for entry in json.loads(registry_path.read_text(encoding="utf-8"))["entries"]
        if entry["id"] in observation_schemas
    }
    assert registered == observation_schemas
    schema_root = registry_path.parents[1] / "schemas" / "payloads"
    for event in events:
        schema_id = observation_schemas.get(event["kind"])
        if schema_id is None:
            continue
        assert event["payload_schema_version"] == schema_id
        schema_path = schema_root / f"{schema_id}.schema.json"
        Draft202012Validator(
            json.loads(schema_path.read_text(encoding="utf-8"))
        ).validate(event["payload"])
    assert observed_api_keys and set(observed_api_keys) == {"mock"}

    event_path = (
        tmp_path / ".breadboard" / "sessions" / session_id / "session_events.jsonl"
    )
    events_before_reads = event_path.read_bytes()
    with TestClient(create_app(include_atp_routes=False)) as restarted_service:
        restored = restarted_service.get(f"/v1/sessions/{session_id}")
        restored_events = _stream_records(
            restarted_service.get(f"/v1/sessions/{session_id}/events")
        )
        restored_artifacts = restarted_service.get(
            f"/v1/sessions/{session_id}/artifacts"
        )
        restored_listing = restarted_service.get("/v1/sessions")
        described = restarted_service.get("/v1/system").json()
        assert restored.status_code == 200
        assert restored.json()["data"]["session"] == current.json()["data"]["session"]
        assert restored_events == events
        assert restored_artifacts.json()["record_refs"] == [
            f".breadboard/sessions/{session_id}/session_events.jsonl"
        ]
        profile = described["data"]["default_profile"]
        assert profile["profile_id"] == "daily_driver.v1"
        assert profile["effective_lock_hash"] == described["hashes"]["profile"]
        assert restored_listing.status_code == 200
        assert restored_listing.json()["data"]["sessions"] == [
            {
                "session_id": session_id,
                "status": "completed",
                "event_count": len(events),
            }
        ]
        assert event_path.read_bytes() == events_before_reads
        assert (
            restored.json()["data"]["session"]["effective_lock_hash"]
            != profile["effective_lock_hash"]
        )

        lock_path = tmp_path / lock_id
        corrupt_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        corrupt_lock["graph_hash"] = "sha256:" + "0" * 64
        lock_path.write_text(json.dumps(corrupt_lock), encoding="utf-8")
        rejected = restarted_service.post(
            "/v1/sessions",
            json={
                "lock_id": lock_id,
                "task": "must reject corrupt lock",
                "session_id": "c4-corrupt-lock",
            },
            headers={"Idempotency-Key": "c4-corrupt"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["error_code"] == "lock_drift"

    persisted = (
        tmp_path / ".breadboard" / "sessions" / session_id / "session_events.jsonl"
    ).read_text(encoding="utf-8")
    assert "C4_SENTINEL" not in persisted


def test_session_invalid_state_is_stable_and_secret_free(
    client: TestClient, tmp_path: Path
) -> None:
    lock_id = _locked_harness(client)
    payload = {
        "lock_id": lock_id,
        "task": "secret-free error",
        "session_id": "duplicate",
    }
    assert (
        client.post(
            "/v1/sessions", json=payload, headers={"Idempotency-Key": "first"}
        ).status_code
        == 202
    )
    duplicate = client.post(
        "/v1/sessions", json=payload, headers={"Idempotency-Key": "second"}
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["error_code"] == "invalid_state"
    assert str(tmp_path) not in duplicate.text
    malformed = client.post("/v1/sessions", json={})
    assert (
        malformed.status_code == 422
        and malformed.json()["schema_version"] == "bb.cli.result.v1"
    )
    assert malformed.json()["error"]["schema_version"] == "bb.problem.v1"
    missing = client.get("/v1/sessions/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["error_code"] == "path_unavailable"
    stray_approval = client.post(
        "/v1/sessions/duplicate/approve",
        json={"request_id": "not-pending", "decision": "allow"},
        headers={"Idempotency-Key": "stray-approval"},
    )
    assert stray_approval.status_code == 422
    assert (
        client.get("/v1/sessions/duplicate").json()["data"]["session"]["status"]
        == "running"
    )
    inactive = client.post(
        "/v1/sessions/duplicate/cancel",
        json={},
        headers={"Idempotency-Key": "cancel-duplicate"},
    )
    assert inactive.status_code == 202
    rejected_input = client.post(
        "/v1/sessions/duplicate/input",
        json={"content": "late"},
        headers={"Idempotency-Key": "late-input"},
    )
    assert rejected_input.status_code == 409
    assert rejected_input.json()["error"]["error_code"] == "invalid_state"
    traversal = client.post(
        "/v1/sessions",
        json={"lock_id": lock_id, "task": "must stay contained", "session_id": ".."},
        headers={"Idempotency-Key": "traversal"},
    )
    assert traversal.status_code == 422
    with pytest.raises(ValueError, match="portable identifier"):
        session_store.load_session(tmp_path, "..")


def test_durable_session_fallback_rejects_symlinked_metadata_root(
    monkeypatch, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    workspace = tmp_path / "workspace"
    event_directory = outside / ".breadboard" / "sessions" / "symlinked-session"
    event_directory.mkdir(parents=True)
    events = [
        {
            "schema_version": "bb.session_event.v1",
            "session_id": "symlinked-session",
            "sequence": 1,
            "kind": "session.started",
            "occurred_at": "2026-08-19T00:00:00Z",
            "payload": {
                "effective_lock_hash": "sha256:" + "1" * 64,
                "task_hash": "sha256:" + "2" * 64,
            },
        },
        {
            "schema_version": "bb.session_event.v1",
            "session_id": "symlinked-session",
            "sequence": 2,
            "kind": "session.completed",
            "occurred_at": "2026-08-19T00:00:01Z",
            "payload": {
                "outcome": "completed",
                "summary": "cross-boundary-secret",
            },
        },
    ]
    (event_directory / "session_events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    workspace.mkdir()
    (workspace / ".breadboard").symlink_to(
        outside / ".breadboard", target_is_directory=True
    )
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    with TestClient(create_app(include_atp_routes=False)) as test_client:
        response = test_client.get("/v1/sessions/symlinked-session")
        assert response.status_code == 404
        assert "cross-boundary-secret" not in response.text
        events_response = test_client.get("/v1/sessions/symlinked-session/events")
        assert events_response.status_code == 404
        assert "cross-boundary-secret" not in events_response.text
    cli_args = SimpleNamespace(workspace=workspace, SESSION_ID="symlinked-session")
    for operation in (
        session_operations.get,
        session_operations.events,
        session_operations.artifacts,
    ):
        result = operation(cli_args)
        assert result.ok is False
        assert "cross-boundary-secret" not in json.dumps(
            result.as_dict(), sort_keys=True
        )
    listing = session_operations.list_sessions(SimpleNamespace(workspace=workspace))
    assert "cross-boundary-secret" not in json.dumps(listing.as_dict(), sort_keys=True)


def test_orphaned_running_session_is_not_exposed_as_resumable(
    monkeypatch, tmp_path: Path
) -> None:
    session_id = "orphaned-running"
    event_directory = tmp_path / ".breadboard" / "sessions" / session_id
    event_directory.mkdir(parents=True)
    started = {
        "schema_version": "bb.session_event.v1",
        "session_id": session_id,
        "sequence": 1,
        "kind": "session.started",
        "occurred_at": "2026-08-19T00:00:00Z",
        "payload": {
            "effective_lock_hash": "sha256:" + "1" * 64,
            "task_hash": "sha256:" + "2" * 64,
        },
    }
    (event_directory / "session_events.jsonl").write_text(
        json.dumps(started, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    with TestClient(create_app(include_atp_routes=False)) as test_client:
        listing = test_client.get("/v1/sessions")
        assert listing.status_code == 200
        assert listing.json()["data"]["sessions"] == []
        requests = (
            test_client.get(f"/v1/sessions/{session_id}"),
            test_client.get(f"/v1/sessions/{session_id}/events"),
            test_client.get(f"/v1/sessions/{session_id}/artifacts"),
            test_client.post(
                f"/v1/sessions/{session_id}/input",
                json={"content": "cannot continue"},
                headers={"Idempotency-Key": "orphaned-input"},
            ),
            test_client.post(
                f"/v1/sessions/{session_id}/approve",
                json={"request_id": "approval-1", "decision": "deny"},
                headers={"Idempotency-Key": "orphaned-approve"},
            ),
            test_client.post(
                f"/v1/sessions/{session_id}/resume",
                headers={"Idempotency-Key": "orphaned-resume"},
            ),
            test_client.post(
                f"/v1/sessions/{session_id}/cancel",
                json={"reason": "cannot continue"},
                headers={"Idempotency-Key": "orphaned-cancel"},
            ),
        )
        for response in requests:
            assert response.status_code == 409
            assert response.json()["error"]["error_code"] == "invalid_state"
            assert response.json()["error"]["message"] == (
                "session runtime state is unavailable after service restart"
            )


def test_durable_session_fallback_rejects_symlinked_event_file(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    event_directory = workspace / ".breadboard" / "sessions" / "symlinked-file"
    event_directory.mkdir(parents=True)
    external = tmp_path / "external-events.jsonl"
    events = [
        {
            "schema_version": "bb.session_event.v1",
            "session_id": "symlinked-file",
            "sequence": 1,
            "kind": "session.started",
            "occurred_at": "2026-08-19T00:00:00Z",
            "payload": {
                "effective_lock_hash": "sha256:" + "1" * 64,
                "task_hash": "sha256:" + "2" * 64,
                "debug_secret": "linked-file-secret",
            },
        },
    ]
    external.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    (event_directory / "session_events.jsonl").symlink_to(external)
    original_external = external.read_bytes()
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    with TestClient(create_app(include_atp_routes=False)) as test_client:
        response = test_client.get("/v1/sessions/symlinked-file")
        assert response.status_code == 404
        assert "linked-file-secret" not in response.text
    cli_args = SimpleNamespace(
        workspace=workspace, SESSION_ID="symlinked-file", reason="must stay contained"
    )
    for operation in (
        session_operations.get,
        session_operations.events,
        session_operations.artifacts,
        session_operations.cancel,
    ):
        result = operation(cli_args)
        assert result.ok is False
        assert "linked-file-secret" not in json.dumps(result.as_dict(), sort_keys=True)
    assert external.read_bytes() == original_external


def test_session_start_preserves_lock_drift_error(
    client: TestClient, tmp_path: Path
) -> None:
    lock_id = _locked_harness(client)
    harness = tmp_path / "daily_driver.v1.yaml"
    original = harness.read_text()
    changed = original.replace("name: coding", "name: changed", 1).replace(
        "mode: coding", "mode: changed", 1
    )
    assert changed != original
    harness.write_text(changed)
    response = client.post(
        "/v1/sessions",
        json={"lock_id": lock_id, "task": "must reject drift"},
        headers={"Idempotency-Key": "drifted-start"},
    )
    assert response.status_code == 409
    assert response.json()["command"] == ["session", "start"]
    assert response.json()["error"]["error_code"] == "lock_drift"


def test_session_start_dispatches_task_to_execution_service(client: TestClient) -> None:
    lock_id = _locked_harness(client)
    payload = {
        "lock_id": lock_id,
        "task": "complete the execution probe",
        "session_id": "execution-fixture",
    }
    started = client.post(
        "/v1/sessions", json=payload, headers={"Idempotency-Key": "execution-start"}
    )
    assert started.status_code == 202
    deadline = monotonic() + 10
    record = client.portal.call(
        client.app.state.session_service.ensure_session, "execution-fixture"
    )
    while not record.event_log and monotonic() < deadline:
        sleep(0.05)
        record = client.portal.call(
            client.app.state.session_service.ensure_session, "execution-fixture"
        )
    assert record.event_log and record.event_log[0].asdict()["type"] == "skills_catalog"
    assert (
        client.post(
            "/v1/sessions/execution-fixture/cancel",
            json={},
            headers={"Idempotency-Key": "execution-cancel"},
        ).status_code
        == 202
    )
    records = _stream_records(client.get("/v1/sessions/execution-fixture/events"))
    assert records[0]["kind"] == "session.started"
    assert records[-1]["kind"] == "session.canceled"


def test_configured_prompt_permissions_reach_public_approval(
    client: TestClient,
) -> None:
    assert (
        client.post("/v1/harnesses", json={"directory": "approval"}).status_code == 200
    )
    harness_path = "approval/daily_driver.v1.yaml"
    definition = client.get(f"/v1/harnesses/{harness_path}").json()["data"][
        "definition"
    ]
    definition["permissions"]["edit"] = {"default": "ask"}
    assert (
        client.put(
            f"/v1/harnesses/{harness_path}",
            json={"definition": definition},
        ).status_code
        == 200
    )
    lock_id = client.post(f"/v1/harnesses/{harness_path}/lock").json()["data"]["path"]
    started = client.post(
        "/v1/sessions",
        json={
            "lock_id": lock_id,
            "task": "Reach a configured edit approval.",
            "session_id": "approval-fixture",
        },
        headers={"Idempotency-Key": "approval-start"},
    )
    assert started.status_code == 202

    deadline = monotonic() + 3
    while monotonic() < deadline:
        session = client.get("/v1/sessions/approval-fixture").json()["data"]["session"]
        if session["status"] != "running":
            break
        sleep(0.025)
    assert session["status"] == "awaiting_approval"
    assert isinstance(session["pending_approval"], str)
    resolved = client.post(
        "/v1/sessions/approval-fixture/approve",
        json={"request_id": session["pending_approval"], "decision": "deny"},
        headers={"Idempotency-Key": "approval-deny"},
    )
    assert resolved.status_code == 202
    deadline = monotonic() + 3
    while monotonic() < deadline:
        session = client.get("/v1/sessions/approval-fixture").json()["data"]["session"]
        if session["status"] in {"completed", "failed"}:
            break
        sleep(0.025)
    assert session["status"] == "completed"


def test_runtime_setup_pause_resume_and_artifact_install(
    client: TestClient,
) -> None:
    lock_id = _locked_harness(client)
    started = client.post(
        "/v1/sessions",
        json={
            "lock_id": lock_id,
            "task": "Exercise supported runtime setup.",
            "session_id": "runtime-setup",
        },
        headers={"Idempotency-Key": "runtime-setup-start"},
    )
    assert started.status_code == 202

    paused = client.post("/v1/sessions/runtime-setup/pause")
    try:
        assert paused.status_code == 202, paused.text
        assert paused.json()["detail"] == {"status": "ok", "paused": True}
        uploaded = client.post(
            "/v1/sessions/runtime-setup/attachments",
            data={"metadata": json.dumps({"source": "public-runtime-setup"})},
            files={"files": ("fixture.txt", b"real artifact bytes\n", "text/plain")},
        )
        assert uploaded.status_code == 200, uploaded.text
        attachment = uploaded.json()["attachments"][0]
        assert attachment == {
            "id": attachment["id"],
            "filename": "fixture.txt",
            "mime": "text/plain",
            "size_bytes": len(b"real artifact bytes\n"),
        }

        session_artifacts = client.get("/v1/sessions/runtime-setup/artifacts").json()[
            "data"
        ]["artifacts"]
        assert len(session_artifacts) == 1
        artifact = session_artifacts[0]
        assert artifact["name"] == attachment["id"]
        assert artifact["media_type"] == "text/plain"
        assert artifact["size_bytes"] == len(b"real artifact bytes\n")
        artifact_id = artifact["digest"]

        listed = client.get("/v1/artifacts").json()["data"]
        assert listed["count"] >= 1
        assert artifact_id in {candidate["digest"] for candidate in listed["artifacts"]}
        assert client.get(f"/v1/artifacts/{artifact_id}").json()["ok"] is True
        verified = client.post(f"/v1/artifacts/{artifact_id}/verify").json()
        assert verified["ok"] is True
        assert verified["data"]["verified"] is True

        resumed = client.post(
            "/v1/sessions/runtime-setup/resume",
            headers={"Idempotency-Key": "runtime-setup-resume"},
        )
        assert resumed.status_code == 202
        assert resumed.json()["data"]["session"]["status"] == "running"

        assert (
            client.post(
                "/v1/sessions/runtime-setup/command",
                json={"command": "pause"},
            ).status_code
            == 404
        )
        openapi_paths = client.get("/openapi.json").json()["paths"]
        assert "/v1/sessions/{session_id}/pause" not in openapi_paths
        assert "/v1/sessions/{session_id}/attachments" not in openapi_paths
    finally:
        session = client.get("/v1/sessions/runtime-setup").json()["data"]["session"]
        if session["status"] == "paused":
            client.post(
                "/v1/sessions/runtime-setup/resume",
                headers={"Idempotency-Key": "runtime-setup-final-resume"},
            )
            session = client.get("/v1/sessions/runtime-setup").json()["data"]["session"]
        if session["status"] in {"running", "awaiting_approval"}:
            client.post(
                "/v1/sessions/runtime-setup/cancel",
                json={},
                headers={"Idempotency-Key": "runtime-setup-cleanup"},
            )


def test_runtime_setup_routes_require_execute_capability(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    multipart_parsed = False

    async def parse_forbidden(_parser) -> None:
        nonlocal multipart_parsed
        multipart_parsed = True
        raise AssertionError("unauthorized multipart body was parsed")

    monkeypatch.setattr(MultiPartParser, "parse", parse_forbidden)
    monkeypatch.setattr(
        app_module,
        "_public_request_principal",
        lambda _request, _required_token: public_models.PublicPrincipal("anonymous"),
    )
    denied = (
        client.post("/v1/sessions/missing/pause"),
        client.post(
            "/v1/sessions/missing/attachments",
            files={"files": ("fixture.txt", b"content", "text/plain")},
        ),
    )
    assert all(response.status_code == 403 for response in denied), [
        response.text for response in denied
    ]
    assert all(
        response.json()["error"]["error_code"] == "capability_required"
        for response in denied
    )
    assert multipart_parsed is False


@pytest.mark.parametrize(
    ("root_path", "request_path"),
    [
        ("/prefix", "/prefix/v1/sessions/missing/attachments"),
        ("/prefix/", "/prefix//v1/sessions/missing/attachments"),
    ],
)
def test_runtime_setup_guard_honors_mounted_root_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    root_path: str,
    request_path: str,
) -> None:
    multipart_parsed = False

    async def parse_forbidden(_parser) -> None:
        nonlocal multipart_parsed
        multipart_parsed = True
        raise AssertionError("mounted unauthorized multipart body was parsed")

    monkeypatch.setattr(MultiPartParser, "parse", parse_forbidden)
    monkeypatch.setattr(
        app_module,
        "_public_request_principal",
        lambda _request, _required_token: public_models.PublicPrincipal("anonymous"),
    )
    with TestClient(
        create_app(include_atp_routes=False),
        root_path=root_path,
    ) as mounted_client:
        denied = mounted_client.post(
            request_path,
            files={"files": ("fixture.txt", b"content", "text/plain")},
        )
    assert denied.status_code == 403
    assert denied.json()["error"]["error_code"] == "capability_required"
    assert multipart_parsed is False


def test_runtime_setup_routes_reject_cross_site_loopback_before_lookup(
    client: TestClient,
) -> None:
    pause = client.post(
        "/v1/sessions/missing/pause",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    upload = client.post(
        "/v1/sessions/missing/attachments",
        headers={"Origin": "https://attacker.example"},
        files={"files": ("fixture.txt", b"content", "text/plain")},
    )
    assert pause.status_code == 403
    assert upload.status_code == 403
    assert pause.json()["error"]["error_code"] == "forbidden"
    assert upload.json()["error"]["error_code"] == "forbidden"


def _new_durable_session(workspace: Path, session_id: str) -> None:
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64})
    session_store.create_session(
        workspace,
        Session.start(lock, "durability test", session_id=session_id),
    )


def test_durable_mutations_are_serialized_and_contiguous(tmp_path: Path) -> None:
    session_id = "serialized"
    _new_durable_session(tmp_path, session_id)
    barrier = Barrier(2)
    callback_guard = Lock()
    active = 0
    maximum_active = 0
    errors: list[BaseException] = []

    def mutation(session: Session, content: str) -> object:
        nonlocal active, maximum_active
        with callback_guard:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            sleep(0.02)
            return session.input(content)
        finally:
            with callback_guard:
                active -= 1

    def worker(content: str) -> None:
        try:
            barrier.wait(timeout=5)
            session_store.mutate_session(
                tmp_path,
                session_id,
                lambda session: mutation(session, content),
            )
        except BaseException as error:
            errors.append(error)

    workers = [
        Thread(target=worker, args=("concurrent input A",)),
        Thread(target=worker, args=("concurrent input B",)),
    ]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join(timeout=5)
    assert all(not worker_thread.is_alive() for worker_thread in workers)
    assert not errors
    assert maximum_active == 1
    restored, _ = session_store.load_session(tmp_path, session_id)
    assert [event.sequence for event in restored.events] == [1, 2, 3]
    input_hashes = {
        event.payload["content_hash"]
        for event in restored.events
        if event.kind == "input.accepted"
    }
    assert len(input_hashes) == 2


@pytest.mark.parametrize("race_error", [FileNotFoundError, NotADirectoryError])
@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-entry race required")
def test_session_names_skips_disappearing_entry_races(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    race_error: type[OSError],
) -> None:
    _new_durable_session(tmp_path, "stable")
    disappearing = session_store.session_directory(tmp_path) / "disappearing"
    disappearing.mkdir()
    original_stat = session_store.os.stat

    def race_stat(path, *args, **kwargs):
        if path == "disappearing":
            raise race_error(2, "entry disappeared")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(session_store.os, "stat", race_stat)
    assert session_store.session_names(tmp_path) == ["stable"]


def test_large_session_history_remains_mutable_after_intent_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_id = "large-history"
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64})
    started = Session.start(lock, "large history", session_id=session_id)
    events = list(started.events)
    for sequence in range(2, 60_000):
        events.append(
            KernelEvent.create(
                session_id,
                sequence,
                "assistant_message",
                str(sequence),
                {"metadata": {"has_content": False}},
            )
        )
    large = Session.restore(events)
    session_store.create_session(tmp_path, large)
    event_path = session_store.session_event_path(tmp_path, session_id)
    assert event_path.stat().st_size > 8 * 1024 * 1024

    original_write = session_store.AnchoredStorage.write_at
    failed = False

    def fail_metadata(parent: int, name: str, content: bytes) -> None:
        nonlocal failed
        if name == "session.json" and not failed:
            failed = True
            raise OSError("injected metadata failure")
        original_write(parent, name, content)

    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(fail_metadata),
    )
    with pytest.raises(OSError, match="injected metadata failure"):
        session_store.mutate_session(
            tmp_path,
            session_id,
            lambda session: session.input("recover large history"),
        )
    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(original_write),
    )

    recovered, _ = session_store.load_session(tmp_path, session_id)
    assert recovered.read_model.event_count == len(events) + 1
    session_store.mutate_session(
        tmp_path,
        session_id,
        lambda session: session.input("control mutation"),
    )
    controlled, _ = session_store.load_session(tmp_path, session_id)
    assert controlled.read_model.event_count == len(events) + 2


def test_pending_session_intent_repairs_split_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_id = "repairable"
    _new_durable_session(tmp_path, session_id)
    original_write = session_store.AnchoredStorage.write_at
    failed = False

    def fail_metadata(parent: int, name: str, content: bytes) -> None:
        nonlocal failed
        if name == "session.json" and not failed:
            failed = True
            raise OSError("injected metadata failure")
        original_write(parent, name, content)

    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(fail_metadata),
    )
    with pytest.raises(OSError, match="injected metadata failure"):
        session_store.mutate_session(
            tmp_path,
            session_id,
            lambda session: session.input("repair me"),
        )
    intent = tmp_path / ".breadboard" / "sessions" / session_id / ".session.intent.json"
    assert intent.is_file()
    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(original_write),
    )
    restored, _ = session_store.load_session(tmp_path, session_id)
    assert restored.read_model.event_count == 2
    assert not intent.exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_oversized_sparse_session_intent_is_rejected_before_reads_or_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_id = "oversized-intent"
    _new_durable_session(tmp_path, session_id)
    event_path = session_store.session_event_path(tmp_path, session_id)
    metadata_path = session_store.session_metadata_path(tmp_path, session_id)
    before = event_path.read_bytes(), metadata_path.read_bytes()
    intent = event_path.parent / ".session.intent.json"
    with intent.open("wb") as stream:
        stream.seek(session_store._MAX_TRANSACTION_INTENT_BYTES)
        stream.write(b"x")

    def unbounded_read_forbidden(*_args, **_kwargs):
        raise AssertionError("intent recovery used unbounded read")

    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "read_at",
        staticmethod(unbounded_read_forbidden),
    )
    with pytest.raises(ValueError, match="oversized"):
        session_store.load_session(tmp_path, session_id)
    assert (event_path.read_bytes(), metadata_path.read_bytes()) == before


def test_session_intent_oversized_projection_is_rejected_before_staged_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "oversized-projection"
    _new_durable_session(tmp_path, session_id)
    event_path = session_store.session_event_path(tmp_path, session_id)
    event_name = event_path.name
    metadata_name = session_store.session_metadata_path(tmp_path, session_id).name
    event_stage_name, metadata_stage_name = session_store._stage_names(
        event_name,
        metadata_name,
    )
    intent = event_path.parent / ".session.intent.json"
    intent.write_text(
        json.dumps(
            {
                "schema_version": session_store._TRANSACTION_SCHEMA,
                "session_id": session_id,
                "event_name": event_name,
                "metadata_name": metadata_name,
                "event_stage_name": event_stage_name,
                "metadata_stage_name": metadata_stage_name,
                "event_size": session_store._MAX_SESSION_PROJECTION_BYTES + 1,
                "metadata_size": 0,
                "event_sha256": "sha256:" + "0" * 64,
                "metadata_sha256": "sha256:" + "0" * 64,
            }
        ),
        encoding="ascii",
    )

    def staged_read_forbidden(*_args, **_kwargs):
        raise AssertionError("oversized projection reached a staged read")

    monkeypatch.setattr(
        session_store,
        "_read_posix_staged_file",
        staged_read_forbidden,
    )
    monkeypatch.setattr(
        session_store,
        "_read_windows_staged_file",
        staged_read_forbidden,
    )
    with pytest.raises(ValueError, match="projection is oversized"):
        session_store.load_session(tmp_path, session_id)


def test_explicit_local_bootstrap_preserves_pre_authority_terminal_session(
    tmp_path: Path,
) -> None:
    session_id = "pre-authority-terminal"
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64})
    legacy = Session.start(lock, "legacy task", session_id=session_id)
    legacy.complete("legacy result")
    event_path = session_store.session_event_path(tmp_path, session_id)
    metadata_path = session_store.session_metadata_path(tmp_path, session_id)
    event_path.parent.mkdir(parents=True)
    event_path.write_bytes(session_store._event_bytes(legacy))
    metadata_path.write_bytes(session_store._metadata_bytes(legacy))

    with pytest.raises(ValueError, match="projection authority is missing"):
        session_store.load_session(tmp_path, session_id)

    restored, restored_path = session_store.bootstrap_local_session_authority(
        tmp_path,
        session_id,
    )
    assert restored.read_model.status == "completed"
    assert restored_path == event_path
    loaded, _ = session_store.load_session(tmp_path, session_id)
    assert loaded.read_model == restored.read_model


def test_create_retry_cleans_single_orphaned_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "partial-orphan"
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "b" * 64})
    durable = Session.start(lock, "partial stage", session_id=session_id)
    original_write = session_store.AnchoredStorage.write_at
    failed = False

    def fail_metadata_stage(parent: int, name: str, content: bytes) -> None:
        nonlocal failed
        if name == ".session.json.stage" and not failed:
            failed = True
            raise OSError("injected metadata stage failure")
        original_write(parent, name, content)

    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(fail_metadata_stage),
    )
    with pytest.raises(OSError, match="injected metadata stage failure"):
        session_store.create_session(tmp_path, durable)
    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(original_write),
    )

    created, _ = session_store.create_session(tmp_path, durable)
    assert created.read_model.session_id == session_id
    assert not list(tmp_path.rglob("*.stage"))


def test_load_recovers_complete_orphaned_stages_without_intent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "complete-orphan"
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "c" * 64})
    durable = Session.start(lock, "complete stage", session_id=session_id)
    original_write = session_store.AnchoredStorage.write_at
    failed = False

    def fail_intent(parent: int, name: str, content: bytes) -> None:
        nonlocal failed
        if name == ".session.intent.json" and not failed:
            failed = True
            raise OSError("injected intent failure")
        original_write(parent, name, content)

    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(fail_intent),
    )
    with pytest.raises(OSError, match="injected intent failure"):
        session_store.create_session(tmp_path, durable)
    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(original_write),
    )

    restored, _ = session_store.load_session(tmp_path, session_id)
    assert restored.read_model == durable.read_model
    assert not list(tmp_path.rglob("*.stage"))


def test_session_artifact_manifest_requires_private_authority(tmp_path: Path) -> None:
    session_id = "artifact-authority"
    _new_durable_session(tmp_path, session_id)
    artifact_row = {
        "name": "proof.txt",
        "digest": "sha256:" + "b" * 64,
        "size": 5,
    }
    manifest = {
        "schema_version": "bb.artifact_manifest.v1",
        "manifest_id": "artifact_manifest:" + "c" * 64,
        "session_id": session_id,
        "artifacts": [artifact_row],
    }
    body = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(body).hexdigest()
    name = f"{session_id}.{digest}.json"
    manifest_root = tmp_path / ".breadboard" / "artifacts" / "manifests"
    manifest_root.mkdir(parents=True)
    (manifest_root / name).write_bytes(body)

    assert session_store.session_artifact_rows(tmp_path, session_id) == []

    session_store.authorize_session_artifact_manifest(
        tmp_path,
        session_id,
        name,
    )
    assert session_store.session_artifact_rows(tmp_path, session_id) == [artifact_row]


def test_session_recovery_rejects_model_forged_projection(
    tmp_path: Path,
) -> None:
    session_id = "forged-projection"
    _new_durable_session(tmp_path, session_id)
    forged, event_path = session_store.load_session(tmp_path, session_id)
    forged.complete("forged success")
    event_payload = session_store._event_bytes(forged)
    metadata_payload = session_store._metadata_bytes(forged)
    event_name = event_path.name
    metadata_name = session_store.session_metadata_path(tmp_path, session_id).name
    event_stage_name, metadata_stage_name = session_store._stage_names(
        event_name,
        metadata_name,
    )
    parent = event_path.parent
    (parent / event_stage_name).write_bytes(event_payload)
    (parent / metadata_stage_name).write_bytes(metadata_payload)
    (parent / ".session.intent.json").write_bytes(
        session_store._intent_bytes(
            session_id,
            event_name,
            metadata_name,
            event_stage_name,
            metadata_stage_name,
            len(event_payload),
            len(metadata_payload),
            session_store._digest(event_payload),
            session_store._digest(metadata_payload),
        )
    )

    with pytest.raises(ValueError, match="projection authority mismatch"):
        session_store.load_session(tmp_path, session_id)
    assert b"forged success" not in event_path.read_bytes()


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo"),
    reason="POSIX FIFO semantics required",
)
def test_session_intent_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    session_id = "fifo-intent"
    _new_durable_session(tmp_path, session_id)
    event_path = session_store.session_event_path(tmp_path, session_id)
    metadata_path = session_store.session_metadata_path(tmp_path, session_id)
    before = event_path.read_bytes(), metadata_path.read_bytes()
    intent = event_path.parent / ".session.intent.json"
    os.mkfifo(intent)
    result: dict[str, BaseException] = {}

    def load() -> None:
        try:
            session_store.load_session(tmp_path, session_id)
        except BaseException as error:
            result["error"] = error

    worker = Thread(target=load, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    error = result.get("error")
    assert isinstance(error, FileNotFoundError)
    assert error.__cause__ is not None
    assert "unsafe session intent" in str(error.__cause__)
    assert (event_path.read_bytes(), metadata_path.read_bytes()) == before


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo"),
    reason="POSIX FIFO semantics required",
)
def test_canonical_session_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    session_id = "fifo-canonical"
    _new_durable_session(tmp_path, session_id)
    event_path = session_store.session_event_path(tmp_path, session_id)
    event_path.unlink()
    os.mkfifo(event_path)
    result: dict[str, BaseException] = {}

    def load() -> None:
        try:
            session_store.load_session(tmp_path, session_id)
        except BaseException as error:
            result["error"] = error

    worker = Thread(target=load, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert isinstance(result.get("error"), FileNotFoundError)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "event_sha256",
            "sha256:" + "0" * 64,
            "projection authority mismatch",
        ),
        ("session_id", "other-session", "mismatched"),
    ],
)
def test_corrupt_session_intent_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    session_id = "corrupt-intent"
    _new_durable_session(tmp_path, session_id)
    original_write = session_store.AnchoredStorage.write_at

    def fail_metadata(parent: int, name: str, content: bytes) -> None:
        if name == "session.json":
            raise OSError("injected metadata failure")
        original_write(parent, name, content)

    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(fail_metadata),
    )
    with pytest.raises(OSError):
        session_store.mutate_session(
            tmp_path,
            session_id,
            lambda session: session.input("leave intent"),
        )
    intent = tmp_path / ".breadboard" / "sessions" / session_id / ".session.intent.json"
    event_path = session_store.session_event_path(tmp_path, session_id)
    metadata_path = session_store.session_metadata_path(tmp_path, session_id)
    before = event_path.read_bytes(), metadata_path.read_bytes()
    corrupt = json.loads(intent.read_text(encoding="utf-8"))
    corrupt[field] = value
    intent.write_text(json.dumps(corrupt), encoding="utf-8")
    monkeypatch.setattr(
        session_store.AnchoredStorage,
        "write_at",
        staticmethod(original_write),
    )
    with pytest.raises(ValueError, match=message):
        session_store.load_session(tmp_path, session_id)
    assert (event_path.read_bytes(), metadata_path.read_bytes()) == before


def test_session_intent_rejects_foreign_projection_identity(tmp_path: Path) -> None:
    session_id = "intent-owner"
    _new_durable_session(tmp_path, session_id)
    event_path = session_store.session_event_path(tmp_path, session_id)
    metadata_path = session_store.session_metadata_path(tmp_path, session_id)
    before = event_path.read_bytes(), metadata_path.read_bytes()
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "c" * 64})
    foreign = Session.start(lock, "foreign", session_id="foreign-session")
    event_payload = session_store._event_bytes(foreign)
    metadata_payload = session_store._metadata_bytes(foreign)
    event_stage_name, metadata_stage_name = session_store._stage_names(
        "session_events.jsonl",
        "session.json",
    )
    (event_path.parent / event_stage_name).write_bytes(event_payload)
    (event_path.parent / metadata_stage_name).write_bytes(metadata_payload)
    intent = event_path.parent / ".session.intent.json"
    intent.write_bytes(
        session_store._intent_bytes(
            session_id,
            "session_events.jsonl",
            "session.json",
            event_stage_name,
            metadata_stage_name,
            len(event_payload),
            len(metadata_payload),
            session_store._digest(event_payload),
            session_store._digest(metadata_payload),
        )
    )

    with pytest.raises(ValueError, match="projection authority mismatch"):
        session_store.load_session(tmp_path, session_id)
    assert (event_path.read_bytes(), metadata_path.read_bytes()) == before


def test_session_name_inventory_excludes_transaction_locks(tmp_path: Path) -> None:
    session_id = "listed-session"
    _new_durable_session(tmp_path, session_id)
    session_store.load_session(tmp_path, session_id)

    assert session_store.session_names(tmp_path) == [session_id]
    assert not list((tmp_path / ".breadboard" / "sessions").glob("*.lock"))
    authority_root = Path(os.environ["BREADBOARD_SESSION_AUTHORITY_ROOT"])
    assert len(list((authority_root / "locks").glob("*.lock"))) == 1


def test_concurrent_durable_create_has_one_winner(tmp_path: Path) -> None:
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "b" * 64})
    sessions = [
        Session.start(lock, "duplicate create", session_id="duplicate-create")
        for _ in range(2)
    ]
    barrier = Barrier(2)
    outcomes: list[object] = []

    def worker(session: Session) -> None:
        barrier.wait(timeout=5)
        try:
            outcomes.append(session_store.create_session(tmp_path, session))
        except BaseException as error:
            outcomes.append(error)

    threads = [Thread(target=worker, args=(session,)) for session in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(isinstance(outcome, tuple) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.intent.json"))


def test_abandoned_prepare_without_projection_evidence_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "retry-abandoned-prepare"
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "d" * 64})
    prepare = session_store._prepare_projection_authority
    interrupt = True

    def interrupt_after_authority(*args, **kwargs):
        nonlocal interrupt
        authority = prepare(*args, **kwargs)
        if interrupt:
            interrupt = False
            raise OSError("injected interruption after authority prepare")
        return authority

    monkeypatch.setattr(
        session_store,
        "_prepare_projection_authority",
        interrupt_after_authority,
    )
    with pytest.raises(OSError, match="after authority prepare"):
        session_store.create_session(
            tmp_path,
            Session.start(lock, "first attempt", session_id=session_id),
        )
    assert not session_store.session_event_path(tmp_path, session_id).exists()
    assert not session_store.session_metadata_path(tmp_path, session_id).exists()

    session_store.create_session(
        tmp_path,
        Session.start(lock, "retry attempt", session_id=session_id),
    )
    restored, _ = session_store.load_session(tmp_path, session_id)
    assert restored.read_model.session_id == session_id


def test_concurrent_case_variant_session_ids_share_one_identity(
    tmp_path: Path,
) -> None:
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "e" * 64})
    sessions = [
        Session.start(lock, "case collision", session_id=session_id)
        for session_id in ("Case-Collision", "case-collision")
    ]
    barrier = Barrier(2)
    outcomes: list[object] = []

    def worker(session: Session) -> None:
        barrier.wait(timeout=5)
        try:
            outcomes.append(session_store.create_session(tmp_path, session))
        except BaseException as error:
            outcomes.append(error)

    threads = [Thread(target=worker, args=(session,)) for session in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(isinstance(outcome, tuple) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
    assert [name.casefold() for name in session_store.session_names(tmp_path)] == [
        "case-collision"
    ]
