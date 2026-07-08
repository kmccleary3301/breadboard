from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.trace.graph import TrajectoryGraph


@dataclass(frozen=True)
class ReplayParityReport:
    parity_tier: str
    passed: bool
    mismatches: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parity_tier": self.parity_tier,
            "passed": self.passed,
            "mismatches": list(self.mismatches),
            "metadata": dict(self.metadata),
        }


def compare_replay_parity(live_graph: TrajectoryGraph, replay_graph: TrajectoryGraph) -> ReplayParityReport:
    mismatches: list[str] = []
    live_kinds = [node.node_kind for node in live_graph.nodes]
    replay_kinds = [node.node_kind for node in replay_graph.nodes]
    if live_kinds != replay_kinds:
        mismatches.append("node_kind_sequence_mismatch")
    live_edges = [edge.edge_kind for edge in live_graph.edges]
    replay_edges = [edge.edge_kind for edge in replay_graph.edges]
    if live_edges != replay_edges:
        mismatches.append("edge_kind_sequence_mismatch")
    live_rewards = [
        node.payload.get("payload", {}).get("reward")
        for node in live_graph.nodes
        if node.node_kind == "evaluate"
    ]
    replay_rewards = [
        node.payload.get("payload", {}).get("reward")
        for node in replay_graph.nodes
        if node.node_kind == "evaluate"
    ]
    if live_rewards != replay_rewards:
        mismatches.append("reward_mismatch")
    return ReplayParityReport(
        parity_tier="T2_deterministic_runtime_verifier",
        passed=not mismatches,
        mismatches=mismatches,
        metadata={"live_graph_id": live_graph.graph_id, "replay_graph_id": replay_graph.graph_id},
    )
