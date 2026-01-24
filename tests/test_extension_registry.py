from __future__ import annotations

from agentic_coder_prototype.extensions import ExtensionRegistry, MiddlewareSpec


def test_extension_registry_phase_ordering() -> None:
    registry = ExtensionRegistry()
    registry.add_middleware(MiddlewareSpec("b", "plugin_b", "before_model", priority=10))
    registry.add_middleware(MiddlewareSpec("a", "plugin_a", "before_model", priority=1))
    registry.add_middleware(MiddlewareSpec("c", "plugin_c", "after_model", priority=1))

    before = registry.list_phase("before_model")
    after = registry.list_phase("after_model")

    assert [spec.middleware_id for spec in before] == ["a", "b"]
    assert [spec.middleware_id for spec in after] == ["c"]


def test_extension_registry_deterministic_tiebreaks() -> None:
    registry = ExtensionRegistry()
    registry.add_middleware(MiddlewareSpec("alpha", "plugin_b", "before_tool", priority=5))
    registry.add_middleware(MiddlewareSpec("beta", "plugin_a", "before_tool", priority=5))

    ordered = registry.list_phase("before_tool")
    assert [spec.middleware_id for spec in ordered] == ["beta", "alpha"]
