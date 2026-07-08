from __future__ import annotations

from breadboard.rl.export import build_projection_manifest
from breadboard.rl.trace import build_graph_from_session_events
from tests.rl.session.helpers import build_successful_toy_session


def test_projection_manifest_records_preserved_and_lost_fields() -> None:
    session = build_successful_toy_session()
    graph = build_graph_from_session_events(
        graph_id="toy.graph",
        session_id=session.session_id,
        events=session.events,
    )

    manifest = build_projection_manifest(
        graph=graph,
        target_format="jsonl_transition_probe",
        preserved_fields=["node_kind", "reward", "event_id"],
        lost_fields=["full_runtime_state"],
        included_node_kinds={"step", "evaluate"},
    )

    assert manifest.source_graph_id == graph.graph_id
    assert "full_runtime_state" in manifest.lost_fields
    assert len(manifest.included_node_ids) == 2
    assert len(manifest.excluded_node_ids) == 2
    assert manifest.metadata["canonical_truth"] == "breadboard_graph_replay_runtime"
