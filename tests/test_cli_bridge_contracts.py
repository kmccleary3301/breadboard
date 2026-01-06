from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "docs" / "contracts" / "cli_bridge"
SCHEMA_DIR = CONTRACT_DIR / "schemas"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cli_bridge_openapi_contract_paths() -> None:
    openapi_path = CONTRACT_DIR / "openapi.json"
    assert openapi_path.exists(), "OpenAPI contract missing; run scripts/export_cli_bridge_contracts.py"
    payload = _load_json(openapi_path)
    paths = payload.get("paths", {})
    required_paths = [
        "/health",
        "/models",
        "/sessions",
        "/sessions/{session_id}",
        "/sessions/{session_id}/input",
        "/sessions/{session_id}/command",
        "/sessions/{session_id}/attachments",
        "/sessions/{session_id}/files",
        "/sessions/{session_id}/skills",
        "/sessions/{session_id}/ctrees",
        "/sessions/{session_id}/events",
        "/sessions/{session_id}/download",
    ]
    missing = [path for path in required_paths if path not in paths]
    assert not missing, f"OpenAPI missing required paths: {missing}"


def test_cli_bridge_event_envelope_schema() -> None:
    schema_path = SCHEMA_DIR / "session_event_envelope.schema.json"
    assert schema_path.exists(), "Session event envelope schema missing."
    schema = _load_json(schema_path)
    required = set(schema.get("required", []))
    expected = {"id", "type", "session_id", "payload", "timestamp", "timestamp_ms", "protocol_version"}
    assert expected.issubset(required), f"Event envelope missing required keys: {expected - required}"


def test_cli_bridge_request_schemas_present() -> None:
    for name in [
        "session_create_request.schema.json",
        "session_input_request.schema.json",
        "session_command_request.schema.json",
    ]:
        path = SCHEMA_DIR / name
        assert path.exists(), f"Missing {name}; run scripts/export_cli_bridge_contracts.py"


def test_cli_bridge_payload_schemas_present() -> None:
    for name in [
        "session_event_payload_tool_call.schema.json",
        "session_event_payload_tool_result.schema.json",
        "session_event_payload_permission_request.schema.json",
        "session_event_payload_permission_response.schema.json",
        "session_event_payload_ctree_snapshot.schema.json",
        "session_event_payload_ctree_node.schema.json",
        "session_event_payload_ctree_delta.schema.json",
    ]:
        path = SCHEMA_DIR / name
        assert path.exists(), f"Missing {name}; run scripts/export_cli_bridge_contracts.py"
