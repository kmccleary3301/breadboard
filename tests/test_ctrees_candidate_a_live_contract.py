from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.candidate_a_live_contract import execute_phase11_candidate_a_live_cell
from agentic_coder_prototype.ctrees.live_benchmark_adapter import build_phase11_live_protocol_payload


def test_candidate_a_live_workspace_root_is_decoupled_from_out_root(tmp_path: Path) -> None:
    payload = execute_phase11_candidate_a_live_cell(
        cell_id="candidate_a_flagship",
        tasks_payload={"schema_version": "test_v1", "tasks": []},
        out_root=tmp_path / "outside_repo_outputs",
        dry_run=True,
    )

    assert payload["status"] == "dry_run_executed"
    assert str(tmp_path / "outside_repo_outputs") in payload["out_path"]
    assert "/tmp/phase11_candidate_a_live_runs/" in payload["workspace_root"]


def test_candidate_a_control_config_emits_controller_contract() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt5_e4_live_candidate_a_control.yaml")
    payload = build_phase11_live_protocol_payload(
        config=config,
        task={
            "id": "downstream_interrupt_repair_v1__r1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "prompt": "Resume the repair.",
        },
    )

    assert payload["applied"] is True
    assert payload["control_contract"]["enabled"] is True
    assert payload["control_contract"]["variant"] == "candidate_a_control_v1"
    assert "Controller contract" in payload["prompt"]


def test_candidate_a_control_flagship_live_cell_executes_dry_run(tmp_path: Path) -> None:
    payload = execute_phase11_candidate_a_live_cell(
        cell_id="candidate_a_control_flagship",
        tasks_payload={
            "schema_version": "phase12_calibration_pack_v1",
            "tasks": [
                {
                    "id": "phase12_calibration_interrupt_focus_v1",
                    "base_task_id": "downstream_interrupt_repair_v1",
                    "prompt": "Inspect and verify the single active continuity issue.",
                    "task_type": "coding_continuation",
                    "latency_budget_ms": 120000,
                    "max_steps": 12,
                    "overrides": {"max_iterations": 12},
                }
            ],
        },
        out_root=tmp_path,
        dry_run=True,
    )

    assert payload["status"] == "dry_run_executed"
    row = payload["result"]["results"][0]
    assert row["phase11_live_protocol"]["control_contract"]["variant"] == "candidate_a_control_v1"
