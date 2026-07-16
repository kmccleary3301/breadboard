from pathlib import Path

import pytest
import yaml

from breadboard.product.harness.compile import compile_harness_definition
from breadboard.product.harness.modules.extensions import (
    CompositionError,
    LocalExtensionRegistry,
    Operation,
    compose_modules,
    contribution,
)
from breadboard.product.harness.modules.policies import build_policy_module
from breadboard.product.harness.modules.longrun import build_longrun_module
from breadboard.product.harness.modules.providers import build_provider_module
from breadboard.product.harness.modules.tools import build_tool_module
from breadboard.product.harness.validate import load_harness_definition

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "product" / "harness"


def _load(name: str) -> dict[str, object]:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def test_representative_fixtures_share_the_frozen_primitive_language() -> None:
    paths = [
        ROOT / "agent_configs" / "templates" / "representative_harness.v1.yaml",
        FIXTURES / "research.v1.yaml",
        FIXTURES / "engineering.v1.yaml",
    ]
    documents = [load_harness_definition(path).as_dict() for path in paths]
    forbidden = {"resources", "host", "policies", "extensions"}
    assert all(not forbidden.intersection(document) for document in documents)
    assert all(
        {"workspace", "providers", "modes", "loop"} <= set(document)
        for document in documents
    )
    legacy = {"schema_version": "bb.agent_config_surface.v2", "version": 2}
    with pytest.raises(CompositionError, match="bb.harness_definition.v1"):
        compose_modules(legacy, [])


def test_operations_compose_by_explicit_precedence_and_detach_inputs() -> None:
    base = _load("engineering.v1.yaml")
    aliases = {"read": "read_file"}
    path = ("providers", "stream_responses")
    with pytest.raises(CompositionError, match="repeat a kind and path"):
        contribution(
            "duplicate",
            30,
            [Operation("replace", path, False), Operation("replace", path, True)],
        )
    seed = contribution("seed", 5, [Operation("add", path, False)])
    provider = build_provider_module([Operation("replace", path, True)], 10)
    tools = build_tool_module([Operation("add", ("tools", "aliases"), aliases)], 20)
    composed = compose_modules(base, [tools, provider, seed])
    aliases["read"] = "changed"
    assert composed["providers"]["stream_responses"] is True
    assert composed["tools"]["aliases"] == {"read": "read_file"}
    assert "stream_responses" not in base["providers"]
    ordered = build_tool_module(
        [
            Operation("add", ("tools", "aliases", "write"), "write_file"),
            Operation("replace", ("tools", "mark_task_complete"), False),
            Operation("remove", ("tools", "aliases", "read")),
        ]
    )
    result = compose_modules(composed, [ordered])
    assert result["tools"]["aliases"] == {"write": "write_file"}
    assert result["tools"]["mark_task_complete"] is False


def test_one_module_mutation_changes_only_its_lock_subgraph() -> None:
    base = _load("engineering.v1.yaml")
    changed = compose_modules(
        base,
        [
            build_provider_module(
                [Operation("add", ("providers", "stream_responses"), True)]
            )
        ],
    )
    left = compile_harness_definition(
        base, source_ref="engineering.yaml"
    ).lock.as_dict()
    right = compile_harness_definition(
        changed, source_ref="engineering.yaml"
    ).lock.as_dict()
    left_rows = {row["path"]: row for row in left["effective_values"]}
    right_rows = {row["path"]: row for row in right["effective_values"]}
    assert set(right_rows) - set(left_rows) == {"providers.stream_responses"}
    assert all(left_rows[path] == right_rows[path] for path in left_rows)
    assert (
        left["source_layers"][0]["layer_hash"]
        != right["source_layers"][0]["layer_hash"]
    )
    assert left["graph_hash"] != right["graph_hash"]


def test_local_extensions_fail_closed_without_a_core_fork() -> None:
    base = _load("research.v1.yaml")
    registry = LocalExtensionRegistry()
    registry.register(
        "local-confidence",
        lambda config: build_longrun_module(
            [
                Operation(
                    "replace",
                    ("completion", "confidence_threshold"),
                    config["value"],
                )
            ]
        ),
    )
    extension = registry.resolve("local-confidence", {"value": 0.9}, precedence=70)
    assert (
        compose_modules(base, [extension])["completion"]["confidence_threshold"] == 0.9
    )
    with pytest.raises(CompositionError, match="unknown extension id"):
        registry.resolve("missing", {})
    registry.register("bare", lambda _: [])
    with pytest.raises(CompositionError, match="owned contribution"):
        registry.resolve("bare", {})


def test_module_invariants_fail_before_lock() -> None:
    base = _load("engineering.v1.yaml")
    bad_model = Operation("replace", ("providers", "default_model"), "x")
    with pytest.raises(CompositionError, match="declared model"):
        compose_modules(base, [build_provider_module([bad_model])])
    bad_resume = Operation("add", ("long_running", "resume"), {"enabled": True})
    with pytest.raises(CompositionError, match="state_path"):
        compose_modules(base, [build_longrun_module([bad_resume])])
    budget = Operation("replace", ("long_running", "budgets"), {"total_tokens": 1})
    bounded = compose_modules(base, [build_longrun_module([budget])])
    assert bounded["long_running"]["budgets"] == {"total_tokens": 1}


def test_composition_preconditions_and_module_ownership_fail_closed() -> None:
    base = _load("engineering.v1.yaml")
    with pytest.raises(CompositionError, match="precedence"):
        compose_modules(base, [contribution("a", 1, []), contribution("b", 1, [])])
    with pytest.raises(CompositionError, match="protected root"):
        compose_modules(
            base,
            [contribution("protected", 1, [Operation("replace", ("version",), 1)])],
        )
    with pytest.raises(CompositionError, match="not canonical"):
        compose_modules(
            base,
            [contribution("unknown", 1, [Operation("add", ("extensions",), {})])],
        )
    with pytest.raises(CompositionError, match="already exists"):
        compose_modules(
            base,
            [build_tool_module([Operation("add", ("tools", "registry"), {})])],
        )
    with pytest.raises(CompositionError, match="does not exist"):
        compose_modules(
            base,
            [build_tool_module([Operation("replace", ("tools", "aliases"), {})])],
        )
    with pytest.raises(CompositionError, match="unowned root"):
        build_policy_module([Operation("replace", ("workspace",), {})])
