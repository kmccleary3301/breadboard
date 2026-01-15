from __future__ import annotations

from agentic_coder_prototype.ctrees.compiler import compile_ctree
from agentic_coder_prototype.ctrees.policy import collapse_policy
from agentic_coder_prototype.ctrees.store import CTreeStore


def test_ctree_compiler_hashes_deterministic() -> None:
    store_a = CTreeStore()
    store_a.record("message", {"text": "hello", "seq": 1, "timestamp": 1}, turn=1)

    store_b = CTreeStore()
    store_b.record("message", {"text": "hello", "seq": 99, "timestamp": 999}, turn=1)

    compiled_a = compile_ctree(store_a)
    compiled_b = compile_ctree(store_b)

    assert compiled_a["hashes"]["z1"] == compiled_b["hashes"]["z1"]
    assert compiled_a["hashes"]["z2"] == compiled_b["hashes"]["z2"]
    assert compiled_a["hashes"]["z3"] == compiled_b["hashes"]["z3"]


def test_ctree_collapse_policy_ordering() -> None:
    store = CTreeStore()
    store.nodes.append({"id": "ctn_000010"})
    store.nodes.append({"id": "ctn_000002"})
    store.nodes.append({"id": "ctn_000001"})

    policy_target_2 = collapse_policy(store, target=2)
    policy_target_1 = collapse_policy(store, target=1)

    assert policy_target_2["drop"] == ["ctn_000001"]
    assert policy_target_1["drop"] == ["ctn_000001", "ctn_000002"]
