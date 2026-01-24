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
    id1 = store.record("message", {"text": "alpha"}, turn=1)
    id2 = store.record("message", {"text": "beta"}, turn=1)
    id3 = store.record("message", {"text": "gamma"}, turn=1)

    policy_target_2 = collapse_policy(store, target=2)
    policy_target_1 = collapse_policy(store, target=1)

    assert policy_target_2["drop"] == [id1]
    assert policy_target_1["drop"] == [id1, id2]
    assert policy_target_2["collapse"] == [id2]
    assert policy_target_1["collapse"] == []


def test_ctree_collapse_policy_mode_none_disables_collapse() -> None:
    store = CTreeStore()
    store.record("message", {"text": "alpha"}, turn=1)
    store.record("message", {"text": "beta"}, turn=1)

    policy = collapse_policy(store, target=None, mode="none")
    assert policy["mode"] == "none"
    assert policy["collapse"] == []


def test_ctree_collapse_policy_kind_allowlist_filters_nodes() -> None:
    store = CTreeStore()
    id_msg = store.record("message", {"text": "alpha"}, turn=1)
    store.record("tool", {"name": "ls"}, turn=1)

    policy = collapse_policy(store, target=0, kind_allowlist=["message"])
    assert policy["drop"] == [id_msg]


def test_ctree_collapse_policy_tiebreaks_by_append_order() -> None:
    store = CTreeStore()
    id_first = store.record("message", {"text": "same"}, turn=1)
    store.record("message", {"text": "same"}, turn=1)

    policy = collapse_policy(store, target=1)
    assert policy["drop"] == [id_first]


def test_ctree_compiler_respects_selection_limits() -> None:
    store = CTreeStore()
    store.record("message", {"text": "t1"}, turn=1)
    store.record("message", {"text": "t2"}, turn=2)
    store.record("message", {"text": "t3"}, turn=3)

    compiled = compile_ctree(store, config={"selection": {"kind_allowlist": ["message"], "max_turns": 1}})
    spec = compiled["stages"]["SPEC"]
    assert spec["selection"]["max_turns"] == 1
    assert spec["selection"]["selected_count"] == 1
    assert spec["selected"][0]["turn"] == 3


def test_ctree_compiler_sanitized_content_preview_redacts_secrets() -> None:
    store = CTreeStore()
    store.record("message", {"role": "user", "content": "sk-abcdef0123456789abcdef0123456789"}, turn=1)

    compiled = compile_ctree(store, config={"header": {"mode": "sanitized_content", "preview_chars": 8}})
    header = compiled["stages"]["HEADER"]
    assert header["header_mode"] == "sanitized_content"
    assert header["messages"][0]["content_preview"] == "***REDACTED***"


def test_ctree_compiler_pins_latest_user_and_assistant_even_when_trimmed() -> None:
    store = CTreeStore()
    store.record("message", {"role": "system", "content": "sys"}, turn=0)
    store.record("message", {"role": "user", "content": "u1"}, turn=1)
    id_assistant = store.record("message", {"role": "assistant", "content": "a1"}, turn=1)
    id_user = store.record("message", {"role": "user", "content": "u2"}, turn=2)

    compiled = compile_ctree(
        store,
        config={"selection": {"kind_allowlist": ["message"], "max_nodes": 1, "pin_latest_user_assistant": True}},
    )
    spec = compiled["stages"]["SPEC"]
    header = compiled["stages"]["HEADER"]

    assert spec["selection"]["max_nodes"] == 1
    assert spec["selection"]["pinned_ids"] == [id_assistant, id_user]
    assert header["selected_node_ids"] == [id_assistant, id_user]


def test_ctree_compiler_max_message_chars_budget_is_deterministic() -> None:
    store = CTreeStore()
    store.record("message", {"role": "system", "content": "sys"}, turn=0)
    store.record("message", {"role": "user", "content": "aaaaaa"}, turn=1)  # 6 chars
    id_assistant = store.record("message", {"role": "assistant", "content": "bbbbbb"}, turn=1)  # 6 chars
    id_user = store.record("message", {"role": "user", "content": "cccccc"}, turn=2)  # 6 chars

    compiled = compile_ctree(
        store,
        config={
            "selection": {
                "kind_allowlist": ["message"],
                "max_message_chars": 6,
                "pin_latest_user_assistant": True,
            }
        },
    )
    header = compiled["stages"]["HEADER"]
    # Latest pinned user+assistant are always included; older system/user may be dropped by budget.
    assert id_assistant in header["selected_node_ids"]
    assert id_user in header["selected_node_ids"]
