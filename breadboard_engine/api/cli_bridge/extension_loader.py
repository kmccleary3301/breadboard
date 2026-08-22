from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

from breadboard.ext import ExtensionRegistry, EndpointProvider

logger = logging.getLogger(__name__)


def _extension_enabled(config: Dict[str, Any], ext_id: str) -> bool:
    ext_cfg = (config.get("extensions") or {}) if isinstance(config, dict) else {}
    entry = ext_cfg.get(ext_id)
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        enabled = entry.get("enabled")
        if isinstance(enabled, bool):
            return enabled
    return False


def load_extension_config_from_env() -> Dict[str, Any] | None:
    raw = (os.environ.get("BREADBOARD_EXTENSIONS_CONFIG_PATH") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.exists():
        raise FileNotFoundError(f"Extension config not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"Failed to read extension config: {path}") from exc
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse extension config: {path}") from exc
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"Extension config must be a mapping: {path}")
    return data


def _load_extensions(config: Dict[str, Any]) -> ExtensionRegistry:
    registry = ExtensionRegistry()
    if _extension_enabled(config, "atp"):
        try:
            from breadboard_ext.atp import ATPBridgeExtension
        except Exception:
            raise
        registry.register(ATPBridgeExtension())
    if _extension_enabled(config, "evolake"):
        try:
            from breadboard_ext.evolake import EvoLakeBridgeExtension
        except Exception:
            raise
        registry.register(EvoLakeBridgeExtension())
    return registry


def mount_extension_routes(app: Any, get_service, config: Dict[str, Any]) -> List[str]:
    registry = _load_extensions(config)
    enabled = registry.enabled_extensions(config)
    mounted: List[str] = []
    for extension in enabled:
        for provider in extension.providers():
            if isinstance(provider, EndpointProvider):
                provider.register_routes(app, get_service)
                mounted.append(extension.manifest.ext_id)
    return mounted
