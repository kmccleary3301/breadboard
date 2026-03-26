from __future__ import annotations

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.control_contract import build_control_contract


def test_control_contract_disabled_by_default() -> None:
    payload = build_control_contract({})
    assert payload["enabled"] is False
    assert payload["prompt_block"] == ""


def test_control_contract_builds_candidate_a_variant_from_config() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt5_e4_live_candidate_a_control.yaml")
    payload = build_control_contract(config)
    assert payload["enabled"] is True
    assert payload["variant"] == "candidate_a_control_v1"
    assert payload["watchdog"]["max_no_progress_turns"] == 3
    assert "Controller contract" in payload["prompt_block"]


def test_control_contract_builds_v2_budget_rules() -> None:
    config = {
        "phase12": {
            "live_controller": {
                "enabled": True,
                "variant": "candidate_a_control_v2",
                "max_no_progress_turns": 3,
                "max_inspection_turns": 2,
                "mandatory_write_by_turn": 4,
                "escalation_after_no_progress": 2,
            }
        }
    }
    payload = build_control_contract(config)
    assert payload["enabled"] is True
    assert payload["variant"] == "candidate_a_control_v2"
    assert payload["action_budget"]["max_inspection_turns"] == 2
    assert payload["action_budget"]["mandatory_write_by_turn"] == 4
    assert "Do at most 2 read-only inspection turns" in payload["prompt_block"]
