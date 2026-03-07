#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_COMPONENTS: dict[str, str] = {
    "proof_plan_ir": "atp_proof_plan_ir_v1.schema.json",
    "retrieval_request": "retrieval_adapter_request_v1.schema.json",
    "retrieval_response": "retrieval_adapter_response_v1.schema.json",
    "specialist_solver_request": "specialist_solver_request_v1.schema.json",
    "specialist_solver_response": "specialist_solver_response_v1.schema.json",
    "specialist_solver_trace": "specialist_solver_trace_v1.schema.json",
    "decomposition_event": "search_loop_decomposition_node_event_v1.schema.json",
    "decomposition_trace": "search_loop_decomposition_trace_v1.schema.json",
}


def _load_validator(schema_path: Path) -> Draft202012Validator:
    schema_obj = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema_obj)
    return Draft202012Validator(schema_obj)


def _validate_component(
    *,
    validators: dict[str, Draft202012Validator],
    fixture: dict[str, Any],
) -> list[str]:
    errors: list[str] = []

    required_single = [
        "proof_plan_ir",
        "retrieval_request",
        "retrieval_response",
        "specialist_solver_request",
        "specialist_solver_response",
        "specialist_solver_trace",
        "decomposition_trace",
    ]
    for key in required_single:
        payload = fixture.get(key)
        if payload is None:
            errors.append(f"missing component: {key}")
            continue
        for err in validators[key].iter_errors(payload):
            msg = f"{key}: {err.message}"
            if err.path:
                msg = f"{key}.{'.'.join(str(part) for part in err.path)}: {err.message}"
            errors.append(msg)

    events = fixture.get("decomposition_events")
    if events is None:
        errors.append("missing component: decomposition_events")
    elif not isinstance(events, list):
        errors.append("decomposition_events must be an array")
    else:
        for index, event in enumerate(events):
            for err in validators["decomposition_event"].iter_errors(event):
                msg = f"decomposition_events[{index}]: {err.message}"
                if err.path:
                    msg = f"decomposition_events[{index}].{'.'.join(str(part) for part in err.path)}: {err.message}"
                errors.append(msg)

    return errors


def validate_fixture(*, fixture_path: Path, schema_dir: Path) -> dict[str, Any]:
    validators = {key: _load_validator(schema_dir / rel) for key, rel in SCHEMA_COMPONENTS.items()}
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors = _validate_component(validators=validators, fixture=fixture)
    return {
        "schema_version": "atp_capability_contract_validation_v1",
        "fixture": str(fixture_path.resolve()),
        "schema_dir": str(schema_dir.resolve()),
        "validated_components": sorted(SCHEMA_COMPONENTS.keys()),
        "error_count": len(errors),
        "errors": errors,
        "ok": not errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/atp_capabilities/retrieval_solver_replay_fixture_v1.json",
        help="Fixture payload for ATP retrieval+solver contract checks.",
    )
    parser.add_argument(
        "--schema-dir",
        default="docs/contracts/atp/schemas",
        help="Directory containing ATP contract schemas.",
    )
    parser.add_argument("--json-out", help="Optional path to write validation report JSON.")
    args = parser.parse_args()

    result = validate_fixture(fixture_path=Path(args.fixture), schema_dir=Path(args.schema_dir))
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
