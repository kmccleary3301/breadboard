from __future__ import annotations

from breadboard.rl.trace import build_graph_from_session_events, build_terminal_credit_frame
from tests.rl.session.helpers import build_successful_toy_session


def test_terminal_reward_credit_frame_targets_step_and_evaluate_nodes() -> None:
    session = build_successful_toy_session()
    graph = build_graph_from_session_events(
        graph_id="toy.graph",
        session_id=session.session_id,
        events=session.events,
    )

    frame = build_terminal_credit_frame(graph, reward=1.0)

    assert frame.reward == 1.0
    assert any(node_id.endswith(".event.3") for node_id in frame.credited_node_ids)
    assert any(node_id.endswith(".event.4") for node_id in frame.credited_node_ids)
