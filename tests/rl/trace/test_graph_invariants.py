from __future__ import annotations

from breadboard.rl.trace import build_graph_from_session_events, validate_graph_invariants
from breadboard.rl.trace.edges import TraceEdge
from breadboard.rl.trace.graph import TrajectoryGraph
from breadboard.rl.trace.nodes import TraceNode
from tests.rl.session.helpers import build_successful_toy_session


def test_toy_session_emits_valid_trajectory_graph() -> None:
    session = build_successful_toy_session()
    graph = build_graph_from_session_events(
        graph_id="toy.graph",
        session_id=session.session_id,
        events=session.events,
    )

    assert validate_graph_invariants(graph) == []
    assert [node.node_kind for node in graph.nodes] == ["reset", "observe", "step", "evaluate"]
    assert len(graph.edges) == 3


def test_graph_invariants_reject_missing_edge_target() -> None:
    graph = TrajectoryGraph(
        graph_id="bad.graph",
        nodes=[TraceNode(node_id="n1", node_kind="reset")],
        edges=[TraceEdge(edge_id="e1", source_id="n1", target_id="missing", edge_kind="session_order")],
    )

    assert "edge e1 references missing target_id" in validate_graph_invariants(graph)
