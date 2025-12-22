#!/usr/bin/env python3
"""GEPA-style prompt evolver skeleton.

Implements a simplified reflective loop that proposes prompt edits, evaluates
them via the BreadBoard runner, and maintains a Pareto front over reward vs cost.

The script is intentionally lightweight: edit proposals append commentary to the
system prompt, and evaluation relies on the reward metrics emitted by the agent
(`meta/reward_metrics.json`). Extend the `_propose_edit` and `extract_objectives`
functions to integrate with richer failure traces or token cost models.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class PromptCandidate:
    name: str
    prompt_path: Path
    metadata: Dict[str, Any]


@dataclass
class CandidateResult:
    name: str
    reward: float
    cost: float
    telemetry_path: Path
    prompt_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GEPA-style prompt evolution sweep")
    parser.add_argument("prompt", help="Base system prompt file")
    parser.add_argument("config", help="Agent config path")
    parser.add_argument("task", help="Task prompt path")
    parser.add_argument("--iterations", type=int, default=3, help="Number of evolution iterations")
    parser.add_argument("--population", type=int, default=2, help="Number of candidates to evaluate per iteration")
    parser.add_argument("--telemetry-db", default=None, help="Optional SQLite DB path for reward metrics")
    parser.add_argument("--output", default="gepa_results.json", help="Output JSON with Pareto front")
    return parser.parse_args()


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_prompt(base_text: str, edit: str, dest: Path) -> None:
    dest.write_text(base_text + "\n" + edit, encoding="utf-8")


def propose_edits(base_prompt: str, iteration: int, population: int) -> List[str]:
    seeds = [
        "Re-iterate the completion handshake requirement clearly.",
        "Encourage concise tool usage when no edits are required.",
        "Highlight that tests should be run after substantial edits.",
        "Remind the model to keep diff hunks minimal and focused.",
    ]
    random.shuffle(seeds)
    chosen = seeds[:population]
    edits = []
    for idx, seed in enumerate(chosen):
        edits.append(f"<!-- GEPA iteration {iteration} proposal {idx}: {seed} -->")
    return edits


def evaluate_candidate(
    candidate: PromptCandidate,
    config: Path,
    task: Path,
    telemetry_db: Optional[str],
) -> CandidateResult:
    run_dir = Path("logging") / f"gepa_{candidate.name}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    env = dict(os.environ)
    telemetry_path = run_dir / "meta" / "telemetry.jsonl"
    env["RAYCODE_TELEMETRY_PATH"] = str(telemetry_path)
    if telemetry_db:
        env["KC_TELEMETRY_DB"] = telemetry_db
    overrides = json.dumps({
        "prompts.packs.base.system": str(candidate.prompt_path),
        "prompts.packs.base.builder": str(candidate.prompt_path),
        "prompts.packs.base.plan": str(candidate.prompt_path),
    })
    subprocess.run(
        [
            sys.executable,
            "main.py",
            str(config),
            "--task",
            str(task),
            "--overrides",
            overrides,
        ],
        check=True,
        env=env,
    )
    reward, cost = extract_objectives(run_dir)
    return CandidateResult(
        name=candidate.name,
        reward=reward,
        cost=cost,
        telemetry_path=telemetry_path,
        prompt_path=candidate.prompt_path,
    )


def extract_objectives(run_dir: Path) -> Tuple[float, float]:
    reward_file = run_dir / "meta" / "reward_metrics.json"
    if not reward_file.exists():
        return 0.0, 0.0
    payload = json.loads(reward_file.read_text())
    turns = payload.get("turns", [])
    if not turns:
        return 0.0, 0.0
    last_metrics = turns[-1].get("metrics", {})
    reward_keys = ["PAS", "ACS", "TPF_DELTA", "LED"]
    reward = sum(float(last_metrics.get(k, 0.0)) for k in reward_keys)
    cost = float(last_metrics.get("TE", 0.0))
    return reward, cost


def pareto_front(results: Iterable[CandidateResult]) -> List[CandidateResult]:
    front: List[CandidateResult] = []
    for result in results:
        dominated = False
        for other in front:
            if other.reward >= result.reward and other.cost <= result.cost and (
                other.reward > result.reward or other.cost < result.cost
            ):
                dominated = True
                break
        if dominated:
            continue
        front = [r for r in front if not (result.reward >= r.reward and result.cost <= r.cost and (result.reward > r.reward or result.cost < r.cost))]
        front.append(result)
    return front


def aggregate(results: List[CandidateResult], output: Path) -> None:
    payload = [
        {
            "name": r.name,
            "reward": r.reward,
            "cost": r.cost,
            "telemetry_path": str(r.telemetry_path),
            "prompt_path": str(r.prompt_path),
        }
        for r in results
    ]
    output.write_text(json.dumps(payload, indent=2))


def main() -> int:
    args = parse_args()
    base_prompt_path = Path(args.prompt).resolve()
    config_path = Path(args.config).resolve()
    task_path = Path(args.task).resolve()
    base_prompt_text = load_prompt(base_prompt_path)

    pareto: List[CandidateResult] = []
    for iteration in range(args.iterations):
        edits = propose_edits(base_prompt_text, iteration, args.population)
        candidates: List[PromptCandidate] = []
        for idx, edit in enumerate(edits):
            candidate_prompt = base_prompt_path.parent / f"gepa_prompt_{iteration}_{idx}.md"
            write_prompt(base_prompt_text, edit, candidate_prompt)
            candidates.append(PromptCandidate(name=f"iter{iteration}_cand{idx}", prompt_path=candidate_prompt, metadata={"edit": edit}))
        for candidate in candidates:
            result = evaluate_candidate(candidate, config_path, task_path, args.telemetry_db)
            pareto.append(result)
        pareto = pareto_front(pareto)

    aggregate(pareto, Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
