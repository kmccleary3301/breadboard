from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List

from .interfaces import Extension, ExtensionManifest


class ExtensionError(ValueError):
    """Raised when an extension does not implement the trusted extension port."""


_EXT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

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
        manifest = getattr(extension, "manifest", None)
        providers = getattr(extension, "providers", None)
        if not isinstance(manifest, ExtensionManifest) or not _EXT_ID.fullmatch(manifest.ext_id):
            raise ExtensionError("extension manifest is unknown or malformed")
        if not callable(providers):
            raise ExtensionError("extension does not expose providers()")
        ext_id = manifest.ext_id
        if ext_id in self._extensions:
            raise ValueError(f"Extension '{ext_id}' already registered")
        self._extensions[ext_id] = extension
        return None

    def list_all(self) -> List[Extension]:
        return list(self._extensions.values())

    def enabled_extensions(self, config: Dict[str, Any]) -> List[Extension]:
        enabled: List[Extension] = []
        for ext in self._extensions.values():
            if _is_enabled(ext.manifest, config):
                enabled.append(ext)
        return enabled
