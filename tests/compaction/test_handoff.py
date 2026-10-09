"""Tests for handoff compaction."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence
import pytest

from breadboard_engine.compaction.handoff import HandoffCompaction, generate_handoff
from breadboard_engine.compaction.methods import (
    Compactor,
    CompactionContext,
    CompactionRecord,
    MethodUnavailable,
    SummaryModel,
    SummaryRequest,
    SummaryResponse,
)
from breadboard_engine.compaction.settings import CompactionSettings
from breadboard_engine.compaction.soft import SoftCompaction
from breadboard_engine.compaction.state import CompactionState, ProjectionTarget


@dataclass
class FakeSummaryModel:
    responses: list[str] = field(default_factory=lambda: ["Handoff document content"])
    requests: list[SummaryRequest] = field(default_factory=list)

    def complete(self, request: SummaryRequest) -> SummaryResponse:
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self.responses) - 1)
        text = self.responses[idx]
        return SummaryResponse(text=text, model=request.model or "fake-model")


def _make_context(
    messages: Sequence[Mapping[str, Any]],
    summarizer: Optional[SummaryModel] = None,
    settings: Optional[CompactionSettings] = None,
    state: Optional[CompactionState] = None,
    context_window: int = 100_000,
    reason: str = "threshold",
    custom_instructions: Optional[str] = None,
) -> CompactionContext:
    return CompactionContext(
        messages=messages,
        state=state or CompactionState(),
        settings=settings or CompactionSettings(enabled=True, keep_recent_tokens=50),
        reason=reason,
        target=ProjectionTarget(provider="test", api="chat", model="test-model"),
        context_window=context_window,
        tokens_before=50_000,
        summarizer=summarizer,
        custom_instructions=custom_instructions,
    )


def test_handoff_raises_when_no_summarizer():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    ctx = _make_context(messages, summarizer=None)
    handoff = HandoffCompaction()
    with pytest.raises(MethodUnavailable, match="No summarizer"):
        handoff.run(ctx)


def test_handoff_raises_on_overflow_reason():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    ctx = _make_context(messages, summarizer=FakeSummaryModel(), reason="overflow")
    handoff = HandoffCompaction()
    with pytest.raises(MethodUnavailable, match="unavailable for overflow"):
        handoff.run(ctx)


def test_handoff_raises_when_nothing_to_hand_off():
    messages = [
        {"role": "user", "content": "hello"},
    ]
    ctx = _make_context(messages, summarizer=FakeSummaryModel(), settings=CompactionSettings(keep_recent_tokens=100_000))
    handoff = HandoffCompaction()
    with pytest.raises(MethodUnavailable, match="Nothing to hand off"):
        handoff.run(ctx)


def test_handoff_basic_run():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "task: fix login"},
        {
            "role": "assistant",
            "content": "reading auth.ts",
            "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": {"path": "src/auth.ts"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "function login() {}"},
        {"role": "user", "content": "now edit it"},
        {
            "role": "assistant",
            "content": "editing",
            "tool_calls": [{"id": "c2", "function": {"name": "edit", "arguments": {"path": "src/auth.ts"}}}],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "ok"},
        {"role": "user", "content": "check status"},
        {"role": "assistant", "content": "all set"},
    ]

    settings = CompactionSettings(enabled=True, keep_recent_tokens=30)
    summarizer = FakeSummaryModel(responses=["## Goal\nFix login\n\n## Progress\n### Done\n- [x] auth.ts"])
    ctx = _make_context(messages, summarizer=summarizer, settings=settings)

    handoff = HandoffCompaction()
    record = handoff.run(ctx)

    assert record.method == "handoff"
    assert record.is_boundary
    assert record.first_kept_index > 0
    assert "Fix login" in record.summary
    assert "<files>" in record.summary
    assert "auth.ts (RW)" in record.summary
    assert record.short_summary is None

    assert len(record.summary_messages) == 1
    msg = record.summary_messages[0]
    assert msg["role"] == "user"
    assert "<handoff>" in msg["content"]
    assert "</handoff>" in msg["content"]

    assert len(summarizer.requests) == 1
    assert summarizer.requests[0].purpose == "handoff"

    # State validation
    ctx.state.validate(record, messages)
    projected = CompactionState([record]).project(messages, ctx.target)
    assert projected[0]["role"] == "system"
    assert projected[1]["role"] == "user"
    assert "<handoff>" in projected[1]["content"]


def test_handoff_custom_instructions():
    messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "turn 1 reply"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "turn 2 reply"},
    ]
    summarizer = FakeSummaryModel()
    ctx = _make_context(
        messages,
        summarizer=summarizer,
        settings=CompactionSettings(keep_recent_tokens=20, custom_instructions="Focus on performance"),
        custom_instructions="Preserve DB queries",
    )
    handoff = HandoffCompaction()
    handoff.run(ctx)

    req = summarizer.requests[0]
    prompt_text = req.messages[-1]["content"]
    assert "Focus on performance" in prompt_text
    assert "Preserve DB queries" in prompt_text


def test_compactor_with_both_soft_and_handoff():
    # Large tool output fixture so handoff compaction achieves real token reduction
    messages = [
        {"role": "user", "content": "inspect codebase structure"},
        {
            "role": "assistant",
            "content": "listing directories",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": {"path": "large_tree.json"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "item data line in listing\n" * 400},
        {"role": "user", "content": "next action?"},
        {"role": "assistant", "content": "ready to proceed"},
    ]
    settings = CompactionSettings(
        enabled=True,
        method_order=("handoff", "soft"),
        keep_recent_tokens=30,
    )
    summarizer = FakeSummaryModel(responses=["## Goal\nHandoff doc summarizing tree"])
    ctx = _make_context(messages, summarizer=summarizer, settings=settings)

    compactor = Compactor(settings, {
        "soft": SoftCompaction(),
        "handoff": HandoffCompaction(),
    })
    outcome = compactor.run(ctx)

    assert outcome.compacted
    assert len(outcome.records) == 1
    assert outcome.records[0].method == "handoff"
    assert outcome.tokens_after < outcome.tokens_before
