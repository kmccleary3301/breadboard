from __future__ import annotations

import pytest

from breadboard.search_loop_v1 import SearchLoop, SearchLoopSpec


def _spec(determinism: str = "replayable") -> SearchLoopSpec:
    return SearchLoopSpec(
        loop_id="loop-atp-toy",
        objective="prove toy theorem",
        budget={"max_iters": 4, "max_time_s": 60},
        policy_id="policy.toy.v1",
        determinism=determinism,
        run_id="run-1",
        session_id="session-1",
        turn_id="turn-1",
    )


def test_search_loop_replayable_ids_and_events() -> None:
    loop = SearchLoop(spec=_spec("replayable"))
    root = loop.start(state_storage_locator="artifacts/state_root.json")
    child = loop.expand(
        parent_id=root,
        state_storage_locator="artifacts/state_child.json",
        score={"primary": 0.9},
    )
    loop.close(node_id=child, status="closed", reason="verified")

    events = loop.event_log()
    assert root == "n000001"
    assert child == "n000002"
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert [event["type"] for event in events] == [
        "search_loop.started",
        "search_loop.node_expanded",
        "search_loop.node_closed",
    ]


def test_search_loop_lineage_artifact_contains_nodes_edges() -> None:
    loop = SearchLoop(spec=_spec("replayable"))
    root = loop.start(state_storage_locator="artifacts/root.json")
    child_a = loop.expand(parent_id=root, state_storage_locator="artifacts/a.json")
    child_b = loop.expand(parent_id=root, state_storage_locator="artifacts/b.json")
    loop.close(node_id=child_a, status="failed", reason="timeout")

    lineage = loop.lineage_artifact()
    node_ids = {node["node_id"] for node in lineage["nodes"]}
    edges = {(edge["parent_id"], edge["child_id"]) for edge in lineage["edges"]}

    assert lineage["schema_id"] == "breadboard.search_loop.lineage.v1"
    assert node_ids == {root, child_a, child_b}
    assert (root, child_a) in edges
    assert (root, child_b) in edges


def test_search_loop_from_event_log_rehydrates_state() -> None:
    loop = SearchLoop(spec=_spec("replayable"))
    root = loop.start(state_storage_locator="artifacts/root.json")
    child = loop.expand(parent_id=root, state_storage_locator="artifacts/child.json")
    loop.close(node_id=child, status="closed", reason="done")

    replay = SearchLoop.from_event_log(spec=_spec("replayable"), event_log=loop.event_log())
    lineage = replay.lineage_artifact()
    by_id = {node["node_id"]: node for node in lineage["nodes"]}
    assert by_id[root]["status"] == "open"
    assert by_id[child]["status"] == "closed"
    assert lineage["event_count"] == 3


def test_search_loop_exploratory_ids_are_non_sequential() -> None:
    loop = SearchLoop(spec=_spec("exploratory"))
    root = loop.start(state_storage_locator="artifacts/root.json")
    child = loop.expand(parent_id=root, state_storage_locator="artifacts/child.json")

    assert root.startswith("n")
    assert child.startswith("n")
    assert root != child
    assert root != "n000001"


def test_search_loop_node_type_round_trips_into_replay() -> None:
    loop = SearchLoop(spec=_spec("replayable"))
    root = loop.start(state_storage_locator="artifacts/root.json")
    lemma = loop.expand(
        parent_id=root,
        state_storage_locator="artifacts/lemma.json",
        node_type="lemma",
    )
    repair = loop.expand(
        parent_id=lemma,
        state_storage_locator="artifacts/repair.json",
        node_type="repair",
    )
    loop.close(node_id=repair, status="verified", reason="proof accepted")

    replay = SearchLoop.from_event_log(spec=_spec("replayable"), event_log=loop.event_log())
    lineage = replay.lineage_artifact()
    by_id = {node["node_id"]: node for node in lineage["nodes"]}

    assert by_id[root]["node_type"] == "root"
    assert by_id[lemma]["node_type"] == "lemma"
    assert by_id[repair]["node_type"] == "repair"
    assert by_id[repair]["status"] == "verified"


def test_search_loop_rejects_unknown_node_type() -> None:
    loop = SearchLoop(spec=_spec("replayable"))
    root = loop.start(state_storage_locator="artifacts/root.json")
    try:
        loop.expand(
            parent_id=root,
            state_storage_locator="artifacts/child.json",
            node_type="unknown",
        )
    except ValueError as exc:
        assert "node_type must be" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid node_type")


def test_search_loop_rejects_invalid_close_status() -> None:
    loop = SearchLoop(spec=_spec("replayable"))
    root = loop.start(state_storage_locator="artifacts/root.json")
    child = loop.expand(parent_id=root, state_storage_locator="artifacts/child.json")
    with pytest.raises(ValueError, match="status must be closed, failed, or verified"):
        loop.close(node_id=child, status="open")


def test_search_loop_replay_rejects_invalid_node_type() -> None:
    loop = SearchLoop(spec=_spec("replayable"))
    root = loop.start(state_storage_locator="artifacts/root.json")
    child = loop.expand(parent_id=root, state_storage_locator="artifacts/child.json")
    events = loop.event_log()
    events[1]["payload"]["node"]["node_type"] = "invalid_type"
    with pytest.raises(ValueError, match="invalid node_type in replay event"):
        SearchLoop.from_event_log(spec=_spec("replayable"), event_log=events)
