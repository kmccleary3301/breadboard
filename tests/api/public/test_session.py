from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentic_coder_prototype.api.cli_bridge.app import create_app
from breadboard.product.cli import session as session_operations


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    with TestClient(create_app(include_atp_routes=False)) as test_client:
        yield test_client
def _locked_harness(client: TestClient) -> str:
    assert client.post("/v1/harnesses", json={}).json()["ok"] is True
    result = client.post("/v1/harnesses/minimal_harness.v2.yaml/lock").json()
    assert result["ok"] is True
    return result["data"]["path"]
def _stream_records(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
def test_session_lifecycle_and_resumable_event_stream(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    lock_id = _locked_harness(client)
    started = client.post(
        "/v1/sessions",
        json={"lock_id": lock_id, "task": "exercise public session", "session_id": "session-fixture"},
        headers={"Idempotency-Key": "start-fixture"},
    )
    assert started.status_code == 202
    assert started.json()["data"]["session"]["status"] == "running"
    lock = json.loads((tmp_path / lock_id).read_text(encoding="utf-8"))
    assert started.json()["hashes"]["lock"] == lock["graph_hash"]
    monkeypatch.setenv("SESSION_TOKEN", "abc")
    def finish() -> None:
        sleep(0.05)
        headers = {"Idempotency-Key": "input-fixture"}
        assert client.post("/v1/sessions/session-fixture/input", json={"content": "continue"}, headers=headers).status_code == 202
        assert client.post("/v1/sessions/session-fixture/input", json={"content": "continue"}, headers=headers).status_code == 202
        assert client.post("/v1/sessions/session-fixture/cancel", json={"reason": "abc"}, headers={"Idempotency-Key": "cancel-fixture"}).status_code == 202
    worker = Thread(target=finish); worker.start()
    streamed = client.get("/v1/sessions/session-fixture/events"); worker.join()
    first = _stream_records(streamed)
    assert '"reason":"abc"' not in streamed.text
    sequences = [event["seq"] for event in first]
    assert len(sequences) >= 2 and sequences == list(range(1, len(sequences) + 1))
    assert all(event["schema_version"] == "bb.kernel_event.v2" for event in first)
    assert first[-1]["kind"] == "session.canceled"
    assert first[-1]["payload"]["reason"] == "<redacted>"
    resumed = _stream_records(
        client.get("/v1/sessions/session-fixture/events", headers={"Last-Event-ID": str(first[-2]["seq"])})
    )
    assert resumed == [first[-1]]
    assert client.get("/v1/sessions/session-fixture").json()["data"]["session"]["status"] == "canceled"
    assert client.get("/v1/sessions/session-fixture/artifacts").json()["ok"] is True
def test_session_invalid_state_is_stable_and_secret_free(client: TestClient, tmp_path: Path) -> None:
    lock_id = _locked_harness(client)
    payload = {"lock_id": lock_id, "task": "secret-free error", "session_id": "duplicate"}
    assert client.post("/v1/sessions", json=payload, headers={"Idempotency-Key": "first"}).status_code == 202
    duplicate = client.post("/v1/sessions", json=payload, headers={"Idempotency-Key": "second"})
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["error_code"] == "invalid_state"
    assert str(tmp_path) not in duplicate.text
    malformed = client.post("/v1/sessions", json={})
    assert malformed.status_code == 422 and malformed.json()["schema_version"] == "bb.cli.result.v1"
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
    assert client.get("/v1/sessions/duplicate").json()["data"]["session"]["status"] == "running"
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
        session_operations.load_session(tmp_path, "..")
def test_durable_session_fallback_rejects_symlinked_metadata_root(monkeypatch, tmp_path: Path) -> None:
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
    (workspace / ".breadboard").symlink_to(outside / ".breadboard", target_is_directory=True)
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
    for operation in (session_operations.get, session_operations.events, session_operations.artifacts):
        result = operation(cli_args)
        assert result.ok is False
        assert "cross-boundary-secret" not in json.dumps(result.as_dict(), sort_keys=True)
    listing = session_operations.list_sessions(SimpleNamespace(workspace=workspace))
    assert "cross-boundary-secret" not in json.dumps(listing.as_dict(), sort_keys=True)


def test_durable_session_fallback_rejects_symlinked_event_file(monkeypatch, tmp_path: Path) -> None:
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
    cli_args = SimpleNamespace(workspace=workspace, SESSION_ID="symlinked-file", reason="must stay contained")
    for operation in (session_operations.get, session_operations.events, session_operations.artifacts, session_operations.cancel):
        result = operation(cli_args)
        assert result.ok is False
        assert "linked-file-secret" not in json.dumps(result.as_dict(), sort_keys=True)
    assert external.read_bytes() == original_external


def test_session_start_preserves_lock_drift_error(client: TestClient, tmp_path: Path) -> None:
    lock_id = _locked_harness(client)
    harness = tmp_path / "minimal_harness.v2.yaml"
    original = harness.read_text()
    changed = original.replace("name: respond", "name: changed", 1).replace("mode: respond", "mode: changed", 1)
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
    payload = {"lock_id": lock_id, "task": "complete the execution probe", "session_id": "execution-fixture"}
    started = client.post("/v1/sessions", json=payload, headers={"Idempotency-Key": "execution-start"})
    assert started.status_code == 202
    deadline = monotonic() + 10
    record = client.portal.call(client.app.state.session_service.ensure_session, "execution-fixture")
    while not record.event_log and monotonic() < deadline:
        sleep(0.05)
        record = client.portal.call(client.app.state.session_service.ensure_session, "execution-fixture")
    assert record.event_log and record.event_log[0].asdict()["type"] == "skills_catalog"
    assert client.post("/v1/sessions/execution-fixture/cancel", json={}, headers={"Idempotency-Key": "execution-cancel"}).status_code == 202
    records = _stream_records(client.get("/v1/sessions/execution-fixture/events"))
    assert records[0]["kind"] == "session.started"
    assert records[-1]["kind"] == "session.canceled"
