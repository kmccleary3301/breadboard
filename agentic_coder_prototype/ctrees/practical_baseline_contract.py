from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .downstream_task_pack import build_phase11_downstream_benchmark_tasks, build_phase12_calibration_tasks


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
    cells: List[Dict[str, Any]] = []
    live_ready = False
    for cell in build_phase11_practical_baseline_cells():
        missing_env = [name for name in list(cell.get("provider_env_requirements") or []) if not os.environ.get(name)]
        status = "contract_ready" if not missing_env else "provider_blocked_missing_env"
        if not missing_env:
            live_ready = True
        cells.append(
            {
                "cell_id": str(cell.get("cell_id") or ""),
                "model_tier": str(cell.get("model_tier") or ""),
                "baseline_id": str(cell.get("baseline_id") or ""),
                "config_path": str(cell.get("config_path") or ""),
                "runner_script": str(cell.get("runner_script") or ""),
                "provider_env_requirements": list(cell.get("provider_env_requirements") or []),
                "missing_env": missing_env,
                "status": status,
            }
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


def _pythonpath_env() -> str:
    existing = str(os.environ.get("PYTHONPATH") or "").strip()
    if existing:
        return f"{_REPO_ROOT}:{existing}"
    return str(_REPO_ROOT)


def _workspace_root_for_cell(cell_id: str) -> Path:
    return _DEFAULT_WORKSPACE_ROOT / str(cell_id)


def build_phase11_practical_baseline_command(
    *,
    cell_id: str,
    tasks_path: Path,
    out_path: Path,
    workspace_root: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    cell = _cell_by_id(cell_id)
    cmd = [
        "python",
        str(_RUNNER_SCRIPT),
        "--config",
        str(cell["config_path"]),
        "--tasks",
        str(tasks_path),
        "--out",
        str(out_path),
        "--workspace-root",
        str(workspace_root),
    ]
    if dry_run:
        cmd.append("--dry-run")
    return {
        "cmd": cmd,
        "env": {"PYTHONPATH": _pythonpath_env()},
        "cell": cell,
    }


def execute_phase11_practical_baseline_cell(
    *,
    cell_id: str,
    out_root: Path | None = None,
    dry_run: bool = False,
    task_pack: str = "phase11_downstream_pilot_v1",
) -> Dict[str, Any]:
    contract = build_phase11_practical_baseline_contract_status()
    target_cell = next(item for item in contract["cells"] if str(item.get("cell_id") or "") == str(cell_id))
    resolved_out_root = Path(out_root or _DEFAULT_OUTPUT_ROOT)
    resolved_out_root.mkdir(parents=True, exist_ok=True)
    tasks_path = resolved_out_root / f"{cell_id}_tasks.json"
    out_path = resolved_out_root / f"{cell_id}_results.json"
    workspace_root = _workspace_root_for_cell(cell_id)
    if not dry_run:
        workspace_root.mkdir(parents=True, exist_ok=True)
    tasks_payload = build_phase11_practical_baseline_tasks_payload(task_pack=task_pack)
    tasks_path.write_text(json.dumps(tasks_payload, indent=2), encoding="utf-8")

    command_payload = build_phase11_practical_baseline_command(
        cell_id=cell_id,
        tasks_path=tasks_path,
        out_path=out_path,
        workspace_root=workspace_root,
        dry_run=dry_run,
    )
    missing_env = list(target_cell.get("missing_env") or [])
    if missing_env and not dry_run:
        return {
            "schema_version": "phase11_practical_baseline_execution_v1",
            "cell_id": cell_id,
            "model_tier": str(target_cell.get("model_tier") or ""),
            "status": "provider_blocked_missing_env",
            "missing_env": missing_env,
            "tasks_path": str(tasks_path),
            "out_path": str(out_path),
            "workspace_root": str(workspace_root),
            "command": command_payload["cmd"],
        }

    env = os.environ.copy()
    env.update(dict(command_payload.get("env") or {}))
    completed = subprocess.run(
        list(command_payload["cmd"]),
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    payload: Dict[str, Any] = {
        "schema_version": "phase11_practical_baseline_execution_v1",
        "cell_id": cell_id,
        "model_tier": str(target_cell.get("model_tier") or ""),
        "status": "dry_run_executed" if dry_run and completed.returncode == 0 else ("executed" if completed.returncode == 0 else "runner_failed"),
        "missing_env": missing_env,
        "tasks_path": str(tasks_path),
        "out_path": str(out_path),
        "workspace_root": str(workspace_root),
        "task_pack": task_pack,
        "command": command_payload["cmd"],
        "returncode": int(completed.returncode),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if out_path.exists():
        try:
            payload["result"] = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            payload["result"] = None
    return payload
