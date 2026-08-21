from __future__ import annotations

from typing import Any

from scripts.e4_parity.lane_acceptance_artifacts import build_lane_from_definition


def capture(*args: Any, **kwargs: Any) -> Any:
    """Delegate lane-definition package capture to the accepted artifact builder."""
    return build_lane_from_definition(*args, **kwargs)
