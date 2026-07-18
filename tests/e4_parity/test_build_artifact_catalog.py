from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.compilation.primitive_records import (
    canonical_record_bytes,
    finalize_record,
    get_spec,
    sha256_ref,
)
from agentic_coder_prototype.conformance.catalog_binding import catalog_segments, stable_entries_hash
from scripts.e4_parity import build_artifact_catalog as builder


CHECKOUT_DIRNAME = "breadboard_repo_integration_main_20260326"
GENERATED_AT = "2026-07-03T00:00:00Z"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog_entry(catalog: dict[str, Any], role_id: str) -> dict[str, Any]:
    for entry in catalog["entries"]:
        if entry["role_id"] == role_id:
            return entry
    raise AssertionError(f"missing catalog role {role_id!r}")

def _run_catalog_cli(paths: dict[str, Path], *, generated_at_utc: str = GENERATED_AT) -> None:
    assert builder.main(
        [
            "--inventory",
            str(paths["inventory"]),
            "--report-roles",
            str(paths["report_roles"]),
            "--output",
            str(paths["output"]),
            "--generated-at-utc",
            generated_at_utc,
        ]
    ) == 0



def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _patch_roots(monkeypatch: pytest.MonkeyPatch, workspace: Path, checkout: Path) -> None:
    monkeypatch.setattr(builder, "ROOT", checkout)
    monkeypatch.setattr(builder, "WORKSPACE", workspace)
    monkeypatch.setattr(builder, "CHECKOUT_PREFIX", f"{checkout.name}/")


def _write_catalog_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_report_roles: bool = True,
) -> dict[str, Path]:
    workspace = tmp_path / "workspace"
    checkout = workspace / CHECKOUT_DIRNAME
    docs = checkout / "docs" / "conformance"
    support_claims = docs / "support_claims"
    lane_dir = docs / "e4_target_support" / "lane_alpha"
    docs_tmp = workspace / "docs_tmp" / "phase_16"
    _patch_roots(monkeypatch, workspace, checkout)

    capture_path = lane_dir / "raw_capture_manifest.json"
    support_claim_path = support_claims / "lane_alpha_v1_c4_support_claim.json"
    evidence_manifest_path = support_claims / "lane_alpha_v1_c4_evidence_manifest.json"
    comparator_path = lane_dir / "comparator_report.json"
    checkout_static_path = docs / "static_prefixed_report.json"
    docs_tmp_static_path = docs_tmp / "static_plan_artifact.txt"
    script_path = checkout / "scripts" / "e4_parity" / "scaffold_e4_target_lane.py"

    _write_json(capture_path, {"kind": "capture", "ordinal": 1})
    _write_json(support_claim_path, {"accepted": True, "claim_id": "lane_alpha_v1_c4_support_claim"})
    _write_json(comparator_path, {"kind": "comparator", "accepted": True})
    _write_text(checkout_static_path, "checkout static artifact\n")
    _write_text(docs_tmp_static_path, "docs_tmp static artifact\n")
    _write_text(script_path, "# scaffold helper\n")

    capture_rel = _relative(capture_path, checkout)
    support_claim_rel = _relative(support_claim_path, checkout)
    comparator_rel = _relative(comparator_path, checkout)
    _write_json(
        evidence_manifest_path,
        {
            "schema_version": "bb.e4.evidence_manifest.v1",
            "claim_id": "lane_alpha_v1_c4_support_claim",
            "config_id": "lane_alpha_v1",
            "lane_id": "lane_alpha",
            "generated_at_utc": GENERATED_AT,
            "artifacts": [
                {
                    "role": "capture_ref",
                    "path": capture_rel,
                    "sha256": _sha256_file(capture_path),
                },
                {
                    "role": "support_claim_ref",
                    "path": support_claim_rel,
                    "sha256": _sha256_file(support_claim_path),
                },
                {
                    "role": "comparator_ref",
                    "path": comparator_rel,
                    "sha256": _sha256_file(comparator_path),
                    "derived_from": [capture_rel],
                },
            ],
        },
    )
    inventory_path = docs / "e4_lane_inventory.json"
    _write_json(
        inventory_path,
        {
            "schema_version": "bb.e4.lane_inventory.v1",
            "inventory_id": "e4_lane_inventory_test",
            "generated_at_utc": GENERATED_AT,
            "revision": 1,
            "lanes": [
                {
                    "lane_id": "lane_alpha",
                    "config_id": "lane_alpha_v1",
                    "claim_id": "lane_alpha_v1_c4_support_claim",
                    "phase": "P-test",
                    "kind": "target_support",
                    "status": "accepted",
                    "points": 1,
                    "target_family": "breadboard",
                    "target_version": "test-target",
                    "run_id": "test-run",
                    "provider_model": "test-model",
                    "sandbox_mode": "read-only",
                    "primitives": ["bb.effective_config_graph.v1"],
                    "builder": None,
                    "comparator_id": None,
                    "ct": None,
                    "artifact_roles": {
                        "capture": "lane_alpha:capture",
                        "comparator": "lane_alpha:comparator",
                        "evidence_manifest": "lane_alpha:evidence_manifest",
                        "support_claim": "lane_alpha:support_claim",
                    },
                    "ledger_feature_ids": ["feature_alpha"],
                    "score_row_id": "score_lane_alpha",
                    "blocked_reason": None,
                    "reverify_command": None,
                }
            ],
        },
    )

    report_roles_path = docs / "e4_report_roles.json"
    if include_report_roles:
        _write_json(
            report_roles_path,
            {
                "schema_version": "bb.e4.report_roles.v1",
                "report_id": "e4_report_roles_test",
                "generated_at_utc": GENERATED_AT,
                "lane_artifact_roles": [
                    {
                        "lane_id": "lane_alpha",
                        "role_key": "capture",
                        "role_id": "lane_alpha:capture",
                    },
                    {
                        "lane_id": "lane_alpha",
                        "role_key": "comparator",
                        "role_id": "lane_alpha:comparator",
                    },
                    {
                        "lane_id": "lane_alpha",
                        "role_key": "evidence_manifest",
                        "role_id": "lane_alpha:evidence_manifest",
                    },
                    {
                        "lane_id": "lane_alpha",
                        "role_key": "support_claim",
                        "role_id": "lane_alpha:support_claim",
                    },
                ],
                "static_artifact_roles": [
                    {
                        "role_id": "e4_static:report/bb_e4_evidence_governance_json",
                        "path": f"{CHECKOUT_DIRNAME}/docs/conformance/static_prefixed_report.json",
                        "artifact_kind": "report",
                        "media_type": "application/json",
                        "derived_from": [],
                        "generated_by": "manual",
                    },
                    {
                        "role_id": "e4_static:report/bb_e4_compatibility_migration_notes_md",
                        "path": "docs_tmp/phase_16/static_plan_artifact.txt",
                        "artifact_kind": "other",
                        "media_type": "text/plain",
                        "derived_from": [],
                        "generated_by": "manual",
                    },
                    {
                        "role_id": "e4_static:script/scaffold_e4_target_lane",
                        "path": "scripts/e4_parity/scaffold_e4_target_lane.py",
                        "artifact_kind": "script",
                        "media_type": "text/x-python",
                        "derived_from": [],
                        "generated_by": "manual",
                    },
                ],
            },
        )

    return {
        "workspace": workspace,
        "checkout": checkout,
        "inventory": inventory_path,
        "report_roles": report_roles_path,
        "output": docs / "e4_artifact_catalog.json",
        "capture": capture_path,
        "comparator": comparator_path,
        "support_claim": support_claim_path,
        "evidence_manifest": evidence_manifest_path,
        "checkout_static": checkout_static_path,
        "docs_tmp_static": docs_tmp_static_path,
        "script": script_path,
    }


def _make_unlocked_binding_sync_eligible(paths: dict[str, Path]) -> Path:
    ledger_ref = "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    ledger_path = paths["workspace"] / ledger_ref
    stale_hash = "sha256:" + "0" * 64
    _write_json(
        ledger_path,
        {
            "schema_version": "bb.e4.atomic_feature_ledger_seed.v1",
            "rows": [{"feature_id": "feature_alpha", "status": "accepted", "points": 1}],
        },
    )

    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    _write_json(paths["inventory"], inventory)
    report_roles = json.loads(paths["report_roles"].read_text(encoding="utf-8"))
    report_roles["lane_artifact_roles"].append(
        {
            "lane_id": "lane_alpha",
            "role_key": "atomic_feature_ledger",
            "role_id": "lane_alpha:atomic_feature_ledger",
        }
    )
    _write_json(paths["report_roles"], report_roles)

    support_claim = json.loads(paths["support_claim"].read_text(encoding="utf-8"))
    support_claim["evidence_manifest_ref"] = _relative(paths["evidence_manifest"], paths["checkout"])
    support_claim["ledger_row_refs"] = [f"{ledger_ref}#feature_alpha#{stale_hash}"]
    _write_json(paths["support_claim"], support_claim)

    evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    support_artifact = next(
        artifact for artifact in evidence_manifest["artifacts"] if artifact["role"] == "support_claim_ref"
    )
    support_artifact["sha256"] = stale_hash
    evidence_manifest["artifacts"].append(
        {
            "role": "atomic_feature_ledger",
            "path": ledger_ref,
            "sha256": stale_hash,
            "bytes": 1,
            "exists": False,
        }
    )
    _write_json(paths["evidence_manifest"], evidence_manifest)
    return ledger_path


def test_build_catalog_is_deterministic_valid_and_covers_lane_and_static_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)

    assert builder.main(
        [
            "--inventory",
            str(paths["inventory"]),
            "--report-roles",
            str(paths["report_roles"]),
            "--output",
            str(paths["output"]),
            "--generated-at-utc",
            GENERATED_AT,
        ]
    ) == 0
    first = json.loads(paths["output"].read_text(encoding="utf-8"))
    first_bytes = paths["output"].read_bytes()
    assert builder.main(
        [
            "--inventory",
            str(paths["inventory"]),
            "--report-roles",
            str(paths["report_roles"]),
            "--output",
            str(paths["output"]),
            "--generated-at-utc",
            GENERATED_AT,
        ]
    ) == 0
    second = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert second == first
    assert paths["output"].read_bytes() == first_bytes
    assert [entry["role_id"] for entry in first["entries"]] == sorted(
        entry["role_id"] for entry in first["entries"]
    )
    role_ids = {entry["role_id"] for entry in first["entries"]}
    unresolved = [
        f"{entry['role_id']}<-{upstream}"
        for entry in first["entries"]
        for upstream in entry["derived_from"]
        if upstream not in role_ids
    ]
    assert unresolved == []

    finalized = finalize_record(get_spec(first["schema_version"]), copy.deepcopy(first))
    assert finalized == first
    assert first["integrity"] == {
        "entry_count": len(first["entries"]),
        "entries_hash": sha256_ref(canonical_record_bytes(first["entries"])),
        "stable_entries_hash": stable_entries_hash(first["entries"]),
        "segments_hash": sha256_ref(canonical_record_bytes(first["segments"])),
    }

    script_entry = _catalog_entry(first, "e4_static:script/scaffold_e4_target_lane")
    assert script_entry["artifact_kind"] == "script"
    assert script_entry["sha256"] == _sha256_file(paths["script"])
    tooling_entry = _catalog_entry(first, "e4_static:report/tooling_manifest_json")
    assert tooling_entry["artifact_kind"] == "report"
    assert tooling_entry["derived_from"] == ["e4_static:script/scaffold_e4_target_lane"]
    tooling_manifest = json.loads((paths["checkout"] / "docs" / "conformance" / "e4_tooling_manifest.json").read_text())
    assert tooling_manifest["scripts"] == [
        {
            "bytes": paths["script"].stat().st_size,
            "path": "scripts/e4_parity/scaffold_e4_target_lane.py",
            "role_id": "e4_static:script/scaffold_e4_target_lane",
            "sha256": _sha256_file(paths["script"]),
        }
    ]

    emitted_paths = [entry["path"] for entry in first["entries"]]
    assert all(not path.startswith(f"{CHECKOUT_DIRNAME}/") for path in emitted_paths)
    assert _catalog_entry(first, "e4_static:report/bb_e4_evidence_governance_json")["path"] == "docs/conformance/static_prefixed_report.json"
    assert _catalog_entry(first, "e4_static:report/bb_e4_evidence_governance_json")["sha256"] == _sha256_file(paths["checkout_static"])
    assert _catalog_entry(first, "e4_static:report/bb_e4_evidence_governance_json")["bytes"] == paths["checkout_static"].stat().st_size
    assert _catalog_entry(first, "e4_static:report/bb_e4_compatibility_migration_notes_md")["path"] == "docs_tmp/phase_16/static_plan_artifact.txt"
    assert _catalog_entry(first, "e4_static:report/bb_e4_compatibility_migration_notes_md")["sha256"] == _sha256_file(paths["docs_tmp_static"])

    assert _catalog_entry(first, "lane_alpha:capture")["lane_id"] == "lane_alpha"
    assert _catalog_entry(first, "lane_alpha:capture")["path"] == "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json"
    assert _catalog_entry(first, "lane_alpha:comparator")["path"] == (
        "docs/conformance/e4_target_support/lane_alpha/comparator_report.json"
    )
    assert _catalog_entry(first, "lane_alpha:comparator")["derived_from"] == ["lane_alpha:capture"]
    assert _catalog_entry(first, "lane_alpha:support_claim")["path"] == (
        "docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json"
    )
    assert _catalog_entry(first, "lane_alpha:evidence_manifest")["path"] == (
        "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json"
    )
    assert _catalog_entry(first, "lane_alpha:evidence_manifest")["derived_from"] == ["lane_alpha:support_claim"]

    mutated_entries = copy.deepcopy(first["entries"])
    for entry in mutated_entries:
        if entry["role_id"] == "lane_alpha:support_claim":
            entry["sha256"] = "sha256:" + "0" * 64
            break
    else:
        raise AssertionError("missing support-claim catalog entry")
    assert stable_entries_hash(mutated_entries) == first["integrity"]["stable_entries_hash"]
    assert sha256_ref(canonical_record_bytes(mutated_entries)) != first["integrity"]["entries_hash"]


def test_catalog_omits_superseded_lane_with_unavailable_historical_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["status"] = "superseded"
    inventory["lanes"][0]["evidence_status"] = "accepted"
    _write_json(paths["inventory"], inventory)
    for key in ("capture", "comparator", "support_claim", "evidence_manifest"):
        paths[key].unlink()

    _run_catalog_cli(paths)
    catalog = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert not any(entry.get("lane_id") == "lane_alpha" for entry in catalog["entries"])


def test_bootstrap_catalog_omits_derived_lane_roles_and_unreferenced_static_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    paths["checkout_static"].unlink()
    paths["docs_tmp_static"].unlink()
    paths["script"].unlink()

    catalog = builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        excluded_lane_roles={"evidence_manifest", "node_gate", "support_claim"},
        referenced_static_only=True,
    )

    assert {entry["role_id"] for entry in catalog["entries"]} == {
        "lane_alpha:capture",
        "lane_alpha:comparator",
    }
    assert _catalog_entry(catalog, "lane_alpha:comparator")["derived_from"] == [
        "lane_alpha:capture"
    ]
    assert not (paths["checkout"] / "docs/conformance/e4_tooling_manifest.json").exists()
def test_bootstrap_catalog_prunes_lane_roles_derived_from_excluded_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)

    catalog = builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        excluded_lane_roles={"capture", "evidence_manifest", "node_gate", "support_claim"},
        referenced_static_only=True,
    )

    assert catalog["entries"] == []


def test_bootstrap_catalog_keeps_default_ledger_needed_by_support_claim_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    ledger = paths["workspace"] / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    _write_json(ledger, {"rows": [{"feature_id": "feature_alpha"}]})
    report_roles = json.loads(paths["report_roles"].read_text(encoding="utf-8"))
    report_roles["static_artifact_roles"].append(
        {
            "role_id": builder.DEFAULT_ATOMIC_LEDGER_ROLE_ID,
            "path": "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
            "artifact_kind": "ledger",
            "media_type": "application/json",
            "derived_from": [],
            "generated_by": "scripts/e4_parity/seed_atomic_feature_ledger.py",
        }
    )
    _write_json(paths["report_roles"], report_roles)

    catalog = builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        excluded_lane_roles={"evidence_manifest", "node_gate", "support_claim"},
        referenced_static_only=True,
    )

    assert builder.DEFAULT_ATOMIC_LEDGER_ROLE_ID in {
        entry["role_id"] for entry in catalog["entries"]
    }


def test_static_script_entries_are_derived_and_recorded_in_tooling_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _run_catalog_cli(paths)
    before = json.loads(paths["output"].read_text(encoding="utf-8"))
    before_stable = before["integrity"]["stable_entries_hash"]
    before_revision = before["revision"]

    _write_text(paths["script"], "# scaffold helper\n# comment-only tooling edit\n")
    _run_catalog_cli(paths, generated_at_utc="2026-07-04T00:00:00Z")
    after = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert after["revision"] == before_revision
    assert after["integrity"]["stable_entries_hash"] == before_stable
    assert _catalog_entry(after, "e4_static:script/scaffold_e4_target_lane")["sha256"] == _sha256_file(paths["script"])
    assert _catalog_entry(after, "e4_static:report/tooling_manifest_json")["sha256"] != _catalog_entry(
        before, "e4_static:report/tooling_manifest_json"
    )["sha256"]


def test_support_claim_hash_binding_sync_is_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    sync_inventory_paths: list[Path] = []

    def recording_sync(*, inventory_path: Path | str) -> dict[str, int]:
        sync_inventory_paths.append(Path(inventory_path))
        return {
            "support_claims_checked": 0,
            "support_claims_changed": 0,
            "evidence_manifests_changed": 0,
            "node_gates_changed": 0,
        }

    monkeypatch.setattr(builder, "_sync_support_claim_hash_bindings", recording_sync)
    builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        generated_at_utc=GENERATED_AT,
    )
    assert sync_inventory_paths == []

    builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        generated_at_utc=GENERATED_AT,
        write_bindings=True,
    )
    assert sync_inventory_paths == [paths["inventory"]]


def test_write_bindings_refreshes_node_gate_hashes_after_evidence_manifest_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    ledger_ref = "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    ledger_path = paths["workspace"] / ledger_ref
    node_gate_ref = "artifacts/conformance/node_gate/ct_lane_alpha_c4_chain.json"
    node_gate_path = paths["checkout"] / node_gate_ref
    stale_hash = "sha256:" + "0" * 64

    _write_json(
        ledger_path,
        {
            "schema_version": "bb.e4.atomic_feature_ledger_seed.v1",
            "rows": [
                {
                    "feature_id": "feature_alpha",
                    "status": "accepted",
                    "points": 1,
                }
            ],
        },
    )

    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["ct"] = {
        "command": {
            "argv": [
                "python",
                "scripts/validate_e4_c4_chain.py",
                "lane_alpha_v1",
                "--json-out",
                node_gate_ref,
            ],
            "cwd": ".",
        }
    }
    _write_json(paths["inventory"], inventory)
    inventory["lanes"][0]["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    _write_json(paths["inventory"], inventory)
    report_roles = json.loads(paths["report_roles"].read_text(encoding="utf-8"))
    report_roles["lane_artifact_roles"].append(
        {
            "lane_id": "lane_alpha",
            "role_key": "atomic_feature_ledger",
            "role_id": "lane_alpha:atomic_feature_ledger",
        }
    )
    _write_json(paths["report_roles"], report_roles)

    support_claim = json.loads(paths["support_claim"].read_text(encoding="utf-8"))
    support_claim["evidence_manifest_ref"] = "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json"
    support_claim["ledger_row_refs"] = [f"{ledger_ref}#feature_alpha#{stale_hash}"]
    _write_json(paths["support_claim"], support_claim)

    evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    for artifact in evidence_manifest["artifacts"]:
        if artifact["role"] == "support_claim_ref":
            artifact["sha256"] = stale_hash
            break
    else:
        raise AssertionError("fixture missing support_claim_ref artifact")
    evidence_manifest["artifacts"].append(
        {
            "role": "atomic_feature_ledger",
            "path": ledger_ref,
            "sha256": stale_hash,
            "bytes": 1,
            "exists": False,
        }
    )
    _write_json(paths["evidence_manifest"], evidence_manifest)

    original_node_gate_top_level_paths = {
        "support_claim": "legacy/support_claim.json",
        "evidence_manifest": "legacy/evidence_manifest.json",
    }
    original_node_gate_refs = {
        "support_claim": "docs/conformance/support_claims/lane_alpha_v1_c4_support_claim.json",
        "evidence_manifest": "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json",
    }

    node_gate = {
        "schema_version": "bb.e4.c4_chain_validation_report.v1",
        "ok": True,
        "accepted": True,
        **original_node_gate_top_level_paths,
        "refs": original_node_gate_refs,
        "hashes": {
            "support_claim": stale_hash,
            "evidence_manifest": stale_hash,
        },
        "errors": [],
    }
    _write_json(node_gate_path, {**node_gate, "hashes": []})
    binding_bytes_before_invalid_gate = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
    }
    with pytest.raises(ValueError, match="hashes must be an object"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])
    assert {
        path: path.read_bytes() for path in binding_bytes_before_invalid_gate
    } == binding_bytes_before_invalid_gate
    _write_json(node_gate_path, node_gate)

    builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        generated_at_utc=GENERATED_AT,
        write_bindings=True,
    )

    synced_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    artifacts_by_role = {artifact["role"]: artifact for artifact in synced_manifest["artifacts"]}
    assert artifacts_by_role["support_claim_ref"]["sha256"] == _sha256_file(paths["support_claim"])
    assert artifacts_by_role["atomic_feature_ledger"]["sha256"] == _sha256_file(ledger_path)
    assert artifacts_by_role["atomic_feature_ledger"]["bytes"] == ledger_path.stat().st_size
    assert artifacts_by_role["atomic_feature_ledger"]["exists"] is True

    synced_node_gate = json.loads(node_gate_path.read_text(encoding="utf-8"))
    assert synced_node_gate["hashes"]["support_claim"] == _sha256_file(paths["support_claim"])
    assert synced_node_gate["hashes"]["evidence_manifest"] == _sha256_file(paths["evidence_manifest"])
    assert {
        key: synced_node_gate[key] for key in original_node_gate_top_level_paths
    } == original_node_gate_top_level_paths
    assert synced_node_gate["refs"] == original_node_gate_refs


def test_write_bindings_uses_lane_declared_tracked_ledger_instead_of_ambient_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    ambient_ref = "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    tracked_ref = "config/e4_lanes/evidence_inputs/oh_my_pi_p6_6_atomic_feature_ledger.v1.json"
    ambient_path = paths["workspace"] / ambient_ref
    tracked_path = paths["checkout"] / tracked_ref
    stale_hash = "sha256:" + "0" * 64

    _write_json(
        ambient_path,
        {
            "schema_version": "bb.e4.atomic_feature_ledger_seed.v1",
            "rows": [{"feature_id": "ambient_only", "status": "accepted", "points": 99}],
        },
    )
    tracked_row = {"feature_id": "feature_alpha", "status": "accepted", "points": 1}
    _write_json(
        tracked_path,
        {
            "schema_version": "bb.e4.atomic_feature_ledger_seed.v1",
            "rows": [tracked_row],
        },
    )
    lane_lock_path = paths["checkout"] / "config" / "e4_lanes" / "lane_alpha.lock.json"
    _write_json(
        lane_lock_path,
        {
            "lane_id": "lane_alpha",
            "artifact_roles": {
                "atomic_feature_ledger": {
                    "path": tracked_ref,
                }
            },
            "resolved_inputs": [
                {
                    "path": tracked_ref,
                    "sha256": _sha256_file(tracked_path),
                    "bytes": tracked_path.stat().st_size,
                }
            ],
        },
    )

    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    _write_json(paths["inventory"], inventory)

    support_claim = json.loads(paths["support_claim"].read_text(encoding="utf-8"))
    support_claim["evidence_manifest_ref"] = _relative(paths["evidence_manifest"], paths["checkout"])
    support_claim["ledger_row_refs"] = [f"{tracked_ref}#feature_alpha#{stale_hash}"]
    _write_json(paths["support_claim"], support_claim)

    evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    evidence_manifest["artifacts"].append(
        {
            "role": "atomic_feature_ledger",
            "path": tracked_ref,
            "sha256": _sha256_file(ambient_path),
            "bytes": ambient_path.stat().st_size,
            "exists": True,
        }
    )
    _write_json(paths["evidence_manifest"], evidence_manifest)

    result = builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])

    assert result["support_claims_checked"] == 1
    synced_claim = json.loads(paths["support_claim"].read_text(encoding="utf-8"))
    expected_row_hash = builder._row_content_hash("feature_alpha", tracked_row)
    assert synced_claim["ledger_row_refs"] == [f"{tracked_ref}#feature_alpha#{expected_row_hash}"]

    synced_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    ledger_artifact = next(
        artifact for artifact in synced_manifest["artifacts"] if artifact["role"] == "atomic_feature_ledger"
    )
    assert ledger_artifact["path"] == tracked_ref
    assert ledger_artifact["sha256"] == _sha256_file(tracked_path)
    assert ledger_artifact["bytes"] == tracked_path.stat().st_size
    assert ledger_artifact["sha256"] != _sha256_file(ambient_path)
    synced_claim_bytes = paths["support_claim"].read_bytes()
    synced_manifest_bytes = paths["evidence_manifest"].read_bytes()
    second_result = builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])
    assert second_result == {
        "support_claims_checked": 1,
        "support_claims_changed": 0,
        "evidence_manifests_changed": 0,
        "node_gates_changed": 0,
        "accepted_p0_lanes_skipped_missing_lock": 0,
    }
    assert paths["support_claim"].read_bytes() == synced_claim_bytes
    assert paths["evidence_manifest"].read_bytes() == synced_manifest_bytes
    lane_lock = json.loads(lane_lock_path.read_text(encoding="utf-8"))
    lane_lock["resolved_inputs"][0]["sha256"] = stale_hash
    _write_json(lane_lock_path, lane_lock)
    with pytest.raises(ValueError, match="atomic_feature_ledger sha256 is inconsistent"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])
    lane_lock["resolved_inputs"][0]["sha256"] = _sha256_file(tracked_path)
    lane_lock["resolved_inputs"][0]["bytes"] = 0
    _write_json(lane_lock_path, lane_lock)
    with pytest.raises(ValueError, match="atomic_feature_ledger bytes are inconsistent"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])

    lane_lock["resolved_inputs"][0]["bytes"] = tracked_path.stat().st_size
    lane_lock["lane_id"] = "wrong_lane"
    _write_json(lane_lock_path, lane_lock)
    with pytest.raises(ValueError, match="lane_id does not match"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])

    lane_lock["lane_id"] = "lane_alpha"
    lane_lock["artifact_roles"]["atomic_feature_ledger"]["path"] = "../escaping-ledger.json"
    _write_json(lane_lock_path, lane_lock)
    with pytest.raises(ValueError, match="escapes its allowed root"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])


    _write_json(
        lane_lock_path,
        {
            "lane_id": "lane_alpha",
            "artifact_roles": {
                "atomic_feature_ledger": {
                    "path": ambient_ref,
                }
            }
        },
    )
    with pytest.raises(ValueError, match="does not match its lane lock"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])


def test_binding_sync_rejects_support_and_evidence_path_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    lane = inventory["lanes"][0]
    lane["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    lane["claim_id"] = "lane_alpha_v1_c4_evidence_manifest"
    _write_json(paths["inventory"], inventory)

    with pytest.raises(ValueError, match="ambiguous binding output path"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])


def test_binding_sync_rejects_manifest_lane_mismatch_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    _write_json(paths["inventory"], inventory)
    evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    evidence_manifest["lane_id"] = "wrong_lane"
    _write_json(paths["evidence_manifest"], evidence_manifest)
    bindings_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
    }

    with pytest.raises(ValueError, match="evidence manifest lane_id binding is inconsistent"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])
    assert {path: path.read_bytes() for path in bindings_before} == bindings_before


@pytest.mark.parametrize(
    ("node_gate_ref", "error_match"),
    [
        ("/tmp/outside-node-gate.json", "must be repository/workspace-relative"),
        ("../outside-node-gate.json", "escapes its allowed root"),
    ],
)
def test_binding_sync_rejects_unsafe_node_gate_path_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    node_gate_ref: str,
    error_match: str,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    lane = inventory["lanes"][0]
    lane["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    lane["ct"] = {"command": {"argv": ["python", "--json-out", node_gate_ref]}}
    _write_json(paths["inventory"], inventory)
    bindings_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
    }

    with pytest.raises(ValueError, match=error_match):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])
    assert {path: path.read_bytes() for path in bindings_before} == bindings_before


def test_binding_sync_rejects_node_gate_alias_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    lane = inventory["lanes"][0]
    lane["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    lane["ct"] = {
        "command": {
            "argv": [
                "python",
                "scripts/validate_e4_c4_chain.py",
                "--json-out",
                _relative(paths["support_claim"], paths["checkout"]),
            ]
        }
    }
    _write_json(paths["inventory"], inventory)
    bindings_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
    }

    with pytest.raises(ValueError, match="ambiguous binding output path"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])
    assert {path: path.read_bytes() for path in bindings_before} == bindings_before


def test_binding_sync_rejects_shared_node_gate_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    shared_node_gate_ref = "artifacts/conformance/node_gate/shared.json"
    first_lane = inventory["lanes"][0]
    first_lane["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    first_lane["ct"] = {"command": {"argv": ["python", "--json-out", shared_node_gate_ref]}}
    second_lane = copy.deepcopy(first_lane)
    second_lane["lane_id"] = "lane_beta"
    second_lane["config_id"] = "lane_beta_v1"
    second_lane["claim_id"] = "lane_beta_v1_c4_support_claim"
    second_lane["artifact_roles"] = {
        role_key: role_id.replace("lane_alpha:", "lane_beta:")
        for role_key, role_id in second_lane["artifact_roles"].items()
    }
    inventory["lanes"].append(second_lane)
    _write_json(paths["inventory"], inventory)
    bindings_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
    }

    with pytest.raises(ValueError, match="ambiguous binding output path"):
        builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])
    assert {path: path.read_bytes() for path in bindings_before} == bindings_before


def test_binding_path_resolution_rejects_repository_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_catalog_fixture(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="escapes its allowed root"):
        builder._sync_artifact_path("../outside-ledger.json", role="atomic_feature_ledger")
    with pytest.raises(ValueError, match="must be repository/workspace-relative"):
        builder._sync_artifact_path(str(tmp_path / "absolute-ledger.json"), role="atomic_feature_ledger")


def test_binding_sync_rejects_traversal_lane_lock_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    ledger_path = _make_unlocked_binding_sync_eligible(paths)
    ledger_ref = _relative(ledger_path, paths["workspace"])
    malicious_lane_id = "lane/../../../../foreign"
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["lane_id"] = malicious_lane_id
    _write_json(paths["inventory"], inventory)
    report_roles = json.loads(paths["report_roles"].read_text(encoding="utf-8"))
    for role in report_roles["lane_artifact_roles"]:
        role["lane_id"] = malicious_lane_id
    _write_json(paths["report_roles"], report_roles)
    evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    evidence_manifest["lane_id"] = malicious_lane_id
    _write_json(paths["evidence_manifest"], evidence_manifest)
    escaped_lock = (
        paths["checkout"] / "config" / "e4_lanes" / f"{malicious_lane_id}.lock.json"
    ).resolve()
    _write_json(
        escaped_lock,
        {
            "lane_id": malicious_lane_id,
            "artifact_roles": {"atomic_feature_ledger": {"path": ledger_ref}},
            "resolved_inputs": [
                {
                    "path": ledger_ref,
                    "sha256": _sha256_file(ledger_path),
                    "bytes": ledger_path.stat().st_size,
                }
            ],
        },
    )
    _write_text(paths["output"], "existing catalog sentinel\n")
    files_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
        escaped_lock: escaped_lock.read_bytes(),
        paths["output"]: paths["output"].read_bytes(),
    }

    with pytest.raises(ValueError, match="filename-safe"):
        builder.main(
            [
                "--inventory",
                str(paths["inventory"]),
                "--report-roles",
                str(paths["report_roles"]),
                "--output",
                str(paths["output"]),
                "--generated-at-utc",
                GENERATED_AT,
                "--write-bindings",
            ]
        )

    assert {path: path.read_bytes() for path in files_before} == files_before


def test_binding_sync_rejects_symlinked_lane_lock_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    ledger_path = _make_unlocked_binding_sync_eligible(paths)
    ledger_ref = _relative(ledger_path, paths["workspace"])
    outside_lock = tmp_path / "foreign-lane-lock.json"
    _write_json(
        outside_lock,
        {
            "lane_id": "lane_alpha",
            "artifact_roles": {"atomic_feature_ledger": {"path": ledger_ref}},
            "resolved_inputs": [
                {
                    "path": ledger_ref,
                    "sha256": _sha256_file(ledger_path),
                    "bytes": ledger_path.stat().st_size,
                }
            ],
        },
    )
    lock_path = paths["checkout"] / "config" / "e4_lanes" / "lane_alpha.lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_path.symlink_to(outside_lock)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    _write_text(paths["output"], "existing catalog sentinel\n")
    files_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
        outside_lock: outside_lock.read_bytes(),
        paths["output"]: paths["output"].read_bytes(),
    }

    with pytest.raises(ValueError, match="escapes the lane-lock directory"):
        builder.main(
            [
                "--inventory",
                str(paths["inventory"]),
                "--report-roles",
                str(paths["report_roles"]),
                "--output",
                str(paths["output"]),
                "--generated-at-utc",
                GENERATED_AT,
                "--write-bindings",
            ]
        )

    assert {path: path.read_bytes() for path in files_before} == files_before


def test_p0_ambient_ledger_without_tracked_lane_authority_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_catalog_fixture(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="has no tracked lane-lock authority"):
        builder._declared_ledger_ref(
            {"lane_id": "north_star_lane", "phase": "P0"},
            "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
        )


def test_write_bindings_skips_accepted_p0_legacy_lane_without_lock_or_binding_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _make_unlocked_binding_sync_eligible(paths)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["phase"] = "P0"
    inventory["lanes"][0]["status"] = "accepted"
    _write_json(paths["inventory"], inventory)
    bindings_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
    }

    result = builder._sync_support_claim_hash_bindings(inventory_path=paths["inventory"])

    assert result == {
        "support_claims_checked": 0,
        "support_claims_changed": 0,
        "evidence_manifests_changed": 0,
        "node_gates_changed": 0,
        "accepted_p0_lanes_skipped_missing_lock": 1,
    }
    assert {path: path.read_bytes() for path in bindings_before} == bindings_before
    assert builder.main(
        [
            "--inventory",
            str(paths["inventory"]),
            "--report-roles",
            str(paths["report_roles"]),
            "--output",
            str(paths["output"]),
            "--generated-at-utc",
            GENERATED_AT,
            "--write-bindings",
        ]
    ) == 0
    assert paths["output"].is_file()
    assert {path: path.read_bytes() for path in bindings_before} == bindings_before


def test_duplicate_inventory_artifact_role_ids_fail_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _make_unlocked_binding_sync_eligible(paths)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["artifact_roles"]["comparator"] = "lane_alpha:capture"
    _write_json(paths["inventory"], inventory)
    report_roles = json.loads(paths["report_roles"].read_text(encoding="utf-8"))
    comparator_role = next(
        item for item in report_roles["lane_artifact_roles"] if item["role_key"] == "comparator"
    )
    comparator_role["role_id"] = "lane_alpha:capture"
    _write_json(paths["report_roles"], report_roles)
    _write_text(paths["output"], "existing catalog sentinel\n")
    files_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
        paths["output"]: paths["output"].read_bytes(),
    }

    with pytest.raises(ValueError, match="duplicate inventory artifact role_id"):
        builder.main(
            [
                "--inventory",
                str(paths["inventory"]),
                "--report-roles",
                str(paths["report_roles"]),
                "--output",
                str(paths["output"]),
                "--generated-at-utc",
                GENERATED_AT,
                "--write-bindings",
            ]
        )

    assert {path: path.read_bytes() for path in files_before} == files_before


@pytest.mark.parametrize("identifier_field", ["claim_id", "config_id"])
def test_inventory_claim_paths_cannot_escape_support_claims_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identifier_field: str,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _make_unlocked_binding_sync_eligible(paths)
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    malicious_id = f"../../escaped_{identifier_field}"
    inventory["lanes"][0][identifier_field] = malicious_id

    if identifier_field == "claim_id":
        escaped_path = paths["checkout"] / "docs" / "escaped_claim_id.json"
        support_claim = json.loads(paths["support_claim"].read_text(encoding="utf-8"))
        support_claim["claim_id"] = malicious_id
        _write_json(escaped_path, support_claim)
        evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
        evidence_manifest["claim_id"] = malicious_id
        support_artifact = next(
            artifact for artifact in evidence_manifest["artifacts"] if artifact["role"] == "support_claim_ref"
        )
        support_artifact["path"] = _relative(escaped_path, paths["checkout"])
        _write_json(paths["evidence_manifest"], evidence_manifest)
    else:
        escaped_path = paths["checkout"] / "docs" / "escaped_config_id_c4_evidence_manifest.json"
        evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
        evidence_manifest["config_id"] = malicious_id
        _write_json(escaped_path, evidence_manifest)
        support_claim = json.loads(paths["support_claim"].read_text(encoding="utf-8"))
        support_claim["evidence_manifest_ref"] = _relative(escaped_path, paths["checkout"])
        _write_json(paths["support_claim"], support_claim)

    _write_json(paths["inventory"], inventory)
    _write_text(paths["output"], "existing catalog sentinel\n")
    files_before = {
        paths["support_claim"]: paths["support_claim"].read_bytes(),
        paths["evidence_manifest"]: paths["evidence_manifest"].read_bytes(),
        escaped_path: escaped_path.read_bytes(),
        paths["output"]: paths["output"].read_bytes(),
    }

    with pytest.raises(ValueError, match="filename-safe"):
        builder.main(
            [
                "--inventory",
                str(paths["inventory"]),
                "--report-roles",
                str(paths["report_roles"]),
                "--output",
                str(paths["output"]),
                "--generated-at-utc",
                GENERATED_AT,
                "--write-bindings",
            ]
        )

    assert {path: path.read_bytes() for path in files_before} == files_before


def test_build_catalog_keeps_revision_for_derived_only_existing_entry_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _run_catalog_cli(paths)
    existing = json.loads(paths["output"].read_text(encoding="utf-8"))
    existing["revision"] = 7
    _catalog_entry(existing, "lane_alpha:support_claim")["sha256"] = "sha256:" + "0" * 64
    assert stable_entries_hash(existing["entries"]) == existing["integrity"]["stable_entries_hash"]
    assert sha256_ref(canonical_record_bytes(existing["entries"])) != existing["integrity"]["entries_hash"]
    _write_json(paths["output"], existing)

    _run_catalog_cli(paths, generated_at_utc="2026-07-04T00:00:00Z")
    rebuilt = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert rebuilt["revision"] == 7
    assert _catalog_entry(rebuilt, "lane_alpha:support_claim")["sha256"] == _sha256_file(paths["support_claim"])
    assert rebuilt["generated_at_utc"] == "2026-07-04T00:00:00Z"


def test_build_catalog_increments_revision_once_for_stable_existing_entry_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _run_catalog_cli(paths)
    existing = json.loads(paths["output"].read_text(encoding="utf-8"))
    existing_stable_hash = existing["integrity"]["stable_entries_hash"]
    existing["revision"] = 7
    _catalog_entry(existing, "lane_alpha:capture")["sha256"] = "sha256:" + "1" * 64
    assert stable_entries_hash(existing["entries"]) != existing_stable_hash
    _write_json(paths["output"], existing)

    _run_catalog_cli(paths, generated_at_utc="2026-07-04T00:00:00Z")
    rebuilt = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert rebuilt["revision"] == 8
    assert _catalog_entry(rebuilt, "lane_alpha:capture")["sha256"] == _sha256_file(paths["capture"])
    _run_catalog_cli(paths, generated_at_utc="2026-07-05T00:00:00Z")
    second_rebuild = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert second_rebuild["revision"] == 8


def test_build_catalog_claim_entry_churn_reaches_revision_fixed_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    _run_catalog_cli(paths)
    before = json.loads(paths["output"].read_text(encoding="utf-8"))
    _write_json(paths["support_claim"], {"accepted": True, "claim_id": "lane_alpha_v1_c4_support_claim", "rev": 2})

    _run_catalog_cli(paths, generated_at_utc="2026-07-04T00:00:00Z")
    after_first_churn = json.loads(paths["output"].read_text(encoding="utf-8"))
    _run_catalog_cli(paths, generated_at_utc="2026-07-05T00:00:00Z")
    after_second_churn = json.loads(paths["output"].read_text(encoding="utf-8"))

    assert after_first_churn["revision"] == before["revision"]
    assert after_second_churn["revision"] == after_first_churn["revision"]
    assert after_first_churn["integrity"]["stable_entries_hash"] == before["integrity"]["stable_entries_hash"]
    assert after_second_churn["integrity"]["stable_entries_hash"] == after_first_churn["integrity"]["stable_entries_hash"]
    assert _catalog_entry(after_first_churn, "lane_alpha:support_claim")["sha256"] != _catalog_entry(
        before, "lane_alpha:support_claim"
    )["sha256"]


def test_build_catalog_rejects_unregistered_derived_from_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    unregistered_source = paths["checkout"] / "docs" / "conformance" / "support_claims" / "unregistered_source.json"
    _write_json(unregistered_source, {"registered": False})
    manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        if artifact["role"] == "comparator_ref":
            artifact["derived_from"] = [_relative(unregistered_source, paths["checkout"])]
            break
    else:
        raise AssertionError("fixture missing comparator_ref artifact")
    _write_json(paths["evidence_manifest"], manifest)

    with pytest.raises(ValueError, match="catalog derived_from values must be role_ids"):
        builder.build_catalog(
            inventory_path=paths["inventory"],
            report_roles_path=paths["report_roles"],
            output_path=paths["output"],
            generated_at_utc=GENERATED_AT,
        )

    assert not paths["output"].exists()

def test_build_catalog_requires_report_role_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch, include_report_roles=False)

    with pytest.raises(FileNotFoundError):
        builder.build_catalog(
            inventory_path=paths["inventory"],
            report_roles_path=paths["report_roles"],
            output_path=paths["output"],
            generated_at_utc=GENERATED_AT,
        )

    assert not paths["output"].exists()


def test_build_catalog_validates_with_finalize_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)
    calls: list[tuple[str, bool]] = []
    real_finalize_record = builder.finalize_record

    def recording_finalize_record(spec: Any, record: Any, *, validate: bool = True) -> dict[str, Any]:
        calls.append((spec.schema_version, validate))
        return real_finalize_record(spec, record, validate=validate)

    monkeypatch.setattr(builder, "finalize_record", recording_finalize_record)

    builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        generated_at_utc=GENERATED_AT,
    )

    assert ("bb.e4.artifact_catalog.v2", True) in calls



def test_build_catalog_defaults_to_v2_and_rejects_v1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_catalog_fixture(tmp_path, monkeypatch)

    v2 = builder.build_catalog(
        inventory_path=paths["inventory"],
        report_roles_path=paths["report_roles"],
        output_path=paths["output"],
        generated_at_utc=GENERATED_AT,
    )

    with pytest.raises(
        ValueError,
        match=r"artifact catalog generation requires bb\.e4\.artifact_catalog\.v2, got 'v1'",
    ):
        builder.build_catalog(
            inventory_path=paths["inventory"],
            report_roles_path=paths["report_roles"],
            output_path=paths["output"],
            generated_at_utc=GENERATED_AT,
            schema_version="v1",
        )

    assert v2["schema_version"] == "bb.e4.artifact_catalog.v2"
    assert finalize_record(get_spec("bb.e4.artifact_catalog.v2"), copy.deepcopy(v2)) == v2
    assert v2["segments"] == catalog_segments(v2["entries"])
    assert v2["integrity"]["segments_hash"] == sha256_ref(canonical_record_bytes(v2["segments"]))
    assert {segment["segment_id"] for segment in v2["segments"]} == {"lane_alpha", "shared"}


def test_catalog_v2_segments_isolate_synthetic_lane_hashes() -> None:
    alpha_capture = {
        "role_id": "lane_alpha:capture",
        "path": "docs/conformance/e4_target_support/lane_alpha/raw_capture_manifest.json",
        "sha256": "sha256:" + "1" * 64,
        "bytes": 1,
        "exists": True,
        "artifact_kind": "capture",
        "lane_id": "lane_alpha",
        "media_type": "application/json",
        "derived_from": [],
        "generated_by": "manual",
    }
    beta_capture = {
        **alpha_capture,
        "role_id": "lane_beta:capture",
        "path": "docs/conformance/e4_target_support/lane_beta/raw_capture_manifest.json",
        "sha256": "sha256:" + "2" * 64,
        "lane_id": "lane_beta",
    }
    shared_schema = {
        **alpha_capture,
        "role_id": "e4_static:config/e4_lane_inventory",
        "path": "docs/conformance/e4_lane_inventory.json",
        "sha256": "sha256:" + "3" * 64,
        "artifact_kind": "config",
        "lane_id": None,
    }
    before = builder._catalog_record(
        schema_version="bb.e4.artifact_catalog.v2",
        generated_at_utc=GENERATED_AT,
        revision=1,
        entries=[alpha_capture, beta_capture, shared_schema],
    )
    after = builder._catalog_record(
        schema_version="bb.e4.artifact_catalog.v2",
        generated_at_utc=GENERATED_AT,
        revision=1,
        entries=[alpha_capture, {**beta_capture, "sha256": "sha256:" + "4" * 64}, shared_schema],
    )

    before_by_id = {segment["segment_id"]: segment for segment in before["segments"]}
    after_by_id = {segment["segment_id"]: segment for segment in after["segments"]}
    assert before_by_id["lane_alpha"] == after_by_id["lane_alpha"]
    assert before_by_id["shared"] == after_by_id["shared"]
    assert before_by_id["lane_beta"]["entries_hash"] != after_by_id["lane_beta"]["entries_hash"]
    assert before_by_id["lane_beta"]["stable_entries_hash"] != after_by_id["lane_beta"]["stable_entries_hash"]