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
from breadboard_engine.compaction.primitives.accounting import ESTIMATORS
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


CANONICAL_IMAGES = [
    {"type": "media", "kind": "image", "uri": "attachment://" + "a" * 64, "mime": "image/png"},
    {"type": "media", "kind": "codex_image_url", "uri": "https://example.test/" + "a" * 64, "detail": "high"},
]


@pytest.mark.parametrize("media", CANONICAL_IMAGES)
@pytest.mark.parametrize("estimator", sorted(ESTIMATORS))
def test_canonical_media_image_is_estimated_as_an_image(media, estimator):
    image = {"type": "input_image", "image_url": "data:image/png;base64,AA==",
             **({"detail": media["detail"]} if "detail" in media else {})}
    estimate = ESTIMATORS[estimator]
    assert estimate([{"role": "user", "content": [media]}]) == estimate([{"role": "user", "content": [image]}])


@pytest.mark.parametrize("media", CANONICAL_IMAGES)
def test_drop_images_removes_canonical_media_images(media):
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": [media]}]
    record = drop_images(_context(messages))
    assert record is not None
    assert record.edits[0].message["content"] == [{"type": "text", "text": IMAGE_PLACEHOLDER}]


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


def test_summary_media_strip_keeps_typed_tool_output_answering_its_call():
    from breadboard_engine.compaction.primitives.message_reduction import model_message
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message
    from breadboard_engine.provider.contract_requests import normalize_request_messages

    call = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_read", "type": "function", "function": {"name": "read", "arguments": "{}"}}]}
    result = responses_image_tool_result_message("call_read", [
        {"type": "input_text", "text": "Image read successfully"},
        {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
    ])
    stripped = model_message(result, strip_media=True)
    assert stripped["content"] == [
        {"type": "tool_result", "call_id": "call_read", "content": "Image read successfully", "is_error": False}]
    assert normalize_request_messages([call, stripped])[1]["content"] == stripped["content"]


@pytest.mark.parametrize("preset", ["codex@0.139.0", "opencode@1.2.17"])
def test_summary_carries_a_dropped_image_as_its_placeholder(preset):
    from breadboard_engine.compaction.presets import CompactionConfig, build_recipe
    from breadboard_engine.compaction.primitives.reducers import Reduction

    messages = [
        {"role": "user", "content": "Describe the screenshot I send next."},
        {"role": "assistant", "content": "Ready"},
        {"role": "user", "content": [PNG_BLOCK]},
    ]
    ctx = _context(messages, reason="overflow")
    ctx.state.append(drop_images(ctx), messages)
    step = build_recipe(CompactionConfig(True, preset, ctx.settings, {}, None, None)).pipeline.stages["summary"].step

    placed = step.placement.place(ctx, step.selector.select(ctx), Reduction(summary="S"))

    assert IMAGE_PLACEHOLDER in repr(placed)


def test_drop_images_strips_typed_tool_output_and_keeps_its_call():
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message
    from breadboard_engine.provider.contract_requests import normalize_request_messages

    messages = [
        {"role": "user", "content": "Look at the screenshot."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_view", "type": "function", "function": {"name": "view_image", "arguments": "{}"}}]},
        responses_image_tool_result_message("call_view", [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]),
    ]
    record = drop_images(_context(messages))

    [edit] = record.edits
    assert "input_image" not in repr(edit.message) and IMAGE_PLACEHOLDER in repr(edit.message)
    assert edit.message["content"][0]["payload"]["call_id"] == "call_view"
    normalize_request_messages([messages[1], edit.message])


def test_drop_images_strips_the_protected_prefix():
    from breadboard_engine.compaction.state import CompactionRecord

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "Original task"}, PNG_BLOCK]},
        {"role": "assistant", "content": "Started"},
        {"role": "user", "content": "Middle turn"},
        {"role": "assistant", "content": "Middle answer"},
        {"role": "user", "content": "Recent turn"},
    ]
    state = CompactionState()
    state.append(CompactionRecord(
        record_id="r1", sequence=0, method="summary", reason="threshold", created_at="t", tokens_before=1,
        history_length=5, first_kept_index=5, prefix_end=3, summary="S",
        summary_messages=({"role": "user", "content": "S"},),
    ), messages[:5])

    record = drop_images(_context(messages, state))

    assert [edit.index for edit in record.edits] == [1]
    state.append(record, messages)
    assert not any(has_images(m) for m in state.project(messages, TARGET))


def test_pi_estimator_counts_a_typed_tool_result_as_a_tool_result():
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message

    typed = responses_image_tool_result_message("c1", [{"type": "input_text", "text": "x" * 400}, INPUT_IMAGE_BLOCK])
    chat = {"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "x" * 400}, INPUT_IMAGE_BLOCK]}
    estimate = ESTIMATORS["pi_chars4"]
    assert estimate([typed]) == estimate([chat]) == (400 + 4800) // 4
