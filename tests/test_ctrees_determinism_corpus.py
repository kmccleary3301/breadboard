from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.ctrees.compiler import compile_ctree
from agentic_coder_prototype.ctrees.policy import collapse_policy
from agentic_coder_prototype.ctrees.store import CTreeStore


def test_ctree_compiler_ignores_nested_volatiles_and_secrets() -> None:
    store_a = CTreeStore()
    store_a.record(
        "message",
        {
            "role": "assistant",
            "content": "hello",
            "tool_calls": [
                {"id": "call-1", "timestamp": 1, "api_key": "secret-a"},
            ],
        },
        turn=1,
    )

    store_b = CTreeStore()
    store_b.record(
        "message",
        {
            "role": "assistant",
            "content": "hello",
            "tool_calls": [
                {"id": "call-1", "timestamp": 999, "api_key": "secret-b"},
            ],
        },
        turn=1,
    )

    compiled_a = compile_ctree(store_a)
    compiled_b = compile_ctree(store_b)

    assert compiled_a["hashes"]["z2"] == compiled_b["hashes"]["z2"]
    assert compiled_a["hashes"]["z3"] == compiled_b["hashes"]["z3"]


def test_ctree_redaction_case_insensitive_and_nested(tmp_path: Path) -> None:
    store_a = CTreeStore()
    store_a.record(
        "message",
        {"text": "hello", "Authorization": "Bearer secret-a", "nested": {"OPENAI_API_KEY": "a"}},
        turn=1,
    )
    store_b = CTreeStore()
    store_b.record(
        "message",
        {"text": "hello", "Authorization": "Bearer secret-b", "nested": {"OPENAI_API_KEY": "b"}},
        turn=1,
    )

    assert store_a.hashes() == store_b.hashes()

    result = store_a.persist(str(tmp_path))
    events_path = Path(result["paths"]["events"])
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    event = json.loads(lines[1])
    assert event["payload"]["Authorization"] == "***REDACTED***"
    assert event["payload"]["nested"]["OPENAI_API_KEY"] == "***REDACTED***"


def test_ctree_collapse_policy_uses_append_order() -> None:
    store = CTreeStore()
    ids = [
        store.record("message", {"text": "one"}, turn=1),
        store.record("message", {"text": "two"}, turn=1),
        store.record("message", {"text": "three"}, turn=1),
    ]
    policy = collapse_policy(store, target=2)
    assert policy["ordering"] == "append"
    assert policy["drop"] == [ids[0]]


def test_ctree_async_eventlog_replay_hash_is_stable() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests" / "fixtures" / "ctrees" / "async_ctree_events.jsonl"
    events = []
    for line in fixture.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict) and parsed.get("_type") == "ctree_eventlog_header":
            continue
        events.append(parsed)

    store_a = CTreeStore.from_events(events)
    store_b = CTreeStore.from_events(events)
    assert store_a.hashes() == store_b.hashes()
    assert store_a.hashes().get("node_hash") == "991df05bcdf9edff60e9c960a33448e263bf7416"
