from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

@dataclass(frozen=True)
class Phase11BenchmarkExecutionRequest:
    execution_schema_version: str
    cell: Dict[str, Any]
    tasks_payload: Dict[str, Any]
    out_root: Path
    workspace_root: Path
    runner_script: Path
    repo_root: Path
    dry_run: bool
    executed_payload_fields: Dict[str, Any] | None = None


def build_phase11_contract_cell_statuses(
    cells: Sequence[Dict[str, Any]],
    *,
    identity_key: str,
) -> tuple[List[Dict[str, Any]], bool]:
    statuses: List[Dict[str, Any]] = []
    live_ready = False
    for cell in cells:
        missing_env = [name for name in list(cell.get("provider_env_requirements") or []) if not os.environ.get(name)]
        if not missing_env:
            live_ready = True
        statuses.append(
            {
                "cell_id": str(cell.get("cell_id") or ""),
                "model_tier": str(cell.get("model_tier") or ""),
                identity_key: str(cell.get(identity_key) or ""),
                "config_path": str(cell.get("config_path") or ""),
                "runner_script": str(cell.get("runner_script") or ""),
                "provider_env_requirements": list(cell.get("provider_env_requirements") or []),
                "missing_env": missing_env,
                "status": "contract_ready" if not missing_env else "provider_blocked_missing_env",
            }
        )
    return statuses, live_ready


def build_phase11_benchmark_command(
    *,
    cell: Dict[str, Any],
    runner_script: Path,
    repo_root: Path,
    tasks_path: Path,
    out_path: Path,
    workspace_root: Path,
    dry_run: bool,
) -> Dict[str, Any]:
    cmd = [
        "python",
        str(runner_script),
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
    existing_pythonpath = str(os.environ.get("PYTHONPATH") or "").strip()
    pythonpath = f"{repo_root}:{existing_pythonpath}" if existing_pythonpath else str(repo_root)
    return {
        "cmd": cmd,
        "env": {"PYTHONPATH": pythonpath},
        "cell": cell,
    }


def execute_phase11_benchmark_cell(
    request: Phase11BenchmarkExecutionRequest,
) -> Dict[str, Any]:
    cell_id = str(request.cell.get("cell_id") or "")
    request.out_root.mkdir(parents=True, exist_ok=True)
    tasks_path = request.out_root / f"{cell_id}_tasks.json"
    out_path = request.out_root / f"{cell_id}_results.json"
    if not request.dry_run:
        request.workspace_root.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(json.dumps(request.tasks_payload, indent=2), encoding="utf-8")

    command_payload = build_phase11_benchmark_command(
        cell=request.cell,
        runner_script=request.runner_script,
        repo_root=request.repo_root,
        tasks_path=tasks_path,
        out_path=out_path,
        workspace_root=request.workspace_root,
        dry_run=request.dry_run,
    )
    missing_env = list(request.cell.get("missing_env") or [])
    if missing_env and not request.dry_run:
        return {
            "schema_version": request.execution_schema_version,
            "cell_id": cell_id,
            "model_tier": str(request.cell.get("model_tier") or ""),
            "status": "provider_blocked_missing_env",
            "missing_env": missing_env,
            "tasks_path": str(tasks_path),
            "out_path": str(out_path),
            "workspace_root": str(request.workspace_root),
            "command": command_payload["cmd"],
        }

    env = os.environ.copy()
    env.update(dict(command_payload.get("env") or {}))
    completed = subprocess.run(
        list(command_payload["cmd"]),
        cwd=str(request.repo_root),
        env=env,
        capture_output=True,
        text=True,
    )
    payload: Dict[str, Any] = {
        "schema_version": request.execution_schema_version,
        "cell_id": cell_id,
        "model_tier": str(request.cell.get("model_tier") or ""),
        "status": "dry_run_executed"
        if request.dry_run and completed.returncode == 0
        else ("executed" if completed.returncode == 0 else "runner_failed"),
        "missing_env": missing_env,
        "tasks_path": str(tasks_path),
        "out_path": str(out_path),
        "workspace_root": str(request.workspace_root),
    }
    if request.executed_payload_fields:
        payload.update(request.executed_payload_fields)
    payload.update(
        {
            "command": command_payload["cmd"],
            "returncode": int(completed.returncode),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )
    if out_path.exists():
        try:
            payload["result"] = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            payload["result"] = None
    return payload
