from __future__ import annotations

import pytest

from breadboard_engine.compaction import (
    NATIVE_MARKER_KEY,
    CompactionCancelled,
    CompactionContext,
    CompactionState,
    CompactionStateError,
    Compactor,
    MessageEdit,
    MethodUnavailable,
    NativeCompaction,
    ProjectionTarget,
    check_tool_pairing,
    context_tokens_from_usage,
    estimate_messages_tokens,
    is_context_overflow,
    resolve_threshold_tokens,
    settings_from_config,
    should_compact,
)
from breadboard_engine.provider.contract_runtime import ProviderRuntimeError

TARGET = ProjectionTarget("openai", "responses", "gpt-x")


def _history(groups: int = 6, tool_chars: int = 4000) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(groups):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * tool_chars})
    return messages


def _context(messages, state=None, *, reason="threshold", window=4000, **config) -> CompactionContext:
    settings = settings_from_config({"enabled": True, **config})
    return CompactionContext(
        messages=messages,
        state=state or CompactionState(),
        settings=settings,
        reason=reason,
        target=TARGET,
        context_window=window,
        tokens_before=estimate_messages_tokens(messages),
    )


class _Boundary:
    name = "soft"

    def __init__(self, first_kept: int) -> None:
        self.first_kept = first_kept

    def run(self, context):
        return context.new_record(
            method=self.name,
            first_kept_index=self.first_kept,
            summary="S",
            summary_messages=[{"role": "user", "content": "<summary>S</summary>"}],
        )


class _Raises:
    def __init__(self, name: str, error: BaseException) -> None:
        self.name = name
        self.error = error

    def run(self, context):
        raise self.error


class _ShrinkTool:
    """Edit-only method that empties one tool result."""

    name = "shake"

    def __init__(self, index: int) -> None:
        self.index = index

    def run(self, context):
        original = context.messages[self.index]
        return context.new_record(method=self.name, edits=[MessageEdit(self.index, {**original, "content": "[elided]"})])


# --- settings --------------------------------------------------------------


@pytest.mark.parametrize(
    ("strategy", "remote", "expected"),
    [
        ("context-full", True, ("remote", "soft")),
        ("context-full", False, ("soft",)),
        ("handoff", True, ("handoff", "remote", "soft")),
        ("shake", False, ("shake", "soft")),
        ("shake-summary", True, ("shake", "remote", "soft")),
        ("snapcompact", False, ("snapcompact", "soft")),
        ("off", True, ()),
    ],
)
def test_legacy_strategy_migrates_to_method_order_like_omp(strategy, remote, expected):
    settings = settings_from_config({"enabled": True, "strategy": strategy, "remoteEnabled": remote})
    assert settings.method_order == expected
    assert settings.active is bool(expected)


def test_remote_disabled_without_strategy_filters_default_order():
    settings = settings_from_config({"enabled": True, "remoteEnabled": False})
    assert settings.method_order == ("snapcompact", "handoff", "shake", "soft")


def test_compaction_is_disabled_unless_configured():
    assert not settings_from_config(None).active
    assert not settings_from_config({"methodOrder": ["soft"]}).active


@pytest.mark.parametrize(
    "raw",
    [
        {"strategy": "handoff", "methodOrder": ["soft"]},
        {"keepRecentTokens": 1, "keep_recent_tokens": 2},
        {"methodOrder": ["soft", "summarize"]},
        {"unknownKey": 1},
        {"prune": {"protectTokenz": 1}},
        {"overflowPolicy": "retry"},
    ],
)
def test_invalid_config_is_rejected(raw):
    with pytest.raises(ValueError):
        settings_from_config(raw)


def test_threshold_uses_window_minus_reserve_by_default():
    settings = settings_from_config({"enabled": True})
    # max(15% of window, 16384) reserved below the window
    assert resolve_threshold_tokens(131072, settings) == 131072 - 19660
    assert resolve_threshold_tokens(200000, settings) == 200000 - 30000


def test_defaulted_reserve_recovers_on_small_windows_but_explicit_reserve_wins():
    defaulted = settings_from_config({"enabled": True})
    assert resolve_threshold_tokens(16000, defaulted) == 16000 - 2400
    explicit = settings_from_config({"enabled": True, "reserveTokens": 8000})
    assert resolve_threshold_tokens(16000, explicit) == 16000 - 8000


def test_fixed_and_percent_thresholds():
    assert resolve_threshold_tokens(1000, settings_from_config({"enabled": True, "thresholdTokens": 5000})) == 999
    assert resolve_threshold_tokens(1000, settings_from_config({"enabled": True, "thresholdPercent": 50})) == 500
    settings = settings_from_config({"enabled": True, "thresholdTokens": 500})
    assert not should_compact(500, 1000, settings)
    assert should_compact(501, 1000, settings)
    assert not should_compact(10**6, 1000, settings_from_config({"enabled": False}))


# --- token accounting and overflow -----------------------------------------


def test_usage_tokens_add_anthropic_cache_fields_but_not_openai_cached():
    assert context_tokens_from_usage({"input_tokens": 10, "cache_read_input_tokens": 100, "output_tokens": 5}) == 115
    assert context_tokens_from_usage(
        {"prompt_tokens": 110, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 100}}
    ) == 115
    assert context_tokens_from_usage({"output_tokens": 5}) is None


@pytest.mark.parametrize(
    "error",
    [
        {"error": {"message": "context full", "type": "invalid_request_error", "code": "context_length_exceeded"}},
        "This model's maximum context length is 131072 tokens. However, you requested 163840 tokens",
        "prompt is too long: 210000 tokens > 200000 maximum",
        ProviderRuntimeError("bad request", details={"status": 400, "error": {"code": "context_length_exceeded"}}),
    ],
)
def test_context_overflow_is_recognized(error):
    assert is_context_overflow(error)


def test_overflow_found_through_exception_cause_chain():
    try:
        try:
            raise ValueError("Input exceeds the context window of this model")
        except ValueError as inner:
            raise RuntimeError("provider call failed") from inner
    except RuntimeError as outer:
        assert is_context_overflow(outer)


@pytest.mark.parametrize(
    "error",
    [
        "Rate limit reached for requests",
        ProviderRuntimeError("server error", details={"status": 500, "code": "server_error"}),
        {"error": {"code": "invalid_api_key"}},
    ],
)
def test_other_failures_are_not_overflow(error):
    assert not is_context_overflow(error)


# --- state and projection ---------------------------------------------------


def test_boundary_projection_keeps_system_head_and_tool_pairs():
    messages = _history()
    state = CompactionState()
    context = _context(messages, state)
    state.append(_Boundary(10).run(context), messages)
    view = state.project(messages, TARGET)
    assert [m["role"] for m in view] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert view[1]["content"] == "<summary>S</summary>"
    assert view[2:] == messages[10:]
    assert check_tool_pairing(view) is None
    assert messages == _history(), "full history must not be mutated"


@pytest.mark.parametrize(
    ("first_kept", "error"),
    [(3, "orphans a tool result"), (0, "outside"), (99, "outside")],
)
def test_invalid_boundaries_are_rejected(first_kept, error):
    messages = _history()
    context = _context(messages)
    with pytest.raises(CompactionStateError, match=error):
        context.state.validate(_Boundary(first_kept).run(context), messages)


def test_boundary_cannot_move_backwards():
    messages = _history()
    state = CompactionState()
    state.append(_Boundary(10).run(_context(messages, state)), messages)
    with pytest.raises(CompactionStateError, match="backwards"):
        state.validate(_Boundary(6).run(_context(messages, state)), messages)


def test_edits_must_preserve_role_and_tool_identity():
    messages = _history()
    context = _context(messages)
    bad = context.new_record(method="shake", edits=[MessageEdit(3, {"role": "tool", "tool_call_id": "c9", "content": ""})])
    with pytest.raises(CompactionStateError, match="tool_call_id"):
        context.state.validate(bad, messages)


def test_native_payload_replays_only_to_matching_route_and_falls_back_to_readable():
    messages = _history()
    state = CompactionState()
    state.append(_Boundary(6).run(_context(messages, state)), messages)
    native = NativeCompaction("openai", "responses", "gpt-x", ({"type": "compaction", "encrypted_content": "e"},), 50)
    record = _context(messages, state).new_record(method="remote", first_kept_index=10, native=native)
    state.append(record, messages)

    same = state.project(messages, TARGET)
    assert same[1][NATIVE_MARKER_KEY]["items"] == [{"type": "compaction", "encrypted_content": "e"}]
    assert same[2:] == messages[10:]

    other = state.project(messages, ProjectionTarget("anthropic", "messages", "claude"))
    assert NATIVE_MARKER_KEY not in other[1]
    assert other[1]["content"] == "<summary>S</summary>"
    assert other[2:] == messages[6:]


def test_state_round_trips_and_record_ids_are_content_derived():
    messages = _history()
    state = CompactionState()
    state.append(_Boundary(10).run(_context(messages, state)), messages)
    restored = CompactionState.from_list(state.to_list())
    assert restored.project(messages, TARGET) == state.project(messages, TARGET)
    again = CompactionState()
    assert _Boundary(10).run(_context(messages, again)).record_id == state.records[0].record_id


# --- cascade ----------------------------------------------------------------


def test_cascade_skips_unavailable_and_failed_methods():
    messages = _history()
    context = _context(messages, keepRecentTokens=2500, methodOrder=["remote", "handoff", "soft"])
    methods = {
        "remote": _Raises("remote", MethodUnavailable("no native route")),
        "handoff": _Raises("handoff", RuntimeError("summary model failed")),
        "soft": _Boundary(10),
    }
    outcome = Compactor(context.settings, methods).run(context)
    assert [(a.method, a.status) for a in outcome.attempts] == [
        ("remote", "unavailable"),
        ("handoff", "failed"),
        ("soft", "applied"),
    ]
    assert outcome.reached_target and outcome.tokens_after < outcome.tokens_before
    assert len(context.state.records) == 1


def test_cascade_discards_records_that_do_not_shrink_the_view():
    messages = _history()
    context = _context(messages, methodOrder=["handoff", "soft"])
    head_only = _Boundary(2)  # summarizes only the short task message
    outcome = Compactor(context.settings, {"handoff": head_only, "soft": _Boundary(10)}).run(context)
    assert [(a.method, a.status) for a in outcome.attempts] == [("handoff", "no_progress"), ("soft", "applied")]


def test_partial_edit_pass_stacks_with_following_boundary():
    messages = _history()
    context = _context(messages, methodOrder=["shake", "soft"])
    outcome = Compactor(context.settings, {"shake": _ShrinkTool(11), "soft": _Boundary(10)}).run(context)
    assert [a.status for a in outcome.attempts] == ["applied", "applied"]
    view = context.state.project(messages, TARGET)
    assert [m.get("content") for m in view[2:]] == ["", "[elided]", "", messages[13]["content"]]


def test_cascade_stops_once_target_is_reached():
    messages = _history()
    context = _context(messages, window=100000, methodOrder=["soft", "shake"])
    context.tokens_before = 99999
    context.reason = "manual"
    called = []

    class _Recorder(_ShrinkTool):
        def run(self, ctx):
            called.append(True)
            return super().run(ctx)

    Compactor(context.settings, {"soft": _Boundary(10), "shake": _Recorder(11)}).run(context)
    assert called == []


def test_cancellation_stops_the_cascade():
    messages = _history()
    context = _context(messages, methodOrder=["remote", "soft"])
    methods = {"remote": _Raises("remote", CompactionCancelled()), "soft": _Boundary(10)}
    with pytest.raises(CompactionCancelled):
        Compactor(context.settings, methods).run(context)
    assert context.state.records == ()


def test_overflow_target_is_window_minus_reserve():
    messages = _history()
    context = _context(messages, reason="overflow", window=4000, keepRecentTokens=2500)
    assert context.target_tokens == 4000 - 600
