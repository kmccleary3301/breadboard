# GENERATED FILE - do not edit by hand.
# generator: scripts/quality/generate_public_bindings.py
# generator-version: 6
# public-projection-sha256: sha256:3f207c6ff2608695aba89c615b174a8ff9145da2c89f1fb9b60d63d6c2f7cf80

from types import MappingProxyType
from typing import Final, Literal, Mapping

PublicSessionEventKind = Literal[
    "annotation",
    "approval.requested",
    "approval.resolved",
    "assistant_message",
    "input.accepted",
    "module_output",
    "session.canceled",
    "session.completed",
    "session.failed",
    "session.paused",
    "session.reconfigured",
    "session.resumed",
    "session.started",
    "tool_call",
    "tool_result",
]
PublicSessionEventPayloadSchema = Literal[
    "bb.payload.message.assistant.v1",
    "bb.payload.product_session.annotation.v1",
    "bb.payload.product_session.lifecycle.v1",
    "bb.payload.product_session.module_output.v1",
    "bb.payload.tool.called.v1",
    "bb.payload.tool.completed.v1",
]
PublicSessionLifecycleEventKind = Literal[
    "approval.requested",
    "approval.resolved",
    "input.accepted",
    "session.canceled",
    "session.completed",
    "session.failed",
    "session.paused",
    "session.reconfigured",
    "session.resumed",
    "session.started",
]

PUBLIC_SESSION_EVENT_PAYLOAD_SCHEMAS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "annotation": "bb.payload.product_session.annotation.v1",
        "approval.requested": "bb.payload.product_session.lifecycle.v1",
        "approval.resolved": "bb.payload.product_session.lifecycle.v1",
        "assistant_message": "bb.payload.message.assistant.v1",
        "input.accepted": "bb.payload.product_session.lifecycle.v1",
        "module_output": "bb.payload.product_session.module_output.v1",
        "session.canceled": "bb.payload.product_session.lifecycle.v1",
        "session.completed": "bb.payload.product_session.lifecycle.v1",
        "session.failed": "bb.payload.product_session.lifecycle.v1",
        "session.paused": "bb.payload.product_session.lifecycle.v1",
        "session.reconfigured": "bb.payload.product_session.lifecycle.v1",
        "session.resumed": "bb.payload.product_session.lifecycle.v1",
        "session.started": "bb.payload.product_session.lifecycle.v1",
        "tool_call": "bb.payload.tool.called.v1",
        "tool_result": "bb.payload.tool.completed.v1",
    }
)
