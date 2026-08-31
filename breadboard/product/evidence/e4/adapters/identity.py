from __future__ import annotations

from typing import Any, Mapping


def translate(payload: Any, *, config: Mapping[str, Any] | None = None) -> Any:
    """Return an already-normalized E4 payload unchanged."""
    return payload
