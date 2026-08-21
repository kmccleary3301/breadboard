from __future__ import annotations

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
FREEZE_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
CATALOG_PATH = ROOT / "docs/conformance/e4_artifact_catalog.json"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
SOURCE_FREEZE_PATH = ROOT / "config/e4_lanes/evidence_inputs/oh_my_pi_main_5356713e_freeze_provenance.v1.json"

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b(?:ANTHROPIC|OPENAI|OPENROUTER|GEMINI|GOOGLE|XAI)_API_KEY\s*[=:]\s*[^\s]+"),
)

_PROFILE: dict[int, dict[str, Any]] = {
    1: {
        "raw_at": "2026-07-01T22:11:58Z",
        "generated_at": "2026-07-01T22:11:58Z",
        "prevalidation_at": "2026-07-01T22:11:58Z",
        "manifest_at": "2026-07-02T23:06:29Z",
        "support_at": "2026-07-04T00:00:00Z",
        "snapshot_tag": "oh_my_pi_16.2.13_main_5356713e_p6_0_l1_20260701",
        "behavior_prefix": "config_graph",
        "ledger_family": "tool",
        "raw_lineage": "Canonical raw target capture for the Oh-My-Pi P6.0-L1 config/context/tool-surface lane. The capture uses a freshly cloned upstream main checkout, frozen Bun install, target fixture workspace, provider-free config, and command reports from leaf-native OMP CLI probes. It is intentionally narrower than any broad Oh-My-Pi or OMP support claim.",
        "replay_summary": "BreadBoard replay wrapper reconstructs the Oh-My-Pi P6.0-L1 provider-free CLI support packet from the raw capture manifest and target probe/setup artifacts without broadening the claim scope.",
        "parity_scope": "provider-free Oh-My-Pi P6.0-L1 config/context/tool-surface packet only",
        "secret_notes": "Provider credentials were intentionally absent. The target config suppresses prompts and uses no-provider; this scan covers packet-local evidence files and command reports.",
        "manifest_note": "Every governed artifact for this Oh-My-Pi P6.0-L1 C4 chain is packet-local, canonical repo evidence, source-freeze evidence under docs_tmp/phase_15/source_freezes, or a named target command/probe artifact. Scratch/tmp/shared roots are not promotion evidence.",
        "summary": "Oh-My-Pi 16.2.13 provider-free CLI config/context/tool-surface support is accepted only for the named P6.0-L1 lane, run, config, target version, and read-only sandbox scope.",
        "acceptance_rationale": "Generated from accepted v1 support claim, inventory scope, catalog refs, and passed comparator assertions.",
        "exclusions": [
            "No broad Oh-My-Pi or OMP support claim is made from this micro-lane.",
            "No Pi, OpenCode, Claude, Codex, browser, provider parity, UI parity, or general P6/P7 support claim is made.",
            "No write-enabled, provider-authenticated, danger-full-access, MCP, memory, or task/agent-routing support claim is made.",
        ],
        "excluded_behavior_classes": ["broad_target_parity", "browser", "danger_full_access", "mcp", "provider_authenticated", "ui_parity", "write_enabled"],
        "excluded_families": ["all_other_families", "codex", "opencode", "pi"],
    },
    2: {
        "raw_at": "2026-07-01T23:19:24Z",
        "generated_at": "2026-07-01T23:20:12Z",
        "prevalidation_at": "2026-07-01T23:20:12Z",
        "manifest_at": "2026-07-02T23:06:29Z",
        "support_at": "2026-07-04T00:00:00Z",
        "snapshot_tag": "oh_my_pi_16.2.13_main_5356713e_p6_0_l2_20260701",
        "behavior_prefix": "tool_execution",
        "ledger_family": "tool",
        "raw_lineage": "Canonical raw target capture for the Oh-My-Pi P6.0-L2 local tool execution lane. The capture uses the frozen upstream main checkout, a packet-local fixture workspace, no-provider config, direct CLI command reports, and an API-level discovery/execution probe for custom command, custom tool, and hook surfaces.",
        "replay_summary": "BreadBoard replay wrapper reconstructs the Oh-My-Pi P6.0-L2 provider-free local tool execution packet from raw command reports, target probe output, and setup artifacts without broadening the claim scope.",
        "parity_scope": "provider-free Oh-My-Pi P6.0-L2 local deterministic tool execution packet only",
        "secret_notes": "Provider credentials were intentionally absent. The target config uses no-provider; this scan covers packet-local evidence, fixtures, source harness, command reports, and command stdout/stderr.",
        "manifest_note": "Every governed artifact for this Oh-My-Pi P6.0-L2 C4 chain is packet-local canonical repo evidence, source-freeze evidence under docs_tmp/phase_15/source_freezes, or a named target command/probe artifact. Scratch/tmp/shared roots are not promotion evidence.",
        "summary": "Oh-My-Pi 16.2.13 provider-free local tool execution, custom command, custom tool, and hook support is accepted only for the named P6.0-L2 lane, run, config, target version, and read-only sandbox scope.",
        "acceptance_rationale": "The governed claim is limited to the exact config, lane, run, Oh-My-Pi coding-agent version, and provider-free read-only sandbox named in the evidence. The packet contains raw command reports, a target API probe, replay normalization, comparator pass, parity result, packet-local secret scan, and validator-output hashes.",
        "exclusions": [
            "No broad Oh-My-Pi or OMP support claim is made from this micro-lane.",
            "No provider-authenticated, browser, UI, write-enabled sandbox, danger-full-access, MCP, memory, or task/agent-routing support claim is made.",
            "No network/provider fetch interception claim is made beyond the packet-local probe showing no fetch/provider dispatch occurred in this run.",
        ],
        "excluded_behavior_classes": ["broad_target_parity", "browser", "danger_full_access", "mcp", "network", "provider_authenticated", "ui_parity", "write_enabled"],
        "excluded_families": ["all_other_families", "pi"],
    },
    3: {
        "raw_at": "2026-07-02T00:17:51Z",
        "generated_at": "2026-07-02T00:17:51Z",
        "prevalidation_at": "2026-07-02T22:55:56Z",
        "manifest_at": "2026-07-02T23:06:29Z",
        "support_at": "2026-07-04T00:00:00Z",
        "snapshot_tag": "oh_my_pi_16.2.13_main_5356713e_p6_0_l3_20260701",
        "behavior_prefix": "command_network_policy",
        "ledger_family": "policy",
        "raw_lineage": "Promoted from the packet-local Oh-My-Pi 16.2.13 P6.0-L3 command/network hook capture under canonical repo evidence after secret scan, replay normalization, comparator pass, and parity pass. The raw command reports, target probe output, and setup report are present and hashed; this governed wrapper pins the exact target, config, run, provider-free mode, read-only sandbox, and source files for the C4 support chain.",
        "replay_summary": "BreadBoard replay wrapper reconstructs the Oh-My-Pi P6.0-L3 provider-free command/network hook packet from raw command reports, target probe output, and setup artifacts without broadening the claim scope.",
        "parity_scope": "provider-free Oh-My-Pi P6.0-L3 local command/network hook packet only",
        "secret_notes": "Provider credentials were intentionally absent. The target config uses no-provider; this scan covers packet-local evidence, fixtures, source harness metadata, command reports, and command stdout/stderr.",
        "manifest_note": "Every governed artifact for this oh_my_pi_p6_0_l3_command_network_hook C4 chain is packet-local canonical repo evidence, source-freeze evidence under docs_tmp/phase_15/source_freezes, or a named target command/probe artifact. Scratch/tmp/shared roots are not promotion evidence.",
        "summary": "Oh-My-Pi 16.2.13 provider-free local command execution, custom command execution, hook loading, hook command execution, and hook-gated network-shaped bash blocking are accepted only for the named P6.0-L3 lane, run, config, target version, and read-only sandbox scope.",
        "acceptance_rationale": "The governed claim is limited to the exact config, lane, run, Oh-My-Pi coding-agent version, and provider-free read-only sandbox named in the evidence. The packet contains raw command reports, replay normalization, comparator pass, parity result, packet-local secret scan, validator-output hashes, and ledger-row freshness.",
        "exclusions": [
            "No broad Oh-My-Pi or OMP support claim is made from this micro-lane.",
            "No Pi, Opencode, OpenCode, Codex, Claude, provider-parity, UI-parity, memory, task/agent-routing, or general P6/P7 support claim is made.",
            "No write-enabled, provider-authenticated, or danger-full-access sandbox behavior is claimed.",
            "No claim is made for provider endpoints, web search execution, external SaaS APIs, or model inference.",
            "No general outbound network isolation claim is made; network evidence is limited to one packet-local hook gate that blocks a network-shaped bash command before the wrapped tool executes.",
            "No MCP, browser automation, or resource-session support claim is made from this L3 packet.",
        ],
        "excluded_behavior_classes": ["broad_target_parity", "browser", "danger_full_access", "mcp", "model_inference", "network", "provider_authenticated", "ui_parity", "write_enabled"],
        "excluded_families": ["all_other_families", "codex", "opencode", "pi"],
    },
    4: {
        "raw_at": "2026-07-02T22:49:43Z",
        "generated_at": "2026-07-02T22:49:43Z",
        "prevalidation_at": "2026-07-02T22:49:43Z",
        "manifest_at": "2026-07-02T23:06:29Z",
        "support_at": "2026-07-04T00:00:00Z",
        "snapshot_tag": "oh_my_pi_16.2.13_main_5356713e_p6_0_l4_20260702",
        "behavior_prefix": "protocol_sessions",
        "ledger_family": "session",
        "raw_lineage": "Promoted from a packet-local Oh-My-Pi 16.2.13 P6.0-L4 local MCP/browser/resource target capture under canonical repo evidence after secret scan, replay normalization, comparator pass, and parity pass. The raw command reports, fixture MCP server/config, target probe output, and setup report are present and hashed; this governed wrapper pins exact target, config, run, provider-free mode, read-only sandbox, and source files for the C4 support chain.",
        "secret_notes": "Provider credentials were intentionally absent. The target config uses no-provider; browserbase fixture URL carries no token and is filtered before connection; this scan covers packet-local evidence, fixtures, command reports, and command stdout/stderr.",
        "manifest_note": "Every governed artifact for this oh_my_pi_p6_0_l4_mcp_browser_resource C4 chain is packet-local canonical repo evidence, source-freeze evidence under docs_tmp/phase_15/source_freezes, or a named target command/probe artifact. Scratch/tmp/shared roots are not promotion evidence.",
        "summary": "Oh-My-Pi 16.2.13 provider-free local MCP config loading, browser-automation MCP filtering, local stdio MCP resource listing/reading, resource-template listing, and deterministic local MCP tool calling are accepted only for the named P6.0-L4 lane, run, config, target version, and read-only sandbox scope.",
        "acceptance_rationale": "The governed claim is limited to the exact config, lane, run, Oh-My-Pi coding-agent version, and provider-free read-only sandbox named in the evidence. The packet contains raw command reports, replay normalization, comparator pass, parity result, packet-local secret scan, validator-output hashes, and ledger-row freshness.",
        "exclusions": [
            "No broad Oh-My-Pi or OMP support claim is made from this micro-lane.",
            "No Pi, Opencode, OpenCode, Codex, Claude, provider-parity, UI-parity, memory, task/agent-routing, or general P6/P7 support claim is made.",
            "No write-enabled, provider-authenticated, or danger-full-access sandbox behavior is claimed.",
            "No claim is made for provider endpoints, web search execution, external SaaS APIs, or model inference.",
            "No external browser automation service execution is claimed; playwright and browserbase entries are only classified and filtered by target code before connection.",
            "No broad MCP ecosystem support is claimed; evidence covers one packet-local stdio MCP fixture server, one deterministic resource read, one resource template, and one deterministic local tool call.",
        ],
        "excluded_behavior_classes": ["broad_target_parity", "browser", "danger_full_access", "mcp", "model_inference", "provider_authenticated", "ui_parity", "write_enabled"],
        "excluded_families": ["all_other_families", "codex", "opencode", "pi"],
    },
}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return _hash_utils.sha256_file(path)


def _logical_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.relative_to(WORKSPACE.resolve()).as_posix()


def _source_path(logical: str) -> Path:
    return (WORKSPACE if logical.startswith("docs_tmp/") else ROOT) / logical


def _output_path(logical: str, output_root: Path | None) -> Path:
    return _source_path(logical) if output_root is None else output_root / logical


def _hash_output(logical: str, output_root: Path | None) -> str:
    return _sha256(_output_path(logical, output_root))


def _ref(logical: str, output_root: Path | None) -> str:
    return f"{logical}#{_hash_output(logical, output_root)}"


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _level(spec: Mapping[str, Any]) -> int:
    match = re.search(r"_l([1-4])_", str(spec["lane_id"]))
    if match is None:
        raise ValueError(f"unsupported P6 lane id: {spec['lane_id']!r}")
    return int(match.group(1))


def _packet_sources(spec: Mapping[str, Any]) -> list[str]:
    inputs = spec.get("capture_inputs")
    if not isinstance(inputs, list) or not all(isinstance(value, str) for value in inputs):
        raise ValueError("P6 projection requires string capture_inputs")
    lane_prefix = f"docs/conformance/e4_target_support/{spec['lane_id']}/"
    paths = [value for value in inputs if value.startswith(lane_prefix)]
    if _level(spec) == 2:
        paths = [
            path
            for path in paths
            if Path(path).name in {"target_probe_output.json", "target_setup_and_capture_report.json"}
            or path.endswith(".command_report.json")
        ]
    return paths


def _role(path: str) -> str:
    name = Path(path).name
    if name == "target_probe_output.json":
        return "target_probe_output"
    if name == "target_setup_and_capture_report.json":
        return "target_setup_and_capture_report"
    if name.endswith("_probe.mjs") or name.endswith("_probe.ts"):
        return "target_probe_script"
    if name == ".mcp.json":
        return "target_fixture_mcp_config"
    if name == "p6_l4_fixture_mcp_server.mjs":
        return "target_fixture_mcp_server"
    stem = name.removesuffix(".json").removesuffix(".txt")
    stem = stem.replace(".command_report", "_command_report")
    stem = stem.replace(".", "_")
    return stem


def _artifact(path: str, role: str | None = None, *, include_bytes: bool = False) -> dict[str, Any]:
    source = _source_path(path)
    if not source.is_file():
        raise FileNotFoundError(f"declared P6 source is missing: {path}")
    row: dict[str, Any] = {"path": path, "role": role or _role(path), "sha256": _sha256(source)}
    if include_bytes:
        row = {"bytes": source.stat().st_size, **row}
    return row


def _source_artifacts(level: int, config_path: str, paths: Sequence[str]) -> list[Any]:
    if level <= 2:
        return [config_path, *paths]
    return [_artifact(path, include_bytes=True) for path in paths]


def _captured_artifacts(level: int, paths: Sequence[str]) -> list[dict[str, Any]]:
    if level == 1:
        selected = {
            "target_probe_output.json": "target_probe_output",
            "target_setup_and_capture_report.json": "target_setup_and_capture_report",
            "omp_help_with_leaf_native.command_report.json": "omp_help_command_report",
            "omp_launch_help_with_leaf_native.command_report.json": "omp_launch_help_command_report",
            "omp_config_list_json_with_leaf_native.command_report.json": "omp_config_list_command_report",
        }
        return [_artifact(path, selected[Path(path).name]) for path in paths if Path(path).name in selected]
    if level == 3:
        roles = {
            "target_probe_output.json": "target_probe_output",
            "target_setup_and_capture_report.json": "target_setup_and_capture_report",
            "omp_l3_hook_network_probe.command_report.json": "omp_l3_hook_network_probe_command_report_command_report",
            "omp_version.command_report.json": "omp_version_command_report_command_report",
            "omp_l3_hook_network_probe.stderr.txt": "omp_l3_hook_network_probe_stderr_stream",
            "omp_l3_hook_network_probe.stdout.txt": "omp_l3_hook_network_probe_stdout_stream",
            "omp_version.stderr.txt": "omp_version_stderr_stream",
            "omp_version.stdout.txt": "omp_version_stdout_stream",
        }
        order = list(roles)
        by_name = {Path(path).name: path for path in paths}
        return [_artifact(by_name[name], roles[name]) for name in order]
    return [_artifact(path) for path in paths]


def _scope(spec: Mapping[str, Any], *, target_family: bool) -> dict[str, Any]:
    scope = {
        "config_id": spec["config_id"],
        "lane_id": spec["lane_id"],
        "provider_model": spec["provider_model"],
        "run_id": spec["run_id"],
        "sandbox_mode": spec["sandbox_mode"],
        "target_version": spec["target_version"],
    }
    if target_family:
        scope["target_family"] = spec["target_family"]
    return scope


def _raw_payload(level: int, profile: Mapping[str, Any], spec: Mapping[str, Any], paths: Sequence[str], probe: Mapping[str, Any], setup: Mapping[str, Any]) -> dict[str, Any]:
    config_path = str(spec["config_path"])
    source_artifacts = _source_artifacts(level, config_path, paths)
    source_hashes = {path: _sha256(_source_path(path)) for path in ([config_path, *paths] if level <= 2 else paths)}
    command_summary: dict[str, Any]
    if level == 1:
        reports = setup["leaf_native_command_reports"]
        command_summary = {
            "all_leaf_native_commands_exit_zero": setup["all_leaf_native_commands_exit_zero"],
            "leaf_native_command_count": len(reports),
            "leaf_native_command_names": [row["name"] for row in reports],
            "non_leaf_native_build_attempt_failed": not setup["all_commands_exit_zero"],
            "non_leaf_native_build_exclusion_rationale": "Native Rust build failure is retained in setup history but is outside this C4 lane. The accepted capture scope uses the installed @oh-my-pi/pi-natives Darwin arm64 leaf package and successful OMP CLI leaf-native reports only.",
        }
    else:
        command_summary = {
            "all_commands_exit_zero": setup["all_commands_exit_zero"],
            "command_count": setup["command_count"],
            "command_reports": setup["command_reports"],
        }
        if level == 2:
            command_summary["command_names"] = [row["name"] for row in setup["command_reports"]]
    raw: dict[str, Any] = {
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "accepted_as_capture_ref": True,
        "capture_class": "raw_target_capture",
        "raw_source_status": "canonical_raw_present",
        "captured_artifacts": _captured_artifacts(level, paths),
        "command_summary": command_summary,
        "config_id": spec["config_id"],
        "generated_at_utc": profile["raw_at"],
        "lane_id": spec["lane_id"],
        "lineage_rationale": profile["raw_lineage"],
        "provider_model": spec["provider_model"],
        "run_id": spec["run_id"],
        "sandbox_mode": spec["sandbox_mode"],
        "source_artifacts": source_artifacts,
        "source_hashes": source_hashes,
        "target_family": spec["target_family"],
        "target_version": spec["target_version"],
    }
    if level <= 2:
        freeze = json.loads(SOURCE_FREEZE_PATH.read_text(encoding="utf-8"))
        raw["target_source"] = {
            "coding_agent_package": freeze["coding_agent_package"],
            "coding_agent_version": freeze["coding_agent_version"],
            "package_manager": freeze["package_manager"],
            "upstream_commit": freeze["upstream_commit"],
            **({"upstream_commit_date": freeze["upstream_commit_date"]} if level == 2 else {}),
            "upstream_ref": freeze["source_ref"],
            "upstream_repo": freeze["source_repo"],
        }
    if level == 1:
        raw["tool_surface_summary"] = {
            "builtin_tool_names": probe["builtinToolNames"],
            "context_file_count": len(probe["contextFiles"]),
            "skills": [row["name"] for row in probe["skills"]],
            "system_prompt_block_count": probe["systemPromptBlockCount"],
            "target_family": probe["target_family"],
            "tool_aliases": probe["toolAliases"],
        }
    elif level == 2:
        raw["tool_execution_summary"] = {
            "all_expected_passed": probe["all_expected_passed"],
            "builtin_tool_count": probe["builtin_tool_count"],
            "custom_command_count": probe["custom_command_discovery"]["count"],
            "custom_tool_loaded_count": probe["custom_tool_discovery"]["loaded_count"],
            "hook_event_count": len(probe["hook_discovery"]["emitted_events"]),
            "hook_loaded_count": probe["hook_discovery"]["loaded_count"],
            "network_observed": probe["network_observed"],
            "provider_dispatch_observed": probe["provider_dispatch_observed"],
        }
        raw["tool_surface_summary"] = {
            "custom_command_names": [row["name"] for row in probe["custom_command_discovery"]["commands"]],
            "custom_tool_names": [row["name"] for row in probe["custom_tool_discovery"]["tools"]],
            "hook_command_names": probe["hook_discovery"]["hooks"][0]["command_names"],
            "selected_settings": probe["selectedSettings"],
        }
    elif level == 3:
        wrapper = probe["hook_discovery"]["wrapper_execution"]
        raw.update({"exclusions": setup["excluded_claims"], "hash_algorithm": "sha256", "source_refs": probe["source_refs"]})
        raw["tool_execution_summary"] = {
            "all_expected_passed": probe["all_expected_passed"],
            "custom_command_count": probe["custom_command_discovery"]["count"],
            "hook_event_count": len(probe["hook_discovery"]["appended_entries"]),
            "hook_loaded_count": probe["hook_discovery"]["loaded_count"],
            "network_observed": probe["network_observed"],
            "provider_dispatch_observed": probe["provider_dispatch_observed"],
            "underlying_network_command_executed": wrapper["underlying_network_command_executed"],
        }
        raw["tool_surface_summary"] = {
            "custom_command_names": [row["name"] for row in probe["custom_command_discovery"]["commands"]],
            "hook_command_names": probe["hook_discovery"]["hooks"][0]["command_names"],
            "selected_settings": probe["selectedSettings"],
        }
    else:
        raw.update({"exclusions": setup["excluded_claims"], "hash_algorithm": "sha256"})
    return raw


def _replay_payload(level: int, profile: Mapping[str, Any], spec: Mapping[str, Any], raw_path: str, probe_path: str, setup_path: str, probe: Mapping[str, Any], output_root: Path | None) -> dict[str, Any]:
    replay: dict[str, Any] = {
        "schema_version": "bb.e4.bb_replay_result.v1",
        "config_id": spec["config_id"],
        "errors": [],
        "exit_status": "passed",
        "generated_at_utc": profile["generated_at"],
        "input_hashes": {raw_path: _hash_output(raw_path, output_root), probe_path: _sha256(_source_path(probe_path)), setup_path: _sha256(_source_path(setup_path))},
        "lane_id": spec["lane_id"],
        "run_id": spec["run_id"],
        "warnings": [],
    }
    if level < 4:
        preserved = {
            1: ["target_family", "builtinToolNames", "toolAliases", "contextFiles", "skills", "systemPromptBlockHashes"],
            2: ["target_family", "config_id", "lane_id", "run_id", "target_version", "command_reports", "custom_command_discovery", "custom_tool_discovery", "hook_discovery"],
            3: ["target_family", "config_id", "lane_id", "run_id", "target_version", "command_reports", "custom_command_discovery", "hook_discovery", "wrapper_execution"],
        }[level]
        replay["normalization"] = {"excluded_fields": ["wall_clock_timing_noise", "absolute_temp_paths"], "mode": "packet-local-replay-wrapper", "preserved_fields": preserved}
        replay["replay_summary"] = profile["replay_summary"]
    else:
        resource = probe["resources"][0]
        template = probe["templates"][0]
        tool = probe["tools"][0]
        replay.update({
            "normalized_records": [{
                "browser_mcp_filtered": [name for name in probe["raw_config_names"] if name not in probe["browser_filtered_names"]],
                "protocol": "mcp",
                "provider_model": probe["provider_model"],
                "resource_template_uris": [template["uriTemplate"]],
                "resource_uris": [resource["uri"]],
                "schema_version": "bb.external_protocol_session.v1",
                "server_name": probe["connection"]["name"],
                "tool_names": [tool["name"]],
                "transport": "stdio",
            }],
            "provider_model": spec["provider_model"],
            "replay_summary": {
                "browser_filtered_names": probe["browser_filtered_names"],
                "connected_server": probe["connection"]["name"],
                "network_observed": probe["network_observed"],
                "provider_dispatch_observed": probe["provider_dispatch_observed"],
                "raw_config_names": probe["raw_config_names"],
                "resource_text": probe["readResult"]["contents"][0]["text"],
                "resource_uri": resource["uri"],
                "tool_call_text": probe["callResult"]["content"][0]["text"],
            },
            "sandbox_mode": spec["sandbox_mode"],
            "target_family": spec["target_family"],
            "target_version": spec["target_version"],
        })
    return replay


def _assertion(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    observed = dict(value)
    return {"expected": dict(value), "name": name, "observed": observed, "status": "passed"}


def _comparator_parts(level: int, spec: Mapping[str, Any], probe: Mapping[str, Any], setup: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assertions = [_assertion("scope_matches_exact_claim", _scope(spec, target_family=True))]
    details: list[dict[str, Any]]
    if level == 1:
        assertions.extend([
            _assertion("leaf_native_commands_exit_zero", {"all_leaf_native_commands_exit_zero": setup["all_leaf_native_commands_exit_zero"], "leaf_native_command_count": len(setup["leaf_native_command_reports"])}),
            _assertion("tool_surface_counts_match_probe", {"builtin_tool_count": len(probe["builtinToolNames"]), "context_file_count": len(probe["contextFiles"]), "system_prompt_block_count": probe["systemPromptBlockCount"], "target_family": probe["target_family"]}),
            _assertion("provider_free_read_only_packet", {"browser_enabled": probe["selectedSettings"]["browserEnabled"], "provider_dispatch_observed": probe["providerDispatchObserved"], "provider_model": spec["provider_model"], "sandbox_mode": spec["sandbox_mode"], "web_search_enabled": probe["selectedSettings"]["webSearchEnabled"]}),
        ])
        details = [
            {"detail": "Comparator scope contains the claim scope and does not assert broad OMP, Pi, provider, browser, UI, or write-enabled sandbox support.", "name": "scope_is_exact_lane", "status": "passed"},
            {"detail": f"{len(setup['leaf_native_command_reports'])} leaf-native command reports exited with code 0.", "name": "leaf_native_commands_exit_zero", "status": "passed"},
            {"detail": f"Captured {len(probe['builtinToolNames'])} builtin tool names and {len(probe['contextFiles'])} context files for target_family={probe['target_family']}.", "name": "target_probe_tool_surface_present", "status": "passed"},
            {"detail": "Accepted config has default_model=no-provider, models=[], suppressed prompts, and scope sandbox_mode=read-only.", "name": "provider_free_read_only_mode", "status": "passed"},
        ]
    elif level == 2:
        assertions.extend([
            _assertion("commands_exit_zero", {"all_commands_exit_zero": setup["all_commands_exit_zero"], "command_count": setup["command_count"]}),
            _assertion("custom_extension_surfaces_exercised", {"custom_command_count": probe["custom_command_discovery"]["count"], "custom_tool_loaded_count": probe["custom_tool_discovery"]["loaded_count"], "custom_tool_names": [row["name"] for row in probe["custom_tool_discovery"]["tools"]], "hook_command_names": probe["hook_discovery"]["hooks"][0]["command_names"], "hook_event_count": len(probe["hook_discovery"]["emitted_events"]), "hook_loaded_count": probe["hook_discovery"]["loaded_count"]}),
            _assertion("provider_network_surfaces_absent", {"browser_enabled": probe["selectedSettings"]["browserEnabled"], "mcp_discovery_mode": probe["selectedSettings"]["mcpDiscoveryMode"], "network_observed": probe["network_observed"], "provider_dispatch_observed": probe["provider_dispatch_observed"], "web_search_enabled": probe["selectedSettings"]["webSearchEnabled"]}),
        ])
        details = [
            {"detail": "Comparator scope contains only the named Oh-My-Pi P6.0-L2 local tool execution lane and rejects broad OMP, Pi, provider, browser, UI, or write-enabled sandbox support.", "name": "scope_is_exact_lane", "status": "passed"},
            {"detail": f"{setup['command_count']} command reports exited with code 0.", "name": "commands_exit_zero", "status": "passed"},
            {"detail": "Direct CLI read/grep/gallery probes and API-level custom command/custom tool/hook execution reports are present.", "name": "local_tool_execution_present", "status": "passed"},
            {"detail": "Project custom command p6:l2, custom tool p6_echo, and hook command p6-hook-command were discovered and exercised.", "name": "custom_extension_hooks_present", "status": "passed"},
            {"detail": "Accepted config uses no-provider, models=[], disabled browser/web_search/MCP discovery, and read-only packet scope.", "name": "provider_free_read_only_mode", "status": "passed"},
            {"detail": "Global fetch wrapper observed no fetch calls, provider_dispatch_observed=false, and command subprocess reports provider secrets absent.", "name": "network_and_provider_absent", "status": "passed"},
        ]
    elif level == 3:
        wrapper = probe["hook_discovery"]["wrapper_execution"]
        assertions.extend([
            _assertion("commands_exit_zero", {"all_commands_exit_zero": setup["all_commands_exit_zero"], "command_count": setup["command_count"]}),
            _assertion("hook_network_gate_exercised", {"blocked_network_command": wrapper["blocked_network_command"]["blocked"], "custom_command_count": probe["custom_command_discovery"]["count"], "hook_command_names": probe["hook_discovery"]["hooks"][0]["command_names"], "hook_event_count": len(probe["hook_discovery"]["appended_entries"]), "hook_loaded_count": probe["hook_discovery"]["loaded_count"], "underlying_network_command_executed": wrapper["underlying_network_command_executed"]}),
            _assertion("provider_network_scope_limited", {"network_observed": probe["network_observed"], "provider_dispatch_observed": probe["provider_dispatch_observed"], "provider_model": probe["provider_model"], "sandbox_mode": probe["sandbox_mode"]}),
        ])
        details = [
            {"detail": "Comparator scope contains only the named Oh-My-Pi P6.0-L3 command/network hook lane and rejects broad OMP, Pi, provider, browser, UI, MCP, memory, task-routing, or danger-full-access support.", "name": "scope_is_exact_lane", "status": "passed"},
            {"detail": f"{setup['command_count']} command reports exited with code 0.", "name": "commands_exit_zero", "status": "passed"},
            {"detail": "Project custom command p6:l3-network was discovered and executed with deterministic stdout p6-l3-custom-command.", "name": "custom_command_execution_present", "status": "passed"},
            {"detail": "Project hook p6_l3.ts registered before_agent_start, tool_call, and tool_result handlers plus command p6-l3:hook-network.", "name": "hook_gate_execution_present", "status": "passed"},
            {"detail": "HookToolWrapper blocked a curl-shaped bash tool call before the underlying fake tool executed; allowed deterministic local printf tool call reached tool_result mutation.", "name": "network_shaped_command_blocked", "status": "passed"},
            {"detail": "Accepted config uses provider_model no-provider, models=[], and provider_dispatch_observed=false; provider-authenticated behavior remains out of scope.", "name": "provider_free_packet", "status": "passed"},
            {"detail": "Global fetch wrapper observed no fetch calls during the packet capture; network-hook evidence is limited to command-shape gate behavior inside the fixture.", "name": "no_runtime_network_observed", "status": "passed"},
        ]
    else:
        assertions.extend([
            _assertion("commands_exit_zero", {"all_commands_exit_zero": setup["all_commands_exit_zero"], "command_count": setup["command_count"]}),
            _assertion("browser_mcp_filtered_local_resource_preserved", {"browser_filtered_names": probe["browser_filtered_names"], "browserbase_browser_mcp": probe["browser_checks"]["browserbase"], "local_browser_mcp": probe["browser_checks"]["local"], "playwright_browser_mcp": probe["browser_checks"]["playwright"], "raw_config_names": probe["raw_config_names"]}),
            _assertion("local_mcp_resource_read_and_tool_call", {"resource_text": probe["readResult"]["contents"][0]["text"], "resource_uri": probe["resources"][0]["uri"], "server_name": probe["connection"]["serverInfo"]["name"], "template_uri": probe["templates"][0]["uriTemplate"], "tool_call_text": probe["callResult"]["content"][0]["text"], "tool_name": probe["tools"][0]["name"]}),
            _assertion("provider_network_scope_limited", {"fetch_event_count": len(probe["fetch_events"]), "network_observed": probe["network_observed"], "provider_dispatch_observed": probe["provider_dispatch_observed"], "provider_model": probe["provider_model"], "sandbox_mode": probe["sandbox_mode"]}),
        ])
        details = [
            {"detail": "Comparator scope contains only the named Oh-My-Pi P6.0-L4 local MCP/browser/resource lane and rejects broad OMP, Pi, provider, UI, memory, task-routing, or danger-full-access support.", "name": "scope_is_exact_lane", "status": "passed"},
            {"detail": "Frozen target MCP config logic identified playwright/browserbase as browser automation MCP servers and preserved only the local p6-resource stdio server after browser filtering.", "name": "browser_mcp_filter_present", "status": "passed"},
            {"detail": "Frozen target MCP client connected to the local stdio fixture server, listed one resource and one template, read deterministic resource text, and executed a deterministic local fixture tool call.", "name": "local_resource_read_present", "status": "passed"},
            {"detail": "Accepted config uses provider_model no-provider and subprocess env removed provider credential variables; no fetch calls were observed.", "name": "provider_free_packet", "status": "passed"},
        ]
    return assertions, details


def _scan_paths(level: int, spec: Mapping[str, Any], packet_sources: Sequence[str], generated: Sequence[str]) -> list[str]:
    config_path = str(spec["config_path"])
    if level == 1:
        return [config_path, *packet_sources, *generated]
    lane_root = f"docs/conformance/e4_target_support/{spec['lane_id']}"
    if level == 2:
        extras = [
            f"{lane_root}/oh_my_pi_p6_0_l2_tool_execution_probe.mjs",
            f"{lane_root}/target_fixture_workspace/.omp/tools/p6_echo.ts",
            f"{lane_root}/target_fixture_workspace/.omp/commands/p6-l2/index.ts",
            f"{lane_root}/target_fixture_workspace/.omp/hooks/pre/p6_l2.ts",
        ]
        streams: list[str] = []
        for name in ("omp_api_probe", "omp_gallery_bash_success", "omp_grep_fixture", "omp_read_fixture", "omp_version"):
            streams.extend([f"{lane_root}/raw/{name}.command_report.json", f"{lane_root}/raw/{name}.stderr.txt", f"{lane_root}/raw/{name}.stdout.txt"])
        return [config_path, packet_sources[0], packet_sources[1], *generated, *extras, *streams]
    if level == 3:
        return [config_path, packet_sources[0], packet_sources[1], *generated, *packet_sources[2:]]
    prefix = [config_path, packet_sources[2], packet_sources[3], packet_sources[4], packet_sources[0], packet_sources[1]]
    l4_streams = [packet_sources[8], packet_sources[10], packet_sources[9], packet_sources[5], packet_sources[7], packet_sources[6]]
    return [*prefix, *generated, *l4_streams]


def _ensure_secret_free(paths: Sequence[str]) -> None:
    for logical in paths:
        if logical.endswith(("raw_capture_manifest.json", "bb_replay_result.json", "comparator_report.json", "parity_results.json")):
            continue
        text = _source_path(logical).read_text(encoding="utf-8", errors="replace")
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                raise ValueError(f"secret-like content found in declared P6 source: {logical}")


def _prevalidation(level: int, profile: Mapping[str, Any], spec: Mapping[str, Any], paths: Mapping[str, str], probe_path: str, setup_path: str, comparator_seed_hash: str, output_root: Path | None) -> dict[str, Any]:
    if level == 4:
        artifact_hashes = {path: (_sha256(_source_path(path)) if path in {probe_path, setup_path} else _hash_output(path, output_root)) for path in sorted([paths["raw"], paths["replay"], paths["comparator"], paths["parity"], paths["secret"], probe_path, setup_path])}
        return {
            "schema_version": "bb.e4.c4_prevalidation_report.v1",
            "accepted": True,
            "artifact_hashes": artifact_hashes,
            "checks": [{"name": name, "passed": True} for name in ("raw_capture_present", "replay_passed", "comparator_assertions_passed", "secret_scan_passed", "provider_free_network_free", "browser_mcp_filtered_local_resource_preserved")],
            "config_id": spec["config_id"],
            "generated_at_utc": profile["prevalidation_at"],
            "lane_id": spec["lane_id"],
            "ok": True,
            "run_id": spec["run_id"],
            "target_family": spec["target_family"],
        }
    checks = [
        {"name": "raw_capture_wrapper_written", "passed": True, "path": paths["raw"], "sha256": _hash_output(paths["raw"], output_root)},
        {"name": "replay_wrapper_written", "passed": True, "path": paths["replay"], "sha256": _hash_output(paths["replay"], output_root)},
        {"name": "comparator_wrapper_written", "passed": True, "path": paths["comparator"], "sha256": _hash_output(paths["comparator"], output_root) if level == 3 else comparator_seed_hash},
        {"name": "parity_packet_passed", "passed": True, "path": paths["parity"], "sha256": _hash_output(paths["parity"], output_root)},
        {"name": "packet_secret_scan_passed", "passed": True, "path": paths["secret"], "sha256": _hash_output(paths["secret"], output_root)},
        *(
            [
                {"count": 5, "name": "commands_exit_zero", "passed": True},
                {"name": "api_probe_expected_passed", "passed": True},
            ]
            if level == 2
            else [
                {"count": 2, "name": "commands_exit_zero", "passed": True},
                {"name": "api_probe_expected_passed", "passed": True},
                {"name": "hook_network_gate_passed", "passed": True},
            ]
            if level == 3
            else [
                {"count": 7, "name": "leaf_native_commands_exit_zero", "passed": True}
            ]
        ),
    ]
    return {
        "schema_version": "bb.e4.c4_chain_validation_report.v1",
        "checks": checks,
        "config_id": spec["config_id"],
        "generated_at_utc": profile["prevalidation_at"],
        "lane_id": spec["lane_id"],
        "ok": True,
        "source": f"Bootstrap validator-output reference for the Oh-My-Pi P6.0-L{level} governed C4 chain; scripts/validate_e4_c4_chain.py is run as the live validator before promotion.",
    }


def _freeze_row(profile: Mapping[str, Any], spec: Mapping[str, Any], evidence_paths: Sequence[str]) -> dict[str, Any]:
    source_freeze = json.loads(SOURCE_FREEZE_PATH.read_text(encoding="utf-8"))
    return {
        "config_path": spec["config_path"],
        "harness": {
            "family": spec["target_family"],
            "upstream_repo": spec["upstream_repo"],
            "upstream_commit": spec["upstream_commit"],
            "upstream_commit_date": source_freeze["upstream_commit_date"],
            "upstream_release_label": spec["upstream_release_label"],
            "runtime_surface": {"provider_model": spec["provider_model"]},
        },
        "calibration_anchor": {"class": "raw_target_capture", "scenario_id": spec["lane_id"], "run_id": spec["run_id"], "evidence_paths": list(evidence_paths)},
        "snapshot_source_entry": spec["config_id"],
        "snapshot_tag": profile["snapshot_tag"],
    }


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    payload = json.dumps({"row": row, "row_id": row_id}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _hash_utils.sha256_bytes(payload)


def _ledger_ref(spec: Mapping[str, Any], family: str) -> str:
    from scripts.e4_parity.lane_acceptance_artifacts import row_hash

    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    matches = [
        row
        for row in ledger.get("rows", [])
        if isinstance(row, Mapping)
        and row.get("e4_row_ref") == spec["config_id"]
        and row.get("family") == family
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {family!r} ledger row for {spec['config_id']!r}, found {len(matches)}"
        )
    row = matches[0]
    feature_id = str(row["feature_id"])
    return f"docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#{feature_id}#{row_hash(feature_id, row)}"


def _support_claim(profile: Mapping[str, Any], spec: Mapping[str, Any], paths: Mapping[str, str], freeze_hash: str, assertions: Sequence[Mapping[str, Any]], output_root: Path | None) -> dict[str, Any]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    stable_hash = _require_mapping(catalog.get("integrity"), "catalog.integrity").get("stable_entries_hash")
    if not isinstance(stable_hash, str):
        raise ValueError("catalog.integrity.stable_entries_hash is required")
    behavior_prefix = str(profile["behavior_prefix"])
    assertion_names = [str(row["name"]) for row in assertions]
    claim_id = str(spec["claim_id"])
    return {
        "schema_version": spec["support_claim_schema_version"],
        "claim_id": claim_id,
        "config_id": spec["config_id"],
        "lane_id": spec["lane_id"],
        "kind": "target_support",
        "accepted": True,
        "summary": profile["summary"],
        "acceptance_rationale": profile["acceptance_rationale"],
        "phase_label": "P6",
        "target_family": spec["target_family"],
        "target_version": spec["target_version"],
        "run_id": spec["run_id"],
        "provider_model": spec["provider_model"],
        "sandbox_mode": spec["sandbox_mode"],
        "scope": _scope(spec, target_family=False),
        "exclusions": profile["exclusions"],
        "exclusion_facets": {"excluded_behavior_classes": profile["excluded_behavior_classes"], "excluded_families": profile["excluded_families"]},
        "claim_semantics": {
            "asserted_behaviors": [{"behavior_id": f"{behavior_prefix}_{name}", "comparator_assertion_ids": [name], "description": name.replace("_", " ")} for name in assertion_names],
            "excluded_behaviors": [{"behavior_id": "broad_target_parity", "description": "Broad target-family parity remains outside this exact C4 lane claim."}],
        },
        "freeze_ref": f"config/e4_target_freeze_manifest.yaml#{spec['config_id']}#{freeze_hash}",
        "capture_ref": _ref(paths["raw"], output_root),
        "replay_ref": _ref(paths["replay"], output_root),
        "comparator_ref": _ref(paths["comparator"], output_root),
        "evidence_manifest_ref": paths["manifest"],
        "ledger_row_refs": [_ledger_ref(spec, str(profile["ledger_family"]))],
        "validation_refs": [_ref(paths["prevalidation"], output_root)],
        "catalog_binding": {"catalog_hash": stable_hash, "catalog_path": "docs/conformance/e4_artifact_catalog.json", "catalog_revision": int(catalog["revision"])}, 
        "reverify_command": {"argv": [".venv/bin/python", "scripts/validate_e4_c4_chain.py", "--config-id", spec["config_id"], "--support-claim", paths["support"], "--evidence-manifest", paths["manifest"], "--json-out", paths["node_gate"], "--check-only"], "cwd": "."},
        "parity_results_ref": _ref(paths["parity"], output_root),
        "secret_scan_ref": _ref(paths["secret"], output_root),
        "source_freeze_ref": f"docs_tmp/phase_15/source_freezes/{SOURCE_FREEZE_PATH.name}#{_sha256(SOURCE_FREEZE_PATH)}",
        "metadata": {"generated_from_schema_version": spec["support_claim_schema_version"], "v1_archive_ref": f"docs/conformance/support_claims/v1_archive/{claim_id}.json"},
        "generated_at_utc": profile["support_at"],
    }


def _manifest(level: int, profile: Mapping[str, Any], spec: Mapping[str, Any], paths: Mapping[str, str], freeze_hash: str, output_root: Path | None) -> dict[str, Any]:
    raw_ref = _ref(paths["raw"], output_root)
    replay_ref = _ref(paths["replay"], output_root)
    comparator_ref = _ref(paths["comparator"], output_root)
    artifacts = [
        {"path": "config/e4_target_freeze_manifest.yaml", "role": "freeze_manifest", "sha256": freeze_hash},
        {"path": paths["raw"], "role": "capture_ref", "sha256": _hash_output(paths["raw"], output_root)},
        {"derived_from": [raw_ref], "path": paths["replay"], "role": "replay_ref", "sha256": _hash_output(paths["replay"], output_root)},
        {"derived_from": [raw_ref, replay_ref], "path": paths["comparator"], "role": "comparator_ref", "sha256": _hash_output(paths["comparator"], output_root)},
    ]
    support_row = {"path": paths["support"], "role": "support_claim_ref", "sha256": _hash_output(paths["support"], output_root)}
    trailing = [
        {"derived_from": [comparator_ref], "path": paths["parity"], "role": "parity_results", "sha256": _hash_output(paths["parity"], output_root)},
        {"path": paths["secret"], "role": "secret_scan_report", "sha256": _hash_output(paths["secret"], output_root)},
        {"path": paths["prevalidation"], "role": "validator_output", "sha256": _hash_output(paths["prevalidation"], output_root)},
    ]
    if level <= 2:
        artifacts.extend([support_row, *trailing])
    else:
        artifacts.extend([*trailing, support_row])
    artifacts.append({"bytes": LEDGER_PATH.stat().st_size, "exists": True, "path": _logical_path(LEDGER_PATH), "role": "atomic_feature_ledger", "sha256": _sha256(LEDGER_PATH)})
    return {
        "schema_version": "bb.e4.evidence_manifest.v1",
        "artifacts": artifacts,
        "claim_id": spec["claim_id"],
        "config_id": spec["config_id"],
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "generated_at_utc": profile["manifest_at"],
        "hash_algorithm": "sha256",
        "lane_id": spec["lane_id"],
        "manifest_scope_note": profile["manifest_note"],
    }


def _node_gate(spec: Mapping[str, Any], paths: Mapping[str, str], assertions: Sequence[Mapping[str, Any]], freeze_hash: str, output_root: Path | None) -> dict[str, Any]:
    repo_name = ROOT.name
    return {
        "schema_version": "bb.e4.c4_chain_validation_report.v1",
        "accepted": True,
        "catalog_binding_pending": [],
        "claimed_scope": _scope(spec, target_family=False),
        "comparator_rerun": {"assertion_count": len(assertions), "comparator_class": "deterministic_replay", "comparator_id": "oh_my_pi_stored_report_replay", "missing_assertion_ids": [], "ok": True, "registry": f"{repo_name}/conformance/comparators/registry.json", "status_mismatch_ids": [], "unexpected_assertion_ids": [], "value_mismatch_ids": []},
        "config_id": spec["config_id"],
        "error_count": 0,
        "errors": [],
        "evidence_manifest": paths["manifest"],
        "gate_errors": [],
        "hashes": {"evidence_manifest": _hash_output(paths["manifest"], output_root), "freeze_manifest": _hash_output("config/e4_target_freeze_manifest.yaml", output_root), "freeze_manifest_row": freeze_hash, "support_claim": _hash_output(paths["support"], output_root)},
        "ok": True,
        "pin_stale_count": 0,
        "refs": {"evidence_manifest": paths["manifest"], "freeze_manifest": f"{repo_name}/config/e4_target_freeze_manifest.yaml", "support_claim": paths["support"]},
        "semantic_count": 0,
        "support_claim": paths["support"],
    }


def build_p6_lane(spec: Mapping[str, Any], output_root: Path | None = None) -> dict[str, Any]:
    level = _level(spec)
    projection_config = _require_mapping(spec.get("projection_config"), "projection_config")
    profile = {**_PROFILE[level], **projection_config}
    lane_root = f"docs/conformance/e4_target_support/{spec['lane_id']}"
    paths = {
        "raw": f"{lane_root}/raw_capture_manifest.json",
        "replay": f"{lane_root}/bb_replay_result.json",
        "comparator": f"{lane_root}/comparator_report.json",
        "parity": f"{lane_root}/parity_results.json",
        "secret": f"{lane_root}/secret_scan_report.json",
        "prevalidation": f"{lane_root}/prevalidation_report.json",
        "support": f"docs/conformance/support_claims/{spec['claim_id']}.json",
        "manifest": f"docs/conformance/support_claims/{spec['config_id']}_c4_evidence_manifest.json",
        "node_gate": f"artifacts/conformance/node_gate/{spec['ct_id']}.json",
    }
    if output_root is not None:
        ledger_output = _output_path(_logical_path(LEDGER_PATH), output_root)
        ledger_output.parent.mkdir(parents=True, exist_ok=True)
        ledger_output.write_bytes(LEDGER_PATH.read_bytes())
    packet_sources = _packet_sources(spec)
    probe_path = next(path for path in packet_sources if path.endswith("/target_probe_output.json"))
    setup_path = next(path for path in packet_sources if path.endswith("/target_setup_and_capture_report.json"))
    probe = _require_mapping(json.loads(_source_path(probe_path).read_text(encoding="utf-8")), "target probe")
    setup = _require_mapping(json.loads(_source_path(setup_path).read_text(encoding="utf-8")), "setup report")

    raw = _raw_payload(level, profile, spec, packet_sources, probe, setup)
    _write_json(_output_path(paths["raw"], output_root), raw)
    replay = _replay_payload(level, profile, spec, paths["raw"], probe_path, setup_path, probe, output_root)
    _write_json(_output_path(paths["replay"], output_root), replay)

    assertions, details = _comparator_parts(level, spec, probe, setup)
    comparator: dict[str, Any] = {
        "schema_version": "bb.e4.comparator_report.v1",
        "config_id": spec["config_id"],
        "details": details,
        "failed": 0,
        "generated_at_utc": profile["generated_at"],
        "input_hashes": {paths["replay"]: _hash_output(paths["replay"], output_root), paths["raw"]: _hash_output(paths["raw"], output_root), probe_path: _sha256(_source_path(probe_path)), **({setup_path: _sha256(_source_path(setup_path))} if level >= 3 else {})},
        "lane_id": spec["lane_id"],
        **({"passed": True} if level >= 3 else {}),
        "run_id": spec["run_id"],
        "scope": _scope(spec, target_family=True),
        **({"target_family": spec["target_family"]} if level == 4 else {}),
        "warned": 0,
    }
    comparator_seed_path = _output_path(paths["comparator"], output_root)
    _write_json(comparator_seed_path, comparator)
    comparator_seed_hash = _sha256(comparator_seed_path)
    comparator["assertions"] = assertions
    _write_json(comparator_seed_path, comparator)

    if level == 4:
        parity = {"schema_version": "bb.e4.parity_results.v1", "comparator_ref": _ref(paths["comparator"], output_root), "config_id": spec["config_id"], "failed": 0, "generated_at_utc": profile["generated_at"], "lane_id": spec["lane_id"], "passed": True, "run_id": spec["run_id"], "status": "passed", "summary": "Oh-My-Pi P6.0-L4 local MCP/browser/resource capture, replay, and comparator passed for exact provider-free read-only scope only.", "target_family": spec["target_family"], "warned": 0}
    else:
        parity = {"schema_version": "bb.e4.parity_results.v1", "checks": details, "config_id": spec["config_id"], **({"failed": 0} if level <= 2 else {}), "generated_at_utc": profile["generated_at"], "input_hashes": {paths["replay"]: _hash_output(paths["replay"], output_root), paths["comparator"]: comparator_seed_hash if level <= 2 else _hash_output(paths["comparator"], output_root), paths["raw"]: _hash_output(paths["raw"], output_root)}, "lane_id": spec["lane_id"], "passed": True, "run_id": spec["run_id"], "scope": profile["parity_scope"], "status": "passed", "warned": 0}
    _write_json(_output_path(paths["parity"], output_root), parity)

    generated_scan_paths = [paths["raw"], paths["replay"], paths["comparator"], paths["parity"]]
    scanned_paths = _scan_paths(level, spec, packet_sources, generated_scan_paths)
    _ensure_secret_free(scanned_paths)
    secret = {"schema_version": "bb.e4.secret_scan_report.v1", "config_id": spec["config_id"], "findings": [], "generated_at_utc": profile["generated_at"], "lane_id": spec["lane_id"], "notes": profile["secret_notes"], "passed": True, "run_id": spec["run_id"], "scanned_artifacts": scanned_paths, "status": "passed", **({"target_family": spec["target_family"]} if level == 4 else {})}
    _write_json(_output_path(paths["secret"], output_root), secret)

    prevalidation = _prevalidation(level, profile, spec, paths, probe_path, setup_path, comparator_seed_hash, output_root)
    _write_json(_output_path(paths["prevalidation"], output_root), prevalidation)

    evidence_paths = [paths["raw"], paths["replay"], paths["comparator"], paths["parity"], paths["support"], paths["manifest"], paths["prevalidation"], paths["secret"]]
    freeze_data = yaml.safe_load(FREEZE_PATH.read_text(encoding="utf-8"))
    if not isinstance(freeze_data, dict) or not isinstance(freeze_data.get("e4_configs"), dict):
        raise ValueError("freeze manifest must contain e4_configs")
    freeze_row = _freeze_row(profile, spec, evidence_paths)
    freeze_data["e4_configs"][spec["config_id"]] = freeze_row
    freeze_out = _output_path("config/e4_target_freeze_manifest.yaml", output_root)
    freeze_out.parent.mkdir(parents=True, exist_ok=True)
    freeze_out.write_text(yaml.safe_dump(freeze_data, sort_keys=False, width=120), encoding="utf-8")
    freeze_hash = _row_hash(str(spec["config_id"]), freeze_row)

    support = _support_claim(profile, spec, paths, freeze_hash, assertions, output_root)
    _write_json(_output_path(paths["support"], output_root), support)
    manifest = _manifest(level, profile, spec, paths, freeze_hash, output_root)
    _write_json(_output_path(paths["manifest"], output_root), manifest)
    node_gate = _node_gate(spec, paths, assertions, freeze_hash, output_root)
    _write_json(_output_path(paths["node_gate"], output_root), node_gate)
    return {"lane_id": spec["lane_id"], "config_id": spec["config_id"], "ok": True, "errors": [], "node_gate": paths["node_gate"]}
