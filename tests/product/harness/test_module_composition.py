from dataclasses import replace
from pathlib import Path

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
    paths = [
        ROOT / "agent_configs" / "templates" / "representative_harness.v1.yaml",
        FIXTURES / "research.v1.yaml",
        FIXTURES / "engineering.v1.yaml",
    ]
    documents = [load_harness_definition(path).as_dict() for path in paths]
    forbidden = {"resources", "host", "policies", "extensions"}
    assert all(not forbidden.intersection(document) for document in documents)
    required = {"workspace", "providers", "modes", "loop"}
    assert all(required <= set(document) for document in documents)
    called = []
    probe = ModuleContribution("side-effect", 1, [], lambda _: called.append(True))
    for field, value in (
        ("schema_version", "bb.agent_config_surface.v2"),
        ("version", True),
        ("version", 1.0),
    ):
        invalid = _load("engineering.v1.yaml")
        invalid[field] = value
        with pytest.raises(CompositionError, match="bb.harness_definition.v1"):
            compose_modules(invalid, [probe])
    assert not called


def test_operations_compose_by_explicit_precedence_and_detach_inputs() -> None:
    base = _load("engineering.v1.yaml")
    aliases = {"read": "read_file"}
    path = ("providers", "stream_responses")
    duplicate = Operation("replace", path, True)
    with pytest.raises(CompositionError, match="repeat a kind and path"):
        ModuleContribution("duplicate", 30, [duplicate, duplicate])
    seed = build_provider_module([Operation("add", path, False)], 5)
    provider = build_provider_module([Operation("replace", path, True)], 10)
    tools = build_tool_module([Operation("add", ("tools", "aliases"), aliases)], 20)
    composed = compose_modules(base, [tools, provider, seed])
    aliases["read"] = "changed"
    assert composed["providers"]["stream_responses"] is True
    assert composed["tools"]["aliases"] == {"read": "read_file"}
    assert "stream_responses" not in base["providers"]
    dependent = build_tool_module(
        [
            Operation("add", ("tools", "aliases"), {"read": "pending"}),
            Operation("replace", ("tools", "aliases", "read"), "read_file"),
            Operation("remove", ("tools", "aliases", "read")),
        ]
    )
    assert compose_modules(base, [dependent])["tools"]["aliases"] == {}


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
    confidence_path = ("completion", "confidence_threshold")
    registry.register(
        "local-confidence",
        lambda config: build_longrun_module(
            [Operation("replace", confidence_path, config["value"])]
        ),
    )
    extension = registry.resolve("local-confidence", {"value": 0.9}, precedence=70)
    document = compose_modules(base, [extension])
    assert document["completion"]["confidence_threshold"] == 0.9
    with pytest.raises(CompositionError, match="unknown extension id"):
        registry.resolve("missing", {})
    registry.register("bare", lambda _: ModuleContribution("bare", 1, []))
    with pytest.raises(CompositionError, match="owned contribution"):
        registry.resolve("bare", {})

    def forge(_: object) -> ModuleContribution:
        return replace(build_provider_module([]), operations=(_FOREIGN,))

    registry.register("forged", forge)
    with pytest.raises(CompositionError, match="builder 'forged' failed"):
        registry.resolve("forged", {})


def test_module_invariants_fail_before_lock() -> None:
    base = _load("engineering.v1.yaml")
    bad_model = Operation("replace", ("providers", "default_model"), "x")
    with pytest.raises(CompositionError, match="declared model"):
        compose_modules(base, [build_provider_module([bad_model])])
    bad_resume = Operation("add", ("long_running", "resume"), {"enabled": True})
    with pytest.raises(CompositionError, match="state_path"):
        compose_modules(base, [build_longrun_module([bad_resume])])
    budget = Operation("replace", ("long_running", "budgets"), {"total_tokens": 1})
    compose_modules(base, [build_longrun_module([budget])])
    for name in ("wall_clock_s", "total_episodes"):
        unsupported = Operation("replace", ("long_running", "budgets"), {name: 1})
        with pytest.raises(CompositionError, match="positive stopping budget"):
            compose_modules(base, [build_longrun_module([unsupported])])
    cases = (
        (build_host_module, ("workspace", "mirror"), {"enabled": True}),
        (build_resource_module, ("concurrency", "at_most_one_of"), [["x", "x"]]),
        (build_policy_module, ("permissions", "shell", "allow"), ["x", "x"]),
    )
    for builder, path, value in cases:
        with pytest.raises(CompositionError):
            compose_modules(base, [builder([Operation("add", path, value)])])
    duplicate_paths = Operation("replace", ("tools", "registry", "paths"), ["x", "x"])
    with pytest.raises(CompositionError, match="tools.registry.paths"):
        compose_modules(base, [build_tool_module([duplicate_paths])])
    with pytest.raises(CompositionError, match="precedence"):
        compose_modules(base, [build_provider_module([], 1), build_tool_module([], 1)])
    generic = ModuleContribution("generic", 2, [_FOREIGN])
    with pytest.raises(CompositionError, match="owned module"):
        compose_modules(base, [generic])
    typed = build_provider_module([])
    with pytest.raises(CompositionError, match="internal capability"):
        replace(typed, operations=(_FOREIGN,), validator=lambda _: None)
    target_cases = (
        (Operation("add", ("tools", "registry"), {}), "already exists"),
        (Operation("replace", ("tools", "aliases"), {}), "does not exist"),
    )
    for operation, message in target_cases:
        with pytest.raises(CompositionError, match=message):
            compose_modules(base, [build_tool_module([operation])])
    deep = {}
    for _ in range(102):
        deep = {"x": deep}
    with pytest.raises(CompositionError, match="100 segments"):
        Operation("add", ("tools", "deep"), deep)
    deep_base = _load("engineering.v1.yaml")
    deep_base["workspace"]["root"] = deep
    with pytest.raises(CompositionError, match="100 segments"):
        compose_modules(deep_base, [])
    owners = (
        (build_provider_module, "providers provider_tools"),
        (build_tool_module, "tools enhanced_tools"),
        (build_resource_module, "concurrency"),
        (build_policy_module, "permissions guardrails replay"),
        (build_host_module, "workspace"),
        (build_longrun_module, "long_running turn_strategy completion"),
    )
    roots = set(
        "providers provider_tools tools enhanced_tools concurrency permissions guardrails replay workspace long_running turn_strategy completion".split()
    )
    for builder, names in owners:
        for root in roots:
            operation = Operation("add", (root, "probe"), None)
            if root in names.split():
                builder([operation])
            else:
                with pytest.raises(CompositionError, match="unowned root"):
                    builder([operation])
