from __future__ import annotations

import base64
import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.rl_phase5 import build_phase5_v2_freeze_pack as contract_builder
from scripts.rl_phase5 import validate_phase5_v2_freeze_pack as freeze_validator
from scripts.rl_phase5 import validate_phase5_v2_rc4_safety as safety_validator
from scripts.rl_phase5 import probe_phase5_v2_rc4_runtime as runtime_probe


RC5_ARTIFACT_MANIFEST_SHA256 = (
    "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
)
RC4_ARTIFACT_MANIFEST_SHA256 = (
    "sha256:b5897c0465bfb0cdf4b3aa79427c55e85b8a1d0b600c40e6d6eb62b579e9cbfd"
)
RC3_ARTIFACT_MANIFEST_SHA256 = (
    "sha256:57144dd1e87369cc5d0e70065846ec4b2acddcbe9020ca84ed49f84b51117d19"
)
SEALED_ARCHIVE_MANIFEST_SHA256 = (
    "sha256:91519465cfc7a45d8a6375a23908753f48bf61f2d3e90f7734f20affee2ca2d8"
)
STORE_ORDER = ["v2_event_log", "beads_projection", "root_active_selector"]


ContractBundle = dict[str, dict[str, Any]]
ContractMutation = Callable[[ContractBundle], None]
def test_load_beads_uses_only_the_frozen_assurance_child_set(
    tmp_path: Path,
) -> None:
    issues = [
        {
            "dependencies": [
                {"depends_on_id": "bb-auh", "type": "parent-child"}
            ],
            "id": f"bb-auh.{index}",
            "status": "open",
        }
        for index in range(1, 70)
    ]
    issues.extend(
        {"dependencies": [], "id": f"bb-6d4.{index}", "status": "closed"}
        for index in range(1, 9)
    )
    export_path = tmp_path / "beads.jsonl"
    export_path.write_text(
        "".join(json.dumps(issue, sort_keys=True) + "\n" for issue in issues)
    )

    children, decisions = contract_builder.load_beads(export_path)

    assert [issue["id"] for issue in children] == [
        f"bb-auh.{index}" for index in range(1, 68)
    ]
    assert [issue["id"] for issue in decisions] == [
        f"bb-6d4.{index}" for index in range(1, 9)
    ]




def _contract_bundle() -> ContractBundle:
    return {
        "manifest": {
            "revision_id": contract_builder.REVISION_ID,
            "superseded_artifact_manifest_sha256": RC4_ARTIFACT_MANIFEST_SHA256,
            "superseded_revision_id": contract_builder.SUPERSEDED_REVISION_ID,
            "supersession_scope": (
                "migration and cutover mechanics only; prior rc4 SPEC_FREEZE "
                "grants no rc5 authority"
            ),
        },
        "migration": contract_builder.migration_plan(835),
        "transaction": contract_builder.migration_transaction(),
        "quiescence": contract_builder.quiescence_contract(),
        "session_handoff": contract_builder.session_handoff_contract(),
        "migration_replay": contract_builder.migration_replay_contract(),
        "handoff": contract_builder.fresh_worker_handoff_contract(),
    }


def _validate_contract_bundle(bundle: ContractBundle) -> set[str]:
    return freeze_validator.validate_candidate_migration_contracts(
        bundle["manifest"],
        bundle["migration"],
        bundle["transaction"],
        bundle["quiescence"],
        bundle["session_handoff"],
        bundle["migration_replay"],
        bundle["handoff"],
        RC4_ARTIFACT_MANIFEST_SHA256,
    )


def _add_session_store(bundle: ContractBundle) -> None:
    bundle["transaction"]["stores"].append(
        {"id": "session_queue_and_todos", "reversible": True}
    )


def _claim_migration_in_progress(bundle: ContractBundle) -> None:
    bundle["quiescence"]["client_behavior"]["domain_error_claimed"] = True


def _commit_selector_before_beads(bundle: ContractBundle) -> None:
    bundle["transaction"]["commit_order"] = [
        "v2_event_log",
        "root_active_selector",
        "beads_projection",
    ]


def _replace_continuous_lock_with_creation_lock(
    bundle: ContractBundle,
) -> None:
    locking = bundle["transaction"]["locking"]
    locking["rule"] = locking["rule"].replace(
        "stable-inode held-flock lease",
        "one-time lock-file creation",
    )


def _remove_durable_journal(bundle: ContractBundle) -> None:
    locking = bundle["transaction"]["locking"]
    locking["journal"] = locking["journal"].replace(
        "durable append-only per-step",
        "best-effort in-memory",
    )


def _accept_forced_kill_as_success(bundle: ContractBundle) -> None:
    rules = bundle["quiescence"]["receipt_contract"]["flush_outcome_rules"]
    rules["success_only"]["allowed"].append(
        "forced_or_timeout_kill_without_flush"
    )


def _claim_release_fact_in_intent(bundle: ContractBundle) -> None:
    receipt = bundle["quiescence"]["receipt_contract"][
        "release_receipt_contracts"
    ]["release_intent_receipt"]
    receipt["required_fields"].append("flock_released_at")


def _fabricate_absent_event_bytes(bundle: ContractBundle) -> None:
    event_store = bundle["transaction"]["stores"][0]
    event_store["before_state"]["absent"] = event_store["before_state"][
        "absent"
    ].replace("bytes_sha256=null", "bytes_sha256=sha256:empty-file")


def _reuse_rc4_authority(bundle: ContractBundle) -> None:
    bundle["migration"]["superseded_rc4"][
        "spec_freeze_grants_rc5_authority"
    ] = True


def _permit_unknown_adapter(bundle: ContractBundle) -> None:
    bundle["quiescence"]["adapter_discovery"][
        "allowed_dolt_adapters"
    ].append("unknown")


def _resume_prior_session(bundle: ContractBundle) -> None:
    invariants = bundle["session_handoff"]["post_handoff_receipt"][
        "invariants"
    ]
    invariants.remove("new_session_id differs from prior_session_id")


def _grant_target_authority(bundle: ContractBundle) -> None:
    bundle["handoff"]["derivation"]["target_execution_allowed"] = True


def _drop_journal_replay_input(bundle: ContractBundle) -> None:
    bundle["migration_replay"]["allowed_inputs"].remove(
        "MIGRATION_JOURNAL.jsonl"
    )


def _permit_future_replay_receipt(bundle: ContractBundle) -> None:
    bundle["migration_replay"]["allowed_inputs"].append(
        "MIGRATION_TRANSACTION_RECEIPT.json"
    )


def _emit_transaction_receipt_before_replay(bundle: ContractBundle) -> None:
    order = bundle["transaction"]["receipt_production_order"]
    order.insert(0, order.pop())


def test_migration_replay_receipt_graph_is_acyclic() -> None:
    bundle = _contract_bundle()

    replay_inputs = set(bundle["migration_replay"]["allowed_inputs"])
    assert replay_inputs.isdisjoint(
        {
            "MIGRATION_TRANSACTION_RECEIPT.json",
            "QUIESCENCE_RELEASE_INTENT_RECEIPT.json",
            "QUIESCENCE_POST_RELEASE_RECEIPT.json",
            "SESSION_POST_HANDOFF_RECEIPT.json",
        }
    )
    assert bundle["transaction"]["receipt_production_order"] == [
        "pre_replay_inputs_complete",
        "migration_and_fresh_worker_replay_receipts_complete",
        "quiescence_release_intent_receipt_complete",
        "lease_released_and_file_descriptor_closed",
        "quiescence_post_release_receipt_complete",
        "session_post_handoff_receipt_complete",
        "migration_transaction_receipt_complete",
    ]
def test_standalone_safety_validator_rejects_replay_receipt_cycles() -> None:
    bundle = _contract_bundle()
    _permit_future_replay_receipt(bundle)
    with pytest.raises(ValueError, match="future receipt"):
        safety_validator.validate_replay_contract(bundle["migration_replay"])

    bundle = _contract_bundle()
    _emit_transaction_receipt_before_replay(bundle)
    with pytest.raises(ValueError, match="production order"):
        safety_validator.validate_migration_contract(bundle["transaction"])




def test_exact_rc5_contract_is_three_store_fresh_session_and_zero_authority() -> None:
    bundle = _contract_bundle()

    handoff_inputs = _validate_contract_bundle(bundle)

    transaction = bundle["transaction"]
    session = bundle["session_handoff"]["post_handoff_receipt"]
    assert transaction["commit_order"] == STORE_ORDER
    assert [store["id"] for store in transaction["stores"]] == STORE_ORDER
    assert transaction["receipt_required"]["session_fields"]["location"] == (
        "outside stores and commit_order"
    )
    assert "new_session_id differs from prior_session_id" in session["invariants"]
    assert {
        "capabilities is empty",
        "active_authority is false",
        "score_authority is false",
        "checkpoint_authority is false",
        "target_execution_allowed is false",
    }.issubset(session["invariants"])
    assert handoff_inputs == set(bundle["handoff"]["allowed_inputs"])
    assert freeze_validator.ARTIFACT_MANIFEST_SHA256 == (
        RC5_ARTIFACT_MANIFEST_SHA256
    )
    assert freeze_validator.ARCHIVE_MANIFEST_SHA256 == (
        SEALED_ARCHIVE_MANIFEST_SHA256
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (_add_session_store, "exactly three stores"),
        (_claim_migration_in_progress, "MIGRATION_IN_PROGRESS"),
        (_commit_selector_before_beads, "root active selector"),
        (
            _replace_continuous_lock_with_creation_lock,
            "stable-inode lock or durable journal",
        ),
        (_remove_durable_journal, "stable-inode lock or durable journal"),
        (_accept_forced_kill_as_success, "forced or timeout kill"),
        (_claim_release_fact_in_intent, "release-intent receipt"),
        (_fabricate_absent_event_bytes, "absent event log"),
        (_reuse_rc4_authority, "rc4 SPEC_FREEZE authority"),
        (_permit_unknown_adapter, "runtime adapter set"),
        (_resume_prior_session, "fresh-session handoff"),
        (_grant_target_authority, "fresh-worker handoff"),
        (_drop_journal_replay_input, "migration replay"),
        (_permit_future_replay_receipt, "migration replay"),
        (_emit_transaction_receipt_before_replay, "production order"),
    ],
    ids=[
        "four-store-session-transaction",
        "client-domain-error-claim",
        "selector-not-last",
        "creation-only-lock",
        "nondurable-journal",
        "forced-kill-success",
        "release-intent-future-fact",
        "fabricated-event-absence",
        "rc3-authority-reuse",
        "unknown-runtime-adapter",
        "prior-session-resume",
        "target-authority",
        "missing-replay-journal",
        "future-receipt-replay-input",
        "transaction-receipt-before-replay",
    ],
)
def test_validator_rejects_rc3_contract_regressions(
    mutation: ContractMutation,
    message: str,
) -> None:
    bundle = copy.deepcopy(_contract_bundle())
    mutation(bundle)

    with pytest.raises(ValueError, match=message):
        _validate_contract_bundle(bundle)


@pytest.mark.parametrize(
    ("field_list", "missing_field", "message"),
    [
        ("required_fields", "journal", "quiescence receipt"),
        ("lease_fields", "flock_held", "lease"),
        ("journal_fields", "fsynced_through_sequence", "journal"),
        ("dolt_adapter_fields", "adapter_kind", "Dolt adapter"),
        ("process_entry_fields", "start_identity", "process inventory"),
        ("descriptor_scan_fields", "process_snapshot_sha256", "descriptor scan"),
        ("descriptor_target_fields", "inode", "descriptor target"),
        ("omp_rpc_session_fields", "process_exit_status", "OMP/RPC process"),
        ("closed_transcript_fields", "open_fd_count", "closed transcript"),
    ],
)
def test_validator_requires_lock_journal_adapter_process_and_fd_evidence(
    field_list: str,
    missing_field: str,
    message: str,
) -> None:
    bundle = copy.deepcopy(_contract_bundle())
    fields = bundle["quiescence"]["receipt_contract"][field_list]
    fields.remove(missing_field)

    with pytest.raises(ValueError, match=message):
        _validate_contract_bundle(bundle)


def _catalog_and_archive() -> tuple[dict[str, Any], dict[str, Any]]:
    points = [20] * 48 + [40]
    items = [
        {
            "description": f"item {index}",
            "item_id": f"I{index:02d}",
            "pass_predicate": f"predicate {index}",
            "points": item_points,
            "proof_floor": f"floor {index}",
            "workstream": f"workstream {index % 4}",
        }
        for index, item_points in enumerate(points)
    ]
    return (
        {
            "catalog_points": 1000,
            "item_count": 49,
            "items": copy.deepcopy(items),
        },
        {"SCORECARD.json": {"items": copy.deepcopy(items)}},
    )


def _zero_state_documents() -> dict[str, dict[str, Any]]:
    catalog, _archive = _catalog_and_archive()
    item_ids = [item["item_id"] for item in catalog["items"]]
    return {
        "ASSURANCE_CATALOG.json": catalog,
        "AUTHORITY_POLICY.json": {
            "cryptographic_trust": {
                "public_keys": [],
                "required_before_first_target_campaign": True,
                "state": "not_provisioned",
            }
        },
        "DRAFT_STATUS.json": {
            "active": False,
            "internal_completion": False,
            "target_lease": None,
            "tracks": {
                "assurance": {
                    "awarded_items": [],
                    "current_verified_points": 0,
                    "evidence_ref_count": 0,
                    "review_ref_count": 0,
                }
            },
        },
        "EVIDENCE_INDEX.json": {
            "active_relations": 0,
            "rows": [{"active": False, "admitted": False}],
        },
        "RUN_QUEUE.json": {"eligible": [], "target_lease": None},
        "WORK_PACKET_DAG.yaml": {
            "nodes": [
                {
                    "depends_on": [],
                    "id": "AT0",
                    "score_rows": item_ids,
                }
            ]
        },
    }


def test_safety_logic_rejects_catalog_and_sealed_archive_drift() -> None:
    documents, archive = _catalog_and_archive()
    safety_validator._validate_catalog_score_archive(
        {"ASSURANCE_CATALOG.json": documents},
        archive,
    )

    catalog_drift = copy.deepcopy(documents)
    catalog_drift["catalog_points"] = 999
    with pytest.raises(ValueError, match="catalog item/points drift"):
        safety_validator._validate_catalog_score_archive(
            {"ASSURANCE_CATALOG.json": catalog_drift},
            archive,
        )

    archive_drift = copy.deepcopy(archive)
    archive_drift["SCORECARD.json"]["items"][0]["points"] += 1
    with pytest.raises(ValueError, match="score/archive drift"):
        safety_validator._validate_catalog_score_archive(
            {"ASSURANCE_CATALOG.json": documents},
            archive_drift,
        )


@pytest.mark.parametrize(
    (("mutation", "message")),
    [
        (
            lambda documents: documents["WORK_PACKET_DAG.yaml"]["nodes"][0][
                "depends_on"
            ].append("missing-node"),
            "DAG drift",
        ),
        (
            lambda documents: documents["RUN_QUEUE.json"].__setitem__(
                "eligible", ["AT0"]
            ),
            "queue eligibility drift",
        ),
        (
            lambda documents: documents["DRAFT_STATUS.json"]["tracks"][
                "assurance"
            ].__setitem__("current_verified_points", 1),
            "score or award authority",
        ),
    ],
    ids=["dag", "queue", "score"],
)
def test_safety_logic_rejects_dag_queue_and_score_drift(
    mutation: Callable[[dict[str, dict[str, Any]]], None],
    message: str,
) -> None:
    documents = _zero_state_documents()
    mutation(documents)

    with pytest.raises(ValueError, match=message):
        safety_validator._validate_dag_and_zero_state(documents)


def _manifest_row(path: str, marker: str) -> dict[str, Any]:
    return {
        "mode": "0444",
        "path": path,
        "sha256": "sha256:" + marker * 64,
        "size": ord(marker),
    }


def _safety_binding_fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
]:
    predecessor_rows = {
        name: {**_manifest_row(name, "a"), "media_type": "application/json"}
        for name in safety_validator.EXPECTED_PREDECESSOR_FILES
    }
    candidate_rows: dict[str, dict[str, Any]] = {}
    for name in safety_validator.EXPECTED_CANDIDATE_FILES:
        if name in safety_validator.UNCHANGED_FILES:
            candidate_rows[name] = copy.deepcopy(predecessor_rows[name])
        elif name in safety_validator.REVIEWED_ADDED_FILES:
            candidate_rows[name] = {
                **_manifest_row(name, "c"),
                "media_type": "application/json",
            }
        else:
            candidate_rows[name] = {
                **_manifest_row(name, "b"),
                "media_type": "application/json",
            }
    archive_rows = [
        _manifest_row(f"archive-{index:02d}.json", "d")
        for index in range(
            safety_validator.EXPECTED_BUILD_FILE_COUNT - len(candidate_rows) - 2
        )
    ]
    candidate_manifest = {
        "archive_manifest_sha256": SEALED_ARCHIVE_MANIFEST_SHA256,
        "files": list(candidate_rows.values()),
        "immutable": True,
        "program_id": safety_validator.PROGRAM_ID,
        "revision_id": safety_validator.CANDIDATE_REVISION_ID,
        "schema_version": "bb.rl.phase5.artifact_manifest.v4",
        "superseded_artifact_manifest_sha256": RC4_ARTIFACT_MANIFEST_SHA256,
        "superseded_revision_id": safety_validator.PREDECESSOR_REVISION_ID,
        "supersession_scope": (
            "migration and cutover mechanics only; prior rc4 SPEC_FREEZE "
            "grants no rc5 authority"
        ),
        "v1_active_status_sha256": safety_validator.V1_ACTIVE_SHA256,
        "v1_scorecard_sha256": safety_validator.V1_SCORECARD_SHA256,
    }
    predecessor_manifest = {
        "archive_manifest_sha256": SEALED_ARCHIVE_MANIFEST_SHA256,
        "files": list(predecessor_rows.values()),
        "immutable": True,
        "program_id": safety_validator.PROGRAM_ID,
        "revision_id": safety_validator.PREDECESSOR_REVISION_ID,
        "schema_version": "bb.rl.phase5.artifact_manifest.v4",
        "superseded_artifact_manifest_sha256": RC3_ARTIFACT_MANIFEST_SHA256,
        "superseded_revision_id": safety_validator.PRE_PREDECESSOR_REVISION_ID,
        "supersession_scope": (
            "migration and cutover mechanics only; prior rc3 SPEC_FREEZE "
            "grants no rc4 authority"
        ),
        "v1_active_status_sha256": safety_validator.V1_ACTIVE_SHA256,
        "v1_scorecard_sha256": safety_validator.V1_SCORECARD_SHA256,
    }
    archive_manifest = {
        "archive_id": safety_validator.ARCHIVE_ID,
        "files": archive_rows,
        "original_active_status_sha256": safety_validator.V1_ACTIVE_SHA256,
        "original_scorecard_sha256": safety_validator.V1_SCORECARD_SHA256,
        "policy": {
            "byte_identical": True,
            "no_v2_authority": True,
            "read_only_historical": True,
        },
        "program_id": "bb-zyphra-rl-phase5-v1",
        "schema_version": "bb.rl.phase5.v1_archive_manifest.v1",
        "source_root": (
            "/Users/kylemccleary/projects/breadboard/docs_tmp/ZYPHRA/RL_PHASE_5/execution"
        ),
    }
    build_report = {
        "archive_manifest_sha256": SEALED_ARCHIVE_MANIFEST_SHA256,
        "artifact_manifest_sha256": RC5_ARTIFACT_MANIFEST_SHA256,
        "build_a_file_count": 55,
        "build_b_file_count": 55,
        "byte_identical": True,
        "catalog_sha256": candidate_rows["ASSURANCE_CATALOG.json"]["sha256"],
        "equivalence_sha256": candidate_rows["CATALOG_EQUIVALENCE.json"]["sha256"],
        "installed": True,
        "program_id": safety_validator.PROGRAM_ID,
        "result": "pass",
        "revision_id": safety_validator.CANDIDATE_REVISION_ID,
        "revision_root": "versions/v2-two-track/v2.0.0-rc5-20260717",
        "schema_version": "bb.rl.phase5.freeze_build_report.v1",
        "source_entries": 835,
    }
    artifact_hashes = {
        "archive_manifest_sha256": SEALED_ARCHIVE_MANIFEST_SHA256,
        "build_report_sha256": safety_validator.BUILD_REPORT_SHA256,
        "candidate_artifact_manifest_sha256": RC5_ARTIFACT_MANIFEST_SHA256,
        "predecessor_artifact_manifest_sha256": RC4_ARTIFACT_MANIFEST_SHA256,
    }
    return (
        candidate_manifest,
        predecessor_manifest,
        archive_manifest,
        build_report,
        artifact_hashes,
    )


def test_safety_bindings_pin_exact_candidate_predecessor_archive_and_build_report() -> None:
    fixture = _safety_binding_fixture()

    matrix = safety_validator.validate_safety_bindings(*fixture)

    manifest_row = matrix[-1]
    assert manifest_row == {
        "classification": "reviewed_manifest_identity_and_supersession",
        "path": "ARTIFACT_MANIFEST.json",
        "candidate_sha256": RC5_ARTIFACT_MANIFEST_SHA256,
        "candidate_size": None,
        "predecessor_sha256": RC4_ARTIFACT_MANIFEST_SHA256,
        "predecessor_size": None,
    }


def test_safety_bindings_reject_predecessor_pin_and_archive_authority_drift() -> None:
    fixture = list(_safety_binding_fixture())
    wrong_hashes = copy.deepcopy(fixture[4])
    wrong_hashes["predecessor_artifact_manifest_sha256"] = "sha256:" + "0" * 64
    fixture[4] = wrong_hashes
    with pytest.raises(ValueError, match="predecessor artifact manifest pin mismatch"):
        safety_validator.validate_safety_bindings(*fixture)

    fixture = list(_safety_binding_fixture())
    archive = copy.deepcopy(fixture[2])
    archive["policy"]["no_v2_authority"] = False
    fixture[2] = archive
    with pytest.raises(ValueError, match="archive ID or authority drift"):
        safety_validator.validate_safety_bindings(*fixture)


def test_runtime_adapter_selection_is_discovered_and_fails_closed() -> None:
    repo_root = "/workspace/repo"
    common_context = {
        "backend": "dolt",
        "beads_dir": f"{repo_root}/.beads",
        "database": "beads",
        "is_redirected": False,
        "repo_root": repo_root,
    }
    embedded = {
        **common_context,
        "dolt_mode": "direct",
    }
    server = {
        **common_context,
        "dolt_mode": "sql_server",
        "server_host": "127.0.0.1",
        "server_port": 3306,
    }
    assert runtime_probe.select_runtime_adapter(embedded) == "embedded_dolt_cli"
    assert runtime_probe.select_runtime_adapter(server) == "sql_server"

    invalid_contexts = [
        {**embedded, "is_redirected": True},
        {**embedded, "server_host": "127.0.0.1", "server_port": 3306},
        {**server, "server_port": None},
        {**embedded, "dolt_mode": "unknown"},
        {**embedded, "mode": "sql_server"},
    ]
    for context in invalid_contexts:
        with pytest.raises(runtime_probe.RuntimeProbeError):
            runtime_probe.select_runtime_adapter(context)


def _runtime_probe_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    repo_root = tmp_path / "repo"
    beads_dir = repo_root / ".beads"
    repository = beads_dir / "embeddeddolt" / "beads"
    dolt_metadata = repository / ".dolt"
    dolt_metadata.mkdir(parents=True)
    marker = dolt_metadata / "manifest"
    marker.write_bytes(b"immutable dolt fixture\n")
    marker.chmod(0o640)
    execution_root = tmp_path / "execution"
    candidate = execution_root / "candidate"
    candidate.mkdir(parents=True)
    publication_root = execution_root.parent / "runtime_preflight_observations"
    publication_root.mkdir()
    publication_root.chmod(0o755)

    for name in (
        "BEADS_DIR",
        "BEADS_DOLT_DATA_DIR",
        "BEADS_DOLT_PASSWORD",
        "BEADS_DOLT_SERVER_DATABASE",
        "BEADS_DOLT_SERVER_HOST",
        "BEADS_DOLT_SERVER_PORT",
        "BEADS_DOLT_SERVER_TLS",
        "BEADS_DOLT_SERVER_USER",
    ):
        monkeypatch.delenv(name, raising=False)

    context = {
        "backend": "dolt",
        "bd_version": "1.0.5",
        "beads_dir": str(beads_dir),
        "database": "beads",
        "dolt_mode": "direct",
        "is_redirected": False,
        "repo_root": str(repo_root),
    }
    manifest_path = candidate / "ARTIFACT_MANIFEST.json"
    candidate_binding = {
        "file_count": 54,
        "manifest_path": str(manifest_path),
        "manifest_sha256": RC4_ARTIFACT_MANIFEST_SHA256,
        "manifest_identity": {
            "device": 1,
            "inode": 9,
            "link_count": 1,
            "mode": "0444",
            "mtime_ns": 1,
            "path": str(manifest_path),
            "sha256": RC4_ARTIFACT_MANIFEST_SHA256,
            "size": 1,
        },
    }

    def frozen_snapshot(path: Path, digest: str, inode: int) -> dict[str, Any]:
        return {
            "device": 1,
            "inode": inode,
            "link_count": 1,
            "mode": "0444",
            "mtime_ns": 1,
            "path": str(path),
            "sha256": digest,
            "size": 1,
        }

    lineage_binding = {
        "root_active_selector": frozen_snapshot(
            execution_root / "ACTIVE_STATUS.json",
            runtime_probe.V1_ACTIVE_STATUS_SHA256,
            12,
        ),
        "sealed_v1_archive_manifest": frozen_snapshot(
            execution_root
            / "versions"
            / "v1-bootstrap-20260709-sealed-rc3"
            / "ARCHIVE_MANIFEST.json",
            SEALED_ARCHIVE_MANIFEST_SHA256,
            11,
        ),
        "superseded_rc3_manifest": frozen_snapshot(
            execution_root
            / "versions"
            / "v2-two-track"
            / "v2.0.0-rc3-20260715"
            / "ARTIFACT_MANIFEST.json",
            RC3_ARTIFACT_MANIFEST_SHA256,
            10,
        ),
    }
    environment_binding = {
        name: {"present": False, "sha256": None, "size": 0}
        for name in (
            "BEADS_DIR",
            "BEADS_DOLT_DATA_DIR",
            "BEADS_DOLT_PASSWORD",
            "BEADS_DOLT_SERVER_DATABASE",
            "BEADS_DOLT_SERVER_HOST",
            "BEADS_DOLT_SERVER_PORT",
            "BEADS_DOLT_SERVER_TLS",
            "BEADS_DOLT_SERVER_USER",
        )
    }
    platform_binding = {
        "architecture": "arm64",
        "fd_scan": {
            "backend": "lsof",
            "exercised": False,
            "lsof_path": "/usr/sbin/lsof",
            "present": True,
        },
        "lock": {
            "backend": "fcntl.flock",
            "continuous_stable_inode_capable": True,
            "exercised": False,
            "present": True,
        },
        "os": "darwin",
        "os_release": "25.5.0",
        "process_birth_identity": {
            "backend": "libproc_proc_pidinfo_plus_ps_lstart",
            "exercised": False,
            "present": True,
            "proc_pidinfo_present": True,
            "ps_path": "/bin/ps",
        },
        "python_version": "3.13.5",
        "result": "capability_presence_only",
    }
    binary_inventory = {
        name: {
            "device": 1,
            "inode": 1 if name == "bd" else 2,
            "mode": "0555",
            "mtime_ns": 1,
            "name": name,
            "requested_path": f"/installed/{name}",
            "resolved_path": f"/installed/{name}",
            "sha256": "sha256:" + ("b" if name == "bd" else "d") * 64,
            "size": 1,
        }
        for name in ("bd", "dolt")
    }
    monkeypatch.setattr(
        runtime_probe,
        "_verify_candidate",
        lambda _execution: (candidate, {}, candidate_binding),
    )
    monkeypatch.setattr(
        runtime_probe,
        "_verify_frozen_lineage",
        lambda _execution: copy.deepcopy(lineage_binding),
    )
    monkeypatch.setattr(
        runtime_probe,
        "_platform_capabilities",
        lambda: copy.deepcopy(platform_binding),
    )
    monkeypatch.setattr(
        runtime_probe,
        "_environment_evidence",
        lambda: copy.deepcopy(environment_binding),
    )
    monkeypatch.setattr(
        runtime_probe,
        "_binary_identity",
        lambda name, required: copy.deepcopy(binary_inventory[name]),
    )
    monkeypatch.setattr(
        runtime_probe,
        "_command_binary_identity",
        lambda path: {
            "device": 1,
            "inode": 1 if path.name == "bd" else 2,
            "mode": "0555",
            "mtime_ns": 1,
            "path": str(path),
            "sha256": "sha256:" + ("b" if path.name == "bd" else "d") * 64,
            "size": 1,
        },
    )
    return {
        "beads_dir": beads_dir,
        "candidate": candidate,
        "candidate_binding": candidate_binding,
        "binary_inventory": binary_inventory,
        "context": context,
        "environment_binding": environment_binding,
        "execution_root": execution_root,
        "marker": marker,
        "output_report": Path("runtime.json"),
        "publication_root": publication_root,
        "lineage_binding": lineage_binding,
        "platform_binding": platform_binding,
        "repo_root": repo_root,
        "repository": repository,
    }


def _successful_runtime_runner(
    fixture: dict[str, Any],
    *,
    on_dolt: Callable[[tuple[str, ...], Path], None] | None = None,
) -> tuple[
    runtime_probe.CommandRunner,
    list[tuple[tuple[str, ...], Path]],
]:
    calls: list[tuple[tuple[str, ...], Path]] = []
    query_rows = {
        runtime_probe.SUMMARY_QUERY: [
            {
                "branch": "main",
                "dolt_version": "1.75.0",
                "head_commit": "a" * 32,
                "head_root": "b" * 32,
                "staged_root": "b" * 32,
                "working_root": "c" * 32,
            }
        ],
        runtime_probe.STATUS_QUERY: [],
        runtime_probe.TABLES_QUERY: [{"table_name": "issues"}],
        runtime_probe.SCHEMA_COLUMNS_QUERY: [
            {"column_name": "id", "table_name": "issues"}
        ],
        runtime_probe.SCHEMA_CONSTRAINTS_QUERY: [],
        runtime_probe.SCHEMA_INDEXES_QUERY: [],
        "SELECT * FROM `issues`": [{"id": "bb-test"}],
    }

    def runner(argv: tuple[str, ...], cwd: Path) -> runtime_probe.CommandResult:
        calls.append((argv, cwd))
        if argv == ("/installed/bd", "--version"):
            assert cwd == fixture["repo_root"]
            stdout = b"bd version 1.0.5\n"
        elif argv == ("/installed/bd", "context", "--json"):
            assert cwd == fixture["repo_root"]
            stdout = json.dumps(fixture["context"]).encode("utf-8")
        elif argv == ("/installed/dolt", "version"):
            if on_dolt is not None:
                on_dolt(argv, cwd)
            stdout = b"dolt version 1.75.0\n"
        elif argv[:5] == (
            "/installed/dolt",
            "sql",
            "--result-format",
            "json",
            "--query",
        ):
            if on_dolt is not None:
                on_dolt(argv, cwd)
            query = argv[5]
            assert query in query_rows, f"unexpected SELECT: {query!r}"
            stdout = json.dumps({"rows": query_rows[query]}).encode("utf-8")
        else:
            raise AssertionError(f"unexpected command: {argv!r}")
        return runtime_probe.CommandResult(
            argv=argv,
            cwd=cwd,
            binary_path=argv[0],
            exit_code=0,
            stdout=stdout,
            stderr=b"",
            execution_mode="injected_non_native_test_seam",
            used_binary_identity=None,
            used_cwd_identity=None,
        )

    return runner, calls


def _assert_retained_output_binding(
    command: dict[str, Any],
    *,
    stdout: bytes,
    stderr: bytes,
) -> None:
    for stream, expected in (("stdout", stdout), ("stderr", stderr)):
        encoded = command[f"{stream}_base64"]
        assert isinstance(encoded, str)
        decoded = base64.b64decode(encoded, validate=True)
        assert base64.b64encode(decoded).decode("ascii") == encoded
        assert decoded == expected
        assert command[f"{stream}_size"] == len(decoded)
        assert command[f"{stream}_sha256"] == runtime_probe._sha256(decoded)


def _pinned_file_verifier_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_file: str,
) -> tuple[Path, Callable[[], Any]]:
    execution_root = tmp_path / "execution"
    if pinned_file == "rc4_artifact_manifest":
        candidate = (
            execution_root
            / "versions"
            / "v2-two-track"
            / runtime_probe.REVISION_ID
        )
        candidate.mkdir(parents=True)
        artifact = candidate / "payload.txt"
        artifact_payload = b"pinned rc4 payload\n"
        artifact.write_bytes(artifact_payload)
        artifact.chmod(0o444)
        manifest = {
            "archive_manifest_sha256": runtime_probe.SEALED_V1_ARCHIVE_MANIFEST_SHA256,
            "files": [
                {
                    "mode": "0444",
                    "path": artifact.name,
                    "sha256": runtime_probe._sha256(artifact_payload),
                    "size": len(artifact_payload),
                }
            ],
            "immutable": True,
            "program_id": runtime_probe.PROGRAM_ID,
            "revision_id": runtime_probe.REVISION_ID,
            "superseded_artifact_manifest_sha256": (
                runtime_probe.SUPERSEDED_RC3_MANIFEST_SHA256
            ),
            "superseded_revision_id": "v2.0.0-rc3-20260715",
            "v1_active_status_sha256": runtime_probe.V1_ACTIVE_STATUS_SHA256,
        }
        target = candidate / "ARTIFACT_MANIFEST.json"
        raw = json.dumps(manifest, sort_keys=True).encode("utf-8")
        target.write_bytes(raw)
        target.chmod(0o444)
        monkeypatch.setattr(
            runtime_probe,
            "ARTIFACT_MANIFEST_SHA256",
            runtime_probe._sha256(raw),
        )
        return target, lambda: runtime_probe._verify_candidate(execution_root)

    lineage = {
        "superseded_rc3_manifest": (
            execution_root
            / "versions"
            / "v2-two-track"
            / "v2.0.0-rc3-20260715"
            / "ARTIFACT_MANIFEST.json",
            "SUPERSEDED_RC3_MANIFEST_SHA256",
        ),
        "sealed_v1_archive_manifest": (
            execution_root
            / "versions"
            / "v1-bootstrap-20260709-sealed-rc3"
            / "ARCHIVE_MANIFEST.json",
            "SEALED_V1_ARCHIVE_MANIFEST_SHA256",
        ),
        "root_active_selector": (
            execution_root / "ACTIVE_STATUS.json",
            "V1_ACTIVE_STATUS_SHA256",
        ),
    }
    for name, (path, digest_name) in lineage.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"pinned {name}\n".encode("utf-8")
        path.write_bytes(payload)
        path.chmod(0o444)
        monkeypatch.setattr(runtime_probe, digest_name, runtime_probe._sha256(payload))
    target = lineage[pinned_file][0]
    return target, lambda: runtime_probe._verify_frozen_lineage(execution_root)


@pytest.mark.parametrize(
    "pinned_file",
    (
        "rc4_artifact_manifest",
        "superseded_rc3_manifest",
        "sealed_v1_archive_manifest",
        "root_active_selector",
    ),
)
@pytest.mark.parametrize("link_timing", ("before_read", "after_read"))
def test_every_individually_pinned_file_rejects_hardlink_aliases_around_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_file: str,
    link_timing: str,
) -> None:
    target, verify = _pinned_file_verifier_fixture(
        tmp_path,
        monkeypatch,
        pinned_file,
    )
    alias = target.with_name(f"{target.name}.hardlink")
    target_inode = target.stat().st_ino

    if link_timing == "before_read":
        os.link(target, alias)
    else:
        original_read = runtime_probe.os.read

        def link_at_eof(descriptor: int, size: int) -> bytes:
            chunk = original_read(descriptor, size)
            if (
                chunk == b""
                and not alias.exists()
                and os.fstat(descriptor).st_ino == target_inode
            ):
                os.link(target, alias)
            return chunk

        monkeypatch.setattr(runtime_probe.os, "read", link_at_eof)

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match=r"(?:exactly one link|link count changed|file drifted while it was read)",
    ):
        verify()

    assert alias.is_file()
    assert target.stat().st_nlink == 2
    assert alias.stat().st_ino == target_inode


def test_default_runner_seals_dolt_environment_inside_isolated_temp_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path.chmod(0o700)
    beads_dir = tmp_path / ".beads"
    repository = beads_dir / "embeddeddolt" / "beads"
    repository.mkdir(parents=True)
    installed_dolt = tmp_path / "bin" / "dolt"
    installed_dolt.parent.mkdir()
    installed_dolt.write_bytes(b"test-only Dolt placeholder\n")
    installed_bd = installed_dolt.with_name("bd")
    installed_bd.write_bytes(b"test-only bd placeholder\n")
    monkeypatch.setattr(
        runtime_probe.shutil,
        "which",
        lambda name: str(installed_dolt) if name == "dolt" else None,
    )
    ambient_environment = {
        "BEADS_DIR": "/ambient/beads",
        "BEADS_DOLT_DATA_DIR": "/ambient/dolt-data",
        "BEADS_DOLT_PASSWORD": "ambient-password",
        "BEADS_DOLT_SERVER_DATABASE": "ambient-database",
        "BEADS_DOLT_SERVER_HOST": "ambient-host",
        "BEADS_DOLT_SERVER_PORT": "3306",
        "BEADS_DOLT_SERVER_TLS": "true",
        "BEADS_DOLT_SERVER_USER": "ambient-user",
        "HOME": "/ambient/home",
        "LOGNAME": "ambient-logname",
        "PATH": "/ambient/bin",
        "TMPDIR": "/ambient/tmp",
        "USER": "ambient-user",
        "XDG_CONFIG_HOME": "/ambient/xdg",
        "BB_RC4_AMBIENT_SENTINEL": "must-not-reach-dolt",
    }
    for name, value in ambient_environment.items():
        monkeypatch.setenv(name, value)

    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    spawn_bindings: list[tuple[os.stat_result, os.stat_result]] = []

    def fake_run(
        argv: tuple[str, ...],
        **kwargs: Any,
    ) -> Any:
        binary_descriptor, cwd_descriptor, evidence_descriptor = kwargs["pass_fds"]
        binary_metadata = os.fstat(binary_descriptor)
        cwd_metadata = os.fstat(cwd_descriptor)
        child_evidence = runtime_probe._canonical_bytes(
            {
                "binary_identity": runtime_probe._child_binary_descriptor_identity(
                    binary_metadata
                ),
                "cwd_identity": runtime_probe._child_cwd_descriptor_identity(
                    cwd_metadata
                ),
            }
        )
        os.write(evidence_descriptor, child_evidence)
        calls.append((argv, kwargs))
        spawn_bindings.append((binary_metadata, cwd_metadata))
        return runtime_probe.subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=b"observed stdout\n",
            stderr=b"observed stderr\n",
        )

    monkeypatch.setattr(runtime_probe.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runtime_probe,
        "_descriptor_executable_path",
        lambda descriptor: f"/test-only-retained-fd/{descriptor}",
    )
    dolt_commands = (
        (str(installed_dolt), "version"),
        (str(installed_dolt), "sql", "--query", "SELECT 1"),
    )
    results = []
    for argv in dolt_commands:
        results.append(runtime_probe._default_runner(argv, repository))
    results.append(
        runtime_probe._default_runner((str(installed_bd), "--version"), tmp_path)
    )
    for result in results:
        assert result.stdout == b"observed stdout\n"
        assert result.stderr == b"observed stderr\n"
        assert result.execution_mode == "native_descriptor_bound"
        assert result.used_binary_identity is not None
        assert result.used_cwd_identity is not None

    assert [argv for argv, _kwargs in calls] == [
        *dolt_commands,
        (str(installed_bd), "--version"),
    ]
    for (argv, kwargs), (binary_metadata, cwd_metadata), result in zip(
        calls,
        spawn_bindings,
        results,
        strict=True,
    ):
        binary_descriptor, cwd_descriptor, evidence_descriptor = kwargs["pass_fds"]
        assert kwargs["executable"] == runtime_probe._descriptor_executable_path(
            binary_descriptor
        )
        assert kwargs["cwd"] is None
        assert kwargs["stdin"] is runtime_probe.subprocess.DEVNULL
        assert kwargs["stdout"] is runtime_probe.subprocess.PIPE
        assert kwargs["stderr"] is runtime_probe.subprocess.PIPE
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 60
        assert kwargs["pass_fds"] == (
            binary_descriptor,
            cwd_descriptor,
            evidence_descriptor,
        )
        assert callable(kwargs["preexec_fn"])
        assert result.used_binary_identity == {
            "device": binary_metadata.st_dev,
            "inode": binary_metadata.st_ino,
            "mode": f"{binary_metadata.st_mode & 0o7777:04o}",
            "mtime_ns": binary_metadata.st_mtime_ns,
            "path": argv[0],
            "sha256": result.used_binary_identity["sha256"],
            "size": binary_metadata.st_size,
        }
        assert result.used_cwd_identity == {
            "device": cwd_metadata.st_dev,
            "inode": cwd_metadata.st_ino,
            "mode": f"{cwd_metadata.st_mode & 0o7777:04o}",
            "path": str(repository if argv in dolt_commands else tmp_path),
        }

    forbidden_dolt_environment = {
        "BEADS_DIR",
        "BEADS_DOLT_DATA_DIR",
        "BEADS_DOLT_PASSWORD",
        "BEADS_DOLT_SERVER_DATABASE",
        "BEADS_DOLT_SERVER_HOST",
        "BEADS_DOLT_SERVER_PORT",
        "BEADS_DOLT_SERVER_TLS",
        "BEADS_DOLT_SERVER_USER",
        "LOGNAME",
        "USER",
    }
    for argv, kwargs in calls[:2]:
        assert argv[0] == str(installed_dolt)
        assert kwargs["cwd"] is None
        environment = kwargs["env"]
        assert set(environment) == {
            "HOME",
            "LANG",
            "LC_ALL",
            "NO_COLOR",
            "PAGER",
            "PATH",
            "TMPDIR",
            "XDG_CONFIG_HOME",
        }
        assert environment == {
            "HOME": str(tmp_path / ".dolt-home"),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "PAGER": "cat",
            "PATH": os.defpath,
            "TMPDIR": str(tmp_path / ".dolt-tmp"),
            "XDG_CONFIG_HOME": str(tmp_path / ".dolt-xdg-config"),
        }
        assert "BB_RC4_AMBIENT_SENTINEL" not in environment
        assert forbidden_dolt_environment.isdisjoint(environment)
        assert environment["PATH"] == os.defpath
        for name in ("HOME", "XDG_CONFIG_HOME", "TMPDIR"):
            private_path = Path(environment[name]).resolve(strict=True)
            assert private_path.is_dir()
            assert private_path.is_relative_to(tmp_path)
            assert not private_path.is_relative_to(beads_dir)
            assert environment[name] != ambient_environment[name]

    bd_argv, bd_kwargs = calls[2]
    assert bd_argv == (str(installed_bd), "--version")
    assert bd_kwargs["cwd"] is None
    expected_bd_environment = dict(os.environ)
    expected_bd_environment.update(
        {"LC_ALL": "C", "NO_COLOR": "1", "PAGER": "cat"}
    )
    assert bd_kwargs["env"] == expected_bd_environment


def test_linux_native_runner_executes_retained_descriptors_after_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if runtime_probe.platform.system() != "Linux":
        pytest.skip("genuine descriptor-bound executable launch requires Linux")
    executable = tmp_path / "installed-cat"
    binary_payload = Path("/bin/cat").read_bytes()
    executable.write_bytes(binary_payload)
    executable.chmod(0o755)
    cwd = tmp_path / "verified-cwd"
    cwd.mkdir()
    (cwd / "marker").write_bytes(b"verified cwd bytes\n")
    binary_before = executable.stat()
    cwd_before = cwd.stat()
    retained_executable = tmp_path / "retained-cat"
    retained_cwd = tmp_path / "retained-cwd"
    real_run = runtime_probe.subprocess.run
    swapped = False

    def swap_paths_after_descriptor_acquisition(
        argv: tuple[str, ...],
        **kwargs: Any,
    ) -> Any:
        nonlocal swapped
        assert not swapped
        assert kwargs["executable"] == f"/proc/self/fd/{kwargs['pass_fds'][0]}"
        executable.rename(retained_executable)
        executable.write_bytes(b"swapped executable pathname\n")
        executable.chmod(0o755)
        cwd.rename(retained_cwd)
        cwd.mkdir()
        (cwd / "marker").write_bytes(b"swapped cwd bytes\n")
        swapped = True
        return real_run(argv, **kwargs)

    monkeypatch.setattr(runtime_probe.subprocess, "run", swap_paths_after_descriptor_acquisition)
    result = runtime_probe._default_runner((str(executable), "marker"), cwd)

    assert swapped is True
    assert result.exit_code == 0
    assert result.stdout == b"verified cwd bytes\n"
    assert result.stderr == b""
    assert result.execution_mode == "native_descriptor_bound"
    assert result.used_binary_identity == {
        "device": binary_before.st_dev,
        "inode": binary_before.st_ino,
        "mode": "0755",
        "mtime_ns": binary_before.st_mtime_ns,
        "path": str(executable),
        "sha256": runtime_probe._sha256(binary_payload),
        "size": len(binary_payload),
    }
    assert result.used_cwd_identity == {
        "device": cwd_before.st_dev,
        "inode": cwd_before.st_ino,
        "mode": f"{cwd_before.st_mode & 0o7777:04o}",
        "path": str(cwd),
    }
    assert executable.stat().st_ino != binary_before.st_ino
    assert cwd.stat().st_ino != cwd_before.st_ino
    assert retained_executable.stat().st_ino == binary_before.st_ino
    assert retained_cwd.stat().st_ino == cwd_before.st_ino


def test_darwin_native_runner_fails_closed_without_subprocess_or_command_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "installed-command"
    executable.write_bytes(b"unsupported native command fixture\n")
    executable.chmod(0o755)
    cwd = tmp_path / "verified-cwd"
    cwd.mkdir()
    subprocess_calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def forbidden_subprocess(
        argv: tuple[str, ...],
        **kwargs: Any,
    ) -> Any:
        subprocess_calls.append((argv, kwargs))
        raise AssertionError("unsupported platform must not invoke subprocess")

    monkeypatch.setattr(runtime_probe.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime_probe.subprocess, "run", forbidden_subprocess)
    command_records: list[dict[str, Any]] = []

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="^descriptor-bound executable launch is unavailable$",
    ):
        runtime_probe._run_checked(
            runtime_probe._default_runner,
            (str(executable),),
            cwd,
            command_records,
        )

    assert subprocess_calls == []
    assert command_records == []


def test_darwin_var_alias_canonicalizes_the_process_created_temp_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_var = Path("/private/var")
    if (
        not Path("/var").is_symlink()
        or Path("/var").resolve(strict=True) != private_var
        or not tmp_path.is_relative_to(private_var)
    ):
        pytest.skip("Darwin /var lexical alias is unavailable")

    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    canonical_parent = tmp_path / "darwin-var-alias"
    canonical_parent.mkdir()
    canonical_parent.chmod(0o700)
    lexical_parent = Path("/var") / canonical_parent.relative_to(private_var)
    real_temporary_directory = runtime_probe.tempfile.TemporaryDirectory
    created: dict[str, Any] = {}

    def aliased_temporary_directory(*, prefix: str) -> Any:
        temporary = real_temporary_directory(prefix=prefix, dir=str(lexical_parent))
        lexical_leaf = Path(temporary.name)
        created["lexical_leaf"] = lexical_leaf
        created["identity"] = (lexical_leaf.lstat().st_dev, lexical_leaf.lstat().st_ino)
        return temporary

    observed_canonical_roots: list[Path] = []

    def observe_dolt_root(_argv: tuple[str, ...], cwd: Path) -> None:
        canonical_root = cwd.parents[2]
        observed_canonical_roots.append(canonical_root)
        assert str(created["lexical_leaf"]).startswith("/var/")
        assert str(canonical_root).startswith("/private/var/")
        canonical_metadata = canonical_root.stat(follow_symlinks=False)
        assert (canonical_metadata.st_dev, canonical_metadata.st_ino) == created["identity"]

    monkeypatch.setattr(
        runtime_probe.tempfile,
        "TemporaryDirectory",
        aliased_temporary_directory,
    )
    runner, _calls = _successful_runtime_runner(fixture, on_dolt=observe_dolt_root)

    report = runtime_probe.probe_runtime(
        fixture["repo_root"],
        fixture["execution_root"],
        fixture["publication_root"],
        fixture["output_report"],
        runner=runner,
    )

    assert observed_canonical_roots
    assert Path(os.path.realpath(created["lexical_leaf"])) == observed_canonical_roots[0]
    assert report["result"] == "preflight_observation_only"
    assert (fixture["publication_root"] / fixture["output_report"]).is_file()


def test_runtime_probe_rejects_tempfile_leaf_symlink_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    lexical_leaf = tmp_path / "substituted-temporary-leaf"
    substitution_target = tmp_path / "substitution-target"

    class SubstitutedTemporaryDirectory:
        def __enter__(self) -> str:
            substitution_target.mkdir()
            substitution_target.chmod(0o700)
            lexical_leaf.mkdir()
            lexical_leaf.rmdir()
            lexical_leaf.symlink_to(substitution_target, target_is_directory=True)
            return str(lexical_leaf)

        def __exit__(self, *_exc: object) -> None:
            lexical_leaf.unlink()
            substitution_target.rmdir()

    monkeypatch.setattr(
        runtime_probe.tempfile,
        "TemporaryDirectory",
        lambda *, prefix: SubstitutedTemporaryDirectory(),
    )
    runner, calls = _successful_runtime_runner(fixture)

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match=(
            "isolated clone temporary root lexical leaf must be an "
            "effective-UID-owned directory"
        ),
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert all(argv[0] != "/installed/dolt" for argv, _cwd in calls)
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_retained_tempfile_leaf_rejects_pathname_symlink_swap_during_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    lexical_leaf = tmp_path / "retained-temporary-leaf"
    retained_leaf = tmp_path / "same-inode-retained-leaf"
    substitution_target = tmp_path / "substitution-target"
    swapped_identity: list[tuple[int, int]] = []

    class ControlledTemporaryDirectory:
        def __enter__(self) -> str:
            lexical_leaf.mkdir()
            lexical_leaf.chmod(0o700)
            return str(lexical_leaf)

        def __exit__(self, *_exc: object) -> None:
            if lexical_leaf.is_symlink():
                lexical_leaf.unlink()
            for path in (lexical_leaf, retained_leaf, substitution_target):
                if path.exists():
                    runtime_probe.shutil.rmtree(path)

    monkeypatch.setattr(
        runtime_probe.tempfile,
        "TemporaryDirectory",
        lambda *, prefix: ControlledTemporaryDirectory(),
    )
    real_clone_tree = runtime_probe._clone_tree

    def swap_leaf_then_clone(
        source: Path,
        destination: Path,
        *,
        destination_parent_descriptor: int | None = None,
    ) -> Path:
        assert destination_parent_descriptor is not None
        created = lexical_leaf.stat(follow_symlinks=False)
        lexical_leaf.rename(retained_leaf)
        substitution_target.mkdir()
        substitution_target.chmod(0o700)
        lexical_leaf.symlink_to(substitution_target, target_is_directory=True)
        retained = retained_leaf.stat(follow_symlinks=False)
        swapped_identity.append(
            ((created.st_dev, created.st_ino), (retained.st_dev, retained.st_ino))
        )
        return real_clone_tree(
            source,
            destination,
            destination_parent_descriptor=destination_parent_descriptor,
        )

    monkeypatch.setattr(runtime_probe, "_clone_tree", swap_leaf_then_clone)
    runner, calls = _successful_runtime_runner(fixture)

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="isolated clone temporary root descriptor or dirent identity drifted",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert swapped_identity
    assert swapped_identity[0][0] == swapped_identity[0][1]
    assert all(argv[0] != "/installed/dolt" for argv, _cwd in calls)
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


@pytest.mark.parametrize(
    "relative_cwd",
    (
        Path("missing") / "embeddeddolt" / "beads",
        Path(".beads") / "nested" / ".beads" / "embeddeddolt" / "beads",
    ),
)
def test_private_dolt_environment_rejects_missing_or_ambiguous_beads_ancestor(
    tmp_path: Path,
    relative_cwd: Path,
) -> None:
    cwd = tmp_path / relative_cwd
    cwd.mkdir(parents=True)

    with pytest.raises(runtime_probe.RuntimeProbeError):
        runtime_probe._private_dolt_environment_paths(cwd)


def test_direct_runtime_probe_uses_isolated_dolt_cli_and_never_bd_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    clone_identities: list[tuple[int, int]] = []

    def churn_clone_once(_argv: tuple[str, ...], cwd: Path) -> None:
        if clone_identities:
            return
        clone_marker = cwd / ".dolt" / "manifest"
        assert cwd != fixture["repository"]
        assert not cwd.is_relative_to(fixture["repo_root"])
        assert clone_marker.read_bytes() == fixture["marker"].read_bytes()
        before = clone_marker.stat()
        replacement = cwd / ".dolt" / "manifest.replacement"
        replacement.write_bytes(clone_marker.read_bytes())
        replacement.chmod(before.st_mode & 0o7777)
        os.replace(replacement, clone_marker)
        os.utime(clone_marker, ns=(before.st_atime_ns, before.st_mtime_ns + 1))
        clone_identities.append((before.st_ino, clone_marker.stat().st_ino))

    runner, calls = _successful_runtime_runner(fixture, on_dolt=churn_clone_once)
    report = runtime_probe.probe_runtime(
        fixture["repo_root"],
        fixture["execution_root"],
        fixture["publication_root"],
        fixture["output_report"],
        runner=runner,
    )

    dolt_calls = [(argv, cwd) for argv, cwd in calls if argv[0] == "/installed/dolt"]
    assert dolt_calls
    assert {cwd for _argv, cwd in dolt_calls} == {dolt_calls[0][1]}
    assert all(cwd != fixture["repository"] for _argv, cwd in dolt_calls)
    assert all(
        not cwd.is_relative_to(fixture["repo_root"]) for _argv, cwd in dolt_calls
    )
    assert clone_identities[0][0] != clone_identities[0][1]
    assert any(argv[:2] == ("/installed/dolt", "sql") for argv, _cwd in calls)
    assert all(
        not (argv[0] == "/installed/bd" and "sql" in argv[1:])
        for argv, _cwd in calls
    )
    assert {
        command["cwd"]
        for command in report["commands"]
        if command["argv"][0] == "/installed/dolt"
    } == {"isolated-store://.beads/embeddeddolt/beads"}
    assert report["authority"] == {
        "checkpoint_authority": False,
        "completion_authority": False,
        "cutover_authority": False,
        "migration_authority": False,
        "prior_rc3_authority_reused": False,
        "score_authority": False,
        "selector_authority": False,
        "spec_freeze_authority": False,
        "target_authority": False,
        "zero_authority": True,
    }
    assert report["quiescence"] == {
        "descriptor_scan_executed": False,
        "flock_held": False,
        "journal_opened": False,
        "process_inventory_executed": False,
        "quiesced": False,
    }
    assert report["consumption_policy"] == {
        "consumable": False,
        "prohibited_downstream_receipt_roles": [
            "quiescence_acquisition_receipt",
            "migration_preparation_receipt",
            "migration_commit_receipt",
            "migration_replay_receipt",
            "release_intent_receipt",
            "post_release_receipt",
            "fresh_worker_handoff_receipt",
        ],
        "reason": "no spawn freeze, lease, journal, process inventory, descriptor scan, or quiescence",
    }
    observation = report["immutable_observation"]
    assert observation["isolated_store_exact_snapshot"] is True
    assert observation["no_live_dolt_command"] is True
    assert observation["live_store_drift"] is False
    assert observation["clone_content_drift"] is False
    assert observation["clone_disposed_before_publication"] is True
    assert (
        observation["live_store_pre_context_sha256"]
        == observation["live_store_before_sha256"]
        == observation["live_store_after_sha256"]
    )
    assert report["isolated_store"] == {
        "clone_after_content_sha256": observation["clone_after_content_sha256"],
        "clone_before_content_sha256": observation["clone_before_content_sha256"],
        "disposed_before_publication": True,
        "exact_snapshot": True,
        "live_adapter_write_free_behavior_proved": False,
        "live_dolt_command_executed": False,
        "query_repository": "isolated-store://.beads/embeddeddolt/beads",
        "source_content_sha256": observation["live_store_content_at_clone_sha256"],
    }
    assert report["limitations"][:4] == [
        "This receipt proves installed adapter discovery and isolated-clone preflight only; it is not a quiescence, lease, prepare, migration, cutover, rollback, release, or handoff receipt.",
        "Every Dolt version and SELECT command ran only in an isolated exact content clone; no Dolt command ran against the live Beads/Dolt store.",
        "The isolated result does not prove that the live direct Dolt adapter is write-free; native direct SELECT may write lock or journal metadata when run against a live store.",
        "The isolated result does not prove live-store quiescence, process absence, descriptor absence, or migration safety.",
    ]
    assert report["result"] == "preflight_observation_only"
    assert report["scope_result"] == "non_consumable_for_quiescence_or_migration"
    assert report["target_execution_allowed"] is False
    assert fixture["marker"].read_bytes() == b"immutable dolt fixture\n"
    assert (fixture["publication_root"] / fixture["output_report"]).is_file()


def test_runtime_receipt_pins_exact_security_and_provenance_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_probe,
        "_utc_now",
        lambda: "2026-07-15T00:00:00Z",
    )
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    runner, _calls = _successful_runtime_runner(fixture)
    report = runtime_probe.probe_runtime(
        fixture["repo_root"],
        fixture["execution_root"],
        fixture["publication_root"],
        fixture["output_report"],
        runner=runner,
    )
    assert set(report) == {
        "adapter",
        "artifact_manifest_sha256",
        "authority",
        "binary_inventory",
        "candidate",
        "captured_at",
        "commands",
        "consumption_policy",
        "environment",
        "filesystem_roots",
        "immutable_observation",
        "isolated_store",
        "limitations",
        "platform",
        "program_id",
        "publication",
        "quiescence",
        "receipt_sha256",
        "result",
        "revision_id",
        "schema_version",
        "scope_result",
        "sealed_v1_archive_manifest_sha256",
        "snapshot",
        "superseded_rc3_manifest_sha256",
        "target_execution_allowed",
    }
    assert report["captured_at"] == "2026-07-15T00:00:00Z"

    assert report["program_id"] == runtime_probe.PROGRAM_ID
    assert report["schema_version"] == "bb.rl.phase5.runtime_preflight_observation.v1"
    assert report["revision_id"] == runtime_probe.REVISION_ID
    assert report["artifact_manifest_sha256"] == RC4_ARTIFACT_MANIFEST_SHA256
    assert (
        report["sealed_v1_archive_manifest_sha256"]
        == SEALED_ARCHIVE_MANIFEST_SHA256
    )
    assert report["superseded_rc3_manifest_sha256"] == RC3_ARTIFACT_MANIFEST_SHA256
    assert report["candidate"] == fixture["candidate_binding"]
    assert report["candidate"]["manifest_sha256"] == report["artifact_manifest_sha256"]
    assert report["binary_inventory"] == fixture["binary_inventory"]
    assert report["environment"] == fixture["environment_binding"]
    assert report["platform"] == fixture["platform_binding"]

    assert report["authority"] == {
        "checkpoint_authority": False,
        "completion_authority": False,
        "cutover_authority": False,
        "migration_authority": False,
        "prior_rc3_authority_reused": False,
        "score_authority": False,
        "selector_authority": False,
        "spec_freeze_authority": False,
        "target_authority": False,
        "zero_authority": True,
    }
    assert report["quiescence"] == {
        "descriptor_scan_executed": False,
        "flock_held": False,
        "journal_opened": False,
        "process_inventory_executed": False,
        "quiesced": False,
    }
    assert report["consumption_policy"] == {
        "consumable": False,
        "prohibited_downstream_receipt_roles": [
            "quiescence_acquisition_receipt",
            "migration_preparation_receipt",
            "migration_commit_receipt",
            "migration_replay_receipt",
            "release_intent_receipt",
            "post_release_receipt",
            "fresh_worker_handoff_receipt",
        ],
        "reason": "no spawn freeze, lease, journal, process inventory, descriptor scan, or quiescence",
    }

    observation = report["immutable_observation"]
    assert observation == {
        "beads_store_after_sha256": observation["live_store_after_sha256"],
        "beads_store_before_sha256": observation["live_store_before_sha256"],
        "candidate_after_sha256": observation["candidate_before_sha256"],
        "candidate_before_sha256": observation["candidate_before_sha256"],
        "candidate_drift": False,
        "clone_after_content_sha256": observation["clone_before_content_sha256"],
        "clone_before_content_sha256": observation["clone_before_content_sha256"],
        "clone_content_drift": False,
        "clone_disposed_before_publication": True,
        "isolated_store_exact_snapshot": True,
        "lineage_after": fixture["lineage_binding"],
        "lineage_before": fixture["lineage_binding"],
        "lineage_drift": False,
        "live_store_after_sha256": observation["live_store_before_sha256"],
        "live_store_before_sha256": observation["live_store_before_sha256"],
        "live_store_content_at_clone_sha256": observation[
            "clone_before_content_sha256"
        ],
        "live_store_drift": False,
        "live_store_pre_context_sha256": observation["live_store_before_sha256"],
        "no_live_dolt_command": True,
        "store_drift": False,
    }
    assert observation["lineage_before"]["root_active_selector"]["sha256"] == (
        runtime_probe.V1_ACTIVE_STATUS_SHA256
    )
    assert observation["lineage_before"]["sealed_v1_archive_manifest"][
        "sha256"
    ] == report["sealed_v1_archive_manifest_sha256"]
    assert observation["lineage_before"]["superseded_rc3_manifest"][
        "sha256"
    ] == report["superseded_rc3_manifest_sha256"]
    assert report["isolated_store"] == {
        "clone_after_content_sha256": observation["clone_before_content_sha256"],
        "clone_before_content_sha256": observation["clone_before_content_sha256"],
        "disposed_before_publication": True,
        "exact_snapshot": True,
        "live_adapter_write_free_behavior_proved": False,
        "live_dolt_command_executed": False,
        "query_repository": "isolated-store://.beads/embeddeddolt/beads",
        "source_content_sha256": observation["clone_before_content_sha256"],
    }

    expected_limitations = [
        "This receipt proves installed adapter discovery and isolated-clone preflight only; it is not a quiescence, lease, prepare, migration, cutover, rollback, release, or handoff receipt.",
        "Every Dolt version and SELECT command ran only in an isolated exact content clone; no Dolt command ran against the live Beads/Dolt store.",
        "The isolated result does not prove that the live direct Dolt adapter is write-free; native direct SELECT may write lock or journal metadata when run against a live store.",
        "The isolated result does not prove live-store quiescence, process absence, descriptor absence, or migration safety.",
        "No process was stopped, signalled, or quiesced; no process-birth or file-descriptor scan was executed.",
        "No advisory lock was acquired and no migration journal was opened or written by this probe.",
        "Read-only observation children were spawned and reaped by the probe, but this receipt does not claim a spawn freeze or prove that unrelated processes were absent.",
        "The live Beads/Dolt tree, immutable candidate, root selector, rc3 manifest, and sealed-v1 archive manifest were content- and full-identity-stable across discovery; session, target, score, and checkpoint stores outside those paths were not opened.",
        "Live-store equality includes every relative path, kind, device, inode, mode, mtime, and regular-file size/content SHA-256; no live mtime normalization is performed.",
        "Clone equality intentionally projects relative path, kind, mode, and regular-file size/content SHA-256 so clone-only inode and mtime churn is non-authoritative.",
        "SQL server mode fails closed because this preflight cannot independently bind its configured endpoint, connected database, socket/DSN, and descriptor identity without contacting a live server.",
        "Platform lock, process-birth, and FD-scan entries record primitive presence only and were not exercised.",
        "DOLT_HASHOF_DB('WORKING') may diverge from clean HEAD/STAGED roots on installed Dolt; the exact value and equality result are recorded, but WORKING-root equality is not used or claimed as a cleanliness or authority invariant.",
    ]
    assert report["limitations"] == expected_limitations
    assert report["result"] == "preflight_observation_only"
    assert report["scope_result"] == "non_consumable_for_quiescence_or_migration"
    assert report["target_execution_allowed"] is False

    commands = report["commands"]
    expected_dolt_query_argv = [
        ["/installed/dolt", "sql", "--result-format", "json", "--query", query]
        for query in (
            runtime_probe.SUMMARY_QUERY,
            runtime_probe.STATUS_QUERY,
            runtime_probe.TABLES_QUERY,
            runtime_probe.SCHEMA_COLUMNS_QUERY,
            runtime_probe.SCHEMA_CONSTRAINTS_QUERY,
            runtime_probe.SCHEMA_INDEXES_QUERY,
            "SELECT * FROM `issues`",
        )
    ]
    assert [command["argv"] for command in commands] == [
        ["/installed/bd", "--version"],
        ["/installed/bd", "context", "--json"],
        ["/installed/dolt", "version"],
        *expected_dolt_query_argv,
    ]
    assert [command["cwd"] for command in commands[:2]] == [
        str(fixture["repo_root"]),
        str(fixture["repo_root"]),
    ]
    assert {
        command["cwd"]
        for command in commands
        if command["binary_path"] == "/installed/dolt"
    } == {"isolated-store://.beads/embeddeddolt/beads"}
    expected_retained_outputs = {
        ("/installed/bd", "--version"): (b"bd version 1.0.5\n", b""),
        ("/installed/bd", "context", "--json"): (
            json.dumps(fixture["context"]).encode("utf-8"),
            b"",
        ),
        ("/installed/dolt", "version"): (b"dolt version 1.75.0\n", b""),
    }
    for command in commands:
        expected_command_keys = {
            "argv",
            "binary_identity",
            "binary_path",
            "command_sha256",
            "cwd",
            "cwd_identity",
            "execution_mode",
            "exit_code",
            "result_sha256",
            "stderr_sha256",
            "stderr_size",
            "stdout_sha256",
            "stdout_size",
            "used_binary_identity",
            "used_cwd_identity",
        }
        if command["argv"] in (
            ["/installed/bd", "--version"],
            ["/installed/bd", "context", "--json"],
            ["/installed/dolt", "version"],
        ):
            expected_command_keys.update({"stderr_base64", "stdout_base64"})
        assert set(command) == expected_command_keys
        assert command["execution_mode"] == "injected_non_native_test_seam"
        assert command["used_binary_identity"] is None
        assert command["used_cwd_identity"] is None
        expected_output = expected_retained_outputs.get(tuple(command["argv"]))
        if expected_output is None:
            assert "stdout_base64" not in command
            assert "stderr_base64" not in command
        else:
            _assert_retained_output_binding(
                command,
                stdout=expected_output[0],
                stderr=expected_output[1],
            )
        inventory_name = Path(command["binary_path"]).name
        inventory = report["binary_inventory"][inventory_name]
        assert command["binary_identity"] == {
            "device": inventory["device"],
            "inode": inventory["inode"],
            "mode": inventory["mode"],
            "mtime_ns": inventory["mtime_ns"],
            "path": inventory["resolved_path"],
            "sha256": inventory["sha256"],
            "size": inventory["size"],
        }
        assert command["binary_identity"]["path"] == inventory["resolved_path"]
        assert command["binary_identity"]["sha256"] == inventory["sha256"]
        assert command["command_sha256"] == runtime_probe._sha256(
            runtime_probe._canonical_bytes(
                {"argv": command["argv"], "cwd": command["cwd"]}
            )
        )
        result_projection = {
            key: command[key]
            for key in (
                "argv",
                "binary_identity",
                "binary_path",
                "cwd",
                "cwd_identity",
                "execution_mode",
                "exit_code",
                "stderr_sha256",
                "stderr_size",
                "stdout_sha256",
                "stdout_size",
                "used_binary_identity",
                "used_cwd_identity",
            )
        }
        assert command["result_sha256"] == runtime_probe._sha256(
            runtime_probe._canonical_bytes(result_projection)
        )
    expected_adapter_discovery = {
        "adapter_kind": "embedded_dolt_cli",
        "bd_context_result_sha256": commands[1]["result_sha256"],
        "bd_binary_sha256": report["binary_inventory"]["bd"]["sha256"],
        "environment": fixture["environment_binding"],
        "isolated_query_cwd": "isolated-store://.beads/embeddeddolt/beads",
        "server_endpoint": None,
    }
    assert report["adapter"] == {
        "adapter_kind": "embedded_dolt_cli",
        "bd_version": "1.0.5",
        "branch": "main",
        "database": "beads",
        "discovery_evidence_sha256": runtime_probe._sha256(
            runtime_probe._canonical_bytes(expected_adapter_discovery)
        ),
        "dolt_version": "1.75.0",
        "isolated_query_repository": "isolated-store://.beads/embeddeddolt/beads",
        "mode": "direct",
        "repository_path": str(fixture["repository"]),
        "selection_rationale": (
            "bd context reported one direct/embedded mode with no server or "
            "environment endpoint evidence; the live repository was resolved "
            "without a Dolt command and every Dolt command observed an isolated "
            "exact clone"
        ),
        "server_socket_or_dsn": None,
        "store_root": str(fixture["beads_dir"]),
    }

    query_results = {
        command["argv"][-1]: command["result_sha256"]
        for command in commands
        if command["argv"][:5]
        == ["/installed/dolt", "sql", "--result-format", "json", "--query"]
    }
    rows_sha256 = runtime_probe._sha256(
        runtime_probe._canonical_bytes({"id": "bb-test"})
    )
    table_receipts = [
        {
            "command_result_sha256": query_results["SELECT * FROM `issues`"],
            "row_count": 1,
            "rows_sha256": rows_sha256,
            "table": "issues",
        }
    ]
    schema_command_result_sha256s = {
        "columns": query_results[runtime_probe.SCHEMA_COLUMNS_QUERY],
        "constraints": query_results[runtime_probe.SCHEMA_CONSTRAINTS_QUERY],
        "indexes": query_results[runtime_probe.SCHEMA_INDEXES_QUERY],
    }
    schema_parts = {
        "columns": [{"column_name": "id", "table_name": "issues"}],
        "constraints": [],
        "indexes": [],
    }
    snapshot = report["snapshot"]
    assert snapshot == {
        "adapter_kind": "embedded_dolt_cli",
        "branch": "main",
        "canonical_rows_sha256": runtime_probe._sha256(
            runtime_probe._canonical_bytes(
                [{"row_count": 1, "rows_sha256": rows_sha256, "table": "issues"}]
            )
        ),
        "clean": True,
        "clean_invariant": "empty_dolt_status_and_head_root_equals_staged_root",
        "database": "beads",
        "head_commit": "a" * 32,
        "head_root": "b" * 32,
        "head_root_equals_staged_root": True,
        "observation_repository": "isolated-store://.beads/embeddeddolt/beads",
        "repository_path": str(fixture["repository"]),
        "schema_command_result_sha256s": schema_command_result_sha256s,
        "schema_sha256": runtime_probe._sha256(
            runtime_probe._canonical_bytes(schema_parts)
        ),
        "status_command_result_sha256": query_results[runtime_probe.STATUS_QUERY],
        "store_root": str(fixture["beads_dir"]),
        "staged_root": "b" * 32,
        "summary_command_result_sha256": query_results[runtime_probe.SUMMARY_QUERY],
        "table_inventory_command_result_sha256": query_results[
            runtime_probe.TABLES_QUERY
        ],
        "table_receipts": table_receipts,
        "working_root": "c" * 32,
        "working_root_diverged_from_head": True,
        "working_root_equality_claimed": False,
    }

    publication = report["publication"]
    output = fixture["publication_root"] / fixture["output_report"]
    assert publication == {
        "output_basename": fixture["output_report"].name,
        "output_path": str(output),
        "publication_root": publication["publication_root"],
        "root_disjoint_from_repo_and_execution": True,
    }
    assert publication["publication_root"] == {
        "device": fixture["publication_root"].stat().st_dev,
        "inode": fixture["publication_root"].stat().st_ino,
        "mode": "0755",
        "path": str(fixture["publication_root"]),
    }

    def expected_root_identity(kind: str, path: Path) -> dict[str, Any]:
        root_metadata = path.stat()
        return {
            "device": root_metadata.st_dev,
            "inode": root_metadata.st_ino,
            "kind": kind,
            "mode": f"{root_metadata.st_mode & 0o7777:04o}",
            "path": str(path),
        }

    assert report["filesystem_roots"] == [
        expected_root_identity("repo_root", fixture["repo_root"]),
        expected_root_identity("execution_root", fixture["execution_root"]),
        expected_root_identity("candidate_root", fixture["candidate"]),
        expected_root_identity("beads_store_root", fixture["beads_dir"]),
        expected_root_identity("dolt_repository", fixture["repository"]),
        expected_root_identity(
            "runtime_preflight_publication_root",
            fixture["publication_root"],
        ),
    ]
    assert output.read_bytes() == runtime_probe._canonical_bytes(report)
    assert output.stat().st_mode & 0o7777 == 0o444

    receipt_sha256 = report["receipt_sha256"]
    report_without_self_hash = {
        key: value for key, value in report.items() if key != "receipt_sha256"
    }
    assert receipt_sha256 == runtime_probe._sha256(
        runtime_probe._canonical_bytes(report_without_self_hash)
    )


def test_runtime_probe_rejects_sql_server_context_without_contact_or_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    fixture["context"].update(
        {
            "dolt_mode": "sql_server",
            "server_host": "127.0.0.1",
            "server_port": 3306,
        }
    )
    runner, calls = _successful_runtime_runner(fixture)

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match=(
            "sql_server is non-conformant for this preflight because endpoint, "
            "connected database, socket/DSN, and descriptor identity are not "
            "independently proven"
        ),
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert [argv for argv, _cwd in calls] == [
        ("/installed/bd", "--version"),
        ("/installed/bd", "context", "--json"),
    ]
    assert all("sql" not in argv for argv, _cwd in calls)
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_runtime_probe_rejects_non_singleton_summary_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    successful_runner, calls = _successful_runtime_runner(fixture)

    def runner(argv: tuple[str, ...], cwd: Path) -> runtime_probe.CommandResult:
        result = successful_runner(argv, cwd)
        if argv[:2] == ("/installed/dolt", "sql") and argv[-1] == runtime_probe.SUMMARY_QUERY:
            return runtime_probe.CommandResult(
                argv=result.argv,
                cwd=result.cwd,
                binary_path=result.binary_path,
                exit_code=result.exit_code,
                stdout=b'{"rows": []}\n',
                stderr=result.stderr,
            )
        return result

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="summary query did not return exactly one row",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert any(argv[:2] == ("/installed/dolt", "sql") for argv, _cwd in calls)
    assert all(
        not (argv[0] == "/installed/bd" and "sql" in argv[1:])
        for argv, _cwd in calls
    )
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_runtime_probe_rejects_live_store_mutation_during_bd_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    live_inode_before = fixture["marker"].stat().st_ino
    successful_runner, calls = _successful_runtime_runner(fixture)

    def runner(argv: tuple[str, ...], cwd: Path) -> runtime_probe.CommandResult:
        result = successful_runner(argv, cwd)
        if argv == ("/installed/bd", "context", "--json"):
            replacement = fixture["marker"].with_suffix(".context-replacement")
            replacement.write_bytes(fixture["marker"].read_bytes())
            replacement.chmod(fixture["marker"].stat().st_mode & 0o7777)
            os.replace(replacement, fixture["marker"])
        return result

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="live Beads/Dolt store drifted during bd version/context discovery",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert fixture["marker"].stat().st_ino != live_inode_before
    assert all(argv[0] != "/installed/dolt" for argv, _cwd in calls)
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_runtime_probe_rejects_same_inode_live_content_drift_before_dolt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    marker = fixture["marker"]
    original_bytes = marker.read_bytes()
    before = marker.stat()
    drifted_bytes = b"different dolt fixture\n"
    assert drifted_bytes != original_bytes
    assert len(drifted_bytes) == len(original_bytes)
    successful_runner, calls = _successful_runtime_runner(fixture)

    def runner(argv: tuple[str, ...], cwd: Path) -> runtime_probe.CommandResult:
        result = successful_runner(argv, cwd)
        if argv == ("/installed/bd", "context", "--json"):
            marker.write_bytes(drifted_bytes)
            os.utime(marker, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="live Beads/Dolt store drifted during bd version/context discovery",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    after = marker.stat()
    assert (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_atime_ns,
        after.st_mtime_ns,
    ) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_atime_ns,
        before.st_mtime_ns,
    )
    assert marker.read_bytes() == drifted_bytes
    assert runtime_probe._sha256(drifted_bytes) != runtime_probe._sha256(original_bytes)
    assert all(argv[0] != "/installed/dolt" for argv, _cwd in calls)
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_runtime_probe_rejects_live_source_hardlink_before_dolt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    hardlink = fixture["marker"].with_name("manifest-hardlink")
    os.link(fixture["marker"], hardlink)
    runner, calls = _successful_runtime_runner(fixture)

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="hardlink alias in confined observed tree",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert fixture["marker"].stat().st_nlink == 2
    assert hardlink.stat().st_ino == fixture["marker"].stat().st_ino
    assert all(argv[0] != "/installed/dolt" for argv, _cwd in calls)
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_runtime_probe_rejects_live_store_identity_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    live_inode_before = fixture["marker"].stat().st_ino
    mutated = False

    def replace_live_file(_argv: tuple[str, ...], _cwd: Path) -> None:
        nonlocal mutated
        if mutated:
            return
        replacement = fixture["marker"].with_suffix(".replacement")
        replacement.write_bytes(fixture["marker"].read_bytes())
        replacement.chmod(fixture["marker"].stat().st_mode & 0o7777)
        os.replace(replacement, fixture["marker"])
        mutated = True

    runner, _calls = _successful_runtime_runner(fixture, on_dolt=replace_live_file)
    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="live Beads/Dolt store drifted",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert mutated is True
    assert fixture["marker"].stat().st_ino != live_inode_before
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_runtime_probe_rejects_isolated_clone_content_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    mutated_clone: list[Path] = []

    def mutate_clone(_argv: tuple[str, ...], cwd: Path) -> None:
        if mutated_clone:
            return
        clone_marker = cwd / ".dolt" / "manifest"
        clone_marker.write_bytes(b"drifted clone bytes\n")
        mutated_clone.append(clone_marker)

    runner, _calls = _successful_runtime_runner(fixture, on_dolt=mutate_clone)
    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="isolated clone content drifted",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert mutated_clone
    assert fixture["marker"].read_bytes() == b"immutable dolt fixture\n"
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


def test_runtime_probe_rejects_clone_that_does_not_match_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _runtime_probe_fixture(tmp_path, monkeypatch)
    real_clone_tree = runtime_probe._clone_tree

    def mismatching_clone(
        source: Path,
        destination: Path,
        *,
        destination_parent_descriptor: int | None = None,
    ) -> Path:
        clone = real_clone_tree(
            source,
            destination,
            destination_parent_descriptor=destination_parent_descriptor,
        )
        (clone / "embeddeddolt" / "beads" / ".dolt" / "manifest").write_bytes(
            b"post-copy mismatch\n"
        )
        return clone

    monkeypatch.setattr(runtime_probe, "_clone_tree", mismatching_clone)
    runner, calls = _successful_runtime_runner(fixture)
    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="isolated clone does not exactly match source",
    ):
        runtime_probe.probe_runtime(
            fixture["repo_root"],
            fixture["execution_root"],
            fixture["publication_root"],
            fixture["output_report"],
            runner=runner,
        )

    assert all(argv[0] != "/installed/dolt" for argv, _cwd in calls)
    assert not (fixture["publication_root"] / fixture["output_report"]).exists()


@pytest.mark.parametrize("entry_kind", ["symlink", "fifo"])
def test_clone_tree_rejects_source_symlink_and_special_file(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    source = tmp_path / "source"
    destination_parent = tmp_path / "destination"
    source.mkdir()
    destination_parent.mkdir()
    regular = source / "regular"
    regular.write_bytes(b"source\n")
    forbidden = source / "forbidden"
    if entry_kind == "symlink":
        forbidden.symlink_to(regular)
    else:
        os.mkfifo(forbidden)

    with pytest.raises(
        runtime_probe.RuntimeProbeError,
        match="clone source contains symlink or special file",
    ):
        runtime_probe._clone_tree(source, destination_parent / "clone")

    assert not (destination_parent / "clone" / "forbidden").exists()


def test_clone_content_snapshot_ignores_only_identity_and_mtime_churn(
    tmp_path: Path,
) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    payload = clone / "manifest"
    payload.write_bytes(b"same clone content\n")
    payload.chmod(0o640)
    full_before = runtime_probe._tree_snapshot(clone)
    content_before = runtime_probe._tree_snapshot(clone, content_only=True)
    inode_before = payload.stat().st_ino
    replacement = clone / "replacement"
    replacement.write_bytes(payload.read_bytes())
    replacement.chmod(payload.stat().st_mode & 0o7777)
    os.replace(replacement, payload)
    os.utime(payload, ns=(payload.stat().st_atime_ns, payload.stat().st_mtime_ns + 1))

    assert payload.stat().st_ino != inode_before
    assert runtime_probe._tree_snapshot(clone) != full_before
    assert runtime_probe._tree_snapshot(clone, content_only=True) == content_before

    payload.chmod(0o600)
    assert runtime_probe._tree_snapshot(clone, content_only=True) != content_before


@pytest.mark.parametrize(
    "fault",
    (
        "write",
        "first_file_fsync",
        "second_post_chmod_file_fsync",
        "fchmod",
        "parent_fsync",
    ),
)
def test_immutable_publication_removes_output_after_post_create_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    publication_root = tmp_path / "publication"
    publication_root.mkdir()
    metadata = publication_root.stat()
    expected_root_identity = {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": f"{metadata.st_mode & 0o7777:04o}",
        "path": str(publication_root),
    }
    output_name = f"fault-{fault}.json"
    output = publication_root / output_name
    original_fsync = runtime_probe.os.fsync
    injected: list[str] = []
    file_fsync_calls = 0

    if fault == "write":

        def failing_write(descriptor: int, payload: Any) -> int:
            injected.append(fault)
            raise OSError(f"injected {fault}")

        monkeypatch.setattr(runtime_probe.os, "write", failing_write)
    elif fault == "fchmod":

        def failing_fchmod(descriptor: int, mode: int) -> None:
            injected.append(fault)
            raise OSError(f"injected {fault}")

        monkeypatch.setattr(runtime_probe.os, "fchmod", failing_fchmod)
    else:

        def failing_fsync(descriptor: int) -> None:
            nonlocal file_fsync_calls
            descriptor_is_directory = (
                os.fstat(descriptor).st_mode & 0o170000
            ) == 0o040000
            if not descriptor_is_directory:
                file_fsync_calls += 1
            should_fail = (
                fault == "parent_fsync" and descriptor_is_directory
            ) or (
                fault == "first_file_fsync"
                and not descriptor_is_directory
                and file_fsync_calls == 1
            ) or (
                fault == "second_post_chmod_file_fsync"
                and not descriptor_is_directory
                and file_fsync_calls == 2
            )
            if should_fail and not injected:
                injected.append(fault)
                raise OSError(f"injected {fault}")
            original_fsync(descriptor)

        monkeypatch.setattr(runtime_probe.os, "fsync", failing_fsync)

    with pytest.raises(OSError, match=f"injected {fault}"):
        runtime_probe._write_immutable_report(
            publication_root,
            output_name,
            {"result": "must-not-survive"},
            expected_root_identity,
        )

    assert injected == [fault]
    assert not output.exists()
