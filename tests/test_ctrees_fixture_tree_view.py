from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.ctrees.persistence import load_events
from agentic_coder_prototype.ctrees.store import CTreeStore
from agentic_coder_prototype.ctrees.tree_view import build_ctree_tree_view


def test_ctrees_fixture_tree_view_matches_expected() -> None:
    base = Path(__file__).parent / "fixtures" / "ctrees"
    events = load_events(base / "simple_ctree_events.jsonl")
    store = CTreeStore.from_events(events)

    compiler_cfg = {
        "selection": {"kind_allowlist": ["message", "task_event"], "max_nodes": None, "max_turns": None},
        "header": {"mode": "hash_only", "preview_chars": 16, "redact_secret_like": True},
    }
    payload = build_ctree_tree_view(
        store,
        stage="FROZEN",
        compiler_config=compiler_cfg,
        collapse_target=2,
        include_previews=False,
    )
    expected = json.loads((base / "simple_ctree_tree_frozen.json").read_text(encoding="utf-8"))
    assert payload == expected


def test_ctrees_tree_view_emits_collapsed_summary_nodes() -> None:
    store = CTreeStore()
    store.record("message", {"role": "system", "content": "sys"}, turn=0)
    store.record("message", {"role": "user", "content": "u1"}, turn=1)
    store.record("message", {"role": "assistant", "content": "a1"}, turn=1)
    store.record("message", {"role": "user", "content": "u2"}, turn=2)
    store.record("message", {"role": "assistant", "content": "a2"}, turn=2)

    compiler_cfg = {
        "selection": {"kind_allowlist": ["message"], "max_nodes": None, "max_turns": None},
        "header": {"mode": "hash_only", "preview_chars": 16, "redact_secret_like": True},
    }
    payload = build_ctree_tree_view(
        store,
        stage="FROZEN",
        compiler_config=compiler_cfg,
        collapse_target=None,
        collapse_mode="all_but_last",
        include_previews=False,
    )

    collapsed_ids = set(payload.get("selection", {}).get("collapsed_ids") or [])
    assert collapsed_ids, "expected some collapsed ids"

    collapsed_nodes = [node for node in payload.get("nodes", []) if isinstance(node, dict) and node.get("kind") == "collapsed"]
    assert collapsed_nodes, "expected collapsed summary nodes"
    for node in collapsed_nodes:
        meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
        ids = meta.get("collapsed_ids")
        assert isinstance(ids, list) and ids

    emitted_ids = {str(node.get("id")) for node in payload.get("nodes", []) if isinstance(node, dict) and node.get("id")}
    for collapsed_id in collapsed_ids:
        assert f"ctrees:node:{collapsed_id}" not in emitted_ids
