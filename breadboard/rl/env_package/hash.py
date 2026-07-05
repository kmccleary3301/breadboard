from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _without_package_hash(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        return {
            str(key): _without_package_hash(value)
            for key, value in payload.items()
            if str(key) != "package_hash"
        }
    if isinstance(payload, list):
        return [_without_package_hash(item) for item in payload]
    return payload


def canonical_env_package_hash(payload: Mapping[str, Any]) -> str:
    """Return a stable sha256 hash for an EnvPackage mapping.

    The declared ``package_hash`` field is excluded so a package can carry its
    own hash without changing the canonical digest.
    """

    canonical_payload = _without_package_hash(payload)
    encoded = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
