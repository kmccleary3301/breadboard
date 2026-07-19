from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path
from typing import Any

PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
REVISION_ID = "v2.0.0-rc5-20260717"
SUPERSEDED_REVISION_ID = "v2.0.0-rc4-20260715"
ARCHIVE_ID = "v1-bootstrap-20260709-sealed-rc3"
ARTIFACT_MANIFEST_SHA256 = (
    "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
)
SUPERSEDED_ARTIFACT_MANIFEST_SHA256 = (
    "sha256:b5897c0465bfb0cdf4b3aa79427c55e85b8a1d0b600c40e6d6eb62b579e9cbfd"
)
ARCHIVE_MANIFEST_SHA256 = (
    "sha256:91519465cfc7a45d8a6375a23908753f48bf61f2d3e90f7734f20affee2ca2d8"
)
V1_ACTIVE_SHA256 = "sha256:bec45628402972644a24f1c11f80024e8780eb2c6817d90a45d3cd19a94928b6"
V1_SCORECARD_SHA256 = "sha256:df8e69a610b7ba69237642ff7a49d42fb1819ae919be224e4a1399b246542a23"
BUILD_REPORT_SHA256 = "sha256:73bd2d011fbc83ad7d5081cef8c433222cf33166b3624377abf96d3d5450a2b5"
FRESH_WORKER_REPLAY_SHA256 = "sha256:f4ac821918908d2e1ab89f04d3a095908517dc27aa24d1dd255bc1121a39199f"
SAFETY_REPORT_SHA256 = "sha256:3e1d213e9686f1fa054cf4a576d96a5785a907afcad7e08cd519889c5850d88a"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value




def check_manifest(root: Path, manifest: dict[str, Any]) -> list[str]:
    checked: list[str] = []
    for row in manifest["files"]:
        path = root / row["path"]
        if not path.is_file():
            raise ValueError(f"manifest file missing: {path}")
        if path.stat().st_size != row["size"]:
            raise ValueError(f"manifest size mismatch: {path}")
        if sha256_file(path) != row["sha256"]:
            raise ValueError(f"manifest hash mismatch: {path}")
        actual_mode = f"{stat.S_IMODE(path.stat().st_mode):04o}"
        if actual_mode != row["mode"]:
            raise ValueError(f"manifest mode mismatch: {path}: {actual_mode} != {row['mode']}")
        checked.append(row["path"])
    expected = sorted(path.name for path in root.iterdir() if path.is_file() and path.name != "ARTIFACT_MANIFEST.json")
    if sorted(checked) != expected:
        raise ValueError("artifact manifest file set does not equal revision file set")
    return checked


def check_archive(root: Path, manifest: dict[str, Any]) -> list[str]:
    checked: list[str] = []
    for row in manifest["files"]:
        path = root / row["path"]
        if not path.is_file() or path.stat().st_size != row["size"] or sha256_file(path) != row["sha256"]:
            raise ValueError(f"v1 archive mismatch: {path}")
        actual_mode = f"{stat.S_IMODE(path.stat().st_mode):04o}"
        if actual_mode != row["mode"] or actual_mode != "0444":
            raise ValueError(
                f"v1 archive mode mismatch: {path}: {actual_mode} != {row['mode']}"
            )
        checked.append(row["path"])
    actual = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "ARCHIVE_MANIFEST.json"
    )
    if sorted(checked) != actual:
        raise ValueError("v1 archive manifest file set mismatch")
    manifest_mode = f"{stat.S_IMODE((root / 'ARCHIVE_MANIFEST.json').stat().st_mode):04o}"
    if manifest_mode != "0444":
        raise ValueError("v1 archive manifest is not read-only")
    if manifest["original_active_status_sha256"] != V1_ACTIVE_SHA256:
        raise ValueError("v1 active selector digest changed")
    if manifest["original_scorecard_sha256"] != V1_SCORECARD_SHA256:
        raise ValueError("v1 scorecard digest changed")
    if sha256_file(root / "ACTIVE_STATUS.json") != V1_ACTIVE_SHA256:
        raise ValueError("archived v1 active selector bytes changed")
    if sha256_file(root / "SCORECARD.json") != V1_SCORECARD_SHA256:
        raise ValueError("archived v1 scorecard bytes changed")
    return checked


def topo_sort(nodes: list[dict[str, Any]]) -> list[str]:
    by_id = {node["id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("duplicate DAG node")
    remaining = {node_id: set(node["depends_on"]) for node_id, node in by_id.items()}
    for node_id, dependencies in remaining.items():
        missing = dependencies - set(by_id)
        if missing:
            raise ValueError(f"DAG node {node_id} has missing dependencies {sorted(missing)}")
    ordered: list[str] = []
    while remaining:
        ready = sorted(node_id for node_id, dependencies in remaining.items() if not dependencies)
        if not ready:
            raise ValueError(f"DAG cycle: {remaining}")
        for node_id in ready:
            ordered.append(node_id)
            remaining.pop(node_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return ordered


def _require_exact_fields(actual: Any, expected: set[str], label: str) -> None:
    if not isinstance(actual, list) or any(
        not isinstance(field, str) for field in actual
    ):
        raise ValueError(f"{label} fields are not a string list")
    actual_set = set(actual)
    if len(actual) != len(actual_set) or actual_set != expected:
        missing = sorted(expected - actual_set)
        extra = sorted(actual_set - expected)
        raise ValueError(f"{label} fields changed: missing={missing}, extra={extra}")


def _contains_in_order(value: Any, fragments: tuple[str, ...]) -> bool:
    if not isinstance(value, str):
        return False
    cursor = -1
    for fragment in fragments:
        cursor = value.find(fragment, cursor + 1)
        if cursor == -1:
            return False
    return True


def validate_candidate_migration_contracts(
    manifest: dict[str, Any],
    migration: dict[str, Any],
    transaction: dict[str, Any],
    quiescence: dict[str, Any],
    session_handoff: dict[str, Any],
    migration_replay: dict[str, Any],
    handoff: dict[str, Any],
    superseded_manifest_sha256: str,
) -> set[str]:
    contracts = (
        migration,
        transaction,
        quiescence,
        session_handoff,
        migration_replay,
        handoff,
    )
    if any(
        contract.get("program_id") != PROGRAM_ID
        or contract.get("revision_id", REVISION_ID) != REVISION_ID
        for contract in contracts
    ):
        raise ValueError("rc5 migration contract program or revision identity changed")
    if (
        superseded_manifest_sha256 != SUPERSEDED_ARTIFACT_MANIFEST_SHA256
        or manifest.get("superseded_artifact_manifest_sha256")
        != SUPERSEDED_ARTIFACT_MANIFEST_SHA256
        or manifest.get("superseded_revision_id") != SUPERSEDED_REVISION_ID
        or manifest.get("supersession_scope")
        != "migration and cutover mechanics only; prior rc4 SPEC_FREEZE grants no rc5 authority"
    ):
        raise ValueError("rc5 manifest does not supersede the exact rc4 manifest and scope")
    superseded = migration.get("superseded_rc4", {})
    if (
        superseded.get("artifact_manifest_sha256")
        != SUPERSEDED_ARTIFACT_MANIFEST_SHA256
        or superseded.get("revision_id") != SUPERSEDED_REVISION_ID
        or superseded.get("supersession_scope")
        != "migration and cutover mechanics only"
        or superseded.get("spec_freeze_grants_rc5_authority") is not False
        or not any(
            isinstance(nonclaim, str)
            and "prior rc4 SPEC_FREEZE grants no rc5 authority" in nonclaim
            for nonclaim in migration.get("nonclaims", [])
        )
    ):
        raise ValueError("rc4 SPEC_FREEZE authority was reused for rc5")

    expected_store_order = [
        "v2_event_log",
        "beads_projection",
        "root_active_selector",
    ]
    store_rows = transaction.get("stores", [])
    store_ids = [store.get("id") for store in store_rows]
    if len(store_ids) != 3 or set(store_ids) != set(expected_store_order):
        raise ValueError(
            "migration transaction must contain exactly three stores; session is not a store"
        )
    commit_order = transaction.get("commit_order")
    if (
        not isinstance(commit_order, list)
        or len(commit_order) != 3
        or commit_order[-1] != "root_active_selector"
    ):
        raise ValueError("root active selector must be the third and final store commit")
    if commit_order != expected_store_order or store_ids != expected_store_order:
        raise ValueError("three-store migration commit order changed")
    if (
        transaction.get("mode")
        != "stop_the_world_three_store_compensating_transaction"
        or any("session" in store_id for store_id in store_ids)
        or transaction.get("receipt_required", {})
        .get("session_fields", {})
        .get("location")
        != "outside stores and commit_order"
        or "outside transaction stores"
        not in session_handoff.get("handoff_model", {}).get("session_store_role", "")
    ):
        raise ValueError("session handoff must remain outside transaction stores")

    locking = transaction.get("locking", {})
    if "consumer_barrier_scope" in locking or "consumer_read_policy" in locking:
        raise ValueError("rc4 cannot claim a MIGRATION_IN_PROGRESS reader barrier")
    if (
        "MIGRATION_IN_PROGRESS" in json.dumps(transaction, sort_keys=True)
        or quiescence.get("client_behavior", {}).get("domain_error_claimed") is not False
        or quiescence.get("client_behavior", {}).get("forbidden_claim")
        != "ordinary readers receive MIGRATION_IN_PROGRESS"
        or "clients remain stopped"
        not in locking.get("client_policy", "")
        or "no domain-error behavior is claimed"
        not in locking.get("client_policy", "")
        or quiescence.get("mode")
        != "out_of_band_supervisor_owned_stop_the_world"
    ):
        raise ValueError(
            "clients must be stopped out of band with no MIGRATION_IN_PROGRESS claim"
        )
    if set(locking.get("lease_scope", [])) != {
        "root_active_selector",
        "beads_projection",
        "v2_event_log",
        "program_root",
        "beads_data_directory",
    }:
        raise ValueError("migration lease scope does not cover stores and their roots")
    if (
        "stable-inode held-flock lease"
        not in locking.get("rule", "")
        or "durable append-only per-step" not in locking.get("journal", "")
        or "pid, process group, process-start identity, stable lock inode"
        not in locking.get("owner", "")
    ):
        raise ValueError("migration lacks a continuous stable-inode lock or durable journal")

    expected_receipt_production_order = [
        "pre_replay_inputs_complete",
        "migration_and_fresh_worker_replay_receipts_complete",
        "quiescence_release_intent_receipt_complete",
        "lease_released_and_file_descriptor_closed",
        "quiescence_post_release_receipt_complete",
        "session_post_handoff_receipt_complete",
        "migration_transaction_receipt_complete",
    ]
    if transaction.get("receipt_production_order") != expected_receipt_production_order:
        raise ValueError("migration receipt production order is cyclic or incomplete")

    transaction_receipt = transaction.get("receipt_required", {})
    if (
        transaction_receipt.get("additional_fields_allowed") is not False
        or "no rc4 authority carries forward"
        not in transaction_receipt.get("authority", "")
    ):
        raise ValueError("migration receipt can carry undeclared or rc4 authority")
    if transaction_receipt.get("emission") != (
        "emit and fsync this final immutable summary only after replay, "
        "release-intent, lease release, post-release, and session post-handoff "
        "receipts exist"
    ):
        raise ValueError("migration transaction receipt can precede its dependencies")
    _require_exact_fields(
        transaction_receipt.get("fields", []),
        {
            "migration_id",
            "lease_id",
            "quiescence_receipt_sha256",
            "session_pre_handoff_receipt_sha256",
            "dolt_adapter_kind",
            "migration_journal_sha256",
            "migration_journal_final_sequence",
            "authority_decision_sha256",
            "before_images",
            "after_images",
            "prepared_validation_sha256",
            "commit_results",
            "post_commit_hashes",
            "migration_replay_sha256",
            "fresh_worker_replay_sha256",
            "quiescence_release_intent_receipt_sha256",
            "quiescence_post_release_receipt_sha256",
            "session_post_handoff_receipt_sha256",
            "released_lease",
        },
        "migration transaction receipt",
    )
    _require_exact_fields(
        transaction_receipt.get("store_fields", []),
        {
            "store_id",
            "presence",
            "parent_device",
            "parent_inode",
            "native_adapter",
            "native_revision",
            "bytes_sha256",
            "size",
            "schema_sha256",
            "canonical_rows_sha256",
            "rollback_operation_sha256",
            "reversible",
            "rollback_invariant",
        },
        "migration store receipt",
    )
    _require_exact_fields(
        transaction_receipt.get("failure_fields", []),
        {
            "failed_phase",
            "failed_store",
            "error",
            "rollback_results",
            "rollback_invariant_results",
            "rollback_post_state_hashes",
            "event_compensation_sha256",
            "rollback_session_handoff_sha256",
            "quarantined",
        },
        "migration failure receipt",
    )

    receipt_contract = quiescence.get("receipt_contract", {})
    _require_exact_fields(
        receipt_contract.get("required_fields", []),
        {
            "migration_id",
            "supervisor_identity",
            "platform_adapter",
            "lease",
            "journal",
            "breadboard_processes",
            "bd_dolt_processes",
            "omp_rpc_session",
            "closed_transcript",
            "child_transcript_manifest",
            "prior_todo_projection",
            "dolt_adapter",
            "dolt_snapshot",
            "filesystem_roots",
            "descriptor_scans",
            "inventory_sha256",
            "quiesced_at",
            "result",
        },
        "quiescence receipt",
    )
    _require_exact_fields(
        receipt_contract.get("lease_fields", []),
        {
            "lease_id",
            "migration_id",
            "path",
            "device",
            "inode",
            "holder_pid",
            "holder_pgid",
            "holder_start_identity",
            "os",
            "adapter_kind",
            "program_root",
            "beads_data_directory",
            "provisioned_with_exclusive_create",
            "flock_held",
            "acquired_at",
        },
        "lease",
    )
    _require_exact_fields(
        receipt_contract.get("journal_fields", []),
        {
            "path",
            "device",
            "inode",
            "opened_at",
            "sha256",
            "fsynced_through_sequence",
        },
        "journal",
    )
    _require_exact_fields(
        receipt_contract.get("dolt_adapter_fields", []),
        {
            "adapter_kind",
            "discovery_evidence_sha256",
            "bd_version",
            "dolt_version",
            "mode",
            "store_root",
            "repository_path",
            "database",
            "branch",
            "server_socket_or_dsn",
        },
        "Dolt adapter",
    )
    _require_exact_fields(
        receipt_contract.get("process_entry_fields", []),
        {
            "kind",
            "adapter",
            "pid",
            "ppid",
            "pgid",
            "sid",
            "uid",
            "start_identity",
            "executable",
            "argv_sha256",
            "cwd",
            "root_or_datadir",
            "discovered_at",
            "identity_revalidated_at",
            "stop_method",
            "stopped_at",
            "exit_status",
        },
        "process inventory",
    )
    _require_exact_fields(
        receipt_contract.get("descriptor_scan_fields", []),
        {
            "platform_adapter",
            "backend",
            "root",
            "started_at",
            "completed_at",
            "process_snapshot_sha256",
            "targets",
            "coverage",
            "permission_errors",
            "result",
        },
        "descriptor scan",
    )
    _require_exact_fields(
        receipt_contract.get("descriptor_target_fields", []),
        {
            "pid",
            "start_identity",
            "descriptor",
            "kind",
            "device",
            "inode",
            "resolved_path",
            "socket_target",
        },
        "descriptor target",
    )
    _require_exact_fields(
        receipt_contract.get("omp_rpc_session_fields", []),
        {
            "session_id",
            "pid",
            "ppid",
            "pgid",
            "sid",
            "uid",
            "start_identity",
            "cwd",
            "state_before_abort",
            "state_after_abort",
            "abort_sent",
            "flush_outcome",
            "forced_or_timeout_kill",
            "process_exit_status",
            "reaped",
            "commit_prohibited",
        },
        "OMP/RPC process",
    )
    _require_exact_fields(
        receipt_contract.get("closed_transcript_fields", []),
        {
            "session_id",
            "cwd",
            "path",
            "size",
            "sha256",
            "title_slot_size",
            "title_slot_sha256",
            "session_header_sha256",
            "final_cursor",
            "final_event_sha256",
            "final_nonempty_record_sha256",
            "flush_outcome",
            "supervisor_fsynced_file",
            "supervisor_fsynced_parent",
            "stability_observations_sha256",
            "open_fd_count",
            "closed_after_process_exit",
            "snapshot_kind",
        },
        "closed transcript",
    )

    discovery = quiescence.get("adapter_discovery", {})
    if discovery.get("allowed_dolt_adapters") != [
        "embedded_dolt_cli",
        "sql_server",
    ]:
        raise ValueError("runtime adapter set must be embedded_dolt_cli or sql_server")
    if (
        "ambiguous" not in discovery.get("fail_closed", "")
        or "runtime-discovers exactly one adapter"
        not in locking.get("client_policy", "")
        and "runtime-discovers exactly one adapter"
        not in transaction["stores"][1].get("prepare", "")
        or "receipt-selected native adapter"
        not in transaction["stores"][1].get("commit", "")
        or "embedded_dolt_cli or sql_server"
        not in " ".join(migration_replay.get("replay_requirements", []))
    ):
        raise ValueError("runtime adapter discovery is ambiguous or hardcoded")

    flush_rules = receipt_contract.get("flush_outcome_rules", {})
    close_sequence = (
        quiescence.get("native_observations", {})
        .get("omp_rpc", {})
        .get("close_sequence", [])
    )
    if (
        set(flush_rules.get("success_only", {}).get("allowed", []))
        != {"native_ack", "graceful_process_exit"}
        or set(flush_rules.get("failure_only", {}).get("allowed", []))
        != {"forced_or_timeout_kill_without_flush"}
        or set(flush_rules.get("failure_only", {}).get("invariants", []))
        != {
            "result is fail",
            "forced_or_timeout_kill is true",
            "commit_prohibited is true",
        }
        or "forced_or_timeout_kill requires result fail and commit_prohibited true"
        not in receipt_contract.get("acquisition_receipt_invariants", [])
        or not isinstance(close_sequence, list)
        or not close_sequence
        or "forced or timeout kill" not in close_sequence[-1]
    ):
        raise ValueError("forced or timeout kill cannot produce successful quiescence")

    release_contracts = receipt_contract.get("release_receipt_contracts", {})
    release_intent = release_contracts.get("release_intent_receipt", {})
    post_release = release_contracts.get("post_release_receipt", {})
    _require_exact_fields(
        release_intent.get("required_fields", []),
        {
            "migration_id",
            "acquisition_receipt_sha256",
            "lease_id",
            "lease_device",
            "lease_inode",
            "journal_final_sequence",
            "journal_sha256",
            "verified_stores_sha256",
            "migration_replay_sha256",
            "zero_authority",
            "release_intent_at",
            "flock_held",
            "receipt_sha256",
        },
        "release-intent receipt",
    )
    _require_exact_fields(
        post_release.get("required_fields", []),
        {
            "migration_id",
            "release_intent_receipt_sha256",
            "lease_id",
            "lease_device",
            "lease_inode",
            "flock_released_at",
            "file_descriptor_closed",
            "post_release_journal_sha256",
            "receipt_sha256",
        },
        "post-release receipt",
    )
    release_rule = locking.get("release", "")
    if (
        not _contains_in_order(
            release_rule,
            (
                "release-intent receipt while held",
                "release the stable-inode flock and close its file descriptor",
                "post-release receipt",
                "before any fresh OMP/RPC session",
            ),
        )
        or "flock_released_at" in release_intent.get("required_fields", [])
        or "file_descriptor_closed" in release_intent.get("required_fields", [])
    ):
        raise ValueError("release receipt claims future release facts")

    stores = {store["id"]: store for store in store_rows}
    event_store = stores["v2_event_log"]
    event_absent = event_store.get("before_state", {}).get("absent", "")
    if (
        not all(
            fragment in event_absent
            for fragment in (
                "presence=absent",
                "bytes_sha256=null",
                "size=null",
                "before_head_sha256=null",
                "before_event_count=0",
            )
        )
        or "never synthesize an empty before-image"
        not in event_store.get("prepare", "")
        or "O_CREAT|O_EXCL" not in event_store.get("commit", "")
        or "predecessor-null genesis" not in event_store.get("commit", "")
        or "recheck absence" not in event_store.get("commit", "")
    ):
        raise ValueError("absent event log must remain absent until exclusive genesis creation")
    _require_exact_fields(
        transaction.get("receipt_required", {}).get("event_store_fields", []),
        {
            "store_id",
            "before_presence",
            "before_head_sha256",
            "before_event_count",
            "parent_device",
            "parent_inode",
            "absence_rechecked",
            "genesis_created",
            "genesis_predecessor_sha256",
            "creation_device",
            "creation_inode",
            "committed_event_sha256s",
            "after_head_sha256",
            "compensation_event_sha256",
            "compensation_head_sha256",
            "file_fsync_ack",
            "parent_fsync_ack",
        },
        "event store receipt",
    )
    if not any(
        "absent event-log before-state" in requirement
        and "predecessor is null" in requirement
        and "exclusive genesis creation" in requirement
        for requirement in migration_replay.get("replay_requirements", [])
    ):
        raise ValueError("migration replay fabricates an absent event-log before-state")

    failure = transaction.get("failure_contract", {})
    ordered_rollback = failure.get("ordered_rollback", [])
    rollback_fragments = (
        "keep every client stopped and retain the exclusive migration lease",
        "restore its exact before-image first",
        "restore the exact logical Beads rows and schema in one native transaction",
        "append MIGRATION_ROLLED_BACK",
        "run isolated migration replay",
        "release-intent receipt while held",
        "distinct immutable post-release receipt",
    )
    if (
        len(ordered_rollback) != len(rollback_fragments)
        or any(
            fragment not in step
            for fragment, step in zip(rollback_fragments, ordered_rollback)
        )
        or "keep all clients stopped until typed recovery authority"
        not in failure.get("rollback_failure", [])
        or "deny local and target execution"
        not in failure.get("rollback_failure", [])
    ):
        raise ValueError("failure rollback does not restore each store before fresh handoff")

    if (
        event_store.get("reversible") is not False
        or "append-only chain ends with MIGRATION_ROLLED_BACK"
        not in event_store.get("rollback_invariant", "")
        or "logical authority equals the absent or present before-state"
        not in event_store.get("rollback_invariant", "")
        or stores["beads_projection"].get("reversible") is not True
        or "canonical logical Beads rows and schema equal the captured before hashes"
        not in stores["beads_projection"].get("rollback_invariant", "")
        or stores["root_active_selector"].get("reversible") is not True
        or V1_ACTIVE_SHA256
        not in stores["root_active_selector"].get("rollback_invariant", "")
        or migration.get("rollback", {}).get("order")
        != [
            "restore root selector if committed",
            "restore Beads before-image in one Dolt transaction",
            "append event compensation",
            "verify rollback and zero authority",
            "emit release intent while held",
            "release lease and emit post-release receipt",
            "start a fresh session from the prior handoff",
        ]
        or "append-only event compensation chain"
        not in migration.get("rollback", {}).get("result", "")
    ):
        raise ValueError("per-store rollback and event compensation invariants changed")

    pre_handoff = session_handoff.get("pre_handoff_receipt", {})
    post_handoff = session_handoff.get("post_handoff_receipt", {})
    _require_exact_fields(
        pre_handoff.get("required_fields", []),
        {
            "migration_id",
            "quiescence_receipt_sha256",
            "prior_session_id",
            "prior_session_cwd",
            "closed_transcript_path",
            "closed_transcript_sha256",
            "closed_transcript_size",
            "closed_transcript_title_slot_sha256",
            "closed_transcript_session_header_sha256",
            "closed_transcript_final_cursor",
            "closed_transcript_final_event_sha256",
            "child_transcript_manifest_sha256",
            "prior_todo_projection_sha256",
            "prior_todo_projection_size",
            "prior_todo_projection_cursor",
            "frozen_program_inputs",
            "derived_handoff",
            "created_at",
            "receipt_sha256",
        },
        "pre-session handoff receipt",
    )
    _require_exact_fields(
        pre_handoff.get("derived_handoff_fields", []),
        {
            "program_state",
            "allowed_next",
            "execution_frontier",
            "capabilities",
            "active_authority",
            "score_authority",
            "checkpoint_authority",
            "target_execution_allowed",
            "nonclaims",
        },
        "pre-session derived handoff",
    )
    _require_exact_fields(
        post_handoff.get("required_fields", []),
        {
            "migration_id",
            "handoff_kind",
            "pre_handoff_receipt_sha256",
            "new_session_id",
            "new_session_cwd",
            "new_session_transcript_path",
            "new_session_header_sha256",
            "parent_session_id",
            "started_at",
            "consumed_input_hashes",
            "quiescence_post_release_receipt_sha256",
            "selector_receipt_sha256",
            "event_receipt_sha256",
            "dolt_receipt_sha256",
            "derived_action",
            "execution_frontier",
            "capabilities",
            "active_authority",
            "score_authority",
            "checkpoint_authority",
            "target_execution_allowed",
            "ambient_inputs_used",
            "receipt_sha256",
        },
        "post-session handoff receipt",
    )
    if (
        post_handoff.get("allowed_handoff_kinds")
        != ["committed_cutover", "rolled_back"]
        or set(post_handoff.get("invariants", []))
        != {
            "new_session_id differs from prior_session_id",
            "capabilities is empty",
            "active_authority is false",
            "score_authority is false",
            "checkpoint_authority is false",
            "target_execution_allowed is false",
            "ambient_inputs_used is empty",
        }
        or "quiescence_post_release_receipt_sha256"
        not in post_handoff.get("required_fields", [])
        or "target_execution_allowed" not in pre_handoff.get(
            "derived_handoff_fields", []
        )
    ):
        raise ValueError("fresh-session handoff or zero-authority invariants changed")

    expected_handoff_inputs = {
        "ARTIFACT_MANIFEST.json",
        "FRESH_WORKER_HANDOFF_CONTRACT.json",
        "PROGRAM_SPEC.yaml",
        "WORK_PACKET_DAG.yaml",
        "RUN_QUEUE.json",
        "DRAFT_STATUS.json",
        "SOURCE_MANIFEST.json",
        "MIGRATION_PLAN.json",
        "MIGRATION_TRANSACTION.json",
        "QUIESCENCE_CONTRACT.json",
        "SESSION_HANDOFF_CONTRACT.json",
        "MIGRATION_REPLAY_CONTRACT.json",
    }
    if (
        set(handoff.get("allowed_inputs", [])) != expected_handoff_inputs
        or len(handoff.get("allowed_inputs", [])) != len(expected_handoff_inputs)
        or handoff.get("isolation", {}).get("minimum_processes", 0) < 2
        or handoff.get("derivation", {}).get("post_cutover_execution_frontier")
        != ["AT0"]
        or handoff.get("derivation", {}).get("target_execution_allowed") is not False
        or "exact rc5 artifact manifest"
        not in handoff.get("derivation", {}).get("current_inactive_action", "")
        or "rc4 decision has no rc5 authority"
        not in handoff.get("derivation", {}).get("current_inactive_action", "")
    ):
        raise ValueError("fresh-worker handoff is incomplete or carries authority")
    migration_replay_receipt = migration_replay.get("receipt_contract", {})
    if migration_replay_receipt.get("additional_fields_allowed") is not False:
        raise ValueError("migration replay receipt admits undeclared fields")
    _require_exact_fields(
        migration_replay_receipt.get("required_fields", []),
        {
            "migration_id",
            "contract_sha256",
            "worker_count",
            "worker_semantic_sha256",
            "workers",
            "crash_fixture_results",
            "result",
        },
        "migration replay receipt",
    )
    _require_exact_fields(
        migration_replay_receipt.get("journal_step_fields", []),
        {
            "sequence",
            "migration_id",
            "operation_id",
            "store_id",
            "phase",
            "intent_sha256",
            "effect_sha256",
            "previous_record_sha256",
            "record_sha256",
            "fsynced",
        },
        "migration replay journal step",
    )
    _require_exact_fields(
        migration_replay_receipt.get("store_result_fields", []),
        {
            "store_id",
            "before_presence",
            "before_sha256",
            "after_sha256",
            "adapter_kind",
            "commit_valid",
            "rollback_valid",
            "journal_sequences",
        },
        "migration replay store result",
    )
    expected_replay_inputs = {
        "ARTIFACT_MANIFEST.json",
        "MIGRATION_PLAN.json",
        "MIGRATION_TRANSACTION.json",
        "QUIESCENCE_CONTRACT.json",
        "SESSION_HANDOFF_CONTRACT.json",
        "QUIESCENCE_RECEIPT.json",
        "SESSION_PRE_HANDOFF_RECEIPT.json",
        "MIGRATION_JOURNAL.jsonl",
        "captured before-images and prepared or committed after-image receipts available before replay",
        "rollback store receipts written before rollback replay",
    }
    replay_inputs = set(migration_replay.get("allowed_inputs", []))
    if (
        migration_replay.get("isolation", {}).get("minimum_processes", 0) < 2
        or replay_inputs != expected_replay_inputs
        or set(
            migration_replay.get("receipt_contract", {}).get(
                "worker_output_fields", []
            )
        )
        != {
            "migration_id",
            "mode",
            "input_hashes",
            "quiescence_valid",
            "adapter_kind",
            "journal_valid",
            "commit_order",
            "store_results",
            "selector_committed_last",
            "rollback_results",
            "session_handoff_result",
            "zero_authority",
            "semantic_sha256",
        }
    ):
        raise ValueError("migration replay omits journal, adapter, rollback, or authority proof")
    return expected_handoff_inputs


def validate(args: argparse.Namespace) -> dict[str, Any]:
    revision = args.revision
    archive = args.archive
    manifest_path = revision / "ARTIFACT_MANIFEST.json"
    archive_manifest_path = archive / "ARCHIVE_MANIFEST.json"
    manifest = load(manifest_path)
    archive_manifest = load(archive_manifest_path)
    superseded_manifest = load(args.superseded_manifest)
    manifest_sha256 = sha256_file(manifest_path)
    archive_manifest_sha256 = sha256_file(archive_manifest_path)
    superseded_manifest_sha256 = sha256_file(args.superseded_manifest)
    if manifest_sha256 != ARTIFACT_MANIFEST_SHA256:
        raise ValueError("artifact manifest is not the exact immutable rc5 candidate")
    if superseded_manifest_sha256 != SUPERSEDED_ARTIFACT_MANIFEST_SHA256:
        raise ValueError("superseded rc4 artifact manifest digest changed")
    if (
        superseded_manifest.get("program_id") != PROGRAM_ID
        or superseded_manifest.get("revision_id") != SUPERSEDED_REVISION_ID
        or superseded_manifest.get("immutable") is not True
    ):
        raise ValueError("superseded rc4 artifact manifest identity changed")
    if archive_manifest_sha256 != ARCHIVE_MANIFEST_SHA256:
        raise ValueError("sealed v1 archive manifest digest changed")
    files = check_manifest(revision, manifest)
    archived_files = check_archive(archive, archive_manifest)
    if (
        manifest["program_id"] != PROGRAM_ID
        or manifest["revision_id"] != REVISION_ID
        or archive_manifest["archive_id"] != ARCHIVE_ID
        or not manifest["immutable"]
    ):
        raise ValueError("wrong program/revision/archive identity or mutable manifest")
    if (
        manifest["archive_manifest_sha256"] != ARCHIVE_MANIFEST_SHA256
        or manifest["archive_manifest_sha256"] != archive_manifest_sha256
    ):
        raise ValueError("artifact manifest does not bind the sealed archive manifest")

    v1_scorecard = load(archive / "SCORECARD.json")
    catalog = load(revision / "ASSURANCE_CATALOG.json")
    equivalence = load(revision / "CATALOG_EQUIVALENCE.json")
    if equivalence["result"] != "pass" or not all(equivalence["checks"].values()):
        raise ValueError("catalog equivalence failed")
    if catalog["item_count"] != 49 or catalog["catalog_points"] != 1000:
        raise ValueError("catalog count/points changed")
    if len(catalog["items"]) != 49 or sum(item["points"] for item in catalog["items"]) != 1000:
        raise ValueError("catalog row total changed")
    exact_fields = ("item_id", "description", "points", "proof_floor", "pass_predicate", "workstream")
    for index, item in enumerate(catalog["items"]):
        source = v1_scorecard["items"][index]
        for field in exact_fields:
            if item[field] != source[field]:
                raise ValueError(f"catalog field changed: row {index}, {field}")
    if any({"state", "awarded_points", "evidence_ids", "review_ids"} & set(item) for item in catalog["items"]):
        raise ValueError("catalog contains mutable award state")
    definitions = catalog["definitions"]
    required_crossrefs = {
        "L6",
        "Training gates",
        "Config optimizer acceptance",
        "canonical two-hour soak",
        "control-plane thresholds",
        "load-ladder thresholds",
    }
    if set(definitions["cross_reference_resolution"]) != required_crossrefs:
        raise ValueError("catalog cross-reference resolution incomplete")
    training_gates = definitions["training_gates"]
    if (
        training_gates["optimizer_steps_min"] < 3
        or training_gates["calibration_generated_samples_min"] < 64
        or training_gates["longer_bounded_run"]["optimizer_steps_min"] <= 3
        or training_gates["longer_bounded_run"]["generated_samples_min"] <= 64
    ):
        raise ValueError("catalog training acceptance weakened")
    optimizer = definitions["config_optimizer_acceptance"]
    if (
        optimizer["accepted_variant_min_paired_ab_evaluations"] < 20
        or not optimizer["aa_noise_control_required"]
        or not optimizer["accepted_variant_must_repeat_on_held_out_task_set"]
    ):
        raise ValueError("catalog optimizer acceptance weakened")
    soak = definitions["f7_performance_and_soak"]["canonical_soak"]
    if (
        soak["total_minutes"] != 120
        or soak["attempted_episodes_min"] < 256
        or soak["completion_fraction_min"] < 0.995
        or soak["integrity_identity_cleanup_secret_failures_max"] != 0
    ):
        raise ValueError("catalog F7 soak acceptance weakened")
    if [
        item["environment_id"]
        for item in definitions["l6_admitted_set_conformance"][
            "claimed_environments"
        ]
    ] != ["local_docker", "ibm_one_node"]:
        raise ValueError("catalog L6 environment set changed")

    graph = load(revision / "WORK_PACKET_DAG.yaml")
    order = topo_sort(graph["nodes"])
    graph_rows = [row for node in graph["nodes"] for row in node["score_rows"]]
    catalog_rows = [item["item_id"] for item in catalog["items"]]
    if len(graph_rows) != 49 or len(set(graph_rows)) != 49 or set(graph_rows) != set(catalog_rows):
        raise ValueError("DAG does not contain each catalog row exactly once")
    if any(predicate["points"] != 0 for predicate in graph["readiness_predicates"]):
        raise ValueError("readiness predicate carries points")
    node = {item["id"]: item for item in graph["nodes"]}
    if (
        "AT6_F3" in node["AT7_F8_F9"]["depends_on"]
        or "AT7_F7_TWO_NODE" in node["AT7_F8_F9"]["depends_on"]
        or "AT7_F7_FOUR_NODE" in node["AT7_F8_F9"]["depends_on"]
    ):
        raise ValueError("F8 is incorrectly blocked by F3 or F7")
    if not {"AT6_F3", "AT6_F4", "AT6_F5", "AT6_F6"}.issubset(
        node["AT7_F7_TWO_NODE"]["depends_on"]
    ):
        raise ValueError("two-node F7 qualification can start before AT6 closure")
    if (
        node["AT7_F7_TWO_NODE"]["score_rows"]
        or node["AT7_F7_FOUR_NODE"]["depends_on"] != ["AT7_F7_TWO_NODE"]
        or node["AT7_F7_FOUR_NODE"]["score_rows"] != ["F7"]
        or node["AT7_F7_TWO_NODE"]["live_submission_sequences"] != 1
        or node["AT7_F7_FOUR_NODE"]["live_submission_sequences"] != 1
    ):
        raise ValueError("F7 topology packets do not have isolated qualification/scoring budgets")

    spec = load(revision / "PROGRAM_SPEC.yaml")
    training = load(revision / "TRAINING_PROOF_CONTRACT.json")
    authority = load(revision / "AUTHORITY_POLICY.json")
    transport = load(revision / "DURABLE_TRANSPORT_CONTRACT.json")
    migration_revision = spec.get("migration_revision", {})
    if (
        spec.get("revision_id") != REVISION_ID
        or spec["status"] != "draft_waiting_rc5_spec_freeze"
        or spec["tracks"]["training_proof"]["scored"]
    ):
        raise ValueError("rc5 draft/track state wrong")
    if (
        migration_revision.get("candidate_revision_id") != REVISION_ID
        or migration_revision.get("superseded_revision_id")
        != SUPERSEDED_REVISION_ID
        or migration_revision.get("superseded_artifact_manifest_sha256")
        != SUPERSEDED_ARTIFACT_MANIFEST_SHA256
        or migration_revision.get("prior_spec_freeze_authority_for_candidate")
        is not False
        or migration_revision.get("supersession_scope")
        != "migration and cutover mechanics only; catalog, program, queue, score, authority, transport, and target semantics are unchanged"
    ):
        raise ValueError("PROGRAM_SPEC reuses rc4 authority or changes supersession scope")
    if spec["tracks"]["assurance"]["catalog_points"] != 1000 or not spec["tracks"]["assurance"]["f3_required"]:
        raise ValueError("Assurance contract weakened")
    if spec["tracks"]["assurance"]["f3_blocks_training_proof"]:
        raise ValueError("F3 incorrectly blocks Training Proof")
    if training["task"]["task_id"] != "TRP-CAL-001" or training["data"]["rollout_n"] != 4:
        raise ValueError("Training Proof task/data changed")
    if training["predicates"]["rewards"].find("max>min") == -1 or training["predicates"]["optimizer"].find("named tensor digest changes") == -1:
        raise ValueError("Training Proof nondegeneracy/optimizer predicate missing")
    if authority["cryptographic_trust"]["state"] != "not_provisioned" or not authority["cryptographic_trust"]["required_before_first_target_campaign"]:
        raise ValueError("unprovisioned trust is not fail-closed")
    if len(spec["human_gates"]) != 4:
        raise ValueError("human gate count changed")
    if len(transport["adversarial_gate"]) != 13 or transport["status"] != "blocked_pending_repair_review_smoke_and_admission":
        raise ValueError("transport gate incomplete or unblocked")
    taxonomy = load(revision / "EVIDENCE_TAXONOMY.json")
    if set(taxonomy["edge_types"]) != {
        "depends_on",
        "qualifies",
        "supports",
        "contradicts",
    }:
        raise ValueError("evidence relation vocabulary changed")
    if not taxonomy["edge_contract"]["axes_are_independent"]:
        raise ValueError("evidence relation type and lifecycle are conflated")
    concrete_transport_types = {
        "TRANSPORT_SMOKE_ADMISSION",
        "TRANSPORT_ADMISSION",
    }
    may_issue = set(
        authority["roles"]["kyle_internal_program_authority"]["may_issue"]
    )
    if not concrete_transport_types.issubset(may_issue):
        raise ValueError("authority cannot issue concrete transport decisions")
    campaign_gate = next(
        gate for gate in spec["human_gates"] if gate["type"] == "CAMPAIGN_ADMISSION"
    )
    if not concrete_transport_types.issubset(
        campaign_gate["concrete_records"]
    ):
        raise ValueError("program gate omits concrete transport decisions")

    campaigns = load(revision / "CAMPAIGN_MATRIX.yaml")
    if any(row.get("live_sequences") != 1 or "CAMPAIGN_ADMISSION" not in row.get("approval", "") for row in campaigns["campaigns"] if row["id"] != "TRANSPORT_SMOKE"):
        raise ValueError("campaign lacks one-attempt human gate")
    topology = {
        row["id"]: row
        for row in campaigns["campaigns"]
        if row["id"] in {"F7_TWO_NODE", "F7_FOUR_NODE"}
    }
    if set(topology) != {"F7_TWO_NODE", "F7_FOUR_NODE"}:
        raise ValueError("F7 topology campaigns missing")
    if (
        topology["F7_TWO_NODE"]["packet_key"] != "AT7_F7_TWO_NODE"
        or topology["F7_TWO_NODE"]["attempt_budget_key"] != "AT7_F7_TWO_NODE"
        or topology["F7_TWO_NODE"].get("score_rows") != []
        or topology["F7_FOUR_NODE"]["packet_key"] != "AT7_F7_FOUR_NODE"
        or topology["F7_FOUR_NODE"]["attempt_budget_key"] != "AT7_F7_FOUR_NODE"
        or topology["F7_FOUR_NODE"].get("score_rows") != ["F7"]
        or topology["F7_FOUR_NODE"]["depends_on"] != ["F7_TWO_NODE"]
    ):
        raise ValueError("F7 topology campaign identities, budgets, or scoring are conflated")
    smoke = next(
        row for row in campaigns["campaigns"] if row["id"] == "TRANSPORT_SMOKE"
    )
    if "TRANSPORT_SMOKE_ADMISSION" not in smoke["approval"]:
        raise ValueError("transport smoke uses the wrong human decision type")
    if transport["smoke"]["approval"] != "TRANSPORT_SMOKE_ADMISSION":
        raise ValueError("transport contract smoke decision changed")
    if any(action["submit_allowed"] or action["upload_allowed"] or action["new_attempt_allowed"] for action in campaigns["recovery_actions"]):
        raise ValueError("observation-only action can submit/upload/create attempt")

    beads = load(revision / "BEADS_MIGRATION.json")
    frozen_children = beads["legacy_snapshot"]
    frozen_decisions = beads["map_decision_snapshot"]
    if len(frozen_children) != 67:
        raise ValueError("frozen legacy Beads source snapshot count changed")
    if (
        sha256_bytes(canonical_bytes(frozen_children))
        != beads["legacy_parent"]["snapshot_sha256"]
    ):
        raise ValueError("embedded legacy Beads snapshot digest changed")
    if len(frozen_decisions) != 8:
        raise ValueError("frozen Wayfinder decision snapshot count changed")
    if (
        sha256_bytes(canonical_bytes(frozen_decisions))
        != beads["map_decision_snapshot_sha256"]
    ):
        raise ValueError("embedded Wayfinder decision snapshot digest changed")
    expected_decisions = [
        {
            "issue_id": issue["id"],
            "record_sha256": sha256_bytes(canonical_bytes(issue)),
        }
        for issue in frozen_decisions
    ]
    if beads["map_decisions"] != expected_decisions:
        raise ValueError("Wayfinder decision record hash mismatch")
    if beads["freeze_request_issue_id"] != "bb-6d4.9":
        raise ValueError("freeze request issue changed")
    if len(beads["mappings"]) != 67 or len({row["legacy_issue_id"] for row in beads["mappings"]}) != 67:
        raise ValueError("legacy Beads mapping incomplete")
    if any(not row["successor_packet_keys"] for row in beads["mappings"]):
        raise ValueError("legacy Beads issue is unmapped")
    packet_keys = {node["id"] for node in graph["nodes"]}
    if any(set(row["successor_packet_keys"]) - packet_keys for row in beads["mappings"]):
        raise ValueError("Beads mapping references unknown packet")

    source = load(revision / "SOURCE_MANIFEST.json")
    if len(source["repositories"]) != 2 or sum(repo["dirty_entries"] for repo in source["repositories"]) != args.expected_source_entries:
        raise ValueError("source inventory count changed")
    if any(entry["adoption_state"] != "paused_unadmitted" for repo in source["repositories"] for entry in repo["entries"]):
        raise ValueError("source path silently adopted")


    status = load(revision / "DRAFT_STATUS.json")
    queue = load(revision / "RUN_QUEUE.json")
    index = load(revision / "EVIDENCE_INDEX.json")
    if status["active"] or status["target_lease"] is not None or status["shared_transport"]["state"] != "blocked":
        raise ValueError("draft accidentally active or target-capable")
    candidate_authority = status.get("candidate_authority", {})
    if (
        status.get("revision_id") != REVISION_ID
        or candidate_authority.get("prior_rc4_spec_freeze_applies") is not False
        or candidate_authority.get("required") != "new exact rc5 SPEC_FREEZE"
        or candidate_authority.get("superseded_revision_id")
        != SUPERSEDED_REVISION_ID
        or candidate_authority.get("superseded_artifact_manifest_sha256")
        != SUPERSEDED_ARTIFACT_MANIFEST_SHA256
    ):
        raise ValueError("draft status reuses rc4 authority for rc5")
    assurance = status["tracks"]["assurance"]
    if assurance["current_verified_points"] != 0 or assurance["awarded_items"] or assurance["evidence_ref_count"] or assurance["review_ref_count"]:
        raise ValueError("migration awards Assurance state")
    if status["tracks"]["training_proof"].get("score_field_present") is not False:
        raise ValueError("Training Proof has score field")
    if queue["eligible"] or queue["target_lease"] is not None:
        raise ValueError("draft queue has eligible work or target lease")
    if index["active_relations"] != 0 or any(row["active"] or row["admitted"] for row in index["rows"]):
        raise ValueError("migration admits evidence")
    for row in index["rows"]:
        if "relation" in row or "claim_boundary" in row or "object_sha256" in row:
            raise ValueError("evidence index retains conflated legacy relation fields")
        if row["edge_type"] not in taxonomy["edge_types"]:
            raise ValueError("evidence index has unknown edge type")
        if row["lifecycle_state"] not in taxonomy["lifecycle_states"]:
            raise ValueError("evidence index has unknown lifecycle state")
        if not row["evidence_digest"].startswith("sha256:"):
            raise ValueError("evidence index digest missing")

    migration = load(revision / "MIGRATION_PLAN.json")
    transaction = load(revision / "MIGRATION_TRANSACTION.json")
    quiescence = load(revision / "QUIESCENCE_CONTRACT.json")
    session_handoff = load(revision / "SESSION_HANDOFF_CONTRACT.json")
    migration_replay = load(revision / "MIGRATION_REPLAY_CONTRACT.json")
    handoff = load(revision / "FRESH_WORKER_HANDOFF_CONTRACT.json")
    if (
        migration["transaction"] != "MIGRATION_TRANSACTION.json"
        or migration["fresh_worker_contract"]
        != "FRESH_WORKER_HANDOFF_CONTRACT.json"
        or migration.get("quiescence_contract") != "QUIESCENCE_CONTRACT.json"
        or migration.get("session_handoff_contract")
        != "SESSION_HANDOFF_CONTRACT.json"
        or migration.get("migration_replay_contract")
        != "MIGRATION_REPLAY_CONTRACT.json"
        or migration["post_cutover"]["execution_frontier"] != ["AT0"]
        or migration["post_cutover"]["target_execution_allowed"]
    ):
        raise ValueError("migration plan does not bind the fail-closed rc5 cutover")
    expected_handoff_inputs = validate_candidate_migration_contracts(
        manifest,
        migration,
        transaction,
        quiescence,
        session_handoff,
        migration_replay,
        handoff,
        superseded_manifest_sha256,
    )

    if sha256_file(args.build_report) != BUILD_REPORT_SHA256:
        raise ValueError("exact rc5 build report digest changed")
    build_report = load(args.build_report)
    if (
        build_report.get("result") != "pass"
        or build_report.get("byte_identical") is not True
        or build_report.get("installed") is not True
        or build_report.get("program_id") != PROGRAM_ID
        or build_report.get("revision_id") != REVISION_ID
        or build_report.get("build_a_file_count") != 55
        or build_report.get("build_b_file_count") != 55
        or build_report.get("source_entries") != 835
    ):
        raise ValueError("exact rc5 double-build receipt failed")
    if build_report.get("artifact_manifest_sha256") != manifest_sha256:
        raise ValueError("build receipt does not bind exact rc5 manifest")
    if build_report.get("archive_manifest_sha256") != archive_manifest_sha256:
        raise ValueError("build receipt does not bind exact sealed v1 archive")
    if args.expected_source_entries != 835:
        raise ValueError("rc5 frozen source inventory count must be exactly 835")

    if sha256_file(args.replay_report) != FRESH_WORKER_REPLAY_SHA256:
        raise ValueError("exact rc5 fresh-worker replay report digest changed")
    replay = load(args.replay_report)
    current_input_hashes = {
        name: sha256_file(revision / name) for name in expected_handoff_inputs
    }
    expected_worker_semantic = {
        "ambient_inputs_used": [],
        "derived_action": handoff["derivation"]["current_inactive_action"],
        "execution_frontier": [],
        "input_hashes": current_input_hashes,
        "target_execution_allowed": False,
    }
    expected_worker_semantic_sha256 = sha256_bytes(
        canonical_bytes(expected_worker_semantic)
    )
    if set(replay) != set(handoff["receipt"]["top_level_fields"]):
        raise ValueError("fresh-worker replay receipt fields changed")
    if (
        replay.get("result") != "pass"
        or replay.get("worker_count") != 2
        or replay.get("artifact_manifest_sha256")
        != sha256_file(revision / "ARTIFACT_MANIFEST.json")
        or replay.get("contract_sha256")
        != sha256_file(revision / "FRESH_WORKER_HANDOFF_CONTRACT.json")
        or replay.get("worker_semantic_sha256")
        != expected_worker_semantic_sha256
    ):
        raise ValueError("fresh-worker replay receipt identity or semantics failed")

    if sha256_file(args.safety_report) != SAFETY_REPORT_SHA256:
        raise ValueError("exact rc5 standalone safety report digest changed")
    safety = load(args.safety_report)
    if (
        safety.get("result") != "pass"
        or safety.get("schema_version")
        != "bb.rl.phase5.v2_candidate_safety_validation_report.v1"
        or safety.get("candidate_revision_id") != REVISION_ID
        or safety.get("candidate_artifact_manifest_sha256") != manifest_sha256
        or safety.get("predecessor_revision_id") != SUPERSEDED_REVISION_ID
        or safety.get("predecessor_artifact_manifest_sha256")
        != SUPERSEDED_ARTIFACT_MANIFEST_SHA256
        or safety.get("archive_manifest_sha256") != archive_manifest_sha256
        or safety.get("build_report_sha256") != BUILD_REPORT_SHA256
        or safety.get("source_entries") != args.expected_source_entries
        or safety.get("live_store_access") is not False
        or safety.get("target_admitted") is not False
        or safety.get("target_execution_allowed") is not False
        or safety.get("verified_points") != 0
        or safety.get("zero_authority") is not True
        or not safety.get("normalized_semantics")
        or any(
            row.get("equal") is not True
            for row in safety["normalized_semantics"].values()
        )
    ):
        raise ValueError("exact rc5 standalone safety receipt failed")

    root_active = args.execution_root / "ACTIVE_STATUS.json"
    if sha256_file(root_active) != V1_ACTIVE_SHA256:
        raise ValueError("root ACTIVE_STATUS changed before SPEC_FREEZE")

    return {
        "archive_file_count": len(archived_files),
        "archive_manifest_sha256": sha256_file(archive / "ARCHIVE_MANIFEST.json"),
        "artifact_file_count": len(files),
        "artifact_manifest_sha256": sha256_file(revision / "ARTIFACT_MANIFEST.json"),
        "superseded_artifact_manifest_sha256": superseded_manifest_sha256,
        "superseded_revision_id": SUPERSEDED_REVISION_ID,
        "supersession_scope": manifest["supersession_scope"],
        "zero_authority": True,
        "active_authority": False,
        "score_authority": False,
        "checkpoint_authority": False,
        "target_execution_allowed": False,
        "spec_freeze_authority": False,
        "revision_id": REVISION_ID,
        "beads_mappings": len(beads["mappings"]),
        "beads_legacy_snapshot_sha256": beads["legacy_parent"]["snapshot_sha256"],
        "beads_wayfinder_decisions": len(beads["map_decisions"]),
        "beads_wayfinder_snapshot_sha256": beads[
            "map_decision_snapshot_sha256"
        ],
        "catalog_items": 49,
        "catalog_points": 1000,
        "dag_order": order,
        "evidence_active_relations": 0,
        "fresh_worker_replay_sha256": FRESH_WORKER_REPLAY_SHA256,
        "fresh_worker_semantic_sha256": replay["worker_semantic_sha256"],
        "safety_report_sha256": SAFETY_REPORT_SHA256,
        "build_report_sha256": BUILD_REPORT_SHA256,
        "program_id": PROGRAM_ID,
        "result": "pass",
        "schema_version": "bb.rl.phase5.freeze_validation_report.v2",
        "source_entries": args.expected_source_entries,
        "target_admitted": False,
        "verified_points": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--superseded-manifest", type=Path, required=True
    )
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--replay-report", type=Path, required=True)
    parser.add_argument("--safety-report", type=Path, required=True)
    parser.add_argument("--expected-source-entries", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(canonical_bytes(result))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
