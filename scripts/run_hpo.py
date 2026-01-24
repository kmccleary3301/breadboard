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
    parser = argparse.ArgumentParser(description="Run HPO sweeps over BreadBoard configs")
    parser.add_argument("config", help="Path to base agent config")
    parser.add_argument("task", help="Path to task prompt")
    parser.add_argument("--trials", type=int, default=3, help="Number of random trials")
    parser.add_argument("--trials-config", default=None, help="Optional JSON/YAML trial config")
    parser.add_argument("--telemetry-db", default=None, help="Optional SQLite DB path")
    parser.add_argument("--output", default="hpo_results.json", help="Where to write aggregated results")
    parser.add_argument("--dry-run", action="store_true", help="Emit trial manifest without running")
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


def _load_config_payload(path: Path) -> Any:
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("PyYAML is required for YAML trial configs") from exc
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _random_value(spec: Dict[str, Any]) -> Any:
    if "choices" in spec and isinstance(spec["choices"], list):
        return random.choice(spec["choices"])
    if "values" in spec and isinstance(spec["values"], list):
        return random.choice(spec["values"])
    if "min" in spec and "max" in spec:
        low = float(spec["min"])
        high = float(spec["max"])
        if spec.get("type") == "int":
            return random.randint(int(low), int(high))
        return round(random.uniform(low, high), 4)
    raise ValueError(f"Unsupported random spec: {spec}")


def load_trials_config(base_config: Path, path: Path) -> List[TrialConfig]:
    payload = _load_config_payload(path)
    if isinstance(payload, dict) and "trials" in payload:
        payload = payload.get("trials")
    if isinstance(payload, list):
        trials: List[TrialConfig] = []
        for idx, entry in enumerate(payload):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or f"trial_{idx}"
            overrides = entry.get("overrides") or {}
            if not isinstance(overrides, dict):
                overrides = {}
            trials.append(TrialConfig(name=str(name), agent_config=base_config, overrides=overrides))
        return trials
    if isinstance(payload, dict) and "random" in payload:
        spec = payload.get("random") or {}
        count = int(spec.get("count") or 0)
        space = spec.get("space") or {}
        trials: List[TrialConfig] = []
        for idx in range(count):
            overrides: Dict[str, Any] = {}
            if isinstance(space, dict):
                for key, val in space.items():
                    if isinstance(val, dict):
                        overrides[key] = _random_value(val)
            trials.append(TrialConfig(name=f"trial_{idx}", agent_config=base_config, overrides=overrides))
        return trials
    raise ValueError("Unsupported trials config format")


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
    reward_v1_path = run_dir / "meta" / "reward_v1.json"
    summary_path = run_dir / "meta" / "run_summary.json"
    reward_metrics_path = run_dir / "meta" / "reward_metrics.json"
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
    if not reward_metrics_path.exists():
        return 0.0
    data = json.loads(reward_metrics_path.read_text())
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
    if args.trials_config:
        trials = load_trials_config(base_config, Path(args.trials_config))
    else:
        trials = sample_trials(base_config, args.trials)
    results: List[TrialResult] = []
    for trial in trials:
        if args.dry_run:
            results.append(
                TrialResult(name=trial.name, reward=0.0, metrics={"overrides": trial.overrides}, telemetry_path=Path(""))
            )
        else:
            result = run_trial(trial, task_path, args.telemetry_db)
            results.append(result)
    aggregate_results(results, Path(args.output))
    return 0


if __name__ == "__main__":
    import os
    raise SystemExit(main())
