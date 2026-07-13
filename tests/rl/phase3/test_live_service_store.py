from __future__ import annotations

from breadboard.rl.phase2.service import ArtifactRecord, RunSubmission
from breadboard.rl.phase3.service_live import LiveRLRunService
from breadboard.rl.phase3.store import SQLiteRLRunStore

TARGET = "20260623T000000Z-slurm-234555"


def sub() -> RunSubmission:
    return RunSubmission("run-1", "tenant-a", "ws", "ws/env.tar", TARGET, 1, 1, 1.0, 60, {})


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
