"""Public session-event projection owned by the product runtime."""

from collections.abc import Iterable
from types import MappingProxyType
from typing import Any, Final, Mapping

from breadboard.product.runtime.events import KernelEvent


PUBLIC_SESSION_EVENT_SCHEMA_VERSION: Final = "bb.public_session_event.v1"

# This is the one product-owned mapping from durable event kinds to public
# payload schemas. The binding generator consumes it when producing the SDK
# projection metadata, so Python and TypeScript cannot silently drift.
_PUBLIC_PAYLOAD_SCHEMAS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "session.started": "bb.payload.product_session.lifecycle.v1",
        "input.accepted": "bb.payload.product_session.lifecycle.v1",
        "approval.requested": "bb.payload.product_session.lifecycle.v1",
        "approval.resolved": "bb.payload.product_session.lifecycle.v1",
        "session.reconfigured": "bb.payload.product_session.lifecycle.v1",
        "session.paused": "bb.payload.product_session.lifecycle.v1",
        "session.resumed": "bb.payload.product_session.lifecycle.v1",
        "session.completed": "bb.payload.product_session.lifecycle.v1",
        "session.failed": "bb.payload.product_session.lifecycle.v1",
        "session.canceled": "bb.payload.product_session.lifecycle.v1",
        "assistant_message": "bb.payload.message.assistant.v1",
        "tool_call": "bb.payload.tool.called.v1",
        "tool_result": "bb.payload.tool.completed.v1",
        "annotation": "bb.payload.product_session.annotation.v1",
    }
)

# Public name used by deterministic code generation and equivalence checks.
PUBLIC_PAYLOAD_SCHEMAS: Final[Mapping[str, str]] = _PUBLIC_PAYLOAD_SCHEMAS
_INTERNAL_EVENT_KINDS = frozenset({"context.compacted"})


def public_session_event(
    event: KernelEvent | Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project one durable event, omitting internal kinds until publicly amended."""
    source = event.as_dict() if isinstance(event, KernelEvent) else event
    kind = str(source["kind"])
    if kind in _INTERNAL_EVENT_KINDS:
        return None
    payload = source["payload"]
    lineage = payload.get("lineage")
    session_id = str(source["session_id"])
    sequence = int(source["sequence"])
    return {
        "schema_version": PUBLIC_SESSION_EVENT_SCHEMA_VERSION,
        "event_id": f"session:{session_id}:{sequence}",
        "seq": sequence,
        "timestamp": source["occurred_at"],
        "work_item_id": None if lineage is None else lineage["child_work_item_id"],
        "parent_work_item_id": None if lineage is None else lineage["parent_work_item_id"],
        "attempt_id": None,
        "session_id": session_id,
        "span_id": None,
        "visibility": {
            "model_visible": kind != "annotation",
            "provider_visible": kind != "annotation",
            "host_visible": True,
            "redaction_state": "none",
        },
        "kind": kind,
        "payload": payload,
        "payload_schema_version": _PUBLIC_PAYLOAD_SCHEMAS[kind],
    }


def public_session_events(
    events: Iterable[KernelEvent | Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Project durable events while dropping internal events without changing cursors."""
    projected: list[dict[str, Any]] = []
    for event in events:
        item = public_session_event(event)
        if item is not None:
            projected.append(item)
    return tuple(projected)


__all__ = [
    "PUBLIC_PAYLOAD_SCHEMAS",
    "PUBLIC_SESSION_EVENT_SCHEMA_VERSION",
    "public_session_event",
    "public_session_events",
]
