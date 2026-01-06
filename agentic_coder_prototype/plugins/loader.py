from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PluginManifest:
    plugin_id: str
    permissions: Dict[str, Any] = field(default_factory=dict)
    runtime: Dict[str, Any] = field(default_factory=dict)
    skills_paths: List[str] = field(default_factory=list)


def discover_plugin_manifests(config: Dict[str, Any], workspace: str) -> List[PluginManifest]:
    # Plugins are intentionally disabled unless explicitly enabled/configured.
    plugins_cfg = (config or {}).get("plugins") if isinstance(config, dict) else None
    if not isinstance(plugins_cfg, dict) or not plugins_cfg.get("enabled"):
        return []
    # Placeholder: real plugin discovery will scan plugin dirs/manifests.
    return []


def plugin_snapshot(manifests: List[PluginManifest]) -> Dict[str, Any]:
    return {"plugins": [m.plugin_id for m in manifests]}

