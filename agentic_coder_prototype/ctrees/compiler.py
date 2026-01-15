from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from .store import CTreeStore
from .schema import CTREE_SCHEMA_VERSION


def _canonical_dumps(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def _hash_payload(value: Any) -> str:
    blob = _canonical_dumps(value)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def compile_ctree(store: CTreeStore, *, prompt_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Placeholder compiler producing deterministic stage hashes."""

    hashes = {}
    try:
        hashes = store.hashes()
    except Exception:
        hashes = {}
    node_count = len(getattr(store, "nodes", []) or [])
    event_count = len(getattr(store, "events", []) or [])

    raw_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "hashes": hashes,
    }
    if prompt_summary:
        raw_payload["prompt_summary"] = prompt_summary

    spec_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "hashes": hashes,
    }
    header_payload: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
    }

    z1 = _hash_payload(raw_payload)
    z2 = _hash_payload(spec_payload)
    z3 = _hash_payload(header_payload)

    return {
        "kind": "stub",
        "schema_version": CTREE_SCHEMA_VERSION,
        "node_count": node_count,
        "event_count": event_count,
        "has_prompt_summary": bool(prompt_summary),
        "hashes": {
            **hashes,
            "z1": z1,
            "z2": z2,
            "z3": z3,
        },
        "stages": {
            "RAW": raw_payload,
            "SPEC": spec_payload,
            "HEADER": header_payload,
            "FROZEN": header_payload,
        },
    }
