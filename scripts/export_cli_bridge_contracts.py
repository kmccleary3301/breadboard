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
        # SessionRunner normalizes tool-call payloads to include these keys.
        "required": ["call", "call_id", "tool"],
        "properties": {
            "call": {"type": "object"},
            "call_id": {"type": ["string", "null"]},
            "tool": {"type": ["string", "null"]},
            "action": {"type": ["string", "null"]},
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
        # We always expose status/error for UI rendering; call_id may be missing in edge cases.
        "required": ["status", "error"],
        "properties": {
            "call_id": {"type": ["string", "null"]},
            "tool": {"type": ["string", "null"]},
            "success": {"type": ["boolean", "null"]},
            "metadata": {"type": ["object", "null"]},
            "status": {"type": ["string", "null"]},
            "error": {"type": "boolean"},
            "result": {},
            "message": {},
        },
    }


def _payload_schema_turn_start() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "TurnStartPayload",
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "turn": {"type": ["integer", "null"]},
            "mode": {"type": ["string", "null"]},
        },
    }


def _payload_schema_assistant_message() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AssistantMessagePayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "message": {},
            "source": {"type": ["string", "null"]},
        },
    }


def _payload_schema_user_message() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "UserMessagePayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "message": {},
            "source": {"type": ["string", "null"]},
        },
    }


def _payload_schema_checkpoint_list() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "CheckpointListPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["checkpoints"],
        "properties": {
            "checkpoints": {"type": "array", "items": {"type": "object"}},
        },
    }


def _payload_schema_checkpoint_restored() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "CheckpointRestoredPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["checkpoint_id"],
        "properties": {
            "checkpoint_id": {"type": "string"},
            "mode": {"type": ["string", "null"]},
            "prune": {"type": ["boolean", "null"]},
        },
    }


def _payload_schema_skills_catalog() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "SkillsCatalogPayload",
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "catalog": {"type": ["object", "null"]},
            "selection": {"type": ["object", "null"]},
            "sources": {"type": ["object", "null"]},
        },
    }


def _payload_schema_skills_selection() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "SkillsSelectionPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["selection"],
        "properties": {"selection": {"type": "object"}},
    }


def _payload_schema_ctree_node() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "CTreeNodePayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["node", "snapshot"],
        "properties": {
            "node": {"type": "object"},
            "snapshot": {"type": "object"},
        },
    }


def _payload_schema_ctree_snapshot() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "CTreeSnapshotPayload",
        "type": "object",
        "additionalProperties": True,
    }


def _payload_schema_task_event() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "TaskEventPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["kind"],
        "properties": {
            "kind": {"type": "string"},
            "task_id": {"type": ["string", "integer", "null"]},
            "description": {"type": ["string", "null"]},
            "status": {"type": ["string", "null"]},
            "timestamp": {"type": ["number", "null"]},
            "tree_path": {"type": ["string", "null"]},
            "depth": {"type": ["integer", "null"]},
        },
    }


def _payload_schema_reward_update() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RewardUpdatePayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["summary"],
        "properties": {"summary": {"type": "object"}},
    }


def _payload_schema_completion() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "CompletionPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["summary"],
        "properties": {
            "summary": {"type": "object"},
            "mode": {"type": ["string", "null"]},
            "usage": {"type": ["object", "null"]},
        },
    }


def _payload_schema_log_link() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "LogLinkPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["url"],
        "properties": {"url": {"type": "string"}},
    }


def _payload_schema_error() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ErrorPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["message"],
        "properties": {"message": {"type": "string"}},
    }


def _payload_schema_run_finished() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RunFinishedPayload",
        "type": "object",
        "additionalProperties": True,
        "required": ["eventCount"],
        "properties": {
            "eventCount": {"type": "integer"},
            "steps": {"type": ["integer", "null"]},
            "completed": {"type": ["boolean", "null"]},
            "reason": {"type": ["string", "null"]},
            "logging_dir": {"type": ["string", "null"]},
            "usage": {"type": ["object", "null"]},
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

def _command_schema_v1() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "SessionCommandRequestV1",
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": [
                    "list_checkpoints",
                    "restore_checkpoint",
                    "permission_decision",
                    "set_skills",
                    "stop",
                    "set_model",
                    "set_mode",
                    "respond_permission",
                    "permission_response",
                ],
            },
            "payload": {"type": ["object", "null"]},
        },
        "oneOf": [
            {
                "title": "ListCheckpoints",
                "properties": {
                    "command": {"const": "list_checkpoints"},
                    "payload": {"type": ["object", "null"]},
                },
            },
            {
                "title": "RestoreCheckpoint",
                "properties": {
                    "command": {"const": "restore_checkpoint"},
                    "payload": {
                        "type": "object",
                        "additionalProperties": True,
                        "required": ["checkpoint_id"],
                        "properties": {
                            "checkpoint_id": {"type": "string"},
                            "checkpointId": {"type": "string"},
                            "id": {"type": "string"},
                            "mode": {"type": "string", "enum": ["code", "conversation", "both"]},
                        },
                    },
                },
            },
            {
                "title": "PermissionDecision",
                "properties": {
                    "command": {"const": "permission_decision"},
                    "payload": {
                        "type": "object",
                        "additionalProperties": True,
                        "required": ["request_id", "decision"],
                        "properties": {
                            "request_id": {"type": "string"},
                            "requestId": {"type": "string"},
                            "permission_id": {"type": "string"},
                            "permissionId": {"type": "string"},
                            "id": {"type": "string"},
                            "decision": {"type": "string"},
                            "response": {"type": "string"},
                            "rule": {"type": ["string", "null"]},
                            "scope": {"type": ["string", "null"]},
                            "note": {"type": ["string", "null"]},
                            "stop": {"type": ["boolean", "null"]},
                        },
                    },
                },
            },
            {
                "title": "SetSkills",
                "properties": {
                    "command": {"const": "set_skills"},
                    "payload": {"type": "object", "additionalProperties": True},
                },
            },
            {
                "title": "Stop",
                "properties": {"command": {"const": "stop"}, "payload": {"type": ["object", "null"]}},
            },
            {
                "title": "SetModel",
                "properties": {
                    "command": {"const": "set_model"},
                    "payload": {
                        "type": "object",
                        "additionalProperties": True,
                        "required": ["model"],
                        "properties": {"model": {"type": "string"}},
                    },
                },
            },
            {
                "title": "SetMode",
                "properties": {
                    "command": {"const": "set_mode"},
                    "payload": {
                        "type": "object",
                        "additionalProperties": True,
                        "required": ["mode"],
                        "properties": {"mode": {"type": "string"}},
                    },
                },
            },
            {
                "title": "RespondPermission",
                "properties": {
                    "command": {"enum": ["respond_permission", "permission_response"]},
                    "payload": {
                        "type": "object",
                        "additionalProperties": True,
                        "required": ["request_id"],
                        "properties": {
                            "request_id": {"type": "string"},
                            "requestId": {"type": "string"},
                            "permission_id": {"type": "string"},
                            "permissionId": {"type": "string"},
                            "id": {"type": "string"},
                            "response": {"type": ["string", "null"]},
                            "decision": {"type": ["string", "null"]},
                            "responses": {"type": ["object", "null"]},
                            "items": {"type": ["object", "null"]},
                        },
                    },
                },
            },
        ],
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
    _write_json(SCHEMA_DIR / "session_command_request_v1.schema.json", _command_schema_v1())
    _write_json(SCHEMA_DIR / "session_event_payload_turn_start.schema.json", _payload_schema_turn_start())
    _write_json(
        SCHEMA_DIR / "session_event_payload_assistant_message.schema.json",
        _payload_schema_assistant_message(),
    )
    _write_json(SCHEMA_DIR / "session_event_payload_user_message.schema.json", _payload_schema_user_message())
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
    _write_json(SCHEMA_DIR / "session_event_payload_checkpoint_list.schema.json", _payload_schema_checkpoint_list())
    _write_json(
        SCHEMA_DIR / "session_event_payload_checkpoint_restored.schema.json",
        _payload_schema_checkpoint_restored(),
    )
    _write_json(SCHEMA_DIR / "session_event_payload_skills_catalog.schema.json", _payload_schema_skills_catalog())
    _write_json(
        SCHEMA_DIR / "session_event_payload_skills_selection.schema.json",
        _payload_schema_skills_selection(),
    )
    _write_json(SCHEMA_DIR / "session_event_payload_ctree_node.schema.json", _payload_schema_ctree_node())
    _write_json(SCHEMA_DIR / "session_event_payload_ctree_snapshot.schema.json", _payload_schema_ctree_snapshot())
    _write_json(SCHEMA_DIR / "session_event_payload_task_event.schema.json", _payload_schema_task_event())
    _write_json(SCHEMA_DIR / "session_event_payload_reward_update.schema.json", _payload_schema_reward_update())
    _write_json(SCHEMA_DIR / "session_event_payload_completion.schema.json", _payload_schema_completion())
    _write_json(SCHEMA_DIR / "session_event_payload_log_link.schema.json", _payload_schema_log_link())
    _write_json(SCHEMA_DIR / "session_event_payload_error.schema.json", _payload_schema_error())
    _write_json(SCHEMA_DIR / "session_event_payload_run_finished.schema.json", _payload_schema_run_finished())

    print(f"Wrote OpenAPI contract to {CONTRACT_DIR / 'openapi.json'}")
    print(f"Wrote schemas to {SCHEMA_DIR}")


if __name__ == "__main__":
    main()
