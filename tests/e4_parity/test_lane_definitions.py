from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.e4_parity.lane_definitions import inventory_lane_sources, load_lane_defs


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



def test_lane_source_inventory_and_legacy_executable_stage_contracts() -> None:
    source_inventory = inventory_lane_sources(LIVE_LANE_DEFS)

    assert [(row["path"], row["kind"]) for row in source_inventory] == [
        ("breadboard_self_runtime_records_v1.yaml", "lane_def_legacy"),
        ("claude_code_north_star_capture_v1.yaml", "lane_def_legacy"),
        ("codex_cli_e4_capture_probe_v1.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_1_effective_config_graph_compiler.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_2_context_resource_pack_compiler.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_3_capability_registry_compiler.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_4_extension_hook_execution_compiler.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_5_resource_blob_compiler.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_6_protocol_provider_policy_compiler.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_7_memory_work_compiler.yaml", "lane_def_legacy"),
        ("oh_my_pi_p3_8_projection_broker_adapter.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_0_l1_config_context_tool_surface.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_0_l2_tool_execution.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_0_l3_command_network_hook.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_0_l4_mcp_browser_resource.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_0_l5_memory_compaction.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_0_l6_tui_projection.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_6_task_job_subagent.manifest.yaml", "lane_manifest"),
        ("oh_my_pi_p6_6_task_job_subagent.payloads.yaml", "payload_source"),
        ("oh_my_pi_p6_6_task_job_subagent.yaml", "lane_def_legacy"),
        ("oh_my_pi_p6_6_task_job_subagent_v2.yaml", "lane_def_legacy"),
        ("opencode_north_star_capture_v1.yaml", "lane_def_legacy"),
        ("pi_p5_l1_cli_config_context_tool_surface.yaml", "lane_def_legacy"),
        ("pi_p5_l2_extension_session_residual.yaml", "lane_def_legacy"),
    ]

    excluded_rows = [row for row in source_inventory if row["kind"] == "excluded"]
    assert not excluded_rows, {
        row["path"]: row["reason"] or "missing exclusion reason"
        for row in excluded_rows
    }
    source_lane_defs: dict[str, dict[str, Any]] = {}
    for row in source_inventory:
        if row["kind"] != "lane_def_legacy":
            continue
        path = LIVE_LANE_DEFS / row["path"]
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), path.name
        source_lane_defs[path.stem] = payload

    loaded_lane_defs = load_lane_defs(LIVE_LANE_DEFS)

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