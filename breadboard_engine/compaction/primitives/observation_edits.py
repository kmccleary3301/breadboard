"""Deterministic edits of visible tool observations; originals remain in history."""

import hashlib
import json
from jinja2 import Environment

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


class ObservationClip:
    kind = "observation_clip"

    def __init__(self, params):
        self.boundary = params.int("boundary_chars", 10000, minimum=1)
        self.head = params.int("head_chars", 5000, minimum=0)
        self.tail = params.int("tail_chars", 5000, minimum=0)
        from .reducers import resolve_prompt
        self.template = Environment().from_string(resolve_prompt(params.value("template"), params, "template"))
        params.done()

    def reduce(self, context, selection):
        from .reducers import Reduction
        messages = visible_messages(context)
        edits = []
        for index in selection.targets:
            message = messages[index]
            content = message.get("content")
            raw = (message.get("extra") or {}).get("raw_output")
            if isinstance(raw, dict):
                observation = raw
            else:
                try:
                    observation = json.loads(content) if isinstance(content, str) else None
                except ValueError:
                    observation = None
            if not isinstance(observation, dict) or not isinstance(observation.get("output"), str):
                continue  # Already clipped envelopes are never expanded on replay.
            text = observation["output"]
            if len(text) < self.boundary:
                continue
            rendered = self.template.render(output=observation, boundary_chars=self.boundary,
                                            head_chars=self.head, tail_chars=self.tail)
            if rendered != content:
                edits.append(MessageEdit(index, {**message, "content": rendered}))
        if not edits:
            raise MethodUnavailable("No observations require clipping")
        return Reduction(edits=tuple(edits))
