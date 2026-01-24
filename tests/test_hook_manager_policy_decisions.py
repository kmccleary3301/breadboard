from __future__ import annotations

from agentic_coder_prototype.hooks.manager import HookManager
from agentic_coder_prototype.hooks.model import HookResult


class _DecisionHook:
    hook_id = "decision_hook"
    owner = "engine"
    phases = ("pre_tool",)

    def run(self, phase, payload, **kwargs):  # type: ignore[no-untyped-def]
        return HookResult(
            action="allow",
            payload={
                "policy_decisions": [
                    {"action": "deny", "reason_code": "blocked", "message": "blocked"},
                ]
            },
        )


def test_hook_manager_collects_policy_decisions() -> None:
    manager = HookManager([_DecisionHook()], collect_decisions=True)
    manager.run("pre_tool", {})
    decisions = manager.decision_log()
    assert len(decisions) == 1
    assert decisions[0]["action"] == "deny"


def test_hook_manager_skips_decisions_when_disabled() -> None:
    manager = HookManager([_DecisionHook()], collect_decisions=False)
    manager.run("pre_tool", {})
    assert manager.decision_log() == []
