"""Deterministic edits of visible tool observations; originals remain in history."""

import hashlib

from ..methods import MethodUnavailable
from ..state import MessageEdit
from .protected_windows import visible_messages


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
            content = message.get("content")
            if not isinstance(content, str) or len(content) < self.minimum:
                continue
            digest = hashlib.md5(content.encode("utf-8", errors="replace")).digest()
            if digest in hashes:
                edits.append(MessageEdit(index, {**message, "content": self.placeholder}))
            hashes.add(digest)
        if not edits:
            raise MethodUnavailable("No duplicate tool results")
        return Reduction(edits=tuple(sorted(edits, key=lambda e: e.index)))

