from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.ctrees.store import CTreeStore
from agentic_coder_prototype.ctrees.persistence import EVENTLOG_HEADER_TYPE
from agentic_coder_prototype.ctrees.schema import CTREE_SCHEMA_VERSION


def test_ctree_hashes_ignore_volatiles() -> None:
    store_a = CTreeStore()
    store_a.record("message", {"text": "hello", "seq": 1, "timestamp": 1}, turn=1)

    store_b = CTreeStore()
    store_b.record("message", {"text": "hello", "seq": 99, "timestamp": 999}, turn=1)

    assert store_a.hashes() == store_b.hashes()


def test_ctree_replay_roundtrip() -> None:
    store = CTreeStore()
    store.record("message", {"text": "alpha"}, turn=1)
    store.record("tool", {"name": "ls"}, turn=1)

    replay = CTreeStore.from_events(store.events)
    assert replay.hashes() == store.hashes()
    assert replay.snapshot()["node_count"] == store.snapshot()["node_count"]


def test_ctree_node_ids_unique_for_duplicate_payloads() -> None:
    store = CTreeStore()
    node_id_a = store.record("message", {"text": "dup"}, turn=1)
    node_id_b = store.record("message", {"text": "dup"}, turn=1)
    assert node_id_a != node_id_b
    assert store.nodes[0]["digest"] == store.nodes[1]["digest"]


def test_ctree_from_events_preserves_node_id_when_present() -> None:
    events = [{"kind": "message", "payload": {"text": "hi"}, "turn": 1, "node_id": "fixed-node-id"}]
    store = CTreeStore.from_events(events)
    assert store.nodes[0]["id"] == "fixed-node-id"
    assert store.events[0]["node_id"] == "fixed-node-id"


def test_ctree_snapshot_has_schema_version() -> None:
    store = CTreeStore()
    store.record("message", {"text": "schema"}, turn=1)
    assert store.snapshot()["schema_version"] == CTREE_SCHEMA_VERSION


def test_ctree_persist_roundtrip(tmp_path: Path) -> None:
    store = CTreeStore()
    store.record("message", {"text": "persist"}, turn=1)
    store.record("tool", {"name": "cat"}, turn=1)

    result = store.persist(str(tmp_path))
    events_path = Path(result["paths"]["events"])
    snapshot_path = Path(result["paths"]["snapshot"])

    assert events_path.exists()
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == CTREE_SCHEMA_VERSION

    replay = CTreeStore.from_dir(str(tmp_path))
    assert replay.hashes() == store.hashes()
    assert [node.get("id") for node in replay.nodes] == [node.get("id") for node in store.nodes]


def test_ctree_eventlog_header_roundtrip(tmp_path: Path) -> None:
    store = CTreeStore()
    store.record("message", {"text": "header"}, turn=1)

    result = store.persist(str(tmp_path))
    events_path = Path(result["paths"]["events"])
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert lines, "ctree_events.jsonl should not be empty"

    header = json.loads(lines[0])
    assert header["_type"] == EVENTLOG_HEADER_TYPE
    assert header["schema_version"] == CTREE_SCHEMA_VERSION

    replay = CTreeStore.from_dir(str(tmp_path))
    assert replay.hashes() == store.hashes()


def test_ctree_redacts_secrets_for_hash_and_persist(tmp_path: Path) -> None:
    store_a = CTreeStore()
    store_a.record("message", {"text": "hello", "api_key": "secret-a"}, turn=1)

    store_b = CTreeStore()
    store_b.record("message", {"text": "hello", "api_key": "secret-b"}, turn=1)

    assert store_a.hashes() == store_b.hashes()

    result = store_a.persist(str(tmp_path))
    events_path = Path(result["paths"]["events"])
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2, "expected header + at least one event line"

    event = json.loads(lines[1])
    assert event["payload"]["api_key"] == "***REDACTED***"
