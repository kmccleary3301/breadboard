"""Tests for LLM-backed soft compaction."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence
import pytest

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
    """Mock SummaryModel recording requests and returning configured responses."""

    responses: list[str] = field(default_factory=lambda: ["Generated summary"])
    requests: list[SummaryRequest] = field(default_factory=list)
    raise_on_request: Optional[Exception] = None

    def complete(self, request: SummaryRequest) -> SummaryResponse:
        self.requests.append(request)
        if self.raise_on_request:
            raise self.raise_on_request
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


def test_soft_compaction_raises_when_no_summarizer():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    ctx = _make_context(messages, summarizer=None)
    soft = SoftCompaction()
    with pytest.raises(MethodUnavailable, match="No summarizer"):
        soft.run(ctx)


def test_soft_compaction_raises_when_nothing_to_summarize():
    messages = [
        {"role": "user", "content": "hello"},
    ]
    ctx = _make_context(messages, summarizer=FakeSummaryModel(), settings=CompactionSettings(keep_recent_tokens=100_000))
    soft = SoftCompaction()
    with pytest.raises(MethodUnavailable, match="Nothing to summarize"):
        soft.run(ctx)


def test_soft_compaction_basic_run():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "turn 1: write foo.py"},
        {
            "role": "assistant",
            "content": "writing",
            "tool_calls": [{"id": "c1", "function": {"name": "write", "arguments": {"path": "foo.py"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "turn 2: read foo.py"},
        {
            "role": "assistant",
            "content": "reading",
            "tool_calls": [{"id": "c2", "function": {"name": "read", "arguments": {"path": "foo.py"}}}],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "file contents"},
        {"role": "user", "content": "turn 3: what is in foo.py?" + " context " * 20},
        {"role": "assistant", "content": "it has file contents" + " context " * 20},
    ]
    settings = CompactionSettings(enabled=True, keep_recent_tokens=50)
    summarizer = FakeSummaryModel(responses=["Summary of work done", "Short summary"])
    ctx = _make_context(messages, summarizer=summarizer, settings=settings)

    soft = SoftCompaction()
    record = soft.run(ctx)

    assert record.method == "soft"
    assert record.is_boundary
    assert record.first_kept_index >= 7
    assert record.summary is not None
    assert "Summary of work done" in record.summary
    assert "<files>" in record.summary
    assert "foo.py (RW)" in record.summary
    assert record.short_summary == "Short summary"
    assert len(record.summary_messages) == 1
    assert record.summary_messages[0]["role"] == "user"
    assert "<summary>" in record.summary_messages[0]["content"]

    # Verify state validates and projects
    ctx.state.validate(record, messages)
    projected = CompactionState([record]).project(messages, ctx.target)
    assert projected[0]["role"] == "system"
    assert projected[1]["role"] == "user"
    assert "<summary>" in projected[1]["content"]


def test_soft_compaction_second_pass_update_summary():
    messages = [
        {"role": "user", "content": "first turn " + "data " * 20},
        {"role": "assistant", "content": "first reply " + "data " * 20},
        {"role": "user", "content": "second turn " + "data " * 20},
        {"role": "assistant", "content": "second reply " + "data " * 20},
        {"role": "user", "content": "third turn " + "data " * 20},
        {"role": "assistant", "content": "third reply " + "data " * 20},
    ]
    summarizer1 = FakeSummaryModel(responses=["Initial summary", "Short 1"])
    ctx1 = _make_context(messages[:4], summarizer=summarizer1, settings=CompactionSettings(keep_recent_tokens=30))
    soft = SoftCompaction()
    rec1 = soft.run(ctx1)

    state = CompactionState([rec1])

    summarizer2 = FakeSummaryModel(responses=["Updated summary", "Short 2"])
    ctx2 = _make_context(messages, summarizer=summarizer2, state=state, settings=CompactionSettings(keep_recent_tokens=30))
    rec2 = soft.run(ctx2)

    assert rec2.sequence == 1
    requests = summarizer2.requests
    assert len(requests) >= 1
    history_req = requests[0]
    prompt_text = history_req.messages[0]["content"]
    assert "<previous-summary>" in prompt_text
    assert "Initial summary" in prompt_text


def test_soft_compaction_split_turn_handling():
    messages = [
        {"role": "user", "content": "start a big turn"},
        {
            "role": "assistant",
            "content": "step 1",
            "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": {"path": "a.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "content a"},
        {
            "role": "assistant",
            "content": "step 2",
            "tool_calls": [{"id": "c2", "function": {"name": "read", "arguments": {"path": "b.txt"}}}],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "content b"},
        {"role": "assistant", "content": "final answer of turn " + "x" * 200},
    ]
    settings = CompactionSettings(enabled=True, keep_recent_tokens=60)
    summarizer = FakeSummaryModel(responses=["Prefix summary", "Turn context summary", "Short summary"])
    ctx = _make_context(messages, summarizer=summarizer, settings=settings)

    soft = SoftCompaction()
    record = soft.run(ctx)

    assert "**Turn Context (split turn):**" in record.summary
    purposes = [r.purpose for r in summarizer.requests]
    assert "turn_prefix" in purposes


def test_soft_compaction_oversized_folding():
    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": f"turn {i} " + "data " * 400})
        messages.append({"role": "assistant", "content": f"reply {i} " + "response " * 400})

    context_window = 4000
    summarizer = FakeSummaryModel(responses=[f"Folded summary {i}" for i in range(10)])
    ctx = _make_context(messages, summarizer=summarizer, context_window=context_window, settings=CompactionSettings(keep_recent_tokens=500))

    soft = SoftCompaction()
    record = soft.run(ctx)

    assert record.is_boundary
    summary_calls = [r for r in summarizer.requests if r.purpose in ("summary", "update_summary")]
    assert len(summary_calls) > 1
    assert "<previous-summary>" in summary_calls[1].messages[0]["content"]


def test_soft_compaction_context_overflow_halving():
    # Large messages that exceed window budget
    messages = [
        {"role": "user", "content": "user " + "word " * 1500},
        {"role": "assistant", "content": "assistant " + "word " * 1500},
        {"role": "user", "content": "recent turn"},
    ]

    class OverflowOnceSummarizer:
        def __init__(self):
            self.requests = []
            self.calls = 0

        def complete(self, request: SummaryRequest) -> SummaryResponse:
            self.requests.append(request)
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("This model's maximum context length is 1000 tokens")
            return SummaryResponse(text=f"Summary after {self.calls}", model="mock")

    summarizer = OverflowOnceSummarizer()
    ctx = _make_context(messages, summarizer=summarizer, context_window=40_000, settings=CompactionSettings(keep_recent_tokens=50))
    soft = SoftCompaction()
    record = soft.run(ctx)

    assert record.is_boundary
    assert summarizer.calls > 1


def test_compactor_integration_with_soft():
    # Large tool output fixture so compaction achieves real token reduction
    messages = [
        {"role": "user", "content": "analyze big log"},
        {
            "role": "assistant",
            "content": "reading logs",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": {"path": "server.log"}}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "error log entry line\n" * 400},
        {"role": "user", "content": "what errors?"},
        {"role": "assistant", "content": "connection timeouts"},
    ]
    settings = CompactionSettings(enabled=True, method_order=("soft",), keep_recent_tokens=30)
    summarizer = FakeSummaryModel(responses=["Compacted summary of log errors", "Short summary"])
    ctx = _make_context(messages, summarizer=summarizer, settings=settings)

    compactor = Compactor(settings, {"soft": SoftCompaction()})
    outcome = compactor.run(ctx)

    assert outcome.compacted
    assert len(outcome.records) == 1
    assert outcome.records[0].method == "soft"
    assert outcome.tokens_after < outcome.tokens_before
