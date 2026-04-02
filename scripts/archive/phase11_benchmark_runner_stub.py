#!/usr/bin/env python3
"""Minimal headless benchmark runner stub for reward_v1 pilots.

Usage:
  python scripts/archive/phase11_benchmark_runner_stub.py \
    --config agent_configs/opencode_e4.yaml \
    --tasks tasks.json \
    --out results.json

Tasks file format (JSON array):
[
  {
    "id": "task-1",
    "prompt": "...",
    "task_type": "function",
    "latency_budget_ms": 120000,
    "initial_tpf": 0.0,
    "winrate_vs_baseline": 0.0,
    "max_steps": 8,
    "overrides": {"max_iterations": 8}
  },
  {"id": "task-2", "prompt": "...", "task_type": "bugfix"}
]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List
import random

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_existing_pythonpath = os.environ.get("PYTHONPATH", "")
if str(_REPO_ROOT) not in [item for item in _existing_pythonpath.split(os.pathsep) if item]:
    os.environ["PYTHONPATH"] = (
        f"{_REPO_ROOT}{os.pathsep}{_existing_pythonpath}" if _existing_pythonpath else str(_REPO_ROOT)
    )


def _load_env(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    try:
        for raw in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_load_env(_REPO_ROOT / ".env")

from agentic_coder_prototype.agent import AgenticCoder
from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.branch_receipt_contract import branch_receipt_executor_enabled
from agentic_coder_prototype.ctrees.finish_closure_contract import finish_closure_executor_enabled
from agentic_coder_prototype.ctrees.live_benchmark_adapter import (
    build_phase14_executor_context_metadata,
    build_phase14_executor_protocol_payload,
    build_phase18_finish_closure_context_metadata,
    build_phase18_finish_closure_protocol_payload,
    build_phase17_branch_receipt_context_metadata,
    build_phase17_branch_receipt_protocol_payload,
    build_phase16_invocation_first_context_metadata,
    build_phase16_invocation_first_protocol_payload,
    build_phase15_verifier_executor_context_metadata,
    build_phase15_verifier_executor_protocol_payload,
    build_phase13_runtime_context_metadata,
    build_phase13_runtime_protocol_payload,
    build_phase11_live_context_metadata,
    build_phase11_live_protocol_payload,
)
from agentic_coder_prototype.ctrees.executor_contract import executor_enabled
from agentic_coder_prototype.ctrees.invocation_first_contract import invocation_first_executor_enabled
from agentic_coder_prototype.ctrees.runtime_policy_contract import runtime_policy_enabled
from agentic_coder_prototype.ctrees.verifier_executor_contract import verifier_executor_enabled
from agentic_coder_prototype.ctrees.action_budget import evaluate_action_budget
from agentic_coder_prototype.ctrees.completion_gate import evaluate_completion_gate
from agentic_coder_prototype.ctrees.live_grounding import summarize_live_run
from agentic_coder_prototype.ctrees.progress_watchdog import evaluate_progress_watchdog


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tasks" in payload:
        payload = payload["tasks"]
    if not isinstance(payload, list):
        raise ValueError("tasks file must be a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _seed_workspace_from_task(workspace_dir: Path, task: Dict[str, Any]) -> bool:
    seed_path_raw = str(task.get("workspace_seed_path") or "").strip()
    if not seed_path_raw:
        return False
    seed_path = Path(seed_path_raw).expanduser().resolve()
    if not seed_path.exists() or not seed_path.is_dir():
        raise FileNotFoundError(f"workspace_seed_path does not exist: {seed_path}")
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    ignore = shutil.ignore_patterns(
        ".beads",
        ".git",
        ".pytest_cache",
        "__pycache__",
        "logging",
        "tmp",
        "docs_tmp",
        "node_modules",
    )
    shutil.copytree(seed_path, workspace_dir, ignore=ignore, dirs_exist_ok=False)
    return True


def _set_provider_dump_env(log_dir: Path, *, workspace_dir: Path, session_id: str) -> Dict[str, str | None]:
    previous = {
        "KC_PROVIDER_LOG_DIR": os.environ.get("KC_PROVIDER_LOG_DIR"),
        "KC_PROVIDER_WORKSPACE": os.environ.get("KC_PROVIDER_WORKSPACE"),
        "KC_PROVIDER_SESSION_ID": os.environ.get("KC_PROVIDER_SESSION_ID"),
    }
    os.environ["KC_PROVIDER_LOG_DIR"] = str(log_dir)
    os.environ["KC_PROVIDER_WORKSPACE"] = str(workspace_dir)
    os.environ["KC_PROVIDER_SESSION_ID"] = session_id
    return previous


def _restore_env(previous: Dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workspace-root", default="tmp/bench_runs")
    parser.add_argument("--shuffle-seed", type=int, default=None)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config)
    tasks_path = Path(args.tasks)
    out_path = Path(args.out)
    workspace_root = Path(args.workspace_root)
    if not args.dry_run:
        workspace_root.mkdir(parents=True, exist_ok=True)

    tasks = _load_tasks(tasks_path)
    loaded_config = load_agent_config(str(config_path))
    if args.shuffle_seed is not None:
        rng = random.Random(args.shuffle_seed)
        rng.shuffle(tasks)
    if isinstance(args.max_tasks, int) and args.max_tasks > 0:
        tasks = tasks[: args.max_tasks]
    results: List[Dict[str, Any]] = []

    completed_count = 0
    reward_values: List[float] = []
    missing_reward = 0

    for idx, task in enumerate(tasks):
        task_id = str(task.get("id") or f"task_{idx+1}")
        prompt = str(task.get("prompt") or "").strip()
        if not prompt:
            results.append({"id": task_id, "error": "empty prompt"})
            continue
        phase18_enabled = finish_closure_executor_enabled(loaded_config)
        phase17_enabled = branch_receipt_executor_enabled(loaded_config)
        phase16_enabled = invocation_first_executor_enabled(loaded_config)
        phase15_enabled = verifier_executor_enabled(loaded_config)
        phase14_enabled = executor_enabled(loaded_config)
        runtime_enabled = runtime_policy_enabled(loaded_config)
        overrides = task.get("overrides") or {}
        if isinstance(overrides, dict):
            overrides = dict(overrides)
        else:
            overrides = {}
        if args.dry_run:
            run_dir = None
            reward_v1 = None
            completion_summary = None
            if phase18_enabled:
                protocol_payload = build_phase18_finish_closure_protocol_payload(config=loaded_config, task=task)
            elif phase17_enabled:
                protocol_payload = build_phase17_branch_receipt_protocol_payload(config=loaded_config, task=task)
            elif phase16_enabled:
                protocol_payload = build_phase16_invocation_first_protocol_payload(config=loaded_config, task=task)
            elif phase15_enabled:
                protocol_payload = build_phase15_verifier_executor_protocol_payload(config=loaded_config, task=task)
            elif phase14_enabled:
                protocol_payload = build_phase14_executor_protocol_payload(config=loaded_config, task=task)
            elif runtime_enabled:
                protocol_payload = build_phase13_runtime_protocol_payload(config=loaded_config, task=task)
            else:
                protocol_payload = build_phase11_live_protocol_payload(config=loaded_config, task=task)
        else:
            workspace_dir = workspace_root / task_id
            seeded_workspace = _seed_workspace_from_task(workspace_dir, task)
            provider_dump_dir = workspace_dir / "provider_dump"
            provider_dump_dir.mkdir(parents=True, exist_ok=True)
            coder = AgenticCoder(str(config_path), workspace_dir=str(workspace_dir), overrides=overrides)
            if phase18_enabled:
                protocol_payload = build_phase18_finish_closure_protocol_payload(config=loaded_config, task=task)
            elif phase17_enabled:
                protocol_payload = build_phase17_branch_receipt_protocol_payload(config=loaded_config, task=task)
            elif phase16_enabled:
                protocol_payload = build_phase16_invocation_first_protocol_payload(config=loaded_config, task=task)
            elif phase15_enabled:
                protocol_payload = build_phase15_verifier_executor_protocol_payload(config=loaded_config, task=task)
            elif phase14_enabled:
                protocol_payload = build_phase14_executor_protocol_payload(config=loaded_config, task=task)
            elif runtime_enabled:
                protocol_payload = build_phase13_runtime_protocol_payload(config=loaded_config, task=task)
            else:
                protocol_payload = build_phase11_live_protocol_payload(config=loaded_config, task=task)
            context = {
                "task_type": task.get("task_type") or "general",
                "latency_budget_ms": task.get("latency_budget_ms"),
                "initial_tpf": task.get("initial_tpf"),
                "winrate_vs_baseline": task.get("winrate_vs_baseline"),
            }
            if phase18_enabled:
                context.update(build_phase18_finish_closure_context_metadata(protocol_payload))
            elif phase17_enabled:
                context.update(build_phase17_branch_receipt_context_metadata(protocol_payload))
            elif phase16_enabled:
                context.update(build_phase16_invocation_first_context_metadata(protocol_payload))
            elif phase15_enabled:
                context.update(build_phase15_verifier_executor_context_metadata(protocol_payload))
            elif phase14_enabled:
                context.update(build_phase14_executor_context_metadata(protocol_payload))
            elif runtime_enabled:
                context.update(build_phase13_runtime_context_metadata(protocol_payload))
            else:
                context.update(build_phase11_live_context_metadata(protocol_payload))
            previous_preserve = os.environ.get("PRESERVE_SEEDED_WORKSPACE")
            provider_env = _set_provider_dump_env(
                provider_dump_dir,
                workspace_dir=workspace_dir,
                session_id=f"{task_id}-bench",
            )
            if seeded_workspace:
                os.environ["PRESERVE_SEEDED_WORKSPACE"] = "1"
            try:
                result = coder.run_task(
                    str(protocol_payload.get("prompt") or prompt),
                    max_iterations=task.get("max_steps"),
                    context=context,
                )
            finally:
                _restore_env(provider_env)
                if seeded_workspace:
                    if previous_preserve is None:
                        os.environ.pop("PRESERVE_SEEDED_WORKSPACE", None)
                    else:
                        os.environ["PRESERVE_SEEDED_WORKSPACE"] = previous_preserve
            run_dir = result.get("run_dir") or result.get("logging_dir")
            completion_summary = result.get("completion_summary")
        reward_v1 = None
        run_summary = None
        if run_dir:
            reward_path = Path(run_dir) / "meta" / "reward_v1.json"
            summary_path = Path(run_dir) / "meta" / "run_summary.json"
            if reward_path.exists():
                try:
                    reward_v1 = json.loads(reward_path.read_text(encoding="utf-8"))
                except Exception:
                    reward_v1 = None
            if reward_v1 is None and summary_path.exists():
                try:
                    run_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    reward_v1 = run_summary.get("reward_v1")
                except Exception:
                    run_summary = None
        if isinstance(completion_summary, dict) and completion_summary.get("completed"):
            completed_count += 1
        if reward_v1 and isinstance(reward_v1.get("episode_return"), (int, float)):
            reward_values.append(float(reward_v1["episode_return"]))
        elif not args.dry_run:
            missing_reward += 1
        grounded_summary = summarize_live_run(
            run_dir=run_dir,
            completion_summary=completion_summary if isinstance(completion_summary, dict) else None,
        )
        action_budget_cfg = dict(((protocol_payload.get("control_contract") or {}).get("action_budget") or {}))
        action_budget = evaluate_action_budget(
            grounded_summary,
            max_inspection_turns=int(action_budget_cfg.get("max_inspection_turns") or 2),
            mandatory_write_by_turn=int(action_budget_cfg.get("mandatory_write_by_turn") or 4),
            escalation_after_no_progress=int(action_budget_cfg.get("escalation_after_no_progress") or 2),
        )
        completion_gate = evaluate_completion_gate(grounded_summary)
        watchdog = evaluate_progress_watchdog(
            grounded_summary,
            max_no_progress_turns=int(
                (((protocol_payload.get("control_contract") or {}).get("watchdog") or {}).get("max_no_progress_turns") or 3)
            ),
            escalation_after_no_progress=int(action_budget_cfg.get("escalation_after_no_progress") or 2),
        )
        completion_gate = evaluate_completion_gate(grounded_summary, action_budget=action_budget)

        results.append(
            {
                "id": task_id,
                "run_dir": run_dir,
                "completion_summary": completion_summary,
                "reward_v1": reward_v1,
                "run_summary": run_summary,
                "grounded_summary": grounded_summary,
                "provider_dump_dir": str(provider_dump_dir) if not args.dry_run else None,
                "action_budget": action_budget,
                "completion_gate": completion_gate,
                "progress_watchdog": watchdog,
                "phase11_live_protocol": protocol_payload,
                "dry_run": bool(args.dry_run),
            }
        )

    summary = {
        "total": len(results),
        "completed": completed_count,
        "completion_rate": (completed_count / len(results)) if results else 0.0,
        "grounded_completed": sum(
            1 for item in results if bool(((item.get("grounded_summary") or {}).get("grounded") or {}).get("grounded_completion"))
        ),
        "ungrounded_stop_count": sum(
            1 for item in results if bool(((item.get("grounded_summary") or {}).get("grounded") or {}).get("ungrounded_stop"))
        ),
        "verified_completed": sum(
            1 for item in results if bool(((item.get("completion_gate") or {}).get("verified_completion")))
        ),
        "watchdog_triggered_count": sum(
            1 for item in results if bool((item.get("progress_watchdog") or {}).get("triggered"))
        ),
        "mean_episode_return": (sum(reward_values) / len(reward_values)) if reward_values else None,
        "missing_reward_v1": missing_reward,
    }
    if args.dry_run:
        summary["dry_run"] = True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
