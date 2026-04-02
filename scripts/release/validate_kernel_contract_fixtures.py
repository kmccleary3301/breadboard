from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from jsonschema import Draft202012Validator, RefResolver

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
EXAMPLES_DIR = ROOT / "contracts" / "kernel" / "examples"
FIXTURE_DIR = ROOT / "conformance" / "engine_fixtures"
MANIFEST_PATH = FIXTURE_DIR / "python_reference_manifest_v1.json"

EXAMPLE_SCHEMA_MAP = {
    "kernel_event_minimal.json": "bb.kernel_event.v1.schema.json",
    "signal_complete_minimal.json": "bb.signal.v1.schema.json",
    "signal_blocked_minimal.json": "bb.signal.v1.schema.json",
    "review_verdict_complete_validated.json": "bb.review_verdict.v1.schema.json",
    "review_verdict_blocked_retry.json": "bb.review_verdict.v1.schema.json",
    "directive_retry_minimal.json": "bb.directive.v1.schema.json",
    "wake_subscription_minimal.json": "bb.wake_subscription.v1.schema.json",
    "session_transcript_minimal.json": "bb.session_transcript.v1.schema.json",
    "tool_call_minimal.json": "bb.tool_call.v1.schema.json",
    "run_request_minimal.json": "bb.run_request.v1.schema.json",
    "run_context_minimal.json": "bb.run_context.v1.schema.json",
    "provider_exchange_minimal.json": "bb.provider_exchange.v1.schema.json",
    "permission_minimal.json": "bb.permission.v1.schema.json",
    "execution_capability_minimal.json": "bb.execution_capability.v1.schema.json",
    "execution_placement_minimal.json": "bb.execution_placement.v1.schema.json",
    "sandbox_request_minimal.json": "bb.sandbox_request.v1.schema.json",
    "sandbox_result_minimal.json": "bb.sandbox_result.v1.schema.json",
    "distributed_task_descriptor_minimal.json": "bb.distributed_task_descriptor.v1.schema.json",
    "transcript_continuation_patch_minimal.json": "bb.transcript_continuation_patch.v1.schema.json",
    "unsupported_case_minimal.json": "bb.unsupported_case.v1.schema.json",
    "replay_session_minimal.json": "bb.replay_session.v1.schema.json",
    "task_minimal.json": "bb.task.v1.schema.json",
    "checkpoint_metadata_minimal.json": "bb.checkpoint_metadata.v1.schema.json",
    "tool_spec_minimal.json": "bb.tool_spec.v1.schema.json",
    "environment_selector_minimal.json": "bb.environment_selector.v1.schema.json",
    "tool_binding_minimal.json": "bb.tool_binding.v1.schema.json",
    "tool_support_claim_minimal.json": "bb.tool_support_claim.v1.schema.json",
    "effective_tool_surface_minimal.json": "bb.effective_tool_surface.v1.schema.json",
    "tool_execution_outcome_minimal.json": "bb.tool_execution_outcome.v1.schema.json",
    "tool_model_render_minimal.json": "bb.tool_model_render.v1.schema.json",
    "terminal_session_descriptor_minimal.json": "bb.terminal_session_descriptor.v1.schema.json",
    "terminal_output_delta_minimal.json": "bb.terminal_output_delta.v1.schema.json",
    "terminal_interaction_minimal.json": "bb.terminal_interaction.v1.schema.json",
    "terminal_session_end_minimal.json": "bb.terminal_session_end.v1.schema.json",
    "terminal_registry_snapshot_minimal.json": "bb.terminal_registry_snapshot.v1.schema.json",
    "terminal_cleanup_result_minimal.json": "bb.terminal_cleanup_result.v1.schema.json",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _build_schema_store() -> Dict[str, object]:
    return {path.name: _load_json(path) for path in SCHEMA_DIR.glob("*.json")}


def _validate_examples() -> List[str]:
    errors: List[str] = []
    schema_store = _build_schema_store()
    for example_name, schema_name in EXAMPLE_SCHEMA_MAP.items():
        example_path = EXAMPLES_DIR / example_name
        schema_path = SCHEMA_DIR / schema_name
        example = _load_json(example_path)
        schema = schema_store[schema_path.name]
        resolver = RefResolver.from_schema(schema, store=schema_store)
        validator = Draft202012Validator(schema, resolver=resolver)
        for err in validator.iter_errors(example):
            errors.append(f"example {example_name} failed {schema_name}: {err.message}")
    return errors


def _validate_manifest_and_fixtures() -> List[str]:
    errors: List[str] = []
    schema_store = _build_schema_store()
    valid_support_tiers = {"draft-shape", "draft-semantic", "reference-engine"}
    valid_comparator_classes = {
        "shape-equal",
        "normalized-trace-equal",
        "model-visible-equal",
        "workspace-side-effects-equal",
        "projection-equal",
    }
    manifest_schema = _load_json(ROOT / "contracts" / "kernel" / "manifests" / "bb.engine_conformance_manifest.v1.schema.json")
    manifest = _load_json(MANIFEST_PATH)
    validator = Draft202012Validator(manifest_schema)
    for err in validator.iter_errors(manifest):
        errors.append(f"manifest invalid: {err.message}")
    for row in manifest.get("rows", []):
        comparator = row.get("comparatorClass")
        if comparator not in valid_comparator_classes:
            errors.append(f"manifest invalid comparator class: {row.get('scenarioId')} -> {comparator}")
        for rel in row.get("evidence", []):
            target = FIXTURE_DIR / rel
            if not target.is_file():
                errors.append(f"manifest missing evidence file: {rel}")
                continue
            fixture = _load_json(target)
            support_tier = fixture.get("support_tier")
            if support_tier is not None and support_tier not in valid_support_tiers:
                errors.append(f"fixture invalid support tier: {target} -> {support_tier}")
            comparator_class = fixture.get("comparator_class")
            if comparator_class is not None and comparator_class not in valid_comparator_classes:
                errors.append(f"fixture invalid comparator class: {target} -> {comparator_class}")
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
                    schema = schema_store[schema_path.name]
                    resolver = RefResolver.from_schema(schema, store=schema_store)
                    validator = Draft202012Validator(schema, resolver=resolver)
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
