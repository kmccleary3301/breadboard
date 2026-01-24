from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import json
import os


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_models(payload: Any) -> Dict[str, Dict[str, Any]]:
    models: Dict[str, Dict[str, Any]] = {}
    if isinstance(payload, dict):
        items = []
        for key, value in payload.items():
            if isinstance(value, dict):
                entry = dict(value)
                entry.setdefault("id", str(entry.get("id") or key))
                items.append(entry)
    elif isinstance(payload, list):
        items = [entry for entry in payload if isinstance(entry, dict)]
    else:
        items = []

    for entry in items:
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        model_id = model_id.strip()
        models[model_id] = entry
        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    models[alias.strip()] = entry
    return models


@dataclass
class ModelRegistry:
    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    models: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    default_provider: Optional[str] = None
    auth: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ModelRegistry":
        providers = payload.get("providers")
        models = payload.get("models")
        default_provider = payload.get("default_provider") or payload.get("defaultProvider")
        auth = payload.get("auth") if isinstance(payload.get("auth"), dict) else {}
        return cls(
            providers=dict(providers) if isinstance(providers, dict) else {},
            models=_normalize_models(models),
            default_provider=str(default_provider) if isinstance(default_provider, str) else None,
            auth=dict(auth or {}),
        )

    @classmethod
    def from_path(cls, path: str | Path) -> Optional["ModelRegistry"]:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return None
        payload = _load_json(target)
        if not payload:
            return None
        return cls.from_payload(payload)

    def resolve_model(self, model_id: str) -> Optional[Dict[str, Any]]:
        return self.models.get(model_id)


@dataclass
class AuthResolver:
    inline_keys: Dict[str, str] = field(default_factory=dict)
    env_overrides: Dict[str, str] = field(default_factory=dict)
    key_files: Iterable[str] = field(default_factory=list)
    _cache: Dict[str, Dict[str, Any]] = field(default_factory=dict, init=False)

    @classmethod
    def from_registry(cls, registry: Optional[ModelRegistry]) -> "AuthResolver":
        inline_keys: Dict[str, str] = {}
        env_overrides: Dict[str, str] = {}
        key_files: list[str] = []
        if registry and registry.auth:
            inline = registry.auth.get("keys")
            if isinstance(inline, dict):
                inline_keys = {str(k): str(v) for k, v in inline.items() if v}
            env_map = registry.auth.get("env")
            if isinstance(env_map, dict):
                env_overrides = {str(k): str(v) for k, v in env_map.items() if v}
            files = registry.auth.get("files")
            if isinstance(files, list):
                key_files = [str(item) for item in files if isinstance(item, str) and item.strip()]
        env_paths = os.environ.get("BREADBOARD_API_KEYS_PATH") or ""
        if env_paths:
            for entry in env_paths.split(os.pathsep):
                entry = entry.strip()
                if entry:
                    key_files.append(entry)
        return cls(inline_keys=inline_keys, env_overrides=env_overrides, key_files=key_files)

    def _load_key_file(self, path: str) -> Optional[Dict[str, Any]]:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return None
        cached = self._cache.get(str(target))
        if cached:
            return cached
        payload = _load_json(target)
        if isinstance(payload, dict):
            self._cache[str(target)] = payload
            return payload
        return None

    def resolve(self, provider_id: str, fallback_env: Optional[str] = None) -> Optional[str]:
        if provider_id in self.inline_keys:
            return self.inline_keys.get(provider_id)
        env_key = self.env_overrides.get(provider_id) or fallback_env
        if env_key:
            value = os.environ.get(env_key)
            if value:
                return value
        for path in self.key_files:
            payload = self._load_key_file(path)
            if not payload:
                continue
            if provider_id in payload and payload.get(provider_id):
                return str(payload.get(provider_id))
        return None


def load_model_registry_from_env() -> Optional[ModelRegistry]:
    path = os.environ.get("BREADBOARD_MODEL_REGISTRY") or os.environ.get("BREADBOARD_MODEL_REGISTRY_PATH")
    if not path:
        return None
    return ModelRegistry.from_path(path)
