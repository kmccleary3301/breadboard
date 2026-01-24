#!/usr/bin/env python3
"""Minimal headless benchmark runner stub for reward_v1 pilots.

Usage:
  python scripts/phase11_benchmark_runner_stub.py \
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
from typing import Any, Dict, List
import random

from agentic_coder_prototype.agent import AgenticCoder


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tasks" in payload:
        payload = payload["tasks"]
    if not isinstance(payload, list):
        raise ValueError("tasks file must be a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


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
        overrides = task.get("overrides") or {}
        if isinstance(overrides, dict):
            overrides = dict(overrides)
        else:
            overrides = {}
        if args.dry_run:
            run_dir = None
            reward_v1 = None
            completion_summary = None
        else:
            workspace_dir = workspace_root / task_id
            coder = AgenticCoder(str(config_path), workspace_dir=str(workspace_dir), overrides=overrides)
            context = {
                "task_type": task.get("task_type") or "general",
                "latency_budget_ms": task.get("latency_budget_ms"),
                "initial_tpf": task.get("initial_tpf"),
                "winrate_vs_baseline": task.get("winrate_vs_baseline"),
            }
            result = coder.run_task(prompt, max_iterations=task.get("max_steps"), context=context)
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

        results.append(
            {
                "id": task_id,
                "run_dir": run_dir,
                "completion_summary": completion_summary,
                "reward_v1": reward_v1,
                "run_summary": run_summary,
                "dry_run": bool(args.dry_run),
            }
        )

    summary = {
        "total": len(results),
        "completed": completed_count,
        "completion_rate": (completed_count / len(results)) if results else 0.0,
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
