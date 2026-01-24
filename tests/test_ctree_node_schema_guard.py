from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from agentic_coder_prototype.state.session_state import SessionState


class _EventCollector:
    def __init__(self) -> None:
        self.events: List[Tuple[str, Dict[str, Any], int | None]] = []

    def __call__(self, event_type: str, payload: Dict[str, Any], *, turn: int | None = None) -> None:
        self.events.append((event_type, payload, turn))

    def last_of_type(self, event_type: str) -> Dict[str, Any]:
        for kind, payload, _turn in reversed(self.events):
            if kind == event_type:
                return payload
        return {}


def _load_schema(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _required(schema: Dict[str, Any]) -> List[str]:
    req = schema.get("required")
    return [str(item) for item in req] if isinstance(req, list) else []


def test_ctree_node_payload_matches_schema_required_fields() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "docs" / "contracts" / "cli_bridge" / "schemas" / "session_event_payload_ctree_node.schema.json"
    schema = _load_schema(schema_path)

    required_top = set(_required(schema))
    node_schema = schema.get("properties", {}).get("node", {})
    snapshot_schema = schema.get("properties", {}).get("snapshot", {})
    required_node = set(_required(node_schema))
    required_snapshot = set(_required(snapshot_schema))

    # Guard against schema drift: we expect these fields to remain required.
    assert {"node", "snapshot"}.issubset(required_top)
    assert {"id", "digest", "kind", "payload"}.issubset(required_node)
    assert {"schema_version", "node_count", "event_count", "last_id", "node_hash"}.issubset(required_snapshot)

    collector = _EventCollector()
    state = SessionState("ws", "image", {}, event_emitter=collector)
    state.add_message({"role": "assistant", "content": "hello"}, to_provider=False)

    payload = collector.last_of_type("ctree_node")
    assert payload, "ctree_node event not emitted"

    # Top-level required fields
    assert required_top.issubset(payload.keys())
    node = payload.get("node") or {}
    snapshot = payload.get("snapshot") or {}

    # Node required fields
    assert required_node.issubset(node.keys())
    # Snapshot required fields
    assert required_snapshot.issubset(snapshot.keys())
