from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.candidate_a_live_contract import (
    build_phase11_candidate_a_live_command,
    build_phase11_candidate_a_live_contract_status,
    execute_phase11_candidate_a_live_cell,
)
from agentic_coder_prototype.ctrees.live_benchmark_adapter import (
    build_phase11_live_protocol_payload,
    build_phase18_finish_closure_protocol_payload,
)


def _config_path(rel_path: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    direct = repo_root / rel_path
    if direct.exists():
        return str(direct)
    return str(Path(rel_path))


def test_live_protocol_payload_disabled_for_practical_baseline() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml")
    )
    task = {
        "id": "downstream_semantic_pivot_v1__r1",
        "base_task_id": "downstream_semantic_pivot_v1",
        "prompt": "Pivot cleanly.",
    }
    payload = build_phase11_live_protocol_payload(config=config, task=task)
    assert payload["applied"] is False
    assert payload["reason"] == "adapter_disabled"
    assert payload["prompt"] == "Pivot cleanly."


def test_live_protocol_payload_applies_candidate_a_prompt() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_candidate_a.yaml")
    )
    task = {
        "id": "downstream_dependency_shift_v1__r1",
        "base_task_id": "downstream_dependency_shift_v1",
        "prompt": "Continue with the dependency shift.",
    }
    payload = build_phase11_live_protocol_payload(config=config, task=task)
    assert payload["applied"] is True
    assert payload["strategy"] == "candidate_a"
    assert payload["base_task_id"] == "downstream_dependency_shift_v1"
    assert payload["base_scenario_id"] == "pilot_dependency_noise_v1"
    assert payload["support_node_ids"]
    assert "[Protocol-selected continuation context]" in payload["prompt"]
    assert "[Current task]" in payload["prompt"]


def test_live_protocol_payload_supports_deterministic_control_variant() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt5_e4_live_deterministic_control.yaml")
    )
    task = {
        "id": "downstream_dependency_shift_v1__r1",
        "base_task_id": "downstream_dependency_shift_v1",
        "prompt": "Continue with the dependency shift.",
    }
    payload = build_phase11_live_protocol_payload(config=config, task=task)
    assert payload["applied"] is True
    assert payload["strategy"] == "deterministic_reranker"
    assert payload["control_contract"]["enabled"] is True
    assert "Controller contract" in payload["prompt"]
    assert payload["support_node_ids"]


def test_live_protocol_payload_supports_candidate_a_control_v2_variant() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt5_e4_live_candidate_a_control_v2.yaml")
    )
    task = {
        "id": "downstream_interrupt_repair_v1__r1",
        "base_task_id": "downstream_interrupt_repair_v1",
        "prompt": "Repair the interrupted continuation.",
    }
    payload = build_phase11_live_protocol_payload(config=config, task=task)
    assert payload["applied"] is True
    assert payload["strategy"] == "candidate_a"
    assert payload["control_contract"]["variant"] == "candidate_a_control_v2"
    assert payload["control_contract"]["action_budget"]["mandatory_write_by_turn"] == 4


def test_candidate_a_live_contract_builds_dry_run_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    status = build_phase11_candidate_a_live_contract_status()
    assert status["live_ready"] is True
    cell = next(item for item in status["cells"] if item["cell_id"] == "candidate_a_gpt54_mini")
    assert cell["status"] == "contract_ready"

    command_payload = build_phase11_candidate_a_live_command(
        cell_id="candidate_a_gpt54_mini",
        tasks_path=tmp_path / "tasks.json",
        out_path=tmp_path / "results.json",
        workspace_root=tmp_path / "workspace",
        dry_run=True,
    )
    assert "codex_cli_gpt54mini_e4_live_candidate_a.yaml" in " ".join(command_payload["cmd"])

    execution = execute_phase11_candidate_a_live_cell(
        cell_id="candidate_a_gpt54_mini",
        tasks_payload={
            "tasks": [
                {
                    "id": "downstream_subtree_pressure_v1__r1",
                    "base_task_id": "downstream_subtree_pressure_v1",
                    "prompt": "Continue under subtree pressure.",
                    "task_type": "coding_continuation",
                    "latency_budget_ms": 180000,
                    "max_steps": 10,
                    "overrides": {"max_iterations": 10},
                }
            ]
        },
        out_root=tmp_path,
        dry_run=True,
    )
    assert execution["status"] == "dry_run_executed"
    result = execution["result"]
    assert result["summary"]["dry_run"] is True
    row = result["results"][0]
    assert row["phase11_live_protocol"]["applied"] is True
    assert row["phase11_live_protocol"]["strategy"] == "candidate_a"


def test_candidate_a_live_contract_exposes_v2_cell(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    status = build_phase11_candidate_a_live_contract_status()
    cell = next(item for item in status["cells"] if item["cell_id"] == "candidate_a_control_flagship_v2")
    assert cell["status"] == "contract_ready"


def test_phase18_finish_closure_protocol_supports_calibrated_continuation_strategy() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_continuation_calibrated_finish_closure_executor_v1_native.yaml")
    )
    interrupt_task = {
        "id": "downstream_interrupt_repair_v1__r1",
        "base_task_id": "downstream_interrupt_repair_v1",
        "prompt": "Resume the interrupted continuation.",
    }
    pivot_task = {
        "id": "downstream_semantic_pivot_v1__r2",
        "base_task_id": "downstream_semantic_pivot_v1",
        "prompt": "Pivot to the new related target.",
    }

    interrupt_payload = build_phase18_finish_closure_protocol_payload(config=config, task=interrupt_task)
    pivot_payload = build_phase18_finish_closure_protocol_payload(config=config, task=pivot_task)

    assert interrupt_payload["applied"] is True
    assert interrupt_payload["support_strategy"] == "calibrated_continuation"
    assert interrupt_payload["support_payload"]["base_strategy"] == "deterministic_reranker"
    assert interrupt_payload["support_payload"]["continuation_policy"]["policy_id"] == "interrupt_repair_balanced_v1"
    assert len(interrupt_payload["support_payload"]["support_node_ids"]) == 2
    assert "Continuation policy note:" in interrupt_payload["prompt"]

    assert pivot_payload["support_payload"]["continuation_policy"]["policy_id"] == "semantic_pivot_sparse_v1"
    assert len(pivot_payload["support_payload"]["support_node_ids"]) == 1


def test_phase18_finish_closure_protocol_supports_calibrated_continuation_v1b_strategy() -> None:
    config = load_agent_config(
        _config_path("agent_configs/misc/codex_cli_gpt54mini_e4_live_continuation_calibrated_finish_closure_executor_v1b_native.yaml")
    )
    dependency_task = {
        "id": "downstream_dependency_shift_v1__r2",
        "base_task_id": "downstream_dependency_shift_v1",
        "prompt": "Recover the changed dependency context.",
    }

    payload = build_phase18_finish_closure_protocol_payload(config=config, task=dependency_task)

    assert payload["support_strategy"] == "calibrated_continuation_v1b"
    assert payload["support_payload"]["base_strategy"] == "deterministic_reranker"
    assert payload["support_payload"]["continuation_policy"]["policy_id"] == "dependency_shift_validation_sparse_v1b"
    assert len(payload["support_payload"]["support_node_ids"]) == 1
    assert "Continuation policy note:" in payload["prompt"]
