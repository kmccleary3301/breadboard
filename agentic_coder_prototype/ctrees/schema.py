from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, Optional

CTREE_SCHEMA_VERSION = "0.1"
CTREE_VOLATILE_KEYS = {"seq", "timestamp", "timestamp_ms"}
CTREE_SECRET_KEYS = {
    "api_key",
    "authorization",
    "openai_api_key",
    "openrouter_api_key",
    "anthropic_api_key",
}


def redact_ctree_payload(value: Any) -> Any:
    """Redact obvious secret-bearing fields.

    This is intentionally conservative: key-based redaction only.
    """

    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in CTREE_SECRET_KEYS:
                out[key_str] = "***REDACTED***"
            else:
                out[key_str] = redact_ctree_payload(item)
        return out
    if isinstance(value, list):
        return [redact_ctree_payload(item) for item in value]
    return value


def sanitize_ctree_payload(value: Any) -> Any:
    """Return a stable, JSON-serializable representation for hashing.

    Removes known volatile keys (seq/timestamps) so replays produce stable digests.
    """

    value = redact_ctree_payload(value)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if key in CTREE_VOLATILE_KEYS:
                continue
            out[str(key)] = sanitize_ctree_payload(item)
        return out
    if isinstance(value, list):
        return [sanitize_ctree_payload(item) for item in value]
    return value


def canonical_ctree_json(value: Any) -> str:
    return json.dumps(
        sanitize_ctree_payload(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def normalize_ctree_snapshot_summary(snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a stable, minimal snapshot summary for emission/persistence."""

    if not isinstance(snapshot, dict):
        return None

    def _to_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except Exception:
                return None
        return None

    schema_version = snapshot.get("schema_version") or CTREE_SCHEMA_VERSION
    if not isinstance(schema_version, str):
        schema_version = CTREE_SCHEMA_VERSION

    node_count = _to_int(snapshot.get("node_count"))
    event_count = _to_int(snapshot.get("event_count"))

    last_id = snapshot.get("last_id")
    if last_id is not None and not isinstance(last_id, str):
        last_id = str(last_id)

    node_hash = snapshot.get("node_hash")
    if node_hash is not None and not isinstance(node_hash, str):
        node_hash = str(node_hash)

    backfilled = bool(snapshot.get("backfilled_from_eventlog"))
    if backfilled:
        node_hash = None

    out: Dict[str, Any] = {
        "schema_version": schema_version,
        "node_count": node_count,
        "event_count": event_count,
        "last_id": last_id,
        "node_hash": node_hash,
    }
    if backfilled:
        out["backfilled_from_eventlog"] = True
    return out


@dataclass
class CTreeEvent:
    kind: str
    payload: Any
    turn: Optional[int] = None
    node_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "turn": self.turn,
            "node_id": self.node_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CTreeEvent":
        return cls(
            kind=str(data.get("kind", "")),
            payload=data.get("payload"),
            turn=data.get("turn"),
            node_id=data.get("node_id"),
        )
