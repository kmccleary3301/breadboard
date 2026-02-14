"""Convert Import IR JSON into CLI bridge event stream JSONL.

This is intended as a deterministic, engine-owned conversion tool used by TUI-side
importers (filesystem, harness export) to produce the canonical stream artifacts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from jsonschema import Draft202012Validator

from breadboard_sdk.import_ir import import_ir_to_cli_bridge_events, write_events_jsonl


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to Import IR JSON")
    parser.add_argument("--output", required=True, help="Path to output events JSONL")
    parser.add_argument("--session-id", default="imported-session", help="Session id to embed in event envelopes")
    parser.add_argument("--protocol-version", default="1.0", help="CLI bridge protocol_version to embed")
    parser.add_argument("--no-validate", action="store_true", help="Skip jsonschema validation of the input IR")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    input_path = Path(args.input)
    output_path = Path(args.output)

    import_ir = _load_json(input_path)
    if not args.no_validate:
        schema = _load_json(root / "docs" / "contracts" / "import" / "import_ir_v1.schema.json")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        errors = list(validator.iter_errors(import_ir))
        if errors:
            raise SystemExit(f"Import IR failed schema validation: {errors}")

    events = import_ir_to_cli_bridge_events(
        import_ir,
        session_id=str(args.session_id),
        protocol_version=str(args.protocol_version),
        base_timestamp_ms=int(import_ir.get("created_at_ms") or 0),
    )
    write_events_jsonl(events, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

