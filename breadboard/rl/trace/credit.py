from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.trace.graph import TrajectoryGraph


@dataclass(frozen=True)
class CreditFrame:
    credit_frame_id: str
    graph_id: str
    reward: float
    credited_node_ids: list[str]
    policy: str = "terminal_reward_to_prior_decisions"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "credit_frame_id": self.credit_frame_id,
            "graph_id": self.graph_id,
            "reward": self.reward,
            "credited_node_ids": list(self.credited_node_ids),
            "policy": self.policy,
            "metadata": dict(self.metadata),
        }


def build_terminal_credit_frame(graph: TrajectoryGraph, *, reward: float) -> CreditFrame:
    credited = [node.node_id for node in graph.nodes if node.node_kind in {"step", "evaluate"}]
    return CreditFrame(
        credit_frame_id=f"{graph.graph_id}.credit.terminal",
        graph_id=graph.graph_id,
        reward=float(reward),
        credited_node_ids=credited,
    )
