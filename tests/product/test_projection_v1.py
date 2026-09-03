from __future__ import annotations

from types import SimpleNamespace

import pytest

from breadboard.product.coordination.views import (
    CoordinationProjectionAsOfError,
    CoordinationProjectionVersionError,
    project_coordination_live,
    project_coordination_replay,
    project_coordination_snapshot,
)
from breadboard.product.coordination.work_items import (
    WorkItem,
    WorkItemProjectionAsOfError,
    WorkItemProjectionVersionError,
    project_work_item_live,
    project_work_item_replay,
    project_work_item_snapshot,
)
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.events import (
    Session,
    SessionProjectionAsOfError,
    SessionProjectionVersionError,
    project_session_live,
    project_session_replay,
    project_session_snapshot,
)


HASH = "sha256:" + "a" * 64
CLOCK = SimpleNamespace(now=lambda: "2026-07-17T00:00:04Z")


def _lock() -> EffectiveHarnessLock:
    return EffectiveHarnessLock._from_record({"graph_hash": HASH})


def _run(item: WorkItem) -> None:
    item.acquire_lease("worker-1", lease_id="lease-1")
    item.start_attempt("session-1", lease_id="lease-1", attempt_id="attempt-1")


def test_session_snapshot_replay_and_live_projections_are_identical() -> None:
    session = Session.start(_lock(), "ship packet", session_id="session-1", clock=CLOCK)
    session.input("content")

    replay = project_session_replay(session.events)
    live = project_session_live(session)
    snapshot = project_session_snapshot(session.read_model)

    assert replay == live == snapshot
    assert replay.projector_version == "bb.session.projector.v1"
    assert replay.source.stream == "session:session-1"
    assert (replay.source.first_sequence, replay.source.last_sequence, replay.as_of) == (1, 2, 2)
    with pytest.raises(SessionProjectionAsOfError):
        project_session_replay(session.events, as_of=3)
    with pytest.raises(SessionProjectionVersionError):
        project_session_replay(session.events, expected_projector_version="bb.session.projector.v2")


def test_work_item_snapshot_replay_and_live_projections_are_identical() -> None:
    item = WorkItem.create("ship packet", work_item_id="work-1", clock=CLOCK)
    _run(item)

    replay = project_work_item_replay(item.events)
    live = project_work_item_live(item)
    snapshot = project_work_item_snapshot(item.read_model)

    assert replay == live == snapshot
    assert replay.projector_version == "bb.work_item.projector.v1"
    assert replay.source.stream == "work_item:work-1"
    assert (replay.source.first_sequence, replay.source.last_sequence, replay.as_of) == (1, 3, 3)
    with pytest.raises(WorkItemProjectionAsOfError):
        project_work_item_replay(item.events, as_of=4)
    with pytest.raises(WorkItemProjectionVersionError):
        project_work_item_replay(item.events, expected_projector_version="bb.work_item.projector.v2")


def test_coordination_snapshot_replay_and_live_projections_are_identical() -> None:
    parent = WorkItem.create("parent", work_item_id="parent", clock=CLOCK)
    _run(parent)
    child = parent.delegate("child", attempt_id="attempt-1", child_work_item_id="child")
    streams = {"parent": parent.events, "child": child.events}

    replay = project_coordination_replay(streams)
    live = project_coordination_live((child, parent))
    snapshot = project_coordination_snapshot(replay.value)

    assert replay == live == snapshot
    assert replay.projector_version == "bb.coordination.projector.v1"
    assert replay.source.stream == "coordination"
    assert tuple(component.stream for component in replay.source.components) == ("work_item:child", "work_item:parent")
    with pytest.raises(CoordinationProjectionAsOfError):
        project_coordination_replay(streams, as_of=4)
    with pytest.raises(CoordinationProjectionVersionError):
        project_coordination_replay(streams, expected_projector_version="bb.coordination.projector.v2")


def test_coordination_heterogeneous_cursor_round_trips_without_truncation() -> None:
    short = WorkItem.create("short", work_item_id="short", clock=CLOCK)
    long = WorkItem.create("long", work_item_id="long", clock=CLOCK)
    _run(long)
    streams = {"short": short.events, "long": long.events}

    projected = project_coordination_replay(streams)
    replayed = project_coordination_replay(streams, as_of=projected.as_of)
    live = project_coordination_live((short, long), as_of=projected.as_of)
    snapshot = project_coordination_snapshot(projected.value, as_of=projected.as_of)
    reordered_snapshot = project_coordination_snapshot(
        projected.value,
        as_of=tuple(reversed(projected.as_of)),
    )

    assert projected == replayed == live == snapshot == reordered_snapshot
    assert [(cursor.stream, cursor.sequence) for cursor in projected.as_of] == [
        ("work_item:long", 3),
        ("work_item:short", 1),
    ]
    assert [(component.stream, component.last_sequence) for component in projected.source.components] == [
        ("work_item:long", 3),
        ("work_item:short", 1),
    ]
