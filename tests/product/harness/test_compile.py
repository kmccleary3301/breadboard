from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from agentic_coder_prototype.compilation.v2_loader import load_agent_config_view

from breadboard.product.harness.compile import (
    HarnessCompileError,
    compile_harness_definition,
)

ROOT = Path(__file__).resolve().parents[3]

def _schema_errors(schema_name: str, record: dict[str, Any]) -> list[str]:
    directories = (ROOT / "contracts/public/schemas", ROOT / "contracts/kernel/schemas")
    schemas: dict[str, dict[str, Any]] = {}
    registry = Registry()
    for directory in directories:
        for path in directory.glob("*.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            schemas[path.name] = schema
            if isinstance(schema.get("$id"), str):
                registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return [error.message for error in
            Draft202012Validator(schemas[schema_name], registry=registry).iter_errors(record)]

def test_recursive_merge_is_deterministic_and_keeps_author_dossier() -> None:
    documents = {
        ("root", "base"): ("base", {"extends": "deep", "nested": {"left": 1},
                                    "items": [1], "dossier": {"owner": "base"}}),
        ("base", "deep"): ("deep", {"nested": {"deep": True}, "items": [0]}),
    }
    load = lambda parent, declared: documents[(parent, declared)]
    source = {"extends": "base", "nested": {"right": 2}, "items": [2],
              "dossier": {"owner": "root"}}
    first = compile_harness_definition(source, source_ref="root", load_ref=load)
    second = compile_harness_definition(
        dict(reversed(tuple(source.items()))), source_ref="root", load_ref=load)
    assert first.lock.canonical_json() == second.lock.canonical_json()
    assert first.lock["graph_hash"] == second.lock["graph_hash"]
    assert [layer["layer_id"] for layer in first.lock["source_layers"]] == [
        "agent-config:0000:deep", "agent-config:0001:base", "agent-config:0002:root"]
    assert first.as_dict() == {"items": [2],
                               "nested": {"deep": True, "left": 1, "right": 2}}
    assert first.resolved_author_dict()["dossier"] == {"owner": "root"}
    assert "extends" not in first.resolved_author_dict()

@pytest.mark.parametrize("extends", ["", 3, [None]])
def test_invalid_references_are_rejected(extends: Any) -> None:
    with pytest.raises(HarnessCompileError, match="invalid reference"):
        compile_harness_definition({"extends": extends}, source_ref="root")

def test_missing_cyclic_and_inconsistent_references_are_rejected() -> None:
    with pytest.raises(HarnessCompileError, match="missing reference"):
        compile_harness_definition(
            {"extends": "missing"}, source_ref="root",
            load_ref=lambda _parent, _ref: (_ for _ in ()).throw(KeyError()))
    cyclic = {"root": {"extends": "base"}, "base": {"extends": "root"}}
    with pytest.raises(HarnessCompileError, match="cyclic reference"):
        compile_harness_definition(
            cyclic["root"], source_ref="root",
            load_ref=lambda _parent, ref: (ref, cyclic[ref]))
    with pytest.raises(HarnessCompileError, match="inconsistent content"):
        compile_harness_definition(
            {"extends": ["a", "b"]}, source_ref="root",
            load_ref=lambda _parent, ref: ("same", {"selected": ref}))

def test_lock_and_explanation_match_public_projection_schemas() -> None:
    compiled = compile_harness_definition({
        "schema_version": "bb.harness_definition.v1", "version": 1,
        "workspace": {"root": "."},
        "providers": {"default_model": "mock/reference",
                      "models": [{"id": "mock/reference", "adapter": "mock_chat"}]},
        "modes": [{"name": "respond"}], "loop": {"sequence": [{"mode": "respond"}]},
    }, source_ref="/config/harness.yaml")
    assert _schema_errors(
        "bb.effective_harness_lock.v1.schema.json", compiled.lock.as_dict()) == []
    assert _schema_errors(
        "bb.harness_explanation_report.v1.schema.json",
        compiled.explanation.as_dict()) == []

def test_invalid_and_empty_definitions_fail_before_lock() -> None:
    with pytest.raises(HarnessCompileError, match="invalid Harness Definition"):
        compile_harness_definition(
            {"schema_version": "bb.harness_definition.v1", "version": 1},
            source_ref="root")
    with pytest.raises(HarnessCompileError, match="must contain at least one value"):
        compile_harness_definition({}, source_ref="root")

def test_empty_mapping_remains_an_explained_lock_value() -> None:
    scalar = compile_harness_definition({"tools": 1}, source_ref="root")
    empty = compile_harness_definition({"tools": {}}, source_ref="root")
    row = empty.lock["effective_values"][0]
    assert empty.as_dict() == {"tools": {}}
    assert (row["path"], row["value"], row["value_kind"]) == ("tools", {}, "object")
    assert empty.explanation["fields"][0]["path"] == "tools"
    assert scalar.lock["graph_hash"] != empty.lock["graph_hash"]

def test_legacy_loader_adapter_accepts_canonical_harness_definition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "harness.yaml"
    path.write_text(
        "schema_version: bb.harness_definition.v1\nversion: 1\n"
        "workspace: {root: .}\n"
        "providers: {default_model: mock/reference, models: [{id: mock/reference, adapter: mock_chat}]}\n"
        "modes: [{name: respond}]\nloop: {sequence: [{mode: respond}]}\n",
        encoding="utf-8")
    view = load_agent_config_view(str(path))
    assert view.as_dict()["providers"]["default_model"] == "mock/reference"
    assert view.graph["schema_version"] == "bb.effective_config_graph.v1"
    path.write_text(
        "providers: {api_key: raw-secret}\ncredential:\n"
        "  value: raw-env\n  env_name: TOKEN\n  env_satisfied: true\n",
        encoding="utf-8")
    secret_view = load_agent_config_view(str(path))
    assert "raw-secret" not in json.dumps(secret_view.graph)
    assert secret_view.graph["visibility"]["redacted_paths"] == [
        "credential", "providers.api_key"]
    assert secret_view.graph["env_gates"][0]["gate_id"] == "env.TOKEN"
    assert _schema_errors("bb.effective_harness_lock.v1.schema.json", secret_view.graph) == []
