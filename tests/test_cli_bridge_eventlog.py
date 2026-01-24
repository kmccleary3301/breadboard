from __future__ import annotations

import asyncio

from agentic_coder_prototype.api.cli_bridge.events import EventType, SessionEvent
from agentic_coder_prototype.api.cli_bridge.models import SessionStatus
from agentic_coder_prototype.api.cli_bridge.persistence import (
    SessionEventLog,
    eventlog_capped,
    eventlog_last_activity_ms,
    iter_event_payloads,
    iter_event_log,
    rehydrate_session_from_event_log,
)
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.registry import SessionRegistry
from agentic_coder_prototype.ctrees.store import CTreeStore


async def test_event_log_append_and_read(tmp_path) -> None:
    log = SessionEventLog(tmp_path, "session-test")
    event = SessionEvent(
        type=EventType.TOOL_CALL,
        session_id="session-test",
        payload={"tool": "bash"},
        turn=1,
        seq=5,
    )

    await log.append(event)

    payloads = list(iter_event_payloads(tmp_path, "session-test"))
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["seq"] == 5
    assert payload["payload"]["tool"] == "bash"

    events = list(iter_event_log(tmp_path, "session-test"))
    assert len(events) == 1
    event_out = events[0]
    assert event_out.seq == 5
    assert event_out.payload.get("tool") == "bash"


async def test_event_log_rehydrate_summary(tmp_path) -> None:
    log = SessionEventLog(tmp_path, "session-replay")
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-replay",
            payload={"turn": 1},
            turn=1,
            seq=1,
            created_at=1000,
        )
    )
    await log.append(
        SessionEvent(
            type=EventType.RUN_FINISHED,
            session_id="session-replay",
            payload={"status": "ok"},
            turn=1,
            seq=2,
            created_at=2000,
        )
    )

    replay = rehydrate_session_from_event_log(tmp_path, "session-replay")
    assert replay.summary.session_id == "session-replay"
    assert replay.summary.status == SessionStatus.COMPLETED
    assert replay.summary.created_at <= replay.summary.last_activity_at
    assert len(replay.events) == 2


async def test_event_log_bootstrap_registry(tmp_path) -> None:
    log_a = SessionEventLog(tmp_path, "session-a")
    await log_a.append(
        SessionEvent(
            type=EventType.RUN_FINISHED,
            session_id="session-a",
            payload={"status": "ok"},
            turn=1,
            seq=10,
            created_at=1000,
        )
    )
    log_b = SessionEventLog(tmp_path, "session-b")
    await log_b.append(
        SessionEvent(
            type=EventType.ERROR,
            session_id="session-b",
            payload={"error": "boom"},
            turn=1,
            seq=3,
            created_at=2000,
        )
    )

    registry = SessionRegistry()
    count = await registry.bootstrap_from_event_logs(str(tmp_path), populate_replay=True)
    assert count == 2
    record_a = await registry.get("session-a")
    record_b = await registry.get("session-b")
    assert record_a is not None
    assert record_b is not None
    assert record_a.status == SessionStatus.COMPLETED
    assert record_b.status == SessionStatus.FAILED
    assert record_a.event_seq == 10
    assert record_b.event_seq == 3
    assert len(record_a.event_log) == 1
    assert len(record_b.event_log) == 1


async def test_event_log_bootstrap_without_replay(tmp_path) -> None:
    log = SessionEventLog(tmp_path, "session-c")
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-c",
            payload={"turn": 1},
            turn=1,
            seq=4,
            created_at=1000,
        )
    )
    registry = SessionRegistry()
    count = await registry.bootstrap_from_event_logs(str(tmp_path), populate_replay=False)
    assert count == 1
    record = await registry.get("session-c")
    assert record is not None
    assert record.event_seq == 4
    assert not record.event_log


async def test_event_log_cap_skips_append(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_EVENTLOG_MAX_MB", "1")
    log = SessionEventLog(tmp_path, "session-cap")
    # Pre-fill file beyond 1MB
    log.path.write_text("x" * (1024 * 1024 + 10), encoding="utf-8")
    size_before = log.path.stat().st_size
    await log.append(
        SessionEvent(
            type=EventType.TOOL_CALL,
            session_id="session-cap",
            payload={"tool": "bash"},
            turn=1,
            seq=1,
            created_at=1000,
        )
    )
    size_after = log.path.stat().st_size
    assert size_after == size_before
    assert eventlog_capped() is True


async def test_event_log_resume_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_EVENTLOG_DIR", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_EVENTLOG_RESUME", "1")

    service = SessionService()
    record = SessionRecord(session_id="session-resume", status=SessionStatus.RUNNING)
    await service.registry.create(record)

    record.event_log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-resume",
            payload={},
            turn=1,
            seq=2,
            created_at=1000,
        )
    )
    record.event_log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-resume",
            payload={},
            turn=1,
            seq=3,
            created_at=2000,
        )
    )

    log = SessionEventLog(tmp_path, "session-resume")
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-resume",
            payload={},
            turn=1,
            seq=1,
            created_at=500,
        )
    )
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-resume",
            payload={},
            turn=1,
            seq=2,
            created_at=1000,
        )
    )
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-resume",
            payload={},
            turn=1,
            seq=3,
            created_at=2000,
        )
    )

    queue: asyncio.Queue = asyncio.Queue()
    await service._register_subscriber(record, queue, replay=True, from_id="1")

    first = await queue.get()
    second = await queue.get()
    assert first is not None and second is not None
    assert first.seq == 2
    assert second.seq == 3


async def test_event_log_validate_resume_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_EVENTLOG_DIR", str(tmp_path))
    monkeypatch.setenv("BREADBOARD_EVENTLOG_RESUME", "1")

    service = SessionService()
    record = SessionRecord(session_id="session-validate", status=SessionStatus.RUNNING)
    await service.registry.create(record)

    record.event_log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-validate",
            payload={},
            turn=1,
            seq=2,
            created_at=1000,
        )
    )
    record.event_log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-validate",
            payload={},
            turn=1,
            seq=3,
            created_at=2000,
        )
    )

    log = SessionEventLog(tmp_path, "session-validate")
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-validate",
            payload={},
            turn=1,
            seq=1,
            created_at=500,
        )
    )

    await service.validate_event_stream("session-validate", from_id="1", replay=True)

    # Clean up dispatcher task.
    await record.event_queue.put(None)
    if record.dispatcher_task is not None:
        await record.dispatcher_task


async def test_event_log_last_activity_ms(tmp_path) -> None:
    log = SessionEventLog(tmp_path, "session-last")
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-last",
            payload={},
            turn=1,
            seq=1,
            created_at=1000,
        )
    )
    await log.append(
        SessionEvent(
            type=EventType.TURN_START,
            session_id="session-last",
            payload={},
            turn=1,
            seq=2,
            created_at=5000,
        )
    )
    last_ms = eventlog_last_activity_ms(tmp_path)
    assert last_ms == 5000


async def test_event_dispatch_seq_monotonic() -> None:
    service = SessionService()
    record = SessionRecord(session_id="session-seq", status=SessionStatus.RUNNING)
    await service.registry.create(record)

    await service._ensure_dispatcher(record)
    await record.event_queue.put(
        SessionEvent(type=EventType.TURN_START, session_id="session-seq", payload={"turn": 1}, turn=1, seq=None)
    )
    await record.event_queue.put(
        SessionEvent(type=EventType.TURN_START, session_id="session-seq", payload={"turn": 2}, turn=2, seq=None)
    )
    await record.event_queue.put(
        SessionEvent(type=EventType.TURN_START, session_id="session-seq", payload={"turn": 3}, turn=3, seq=None)
    )
    await record.event_queue.put(None)

    if record.dispatcher_task is not None:
        await record.dispatcher_task

    seqs = [evt.seq for evt in record.event_log if evt is not None]
    assert seqs == sorted(seqs)


async def test_ctree_resume_replay_hash_consistency() -> None:
    service = SessionService()
    record = SessionRecord(session_id="session-ctree", status=SessionStatus.RUNNING)
    await service.registry.create(record)

    store_full = CTreeStore()
    payloads = [
        ("message", {"role": "system", "content": "sys"}, 0),
        ("message", {"role": "user", "content": "hello"}, 1),
        ("message", {"role": "assistant", "content": "hi"}, 1),
    ]

    for idx, (kind, payload, turn) in enumerate(payloads, start=1):
        node_id = store_full.record(kind, payload, turn=turn)
        node = store_full.nodes[-1]
        snapshot = store_full.snapshot()
        record.event_log.append(
            SessionEvent(
                type=EventType.CTREE_NODE,
                session_id="session-ctree",
                payload={"node": dict(node), "snapshot": dict(snapshot)},
                turn=turn,
                seq=idx,
            )
        )

    record.event_seq = len(record.event_log)
    full_hash = store_full.hashes().get("node_hash")

    seed_events = store_full.events[:1]
    store_resume = CTreeStore.from_events(seed_events)

    generator = service.event_stream("session-ctree", replay=True, from_id="1")
    async for event in generator:
        if event.type != EventType.CTREE_NODE:
            continue
        payload = event.payload or {}
        node = payload.get("node") if isinstance(payload, dict) else None
        if not isinstance(node, dict):
            continue
        store_resume.record(
            str(node.get("kind") or "message"),
            node.get("payload"),
            turn=node.get("turn"),
            node_id=str(node.get("id")) if node.get("id") else None,
        )
        if len(store_resume.nodes) >= len(store_full.nodes):
            break

    await generator.aclose()
    assert store_resume.hashes().get("node_hash") == full_hash

    await record.event_queue.put(None)
    if record.dispatcher_task is not None:
        await record.dispatcher_task
