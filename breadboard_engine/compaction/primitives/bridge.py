"""Summary bridge placement with retained messages and reason-dependent suffixes."""
from __future__ import annotations

from ..params import Params
from .reducers import resolve_prompt


class Bridge:
    kind = "bridge"

    def __init__(self, params: Params) -> None:
        self.prefix = resolve_prompt(params.value("prefix", ""), params, "prefix")
        self.separator = params.str("separator", "\n")
        self.suffix = resolve_prompt(params.value("suffix", ""), params, "suffix")
        self.suffix_on = params.list("suffix_on", [])
        self.replay = params.choice("replay", ("none", "before", "after"), "none")
        params.done()

    def place(self, context, selection, reduction):
        if reduction.native is not None:
            return []
        content = self.prefix + self.separator + (reduction.summary or "")
        if context.reason in self.suffix_on:
            content += self.suffix
        summary = [{"role": "user", "content": content}]
        retained = [dict(m) for m in selection.details.get("retained_messages", ())]
        if not retained:
            retained = [dict(context.messages[i]) for i in selection.replay]
        if self.replay == "before":
            return retained + summary
        if self.replay == "after":
            return summary + retained
        return summary
