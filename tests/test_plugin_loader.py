from __future__ import annotations

import json
from pathlib import Path

from agentic_coder_prototype.plugins.loader import discover_plugin_manifests, plugin_snapshot


def test_plugin_discovery_from_config_search_paths(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins" / "hello"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "skills").mkdir(parents=True, exist_ok=True)
    manifest_path = plugin_root / "breadboard.plugin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "hello-plugin",
                "version": "0.1.0",
                "name": "Hello Plugin",
                "description": "Adds a hello skill.",
                "skills": {"paths": ["skills"]},
                "permissions": {"shell": {"allow": ["echo *"]}},
                "mcp": {"servers": [{"name": "dummy", "command": "node", "args": ["server.js"]}]},
            }
        ),
        encoding="utf-8",
    )

    config = {"plugins": {"enabled": True, "search_paths": [str(tmp_path / "plugins")]}}
    manifests = discover_plugin_manifests(config, workspace=str(tmp_path / "ws"))
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.plugin_id == "hello-plugin"
    assert manifest.version == "0.1.0"
    assert manifest.skills_paths == ["skills"]
    assert manifest.mcp_servers and manifest.mcp_servers[0].get("name") == "dummy"
    assert manifest.trusted is True
    assert manifest.runtime.get("trusted") is True

    snapshot = plugin_snapshot(manifests)
    assert snapshot["plugins"][0]["id"] == "hello-plugin"

