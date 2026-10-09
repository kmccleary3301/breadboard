from __future__ import annotations

from typing import Any, Dict, List, Optional
import pytest

from breadboard_engine.compaction.images import (
    IMAGE_PLACEHOLDER,
    DropImagesCompaction,
    drop_images,
    strip_images_from_message,
)
from breadboard_engine.compaction.methods import (
    CompactionContext,
    MethodUnavailable,
)
from breadboard_engine.compaction.settings import settings_from_config
from breadboard_engine.compaction.state import CompactionState, ProjectionTarget
from breadboard_engine.compaction.tokens import estimate_message_tokens
from breadboard_engine.compaction.transcript import check_tool_pairing, has_images

TARGET = ProjectionTarget("openai", "responses", "gpt-x")

PNG_BLOCK = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo"}}
INPUT_IMAGE_BLOCK = {"type": "input_image", "image_url": "https://example.com/a.png"}


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
# strip_images_from_message unit tests (ported from strip-images-from-message.test.ts)
# -----------------------------------------------------------------------------


def test_strip_images_from_user_message_keeps_text_in_order():
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "look at"},
            PNG_BLOCK,
            {"type": "text", "text": "and"},
            INPUT_IMAGE_BLOCK,
        ],
    }
    new_msg, removed = strip_images_from_message(msg)
    assert removed == 2
    assert new_msg["content"] == [
        {"type": "text", "text": "look at"},
        {"type": "text", "text": "and"},
    ]


def test_strip_images_leaves_text_only_untouched():
    msg = {
        "role": "user",
        "content": [{"type": "text", "text": "hi"}, {"type": "text", "text": "there"}],
    }
    new_msg, removed = strip_images_from_message(msg)
    assert removed == 0
    assert new_msg["content"] == msg["content"]


def test_strip_images_returns_zero_for_string_content():
    msg = {"role": "user", "content": "no images here"}
    new_msg, removed = strip_images_from_message(msg)
    assert removed == 0
    assert new_msg["content"] == "no images here"


def test_strip_images_inserts_placeholder_when_emptied():
    msg = {
        "role": "user",
        "content": [PNG_BLOCK, PNG_BLOCK],
    }
    new_msg, removed = strip_images_from_message(msg)
    assert removed == 2
    # Collapsed consecutive placeholders into one
    assert new_msg["content"] == [{"type": "text", "text": IMAGE_PLACEHOLDER}]


def test_strip_images_from_tool_result_content_and_details_images():
    msg = {
        "role": "tool",
        "tool_call_id": "tc1",
        "name": "generate_image",
        "content": [{"type": "text", "text": "generated"}, PNG_BLOCK],
        "details": {"images": [PNG_BLOCK, PNG_BLOCK], "imageCount": 2},
    }
    new_msg, removed = strip_images_from_message(msg)
    assert removed == 3
    assert new_msg["content"] == [{"type": "text", "text": "generated"}]
    assert "images" not in new_msg["details"]
    assert new_msg["details"]["imageCount"] == 2


def test_strip_images_clears_top_level_images_list():
    msg = {
        "role": "user",
        "content": "see attached",
        "images": [PNG_BLOCK],
    }
    new_msg, removed = strip_images_from_message(msg)
    assert removed == 1
    assert "images" not in new_msg


# -----------------------------------------------------------------------------
# drop_images integration tests
# -----------------------------------------------------------------------------


def test_drop_images_returns_none_when_no_images():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    ctx = _context(messages)
    assert drop_images(ctx) is None

    with pytest.raises(MethodUnavailable, match="no images found"):
        DropImagesCompaction().run(ctx)


def test_drop_images_ignores_images_before_kept_start():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [PNG_BLOCK]},
        {"role": "assistant", "content": "seen image"},
        {"role": "user", "content": "what next?"},
    ]
    # Set up boundary that keeps only index >= 3
    ctx = _context(messages)
    rec_bound = ctx.new_record(
        method="soft",
        first_kept_index=3,
        summary="earlier user had an image",
        summary_messages=[{"role": "user", "content": "summary"}],
    )
    ctx.state.append(rec_bound, messages)

    # Image is at index 1, kept_start is 3 -> no kept images to drop
    assert drop_images(ctx) is None


def test_drop_images_drops_kept_images_and_creates_valid_record():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "look at this"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "fetch"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "fetch",
            "content": [{"type": "text", "text": "result:"}, PNG_BLOCK],
        },
        {
            "role": "user",
            "content": [PNG_BLOCK],
        },
    ]
    ctx = _context(messages)
    record = DropImagesCompaction().run(ctx)
    assert record.method == "drop_images"
    assert not record.is_boundary
    assert len(record.edits) == 2
    assert record.details["images_dropped"] == 2

    # Validate with CompactionState
    ctx.state.validate(record, messages)
    ctx.state.append(record, messages)

    view = ctx.state.project(messages, TARGET)
    assert check_tool_pairing(view) is None
    assert not has_images(view[3])
    assert not has_images(view[4])
    assert view[3]["content"] == [{"type": "text", "text": "result:"}]
    assert view[4]["content"] == [{"type": "text", "text": IMAGE_PLACEHOLDER}]


def test_drop_images_composes_with_other_edits():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "fetch"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "fetch",
            "content": [{"type": "text", "text": "old data " * 500}, PNG_BLOCK],
        },
    ]
    ctx = _context(messages)

    # First pass: drop images
    rec1 = DropImagesCompaction().run(ctx)
    assert rec1 is not None
    ctx.state.append(rec1, messages)

    # Second pass: edit message content (e.g. prune or shake edit)
    edited_msg = dict(ctx.state.project(messages, TARGET)[3])
    edited_msg["content"] = [{"type": "text", "text": "[pruned]"}]
    # Convert dict edit to MessageEdit for proper typing
    from breadboard_engine.compaction.state import MessageEdit
    rec2 = ctx.new_record(
        method="prune",
        edits=[MessageEdit(index=3, message=edited_msg)],
    )
    ctx.state.append(rec2, messages)

    view = ctx.state.project(messages, TARGET)
    assert check_tool_pairing(view) is None
    assert view[3]["content"] == [{"type": "text", "text": "[pruned]"}]
    assert not has_images(view[3])
