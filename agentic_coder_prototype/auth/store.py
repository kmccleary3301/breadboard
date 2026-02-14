"""In-memory provider auth material store with TTL expiry."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from .material import EngineAuthMaterial, EmulationProfileRequirement


def _now_ms() -> int:
    return int(time.time() * 1000)


class ProviderAuthStore:
    """Stores short-lived auth material in-memory (never persisted)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: Dict[Tuple[str, str], EngineAuthMaterial] = {}

    def _key(self, provider_id: str, alias: str) -> Tuple[str, str]:
        return (str(provider_id or "").strip(), str(alias or "").strip())

    def attach(
        self,
        material: EngineAuthMaterial,
        *,
        ttl_seconds: Optional[int] = None,
        required_profile: Optional[EmulationProfileRequirement] = None,
    ) -> EngineAuthMaterial:
        now = _now_ms()
        if material.issued_at_ms is None:
            material.issued_at_ms = now
        if required_profile is not None:
            material.required_profile = required_profile
        if ttl_seconds is not None and material.expires_at_ms is None:
            try:
                material.expires_at_ms = now + (max(0, int(ttl_seconds)) * 1000)
            except Exception:
                pass
        key = self._key(material.provider_id, material.alias)
        with self._lock:
            self._items[key] = material
        return material

    def detach(self, provider_id: str, *, alias: str = "") -> bool:
        key = self._key(provider_id, alias)
        with self._lock:
            return self._items.pop(key, None) is not None

    def _is_expired(self, material: EngineAuthMaterial, now_ms: int) -> bool:
        if material.expires_at_ms is None:
            return False
        try:
            return int(material.expires_at_ms) <= int(now_ms)
        except Exception:
            return False

    def get(self, provider_id: str, *, alias: str = "") -> Optional[EngineAuthMaterial]:
        now = _now_ms()
        key = self._key(provider_id, alias)
        with self._lock:
            material = self._items.get(key)
            if material is None:
                return None
            if self._is_expired(material, now):
                self._items.pop(key, None)
                return None
            return material

    def status(self) -> List[Dict[str, Any]]:
        now = _now_ms()
        with self._lock:
            items = list(self._items.values())
        # Best-effort expiry cleanup without holding lock during status formatting.
        for material in items:
            if self._is_expired(material, now):
                self.detach(material.provider_id, alias=material.alias)
        with self._lock:
            active = list(self._items.values())
        return [m.to_sanitized_status(now) for m in active]


DEFAULT_PROVIDER_AUTH_STORE = ProviderAuthStore()

