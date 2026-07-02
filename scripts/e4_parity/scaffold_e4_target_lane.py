#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
DEFAULT_OUTPUT_BASE = WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "lane_scaffolds"
PROBE_SCHEMA_VERSION = "bb.e4.target_probe_report.v1"
SCAFFOLD_SCHEMA_VERSION = "bb.e4.lane_scaffold_manifest.v1"
PROBE_SCHEMA_REF = "docs/conformance/schemas/bb.e4.target_probe_report.v1.schema.json"
SCAFFOLD_SCHEMA_REF = "docs/conformance/schemas/bb.e4.lane_scaffold_manifest.v1.schema.json"
PROBE_SCHEMA_PATH = REPO_ROOT / PROBE_SCHEMA_REF
SCAFFOLD_SCHEMA_PATH = REPO_ROOT / SCAFFOLD_SCHEMA_REF
INVENTORY_SCHEMA_VERSION = "bb.e4.lane_inventory.v1"
INVENTORY_SCHEMA_REF = "contracts/kernel/schemas/bb.e4.lane_inventory.v1.schema.json"
LANE_DEF_SCHEMA_VERSION = "bb.e4.lane_def.v1"
LANE_DEF_SCHEMA_REF = "contracts/kernel/schemas/bb.e4.lane_def.v1.schema.json"
COMPARATOR_PROTOCOL_REF = "conformance/comparators/protocol.py"
BUILDER_SKELETON_REF = "scripts/e4_parity/<lane-builder>.py"
DEFAULT_PROVIDER_MODEL = "no-provider"
DEFAULT_SANDBOX_MODE = "fixture_only"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_CONFIG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]*$")

FORBIDDEN_REPORT_KEYS = {
    "accepted",
    "accepted_points",
    "points",
    "score",
    "support_claim",
    "support_count",
}
FORBIDDEN_OUTPUT_ROOTS = (
    REPO_ROOT / "docs" / "conformance" / "e4_target_support",
    REPO_ROOT / "docs" / "conformance" / "support_claims",
    REPO_ROOT / "artifacts" / "conformance",
    REPO_ROOT / "agent_configs",
    WORKSPACE_ROOT / "docs_tmp" / "phase_15" / "oh_my_pi_p6",
)


@dataclass(frozen=True)
class ScaffoldInputs:
    lane_id: str
    config_id: str
    target_family: str
    target_version: str
    provider_model: str = DEFAULT_PROVIDER_MODEL
    sandbox_mode: str = DEFAULT_SANDBOX_MODE
    run_id: str | None = None

    def normalized_run_id(self) -> str:
        return self.run_id or f"{self.lane_id}_scaffold_run_v1"


def _json_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def display_path(path: Path) -> str:
    resolved = path.resolve()
    if _is_relative_to(resolved, REPO_ROOT):
        return resolved.relative_to(REPO_ROOT).as_posix()
    if _is_relative_to(resolved, WORKSPACE_ROOT):
        return resolved.relative_to(WORKSPACE_ROOT).as_posix()
    return resolved.as_posix()


def _validate_token(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(value):
        raise ValueError(f"{name} must match {pattern.pattern}")


def validate_inputs(inputs: ScaffoldInputs) -> None:
    _validate_token("lane_id", inputs.lane_id, _ID_RE)
    _validate_token("config_id", inputs.config_id, _CONFIG_RE)
    _validate_token("target_family", inputs.target_family, _ID_RE)
    _validate_token("target_version", inputs.target_version, _VERSION_RE)
    _validate_token("provider_model", inputs.provider_model, _PROVIDER_RE)
    _validate_token("sandbox_mode", inputs.sandbox_mode, _ID_RE)
    _validate_token("run_id", inputs.normalized_run_id(), _CONFIG_RE)


def default_output_root(lane_id: str) -> Path:
    _validate_token("lane_id", lane_id, _ID_RE)
    return DEFAULT_OUTPUT_BASE / lane_id


def resolve_output_root(lane_id: str, out_dir: str | Path | None = None) -> Path:
    if out_dir is None:
        return default_output_root(lane_id).resolve()
    candidate = Path(out_dir).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def assert_safe_output_root(output_root: Path) -> None:
    resolved = output_root.resolve()
    if not _is_relative_to(resolved, DEFAULT_OUTPUT_BASE.resolve()):
        raise ValueError(
            "E4 lane scaffolds must be written under docs_tmp/phase_15/lane_scaffolds; "
            f"got {display_path(resolved)}"
        )
    for forbidden in FORBIDDEN_OUTPUT_ROOTS:
        forbidden_resolved = forbidden.resolve()
        if resolved == forbidden_resolved or _is_relative_to(resolved, forbidden_resolved):
            raise ValueError(f"refusing to write scaffold under protected root {display_path(forbidden_resolved)}")


def _schema_path_for(schema_version: str) -> Path:
    if schema_version == PROBE_SCHEMA_VERSION:
        return PROBE_SCHEMA_PATH
    if schema_version == SCAFFOLD_SCHEMA_VERSION:
        return SCAFFOLD_SCHEMA_PATH
    raise ValueError(f"unknown schema version {schema_version}")


@lru_cache(maxsize=4)
def _validator_for_schema(schema_path: str) -> Draft202012Validator:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _format_jsonschema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    prefix = f"schema.{path}: " if path else "schema: "
    return f"{prefix}{error.message}"


def _schema_errors(payload: Mapping[str, Any], schema_path: Path) -> list[str]:
    validator = _validator_for_schema(str(schema_path))
    return [
        _format_jsonschema_error(error)
        for error in sorted(
            validator.iter_errors(payload),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def _iter_object_keys(value: Any, path: str = "$") -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            keys.append((child_path, key_text))
            keys.extend(_iter_object_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            keys.extend(_iter_object_keys(child, f"{path}[{index}]"))
    return keys


def collect_probe_report_errors(report: Mapping[str, Any], schema_path: Path | str = PROBE_SCHEMA_PATH) -> list[str]:
    errors = _schema_errors(report, Path(schema_path))
    for path, key in _iter_object_keys(report):
        if key in FORBIDDEN_REPORT_KEYS:
            errors.append(f"{path}: probe report must not contain support-claim or accepted-score field {key!r}")
    checks = report.get("checks")
    if isinstance(checks, list):
        names = [check.get("name") for check in checks if isinstance(check, Mapping)]
        if len(names) != len(set(names)):
            errors.append("checks.name values must be unique")
        if names != sorted(names):
            errors.append("checks must be sorted by name for deterministic output")
    if report.get("provider_model") == "no-provider":
        observations = report.get("observations")
        if isinstance(observations, Mapping) and observations.get("provider_dispatch_observed") is not False:
            errors.append("no-provider reports must set observations.provider_dispatch_observed=false")
    summary = report.get("summary")
    if isinstance(summary, Mapping) and summary.get("all_expected_passed") is True:
        if summary.get("failed") != 0:
            errors.append("summary.failed must be 0 when all_expected_passed=true")
        if report.get("errors"):
            errors.append("errors must be empty when all_expected_passed=true")
    return errors


def collect_scaffold_manifest_errors(
    manifest: Mapping[str, Any], schema_path: Path | str = SCAFFOLD_SCHEMA_PATH
) -> list[str]:
    errors = _schema_errors(manifest, Path(schema_path))
    output_root = manifest.get("output_root")
    if isinstance(output_root, str) and not output_root.startswith("docs_tmp/phase_15/lane_scaffolds/"):
        errors.append("output_root must stay under docs_tmp/phase_15/lane_scaffolds")
    default_root = manifest.get("default_output_root")
    if isinstance(default_root, str) and default_root != output_root:
        errors.append("default_output_root must match output_root for default scaffolds")
    for collection_name in ("generated_directories", "generated_files"):
        collection = manifest.get(collection_name)
        if not isinstance(collection, list):
            continue
        paths = [item.get("path") if isinstance(item, Mapping) else item for item in collection]
        for path in paths:
            if isinstance(path, str) and not path.startswith(str(output_root) + "/") and path != output_root:
                errors.append(f"{collection_name} entry {path!r} is outside output_root")
        if paths != sorted(paths):
            errors.append(f"{collection_name} entries must be sorted by path")
    guardrails = manifest.get("guardrails")
    if isinstance(guardrails, Mapping):
        if guardrails.get("creates_support_claim") is not False:
            errors.append("scaffold manifest must not create support claims")
        if guardrails.get("changes_accepted_score") is not False:
            errors.append("scaffold manifest must not change accepted score")
        if guardrails.get("writes_accepted_evidence_roots") is not False:
            errors.append("scaffold manifest must not write accepted evidence roots")
    return errors


def validate_probe_report(report: Mapping[str, Any]) -> None:
    errors = collect_probe_report_errors(report)
    if errors:
        raise ValueError("target probe report failed validation: " + "; ".join(errors))


def validate_scaffold_manifest(manifest: Mapping[str, Any]) -> None:
    errors = collect_scaffold_manifest_errors(manifest)
    if errors:
        raise ValueError("lane scaffold manifest failed validation: " + "; ".join(errors))


def build_probe_report(inputs: ScaffoldInputs) -> dict[str, Any]:
    validate_inputs(inputs)
    report = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "target_family": inputs.target_family,
        "config_id": inputs.config_id,
        "lane_id": inputs.lane_id,
        "run_id": inputs.normalized_run_id(),
        "target_version": inputs.target_version,
        "provider_model": inputs.provider_model,
        "sandbox_mode": inputs.sandbox_mode,
        "promotion_state": "scaffold_only",
        "capture_scope": f"Scaffold-only target probe contract for {inputs.target_family}/{inputs.lane_id}.",
        "source_refs": [],
        "environment": {
            "cwd": "<target-workspace>",
            "agent_dir": None,
            "provider_secrets_present": False,
            "selected_settings": {},
        },
        "observations": {
            "fetch_events": [],
            "network_observed": False,
            "provider_dispatch_observed": False,
        },
        "surface_observations": {
            "builtin_tools": [],
            "commands": [],
            "context_files": [],
            "custom_commands": [],
            "custom_tools": [],
            "hooks": [],
            "prompt_templates": [],
            "raw_observations": {},
            "skills": [],
            "slash_commands": [],
            "tool_calls": [],
        },
        "checks": [
            {
                "name": "scaffold.placeholder_contract",
                "status": "not_applicable",
                "detail": "Replace this placeholder with target-observed checks before promotion.",
            }
        ],
        "artifacts": [],
        "summary": {
            "all_expected_passed": False,
            "failed": 0,
            "warned": 0,
        },
        "errors": [],
        "warnings": [],
    }
    validate_probe_report(report)
    return report


def build_config_placeholder(inputs: ScaffoldInputs) -> dict[str, Any]:
    validate_inputs(inputs)
    return {
        "schema_version": "bb.e4.target_lane_config_placeholder.v1",
        "target_family": inputs.target_family,
        "config_id": inputs.config_id,
        "lane_id": inputs.lane_id,
        "run_id": inputs.normalized_run_id(),
        "target_version": inputs.target_version,
        "provider_model": inputs.provider_model,
        "sandbox_mode": inputs.sandbox_mode,
        "promotion_state": "scaffold_only",
        "capture_scope": f"Replace with the exact narrow target support scope for {inputs.lane_id}.",
        "required_next_inputs": [
            "target source refs",
            "probe command argv",
            "expected surface checks",
            "secret-scan scope",
        ],
    }


def build_inventory_row(inputs: ScaffoldInputs) -> dict[str, Any]:
    validate_inputs(inputs)
    return {
        "lane_id": inputs.lane_id,
        "config_id": inputs.config_id,
        "phase": "scaffold",
        "kind": "target_support",
        "status": "scaffolded",
        "points": 0,
        "target_family": inputs.target_family,
        "target_version": inputs.target_version,
        "run_id": inputs.normalized_run_id(),
        "provider_model": inputs.provider_model,
        "sandbox_mode": inputs.sandbox_mode,
        "primitives": [],
        "builder": {
            "argv": ["python", f"docs_tmp/phase_15/lane_scaffolds/{inputs.lane_id}/builder/build_scaffold_lane.py"],
            "cwd": ".",
        },
        "comparator_id": f"{inputs.lane_id}_comparator",
        "ct": {
            "test_id": f"CT-SCAFFOLD-{inputs.lane_id.upper().replace('_', '-')}",
            "gate_level": "C4",
            "command": {
                "argv": [
                    "python",
                    "scripts/validate_e4_c4_chain.py",
                    "--config-id",
                    inputs.config_id,
                    "--json-out",
                    f"artifacts/conformance/node_gate/ct_{inputs.lane_id}.json",
                ],
                "cwd": ".",
            },
        },
        "artifact_roles": {},
        "ledger_feature_ids": [],
        "reverify_command": None,
        "metadata": {
            "scaffold_source": "scripts/e4_parity/scaffold_e4_target_lane.py",
            "promotion_state": "scaffold_only",
        },
    }


def build_lane_def_draft(inputs: ScaffoldInputs) -> dict[str, Any]:
    validate_inputs(inputs)
    return {
        "schema_version": LANE_DEF_SCHEMA_VERSION,
        "lane_id": inputs.lane_id,
        "config_id": inputs.config_id,
        "target_family": inputs.target_family,
        "target_version": inputs.target_version,
        "package_ref": None,
        "kind": "target_support",
        "status": "scaffolded",
        "points": 0,
        "capture": {
            "strategy": "legacy_builder",
            "argv": ["python", f"docs_tmp/phase_15/lane_scaffolds/{inputs.lane_id}/builder/build_scaffold_lane.py"],
            "inputs": [],
            "workspace_template": None,
        },
        "normalize": {"translator": "identity", "config": {}},
        "replay": {"session": None, "comparator_class": "semantic"},
        "compare": {
            "comparator": f"{inputs.lane_id}_comparator",
            "config": {
                "assertions": [
                    {"equals": inputs.config_id, "path": "config_id"},
                    {"equals": "scaffold_only", "path": "promotion_state"},
                ],
            },
        },
        "claim": {
            "scope": {
                "behaviors": [],
                "surfaces": ["scaffold-only lane draft"],
            },
            "exclusions": ["not accepted evidence", "not a support claim"],
        },
        "ct": {
            "description": f"Scaffold-only C4 draft for {inputs.lane_id}; must be replaced before promotion.",
            "timeout_seconds": 180,
        },
        "artifacts_root": f"docs_tmp/phase_15/lane_scaffolds/{inputs.lane_id}",
        "reverify_command": None,
        "metadata": {
            "scaffold_source": "scripts/e4_parity/scaffold_e4_target_lane.py",
            "promotion_state": "scaffold_only",
        },
    }


def build_builder_skeleton(inputs: ScaffoldInputs) -> str:
    validate_inputs(inputs)
    return (
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n\n"
        "import json\n"
        "from pathlib import Path\n\n"
        f"LANE_ID = {inputs.lane_id!r}\n"
        f"CONFIG_ID = {inputs.config_id!r}\n\n"
        "def build() -> dict[str, object]:\n"
        "    # Replace this scaffold-only report with target capture/replay artifact generation before promotion.\n"
        "    return {\n"
        "        \"schema_version\": \"bb.e4.builder_skeleton.v1\",\n"
        "        \"lane_id\": LANE_ID,\n"
        "        \"config_id\": CONFIG_ID,\n"
        "        \"promotion_state\": \"scaffold_only\",\n"
        "        \"artifacts\": [],\n"
        "    }\n\n"
        "def main() -> int:\n"
        "    print(json.dumps(build(), indent=2, sort_keys=True))\n"
        "    return 0\n\n"
        "if __name__ == \"__main__\":\n"
        "    raise SystemExit(main())\n"
    )


def build_comparator_skeleton(inputs: ScaffoldInputs) -> str:
    validate_inputs(inputs)
    return (
        "from __future__ import annotations\n\n"
        "from conformance.comparators.protocol import ComparatorAssertion, ComparatorInput\n\n"
        f"COMPARATOR_ID = {inputs.lane_id + '_comparator'!r}\n\n"
        "def compare(inp: ComparatorInput) -> list[ComparatorAssertion]:\n"
        "    # Replace scaffold assertions with lane-specific capture/replay comparisons before promotion.\n"
        "    return [\n"
        "        {\n"
        "            \"assertion_id\": f\"{COMPARATOR_ID}.todo_assertion\",\n"
        "            \"status\": \"warned\",\n"
        "            \"observed\": None,\n"
        "            \"expected\": \"lane-specific assertion\",\n"
        "            \"detail\": \"Scaffold placeholder; not accepted evidence.\",\n"
        "        }\n"
        "    ]\n"
    )


def _manifest_file_refs(
    output_root_display: str,
    *,
    emit_inventory_row: bool = False,
    emit_builder_skeleton: bool = False,
    emit_comparator_skeleton: bool = False,
    emit_lane_def: bool = False,
) -> list[dict[str, str]]:
    refs = [
        {
            "path": f"{output_root_display}/config/target_lane_config.placeholder.json",
            "role": "config_placeholder",
            "schema_version": "bb.e4.target_lane_config_placeholder.v1",
        },
        {
            "path": f"{output_root_display}/scaffold_manifest.json",
            "role": "scaffold_manifest",
            "schema_version": SCAFFOLD_SCHEMA_VERSION,
        },
        {
            "path": f"{output_root_display}/target_probe_report.placeholder.json",
            "role": "target_probe_report_placeholder",
            "schema_version": PROBE_SCHEMA_VERSION,
        },
    ]
    if emit_inventory_row:
        refs.append(
            {
                "path": f"{output_root_display}/inventory/e4_lane_inventory_row.scaffold.json",
                "role": "inventory_row",
                "schema_version": INVENTORY_SCHEMA_VERSION,
            }
        )
    if emit_builder_skeleton:
        refs.append(
            {
                "path": f"{output_root_display}/builder/build_scaffold_lane.py",
                "role": "builder_skeleton",
            }
        )
    if emit_comparator_skeleton:
        refs.append(
            {
                "path": f"{output_root_display}/comparator/scaffold_lane_comparator.py",
                "role": "comparator_skeleton",
            }
        )
    if emit_lane_def:
        refs.append(
            {
                "path": f"{output_root_display}/config/e4_lane_def.scaffold.yaml",
                "role": "lane_def_draft",
                "schema_version": LANE_DEF_SCHEMA_VERSION,
            }
        )
    return sorted(refs, key=lambda item: item["path"])


def build_scaffold_manifest(
    inputs: ScaffoldInputs,
    output_root: Path | None = None,
    *,
    emit_inventory_row: bool = False,
    emit_builder_skeleton: bool = False,
    emit_comparator_skeleton: bool = False,
    emit_lane_def: bool = False,
) -> dict[str, Any]:
    validate_inputs(inputs)
    resolved_output_root = (output_root or default_output_root(inputs.lane_id)).resolve()
    assert_safe_output_root(resolved_output_root)
    output_root_display = display_path(resolved_output_root)
    default_output_display = display_path(default_output_root(inputs.lane_id))
    directories = [
        output_root_display,
        f"{output_root_display}/config",
        f"{output_root_display}/raw",
        f"{output_root_display}/target_fixture_workspace",
    ]
    if emit_inventory_row:
        directories.append(f"{output_root_display}/inventory")
    if emit_builder_skeleton:
        directories.append(f"{output_root_display}/builder")
    if emit_comparator_skeleton:
        directories.append(f"{output_root_display}/comparator")
    manifest = {
        "schema_version": SCAFFOLD_SCHEMA_VERSION,
        "lane_id": inputs.lane_id,
        "config_id": inputs.config_id,
        "target_family": inputs.target_family,
        "target_version": inputs.target_version,
        "provider_model": inputs.provider_model,
        "sandbox_mode": inputs.sandbox_mode,
        "run_id": inputs.normalized_run_id(),
        "promotion_state": "scaffold_only",
        "output_root": output_root_display,
        "default_output_root": default_output_display,
        "contracts": {
            "probe_report_schema": PROBE_SCHEMA_REF,
            "scaffold_manifest_schema": SCAFFOLD_SCHEMA_REF,
        },
        "generated_directories": sorted(directories),
        "generated_files": _manifest_file_refs(
            output_root_display,
            emit_inventory_row=emit_inventory_row,
            emit_builder_skeleton=emit_builder_skeleton,
            emit_comparator_skeleton=emit_comparator_skeleton,
            emit_lane_def=emit_lane_def,
        ),
        "guardrails": {
            "changes_accepted_score": False,
            "creates_support_claim": False,
            "scaffold_only": True,
            "writes_accepted_evidence_roots": False,
        },
    }
    validate_scaffold_manifest(manifest)
    return manifest


def build_scaffold_payload(
    inputs: ScaffoldInputs,
    output_root: Path | None = None,
    *,
    emit_inventory_row: bool = False,
    emit_builder_skeleton: bool = False,
    emit_comparator_skeleton: bool = False,
    emit_lane_def: bool = False,
) -> dict[str, Any]:
    resolved_output_root = (output_root or default_output_root(inputs.lane_id)).resolve()
    manifest = build_scaffold_manifest(
        inputs,
        resolved_output_root,
        emit_inventory_row=emit_inventory_row,
        emit_builder_skeleton=emit_builder_skeleton,
        emit_comparator_skeleton=emit_comparator_skeleton,
        emit_lane_def=emit_lane_def,
    )
    probe_report = build_probe_report(inputs)
    config_placeholder = build_config_placeholder(inputs)
    files: dict[str, Any] = {
        "config/target_lane_config.placeholder.json": config_placeholder,
        "scaffold_manifest.json": manifest,
        "target_probe_report.placeholder.json": probe_report,
    }
    if emit_inventory_row:
        files["inventory/e4_lane_inventory_row.scaffold.json"] = build_inventory_row(inputs)
    if emit_lane_def:
        files["config/e4_lane_def.scaffold.yaml"] = build_lane_def_draft(inputs)
    if emit_builder_skeleton:
        files["builder/build_scaffold_lane.py"] = build_builder_skeleton(inputs)
    if emit_comparator_skeleton:
        files["comparator/scaffold_lane_comparator.py"] = build_comparator_skeleton(inputs)
    return {
        "dry_run": False,
        "output_root": manifest["output_root"],
        "default_output_root": manifest["default_output_root"],
        "directories": manifest["generated_directories"],
        "files": files,
    }


def write_scaffold(
    inputs: ScaffoldInputs,
    output_root: Path | None = None,
    *,
    emit_inventory_row: bool = False,
    emit_builder_skeleton: bool = False,
    emit_comparator_skeleton: bool = False,
    emit_lane_def: bool = False,
) -> dict[str, Any]:
    resolved_output_root = (output_root or default_output_root(inputs.lane_id)).resolve()
    assert_safe_output_root(resolved_output_root)
    payload = build_scaffold_payload(
        inputs,
        resolved_output_root,
        emit_inventory_row=emit_inventory_row,
        emit_builder_skeleton=emit_builder_skeleton,
        emit_comparator_skeleton=emit_comparator_skeleton,
        emit_lane_def=emit_lane_def,
    )
    for directory in payload["directories"]:
        directory_path = WORKSPACE_ROOT / directory if directory.startswith("docs_tmp/") else REPO_ROOT / directory
        directory_path.mkdir(parents=True, exist_ok=True)
    for relative_name, file_payload in payload["files"].items():
        path = resolved_output_root / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(file_payload, str):
            path.write_text(file_payload, encoding="utf-8")
        else:
            path.write_text(_json_dumps(file_payload), encoding="utf-8")
    return {
        "dry_run": False,
        "output_root": payload["output_root"],
        "default_output_root": payload["default_output_root"],
        "generated_files": sorted(
            f"{payload['output_root']}/{relative_name}" for relative_name in payload["files"].keys()
        ),
    }


def dry_run_scaffold(
    inputs: ScaffoldInputs,
    output_root: Path | None = None,
    *,
    emit_inventory_row: bool = False,
    emit_builder_skeleton: bool = False,
    emit_comparator_skeleton: bool = False,
    emit_lane_def: bool = False,
) -> dict[str, Any]:
    resolved_output_root = (output_root or default_output_root(inputs.lane_id)).resolve()
    payload = build_scaffold_payload(
        inputs,
        resolved_output_root,
        emit_inventory_row=emit_inventory_row,
        emit_builder_skeleton=emit_builder_skeleton,
        emit_comparator_skeleton=emit_comparator_skeleton,
        emit_lane_def=emit_lane_def,
    )
    result = {
        "dry_run": True,
        "output_root": payload["output_root"],
        "default_output_root": payload["default_output_root"],
        "generated_directories": payload["directories"],
        "generated_files": sorted(f"{payload['output_root']}/{name}" for name in payload["files"].keys()),
        "probe_report": payload["files"]["target_probe_report.placeholder.json"],
        "scaffold_manifest": payload["files"]["scaffold_manifest.json"],
    }
    if emit_inventory_row:
        result["inventory_row"] = payload["files"]["inventory/e4_lane_inventory_row.scaffold.json"]
    if emit_lane_def:
        result["lane_def"] = payload["files"]["config/e4_lane_def.scaffold.yaml"]
    if emit_builder_skeleton:
        result["builder_skeleton"] = payload["files"]["builder/build_scaffold_lane.py"]
    if emit_comparator_skeleton:
        result["comparator_skeleton"] = payload["files"]["comparator/scaffold_lane_comparator.py"]
    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scaffold a deterministic E4 target lane under docs_tmp/phase_15/lane_scaffolds.")
    parser.add_argument("--lane-id", required=True)
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--target-family", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--provider-model", default=DEFAULT_PROVIDER_MODEL)
    parser.add_argument("--sandbox-mode", default=DEFAULT_SANDBOX_MODE)
    parser.add_argument("--run-id")
    parser.add_argument("--out-dir", help="Optional explicit scaffold root; must still be under docs_tmp/phase_15/lane_scaffolds.")
    parser.add_argument("--dry-run-json", action="store_true", help="Print deterministic JSON without writing files.")
    parser.add_argument("--emit-inventory-row", action="store_true", help="Include a scaffolded lane-inventory row draft.")
    parser.add_argument("--emit-builder-skeleton", action="store_true", help="Include a placeholder builder script.")
    parser.add_argument("--emit-comparator-skeleton", action="store_true", help="Include a comparator protocol stub.")
    parser.add_argument("--emit-lane-def", action="store_true", help="Include a scaffolded lane_def YAML draft.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    inputs = ScaffoldInputs(
        lane_id=args.lane_id,
        config_id=args.config_id,
        target_family=args.target_family,
        target_version=args.target_version,
        provider_model=args.provider_model,
        sandbox_mode=args.sandbox_mode,
        run_id=args.run_id,
    )
    try:
        output_root = resolve_output_root(inputs.lane_id, args.out_dir)
        options = {
            "emit_inventory_row": args.emit_inventory_row,
            "emit_builder_skeleton": args.emit_builder_skeleton,
            "emit_comparator_skeleton": args.emit_comparator_skeleton,
            "emit_lane_def": args.emit_lane_def,
        }
        if args.dry_run_json:
            print(_json_dumps(dry_run_scaffold(inputs, output_root, **options)), end="")
        else:
            print(_json_dumps(write_scaffold(inputs, output_root, **options)), end="")
    except Exception as exc:
        print(f"scaffold_e4_target_lane: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
