"""Placement primitives: the messages a boundary puts where history was cut."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

from ..methods import CompactionContext
from ..params import Params, PresetError
from ..prompts import load_prompt, render_template
from .reducers import Reduction
from .selectors import Selection

_ROLES = ("user", "assistant", "system")
_PLACEHOLDER_RE = re.compile(r"\{\{(summary|short_summary)\}\}")


def _template_source(value: Any, params: Params, key: str) -> str:
    """Unrendered template: ``{builtin: name}``, ``{file: name}`` or a literal."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and set(value) == {"builtin"}:
        return load_prompt(str(value["builtin"]))
    if isinstance(value, Mapping) and set(value) == {"file"}:
        if params.env.prompt_dir is None:
            raise PresetError(f"{params.where}.{key}: preset has no prompt directory")
        return params.env.prompt_dir.joinpath(str(value["file"])).read_text(encoding="utf-8")
    raise PresetError(f"{params.where}.{key} must be a string, {{builtin: name}} or {{file: name}}")


class Template:
    """Messages built from templates with ``{{summary}}`` (and ``{{short_summary}}``).

    ``engine: literal`` substitutes the placeholders and nothing else (Pi
    ``COMPACTION_SUMMARY_PREFIX + summary + COMPACTION_SUMMARY_SUFFIX``).
    ``engine: handlebars`` renders through OMP's prompt renderer, which also
    normalizes whitespace (``prompt.format``).
    """

    kind = "template"

    def __init__(self, params: Params) -> None:
        self.engine = params.choice("engine", ("literal", "handlebars"))
        raw_messages = params.list("messages")
        if not raw_messages:
            raise PresetError(f"{params.where}.messages must not be empty")
        self.messages: List[Dict[str, str]] = []
        for index, raw in enumerate(raw_messages):
            item = params.child(raw, f"messages[{index}]")
            role = item.choice("role", _ROLES)
            content = _template_source(item.value("content"), item, "content")
            item.done()
            self.messages.append({"role": role, "content": content})
        params.done()

    def _render(self, template: str, reduction: Reduction) -> str:
        values = {"summary": reduction.summary or "", "short_summary": reduction.short_summary or ""}
        if self.engine == "handlebars":
            return render_template(template, values)
        # One pass, so placeholder text inside a summary is never substituted.
        return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)

    def place(self, context: CompactionContext, selection: Selection, reduction: Reduction) -> List[Dict[str, Any]]:
        return [{"role": m["role"], "content": self._render(m["content"], reduction)} for m in self.messages]


from .bridge import Bridge

PLACEMENT_KINDS = {cls.kind: cls for cls in (Template, Bridge)}


def build_placement(params: Params) -> Any:
    kind = params.kind(PLACEMENT_KINDS)
    return PLACEMENT_KINDS[kind](params)
