"""Compaction method registry and compactor builder.

Cascade methods, by ``compaction.methodOrder`` name (OMP
``DEFAULT_COMPACTION_METHOD_ORDER``): ``remote``, ``snapcompact``,
``handoff``, ``shake``, ``soft``. Pruning, image dropping and inline
snapcompact are request-view or manual reductions owned by the controller,
not cascade steps.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from .handoff import HandoffCompaction
from .methods import Compactor, CompactionMethod
from .remote import RemoteCompaction
from .settings import CompactionSettings
from .shake import ShakeCompaction
from .snapcompact import SnapcompactCompaction
from .soft import SoftCompaction


def default_compaction_methods() -> Dict[str, CompactionMethod]:
    """One instance of every cascade method, keyed by method name."""
    return {
        "remote": RemoteCompaction(),
        "snapcompact": SnapcompactCompaction(),
        "handoff": HandoffCompaction(),
        "shake": ShakeCompaction(),
        "soft": SoftCompaction(),
    }


def build_compactor(
    settings: CompactionSettings,
    methods: Optional[Mapping[str, CompactionMethod]] = None,
    *,
    count_view_tokens: Optional[Callable[[Any], int]] = None,
) -> Compactor:
    """Compactor over the default methods, with ``methods`` overriding by name."""
    active_methods = default_compaction_methods()
    if methods:
        active_methods.update(methods)
    kwargs = {}
    if count_view_tokens is not None:
        kwargs["count_view_tokens"] = count_view_tokens
    return Compactor(settings, active_methods, **kwargs)


__all__ = ["default_compaction_methods", "build_compactor"]
