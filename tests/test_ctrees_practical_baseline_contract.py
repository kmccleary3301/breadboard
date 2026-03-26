from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.ctrees.practical_baseline_contract import (
    build_phase11_practical_baseline_contract_status,
    build_phase11_practical_baseline_tasks_payload,
    execute_phase11_practical_baseline_cell,
)


def test_practical_baseline_contract_freezes_flagship_and_gpt54mini_cells() -> None:
    payload = build_phase11_practical_baseline_contract_status()

    assert payload["schema_version"] == "phase11_practical_baseline_contract_v1"
    assert payload["task_count"] == 8
    assert [cell["cell_id"] for cell in payload["cells"]] == [
        "practical_baseline_flagship",
        "practical_baseline_gpt54_mini",
        "deterministic_control_flagship",
        "deterministic_control_flagship_v2",
    ]
    assert payload["cells"][1]["config_path"].endswith("codex_cli_gpt54mini_e4_live.yaml")


def test_practical_baseline_tasks_payload_matches_repeat_expansion() -> None:
    payload = build_phase11_practical_baseline_tasks_payload()

    assert payload["schema_version"] == "phase11_practical_baseline_tasks_v1"
    assert payload["task_pack"] == "phase11_downstream_pilot_v1"
    assert len(payload["tasks"]) == 8
    assert payload["tasks"][0]["id"].endswith("__r1")
    assert payload["tasks"][1]["id"].endswith("__r2")


def test_practical_baseline_can_build_phase12_calibration_pack() -> None:
    payload = build_phase11_practical_baseline_tasks_payload(task_pack="phase12_calibration_pack_v1")

    assert payload["task_pack"] == "phase12_calibration_pack_v1"
    assert len(payload["tasks"]) == 4
    assert payload["tasks"][0]["id"].startswith("phase12_calibration_")


def test_practical_baseline_flagship_dry_run_executes_runner_contract(tmp_path: Path) -> None:
    payload = execute_phase11_practical_baseline_cell(
        cell_id="practical_baseline_flagship",
        out_root=tmp_path,
        dry_run=True,
    )

    assert payload["schema_version"] == "phase11_practical_baseline_execution_v1"
    assert payload["status"] == "dry_run_executed"
    assert payload["returncode"] == 0
    assert Path(payload["out_path"]).exists()
    result = payload["result"]
    assert result["summary"]["dry_run"] is True
    assert "grounded_completed" in result["summary"]
    assert "ungrounded_stop_count" in result["summary"]
    assert "grounded_summary" in result["results"][0]
    assert "/tmp/phase11_practical_baseline_runs/" in payload["workspace_root"]


def test_practical_baseline_flagship_dry_run_supports_calibration_pack(tmp_path: Path) -> None:
    payload = execute_phase11_practical_baseline_cell(
        cell_id="practical_baseline_flagship",
        out_root=tmp_path,
        dry_run=True,
        task_pack="phase12_calibration_pack_v1",
    )

    assert payload["status"] == "dry_run_executed"
    assert payload["task_pack"] == "phase12_calibration_pack_v1"


def test_practical_baseline_workspace_root_is_decoupled_from_out_root(tmp_path: Path) -> None:
    payload = execute_phase11_practical_baseline_cell(
        cell_id="practical_baseline_flagship",
        out_root=tmp_path / "outside_repo_outputs",
        dry_run=True,
    )

    assert str(tmp_path / "outside_repo_outputs") in payload["out_path"]
    assert "/tmp/phase11_practical_baseline_runs/" in payload["workspace_root"]


def test_practical_baseline_contract_exposes_deterministic_control_v2_cell(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    payload = build_phase11_practical_baseline_contract_status()
    cell = next(item for item in payload["cells"] if item["cell_id"] == "deterministic_control_flagship_v2")
    assert cell["status"] == "contract_ready"
    assert cell["config_path"].endswith("codex_cli_gpt5_e4_live_deterministic_control_v2.yaml")
