from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.hooks.manager import build_hook_manager
from agentic_coder_prototype.plugins.loader import PluginManifest


def test_plugin_hooks_load_when_enabled(tmp_path: Path) -> None:
    module_path = tmp_path / "hook_module.py"
    module_path.write_text(
        "class TestHook:\n"
        "    phases = [\"on_turn_end\"]\n"
        "    def run(self, phase, payload, **kwargs):\n"
        "        return type(\"Result\", (), {\"action\": \"allow\"})()\n",
        encoding="utf-8",
    )

    manifest = PluginManifest(
        plugin_id="test.plugin",
        version="0.1.0",
        name="Test Plugin",
        description="Test",
        root=str(tmp_path),
        source="test",
        trusted=True,
        hooks=[{"path": "hook_module:TestHook", "id": "hook1", "phase": "on_turn_end"}],
    )

    config = {"hooks": {"enabled": True, "allow_plugin_hooks": True}}
    manager = build_hook_manager(config, str(tmp_path), plugin_manifests=[manifest])
    snapshot = manager.snapshot() if manager else {}
    hook_ids = [entry.get("hook_id") for entry in snapshot.get("hooks", [])]
    owners = [entry.get("owner") for entry in snapshot.get("hooks", [])]

    assert "hook1" in hook_ids
    assert "test.plugin" in owners
