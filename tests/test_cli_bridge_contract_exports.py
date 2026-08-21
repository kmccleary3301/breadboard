from __future__ import annotations

import ast

import re
import json
from pathlib import Path


SESSION_FILE_DTO_FIELDS = {
    "SessionFileInfo": {
        "path": {"optional": False, "ts_type": "string"},
        "type": {"optional": False, "ts_type": '"file" | "directory"'},
        "size": {"optional": True, "ts_type": "number"},
        "updated_at": {"optional": True, "ts_type": "string"},
    },
    "SessionFileContent": {
        "path": {"optional": False, "ts_type": "string"},
        "content": {"optional": False, "ts_type": "string"},
        "truncated": {"optional": True, "ts_type": "boolean"},
        "total_bytes": {"optional": True, "ts_type": "number"},
    },
}


def _python_bridge_model_fields(models_path: Path) -> dict[str, dict[str, dict[str, object]]]:
    tree = ast.parse(models_path.read_text(encoding="utf-8"))
    definitions: dict[str, int] = {name: 0 for name in SESSION_FILE_DTO_FIELDS}
    fields_by_model: dict[str, dict[str, dict[str, object]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in SESSION_FILE_DTO_FIELDS:
            continue
        definitions[node.name] += 1
        fields: dict[str, dict[str, object]] = {}
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                fields[statement.target.id] = {
                    "optional": _python_field_is_optional(statement),
                    "ts_type": _python_annotation_ts_type(statement.annotation),
                }
        fields_by_model[node.name] = fields
    duplicates = {name: count for name, count in definitions.items() if count != 1}
    assert not duplicates, f"Expected exactly one Python DTO class definition per contract: {duplicates}"
    return fields_by_model



def _python_field_is_optional(statement: ast.AnnAssign) -> bool:
    value = statement.value
    if value is None:
        return False
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Field":
        if value.args and isinstance(value.args[0], ast.Constant) and value.args[0].value is Ellipsis:
            return False
        for keyword in value.keywords:
            if keyword.arg == "default" and isinstance(keyword.value, ast.Constant) and keyword.value.value is Ellipsis:
                return False
    return True


def _python_annotation_ts_type(annotation: ast.expr) -> str:
    if isinstance(annotation, ast.Name):
        mapping = {"str": "string", "int": "number", "bool": "boolean"}
        if annotation.id in mapping:
            return mapping[annotation.id]
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        if annotation.value.id == "Optional":
            return _python_annotation_ts_type(annotation.slice)
        if annotation.value.id == "Literal":
            values = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
            return " | ".join(json.dumps(value.value) for value in values if isinstance(value, ast.Constant))
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        if isinstance(annotation.right, ast.Constant) and annotation.right.value is None:
            return _python_annotation_ts_type(annotation.left)
        if isinstance(annotation.left, ast.Constant) and annotation.left.value is None:
            return _python_annotation_ts_type(annotation.right)
    raise AssertionError(f"Unsupported Python DTO annotation: {ast.unparse(annotation)}")


def _ts_export_fields(types_path: Path, export_name: str) -> dict[str, dict[str, object]]:
    text = types_path.read_text(encoding="utf-8")
    match = re.search(
        rf"export (?:interface {export_name}|type {export_name} =) \{{(?P<body>.*?)\n\}}",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        alias_match = re.search(rf"export type {export_name} = (?P<alias>[A-Za-z0-9_]+)", text)
        assert alias_match, f"Missing {export_name} in {types_path}"
        alias = alias_match.group("alias")
        for import_match in re.finditer(
            r'import type \{(?P<body>.*?)\} from "(?P<source>[^"]+)"',
            text,
            flags=re.DOTALL,
        ):
            imported = re.search(rf"\b(?P<name>[A-Za-z0-9_]+) as {alias}\b", import_match.group("body"))
            if imported is None:
                continue
            source = import_match.group("source")
            generated_path = types_path.parent / f"{source.removesuffix('.js')}.ts"
            return _ts_export_fields(generated_path, imported.group("name"))
        raise AssertionError(f"Unresolved {export_name} alias {alias} in {types_path}")

    fields: dict[str, dict[str, object]] = {}
    for raw_line in match.group("body").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        prop = re.match(
            r'readonly "?(?P<name>[A-Za-z0-9_]+)"?(?P<optional>\?)?: (?P<type>.+?);?$',
            line,
        )
        assert prop, f"Unsupported {export_name} property syntax in {types_path}: {raw_line!r}"
        fields[prop.group("name")] = {
            "optional": bool(prop.group("optional")),
            "ts_type": prop.group("type").strip().removesuffix(" | null"),
        }
    return fields


def test_session_file_dtos_do_not_drift_between_python_and_ts_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    python_fields = _python_bridge_model_fields(root / "agentic_coder_prototype" / "api" / "cli_bridge" / "models.py")
    for model_name, expected_fields in SESSION_FILE_DTO_FIELDS.items():
        assert python_fields[model_name] == expected_fields
        for types_path in (root / "sdk" / "ts" / "src" / "types.ts", root / "tui_skeleton" / "src" / "api" / "types.ts"):
            assert _ts_export_fields(types_path, model_name) == expected_fields


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
