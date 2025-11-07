#!/usr/bin/env python3
"""Phase 5 HPO runner skeleton.

Selects configuration variants, executes evaluation jobs, and aggregates reward metrics
from telemetry SQLite/JSONL outputs. Designed to be extended with real benchmark adapters.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class TrialConfig:
    name: str
    agent_config: Path
    overrides: Dict[str, Any]


@dataclass
class TrialResult:
    name: str
    reward: float
    metrics: Dict[str, Any]
    telemetry_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run HPO sweeps over KyleCode configs")
    parser.add_argument("config", help="Path to base agent config")
    parser.add_argument("task", help="Path to task prompt")
    parser.add_argument("--trials", type=int, default=3, help="Number of random trials")
    parser.add_argument("--telemetry-db", default=None, help="Optional SQLite DB path")
    parser.add_argument("--output", default="hpo_results.json", help="Where to write aggregated results")
    return parser.parse_args()


def sample_trials(base_config: Path, count: int) -> List[TrialConfig]:
    trials: List[TrialConfig] = []
    for idx in range(count):
        variations = {
            "providers.models[0].params.temperature": round(random.uniform(0.0, 0.4), 2),
            "tools.concurrency.groups[0].max_parallel": random.choice([2, 3, 4]),
        }
        trials.append(TrialConfig(name=f"trial_{idx}", agent_config=base_config, overrides=variations))
    return trials


def run_trial(trial: TrialConfig, task: Path, telemetry_db: Optional[str]) -> TrialResult:
    env = dict(os.environ)
    overrides_json = json.dumps(trial.overrides)
    output_dir = Path("logging") / f"hpo_{trial.name}"
    output_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path = output_dir / "meta" / "telemetry.jsonl"
    env["RAYCODE_TELEMETRY_PATH"] = str(telemetry_path)
    if telemetry_db:
        env["KC_TELEMETRY_DB"] = telemetry_db
    subprocess.run(
        [
            sys.executable,
            "main.py",
            str(trial.agent_config),
            "--task",
            str(task),
            "--overrides",
            overrides_json,
        ],
        check=True,
        env=env,
    )
    reward = extract_reward(output_dir)
    return TrialResult(name=trial.name, reward=reward, metrics={"overrides": trial.overrides}, telemetry_path=telemetry_path)


def extract_reward(run_dir: Path) -> float:
    reward_file = run_dir / "meta" / "reward_metrics.json"
    if not reward_file.exists():
        return 0.0
    data = json.loads(reward_file.read_text())
    turns = data.get("turns", [])
    if not turns:
        return 0.0
    last_metrics = turns[-1].get("metrics", {})
    reward_keys = ["PAS", "TPF_DELTA", "LED", "ACS"]
    reward = sum(float(last_metrics.get(k, 0.0)) for k in reward_keys)
    return reward


def aggregate_results(results: Iterable[TrialResult], output: Path) -> None:
    payload = [asdict(result) for result in results]
    output.write_text(json.dumps(payload, indent=2))


def main() -> int:
    args = parse_args()
    base_config = Path(args.config).resolve()
    task_path = Path(args.task).resolve()
    trials = sample_trials(base_config, args.trials)
    results: List[TrialResult] = []
    for trial in trials:
        result = run_trial(trial, task_path, args.telemetry_db)
        results.append(result)
    aggregate_results(results, Path(args.output))
    return 0


if __name__ == "__main__":
    import os
    raise SystemExit(main())
