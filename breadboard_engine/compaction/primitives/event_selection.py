"""Contiguous prefix/suffix selection over an event view with atomic cuts."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from ..methods import CompactionContext, MethodUnavailable
from ..params import Params, PresetError
from ..transcript import leading_system_count, tool_call_ids
from .accounting import build_estimator

if TYPE_CHECKING:
    from .selectors import Selection


class NoCondensationAvailableException(Exception):
    """A required condensation cannot make the requested progress."""


def safe_indices(events: Sequence[Mapping[str, Any]]) -> list[int]:
    """Intersect response-batch, pending-call and thinking-loop boundaries."""
    safe = set(range(len(events) + 1))
    pending = set()
    in_loop = False
    for index, event in enumerate(events):
        meta = event.get("bb_event") or {}
        action = bool(event.get("tool_calls"))
        observation = event.get("role") in {"tool", "function"}
        if action and (meta.get("thinking") or event.get("thinking_blocks")):
            in_loop = True
        elif in_loop and (action or observation):
            safe.discard(index)
        else:
            in_loop = False
        if action:
            pending.update(tool_call_ids(event))
        elif observation:
            call = event.get("tool_call_id")
            if call not in pending:
                raise ValueError("Unmatched or duplicate tool result in event view")
            pending.remove(call)
        if pending:
            safe.discard(index + 1)
        if index and action and events[index - 1].get("tool_calls"):
            prior = events[index - 1].get("bb_event") or {}
            if meta.get("batch") is not None and meta.get("batch") == prior.get("batch"):
                safe.discard(index)
    return sorted(safe)


def indexed_view(context: CompactionContext) -> tuple[list[dict[str, Any]], list[Optional[int]]]:
    """Projected events plus their original indices; synthetic events have no index."""
    events = context.state.project(context.messages, context.target, coalesce=False)
    prior = context.state.latest_boundary()
    if prior is None:
        return events, list(range(len(context.messages)))
    head = 0 if prior.reset_context else leading_system_count(context.messages)
    prefix = head if prior.prefix_end is None else prior.prefix_end
    indices = [*range(prefix), *([None] * len(prior.summary_messages)), *range(prior.first_kept_index, len(context.messages))]
    metadata = prior.details.get("event_metadata") or []
    for offset, item in enumerate(metadata):
        if item:
            events[prefix + offset]["bb_event"] = item
    return events, indices


class PrefixSuffixEvents:
    kind = "prefix_suffix_events"

    def __init__(self, params: Params) -> None:
        self.maximum = params.int("maximum", 240, minimum=1)
        self.keep_first = params.int("keep_first", 2, minimum=0)
        self.max_tokens = params.int("max_tokens", None)
        self.count = build_estimator(params.str("estimator"))
        self.minimum_progress = params.number("minimum_progress", 0.1)
        params.done()
        if self.minimum_progress is None or not 0 < self.minimum_progress < 1:
            raise PresetError("minimum_progress must be in (0, 1)")
        if self.maximum // 2 - self.keep_first - 1 <= 0:
            raise PresetError("maximum // 2 - keep_first - 1 must be positive")

    def select(self, context: CompactionContext) -> Selection:
        from .selectors import Selection

        events, indices = indexed_view(context)
        suffixes = []
        hard = context.reason in {"manual", "overflow"}
        if hard:
            suffixes.append(len(events) // 2 - self.keep_first - 1)
        if len(events) > self.maximum:
            suffixes.append(self.maximum // 2 - self.keep_first - 1)
        caps = [v for v in (self.max_tokens, context.max_input_tokens) if v is not None]
        cap = min(caps) if context.max_input_tokens is not None else None
        if cap is not None:
            total = self.count(events)
            if total > cap:
                hard = True
                base = self.count(events[:self.keep_first])
                reduction = total - cap // 2
                # The source binary search uses strictly greater incremental tokens.
                low, high = self.keep_first, len(events)
                while low < high:
                    middle = (low + high) // 2
                    if self.count(events[:middle]) - base > reduction:
                        high = middle
                    else:
                        low = middle + 1
                suffixes.append(len(events) - low)
        context.severity = "hard" if hard else "soft"
        if not suffixes:
            raise MethodUnavailable("No pressure on event view")
        try:
            safe = safe_indices(events)
            start = next(i for i in safe if i >= self.keep_first)
            end = next(i for i in safe if i >= len(events) - min(suffixes))
        except (ValueError, StopIteration) as error:
            raise NoCondensationAvailableException("Unable to compute forgotten events") from error
        if start == end:
            raise NoCondensationAvailableException("Cannot condense 0 events. This typically occurs when a tool loop spans almost the entire view, leaving no valid range for forgetting events. Consider adjusting keep_first or max_size parameters.")
        if end - start < len(events) * self.minimum_progress:
            raise NoCondensationAvailableException("Cannot apply condensation: events forgotten below minimum progress threshold.")
        prior = context.state.latest_boundary()
        ceiling = len(context.messages) if prior is None else context.state.prefix_end(context.messages)
        prefix_indices = [i for i in indices[:start] if i is not None and i < ceiling]
        prefix_end = prefix_indices[-1] + 1 if prefix_indices else 0
        first = next((i for i in indices[end:] if i is not None), len(context.messages))
        retained = [
            event for event, index in zip(events[:start], indices[:start])
            if index is None or index >= ceiling
        ]
        return Selection(
            prefix_end=prefix_end, first_kept_index=first,
            summarize=tuple(i for i in indices[start:end] if i is not None),
            reset_context=bool(prior and prior.reset_context) or prefix_end < leading_system_count(context.messages),
            details={"events": events[start:end], "synthetic_prefix": retained},
        )


class WholeView:
    kind = "whole_view"

    def __init__(self, params: Params) -> None:
        params.done()

    def select(self, context: CompactionContext) -> Selection:
        from .selectors import Selection

        events, indices = indexed_view(context)
        if not events:
            raise MethodUnavailable("Empty event view")
        return Selection(prefix_end=0, first_kept_index=len(context.messages),
                         summarize=tuple(i for i in indices if i is not None),
                         reset_context=True, details={"events": events})
