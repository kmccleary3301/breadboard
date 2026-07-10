from __future__ import annotations

from typing import Any

from conformance.comparators.semantic_replay import compare as _compare


def compare(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Delegate semantic replay comparisons to the conformance comparator."""
    return _compare(*args, **kwargs)
