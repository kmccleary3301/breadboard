"""Export CLI bridge OpenAPI and JSON schema contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.api.cli_bridge.app import create_app  # noqa: E402
from agentic_coder_prototype.api.cli_bridge.models import (  # noqa: E402
    SessionCommandRequest,
    SessionCreateRequest,
    SessionInputRequest,
)
CONTRACT_DIR = ROOT / "docs" / "contracts" / "cli_bridge"
SCHEMA_DIR = CONTRACT_DIR / "schemas"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _event_schema() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "SessionEventEnvelope",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "id",
            "type",
            "session_id",
            "payload",
            "timestamp",
            "timestamp_ms",
            "protocol_version",
        ],
        "properties": {
            "id": {"type": "string"},
            "seq": {"type": "integer"},
            "type": {"type": "string"},
            "session_id": {"type": "string"},
            "turn": {"type": ["integer", "null"]},
            "timestamp": {"type": "integer"},
            "timestamp_ms": {"type": "integer"},
            "protocol_version": {"type": "string"},
            "run_id": {"type": ["string", "null"]},
            "thread_id": {"type": ["string", "null"]},
            "turn_id": {"type": ["string", "integer", "null"]},
            "payload": {"type": "object", "additionalProperties": True},
        },
    }

def _payload_schema_tool_call() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ToolCallPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["call_id", "tool"],
        "properties": {
            "call_id": {"type": "string"},
            "tool": {"type": "string"},
            "action": {"type": ["string", "null"]},
            "call": {"type": ["object", "null"]},
            "diff_preview": {},
            "progress": {},
        },
    }


def _payload_schema_tool_result() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ToolResultPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["call_id"],
        "properties": {
            "call_id": {"type": "string"},
            "status": {"type": ["string", "null"]},
            "error": {"type": ["boolean", "null"]},
            "result": {},
            "message": {},
        },
    }


def _payload_schema_permission_request() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "PermissionRequestPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id", "tool", "kind"],
        "properties": {
            "request_id": {"type": "string"},
            "tool": {"type": ["string", "null"]},
            "kind": {"type": ["string", "null"]},
            "summary": {"type": ["string", "null"]},
            "default_scope": {"type": ["string", "null"]},
            "rewindable": {"type": ["boolean", "null"]},
            "diff": {"type": ["string", "null"]},
            "rule_suggestion": {},
            "items": {"type": ["array", "null"]},
            "metadata": {"type": ["object", "null"]},
        },
    }


def _payload_schema_permission_response() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "PermissionResponsePayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id"],
        "properties": {
            "request_id": {"type": "string"},
            "decision": {"type": ["string", "null"]},
            "response": {"type": ["string", "null"]},
            "responses": {"type": ["object", "null"]},
        },
    }


def _model_schema(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    return model.schema()


def main() -> None:
    app = create_app()
    openapi = app.openapi()
    _write_json(CONTRACT_DIR / "openapi.json", openapi)

    _write_json(SCHEMA_DIR / "session_event_envelope.schema.json", _event_schema())
    _write_json(SCHEMA_DIR / "session_create_request.schema.json", _model_schema(SessionCreateRequest))
    _write_json(SCHEMA_DIR / "session_input_request.schema.json", _model_schema(SessionInputRequest))
    _write_json(SCHEMA_DIR / "session_command_request.schema.json", _model_schema(SessionCommandRequest))
    _write_json(SCHEMA_DIR / "session_event_payload_tool_call.schema.json", _payload_schema_tool_call())
    _write_json(SCHEMA_DIR / "session_event_payload_tool_result.schema.json", _payload_schema_tool_result())
    _write_json(
        SCHEMA_DIR / "session_event_payload_permission_request.schema.json",
        _payload_schema_permission_request(),
    )
    _write_json(
        SCHEMA_DIR / "session_event_payload_permission_response.schema.json",
        _payload_schema_permission_response(),
    )

    print(f"Wrote OpenAPI contract to {CONTRACT_DIR / 'openapi.json'}")
    print(f"Wrote schemas to {SCHEMA_DIR}")


if __name__ == "__main__":
    main()
