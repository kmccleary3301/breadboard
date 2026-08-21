from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .downstream_task_pack import build_phase11_downstream_benchmark_tasks, build_phase12_calibration_tasks
from .phase11_benchmark_execution import (
    Phase11BenchmarkExecutionRequest,
    build_phase11_benchmark_command,
    build_phase11_contract_cell_statuses,
    execute_phase11_benchmark_cell,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTROL_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
_RUNNER_SCRIPT = _REPO_ROOT / "scripts" / "archive" / "phase11_benchmark_runner_stub.py"
_DEFAULT_OUTPUT_ROOT = _CONTROL_ROOT / "docs_tmp" / "c_trees" / "phase_11" / "artifacts" / "practical_baseline"
_DEFAULT_WORKSPACE_ROOT = _REPO_ROOT / "tmp" / "phase11_practical_baseline_runs"


def _config_path(name: str) -> str:
    return str((_REPO_ROOT / "agent_configs" / "misc" / name).resolve())


def build_phase11_practical_baseline_cells() -> List[Dict[str, Any]]:
    return [
        {
            "cell_id": "practical_baseline_flagship",
            "model_tier": "flagship",
            "baseline_id": "codex_e4_practical",
            "config_path": _config_path("codex_cli_gpt5_e4_live__codex0_1070_claude2_1_63_opencode1_2_17_20260305.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
        {
            "cell_id": "practical_baseline_gpt54_mini",
            "model_tier": "gpt-5.4-mini",
            "baseline_id": "codex_e4_practical_gpt54mini",
            "config_path": _config_path("codex_cli_gpt54mini_e4_live.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
        {
            "cell_id": "deterministic_control_flagship",
            "model_tier": "flagship",
            "baseline_id": "deterministic_control_flagship",
            "config_path": _config_path("codex_cli_gpt5_e4_live_deterministic_control.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
        {
            "cell_id": "deterministic_control_flagship_v2",
            "model_tier": "flagship",
            "baseline_id": "deterministic_control_flagship_v2",
            "config_path": _config_path("codex_cli_gpt5_e4_live_deterministic_control_v2.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
    ]


def build_phase11_practical_baseline_tasks_payload(*, task_pack: str = "phase11_downstream_pilot_v1") -> Dict[str, Any]:
    if task_pack == "phase11_downstream_pilot_v1":
        tasks = build_phase11_downstream_benchmark_tasks(expand_repeats=True)
    elif task_pack == "phase12_calibration_pack_v1":
        tasks = build_phase12_calibration_tasks()
    else:
        raise KeyError(f"unknown practical baseline task pack {task_pack}")
    return {
        "schema_version": "phase11_practical_baseline_tasks_v1",
        "task_pack": task_pack,
        "tasks": tasks,
    }


def build_phase11_practical_baseline_contract_status() -> Dict[str, Any]:
    default_tasks = build_phase11_practical_baseline_tasks_payload(task_pack="phase11_downstream_pilot_v1")
    cells, live_ready = build_phase11_contract_cell_statuses(
        build_phase11_practical_baseline_cells(),
        identity_key="baseline_id",
    )
    return {
        "schema_version": "phase11_practical_baseline_contract_v1",
        "runner_script": str(_RUNNER_SCRIPT),
        "task_schema_version": "phase11_practical_baseline_tasks_v1",
        "task_count": len(list(default_tasks.get("tasks") or [])),
        "live_ready": live_ready,
        "cells": cells,
    }


def _cell_by_id(cell_id: str) -> Dict[str, Any]:
    for cell in build_phase11_practical_baseline_cells():
        if str(cell.get("cell_id") or "") == str(cell_id):
            return dict(cell)
    raise KeyError(f"unknown practical baseline cell {cell_id}")


def build_phase11_practical_baseline_command(
    *,
    cell_id: str,
    tasks_path: Path,
    out_path: Path,
    workspace_root: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    return build_phase11_benchmark_command(
        cell=_cell_by_id(cell_id),
        runner_script=_RUNNER_SCRIPT,
        repo_root=_REPO_ROOT,
        tasks_path=tasks_path,
        out_path=out_path,
        workspace_root=workspace_root,
        dry_run=dry_run,
    )


def execute_phase11_practical_baseline_cell(
    *,
    cell_id: str,
    out_root: Path | None = None,
    dry_run: bool = False,
    task_pack: str = "phase11_downstream_pilot_v1",
) -> Dict[str, Any]:
    contract = build_phase11_practical_baseline_contract_status()
    target_cell = next(item for item in contract["cells"] if str(item.get("cell_id") or "") == str(cell_id))
    return execute_phase11_benchmark_cell(
        Phase11BenchmarkExecutionRequest(
            execution_schema_version="phase11_practical_baseline_execution_v1",
            cell=target_cell,
            tasks_payload=build_phase11_practical_baseline_tasks_payload(task_pack=task_pack),
            out_root=Path(out_root or _DEFAULT_OUTPUT_ROOT),
            workspace_root=_DEFAULT_WORKSPACE_ROOT / str(cell_id),
            runner_script=_RUNNER_SCRIPT,
            repo_root=_REPO_ROOT,
            dry_run=dry_run,
            executed_payload_fields={"task_pack": task_pack},
        ),
    )
