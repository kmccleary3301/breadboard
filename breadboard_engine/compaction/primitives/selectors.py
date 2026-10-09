"""Selector primitives: choose which history indices a stage reduces.

A selector reads the full append-only history plus the ledger and returns a
:class:`Selection`. It never edits anything. Indices are positions in the
full history, the same coordinates :class:`CompactionRecord` uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from ..methods import CompactionContext, MethodUnavailable
from ..params import Params
from ..transcript import find_cut_point, find_cut_point_crossing
from .accounting import build_estimator


@dataclass(frozen=True)
class Selection:
    """What a stage operates on. Each selector fills only the fields it owns.

    ``first_kept_index``: new boundary (``None`` for edit-only stages).
    ``prefix_end``: end of a verbatim protected prefix, if any.
    ``summarize``: indices whose content the reducer condenses.
    ``turn_prefix``: indices of a split turn's prefix, summarized separately.
    ``replay``: indices re-sent verbatim after the summary.
    ``targets``: indices an edit reducer rewrites.
    """

    first_kept_index: Optional[int] = None
    prefix_end: Optional[int] = None
    summarize: Tuple[int, ...] = ()
    turn_prefix: Tuple[int, ...] = ()
    replay: Tuple[int, ...] = ()
    targets: Tuple[int, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name in ("first_kept_index", "prefix_end"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        for name in ("summarize", "turn_prefix", "replay", "targets"):
            value = getattr(self, name)
            if value:
                out[name] = list(value)
        return out


_WALKS: Dict[str, Callable[..., Any]] = {
    "group_gt": find_cut_point,
    "message_crossing_ge": find_cut_point_crossing,
}


class RecentTokens:
    """Keep a recent suffix of about ``budget`` tokens; summarize the rest since the last boundary.

    ``walk``:
      ``group_gt`` — OMP 18.4.5: walk back cut point by cut point and stop
      before the group that would push the suffix over budget (``>``).
      ``message_crossing_ge`` — Pi 0.73.1 ``findCutPoint``: walk back per
      message to the first one where the total reaches ``budget`` (``>=``),
      then cut at the nearest valid cut point at or after it.
    A cut that lands inside a turn summarizes the turn's prefix separately
    (``turn_prefix``). Tool results are never cut points.
    """

    kind = "recent_tokens"

    def __init__(self, params: Params) -> None:
        self.budget = params.int("budget", minimum=0)
        self.walk = params.choice("walk", tuple(_WALKS))
        self.count = build_estimator(params.str("estimator", "bb_chars4"))
        self.when_empty = params.choice("when_empty", ("unavailable", "summarize"), "unavailable")
        params.done()

    def _count_one(self, message: Mapping[str, Any]) -> int:
        return self.count([message])

    def select(self, context: CompactionContext) -> Selection:
        messages = context.messages
        start = context.state.kept_start(messages)
        count_one = context.token_estimator or self._count_one
        cut = _WALKS[self.walk](messages, start, len(messages), self.budget, count_tokens=count_one)
        first = cut.first_kept_index
        history_end = cut.turn_start_index if cut.is_split_turn else first
        summarize = tuple(range(start, history_end))
        turn_prefix = tuple(range(cut.turn_start_index, first)) if cut.is_split_turn else ()
        if not summarize and not turn_prefix and self.when_empty == "unavailable":
            raise MethodUnavailable("Nothing to summarize")
        return Selection(first_kept_index=first, summarize=summarize, turn_prefix=turn_prefix)


from .protected_windows import DecayingPrefixTail, VisibleToolOutputs

SELECTOR_KINDS = {cls.kind: cls for cls in (RecentTokens, DecayingPrefixTail, VisibleToolOutputs)}


def build_selector(params: Params) -> Any:
    kind = params.kind(SELECTOR_KINDS)
    return SELECTOR_KINDS[kind](params)
