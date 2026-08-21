from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator, RefResolver

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
EXAMPLES_DIR = ROOT / "contracts" / "kernel" / "examples"
FIXTURE_DIR = ROOT / "conformance" / "engine_fixtures"
MANIFEST_PATH = FIXTURE_DIR / "python_reference_manifest_v1.json"
ATOMIC_FEATURE_LEDGER_SCHEMA = "bb.atomic_feature_ledger.v1.schema.json"
ATOMIC_FEATURE_LEDGER_CONTRACT = "bb.atomic_feature_ledger.v1"
ATOMIC_FEATURE_LEDGER_VALIDATOR_PATH = ROOT / "scripts" / "e4_parity" / "validate_atomic_feature_ledger.py"
TS_KERNEL_CONTRACT_INDEX = ROOT / "sdk" / "ts-kernel-contracts" / "src" / "index.ts"

_TS_SCHEMA_LOAD_RE = re.compile(r'const\s+(?P<var>\w+Schema)\s*=\s*loadTrackedSchema\("(?P<path>[^"]+)"\)')
_TS_SCHEMA_REGISTER_RE = re.compile(
    r'registerTrackedSchema\("(?P<path>[^"]+)",\s*(?P<var>\w+Schema)\)'
)
_TS_SCHEMA_OBJECT_ENTRY_RE = re.compile(r"^\s*(?P<key>\w+):\s*(?:ajv\.compile\()?(?P<var>\w+Schema)\)?,?\s*$")
TS_KERNEL_OPTIONAL_REGISTRATION_SCHEMA_FILES = {
    "bb.coordination_verification_result.v1.schema.json",
    "bb.directive.v1.schema.json",
    "bb.review_verdict.v1.schema.json",
    "bb.signal.v1.schema.json",
    "bb.wake_subscription.v1.schema.json",
}

VALID_SUPPORT_TIERS = {"draft-shape", "draft-semantic", "reference-engine"}
VALID_COMPARATOR_CLASSES = {
    "shape-equal",
    "normalized-trace-equal",
    "model-visible-equal",
    "workspace-side-effects-equal",
    "projection-equal",
}
INVALID_FIXTURE_GLOB = "*/invalid*.json"
REQUIRED_INVALID_FIXTURE_LABELS = {
    "atomic_feature_ledger/invalid_fixture.json",
    "blob_ref/invalid_fixture.json",
    "capability_registry/invalid_fixture.json",
    "checkpoint_metadata/invalid_fixture.json",
    "config_mutation_record/invalid_fixture.json",
    "context_resource_pack/invalid_fixture.json",
    "coordination/invalid_fixture.json",
    "distributed_task/invalid_fixture.json",
    "effective_config_graph/invalid_fixture.json",
    "effective_operation_policy/invalid_fixture.json",
    "effective_tool_surface/invalid_fixture.json",
    "execution_capability/invalid_fixture.json",
    "execution_placement/invalid_fixture.json",
    "extension_hook_execution/invalid_fixture.json",
    "external_protocol_session/invalid_fixture.json",
    "kernel_event/invalid_fixture.json",
    "memory_compaction_plan/invalid_fixture.json",
    "permission/invalid_fixture.json",
    "projection_event/invalid_fixture.json",
    "provider_exchange/invalid_fixture.json",
    "provider_route/invalid_fixture.json",
    "replay_session/invalid_fixture.json",
    "resource_access/invalid_fixture.json",
    "resource_ref/invalid_fixture.json",
    "run_context/invalid_fixture.json",
    "run_request/invalid_fixture.json",
    "sandbox_roundtrip/invalid_fixture.json",
    "session_transcript/invalid_fixture.json",
    "side_effect_broker/invalid_fixture.json",
    "task_subagent/invalid_fixture.json",
    "terminal_session/invalid_fixture.json",
    "tool_lifecycle/invalid_fixture.json",
    "tool_spec/invalid_fixture.json",
    "transcript_continuation_patch/invalid_fixture.json",
    "unsupported_case/invalid_fixture.json",
    "work_item/invalid_fixture.json",
}

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
    "work_item_minimal.json": "bb.work_item.v1.schema.json",
    "effective_config_graph_minimal.json": "bb.effective_config_graph.v1.schema.json",
    "config_explanation_minimal.json": "bb.config_explanation.v1.schema.json",
    "config_mutation_record_minimal.json": "bb.config_mutation_record.v1.schema.json",
    "context_resource_pack_minimal.json": "bb.context_resource_pack.v1.schema.json",
    "provider_route_minimal.json": "bb.provider_route.v1.schema.json",
    "external_protocol_session_minimal.json": "bb.external_protocol_session.v1.schema.json",
    "effective_operation_policy_minimal.json": "bb.effective_operation_policy.v1.schema.json",
    "capability_registry_minimal.json": "bb.capability_registry.v1.schema.json",
    "extension_hook_execution_minimal.json": "bb.extension_hook_execution.v1.schema.json",
    "side_effect_broker_minimal.json": "bb.side_effect_broker.v1.schema.json",
    "projection_event_minimal.json": "bb.projection_event.v1.schema.json",
    "memory_compaction_plan_minimal.json": "bb.memory_compaction_plan.v1.schema.json",
    "resource_ref_minimal.json": "bb.resource_ref.v1.schema.json",
    "resource_access_minimal.json": "bb.resource_access.v1.schema.json",
    "blob_ref_minimal.json": "bb.blob_ref.v1.schema.json",
    "atomic_feature_ledger_minimal.json": "bb.atomic_feature_ledger.v1.schema.json",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _build_schema_store() -> Dict[str, object]:
    return {path.name: _load_json(path) for path in SCHEMA_DIR.glob("*.json")}

def _validate_atomic_feature_ledger_payload(payload: Any, label: str) -> List[str]:
    if not ATOMIC_FEATURE_LEDGER_VALIDATOR_PATH.is_file():
        return [f"{label} atomic feature ledger validator missing: {ATOMIC_FEATURE_LEDGER_VALIDATOR_PATH}"]
    spec = importlib.util.spec_from_file_location(
        "_bb_atomic_feature_ledger_validator",
        ATOMIC_FEATURE_LEDGER_VALIDATOR_PATH,
    )
    if spec is None or spec.loader is None:
        return [f"{label} atomic feature ledger validator could not be loaded"]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [f"{label}: {error}" for error in module.collect_atomic_feature_ledger_errors(payload)]


def _validate_examples() -> List[str]:
    errors: List[str] = []
    schema_store = _build_schema_store()
    example_names = {path.name for path in EXAMPLES_DIR.glob("*.json")}
    for example_name in sorted(EXAMPLE_SCHEMA_MAP.keys() | example_names):
        example_path = EXAMPLES_DIR / example_name
        if not example_path.is_file():
            errors.append(f"example {example_name} missing example file")
            continue
        example = _load_json(example_path)
        schema_name = EXAMPLE_SCHEMA_MAP.get(example_name)
        if schema_name is None:
            schema_version = example.get("schema_version")
            if not isinstance(schema_version, str):
                errors.append(f"example {example_name} missing schema_version and map entry")
                continue
            schema_name = f"{schema_version}.schema.json"
        schema_path = SCHEMA_DIR / schema_name
        if not schema_path.is_file():
            errors.append(f"example {example_name} references missing schema: {schema_name}")
            continue
        schema = schema_store[schema_path.name]
        resolver = RefResolver.from_schema(schema, store=schema_store)
        validator = Draft202012Validator(schema, resolver=resolver)
        for err in validator.iter_errors(example):
            errors.append(f"example {example_name} failed {schema_name}: {err.message}")
        if schema_name == ATOMIC_FEATURE_LEDGER_SCHEMA:
            errors.extend(_validate_atomic_feature_ledger_payload(example, f"example {example_name}"))
    return errors


def _validate_manifest_and_fixtures() -> List[str]:
    errors: List[str] = []
    schema_store = _build_schema_store()
    manifest_schema = _load_json(ROOT / "contracts" / "kernel" / "manifests" / "bb.engine_conformance_manifest.v1.schema.json")
    manifest = _load_json(MANIFEST_PATH)
    validator = Draft202012Validator(manifest_schema)
    for err in validator.iter_errors(manifest):
        errors.append(f"manifest invalid: {err.message}")
    for row in manifest.get("rows", []):
        comparator = row.get("comparatorClass")
        if comparator not in VALID_COMPARATOR_CLASSES:
            errors.append(f"manifest invalid comparator class: {row.get('scenarioId')} -> {comparator}")
        for rel in row.get("evidence", []):
            target = FIXTURE_DIR / rel
            if not target.is_file():
                errors.append(f"manifest missing evidence file: {rel}")
                continue
            fixture = _load_json(target)
            support_tier = fixture.get("support_tier")
            if support_tier is not None and support_tier not in VALID_SUPPORT_TIERS:
                errors.append(f"fixture invalid support tier: {target} -> {support_tier}")
            comparator_class = fixture.get("comparator_class")
            if comparator_class is not None and comparator_class not in VALID_COMPARATOR_CLASSES:
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
                    if contract == ATOMIC_FEATURE_LEDGER_CONTRACT:
                        errors.extend(
                            _validate_atomic_feature_ledger_payload(
                                reference_output,
                                f"fixture {target.name}",
                            )
                        )
    return errors


def _fixture_label(path: Path) -> str:
    try:
        return str(path.relative_to(FIXTURE_DIR))
    except ValueError:
        return str(path)


def _validate_invalid_fixtures() -> List[str]:
    errors: List[str] = []
    schema_store = _build_schema_store()
    invalid_paths = sorted(FIXTURE_DIR.glob(INVALID_FIXTURE_GLOB))
    present_labels = {_fixture_label(path) for path in invalid_paths}
    for missing_label in sorted(REQUIRED_INVALID_FIXTURE_LABELS - present_labels):
        errors.append(f"missing invalid fixture coverage: {missing_label}")
    for target in invalid_paths:
        fixture = _load_json(target)
        label = _fixture_label(target)
        support_tier = fixture.get("support_tier")
        if support_tier is not None and support_tier not in VALID_SUPPORT_TIERS:
            errors.append(f"invalid fixture invalid support tier: {label} -> {support_tier}")
        comparator_class = fixture.get("comparator_class")
        if comparator_class is not None and comparator_class not in VALID_COMPARATOR_CLASSES:
            errors.append(f"invalid fixture invalid comparator class: {label} -> {comparator_class}")
        contract = fixture.get("contract")
        reference_output = fixture.get("reference_output")
        if not isinstance(contract, str):
            errors.append(f"invalid fixture {label} missing contract")
            continue
        if reference_output is None:
            errors.append(f"invalid fixture {label} missing reference_output")
            continue
        schema_path = SCHEMA_DIR / f"{contract}.schema.json"
        if not schema_path.is_file():
            errors.append(f"invalid fixture references missing contract schema: {label} -> {contract}")
            continue
        schema = schema_store[schema_path.name]
        resolver = RefResolver.from_schema(schema, store=schema_store)
        validator = Draft202012Validator(schema, resolver=resolver)
        validation_errors = list(validator.iter_errors(reference_output))
        if not validation_errors:
            errors.append(f"invalid fixture {label} unexpectedly passed {contract}")
    return errors


def _basename(schema_ref: str) -> str:
    return Path(schema_ref).name


def _ts_object_schema_files(index_text: str, object_name: str, loads: Dict[str, str]) -> set[str]:
    prefixes = {f"const {object_name} = {{", f"export const {object_name} = {{"}
    in_object = False
    schema_files: set[str] = set()
    for line in index_text.splitlines():
        if line.strip() in prefixes:
            in_object = True
            continue
        if not in_object:
            continue
        if line.strip() == "}":
            return schema_files
        match = _TS_SCHEMA_OBJECT_ENTRY_RE.match(line)
        if match:
            schema_file = loads.get(match.group("var"))
            if schema_file is not None:
                schema_files.add(schema_file)
    return schema_files


def _validate_ts_kernel_contracts_registry() -> List[str]:
    if not TS_KERNEL_CONTRACT_INDEX.is_file():
        return [f"ts kernel contracts index missing: {TS_KERNEL_CONTRACT_INDEX}"]

    index_text = TS_KERNEL_CONTRACT_INDEX.read_text(encoding="utf-8")
    loads = {
        match.group("var"): _basename(match.group("path"))
        for match in _TS_SCHEMA_LOAD_RE.finditer(index_text)
    }
    loaded_files = set(loads.values())
    registered_files = {
        _basename(match.group("path"))
        for match in _TS_SCHEMA_REGISTER_RE.finditer(index_text)
    }
    validator_files = _ts_object_schema_files(index_text, "validators", loads)
    exported_files = _ts_object_schema_files(index_text, "kernelSchemas", loads)

    errors: List[str] = []
    for label, files in (
        ("registerTrackedSchema", registered_files),
        ("kernelValidators", validator_files),
        ("kernelSchemas", exported_files),
    ):
        allowed_missing = TS_KERNEL_OPTIONAL_REGISTRATION_SCHEMA_FILES if label == "registerTrackedSchema" else set()
        missing = sorted(loaded_files - files - allowed_missing)
        extra = sorted(files - loaded_files)
        if missing:
            errors.append(f"ts kernel contracts {label} missing loaded schemas: {', '.join(missing)}")
        if extra:
            errors.append(f"ts kernel contracts {label} includes unloaded schemas: {', '.join(extra)}")
    return errors


def _validate_ts_kernel_contracts_runtime_schema_presence() -> List[str]:
    if not TS_KERNEL_CONTRACT_INDEX.is_file():
        return []

    missing: List[str] = []
    for match in _TS_SCHEMA_LOAD_RE.finditer(TS_KERNEL_CONTRACT_INDEX.read_text(encoding="utf-8")):
        schema_ref = match.group("path")
        schema_file = _basename(schema_ref)
        base_dir = ROOT / "contracts" / "kernel" / "manifests" if schema_ref.startswith("../manifests/") else SCHEMA_DIR
        if not (base_dir / schema_file).is_file():
            missing.append(schema_file)
    if not missing:
        return []
    return [f"ts kernel contracts load missing schema file: {', '.join(sorted(missing))}"]


def validate_kernel_contract_fixtures() -> List[str]:
    return (
        _validate_examples()
        + _validate_manifest_and_fixtures()
        + _validate_invalid_fixtures()
        + _validate_ts_kernel_contracts_registry()
        + _validate_ts_kernel_contracts_runtime_schema_presence()
    )


if __name__ == "__main__":
    errs = validate_kernel_contract_fixtures()
    if errs:
        for err in errs:
            print(err)
        raise SystemExit(1)
    print("ok")
