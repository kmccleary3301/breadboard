from __future__ import annotations

from pathlib import Path

from scripts.e4_parity.lane_definitions import load_lane_defs


ROOT = Path(__file__).resolve().parents[2]
LIVE_LANE_DEFS = ROOT / "config" / "e4_lanes"


def test_checked_in_lane_defs_validate_and_include_representative_accepted_lanes() -> None:
    """The checked-in lane_def set validates and includes accepted WS-E representative lanes."""
    lane_defs = load_lane_defs(LIVE_LANE_DEFS)
    accepted_lane_ids = {lane_id for lane_id, lane_def in lane_defs.items() if lane_def["status"] == "accepted"}

    assert {
        "codex_cli_e4_capture_probe_v1",
        "oh_my_pi_p3_1_effective_config_graph_compiler",
    }.issubset(accepted_lane_ids)
    assert any(lane_id.startswith("pi_p5_") for lane_id in accepted_lane_ids)
