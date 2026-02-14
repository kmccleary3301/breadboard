"""Auth material types for provider auth overlays.

These objects represent short-lived credential material the Engine can apply to
outgoing provider calls. The Engine must not persist this material to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Dict, Optional, Sequence, Tuple


@dataclass(frozen=True)
class EmulationProfileRequirement:
    """Sealed-profile requirement for compliance-sensitive auth sources.

    The Engine enforces this at attach-time by recomputing a conformance hash
    over a locked subset of the resolved runtime config.
    """

    profile_id: str
    conformance_hash: str
    locked_json_pointers: Tuple[str, ...] = ()

    @staticmethod
    def from_raw(raw: Any) -> Optional["EmulationProfileRequirement"]:
        if not isinstance(raw, dict):
            return None
        profile_id = str(raw.get("profile_id") or raw.get("profileId") or "").strip()
        conformance_hash = str(raw.get("conformance_hash") or raw.get("conformanceHash") or "").strip()
        locked = raw.get("locked_json_pointers") or raw.get("lockedJsonPointers") or []
        pointers: Tuple[str, ...] = ()
        if isinstance(locked, (list, tuple)):
            pointers = tuple(str(p) for p in locked if isinstance(p, str) and p)
        if not profile_id or not conformance_hash:
            return None
        return EmulationProfileRequirement(
            profile_id=profile_id,
            conformance_hash=conformance_hash,
            locked_json_pointers=pointers,
        )


@dataclass
class EngineAuthMaterial:
    """Short-lived auth material applied to provider calls."""

    provider_id: str
    alias: str = ""
    # For OpenAI/Anthropic/OpenRouter style SDKs this typically maps to the SDK's api_key.
    api_key: Optional[str] = None
    # Additional headers that should be applied to provider calls (redacted from logs).
    headers: Dict[str, str] | None = None
    # Optional base URL override (e.g. local bridge, proxy).
    base_url: Optional[str] = None
    # Optional extra routing metadata (provider-specific).
    routing: Dict[str, Any] | None = None
    issued_at_ms: Optional[int] = None
    expires_at_ms: Optional[int] = None
    # Marks that this material corresponds to a compliance-sensitive plan adapter.
    is_subscription_plan: bool = False
    required_profile: Optional[EmulationProfileRequirement] = None

    def header_keys(self) -> Sequence[str]:
        if not self.headers:
            return []
        return sorted(str(k) for k in self.headers.keys())

    @staticmethod
    def _fingerprint(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:12]}"

    def to_sanitized_status(self, now_ms: int) -> Dict[str, Any]:
        expires_in_ms: Optional[int] = None
        if self.expires_at_ms is not None:
            try:
                expires_in_ms = max(0, int(self.expires_at_ms) - int(now_ms))
            except Exception:
                expires_in_ms = None

        secret_fingerprints: Dict[str, str] = {}
        api_key_fp: Optional[str] = None
        if self.api_key:
            api_key_fp = self._fingerprint(str(self.api_key))
            secret_fingerprints["api_key"] = api_key_fp
        for key, val in (self.headers or {}).items():
            if not key or val is None:
                continue
            k = str(key)
            lk = k.lower()
            # Only fingerprint likely-secret headers. This avoids emitting fingerprints for arbitrary
            # headers that might contain sensitive-but-unexpected data.
            if lk not in {"authorization", "x-api-key", "api-key", "cookie", "set-cookie"}:
                continue
            secret_fingerprints[k] = self._fingerprint(str(val))
        return {
            "provider_id": self.provider_id,
            "alias": self.alias or None,
            "has_api_key": bool(self.api_key),
            "api_key_fingerprint": api_key_fp,
            "header_keys": list(self.header_keys()),
            "secret_fingerprints": secret_fingerprints,
            "base_url": self.base_url,
            "routing_keys": sorted(list((self.routing or {}).keys())),
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "expires_in_ms": expires_in_ms,
            "is_subscription_plan": bool(self.is_subscription_plan),
            "required_profile": (
                {
                    "profile_id": self.required_profile.profile_id,
                    "conformance_hash": self.required_profile.conformance_hash,
                    "locked_json_pointers": list(self.required_profile.locked_json_pointers),
                }
                if self.required_profile
                else None
            ),
        }
