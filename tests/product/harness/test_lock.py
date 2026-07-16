from __future__ import annotations

import pytest

from breadboard.product.harness.compile import compile_harness_definition
from breadboard.product.harness.lock import graph_content_hash


def test_lock_is_frozen_detached_and_self_hashing() -> None:
    compiled = compile_harness_definition(
        {"nested": {"value": 1}, "items": ["a"]}, source_ref="/cfg/root.yaml")
    lock = compiled.lock
    assert lock["schema_version"] == "bb.effective_config_graph.v1"
    assert graph_content_hash(lock) == lock["graph_hash"]
    with pytest.raises(TypeError):
        lock["visibility"]["model_visible_paths"] = []  # type: ignore[index]
    with pytest.raises(AttributeError):
        lock["effective_values"].append({})  # type: ignore[union-attr]
    detached = lock.as_dict()
    detached["effective_values"][0]["value"] = "changed"
    assert detached != lock.as_dict()


def test_one_field_mutation_is_local_to_value_and_content_hashes() -> None:
    first = compile_harness_definition(
        {"a": 1, "b": 2}, source_ref="/cfg/root.yaml").lock.as_dict()
    second = compile_harness_definition(
        {"a": 9, "b": 2}, source_ref="/cfg/root.yaml").lock.as_dict()
    assert first["effective_values"][1] == second["effective_values"][1]
    left, right = first["effective_values"][0], second["effective_values"][0]
    assert {key: value for key, value in left.items() if key != "value"} == {
        key: value for key, value in right.items() if key != "value"}
    assert first["source_layers"][0]["layer_hash"] != second["source_layers"][0]["layer_hash"]
    assert first["graph_hash"] != second["graph_hash"]
