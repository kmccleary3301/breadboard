from __future__ import annotations

import pytest

from breadboard.rl.phase2.service import ArtifactRecord, ResourceCaps, RunSubmission
from breadboard.rl.phase3.service_live import LiveRLRunService
from breadboard.rl.phase3.store import SQLiteRLRunStore

TARGET = "20260623T000000Z-slurm-234555"


def sub(run_id: str = "run-1", **overrides) -> RunSubmission:
    values = {
        "run_id": run_id,
        "tenant_id": "tenant-a",
        "workspace_id": "ws",
        "env_package_ref": "ws/env.tar",
        "target_run_id": TARGET,
        "requested_tasks": 1,
        "requested_gpus": 1,
        "requested_budget_usd": 1.0,
        "requested_duration_seconds": 60,
        "metadata": {},
    }
    values.update(overrides)
    return RunSubmission(**values)


def test_sqlite_persistence_restart(tmp_path) -> None:
    db = tmp_path / "runs.db"
    service = LiveRLRunService(SQLiteRLRunStore(db))
    service.submit(sub())
    service.complete("run-1")
    service.store.close()
    restarted = LiveRLRunService(SQLiteRLRunStore(db))
    assert restarted.status("run-1", tenant_id="tenant-a", workspace_id="ws").state == "succeeded"


def test_artifact_replay_persists(tmp_path) -> None:
    service = LiveRLRunService(SQLiteRLRunStore(tmp_path / "runs.db"))
    service.submit(sub())
    service.add_artifact(ArtifactRecord("run-1", "replay", "ws/replay/a.json", "sha256:abc", 10, True), tenant_id="tenant-a", workspace_id="ws")
    assert service.collect("run-1", tenant_id="tenant-a", workspace_id="ws")[0].artifact_id == "replay"
    assert service.replay("run-1", "replay", tenant_id="tenant-a", workspace_id="ws")["available"] is True


def test_cross_tenant_artifact_denied(tmp_path) -> None:
    service = LiveRLRunService(SQLiteRLRunStore(tmp_path / "runs.db"))
    service.submit(sub())
    try:
        service.collect("run-1", tenant_id="tenant-b", workspace_id="ws")
    except PermissionError:
        return
    raise AssertionError("tenant mismatch should fail")



def test_live_service_rejects_invalid_terminal_state_transitions(tmp_path) -> None:
    service = LiveRLRunService(SQLiteRLRunStore(tmp_path / "runs.db"))
    service.submit(sub())
    service.complete("run-1")

    with pytest.raises(ValueError, match="cannot complete from state succeeded"):
        service.complete("run-1")
    with pytest.raises(ValueError, match="already terminal: succeeded"):
        service.cancel("run-1")

    assert service.status("run-1", tenant_id="tenant-a", workspace_id="ws").state == "succeeded"


def test_live_service_rejected_run_cannot_start(tmp_path) -> None:
    service = LiveRLRunService(
        SQLiteRLRunStore(tmp_path / "runs.db"),
        caps=ResourceCaps(max_tasks=1, max_gpus=1, max_budget_usd=10.0, max_duration_seconds=60, max_artifact_bytes=1024),
    )
    rejected = service.submit(sub(run_id="run-rejected", requested_gpus=2))

    assert rejected.state == "rejected"
    with pytest.raises(ValueError, match="cannot start from state rejected"):
        service.start("run-rejected")
    assert service.status("run-rejected", tenant_id="tenant-a", workspace_id="ws").state == "rejected"


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
def test_live_service_rejects_non_positive_submission_resources(tmp_path, field: str, value: int | float) -> None:
    service = LiveRLRunService(SQLiteRLRunStore(tmp_path / "runs.db"))

    status = service.submit(sub(run_id=f"run-{field}-{abs(int(value))}", **{field: value}))

    assert status.state == "rejected"
    assert status.accepted is False
    assert field in status.reason