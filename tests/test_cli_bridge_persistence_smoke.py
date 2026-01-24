from __future__ import annotations

from datetime import datetime, timezone

from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import SessionStatus, SessionSummary
from agentic_coder_prototype.api.cli_bridge.persistence import SessionEventLog, SessionIndex
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord


async def test_persistence_smoke(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_EVENTLOG_DIR", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_EVENTLOG_BOOTSTRAP", "1")
    monkeypatch.setenv("BREADBOARD_SESSION_INDEX", "1")
    monkeypatch.setenv("BREADBOARD_SESSION_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_SESSION_INDEX_BOOTSTRAP", "1")
    monkeypatch.setenv("BREADBOARD_EVENTLOG_ENRICH", "1")

    index = SessionIndex(tmp_path)
    summary = SessionSummary(
        session_id="session-smoke",
        status=SessionStatus.RUNNING,
        created_at=datetime(2024, 7, 1, tzinfo=timezone.utc),
        last_activity_at=datetime(2024, 7, 2, tzinfo=timezone.utc),
    )
    await index.write_summary(summary)

    log = SessionEventLog(tmp_path, "session-smoke")
    await log.append(
        SessionEvent(
            type=EventType.RUN_FINISHED,
            session_id="session-smoke",
            payload={"status": "ok"},
            turn=1,
            seq=9,
            created_at=2000,
        )
    )

    service = SessionService()
    idx_count = await service.bootstrap_index()
    log_count = await service.bootstrap_event_logs()
    summaries = list(await service.list_sessions())

    assert idx_count == 1
    assert log_count >= 0
    assert len(summaries) == 1
    assert summaries[0].status == SessionStatus.COMPLETED


async def test_ctree_snapshot_api_smoke() -> None:
    service = SessionService()
    record = SessionRecord(session_id="session-ctree-api", status=SessionStatus.RUNNING)

    class RunnerStub:
        def get_ctree_snapshot(self):
            return {
                "snapshot": {
                    "schema_version": "0.1",
                    "node_count": 1,
                    "event_count": 1,
                    "last_id": "n1",
                    "node_hash": "h1",
                },
                "last_node": {"id": "n1", "digest": "d1", "kind": "message", "turn": 1, "payload": {}},
            }

    record.runner = RunnerStub()
    await service.registry.create(record)

    payload = await service.get_ctree_snapshot("session-ctree-api")
    assert payload.snapshot is not None
    assert payload.snapshot.get("last_id") == "n1"
    assert payload.last_node is not None
    assert payload.last_node.get("id") == "n1"
