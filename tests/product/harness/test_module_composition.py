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


class _ForgedOwned(type(build_provider_module([]))):
    def __init__(self) -> None:
        ModuleContribution.__init__(self, "forged", 2, (_FOREIGN,))


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
    seed = build_provider_module([Operation("add", path, False)], 5)
    provider = build_provider_module([Operation("replace", path, True)], 10)
    tools = build_tool_module([Operation("add", ("tools", "aliases"), aliases)], 20)
    composed = compose_modules(base, [tools, provider, seed])
    aliases["read"] = "changed"
    assert composed["providers"]["stream_responses"] is True
    assert composed["tools"]["aliases"] == {"read": "read_file"}
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
    reinitialized = build_provider_module([])
    ModuleContribution.__init__(reinitialized, "forged", 2, (_FOREIGN,))
    for name, builder in (
        ("bare", lambda _: ModuleContribution("bare", 1, [])),
        ("forged", lambda _: replace(reinitialized, operations=(_FOREIGN,))),
        ("subclass", lambda _: _ForgedOwned()),
        ("reinitialized", lambda _: reinitialized),
    ):
        registry.register(name, builder)
        with pytest.raises(CompositionError):
            registry.resolve(name, {})


def test_module_invariants_fail_before_lock() -> None:
    base = _load("engineering.v1.yaml")

    def reject(modules: object, match: str | None = None, document=base) -> None:
        with pytest.raises(CompositionError, match=match):
            compose_modules(document, modules)

    bad_model = Operation("replace", ("providers", "default_model"), "x")
    reject([build_provider_module([bad_model])], "declared model")
    models = [{"id": f"m{i}", "adapter": "mock"} for i in range(1101)]
    for i, model in enumerate(models[:-1]):
        model["routing"] = {"fallback_models": [f"m{i + 1}"]}
    provider_chain = {**base, "providers": {"default_model": "m0", "models": models}}
    compose_modules(provider_chain, [build_provider_module([])])
    models[-1]["routing"] = {"fallback_models": ["m0"]}
    reject([build_provider_module([])], "acyclic", provider_chain)
    bad_resume = Operation("add", ("long_running", "resume"), {"enabled": True})
    reject([build_longrun_module([bad_resume])], "state_path")
    budget = Operation("replace", ("long_running", "budgets"), {"total_tokens": 1})
    compose_modules(base, [build_longrun_module([budget])])
    for budgets in (
        {"wall_clock_s": 1},
        {"total_tokens": 0, "max_total_tokens": 1},
        {"total_cost_usd": 0, "max_total_cost_usd": 1},
    ):
        unsupported = Operation("replace", ("long_running", "budgets"), budgets)
        reject([build_longrun_module([unsupported])], "positive stopping budget")
    zero_backoff = Operation(
        "add", ("long_running", "recovery"), {"backoff_base_seconds": 0}
    )
    reject([build_longrun_module([zero_backoff])], "positive when provided")
    cases = (
        (build_host_module, ("workspace", "mirror"), {"enabled": True}),
        (build_resource_module, ("concurrency", "at_most_one_of"), [["x", "x"]]),
        (build_policy_module, ("permissions", "shell", "allow"), ["x", "x"]),
    )
    for builder, path, value in cases:
        reject([builder([Operation("add", path, value)])])
    zero_group = [{"name": "x", "match_tools": ["x"], "max_parallel": 0}]
    zero_parallel = Operation("replace", ("concurrency", "groups"), zero_group)
    reject([build_resource_module([zero_parallel])], "max_parallel")
    duplicate_paths = Operation("replace", ("tools", "registry", "paths"), ["x", "x"])
    reject([build_tool_module([duplicate_paths])], "tools.registry.paths")
    with pytest.raises(CompositionError, match="precedence"):
        compose_modules(base, [build_provider_module([], 1), build_tool_module([], 1)])
    generic = ModuleContribution("generic", 2, [_FOREIGN])
    reject([generic], "owned module")
    reject([_ForgedOwned()], "owned module")
    forged = type("ForgedOperation", (Operation,), {"__post_init__": lambda self: None})
    with pytest.raises(CompositionError, match="exact Operation"):
        build_provider_module([forged("move", ("providers", "stream_responses"), True)])
    typed = build_provider_module([])
    with pytest.raises(CompositionError, match="internal capability"):
        replace(typed, operations=(_FOREIGN,), validator=lambda _: None)
    ModuleContribution.__init__(typed, "forged", 2, (_FOREIGN,))
    reject([typed], "unowned root")
    target_cases = (
        (Operation("add", ("tools", "registry"), {}), "already exists"),
        (Operation("replace", ("tools", "aliases"), {}), "does not exist"),
    )
    for operation, message in target_cases:
        reject([build_tool_module([operation])], message)
    deep = {}
    for _ in range(99):
        deep = {"x": deep}
    deep_base = _load("engineering.v1.yaml")
    deep_base["dossier"] = deep
    compose_modules(deep_base, [])
    deep["x"] = {"x": deep["x"]}
    reject([], "100 segments", deep_base)
    cycles = ({}, [])
    cycles[0]["x"] = cycles[0]
    cycles[1].append(cycles[1])
    for cycle in cycles:
        with pytest.raises(CompositionError, match="cyclic JSON"):
            Operation("add", ("tools", "deep"), cycle)
        cyclic_base = _load("engineering.v1.yaml")
        cyclic_base["dossier"] = cycle
        reject([], "cyclic JSON", cyclic_base)
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
    for builder, names in owners:
        assert get_type_hints(builder)["return"] is ModuleContribution
        for root in roots:
            operation = Operation("add", (root, "probe"), None)
            if root in names.split():
                builder([operation])
            else:
                with pytest.raises(CompositionError, match="unowned root"):
                    builder([operation])
