from __future__ import annotations

import pytest

from agentic_coder_prototype.hooks.manager import HookManager
from agentic_coder_prototype.hooks.model import HookResult


class _HookBase:
    owner = "engine"
    phases = ("pre_tool",)

    def __init__(self, hook_id: str, *, priority: int = 100, before=(), after=()):
        self.hook_id = hook_id
        self.priority = priority
        self.before = before
        self.after = after

    def run(self, phase, payload, **kwargs):  # type: ignore[no-untyped-def]
        return HookResult(action="allow", payload={"hook": self.hook_id, "phase": phase})


def test_hook_manager_orders_by_priority() -> None:
    calls = []

    class HookA(_HookBase):
        pass

    class HookB(_HookBase):
        pass

    hook_a = HookA("a", priority=10)
    hook_b = HookB("b", priority=1)

    manager = HookManager([hook_a, hook_b])

    def _exec(hook, phase, payload, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(hook.hook_id)
        return hook.run(phase, payload, **kwargs)

    # Patch run order by intercepting hook.run via wrapper
    manager.run("pre_tool", {}, hook_executor=None)
    # Re-run to collect order explicitly
    calls.clear()
    for hook in manager._ordered.get("pre_tool", []):  # noqa: SLF001 (test-only)
        _exec(hook, "pre_tool", {})

    assert calls == ["b", "a"]


def test_hook_manager_respects_before_after() -> None:
    calls = []

    class HookA(_HookBase):
        pass

    class HookB(_HookBase):
        pass

    hook_a = HookA("a", priority=10, before=("b",))
    hook_b = HookB("b", priority=1)

    manager = HookManager([hook_a, hook_b])
    calls.clear()
    for hook in manager._ordered.get("pre_tool", []):  # noqa: SLF001 (test-only)
        calls.append(hook.hook_id)

    assert calls == ["a", "b"]


def test_hook_manager_duplicate_ids_error() -> None:
    hook_a = _HookBase("dup")
    hook_b = _HookBase("dup")
    with pytest.raises(ValueError):
        HookManager([hook_a, hook_b])
