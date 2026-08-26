from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from breadboard_engine.api.cli_bridge.events import EventType, SessionEvent
from breadboard_engine.api.cli_bridge.models import SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry, TurnRecord
from breadboard_engine.api.cli_bridge.runtime_emission import (
    ManagedStateRootError,
    default_runtime_record_root,
    managed_state_paths,
)
from breadboard_engine.api.cli_bridge.service import SessionService, _event_root


LAUNCH_ID = "a" * 43


def _managed_root(tmp_path: Path) -> Path:
    root = tmp_path / "engine-state"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def test_managed_state_derives_private_children_and_ignores_legacy_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _managed_root(tmp_path)
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(root))
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "legacy-records"))
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "legacy-events"))
    monkeypatch.setenv("BREADBOARD_SESSION_STATE_ROOT", str(tmp_path / "legacy-state"))

    service = SessionService()
    paths = managed_state_paths()

    assert paths is not None
    assert paths.root == root
    assert paths.runtime_records == root / "runtime-records"
    assert paths.session_events == root / "session-events"
    assert paths.session_state == root / "session-state"
    assert service.registry._state_root == paths.session_state
    assert default_runtime_record_root() == paths.runtime_records
    assert _event_root() == paths.session_events
    assert all(path.is_dir() and stat.S_IMODE(path.stat().st_mode) == 0o700 for path in (
        paths.runtime_records,
        paths.session_events,
        paths.session_state,
    ))


def test_managed_state_rejects_competing_state_authorities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _managed_root(tmp_path)
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(root))

    with pytest.raises(ManagedStateRootError, match="^invalid managed engine state root$"):
        SessionService(state_root=tmp_path / "override")
    with pytest.raises(ManagedStateRootError, match="^invalid managed engine state root$"):
        SessionService(registry=SessionRegistry(tmp_path / "registry"))


@pytest.mark.parametrize(
    "root_kind",
    ["missing", "relative", "nonexistent", "symlink", "wrong-mode", "wrong-owner", "linked-child"],
)
def test_invalid_managed_state_roots_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root_kind: str
) -> None:
    valid = _managed_root(tmp_path)
    if root_kind == "missing":
        raw = None
    elif root_kind == "relative":
        raw = "relative-engine-state"
    elif root_kind == "nonexistent":
        raw = str(tmp_path / "does-not-exist")
    elif root_kind == "symlink":
        target = _managed_root(tmp_path / "target")
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        raw = str(link)
    elif root_kind == "wrong-mode":
        valid.chmod(0o755)
        raw = str(valid)
    elif root_kind == "wrong-owner":
        monkeypatch.setattr(
            "breadboard_engine.api.cli_bridge.runtime_emission._current_uid",
            lambda: valid.stat().st_uid + 1,
        )
        raw = str(valid)
    else:
        child_target = tmp_path / "linked-runtime-records"
        child_target.mkdir(mode=0o700)
        (valid / "runtime-records").symlink_to(child_target, target_is_directory=True)
        raw = str(valid)

    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", LAUNCH_ID)
    if raw is None:
        monkeypatch.delenv("BREADBOARD_ENGINE_STATE_ROOT", raising=False)
    else:
        monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", raw)

    with pytest.raises(ManagedStateRootError, match="^invalid managed engine state root$"):
        SessionService()


def test_invalid_managed_state_fails_before_app_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard_engine.api.cli_bridge.app import create_app

    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", LAUNCH_ID)
    monkeypatch.delenv("BREADBOARD_ENGINE_STATE_ROOT", raising=False)

    with pytest.raises(ManagedStateRootError, match="^invalid managed engine state root$"):
        create_app()


def test_legacy_roots_remain_unchanged_without_managed_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BREADBOARD_ENGINE_LAUNCH_ID", raising=False)
    records = tmp_path / "records"
    events = tmp_path / "events"
    state = tmp_path / "state"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records))
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events))
    monkeypatch.setenv("BREADBOARD_SESSION_STATE_ROOT", str(state))

    service = SessionService()

    assert default_runtime_record_root() == records
    assert _event_root() == events
    assert service.registry._state_root == state


@pytest.mark.asyncio
async def test_two_terminal_turns_reload_from_managed_state_without_raw_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _managed_root(tmp_path)
    monkeypatch.setenv("BREADBOARD_ENGINE_LAUNCH_ID", LAUNCH_ID)
    monkeypatch.setenv("BREADBOARD_ENGINE_STATE_ROOT", str(root))
    service = SessionService()
    session_id = "session-restart"
    record = SessionRecord(session_id=session_id, status=SessionStatus.RUNNING)
    await service.registry.create(record)

    for index in (1, 2):
        turn = TurnRecord(
            input_id=f"input-{index}",
            turn_id=f"turn-{index}",
            client_message_id=f"client-{index}",
            content=f"raw prompt secret {index}",
            attachments=(f"/private/raw/path/{index}",),
            original_disposition="accepted",
            state="completed",
            execution_committed=True,
            terminal_outcome="completed",
        )
        record.turns_by_id[turn.turn_id] = turn
        record.event_seq = index
        if index == 2:
            record.status = SessionStatus.COMPLETED
        await service.registry.persist(
            record,
            terminal_event=SessionEvent(
                EventType.TURN_COMPLETED,
                session_id,
                {},
                seq=index,
                input_id=turn.input_id,
                turn_id=turn.turn_id,
            ),
        )

    state_files = list((root / "session-state").glob("*.json"))
    assert len(state_files) == 1
    persisted = state_files[0].read_text(encoding="utf-8")
    assert "raw prompt secret" not in persisted
    assert "/private/raw/path" not in persisted

    restarted = SessionService()
    summaries = await restarted.list_sessions()
    assert [summary.session_id for summary in summaries] == [session_id]
    assert summaries[0].status is SessionStatus.COMPLETED
    assert len(summaries[0].terminal_turns) == 2
