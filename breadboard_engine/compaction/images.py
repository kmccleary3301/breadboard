"""Image dropping reducer for context compaction.

Ports OMP ``dropImages`` / image elision from coding-agent session maintenance:
replaces image content parts in kept messages with OMP's text placeholder
(``[image removed]``).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .methods import CompactionContext, MethodUnavailable
from .state import CompactionRecord, MessageEdit
from .transcript import has_images

IMAGE_PLACEHOLDER = "[image removed]"


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
        kept: List[Any] = []
        for part in content:
            if isinstance(part, Mapping) and part.get("type") in {"image", "image_url", "input_image"}:
                removed += 1
            else:
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
    kept_start = context.state.kept_start(context.messages)

    current_messages: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in context.messages]
    for record in context.state.records:
        for edit in record.edits:
            current_messages[edit.index] = copy.deepcopy(dict(edit.message))

    edits: List[MessageEdit] = []
    total_removed = 0

    for i in range(kept_start, len(current_messages)):
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
