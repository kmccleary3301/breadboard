from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TrajectoryStep:
    turn: int
    observation: Dict[str, Any] = field(default_factory=dict)
    action: Dict[str, Any] = field(default_factory=dict)
    reward: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrajectoryEpisode:
    run_id: str
    steps: List[TrajectoryStep]
    summary: Dict[str, Any] = field(default_factory=dict)
    reward_v1: Optional[Dict[str, Any]] = None
    notes: Dict[str, Any] = field(default_factory=dict)


def build_stub_episode(
    *,
    run_id: str,
    reward_metrics: Dict[str, Any],
    run_summary: Dict[str, Any],
    reward_v1: Optional[Dict[str, Any]] = None,
) -> TrajectoryEpisode:
    steps: List[TrajectoryStep] = []
    turns = reward_metrics.get("turns") if isinstance(reward_metrics, dict) else None
    if isinstance(turns, list):
        for entry in turns:
            if not isinstance(entry, dict):
                continue
            turn_index = entry.get("turn")
            if not isinstance(turn_index, int):
                continue
            metrics = entry.get("metrics") or {}
            meta = entry.get("meta") or {}
            step = TrajectoryStep(
                turn=turn_index,
                observation={"reward_metrics": metrics},
                action={},
                reward=None,
                metadata={"reward_meta": meta},
            )
            steps.append(step)
    return TrajectoryEpisode(
        run_id=run_id,
        steps=steps,
        summary=run_summary,
        reward_v1=reward_v1,
        notes={
            "logprobs": "not_available",
            "trajectory_version": "stub-v1",
        },
    )
