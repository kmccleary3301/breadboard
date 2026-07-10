from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

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



def test_all_21_lane_sources_explicitly_declare_the_executable_stage_contract() -> None:
    source_paths = sorted(LIVE_LANE_DEFS.glob("*.yaml"))
    source_lane_defs: dict[str, dict[str, Any]] = {}
    for path in source_paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path.name
        source_lane_defs[path.stem] = payload

    loaded_lane_defs = load_lane_defs(LIVE_LANE_DEFS)

    assert len(source_paths) == 21
    assert set(source_lane_defs) == set(loaded_lane_defs)
    for lane_id, source in source_lane_defs.items():
        assert source["lane_id"] == lane_id
        assert all(stage in source for stage in ("capture", "normalize", "replay", "compare", "claim")), lane_id
        assert source["normalize"]["mode"] in {"identity", "translate"}, lane_id
        assert source["replay"]["mode"] == "stored", lane_id
        replay_artifacts = source["replay"]["artifacts"]
        assert replay_artifacts, lane_id
        assert all(
            isinstance(artifact, str) and artifact.strip() and not Path(artifact).is_absolute()
            for artifact in replay_artifacts
        ), lane_id
        assert {
            stage: loaded_lane_defs[lane_id][stage]
            for stage in ("capture", "normalize", "replay", "compare", "claim")
        } == {
            stage: source[stage]
            for stage in ("capture", "normalize", "replay", "compare", "claim")
        }