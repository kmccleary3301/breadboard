"""Request-keyed approvals frozen at Session start for native concurrent prompts."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.events import CompactionSnapshot, Session, rebuild

_LOCK = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64})
_SCHEMAS = Path(__file__).resolve().parents[3] / "contracts/public/schemas"


def _gated() -> Session:
    session = Session.start(_LOCK, "task", concurrent_approvals=True)
    session.request_approval("child", "read")
    session.request_approval("root", "read")
    return session


@pytest.mark.parametrize("order", [("root", "child"), ("child", "root")])
def test_gated_session_resolves_pending_approvals_in_either_order(order) -> None:
    session = _gated()
    assert session.concurrent_approvals is True
    assert session.pending_approvals == ("child", "root")
    view = session.read_model
    assert (view.status, view.pending_approval) == ("awaiting_approval", "child")
    first, second = order
    view = session.resolve_approval(first, "once")
    assert view.status == "awaiting_approval"
    assert session.pending_approvals == (second,)
    assert view.pending_approval == second
    view = session.resolve_approval(second, "once")
    assert (view.status, view.pending_approval) == ("running", None)
    assert session.pending_approvals == ()
    assert rebuild(session.events) == view
    assert Session.restore(session.events).pending_approvals == ()


def test_gated_session_rejects_unknown_stale_and_duplicate_ids() -> None:
    session = _gated()
    before = session.events
    with pytest.raises(ValueError, match="^approval does not match any pending request$"):
        session.resolve_approval("unknown", "once")
    session.resolve_approval("root", "once")
    with pytest.raises(ValueError, match="^approval does not match any pending request$"):
        session.resolve_approval("root", "once")
    with pytest.raises(ValueError, match="^approval request is already pending$"):
        session.request_approval("child", "read")
    assert session.events[: len(before)] == before
    assert len(session.events) == len(before) + 1
    assert session.pending_approvals == ("child",)


def test_gated_start_payload_is_admitted_and_replayed() -> None:
    session = _gated()
    start = session.events[0].payload
    assert start["concurrent_approvals"] is True
    lifecycle = json.loads(
        (_SCHEMAS / "bb.payload.product_session.lifecycle.v1.schema.json").read_text()
    )
    validator = Draft202012Validator(
        {"$defs": lifecycle["$defs"], "$ref": "#/$defs/sessionStarted"}
    )
    validator.validate(dict(start))
    assert list(validator.iter_errors({**dict(start), "concurrent_approvals": False}))
    restored = Session.restore(session.events)
    assert restored.concurrent_approvals is True
    assert restored.pending_approvals == ("child", "root")
    session_schema = Draft202012Validator(
        json.loads((_SCHEMAS / "bb.session.v1.schema.json").read_text())
    )
    session_schema.validate(session.read_model.as_dict())


def test_ungated_session_keeps_single_request_contract() -> None:
    session = Session.start(_LOCK, "task")
    assert session.concurrent_approvals is False
    assert "concurrent_approvals" not in session.events[0].payload
    session.request_approval("child", "read")
    with pytest.raises(RuntimeError, match="^cannot request approval while session is awaiting_approval$"):
        session.request_approval("root", "read")
    with pytest.raises(ValueError, match="^approval does not match the pending request$"):
        session.resolve_approval("root", "once")
    view = session.resolve_approval("child", "once")
    assert (view.status, view.pending_approval) == ("running", None)
    assert set(view.as_dict()) == {
        "schema_version", "session_id", "status", "effective_lock_hash", "generation_id",
        "trajectory_segment_id", "lineage", "task_hash", "event_count",
        "pending_approval", "terminal_outcome",
    }
    with pytest.raises(TypeError):
        Session.start(_LOCK, "task", concurrent_approvals=1)


def test_gated_session_compacts_while_approvals_are_pending() -> None:
    session = _gated()
    session.compact(CompactionSnapshot(b"[]", ()))
    view = session.read_model
    assert (view.status, view.pending_approval) == ("awaiting_approval", "child")
    assert session.pending_approvals == ("child", "root")
    assert rebuild(session.events) == view


def test_ungated_session_still_refuses_compaction_while_awaiting_approval() -> None:
    session = Session.start(_LOCK, "task")
    session.request_approval("child", "read")
    with pytest.raises(RuntimeError, match="^cannot compact while session is awaiting_approval$"):
        session.compact(CompactionSnapshot(b"[]", ()))
