"""Whole-history replacement, retained user text, and latest-call protection."""
from __future__ import annotations

from typing import Mapping

from ..methods import CompactionContext, MethodUnavailable
from ..params import Params
from .accounting import _text_parts, normalize_usage, _usage_total, rounded_text_tokens
from .byte_estimator import text_tokens
from .selectors import Selection
from .triggers import build_limit
from .native_retention import truncate_middle




class WholeHistory:
    kind = "whole_history"

    def __init__(self, params: Params) -> None:
        self.exclude_latest_user_on = params.list("exclude_latest_user_on", [])
        self.view = params.choice("view", ("history", "events"), "history")
        params.done()

    def select(self, context: CompactionContext) -> Selection:
        if self.view == "events":
            from .event_selection import indexed_view

            events, indices = indexed_view(context)
            if not events:
                raise MethodUnavailable("Empty event view")
            return Selection(
                prefix_end=0, first_kept_index=len(context.messages),
                summarize=tuple(i for i in indices if i is not None),
                reset_context=True, details={"events": events},
            )
        start = context.state.kept_start(context.messages)
        end = len(context.messages)
        replay = ()
        if context.reason in self.exclude_latest_user_on:
            index = next((i for i in range(end - 1, start - 1, -1) if context.messages[i].get("role") == "user"), None)
            if index is not None:
                replay = (index,)
        return Selection(first_kept_index=end, summarize=tuple(i for i in range(start, end) if i not in replay), replay=replay)


class UserMessagesBudget:
    kind = "user_messages_budget"

    def __init__(self, params: Params) -> None:
        self.budget = params.int("budget", minimum=0)
        from .reducers import resolve_prompt
        self.exclude_prefix = resolve_prompt(params.value("exclude_prefix", ""), params, "exclude_prefix")
        self.features = params.list("features", [])
        self.native_feature = params.str("native_feature", None)
        params.done()

    def select(self, context: CompactionContext) -> Selection:
        native = self.native_feature is not None and self.native_feature in self.features
        remaining = self.budget
        retained = []
        replay = []
        start = context.state.kept_start(context.messages)
        if native:
            return Selection(
                first_kept_index=len(context.messages),
                replay=tuple(i for i in range(start, len(context.messages)) if context.messages[i].get("role") == "user"),
                details={"native": True},
            )
        boundary = context.state.latest_boundary()
        previous = boundary.native.items if boundary is not None and boundary.native is not None else (
            boundary.summary_messages if boundary is not None else ()
        )
        candidates = [(None, m) for m in previous] + list(enumerate(context.messages[start:], start))
        for index, message in reversed(candidates):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            text = content if isinstance(content, str) else "".join(
                p["text"] for p in content or () if p.get("type") in {"text", "input_text"}
            )
            if self.exclude_prefix and text.startswith(self.exclude_prefix + "\n"):
                continue
            if remaining == 0:
                break
            cost = text_tokens(text)
            item = {"role": "user", "content": text}
            truncated = cost > remaining
            if truncated:
                item["content"] = truncate_middle(text, remaining)
            retained.append(item)
            if index is not None:
                replay.append(index)
            remaining = max(0, remaining - cost)
            if truncated:
                break
        retained.reverse()
        replay.reverse()
        return Selection(first_kept_index=len(context.messages), summarize=tuple(range(start, len(context.messages))),
                         replay=tuple(replay), details={"retained_messages": tuple(retained)})

class LatestToolOutputs:
    kind = "latest_tool_outputs"

    def __init__(self, params: Params) -> None:
        self.keep_latest_calls = params.int("keep_latest_calls", minimum=0)
        self.residual_target = params.int("residual_target", minimum=0)
        self.min_savings = params.int("min_savings", minimum=0)
        self.warning_delta = params.int("warning_delta", minimum=0)
        self.eligible_tools = params.list("eligible_tools")
        self.disabled = params.bool("disabled", False)
        self.limit = build_limit(params.child(params.mapping("limit"), "limit"))
        self.auto_compact_enabled = params.bool("auto_compact_enabled", True)
        self.disabled_auto_limit = build_limit(params.child(params.mapping("disabled_auto_limit"), "disabled_auto_limit"))
        params.done()

    def select(self, context: CompactionContext) -> Selection:
        if self.disabled:
            raise MethodUnavailable("Tool-output masking disabled")
        start = context.state.kept_start(context.messages)
        calls = []
        results = {}
        edits = {e.index: e.message for r in context.state.records for e in r.edits}
        for index in range(start, len(context.messages)):
            message = edits.get(index, context.messages[index])
            for call in message.get("tool_calls") or ():
                function = call.get("function", call)
                if function.get("name") in self.eligible_tools:
                    calls.append(call["id"])
            call_id = message.get("tool_call_id")
            text = "".join(_text_parts(message.get("content")))
            if call_id and not text.startswith(("<persisted-output>", "[Old tool result content cleared]")):
                results[call_id] = (index, rounded_text_tokens(text))
        eligible = [c for c in calls if c in results]
        total = sum(results[c][1] for c in eligible)
        protected = set(eligible[-self.keep_latest_calls:]) if self.keep_latest_calls else set()
        selected = []
        saved = 0
        for call in eligible:
            if call not in protected and total - saved > self.residual_target:
                selected.append(results[call][0])
                saved += results[call][1]
        usage = normalize_usage(context.last_usage)
        limit = self.limit if self.auto_compact_enabled else self.disabled_auto_limit
        warning = limit.tokens(context.context_window, context.max_output_tokens) - self.warning_delta
        if not context.usage_fresh or usage is None or _usage_total(usage, "components") < warning or saved < self.min_savings:
            selected = []
        if not selected:
            raise MethodUnavailable("No eligible tool outputs meet warning and savings gates")
        return Selection(targets=tuple(selected))
