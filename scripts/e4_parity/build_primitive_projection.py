#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.compilation.effective_config_graph import compile_effective_config_graph

L1_DIR = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface"
L2_DIR = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution"
L1_PROBE_PATH = L1_DIR / "target_probe_output.json"
L2_PROBE_PATH = L2_DIR / "target_probe_output.json"
L1_MANIFEST_PATH = L1_DIR / "raw_capture_manifest.json"
L2_MANIFEST_PATH = L2_DIR / "raw_capture_manifest.json"
SCHEMA_DIR = ROOT / "contracts/kernel/schemas"
OUTPUT_DIR = ROOT / "artifacts/conformance/e4_primitive_projection/oh_my_pi_p6_0_l1_l2"

CONFIG_SCHEMA_PATH = SCHEMA_DIR / "bb.effective_config_graph.v1.schema.json"
REGISTRY_SCHEMA_PATH = SCHEMA_DIR / "bb.capability_registry.v1.schema.json"
SURFACE_SCHEMA_PATH = SCHEMA_DIR / "bb.effective_tool_surface.v1.schema.json"

CONFIG_GRAPH_PATH = OUTPUT_DIR / "effective_config_graph.v1.json"
CAPABILITY_REGISTRY_PATH = OUTPUT_DIR / "capability_registry.v1.json"
TOOL_SURFACE_PATH = OUTPUT_DIR / "effective_tool_surface.v1.json"
MANIFEST_PATH = OUTPUT_DIR / "primitive_projection_manifest.v1.json"

PROJECTION_ID = "oh_my_pi_p6_0_l1_l2"
CONFIG_GRAPH_ID = "oh_my_pi_p6_0_l1_l2_effective_config_graph"
REGISTRY_ID = "oh_my_pi_p6_0_l1_l2_capability_registry"
SURFACE_ID = "oh_my_pi_p6_0_l1_l2_effective_tool_surface"
PROFILE_ID = "oh_my_pi_p6_provider_free_local"
ENVIRONMENT_ID = "oh_my_pi_p6_0_provider_free_local"
NATIVE_PROVIDER_ID = "oh_my_pi.native"

_SCHEMA_PATHS = {
    "bb.effective_config_graph.v1": CONFIG_SCHEMA_PATH,
    "bb.capability_registry.v1": REGISTRY_SCHEMA_PATH,
    "bb.effective_tool_surface.v1": SURFACE_SCHEMA_PATH,
}

_OUTPUT_NAMES = {
    "bb.effective_config_graph.v1": "effective_config_graph.v1.json",
    "bb.capability_registry.v1": "capability_registry.v1.json",
    "bb.effective_tool_surface.v1": "effective_tool_surface.v1.json",
}

_SHA_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ID_RE = re.compile(r"[^a-z0-9_]+")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, separators=(",", ": ")) + "\n").encode(
        "utf-8"
    )


def _sha256_bytes(data: bytes) -> str:
    return _hash_utils.sha256_bytes(data)


def _sha256_file(path: Path) -> str:
    return _hash_utils.sha256_file(path)


def _repo_rel(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _normalize_path_string(value: str) -> str:
    if value.startswith("bundled:"):
        return value
    try:
        path = Path(value)
    except TypeError:
        return value
    if path.is_absolute():
        try:
            return path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return value
    return value


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_path_string(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return value


def _safe_id(value: str) -> str:
    lowered = value.lower().replace(":", "__")
    safe = _SAFE_ID_RE.sub("_", lowered).strip("_")
    return safe or "unnamed"


def _value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _with_hash(record: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    preimage = copy.deepcopy(dict(record))
    preimage[field_name] = None
    digest = _sha256_bytes(_canonical_json_bytes(preimage))
    result = copy.deepcopy(dict(record))
    result[field_name] = digest
    return result


def _schema_validator(schema_path: Path) -> Draft202012Validator:
    schema = _read_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_record(contract: str, record: Mapping[str, Any]) -> list[str]:
    validator = _schema_validator(_SCHEMA_PATHS[contract])
    return [
        _format_schema_error(error)
        for error in sorted(
            validator.iter_errors(record),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def _format_schema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return f"{path}: {error.message}" if path else error.message


def _require_hash(source_hashes: Mapping[str, str], ref: str) -> str:
    value = source_hashes[ref]
    if not _SHA_RE.match(value):
        raise ValueError(f"invalid sha256 for {ref}: {value}")
    return value


def _capability(
    *,
    capability_id: str,
    capability_type: str,
    name: str,
    discovery_mode: str,
    evidence_ref: str,
    exposure_state: str,
    exposure_mode: str,
    model_visible: bool,
    surface_refs: list[str],
    reason: str,
    visibility: str,
    metadata: dict[str, Any],
    provider: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
    discovered_at: str | None = None,
) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "capability_type": capability_type,
        "discovery": {
            "discovered_at": discovered_at,
            "evidence_ref": evidence_ref,
            "mode": discovery_mode,
            "state": "discovered",
        },
        "exposure": {
            "mode": exposure_mode,
            "model_visible": model_visible,
            "reason": reason,
            "state": exposure_state,
            "surface_refs": surface_refs,
        },
        "metadata": _normalize_value(metadata),
        "name": name,
        "provider": provider,
        "scopes": scopes or [],
        "visibility": visibility,
    }


def _native_provider(version: str) -> dict[str, Any]:
    return {"provider_id": NATIVE_PROVIDER_ID, "version": version}


def _build_source_layers(l1_manifest: Mapping[str, Any], l2_manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    l1_hashes = l1_manifest["source_hashes"]
    l2_hashes = l2_manifest["source_hashes"]
    return [
        {
            "host_visible": True,
            "layer_hash": _require_hash(
                l1_hashes,
                "agent_configs/misc/oh_my_pi_p6_0_l1_config_context_tool_surface_v1.yaml",
            ),
            "layer_id": "l1_agent_config",
            "model_visible": False,
            "precedence": 10,
            "scope": l1_manifest["lane_id"],
            "source_kind": "policy",
            "source_ref": "agent_configs/misc/oh_my_pi_p6_0_l1_config_context_tool_surface_v1.yaml",
        },
        {
            "host_visible": True,
            "layer_hash": _require_hash(
                l1_hashes,
                "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json",
            ),
            "layer_id": "l1_probe_observed_runtime",
            "model_visible": True,
            "precedence": 20,
            "scope": l1_manifest["lane_id"],
            "source_kind": "runtime",
            "source_ref": "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json",
        },
        {
            "host_visible": True,
            "layer_hash": _require_hash(
                l2_hashes,
                "agent_configs/misc/oh_my_pi_p6_0_l2_tool_execution_v1.yaml",
            ),
            "layer_id": "l2_agent_config",
            "model_visible": False,
            "precedence": 30,
            "scope": l2_manifest["lane_id"],
            "source_kind": "policy",
            "source_ref": "agent_configs/misc/oh_my_pi_p6_0_l2_tool_execution_v1.yaml",
        },
        {
            "host_visible": True,
            "layer_hash": _require_hash(
                l2_hashes,
                "docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/target_probe_output.json",
            ),
            "layer_id": "l2_probe_observed_runtime",
            "model_visible": True,
            "precedence": 40,
            "scope": l2_manifest["lane_id"],
            "source_kind": "runtime",
            "source_ref": "docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/target_probe_output.json",
        },
    ]


def _effective_value(path: str, value: Any, source_layer_id: str, visibility: str) -> dict[str, Any]:
    return {
        "env_gate_ids": [],
        "path": path,
        "source_layer_id": source_layer_id,
        "value": value,
        "value_kind": _value_kind(value),
        "visibility": visibility,
    }


def _build_effective_config_graph(
    l1_probe: Mapping[str, Any],
    l2_probe: Mapping[str, Any],
    l1_manifest: Mapping[str, Any],
    l2_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    selected = l2_probe["selectedSettings"]
    layers = _build_source_layers(l1_manifest, l2_manifest)
    return compile_effective_config_graph(
        graph_id=CONFIG_GRAPH_ID,
        layers=[
            {
                **layers[0],
                "values": {},
            },
            {
                **layers[1],
                "values": {
                    "l1": {
                        "run_id": l1_manifest["run_id"],
                    }
                },
            },
            {
                **layers[2],
                "values": {},
            },
            {
                **layers[3],
                "values": {
                    "target": {
                        "family": l2_probe["target_family"],
                        "version": l2_manifest["target_version"],
                    },
                    "provider": {
                        "dispatchObserved": l2_probe["provider_dispatch_observed"],
                        "model": l2_probe["provider_model"],
                    },
                    "sandbox": {
                        "mode": l2_probe["sandbox_mode"],
                    },
                    "settings": {
                        "browser": {
                            "enabled": selected["browserEnabled"],
                        },
                        "includeWorkspaceTree": selected["includeWorkspaceTree"],
                        "mcp": {
                            "discoveryMode": selected["mcpDiscoveryMode"],
                            "enableProjectConfig": selected["mcpEnableProjectConfig"],
                        },
                        "tools": {
                            "discoveryMode": selected["toolsDiscoveryMode"],
                        },
                        "webSearch": {
                            "enabled": selected["webSearchEnabled"],
                        },
                    },
                    "network": {
                        "observed": l2_probe["network_observed"],
                    },
                    "l2": {
                        "run_id": l2_manifest["run_id"],
                    },
                },
            },
        ],
        merge_policy={
            "conflict_resolution": "highest-precedence",
            "policy_id": "oh_my_pi_p6_capture_layer_order",
            "strategy": "deep-merge",
        },
        migrations=[
            {
                "applied": True,
                "from_version": None,
                "migration_id": "from-oh-my-pi-p6-l1-l2-capture-v1",
                "to_version": "bb.effective_config_graph.v1",
            }
        ],
        host_only_paths=("l1.run_id", "l2.run_id"),
    )


def _build_builtin_capabilities(
    l1_probe: Mapping[str, Any], target_version: str, generated_at: str
) -> list[dict[str, Any]]:
    aliases_by_target: dict[str, list[str]] = {}
    for alias, target in sorted(l1_probe["toolAliases"].items()):
        aliases_by_target.setdefault(target, []).append(alias)

    capabilities: list[dict[str, Any]] = []
    for order, name in enumerate(l1_probe["builtinToolNames"]):
        capability_id = f"tool.oh_my_pi.builtin.{_safe_id(name)}"
        capabilities.append(
            _capability(
                capability_id=capability_id,
                capability_type="tool",
                name=name,
                provider=_native_provider(target_version.removeprefix("@oh-my-pi/pi-coding-agent@")),
                discovery_mode="static_manifest",
                evidence_ref=(
                    "docs/conformance/e4_target_support/"
                    "oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json#builtinToolNames"
                ),
                discovered_at=generated_at,
                exposure_state="exposed",
                exposure_mode="model_visible",
                model_visible=True,
                surface_refs=[SURFACE_ID],
                reason="Captured in L1 builtinToolNames and corroborated by L2 builtin_tool_count.",
                visibility="model_visible",
                scopes=["tool:invoke"],
                metadata={
                    "aliases": aliases_by_target.get(name, []),
                    "kind": "builtin_tool",
                    "source_order": order,
                },
            )
        )
    return capabilities


def _build_runtime_l1_capabilities(
    l1_probe: Mapping[str, Any], target_version: str, generated_at: str
) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    provider = _native_provider(target_version.removeprefix("@oh-my-pi/pi-coding-agent@"))

    for index, item in enumerate(l1_probe["contextFiles"]):
        path = _normalize_path_string(item["path"])
        capabilities.append(
            _capability(
                capability_id=f"runtime.oh_my_pi.context_file.{index + 1:02d}",
                capability_type="runtime",
                name=path,
                provider=provider,
                discovery_mode="runtime_probe",
                evidence_ref=(
                    "docs/conformance/e4_target_support/"
                    "oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json#contextFiles"
                ),
                discovered_at=generated_at,
                exposure_state="exposed",
                exposure_mode="model_visible",
                model_visible=True,
                surface_refs=[],
                reason="Context file was included in the L1 observed context surface.",
                visibility="model_visible",
                scopes=["context:read"],
                metadata={"kind": "context_file", **_normalize_value(item), "path": path},
            )
        )

    for item in l1_probe["skills"]:
        name = item["name"]
        capabilities.append(
            _capability(
                capability_id=f"runtime.oh_my_pi.skill.{_safe_id(name)}",
                capability_type="runtime",
                name=name,
                provider=provider,
                discovery_mode="runtime_probe",
                evidence_ref=(
                    "docs/conformance/e4_target_support/"
                    "oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json#skills"
                ),
                discovered_at=generated_at,
                exposure_state="exposed",
                exposure_mode="model_visible",
                model_visible=True,
                surface_refs=[],
                reason="Skill was discovered in the L1 observed skill surface.",
                visibility="model_visible",
                scopes=["skill:read"],
                metadata={"kind": "skill", **_normalize_value(item)},
            )
        )

    for item in l1_probe["promptTemplates"]:
        name = item["name"]
        capabilities.append(
            _capability(
                capability_id=f"runtime.oh_my_pi.prompt_template.{_safe_id(name)}",
                capability_type="runtime",
                name=name,
                provider=provider,
                discovery_mode="runtime_probe",
                evidence_ref=(
                    "docs/conformance/e4_target_support/"
                    "oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json#promptTemplates"
                ),
                discovered_at=generated_at,
                exposure_state="exposed",
                exposure_mode="model_visible",
                model_visible=True,
                surface_refs=[],
                reason="Prompt template was discovered in the L1 observed prompt surface.",
                visibility="model_visible",
                scopes=["prompt:read"],
                metadata={"kind": "prompt_template", **_normalize_value(item)},
            )
        )

    for item in l1_probe["slashCommands"]:
        name = item["name"]
        capabilities.append(
            _capability(
                capability_id=f"runtime.oh_my_pi.slash_command.{_safe_id(name)}",
                capability_type="runtime",
                name=name,
                provider=provider,
                discovery_mode="runtime_probe",
                evidence_ref=(
                    "docs/conformance/e4_target_support/"
                    "oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json#slashCommands"
                ),
                discovered_at=generated_at,
                exposure_state="exposed",
                exposure_mode="model_visible",
                model_visible=True,
                surface_refs=[],
                reason="Slash command was discovered in the L1 observed command surface.",
                visibility="model_visible",
                scopes=["command:invoke"],
                metadata={"kind": "slash_command", **_normalize_value(item)},
            )
        )

    return capabilities


def _build_l2_capabilities(
    l2_probe: Mapping[str, Any], target_version: str, generated_at: str
) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    provider = _native_provider(target_version.removeprefix("@oh-my-pi/pi-coding-agent@"))

    custom_tool = l2_probe["custom_tool_discovery"]["tools"][0]
    selected_execution = _normalize_value(l2_probe["custom_tool_discovery"]["selected_execution"])
    capabilities.append(
        _capability(
            capability_id="tool.oh_my_pi.custom.p6_echo",
            capability_type="tool",
            name=custom_tool["name"],
            provider=provider,
            discovery_mode="runtime_probe",
            evidence_ref=(
                "docs/conformance/e4_target_support/"
                "oh_my_pi_p6_0_l2_tool_execution/target_probe_output.json#custom_tool_discovery"
            ),
            discovered_at=generated_at,
            exposure_state="exposed",
            exposure_mode="model_visible",
            model_visible=True,
            surface_refs=[SURFACE_ID],
            reason="Custom tool was loaded and executed in the L2 runtime probe.",
            visibility="model_visible",
            scopes=["tool:invoke"],
            metadata={
                "kind": "custom_tool",
                "label": custom_tool.get("label"),
                "path": _normalize_path_string(custom_tool["path"]),
                "selected_execution": selected_execution,
                "source": _normalize_value(custom_tool.get("source")),
            },
        )
    )

    command_execution = json.loads(l2_probe["custom_command_discovery"]["selected_execution_text"])
    command_execution = _normalize_value(command_execution)
    for command in l2_probe["custom_command_discovery"]["commands"]:
        name = command["name"]
        metadata = {"kind": "custom_command", **_normalize_value(command)}
        if name == command_execution.get("name"):
            metadata["selected_execution"] = command_execution
            metadata["selected_execution_sha256"] = _sha256_bytes(
                _canonical_json_bytes(command_execution)
            )
        capabilities.append(
            _capability(
                capability_id=f"runtime.oh_my_pi.custom_command.{_safe_id(name)}",
                capability_type="runtime",
                name=name,
                provider=provider,
                discovery_mode="runtime_probe",
                evidence_ref=(
                    "docs/conformance/e4_target_support/"
                    "oh_my_pi_p6_0_l2_tool_execution/target_probe_output.json#custom_command_discovery"
                ),
                discovered_at=generated_at,
                exposure_state="exposed",
                exposure_mode="model_visible",
                model_visible=True,
                surface_refs=[],
                reason="Custom command was discovered in the L2 runtime probe.",
                visibility="model_visible",
                scopes=["command:invoke"],
                metadata=metadata,
            )
        )

    hook = l2_probe["hook_discovery"]["hooks"][0]
    capabilities.append(
        _capability(
            capability_id="hook.oh_my_pi.pre.p6_l2",
            capability_type="extension_hook",
            name="p6_l2",
            provider=provider,
            discovery_mode="runtime_probe",
            evidence_ref=(
                "docs/conformance/e4_target_support/"
                "oh_my_pi_p6_0_l2_tool_execution/target_probe_output.json#hook_discovery"
            ),
            discovered_at=generated_at,
            exposure_state="hidden",
            exposure_mode="host_only",
            model_visible=False,
            surface_refs=[],
            reason="Hook runs as host-side extension logic and is not directly model-callable.",
            visibility="host_only",
            scopes=["hook:execute"],
            metadata={
                "appended_entries": _normalize_value(l2_probe["hook_discovery"]["appended_entries"]),
                "command_names": hook["command_names"],
                "handler_events": hook["handler_events"],
                "kind": "extension_hook",
                "path": _normalize_path_string(hook["path"]),
                "sent_messages": l2_probe["hook_discovery"]["sent_messages"],
            },
        )
    )

    return capabilities


def _build_capability_registry(
    l1_probe: Mapping[str, Any],
    l2_probe: Mapping[str, Any],
    l1_manifest: Mapping[str, Any],
    l2_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    generated_at = max(l1_manifest["generated_at_utc"], l2_manifest["generated_at_utc"])
    target_version = l2_manifest["target_version"]
    capabilities = []
    capabilities.extend(_build_builtin_capabilities(l1_probe, target_version, generated_at))
    capabilities.extend(_build_l2_capabilities(l2_probe, target_version, generated_at))
    capabilities.extend(_build_runtime_l1_capabilities(l1_probe, target_version, generated_at))
    capabilities = sorted(capabilities, key=lambda item: item["capability_id"])
    return {
        "capabilities": capabilities,
        "generated_at": generated_at,
        "registry_id": REGISTRY_ID,
        "schema_version": "bb.capability_registry.v1",
        "subject": {
            "environment_id": ENVIRONMENT_ID,
            "run_id": f"{l1_manifest['run_id']}+{l2_manifest['run_id']}",
        },
    }


def _build_effective_tool_surface(l1_probe: Mapping[str, Any]) -> dict[str, Any]:
    tool_ids = [f"tool.oh_my_pi.builtin.{_safe_id(name)}" for name in l1_probe["builtinToolNames"]]
    tool_ids.append("tool.oh_my_pi.custom.p6_echo")
    tool_ids = sorted(tool_ids)
    record = {
        "binding_ids": [f"binding.oh_my_pi.default.{tool_id}" for tool_id in tool_ids],
        "hidden_tool_ids": [],
        "projection_profile_id": PROFILE_ID,
        "schema_version": "bb.effective_tool_surface.v1",
        "surface_hash": None,
        "surface_id": SURFACE_ID,
        "tool_ids": tool_ids,
    }
    return _with_hash(record, "surface_hash")


def _input_records(l1_manifest: Mapping[str, Any], l2_manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    records: dict[str, dict[str, str]] = {}

    def add(path: str, role: str, sha256: str | None = None) -> None:
        file_path = ROOT / path
        digest = sha256 or _sha256_file(file_path)
        if not _SHA_RE.match(digest):
            raise ValueError(f"invalid sha256 for {path}: {digest}")
        existing = records.get(path)
        if existing is None:
            records[path] = {"path": path, "role": role, "sha256": digest}
        else:
            existing["role"] = "+".join(sorted(set(existing["role"].split("+") + role.split("+"))))
            if existing["sha256"] != digest:
                raise ValueError(f"conflicting sha256 for {path}")

    add(_repo_rel(L1_MANIFEST_PATH), "packet_manifest")
    add(_repo_rel(L2_MANIFEST_PATH), "packet_manifest")

    for path, digest in sorted(l1_manifest["source_hashes"].items()):
        add(path, "l1_source_artifact", digest)
    for path, digest in sorted(l2_manifest["source_hashes"].items()):
        add(path, "l2_source_artifact", digest)

    add(_repo_rel(CONFIG_SCHEMA_PATH), "schema")
    add(_repo_rel(REGISTRY_SCHEMA_PATH), "schema")
    add(_repo_rel(SURFACE_SCHEMA_PATH), "schema")
    return [records[path] for path in sorted(records)]


def _build_manifest(
    *,
    l1_manifest: Mapping[str, Any],
    l2_manifest: Mapping[str, Any],
    inputs: list[dict[str, str]],
    primitive_outputs: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
    validations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    input_hash_preimage = {
        "inputs": inputs,
        "schema_version": "bb.e4.primitive_projection.input_set.v1",
        "target_source": l2_manifest["target_source"],
        "target_version": l2_manifest["target_version"],
    }
    output_rows = []
    for contract, record in sorted(primitive_outputs.items()):
        output_path = output_dir / _OUTPUT_NAMES[contract]
        output_rows.append(
            {
                "contract": contract,
                "path": _repo_rel(output_path),
                "sha256": _sha256_bytes(_canonical_json_bytes(record)),
            }
        )

    return {
        "generated_at": max(l1_manifest["generated_at_utc"], l2_manifest["generated_at_utc"]),
        "input_hash": _sha256_bytes(_canonical_json_bytes(input_hash_preimage)),
        "inputs": inputs,
        "outputs": output_rows,
        "projection_id": PROJECTION_ID,
        "schema_version": "bb.e4.primitive_projection_manifest.v1",
        "source_freeze": {
            "target_source": l2_manifest["target_source"],
            "target_version": l2_manifest["target_version"],
        },
        "target_family": l2_manifest["target_family"],
        "target_version": l2_manifest["target_version"],
        "validations": [validations[contract] for contract in sorted(validations)],
    }


def _write_if_changed(path: Path, content: bytes) -> dict[str, Any]:
    old = path.read_bytes() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    if old != content:
        path.write_bytes(content)
        status = "written"
    else:
        status = "unchanged"
    return {"path": _repo_rel(path), "sha256": _sha256_bytes(content), "status": status}


def build_projection(output_dir: Path | str = OUTPUT_DIR, write: bool = True) -> dict[str, Any]:
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    l1_probe = _read_json(L1_PROBE_PATH)
    l2_probe = _read_json(L2_PROBE_PATH)
    l1_manifest = _read_json(L1_MANIFEST_PATH)
    l2_manifest = _read_json(L2_MANIFEST_PATH)

    if l1_probe["builtinToolNames"] and l2_probe["builtin_tool_count"] != len(l1_probe["builtinToolNames"]):
        raise ValueError("L1 builtin tool names and L2 builtin tool count disagree")
    if l1_probe["selectedSettings"] != l2_probe["selectedSettings"]:
        raise ValueError("L1 and L2 selected settings disagree")

    primitive_outputs = {
        "bb.capability_registry.v1": _build_capability_registry(
            l1_probe, l2_probe, l1_manifest, l2_manifest
        ),
        "bb.effective_config_graph.v1": _build_effective_config_graph(
            l1_probe, l2_probe, l1_manifest, l2_manifest
        ),
        "bb.effective_tool_surface.v1": _build_effective_tool_surface(l1_probe),
    }

    validations: dict[str, dict[str, Any]] = {}
    for contract, record in sorted(primitive_outputs.items()):
        errors = _validate_record(contract, record)
        validations[contract] = {
            "contract": contract,
            "error_count": len(errors),
            "errors": errors,
            "ok": not errors,
            "schema_path": _repo_rel(_SCHEMA_PATHS[contract]),
        }

    inputs = _input_records(l1_manifest, l2_manifest)
    manifest = _build_manifest(
        l1_manifest=l1_manifest,
        l2_manifest=l2_manifest,
        inputs=inputs,
        primitive_outputs=primitive_outputs,
        output_dir=output_dir,
        validations=validations,
    )

    output_payloads: dict[str, bytes] = {}
    for contract, record in primitive_outputs.items():
        output_payloads[_OUTPUT_NAMES[contract]] = _canonical_json_bytes(record)
    output_payloads["primitive_projection_manifest.v1.json"] = _canonical_json_bytes(manifest)

    writes = []
    if write:
        for name in sorted(output_payloads):
            writes.append(_write_if_changed(output_dir / name, output_payloads[name]))
    else:
        for name in sorted(output_payloads):
            path = output_dir / name
            writes.append({"path": _repo_rel(path), "sha256": _sha256_bytes(output_payloads[name]), "status": "dry-run"})

    errors = [error for validation in validations.values() for error in validation["errors"]]
    return {
        "input_hash": manifest["input_hash"],
        "ok": not errors,
        "output_dir": _repo_rel(output_dir),
        "outputs": sorted(writes, key=lambda item: item["path"]),
        "projection_id": PROJECTION_ID,
        "validation_status": sorted(validations.values(), key=lambda item: item["contract"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project Oh-My-Pi P6 L1/L2 packet data into BreadBoard kernel primitive records."
    )
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for projection JSON artifacts")
    parser.add_argument("--json", action="store_true", help="Print a JSON report")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing artifacts")
    args = parser.parse_args(argv)

    try:
        report = build_projection(output_dir=Path(args.output_dir), write=not args.dry_run)
    except Exception as exc:  # pragma: no cover - CLI defensive path.
        if args.json:
            print(_canonical_json_bytes({"error": str(exc), "ok": False}).decode("utf-8"), end="")
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(_canonical_json_bytes(report).decode("utf-8"), end="")
    else:
        for output in report["outputs"]:
            print(f"{output['status']}: {output['path']} {output['sha256']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
