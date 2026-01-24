from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def _load_schema(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "$id" not in payload:
        payload = dict(payload)
        payload["$id"] = path.resolve().as_uri()
    return payload


def _build_registry(schema_dir: Path) -> Registry:
    registry = Registry()
    for path in schema_dir.glob("*.json"):
        schema = _load_schema(path)
        registry = registry.with_resource(path.resolve().as_uri(), Resource.from_contents(schema, DRAFT202012))
    return registry


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
        "session_event_payload_completion.schema.json",
        "session_event_payload_log_link.schema.json",
        "session_event_payload_error.schema.json",
        "session_event_payload_run_finished.schema.json",
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
        "completion",
        "log_link",
        "error",
        "run_finished",
    ]
    missing = [value for value in expected if value not in event_types]
    assert not missing, f"Missing event schema variants for: {missing}"


def test_session_event_payloads_validate_minimal_samples() -> None:
    from jsonschema import Draft202012Validator

    root = Path(__file__).resolve().parents[1]
    schema_dir = root / "docs" / "contracts" / "cli_bridge" / "schemas"
    registry = _build_registry(schema_dir)
    envelope = _load_schema(schema_dir / "session_event_envelope.schema.json")
    validator = Draft202012Validator(envelope, registry=registry)

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
        "ctree_node": {
            "node": {"id": "ctn-1", "digest": "d" * 40, "kind": "message", "turn": 1, "payload": {}},
            "snapshot": {
                "schema_version": "0.1",
                "node_count": 1,
                "event_count": 1,
                "last_id": "ctn-1",
                "node_hash": None,
            },
        },
        "ctree_snapshot": {"snapshot": {}},
        "task_event": {"kind": "step"},
        "reward_update": {"summary": {}},
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


def test_ctree_payloads_validate_realistic_snapshot() -> None:
    from jsonschema import Draft202012Validator

    from agentic_coder_prototype.ctrees.store import CTreeStore
    from agentic_coder_prototype.ctrees.summary import build_ctree_hash_summary

    root = Path(__file__).resolve().parents[1]
    schema_dir = root / "docs" / "contracts" / "cli_bridge" / "schemas"
    registry = _build_registry(schema_dir)
    envelope = _load_schema(schema_dir / "session_event_envelope.schema.json")
    validator = Draft202012Validator(envelope, registry=registry)

    store = CTreeStore()
    store.record("message", {"role": "user", "content": "hi"}, turn=1)
    node = store.nodes[-1]
    snapshot = store.snapshot()
    hash_summary = build_ctree_hash_summary(snapshot=snapshot, compiler=None, collapse=None, runner=None)

    base_fields = {
        "id": "evt-ctree",
        "session_id": "sess-ctree",
        "timestamp": 0,
        "timestamp_ms": 0,
        "protocol_version": "1.0",
    }

    node_event = dict(base_fields)
    node_event["type"] = "ctree_node"
    node_event["payload"] = {"node": node, "snapshot": snapshot}
    assert not list(validator.iter_errors(node_event))

    snap_event = dict(base_fields)
    snap_event["type"] = "ctree_snapshot"
    snap_event["payload"] = {"snapshot": snapshot, "hash_summary": hash_summary}
    assert not list(validator.iter_errors(snap_event))
