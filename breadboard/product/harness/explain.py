"""Immutable explanation projection for one harness compilation."""

from __future__ import annotations

from .lock import _FrozenRecord


class HarnessExplanation(_FrozenRecord):
    """Recursively immutable ``bb.config_explanation.v1`` record."""

    __slots__ = ()
