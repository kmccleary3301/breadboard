from __future__ import annotations

from datetime import datetime, timezone

from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import SessionStatus, SessionSummary
from agentic_coder_prototype.api.cli_bridge.persistence import (
    SessionEventLog,
    SessionIndex,
    SessionIndexSQLite,
)
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry


async def test_session_index_roundtrip(tmp_path) -> None:
    index = SessionIndex(tmp_path)
    summary = SessionSummary(
        session_id="session-1",
        status=SessionStatus.RUNNING,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        last_activity_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        model="model-x",
        mode="fast",
        completion_summary={"ok": True},
        reward_summary=None,
        logging_dir="/tmp/logs",
        metadata={"foo": "bar"},
    )
    await index.write_summary(summary)

    summaries = await index.list_summaries()
    assert len(summaries) == 1
    loaded = summaries[0]
    assert loaded.session_id == "session-1"
    assert loaded.status == SessionStatus.RUNNING
    assert loaded.model == "model-x"
    assert loaded.metadata == {"foo": "bar"}


async def test_registry_updates_session_index(tmp_path) -> None:
    index = SessionIndex(tmp_path)
    registry = SessionRegistry(index=index)
    record = SessionRecord(session_id="session-2", status=SessionStatus.STARTING)
    await registry.create(record)
    await registry.update_status("session-2", SessionStatus.STOPPED)

    summaries = await index.list_summaries()
    assert len(summaries) == 1
    loaded = summaries[0]
    assert loaded.session_id == "session-2"
    assert loaded.status == SessionStatus.STOPPED


async def test_registry_updates_metadata_in_index(tmp_path) -> None:
    index = SessionIndex(tmp_path)
    registry = SessionRegistry(index=index)
    record = SessionRecord(session_id="session-meta", status=SessionStatus.RUNNING)
    await registry.create(record)
    await registry.update_metadata(
        "session-meta",
        metadata={"alpha": 1},
        completion_summary={"done": True},
        reward_summary={"score": 0.9},
    )
    summaries = await index.list_summaries()
    assert len(summaries) == 1
    loaded = summaries[0]
    assert loaded.session_id == "session-meta"
    assert loaded.metadata == {"alpha": 1}
    assert loaded.completion_summary == {"done": True}
    assert loaded.reward_summary == {"score": 0.9}


async def test_registry_list_falls_back_to_index(tmp_path) -> None:
    index = SessionIndex(tmp_path)
    summary = SessionSummary(
        session_id="session-fallback",
        status=SessionStatus.RUNNING,
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        last_activity_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
    )
    await index.write_summary(summary)
    registry = SessionRegistry(index=index)
    summaries = await registry.list(use_index=True)
    assert len(summaries) == 1
    assert summaries[0].session_id == "session-fallback"


async def test_registry_bootstrap_from_index_enriches(tmp_path) -> None:
    index = SessionIndex(tmp_path)
    summary = SessionSummary(
        session_id="session-enrich",
        status=SessionStatus.RUNNING,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        last_activity_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    await index.write_summary(summary)

    log = SessionEventLog(tmp_path, "session-enrich")
    await log.append(
        SessionEvent(
            type=EventType.RUN_FINISHED,
            session_id="session-enrich",
            payload={"status": "ok"},
            turn=1,
            seq=7,
            created_at=2000,
        )
    )

    registry = SessionRegistry(index=index)
    count = await registry.bootstrap_from_index(eventlog_dir=str(tmp_path), enrich_from_eventlog=True)
    assert count == 1
    record = await registry.get("session-enrich")
    assert record is not None
    assert record.status == SessionStatus.COMPLETED
    assert record.event_seq == 7


async def test_session_index_sqlite_roundtrip(tmp_path) -> None:
    index = SessionIndexSQLite(tmp_path)
    schema_version = await index.get_schema_version()
    assert schema_version == 1
    summary = SessionSummary(
        session_id="session-sqlite",
        status=SessionStatus.RUNNING,
        created_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
        last_activity_at=datetime(2024, 5, 2, tzinfo=timezone.utc),
        model="model-y",
        mode="accurate",
        completion_summary={"done": True},
        reward_summary={"score": 0.5},
        logging_dir="/tmp/sqlite",
        metadata={"tag": "x"},
    )
    await index.write_summary(summary)
    summaries = await index.list_summaries()
    assert len(summaries) == 1
    loaded = summaries[0]
    assert loaded.session_id == "session-sqlite"
    assert loaded.status == SessionStatus.RUNNING
    assert loaded.metadata == {"tag": "x"}
    await index.delete("session-sqlite")
    summaries = await index.list_summaries()
    assert summaries == []
