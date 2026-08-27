from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from breadboard_engine.api.cli_bridge.app import create_app
import breadboard_engine.provider_broker as provider_broker
from breadboard_engine.provider.runtimes.testing import MockRuntime
from breadboard.product.cli import session as session_operations
from breadboard.product.runtime import session_store


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
    assert started.json()["hashes"]["lock"] == lock["graph_hash"]
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
    assert all(event["schema_version"] == "bb.kernel_event.v2" for event in first)
    assert sum(event["kind"] == "input.accepted" for event in first) == 1
    assert first[-1]["kind"] == "session.canceled"
    assert first[-1]["payload"]["reason"] == "<redacted>"
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
        assert (
            restored.json()["data"]["session"]["effective_lock_hash"]
            == profile["effective_lock_hash"]
            == described["hashes"]["profile"]
        )
        assert restored_listing.status_code == 200
        assert restored_listing.json()["data"]["sessions"] == [
            {
                "session_id": session_id,
                "status": "completed",
                "event_count": len(events),
            }
        ]
        assert event_path.read_bytes() == events_before_reads

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
