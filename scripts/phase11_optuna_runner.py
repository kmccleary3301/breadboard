#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import optuna
except Exception:  # pragma: no cover - optional dependency
    optuna = None

try:
    import ray
except Exception:  # pragma: no cover - optional dependency
    ray = None

from agentic_coder_prototype.agent import AgenticCoder
from agentic_coder_prototype.optimize.optuna_config import load_optuna_config


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tasks" in payload:
        payload = payload["tasks"]
    if not isinstance(payload, list):
        raise ValueError("tasks file must be a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _suggest_param(trial: "optuna.trial.Trial", name: str, spec: Dict[str, Any]) -> Any:
    kind = str(spec.get("type") or "float").lower()
    if kind == "categorical":
        choices = spec.get("choices") or spec.get("values") or []
        return trial.suggest_categorical(name, list(choices))
    if kind == "int":
        low = int(spec.get("low"))
        high = int(spec.get("high"))
        step = spec.get("step")
        if step is None:
            return trial.suggest_int(name, low, high)
        return trial.suggest_int(name, low, high, step=step)
    low = float(spec.get("low"))
    high = float(spec.get("high"))
    log = bool(spec.get("log"))
    step = spec.get("step")
    return trial.suggest_float(name, low, high, step=step, log=log)


def _build_overrides(trial: "optuna.trial.Trial", space: Dict[str, Any], fixed: Dict[str, Any]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = dict(fixed)
    for key, spec in space.items():
        if not isinstance(spec, dict):
            continue
        overrides[key] = _suggest_param(trial, key, spec)
    return overrides


def _sample_override_random(space: Dict[str, Any], fixed: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    overrides: Dict[str, Any] = dict(fixed)
    for key, spec in space.items():
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("type") or "float").lower()
        if kind == "categorical":
            choices = spec.get("choices") or spec.get("values") or []
            overrides[key] = rng.choice(list(choices)) if choices else None
        elif kind == "int":
            low = int(spec.get("low"))
            high = int(spec.get("high"))
            step = spec.get("step")
            if step:
                count = max(int((high - low) / int(step)), 0)
                overrides[key] = low + int(step) * rng.randint(0, count)
            else:
                overrides[key] = rng.randint(low, high)
        else:
            low = float(spec.get("low"))
            high = float(spec.get("high"))
            overrides[key] = round(rng.uniform(low, high), 6)
    return overrides


def _extract_reward(run_dir: Path) -> Optional[float]:
    reward_v1_path = run_dir / "meta" / "reward_v1.json"
    summary_path = run_dir / "meta" / "run_summary.json"
    reward_v1 = None
    if reward_v1_path.exists():
        try:
            reward_v1 = json.loads(reward_v1_path.read_text())
        except Exception:
            reward_v1 = None
    if reward_v1 is None and summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
            reward_v1 = summary.get("reward_v1")
        except Exception:
            reward_v1 = None
    if isinstance(reward_v1, dict):
        episode_return = reward_v1.get("episode_return")
        if isinstance(episode_return, (int, float)):
            return float(episode_return)
    return None


def _evaluate_tasks(
    config_path: str,
    overrides: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    *,
    workspace_root: Path,
) -> Dict[str, Any]:
    reward_values: List[float] = []
    config_path = Path(config_path)
    missing_reward = 0
    completed = 0
    results: List[Dict[str, Any]] = []
    for idx, task in enumerate(tasks):
        task_id = str(task.get("id") or f"task_{idx+1}")
        prompt = str(task.get("prompt") or "").strip()
        if not prompt:
            results.append({"id": task_id, "error": "empty prompt"})
            continue
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
        if isinstance(completion_summary, dict) and completion_summary.get("completed"):
            completed += 1
        reward_value = None
        if run_dir:
            reward_value = _extract_reward(Path(run_dir))
        if reward_value is None:
            missing_reward += 1
        else:
            reward_values.append(float(reward_value))
        results.append(
            {
                "id": task_id,
                "run_dir": run_dir,
                "completion_summary": completion_summary,
                "episode_return": reward_value,
            }
        )
    mean_reward = (sum(reward_values) / len(reward_values)) if reward_values else 0.0
    return {
        "mean_reward": mean_reward,
        "completion_rate": (completed / len(tasks)) if tasks else 0.0,
        "missing_reward": missing_reward,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 11 Optuna sweep runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--space", required=True, help="Optuna search space JSON/YAML")
    parser.add_argument("--out", required=True)
    parser.add_argument("--study-name", default="breadboard_optuna")
    parser.add_argument("--storage", default=None, help="Optuna storage URL (e.g., sqlite:///study.db)")
    parser.add_argument("--n-trials", type=int, default=5)
    parser.add_argument("--sampler", default="tpe", choices=["tpe", "random"])
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--shuffle-seed", type=int, default=None)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workspace-root", default="tmp/optuna_runs")
    parser.add_argument("--ray", action="store_true", help="Evaluate trials inside Ray tasks")
    parser.add_argument("--ray-address", default=None, help="Ray address for remote cluster")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if optuna is None and not args.dry_run:
        raise SystemExit("optuna is not installed; pip install optuna or add to requirements.")
    if args.ray and ray is None:
        raise SystemExit("ray is not available; install ray or disable --ray")
    config_path = Path(args.config).resolve()
    tasks_path = Path(args.tasks).resolve()
    space_cfg = load_optuna_config(Path(args.space).resolve())
    tasks = _load_tasks(tasks_path)
    if args.shuffle_seed is not None:
        rng = random.Random(args.shuffle_seed)
        rng.shuffle(tasks)
    if isinstance(args.max_tasks, int) and args.max_tasks > 0:
        tasks = tasks[: args.max_tasks]

    manifest: List[Dict[str, Any]] = []

    if args.dry_run and optuna is None:
        rng = random.Random(args.seed or 0)
        for idx in range(args.n_trials):
            overrides = _sample_override_random(space_cfg["space"], space_cfg["fixed_overrides"], rng)
            manifest.append({"trial": f"trial_{idx}", "overrides": overrides})
        output = {
            "study": {
                "name": args.study_name,
                "direction": (space_cfg["study"].get("direction") or "maximize"),
                "dry_run": True,
            },
            "trials": manifest,
            "task_count": len(tasks),
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
        return 0

    direction = space_cfg["study"].get("direction") or "maximize"
    sampler = optuna.samplers.TPESampler(seed=args.seed) if args.sampler == "tpe" else optuna.samplers.RandomSampler(seed=args.seed)
    study = optuna.create_study(
        study_name=args.study_name,
        direction=direction,
        storage=args.storage,
        sampler=sampler,
        load_if_exists=True,
    )

    def objective(trial: "optuna.trial.Trial") -> float:
        overrides = _build_overrides(trial, space_cfg["space"], space_cfg["fixed_overrides"])
        trial_id = f"trial_{trial.number}"
        if args.dry_run:
            manifest.append({"trial": trial_id, "overrides": overrides})
            return 0.0
        workspace_root = Path(args.workspace_root) / trial_id
        workspace_root.mkdir(parents=True, exist_ok=True)
        if args.ray:
            if not ray.is_initialized():
                if args.ray_address:
                    ray.init(address=args.ray_address, include_dashboard=False)
                else:
                    ray.init(address="local", include_dashboard=False)
            remote_eval = ray.remote(_evaluate_tasks)
            eval_payload = ray.get(remote_eval.remote(str(config_path), overrides, tasks, workspace_root=workspace_root))
        else:
            eval_payload = _evaluate_tasks(config_path, overrides, tasks, workspace_root=workspace_root)
        trial.set_user_attr("completion_rate", eval_payload["completion_rate"])
        trial.set_user_attr("missing_reward", eval_payload["missing_reward"])
        trial.set_user_attr("results", eval_payload["results"])
        manifest.append({"trial": trial_id, "overrides": overrides, "mean_reward": eval_payload["mean_reward"]})
        return eval_payload["mean_reward"]

    study.optimize(objective, n_trials=args.n_trials)

    output = {
        "study": {
            "name": study.study_name,
            "direction": study.direction.name,
            "best_value": study.best_value if study.best_trial else None,
            "best_params": study.best_trial.params if study.best_trial else None,
        },
        "trials": manifest,
        "task_count": len(tasks),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
