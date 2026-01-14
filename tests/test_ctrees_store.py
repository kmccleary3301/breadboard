from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.ctrees.store import CTreeStore


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


def test_ctree_persist_roundtrip(tmp_path: Path) -> None:
    store = CTreeStore()
    store.record("message", {"text": "persist"}, turn=1)
    store.record("tool", {"name": "cat"}, turn=1)

    result = store.persist(str(tmp_path))
    events_path = Path(result["paths"]["events"])
    snapshot_path = Path(result["paths"]["snapshot"])

    assert events_path.exists()
    assert snapshot_path.exists()

    replay = CTreeStore.from_dir(str(tmp_path))
    assert replay.hashes() == store.hashes()
