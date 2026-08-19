from dataclasses import replace
from pathlib import Path
from typing import get_type_hints

import pytest
import yaml

from breadboard.product.harness.compile import compile_harness_definition
from breadboard.product.harness.modules.extensions import (
    CompositionError,
    LocalExtensionRegistry,
    Operation,
    compose_modules,
    ModuleContribution,
)
from breadboard.product.harness.modules.host import build_host_module
from breadboard.product.harness.modules.resources import build_resource_module
from breadboard.product.harness.modules.policies import build_policy_module
from breadboard.product.harness.modules.longrun import build_longrun_module
from breadboard.product.harness.modules.providers import build_provider_module
from breadboard.product.harness.modules.tools import build_tool_module
from breadboard.product.harness.validate import load_harness_definition

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "product" / "harness"
_FOREIGN = Operation("replace", ("workspace", "root"), "elsewhere")


def _load(name: str) -> dict[str, object]:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def test_representative_fixtures_share_the_frozen_primitive_language() -> None:
    paths = [FIXTURES / name for name in ("research.v1.yaml", "engineering.v1.yaml")]
    paths.append(ROOT / "agent_configs/templates/representative_harness.v1.yaml")
    documents = [load_harness_definition(path).as_dict() for path in paths]
    for document in documents:
        assert not {"resources", "host", "policies", "extensions"} & document.keys()
        assert {"workspace", "providers", "modes", "loop"} <= document.keys()
    for field, value in (
        ("schema_version", "bb.agent_config_surface.v2"),
        ("version", True),
        ("version", 1.0),
    ):
        invalid = _load("engineering.v1.yaml")
        invalid[field] = value
        invalid["providers"]["default_model"] = "missing"
        with pytest.raises(CompositionError, match="bb.harness_definition.v1"):
            compose_modules(invalid, [build_provider_module([])])


def test_operations_compose_by_explicit_precedence_and_detach_inputs() -> None:
    base = _load("engineering.v1.yaml")
    aliases = {"read": "read_file"}
    path = ("providers", "stream_responses")
    duplicate = Operation("replace", path, True)
    with pytest.raises(CompositionError, match="repeat a kind and path"):
        ModuleContribution("duplicate", 30, [duplicate, duplicate])
    with pytest.raises(CompositionError, match="unknown operation kind"):
        Operation(type("Deceptive", (), {"__eq__": lambda *_: True})(), path, True)
    with pytest.raises(CompositionError, match="sequence"):
        build_provider_module(iter([duplicate]))
    with pytest.raises(CompositionError, match="list or tuple"):
        compose_modules(base, iter([build_provider_module([])]))
    seed = build_provider_module([Operation("add", path, False)], 5)
    provider = build_provider_module([Operation("replace", path, True)], 10)
    alias_op = Operation("add", ("tools", "aliases"), aliases)
    tools = build_tool_module([alias_op], 20)
    composed = compose_modules(base, [tools, provider, seed])
    aliases["read"] = "changed"
    assert composed["providers"]["stream_responses"] is True
    object.__setattr__(alias_op, "kind", "remove")
    for _ in range(2):
        assert compose_modules(base, [tools])["tools"] == composed["tools"]
    object.__setattr__(tools.operations[0], "path", ["tools", "aliases"])
    deceptive = type("Seal", (), {"__ne__": lambda *_: False})()
    object.__setattr__(tools, "_sealed", deceptive)
    with pytest.raises(CompositionError, match="mutated after construction"):
        compose_modules(base, [tools])
    object.__setattr__(provider.operations[0], "value", 1)
    with pytest.raises(CompositionError, match="mutated after construction"):
        compose_modules(base, [provider])
    remove = Operation("remove", ("tools", "mark_task_complete"))
    removed = compose_modules(base, [build_tool_module([remove])])
    assert "mark_task_complete" not in removed["tools"]


def test_one_module_mutation_changes_only_its_lock_subgraph() -> None:
    base = _load("engineering.v1.yaml")
    module = build_provider_module(
        [Operation("add", ("providers", "stream_responses"), True)]
    )
    changed = compose_modules(base, [module])
    left = compile_harness_definition(base, source_ref="x").lock.as_dict()
    right = compile_harness_definition(changed, source_ref="x").lock.as_dict()
    left_rows = {row["path"]: row for row in left["effective_values"]}
    right_rows = {row["path"]: row for row in right["effective_values"]}
    assert set(right_rows) - set(left_rows) == {"providers.stream_responses"}
    assert all(left_rows[path] == right_rows[path] for path in left_rows)
    assert left["graph_hash"] != right["graph_hash"]


def test_local_extensions_fail_closed_without_a_core_fork() -> None:
    base = _load("research.v1.yaml")
    registry = LocalExtensionRegistry()
    confidence = Operation("replace", ("completion", "confidence_threshold"), 0.9)
    registry.register("local-confidence", lambda _: build_longrun_module([confidence]))
    extension = registry.resolve("local-confidence", {"value": 0.9}, precedence=70)
    document = compose_modules(base, [extension])
    assert document["completion"]["confidence_threshold"] == 0.9
    with pytest.raises(CompositionError, match="unknown extension id"):
        registry.resolve("missing", {})
    uninitialized = object.__new__(type(build_provider_module([])))
    registry.register("uninitialized", lambda _: uninitialized)
    with pytest.raises(CompositionError):
        registry.resolve("uninitialized", {})


def test_module_invariants_fail_before_lock() -> None:
    base = _load("engineering.v1.yaml")

    def reject(modules: object, match: str | None = None, document=base) -> None:
        with pytest.raises(CompositionError, match=match):
            compose_modules(document, modules)

    base["tools"]["aliases"] = None
    base["concurrency"]["at_most_one_of"] = []
    compose_modules(base, [])
    cases = (
        ("permissions/shell", "{default: allow, deny: [{rm: null}]}"),
        ("permissions/shell/deny", "[' rm ']"),
        ("permissions/edit/default", "ALLOW"),
        ("concurrency/groups", "[{name: a, match_tools: [x, y]}]"),
        ("concurrency/groups", "[{name: a, match_tools: [todowrite, TodoWrite]}]"),
        ("concurrency/groups", "[{name: a, match_tools: [' x ']}]"),
        ("concurrency/groups", "[{name: a, match_tools: [x], barrier_after: y}]"),
        ("long_running/recovery", "{backoff_base_seconds: 3}"),
        ("long_running/budgets", "{total_episodes: 1}"),
        ("long_running/verification", "{tiers: [{name: tier_1}, {}]}"),
        ("tools/dialects", "{selection: {by_model: {'*': bash_block}}}"),
        ("tools/dialects", "{preference: {default: [bash_block]}}"),
        ("tools/dialects", "{selection: {by_model: {' * ': [bash_block]}}}"),
        ("tools/aliases", "{read_file: read}"),
        ("tools/aliases", "{' a ': b}"),
    )
    for path, value in cases:
        document = _load("engineering.v1.yaml")
        document["tools"]["aliases"] = {"x": "y"}
        keys = path.split("/")
        parent = document
        for key in keys[:-1]:
            parent = parent.setdefault(key, {})
        parent[keys[-1]] = yaml.safe_load(value)
        reject([], document=document)
    with pytest.raises(CompositionError, match="precedence"):
        compose_modules(base, [build_provider_module([], 1), build_tool_module([], 1)])
    forged = type("ForgedOperation", (Operation,), {"__post_init__": lambda self: None})
    with pytest.raises(CompositionError, match="exact Operation"):
        build_provider_module([forged("move", ("providers", "stream_responses"), True)])
    with pytest.raises(CompositionError, match="uninitialized"):
        build_provider_module([object.__new__(Operation)])
    typed = build_provider_module([])
    with pytest.raises(CompositionError, match="internal capability"):
        replace(typed, operations=(_FOREIGN,), validator=lambda _: None)
    owners = (
        (build_provider_module, "providers provider_tools"),
        (build_tool_module, "tools enhanced_tools"),
        (build_resource_module, "concurrency"),
        (build_policy_module, "permissions guardrails replay"),
        (build_host_module, "workspace"),
        (build_longrun_module, "long_running turn_strategy completion"),
    )
    roots = set(
        "completion concurrency dossier enhanced_tools features guardrails long_running loop modes multi_agent permissions prompts provider_tools providers replay schema_version tools turn_strategy version workspace".split()
    )
    assert get_type_hints(compose_modules)["modules"] == (
        list[ModuleContribution] | tuple[ModuleContribution, ...]
    )
    for builder, names in owners:
        assert get_type_hints(builder)["return"] is ModuleContribution
        for root in roots:
            operation = Operation("add", (root, "probe"), None)
            if root in names.split():
                builder([operation])
            else:
                with pytest.raises(CompositionError, match="unowned root"):
                    builder([operation])
