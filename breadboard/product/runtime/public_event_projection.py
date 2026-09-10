"""Public session-event projection owned by the product runtime."""

from collections.abc import Iterable
from types import MappingProxyType
from typing import Any, Final, Mapping

from breadboard.product.runtime.events import KernelEvent


PUBLIC_SESSION_EVENT_SCHEMA_VERSION: Final = "bb.public_session_event.v1"

# This is the one product-owned mapping from durable event kinds to public
# payload schemas. The binding generator consumes it when producing the SDK
# projection metadata, so Python and TypeScript cannot silently drift.
_LIFECYCLE_EVENT_KINDS: Final[tuple[str, ...]] = (
    "session.started",
    "input.accepted",
    "approval.requested",
    "approval.resolved",
    "session.reconfigured",
    "session.adoption_committed",
    "session.paused",
    "session.resumed",
    "session.completed",
    "session.failed",
    "session.canceled",
)
_PUBLIC_PAYLOAD_SCHEMAS: Final[Mapping[str, str]] = MappingProxyType(
    dict.fromkeys(
        _LIFECYCLE_EVENT_KINDS,
        "bb.payload.product_session.lifecycle.v1",
    )
    | {
        "module_output": "bb.payload.product_session.module_output.v1",
        "assistant_message": "bb.payload.message.assistant.v1",
        "tool_call": "bb.payload.tool.called.v1",
        "tool_result": "bb.payload.tool.completed.v1",
        "annotation": "bb.payload.product_session.annotation.v1",
    }
)

# Public name used by deterministic code generation and equivalence checks.
PUBLIC_PAYLOAD_SCHEMAS: Final[Mapping[str, str]] = _PUBLIC_PAYLOAD_SCHEMAS
_INTERNAL_EVENT_KINDS = frozenset({"context.compacted"})


def _public_adoption_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    public = {
        key: payload[key]
        for key in (
            "adoption_id",
            "checkpoint_id",
            "source_generation_id",
            "source_module_id",
            "source_instance_id",
            "source_work_id",
            "source_attempt_id",
            "source_schema_id",
            "source_body_sha256",
            "source_frontier",
            "target_generation_id",
            "effective_lock_hash",
            "reason",
            "request_id",
        )
        if key in payload
    }
    migration = payload.get("migration")
    if isinstance(migration, (list, tuple)):
        public["migration"] = [
            {
                key: item[key]
                for key in (
                    "binding",
                    "disposition",
                    "source_schema_id",
                    "target_schema_id",
                    "reason",
                )
                if key in item
            }
            for item in migration
            if isinstance(item, Mapping)
        ]
    return public


def public_session_event(
    event: KernelEvent | Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project one durable event, omitting internal kinds until publicly amended."""
    source = event.as_dict() if isinstance(event, KernelEvent) else event
    kind = str(source["kind"])
    if kind in _INTERNAL_EVENT_KINDS:
        return None
    payload = source["payload"]
    if kind == "session.adoption_committed":
        payload = _public_adoption_payload(payload)
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
