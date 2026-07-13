from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import yaml
import pytest
from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH, stable_entries_hash


@pytest.fixture(autouse=True)
def _workspace_evidence_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docs_tmp").mkdir()
    monkeypatch.setenv("BB_WORKSPACE_ROOT", str(tmp_path))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256(path: Path) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

def _row_hash(row_id: str, row: object) -> str:
    import hashlib

    payload = {"row_id": row_id, "row": row}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()



def _refresh_evidence_artifact_hash(chain: dict[str, Path | str], role: str, digest: str) -> None:
    evidence_manifest = Path(chain["evidence_manifest"])
    payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    for artifact in payload["artifacts"]:
        if artifact.get("role") == role:
            artifact["sha256"] = digest
            _write_json(evidence_manifest, payload)
            return
    raise AssertionError(f"missing evidence manifest artifact role {role}")


def _write_support_claim_and_refresh_manifest(chain: dict[str, Path | str], payload: object) -> str:
    digest = _write_json(Path(chain["support_claim"]), payload)
    _refresh_evidence_artifact_hash(chain, "support_claim_ref", digest)
    return digest


def _write_comparator_and_refresh_refs(chain: dict[str, Path | str], payload: object) -> str:
    digest = _write_json(Path(chain["comparator"]), payload)
    _refresh_evidence_artifact_hash(chain, "comparator_ref", digest)
    support_payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    support_payload["comparator_ref"] = f"{Path(chain['comparator']).relative_to(chain['repo'])}#{digest}"
    _write_support_claim_and_refresh_manifest(chain, support_payload)
    return digest


def _write_validation_report_and_refresh_manifest(chain: dict[str, Path | str], payload: object) -> str:
    digest = _write_json(Path(chain["validation_report"]), payload)
    _refresh_evidence_artifact_hash(chain, "validator_output", digest)
    return digest


def _write_freeze_manifest_and_refresh_refs(chain: dict[str, Path | str], payload: object) -> str:
    freeze_path = Path(chain["repo"]) / "config" / "e4_target_freeze_manifest.yaml"
    freeze_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    row = payload["e4_configs"][chain["config_id"]]  # type: ignore[index]
    digest = _row_hash(str(chain["config_id"]), row)
    _refresh_evidence_artifact_hash(chain, "freeze_manifest", digest)
    support_payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    support_payload["freeze_ref"] = f"config/e4_target_freeze_manifest.yaml#{chain['config_id']}#{digest}"
    _write_support_claim_and_refresh_manifest(chain, support_payload)
    return digest


def _build_chain(tmp_path: Path, *, checkout_local_sources: bool = False) -> dict[str, Path | str]:
    repo = tmp_path / "breadboard_repo_integration_main_20260326"
    source_ref_root = "docs/conformance/e4_target_support/_fixtures/c4_packet" if checkout_local_sources else "docs_tmp/phase_15/packet"
    docs_tmp = repo / source_ref_root if checkout_local_sources else Path(os.environ["BB_WORKSPACE_ROOT"]) / source_ref_root
    _write_json(
        repo / "conformance" / "comparators" / "registry.json",
        {
            "schema_version": "bb.e4.comparator_registry.v1",
            "registry_id": "e4_comparator_registry_v1",
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "comparators": [
                {
                    "comparator_class": "deterministic_replay",
                    "comparator_id": "codex_stored_report_replay",
                    "entrypoint": {
                        "callable": "compare",
                        "module": "conformance.comparators.codex_cli",
                    },
                    "input_roles": ["capture_ref", "replay_ref", "comparator_ref"],
                    "lane_ids": ["codex_cli_e4_capture_probe_v1"],
                    "report_schema_version": "bb.e4.comparator_report.v1",
                }
            ],
        },
    )
    config_id = "codex_cli_gpt55_e4_capture_probe_v1"
    lane_id = "codex_cli_e4_capture_probe_v1"
    scope = {
        "config_id": config_id,
        "lane_id": lane_id,
        "provider_model": "gpt-5.5",
        "run_id": "20260630_codex_gpt55_capture_probe",
        "sandbox_mode": "read-only",
        "target_version": "codex-cli 0.139.0",
    }

    raw_rollout = docs_tmp / "canonical_capture" / "rollout.jsonl"
    raw_rollout_hash = _write_json(raw_rollout, {"type": "turn.completed"})
    replay_session = docs_tmp / "canonical_capture" / "exports" / "replay_session.json"
    replay_session_hash = _write_json(replay_session, [{"role": "user"}, {"role": "assistant"}])
    secret_scan = docs_tmp / "secret_scan_report.json"
    secret_scan_hash = _write_json(secret_scan, {"schema_version": "bb.e4.secret_scan_report.v1", "status": "passed", "findings": []})
    parity_results = docs_tmp / "parity_results.jsonl"
    parity_results.parent.mkdir(parents=True, exist_ok=True)
    parity_results.write_text(json.dumps({"status": "passed", "scenario": lane_id}) + "\n", encoding="utf-8")
    import hashlib

    parity_hash = "sha256:" + hashlib.sha256(parity_results.read_bytes()).hexdigest()

    evidence_dir = repo / "docs" / "conformance" / "e4_target_support" / lane_id
    capture = evidence_dir / "raw_capture_manifest.json"
    capture_hash = _write_json(
        capture,
        {
            "schema_version": "bb.e4.raw_capture_manifest.v1",
            **scope,
            "accepted_as_capture_ref": True,
            "capture_class": "raw_target_capture",
            "generated_at_utc": "2026-07-01T00:00:00Z",
            "lineage_rationale": "Raw Codex rollout is present under canonical docs_tmp packet evidence.",
            "raw_source_status": "canonical_raw_present",
            "source_artifacts": [{"path": f"{source_ref_root}/canonical_capture/rollout.jsonl", "sha256": raw_rollout_hash}],
            "source_hashes": {f"{source_ref_root}/canonical_capture/rollout.jsonl": raw_rollout_hash},
        },
    )
    replay = evidence_dir / "bb_replay_result.json"
    replay_hash = _write_json(
        replay,
        {
            "schema_version": "bb.e4.bb_replay_result.v1",
            **scope,
            "errors": [],
            "exit_status": "passed",
            "generated_at_utc": "2026-07-01T00:00:00Z",
            "input_hashes": {f"{source_ref_root}/canonical_capture/exports/replay_session.json": replay_session_hash},
            "input_refs": [f"{source_ref_root}/canonical_capture/exports/replay_session.json"],
            "normalized_events": [{"tool": "shell_command", "status": "completed"}],
            "replay_engine": {"name": "convert_codex_rollout_to_replay_session.py"},
            "warnings": [],
        },
    )
    comparator = evidence_dir / "comparator_report.json"
    comparator_hash = _write_json(
        comparator,
        {
            "schema_version": "bb.e4.comparator_report.v1",
            **scope,
            "actual_refs": [str(replay.relative_to(repo))],
            "comparator_class": "codex_live_capture_exact_probe",
            "details": [{"name": "command_and_answer_match", "passed": True}],
            "assertions": [
                {
                    "name": "command_and_answer_match",
                    "status": "passed",
                    "observed": {
                        "assistant_answer": "capture probe complete",
                        "tool_command": "printf 'capture probe complete'",
                    },
                    "expected": {
                        "assistant_answer": "capture probe complete",
                        "tool_command": "printf 'capture probe complete'",
                    },
                }
            ],
            "expected_refs": [str(capture.relative_to(repo))],
            "failed": 0,
            "generated_at_utc": "2026-07-01T00:00:00Z",
            "input_hashes": {str(capture.relative_to(repo)): capture_hash, str(replay.relative_to(repo)): replay_hash},
            "normalization_rules": ["preserve_command_output_and_assistant_text"],
            "passed": 1,
            "scope": scope,
            "tool_alias_map": {},
            "warned": 0,
        },
    )
    claim_dir = repo / "docs" / "conformance" / "support_claims"
    support_claim = claim_dir / "codex_cli_gpt55_e4_capture_probe_v1_c4_support_claim.json"
    evidence_manifest = claim_dir / "codex_cli_gpt55_e4_capture_probe_v1_c4_evidence_manifest.json"
    validation_report = claim_dir / "codex_cli_gpt55_e4_capture_probe_v1_c4_validation_report.json"
    validation_hash = _write_json(
        validation_report,
        {
            "schema_version": "bb.e4.validation_report.v1",
            "ok": True,
            "target": "codex-cli-gpt55-e4-capture-probe-v1",
            "checks": [{"name": "exact_scope_chain", "passed": True}],
        },
    )

    config = repo / "agent_configs" / "misc" / "codex_cli_gpt55_e4_capture_probe_v1.yaml"
    config_hash = _write_json(config, {"profile": {"name": "codex-cli-gpt55-e4-capture-probe-v1"}})
    freeze_manifest = repo / "config" / "e4_target_freeze_manifest.yaml"
    freeze_manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest_payload = {
        "schema_version": "e4_target_freeze_manifest_v1",
        "manifest_updated_utc": "2026-07-01T00:00:00Z",
        "e4_configs": {
            config_id: {
                "config_path": str(config.relative_to(repo)),
                "harness": {
                    "family": "codex_cli",
                    "runtime_surface": {"provider_model": "gpt-5.5"},
                    "upstream_commit": "npm-dist-shasum:e8fd38c8e293d358b8ae14102dd8f18213d4c8a0",
                    "upstream_commit_date": "2026-06-09T20:17:25.855Z",
                    "upstream_release_label": "@openai/codex@0.139.0",
                    "upstream_repo": "https://github.com/openai/codex",
                },
                "calibration_anchor": {
                    "class": "codex_cli_live_capture",
                    "evidence_paths": [
                        str(capture.relative_to(repo)),
                        str(replay.relative_to(repo)),
                        str(comparator.relative_to(repo)),
                        f"{source_ref_root}/parity_results.jsonl",
                        str(support_claim.relative_to(repo)),
                        str(evidence_manifest.relative_to(repo)),
                        str(validation_report.relative_to(repo)),
                    ],
                    "run_id": scope["run_id"],
                    "scenario_id": lane_id,
                },
            }
        },
    }
    freeze_manifest.write_text(yaml.safe_dump(manifest_payload, sort_keys=False), encoding="utf-8")
    freeze_hash = _row_hash(config_id, manifest_payload["e4_configs"][config_id])

    ledger = evidence_dir / "atomic_feature_ledger.json"
    ledger_row = {
        "feature_id": "feat_codex_cli_probe_capture",
        "dedupe_key": "target.codex_cli.capture_probe",
        "target": "codex",
        "family": "tool",
        "claim_type": "support",
        "evidence_tier": "C4",
        "source_refs": [str(capture.relative_to(repo))],
    }
    ledger_hash = _write_json(ledger, {"schema_version": "bb.atomic_feature_ledger.v1", "rows": [ledger_row]})
    ledger_row_hash = _row_hash(ledger_row["feature_id"], ledger_row)

    support_payload = {
        "schema_version": "bb.e4.support_claim.v1",
        "accepted": True,
        "claim_id": "claim.codex_cli_gpt55_e4_capture_probe_v1.c4",
        "config_id": config_id,
        "lane_id": lane_id,
        "scope": scope,
        "exclusions": ["broad Codex CLI support", "provider parity", "UI parity"],
        "freeze_ref": f"config/e4_target_freeze_manifest.yaml#{config_id}#{freeze_hash}",
        "capture_ref": f"{capture.relative_to(repo)}#{capture_hash}",
        "replay_ref": f"{replay.relative_to(repo)}#{replay_hash}",
        "comparator_ref": f"{comparator.relative_to(repo)}#{comparator_hash}",
        "evidence_manifest_ref": str(evidence_manifest.relative_to(repo)),
        "validation_refs": [f"{validation_report.relative_to(repo)}#{validation_hash}"],
        "ledger_row_refs": [f"{ledger.relative_to(repo)}#{ledger_row['feature_id']}#{ledger_row_hash}"],
        "generated_at_utc": "2026-07-01T00:00:00Z",
    }
    support_hash = _write_json(support_claim, support_payload)
    evidence_payload = {
        "schema_version": "bb.e4.evidence_manifest.v1",
        "claim_id": support_payload["claim_id"],
        "lane_id": lane_id,
        "config_id": config_id,
        "generated_at_utc": "2026-07-01T00:00:00Z",
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "secret_scan_ref": f"{source_ref_root}/secret_scan_report.json#{secret_scan_hash}",
        "artifacts": [
            {"role": "freeze_manifest", "path": "config/e4_target_freeze_manifest.yaml", "sha256": freeze_hash},
            {"role": "target_config", "path": str(config.relative_to(repo)), "sha256": config_hash},
            {"role": "atomic_feature_ledger", "path": str(ledger.relative_to(repo)), "sha256": ledger_hash},
            {"role": "capture_ref", "path": str(capture.relative_to(repo)), "sha256": capture_hash, "derived_from": [f"{source_ref_root}/canonical_capture/rollout.jsonl#{raw_rollout_hash}"]},
            {"role": "replay_ref", "path": str(replay.relative_to(repo)), "sha256": replay_hash, "derived_from": [f"{source_ref_root}/canonical_capture/exports/replay_session.json#{replay_session_hash}"]},
            {"role": "comparator_ref", "path": str(comparator.relative_to(repo)), "sha256": comparator_hash, "derived_from": [f"{capture.relative_to(repo)}#{capture_hash}", f"{replay.relative_to(repo)}#{replay_hash}"]},
            {"role": "parity_results", "path": f"{source_ref_root}/parity_results.jsonl", "sha256": parity_hash},
            {"role": "support_claim_ref", "path": str(support_claim.relative_to(repo)), "sha256": support_hash},
            {"role": "secret_scan_report", "path": f"{source_ref_root}/secret_scan_report.json", "sha256": secret_scan_hash},
            {"role": "validator_output", "path": str(validation_report.relative_to(repo)), "sha256": validation_hash},
        ],
    }
    _write_json(evidence_manifest, evidence_payload)
    return {
        "repo": repo,
        "config_id": config_id,
        "support_claim": support_claim,
        "evidence_manifest": evidence_manifest,
        "comparator": comparator,
        "capture": capture,
        "replay": replay,
        "validation_report": validation_report,
    }


def test_c4_chain_validator_accepts_complete_exact_scope(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_accept", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is True
    assert report["accepted"] is True
    assert report["errors"] == []


def test_c4_chain_validator_accepts_checkout_local_sources_without_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        "validate_e4_c4_chain_checkout_local",
        "scripts/validate_e4_c4_chain.py",
    )
    monkeypatch.delenv("BB_WORKSPACE_ROOT")
    chain = _build_chain(tmp_path, checkout_local_sources=True)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
        rerun_comparators=False,
        no_rerun_reason="checkout-local fixture validation",
    )

    assert report["ok"] is True
    assert report["accepted"] is True
    assert report["errors"] == []

def test_c4_chain_blame_names_only_mutated_stable_catalog_artifact(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_catalog_blame", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    repo = Path(chain["repo"])
    capture_path = Path(chain["capture"])
    replay_path = Path(chain["replay"])
    support_payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    lane_id = str(support_payload["lane_id"])
    scope = support_payload["scope"]
    capture_ref = str(capture_path.relative_to(repo))
    replay_ref = str(replay_path.relative_to(repo))
    previous_capture_sha = _sha256(capture_path)
    replay_sha = _sha256(replay_path)
    catalog_entries = [
        {
            "role_id": f"{lane_id}:capture_ref",
            "path": capture_ref,
            "sha256": previous_capture_sha,
            "bytes": capture_path.stat().st_size,
            "exists": True,
            "artifact_kind": "capture",
            "lane_id": lane_id,
            "media_type": "application/json",
            "derived_from": [],
            "generated_by": "tests/test_e4_c4_chain_validation.py",
        },
        {
            "role_id": f"{lane_id}:replay_ref",
            "path": replay_ref,
            "sha256": replay_sha,
            "bytes": replay_path.stat().st_size,
            "exists": True,
            "artifact_kind": "replay",
            "lane_id": lane_id,
            "media_type": "application/json",
            "derived_from": [],
            "generated_by": "tests/test_e4_c4_chain_validation.py",
        },
    ]
    stale_catalog_hash = stable_entries_hash(catalog_entries[:1])
    live_catalog_hash = stable_entries_hash(catalog_entries)
    assert stale_catalog_hash != live_catalog_hash
    _write_json(
        repo / CATALOG_PATH,
        {
            "schema_version": "bb.e4.artifact_catalog.v1",
            "catalog_id": "e4_artifact_catalog_v1",
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "revision": 2,
            "entries": catalog_entries,
            "integrity": {
                "entry_count": len(catalog_entries),
                "entries_hash": "sha256:" + "e" * 64,
                "stable_entries_hash": live_catalog_hash,
            },
        },
    )
    support_payload.update(
        {
            "schema_version": "bb.e4.support_claim.v2",
            "kind": "target_support",
            "summary": "Codex CLI capture-probe C4 chain is bound to stable catalog evidence.",
            "target_family": "codex",
            "target_version": scope["target_version"],
            "run_id": scope["run_id"],
            "provider_model": scope["provider_model"],
            "sandbox_mode": scope["sandbox_mode"],
            "exclusion_facets": {
                "excluded_families": ["all_other_families"],
                "excluded_behavior_classes": ["broad_target_parity"],
            },
            "claim_semantics": {
                "asserted_behaviors": [
                    {
                        "behavior_id": "command_and_answer_match",
                        "description": "The replayed command and assistant answer match the captured expectation.",
                        "comparator_assertion_ids": ["command_and_answer_match"],
                    }
                ]
            },
            "catalog_binding": {
                "catalog_path": CATALOG_PATH,
                "catalog_revision": 2,
                "catalog_hash": stale_catalog_hash,
            },
            "reverify_command": {
                "argv": [
                    "python",
                    "scripts/validate_e4_c4_chain.py",
                    "--config-id",
                    str(chain["config_id"]),
                    "--support-claim",
                    str(Path(chain["support_claim"]).relative_to(repo)),
                    "--no-rerun-comparators",
                    "--blame",
                ],
                "cwd": ".",
            },
        }
    )
    _write_support_claim_and_refresh_manifest(chain, support_payload)

    capture_payload = json.loads(capture_path.read_text(encoding="utf-8"))
    capture_payload["lineage_rationale"] = "Mutated after catalog pinning for blame attribution."
    current_capture_sha = _write_json(capture_path, capture_payload)
    assert current_capture_sha != previous_capture_sha
    _refresh_evidence_artifact_hash(chain, "capture_ref", current_capture_sha)
    support_payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    support_payload["capture_ref"] = f"{capture_ref}#{current_capture_sha}"
    _write_support_claim_and_refresh_manifest(chain, support_payload)
    comparator_payload = json.loads(Path(chain["comparator"]).read_text(encoding="utf-8"))
    comparator_payload["input_hashes"][capture_ref] = current_capture_sha
    _write_comparator_and_refresh_refs(chain, comparator_payload)

    report = module.validate_c4_chain(
        repo_root=repo,
        freeze_manifest_path=repo / "config" / "e4_target_freeze_manifest.yaml",
        config_id=str(chain["config_id"]),
        support_claim_path=Path(chain["support_claim"]),
        evidence_manifest_path=Path(chain["evidence_manifest"]),
        rerun_comparators=False,
        no_rerun_reason="test fixture skips comparator rerun",
        blame=True,
    )

    catalog_errors = [
        error
        for error in report["gate_errors"]
        if "support_claim.catalog_binding.catalog_hash mismatch" in error["message"]
    ]
    assert len(catalog_errors) == 1
    [catalog_error] = catalog_errors
    assert catalog_error["blame"] == [
        {
            "role_id": f"{lane_id}:capture_ref",
            "path": str(capture_path.relative_to(repo)),
            "prev_sha256": previous_capture_sha,
            "cur_sha256": current_capture_sha,
        }
    ]

def test_c4_chain_validator_freeze_ref_hashes_selected_row_only(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_row_hash", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    freeze_manifest = chain["repo"] / "config" / "e4_target_freeze_manifest.yaml"
    payload = yaml.safe_load(freeze_manifest.read_text(encoding="utf-8"))
    payload["e4_configs"]["other_config_v1"] = {
        "config_path": "agent_configs/misc/other.yaml",
        "harness": {"runtime_surface": {"provider_model": "no-provider"}},
        "calibration_anchor": {"evidence_paths": []},
    }
    (chain["repo"] / "agent_configs" / "misc" / "other.yaml").write_text("model: no-provider\n", encoding="utf-8")
    freeze_manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=freeze_manifest,
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is True
    assert report["accepted"] is True
    assert report["errors"] == []



def test_c4_chain_validator_rejects_unaccepted_support_claim(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_unaccepted", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload["accepted"] = False
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("support_claim.accepted" in error for error in report["errors"])


def test_c4_chain_validator_rejects_scope_broader_than_comparator(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_scope", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload["scope"]["provider_model"] = "gpt-5"
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("provider_model" in error for error in report["errors"])


def test_c4_chain_validator_rejects_comparator_without_assertions(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_missing_comparator_assertions", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["comparator"]).read_text(encoding="utf-8"))
    payload.pop("assertions")
    _write_comparator_and_refresh_refs(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("comparator_ref.assertions must be a non-empty list" in error for error in report["errors"])


def test_c4_chain_validator_rejects_comparator_assertions_missing_observed_or_expected(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_incomplete_comparator_assertions", "scripts/validate_e4_c4_chain.py")

    for missing_key in ("observed", "expected"):
        chain = _build_chain(tmp_path / missing_key)
        payload = json.loads(Path(chain["comparator"]).read_text(encoding="utf-8"))
        payload["assertions"][0].pop(missing_key)
        _write_comparator_and_refresh_refs(chain, payload)

        report = module.validate_c4_chain(
            repo_root=chain["repo"],
            freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
            config_id=chain["config_id"],
            support_claim_path=chain["support_claim"],
            evidence_manifest_path=chain["evidence_manifest"],
        )

        assert report["ok"] is False, missing_key
        assert any(
            f"comparator_ref.assertions[0].{missing_key}: missing" in error
            for error in report["errors"]
        ), missing_key


def test_c4_chain_validator_rejects_comparator_assertion_with_non_passed_status(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_failed_comparator_assertion", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["comparator"]).read_text(encoding="utf-8"))
    payload["assertions"][0]["status"] = "failed"
    _write_comparator_and_refresh_refs(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("comparator_ref.assertions[0].status must be passed" in error for error in report["errors"])


def test_c4_chain_validator_rejects_comparator_assertion_observed_expected_mismatch(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_mismatched_comparator_assertion", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["comparator"]).read_text(encoding="utf-8"))
    payload["assertions"][0]["observed"]["assistant_answer"] = "different answer"
    _write_comparator_and_refresh_refs(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("comparator_ref.assertions[0].observed must match expected" in error for error in report["errors"])


def test_c4_chain_validator_rejects_derived_capture_as_promotion_ref(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_capture", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["capture"]).read_text(encoding="utf-8"))
    payload["raw_source_status"] = "derived_from_unavailable_raw"
    Path(chain["capture"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("canonical raw" in error for error in report["errors"])


def test_c4_chain_validator_reports_missing_evidence_manifest_ref(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_missing_manifest_ref", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload.pop("evidence_manifest_ref")
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
    )

    assert report["ok"] is False
    assert any("support_claim.evidence_manifest_ref: missing" in error for error in report["errors"])


def test_c4_chain_validator_reports_missing_input_files(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_missing_files", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    Path(chain["evidence_manifest"]).unlink()

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
    )

    assert report["ok"] is False
    assert any("evidence_manifest: missing path" in error for error in report["errors"])


def test_c4_chain_validator_rejects_mismatched_freeze_ref(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_bad_freeze_ref", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload["freeze_ref"] = "config/e4_target_freeze_manifest.yaml#other_config;sha256:" + ("0" * 64)
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("support_claim.freeze_ref" in error for error in report["errors"])

def test_c4_chain_validator_rejects_empty_validation_refs(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_empty_validation_refs", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload["validation_refs"] = []
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("validation_refs must include validator output" in error for error in report["errors"])



def test_c4_chain_validator_rejects_validation_ref_not_manifest_validator_output(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_mismatched_validator_ref", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    alternate_report = chain["repo"] / "docs" / "conformance" / "alternate_validation_report.json"
    alternate_hash = _write_json(
        alternate_report,
        {
            "schema_version": "bb.e4.validation_report.v1",
            "ok": True,
            "checks": [{"name": "alternate_exact_scope_chain", "passed": True}],
        },
    )
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload["validation_refs"] = [f"{alternate_report.relative_to(chain['repo'])}#{alternate_hash}"]
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any(
        "support_claim.validation_refs does not match evidence_manifest role validator_output" in error
        for error in report["errors"]
    )


def test_c4_chain_validator_accepts_extra_ok_validation_ref_before_manifest_validator_output(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_extra_validation_ref", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    alternate_report = chain["repo"] / "docs" / "conformance" / "alternate_validation_report.json"
    alternate_hash = _write_json(
        alternate_report,
        {
            "schema_version": "bb.e4.validation_report.v1",
            "ok": True,
            "checks": [{"name": "alternate_exact_scope_chain", "passed": True}],
        },
    )
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload["validation_refs"] = [
        f"{alternate_report.relative_to(chain['repo'])}#{alternate_hash}",
        *payload["validation_refs"],
    ]
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is True
    assert report["accepted"] is True
    assert report["errors"] == []

def test_c4_chain_validator_rejects_validator_output_missing_from_manifest(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_missing_validator_role", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    payload = json.loads(Path(chain["evidence_manifest"]).read_text(encoding="utf-8"))
    payload["artifacts"] = [
        artifact for artifact in payload["artifacts"] if artifact.get("role") != "validator_output"
    ]
    Path(chain["evidence_manifest"]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("evidence_manifest.artifacts missing roles: validator_output" in error for error in report["errors"])


def test_c4_chain_validator_rejects_validator_output_missing_from_anchor(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_missing_validator_anchor", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    freeze_path = chain["repo"] / "config" / "e4_target_freeze_manifest.yaml"
    freeze_payload = yaml.safe_load(freeze_path.read_text(encoding="utf-8"))
    anchor = freeze_payload["e4_configs"][chain["config_id"]]["calibration_anchor"]
    validator_path = str(Path(chain["validation_report"]).relative_to(chain["repo"]))
    anchor["evidence_paths"] = [path for path in anchor["evidence_paths"] if path != validator_path]
    _write_freeze_manifest_and_refresh_refs(chain, freeze_payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=freeze_path,
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("calibration_anchor.evidence_paths missing validator_output" in error for error in report["errors"])

def test_c4_chain_validator_rejects_failed_validation_ref(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_failed_validation_ref", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    failed_hash = _write_validation_report_and_refresh_manifest(
        chain,
        {
            "schema_version": "bb.e4.validation_report.v1",
            "ok": False,
            "checks": [{"name": "exact_scope_chain", "passed": False}],
        },
    )
    payload = json.loads(Path(chain["support_claim"]).read_text(encoding="utf-8"))
    payload["validation_refs"] = [f"{Path(chain['validation_report']).relative_to(chain['repo'])}#{failed_hash}"]
    _write_support_claim_and_refresh_manifest(chain, payload)

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("referenced validator output must have ok=true" in error for error in report["errors"])

def test_c4_chain_validator_reports_malformed_capture_ref_json(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_bad_capture_json", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    Path(chain["capture"]).write_text("{", encoding="utf-8")

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("capture_ref: unable to load JSON" in error for error in report["errors"])


def test_c4_chain_validator_reports_malformed_replay_ref_json(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_bad_replay_json", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    Path(chain["replay"]).write_text("{", encoding="utf-8")

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("replay_ref: unable to load JSON" in error for error in report["errors"])


def test_c4_chain_validator_reports_malformed_comparator_ref_json(tmp_path: Path) -> None:
    module = _load_module("validate_e4_c4_chain_bad_comparator_json", "scripts/validate_e4_c4_chain.py")
    chain = _build_chain(tmp_path)
    Path(chain["comparator"]).write_text("{", encoding="utf-8")

    report = module.validate_c4_chain(
        repo_root=chain["repo"],
        freeze_manifest_path=chain["repo"] / "config" / "e4_target_freeze_manifest.yaml",
        config_id=chain["config_id"],
        support_claim_path=chain["support_claim"],
        evidence_manifest_path=chain["evidence_manifest"],
    )

    assert report["ok"] is False
    assert any("comparator_ref: unable to load JSON" in error for error in report["errors"])


