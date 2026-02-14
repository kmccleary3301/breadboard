from __future__ import annotations

import json
from pathlib import Path

from breadboard_sdk.import_ir import import_ir_to_cli_bridge_events
from jsonschema import Draft202012Validator, RefResolver


def test_import_ir_to_events_jsonl_matches_event_envelope_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    import_ir = json.loads(
        (root / "tests/fixtures/contracts/import_ir/sample_import_ir_v1.json").read_text(encoding="utf-8")
    )
    events = import_ir_to_cli_bridge_events(import_ir, session_id="sess-import", protocol_version="1.0")
    assert events, "Expected at least one converted event"

    schema_dir = root / "docs/contracts/cli_bridge/schemas"
    envelope = json.loads((schema_dir / "session_event_envelope.schema.json").read_text(encoding="utf-8"))
    resolver = RefResolver(base_uri=schema_dir.resolve().as_uri() + "/", referrer=envelope)
    validator = Draft202012Validator(envelope, resolver=resolver)

    for evt in events:
        errors = list(validator.iter_errors(evt))
        assert not errors, f"Converted event failed validation: {errors}"


def test_import_ir_conversion_is_deterministic() -> None:
    root = Path(__file__).resolve().parents[1]
    import_ir = json.loads(
        (root / "tests/fixtures/contracts/import_ir/sample_import_ir_v1.json").read_text(encoding="utf-8")
    )
    events_a = import_ir_to_cli_bridge_events(import_ir, session_id="sess-import", protocol_version="1.0")
    events_b = import_ir_to_cli_bridge_events(import_ir, session_id="sess-import", protocol_version="1.0")
    assert events_a == events_b

