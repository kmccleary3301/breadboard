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
    with pytest.raises(CompositionError, match="sequence"):
        build_provider_module(iter([duplicate]))
    with pytest.raises(CompositionError, match="modules must be a sequence"):
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
    reinitialized = build_provider_module([])
    uninitialized = object.__new__(type(reinitialized))
    ModuleContribution.__init__(uninitialized, "forged", 2, ())
    ModuleContribution.__init__(reinitialized, "forged", 2, (_FOREIGN,))
    for name, builder in (
        ("bare", lambda _: ModuleContribution("bare", 1, [])),
        ("forged", lambda _: replace(reinitialized, operations=(_FOREIGN,))),
        ("subclass", lambda _: _ForgedOwned()),
        ("reinitialized", lambda _: reinitialized),
        ("uninitialized", lambda _: uninitialized),
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
    builders = (
        build_policy_module,
        build_resource_module,
        build_longrun_module,
        build_tool_module,
    )
    cases = (
        (0, "permissions/shell", "{default: allow, deny: [{rm: null}]}"),
        (
            1,
            "concurrency/groups",
            "[{name: a, match_tools: [x]}, {name: b, match_tools: [x]}]",
        ),
        (2, "long_running/recovery", "{backoff_base_seconds: 3}"),
        (2, "long_running/verification", "{tiers: [{name: tier_1}, {}]}"),
        (3, "tools/dialects", "{selection: {by_model: {'*': bash_block}}}"),
        (3, "tools/aliases", "{a: b, b: a}"),
    )
    for index, path, value in cases:
        kind = "replace" if index < 2 else "add"
        operation = Operation(kind, tuple(path.split("/")), yaml.safe_load(value))
        reject([builders[index]([operation])])
    with pytest.raises(CompositionError, match="precedence"):
        compose_modules(base, [build_provider_module([], 1), build_tool_module([], 1)])
    forged = type("ForgedOperation", (Operation,), {"__post_init__": lambda self: None})
    with pytest.raises(CompositionError, match="exact Operation"):
        build_provider_module([forged("move", ("providers", "stream_responses"), True)])
    typed = build_provider_module([])
    with pytest.raises(CompositionError, match="internal capability"):
        replace(typed, operations=(_FOREIGN,), validator=lambda _: None)
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
    cycle = {}
    cycle["x"] = cycle
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
