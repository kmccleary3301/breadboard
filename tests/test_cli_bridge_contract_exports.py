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

