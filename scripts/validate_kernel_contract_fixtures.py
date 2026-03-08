from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
EXAMPLES_DIR = ROOT / "contracts" / "kernel" / "examples"
FIXTURE_DIR = ROOT / "conformance" / "engine_fixtures"
MANIFEST_PATH = FIXTURE_DIR / "python_reference_manifest_v1.json"

EXAMPLE_SCHEMA_MAP = {
    "kernel_event_minimal.json": "bb.kernel_event.v1.schema.json",
    "session_transcript_minimal.json": "bb.session_transcript.v1.schema.json",
    "tool_call_minimal.json": "bb.tool_call.v1.schema.json",
    "run_request_minimal.json": "bb.run_request.v1.schema.json",
    "provider_exchange_minimal.json": "bb.provider_exchange.v1.schema.json",
    "permission_minimal.json": "bb.permission.v1.schema.json",
    "replay_session_minimal.json": "bb.replay_session.v1.schema.json",
    "task_minimal.json": "bb.task.v1.schema.json",
    "checkpoint_metadata_minimal.json": "bb.checkpoint_metadata.v1.schema.json",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_examples() -> List[str]:
    errors: List[str] = []
    for example_name, schema_name in EXAMPLE_SCHEMA_MAP.items():
        example_path = EXAMPLES_DIR / example_name
        schema_path = SCHEMA_DIR / schema_name
        example = _load_json(example_path)
        schema = _load_json(schema_path)
        validator = Draft202012Validator(schema)
        for err in validator.iter_errors(example):
            errors.append(f"example {example_name} failed {schema_name}: {err.message}")
    return errors


def _validate_manifest_and_fixtures() -> List[str]:
    errors: List[str] = []
    manifest_schema = _load_json(ROOT / "contracts" / "kernel" / "manifests" / "bb.engine_conformance_manifest.v1.schema.json")
    manifest = _load_json(MANIFEST_PATH)
    validator = Draft202012Validator(manifest_schema)
    for err in validator.iter_errors(manifest):
        errors.append(f"manifest invalid: {err.message}")
    for row in manifest.get("rows", []):
        for rel in row.get("evidence", []):
            target = FIXTURE_DIR / rel
            if not target.is_file():
                errors.append(f"manifest missing evidence file: {rel}")
                continue
            fixture = _load_json(target)
            support_tier = fixture.get("support_tier")
            if support_tier is not None and support_tier not in {"draft-shape", "draft-semantic", "reference-engine"}:
                errors.append(f"fixture invalid support tier: {target} -> {support_tier}")
            example_ref = fixture.get("example_ref")
            if example_ref:
                example_path = (target.parent / example_ref).resolve()
                try:
                    example_path.relative_to(ROOT)
                except Exception:
                    errors.append(f"fixture example ref escapes repo: {target}")
                    continue
                if not example_path.is_file():
                    errors.append(f"fixture missing example ref target: {target} -> {example_ref}")
            contract = fixture.get("contract")
            reference_output = fixture.get("reference_output")
            if isinstance(contract, str) and reference_output is not None:
                schema_path = SCHEMA_DIR / f"{contract}.schema.json"
                if not schema_path.is_file():
                    errors.append(f"fixture references missing contract schema: {target} -> {contract}")
                else:
                    schema = _load_json(schema_path)
                    validator = Draft202012Validator(schema)
                    for err in validator.iter_errors(reference_output):
                        errors.append(f"fixture {target.name} failed {contract}: {err.message}")
    return errors


def validate_kernel_contract_fixtures() -> List[str]:
    return _validate_examples() + _validate_manifest_and_fixtures()


if __name__ == "__main__":
    errs = validate_kernel_contract_fixtures()
    if errs:
        for err in errs:
            print(err)
        raise SystemExit(1)
    print("ok")
