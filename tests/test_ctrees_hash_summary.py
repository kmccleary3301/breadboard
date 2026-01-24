from __future__ import annotations

from agentic_coder_prototype.ctrees.collapse import collapse_ctree
from agentic_coder_prototype.ctrees.compiler import compile_ctree
from agentic_coder_prototype.ctrees.summary import build_ctree_hash_summary
from agentic_coder_prototype.ctrees.summary import format_ctrees_context_note
from agentic_coder_prototype.ctrees.store import CTreeStore


def test_ctree_hash_summary_is_stable() -> None:
    store = CTreeStore()
    store.record("message", {"role": "user", "content": "hi", "timestamp": 1}, turn=1)
    store.record("message", {"role": "assistant", "content": "hello", "seq": 99}, turn=1)
    store.record("subagent", {"task_id": "t1", "subagent_type": "repo-scanner"}, turn=1)

    snapshot = store.snapshot()
    compiler = compile_ctree(store)
    collapse = collapse_ctree(store)
    runner = {"enabled": True, "branches": 2}

    summary_a = build_ctree_hash_summary(snapshot=snapshot, compiler=compiler, collapse=collapse, runner=runner)
    summary_b = build_ctree_hash_summary(snapshot=snapshot, compiler=compiler, collapse=collapse, runner=runner)

    assert summary_a["summary_sha256"] == summary_b["summary_sha256"]
    assert summary_a["snapshot"]["node_hash"] == snapshot["node_hash"]
    assert summary_a["compiler"]["z3"] == compiler["hashes"]["z3"]


def test_ctree_context_note_is_compact_and_stable() -> None:
    store = CTreeStore()
    store.record("message", {"role": "user", "content": "hi"}, turn=1)
    snapshot = store.snapshot()
    compiler = compile_ctree(store)
    collapse = collapse_ctree(store)
    summary = build_ctree_hash_summary(snapshot=snapshot, compiler=compiler, collapse=collapse, runner=None)

    note_a = format_ctrees_context_note(summary)
    note_b = format_ctrees_context_note(summary)
    assert note_a == note_b
    assert note_a and "C-TREES:" in note_a


def test_ctree_hash_summary_omits_node_hash_for_backfilled_snapshots() -> None:
    snapshot = {"node_count": 2, "event_count": 2, "last_id": "n1", "node_hash": "deadbeef", "backfilled_from_eventlog": True}
    summary = build_ctree_hash_summary(snapshot=snapshot, compiler=None, collapse=None, runner=None)
    assert summary.get("backfilled_from_eventlog") is True
    assert summary["snapshot"]["node_hash"] is None
