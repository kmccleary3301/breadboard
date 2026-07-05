from __future__ import annotations

from dataclasses import replace

from breadboard.rl.replay import compare_replay_parity
from breadboard.rl.trace import build_graph_from_session_events
from breadboard.rl.trace.graph import TrajectoryGraph
from tests.rl.session.helpers import build_successful_toy_session


def test_identical_live_and_replay_graphs_pass_t2_parity() -> None:
    session = build_successful_toy_session()
    live = build_graph_from_session_events(
        graph_id="toy.live",
        session_id=session.session_id,
        events=session.events,
    )
    replay = TrajectoryGraph(graph_id="toy.replay", nodes=live.nodes, edges=live.edges)

    report = compare_replay_parity(live, replay)

    assert report.passed is True
    assert report.parity_tier == "T2_deterministic_runtime_verifier"


def test_reward_mismatch_fails_replay_parity() -> None:
    session = build_successful_toy_session()
    live = build_graph_from_session_events(
        graph_id="toy.live",
        session_id=session.session_id,
        events=session.events,
    )
    mutated_nodes = list(live.nodes)
    eval_node = mutated_nodes[-1]
    payload = eval_node.payload.copy()
    nested_payload = payload["payload"].copy()
    nested_payload["reward"] = 0.0
    payload["payload"] = nested_payload
    mutated_nodes[-1] = replace(eval_node, payload=payload)
    replay = TrajectoryGraph(graph_id="toy.replay", nodes=mutated_nodes, edges=live.edges)

    report = compare_replay_parity(live, replay)

    assert report.passed is False
    assert "reward_mismatch" in report.mismatches
