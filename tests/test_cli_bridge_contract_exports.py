from __future__ import annotations

import json
from pathlib import Path


def test_cli_bridge_contract_files_present() -> None:
    root = Path(__file__).resolve().parents[1]
    contract_dir = root / "docs" / "contracts" / "cli_bridge"
    schema_dir = contract_dir / "schemas"

    openapi_path = contract_dir / "openapi.json"
    assert openapi_path.is_file(), "Expected exported OpenAPI contract at docs/contracts/cli_bridge/openapi.json"

    expected_schemas = [
        "session_event_envelope.schema.json",
        "session_create_request.schema.json",
        "session_input_request.schema.json",
        "session_command_request.schema.json",
        "session_command_request_v1.schema.json",
        "session_event_payload_turn_start.schema.json",
        "session_event_payload_assistant_message.schema.json",
        "session_event_payload_user_message.schema.json",
        "session_event_payload_tool_call.schema.json",
        "session_event_payload_tool_result.schema.json",
        "session_event_payload_todo_update.schema.json",
        "session_event_payload_permission_request.schema.json",
        "session_event_payload_permission_response.schema.json",
        "session_event_payload_checkpoint_list.schema.json",
        "session_event_payload_checkpoint_restored.schema.json",
        "session_event_payload_skills_catalog.schema.json",
        "session_event_payload_skills_selection.schema.json",
        "session_event_payload_ctree_node.schema.json",
        "session_event_payload_ctree_snapshot.schema.json",
        "session_event_payload_task_event.schema.json",
        "session_event_payload_reward_update.schema.json",
        "session_event_payload_limits_update.schema.json",
        "session_event_payload_completion.schema.json",
        "session_event_payload_log_link.schema.json",
        "session_event_payload_error.schema.json",
        "session_event_payload_run_finished.schema.json",
        "provider_auth_attach_request.schema.json",
        "provider_auth_attach_response.schema.json",
        "provider_auth_detach_request.schema.json",
        "provider_auth_detach_response.schema.json",
        "provider_auth_status_response.schema.json",
    ]

    missing = [name for name in expected_schemas if not (schema_dir / name).is_file()]
    assert not missing, f"Missing exported schema files: {missing}"

    json.loads(openapi_path.read_text(encoding="utf-8"))
    for name in expected_schemas:
        json.loads((schema_dir / name).read_text(encoding="utf-8"))


def test_session_event_schema_variants_cover_all_event_types() -> None:
    root = Path(__file__).resolve().parents[1]
    schema_dir = root / "docs" / "contracts" / "cli_bridge" / "schemas"
    envelope = json.loads((schema_dir / "session_event_envelope.schema.json").read_text(encoding="utf-8"))
    variants = envelope.get("oneOf") or []
    event_types = sorted({variant.get("properties", {}).get("type", {}).get("const") for variant in variants})
    event_types = [value for value in event_types if value]
    assert event_types, "Expected session_event_envelope.schema.json to include oneOf variants."
    assert len(event_types) == len(set(event_types)), "Duplicate event types in schema variants."
    expected = [
        "turn_start",
        "assistant_message",
        "user_message",
        "tool_call",
        "tool_result",
        "permission_request",
        "permission_response",
        "checkpoint_list",
        "checkpoint_restored",
        "skills_catalog",
        "skills_selection",
        "ctree_node",
        "ctree_snapshot",
        "task_event",
        "reward_update",
        "limits_update",
        "completion",
        "log_link",
        "error",
        "run_finished",
    ]
    missing = [value for value in expected if value not in event_types]
    assert not missing, f"Missing event schema variants for: {missing}"


def test_session_event_payloads_validate_minimal_samples() -> None:
    from jsonschema import Draft202012Validator
    from jsonschema import RefResolver

    root = Path(__file__).resolve().parents[1]
    schema_dir = root / "docs" / "contracts" / "cli_bridge" / "schemas"
    envelope = json.loads((schema_dir / "session_event_envelope.schema.json").read_text(encoding="utf-8"))
    resolver = RefResolver(base_uri=schema_dir.resolve().as_uri() + "/", referrer=envelope)
    validator = Draft202012Validator(envelope, resolver=resolver)

    samples = {
        "turn_start": {},
        "assistant_message": {"text": "hello"},
        "user_message": {"text": "hi"},
        "tool_call": {"call": {}, "call_id": "call-1", "tool": "read_file"},
        "tool_result": {"status": "ok", "error": False},
        "permission_request": {"request_id": "req-1", "tool": "read_file", "kind": "tool"},
        "permission_response": {"request_id": "req-1"},
        "checkpoint_list": {"checkpoints": []},
        "checkpoint_restored": {"checkpoint_id": "cp-1"},
        "skills_catalog": {},
        "skills_selection": {"selection": {}},
        "ctree_node": {"node": {}, "snapshot": {}},
        "ctree_snapshot": {},
        "task_event": {"kind": "step"},
        "reward_update": {"summary": {}},
        "limits_update": {"provider": "openai", "observed_at_ms": 0, "buckets": []},
        "completion": {"summary": {}},
        "log_link": {"url": "https://example.com/log"},
        "error": {"message": "error"},
        "run_finished": {"eventCount": 0},
    }

    base_fields = {
        "id": "evt-1",
        "session_id": "sess-1",
        "timestamp": 0,
        "timestamp_ms": 0,
        "protocol_version": "1.0",
    }

    for event_type, payload in samples.items():
        sample = dict(base_fields)
        sample["type"] = event_type
        sample["payload"] = payload
        errors = list(validator.iter_errors(sample))
        assert not errors, f"Schema validation failed for {event_type}: {errors}"
