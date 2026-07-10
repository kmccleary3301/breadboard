from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agentic_coder_prototype.conformance import c4_chain
from scripts.e4_parity import lane_definitions, run_lane
from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as adapter
from scripts.e4_parity.lane_definitions import load_manifest_lane_def


ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
LANE_DIR = ROOT / "config" / "e4_lanes"
LEGACY_PATH = LANE_DIR / f"{LANE_ID}.yaml"
MANIFEST_PATH = LANE_DIR / f"{LANE_ID}.manifest.yaml"
PAYLOAD_SOURCE_PATH = LANE_DIR / f"{LANE_ID}.payloads.yaml"


def _load_yaml(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _runtime_lane(lane_id: str, output_path: Path) -> dict[str, object]:
    return {
        "lane_id": lane_id,
        "config_id": "fixture.config",
        "kind": "target_support",
        "status": "claimed",
        "capture": {
            "strategy": "legacy_builder",
            "argv": ["fixture-capture", "--json-out", str(output_path)],
            "inputs": [],
        },
        "normalize": {"translator": "fixture", "config": {}},
        "replay": {"session": None, "comparator_class": "byte"},
        "compare": {"comparator": "byte", "config": {}},
        "claim": {"scope": {"behaviors": ["fixture"]}, "exclusions": []},
        "artifacts_root": "artifacts/fixture",
        "reverify_command": None,
    }


def test_retired_legacy_payload_blocks_remain_available_as_manifest_runtime_virtual_inputs() -> None:
    legacy = _load_yaml(LEGACY_PATH)
    legacy_packet_constants = legacy["normalize"]["config"]["packet_constants"]
    assert isinstance(legacy_packet_constants, dict)

    promoted_payloads = _load_yaml(PAYLOAD_SOURCE_PATH)
    runtime_config = load_manifest_lane_def(MANIFEST_PATH)["normalize"]["config"]
    runtime_packet_constants = runtime_config["packet_constants"]
    assert runtime_packet_constants["payload_templates"] == promoted_payloads["payload_templates"]
    assert runtime_packet_constants["substitutions"] == promoted_payloads["substitutions"]

    roles = runtime_config["roles"]
    assert runtime_config["runtime_payload_inputs"] == {
        roles[role]: payload
        for role, payload in promoted_payloads["payload_templates"].items()
        if role in roles
    }
    assert {"payload_templates", "substitutions"}.isdisjoint(legacy_packet_constants)


def test_lane_lock_sha256_reports_exact_file_digest_and_none_for_legacy(tmp_path: Path) -> None:
    lock_bytes = b'{"schema_version":"bb.e4.lane_lock.v1","value":"current"}\n'
    (tmp_path / "migrated.lock.json").write_bytes(lock_bytes)

    assert lane_definitions.lane_lock_sha256("migrated", tmp_path) == _sha256(lock_bytes)
    assert lane_definitions.lane_lock_sha256("legacy", tmp_path) is None


def test_run_lane_binds_current_lock_digest_to_executed_and_nonexecuted_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrated_id = "manifest_migrated_fixture"
    legacy_id = "legacy_fixture"
    output_path = tmp_path / "capture.json"
    lock_path = tmp_path / f"{migrated_id}.lock.json"
    lock_path.write_bytes(b'{"generation":1}\n')
    expected_digest = _sha256(lock_path.read_bytes())
    lanes = {
        migrated_id: _runtime_lane(migrated_id, output_path),
        legacy_id: _runtime_lane(legacy_id, output_path),
    }
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps({"lanes": []}), encoding="utf-8")

    def successful_run(command: list[str], **_: object) -> SimpleNamespace:
        destination = Path(command[command.index("--json-out") + 1])
        destination.write_text('{"ok":true}\n', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_lane, "load_lane_defs", lambda _directory: lanes)
    monkeypatch.setattr(run_lane.subprocess, "run", successful_run)

    executed = run_lane.run_lane(
        migrated_id,
        stage="capture",
        out_dir=None,
        lane_def_dir=tmp_path,
        inventory_path=inventory_path,
    )["stages"][0]
    nonexecuted = run_lane.run_lane(
        migrated_id,
        stage="normalize",
        out_dir=None,
        lane_def_dir=tmp_path,
        inventory_path=inventory_path,
    )["stages"][0]
    legacy = run_lane.run_lane(
        legacy_id,
        stage="normalize",
        out_dir=None,
        lane_def_dir=tmp_path,
        inventory_path=inventory_path,
    )["stages"][0]

    assert executed["outcome"] == "executed_pass"
    assert executed["lock_sha256"] == expected_digest
    assert nonexecuted["outcome"] == "executed_fail"
    assert nonexecuted["lock_sha256"] == expected_digest
    assert legacy["outcome"] == "executed_fail"
    assert legacy["lock_sha256"] is None


def test_resolve_display_finds_docs_tmp_at_workspace_ancestor_from_nested_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    nested_root = workspace / "docs_tmp" / "phase_20" / "worktrees" / "wsF3"
    nested_root.mkdir(parents=True)
    (workspace / "docs_tmp" / "phase_15").mkdir(parents=True)
    wrong_workspace = tmp_path / "incorrect-root-parent"

    monkeypatch.setattr(adapter, "ROOT", nested_root)
    monkeypatch.setattr(adapter, "WORKSPACE", wrong_workspace)

    resolved = adapter._resolve_display("docs_tmp/phase_15/ledger.json#feature-id")

    assert resolved == workspace / "docs_tmp" / "phase_15" / "ledger.json"
    assert not resolved.is_relative_to(wrong_workspace)


def test_c4_chain_resolves_docs_tmp_ref_from_nested_packet_worktree(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = workspace / "docs_tmp" / "phase_20" / "worktrees" / "wsF3"
    ledger_path = workspace / "docs_tmp" / "phase_15" / "ledger.json"
    repo_root.mkdir(parents=True)
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text('{"features":[]}\n', encoding="utf-8")

    assert c4_chain._workspace_root(repo_root) == workspace
    assert (
        c4_chain._resolve_path(
            repo_root,
            "docs_tmp/phase_15/ledger.json#feature-id#sha256:" + "a" * 64,
        )
        == ledger_path
    )


def test_c4_chain_keeps_direct_workspace_repo_path_resolution_compatible(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = workspace / "repo"
    workspace_artifact = workspace / "docs_tmp" / "phase_15" / "ledger.json"
    repo_artifact = repo_root / "artifacts" / "capture.json"
    workspace_artifact.parent.mkdir(parents=True)
    repo_artifact.parent.mkdir(parents=True)
    workspace_artifact.write_text('{"features":[]}\n', encoding="utf-8")
    repo_artifact.write_text('{"capture":[]}\n', encoding="utf-8")

    assert c4_chain._workspace_root(repo_root) == workspace
    assert (
        c4_chain._resolve_path(repo_root, "docs_tmp/phase_15/ledger.json#feature-id")
        == workspace_artifact
    )
    assert c4_chain._resolve_path(repo_root, "artifacts/capture.json") == repo_artifact
