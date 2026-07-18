from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any
import yaml

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils

try:
    from build_source_index import DEFAULT_OUT as DEFAULT_INDEX_OUT
    from build_source_index import PLAN_ROOT, REPO_ROOT, _display_path, write_source_index
except ImportError:  # pragma: no cover - package-style import fallback
    from .build_source_index import DEFAULT_OUT as DEFAULT_INDEX_OUT
    from .build_source_index import PLAN_ROOT, REPO_ROOT, _display_path, write_source_index

try:
    from scripts.e4_parity.lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from lane_definitions import DEFAULT_LANE_DEF_DIR, load_lane_defs



SCHEMA_VERSION = "bb.atomic_feature_ledger.v1"
LEDGER_FILENAME = "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
DEFAULT_OUT = PLAN_ROOT / LEDGER_FILENAME
DEFAULT_LANE_EXTENSION_PATH = (
    REPO_ROOT
    / "config"
    / "e4_lanes"
    / "evidence_inputs"
    / "e4_lane_ledger_seed_extensions.v1.json"
)
PLAN_REF = "docs_tmp/phase_15/BB_E4_PRIMITIVE_PARITY_MASTER_PLAN.md:317-323"


TARGET_OWNER = {
    "pi": "target.pi",
    "omp": "target.omp",
    "claude": "target.claude",
    "codex": "target.codex",
    "opencode": "target.opencode",
    "oh_my_opencode": "target.oh_my_opencode",
    "breadboard": "target.breadboard",
}

C4_FIXTURE_REFS: dict[str, list[str]] = {
    "claude_code_haiku45_north_star_capture_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#claude_code_haiku45_north_star_capture_v1",
        "capture:docs/conformance/e4_target_support/claude_code_north_star_capture_v1/raw_capture_manifest.json",
        "replay:docs/conformance/e4_target_support/claude_code_north_star_capture_v1/bb_replay_result.json",
        "comparator:docs/conformance/e4_target_support/claude_code_north_star_capture_v1/comparator_report.json",
        "support_claim:docs/conformance/support_claims/claude_code_haiku45_north_star_capture_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/claude_code_haiku45_north_star_capture_v1_c4_evidence_manifest.json",
    ],
    "opencode_gpt51mini_north_star_capture_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#opencode_gpt51mini_north_star_capture_v1",
        "capture:docs/conformance/e4_target_support/opencode_north_star_capture_v1/raw_capture_manifest.json",
        "replay:docs/conformance/e4_target_support/opencode_north_star_capture_v1/bb_replay_result.json",
        "comparator:docs/conformance/e4_target_support/opencode_north_star_capture_v1/comparator_report.json",
        "support_claim:docs/conformance/support_claims/opencode_gpt51mini_north_star_capture_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/opencode_gpt51mini_north_star_capture_v1_c4_evidence_manifest.json",
    ],
    "breadboard_self_runtime_records_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#breadboard_self_runtime_records_v1",
        "capture:docs/conformance/e4_target_support/breadboard_self_runtime_records_v1/raw_capture_manifest.json",
        "replay:docs/conformance/e4_target_support/breadboard_self_runtime_records_v1/bb_replay_result.json",
        "comparator:docs/conformance/e4_target_support/breadboard_self_runtime_records_v1/comparator_report.json",
        "support_claim:docs/conformance/support_claims/breadboard_self_runtime_records_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/breadboard_self_runtime_records_v1_c4_evidence_manifest.json",
    ],
    "codex_cli_gpt55_e4_capture_probe_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#codex_cli_gpt55_e4_capture_probe_v1#sha256:ae98e65717d03a2d451e65db2b7c7693f77d9786686f8370b888ac52e41c6085",
        "capture:docs/conformance/e4_target_support/codex_cli_e4_capture_probe_v1/raw_capture_manifest.json#sha256:c22cddc606d25a4e4720d812bf8b162e802240b1859c9ab735fde872926b90eb",
        "replay:docs/conformance/e4_target_support/codex_cli_e4_capture_probe_v1/bb_replay_result.json#sha256:457d9c20d86c4e071e537fb9eb2e492f92e5aeb4f0701971e1ffcc54e936658f",
        "comparator:docs/conformance/e4_target_support/codex_cli_e4_capture_probe_v1/comparator_report.json#sha256:c408077792bcdc8cdd7277dc603e4bc800cd17239f7ddf8d6261157a8b941d29",
        "support_claim:docs/conformance/support_claims/codex_cli_gpt55_e4_capture_probe_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/codex_cli_gpt55_e4_capture_probe_v1_c4_evidence_manifest.json",
        "parity_results:docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/parity_results.jsonl#sha256:a87a2ed2f94233db521b2b198cfd7fef6056161622d19750e643bf17ede8a6d1",
        "secret_scan_report:docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/secret_scan_report.json#sha256:02ce7bff97694bd2571c2b3161ae049742e9be8290463f26891c3685a4c61ebf",
        "validator_output:docs/conformance/e4_target_support/codex_cli_e4_capture_probe_v1/c4_validation_report.json#sha256:8e794527c8d964c138ea80e93bdf5d230f7bc66863f0a39a37e38dfec014398c",
    ],
    "oh_my_pi_p6_0_l1_config_context_tool_surface_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#oh_my_pi_p6_0_l1_config_context_tool_surface_v1#sha256:c6d96da67797f42b274f51fd6eb0cb7ad09ef06873083316667dee09a860c50d",
        "capture:docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/raw_capture_manifest.json#sha256:a5819fd983019702e0c5808e018a716fe98f51766e2b95cd3a986df5e5906b98",
        "replay:docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/bb_replay_result.json#sha256:3f3291decc890f0e96f1515f9a43faf14eb9d94cd03b5ddffa405a7456289ef2",
        "comparator:docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/comparator_report.json#sha256:fc4e84e687838b9b6eb15d9feb2ba3dce8bb25285a9e14ce584df0f9c7e8fefd",
        "support_claim:docs/conformance/support_claims/oh_my_pi_p6_0_l1_config_context_tool_surface_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/oh_my_pi_p6_0_l1_config_context_tool_surface_v1_c4_evidence_manifest.json",
        "parity_results:docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/parity_results.json#sha256:0c6dc5fcb57543c9cc2b17eae5ef9f5f32773a97ea23c63314691f63f84fe6a4",
        "secret_scan_report:docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/secret_scan_report.json#sha256:d1e73455492a1c6d2eab8557116c84e325022b56b2f56d996202d665e96d00b6",
        "validator_output:docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/prevalidation_report.json#sha256:dbb62cbb5af25c575ae189ed7239751497831aa28af75d5917a381e8fb2d5a2f",
    ],
    "oh_my_pi_p6_0_l2_tool_execution_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#oh_my_pi_p6_0_l2_tool_execution_v1#sha256:ab8d9820d4d68a9b0567568a10a1e3d0f88844f194c4c947a5173ca0cf61901d",
        "capture:docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/raw_capture_manifest.json#sha256:541c8fa785cf5d8e48b9619755fd201e95df956c14e91e43479b358309bcbf67",
        "replay:docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/bb_replay_result.json#sha256:3461b6a831c014511370cd321a9cfcfdb4d4fde72495b2d37463a7fdbe5be88d",
        "comparator:docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/comparator_report.json#sha256:75cef22187aec0a95aa98a383c5c1754af99fd5b3a9d44ef4a9f35fc93e0208c",
        "support_claim:docs/conformance/support_claims/oh_my_pi_p6_0_l2_tool_execution_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/oh_my_pi_p6_0_l2_tool_execution_v1_c4_evidence_manifest.json",
        "parity_results:docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/parity_results.json#sha256:f88837fa163d4d82ba1fa66e047012aee8090ddefbf48e4f32c3ab2f13905289",
        "secret_scan_report:docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/secret_scan_report.json#sha256:9b7acf436cff49b94783bb241aa0cdd8a871a779968029b943dcf0c55792d6c6",
        "validator_output:docs/conformance/e4_target_support/oh_my_pi_p6_0_l2_tool_execution/prevalidation_report.json#sha256:451572bedadc9e37bc6b59de72908d02cf1df2bdc743cb2b02a6a7bb81dacc7a",
    ],
    "oh_my_pi_p6_0_l3_command_network_hook_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#oh_my_pi_p6_0_l3_command_network_hook_v1#sha256:174e576085fc185b2c8e78b3f934a9aa6469bd96c011e892cf654cfb559853e7",
        "capture:docs/conformance/e4_target_support/oh_my_pi_p6_0_l3_command_network_hook/raw_capture_manifest.json#sha256:bddbcf8b317dd599b55c3d84f9c6fea16c1443c366fa74e3ce26e150122f22a5",
        "replay:docs/conformance/e4_target_support/oh_my_pi_p6_0_l3_command_network_hook/bb_replay_result.json#sha256:edb29da6dfc858a72b8acd3faf86ae20366d2c1385db885e3e81899fe5789800",
        "comparator:docs/conformance/e4_target_support/oh_my_pi_p6_0_l3_command_network_hook/comparator_report.json#sha256:954f4ef6112d537917384c257a6cb146266409f1c285c2606c5ef1698528ca54",
        "support_claim:docs/conformance/support_claims/oh_my_pi_p6_0_l3_command_network_hook_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/oh_my_pi_p6_0_l3_command_network_hook_v1_c4_evidence_manifest.json",
        "parity_results:docs/conformance/e4_target_support/oh_my_pi_p6_0_l3_command_network_hook/parity_results.json#sha256:9cbc75f9be478221d3c9ca8db9d67ab60c95eb47335dd0dee2e038ceb2a63b67",
        "secret_scan_report:docs/conformance/e4_target_support/oh_my_pi_p6_0_l3_command_network_hook/secret_scan_report.json#sha256:77db9c0c1d61b7837f9b188b25114fd2cb17ffae317aadd57097d7de9a2f1e15",
        "validator_output:docs/conformance/e4_target_support/oh_my_pi_p6_0_l3_command_network_hook/prevalidation_report.json#sha256:936143afb98bb1b5a21481d3fa46f4c3393a5b3dfe8106aadf668f4043991559",
    ],
    "oh_my_pi_p6_0_l4_mcp_browser_resource_v1": [
        "freeze:config/e4_target_freeze_manifest.yaml#oh_my_pi_p6_0_l4_mcp_browser_resource_v1#sha256:56a0a8d01d96df777c70e8938066a54ea5aab26c7bba132665c72c1eebad8d10",
        "capture:docs/conformance/e4_target_support/oh_my_pi_p6_0_l4_mcp_browser_resource/raw_capture_manifest.json#sha256:6a1b4bd8293aeaa910dc010dec6d9f5e91cf55456ca2f17b2f05714f878eca87",
        "replay:docs/conformance/e4_target_support/oh_my_pi_p6_0_l4_mcp_browser_resource/bb_replay_result.json#sha256:7821101f52dece47e28cae604cc9a3c20c824bf18e8c415e9ff0f23cc5ada8ec",
        "comparator:docs/conformance/e4_target_support/oh_my_pi_p6_0_l4_mcp_browser_resource/comparator_report.json#sha256:8a3020309dbe445f96a5409ea10c97395f7bf8948180d21be92699429acbb491",
        "support_claim:docs/conformance/support_claims/oh_my_pi_p6_0_l4_mcp_browser_resource_v1_c4_support_claim.json",
        "evidence_manifest:docs/conformance/support_claims/oh_my_pi_p6_0_l4_mcp_browser_resource_v1_c4_evidence_manifest.json",
        "parity_results:docs/conformance/e4_target_support/oh_my_pi_p6_0_l4_mcp_browser_resource/parity_results.json#sha256:4a6fce33c13e929c4dfe314a0d808382d688562e884fa9e87e6e7a91e007ed3e",
        "secret_scan_report:docs/conformance/e4_target_support/oh_my_pi_p6_0_l4_mcp_browser_resource/secret_scan_report.json#sha256:816a7fd42eb826ce0f15224e8ec3334b38a44b272a9f67d141aa11c10f5913e5",
        "validator_output:docs/conformance/e4_target_support/oh_my_pi_p6_0_l4_mcp_browser_resource/prevalidation_report.json#sha256:a48462e6b6e97379e95fa53fd37c9afda7c3b31aa2bcdda302480c4cf6a8511d",
    ],
}



C4_SOURCE_REFS: dict[str, list[str]] = {
    "claude_code_haiku45_north_star_capture_v1": [
        "source:docs/conformance/support_claims/claude_code_haiku45_north_star_capture_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/claude_code_haiku45_north_star_capture_v1_c4_evidence_manifest.json",
    ],
    "opencode_gpt51mini_north_star_capture_v1": [
        "source:docs/conformance/support_claims/opencode_gpt51mini_north_star_capture_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/opencode_gpt51mini_north_star_capture_v1_c4_evidence_manifest.json",
    ],
    "breadboard_self_runtime_records_v1": [
        "source:docs/conformance/support_claims/breadboard_self_runtime_records_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/breadboard_self_runtime_records_v1_c4_evidence_manifest.json",
    ],
    "codex_cli_gpt55_e4_capture_probe_v1": [
        "source:docs/conformance/support_claims/codex_cli_gpt55_e4_capture_probe_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/codex_cli_gpt55_e4_capture_probe_v1_c4_evidence_manifest.json",
    ],
    "oh_my_pi_p6_0_l1_config_context_tool_surface_v1": [
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l1_config_context_tool_surface_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l1_config_context_tool_surface_v1_c4_evidence_manifest.json",
    ],
    "oh_my_pi_p6_0_l2_tool_execution_v1": [
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l2_tool_execution_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l2_tool_execution_v1_c4_evidence_manifest.json",
    ],
    "oh_my_pi_p6_0_l3_command_network_hook_v1": [
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l3_command_network_hook_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l3_command_network_hook_v1_c4_evidence_manifest.json",
    ],
    "oh_my_pi_p6_0_l4_mcp_browser_resource_v1": [
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l4_mcp_browser_resource_v1_c4_support_claim.json",
        "source:docs/conformance/support_claims/oh_my_pi_p6_0_l4_mcp_browser_resource_v1_c4_evidence_manifest.json",
    ],
}

C4_LANE_BEHAVIOR_SPECS: dict[str, list[dict[str, Any]]] = {
    "claude_code_haiku45_north_star_capture_v1": [
        {"family": "session", "semantic_key": "north_star_claude_code_package_capture", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.replay_session.v1"},
    ],
    "opencode_gpt51mini_north_star_capture_v1": [
        {"family": "session", "semantic_key": "north_star_opencode_package_capture", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.replay_session.v1"},
    ],
    "breadboard_self_runtime_records_v1": [
        {"family": "session", "semantic_key": "north_star_breadboard_runtime_records_self_capture", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.kernel_event.v2"},
    ],
    "codex_cli_gpt55_e4_capture_probe_v1": [
        {"family": "session", "semantic_key": "codex_cli_gpt55_capture_probe_replay", "claim_type": "replay", "model_visible": True, "stateful": True, "primitive": "bb.replay_session.v1"},
    ],
    "oh_my_pi_p6_0_l1_config_context_tool_surface_v1": [
        {"family": "config", "semantic_key": "oh_my_pi_p6_0_l1_read_only_config_graph", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1"},
        {"family": "context", "semantic_key": "oh_my_pi_p6_0_l1_context_pack_system_prompt", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.context_resource_pack.v1"},
        {"family": "tool", "semantic_key": "oh_my_pi_p6_0_l1_builtin_tool_surface", "claim_type": "capture", "model_visible": True, "stateful": False, "primitive": "bb.effective_tool_surface.v1"},
    ],
    "oh_my_pi_p6_0_l2_tool_execution_v1": [
        {"family": "tool", "semantic_key": "oh_my_pi_p6_0_l2_local_tool_execution", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.tool_execution_outcome.v1"},
        {"family": "extension", "semantic_key": "oh_my_pi_p6_0_l2_custom_command_tool_hook_execution", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.extension_hook_execution.v1"},
    ],
    "oh_my_pi_p6_0_l3_command_network_hook_v1": [
        {"family": "extension", "semantic_key": "oh_my_pi_p6_0_l3_hook_command_execution", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.extension_hook_execution.v1"},
        {"family": "policy", "semantic_key": "oh_my_pi_p6_0_l3_hook_gated_network_shaped_bash_block", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.effective_operation_policy.v1"},
    ],
    "oh_my_pi_p6_0_l4_mcp_browser_resource_v1": [
        {"family": "session", "semantic_key": "oh_my_pi_p6_0_l4_mcp_browser_resource_session", "claim_type": "capture", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1"},
    ],
}

def _row_hash(row_id: str, row: dict[str, Any]) -> str:
    encoded = json.dumps({"row_id": row_id, "row": row}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + _hash_utils.sha256_hex(encoded)


def _refresh_c4_fixture_refs(config_id: str, fixture_refs: list[str]) -> list[str]:
    refs: list[str] = []
    freeze_manifest: dict[str, Any] | None = None
    physical_roles = {
        "capture",
        "replay",
        "comparator",
        "parity_results",
        "secret_scan_report",
    }
    for ref in fixture_refs:
        role, separator, value = ref.partition(":")
        if not separator or not value:
            refs.append(ref)
            continue
        value_without_hash = value.rsplit("#sha256:", 1)[0]
        unpinned_ref = f"{role}:{value_without_hash}"
        if role == "validator_output":
            refs.append(unpinned_ref)
            continue
        if role == "freeze":
            if freeze_manifest is None:
                loaded = yaml.safe_load((REPO_ROOT / "config/e4_target_freeze_manifest.yaml").read_text(encoding="utf-8"))
                freeze_manifest = loaded if isinstance(loaded, dict) else {}
            row = (freeze_manifest.get("e4_configs") or {}).get(config_id) if isinstance(freeze_manifest.get("e4_configs"), dict) else None
            if isinstance(row, dict):
                refs.append(f"{unpinned_ref}#{_row_hash(config_id, row)}")
                continue
        elif role in physical_roles:
            path = (REPO_ROOT / value_without_hash.split("#", 1)[0]).resolve()
            if path.is_file():
                refs.append(f"{unpinned_ref}#{_hash_utils.sha256_path(path)}")
                continue
        refs.append(ref)
    return refs


def _current_c4_fixture_refs(config_id: str) -> list[str]:
    return _refresh_c4_fixture_refs(config_id, C4_FIXTURE_REFS[config_id])



def _lane_def_c4_seed_specs() -> list[dict[str, Any]]:
    lane_defs = load_lane_defs(DEFAULT_LANE_DEF_DIR)
    specs: list[dict[str, Any]] = []
    for lane_def in lane_defs.values():
        config_id = str(lane_def.get("config_id", ""))
        behavior_specs = C4_LANE_BEHAVIOR_SPECS.get(config_id)
        if behavior_specs is None:
            continue
        target_family = str(lane_def.get("target_family"))
        target = {"oh_my_pi": "omp", "claude_code": "claude"}.get(target_family, target_family)
        if target is None:
            raise ValueError(f"lane_def {lane_def.get('lane_id')!r} target_family has no seed target")
        for behavior_spec in behavior_specs:
            specs.append(
                {
                    **behavior_spec,
                    "target": target,
                    "support": "supported",
                    "truth_scope": "kernel_truth",
                    "evidence_tier": "C4",
                    "gap_kind": "none",
                    "promotion_state": "ready",
                    "e4_row_ref": config_id,
                    "source_refs": C4_SOURCE_REFS[config_id],
                    "fixture_refs": _current_c4_fixture_refs(config_id),
                }
            )
    return specs



SEED_SPECS: list[dict[str, Any]] = [
    {"target": "pi", "family": "config", "semantic_key": "settings_global_project_merge", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["pi_mono", "settings", "config"]},
    {"target": "pi", "family": "context", "semantic_key": "agents_rules_context_pack", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.context_resource_pack.v1", "support": "partial", "keywords": ["pi_mono", "AGENTS", "context"]},
    {"target": "pi", "family": "tool", "semantic_key": "skills_and_commands_tool_surface", "claim_type": "docs", "model_visible": True, "stateful": False, "primitive": "bb.capability_registry.v1", "support": "partial", "keywords": ["pi_mono", "tool", "command"]},
    {"target": "pi", "family": "product", "semantic_key": "extension_hook_tool_call_block", "claim_type": "manifest", "model_visible": True, "stateful": True, "primitive": "bb.extension_hook_execution.v1", "support": "missing", "keywords": ["pi_mono", "extension", "hook"]},
    {"target": "pi", "family": "provider", "semantic_key": "provider_pre_request_transform", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.provider_route.v1", "support": "partial", "keywords": ["pi_mono", "provider", "model"]},
    {"target": "pi", "family": "resource", "semantic_key": "resource_loader_internal_urls", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.resource_access.v1", "support": "partial", "keywords": ["pi_mono", "resource-loader", "resource"]},
    {"target": "pi", "family": "memory", "semantic_key": "compaction_override_cancel", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.memory_compaction_plan.v1", "support": "missing", "keywords": ["pi_mono", "compaction", "memory"]},
    {"target": "pi", "family": "task", "semantic_key": "subagent_job_lifecycle", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.work_item.v1", "support": "partial", "keywords": ["pi_mono", "subagent", "job"]},
    {"target": "pi", "family": "policy", "semantic_key": "vouch_permissions_policy", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.effective_operation_policy.v1", "support": "missing", "keywords": ["pi_mono", "vouch", "permission"]},
    {"target": "pi", "family": "projection", "semantic_key": "chat_projection_frames", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.projection_event.v1", "support": "partial", "keywords": ["pi_mono", "projection", "ui"]},
    {"target": "pi", "family": "product", "semantic_key": "github_side_effect_vouch", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.side_effect_broker.v1", "support": "missing", "keywords": ["pi_mono", "github", "side_effect"]},
    {"target": "pi", "family": "config", "semantic_key": "settings_scope_precedence_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["pi_mono", "settings-manager", "precedence"]},
    {"target": "pi", "family": "config", "semantic_key": "settings_migration_writeback_quirk", "claim_type": "runtime_behavior", "model_visible": False, "stateful": True, "primitive": "bb.config_mutation_record.v1", "support": "missing", "keywords": ["pi_mono", "settings-manager", "migration"]},
    {"target": "pi", "family": "context", "semantic_key": "nearest_agents_rules_resolution_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.context_resource_pack.v1", "support": "partial", "keywords": ["pi_mono", "AGENTS", "rules"]},
    {"target": "pi", "family": "capability", "semantic_key": "tool_registry_filtering_quirk", "claim_type": "registry_discovery", "model_visible": False, "stateful": True, "primitive": "bb.capability_registry.v1", "support": "partial", "keywords": ["pi_mono", "tools", "registry"]},
    {"target": "pi", "family": "extension", "semantic_key": "extension_hook_phase_order_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.extension_hook_execution.v1", "support": "missing", "keywords": ["pi_mono", "extension", "hook"]},
    {"target": "pi", "family": "provider", "semantic_key": "provider_route_transform_quirk", "claim_type": "source", "model_visible": False, "stateful": True, "primitive": "bb.provider_route.v1", "support": "partial", "keywords": ["pi_mono", "model-registry", "provider"]},
    {"target": "pi", "family": "session", "semantic_key": "session_tree_lineage_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.session_transcript.v1", "support": "partial", "keywords": ["pi_mono", "session-manager", "lineage"]},
    {"target": "pi", "family": "tool", "semantic_key": "tool_execution_outcome_quirk", "claim_type": "runtime_behavior", "model_visible": True, "stateful": True, "primitive": "bb.tool_execution_outcome.v1", "support": "partial", "keywords": ["pi_mono", "tools", "execution"]},

    {"target": "omp", "family": "config", "semantic_key": "profile_and_target_config", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["oh_my_pi", "config"]},
    {"target": "omp", "family": "tool", "semantic_key": "skills_commands_builtin_discovery", "claim_type": "source", "model_visible": True, "stateful": False, "primitive": "bb.capability_registry.v1", "support": "partial", "keywords": ["oh_my_pi", "discovery", "builtin", "skills"]},
    {"target": "omp", "family": "product", "semantic_key": "custom_tools_hooks", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.extension_manifest.v1", "support": "missing", "keywords": ["oh_my_pi", "custom-tools", "hooks"]},
    {"target": "omp", "family": "resource", "semantic_key": "internal_url_artifact_read", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.resource_ref.v1", "support": "partial", "keywords": ["oh_my_pi", "internal-urls", "artifact"]},
    {"target": "omp", "family": "session", "semantic_key": "mcp_server_discovery", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1", "support": "partial", "keywords": ["oh_my_pi", "mcp", "protocol"]},
    {"target": "omp", "family": "task", "semantic_key": "tasks_jobs_background", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.work_item.v1", "support": "partial", "keywords": ["oh_my_pi", "task", "job"]},
    {"target": "omp", "family": "product", "semantic_key": "browser_github_tts_image_side_effects", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.side_effect_broker.v1", "support": "missing", "keywords": ["oh_my_pi", "browser", "github", "tts", "image"]},
    {"target": "omp", "family": "config", "semantic_key": "native_config_yml_settings_json_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/discovery/builtin.ts:817-866", "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/config/settings-schema.ts:3765-3838"]},
    {"target": "omp", "family": "config", "semantic_key": "tool_discovery_mcp_settings_quirk", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/config/settings-schema.ts:3765-3838"]},
    {"target": "omp", "family": "context", "semantic_key": "system_md_rules_skills_commands_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.context_resource_pack.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/discovery/builtin.ts:231-447"]},
    {"target": "omp", "family": "context", "semantic_key": "agents_md_nearest_project_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.context_resource_pack.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/discovery/builtin.ts:867-934"]},
    {"target": "omp", "family": "capability", "semantic_key": "provider_priority_dedupe_shadowing_quirk", "claim_type": "registry_discovery", "model_visible": False, "stateful": True, "primitive": "bb.capability_registry.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/capability/index.ts:149-190"]},
    {"target": "omp", "family": "capability", "semantic_key": "disabled_extension_id_filter_quirk", "claim_type": "registry_discovery", "model_visible": False, "stateful": True, "primitive": "bb.capability_registry.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/capability/index.ts:222-253"]},
    {"target": "omp", "family": "extension", "semantic_key": "hook_key_type_tool_name_grammar_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.extension_hook_execution.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/capability/hook.ts:1-40"]},
    {"target": "omp", "family": "extension", "semantic_key": "extension_manifest_module_settings_quirk", "claim_type": "manifest", "model_visible": True, "stateful": True, "primitive": "bb.extension_manifest.v1", "support": "missing", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/discovery/builtin.ts:449-604"]},
    {"target": "omp", "family": "extension", "semantic_key": "custom_tool_loader_wrapper_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.extension_manifest.v1", "support": "missing", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/discovery/builtin.ts:725-817"]},
    {"target": "omp", "family": "resource", "semantic_key": "internal_url_scheme_registry_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.resource_ref.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/internal-urls/router.ts:18-101"]},
    {"target": "omp", "family": "resource", "semantic_key": "artifact_protocol_session_resolution_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.resource_access.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/internal-urls/artifact-protocol.ts:17-104"]},
    {"target": "omp", "family": "resource", "semantic_key": "artifact_dirs_subagent_dedupe_quirk", "claim_type": "source", "model_visible": False, "stateful": True, "primitive": "bb.resource_access.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/internal-urls/registry-helpers.ts:1-35"]},
    {"target": "omp", "family": "mcp", "semantic_key": "stdio_http_transport_pairing_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/capability/mcp.ts:1-74"]},
    {"target": "omp", "family": "mcp", "semantic_key": "project_user_mcp_discovery_mode_quirk", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/discovery/builtin.ts:100-229", "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/config/settings-schema.ts:3791-3838"]},
    {"target": "omp", "family": "task", "semantic_key": "owner_scoped_async_job_registry_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.work_item.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/async/job-manager.ts:9-123"]},
    {"target": "omp", "family": "task", "semantic_key": "job_tool_poll_cancel_list_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.work_item.v1", "support": "partial", "source_refs": ["source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/tools/job.ts:25-110"]},
    *_lane_def_c4_seed_specs(),

    {"target": "claude", "family": "config", "semantic_key": "settings_safe_mode_lockdown", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["claude", "settings", "safe"]},
    {"target": "claude", "family": "context", "semantic_key": "claude_md_context", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.context_resource_pack.v1", "support": "partial", "keywords": ["claude", "CLAUDE", "context"]},
    {"target": "claude", "family": "session", "semantic_key": "mcp_server_config", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1", "support": "partial", "keywords": ["claude", "mcp"]},
    {"target": "claude", "family": "provider", "semantic_key": "sdk_provider_route", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.provider_route.v1", "support": "partial", "keywords": ["claude", "sdk", "provider"]},

    {"target": "codex", "family": "config", "semantic_key": "config_toml_and_agents", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["codex", "config", "AGENTS"]},
    {"target": "codex", "family": "task", "semantic_key": "goal_mode_longrun_objective", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.work_item.v1", "support": "partial", "keywords": ["codex", "goal", "subagent"]},
    {"target": "codex", "family": "policy", "semantic_key": "sandbox_approval_policy", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.effective_operation_policy.v1", "support": "partial", "keywords": ["codex", "sandbox", "approval"]},
    {"target": "codex", "family": "projection", "semantic_key": "slash_app_tui_projection", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.projection_event.v1", "support": "missing", "keywords": ["codex", "slash", "app"]},
    {"target": "codex", "family": "session", "semantic_key": "mcp_plugin_config", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1", "support": "partial", "keywords": ["codex", "mcp"]},
    {"target": "codex", "family": "config", "semantic_key": "config_toml_agents_overlay_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["codex", "config", "toml"]},
    {"target": "codex", "family": "policy", "semantic_key": "sandbox_approval_matrix_quirk", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.effective_operation_policy.v1", "support": "partial", "keywords": ["codex", "sandbox", "approval"]},
    {"target": "codex", "family": "projection", "semantic_key": "tui_slash_command_projection_quirk", "claim_type": "host_projection", "model_visible": False, "stateful": True, "primitive": "bb.projection_event.v1", "support": "missing", "truth_scope": "target_projection", "keywords": ["codex", "slash", "tui"]},

    {"target": "opencode", "family": "config", "semantic_key": "opencode_config", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["opencode", "config"]},
    {"target": "opencode", "family": "tool", "semantic_key": "agents_commands_skills", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.capability_registry.v1", "support": "partial", "keywords": ["opencode", "agents", "commands", "skills"]},
    {"target": "opencode", "family": "policy", "semantic_key": "permissions_policy", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.effective_operation_policy.v1", "support": "partial", "keywords": ["opencode", "permissions"]},
    {"target": "opencode", "family": "session", "semantic_key": "mcp_server_config", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1", "support": "partial", "keywords": ["opencode", "mcp", "server"]},
    {"target": "opencode", "family": "provider", "semantic_key": "provider_config", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.provider_route.v1", "support": "partial", "keywords": ["opencode", "providers"]},

    {"target": "oh_my_opencode", "family": "config", "semantic_key": "harness_pack_config", "claim_type": "config", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["oh_my_opencode", "config"]},
    {"target": "oh_my_opencode", "family": "context", "semantic_key": "system_prompt_policy_context", "claim_type": "docs", "model_visible": True, "stateful": True, "primitive": "bb.context_resource_pack.v1", "support": "partial", "keywords": ["oh_my_opencode", "system", "prompt"]},
    {"target": "oh_my_opencode", "family": "product", "semantic_key": "opencode_automation_side_effects", "claim_type": "docs", "model_visible": False, "stateful": True, "primitive": "bb.side_effect_broker.v1", "support": "missing", "keywords": ["oh_my_opencode", "side", "hook"]},

    {"target": "breadboard", "family": "config", "semantic_key": "e4_target_freeze_manifest", "claim_type": "manifest", "model_visible": False, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "keywords": ["e4_target_freeze_manifest"]},
    {"target": "breadboard", "family": "resource", "semantic_key": "internal_artifact_resource_refs", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.resource_ref.v1", "support": "partial", "keywords": ["contracts/kernel", "resource"]},
    {"target": "breadboard", "family": "session", "semantic_key": "static_mcp_tooling_tapes", "claim_type": "source", "model_visible": True, "stateful": True, "primitive": "bb.external_protocol_session.v1", "support": "partial", "keywords": ["mcp", "tooling"]},
    {"target": "breadboard", "family": "memory", "semantic_key": "memory_compaction_contract", "claim_type": "schema", "model_visible": True, "stateful": True, "primitive": "bb.memory_compaction_plan.v1", "support": "missing", "keywords": ["memory", "compaction"]},
    {"target": "breadboard", "family": "task", "semantic_key": "subagent_background_work_item", "claim_type": "schema", "model_visible": True, "stateful": True, "primitive": "bb.work_item.v1", "support": "partial", "keywords": ["task", "subagent"]},
    {"target": "breadboard", "family": "projection", "semantic_key": "terminal_tui_projection_event", "claim_type": "schema", "model_visible": False, "stateful": True, "primitive": "bb.projection_event.v1", "support": "partial", "keywords": ["projection", "terminal"]},
    {"target": "breadboard", "family": "policy", "semantic_key": "effective_operation_policy_contract", "claim_type": "schema", "model_visible": True, "stateful": True, "primitive": "bb.effective_operation_policy.v1", "support": "missing", "keywords": ["operation", "policy"]},
    {"target": "breadboard", "family": "config", "semantic_key": "runtime_session_effective_config_graph_c3", "claim_type": "runtime_behavior", "model_visible": True, "stateful": True, "primitive": "bb.effective_config_graph.v1", "support": "partial", "truth_scope": "kernel_truth", "evidence_tier": "C3", "gap_kind": "evidence", "promotion_state": "candidate", "source_refs": ["source:agentic_coder_prototype/api/cli_bridge/runtime_emission.py"], "fixture_refs": ["runtime_fixture:conformance/engine_fixtures/runtime_emission/runtime_emission_c3_session/effective_config_graph.json"]},
    {"target": "breadboard", "family": "capability", "semantic_key": "runtime_session_capability_registry_c3", "claim_type": "runtime_behavior", "model_visible": True, "stateful": True, "primitive": "bb.capability_registry.v1", "support": "partial", "truth_scope": "kernel_truth", "evidence_tier": "C3", "gap_kind": "evidence", "promotion_state": "candidate", "source_refs": ["source:agentic_coder_prototype/api/cli_bridge/runtime_emission.py"], "fixture_refs": ["runtime_fixture:conformance/engine_fixtures/runtime_emission/runtime_emission_c3_session/capability_registry.json"]},
    {"target": "breadboard", "family": "tool", "semantic_key": "runtime_session_effective_tool_surface_c3", "claim_type": "runtime_behavior", "model_visible": True, "stateful": False, "primitive": "bb.effective_tool_surface.v1", "support": "partial", "truth_scope": "kernel_truth", "evidence_tier": "C3", "gap_kind": "evidence", "promotion_state": "candidate", "source_refs": ["source:agentic_coder_prototype/api/cli_bridge/runtime_emission.py"], "fixture_refs": ["runtime_fixture:conformance/engine_fixtures/runtime_emission/runtime_emission_c3_session/effective_tool_surface.json"]},
]


def _load_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        write_source_index(index_path)
    return json.loads(index_path.read_text(encoding="utf-8"))


def _source_refs_for(spec: dict[str, Any], entries: list[dict[str, Any]]) -> list[str]:
    explicit_refs = spec.get("source_refs")
    if explicit_refs is not None:
        return [str(source_ref) for source_ref in explicit_refs]

    keywords = [str(keyword).lower() for keyword in spec.get("keywords", [])]
    scored: list[tuple[int, str, str]] = []
    for entry in entries:
        source_ref = str(entry["source_ref"])
        haystack = source_ref.lower()
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score:
            scored.append((score, source_ref, str(entry["entry_id"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return [PLAN_REF]
    return [f"source_index:{entry_id}" for _score, _source_ref, entry_id in scored[:3]]


def _feature_id(dedupe_key: str) -> str:
    return f"feat_{_hash_utils.sha256_hex(dedupe_key.encode('utf-8'))[:16]}"


def _dedupe_key(spec: dict[str, Any]) -> str:
    surface = spec["claim_type"]
    model = "model_yes" if spec["model_visible"] else "model_no"
    state = "stateful_yes" if spec["stateful"] else "stateful_no"
    return f"{spec['target']}/{spec['family']}/{spec['semantic_key']}/{surface}/{model}/{state}"


def _collect_validation_errors(rows: list[dict[str, Any]]) -> list[str]:
    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        module = importlib.import_module("scripts.e4_parity.validate_atomic_feature_ledger")
    except ModuleNotFoundError:
        return []
    collector = getattr(module, "collect_atomic_feature_ledger_errors", None)
    if collector is None:
        return []
    errors: list[str] = []
    for row in rows:
        for error in collector(row):
            errors.append(f"{row.get('feature_id')}: {error}")
    return errors

def _load_lane_seed_extensions(
    path: Path = DEFAULT_LANE_EXTENSION_PATH,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read lane ledger seed extensions {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"lane ledger seed extensions must be an object: {path}")
    if payload.get("schema_version") != "bb.e4.lane_ledger_seed_extensions.v1":
        raise ValueError(f"unsupported lane ledger seed extension schema: {path}")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"lane ledger seed extensions rows must be a list: {path}")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"lane ledger seed extension row {index} must be an object")
        result.append(dict(row))
    return result




def build_ledger(
    index_path: Path = DEFAULT_INDEX_OUT,
    extension_path: Path = DEFAULT_LANE_EXTENSION_PATH,
) -> dict[str, Any]:
    index = _load_index(index_path)
    entries = list(index.get("entries") or [])
    rows = []
    for spec in sorted(SEED_SPECS, key=lambda item: (item["target"], item["family"], item["semantic_key"])):
        dedupe_key = _dedupe_key(spec)
        mapping: dict[str, Any] = {"primitive": spec["primitive"], "support": spec["support"]}
        if "truth_scope" in spec:
            mapping["truth_scope"] = spec["truth_scope"]
        if "negative_ledger_ref" in spec:
            mapping["negative_ledger_ref"] = spec["negative_ledger_ref"]

        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "feature_id": _feature_id(dedupe_key),
                "dedupe_key": dedupe_key,
                "target": spec["target"],
                "family": spec["family"],
                "claim_type": spec["claim_type"],
                "evidence_tier": spec.get("evidence_tier", "C1"),
                "source_refs": _source_refs_for(spec, entries),
                "model_visible": bool(spec["model_visible"]),
                "stateful": bool(spec["stateful"]),
                "breadboard_mapping": mapping,
                "gap_kind": spec.get("gap_kind", "evidence"),
                "fixture_refs": list(spec.get("fixture_refs", [])),
                "e4_row_ref": spec.get("e4_row_ref"),
                "promotion_state": spec.get("promotion_state", "draft"),
            }
        )
    seen_feature_ids = {str(row["feature_id"]) for row in rows}
    seen_dedupe_keys = {str(row["dedupe_key"]) for row in rows}
    for raw_extension_row in _load_lane_seed_extensions(extension_path):
        extension_row = dict(raw_extension_row)
        feature_id = extension_row.get("feature_id")
        dedupe_key = extension_row.get("dedupe_key")
        if not isinstance(feature_id, str) or not feature_id:
            raise ValueError("lane ledger seed extension row must have a feature_id")
        if not isinstance(dedupe_key, str) or not dedupe_key:
            raise ValueError(f"lane ledger seed extension {feature_id} must have a dedupe_key")
        if feature_id in seen_feature_ids:
            raise ValueError(f"duplicate lane ledger seed feature_id: {feature_id}")
        if dedupe_key in seen_dedupe_keys:
            raise ValueError(f"duplicate lane ledger seed dedupe_key: {dedupe_key}")
        if extension_row.get("evidence_tier") == "C4":
            config_id = extension_row.get("e4_row_ref")
            fixture_refs = extension_row.get("fixture_refs")
            if isinstance(config_id, str) and isinstance(fixture_refs, list):
                extension_row["fixture_refs"] = _refresh_c4_fixture_refs(
                    config_id,
                    [str(ref) for ref in fixture_refs],
                )
        seen_feature_ids.add(feature_id)
        seen_dedupe_keys.add(dedupe_key)
        rows.append(extension_row)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_index_ref": _display_path(index_path),
        "plan_ref": PLAN_REF,
        "row_count": len(rows),
        "rows": rows,
    }






def write_ledger(
    out_path: Path = DEFAULT_OUT,
    index_path: Path = DEFAULT_INDEX_OUT,
    extension_path: Path = DEFAULT_LANE_EXTENSION_PATH,
) -> dict[str, Any]:
    payload = build_ledger(index_path, extension_path)
    errors = _collect_validation_errors(list(payload["rows"]))
    if errors:
        raise ValueError("atomic feature ledger seed rows failed validation: " + "; ".join(errors))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": _display_path(out_path), "row_count": payload["row_count"]}


def _parse_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic P1 atomic feature ledger rows from source-index refs.")
    parser.add_argument("--source-index", default=str(DEFAULT_INDEX_OUT), help="Source index JSON path; generated if absent.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output ledger JSON path.")
    parser.add_argument(
        "--lane-extensions",
        default=str(DEFAULT_LANE_EXTENSION_PATH),
        help="Tracked promoted-lane seed extensions.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    args = parser.parse_args()

    summary = write_ledger(
        _parse_path(args.out),
        _parse_path(args.source_index),
        _parse_path(args.lane_extensions),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"atomic_feature_ledger={summary['out_path']} rows={summary['row_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
