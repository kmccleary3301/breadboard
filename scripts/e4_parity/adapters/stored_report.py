from __future__ import annotations

from typing import Any

from conformance.comparators.stored_report import compare as _compare


def compare(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Delegate stored-report comparisons to the conformance comparator."""
    return _compare(*args, **kwargs)
