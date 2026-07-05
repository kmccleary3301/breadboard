from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from breadboard.rl.session.events import SessionEvent
from breadboard.rl.trace.edges import TraceEdge
from breadboard.rl.trace.nodes import TraceNode


@dataclass(frozen=True)
class TrajectoryGraph:
    graph_id: str
    nodes: list[TraceNode]
    edges: list[TraceEdge]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            "metadata": dict(self.metadata),
        }


def build_graph_from_session_events(
    *,
    graph_id: str,
    session_id: str,
    events: Sequence[SessionEvent],
    metadata: dict[str, Any] | None = None,
) -> TrajectoryGraph:
    nodes = [
        TraceNode(
            node_id=event.event_id,
            node_kind=event.event_kind,
            payload=event.to_dict(),
        )
        for event in events
    ]
    edges = [
        TraceEdge(
            edge_id=f"{session_id}.edge.{index}",
            source_id=events[index - 1].event_id,
            target_id=events[index].event_id,
            edge_kind="session_order",
            metadata={"index": index},
        )
        for index in range(1, len(events))
    ]
    return TrajectoryGraph(
        graph_id=graph_id,
        nodes=nodes,
        edges=edges,
        metadata={"session_id": session_id, **dict(metadata or {})},
    )


def validate_graph_invariants(graph: TrajectoryGraph) -> list[str]:
    errors: list[str] = []
    node_ids = [node.node_id for node in graph.nodes]
    node_id_set = set(node_ids)
    if len(node_ids) != len(node_id_set):
        errors.append("node_id values must be unique")
    for edge in graph.edges:
        if edge.source_id not in node_id_set:
            errors.append(f"edge {edge.edge_id} references missing source_id")
        if edge.target_id not in node_id_set:
            errors.append(f"edge {edge.edge_id} references missing target_id")
    if graph.edges and len(graph.edges) != max(0, len(graph.nodes) - 1):
        errors.append("session_order graph must have node_count - 1 edges")
    if graph.nodes and graph.nodes[0].node_kind != "reset":
        errors.append("session graph must start with reset")
    return errors
