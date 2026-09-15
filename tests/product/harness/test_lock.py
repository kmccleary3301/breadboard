from __future__ import annotations

import pytest

from breadboard.product.harness.compile import compile_harness_definition
from breadboard.product.harness.lock import (
    EffectiveHarnessLock,
    graph_content_hash,
    lock_content_hash,
)


def test_lock_is_frozen_detached_and_self_hashing() -> None:
    compiled = compile_harness_definition(
        {"nested": {"value": 1}, "items": ["a"]}, source_ref="/cfg/root.yaml"
    )
    lock = compiled.lock
    assert set(lock) == {
        "schema_version",
        "lock_id",
        "configuration_graph",
        "configuration_artifacts",
        "modules",
    }
    assert lock["schema_version"] == "bb.effective_harness_lock.v2"
    assert lock_content_hash(lock) == lock["lock_id"] == lock.generation_id
    assert (
        graph_content_hash(lock.configuration_graph)
        == lock.configuration_graph["graph_hash"]
        == lock.configuration_graph_hash
    )
    with pytest.raises(ValueError):
        graph_content_hash(lock)
    with pytest.raises(TypeError):
        lock["configuration_graph"]["visibility"]["model_visible_paths"] = []  # type: ignore[index]
    with pytest.raises(AttributeError):
        lock["configuration_graph"]["effective_values"].append({})  # type: ignore[union-attr]
    detached = lock.as_dict()
    detached["configuration_graph"]["effective_values"][0]["value"] = "changed"
    assert detached != lock.as_dict()


def test_v1_graph_history_remains_readable_without_execution_identity() -> None:
    graph = compile_harness_definition(
        {"nested": {"value": 1}, "items": ["a"]}, source_ref="/cfg/root.yaml"
    ).lock.configuration_graph
    history = EffectiveHarnessLock._from_record(graph)
    assert history["schema_version"] == "bb.effective_config_graph.v1"
    assert history.configuration_graph == graph
    assert history.generation_id == graph["graph_hash"]


def test_one_field_mutation_changes_local_value_and_complete_lock_identity() -> None:
    first = compile_harness_definition(
        {"a": 1, "b": 2}, source_ref="/cfg/root.yaml"
    ).lock.as_dict()
    second = compile_harness_definition(
        {"a": 9, "b": 2}, source_ref="/cfg/root.yaml"
    ).lock.as_dict()
    first_graph = first["configuration_graph"]
    second_graph = second["configuration_graph"]
    assert first_graph["effective_values"][1] == second_graph["effective_values"][1]
    assert first_graph["effective_values"][0]["value"] != second_graph["effective_values"][0]["value"]
    assert first_graph["source_layers"][0]["layer_hash"] != second_graph["source_layers"][0]["layer_hash"]
    assert first_graph["graph_hash"] != second_graph["graph_hash"]
    assert first["lock_id"] != second["lock_id"]
    assert lock_content_hash(first) == first["lock_id"]
    assert lock_content_hash(second) == second["lock_id"]
