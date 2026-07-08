from __future__ import annotations

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from breadboard.rl.phase3.api_router import create_phase3_rl_router
from breadboard.rl.phase3.service_live import LiveRLRunService
from breadboard.rl.phase3.store import SQLiteRLRunStore


def client(tmp_path):
    app = FastAPI()
    app.include_router(create_phase3_rl_router(LiveRLRunService(SQLiteRLRunStore(tmp_path / "runs.db"))), prefix="/rl")
    return TestClient(app)


def payload() -> dict:
    return {"run_id": "run-1", "tenant_id": "tenant-a", "workspace_id": "ws", "env_package_ref": "ws/env.tar", "target_run_id": "20260623T000000Z-slurm-234555", "requested_tasks": 1, "requested_gpus": 1, "requested_budget_usd": 1, "requested_duration_seconds": 60}



@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_tasks", 0),
        ("requested_tasks", -1),
        ("requested_gpus", 0),
        ("requested_gpus", -1),
        ("requested_budget_usd", 0.0),
        ("requested_budget_usd", -1.0),
        ("requested_duration_seconds", 0),
        ("requested_duration_seconds", -1),
    ],
)
def test_submit_rejects_non_positive_resource_requests(tmp_path, field: str, value: int | float) -> None:
    c = client(tmp_path)
    body = payload()
    body["run_id"] = f"run-{field}-{abs(int(value))}"
    body[field] = value

    response = c.post("/rl/runs", json=body)

    assert response.status_code == 422
    assert field in response.text

def test_submit_status_cancel_events(tmp_path) -> None:
    c = client(tmp_path)
    response = c.post("/rl/runs", json=payload())
    assert response.status_code == 200
    assert response.json()["state"] == "queued"
    assert c.get("/rl/runs/run-1", params={"tenant_id": "tenant-a", "workspace_id": "ws"}).json()["run_id"] == "run-1"
    assert c.post("/rl/runs/run-1/cancel", json={"tenant_id": "tenant-a", "workspace_id": "ws", "reason": "stop"}).json()["cancellation_state"] == "requested"
    events = c.get("/rl/runs/run-1/events", params={"tenant_id": "tenant-a", "workspace_id": "ws"}).text.strip().splitlines()
    assert len(events) >= 2


def test_tenant_mismatch_returns_403(tmp_path) -> None:
    c = client(tmp_path)
    c.post("/rl/runs", json=payload())
    response = c.get("/rl/runs/run-1", params={"tenant_id": "tenant-b", "workspace_id": "ws"})
    assert response.status_code == 403


def test_cli_bridge_default_run_store_uses_sqlite_memory_dsn(tmp_path, monkeypatch) -> None:
    from agentic_coder_prototype.api.cli_bridge.app import create_app

    monkeypatch.delenv("BREADBOARD_RL_RUN_STORE", raising=False)
    monkeypatch.chdir(tmp_path)

    bridge = TestClient(create_app())
    response = bridge.post("/v1/rl/runs", json=payload())

    assert response.status_code == 200
    assert bridge.get("/v1/rl/runs/run-1", params={"tenant_id": "tenant-a", "workspace_id": "ws"}).status_code == 200
    assert bridge.get("/rl/runs/run-1", params={"tenant_id": "tenant-a", "workspace_id": "ws"}).status_code == 200
    assert not (tmp_path / ":memory:").exists()
