from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .phase11_benchmark_execution import (
    Phase11BenchmarkExecutionRequest,
    build_phase11_benchmark_command,
    build_phase11_contract_cell_statuses,
    execute_phase11_benchmark_cell,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTROL_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
_RUNNER_SCRIPT = _REPO_ROOT / "scripts" / "archive" / "phase11_benchmark_runner_stub.py"
_DEFAULT_OUTPUT_ROOT = _CONTROL_ROOT / "docs_tmp" / "c_trees" / "phase_11" / "artifacts" / "candidate_a_live"
_DEFAULT_WORKSPACE_ROOT = _REPO_ROOT / "tmp" / "phase11_candidate_a_live_runs"


def _config_path(name: str) -> str:
    return str((_REPO_ROOT / "agent_configs" / "misc" / name).resolve())


def build_phase11_candidate_a_live_cells() -> List[Dict[str, Any]]:
    return [
        {
            "cell_id": "candidate_a_flagship",
            "model_tier": "flagship",
            "candidate_id": "candidate_a_live",
            "config_path": _config_path("codex_cli_gpt5_e4_live_candidate_a.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
        {
            "cell_id": "candidate_a_control_flagship",
            "model_tier": "flagship",
            "candidate_id": "candidate_a_control_live",
            "config_path": _config_path("codex_cli_gpt5_e4_live_candidate_a_control.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
        {
            "cell_id": "candidate_a_control_flagship_v2",
            "model_tier": "flagship",
            "candidate_id": "candidate_a_control_live_v2",
            "config_path": _config_path("codex_cli_gpt5_e4_live_candidate_a_control_v2.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
        {
            "cell_id": "candidate_a_gpt54_mini",
            "model_tier": "gpt-5.4-mini",
            "candidate_id": "candidate_a_live_gpt54mini",
            "config_path": _config_path("codex_cli_gpt54mini_e4_live_candidate_a.yaml"),
            "provider_env_requirements": ["OPENAI_API_KEY"],
            "runner_script": str(_RUNNER_SCRIPT),
        },
    ]


def build_phase11_candidate_a_live_contract_status() -> Dict[str, Any]:
    cells, live_ready = build_phase11_contract_cell_statuses(
        build_phase11_candidate_a_live_cells(),
        identity_key="candidate_id",
    )
    return {
        "schema_version": "phase11_candidate_a_live_contract_v1",
        "runner_script": str(_RUNNER_SCRIPT),
        "live_ready": live_ready,
        "cells": cells,
    }


def _cell_by_id(cell_id: str) -> Dict[str, Any]:
    for cell in build_phase11_candidate_a_live_cells():
        if str(cell.get("cell_id") or "") == str(cell_id):
            return dict(cell)
    raise KeyError(f"unknown candidate_a live cell {cell_id}")


def build_phase11_candidate_a_live_command(
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


def execute_phase11_candidate_a_live_cell(
    *,
    cell_id: str,
    tasks_payload: Dict[str, Any],
    out_root: Path | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    contract = build_phase11_candidate_a_live_contract_status()
    target_cell = next(item for item in contract["cells"] if str(item.get("cell_id") or "") == str(cell_id))
    return execute_phase11_benchmark_cell(
        Phase11BenchmarkExecutionRequest(
            execution_schema_version="phase11_candidate_a_live_execution_v1",
            cell=target_cell,
            tasks_payload=tasks_payload,
            out_root=Path(out_root or _DEFAULT_OUTPUT_ROOT),
            workspace_root=_DEFAULT_WORKSPACE_ROOT / str(cell_id),
            runner_script=_RUNNER_SCRIPT,
            repo_root=_REPO_ROOT,
            dry_run=dry_run,
        ),
    )
