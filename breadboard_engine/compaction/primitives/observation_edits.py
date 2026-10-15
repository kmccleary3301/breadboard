"""Deterministic edits of visible tool observations; originals remain in history."""

import hashlib
import json
from typing import Any, List, Mapping, Optional, Tuple

from ..methods import MethodUnavailable
from ..state import MessageEdit
from ..transcript import answers_tool_call, result_parts, rewrite_result_content
from ...provider.anthropic_replay import is_tool_result_replay, native_tool_results
from .protected_windows import visible_messages


def _result_candidates(
    message: Mapping[str, Any],
) -> List[Tuple[Any, Any, Mapping[str, Any], Optional[int]]]:
    """Return result bodies, error flags, sidefacts, and optional rewrite block positions."""
    content = message.get("content")
    if isinstance(content, str):
        return [(content, False, {}, None)]
    if not isinstance(content, list) or not answers_tool_call(message):
        return []

    candidates = []
    unaddressable_result = False
    # The validator binds each canonical result to its native peer by ordinal; IDs may repeat.
    native_results = []
    if any(isinstance(block, dict) and is_tool_result_replay(block) for block in content):
        native_results = native_tool_results(content)
    result_ordinal = -1
    for position, block in enumerate(content):
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "tool_result":
            result_ordinal += 1
            call_id = block.get("call_id")
            if not isinstance(call_id, str):
                unaddressable_result = True
                continue
            native_result = native_results[result_ordinal] if result_ordinal < len(native_results) else None
            result_content = block.get("content")
            is_error = block.get("is_error", False)
            sidefacts = {
                key: value for key, value in block.items()
                if key not in {"type", "call_id", "content", "is_error"}
            }
            if native_result is not None:
                result_content = native_result.get("content")
                is_error = native_result.get("is_error", False)
                sidefacts.update({
                    key: value for key, value in native_result.items()
                    if key not in {"type", "tool_use_id", "content", "is_error"}
                })
            candidates.append((result_content, is_error, sidefacts, position))
            continue

        if block.get("type") != "provider_replay" or block.get("schema_version") != "openai.responses.tool_output.v1":
            continue
        payload = block.get("payload")
        call_id = payload.get("call_id") if isinstance(payload, Mapping) else None
        if isinstance(payload, Mapping) and isinstance(call_id, str) and "output" in payload:
            candidates.append(([block], False, {}, position))

    if candidates or unaddressable_result:
        return candidates
    return [(content, False, {}, None)]


def _result_digest(
    content: Any,
    is_error: Any,
    sidefacts: Mapping[str, Any],
) -> Tuple[bytes, int]:
    """Hash projected text and ordered structured content without carrier metadata."""
    projected_parts = result_parts(content)
    text_parts = []
    has_structured_parts = False
    for part in projected_parts:
        if (
            isinstance(part, Mapping)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            text_parts.append(part["text"])
            if not set(part).issubset({"type", "text"}):
                has_structured_parts = True
        else:
            has_structured_parts = True
    text = "".join(text_parts)
    if not isinstance(content, (str, list)):
        projected_parts.append(content)
        has_structured_parts = True

    if not has_structured_parts and not is_error and not sidefacts:
        encoded = text.encode("utf-8", errors="replace")
    else:
        projection = {
            "text": text,
            "is_error": is_error,
            "sidefacts": dict(sidefacts),
        }
        if has_structured_parts:
            projection["parts"] = projected_parts
        encoded = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8", errors="replace")
    return hashlib.md5(encoded).digest(), len(text)


class DuplicateToolResults:
    kind = "duplicate_tool_results"

    def __init__(self, params):
        self.minimum = params.int("min_chars", 2000, minimum=0)
        self.placeholder = params.str("placeholder")
        params.done()

    def reduce(self, context, selection):
        from .reducers import Reduction

        messages = visible_messages(context)
        hashes, edits = set(), []
        for index in reversed(selection.targets):
            message = messages[index]
            positions = set()
            replace_entire_message = False
            candidates = _result_candidates(message)
            for content, is_error, sidefacts, position in reversed(candidates):
                digest, text_length = _result_digest(content, is_error, sidefacts)
                if text_length < self.minimum:
                    continue
                if digest in hashes:
                    if position is None:
                        replace_entire_message = True
                    else:
                        positions.add(position)
                else:
                    hashes.add(digest)
            if not positions and not replace_entire_message:
                continue

            updated = dict(message)
            if positions and not rewrite_result_content(updated, self.placeholder, selected_blocks=positions):
                continue
            if replace_entire_message:
                updated["content"] = self.placeholder
            edits.append(MessageEdit(index, updated))
        if not edits:
            raise MethodUnavailable("No duplicate tool results")
        return Reduction(edits=tuple(sorted(edits, key=lambda edit: edit.index)))
