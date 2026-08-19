from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from agentic_coder_prototype.conformance import c4_chain
from scripts.e4_parity import generate_support_claims, run_lane
from scripts.e4_parity.lane_definitions import load_lane_defs
from scripts.e4_parity.validators.registries import schema_generation_default


REPO_ROOT = Path(__file__).resolve().parents[2]
LANE_DEF_DIR = REPO_ROOT / "config" / "e4_lanes"
INVENTORY_PATH = REPO_ROOT / "docs" / "conformance" / "e4_lane_inventory.json"
REPORT_ROLES_PATH = REPO_ROOT / "docs" / "conformance" / "e4_report_roles.json"
COMPARATOR_REGISTRY_PATH = REPO_ROOT / "conformance" / "comparators" / "registry.json"
FREEZE_MANIFEST_PATH = REPO_ROOT / "config" / "e4_target_freeze_manifest.yaml"
RUN_LANE_SCRIPT = REPO_ROOT / "scripts" / "e4_parity" / "run_lane.py"
NORTH_STAR_WRAPPER_SCRIPT = REPO_ROOT / "scripts" / "e4_parity" / "build_north_star_proof_packets.py"
SUPPORT_CLAIM_SCHEMA_VERSION = schema_generation_default("support_claim")

EXPECTED_LANES: dict[str, dict[str, str]] = {
    "claude_code_north_star_capture_v1": {
        "config_id": "claude_code_haiku45_north_star_capture_v1",
        "target_family": "claude_code",
        "kind": "target_support",
    },
    "opencode_north_star_capture_v1": {
        "config_id": "opencode_gpt51mini_north_star_capture_v1",
        "target_family": "opencode",
        "kind": "target_support",
    },
    "breadboard_self_runtime_records_v1": {
        "config_id": "breadboard_self_runtime_records_v1",
        "target_family": "breadboard",
        "kind": "non_target_accounting",
    },
}
REQUIRED_ROLE_KEYS = {
    "capture",
    "comparator",
    "evidence_manifest",
    "freeze_manifest",
    "node_gate",
    "parity_results",
    "replay",
    "secret_scan_report",
    "support_claim",
    "validator_output",
}
FORBIDDEN_PATH_PARTS = {"scratch", "scratch_runs", "tmp"}
FORBIDDEN_PREFIXES = ("/shared_folders", "/shared/")




def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must contain a JSON object"
    return payload


def _inventory_by_lane() -> dict[str, dict[str, Any]]:
    payload = _load_json(INVENTORY_PATH)
    lanes = payload.get("lanes")
    assert isinstance(lanes, list), "lane inventory lanes must be a list"
    return {str(row["lane_id"]): row for row in lanes if isinstance(row, dict) and "lane_id" in row}


def _report_roles_by_lane() -> dict[str, dict[str, str]]:
    payload = _load_json(REPORT_ROLES_PATH)
    roles = payload.get("lane_artifact_roles")
    assert isinstance(roles, list), "report roles lane_artifact_roles must be a list"
    result: dict[str, dict[str, str]] = {}
    for role in roles:
        assert isinstance(role, dict), "lane artifact role rows must be objects"
        lane_id = str(role["lane_id"])
        result.setdefault(lane_id, {})[str(role["role_key"])] = str(role["role_id"])
    return result


def _comparator_lane_ids() -> dict[str, str]:
    payload = _load_json(COMPARATOR_REGISTRY_PATH)
    comparators = payload.get("comparators")
    assert isinstance(comparators, list), "comparator registry comparators must be a list"
    result: dict[str, str] = {}
    for comparator in comparators:
        assert isinstance(comparator, dict), "comparator registry rows must be objects"
        comparator_id = str(comparator["comparator_id"])
        lane_ids = comparator.get("lane_ids")
        assert isinstance(lane_ids, list), f"{comparator_id} lane_ids must be a list"
        for lane_id in lane_ids:
            assert str(lane_id) not in result, f"lane {lane_id} registered by multiple comparators"
            result[str(lane_id)] = comparator_id
    return result


def _path_is_canonical(path_text: str) -> bool:
    if path_text.startswith(FORBIDDEN_PREFIXES):
        return False
    if any(part in FORBIDDEN_PATH_PARTS for part in Path(path_text.split("#", 1)[0]).parts):
        return False
    return path_text.startswith(
        (
            "agent_configs/",
            "artifacts/conformance/",
            "config/",
            "docs/conformance/",
            "docs_tmp/phase_15/",
        )
    )


def _assert_command_targets_canonical_c4(config_id: str, argv: list[str]) -> None:
    assert argv[:4] == [".venv/bin/python", "scripts/validate_e4_c4_chain.py", "--config-id", config_id]
    assert "--support-claim" in argv
    assert "--evidence-manifest" in argv
    assert "--json-out" in argv
    assert argv[-1] == "--check-only"

    support_claim = argv[argv.index("--support-claim") + 1]
    evidence_manifest = argv[argv.index("--evidence-manifest") + 1]
    json_out = argv[argv.index("--json-out") + 1]
    assert support_claim == f"docs/conformance/support_claims/{config_id}_c4_support_claim.json"
    assert evidence_manifest == f"docs/conformance/support_claims/{config_id}_c4_evidence_manifest.json"
    assert json_out.startswith("artifacts/conformance/node_gate/ct_north_star_")
    assert _path_is_canonical(support_claim)
    assert _path_is_canonical(evidence_manifest)
    assert _path_is_canonical(json_out)


def _python_dependency_tokens(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tokens.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tokens.add(node.module)
                tokens.update(f"{node.module}.{alias.name}" for alias in node.names)
            else:
                tokens.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            tokens.add(ast.unparse(node.func))
        elif isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            tokens.add(node.value)
    return tokens


def _has_promoted_run_lane_call(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or ast.unparse(node.func) != "run_lane.run_lane":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "promote_accepted"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                return True
    return False


def test_north_star_writer_route_dependency_points_from_wrapper_to_run_lane() -> None:
    """The legacy packet wrapper may call run_lane; run_lane must not call the legacy builder."""
    run_lane_tokens = _python_dependency_tokens(RUN_LANE_SCRIPT)
    wrapper_tokens = _python_dependency_tokens(NORTH_STAR_WRAPPER_SCRIPT)

    forbidden_modules = ("build_north_star_proof_packets", "lane_promotion_artifacts")
    forbidden_run_lane_refs = sorted(
        token
        for token in run_lane_tokens
        if any(forbidden in token for forbidden in forbidden_modules)
    )
    assert forbidden_run_lane_refs == []
    assert "scripts.e4_parity.run_lane" in wrapper_tokens
    assert _has_promoted_run_lane_call(NORTH_STAR_WRAPPER_SCRIPT)


def test_checked_in_north_star_lane_defs_promote_expected_targets() -> None:
    """WS-J packet lanes must be accepted lane_def rows for Claude Code, OpenCode, and BreadBoard self-capture."""
    lane_defs = load_lane_defs(LANE_DEF_DIR)

    assert set(EXPECTED_LANES).issubset(lane_defs)
    for lane_id, expected in EXPECTED_LANES.items():
        lane_def = lane_defs[lane_id]
        assert lane_def["schema_version"] == "bb.e4.lane_def.v2"
        assert lane_def["status"] == "accepted"
        assert lane_def["config_id"] == expected["config_id"]
        assert lane_def["target_family"] == expected["target_family"]
        assert lane_def["kind"] == expected["kind"]
        assert lane_def["artifacts_root"] == f"docs/conformance/e4_target_support/{lane_id}"
        assert lane_def["capture"]["argv"] is None
        assert lane_def["capture"]["strategy"] in {"replay_dump", "runtime_records"}
        assert "north_star_stored_report_replay" == lane_def["compare"]["comparator"]
        assert all(_path_is_canonical(path) for path in lane_def["capture"]["inputs"])
        if lane_id == "breadboard_self_runtime_records_v1":
            joined_text = " ".join(lane_def["claim"]["scope"]["behaviors"] + lane_def["claim"]["scope"]["surfaces"])
            assert "self" in joined_text and "runtime" in joined_text and "records" in joined_text


def _support_claim_schema_version(row: Mapping[str, Any]) -> str:
    command = row["reverify_command"]
    argv = command["argv"]
    claim_path = argv[argv.index("--support-claim") + 1]
    claim = json.loads((REPO_ROOT / claim_path).read_text(encoding="utf-8"))
    return claim["schema_version"]


def test_inventory_report_roles_registry_and_regeneration_cover_north_star_lanes() -> None:
    """Generated indices must mirror the three accepted WS-J lanes instead of leaving promotion state in a builder-only side channel."""
    inventory = _inventory_by_lane()
    report_roles = _report_roles_by_lane()
    from scripts.e4_parity import regenerate_evidence
    comparator_by_lane = _comparator_lane_ids()
    regenerate_stage_ids = {stage.stage_id for stage in regenerate_evidence.LANE_DEF_REVERIFY_STAGES}

    for lane_id, expected in EXPECTED_LANES.items():
        assert lane_id in inventory
        row = inventory[lane_id]
        assert row["status"] == "accepted"
        assert row["config_id"] == expected["config_id"]
        assert row["target_family"] == expected["target_family"]
        assert row["kind"] == expected["kind"]
        assert row["builder"] is None
        assert _support_claim_schema_version(row) == SUPPORT_CLAIM_SCHEMA_VERSION
        assert row["comparator_id"] == "north_star_stored_report_replay"
        assert row["primitives"], f"{lane_id} must claim at least one behavior primitive"

        artifact_roles = row.get("artifact_roles")
        assert isinstance(artifact_roles, dict), f"{lane_id} artifact_roles must be a mapping"
        assert REQUIRED_ROLE_KEYS.issubset(artifact_roles)
        assert report_roles[lane_id] == artifact_roles
        assert comparator_by_lane[lane_id] == "north_star_stored_report_replay"
        assert f"lane_def_reverify_{lane_id}" in regenerate_stage_ids


def test_north_star_lanes_run_through_lane_def_and_support_claim_generators(tmp_path: Path) -> None:
    """WS-J lanes must run from lane_def/inventory data instead of a bespoke packet-builder literal."""
    for lane_id in EXPECTED_LANES:
        result = run_lane.run_lane(
            lane_id,
            stage="all",
            out_dir=tmp_path / lane_id,
            promote_accepted=False,
        )
        assert result["ok"] is True, result
        assert [stage["stage"] for stage in result["stages"]] == ["capture", "normalize", "replay", "compare", "claim"]
        assert result["stages"][0]["artifact_writer"] == "run_lane"
        assert "build_north_star_proof_packets.py" not in json.dumps(result)

    inventory = _inventory_by_lane()
    for lane_id, expected in EXPECTED_LANES.items():
        claim_path, manifest_path, node_gate_path = generate_support_claims.support_paths(inventory[lane_id])
        claim, _archive_ref = generate_support_claims._claim_for_lane(inventory[lane_id], claim_path, manifest_path)
        assert claim["schema_version"] == SUPPORT_CLAIM_SCHEMA_VERSION
        assert claim["scope"]["lane_id"] == lane_id
        assert claim["scope"]["config_id"] == expected["config_id"]
        assert claim["catalog_binding"]["segment_id"] == lane_id
        assert claim["catalog_binding"]["segment_hash"].startswith("sha256:")
        assert claim["catalog_binding"]["shared_segment_hash"].startswith("sha256:")
        assert node_gate_path.name.startswith("ct_north_star_")


def test_north_star_reverify_commands_and_checked_in_artifacts_are_canonical_and_pass_c4(capsys: pytest.CaptureFixture[str]) -> None:
    """The accepted WS-J rows must reverify canonical support-claim/evidence-manifest packets and exclude scratch/tmp/shared roots."""
    inventory = _inventory_by_lane()
    freeze = yaml.safe_load(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(freeze, dict) and isinstance(freeze.get("e4_configs"), dict)

    for lane_id, expected in EXPECTED_LANES.items():
        row = inventory[lane_id]
        config_id = expected["config_id"]
        assert config_id in freeze["e4_configs"]

        command = row.get("reverify_command")
        assert isinstance(command, dict), f"{lane_id} missing reverify_command"
        argv = command.get("argv")
        assert isinstance(argv, list) and all(isinstance(item, str) for item in argv)
        assert command.get("cwd") == "."
        _assert_command_targets_canonical_c4(config_id, argv)

        support_claim_path = REPO_ROOT / argv[argv.index("--support-claim") + 1]
        evidence_manifest_path = REPO_ROOT / argv[argv.index("--evidence-manifest") + 1]
        support_claim = _load_json(support_claim_path)
        evidence_manifest = _load_json(evidence_manifest_path)
        assert support_claim["accepted"] is True
        assert support_claim["schema_version"] == SUPPORT_CLAIM_SCHEMA_VERSION
        assert support_claim["scope"]["lane_id"] == lane_id
        assert support_claim["scope"]["target_family"] == expected["target_family"]
        assert support_claim["reverify_command"] == command
        assert support_claim["catalog_binding"]["segment_id"] == lane_id
        assert evidence_manifest["lane_id"] == lane_id
        assert set(evidence_manifest["forbidden_roots_checked"]) >= {"scratch", "tmp", "scratch_runs", "/shared_folders"}

        artifact_paths = [artifact["path"] for artifact in evidence_manifest["artifacts"] if isinstance(artifact, dict)]
        assert artifact_paths, f"{lane_id} evidence manifest must list artifacts"
        assert all(isinstance(path, str) and _path_is_canonical(path) for path in artifact_paths)

        c4_args = ["--repo-root", str(REPO_ROOT), *argv[2:]]
        assert c4_chain.main(c4_args) == 0

    captured = capsys.readouterr()
    assert "[e4-c4-chain] fail" not in captured.err
