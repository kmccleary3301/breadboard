from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from .interfaces import Extension, ExtensionManifest


def _is_enabled(manifest: ExtensionManifest, config: Dict[str, Any]) -> bool:
    ext_cfg = (config.get("extensions") or {}) if isinstance(config, dict) else {}
    entry = ext_cfg.get(manifest.ext_id)
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        enabled = entry.get("enabled")
        if isinstance(enabled, bool):
            return enabled
    return bool(manifest.default_enabled)


@dataclass
class ExtensionRegistry:
    """Registry for opt-in kernel extensions."""

    _extensions: Dict[str, Extension] = field(default_factory=dict)

    def register(self, extension: Extension) -> None:
        ext_id = extension.manifest.ext_id
        if ext_id in self._extensions:
            raise ValueError(f"Extension '{ext_id}' already registered")
        self._extensions[ext_id] = extension

    def list_all(self) -> List[Extension]:
        return list(self._extensions.values())

    def enabled_extensions(self, config: Dict[str, Any]) -> List[Extension]:
        enabled: List[Extension] = []
        for ext in self._extensions.values():
            if _is_enabled(ext.manifest, config):
                enabled.append(ext)
        return enabled
