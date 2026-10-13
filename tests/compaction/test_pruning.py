from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
import pytest

from breadboard_engine.compaction.methods import (
    CompactionContext,
    MethodUnavailable,
)
from breadboard_engine.compaction.pruning import (
    MIN_PRUNE_TOKENS,
    SUPERSEDED_NOTICE,
    USELESS_NOTICE,
    prune_superseded_tool_results,
    prune_tool_outputs,
    prune_tool_results,
    read_tool_supersede_key,
)
from breadboard_engine.compaction.settings import settings_from_config
from breadboard_engine.compaction.state import CompactionState, ProjectionTarget
from breadboard_engine.compaction.tokens import estimate_message_tokens
from breadboard_engine.compaction.transcript import check_tool_pairing
from breadboard_engine.provider.adapters import AnthropicAdapter
from breadboard_engine.provider.contract_messages import responses_image_tool_result_message

TARGET = ProjectionTarget("openai", "responses", "gpt-x")

T0 = 1770000000000.0
FILE_CONTENT = "export function alpha() { return 1; }\n" * 50
BIG_TEXT = "const value = computeSomething(12345);\n" * 500


def _native_result_carrier(carrier: str, text: str) -> Dict[str, Any]:
    if carrier == "responses":
        return responses_image_tool_result_message(
            "c1", [{"type": "input_text", "text": text}],
        )
    return AnthropicAdapter().create_tool_result_message(
        "c1", "bash", {"__claude_tool_result_messages": [{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": text}],
        }]},
    )


def _context(
    messages: List[Dict[str, Any]],
    state: Optional[CompactionState] = None,
    *,
    reason: str = "threshold",
    window: int = 200000,
    **config: Any,
) -> CompactionContext:
    settings = settings_from_config({"enabled": True, **config})
    st = state or CompactionState()
    return CompactionContext(
        messages=messages,
        state=st,
        settings=settings,
        reason=reason,  # type: ignore[arg-type]
        target=TARGET,
        context_window=window,
        tokens_before=sum(estimate_message_tokens(m) for m in st.project(messages, TARGET)),
    )


# -----------------------------------------------------------------------------
# read_tool_supersede_key unit tests
# -----------------------------------------------------------------------------


def test_read_tool_supersede_key_bare_path_and_exemptions():
    assert read_tool_supersede_key("read", {"path": "src/foo.ts"}) == "src/foo.ts"
    assert read_tool_supersede_key("read_file", {"path": "src/foo.ts"}) == "src/foo.ts"
    assert read_tool_supersede_key("read_file", {"file_path": "src/foo.ts"}) == "src/foo.ts"
    assert read_tool_supersede_key("bash", {"path": "src/foo.ts"}) is None
    assert read_tool_supersede_key("read", {"path": 42}) is None
    assert read_tool_supersede_key("read", {}) is None


def test_read_tool_supersede_key_exempts_urls_and_schemes():
    assert read_tool_supersede_key("read", {"path": "skill://react"}) is None
    assert read_tool_supersede_key("read", {"path": "artifact://art-123"}) is None
    assert read_tool_supersede_key("read", {"path": "https://example.com/page"}) is None


def test_read_tool_supersede_key_strips_trailing_selectors():
    assert read_tool_supersede_key("read", {"path": "src/foo.ts:50-200"}) == "src/foo.ts\u000050-200"
    assert read_tool_supersede_key("read", {"path": "src/foo.ts:raw"}) == "src/foo.ts\u0000raw"
    assert read_tool_supersede_key("read", {"path": "src/foo.ts:conflicts"}) == "src/foo.ts\u0000conflicts"
    assert read_tool_supersede_key("read", {"path": "src/foo.ts:2-4:raw"}) == "src/foo.ts\u00002-4:raw"
    assert read_tool_supersede_key("read", {"path": "src/foo.ts:5-16,960-973"}) == "src/foo.ts\u00005-16,960-973"
    assert read_tool_supersede_key("read", {"path": "src/foo.ts:50+150"}) == "src/foo.ts\u000050+150"


def test_read_tool_supersede_key_does_not_strip_non_selector_colons():
    assert read_tool_supersede_key("read", {"path": "db.sqlite:users"}) == "db.sqlite:users"
    assert read_tool_supersede_key("read", {"path": "db.sqlite:users:42"}) == "db.sqlite:users\u000042"


def test_read_tool_supersede_key_offset_limit_args():
    assert read_tool_supersede_key("read_file", {"path": "foo.py", "offset": 10, "limit": 20}) == "foo.py\u000010-20"


# -----------------------------------------------------------------------------
# prune_superseded_tool_results tests ported from supersede-prune.test.ts
# -----------------------------------------------------------------------------


def _read_pair(path: str, text: str, ts: float, call_id: str, tool: str = "read") -> List[Dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": "",
            "timestamp": ts,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": tool, "arguments": {"path": path}}}],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": tool,
            "content": text,
            "timestamp": ts,
        },
    ]


def _useless_pair(tool: str, text: str, ts: float, call_id: str, **extra: Any) -> List[Dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": "",
            "timestamp": ts,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": tool, "arguments": {"pattern": "z"}}}],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": tool,
            "content": text,
            "timestamp": ts,
            "useless": True,
            **extra,
        },
    ]


def test_prune_superseded_tool_results_tail_case():
    pair1 = _read_pair("src/foo.ts", FILE_CONTENT, T0, "c1")
    pair2 = _read_pair("src/foo.ts", FILE_CONTENT, T0 + 1000, "c2")
    entries = [*pair1, *pair2]

    res = prune_superseded_tool_results(entries, now=T0 + 1000)
    assert res.pruned_count == 1
    assert res.tokens_saved > 0
    assert entries[1]["content"] == SUPERSEDED_NOTICE
    assert entries[1].get("pruned_at") is not None
    assert entries[3]["content"] == FILE_CONTENT


def test_prune_superseded_not_pruned_when_suffix_exceeds_limit_and_not_idle():
    pair1 = _read_pair("src/foo.ts", FILE_CONTENT, T0, "c1")
    pair2 = _read_pair("src/foo.ts", FILE_CONTENT, T0 + 1000, "c2")
    big = {"role": "assistant", "content": BIG_TEXT, "timestamp": T0 + 2000}
    entries = [*pair1, *pair2, big]

    res = prune_superseded_tool_results(entries, suffix_token_limit=10, now=T0 + 2000)
    assert res.pruned_count == 0
    assert entries[1]["content"] == FILE_CONTENT


def test_prune_superseded_idle_gap_prunes_all_candidates():
    pair1 = _read_pair("src/foo.ts", FILE_CONTENT, T0, "c1")
    pair2 = _read_pair("src/bar.ts", FILE_CONTENT, T0 + 1000, "c2")
    pair3 = _read_pair("src/foo.ts", FILE_CONTENT, T0 + 2000, "c3")
    pair4 = _read_pair("src/bar.ts", FILE_CONTENT, T0 + 3000, "c4")
    big = {"role": "assistant", "content": BIG_TEXT, "timestamp": T0 + 4000}
    entries = [*pair1, *pair2, pair3[0], pair3[1], pair4[0], pair4[1], big]

    idle_time = T0 + 4000 + 31 * 60 * 1000
    res = prune_superseded_tool_results(entries, suffix_token_limit=0, idle_flush_ms=30 * 60 * 1000, now=idle_time)
    assert res.pruned_count == 2
    assert entries[1]["content"] == SUPERSEDED_NOTICE
    assert entries[3]["content"] == SUPERSEDED_NOTICE


def test_bare_read_supersedes_selector_read_of_same_file():
    pair1 = _read_pair("src/foo.ts:1-20", FILE_CONTENT, T0, "c1")
    pair2 = _read_pair("src/foo.ts", FILE_CONTENT, T0 + 1000, "c2")
    entries = [*pair1, *pair2]

    res = prune_superseded_tool_results(entries, now=T0 + 1000)
    assert res.pruned_count == 1
    assert entries[1]["content"] == SUPERSEDED_NOTICE


def test_selector_read_does_not_supersede_bare_read():
    pair1 = _read_pair("src/foo.ts", FILE_CONTENT, T0, "c1")
    pair2 = _read_pair("src/foo.ts:1-20", FILE_CONTENT, T0 + 1000, "c2")
    entries = [*pair1, *pair2]

    res = prune_superseded_tool_results(entries, now=T0 + 1000)
    assert res.pruned_count == 0


def test_useless_results_pruned():
    pair = _useless_pair("search", "No matches found in codebase\n" * 10, T0, "c1")
    res = prune_superseded_tool_results(pair, prune_useless=True, now=T0 + 1000)
    assert res.pruned_count == 1
    assert pair[1]["content"] == USELESS_NOTICE


def test_useless_error_result_never_pruned():
    pair = _useless_pair("search", "Fatal crash\n" * 10, T0, "c1", is_error=True)
    res = prune_superseded_tool_results(pair, prune_useless=True, now=T0 + 1000)
    assert res.pruned_count == 0


def test_superseded_and_useless_gets_superseded_notice():
    pair1 = _read_pair("src/foo.ts", FILE_CONTENT, T0, "c1")
    pair1[1]["useless"] = True
    pair2 = _read_pair("src/foo.ts", FILE_CONTENT, T0 + 1000, "c2")
    entries = [*pair1, *pair2]

    res = prune_superseded_tool_results(entries, prune_useless=True, now=T0 + 1000)
    assert res.pruned_count == 1
    assert entries[1]["content"] == SUPERSEDED_NOTICE


def test_prune_superseded_responses_tool_result_uses_replay_call_id():
    first_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": {"path": "src/a.py"}}}],
    }
    latest_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read", "arguments": {"path": "src/a.py"}}}],
    }
    first_result = _native_result_carrier("responses", FILE_CONTENT)
    latest_result = _native_result_carrier("responses", FILE_CONTENT)
    first_result["content"][0]["payload"]["call_id"] = "c1"
    latest_result["content"][0]["payload"]["call_id"] = "c2"
    entries = [first_call, first_result, latest_call, latest_result]

    outcome = prune_superseded_tool_results(entries, now=T0 + 1000)

    assert outcome.pruned_count == 1
    assert entries[1]["content"][0]["payload"]["call_id"] == "c1"
    assert entries[1]["content"][0]["payload"]["output"][0]["text"] == SUPERSEDED_NOTICE
    assert entries[3]["content"][0]["payload"]["output"][0]["text"] == FILE_CONTENT
    assert check_tool_pairing(entries) is None


# -----------------------------------------------------------------------------
# prune_tool_outputs and prune_tool_results integration tests
# -----------------------------------------------------------------------------


def test_prune_tool_outputs_age_and_savings_gate():
    text = "large output line\n" * 500
    c1 = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}]}
    r1 = {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": text}
    c2 = {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "bash"}}]}
    r2 = {"role": "tool", "tool_call_id": "c2", "name": "bash", "content": text}
    recent = {"role": "user", "content": "recent " * 5000}
    messages = [c1, r1, c2, r2, recent]

    # With high protect_tokens, nothing pruned
    res_noprune = prune_tool_outputs(messages, protect_tokens=1_000_000, minimum_savings=100)
    assert res_noprune.pruned_count == 0

    # With 0 protect_tokens and reasonable savings, both pruned
    res = prune_tool_outputs(messages, protect_tokens=0, minimum_savings=100)
    assert res.pruned_count == 2
    assert "Output truncated" in r1["content"]
    assert "Output truncated" in r2["content"]


@pytest.mark.parametrize("carrier", ["responses", "anthropic"])
def test_prune_tool_outputs_rewrites_native_result_carriers(carrier):
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
    }
    result = _native_result_carrier(carrier, "large native result " * 500)
    messages = [assistant, result, {"role": "user", "content": "recent"}]

    outcome = prune_tool_outputs(messages, protect_tokens=0, minimum_savings=50)

    assert outcome.pruned_count == 1
    assert check_tool_pairing(messages) is None
    if carrier == "responses":
        [replay] = messages[1]["content"]
        assert replay["payload"]["call_id"] == "c1"
        assert "Output truncated" in replay["payload"]["output"][0]["text"]
    else:
        canonical, replay = messages[1]["content"]
        native = replay["payload"]["messages"][0]["content"][0]
        assert canonical["call_id"] == "c1"
        assert native["tool_use_id"] == "c1"
        assert native["content"] == canonical["content"]


def test_prune_tool_results_disabled_by_default_returns_none():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": {"path": "a.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": FILE_CONTENT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read", "arguments": {"path": "a.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c2", "name": "read", "content": FILE_CONTENT},
    ]
    # Prune disabled by default
    ctx = _context(messages)
    assert prune_tool_results(ctx) is None


def test_prune_tool_results_runs_and_produces_valid_record():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "a.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": FILE_CONTENT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "a.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c2", "name": "read_file", "content": FILE_CONTENT},
    ]
    ctx = _context(
        messages,
        prune={"enabled": True, "supersede_reads": True, "minimum_savings": 50, "protect_tokens": 40000},
    )
    record = prune_tool_results(ctx)
    assert record is not None
    assert record.method == "prune"
    assert not record.is_boundary
    assert len(record.edits) == 1
    assert record.edits[0].index == 3
    assert record.edits[0].message["content"] == SUPERSEDED_NOTICE

    ctx.state.validate(record, messages)
    ctx.state.append(record, messages)

    view = ctx.state.project(messages, TARGET)
    assert check_tool_pairing(view) is None
    assert view[3]["content"] == SUPERSEDED_NOTICE
    assert view[5]["content"] == FILE_CONTENT


@pytest.mark.parametrize("carrier", ["responses", "anthropic"])
def test_prune_tool_results_rewrites_native_result_without_losing_correlation(carrier):
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
    }
    result = _native_result_carrier(carrier, "large native result " * 500)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "run"}, assistant, result]
    context = _context(
        messages,
        prune={"enabled": True, "protect_tokens": 0, "minimum_savings": 50},
    )

    record = prune_tool_results(context)

    assert record is not None
    [edit] = record.edits
    assert edit.index == 3
    assert edit.message["role"] == result["role"]
    assert edit.message["pruned_at"]
    assert check_tool_pairing([assistant, edit.message]) is None
    if carrier == "responses":
        [replay] = edit.message["content"]
        assert replay["schema_version"] == "openai.responses.tool_output.v1"
        assert replay["replay_scope"] == "same_provider"
        assert replay["payload"]["call_id"] == "c1"
        assert replay["payload"]["output"][0]["type"] == "input_text"
        assert "Output truncated" in replay["payload"]["output"][0]["text"]
    else:
        canonical, replay = edit.message["content"]
        native = replay["payload"]["messages"][0]["content"][0]
        assert canonical["call_id"] == "c1"
        assert native["tool_use_id"] == "c1"
        assert "Output truncated" in canonical["content"]
        assert native["content"] == canonical["content"]
        assert replay["schema_version"] == "anthropic.messages.tool_result.v1"


def test_prune_tool_results_preserves_anthropic_batch_call_correlations():
    first_text = "first native result " * 500
    second_text = "second native result " * 500
    assistant = _read_pair("src/first.py", "", T0, "c1", tool="bash")[0]
    assistant["tool_calls"].extend(
        _read_pair("src/second.py", "", T0, "c2", tool="bash")[0]["tool_calls"]
    )
    result = AnthropicAdapter().create_tool_result_batch_message([{
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": first_text},
            {"type": "tool_result", "tool_use_id": "c2", "content": second_text},
        ],
    }])
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "run"}, assistant, result]
    context = _context(
        messages,
        prune={"enabled": True, "protect_tokens": 0, "minimum_savings": 50},
    )

    record = prune_tool_results(context)

    assert record is not None
    [edit] = record.edits
    first, second, replay = edit.message["content"]
    native_first, native_second = replay["payload"]["messages"][0]["content"]
    assert first["call_id"] == native_first["tool_use_id"] == "c1"
    assert second["call_id"] == native_second["tool_use_id"] == "c2"
    assert first["content"] == native_first["content"]
    assert second["content"] == native_second["content"]
    assert "Output truncated" in first["content"]
    assert "Output truncated" in second["content"]
    assert check_tool_pairing([assistant, edit.message]) is None


def test_prune_persistence_across_state_roundtrip():
    """Port of agent-session-prune-persistence.test.ts: rebuilding from serialized state matches."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": {"path": "a.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": FILE_CONTENT},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read", "arguments": {"path": "a.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c2", "name": "read", "content": FILE_CONTENT},
    ]
    ctx = _context(
        messages,
        prune={"enabled": True, "supersede_reads": True, "minimum_savings": 50, "protect_tokens": 0},
    )
    record = prune_tool_results(ctx)
    assert record is not None
    ctx.state.append(record, messages)

    live_view = ctx.state.project(messages, TARGET)

    # Serialize to JSON-compatible list and deserialize
    serialized = ctx.state.to_list()
    restored_state = CompactionState.from_list(serialized)
    restored_view = restored_state.project(messages, TARGET)

    assert live_view == restored_view
    assert check_tool_pairing(restored_view) is None
