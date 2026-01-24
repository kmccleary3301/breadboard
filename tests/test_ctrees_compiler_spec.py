from __future__ import annotations

from agentic_coder_prototype.ctrees.compiler import compile_ctree
from agentic_coder_prototype.ctrees.store import CTreeStore


def _ids_in_append_order(store: CTreeStore, ids: list[str]) -> list[str]:
    index = {}
    for idx, node in enumerate(store.nodes):
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id and node_id not in index:
            index[node_id] = idx
    return sorted(ids, key=lambda value: index.get(value, 10**12))


def test_compiler_spec_selection_is_stable_and_ordered() -> None:
    store = CTreeStore()
    store.record("message", {"role": "user", "content": "first"}, turn=1)
    store.record("message", {"role": "assistant", "content": "second"}, turn=1)
    store.record("message", {"role": "user", "content": "third"}, turn=2)

    compiled_a = compile_ctree(store)
    compiled_b = compile_ctree(store)

    spec_a = compiled_a.get("stages", {}).get("SPEC") or {}
    spec_b = compiled_b.get("stages", {}).get("SPEC") or {}

    selection_a = spec_a.get("selection") or {}
    selection_b = spec_b.get("selection") or {}

    assert selection_a.get("selection_sha256") == selection_b.get("selection_sha256")
    assert selection_a.get("selected_ids") == selection_b.get("selected_ids")

    selected_ids = list(selection_a.get("selected_ids") or [])
    assert selected_ids == _ids_in_append_order(store, selected_ids)


def test_compiler_header_redacts_secret_like_previews() -> None:
    store = CTreeStore()
    store.record("message", {"role": "user", "content": "sk-abcdef0123456789abcdef0123456789"}, turn=1)

    compiled = compile_ctree(
        store,
        config={"header": {"mode": "sanitized_content", "preview_chars": 64, "redact_secret_like": True}},
    )
    header = compiled.get("stages", {}).get("HEADER") or {}
    messages = header.get("messages") or []
    assert messages and isinstance(messages[0], dict)
    preview = messages[0].get("content_preview")
    assert preview == "***REDACTED***"


def test_compiler_stage_hashes_are_stable() -> None:
    store = CTreeStore()
    store.record("message", {"role": "system", "content": "sys"}, turn=0)
    store.record("message", {"role": "user", "content": "hello"}, turn=1)
    store.record("message", {"role": "assistant", "content": "hi"}, turn=1)

    compiled_a = compile_ctree(store)
    compiled_b = compile_ctree(store)

    hashes_a = compiled_a.get("hashes") or {}
    hashes_b = compiled_b.get("hashes") or {}

    assert hashes_a.get("z1") == hashes_b.get("z1")
    assert hashes_a.get("z2") == hashes_b.get("z2")
    assert hashes_a.get("z3") == hashes_b.get("z3")

    stages_a = compiled_a.get("stages") or {}
    assert stages_a.get("RAW") is not None
    assert stages_a.get("SPEC") is not None
    assert stages_a.get("HEADER") is not None
    assert stages_a.get("FROZEN") is not None
    assert stages_a.get("FROZEN") == stages_a.get("HEADER")
