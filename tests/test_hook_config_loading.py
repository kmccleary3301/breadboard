from __future__ import annotations

from agentic_coder_prototype.hooks.manager import build_hook_manager


def test_build_hook_manager_loads_extra_hooks(tmp_path) -> None:
    config = {
        "hooks": {
            "enabled": True,
            "ctree_recorder": False,
            "extra_hooks": [
                "agentic_coder_prototype.hooks.example_hooks.ExamplePolicyDecisionHook",
            ],
        }
    }
    hook_manager = build_hook_manager(config, str(tmp_path))
    assert hook_manager is not None
    snapshot = hook_manager.snapshot()
    hook_ids = {entry.get("hook_id") for entry in snapshot.get("hooks", []) if isinstance(entry, dict)}
    assert "example_policy_decision" in hook_ids


def test_build_hook_manager_skips_disabled_extra_hook(tmp_path) -> None:
    config = {
        "hooks": {
            "enabled": True,
            "ctree_recorder": False,
            "extra_hooks": [
                {"path": "agentic_coder_prototype.hooks.example_hooks.ExamplePolicyDecisionHook", "enabled": False},
            ],
        }
    }
    hook_manager = build_hook_manager(config, str(tmp_path))
    assert hook_manager is not None
    snapshot = hook_manager.snapshot()
    hook_ids = {entry.get("hook_id") for entry in snapshot.get("hooks", []) if isinstance(entry, dict)}
    assert "example_policy_decision" not in hook_ids

