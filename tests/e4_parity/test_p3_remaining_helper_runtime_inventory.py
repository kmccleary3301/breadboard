from __future__ import annotations

import json
import importlib
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity import build_oh_my_pi_p3_remaining_helper_runtime as builder


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs" / "conformance" / "e4_lane_inventory.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ct_output(lane: dict[str, Any]) -> str:
    argv = lane["ct"]["command"]["argv"]
    return argv[argv.index("--json-out") + 1]


def _p3_item(test_id: str) -> str:
    match = re.search(r"-P3([1-8])-", test_id)
    assert match is not None
    return f"P3.{match.group(1)}"


def test_p3_remaining_lanes_derive_identity_from_inventory() -> None:
    inventory = _read_json(INVENTORY_PATH)
    expected = []
    for lane in inventory["lanes"]:
        if lane.get("phase") != "P3" or lane.get("status") != "accepted" or lane["ct"]["test_id"].startswith("CT-P3-OHMYPI-P31-"):
            continue
        expected.append(
            {
                "p3_item": _p3_item(lane["ct"]["test_id"]),
                "points": lane["points"],
                "lane_id": lane["lane_id"],
                "config_id": lane["config_id"],
                "feature_id": lane["ledger_feature_ids"][0],
                "ct_id": lane["ct"]["test_id"],
                "ct_output": _ct_output(lane),
                "primitive": lane["primitives"][0],
            }
        )

    observed = [
        {key: spec[key] for key in expected[0]}
        for spec in builder.LANES
    ]

    assert observed == expected
    assert builder.LANES == builder._load_p3_remaining_lanes(INVENTORY_PATH)



@pytest.mark.parametrize(
    ("module_name", "script_name"),
    [
        ("scripts.e4_parity.build_oh_my_pi_p3_1_effective_config_graph", "build_oh_my_pi_p3_1_effective_config_graph.py"),
        ("scripts.e4_parity.build_pi_p5_cli_config_context_tool_surface", "build_pi_p5_cli_config_context_tool_surface.py"),
        ("scripts.e4_parity.build_pi_p5_extension_session_residual", "build_pi_p5_extension_session_residual.py"),
        ("scripts.e4_parity.build_oh_my_pi_l5_memory_compaction", "build_oh_my_pi_l5_memory_compaction.py"),
        ("scripts.e4_parity.build_oh_my_pi_l6_tui_projection", "build_oh_my_pi_l6_tui_projection.py"),
        ("scripts.e4_parity.build_oh_my_pi_p6_6_task_job_subagent", "build_oh_my_pi_p6_6_task_job_subagent.py"),
    ],
)
def test_single_lane_builders_derive_identity_from_inventory(module_name: str, script_name: str) -> None:
    inventory = _read_json(INVENTORY_PATH)
    matches = [
        lane
        for lane in inventory["lanes"]
        if isinstance(lane.get("builder"), dict)
        and any(str(arg).endswith(script_name) for arg in lane["builder"].get("argv", []))
    ]
    assert len(matches) == 1
    lane = matches[0]
    module = importlib.import_module(module_name)

    assert module.LANE_ID == lane["lane_id"]
    assert module.CONFIG_ID == lane["config_id"]
    assert module.CLAIM_ID == lane["claim_id"]
    assert module.RUN_ID == lane["run_id"]
    assert module.TARGET_FAMILY == lane["target_family"]
    assert module.TARGET_VERSION == lane["target_version"]
    assert module.PROVIDER_MODEL == lane["provider_model"]
    assert module.SANDBOX_MODE == lane["sandbox_mode"]
    assert module.PHASE == lane["phase"]
    if hasattr(module, "POINTS"):
        assert module.POINTS == lane["points"]
    assert module.FEATURE_ID == lane["ledger_feature_ids"][0]
    assert module.CT_ID == lane["ct"]["test_id"]
    assert module.CT_OUTPUT == _ct_output(lane)