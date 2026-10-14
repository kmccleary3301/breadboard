"""Image dropping reducer for context compaction.

Ports OMP ``dropImages`` / image elision from coding-agent session maintenance:
replaces image content parts in kept messages with OMP's text placeholder
(``[image removed]``).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Tuple
from ..provider.anthropic_replay import is_tool_result_replay, replay_messages
from ..provider.contract_wire import canonical_json

from .methods import CompactionContext, MethodUnavailable
from .state import CompactionRecord, MessageEdit, leading_system_count
from .tokens import is_image_part

IMAGE_PLACEHOLDER = "[image removed]"


def _strip_native_image_blocks(
    blocks: List[Any], placeholder: str,
) -> Tuple[List[Any], int]:
    kept = [block for block in blocks if not is_image_part(block)]
    removed = len(blocks) - len(kept)
    if removed and not kept:
        kept.append({"type": "text", "text": placeholder})
    return kept, removed


def _strip_anthropic_replay_images(content: List[Any], placeholder: str) -> int:
    if not any(
        isinstance(block, dict) and is_tool_result_replay(block)
        for block in content
    ):
        return 0

    native_messages = replay_messages(content)
    semantic_results = [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    removed = 0
    for message in native_messages:
        message["content"], count = _strip_native_image_blocks(
            message["content"], placeholder
        )
        removed += count

    native_results = [
        block
        for message in native_messages
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    for native, semantic in zip(native_results, semantic_results):
        native_content = native.get("content")
        if not isinstance(native_content, list):
            continue
        native_content, count = _strip_native_image_blocks(
            native_content, placeholder
        )
        if count:
            native["content"] = native_content
            semantic["content"] = canonical_json(native_content)
            removed += count
    return removed


def strip_images_from_message(
    message: Mapping[str, Any],
    placeholder: str = IMAGE_PLACEHOLDER,
) -> Tuple[Dict[str, Any], int]:
    """Strip or replace image blocks from ``message`` with ``placeholder``.

    Returns ``(new_message, count_removed)``.
    """
    removed = 0
    new_msg = copy.deepcopy(dict(message))

    # Strip top-level images list if present
    if "images" in new_msg and isinstance(new_msg["images"], list):
        removed += len(new_msg["images"])
        new_msg.pop("images", None)

    # Strip details.images if present
    details = new_msg.get("details")
    if isinstance(details, Mapping) and isinstance(details.get("images"), list):
        det_copy = dict(details)
        removed += len(det_copy["images"])
        det_copy.pop("images", None)
        new_msg["details"] = det_copy

    content = new_msg.get("content")
    if isinstance(content, list):
        removed += _strip_anthropic_replay_images(content, placeholder)
        kept: List[Any] = []
        for part in content:
            if is_image_part(part):
                removed += 1
                continue
            payload = part.get("payload") if isinstance(part, Mapping) and part.get("schema_version") == "openai.responses.tool_output.v1" else None
            output = payload.get("output") if isinstance(payload, Mapping) else None
            if isinstance(output, list) and any(is_image_part(item) for item in output):
                # Typed Responses tool output: keep the envelope and its call id, drop its images.
                text = [item for item in output if not is_image_part(item)]
                removed += len(output) - len(text)
                part = {**part, "payload": {**payload, "output": text or [{"type": "input_text", "text": placeholder}]}}
            kept.append(part)
        if removed > 0:
            if not kept:
                kept.append({"type": "text", "text": placeholder})
            new_msg["content"] = kept
    return new_msg, removed


def drop_images(context: CompactionContext) -> Optional[CompactionRecord]:
    """Execute image dropping on ``context``.

    Returns an edit-only ``CompactionRecord`` if any images were removed, or ``None``.
    """
    kept_start = context.state.kept_start(context.messages, context.target)
    # A boundary's protected prefix stays visible alongside the kept suffix.
    prefix = range(leading_system_count(context.messages), context.state.prefix_end(context.messages, context.target))

    current_messages: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in context.messages]
    for record in context.state.records:
        for edit in record.edits:
            current_messages[edit.index] = copy.deepcopy(dict(edit.message))

    edits: List[MessageEdit] = []
    total_removed = 0

    for i in [*prefix, *range(kept_start, len(current_messages))]:
        msg = current_messages[i]
        new_msg, count = strip_images_from_message(msg)
        if count > 0:
            total_removed += count
            edits.append(MessageEdit(index=i, message=new_msg))

    if total_removed == 0 or not edits:
        return None

    record = context.new_record(
        method="drop_images",
        edits=tuple(edits),
        details={"images_dropped": total_removed},
    )
    context.state.validate(record, context.messages)
    return record


class DropImagesCompaction:
    """Edit-only compaction method ('drop_images') that strips images."""

    name: str = "drop_images"

    def run(self, context: CompactionContext) -> CompactionRecord:
        record = drop_images(context)
        if record is None:
            raise MethodUnavailable("no images found to drop")
        return record
