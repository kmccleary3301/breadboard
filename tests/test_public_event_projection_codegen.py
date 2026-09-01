from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import generate_public_bindings as generator
from breadboard.product.runtime.public_event_projection import PUBLIC_PAYLOAD_SCHEMAS, public_session_event


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts/kernel/registries/kernel_event_kinds.v1.json"


def test_event_bindings_cover_registry_and_projection_owner() -> None:
    outputs = generator.build_outputs(ROOT)
    generated = outputs[ROOT / generator.EVENT_BINDINGS_RELATIVE].decode("utf-8")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for entry in registry["entries"]:
        assert f'eventType: "{entry["id"]}"' in generated
    for kind, schema in PUBLIC_PAYLOAD_SCHEMAS.items():
        assert f'  "{kind}": "{schema}",' in generated
    assert "kernel-event-registry-sha256: sha256:" in generated
    assert "public-projection-sha256: sha256:" in generated


def test_projection_owner_preserves_public_envelope() -> None:
    event = {
        "session_id": "session-1",
        "sequence": 3,
        "kind": "assistant_message",
        "occurred_at": "2026-09-01T00:00:00Z",
        "payload": {"metadata": {"has_content": True}},
    }
    projected = public_session_event(event)
    assert projected["schema_version"] == "bb.public_session_event.v1"
    assert projected["event_id"] == "session:session-1:3"
    assert projected["payload_schema_version"] == "bb.payload.message.assistant.v1"
