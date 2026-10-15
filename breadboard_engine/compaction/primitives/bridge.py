"""Summary bridge placement with retained messages and reason-dependent suffixes."""
from __future__ import annotations

from ..params import Params, PresetError
from .reducers import resolve_prompt
from .context_markers import contextual_user, marker_pairs


class Bridge:
    kind = "bridge"

    def __init__(self, params: Params) -> None:
        self.prefix = resolve_prompt(params.value("prefix", ""), params, "prefix")
        self.separator = params.str("separator", "\n")
        self.suffix = resolve_prompt(params.value("suffix", ""), params, "suffix")
        self.suffix_on = params.list("suffix_on", [])
        self.replay = params.choice("replay", ("none", "before", "after"), "none")
        self.restore_context_roles = params.list("restore_context_roles", [])
        if self.restore_context_roles is None or any(
            role not in ("system", "developer", "assistant", "tool") for role in self.restore_context_roles
        ):
            raise PresetError(f"{params.where}.restore_context_roles must list non-user message roles")
        self.restore_context_user_markers = marker_pairs(params, "restore_context_user_markers")
        self.restore_context_placements = params.mapping("restore_context_placements", {})
        if self.restore_context_placements is None or any(
            phase not in ("pre_turn", "mid_turn") or position not in ("after_summary", "before_last_user")
            for phase, position in self.restore_context_placements.items()
        ):
            raise PresetError(f"{params.where}.restore_context_placements must map pre_turn/mid_turn to after_summary/before_last_user")
        params.done()

    def place(self, context, selection, reduction):
        if reduction.native is not None:
            return []
        if "continuation_messages" in reduction.details:
            return [dict(message) for message in reduction.details["continuation_messages"]]
        content = self.prefix + self.separator + (reduction.summary or "")
        if context.reason in self.suffix_on:
            content += self.suffix
        summary = [{"role": "user", "content": content}]
        retained = [dict(m) for m in selection.details.get("retained_messages", ())]
        if not retained:
            retained = [dict(context.messages[i]) for i in selection.replay]
        if self.replay == "before":
            placed = retained + summary
        elif self.replay == "after":
            placed = summary + retained
        else:
            placed = summary
        position = self.restore_context_placements.get(context.sampling_phase)
        if position is None:
            return placed
        restored = [dict(message) for message in context.projected()
                    if message.get("role") in self.restore_context_roles
                    or contextual_user(message, self.restore_context_user_markers)]
        summary_index = placed.index(summary[0])
        insertion = summary_index + 1
        if position == "before_last_user":
            users = [index for index, message in enumerate(placed)
                     if message.get("role") == "user" and index != summary_index
                     and not contextual_user(message, self.restore_context_user_markers)]
            insertion = users[-1] if users else summary_index
        return placed[:insertion] + restored + placed[insertion:]
