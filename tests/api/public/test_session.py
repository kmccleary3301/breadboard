from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentic_coder_prototype.api.cli_bridge.app import create_app


def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))


def _locked_harness(client: TestClient) -> str:
    assert client.post("/v1/harnesses", json={}).json()["ok"] is True
    result = client.post("/v1/harnesses/minimal_harness.v2.yaml/lock").json()
    assert result["ok"] is True
    return result["data"]["path"]


def _stream_records(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_session_lifecycle_and_resumable_event_stream(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    lock_id = _locked_harness(client)
    started = client.post(
        "/v1/sessions",
        json={"lock_id": lock_id, "task": "exercise public session", "session_id": "session-fixture"},
    )
    assert started.status_code == 202
    assert started.json()["data"]["session"]["status"] == "running"
    assert client.post("/v1/sessions/session-fixture/input", json={"content": "continue"}).status_code == 202
    assert client.post("/v1/sessions/session-fixture/cancel", json={}).status_code == 202

    first = _stream_records(client.get("/v1/sessions/session-fixture/events"))
    assert [event["seq"] for event in first] == [1, 2, 3]
    assert all(event["schema_version"] == "bb.kernel_event.v2" for event in first)
    assert first[-1]["kind"] == "session.canceled"

    resumed = _stream_records(
        client.get("/v1/sessions/session-fixture/events", headers={"Last-Event-ID": "2"})
    )
    assert resumed == [first[-1]]
    assert client.get("/v1/sessions/session-fixture").json()["data"]["session"]["status"] == "canceled"
    assert client.get("/v1/sessions/session-fixture/artifacts").json()["ok"] is True


def test_session_invalid_state_is_stable_and_secret_free(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    lock_id = _locked_harness(client)
    payload = {"lock_id": lock_id, "task": "secret-free error", "session_id": "duplicate"}
    assert client.post("/v1/sessions", json=payload).status_code == 202
    duplicate = client.post("/v1/sessions", json=payload)
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["error_code"] == "invalid_state"
    assert str(tmp_path) not in duplicate.text
