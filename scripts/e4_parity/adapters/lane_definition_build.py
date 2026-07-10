from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts.e4_parity.lane_acceptance_artifacts import build_lane_from_definition


def capture(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any] | None = None,
    *,
    promote_accepted: bool,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Build accepted C4 lane artifacts from the lane definition itself."""
    output_root = None if promote_accepted else out_dir
    return build_lane_from_definition(lane_def, inventory_lane, output_root=output_root)


capture.supports_scratch_out_dir = True
