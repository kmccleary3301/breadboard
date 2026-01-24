from __future__ import annotations

from agentic_coder_prototype.extensions import ExtensionRegistry, MiddlewareSpec
from agentic_coder_prototype.hooks.manager import build_hook_manager_from_registry


def test_registry_bridge_orders_hooks() -> None:
    registry = ExtensionRegistry()
    registry.add_middleware(MiddlewareSpec("b", "plugin_b", "before_model", priority=10))
    registry.add_middleware(MiddlewareSpec("a", "plugin_a", "before_model", priority=1))

    manager = build_hook_manager_from_registry(registry)
    ordered = manager._ordered.get("before_model", [])  # noqa: SLF001 (test-only)
    assert [hook.hook_id for hook in ordered] == ["a", "b"]


def test_registry_bridge_respects_before_after() -> None:
    registry = ExtensionRegistry()
    registry.add_middleware(MiddlewareSpec("a", "plugin_a", "before_tool", priority=5, before=("b",)))
    registry.add_middleware(MiddlewareSpec("b", "plugin_b", "before_tool", priority=1))

    manager = build_hook_manager_from_registry(registry)
    ordered = manager._ordered.get("before_tool", [])  # noqa: SLF001 (test-only)
    assert [hook.hook_id for hook in ordered] == ["a", "b"]
