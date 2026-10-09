"""Tests for SnapcompactCompaction method (archive selection, budgeting, dead-end, no-reduction)."""

import base64
import io
import pytest
from PIL import Image

from breadboard_engine.compaction.methods import (
    CompactionContext,
    CompactionRecord,
    MethodUnavailable,
)
from breadboard_engine.compaction.settings import CompactionSettings, SnapcompactSettings
from breadboard_engine.compaction.snapcompact import SnapcompactCompaction
from breadboard_engine.compaction.snapcompact.archive import Archive
from breadboard_engine.compaction.state import CompactionState, ProjectionTarget


def _make_context(
    messages: list[dict],
    *,
    supports_images: bool = True,
    context_window: int = 200_000,
    keep_recent_tokens: int = 50,
    state: CompactionState | None = None,
    reserve_tokens: int | None = None,
    settings: CompactionSettings | None = None,
) -> CompactionContext:
    if settings is None:
        settings = CompactionSettings(
            enabled=True,
            keep_recent_tokens=keep_recent_tokens,
            reserve_tokens=reserve_tokens,
            snapcompact=SnapcompactSettings(),
        )
    return CompactionContext(
        messages=messages,
        state=state or CompactionState(),
        settings=settings,
        reason="threshold",
        target=ProjectionTarget(provider="anthropic", api="anthropic-messages", model="claude-sonnet-4-5"),
        context_window=context_window,
        tokens_before=100_000,
        supports_images=supports_images,
    )


def test_image_capability_gating():
    """Non-vision target MUST raise MethodUnavailable."""
    method = SnapcompactCompaction()
    messages = [
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "answer here"},
    ]
    ctx = _make_context(messages, supports_images=False)
    with pytest.raises(MethodUnavailable, match="vision-capable"):
        method.run(ctx)


def test_kept_history_alone_exceeds_budget():
    """If kept-recent history alone exceeds context budget, raise MethodUnavailable."""
    method = SnapcompactCompaction()
    # Tiny context window of 500 tokens, but kept-recent is large
    huge_text = "word " * 1000
    messages = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": huge_text},
    ]
    ctx = _make_context(messages, context_window=500, keep_recent_tokens=50)
    with pytest.raises(MethodUnavailable, match="kept history alone exceeds"):
        method.run(ctx)


def test_no_reduction_guard():
    """Small message history where frames cost more than original text MUST raise MethodUnavailable."""
    method = SnapcompactCompaction()
    # A short conversation: original text is only ~50 tokens.
    # Snapcompact frames will cost thousands of tokens, so it would inflate context.
    messages = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
        {"role": "user", "content": "third question"},
    ]
    ctx = _make_context(messages, keep_recent_tokens=1)
    with pytest.raises(MethodUnavailable, match="would not reduce context"):
        method.run(ctx)


def test_opaque_reasoning_excluded_symmetrically():
    """Opaque reasoning signatures MUST NOT inflate the reduction baseline."""
    method = SnapcompactCompaction()
    # Tiny real conversation with a huge opaque reasoning signature
    big_signature = "x" * 200_000
    messages = [
        {"role": "user", "content": "first question"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "short thought", "thinkingSignature": big_signature},
                {"type": "text", "text": "first answer"},
            ],
        },
        {"role": "user", "content": "second question"},
    ]
    ctx = _make_context(messages, keep_recent_tokens=1)
    # The real content is tiny. Without symmetrical exclusion, the 200k signature
    # would make the baseline look huge and permit inflating frames. With exclusion,
    # it is correctly rejected as no-reduction!
    with pytest.raises(MethodUnavailable, match="would not reduce context"):
        method.run(ctx)


def test_successful_compaction_long_history():
    """Long history produces a valid boundary record with decodable PNG pages."""
    method = SnapcompactCompaction()
    # Create substantial history so compaction achieves genuine reduction
    filler = "This is a detailed analysis of the performance characteristics of our distributed queue. " * 250
    messages = [
        {"role": "user", "content": f"Investigate latency issue: {filler}"},
        {
            "role": "assistant",
            "content": "Analyzing traces...",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {"name": "read", "arguments": {"path": "src/queue.ts"}},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": f"Queue implementation:\n{filler}"},
        {"role": "assistant", "content": f"Found the lock contention:\n{filler}"},
        {"role": "user", "content": "Recent question about next steps?"},
        {"role": "assistant", "content": "I will proceed with the fix."},
    ]

    ctx = _make_context(messages, keep_recent_tokens=100)
    record = method.run(ctx)

    assert isinstance(record, CompactionRecord)
    assert record.method == "snapcompact"
    assert record.is_boundary
    assert record.first_kept_index is not None
    assert record.first_kept_index > 0

    # Summary messages: one user message
    assert len(record.summary_messages) == 1
    summary_msg = record.summary_messages[0]
    assert summary_msg["role"] == "user"
    content = summary_msg["content"]
    assert isinstance(content, list)

    # First block is preamble text
    assert content[0]["type"] == "text"
    assert "HISTORY" in content[0]["text"]
    assert "queue.ts (Read)" in content[0]["text"]

    # Image blocks
    image_blocks = [b for b in content if b.get("type") == "image_url"]
    assert len(image_blocks) > 0
    for block in image_blocks:
        url = block["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        b64_data = url[len("data:image/png;base64,") :]
        raw_bytes = base64.b64decode(b64_data)
        # Decode via Pillow
        img = Image.open(io.BytesIO(raw_bytes))
        assert img.format == "PNG"
        assert img.width > 0
        assert img.height > 0

    # Details has archive data
    assert "snapcompact_archive" in record.details
    archive_data = record.details["snapcompact_archive"]
    assert archive_data["totalChars"] > 0
    assert len(archive_data["frames"]) == len(image_blocks)


def test_dead_end_frame_rescue():
    """Over-budget trailing archive is rescued with smaller frame count when nothing new to summarize."""
    method = SnapcompactCompaction()

    # Pre-seed state with an existing boundary record carrying 6 frames
    fake_frames = [
        {"data": "ZmFrZQ==", "mimeType": "image/png", "cols": 64, "rows": 40, "chars": 10}
        for _ in range(6)
    ]
    preserved_archive = {
        "frames": fake_frames,
        "totalChars": 60,
        "truncatedChars": 0,
        "text": "HEAD sentinel. " + "Important fact. " * 300 + "TAIL sentinel.",
    }
    prior_content = [{"type": "text", "text": "Preamble"}]
    for f in fake_frames:
        prior_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{f['data']}"}})
    prior_record = CompactionRecord(
        record_id="prior-1",
        sequence=1,
        method="snapcompact",
        reason="threshold",
        created_at="2026-10-08T00:00:00Z",
        tokens_before=80_000,
        history_length=3,
        first_kept_index=2,
        summary="Previous summary preamble",
        short_summary="Archived 6 frames",
        summary_messages=({"role": "user", "content": prior_content},),
        details={"snapcompact_archive": preserved_archive, "preserve_data": {"snapcompact": preserved_archive}},
    )
    state = CompactionState([prior_record])

    messages = [
        {"role": "user", "content": "old 1"},
        {"role": "assistant", "content": "old 2"},
        {"role": "user", "content": "recent"},
    ]

    # With tight context window, rescue shrinks the frame count
    ctx = _make_context(messages, state=state, context_window=20_000, keep_recent_tokens=50_000)
    rescue_record = method.run(ctx)
    assert rescue_record.is_boundary
    res_arch = rescue_record.details["snapcompact_archive"]
    assert len(res_arch["frames"]) < 6


def test_compactor_cascade_with_snapcompact():
    """Compactor executes snapcompact on vision models, and cascades to fallback on non-vision."""
    from breadboard_engine.compaction.methods import Compactor

    filler = "Detailed data pipeline message for compaction tests. " * 800
    messages = [
        {"role": "user", "content": f"Task: {filler}"},
        {"role": "assistant", "content": f"Result: {filler}"},
        {"role": "user", "content": "Recent question?"},
        {"role": "assistant", "content": "Recent reply."},
    ]

    # 1. Vision capable: snapcompact applied
    settings = CompactionSettings(
        enabled=True,
        method_order=("snapcompact", "soft"),
        keep_recent_tokens=100,
    )
    ctx_vision = _make_context(messages, settings=settings, supports_images=True)
    compactor = Compactor(settings, {"snapcompact": SnapcompactCompaction()})
    outcome = compactor.run(ctx_vision)
    assert outcome.compacted
    assert len(outcome.records) == 1
    assert outcome.records[0].method == "snapcompact"
    assert outcome.attempts[0].status == "applied"

    # 2. Non-vision target: snapcompact reports unavailable, cascades to fallback
    class MockFallback:
        name = "fallback"
        def run(self, context):
            return context.new_record(
                method="fallback",
                first_kept_index=2,
                summary="Fallback summary",
                short_summary="Fallback",
                summary_messages=({"role": "user", "content": "Fallback summary"},),
            )

    fallback_settings = CompactionSettings(
        enabled=True,
        method_order=("snapcompact", "fallback"),
        keep_recent_tokens=100,
    )
    ctx_novision = _make_context(messages, settings=fallback_settings, supports_images=False)
    compactor_novision = Compactor(fallback_settings, {"snapcompact": SnapcompactCompaction(), "fallback": MockFallback()})
    outcome_novision = compactor_novision.run(ctx_novision)
    assert outcome_novision.compacted
    assert outcome_novision.attempts[0].method == "snapcompact"
    assert outcome_novision.attempts[0].status == "unavailable"
    assert outcome_novision.attempts[1].method == "fallback"
    assert outcome_novision.attempts[1].status == "applied"


def test_pillow_missing_raises_method_unavailable(monkeypatch):
    """When Pillow is missing, SnapcompactCompaction raises MethodUnavailable."""
    import builtins
    orig_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("No module named 'PIL'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    method = SnapcompactCompaction()
    ctx = _make_context([{"role": "user", "content": "hi"}], supports_images=True)
    with pytest.raises(MethodUnavailable, match="Pillow is required"):
        method.run(ctx)
