#!/usr/bin/env python3
"""Paired baseline vs candidate evaluation stub.

Runs each task twice (baseline config, candidate config) and reports win/loss/tie
based on reward_v1 episode_return.

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
  }
]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List
import random

from agentic_coder_prototype.agent import AgenticCoder
from agentic_coder_prototype.reward.aggregator import aggregate_reward_v1, validate_reward_v1


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tasks" in payload:
        payload = payload["tasks"]
    if not isinstance(payload, list):
        raise ValueError("tasks file must be a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _run_one(config_path: Path, workspace_root: Path, task: Dict[str, Any], label: str) -> Dict[str, Any]:
    task_id = str(task.get("id") or "task")
    prompt = str(task.get("prompt") or "").strip()
    if not prompt:
        return {"error": "empty prompt"}
    overrides = task.get("overrides") or {}
    if isinstance(overrides, dict):
        overrides = dict(overrides)
    else:
        overrides = {}
    workspace_dir = workspace_root / label / task_id
    coder = AgenticCoder(str(config_path), workspace_dir=str(workspace_dir), overrides=overrides)
    context = {
        "task_type": task.get("task_type") or "general",
        "latency_budget_ms": task.get("latency_budget_ms"),
        "initial_tpf": task.get("initial_tpf"),
        "winrate_vs_baseline": task.get("winrate_vs_baseline"),
    }
    result = coder.run_task(prompt, max_iterations=task.get("max_steps"), context=context)
    run_dir = result.get("run_dir") or result.get("logging_dir")
    reward_v1 = None
    episode_return = None
    reward_metrics = None
    run_summary = None
    if run_dir:
        reward_path = Path(run_dir) / "meta" / "reward_v1.json"
        reward_metrics_path = Path(run_dir) / "meta" / "reward_metrics.json"
        run_summary_path = Path(run_dir) / "meta" / "run_summary.json"
        if reward_path.exists():
            try:
                reward_v1 = json.loads(reward_path.read_text(encoding="utf-8"))
                episode_return = reward_v1.get("episode_return")
            except Exception:
                reward_v1 = None
        if reward_metrics_path.exists():
            try:
                reward_metrics = json.loads(reward_metrics_path.read_text(encoding="utf-8"))
            except Exception:
                reward_metrics = None
        if run_summary_path.exists():
            try:
                run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
            except Exception:
                run_summary = None
    return {
        "run_dir": run_dir,
        "completion_summary": result.get("completion_summary"),
        "reward_v1": reward_v1,
        "episode_return": episode_return,
        "reward_metrics": reward_metrics,
        "run_summary": run_summary,
    }


def _recompute_reward_v1(
    payload: Dict[str, Any],
    *,
    task: Dict[str, Any],
    winrate: float,
) -> Dict[str, Any] | None:
    reward_metrics = payload.get("reward_metrics")
    if not isinstance(reward_metrics, dict):
        return None
    completion_summary = None
    run_summary = payload.get("run_summary")
    if isinstance(run_summary, dict):
        completion_summary = run_summary.get("completion_summary")
    if completion_summary is None:
        completion_summary = payload.get("completion_summary")
    reward_cfg = None
    reward_v1 = payload.get("reward_v1")
    if isinstance(reward_v1, dict):
        reward_cfg = reward_v1.get("config")
    context = {
        "task_type": task.get("task_type") or "general",
        "latency_budget_ms": task.get("latency_budget_ms"),
        "initial_tpf": task.get("initial_tpf"),
        "winrate_vs_baseline": winrate,
    }
    try:
        recomputed = aggregate_reward_v1(
            reward_metrics,
            completion_summary=completion_summary,
            config=reward_cfg,
            context=context,
        )
        recomputed["validation"] = validate_reward_v1(recomputed)
        return recomputed
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workspace-root", default="tmp/paired_eval_runs")
    parser.add_argument("--shuffle-seed", type=int, default=None)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    baseline = Path(args.baseline)
    candidate = Path(args.candidate)
    tasks_path = Path(args.tasks)
    out_path = Path(args.out)
    workspace_root = Path(args.workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    tasks = _load_tasks(tasks_path)
    if args.shuffle_seed is not None:
        rng = random.Random(args.shuffle_seed)
        rng.shuffle(tasks)
    if isinstance(args.max_tasks, int) and args.max_tasks > 0:
        tasks = tasks[: args.max_tasks]

    results: List[Dict[str, Any]] = []
    wins = losses = ties = 0
    base_recomputed_values: List[float] = []
    cand_recomputed_values: List[float] = []

    for task in tasks:
        task_id = str(task.get("id") or "task")
        if args.dry_run:
            base_result = {"dry_run": True}
            cand_result = {"dry_run": True}
            base_score = None
            cand_score = None
        else:
            base_result = _run_one(baseline, workspace_root, task, "baseline")
            cand_result = _run_one(candidate, workspace_root, task, "candidate")
            base_score = base_result.get("episode_return")
            cand_score = cand_result.get("episode_return")

        outcome = "tie"
        if isinstance(base_score, (int, float)) and isinstance(cand_score, (int, float)):
            if cand_score > base_score:
                outcome = "win"
                wins += 1
            elif cand_score < base_score:
                outcome = "loss"
                losses += 1
            else:
                ties += 1
        else:
            outcome = "unknown"

        winrate = None
        if outcome == "win":
            winrate = 1.0
        elif outcome == "loss":
            winrate = 0.0
        elif outcome == "tie":
            winrate = 0.5

        if winrate is not None and not args.dry_run:
            base_winrate = 1.0 - winrate
            base_recomputed = _recompute_reward_v1(base_result, task=task, winrate=base_winrate)
            cand_recomputed = _recompute_reward_v1(cand_result, task=task, winrate=winrate)

            if base_recomputed:
                base_result["reward_v1_recomputed"] = base_recomputed
                base_result["episode_return_recomputed"] = base_recomputed.get("episode_return")
                if isinstance(base_recomputed.get("episode_return"), (int, float)):
                    base_recomputed_values.append(float(base_recomputed["episode_return"]))
            if cand_recomputed:
                cand_result["reward_v1_recomputed"] = cand_recomputed
                cand_result["episode_return_recomputed"] = cand_recomputed.get("episode_return")
                if isinstance(cand_recomputed.get("episode_return"), (int, float)):
                    cand_recomputed_values.append(float(cand_recomputed["episode_return"]))

        results.append(
            {
                "id": task_id,
                "baseline": base_result,
                "candidate": cand_result,
                "outcome": outcome,
                "winrate_context": winrate,
            }
        )

    summary = {
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "total": len(results),
    }
    if summary["total"]:
        summary["winrate_vs_baseline"] = (wins + 0.5 * ties) / summary["total"]
    else:
        summary["winrate_vs_baseline"] = 0.0
    if base_recomputed_values:
        summary["mean_base_recomputed"] = sum(base_recomputed_values) / len(base_recomputed_values)
    if cand_recomputed_values:
        summary["mean_candidate_recomputed"] = sum(cand_recomputed_values) / len(cand_recomputed_values)
    if base_recomputed_values and cand_recomputed_values:
        summary["mean_delta_recomputed"] = summary["mean_candidate_recomputed"] - summary["mean_base_recomputed"]
    if args.dry_run:
        summary["dry_run"] = True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
