from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import stat
import time
import threading

import pytest

import breadboard.rl.phase5.rollback_store as rollback_store_module
from breadboard.rl.phase5.rollback_store import (
    ActiveApprovedTuple,
    ApprovedTupleRef,
    DependentIneligibleError,
    DependentObjectKind,
    DependentOwnership,
    FilesystemActiveApprovedTupleStore,
    FilesystemDependentQuarantineStore,
    FilesystemRollbackJournalStore,
    ImmutableObjectRef,
    RollbackConflictError,
    RollbackCorruptionError,
    RollbackIdempotencyConflict,
    RollbackLeafError,
    RollbackPayloadKind,
    RollbackPayloadRef,
    RollbackPhase,
    RollbackValidationError,
    canonical_digest,
    canonical_json_bytes,
)


KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
REQUEST = "sha256:" + "11" * 32
OTHER_REQUEST = "sha256:" + "22" * 32
CAUSE = "sha256:" + "33" * 32
OTHER_CAUSE = "sha256:" + "44" * 32
RECEIPT = "sha256:" + "55" * 32
TUPLE_OWNER = "sha256:" + "66" * 32


def _request_payload(
    rollback_id: str, base: Path, *, variant: str = "primary"
) -> bytes:
    from scripts.rl_phase5.run_f6_restart_replay import F6RestartReplayInput
    from tests.rl.phase5.test_run_f6_restart_replay import _spec

    base = base.resolve()
    spec = _spec(base)

    def persist_immutable(path: Path, raw: bytes) -> None:
        if not path.exists():
            path.write_bytes(raw)
            path.chmod(0o400)
        assert path.read_bytes() == raw

    def file_identity(path: Path) -> dict[str, object]:
        observed = path.stat(follow_symlinks=False)
        return {
            "ctime_ns": str(observed.st_ctime_ns),
            "device": observed.st_dev,
            "inode": observed.st_ino,
            "mode": stat.S_IMODE(observed.st_mode),
            "mtime_ns": str(observed.st_mtime_ns),
            "nlink": observed.st_nlink,
            "owner_uid": observed.st_uid,
            "size_bytes": observed.st_size,
        }

    original_request_bytes = canonical_json_bytes(
        spec.original_request.model_dump(mode="json")
    )
    descriptor_path = Path(spec.production.composition_ref_path)
    manifest_path = base / "manifest.json"
    authority_path = base / "authority.json"
    original_request_path = base / "request.json"
    persist_immutable(descriptor_path, b"composition-descriptor")
    persist_immutable(manifest_path, b"composition-manifest")
    persist_immutable(authority_path, b"authority-bundle")
    persist_immutable(original_request_path, original_request_bytes)
    for handle_id, source in spec.production.secret_files.items():
        persist_immutable(Path(source.path), f"{handle_id}-secret".encode())

    spec_payload = spec.model_dump(mode="json")
    for handle_id, source in spec.production.secret_files.items():
        spec_payload["production"]["secret_files"][handle_id]["identity"] = (
            file_identity(Path(source.path))
        )
    spec = F6RestartReplayInput.model_validate(spec_payload)
    rerun_input = base / f".{rollback_id}.{variant}.rerun-input.json"
    rerun_bytes = canonical_json_bytes(spec.model_dump(mode="json"))
    persist_immutable(rerun_input, rerun_bytes)

    def source_digest(label: str) -> str:
        return "sha256:" + hashlib.sha256(label.encode()).hexdigest()

    def source_binding(path: Path, digest: str) -> dict[str, object]:
        return {"identity": file_identity(path), "sha256": digest}

    target = spec.target.model_dump(mode="json")
    approved = _tuple(
        f"request-{rollback_id}-{variant}",
        "a" if variant == "primary" else "b",
    )
    dependent_root = _ref(
        f"request-{rollback_id}-{variant}-dependent",
        "d" if variant == "primary" else "e",
    )
    return canonical_json_bytes(
        {
            "affected_episode_ids": ["f6-original-episode"],
            "approved_tuple": approved.canonical_object(),
            "dependent_root_refs": [dependent_root.canonical_object()],
            "evidence_invalidations": [],
            "failed_rerun_invalidations": [],
            "frozen_active_generation": 1,
            "rerun_authoring_input": {
                "authority_bundle": {
                    "path": str(base / "authority.json"),
                    "sha256": spec.production.authority_bundle_ref.digest,
                },
                "composition_descriptor": {
                    "path": spec.production.composition_ref_path,
                    "sha256": (spec.production.composition_descriptor_ref.digest),
                },
                "composition_manifest": {
                    "path": str(base / "manifest.json"),
                    "sha256": spec.production.composition_manifest_ref.digest,
                },
                "fresh_episode_id": spec.fresh_live_request.episode_id,
                "original_request": {
                    "path": str(base / "request.json"),
                    "sha256": canonical_digest(
                        canonical_json_bytes(
                            spec.original_request.model_dump(mode="json")
                        )
                    ),
                },
                "report_path": spec.report_path,
                "run_context": spec.run_context,
                "schema_version": ("bb.rl.phase5-f6-restart-replay-authoring-input.v1"),
                "secret_files": {
                    handle_id: {
                        "path": source.path,
                        "sha256": source.sha256,
                    }
                    for handle_id, source in spec.production.secret_files.items()
                },
                "target": target,
                "task_input": spec.task_input,
            },
            "rerun_source_identities": {
                "authority_bundle": source_binding(
                    authority_path,
                    spec.production.authority_bundle_ref.digest,
                ),
                "composition_descriptor": source_binding(
                    descriptor_path,
                    spec.production.composition_descriptor_ref.digest,
                ),
                "composition_manifest": source_binding(
                    manifest_path,
                    spec.production.composition_manifest_ref.digest,
                ),
                "original_request": source_binding(
                    original_request_path,
                    canonical_digest(original_request_bytes),
                ),
                "rerun_input": source_binding(
                    rerun_input,
                    canonical_digest(rerun_bytes),
                ),
                "secret_files": {
                    handle_id: source_binding(Path(source.path), source.sha256)
                    for handle_id, source in spec.production.secret_files.items()
                },
            },
            "rerun_input_path": str(rerun_input),
            "revocation_publish_request": {
                "binding": {
                    "epoch": 7,
                    "scope_digest": CAUSE,
                    "state_digest": OTHER_CAUSE,
                },
                "expected_epoch": 7,
                "expected_generation": 1,
                "operation_id": f"{rollback_id}.revocation",
                "scope_digest": CAUSE,
            },
            "rollback_id": rollback_id,
            "schema_version": "bb.rl.phase5.g4-rollback-request.v1",
            "source_deletion_plan": {
                "operation_id": f"{rollback_id}.source-deletion",
                "owned_sources": [
                    {
                        "ctime_ns": "3",
                        "device": "1",
                        "inode": "2",
                        "kind": "file",
                        "relative_path": "owned/source.bin",
                        "root_authority_id": "rollback-source-root",
                        "root_path": str(base),
                        "sha256": source_digest("owned-source"),
                        "size_bytes": "4",
                    }
                ],
                "schema_version": "bb.rl.phase5.g4-source-deletion-plan.v1",
            },
        }
    )


def _prepare(
    store: FilesystemRollbackJournalStore,
    rollback_id: str,
    *,
    variant: str = "primary",
) -> object:
    payload = _request_payload(rollback_id, store.root.parent, variant=variant)
    return store.prepare(rollback_id, canonical_digest(payload), payload)


class _CapturedPublication(Exception):
    pass


def _install_active_recovery_intent(
    store: FilesystemRollbackJournalStore,
    rollback_id: str,
) -> tuple[object, str, bytes, bytes]:
    prepared = _prepare(store, rollback_id)
    head_name = f"journal.{rollback_id}.head"
    prior_raw = (store.root / head_name).read_bytes()
    captured: dict[str, object] = {}
    real_publish = store._publish_versioned

    def capture_publication(**kwargs: object) -> None:
        captured.update(kwargs)
        raise _CapturedPublication

    store._publish_versioned = capture_publication
    try:
        with pytest.raises(_CapturedPublication):
            _advance(
                store,
                rollback_id,
                expected_generation=1,
                expected_revision=0,
                phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            )
    finally:
        store._publish_versioned = real_publish
    successor_raw = captured["signed_record"]
    assert type(successor_raw) is bytes
    store._replace(head_name, successor_raw, prior_raw)
    transaction_id = "a" * 32
    intent_name = f".rollback-journal.{transaction_id}.transaction-rollback"
    intent_raw = store._rollback_intent_bytes(
        transaction_id,
        prior_raw,
        successor_raw,
    )
    store._create_immutable(intent_name, intent_raw)
    return prepared, intent_name, prior_raw, successor_raw


def _terminal_path(root: Path, name: str) -> Path:
    return root / ".terminal-rollback" / name


def _assert_semantic_restoration(
    actual: object,
    predecessor: object,
) -> None:
    assert actual is not None
    assert actual.rollback_id == predecessor.rollback_id
    assert actual.request_digest == predecessor.request_digest
    assert actual.request_payload_ref == predecessor.request_payload_ref
    assert actual.generation == predecessor.generation + 2
    assert actual.revision == predecessor.revision
    assert actual.phase is predecessor.phase
    assert actual.phase_receipts == predecessor.phase_receipts
    assert actual.terminal_quarantine_refs[:-1] == predecessor.terminal_quarantine_refs
    assert len(actual.terminal_quarantine_refs) == (
        len(predecessor.terminal_quarantine_refs) + 1
    )


def _complete_terminal_quarantine(
    store: FilesystemRollbackJournalStore,
    rollback_id: str,
) -> tuple[object, bytes, bytes, Path, Path]:
    prepared, intent_name, prior_raw, successor_raw = _install_active_recovery_intent(
        store, rollback_id
    )
    capsule = store._preflight_transaction_rollback_intent(intent_name)
    quarantine_path = _terminal_path(store.root, capsule.quarantine_name)
    tombstone_path = _terminal_path(store.root, capsule.tombstone_name)
    try:
        store._recover_transaction_rollback(capsule)
    finally:
        capsule.close()
    assert quarantine_path.read_bytes() == successor_raw
    assert json.loads(tombstone_path.read_bytes())["payload"]["state"] == (
        "quarantined"
    )
    return (
        prepared,
        prior_raw,
        successor_raw,
        quarantine_path,
        tombstone_path,
    )


def _receipt_body(
    store: FilesystemRollbackJournalStore,
    rollback_id: str,
    phase: RollbackPhase,
    leaf_errors: tuple[RollbackLeafError, ...],
    variant: str,
) -> dict[str, object]:
    current = store.get(rollback_id)
    assert current is not None
    request = json.loads(store.get_request(rollback_id))
    if phase is RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED:
        envelope_digest = "sha256:" + ("cd" if variant == "primary" else "ce") * 32
        return {
            "episode_receipts": [
                {
                    "cancellation_reason": f"g4 rollback {variant}",
                    "cancellation_requested": True,
                    "cleanup_disposition": "released",
                    "closed_envelope_ref": {
                        "artifact_id": envelope_digest,
                        "media_type": (
                            "application/vnd.breadboard.selection-binding+json;"
                            "version=1"
                        ),
                        "schema_version": "bb.rl.artifact-ref.v1",
                        "sha256": envelope_digest,
                        "size_bytes": 128,
                    },
                    "episode_id": request["affected_episode_ids"][0],
                    "terminal_state": "closed",
                    "transition_head_digest": CAUSE,
                    "transition_sequence": 7,
                }
            ],
            "reconcile_receipts": [],
        }
    if phase is RollbackPhase.REVOCATION_PUBLISHED:
        revocation_request = request["revocation_publish_request"]
        snapshot_digest = "sha256:" + "91" * 32
        monotonic = {
            "authority_id": "sha256:" + "92" * 32,
            "config_device": 1,
            "config_digest": "sha256:" + "93" * 32,
            "config_flags": 1,
            "config_gid": 0,
            "config_inode": 2,
            "config_uid": 0,
            "lock_device": 1,
            "lock_flags": 1,
            "lock_gid": 0,
            "lock_inode": 3,
            "lock_uid": 0,
            "root_device": 1,
            "root_flags": 1,
            "root_gid": 0,
            "root_inode": 1,
            "root_uid": 0,
            "schema_version": ("bb.rl.monotonic-revocation-authority-identity.v1"),
        }
        artifact_ref = {
            "artifact_id": snapshot_digest,
            "media_type": (
                "application/vnd.breadboard.revocation-snapshot+json;version=1"
            ),
            "schema_version": "bb.rl.artifact-ref.v1",
            "sha256": snapshot_digest,
            "size_bytes": 256,
        }
        return {
            "revocation_receipt": {
                "active_pointer_digest": "sha256:" + "94" * 32,
                "generation": revocation_request["expected_generation"] + 1,
                "history_digest": "sha256:" + "95" * 32,
                "monotonic_authority": monotonic,
                "operation_id": revocation_request["operation_id"],
                "predecessor_config_digest": "sha256:" + "96" * 32,
                "predecessor_model_type": "f3",
                "predecessor_schema_version": "bb.rl.phase5-f3-authority-input.v1",
                "previous_snapshot_ref": artifact_ref,
                "request_digest": canonical_digest(
                    canonical_json_bytes(revocation_request)
                ),
                "snapshot_ref": artifact_ref,
            }
        }
    if phase is RollbackPhase.DEPENDENTS_QUARANTINED:
        root_ref = ImmutableObjectRef(
            request["dependent_root_refs"][0]["reference"],
            request["dependent_root_refs"][0]["digest"],
        )
        object_ref = _ref(f"{rollback_id}-dependent-receipt", "8")
        return {
            "dependent_quarantine_receipts": [
                {
                    "causal_root_digests": [root_ref.identity_digest],
                    "cause_digest": current.request_digest,
                    "generation": 2,
                    "object_ref": object_ref.canonical_object(),
                    "ownership_digest": CAUSE,
                    "rollback_id": rollback_id,
                    "schema_version": ("bb.rl.phase5.dependent-quarantine-receipt.v1"),
                }
            ],
            "evidence_invalidations": [],
        }
    if phase is RollbackPhase.ACTIVE_TUPLE_RESTORED:
        return {
            "active_tuple_state": {
                "approved_tuple": request["approved_tuple"],
                "generation": request["frozen_active_generation"] + 1,
                "operation_id": f"{rollback_id}.active-tuple",
                "previous_state_digest": CAUSE,
                "schema_version": ("bb.rl.phase5.active-approved-tuple-state.v1"),
            }
        }
    if phase is RollbackPhase.RERUN_RECORDED:
        from scripts.rl_phase5.run_f6_restart_replay import (
            F6RestartReplayInput,
            _validate_f6_restart_replay,
        )
        from tests.rl.phase5.test_run_f6_restart_replay import (
            _DurableFakeRuntime,
        )

        input_bytes = Path(request["rerun_input_path"]).read_bytes()
        spec = F6RestartReplayInput.model_validate_json(input_bytes)
        report = asyncio.run(
            _validate_f6_restart_replay(
                spec,
                input_digest=canonical_digest(input_bytes),
                runtime=_DurableFakeRuntime(spec),
            )
        )
        return {"rerun_report": report.model_dump(mode="json")}
    if phase is RollbackPhase.SOURCE_DELETED:
        from breadboard.rl.phase5.g4_source_deletion import (
            SourceAbsenceProof,
            SourceDeletionGateReceipt,
            SourceDeletionGateReceipts,
            SourceDeletionReceipt,
            SourceDeletionRequest,
            SourceOwnershipIdentity,
        )

        plan = request["source_deletion_plan"]
        raw_source = plan["owned_sources"][0]
        source = SourceOwnershipIdentity(
            root_authority_id=raw_source["root_authority_id"],
            root_path=raw_source["root_path"],
            relative_path=raw_source["relative_path"],
            device=int(raw_source["device"]),
            inode=int(raw_source["inode"]),
            ctime_ns=int(raw_source["ctime_ns"]),
            size_bytes=int(raw_source["size_bytes"]),
            sha256=raw_source["sha256"],
            kind=raw_source["kind"],
        )
        by_phase = {item.phase: item.receipt_refs[0] for item in current.phase_receipts}

        def gate(prior_phase: RollbackPhase) -> SourceDeletionGateReceipt:
            payload_ref = by_phase[prior_phase]
            return SourceDeletionGateReceipt(
                path=str(store.root / payload_ref.relative_path),
                sha256=payload_ref.payload_digest,
            )

        gates = SourceDeletionGateReceipts(
            episode_terminal_refs=(gate(RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED),),
            revocation_snapshot_ref=gate(RollbackPhase.REVOCATION_PUBLISHED),
            dependent_quarantine_refs=(gate(RollbackPhase.DEPENDENTS_QUARANTINED),),
            active_tuple_history_ref=gate(RollbackPhase.ACTIVE_TUPLE_RESTORED),
            rerun_receipt_ref=gate(RollbackPhase.RERUN_RECORDED),
        )
        deletion_request = SourceDeletionRequest(
            operation_id=plan["operation_id"],
            rollback_id=rollback_id,
            journal_request_digest=current.request_digest,
            owned_sources=(source,),
            gates=gates,
        )
        absence = SourceAbsenceProof(
            root_authority_id=source.root_authority_id,
            root_path=source.root_path,
            relative_path=source.relative_path,
            prior_device=source.device,
            prior_inode=source.inode,
            prior_ctime_ns=source.ctime_ns,
            prior_size_bytes=source.size_bytes,
            prior_sha256=source.sha256,
            prior_kind=source.kind,
            absence_anchor_relative_path="",
            anchor_device=9,
            anchor_inode=10,
            observed_at="2026-07-14T00:00:00Z",
        )
        deletion_receipt = SourceDeletionReceipt(
            operation_id=deletion_request.operation_id,
            request_digest=deletion_request.request_digest,
            deleted=(source.key,),
            already_absent=(),
            absence_proofs=(absence,),
            completed_at="2026-07-14T00:00:01Z",
            completion_digest=CAUSE,
            authority_signature="hmac-sha256:" + "ab" * 32,
        )
        return {
            "source_deletion_receipt": deletion_receipt.projection(),
            "source_deletion_request": deletion_request.projection(),
        }
    prior_receipts = current.phase_receipts
    if current.phase is phase:
        prior_receipts = prior_receipts[:-1]
    prior_digests = [
        digest for receipt in prior_receipts for digest in receipt.receipt_digests
    ]
    if phase is RollbackPhase.COMPLETE:
        return {"prior_phase_receipt_digests": prior_digests}
    assert phase is RollbackPhase.QUARANTINED
    prior_phase = prior_receipts[-1].phase if prior_receipts else RollbackPhase.PREPARED
    phase_order = (
        RollbackPhase.PREPARED,
        RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        RollbackPhase.REVOCATION_PUBLISHED,
        RollbackPhase.DEPENDENTS_QUARANTINED,
        RollbackPhase.ACTIVE_TUPLE_RESTORED,
        RollbackPhase.RERUN_RECORDED,
        RollbackPhase.SOURCE_DELETED,
        RollbackPhase.COMPLETE,
    )
    return {
        "cleanup_receipts": [],
        "failed_phase": phase_order[phase_order.index(prior_phase) + 1].value,
        "leaf_errors": [error.canonical_object() for error in leaf_errors],
    }


def _phase_payload(
    store: FilesystemRollbackJournalStore,
    rollback_id: str,
    *,
    expected_generation: int,
    expected_revision: int,
    phase: RollbackPhase,
    leaf_errors: tuple[RollbackLeafError, ...] = (),
    variant: str = "primary",
) -> bytes:
    current = store.get(rollback_id)
    assert current is not None
    return canonical_json_bytes(
        {
            "body": _receipt_body(store, rollback_id, phase, leaf_errors, variant),
            "journal_generation": expected_generation + 1,
            "journal_revision": expected_revision + 1,
            "phase": phase.value,
            "request_digest": current.request_digest,
            "rollback_id": rollback_id,
            "schema_version": "bb.rl.phase5.g4-phase-receipt.v1",
        }
    )


def _advance(
    store: FilesystemRollbackJournalStore,
    rollback_id: str,
    *,
    expected_generation: int,
    expected_revision: int,
    phase: RollbackPhase,
    leaf_errors: tuple[RollbackLeafError, ...] = (),
    variant: str = "primary",
) -> object:
    payload = _phase_payload(
        store,
        rollback_id,
        expected_generation=expected_generation,
        expected_revision=expected_revision,
        phase=phase,
        leaf_errors=leaf_errors,
        variant=variant,
    )
    return store.advance(
        rollback_id,
        expected_generation=expected_generation,
        expected_revision=expected_revision,
        phase=phase,
        receipt_digests=(canonical_digest(payload),),
        receipt_payloads=(payload,),
        leaf_errors=leaf_errors,
    )


def _ref(name: str, byte: str) -> ImmutableObjectRef:
    return ImmutableObjectRef(
        f"cas://rollback/{name}@sha256:{byte * 64}", f"sha256:{byte * 64}"
    )


def _tuple(name: str, byte: str) -> ActiveApprovedTuple:
    return ActiveApprovedTuple.from_refs(
        (
            ApprovedTupleRef("authority", _ref(f"{name}-authority", byte)),
            ApprovedTupleRef("composition", _ref(f"{name}-composition", byte)),
            ApprovedTupleRef("f6-rerun-input", _ref(f"{name}-rerun", byte)),
            ApprovedTupleRef("manifest", _ref(f"{name}-manifest", byte)),
        )
    )


def _ownership(
    name: str,
    byte: str,
    *,
    kind: DependentObjectKind,
    parents: tuple[ImmutableObjectRef, ...] = (),
    registration_id: str | None = None,
    episode_id: str = "episode-1",
    run_id: str = "run-1",
) -> DependentOwnership:
    return DependentOwnership(
        registration_id or f"register-{name}",
        TUPLE_OWNER,
        episode_id,
        run_id,
        kind,
        _ref(name, byte),
        tuple(sorted(parents, key=lambda item: item.identity_digest)),
    )


def _modes(root: Path) -> tuple[int, set[int]]:
    files = {
        stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
        for path in root.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    return stat.S_IMODE(root.stat().st_mode), files


def _process_active_cas(
    root: str,
    gate: object,
    output: object,
    name: str,
    byte: str,
    operation_id: str,
) -> None:
    store = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    gate.wait()
    try:
        state = store.compare_and_swap(1, _tuple(name, byte), operation_id)
    except RollbackConflictError:
        output.put(("conflict", None))
    except BaseException as error:
        output.put(("error", type(error).__name__))
    else:
        output.put(("won", state.generation))
    finally:
        store.close()


def _replace_lock_file(root: Path) -> None:
    lock_path = root / ".store.lock"
    lock_path.unlink()
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _process_crash_quarantine(
    root: str,
    rollback_id: str,
    root_ref: ImmutableObjectRef,
    crash_after: int,
) -> None:
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    real_publish = store._publish_records_locked
    publications = 0

    def crash_during_publish(records: object, old_payload: bytes | None) -> object:
        nonlocal publications
        if publications == crash_after:
            os._exit(77)
        publications += 1
        return real_publish(records, old_payload)

    store._publish_records_locked = crash_during_publish
    store.quarantine_causal(rollback_id, CAUSE, (root_ref,))


def _process_crash_during_temp_write(root: str) -> None:
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)

    def crash_write(fd: int, payload: bytes) -> None:
        os.write(fd, payload[:7])
        os._exit(78)

    store._write_all = crash_write
    _prepare(store, "rollback-temp-crash")


def _process_crash_during_transaction_rollback(root: str) -> None:
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    real_persist = store._persist_locked
    real_create = store._create_immutable

    def publish_then_fail(record: object, old_payload: bytes | None) -> None:
        real_persist(record, old_payload)  # type: ignore[arg-type]
        raise OSError("force publication rollback")

    def crash_after_rollback_intent_fsync(name: str, payload: bytes) -> None:
        real_create(name, payload)
        if name.endswith(".transaction-rollback"):
            os._exit(79)

    store._persist_locked = publish_then_fail
    store._create_immutable = crash_after_rollback_intent_fsync
    _advance(
        store,
        "rollback-transaction-temp-crash",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )


def _process_crash_during_intent_recovery(root: str) -> None:
    real_rename = rollback_store_module._rename_noreplace

    def rename_then_crash(
        source: str,
        destination: str,
        directory_fd: int,
    ) -> None:
        real_rename(source, destination, directory_fd)
        if destination.endswith(".head"):
            os._exit(80)

    rollback_store_module._rename_noreplace = rename_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_successor_displacement(root: str) -> None:
    real_rename = rollback_store_module._rename_noreplace

    def rename_then_crash(
        source: str,
        destination: str,
        directory_fd: int,
    ) -> None:
        real_rename(source, destination, directory_fd)
        if destination.endswith(".displaced-head"):
            os._exit(81)

    rollback_store_module._rename_noreplace = rename_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_prior_candidate_create(root: str) -> None:
    real_create = FilesystemRollbackJournalStore._create_immutable

    def create_then_crash(
        self: FilesystemRollbackJournalStore,
        name: str,
        payload: bytes,
    ) -> None:
        real_create(self, name, payload)
        if name.endswith(".prior-candidate"):
            os._exit(82)

    FilesystemRollbackJournalStore._create_immutable = create_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_cleanup_intent_publish(root: str) -> None:
    real_replace = FilesystemRollbackJournalStore._replace_at

    def replace_then_crash(
        self: FilesystemRollbackJournalStore,
        directory_fd: int,
        name: str,
        payload: bytes,
        old_payload: bytes | None,
        old_file: object,
    ) -> None:
        real_replace(self, directory_fd, name, payload, old_payload, old_file)
        if name.endswith(".transaction-rollback"):
            decoded = json.loads(payload)
            if decoded["payload"]["state"] == "cleanup_pending":
                os._exit(83)

    FilesystemRollbackJournalStore._replace_at = replace_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_successor_quarantine_move(root: str) -> None:
    real_rename = rollback_store_module._rename_noreplace_between

    def rename_then_crash(
        source: str,
        destination: str,
        source_directory_fd: int,
        destination_directory_fd: int,
    ) -> None:
        real_rename(
            source,
            destination,
            source_directory_fd,
            destination_directory_fd,
        )
        if destination.startswith("rollback-quarantine.") and destination.endswith(
            ".successor"
        ):
            os._exit(84)

    rollback_store_module._rename_noreplace_between = rename_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_terminal_intent_publish(root: str) -> None:
    real_replace = FilesystemRollbackJournalStore._replace_at

    def replace_then_crash(
        self: FilesystemRollbackJournalStore,
        directory_fd: int,
        name: str,
        payload: bytes,
        old_payload: bytes | None,
        old_file: object,
    ) -> None:
        real_replace(self, directory_fd, name, payload, old_payload, old_file)
        if name.endswith(".transaction-rollback"):
            decoded = json.loads(payload)
            if decoded["payload"]["state"] == "quarantined":
                os._exit(85)

    FilesystemRollbackJournalStore._replace_at = replace_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_tombstone_move(root: str) -> None:
    real_rename = rollback_store_module._rename_noreplace_between

    def rename_then_crash(
        source: str,
        destination: str,
        source_directory_fd: int,
        destination_directory_fd: int,
    ) -> None:
        real_rename(
            source,
            destination,
            source_directory_fd,
            destination_directory_fd,
        )
        if destination.endswith(".tombstone"):
            os._exit(86)

    rollback_store_module._rename_noreplace_between = rename_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_restoration_head(root: str) -> None:
    real_replace = FilesystemRollbackJournalStore._replace

    def replace_then_crash(
        self: FilesystemRollbackJournalStore,
        name: str,
        payload: bytes,
        old_payload: bytes | None,
    ) -> None:
        real_replace(self, name, payload, old_payload)
        if not name.endswith(".head"):
            return
        decoded = json.loads(payload)
        record = decoded.get("payload", {})
        if record.get("terminal_quarantine_refs"):
            os._exit(92)

    FilesystemRollbackJournalStore._replace = replace_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_during_anchor_index_replace(root: str) -> None:
    real_write_all = FilesystemRollbackJournalStore._write_all

    def write_all_then_crash(fd: int, payload: bytes) -> None:
        decoded = json.loads(payload)
        if decoded.get("kind") == "terminal-quarantine-anchor-index" and decoded.get(
            "payload", {}
        ).get("entries"):
            os.write(fd, payload[:17])
            os.fsync(fd)
            os._exit(93)
        real_write_all(fd, payload)

    FilesystemRollbackJournalStore._write_all = staticmethod(write_all_then_crash)
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _process_crash_after_revalidation(
    root: str,
    revalidation_call: int,
    exit_code: int,
) -> None:
    real_revalidate = FilesystemRollbackJournalStore._revalidate_recovery_capsule
    calls = 0

    def revalidate_then_crash(
        self: FilesystemRollbackJournalStore,
        capsule: object,
        **kwargs: object,
    ) -> None:
        nonlocal calls
        real_revalidate(self, capsule, **kwargs)
        calls += 1
        if calls == revalidation_call:
            os._exit(exit_code)

    FilesystemRollbackJournalStore._revalidate_recovery_capsule = revalidate_then_crash
    FilesystemRollbackJournalStore(root, authority_key=KEY)


def _install_nth_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    nth_write: int,
    mode: str,
) -> object:
    real_write = os.write
    calls = 0
    fail_after_short = False

    def controlled_write(fd: int, payload: bytes | memoryview) -> int:
        nonlocal calls, fail_after_short
        if fail_after_short:
            fail_after_short = False
            raise OSError(f"short write failure {nth_write}")
        calls += 1
        if calls == nth_write:
            if mode == "failure":
                raise OSError(f"write failure {nth_write}")
            prefix = max(1, len(payload) // 2)
            written = real_write(fd, payload[:prefix])
            fail_after_short = True
            return written
        return real_write(fd, payload)

    monkeypatch.setattr(os, "write", controlled_write)
    return real_write


def _owned_temps(root: Path, domain: str) -> tuple[str, ...]:
    locations = [root]
    staging = root / f".{domain}.cleanup-staging"
    if staging.is_dir():
        locations.append(staging)
    return tuple(
        sorted(
            path.name
            for location in locations
            for path in location.iterdir()
            if path.name.startswith(f".{domain}.")
            and path.name.endswith(
                (".immutable", ".rollback", ".tmp", ".transaction-rollback")
            )
        )
    )


def _file_inventory(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(root.iterdir())
        if path.is_file()
    }


def _exact_file_identity(path: Path) -> tuple[int, int, int, int, int, int, int]:
    value = path.stat(follow_symlinks=False)
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_gid,
        stat.S_IMODE(value.st_mode),
        value.st_nlink,
        value.st_size,
    )


def _replace_with_same_bytes(path: Path) -> None:
    payload = path.read_bytes()
    mode = stat.S_IMODE(path.stat().st_mode)
    path.unlink()
    path.write_bytes(payload)
    path.chmod(mode)


def test_records_are_frozen_strict_and_have_stable_canonical_digests() -> None:
    approved = _tuple("approved", "a")
    assert approved.tuple_digest == canonical_digest(
        canonical_json_bytes(
            {
                "immutable_refs": [
                    item.canonical_object() for item in approved.immutable_refs
                ],
                "schema_version": "bb.rl.phase5.active-approved-tuple.v1",
            }
        )
    )
    assert (
        approved.tuple_digest
        == "sha256:73befc724b84c19f7a0f278b98bffb91270341239c464dadc6b10e641b5a2a5b"
    )
    assert approved.canonical_bytes() == canonical_json_bytes(
        approved.canonical_object()
    )
    with pytest.raises(FrozenInstanceError):
        approved.tuple_digest = OTHER_REQUEST  # type: ignore[misc]
    with pytest.raises(ValueError, match="sorted"):
        ActiveApprovedTuple.from_refs(tuple(reversed(approved.immutable_refs)))
    with pytest.raises(ValueError, match="digest"):
        ImmutableObjectRef("cas://mutable", "bad")


@pytest.mark.parametrize(
    "journal_generation",
    (
        2,
        4,
        2 * rollback_store_module._MAX_ROLLBACK_QUARANTINE_PAIRS + 3,
    ),
)
def test_payload_ref_rejects_odd_or_unbounded_lineage_offsets(
    journal_generation: int,
) -> None:
    relative_path = rollback_store_module._payload_relative_path(
        "rollback-payload-lineage",
        RollbackPayloadKind.PHASE_RECEIPT,
        RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        journal_generation,
        0,
        RECEIPT,
    )
    with pytest.raises(RollbackValidationError, match="lineage"):
        RollbackPayloadRef(
            "rollback-payload-lineage",
            REQUEST,
            RECEIPT,
            RollbackPayloadKind.PHASE_RECEIPT,
            RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            journal_generation,
            0,
            relative_path,
        )


def test_journal_lineage_rejects_missing_duplicate_out_of_order_and_future_refs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal-lineage"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-journal-lineage"
    prepared, _, _, _, _ = _complete_terminal_quarantine(store, rollback_id)
    restored = store.get(rollback_id)
    assert restored is not None
    first_ref = restored.terminal_quarantine_refs[0]

    for unbound_generation in (
        3,
        5,
        2 * rollback_store_module._MAX_ROLLBACK_QUARANTINE_PAIRS + 3,
    ):
        with pytest.raises(RollbackValidationError, match="lineage"):
            replace(prepared, generation=unbound_generation)

    with pytest.raises(RollbackValidationError, match="unique"):
        replace(
            restored,
            generation=5,
            terminal_quarantine_refs=(first_ref, first_ref),
        )

    second_transaction = "b" * 32
    second_successor_name, second_tombstone_name = store._rollback_quarantine_names(
        second_transaction,
        rollback_id,
        first_ref.successor_record_digest,
    )
    second_ref = replace(
        first_ref,
        transaction_id=second_transaction,
        predecessor_generation=3,
        successor_generation=4,
        successor_name=second_successor_name,
        tombstone_name=second_tombstone_name,
    )
    with pytest.raises(RollbackValidationError, match="chronology"):
        replace(
            restored,
            generation=5,
            terminal_quarantine_refs=(second_ref, first_ref),
        )

    advanced = _advance(
        store,
        rollback_id,
        expected_generation=3,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        variant="lineage",
    )
    future_transaction = "c" * 32
    future_successor_name, future_tombstone_name = store._rollback_quarantine_names(
        future_transaction,
        rollback_id,
        first_ref.successor_record_digest,
    )
    future_ref = replace(
        first_ref,
        transaction_id=future_transaction,
        predecessor_generation=4,
        successor_generation=5,
        successor_name=future_successor_name,
        tombstone_name=future_tombstone_name,
    )
    with pytest.raises(RollbackValidationError, match="chronology"):
        replace(advanced, terminal_quarantine_refs=(future_ref,))


def test_journal_restart_idempotency_generation_revision_history_and_quarantine(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal"
    first = FilesystemRollbackJournalStore(root, authority_key=KEY)
    prepared = _prepare(first, "rollback-1")
    assert (prepared.generation, prepared.revision, prepared.phase) == (
        1,
        0,
        RollbackPhase.PREPARED,
    )
    assert _prepare(first, "rollback-1") == prepared
    with pytest.raises(RollbackIdempotencyConflict, match="different request"):
        _prepare(first, "rollback-1", variant="other")

    advanced = _advance(
        first,
        "rollback-1",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    assert (
        _advance(
            first,
            "rollback-1",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        )
        == advanced
    )
    with pytest.raises(RollbackIdempotencyConflict, match="divergent receipt"):
        _advance(
            first,
            "rollback-1",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            variant="other",
        )
    with pytest.raises(RollbackConflictError, match="compare-and-swap"):
        _advance(
            first,
            "rollback-1",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.REVOCATION_PUBLISHED,
        )
    first.close()

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert restarted.get("rollback-1") == advanced
    assert restarted.history("rollback-1") == (prepared, advanced)
    leaf = RollbackLeafError("episode-adapter", "episode-1", "close-failed", CAUSE)
    quarantined = _advance(
        restarted,
        "rollback-1",
        expected_generation=2,
        expected_revision=1,
        phase=RollbackPhase.QUARANTINED,
        leaf_errors=(leaf,),
    )
    assert quarantined.phase is RollbackPhase.QUARANTINED
    assert quarantined.phase_receipts[-1].leaf_errors == (leaf,)
    with pytest.raises(RollbackConflictError, match="terminal"):
        _advance(
            restarted,
            "rollback-1",
            expected_generation=3,
            expected_revision=2,
            phase=RollbackPhase.REVOCATION_PUBLISHED,
        )


def test_journal_rejects_non_monotonic_phase_and_wrong_generation_revision(
    tmp_path: Path,
) -> None:
    store = FilesystemRollbackJournalStore(tmp_path / "journal", authority_key=KEY)
    _prepare(store, "rollback-order")
    with pytest.raises(RollbackConflictError, match="monotonic"):
        _advance(
            store,
            "rollback-order",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.DEPENDENTS_QUARANTINED,
        )
    with pytest.raises(RollbackConflictError, match="compare-and-swap"):
        _advance(
            store,
            "rollback-order",
            expected_generation=2,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        )
    with pytest.raises(ValueError, match="leaf errors"):
        _advance(
            store,
            "rollback-order",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.QUARANTINED,
        )


def test_signed_request_and_all_phase_payloads_reconstruct_exactly_after_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "signed-payloads"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    record = _prepare(store, "rollback-payloads")
    request_payload = store.get_request("rollback-payloads")
    request_ref = store.get_request_ref("rollback-payloads")
    assert type(request_ref) is RollbackPayloadRef
    assert request_ref.kind is RollbackPayloadKind.REQUEST
    assert (request_ref.journal_generation, request_ref.journal_revision) == (
        1,
        0,
    )
    assert request_ref.payload_digest == canonical_digest(request_payload)
    assert store.get_request("rollback-payloads") == request_payload
    assert (root / request_ref.relative_path).is_file()
    assert request_ref.digest == canonical_digest(request_ref.canonical_bytes())
    with pytest.raises(FrozenInstanceError):
        request_ref.relative_path = "elsewhere"  # type: ignore[misc]

    payloads: dict[str, bytes] = {}
    for phase in (
        RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        RollbackPhase.REVOCATION_PUBLISHED,
        RollbackPhase.DEPENDENTS_QUARANTINED,
        RollbackPhase.ACTIVE_TUPLE_RESTORED,
        RollbackPhase.RERUN_RECORDED,
        RollbackPhase.SOURCE_DELETED,
        RollbackPhase.COMPLETE,
    ):
        payload = _phase_payload(
            store,
            "rollback-payloads",
            expected_generation=record.generation,
            expected_revision=record.revision,
            phase=phase,
        )
        record = store.advance(
            "rollback-payloads",
            expected_generation=record.generation,
            expected_revision=record.revision,
            phase=phase,
            receipt_digests=(canonical_digest(payload),),
            receipt_payloads=(payload,),
        )
        payloads[canonical_digest(payload)] = payload
        ref = store.get_receipt_ref("rollback-payloads", canonical_digest(payload))
        assert ref.kind is RollbackPayloadKind.PHASE_RECEIPT
        assert ref.phase is phase
        assert ref.journal_generation == record.generation
        assert ref.journal_revision == record.revision
        assert (
            store.get_receipt_payload("rollback-payloads", canonical_digest(payload))
            == payload
        )
    assert record.phase is RollbackPhase.COMPLETE
    assert len(store.history("rollback-payloads")) == 8
    store.close()

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    recovered = restarted.get("rollback-payloads")
    assert recovered == record
    assert restarted.get_request("rollback-payloads") == request_payload
    for digest, payload in payloads.items():
        assert restarted.get_receipt_payload("rollback-payloads", digest) == payload


def test_nested_request_attacks_fail_before_any_journal_payload_is_written(
    tmp_path: Path,
) -> None:
    root = tmp_path / "request-attacks"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    original = json.loads(
        _request_payload("rollback-request-attack", store.root.parent)
    )
    before = {path.name for path in root.iterdir()}

    attacks: list[dict[str, object]] = []
    wrong_schema = json.loads(canonical_json_bytes(original))
    wrong_schema["schema_version"] = "bb.rl.phase5.g4-rollback-request.v0"
    attacks.append(wrong_schema)
    extra_f6_key = json.loads(canonical_json_bytes(original))
    extra_f6_key["rerun_authoring_input"]["unexpected"] = True
    attacks.append(extra_f6_key)
    missing_revocation_binding = json.loads(canonical_json_bytes(original))
    del missing_revocation_binding["revocation_publish_request"]["binding"][
        "state_digest"
    ]
    attacks.append(missing_revocation_binding)
    empty_episode_set = json.loads(canonical_json_bytes(original))
    empty_episode_set["affected_episode_ids"] = []
    attacks.append(empty_episode_set)
    duplicate_source = json.loads(canonical_json_bytes(original))
    duplicate_source["source_deletion_plan"]["owned_sources"].append(
        duplicate_source["source_deletion_plan"]["owned_sources"][0]
    )
    attacks.append(duplicate_source)

    for attack in attacks:
        raw = canonical_json_bytes(attack)
        with pytest.raises(RollbackValidationError):
            store.prepare("rollback-request-attack", canonical_digest(raw), raw)
        assert {path.name for path in root.iterdir()} == before
        assert _owned_temps(root, "rollback-journal") == ()
    assert store.get("rollback-request-attack") is None


def test_nested_f6_and_source_deletion_attacks_leave_no_orphan_payloads(
    tmp_path: Path,
) -> None:
    root = tmp_path / "receipt-attacks"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    record = _prepare(store, "rollback-receipt-attack")
    for phase in (
        RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        RollbackPhase.REVOCATION_PUBLISHED,
        RollbackPhase.DEPENDENTS_QUARANTINED,
        RollbackPhase.ACTIVE_TUPLE_RESTORED,
    ):
        record = _advance(
            store,
            "rollback-receipt-attack",
            expected_generation=record.generation,
            expected_revision=record.revision,
            phase=phase,
        )

    valid_rerun = _phase_payload(
        store,
        "rollback-receipt-attack",
        expected_generation=record.generation,
        expected_revision=record.revision,
        phase=RollbackPhase.RERUN_RECORDED,
    )
    wrong_original_episode = json.loads(valid_rerun)
    for observed_phase in ("original", "cached"):
        observation = wrong_original_episode["body"]["rerun_report"][observed_phase]
        observation["episode_id"] = "wrong-original-episode"
        observation["episode_binding"]["episode_id"] = "wrong-original-episode"
        observation["durable"]["episode_id"] = "wrong-original-episode"
    attacked_rerun = canonical_json_bytes(wrong_original_episode)
    before_rerun = {path.name for path in root.iterdir()}
    with pytest.raises(RollbackValidationError, match="binding mismatch"):
        store.advance(
            "rollback-receipt-attack",
            expected_generation=record.generation,
            expected_revision=record.revision,
            phase=RollbackPhase.RERUN_RECORDED,
            receipt_digests=(canonical_digest(attacked_rerun),),
            receipt_payloads=(attacked_rerun,),
        )
    assert {path.name for path in root.iterdir()} == before_rerun
    assert _owned_temps(root, "rollback-journal") == ()

    record = store.advance(
        "rollback-receipt-attack",
        expected_generation=record.generation,
        expected_revision=record.revision,
        phase=RollbackPhase.RERUN_RECORDED,
        receipt_digests=(canonical_digest(valid_rerun),),
        receipt_payloads=(valid_rerun,),
    )
    valid_source = _phase_payload(
        store,
        "rollback-receipt-attack",
        expected_generation=record.generation,
        expected_revision=record.revision,
        phase=RollbackPhase.SOURCE_DELETED,
    )
    source_attacks: list[bytes] = []
    wrong_gate = json.loads(valid_source)
    wrong_gate["body"]["source_deletion_request"]["gates"]["rerun_receipt_ref"][
        "sha256"
    ] = OTHER_REQUEST
    source_attacks.append(canonical_json_bytes(wrong_gate))
    wrong_proof = json.loads(valid_source)
    wrong_proof["body"]["source_deletion_receipt"]["absence_proofs"][0][
        "prior_inode"
    ] = "99"
    source_attacks.append(canonical_json_bytes(wrong_proof))
    missing_gate_group = json.loads(valid_source)
    del missing_gate_group["body"]["source_deletion_request"]["gates"][
        "dependent_quarantine_refs"
    ]
    source_attacks.append(canonical_json_bytes(missing_gate_group))

    before_source = {path.name for path in root.iterdir()}
    for attacked_source in source_attacks:
        with pytest.raises(RollbackValidationError):
            store.advance(
                "rollback-receipt-attack",
                expected_generation=record.generation,
                expected_revision=record.revision,
                phase=RollbackPhase.SOURCE_DELETED,
                receipt_digests=(canonical_digest(attacked_source),),
                receipt_payloads=(attacked_source,),
            )
        assert {path.name for path in root.iterdir()} == before_source
        assert _owned_temps(root, "rollback-journal") == ()
    assert store.get("rollback-receipt-attack") == record


@pytest.mark.parametrize(
    "attack",
    (
        "nonexistent-authority",
        "alternate-manifest-digest",
        "alternate-descriptor-path",
        "renamed-secret",
        "substituted-original-request",
    ),
)
def test_live_f6_authoring_source_attacks_fail_before_prepare_writes(
    tmp_path: Path,
    attack: str,
) -> None:
    base = tmp_path / attack
    base.mkdir()
    root = base / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    request_object = json.loads(_request_payload("rollback-source-attack", base))
    authoring = request_object["rerun_authoring_input"]
    if attack == "nonexistent-authority":
        authoring["authority_bundle"]["path"] = str(base / "missing.json")
    elif attack == "alternate-manifest-digest":
        authoring["composition_manifest"]["sha256"] = OTHER_REQUEST
    elif attack == "alternate-descriptor-path":
        alternate = base / "alternate-composition.ref.json"
        alternate.write_bytes(b"composition-descriptor")
        alternate.chmod(0o400)
        authoring["composition_descriptor"]["path"] = str(alternate)
    elif attack == "renamed-secret":
        substitute = base / "renamed.secret"
        substitute_raw = b"renamed-secret"
        substitute.write_bytes(substitute_raw)
        substitute.chmod(0o400)
        authoring["secret_files"] = {
            "renamed": {
                "path": str(substitute),
                "sha256": canonical_digest(substitute_raw),
            }
        }
    else:
        substitute = base / "substituted-request.json"
        original = json.loads((base / "request.json").read_bytes())
        original["episode_id"] = "shape-valid-substituted-original"
        substitute_raw = canonical_json_bytes(original)
        substitute.write_bytes(substitute_raw)
        substitute.chmod(0o400)
        authoring["original_request"] = {
            "path": str(substitute),
            "sha256": canonical_digest(substitute_raw),
        }

    attacked = canonical_json_bytes(request_object)
    before = {path.name for path in root.iterdir()}
    with pytest.raises(RollbackValidationError):
        store.prepare(
            "rollback-source-attack",
            canonical_digest(attacked),
            attacked,
        )
    assert {path.name for path in root.iterdir()} == before
    assert _owned_temps(root, "rollback-journal") == ()
    assert store.get("rollback-source-attack") is None


def test_live_f6_authoring_source_drift_quarantines_on_cold_load(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source-drift" / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-source-drift")
    head_path = root / "journal.rollback-source-drift.head"
    head_before = head_path.read_bytes()
    history_before = {
        path.name for path in root.iterdir() if path.name.endswith(".history")
    }
    manifest = root.parent / "manifest.json"
    manifest.chmod(0o600)
    manifest.write_bytes(b"composition-manifest-drifted")
    manifest.chmod(0o400)
    store.close()

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        restarted.get("rollback-source-drift")
    assert head_path.read_bytes() == head_before
    assert {
        path.name for path in root.iterdir() if path.name.endswith(".history")
    } == history_before
    assert _owned_temps(root, "rollback-journal") == ()
    assert (root / "journal.rollback-source-drift.blocked").is_file()


@pytest.mark.parametrize(
    "source_name",
    (
        "authority_bundle",
        "composition_descriptor",
        "composition_manifest",
        "original_request",
        "secret",
        "rerun_input",
    ),
)
def test_prepare_rejects_same_byte_source_unlink_recreate_without_writes(
    tmp_path: Path,
    source_name: str,
) -> None:
    base = tmp_path / source_name
    base.mkdir()
    root = base / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    request_payload = _request_payload("rollback-same-byte", base)
    request = json.loads(request_payload)
    if source_name == "secret":
        source_path = Path(
            next(iter(request["rerun_authoring_input"]["secret_files"].values()))[
                "path"
            ]
        )
    elif source_name == "rerun_input":
        source_path = Path(request["rerun_input_path"])
    else:
        source_path = Path(request["rerun_authoring_input"][source_name]["path"])
    original_bytes = source_path.read_bytes()
    original_mode = stat.S_IMODE(source_path.stat().st_mode)
    source_path.unlink()
    source_path.write_bytes(original_bytes)
    source_path.chmod(original_mode)
    before = {path.name for path in root.iterdir()}

    with pytest.raises(RollbackValidationError, match="identity mismatch"):
        store.prepare(
            "rollback-same-byte",
            canonical_digest(request_payload),
            request_payload,
        )
    assert {path.name for path in root.iterdir()} == before
    assert _owned_temps(root, "rollback-journal") == ()
    assert store.get("rollback-same-byte") is None


def test_prepare_revalidates_pinned_sources_before_first_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "prepare-toctou"
    base.mkdir()
    root = base / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    request_payload = _request_payload("rollback-prepare-toctou", base)
    manifest = base / "manifest.json"
    manifest_bytes = manifest.read_bytes()
    manifest_mode = stat.S_IMODE(manifest.stat().st_mode)
    before = {path.name for path in root.iterdir()}
    real_revalidate = rollback_store_module._revalidate_source_capsules
    swapped = False

    def replace_then_revalidate(capsules: object) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            manifest.unlink()
            manifest.write_bytes(manifest_bytes)
            manifest.chmod(manifest_mode)
        real_revalidate(capsules)  # type: ignore[arg-type]

    monkeypatch.setattr(
        rollback_store_module,
        "_revalidate_source_capsules",
        replace_then_revalidate,
    )
    with pytest.raises(RollbackValidationError, match="authority changed"):
        store.prepare(
            "rollback-prepare-toctou",
            canonical_digest(request_payload),
            request_payload,
        )
    assert swapped
    assert {path.name for path in root.iterdir()} == before
    assert _owned_temps(root, "rollback-journal") == ()
    assert store.get("rollback-prepare-toctou") is None


def test_prepare_late_source_swap_inside_first_immutable_write_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "prepare-late-create"
    base.mkdir()
    root = base / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-prepare-late-create"
    request_payload = _request_payload(rollback_id, base)
    manifest = base / "manifest.json"
    before = _file_inventory(root)
    real_create = store._create_immutable
    swapped = False

    def create_then_swap(name: str, payload: bytes) -> None:
        nonlocal swapped
        real_create(name, payload)
        if not swapped:
            swapped = True
            _replace_with_same_bytes(manifest)

    monkeypatch.setattr(store, "_create_immutable", create_then_swap)
    with pytest.raises(
        RollbackValidationError,
        match="identity mismatch|authority changed",
    ):
        store.prepare(
            rollback_id,
            canonical_digest(request_payload),
            request_payload,
        )
    assert swapped
    assert _file_inventory(root) == before
    assert _owned_temps(root, "rollback-journal") == ()
    assert store.get(rollback_id) is None


def test_advance_late_source_swap_inside_first_payload_write_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "advance-late-payload" / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-advance-late-payload"
    _prepare(store, rollback_id)
    manifest = root.parent / "manifest.json"
    before = _file_inventory(root)
    real_store_payload = store._store_payload_locked
    swapped = False

    def store_then_swap(*args: object, **kwargs: object) -> None:
        nonlocal swapped
        real_store_payload(*args, **kwargs)
        if not swapped:
            swapped = True
            _replace_with_same_bytes(manifest)

    monkeypatch.setattr(store, "_store_payload_locked", store_then_swap)
    with pytest.raises(RollbackValidationError, match="authority changed"):
        _advance(
            store,
            rollback_id,
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        )
    assert swapped
    assert _file_inventory(root) == before
    assert _owned_temps(root, "rollback-journal") == ()


def test_prepare_swap_at_final_publication_boundary_rolls_back_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "prepare-final-boundary"
    base.mkdir()
    root = base / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-prepare-final-boundary"
    request_payload = _request_payload(rollback_id, base)
    manifest = base / "manifest.json"
    before = _file_inventory(root)
    real_revalidate = rollback_store_module._revalidate_source_capsules
    calls = 0

    def swap_on_final_revalidation(capsules: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            _replace_with_same_bytes(manifest)
        real_revalidate(capsules)  # type: ignore[arg-type]

    monkeypatch.setattr(
        rollback_store_module,
        "_revalidate_source_capsules",
        swap_on_final_revalidation,
    )
    with pytest.raises(RollbackValidationError, match="authority changed"):
        store.prepare(
            rollback_id,
            canonical_digest(request_payload),
            request_payload,
        )
    assert calls == 2
    assert _file_inventory(root) == before
    assert _owned_temps(root, "rollback-journal") == ()
    assert store.get(rollback_id) is None


def test_receipt_count_and_aggregate_preflight_rejects_without_orphans(
    tmp_path: Path,
) -> None:
    root = tmp_path / "receipt-preflight"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-preflight")
    before = {path.name for path in root.iterdir()}

    tiny_payloads = tuple(b"x" for _ in range(65))
    with pytest.raises(RollbackValidationError, match="count exceeds"):
        store.advance(
            "rollback-preflight",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            receipt_digests=tuple(
                canonical_digest(payload) for payload in tiny_payloads
            ),
            receipt_payloads=tiny_payloads,
        )
    assert {path.name for path in root.iterdir()} == before
    assert _owned_temps(root, "rollback-journal") == ()

    oversized = b"x" * (4 * 1024 * 1024 + 1)
    with pytest.raises(RollbackValidationError, match="aggregate"):
        store.advance(
            "rollback-preflight",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            receipt_digests=(canonical_digest(oversized),),
            receipt_payloads=(oversized,),
        )
    assert {path.name for path in root.iterdir()} == before
    assert _owned_temps(root, "rollback-journal") == ()
    record = store.get("rollback-preflight")
    assert record is not None and record.phase is RollbackPhase.PREPARED


def test_request_and_receipt_payloads_require_closed_canonical_bound_schemas(
    tmp_path: Path,
) -> None:
    store = FilesystemRollbackJournalStore(
        tmp_path / "payload-validation", authority_key=KEY
    )
    request = _request_payload("rollback-validation", store.root.parent)
    with pytest.raises(RollbackValidationError, match="digest mismatch"):
        store.prepare("rollback-validation", REQUEST, request)
    with pytest.raises(RollbackValidationError, match="canonical JSON object"):
        store.prepare(
            "rollback-validation",
            canonical_digest(request + b" "),
            request + b" ",
        )
    request_object = json.loads(request)
    request_object["unexpected"] = True
    extra_request = canonical_json_bytes(request_object)
    with pytest.raises(RollbackValidationError, match="exactly"):
        store.prepare(
            "rollback-validation",
            canonical_digest(extra_request),
            extra_request,
        )
    oversized = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(RollbackValidationError, match="size bound"):
        store.prepare(
            "rollback-validation",
            canonical_digest(oversized),
            oversized,
        )

    _prepare(store, "rollback-validation")
    receipt = _phase_payload(
        store,
        "rollback-validation",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    receipt_object = json.loads(receipt)
    receipt_object["body"]["unexpected"] = True
    extra_receipt = canonical_json_bytes(receipt_object)
    with pytest.raises(RollbackValidationError, match="exactly"):
        store.advance(
            "rollback-validation",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            receipt_digests=(canonical_digest(extra_receipt),),
            receipt_payloads=(extra_receipt,),
        )
    receipt_object = json.loads(receipt)
    receipt_object["journal_generation"] = 9
    stale_receipt = canonical_json_bytes(receipt_object)
    with pytest.raises(RollbackValidationError, match="binding mismatch"):
        store.advance(
            "rollback-validation",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            receipt_digests=(canonical_digest(stale_receipt),),
            receipt_payloads=(stale_receipt,),
        )


@pytest.mark.parametrize(
    "damage", ("missing-request", "tampered-request", "tampered-receipt")
)
def test_committed_payload_missing_or_tamper_quarantines_on_cold_restart(
    tmp_path: Path, damage: str
) -> None:
    root = tmp_path / damage
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-damage")
    advanced = _advance(
        store,
        "rollback-damage",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    assert advanced.phase_receipts
    target_ref = (
        advanced.request_payload_ref
        if damage.endswith("request")
        else advanced.phase_receipts[-1].receipt_refs[0]
    )
    store.close()
    target = root / target_ref.relative_path
    if damage == "missing-request":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"x")

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="payload"):
        restarted.get("rollback-damage")
    assert (root / "journal.rollback-damage.blocked").is_file()


def test_cross_rollback_signed_payload_substitution_is_quarantined(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cross-run"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-a")
    a = _advance(
        store,
        "rollback-a",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    _prepare(store, "rollback-b")
    b = _advance(
        store,
        "rollback-b",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    a_path = root / a.phase_receipts[-1].receipt_refs[0].relative_path
    b_path = root / b.phase_receipts[-1].receipt_refs[0].relative_path
    b_path.write_bytes(a_path.read_bytes())
    store.close()

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="payload"):
        restarted.get("rollback-b")
    assert restarted.get("rollback-a") == a


def test_uncommitted_payload_ref_is_not_visible_and_exact_retry_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "partial-payload"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-partial-payload")
    real_persist = store._persist_locked
    captured: list[object] = []

    def interrupt(record: object, old_payload: bytes | None) -> None:
        captured.append(record)
        raise OSError("journal commit interrupted")

    monkeypatch.setattr(store, "_persist_locked", interrupt)
    with pytest.raises(OSError, match="commit interrupted"):
        _advance(
            store,
            "rollback-partial-payload",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        )
    attempted = captured[0]
    assert hasattr(attempted, "phase_receipts")
    receipt_ref = attempted.phase_receipts[-1].receipt_refs[0]  # type: ignore[union-attr]
    assert not (root / receipt_ref.relative_path).exists()
    with pytest.raises(RollbackConflictError, match="not committed"):
        store.get_receipt_payload(
            "rollback-partial-payload", receipt_ref.payload_digest
        )
    monkeypatch.setattr(store, "_persist_locked", real_persist)
    recovered = _advance(
        store,
        "rollback-partial-payload",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    assert store.get_receipt_payload(
        "rollback-partial-payload", receipt_ref.payload_digest
    ) == _phase_payload(
        store,
        "rollback-partial-payload",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    assert recovered.generation == 2


@pytest.mark.parametrize(
    "failpoint",
    (
        "payload-write",
        "payload-file-fsync",
        "payload-dir-fsync",
        "journal-head-rename",
        "journal-commit",
    ),
)
def test_phase_payload_and_journal_crash_boundaries_restart_at_old_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failpoint: str,
) -> None:
    root = tmp_path / failpoint
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-crash")
    if failpoint == "payload-write":
        real_write_temp = store._write_temp
        failed = False

        def fail_write(name: str, payload: bytes) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("payload write crash")
            real_write_temp(name, payload)

        monkeypatch.setattr(store, "_write_temp", fail_write)
    elif failpoint in ("payload-file-fsync", "payload-dir-fsync"):
        real_fsync = os.fsync
        calls = 0
        target = 1 if failpoint == "payload-file-fsync" else 2

        def fail_fsync(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == target:
                raise OSError("payload fsync crash")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_fsync)
    elif failpoint == "journal-head-rename":
        monkeypatch.setattr(
            os,
            "replace",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("journal rename crash")
            ),
        )
    else:
        real_create = store._create_immutable

        def fail_commit(name: str, payload: bytes) -> None:
            if name.endswith(".commit"):
                raise OSError("journal commit crash")
            real_create(name, payload)

        monkeypatch.setattr(store, "_create_immutable", fail_commit)

    with pytest.raises(OSError, match="crash"):
        _advance(
            store,
            "rollback-crash",
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        )
    monkeypatch.undo()
    store.close()
    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    prepared = restarted.get("rollback-crash")
    assert prepared is not None and prepared.generation == 1
    advanced = _advance(
        restarted,
        "rollback-crash",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    assert advanced.generation == 2


def test_active_tuple_restart_append_only_history_and_old_tuple_new_generation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "active"
    old = _tuple("old", "a")
    current = _tuple("current", "b")
    store = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    generation_one = store.compare_and_swap(None, old, "bootstrap")
    generation_two = store.compare_and_swap(1, current, "promotion-2")
    restored = store.compare_and_swap(2, old, "rollback-1")
    assert restored.generation == 3
    assert restored.approved_tuple == generation_one.approved_tuple
    assert restored.digest != generation_one.digest
    assert store.compare_and_swap(2, old, "rollback-1") == restored
    with pytest.raises(RollbackIdempotencyConflict, match="different request"):
        store.compare_and_swap(2, current, "rollback-1")
    store.close()

    restarted = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    assert restarted.get() == restored
    history = restarted.history()
    assert tuple(entry.state for entry in history) == (
        generation_one,
        generation_two,
        restored,
    )
    assert tuple(entry.state_digest for entry in history) == tuple(
        entry.state.digest for entry in history
    )


def test_two_active_tuple_writers_have_one_winner_and_one_generation_conflict(
    tmp_path: Path,
) -> None:
    root = tmp_path / "active"
    bootstrap = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    bootstrap.compare_and_swap(None, _tuple("base", "a"), "bootstrap")
    writers = (
        FilesystemActiveApprovedTupleStore(root, authority_key=KEY),
        FilesystemActiveApprovedTupleStore(root, authority_key=KEY),
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def contend(index: int) -> None:
        barrier.wait()
        try:
            writers[index].compare_and_swap(
                1, _tuple(f"candidate-{index}", str(index + 1)), f"writer-{index}"
            )
        except RollbackConflictError:
            outcome = "conflict"
        else:
            outcome = "won"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=contend, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert sorted(outcomes) == ["conflict", "won"]
    assert bootstrap.get() is not None and bootstrap.get().generation == 2


@pytest.mark.parametrize("failure_call", tuple(range(1, 11)))
def test_journal_fsync_failure_never_commits_head_and_restart_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    root = tmp_path / f"journal-{failure_call}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    real_fsync = os.fsync
    calls = 0

    def failing_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError(f"deterministic fsync failure {failure_call}")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="deterministic fsync failure"):
        _prepare(store, "rollback-fsync")
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert store.get("rollback-fsync") is None
    store.close()

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _prepare(restarted, "rollback-fsync").phase is RollbackPhase.PREPARED


def test_partial_write_leaves_no_committed_record_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    real_write = os.write
    calls = 0

    def partial_then_fail(fd: int, payload: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, payload[:7])
        raise OSError("deterministic partial write")

    monkeypatch.setattr(os, "write", partial_then_fail)
    with pytest.raises(OSError, match="partial write"):
        _prepare(store, "rollback-partial")
    monkeypatch.setattr(os, "write", real_write)
    assert store.get("rollback-partial") is None
    assert not tuple(root.glob("*.tmp"))
    assert _prepare(store, "rollback-partial").generation == 1


def test_signed_tamper_is_quarantined_and_identity_cannot_be_reused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "journal"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-tamper")
    head = root / "journal.rollback-tamper.head"
    envelope = json.loads(head.read_bytes())
    envelope["payload"]["request_digest"] = OTHER_REQUEST
    envelope["payload_digest"] = canonical_digest(
        canonical_json_bytes(envelope["payload"])
    )
    head.write_bytes(canonical_json_bytes(envelope))

    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        store.get("rollback-tamper")
    assert not head.exists()
    assert tuple((root / ".quarantine").glob("*.corrupt"))
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        _prepare(store, "rollback-tamper")


def test_wrong_authority_key_detects_tamper_without_accepting_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "active"
    store = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    store.compare_and_swap(None, _tuple("approved", "a"), "bootstrap")
    store.close()
    wrong_authority = FilesystemActiveApprovedTupleStore(root, authority_key=OTHER_KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        wrong_authority.get()


def test_symlink_alias_leaf_and_root_replacement_are_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(RollbackCorruptionError, match="symlink|alias"):
        FilesystemRollbackJournalStore(alias, authority_key=KEY)

    root = tmp_path / "journal"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    (root / "journal.rollback-leaf.head").symlink_to(outside / "captured")
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        _prepare(store, "rollback-leaf")
    assert not (outside / "captured").exists()
    (root / "journal.rollback-leaf.head").unlink(missing_ok=True)

    root.rename(tmp_path / "old-journal")
    root.mkdir(mode=0o700)
    with pytest.raises(RollbackCorruptionError, match="identity changed"):
        _prepare(store, "rollback-root")
    assert not (root / "journal.rollback-root.head").exists()


def test_store_layout_is_0700_and_all_persisted_files_are_0600(tmp_path: Path) -> None:
    roots = (tmp_path / "journal", tmp_path / "active", tmp_path / "dependents")
    journal = FilesystemRollbackJournalStore(roots[0], authority_key=KEY)
    _prepare(journal, "rollback-modes")
    active = FilesystemActiveApprovedTupleStore(roots[1], authority_key=KEY)
    active.compare_and_swap(None, _tuple("approved", "a"), "bootstrap")
    dependents = FilesystemDependentQuarantineStore(roots[2], authority_key=KEY)
    dependents.register(_ownership("reward", "1", kind=DependentObjectKind.REWARD))
    for root in roots:
        root_mode, file_modes = _modes(root)
        assert root_mode == 0o700
        assert file_modes == {0o600}
        assert stat.S_IMODE((root / ".quarantine").stat().st_mode) == 0o700


def test_dependent_causal_traversal_is_exact_idempotent_and_restart_durable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dependents"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    checkpoint = _ownership("checkpoint", "1", kind=DependentObjectKind.CHECKPOINT)
    reward = _ownership(
        "reward",
        "2",
        kind=DependentObjectKind.REWARD,
        parents=(checkpoint.object_ref,),
    )
    evidence = _ownership(
        "evidence",
        "3",
        kind=DependentObjectKind.EVIDENCE,
        parents=(reward.object_ref,),
    )
    unrelated = _ownership(
        "unrelated",
        "4",
        kind=DependentObjectKind.EVIDENCE,
        episode_id="episode-2",
        run_id="run-2",
    )
    for ownership in (checkpoint, reward, evidence, unrelated):
        assert store.register(ownership).promotion_eligible

    assert {
        item.ownership.object_ref
        for item in store.list_owned(
            approved_tuple_digest=TUPLE_OWNER,
            episode_id="episode-1",
            run_id="run-1",
        )
    } == {checkpoint.object_ref, evidence.object_ref, reward.object_ref}
    receipts = store.quarantine_causal(
        "rollback-causal", CAUSE, (checkpoint.object_ref,)
    )
    assert {item.object_ref for item in receipts} == {
        checkpoint.object_ref,
        reward.object_ref,
        evidence.object_ref,
    }
    assert len({item.digest for item in receipts}) == 3
    assert (
        store.quarantine_causal("rollback-causal", CAUSE, (checkpoint.object_ref,))
        == receipts
    )
    assert store.get(unrelated.object_ref).promotion_eligible

    for object_ref in (checkpoint.object_ref, reward.object_ref, evidence.object_ref):
        record = store.get(object_ref)
        assert record is not None
        assert not record.promotion_eligible and not record.export_eligible
        with pytest.raises(DependentIneligibleError, match="promotion"):
            store.assert_promotion_eligible(object_ref)
        with pytest.raises(DependentIneligibleError, match="export"):
            store.assert_export_eligible(object_ref)
    store.close()

    restarted = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    assert (
        restarted.quarantine_causal("rollback-causal", CAUSE, (checkpoint.object_ref,))
        == receipts
    )
    assert not restarted.get(evidence.object_ref).export_eligible


def test_quarantine_is_irreversible_by_reregister_or_new_rerun_descendant(
    tmp_path: Path,
) -> None:
    store = FilesystemDependentQuarantineStore(
        tmp_path / "dependents", authority_key=KEY
    )
    checkpoint = _ownership("checkpoint", "1", kind=DependentObjectKind.CHECKPOINT)
    initial = store.register(checkpoint)
    store.quarantine_causal("rollback-irreversible", CAUSE, (checkpoint.object_ref,))
    assert not store.register(checkpoint).promotion_eligible
    changed_owner = _ownership(
        "checkpoint",
        "1",
        kind=DependentObjectKind.CHECKPOINT,
        registration_id=checkpoint.registration_id,
        run_id="rerun-2",
    )
    with pytest.raises(RollbackIdempotencyConflict, match="different ownership"):
        store.register(changed_owner)

    rerun_reward = _ownership(
        "rerun-reward",
        "2",
        kind=DependentObjectKind.REWARD,
        parents=(checkpoint.object_ref,),
        run_id="rerun-2",
    )
    inherited = store.register(rerun_reward)
    assert inherited.generation == 2
    assert not inherited.promotion_eligible and not inherited.export_eligible
    assert inherited.quarantine_receipts[0].rollback_id == "rollback-irreversible"
    assert store.register(rerun_reward) == inherited
    assert initial.ownership == store.get(checkpoint.object_ref).ownership


def test_same_rollback_id_different_dependent_request_conflicts(tmp_path: Path) -> None:
    store = FilesystemDependentQuarantineStore(
        tmp_path / "dependents", authority_key=KEY
    )
    reward = _ownership("reward", "1", kind=DependentObjectKind.REWARD)
    store.register(reward)
    store.quarantine_causal("rollback-idempotent", CAUSE, (reward.object_ref,))
    with pytest.raises(RollbackIdempotencyConflict, match="different"):
        store.quarantine_causal(
            "rollback-idempotent", OTHER_CAUSE, (reward.object_ref,)
        )


def test_dependent_tamper_blocks_incomplete_causal_traversal(tmp_path: Path) -> None:
    root = tmp_path / "dependents"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    reward = _ownership("reward", "1", kind=DependentObjectKind.REWARD)
    store.register(reward)
    head = root / f"dependent.{reward.object_ref.identity_digest[7:]}.head"
    envelope = json.loads(head.read_bytes())
    envelope["payload"]["promotion_eligible"] = False
    head.write_bytes(canonical_json_bytes(envelope))
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        store.get(reward.object_ref)
    with pytest.raises(RollbackCorruptionError):
        store.quarantine_causal("rollback-corrupt", CAUSE, (reward.object_ref,))


def test_active_tuple_generation_cas_is_subprocess_safe(tmp_path: Path) -> None:
    root = tmp_path / "active-process"
    bootstrap = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    bootstrap.compare_and_swap(None, _tuple("base-process", "a"), "bootstrap-process")
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_process_active_cas,
            args=(
                str(root),
                gate,
                output,
                f"process-{index}",
                str(index + 1),
                f"process-operation-{index}",
            ),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(timeout=10)
        assert not process.is_alive() and process.exitcode == 0
    outcomes = [output.get(timeout=2) for _ in processes]
    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "won"]
    assert bootstrap.get() is not None and bootstrap.get().generation == 2


def test_root_directory_flock_survives_lock_path_replacement_during_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "active-lock"
    writer_a = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    writer_a.compare_and_swap(None, _tuple("lock-base", "a"), "lock-bootstrap")
    head = root / "active-approved.head"
    before = head.read_bytes()
    entered = threading.Event()
    release = threading.Event()
    original_load = writer_a._load_locked
    outcome_a: list[str] = []

    def paused_load() -> object:
        result = original_load()
        entered.set()
        assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(writer_a, "_load_locked", paused_load)

    def mutate_a() -> None:
        try:
            writer_a.compare_and_swap(1, _tuple("old-writer", "1"), "old-writer")
        except BaseException as error:
            outcome_a.append(type(error).__name__)
        else:
            outcome_a.append("won")

    thread = threading.Thread(target=mutate_a)
    thread.start()
    assert entered.wait(timeout=5)
    _replace_lock_file(root)

    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    output = context.Queue()
    process = context.Process(
        target=_process_active_cas,
        args=(
            str(root),
            gate,
            output,
            "new-writer",
            "2",
            "new-writer",
        ),
    )
    process.start()
    gate.set()
    time.sleep(0.2)
    assert process.is_alive()
    assert thread.is_alive()
    assert head.read_bytes() == before

    release.set()
    thread.join(timeout=10)
    process.join(timeout=10)
    assert not thread.is_alive()
    assert not process.is_alive() and process.exitcode == 0
    assert outcome_a == ["won"]
    assert output.get(timeout=2) == ("conflict", None)
    reopened = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    assert reopened.get() is not None
    assert reopened.get().operation_id == "old-writer"


def test_valid_signed_old_history_hardlinks_cannot_replay_any_mutable_head(
    tmp_path: Path,
) -> None:
    journal_root = tmp_path / "journal-replay"
    journal = FilesystemRollbackJournalStore(journal_root, authority_key=KEY)
    _prepare(journal, "rollback-replay")
    _advance(
        journal,
        "rollback-replay",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    journal_head = journal_root / "journal.rollback-replay.head"
    journal_old = next(
        journal_root.glob("journal.rollback-replay.g00000000000000000001.*.history")
    )
    journal_head.unlink()
    os.link(journal_old, journal_head)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        journal.get("rollback-replay")
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        FilesystemRollbackJournalStore(journal_root, authority_key=KEY).get(
            "rollback-replay"
        )

    active_root = tmp_path / "active-replay"
    active = FilesystemActiveApprovedTupleStore(active_root, authority_key=KEY)
    active.compare_and_swap(None, _tuple("active-old", "a"), "active-old")
    active.compare_and_swap(1, _tuple("active-new", "b"), "active-new")
    active_head = active_root / "active-approved.head"
    active_old = next(
        active_root.glob("active-approved.g00000000000000000001.*.history")
    )
    active_head.unlink()
    os.link(active_old, active_head)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        active.get()
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        FilesystemActiveApprovedTupleStore(active_root, authority_key=KEY).get()

    dependent_root = tmp_path / "dependent-replay"
    dependent = FilesystemDependentQuarantineStore(dependent_root, authority_key=KEY)
    reward = _ownership("replay-reward", "7", kind=DependentObjectKind.REWARD)
    dependent.register(reward)
    dependent.quarantine_causal(
        "rollback-dependent-replay", CAUSE, (reward.object_ref,)
    )
    dependent_head = (
        dependent_root / f"dependent.{reward.object_ref.identity_digest[7:]}.head"
    )
    dependent_old = next(
        dependent_root.glob(
            f"dependent.{reward.object_ref.identity_digest[7:]}.g00000000000000000001.*.history"
        )
    )
    dependent_head.unlink()
    os.link(dependent_old, dependent_head)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        dependent.get(reward.object_ref)
    with pytest.raises(RollbackCorruptionError):
        FilesystemDependentQuarantineStore(
            dependent_root, authority_key=KEY
        ).assert_export_eligible(reward.object_ref)


def test_corrupt_predecessor_history_absorbingly_blocks_every_identity(
    tmp_path: Path,
) -> None:
    journal_root = tmp_path / "journal-predecessor"
    journal = FilesystemRollbackJournalStore(journal_root, authority_key=KEY)
    _prepare(journal, "rollback-predecessor")
    _advance(
        journal,
        "rollback-predecessor",
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    journal_old = next(journal_root.glob("*.g00000000000000000001.*.history"))
    journal_old.write_bytes(journal_old.read_bytes() + b"x")
    with pytest.raises(RollbackCorruptionError, match="history was quarantined"):
        journal.history("rollback-predecessor")
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        journal.get("rollback-predecessor")

    active_root = tmp_path / "active-predecessor"
    active = FilesystemActiveApprovedTupleStore(active_root, authority_key=KEY)
    active.compare_and_swap(None, _tuple("predecessor-old", "a"), "predecessor-old")
    active.compare_and_swap(1, _tuple("predecessor-new", "b"), "predecessor-new")
    active_old = next(active_root.glob("*.g00000000000000000001.*.history"))
    active_old.write_bytes(active_old.read_bytes() + b"x")
    with pytest.raises(RollbackCorruptionError, match="history was quarantined"):
        active.history()
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        active.get()

    dependent_root = tmp_path / "dependent-predecessor"
    dependents = FilesystemDependentQuarantineStore(dependent_root, authority_key=KEY)
    evidence = _ownership(
        "predecessor-evidence", "8", kind=DependentObjectKind.EVIDENCE
    )
    dependents.register(evidence)
    dependents.quarantine_causal(
        "rollback-predecessor-dependent", CAUSE, (evidence.object_ref,)
    )
    dependent_old = next(
        dependent_root.glob(
            f"dependent.{evidence.object_ref.identity_digest[7:]}.g00000000000000000001.*.history"
        )
    )
    dependent_old.write_bytes(dependent_old.read_bytes() + b"x")
    with pytest.raises(RollbackCorruptionError, match="history was quarantined"):
        dependents.get(evidence.object_ref)
    with pytest.raises(RollbackCorruptionError):
        dependents.assert_promotion_eligible(evidence.object_ref)


def test_command_identity_bindings_are_global_durable_and_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal_root = tmp_path / "journal-binding"
    journal = FilesystemRollbackJournalStore(journal_root, authority_key=KEY)
    real_persist = journal._persist_locked
    monkeypatch.setattr(
        journal,
        "_persist_locked",
        lambda record, old_payload: (_ for _ in ()).throw(
            OSError("prepare interrupted after binding")
        ),
    )
    with pytest.raises(OSError, match="after binding"):
        _prepare(journal, "rollback-bound")
    monkeypatch.setattr(journal, "_persist_locked", real_persist)
    assert not any(
        path.name.startswith("request.rollback-bound.")
        for path in journal_root.iterdir()
    )
    prepared = _prepare(
        FilesystemRollbackJournalStore(journal_root, authority_key=KEY),
        "rollback-bound",
    )
    assert prepared.request_digest == canonical_digest(
        _request_payload("rollback-bound", journal_root.parent)
    )
    with pytest.raises(RollbackIdempotencyConflict, match="different request"):
        _prepare(
            FilesystemRollbackJournalStore(
                journal_root,
                authority_key=KEY,
            ),
            "rollback-bound",
            variant="other",
        )

    active_root = tmp_path / "active-binding"
    active = FilesystemActiveApprovedTupleStore(active_root, authority_key=KEY)
    first = active.compare_and_swap(None, _tuple("binding-first", "a"), "op-reused")
    active.compare_and_swap(1, _tuple("binding-second", "b"), "op-second")
    assert active.compare_and_swap(None, first.approved_tuple, "op-reused") == first
    with pytest.raises(RollbackIdempotencyConflict, match="different request"):
        active.compare_and_swap(2, _tuple("binding-third", "c"), "op-reused")

    dependent_root = tmp_path / "dependent-binding"
    dependents = FilesystemDependentQuarantineStore(dependent_root, authority_key=KEY)
    first_owner = _ownership(
        "binding-reward",
        "1",
        kind=DependentObjectKind.REWARD,
        registration_id="global-registration",
    )
    second_owner = _ownership(
        "binding-evidence",
        "2",
        kind=DependentObjectKind.EVIDENCE,
        registration_id="global-registration",
    )
    assert dependents.register(first_owner).ownership == first_owner
    with pytest.raises(RollbackIdempotencyConflict, match="different ownership"):
        dependents.register(second_owner)
    restarted = FilesystemDependentQuarantineStore(dependent_root, authority_key=KEY)
    assert restarted.register(first_owner).ownership == first_owner


@pytest.mark.parametrize("fail_after", (0, 1, 2, 3))
def test_partial_causal_quarantine_intent_fails_closed_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_after: int,
) -> None:
    root = tmp_path / f"partial-causal-{fail_after}"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    checkpoint = _ownership(
        f"partial-checkpoint-{fail_after}",
        "1",
        kind=DependentObjectKind.CHECKPOINT,
    )
    reward_a = _ownership(
        f"partial-reward-a-{fail_after}",
        "2",
        kind=DependentObjectKind.REWARD,
        parents=(checkpoint.object_ref,),
    )
    reward_b = _ownership(
        f"partial-reward-b-{fail_after}",
        "3",
        kind=DependentObjectKind.REWARD,
        parents=(checkpoint.object_ref,),
    )
    evidence = _ownership(
        f"partial-evidence-{fail_after}",
        "4",
        kind=DependentObjectKind.EVIDENCE,
        parents=(reward_a.object_ref, reward_b.object_ref),
    )
    closure = (checkpoint, reward_a, reward_b, evidence)
    for ownership in closure:
        store.register(ownership)
    real_publish = store._publish_records_locked
    publications = 0

    def fail_during_closure(records: object, old_payload: bytes | None) -> object:
        nonlocal publications
        if publications == fail_after:
            raise OSError(f"causal failpoint {fail_after}")
        publications += 1
        return real_publish(records, old_payload)

    monkeypatch.setattr(store, "_publish_records_locked", fail_during_closure)
    with pytest.raises(OSError, match="causal failpoint"):
        store.quarantine_causal(
            f"rollback-partial-{fail_after}",
            CAUSE,
            (checkpoint.object_ref,),
        )
    monkeypatch.setattr(store, "_publish_records_locked", real_publish)
    store.close()

    restarted = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    for ownership in closure:
        with pytest.raises(DependentIneligibleError, match="incomplete"):
            restarted.assert_export_eligible(ownership.object_ref)
    with pytest.raises(DependentIneligibleError, match="incomplete"):
        with restarted.read_fence():
            raise AssertionError("incomplete intent must not yield a read fence")

    late_child = _ownership(
        f"partial-late-child-{fail_after}",
        "5",
        kind=DependentObjectKind.EVIDENCE,
        parents=(checkpoint.object_ref,),
        run_id=f"late-run-{fail_after}",
    )
    inherited = restarted.register(late_child)
    assert not inherited.promotion_eligible and not inherited.export_eligible
    receipts = restarted.quarantine_causal(
        f"rollback-partial-{fail_after}",
        CAUSE,
        (checkpoint.object_ref,),
    )
    assert {receipt.object_ref for receipt in receipts} == {
        item.object_ref for item in closure
    }
    with restarted.read_fence() as fenced:
        assert {record.ownership.object_ref for record in fenced} == {
            *(item.object_ref for item in closure),
            late_child.object_ref,
        }


def test_inherited_quarantine_child_has_no_eligible_publication_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "inherited-atomic"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    checkpoint = _ownership(
        "atomic-checkpoint", "1", kind=DependentObjectKind.CHECKPOINT
    )
    store.register(checkpoint)
    store.quarantine_causal("rollback-atomic", CAUSE, (checkpoint.object_ref,))
    child = _ownership(
        "atomic-child",
        "2",
        kind=DependentObjectKind.REWARD,
        parents=(checkpoint.object_ref,),
        run_id="atomic-rerun",
    )
    real_publish = store._publish_versioned
    monkeypatch.setattr(
        store,
        "_publish_versioned",
        lambda **kwargs: (_ for _ in ()).throw(
            OSError("child publication interrupted")
        ),
    )
    with pytest.raises(OSError, match="child publication interrupted"):
        store.register(child)
    monkeypatch.setattr(store, "_publish_versioned", real_publish)
    assert not (
        root / f"dependent.{child.object_ref.identity_digest[7:]}.head"
    ).exists()
    store.close()

    restarted = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    assert restarted.get(child.object_ref) is None
    recovered = restarted.register(child)
    assert recovered.generation == 2
    assert not recovered.promotion_eligible and not recovered.export_eligible


def test_corruption_marker_is_durable_before_suspect_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "marker-order"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, "rollback-marker-order")
    head = root / "journal.rollback-marker-order.head"
    head.write_bytes(head.read_bytes() + b"x")
    real_rename = os.rename

    def fail_move(*args: object, **kwargs: object) -> None:
        raise OSError("move interrupted after marker")

    monkeypatch.setattr(os, "rename", fail_move)
    with pytest.raises(OSError, match="after marker"):
        store.get("rollback-marker-order")
    monkeypatch.setattr(os, "rename", real_rename)
    assert (root / "journal.rollback-marker-order.blocked").is_file()
    assert head.is_file()
    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        restarted.get("rollback-marker-order")


def test_corrupt_quarantine_operation_becomes_absorbing_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "operation-corrupt"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    reward = _ownership(
        "operation-corrupt-reward", "1", kind=DependentObjectKind.REWARD
    )
    store.register(reward)
    real_publish = store._publish_records_locked
    monkeypatch.setattr(
        store,
        "_publish_records_locked",
        lambda records, old_payload: (_ for _ in ()).throw(
            OSError("operation left incomplete")
        ),
    )
    with pytest.raises(OSError, match="left incomplete"):
        store.quarantine_causal(
            "rollback-operation-corrupt", CAUSE, (reward.object_ref,)
        )
    monkeypatch.setattr(store, "_publish_records_locked", real_publish)
    request = root / "quarantine.rollback-operation-corrupt.request"
    request.write_bytes(request.read_bytes() + b"x")
    with pytest.raises(RollbackCorruptionError, match="operation was quarantined"):
        store.assert_export_eligible(reward.object_ref)
    assert (root / "quarantine.rollback-operation-corrupt.blocked").is_file()
    with pytest.raises(RollbackCorruptionError, match="blocked"):
        store.quarantine_causal(
            "rollback-operation-corrupt", CAUSE, (reward.object_ref,)
        )


def test_causal_graph_rejects_missing_parent_cycles_and_duplicate_roots(
    tmp_path: Path,
) -> None:
    store = FilesystemDependentQuarantineStore(
        tmp_path / "causal-validation", authority_key=KEY
    )
    missing_parent = _ref("missing-parent", "9")
    child = _ownership(
        "missing-child",
        "1",
        kind=DependentObjectKind.EVIDENCE,
        parents=(missing_parent,),
    )
    with pytest.raises(RollbackConflictError, match="parent"):
        store.register(child)

    root = _ownership("cycle-root", "2", kind=DependentObjectKind.CHECKPOINT)
    store.register(root)
    descendant = _ownership(
        "cycle-descendant",
        "3",
        kind=DependentObjectKind.REWARD,
        parents=(root.object_ref,),
    )
    store.register(descendant)
    changed_root = DependentOwnership(
        root.registration_id,
        root.approved_tuple_digest,
        root.episode_id,
        root.run_id,
        root.object_kind,
        root.object_ref,
        (descendant.object_ref,),
    )
    with pytest.raises(RollbackIdempotencyConflict, match="different ownership"):
        store.register(changed_root)
    with pytest.raises(ValueError, match="unique"):
        store.quarantine_causal(
            "rollback-duplicate-roots",
            CAUSE,
            (root.object_ref, root.object_ref),
        )


@pytest.mark.parametrize("failure_call", tuple(range(1, 9)))
def test_active_cas_fsync_failure_at_every_boundary_recovers_exact_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    root = tmp_path / f"active-fsync-{failure_call}"
    store = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    real_fsync = os.fsync
    calls = 0

    def failing_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError(f"active fsync failpoint {failure_call}")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="active fsync failpoint"):
        store.compare_and_swap(
            None, _tuple(f"active-fsync-{failure_call}", "a"), "active-fsync-op"
        )
    monkeypatch.setattr(os, "fsync", real_fsync)
    store.close()
    restarted = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
    assert restarted.get() is None
    assert (
        restarted.compare_and_swap(
            None, _tuple(f"active-fsync-{failure_call}", "a"), "active-fsync-op"
        ).generation
        == 1
    )


@pytest.mark.parametrize("failure_call", tuple(range(1, 11)))
def test_dependent_register_fsync_failure_has_no_eligible_partial_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    root = tmp_path / f"dependent-register-fsync-{failure_call}"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    ownership = _ownership(
        f"register-fsync-{failure_call}",
        "6",
        kind=DependentObjectKind.EVIDENCE,
    )
    real_fsync = os.fsync
    calls = 0

    def failing_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError(f"register fsync failpoint {failure_call}")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="register fsync failpoint"):
        store.register(ownership)
    monkeypatch.setattr(os, "fsync", real_fsync)
    store.close()
    restarted = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    assert restarted.get(ownership.object_ref) is None
    assert restarted.register(ownership).promotion_eligible


@pytest.mark.parametrize("failure_call", tuple(range(1, 13)))
def test_quarantine_fsync_failure_intent_gates_or_precedes_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    root = tmp_path / f"quarantine-fsync-{failure_call}"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    reward = _ownership(
        f"quarantine-fsync-{failure_call}",
        "7",
        kind=DependentObjectKind.REWARD,
    )
    store.register(reward)
    real_fsync = os.fsync
    calls = 0

    def failing_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError(f"quarantine fsync failpoint {failure_call}")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="quarantine fsync failpoint"):
        store.quarantine_causal(
            f"rollback-quarantine-fsync-{failure_call}",
            CAUSE,
            (reward.object_ref,),
        )
    monkeypatch.setattr(os, "fsync", real_fsync)
    request = root / f"quarantine.rollback-quarantine-fsync-{failure_call}.request"
    if request.exists():
        with pytest.raises(DependentIneligibleError):
            store.assert_export_eligible(reward.object_ref)
    else:
        store.assert_export_eligible(reward.object_ref)
    store.close()
    restarted = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    receipts = restarted.quarantine_causal(
        f"rollback-quarantine-fsync-{failure_call}",
        CAUSE,
        (reward.object_ref,),
    )
    assert len(receipts) == 1
    with pytest.raises(DependentIneligibleError, match="export"):
        restarted.assert_export_eligible(reward.object_ref)


def test_process_kill_mid_causal_quarantine_is_fail_closed_after_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "process-kill-causal"
    store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    checkpoint = _ownership("kill-checkpoint", "1", kind=DependentObjectKind.CHECKPOINT)
    reward = _ownership(
        "kill-reward",
        "2",
        kind=DependentObjectKind.REWARD,
        parents=(checkpoint.object_ref,),
    )
    evidence = _ownership(
        "kill-evidence",
        "3",
        kind=DependentObjectKind.EVIDENCE,
        parents=(reward.object_ref,),
    )
    for ownership in (checkpoint, reward, evidence):
        store.register(ownership)
    store.close()
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_process_crash_quarantine,
        args=(
            str(root),
            "rollback-process-kill",
            checkpoint.object_ref,
            1,
        ),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 77

    restarted = FilesystemDependentQuarantineStore(root, authority_key=KEY)
    for ownership in (checkpoint, reward, evidence):
        with pytest.raises(DependentIneligibleError, match="incomplete"):
            restarted.assert_promotion_eligible(ownership.object_ref)
    receipts = restarted.quarantine_causal(
        "rollback-process-kill", CAUSE, (checkpoint.object_ref,)
    )
    assert len(receipts) == 3
    with restarted.read_fence() as fenced:
        assert len(fenced) == 3


def test_restart_cleans_only_owned_abandoned_temps_after_process_death(
    tmp_path: Path,
) -> None:
    root = tmp_path / "temp-crash"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_process_crash_during_temp_write,
        args=(str(root),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 78
    assert _owned_temps(root, "rollback-journal")
    foreign = root / ".foreign-domain.00000000000000000000000000000000.tmp"
    foreign.write_bytes(b"foreign")
    os.chmod(foreign, 0o600)

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _owned_temps(root, "rollback-journal") == ()
    assert foreign.read_bytes() == b"foreign"
    assert restarted.get("rollback-temp-crash") is None
    assert _prepare(restarted, "rollback-temp-crash").phase is RollbackPhase.PREPARED


def test_restart_cleans_crashed_transaction_rollback_temp_and_recovers_head(
    tmp_path: Path,
) -> None:
    root = tmp_path / "transaction-rollback-temp"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-transaction-temp-crash"
    prepared = _prepare(store, rollback_id)
    head = root / f"journal.{rollback_id}.head"
    head_before = head.read_bytes()
    histories_before = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.endswith(".history")
    }
    commits_before = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.endswith(".commit")
    }
    store.close()

    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_process_crash_during_transaction_rollback,
        args=(str(root),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 79
    assert any(
        name.endswith(".transaction-rollback")
        for name in _owned_temps(root, "rollback-journal")
    )
    foreign = (
        root / ".foreign-domain.00000000000000000000000000000000.transaction-rollback"
    )
    foreign_payload = b"foreign-transaction-rollback"
    foreign.write_bytes(foreign_payload)
    foreign.chmod(0o600)

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    recovered = restarted.get(rollback_id)
    _assert_semantic_restoration(recovered, prepared)
    assert head.read_bytes() != head_before
    current_histories = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.endswith(".history")
    }
    current_commits = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.endswith(".commit")
    }
    assert histories_before.items() <= current_histories.items()
    assert commits_before.items() <= current_commits.items()
    assert _owned_temps(root, "rollback-journal") == ()
    assert foreign.read_bytes() == foreign_payload


def test_signed_intent_replay_cannot_rollback_committed_successor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "committed-successor-replay"
    rollback_id = "rollback-committed-successor-replay"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, rollback_id)
    _advance(
        store,
        rollback_id,
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    history = store.history(rollback_id)
    prior_raw = (root / store._history_name(history[0])).read_bytes()
    successor_raw = (root / f"journal.{rollback_id}.head").read_bytes()
    transaction_id = "1" * 32
    intent = store._rollback_intent_bytes(
        transaction_id,
        prior_raw,
        successor_raw,
    )
    intent_path = root / f".rollback-journal.{transaction_id}.transaction-rollback"
    intent_path.write_bytes(intent)
    intent_path.chmod(0o600)
    store.close()
    before = _file_inventory(root)
    with pytest.raises(
        RollbackCorruptionError,
        match="rollback intent is invalid",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_multiple_transaction_intents_fail_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "multiple-intents"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-multiple-intents"
    _prepare(store, rollback_id)
    _advance(
        store,
        rollback_id,
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    records = store.history(rollback_id)
    first_raw = (root / store._history_name(records[0])).read_bytes()
    second_raw = (root / store._history_name(records[1])).read_bytes()
    first_id = "2" * 32
    first = store._rollback_intent_bytes(
        first_id,
        first_raw,
        second_raw,
    )
    first_path = root / f".rollback-journal.{first_id}.transaction-rollback"
    first_path.write_bytes(first)
    first_path.chmod(0o600)
    second_id = "3" * 32
    second_path = root / f".rollback-journal.{second_id}.transaction-rollback"
    second_path.write_bytes(first)
    second_path.chmod(0o600)
    store.close()
    before = _file_inventory(root)
    with pytest.raises(
        RollbackCorruptionError,
        match="multiple transaction rollback intents",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_already_restored_prior_head_only_cleans_valid_intent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "already-prior"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-transaction-temp-crash"
    prepared = _prepare(store, rollback_id)
    store.close()
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_process_crash_during_transaction_rollback,
        args=(str(root),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 79
    intent_names = tuple(
        name
        for name in _owned_temps(root, "rollback-journal")
        if name.endswith(".transaction-rollback")
    )
    assert len(intent_names) == 1
    prior_history = next(
        path
        for path in root.iterdir()
        if ".g00000000000000000001." in path.name and path.name.endswith(".history")
    )
    head = root / f"journal.{rollback_id}.head"
    transaction_id = intent_names[0].split(".")[2]
    displaced = root / f".rollback-journal.{transaction_id}.displaced-head"
    os.replace(head, displaced)
    head.write_bytes(prior_history.read_bytes())
    head.chmod(0o600)

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _assert_semantic_restoration(restarted.get(rollback_id), prepared)
    assert not (root / intent_names[0]).exists()


def test_crash_during_signed_intent_recovery_resumes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "recovery-crash"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-transaction-temp-crash"
    prepared = _prepare(store, rollback_id)
    store.close()
    context = multiprocessing.get_context("spawn")
    first = context.Process(
        target=_process_crash_during_transaction_rollback,
        args=(str(root),),
    )
    first.start()
    first.join(timeout=10)
    assert not first.is_alive() and first.exitcode == 79
    second = context.Process(
        target=_process_crash_during_intent_recovery,
        args=(str(root),),
    )
    second.start()
    second.join(timeout=10)
    assert not second.is_alive() and second.exitcode == 80
    assert any(
        name.endswith(".transaction-rollback")
        for name in _owned_temps(root, "rollback-journal")
    )

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _assert_semantic_restoration(restarted.get(rollback_id), prepared)
    assert _owned_temps(root, "rollback-journal") == ()


@pytest.mark.parametrize(
    ("crash_target", "exit_code"),
    (
        (_process_crash_after_successor_displacement, 81),
        (_process_crash_after_prior_candidate_create, 82),
        (_process_crash_during_intent_recovery, 80),
        (_process_crash_after_cleanup_intent_publish, 83),
        (_process_crash_after_successor_quarantine_move, 84),
        (_process_crash_after_terminal_intent_publish, 85),
        (_process_crash_after_tombstone_move, 86),
    ),
)
def test_each_signed_intent_recovery_crash_boundary_resumes(
    tmp_path: Path,
    crash_target: object,
    exit_code: int,
) -> None:
    root = tmp_path / f"recovery-boundary-{exit_code}"
    rollback_id = "rollback-transaction-temp-crash"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    prepared = _prepare(store, rollback_id)
    store.close()
    context = multiprocessing.get_context("spawn")
    publication_crash = context.Process(
        target=_process_crash_during_transaction_rollback,
        args=(str(root),),
    )
    publication_crash.start()
    publication_crash.join(timeout=10)
    assert not publication_crash.is_alive() and publication_crash.exitcode == 79
    recovery_crash = context.Process(
        target=crash_target,
        args=(str(root),),
    )
    recovery_crash.start()
    recovery_crash.join(timeout=10)
    assert not recovery_crash.is_alive()
    assert recovery_crash.exitcode == exit_code

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _assert_semantic_restoration(restarted.get(rollback_id), prepared)
    assert _owned_temps(root, "rollback-journal") == ()


@pytest.mark.parametrize(
    ("revalidation_call", "exit_code"),
    ((2, 87), (3, 88), (5, 89), (6, 90), (7, 91)),
)
def test_each_post_fsync_revalidation_crash_boundary_resumes(
    tmp_path: Path,
    revalidation_call: int,
    exit_code: int,
) -> None:
    root = tmp_path / f"post-fsync-boundary-{revalidation_call}"
    rollback_id = "rollback-transaction-temp-crash"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    prepared = _prepare(store, rollback_id)
    store.close()
    context = multiprocessing.get_context("spawn")
    publication_crash = context.Process(
        target=_process_crash_during_transaction_rollback,
        args=(str(root),),
    )
    publication_crash.start()
    publication_crash.join(timeout=10)
    assert not publication_crash.is_alive() and publication_crash.exitcode == 79
    recovery_crash = context.Process(
        target=_process_crash_after_revalidation,
        args=(
            str(root),
            revalidation_call,
            exit_code,
        ),
    )
    recovery_crash.start()
    recovery_crash.join(timeout=10)
    assert not recovery_crash.is_alive()
    assert recovery_crash.exitcode == exit_code

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _assert_semantic_restoration(restarted.get(rollback_id), prepared)
    assert _owned_temps(root, "rollback-journal") == ()


@pytest.mark.parametrize(
    "authority",
    ("head", "predecessor-history", "predecessor-commit"),
)
def test_recovery_capsule_rejects_same_byte_authority_replacement(
    tmp_path: Path,
    authority: str,
) -> None:
    root = tmp_path / f"capsule-{authority}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = f"rollback-capsule-{authority}"
    _, intent_name, _, _ = _install_active_recovery_intent(
        store,
        rollback_id,
    )
    capsule = store._preflight_transaction_rollback_intent(intent_name)
    try:
        target_name = {
            "head": capsule.head_name,
            "predecessor-history": capsule.predecessor.name,
            "predecessor-commit": capsule.predecessor_commit.name,
        }[authority]
        target = root / target_name
        replacement = root / f"{target_name}.replacement"
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, target)
        attacked = _file_inventory(root)

        with pytest.raises(
            RollbackCorruptionError,
            match="recovery authority identity changed",
        ):
            store._recover_transaction_rollback(capsule)
        assert _file_inventory(root) == attacked
    finally:
        capsule.close()


@pytest.mark.parametrize("late_authority", ("history", "commit"))
def test_recovery_capsule_rejects_late_successor_authority(
    tmp_path: Path,
    late_authority: str,
) -> None:
    root = tmp_path / f"late-successor-{late_authority}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = f"rollback-late-successor-{late_authority}"
    _, intent_name, _, _ = _install_active_recovery_intent(
        store,
        rollback_id,
    )
    capsule = store._preflight_transaction_rollback_intent(intent_name)
    try:
        late_name = (
            capsule.successor_history_name
            if late_authority == "history"
            else capsule.successor_commit_name
        )
        late_path = root / late_name
        late_path.write_bytes(b"late-successor-authority")
        late_path.chmod(0o600)
        attacked = _file_inventory(root)

        with pytest.raises(
            RollbackCorruptionError,
            match="successor authority appeared",
        ):
            store._recover_transaction_rollback(capsule)
        assert _file_inventory(root) == attacked
    finally:
        capsule.close()


@pytest.mark.parametrize(
    ("injection_call", "expected_intent_state"),
    ((2, "active"), (3, "active"), (4, "cleanup_pending")),
)
def test_late_successor_during_recovery_never_clobbers_authority(
    tmp_path: Path,
    injection_call: int,
    expected_intent_state: str,
) -> None:
    root = tmp_path / f"late-recovery-{injection_call}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = f"rollback-late-recovery-{injection_call}"
    _, intent_name, prior_raw, successor_raw = _install_active_recovery_intent(
        store, rollback_id
    )
    capsule = store._preflight_transaction_rollback_intent(intent_name)
    late_path = root / capsule.successor_history_name
    late_payload = b"late-successor-history"
    real_revalidate = store._revalidate_recovery_capsule
    calls = 0

    def inject_late_authority(
        recovery_capsule: object,
        **kwargs: object,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == injection_call:
            late_path.write_bytes(late_payload)
            late_path.chmod(0o600)
        real_revalidate(recovery_capsule, **kwargs)

    store._revalidate_recovery_capsule = inject_late_authority
    try:
        with pytest.raises(
            RollbackCorruptionError,
            match="successor authority appeared",
        ):
            store._recover_transaction_rollback(capsule)
        assert late_path.read_bytes() == late_payload
        head = root / capsule.head_name
        intent = json.loads((root / intent_name).read_bytes())
        assert intent["payload"]["state"] == expected_intent_state
        if expected_intent_state == "active":
            assert head.read_bytes() == successor_raw
            assert not (root / capsule.displaced_name).exists()
            assert not (root / capsule.candidate_name).exists()
        else:
            assert head.read_bytes() == prior_raw
            assert (root / capsule.displaced_name).read_bytes() == successor_raw
    finally:
        store._revalidate_recovery_capsule = real_revalidate
        capsule.close()


@pytest.mark.parametrize(
    ("injection_call", "target_kind", "cold_valid"),
    (
        (4, "intent", True),
        (5, "successor-quarantine", False),
        (6, "intent", True),
    ),
)
def test_inflight_terminal_same_byte_swaps_preserve_all_authority(
    tmp_path: Path,
    injection_call: int,
    target_kind: str,
    cold_valid: bool,
) -> None:
    root = tmp_path / f"inflight-terminal-swap-{injection_call}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = f"rollback-inflight-terminal-swap-{injection_call}"
    prepared, intent_name, _, successor_raw = _install_active_recovery_intent(
        store, rollback_id
    )
    capsule = store._preflight_transaction_rollback_intent(intent_name)
    real_revalidate = store._revalidate_recovery_capsule
    calls = 0

    def swap_after_revalidation(
        recovery_capsule: object,
        **kwargs: object,
    ) -> None:
        nonlocal calls
        real_revalidate(recovery_capsule, **kwargs)
        calls += 1
        if calls != injection_call:
            return
        target = (
            _terminal_path(root, capsule.quarantine_name)
            if target_kind == "successor-quarantine"
            else root / capsule.intent.name
        )
        replacement = target.with_suffix(target.suffix + ".replacement")
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, target)

    store._revalidate_recovery_capsule = swap_after_revalidation
    try:
        with pytest.raises(
            RollbackCorruptionError,
            match="identity changed",
        ):
            store._recover_transaction_rollback(capsule)
    finally:
        store._revalidate_recovery_capsule = real_revalidate
        capsule.close()
        store.close()
    quarantine_path = _terminal_path(root, capsule.quarantine_name)
    assert quarantine_path.read_bytes() == successor_raw
    before_restart = _file_inventory(root)

    if cold_valid:
        restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
        _assert_semantic_restoration(restarted.get(rollback_id), prepared)
        assert quarantine_path.read_bytes() == successor_raw
    else:
        with pytest.raises(RollbackCorruptionError):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
        assert _file_inventory(root) == before_restart


def test_active_intent_with_prior_head_and_missing_displaced_fails_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "active-prior-without-displaced"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-active-prior-without-displaced"
    _, intent_name, prior_raw, _ = _install_active_recovery_intent(
        store,
        rollback_id,
    )
    head = root / f"journal.{rollback_id}.head"
    head.write_bytes(prior_raw)
    head.chmod(0o600)
    store.close()
    before = _file_inventory(root)

    with pytest.raises(
        RollbackCorruptionError,
        match="intent is invalid",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before
    assert (root / intent_name).is_file()


@pytest.mark.parametrize("missing", ("successor", "tombstone"))
def test_terminal_quarantine_missing_pair_fails_closed_without_pruning(
    tmp_path: Path,
    missing: str,
) -> None:
    root = tmp_path / f"terminal-missing-{missing}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _, _, _, successor_path, tombstone_path = _complete_terminal_quarantine(
        store,
        f"rollback-terminal-missing-{missing}",
    )
    store.close()
    missing_path = successor_path if missing == "successor" else tombstone_path
    missing_path.unlink()
    retained_path = tombstone_path if missing == "successor" else successor_path
    retained_raw = retained_path.read_bytes()

    with pytest.raises(
        RollbackCorruptionError,
        match="pair is incomplete",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert retained_path.read_bytes() == retained_raw


def test_terminal_whole_pair_deletion_cannot_truncate_forward_anchor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "terminal-whole-pair-deletion"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-terminal-whole-pair-deletion"
    prepared, _, _, successor_path, tombstone_path = _complete_terminal_quarantine(
        store, rollback_id
    )
    restored = store.get(rollback_id)
    assert restored is not None
    assert restored.generation == 3
    assert restored.revision == prepared.revision
    assert restored.phase is prepared.phase
    assert restored.phase_receipts == prepared.phase_receipts
    assert len(restored.terminal_quarantine_refs) == 1
    canonical_anchor = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.startswith(f"journal.{rollback_id}.")
    }
    store.close()
    successor_path.unlink()
    tombstone_path.unlink()
    attacked = _file_inventory(root)

    with pytest.raises(
        RollbackCorruptionError,
        match="anchor and pair inventory diverged",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == attacked
    assert {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.name.startswith(f"journal.{rollback_id}.")
    } == canonical_anchor


def test_terminal_scan_stops_before_unbounded_stat_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "terminal-bounded-scan"
    root.mkdir(mode=0o700)
    terminal = root / ".terminal-rollback"
    terminal.mkdir(mode=0o700)
    for index in range(513):
        name = f"rollback-quarantine.{index:064x}.{index:032x}.{index:064x}.successor"
        artifact = terminal / name
        artifact.write_bytes(b"x")
        artifact.chmod(0o600)
    real_scandir = rollback_store_module.os.scandir
    stat_calls = 0

    class CountingEntry:
        def __init__(self, entry: object) -> None:
            self._entry = entry
            self.name = entry.name  # type: ignore[attr-defined]

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            nonlocal stat_calls
            stat_calls += 1
            return self._entry.stat(  # type: ignore[no-any-return,attr-defined]
                follow_symlinks=follow_symlinks
            )

    class CountingScandir:
        def __init__(self, path: object) -> None:
            self._scandir = real_scandir(path)

        def __enter__(self) -> object:
            self._scandir.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._scandir.__exit__(*args)

        def __iter__(self) -> object:
            return (CountingEntry(entry) for entry in self._scandir)

    monkeypatch.setattr(
        rollback_store_module.os,
        "scandir",
        CountingScandir,
    )
    with pytest.raises(
        RollbackCorruptionError,
        match="pair bound|artifact bound",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert stat_calls <= 512


@pytest.mark.parametrize(
    ("target_name", "attack"),
    (
        ("successor", "tamper"),
        ("tombstone", "tamper"),
        ("successor", "same-byte-inode"),
    ),
)
def test_terminal_quarantine_tamper_and_inode_swap_fail_closed(
    tmp_path: Path,
    target_name: str,
    attack: str,
) -> None:
    root = tmp_path / f"terminal-{target_name}-{attack}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _, _, _, successor_path, tombstone_path = _complete_terminal_quarantine(
        store,
        f"rollback-terminal-{target_name}-{attack}",
    )
    store.close()
    target = successor_path if target_name == "successor" else tombstone_path
    if attack == "tamper":
        raw = bytearray(target.read_bytes())
        raw[len(raw) // 2] ^= 1
        target.write_bytes(raw)
        target.chmod(0o600)
    else:
        replacement = target.with_suffix(target.suffix + ".replacement")
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, target)
    attacked = _file_inventory(root)

    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == attacked
    assert successor_path.is_file()
    assert tombstone_path.is_file()


def test_byte_identical_tombstone_replacement_is_value_equivalent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "terminal-tombstone-value-equivalent"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-terminal-tombstone-value-equivalent"
    prepared, _, _, successor_path, tombstone_path = _complete_terminal_quarantine(
        store, rollback_id
    )
    store.close()
    before = _file_inventory(root)
    replacement = tombstone_path.with_suffix(".replacement")
    replacement.write_bytes(tombstone_path.read_bytes())
    replacement.chmod(0o600)
    os.replace(replacement, tombstone_path)

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _assert_semantic_restoration(restarted.get(rollback_id), prepared)
    assert _file_inventory(root) == before
    assert successor_path.is_file()
    assert tombstone_path.is_file()


@pytest.mark.parametrize("attack", ("swap", "replay"))
def test_terminal_quarantine_swap_and_replay_fail_closed(
    tmp_path: Path,
    attack: str,
) -> None:
    root = tmp_path / f"terminal-{attack}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _, _, _, first_successor, first_tombstone = _complete_terminal_quarantine(
        store,
        f"rollback-terminal-{attack}-first",
    )
    if attack == "swap":
        _, _, _, second_successor, _ = _complete_terminal_quarantine(
            store,
            f"rollback-terminal-{attack}-second",
        )
        first_raw = first_successor.read_bytes()
        second_raw = second_successor.read_bytes()
        first_successor.write_bytes(second_raw)
        second_successor.write_bytes(first_raw)
        first_successor.chmod(0o600)
        second_successor.chmod(0o600)
    else:
        first_payload = json.loads(first_tombstone.read_bytes())["payload"]
        digest_hex = first_payload["successor_record_digest"][7:]
        replay_base = (
            "rollback-quarantine."
            f"{canonical_digest(first_payload['rollback_id'].encode())[7:]}."
            f"{'b' * 32}.{digest_hex}"
        )
        replay_successor = _terminal_path(
            root,
            f"{replay_base}.successor",
        )
        replay_tombstone = _terminal_path(
            root,
            f"{replay_base}.tombstone",
        )
        replay_successor.write_bytes(first_successor.read_bytes())
        replay_successor.chmod(0o600)
        replay_tombstone.write_bytes(first_tombstone.read_bytes())
        replay_tombstone.chmod(0o600)
    store.close()
    attacked = _file_inventory(root)

    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == attacked


@pytest.mark.parametrize("bound", ("pairs", "bytes"))
def test_terminal_quarantine_bound_exhaustion_retains_resumable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bound: str,
) -> None:
    root = tmp_path / f"terminal-bound-{bound}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = f"rollback-terminal-bound-{bound}"
    prepared, intent_name, _, successor_raw = _install_active_recovery_intent(
        store, rollback_id
    )
    capsule = store._preflight_transaction_rollback_intent(intent_name)
    if bound == "pairs":
        monkeypatch.setattr(
            rollback_store_module,
            "_MAX_ROLLBACK_QUARANTINE_PAIRS",
            0,
        )
    else:
        monkeypatch.setattr(
            rollback_store_module,
            "_MAX_ROLLBACK_QUARANTINE_BYTES",
            len(successor_raw),
        )
    try:
        with pytest.raises(
            RollbackCorruptionError,
            match="retention bound is exhausted",
        ):
            store._recover_transaction_rollback(capsule)
        intent_path = root / intent_name
        assert json.loads(intent_path.read_bytes())["payload"]["state"] == (
            "cleanup_pending"
        )
        assert (root / capsule.displaced_name).read_bytes() == successor_raw
        assert not _terminal_path(root, capsule.quarantine_name).exists()
    finally:
        capsule.close()
    monkeypatch.undo()

    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _assert_semantic_restoration(restarted.get(rollback_id), prepared)
    assert any(
        path.name.endswith(".successor")
        for path in (root / ".terminal-rollback").iterdir()
        if path.name.startswith("rollback-quarantine.")
    )


@pytest.mark.parametrize("late_authority", ("history", "commit"))
def test_terminal_quarantine_requires_successor_history_and_commit_anchor(
    tmp_path: Path,
    late_authority: str,
) -> None:
    root = tmp_path / f"terminal-late-{late_authority}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = f"rollback-terminal-late-{late_authority}"
    _, _, successor_raw, successor_path, tombstone_path = _complete_terminal_quarantine(
        store, rollback_id
    )
    payload = json.loads(tombstone_path.read_bytes())["payload"]
    generation = payload["successor_generation"]
    record_digest = payload["successor_record_digest"]
    late_name = store._journal_version_name(
        rollback_id,
        generation,
        record_digest,
        late_authority,
    )
    late_path = root / late_name
    late_raw = (
        successor_raw
        if late_authority == "history"
        else store._commit_bytes(rollback_id, generation, record_digest)
    )
    terminal_before = {
        successor_path.name: successor_path.read_bytes(),
        tombstone_path.name: tombstone_path.read_bytes(),
    }
    store.close()
    assert late_path.read_bytes() == late_raw
    late_path.unlink()
    attacked = _file_inventory(root)
    removed_name = (
        store._journal_version_name(
            rollback_id,
            generation,
            record_digest,
            "commit",
        )
        if late_authority == "history"
        else f"journal.{rollback_id}.head"
    )

    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert successor_path.read_bytes() == terminal_before[successor_path.name]
    assert tombstone_path.read_bytes() == terminal_before[tombstone_path.name]
    assert not late_path.exists()
    blocked_path = root / f"journal.{rollback_id}.blocked"
    blocked_raw = blocked_path.read_bytes()
    assert stat.S_IMODE(blocked_path.stat().st_mode) == 0o600
    blocked = store._verify_signed(blocked_raw, "corruption-marker")
    assert blocked == {
        "domain": "rollback-journal",
        "identity": rollback_id,
    }
    expected = dict(attacked)
    del expected[removed_name]
    expected[blocked_path.name] = blocked_raw
    assert _file_inventory(root) == expected
    restarted = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        restarted.get(rollback_id)
    unrelated_id = f"rollback-unrelated-{late_authority}"
    unrelated = _prepare(restarted, unrelated_id)
    assert restarted.get(unrelated_id) == unrelated
    assert successor_path.read_bytes() == terminal_before[successor_path.name]
    assert tombstone_path.read_bytes() == terminal_before[tombstone_path.name]
    restarted.close()
    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        reopened.get(rollback_id)
    assert reopened.get(unrelated_id) == unrelated


def test_exact_quarantined_generation_is_rejected_before_publication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "terminal-exact-replay"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-terminal-exact-replay"
    _complete_terminal_quarantine(store, rollback_id)
    before = _file_inventory(root)

    with pytest.raises(
        RollbackConflictError,
        match="compare-and-swap failed",
    ):
        _advance(
            store,
            rollback_id,
            expected_generation=1,
            expected_revision=0,
            phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        )
    assert _file_inventory(root) == before

    alternative = _advance(
        store,
        rollback_id,
        expected_generation=3,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
        variant="alternate",
    )
    assert alternative.generation == 4


@pytest.mark.parametrize("attack", ("malformed", "missing-head"))
def test_invalid_transaction_intent_set_fails_without_mutation(
    tmp_path: Path,
    attack: str,
) -> None:
    root = tmp_path / attack
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-transaction-temp-crash"
    _prepare(store, rollback_id)
    store.close()
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_process_crash_during_transaction_rollback,
        args=(str(root),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 79
    intent_path = next(
        root / name
        for name in _owned_temps(root, "rollback-journal")
        if name.endswith(".transaction-rollback")
    )
    if attack == "malformed":
        intent_path.write_bytes(b"malformed-intent")
        intent_path.chmod(0o600)
    else:
        (root / f"journal.{rollback_id}.head").unlink()
    before = _file_inventory(root)
    with pytest.raises(
        RollbackCorruptionError,
        match="rollback intent is invalid",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_cross_run_transaction_intent_fails_without_mutation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "cross-source"
    source = FilesystemRollbackJournalStore(
        source_root,
        authority_key=KEY,
    )
    _prepare(source, "rollback-transaction-temp-crash")
    source.close()
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_process_crash_during_transaction_rollback,
        args=(str(source_root),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 79
    source_intent = next(
        source_root / name
        for name in _owned_temps(source_root, "rollback-journal")
        if name.endswith(".transaction-rollback")
    )

    target_root = tmp_path / "cross-target"
    target = FilesystemRollbackJournalStore(
        target_root,
        authority_key=KEY,
    )
    _prepare(target, "rollback-cross-target")
    target.close()
    copied = target_root / source_intent.name
    copied.write_bytes(source_intent.read_bytes())
    copied.chmod(0o600)
    before = _file_inventory(target_root)
    with pytest.raises(
        RollbackCorruptionError,
        match="rollback intent is invalid",
    ):
        FilesystemRollbackJournalStore(target_root, authority_key=KEY)
    assert _file_inventory(target_root) == before


def test_raw_predecessor_history_is_not_a_transaction_intent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "raw-history-intent"
    rollback_id = "rollback-raw-history-intent"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, rollback_id)
    _advance(
        store,
        rollback_id,
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    prior = store.history(rollback_id)[0]
    raw_history = (root / store._history_name(prior)).read_bytes()
    suspect = (
        root / ".rollback-journal.44444444444444444444444444444444.transaction-rollback"
    )
    suspect.write_bytes(raw_history)
    suspect.chmod(0o600)
    store.close()
    before = _file_inventory(root)
    with pytest.raises(
        RollbackCorruptionError,
        match="rollback intent is invalid",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_ordered_prior_intents_cannot_walk_back_committed_generation_three(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ordered-intents"
    rollback_id = "rollback-ordered-intents"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _prepare(store, rollback_id)
    _advance(
        store,
        rollback_id,
        expected_generation=1,
        expected_revision=0,
        phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
    )
    _advance(
        store,
        rollback_id,
        expected_generation=2,
        expected_revision=1,
        phase=RollbackPhase.REVOCATION_PUBLISHED,
    )
    records = store.history(rollback_id)
    raws = tuple(
        (root / store._history_name(record)).read_bytes() for record in records
    )
    for transaction_id, previous, successor in (
        ("5" * 32, raws[1], raws[2]),
        ("6" * 32, raws[0], raws[1]),
    ):
        intent = store._rollback_intent_bytes(
            transaction_id,
            previous,
            successor,
        )
        path = root / f".rollback-journal.{transaction_id}.transaction-rollback"
        path.write_bytes(intent)
        path.chmod(0o600)
    store.close()
    before = _file_inventory(root)
    with pytest.raises(
        RollbackCorruptionError,
        match="multiple transaction rollback intents",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_restart_rejects_owned_temp_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "temp-symlink"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    store.close()
    outside = tmp_path / "outside-temp"
    outside.write_bytes(b"outside")
    suspect = root / ".rollback-journal.0123456789abcdef0123456789abcdef.tmp"
    suspect.symlink_to(outside)
    with pytest.raises(RollbackCorruptionError, match="abandoned rollback temp"):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert outside.read_bytes() == b"outside"
    assert suspect.is_symlink()


_WRITE_SURFACES = tuple(
    (surface, nth_write)
    for surface, write_count in (
        ("journal", 5),
        ("active", 4),
        ("registration", 5),
        ("quarantine", 6),
        ("corruption-marker", 1),
    )
    for nth_write in range(1, write_count + 1)
)


@pytest.mark.parametrize(("surface", "nth_write"), _WRITE_SURFACES)
@pytest.mark.parametrize("mode", ("failure", "short"))
def test_nth_write_failure_matrix_has_no_temp_leak_and_exact_retry_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    nth_write: int,
    mode: str,
) -> None:
    root = tmp_path / f"{surface}-{mode}-{nth_write}"
    if surface == "journal":
        store: object = FilesystemRollbackJournalStore(root, authority_key=KEY)

        def operation() -> object:
            return _prepare(store, "rollback-write-matrix")

        domain = "rollback-journal"
    elif surface == "active":
        store = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
        approved = _tuple(f"write-matrix-{mode}-{nth_write}", "a")

        def operation() -> object:
            return store.compare_and_swap(None, approved, "active-write-matrix")

        domain = "active-approved-tuple"
    elif surface == "registration":
        store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
        ownership = _ownership(
            f"registration-write-{mode}-{nth_write}",
            "8",
            kind=DependentObjectKind.EVIDENCE,
        )

        def operation() -> object:
            return store.register(ownership)

        domain = "dependent-quarantine"
    elif surface == "quarantine":
        store = FilesystemDependentQuarantineStore(root, authority_key=KEY)
        ownership = _ownership(
            f"quarantine-write-{mode}-{nth_write}",
            "9",
            kind=DependentObjectKind.REWARD,
        )
        store.register(ownership)

        def operation() -> object:
            return store.quarantine_causal(
                "rollback-write-matrix", CAUSE, (ownership.object_ref,)
            )

        domain = "dependent-quarantine"
    else:
        store = FilesystemRollbackJournalStore(root, authority_key=KEY)
        _prepare(store, "rollback-marker-write")
        head = root / "journal.rollback-marker-write.head"
        head.write_bytes(head.read_bytes() + b"x")

        def operation() -> object:
            return store.get("rollback-marker-write")

        domain = "rollback-journal"

    real_write = _install_nth_write_failure(monkeypatch, nth_write, mode)
    expected = (
        f"write failure {nth_write}"
        if mode == "failure"
        else f"short write failure {nth_write}"
    )
    with pytest.raises(OSError, match=expected):
        operation()
    monkeypatch.setattr(os, "write", real_write)
    assert _owned_temps(root, domain) == ()

    if surface == "journal":
        store.close()
        recovered = FilesystemRollbackJournalStore(root, authority_key=KEY)
        assert recovered.get("rollback-write-matrix") is None
        assert _prepare(recovered, "rollback-write-matrix").generation == 1
    elif surface == "active":
        store.close()
        recovered = FilesystemActiveApprovedTupleStore(root, authority_key=KEY)
        assert recovered.get() is None
        assert (
            recovered.compare_and_swap(None, approved, "active-write-matrix").generation
            == 1
        )
    elif surface == "registration":
        store.close()
        recovered = FilesystemDependentQuarantineStore(root, authority_key=KEY)
        assert recovered.get(ownership.object_ref) is None
        assert recovered.register(ownership).promotion_eligible
    elif surface == "quarantine":
        request = root / "quarantine.rollback-write-matrix.request"
        if request.exists():
            with pytest.raises(DependentIneligibleError):
                store.assert_export_eligible(ownership.object_ref)
        else:
            store.assert_export_eligible(ownership.object_ref)
        store.close()
        recovered = FilesystemDependentQuarantineStore(root, authority_key=KEY)
        receipts = recovered.quarantine_causal(
            "rollback-write-matrix", CAUSE, (ownership.object_ref,)
        )
        assert len(receipts) == 1
        with pytest.raises(DependentIneligibleError):
            recovered.assert_export_eligible(ownership.object_ref)
    else:
        assert (root / "journal.rollback-marker-write.blocked").exists() is False
        with pytest.raises(RollbackCorruptionError, match="quarantined"):
            store.get("rollback-marker-write")
        assert (root / "journal.rollback-marker-write.blocked").is_file()


def test_restoration_head_before_commit_crash_reconstructs_exact_commit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "restoration-head-before-commit"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-restoration-head-before-commit"
    prepared, _, _, _ = _install_active_recovery_intent(
        store,
        rollback_id,
    )
    store.close()

    process = multiprocessing.get_context("spawn").Process(
        target=_process_crash_after_restoration_head,
        args=(str(root),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 92

    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    restored = reopened.get(rollback_id)
    _assert_semantic_restoration(restored, prepared)
    assert restored is not None
    commit = root / reopened._commit_name(restored)
    assert commit.read_bytes() == reopened._commit_bytes(
        rollback_id,
        restored.generation,
        restored.digest,
    )
    anchors = reopened._terminal_quarantine_anchors()
    assert tuple(anchors.values()) == restored.terminal_quarantine_refs
    assert not list(root.glob(".terminal-anchor-pending.*"))
    assert not (root / f"journal.{rollback_id}.blocked").exists()


def test_commit_durable_anchor_replace_crash_rejects_torn_temp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "anchor-index-replace-crash"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-anchor-index-replace-crash"
    prepared, _, _, _ = _install_active_recovery_intent(
        store,
        rollback_id,
    )
    store.close()

    process = multiprocessing.get_context("spawn").Process(
        target=_process_crash_during_anchor_index_replace,
        args=(str(root),),
    )
    process.start()
    process.join(timeout=10)
    assert not process.is_alive() and process.exitcode == 93
    torn_temps = tuple(
        path
        for path in root.iterdir()
        if path.name.startswith(".rollback-journal.") and path.name.endswith(".tmp")
    )
    assert len(torn_temps) == 1
    assert torn_temps[0].stat().st_size == 17
    attacked = _v15_tree_snapshot(root)

    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v15_tree_snapshot(root) == attacked


@pytest.mark.parametrize("target_name", ("successor", "tombstone"))
def test_tampered_blocked_pair_is_scoped_to_attacked_rollback(
    tmp_path: Path,
    target_name: str,
) -> None:
    root = tmp_path / f"blocked-pair-tamper-{target_name}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    attacked_id = f"rollback-blocked-pair-tamper-{target_name}"
    _, _, _, successor_path, tombstone_path = _complete_terminal_quarantine(
        store, attacked_id
    )
    tombstone_payload = json.loads(tombstone_path.read_bytes())["payload"]
    successor_commit = root / store._journal_version_name(
        attacked_id,
        tombstone_payload["successor_generation"],
        tombstone_payload["successor_record_digest"],
        "commit",
    )
    store.close()
    successor_commit.unlink()
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    target = successor_path if target_name == "successor" else tombstone_path
    attacked_raw = target.read_bytes() + b"x"
    target.write_bytes(attacked_raw)
    target.chmod(0o600)

    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        reopened.get(attacked_id)
    unrelated_id = f"rollback-blocked-pair-unrelated-{target_name}"
    unrelated = _prepare(reopened, unrelated_id)
    assert reopened.get(unrelated_id) == unrelated
    reopened.close()
    again = FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert again.get(unrelated_id) == unrelated
    assert target.read_bytes() == attacked_raw


class _OwnerMismatchStat:
    def __init__(
        self,
        value: os.stat_result,
        field: str,
    ) -> None:
        self._value = value
        self._field = field

    def __getattr__(self, name: str) -> object:
        value = getattr(self._value, name)
        if name == self._field:
            return int(value) + 1
        return value


@pytest.mark.parametrize(
    ("surface", "owner_field"),
    (
        ("terminal-directory", "st_uid"),
        ("terminal-directory", "st_gid"),
        ("successor", "st_uid"),
        ("successor", "st_gid"),
        ("tombstone", "st_uid"),
        ("tombstone", "st_gid"),
    ),
)
def test_terminal_owner_mismatch_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    owner_field: str,
) -> None:
    root = tmp_path / f"owner-{surface}-{owner_field}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _, _, _, successor_path, tombstone_path = _complete_terminal_quarantine(
        store,
        f"rollback-owner-{surface}-{owner_field}",
    )
    store.close()
    before = _file_inventory(root)
    target_name = {
        "successor": successor_path.name,
        "tombstone": tombstone_path.name,
    }.get(surface)
    real_stat = rollback_store_module.os.stat

    def mismatched_stat(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        value = real_stat(path, *args, **kwargs)
        if (surface == "terminal-directory" and str(path) == ".terminal-rollback") or (
            target_name is not None and str(path) == target_name
        ):
            return _OwnerMismatchStat(value, owner_field)
        return value

    monkeypatch.setattr(rollback_store_module.os, "stat", mismatched_stat)
    with pytest.raises(RollbackCorruptionError, match="owner|identity"):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    monkeypatch.undo()
    assert _file_inventory(root) == before


def test_root_history_flood_is_never_enumerated_or_touched_by_id_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root-history-flood"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-root-history-flood"
    expected = _complete_terminal_quarantine(store, rollback_id)[0]
    store.close()
    flood_names: set[str] = set()
    for index in range(1025):
        name = f"journal.flood-{index}.g00000000000000000001.{index:064x}.history"
        flood_names.add(name)
        path = root / name
        path.write_bytes(b"unrelated")
        path.chmod(0o600)
    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    real_read = FilesystemRollbackJournalStore._read
    real_stat = rollback_store_module.os.stat
    real_scandir = rollback_store_module.os.scandir
    touched_names: list[str] = []
    root_enumerations = 0

    def counted_read(
        self: FilesystemRollbackJournalStore,
        name: str,
    ) -> bytes | None:
        if name in flood_names:
            touched_names.append(name)
        return real_read(self, name)

    def counted_stat(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        if str(path) in flood_names:
            touched_names.append(str(path))
        return real_stat(path, *args, **kwargs)

    def counted_scandir(path: object) -> object:
        nonlocal root_enumerations
        if path == reopened._root_fd:
            root_enumerations += 1
        return real_scandir(path)

    def forbidden_listdir(path: object) -> object:
        raise AssertionError(f"unexpected whole-root listdir: {path}")

    monkeypatch.setattr(
        FilesystemRollbackJournalStore,
        "_read",
        counted_read,
    )
    monkeypatch.setattr(rollback_store_module.os, "stat", counted_stat)
    monkeypatch.setattr(rollback_store_module.os, "scandir", counted_scandir)
    monkeypatch.setattr(
        rollback_store_module.os,
        "listdir",
        forbidden_listdir,
    )
    _assert_semantic_restoration(reopened.get(rollback_id), expected)
    assert reopened.history(rollback_id)[-1] == reopened.get(rollback_id)
    assert root_enumerations == 0
    assert touched_names == []


@pytest.mark.parametrize("attack", ("duplicate", "out-of-order"))
def test_signed_terminal_anchor_index_rejects_noncanonical_entries(
    tmp_path: Path,
    attack: str,
) -> None:
    root = tmp_path / f"anchor-index-{attack}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _complete_terminal_quarantine(store, f"rollback-index-{attack}-a")
    _complete_terminal_quarantine(store, f"rollback-index-{attack}-b")
    index_name = rollback_store_module._ROLLBACK_TERMINAL_ANCHOR_INDEX
    index_raw = (root / index_name).read_bytes()
    entries = json.loads(index_raw)["payload"]["entries"]
    attacked_entries = (
        [entries[0], entries[0]] if attack == "duplicate" else list(reversed(entries))
    )
    attacked_raw = store._signed_bytes(
        "terminal-quarantine-anchor-index",
        {
            "entries": attacked_entries,
            "schema_version": ("bb.rl.phase5.rollback-terminal-anchor-index.v1"),
        },
    )
    store._replace(index_name, attacked_raw, index_raw)
    store.close()
    before = _file_inventory(root)

    with pytest.raises(RollbackCorruptionError, match="anchor index"):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_signed_terminal_anchor_index_replay_blocks_only_omitted_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "anchor-index-replay"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    first_id = "rollback-index-replay-first"
    second_id = "rollback-index-replay-second"
    first_prepared, _, _, _, _ = _complete_terminal_quarantine(
        store,
        first_id,
    )
    index_name = rollback_store_module._ROLLBACK_TERMINAL_ANCHOR_INDEX
    first_index = (root / index_name).read_bytes()
    _complete_terminal_quarantine(store, second_id)
    current_index = (root / index_name).read_bytes()
    store._replace(index_name, first_index, current_index)
    store.close()

    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    _assert_semantic_restoration(reopened.get(first_id), first_prepared)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        reopened.get(second_id)
    unrelated = _prepare(reopened, "rollback-index-replay-unrelated")
    assert reopened.get(unrelated.rollback_id) == unrelated


def test_terminal_names_bind_same_transaction_and_successor_to_rollback_id(
    tmp_path: Path,
) -> None:
    store = FilesystemRollbackJournalStore(
        tmp_path / "terminal-cross-id",
        authority_key=KEY,
    )
    transaction_id = "a" * 32
    successor_digest = "sha256:" + "ab" * 32
    first_names = store._rollback_quarantine_names(
        transaction_id,
        "rollback-cross-id-first",
        successor_digest,
    )
    second_names = store._rollback_quarantine_names(
        transaction_id,
        "rollback-cross-id-second",
        successor_digest,
    )
    assert set(first_names).isdisjoint(second_names)
    prepared, _, _, _, _ = _complete_terminal_quarantine(
        store,
        "rollback-cross-id-first",
    )
    restored = store.get("rollback-cross-id-first")
    _assert_semantic_restoration(restored, prepared)
    assert restored is not None
    first_ref = restored.terminal_quarantine_refs[0]
    with pytest.raises(RollbackValidationError, match="artifact names"):
        replace(first_ref, rollback_id="rollback-cross-id-second")


def test_lazy_root_name_flood_stops_at_byte_cap_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "lazy-root-name-flood"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    real_scandir = rollback_store_module.os.scandir
    yielded = 0
    scans = 0
    stat_calls = 0
    unlink_calls = 0
    fsync_calls = 0
    chunk_bytes = 1024 * 1024

    class LazyEntry:
        def __init__(self, index: int) -> None:
            self._index = index

        @property
        def name(self) -> str:
            return f"foreign-{self._index}-" + "x" * chunk_bytes

    class LazyScandir:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> object:
            nonlocal yielded
            for index in range(10_000):
                yielded += 1
                yield LazyEntry(index)

    def synthetic_scandir(path: object) -> object:
        nonlocal scans
        if path == store._root_fd:
            scans += 1
            return LazyScandir()
        return real_scandir(path)

    real_stat = rollback_store_module.os.stat
    real_unlink = rollback_store_module.os.unlink
    real_fsync = rollback_store_module.os.fsync

    def counted_stat(*args: object, **kwargs: object) -> object:
        nonlocal stat_calls
        stat_calls += 1
        return real_stat(*args, **kwargs)

    def counted_unlink(*args: object, **kwargs: object) -> object:
        nonlocal unlink_calls
        unlink_calls += 1
        return real_unlink(*args, **kwargs)

    def counted_fsync(*args: object, **kwargs: object) -> object:
        nonlocal fsync_calls
        fsync_calls += 1
        return real_fsync(*args, **kwargs)

    monkeypatch.setattr(
        rollback_store_module.os,
        "scandir",
        synthetic_scandir,
    )
    monkeypatch.setattr(rollback_store_module.os, "stat", counted_stat)
    monkeypatch.setattr(rollback_store_module.os, "unlink", counted_unlink)
    monkeypatch.setattr(rollback_store_module.os, "fsync", counted_fsync)
    with pytest.raises(
        RollbackCorruptionError,
        match="enumeration bound",
    ):
        store._cleanup_abandoned_temps()
    assert scans == 1
    assert yielded <= (rollback_store_module._MAX_ROOT_NAME_BYTES // chunk_bytes + 1)
    assert yielded * chunk_bytes >= (rollback_store_module._MAX_ROOT_NAME_BYTES)
    assert stat_calls == 0
    assert unlink_calls == 0
    assert fsync_calls == 0


@pytest.mark.parametrize(("offset", "succeeds"), ((-1, True), (0, True), (1, False)))
def test_actual_root_entry_count_boundary(
    tmp_path: Path,
    offset: int,
    succeeds: bool,
) -> None:
    root = tmp_path / f"root-entry-bound-{offset}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    store.close()
    target_count = rollback_store_module._MAX_ROOT_ENTRIES + offset
    existing_count = sum(1 for _ in root.iterdir())
    for index in range(target_count - existing_count):
        path = root / f"foreign-root-entry-{index:04d}"
        path.write_bytes(b"")
        path.chmod(0o600)
    before = _file_inventory(root)
    if succeeds:
        reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
        reopened.close()
    else:
        with pytest.raises(
            RollbackCorruptionError,
            match="enumeration bound",
        ):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_actual_root_aggregate_name_byte_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root-name-byte-bound"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    store.close()
    first = root / ("foreign-" + "a" * 200)
    first.write_bytes(b"")
    first.chmod(0o600)
    exact_bytes = sum(len(path.name.encode("utf-8")) for path in root.iterdir())
    monkeypatch.setattr(
        rollback_store_module,
        "_MAX_ROOT_NAME_BYTES",
        exact_bytes,
    )
    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    reopened.close()
    second = root / ("foreign-" + "b" * 200)
    second.write_bytes(b"")
    second.chmod(0o600)
    before = _file_inventory(root)
    with pytest.raises(
        RollbackCorruptionError,
        match="enumeration bound",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_malformed_owned_temp_name_fails_before_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "malformed-owned-temp"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    store.close()
    malformed = root / ".rollback-journal.not-a-transaction.tmp"
    malformed.write_bytes(b"retained")
    malformed.chmod(0o600)
    before = _file_inventory(root)
    with pytest.raises(
        RollbackCorruptionError,
        match="temp name is invalid",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _file_inventory(root) == before


def test_late_owned_temp_addition_fails_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "late-owned-temp-add"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    before = _file_inventory(root)
    late = root / (".rollback-journal.0123456789abcdef0123456789abcdef.tmp")
    real_scandir = rollback_store_module.os.scandir
    root_scans = 0

    def late_scandir(path: object) -> object:
        nonlocal root_scans
        if path == store._root_fd:
            root_scans += 1
            if root_scans == 2:
                late.write_bytes(b"late")
                late.chmod(0o600)
        return real_scandir(path)

    monkeypatch.setattr(
        rollback_store_module.os,
        "scandir",
        late_scandir,
    )
    with pytest.raises(
        RollbackCorruptionError,
        match="changed during abandoned temp scan",
    ):
        store._cleanup_abandoned_temps()
    assert _file_inventory(root) == {**before, late.name: b"late"}


def test_late_owned_temp_swap_fails_before_cleanup_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "late-owned-temp-swap"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    name = ".rollback-journal.0123456789abcdef0123456789abcdef.tmp"
    target = root / name
    target.write_bytes(b"original")
    target.chmod(0o600)
    replacement = tmp_path / "replacement-temp"
    replacement.write_bytes(b"replacement")
    replacement.chmod(0o600)
    real_stat = rollback_store_module.os.stat
    target_stats = 0

    def swapping_stat(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal target_stats
        if str(path) == name:
            target_stats += 1
            if target_stats == 2:
                os.replace(replacement, target)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(rollback_store_module.os, "stat", swapping_stat)
    with pytest.raises(
        RollbackCorruptionError,
        match="identity changed",
    ):
        store._cleanup_abandoned_temps()
    assert target.read_bytes() == b"replacement"


def test_multi_temp_late_admission_failure_restores_every_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "multi-temp-late-admission"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    names = (
        ".rollback-journal.11111111111111111111111111111111.tmp",
        ".rollback-journal.ffffffffffffffffffffffffffffffff.tmp",
    )
    paths = tuple(root / name for name in names)
    for path, raw in zip(paths, (b"ordinary-a", b"ordinary-b"), strict=True):
        path.write_bytes(raw)
        path.chmod(0o600)
    before = _file_inventory(root)
    identities = tuple(_exact_file_identity(path) for path in paths)
    real_unlink = rollback_store_module.os.unlink
    real_rename_between = rollback_store_module._rename_noreplace_between
    unlink_calls = 0
    move_calls = 0
    injected_failures = 0

    def fail_second_unlink(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal unlink_calls
        if str(path) in names and kwargs.get("dir_fd") == store._root_fd:
            unlink_calls += 1
            if unlink_calls == 2:
                raise FileNotFoundError(str(path))
        return real_unlink(path, *args, **kwargs)

    def fail_second_stage_move(
        source: str,
        destination: str,
        source_directory_fd: int,
        destination_directory_fd: int,
    ) -> None:
        nonlocal move_calls, injected_failures
        if source in names and destination == source:
            move_calls += 1
            if move_calls == 2:
                injected_failures += 1
                raise FileNotFoundError(source)
        real_rename_between(
            source,
            destination,
            source_directory_fd,
            destination_directory_fd,
        )

    monkeypatch.setattr(rollback_store_module.os, "unlink", fail_second_unlink)
    monkeypatch.setattr(
        rollback_store_module,
        "_rename_noreplace_between",
        fail_second_stage_move,
    )
    with pytest.raises((FileNotFoundError, RollbackCorruptionError)):
        store._cleanup_abandoned_temps()
    monkeypatch.undo()
    assert injected_failures == 1
    assert _file_inventory(root) == before
    assert tuple(_exact_file_identity(path) for path in paths) == identities
    assert not (root / ".rollback-journal.cleanup-staging").exists()


def test_recovery_and_ordinary_late_admission_failure_publish_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "recovery-ordinary-late-admission"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-recovery-ordinary-late-admission"
    _install_active_recovery_intent(store, rollback_id)
    ordinary_name = ".rollback-journal.ffffffffffffffffffffffffffffffff.tmp"
    ordinary = root / ordinary_name
    ordinary.write_bytes(b"ordinary-b")
    ordinary.chmod(0o600)
    before = _file_inventory(root)
    terminal_before = {
        path.name: path.read_bytes()
        for path in (root / ".terminal-rollback").iterdir()
        if path.is_file()
    }
    ordinary_identity = _exact_file_identity(ordinary)
    real_unlink = rollback_store_module.os.unlink
    real_rename_between = rollback_store_module._rename_noreplace_between
    failed_unlink = False
    failed_move = False
    injected_failures = 0

    def fail_ordinary_unlink(
        path: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal failed_unlink
        if str(path) == ordinary_name and kwargs.get("dir_fd") == store._root_fd:
            failed_unlink = True
            raise FileNotFoundError(ordinary_name)
        return real_unlink(path, *args, **kwargs)

    def fail_ordinary_stage_move(
        source: str,
        destination: str,
        source_directory_fd: int,
        destination_directory_fd: int,
    ) -> None:
        nonlocal failed_move, injected_failures
        if source == ordinary_name and destination == ordinary_name:
            failed_move = True
            injected_failures += 1
            raise FileNotFoundError(ordinary_name)
        real_rename_between(
            source,
            destination,
            source_directory_fd,
            destination_directory_fd,
        )

    monkeypatch.setattr(rollback_store_module.os, "unlink", fail_ordinary_unlink)
    monkeypatch.setattr(
        rollback_store_module,
        "_rename_noreplace_between",
        fail_ordinary_stage_move,
    )
    with pytest.raises((FileNotFoundError, RollbackCorruptionError)):
        store._cleanup_abandoned_temps()
    monkeypatch.undo()
    assert failed_move and injected_failures == 1
    assert _file_inventory(root) == before
    assert {
        path.name: path.read_bytes()
        for path in (root / ".terminal-rollback").iterdir()
        if path.is_file()
    } == terminal_before
    assert _exact_file_identity(ordinary) == ordinary_identity
    assert not (root / ".rollback-journal.cleanup-staging").exists()


@pytest.mark.parametrize(
    "attack",
    ("mode", "link", "type", "owner-uid", "owner-gid"),
)
def test_abandoned_temp_identity_negatives_fail_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    root = tmp_path / f"temp-identity-{attack}"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    store.close()
    name = ".rollback-journal.0123456789abcdef0123456789abcdef.tmp"
    suspect = root / name
    if attack == "type":
        suspect.mkdir(mode=0o700)
    else:
        suspect.write_bytes(b"retained")
        suspect.chmod(0o600)
    if attack == "mode":
        suspect.chmod(0o640)
    elif attack == "link":
        os.link(suspect, root / "foreign-hardlink")
    elif attack.startswith("owner-"):
        real_stat = rollback_store_module.os.stat
        owner_field = "st_uid" if attack == "owner-uid" else "st_gid"

        def mismatched_stat(
            path: object,
            *args: object,
            **kwargs: object,
        ) -> object:
            value = real_stat(path, *args, **kwargs)
            if str(path) == name:
                return _OwnerMismatchStat(value, owner_field)
            return value

        monkeypatch.setattr(
            rollback_store_module.os,
            "stat",
            mismatched_stat,
        )
    with pytest.raises(
        RollbackCorruptionError,
        match="abandoned rollback temp",
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert suspect.exists()
    if suspect.is_file():
        assert suspect.read_bytes() == b"retained"


def test_same_id_anchor_index_replay_plus_omitted_pair_deletion_is_absorbing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "same-id-anchor-replay-deletion"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-same-id-anchor-replay-deletion"
    first_predecessor, _, _, first_successor, first_tombstone = (
        _complete_terminal_quarantine(store, rollback_id)
    )
    index_name = rollback_store_module._ROLLBACK_TERMINAL_ANCHOR_INDEX
    first_index = (root / index_name).read_bytes()
    first_pair = {
        first_successor.name: first_successor.read_bytes(),
        first_tombstone.name: first_tombstone.read_bytes(),
    }
    restored = store.get(rollback_id)
    assert restored is not None
    prior_raw = (root / f"journal.{rollback_id}.head").read_bytes()
    captured: dict[str, object] = {}
    real_publish = store._publish_versioned

    def capture_publication(**kwargs: object) -> None:
        captured.update(kwargs)
        raise _CapturedPublication

    store._publish_versioned = capture_publication
    try:
        with pytest.raises(_CapturedPublication):
            _advance(
                store,
                rollback_id,
                expected_generation=restored.generation,
                expected_revision=restored.revision,
                phase=RollbackPhase.EPISODES_CLOSED_OR_QUARANTINED,
            )
    finally:
        store._publish_versioned = real_publish
    successor_raw = captured["signed_record"]
    assert type(successor_raw) is bytes
    head_name = f"journal.{rollback_id}.head"
    store._replace(head_name, successor_raw, prior_raw)
    transaction_id = "b" * 32
    intent_name = f".rollback-journal.{transaction_id}.transaction-rollback"
    store._create_immutable(
        intent_name,
        store._rollback_intent_bytes(
            transaction_id,
            prior_raw,
            successor_raw,
        ),
    )
    capsule = store._preflight_transaction_rollback_intent(intent_name)
    second_successor = _terminal_path(root, capsule.quarantine_name)
    second_tombstone = _terminal_path(root, capsule.tombstone_name)
    try:
        store._recover_transaction_rollback(capsule)
    finally:
        capsule.close()
    twice_restored = store.get(rollback_id)
    assert twice_restored is not None
    assert len(twice_restored.terminal_quarantine_refs) == 2
    unrelated = _prepare(store, "rollback-same-id-replay-unrelated")
    current_index = (root / index_name).read_bytes()
    store._replace(index_name, first_index, current_index)
    store.close()
    second_successor.unlink()
    second_tombstone.unlink()
    attacked = _file_inventory(root)

    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        reopened.get(rollback_id)
    assert (root / f"journal.{rollback_id}.blocked").is_file()
    assert reopened.get(unrelated.rollback_id) == unrelated
    for name, raw in first_pair.items():
        assert _terminal_path(root, name).read_bytes() == raw
    for name, raw in attacked.items():
        if name == f"journal.{rollback_id}.blocked":
            continue
        assert (root / name).read_bytes() == raw
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        reopened.get(rollback_id)
    reopened.close()
    again = FilesystemRollbackJournalStore(root, authority_key=KEY)
    with pytest.raises(RollbackCorruptionError, match="quarantined"):
        again.get(rollback_id)
    assert again.get(unrelated.rollback_id) == unrelated
    _assert_semantic_restoration(twice_restored, restored)
    _assert_semantic_restoration(restored, first_predecessor)


class _V14ProcessCrash(BaseException):
    pass


def _v15_process_authority_crash(
    root: str,
    authority: str,
    phase: str,
) -> None:
    prefix = {
        "preparing": "authority.preparing.initial",
        "committed": "authority.committed.g0",
        "receipt": "authority.receipt",
    }[authority]
    targets = {
        "create": f"{prefix}.after_temp_create",
        "zero": f"{prefix}.before_write_chunk.0",
        "partial": f"{prefix}.after_short_write.0",
        "short-0": f"{prefix}.after_short_write.0",
        "short-1": f"{prefix}.after_short_write.1",
        "short-2": f"{prefix}.after_short_write.2",
        "full-before-fsync": f"{prefix}.after_temp_write",
        "full": f"{prefix}.before_rename",
        "after-fsync": f"{prefix}.after_temp_fsync",
        "post": f"{prefix}.after_rename",
        "replacement": f"{prefix}.before_rename",
    }
    target = targets[phase]
    short_next = [False]
    real_write = rollback_store_module.os.write

    def short_write(fd: int, payload: object) -> int:
        if not short_next[0]:
            return real_write(fd, payload)
        short_next[0] = False
        view = memoryview(payload)
        return real_write(fd, view[: max(1, len(view) // 2)])

    def crash(boundary: str) -> None:
        short_match = re.fullmatch(
            rf"{re.escape(prefix)}\.before_write_chunk\.(\d+)",
            boundary,
        )
        if phase == "partial" and boundary == f"{prefix}.before_write_chunk.0":
            short_next[0] = True
        elif phase.startswith("short-") and short_match is not None:
            short_index = int(phase.split("-", 1)[1])
            if int(short_match.group(1)) <= short_index:
                short_next[0] = True
        if phase == "replacement" and boundary == target:
            stage = Path(root) / ".rollback-journal.cleanup-staging"
            destination = stage / authority
            if destination.exists():
                _replace_with_same_bytes(destination)
            else:
                temporary = stage / f"{authority}.tmp"
                destination.write_bytes(temporary.read_bytes())
                destination.chmod(0o600)
            return
        if boundary == target:
            raise _V14ProcessCrash(boundary)

    rollback_store_module.os.write = short_write
    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = crash
    try:
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
        rollback_store_module.os.write = real_write


def _v14_ordinary_names(count: int) -> tuple[str, ...]:
    return tuple(f".rollback-journal.{index + 1:032x}.tmp" for index in range(count))


def _v14_seed_ordinary(
    root: Path,
    count: int,
) -> tuple[FilesystemRollbackJournalStore, tuple[str, ...], dict[str, bytes]]:
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    names = _v14_ordinary_names(count)
    payloads = {name: f"ordinary-{index}".encode() for index, name in enumerate(names)}
    for name, raw in payloads.items():
        path = root / name
        path.write_bytes(raw)
        path.chmod(0o600)
    return store, names, payloads


def _v14_run_cleanup_with_fault(
    store: FilesystemRollbackJournalStore,
    *,
    crash_at: int | str | None,
) -> tuple[str, ...]:
    events: list[str] = []

    def fault(boundary: str) -> None:
        events.append(boundary)
        if crash_at is not None and (
            (type(crash_at) is int and len(events) - 1 == crash_at)
            or boundary == crash_at
        ):
            raise _V14ProcessCrash(boundary)

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = fault
    try:
        if crash_at is None:
            store._cleanup_abandoned_temps()
        else:
            with pytest.raises(rollback_store_module._CleanupInjectedCrash):
                store._cleanup_abandoned_temps()
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    return tuple(events)


def _v14_open_with_fault(root: Path, crash_at: int) -> tuple[str, ...]:
    events: list[str] = []

    def fault(boundary: str) -> None:
        events.append(boundary)
        if len(events) - 1 == crash_at:
            raise _V14ProcessCrash(boundary)

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = fault
    try:
        with pytest.raises(rollback_store_module._CleanupInjectedCrash):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    return tuple(events)


def _v14_leave_preparing(
    root: Path,
    count: int,
) -> tuple[tuple[str, ...], dict[str, bytes]]:
    store, names, payloads = _v14_seed_ordinary(root, count)
    events: list[str] = []

    def fault(boundary: str) -> None:
        events.append(boundary)
        if boundary == "stage.all_moved":
            raise _V14ProcessCrash(boundary)

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = fault
    try:
        with pytest.raises(rollback_store_module._CleanupInjectedCrash):
            store._cleanup_abandoned_temps()
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
        store.close()
    assert "stage.all_moved" in events
    return names, payloads


def _v14_tree_inventory(root: Path) -> dict[str, tuple[str, bytes | None]]:
    inventory: dict[str, tuple[str, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            inventory[relative] = ("symlink", os.readlink(path).encode())
        elif path.is_dir():
            inventory[relative] = ("directory", None)
        else:
            inventory[relative] = ("file", path.read_bytes())
    return inventory


def _v15_tree_snapshot(
    root: Path,
) -> dict[str, tuple[str, bytes | None, tuple[int, ...], str | None]]:
    snapshot: dict[
        str,
        tuple[str, bytes | None, tuple[int, ...], str | None],
    ] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        value = path.lstat()
        identity = (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            stat.S_IFMT(value.st_mode),
            stat.S_IMODE(value.st_mode),
            value.st_nlink,
            value.st_size,
            value.st_ctime_ns,
        )
        if path.is_symlink():
            raw = os.readlink(path).encode()
            snapshot[relative] = (
                "symlink",
                raw,
                identity,
                canonical_digest(raw),
            )
        elif path.is_dir():
            snapshot[relative] = ("directory", None, identity, None)
        else:
            raw = path.read_bytes()
            snapshot[relative] = (
                "file",
                raw,
                identity,
                canonical_digest(raw),
            )
    return snapshot


def _v15_authority_boundaries(
    prefix: str,
    *,
    root_fsync: bool = False,
) -> tuple[str, ...]:
    boundaries = (
        f"{prefix}.before_temp_create",
        f"{prefix}.after_temp_create",
        f"{prefix}.before_temp_write",
        f"{prefix}.before_write_chunk.0",
        f"{prefix}.after_write_chunk.0",
        f"{prefix}.after_temp_write",
        f"{prefix}.before_temp_fsync",
        f"{prefix}.after_temp_fsync",
        f"{prefix}.before_rename",
        f"{prefix}.after_rename",
        f"{prefix}.before_stage_fsync",
        f"{prefix}.after_stage_fsync",
    )
    if root_fsync:
        boundaries += (
            f"{prefix}.before_root_fsync",
            f"{prefix}.after_root_fsync",
        )
    return boundaries


def _v15_expected_initial_boundaries(candidate_count: int) -> tuple[str, ...]:
    names = _v14_ordinary_names(candidate_count)
    boundaries = (
        "stage_dir.before_create",
        "stage_dir.after_create",
        "stage_dir.before_root_fsync",
        "stage_dir.after_root_fsync",
        *_v15_authority_boundaries("authority.preparing.initial"),
    )
    for index, name in enumerate(names):
        prefix = f"stage.move.{index}.{name}"
        boundaries += (
            f"{prefix}.before_move",
            f"{prefix}.after_move",
            f"{prefix}.before_stage_fsync",
            f"{prefix}.after_stage_fsync",
            f"{prefix}.before_root_fsync",
            f"{prefix}.after_root_fsync",
        )
    boundaries += (
        "stage.all_moved",
        *_v15_authority_boundaries("authority.preparing.staged"),
        *_v15_authority_boundaries(
            "authority.committed.g0",
            root_fsync=True,
        ),
    )
    for index, name in enumerate(names):
        processing_generation = 2 * index + 1
        processed_generation = processing_generation + 1
        prefix = f"forward.tombstone.{name}"
        boundaries += (
            *_v15_authority_boundaries(f"authority.committed.g{processing_generation}"),
            *_v15_authority_boundaries(
                f"authority.recovery_checkpoint.tombstone_plan.{index}"
            ),
            f"{prefix}.before_move",
            f"{prefix}.before_stage_fsync",
            *_v15_authority_boundaries(f"authority.committed.g{processed_generation}"),
            f"{prefix}.after_stage_fsync",
            f"{prefix}.after_move",
        )
    boundaries += (
        *_v15_authority_boundaries("authority.receipt"),
        *(
            boundary
            for index in range(candidate_count)
            for boundary in (
                f"receipt.remove.tombstone.{index}.before_unlink.{index}",
                f"receipt.remove.tombstone.{index}.after_unlink.{index}",
                f"receipt.remove.tombstone.{index}.before_stage_fsync",
                f"receipt.remove.tombstone.{index}.after_stage_fsync",
            )
        ),
        "receipt.remove.committed.before_unlink.committed",
        "receipt.remove.committed.after_unlink.committed",
        "receipt.remove.committed.before_stage_fsync",
        "receipt.remove.committed.after_stage_fsync",
        "receipt.remove.preparing.before_unlink.preparing",
        "receipt.remove.preparing.after_unlink.preparing",
        "receipt.remove.preparing.before_stage_fsync",
        "receipt.remove.preparing.after_stage_fsync",
        "receipt.remove.receipt.before_unlink.receipt",
        "receipt.remove.receipt.after_unlink.receipt",
        "receipt.remove.receipt.before_stage_fsync",
        "receipt.remove.receipt.after_stage_fsync",
        "receipt.terminal.before_stage_rmdir",
        "receipt.terminal.after_stage_rmdir",
        "receipt.terminal.before_parent_fsync",
        "receipt.terminal.after_parent_fsync",
    )
    return boundaries


def _v15_expected_rollback_boundaries(candidate_count: int) -> tuple[str, ...]:
    boundaries: tuple[str, ...] = ()
    for name in reversed(_v14_ordinary_names(candidate_count)):
        prefix = f"rollback.move.{name}"
        boundaries += (
            f"{prefix}.before_move",
            f"{prefix}.after_move",
            f"{prefix}.before_stage_fsync",
            f"{prefix}.after_stage_fsync",
            f"{prefix}.before_root_fsync",
            f"{prefix}.after_root_fsync",
        )
    return boundaries + (
        "rollback.remove.preparing.before_unlink.preparing",
        "rollback.remove.preparing.after_unlink.preparing",
        "rollback.remove.preparing.before_stage_fsync",
        "rollback.remove.preparing.after_stage_fsync",
        "rollback.terminal.before_stage_rmdir",
        "rollback.terminal.after_stage_rmdir",
        "rollback.terminal.before_parent_fsync",
        "rollback.terminal.after_parent_fsync",
    )


@pytest.mark.parametrize("candidate_count", (1, 2, 5))
def test_v14_every_initial_and_preparing_rollback_boundary_reopens_exactly(
    tmp_path: Path,
    candidate_count: int,
) -> None:
    inventory_root = tmp_path / f"inventory-{candidate_count}"
    inventory_store, _, _ = _v14_seed_ordinary(
        inventory_root,
        candidate_count,
    )
    initial_events = _v14_run_cleanup_with_fault(
        inventory_store,
        crash_at=None,
    )
    inventory_store.close()
    expected_initial_events = _v15_expected_initial_boundaries(candidate_count)
    assert initial_events == expected_initial_events
    assert len(initial_events) == len(set(initial_events))
    assert initial_events.count("stage.all_moved") == 1
    assert any(event.startswith("authority.preparing.") for event in initial_events)
    assert any(event.startswith("authority.committed.") for event in initial_events)
    assert any(event.startswith("forward.tombstone.") for event in initial_events)

    for crash_at, boundary in enumerate(initial_events):
        root = tmp_path / f"initial-{candidate_count}-{crash_at}"
        store, names, payloads = _v14_seed_ordinary(root, candidate_count)
        observed = _v14_run_cleanup_with_fault(store, crash_at=crash_at)
        store.close()
        assert observed[-1] == boundary
        stage = root / ".rollback-journal.cleanup-staging"
        stage_was_durable = stage.is_dir()
        committed_was_durable = (stage / "committed").is_file()
        candidate_was_present = any(
            (root / name).exists() or (stage / name).exists() for name in names
        )
        receipt_was_durable = (stage / "receipt").is_file()
        reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
        assert not stage.exists(), boundary
        if (
            stage_was_durable
            and candidate_was_present
            and not committed_was_durable
            and not receipt_was_durable
        ):
            assert {name: (root / name).read_bytes() for name in names} == payloads
        else:
            assert all(not (root / name).exists() for name in names)
        reopened.close()

    preparing_root = tmp_path / f"rollback-inventory-{candidate_count}"
    _v14_leave_preparing(preparing_root, candidate_count)
    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = lambda boundary: None
    try:
        restored = FilesystemRollbackJournalStore(
            preparing_root,
            authority_key=KEY,
        )
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    rollback_events: list[str] = []

    def collect_rollback(boundary: str) -> None:
        rollback_events.append(boundary)

    restored.close()
    collection_root = tmp_path / f"rollback-collection-{candidate_count}"
    _v14_leave_preparing(collection_root, candidate_count)
    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = collect_rollback
    try:
        collection_store = FilesystemRollbackJournalStore(
            collection_root,
            authority_key=KEY,
        )
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    collection_store.close()
    expected_rollback_events = _v15_expected_rollback_boundaries(candidate_count)
    assert tuple(rollback_events) == expected_rollback_events
    assert len(rollback_events) == len(set(rollback_events))

    for crash_at, boundary in enumerate(rollback_events):
        root = tmp_path / f"rollback-{candidate_count}-{crash_at}"
        names, payloads = _v14_leave_preparing(root, candidate_count)
        observed = _v14_open_with_fault(root, crash_at)
        assert observed[-1] == boundary
        reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
        rollback_stage_survived = boundary not in {
            "rollback.terminal.after_stage_rmdir",
            "rollback.terminal.before_parent_fsync",
            "rollback.terminal.after_parent_fsync",
        }
        if rollback_stage_survived:
            assert {name: (root / name).read_bytes() for name in names} == payloads
        else:
            assert all(not (root / name).exists() for name in names)
        assert not (root / ".rollback-journal.cleanup-staging").exists()
        before_noop = _v14_tree_inventory(root)
        assert not reopened._resume_cleanup_staging()
        assert _v14_tree_inventory(root) == before_noop
        reopened.close()


@pytest.mark.parametrize(
    "attack",
    (
        "symlink",
        "directory",
        "mode",
        "hardlink",
        "inode",
        "size",
        "digest",
        "manifest-dev",
        "manifest-uid",
        "manifest-gid",
        "manifest-type",
        "manifest-mode",
        "manifest-nlink",
        "manifest-size",
        "manifest-digest",
        "duplicate",
        "missing",
        "extra",
        "manifest-noncanonical",
        "manifest-oversized",
        "manifest-version",
        "manifest-state",
        "manifest-traversal",
        "manifest-absolute",
        "manifest-duplicate",
        "manifest-missing",
        "manifest-extra",
        "root-identity",
        "root-inventory",
        "unknown-temp",
        "manifest-hardlink",
    ),
)
def test_v14_forged_preparing_staging_fails_before_any_mutation(
    tmp_path: Path,
    attack: str,
) -> None:
    root = tmp_path / attack
    names, _ = _v14_leave_preparing(root, 2)
    stage = root / ".rollback-journal.cleanup-staging"
    preparing_path = stage / "preparing"
    candidate = stage / names[0]
    signer = object.__new__(FilesystemRollbackJournalStore)
    signer._authority_key = KEY
    signer._domain = "rollback-journal"
    signed_attack = attack.startswith(("manifest-", "root-"))
    if signed_attack and attack not in {
        "manifest-noncanonical",
        "manifest-oversized",
        "manifest-hardlink",
    }:
        verifier = object.__new__(FilesystemRollbackJournalStore)
        verifier._authority_key = KEY
        verifier._domain = "rollback-journal"
        payload = dict(
            verifier._verify_signed(
                preparing_path.read_bytes(),
                "abandoned-cleanup-preparing",
            )
        )
        payload["candidates"] = [dict(item) for item in payload["candidates"]]
        payload["root_identity"] = list(payload["root_identity"])
        payload["root_names"] = list(payload["root_names"])
        if attack == "manifest-version":
            payload["schema_version"] = "invalid"
        elif attack == "manifest-state":
            payload["state"] = "committed"
        elif attack in {
            "manifest-dev",
            "manifest-uid",
            "manifest-gid",
            "manifest-mode",
            "manifest-nlink",
            "manifest-size",
        }:
            identity_index = {
                "manifest-dev": 0,
                "manifest-uid": 2,
                "manifest-gid": 3,
                "manifest-mode": 4,
                "manifest-nlink": 5,
                "manifest-size": 6,
            }[attack]
            payload["candidates"][0]["identity"][identity_index] += 1
        elif attack == "manifest-type":
            payload["candidates"][0]["identity"][4] = stat.S_IFDIR | 0o700
        elif attack == "manifest-digest":
            payload["candidates"][0]["raw_sha256"] = "sha256:" + "0" * 64
        elif attack == "manifest-traversal":
            payload["candidates"][0]["name"] = "../escape"
        elif attack == "manifest-absolute":
            payload["candidates"][0]["name"] = "/escape"
        elif attack == "manifest-duplicate":
            payload["candidates"][1] = dict(payload["candidates"][0])
        elif attack == "manifest-missing":
            payload["candidates"].pop()
        elif attack == "manifest-extra":
            payload["candidates"].append(dict(payload["candidates"][0]))
            payload["candidates"][-1]["name"] = (
                ".rollback-journal.ffffffffffffffffffffffffffffffff.tmp"
            )
        elif attack == "root-identity":
            payload["root_identity"][1] += 1
        elif attack == "root-inventory":
            payload["root_names"].append("forged")
            payload["root_names"].sort()
        preparing_path.write_bytes(
            signer._signed_bytes(
                "abandoned-cleanup-preparing",
                payload,
            )
        )
        preparing_path.chmod(0o600)
    elif attack == "symlink":
        candidate.unlink()
        candidate.symlink_to(tmp_path / "outside")
    elif attack == "directory":
        candidate.unlink()
        candidate.mkdir(mode=0o700)
    elif attack == "mode":
        candidate.chmod(0o640)
    elif attack == "hardlink":
        os.link(candidate, tmp_path / "candidate-hardlink")
    elif attack in {"inode", "digest", "size"}:
        raw = candidate.read_bytes()
        candidate.unlink()
        candidate.write_bytes(raw + (b"x" if attack in {"digest", "size"} else b""))
        candidate.chmod(0o600)
    elif attack == "duplicate":
        (root / names[0]).write_bytes(candidate.read_bytes())
        (root / names[0]).chmod(0o600)
    elif attack == "missing":
        candidate.unlink()
    elif attack == "extra":
        (stage / "extra").write_bytes(b"extra")
        (stage / "extra").chmod(0o600)
    elif attack == "manifest-noncanonical":
        preparing_path.write_bytes(preparing_path.read_bytes() + b"\n")
    elif attack == "manifest-oversized":
        preparing_path.write_bytes(
            b"x" * (rollback_store_module._MAX_CLEANUP_MANIFEST_BYTES + 1)
        )
    elif attack == "unknown-temp":
        (stage / ".unknown.tmp").write_bytes(b"unknown")
        (stage / ".unknown.tmp").chmod(0o600)
    elif attack == "manifest-hardlink":
        os.link(preparing_path, tmp_path / "manifest-hardlink-copy")
    before = _v14_tree_inventory(root)
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v14_tree_inventory(root) == before


def _v14_seed_mixed(
    root: Path,
) -> tuple[
    FilesystemRollbackJournalStore,
    str,
    object,
    object,
    tuple[str, ...],
]:
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    rollback_id = "rollback-v14-mixed"
    predecessor, _, _, _ = _install_active_recovery_intent(store, rollback_id)
    unrelated = _prepare(store, "rollback-v14-unrelated")
    ordinary_names = _v14_ordinary_names(2)
    for index, name in enumerate(ordinary_names):
        path = root / name
        path.write_bytes(f"mixed-{index}".encode())
        path.chmod(0o600)
    return store, rollback_id, predecessor, unrelated, ordinary_names


_V18_RECOVERY_TRANSITIONS = (
    "successor_displacement.before_move",
    "successor_displacement.after_durable",
    "prior_candidate.before_publish",
    "prior_candidate.after_publish",
    "prior_head.before_publish",
    "prior_head.after_durable",
    "cleanup_intent.before_temp_create",
    "cleanup_intent.after_temp_create",
    "cleanup_intent.before_temp_write",
    "cleanup_intent.after_temp_write",
    "cleanup_intent.before_temp_fsync",
    "cleanup_intent.after_temp_fsync",
    "cleanup_intent.after_temp_ready",
    "cleanup_intent.before_publish",
    "cleanup_intent.after_publish",
    "successor_quarantine.before_move",
    "successor_quarantine.after_durable",
    "terminal_intent.before_temp_create",
    "terminal_intent.after_temp_create",
    "terminal_intent.before_temp_write",
    "terminal_intent.after_temp_write",
    "terminal_intent.before_temp_fsync",
    "terminal_intent.after_temp_fsync",
    "terminal_intent.after_temp_ready",
    "terminal_intent.before_publish",
    "terminal_intent.after_publish",
    "terminal_tombstone.before_move",
    "terminal_tombstone.after_durable",
    "restoration.before_publish",
    "pending_anchor.before_publish",
    "pending_anchor.after_publish",
    "successor_history.before_publish",
    "successor_history.after_publish",
    "successor_commit.before_publish",
    "successor_commit.after_publish",
    "restoration_head.before_publish",
    "restoration_head.before_temp_create",
    "restoration_head.after_temp_create",
    "restoration_head.before_temp_write",
    "restoration_head.after_temp_write",
    "restoration_head.before_temp_fsync",
    "restoration_head.after_temp_fsync",
    "restoration_head.after_temp_ready",
    "restoration_head.before_replace",
    "restoration_head.after_replace",
    "restoration_head.after_publish",
    "terminal_anchor.before_publish",
    "terminal_anchor.before_temp_create",
    "terminal_anchor.after_temp_create",
    "terminal_anchor.before_temp_write",
    "terminal_anchor.after_temp_write",
    "terminal_anchor.before_temp_fsync",
    "terminal_anchor.after_temp_fsync",
    "terminal_anchor.after_temp_ready",
    "terminal_anchor.before_replace",
    "terminal_anchor.after_replace",
    "terminal_anchor.after_publish",
    "restoration.after_publish",
)


def _v18_expected_mixed_boundaries() -> tuple[str, ...]:
    ordinary_names = _v14_ordinary_names(2)
    transaction_id = "a" * 32
    recovery_name = f".rollback-journal.{transaction_id}.transaction-rollback"
    staged_names = (*ordinary_names, recovery_name)
    boundaries = (
        "stage_dir.before_create",
        "stage_dir.after_create",
        "stage_dir.before_root_fsync",
        "stage_dir.after_root_fsync",
        *_v15_authority_boundaries("authority.preparing.initial"),
    )
    for index, name in enumerate(staged_names):
        prefix = f"stage.move.{index}.{name}"
        boundaries += (
            f"{prefix}.before_move",
            f"{prefix}.after_move",
            f"{prefix}.before_stage_fsync",
            f"{prefix}.after_stage_fsync",
            f"{prefix}.before_root_fsync",
            f"{prefix}.after_root_fsync",
        )
    boundaries += (
        "stage.all_moved",
        *_v15_authority_boundaries("authority.preparing.staged"),
        *_v15_authority_boundaries("authority.committed.g0", root_fsync=True),
        *_v15_authority_boundaries("authority.committed.g1"),
        f"forward.recovery.before.{transaction_id}",
    )
    for transition in _V18_RECOVERY_TRANSITIONS:
        boundaries += (
            *_v15_authority_boundaries(
                f"authority.recovery_checkpoint.{transition}"
            ),
            f"forward.recovery.{transition}",
        )
    boundaries += (
        f"forward.recovery.after.{transaction_id}",
        *_v15_authority_boundaries("authority.committed.g2"),
    )
    for index, name in enumerate(ordinary_names):
        processing_generation = 2 * index + 3
        processed_generation = processing_generation + 1
        prefix = f"forward.tombstone.{name}"
        boundaries += (
            *_v15_authority_boundaries(
                f"authority.committed.g{processing_generation}"
            ),
            *_v15_authority_boundaries(
                f"authority.recovery_checkpoint.tombstone_plan.{index}"
            ),
            f"{prefix}.before_move",
            f"{prefix}.before_stage_fsync",
            *_v15_authority_boundaries(
                f"authority.committed.g{processed_generation}"
            ),
            f"{prefix}.after_stage_fsync",
            f"{prefix}.after_move",
        )
    boundaries += _v15_authority_boundaries("authority.receipt")
    for index in range(len(ordinary_names)):
        boundaries += (
            f"receipt.remove.tombstone.{index}.before_unlink.{index}",
            f"receipt.remove.tombstone.{index}.after_unlink.{index}",
            f"receipt.remove.tombstone.{index}.before_stage_fsync",
            f"receipt.remove.tombstone.{index}.after_stage_fsync",
        )
    return boundaries + (
        "receipt.remove.committed.before_unlink.committed",
        "receipt.remove.committed.after_unlink.committed",
        "receipt.remove.committed.before_stage_fsync",
        "receipt.remove.committed.after_stage_fsync",
        "receipt.remove.preparing.before_unlink.preparing",
        "receipt.remove.preparing.after_unlink.preparing",
        "receipt.remove.preparing.before_stage_fsync",
        "receipt.remove.preparing.after_stage_fsync",
        "receipt.remove.receipt.before_unlink.receipt",
        "receipt.remove.receipt.after_unlink.receipt",
        "receipt.remove.receipt.before_stage_fsync",
        "receipt.remove.receipt.after_stage_fsync",
        "receipt.terminal.before_stage_rmdir",
        "receipt.terminal.after_stage_rmdir",
        "receipt.terminal.before_parent_fsync",
        "receipt.terminal.after_parent_fsync",
    )

_V18_EXPECTED_MIXED_BOUNDARY_CATEGORIES = {
    "stage_directory": 4,
    "stage_move": 18,
    "stage_complete": 1,
    "authority_state_write": 122,
    "recovery_checkpoint_write": 720,
    "recovery_transition": 60,
    "ordinary_tombstone": 8,
    "receipt_removal": 20,
    "stage_rmdir": 2,
    "parent_fsync": 2,
}
_V18_AUTHORITY_STATE_EVENTS = frozenset(
    (
        *_v15_authority_boundaries("authority.preparing.initial"),
        *_v15_authority_boundaries("authority.preparing.staged"),
        *_v15_authority_boundaries("authority.committed.g0", root_fsync=True),
        *(
            event
            for generation in range(1, 7)
            for event in _v15_authority_boundaries(
                f"authority.committed.g{generation}"
            )
        ),
        *_v15_authority_boundaries("authority.receipt"),
    )
)
_V18_RECOVERY_CHECKPOINT_EVENTS = frozenset(
    event
    for transition in (
        *_V18_RECOVERY_TRANSITIONS,
        "tombstone_plan.0",
        "tombstone_plan.1",
    )
    for event in _v15_authority_boundaries(
        f"authority.recovery_checkpoint.{transition}"
    )
)
_V18_MIXED_BOUNDARY_EVENT_UNIVERSE = {
    "stage_directory": frozenset(
        {
            "stage_dir.before_create",
            "stage_dir.after_create",
            "stage_dir.before_root_fsync",
            "stage_dir.after_root_fsync",
        }
    ),
    "stage_move": frozenset(
        f"stage.move.{index}.{name}.{step}"
        for index, name in enumerate(
            (
                *_v14_ordinary_names(2),
                ".rollback-journal."
                + "a" * 32
                + ".transaction-rollback",
            )
        )
        for step in (
            "before_move",
            "after_move",
            "before_stage_fsync",
            "after_stage_fsync",
            "before_root_fsync",
            "after_root_fsync",
        )
    ),
    "stage_complete": frozenset({"stage.all_moved"}),
    "authority_state_write": _V18_AUTHORITY_STATE_EVENTS,
    "recovery_checkpoint_write": _V18_RECOVERY_CHECKPOINT_EVENTS,
    "recovery_transition": frozenset(
        {
            f"forward.recovery.before.{'a' * 32}",
            f"forward.recovery.after.{'a' * 32}",
            *(
                f"forward.recovery.{transition}"
                for transition in _V18_RECOVERY_TRANSITIONS
            ),
        }
    ),
    "ordinary_tombstone": frozenset(
        f"forward.tombstone.{name}.{step}"
        for name in _v14_ordinary_names(2)
        for step in (
            "before_move",
            "before_stage_fsync",
            "after_stage_fsync",
            "after_move",
        )
    ),
    "receipt_removal": frozenset(
        (
            *(
                f"receipt.remove.tombstone.{index}.{step}"
                for index in range(2)
                for step in (
                    f"before_unlink.{index}",
                    f"after_unlink.{index}",
                    "before_stage_fsync",
                    "after_stage_fsync",
                )
            ),
            *(
                f"receipt.remove.{leaf}.{step}"
                for leaf in ("committed", "preparing", "receipt")
                for step in (
                    f"before_unlink.{leaf}",
                    f"after_unlink.{leaf}",
                    "before_stage_fsync",
                    "after_stage_fsync",
                )
            ),
        )
    ),
    "stage_rmdir": frozenset(
        {
            "receipt.terminal.before_stage_rmdir",
            "receipt.terminal.after_stage_rmdir",
        }
    ),
    "parent_fsync": frozenset(
        {
            "receipt.terminal.before_parent_fsync",
            "receipt.terminal.after_parent_fsync",
        }
    ),
}


def _v18_mixed_boundary_category(event: str) -> str:
    matches = tuple(
        category
        for category, universe in _V18_MIXED_BOUNDARY_EVENT_UNIVERSE.items()
        if event in universe
    )
    if len(matches) != 1:
        raise AssertionError(
            f"mixed boundary event must have exactly one category: {event}"
        )
    return matches[0]




def test_v18_every_mixed_transaction_boundary_reopens_exact957(
    tmp_path: Path,
) -> None:
    inventory_root = tmp_path / "mixed-inventory"
    inventory_store, rollback_id, predecessor, unrelated, ordinary_names = (
        _v14_seed_mixed(inventory_root)
    )
    events = _v14_run_cleanup_with_fault(inventory_store, crash_at=None)
    expected_events = _v18_expected_mixed_boundaries()
    with pytest.raises(AssertionError, match="exactly one category"):
        _v18_mixed_boundary_category("unknown.event.form")
    expected_categories = Counter(map(_v18_mixed_boundary_category, expected_events))
    observed_categories = Counter(map(_v18_mixed_boundary_category, events))
    assert set(_V18_MIXED_BOUNDARY_EVENT_UNIVERSE) == set(
        _V18_EXPECTED_MIXED_BOUNDARY_CATEGORIES
    )
    assert (
        sum(map(len, _V18_MIXED_BOUNDARY_EVENT_UNIVERSE.values()))
        == 957
    )
    assert sum(_V18_EXPECTED_MIXED_BOUNDARY_CATEGORIES.values()) == 957
    assert len(expected_events) == 957
    assert len(events) == 957
    assert len(expected_events) == len(set(expected_events))
    assert len(events) == len(set(events))
    assert expected_categories == _V18_EXPECTED_MIXED_BOUNDARY_CATEGORIES
    assert observed_categories == _V18_EXPECTED_MIXED_BOUNDARY_CATEGORIES
    assert events == expected_events
    completed = inventory_store.get(rollback_id)
    assert completed is not None
    assert completed.generation == predecessor.generation + 2
    assert len(completed.terminal_quarantine_refs) == 1
    assert inventory_store.get(unrelated.rollback_id) == unrelated
    inventory_store.close()
    boundary_indexes = tuple(range(len(expected_events)))
    assert boundary_indexes
    assert any(
        expected_events[index].startswith("forward.recovery.")
        for index in boundary_indexes
    )
    for crash_at in boundary_indexes:
        root = tmp_path / f"mixed-{crash_at}"
        store, rollback_id, predecessor, unrelated, ordinary_names = _v14_seed_mixed(
            root
        )
        observed = _v14_run_cleanup_with_fault(store, crash_at=events[crash_at])
        assert events[crash_at] in observed
        assert observed.count(events[crash_at]) == 1
        store.close()
        before_reopen = _v15_tree_snapshot(root)
        try:
            reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
        except RollbackCorruptionError:
            assert _v15_tree_snapshot(root) == before_reopen
            with pytest.raises(RollbackCorruptionError):
                FilesystemRollbackJournalStore(root, authority_key=KEY)
            assert _v15_tree_snapshot(root) == before_reopen
            continue
        after_reopen = _v15_tree_snapshot(root)
        stage_name = ".rollback-journal.cleanup-staging"
        stage_prefix = f"{stage_name}/"
        partial_stage = (
            stage_name in before_reopen
            and f"{stage_prefix}committed" not in before_reopen
            and f"{stage_prefix}receipt" not in before_reopen
        )
        if partial_stage:
            expected_after_rollback = {}
            relocated_names: set[str] = set()
            for name, value in before_reopen.items():
                if name == stage_name:
                    continue
                if name.startswith(stage_prefix):
                    leaf = name.removeprefix(stage_prefix)
                    if leaf in {
                        "preparing",
                        ".preparing.tmp",
                        ".committed.tmp",
                        ".receipt.tmp",
                    }:
                        continue
                    relocated_names.add(leaf)
                    expected_after_rollback[leaf] = value
                    continue
                expected_after_rollback[name] = value
            for name in relocated_names:
                expected = expected_after_rollback[name]
                actual = after_reopen[name]
                assert actual[:2] == expected[:2]
                assert actual[2][:8] == expected[2][:8]
                assert actual[2][8] >= expected[2][8]
                if rollback_store_module.sys.platform == "darwin":
                    assert actual[2][8] > expected[2][8]
                assert actual[3] == expected[3]
                expected_after_rollback[name] = actual
            assert after_reopen == expected_after_rollback
            reopened.close()
            again = FilesystemRollbackJournalStore(root, authority_key=KEY)
            current = again.get(rollback_id)
            assert current is not None
            assert current.generation == predecessor.generation + 2
            assert len(current.terminal_quarantine_refs) == 1
            assert again.get(unrelated.rollback_id) == unrelated
            assert all(not (root / name).exists() for name in ordinary_names)
            assert not (root / ".rollback-journal.cleanup-staging").exists()
            terminal = _v15_tree_snapshot(root)
            again.close()
            final = FilesystemRollbackJournalStore(root, authority_key=KEY)
            assert _v15_tree_snapshot(root) == terminal
            assert final.get(rollback_id) == current
            assert final.get(unrelated.rollback_id) == unrelated
            final.close()
            continue
        if after_reopen == before_reopen:
            assert reopened.get(unrelated.rollback_id) == unrelated
            reopened.close()
            again = FilesystemRollbackJournalStore(root, authority_key=KEY)
            assert _v15_tree_snapshot(root) == after_reopen
            assert again.get(unrelated.rollback_id) == unrelated
            again.close()
            continue
        current = reopened.get(rollback_id)
        assert current is not None
        assert current.generation == predecessor.generation + 2
        assert len(current.terminal_quarantine_refs) == 1
        assert reopened.get(unrelated.rollback_id) == unrelated
        assert all(not (root / name).exists() for name in ordinary_names)
        assert not (root / ".rollback-journal.cleanup-staging").exists()
        history = reopened.history(rollback_id)
        reopened.close()
        again = FilesystemRollbackJournalStore(root, authority_key=KEY)
        assert again.get(rollback_id) == current
        assert again.history(rollback_id) == history
        assert again.get(unrelated.rollback_id) == unrelated
        again.close()


def test_v14_staging_entry_and_manifest_caps_fail_before_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "entry-cap"
    store = FilesystemRollbackJournalStore(root, authority_key=KEY)
    store.close()
    stage = root / ".rollback-journal.cleanup-staging"
    stage.mkdir(mode=0o700)
    for index in range(rollback_store_module._MAX_ABANDONED_TEMPS + 4):
        path = stage / f"x{index:03d}"
        path.write_bytes(b"x")
        path.chmod(0o600)
    before = _v14_tree_inventory(root)
    with pytest.raises(RollbackCorruptionError, match="enumeration bound"):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v14_tree_inventory(root) == before


def _v14_leave_committed_mixed(
    root: Path,
) -> tuple[str, tuple[str, ...]]:
    store, rollback_id, _, _, ordinary_names = _v14_seed_mixed(root)

    def crash(boundary: str) -> None:
        if boundary == "authority.committed.g0.after_stage_fsync":
            raise _V14ProcessCrash(boundary)

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = crash
    try:
        with pytest.raises(rollback_store_module._CleanupInjectedCrash):
            store._cleanup_abandoned_temps()
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
        store.close()
    return rollback_id, ordinary_names


@pytest.mark.parametrize("transition", ("cleanup_intent", "terminal_intent"))
@pytest.mark.parametrize(
    "attack",
    ("missing", "torn", "mutated", "same-name-new-inode"),
)
def test_v18_created_stage_replacement_temp_tamper_is_nonmutating(
    tmp_path: Path,
    transition: str,
    attack: str,
) -> None:
    root = tmp_path / f"{transition}-{attack}"
    _v14_leave_committed_mixed(root)

    def stop_after_create(boundary: str) -> None:
        if boundary == f"forward.recovery.{transition}.after_temp_create":
            raise _V14ProcessCrash(boundary)

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = stop_after_create
    try:
        with pytest.raises(rollback_store_module._CleanupInjectedCrash):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None

    stage = root / ".rollback-journal.cleanup-staging"
    committed = stage / "committed"
    verifier = object.__new__(FilesystemRollbackJournalStore)
    verifier._authority_key = KEY
    verifier._domain = "rollback-journal"
    committed_payload = dict(
        verifier._verify_signed(
            committed.read_bytes(),
            "abandoned-cleanup-committed",
        )
    )
    recovery_proof = committed_payload["recovery_proof"]
    assert type(recovery_proof) is dict
    replacement = recovery_proof["replacement"]
    assert type(replacement) is dict
    assert replacement["state"] == "created"
    temp = stage / str(replacement["temp"])
    assert temp.read_bytes() == b""
    expected_payload = str(replacement["expected_payload"]).encode("utf-8")
    if attack == "missing":
        temp.unlink()
    elif attack == "torn":
        temp.write_bytes(expected_payload[: len(expected_payload) // 2])
    elif attack == "mutated":
        temp.write_bytes(b"x" * len(expected_payload))
    else:
        _replace_with_same_bytes(temp)

    attacked = _v15_tree_snapshot(root)
    for _ in range(2):
        with pytest.raises(RollbackCorruptionError, match="replacement"):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
        assert _v15_tree_snapshot(root) == attacked


@pytest.mark.parametrize(
    "attack",
    (
        "missing-recovery",
        "ordinary-replayed",
        "ordinary-tamper",
        "candidate-symlink",
        "candidate-directory",
        "candidate-mode",
        "candidate-hardlink",
        "candidate-inode",
        "candidate-dev",
        "candidate-uid",
        "candidate-gid",
        "candidate-type",
        "candidate-manifest-mode",
        "candidate-nlink",
        "candidate-size",
        "candidate-digest",
        "committed-mode",
        "committed-hardlink",
        "committed-noncanonical",
        "committed-version",
        "committed-state",
        "committed-token",
        "committed-preparing-digest",
        "committed-domain",
        "committed-candidates",
        "intent-token",
        "intent-rollback-id",
        "cross-id-intent",
        "stale-preparing",
        "extra",
    ),
)
def test_v14_forged_committed_staging_cannot_publish_unrelated_state(
    tmp_path: Path,
    attack: str,
) -> None:
    root = tmp_path / attack
    rollback_id, ordinary_names = _v14_leave_committed_mixed(root)
    stage = root / ".rollback-journal.cleanup-staging"
    intent = next(stage.glob("*.transaction-rollback"))
    committed = stage / "committed"
    preparing = stage / "preparing"
    signer = object.__new__(FilesystemRollbackJournalStore)
    signer._authority_key = KEY
    signer._domain = "rollback-journal"
    verifier = object.__new__(FilesystemRollbackJournalStore)
    verifier._authority_key = KEY
    verifier._domain = "rollback-journal"
    if attack == "missing-recovery":
        intent.unlink()
    elif attack == "ordinary-replayed":
        os.rename(stage / ordinary_names[0], root / ordinary_names[0])
    elif attack == "ordinary-tamper":
        path = stage / ordinary_names[0]
        path.write_bytes(path.read_bytes() + b"x")
    elif attack == "candidate-symlink":
        path = stage / ordinary_names[0]
        path.unlink()
        path.symlink_to(tmp_path / "outside")
    elif attack == "candidate-directory":
        path = stage / ordinary_names[0]
        path.unlink()
        path.mkdir(mode=0o700)
    elif attack == "candidate-mode":
        (stage / ordinary_names[0]).chmod(0o640)
    elif attack == "candidate-hardlink":
        os.link(stage / ordinary_names[0], tmp_path / "candidate-copy")
    elif attack == "candidate-inode":
        path = stage / ordinary_names[0]
        raw = path.read_bytes()
        path.unlink()
        path.write_bytes(raw)
        path.chmod(0o600)
    elif attack.startswith("candidate-"):
        preparing_payload = dict(
            verifier._verify_signed(
                preparing.read_bytes(),
                "abandoned-cleanup-preparing",
            )
        )
        preparing_payload["candidates"] = [
            dict(item) for item in preparing_payload["candidates"]
        ]
        candidate_payload = preparing_payload["candidates"][0]
        candidate_payload["identity"] = list(candidate_payload["identity"])
        if attack in {
            "candidate-dev",
            "candidate-uid",
            "candidate-gid",
            "candidate-manifest-mode",
            "candidate-nlink",
            "candidate-size",
        }:
            identity_index = {
                "candidate-dev": 0,
                "candidate-uid": 2,
                "candidate-gid": 3,
                "candidate-manifest-mode": 4,
                "candidate-nlink": 5,
                "candidate-size": 6,
            }[attack]
            candidate_payload["identity"][identity_index] += 1
        elif attack == "candidate-type":
            candidate_payload["identity"][4] = stat.S_IFDIR | 0o700
        elif attack == "candidate-digest":
            candidate_payload["raw_sha256"] = "sha256:" + "0" * 64
        preparing_raw = signer._signed_bytes(
            "abandoned-cleanup-preparing",
            preparing_payload,
        )
        preparing.write_bytes(preparing_raw)
        preparing.chmod(0o600)
        committed_payload = dict(
            verifier._verify_signed(
                committed.read_bytes(),
                "abandoned-cleanup-committed",
            )
        )
        committed_payload["preparing_digest"] = canonical_digest(preparing_raw)
        committed.write_bytes(
            signer._signed_bytes(
                "abandoned-cleanup-committed",
                committed_payload,
            )
        )
        committed.chmod(0o600)
    elif attack == "committed-mode":
        committed.chmod(0o640)
    elif attack == "committed-hardlink":
        os.link(committed, tmp_path / "committed-hardlink-copy")
    elif attack == "committed-noncanonical":
        committed.write_bytes(committed.read_bytes() + b"\n")
    elif attack.startswith("committed-"):
        payload = dict(
            verifier._verify_signed(
                committed.read_bytes(),
                "abandoned-cleanup-committed",
            )
        )
        if attack == "committed-version":
            payload["schema_version"] = "invalid"
        elif attack == "committed-state":
            payload["state"] = "preparing"
        elif attack == "committed-token":
            payload["transaction_id"] = "0" * 64
        elif attack == "committed-preparing-digest":
            payload["preparing_digest"] = "sha256:" + "0" * 64
        elif attack == "committed-domain":
            payload["domain"] = "other"
        elif attack == "committed-candidates":
            payload["candidate_states"] = list(payload["candidate_states"])[1:]
        committed.write_bytes(
            signer._signed_bytes("abandoned-cleanup-committed", payload)
        )
        committed.chmod(0o600)
    elif attack in {"intent-token", "intent-rollback-id", "cross-id-intent"}:
        payload = dict(
            verifier._verify_signed(
                intent.read_bytes(),
                "publication-rollback-intent",
            )
        )
        if attack in {"intent-token", "cross-id-intent"}:
            payload["transaction_id"] = "0" * 32
        if attack in {"intent-rollback-id", "cross-id-intent"}:
            payload["rollback_id"] = "rollback-v14-cross-id"
        intent.write_bytes(signer._signed_bytes("publication-rollback-intent", payload))
        intent.chmod(0o600)
    elif attack == "stale-preparing":
        payload = dict(
            verifier._verify_signed(
                preparing.read_bytes(),
                "abandoned-cleanup-preparing",
            )
        )
        payload["root_identity"] = list(payload["root_identity"])
        payload["root_identity"][1] += 1
        preparing_raw = signer._signed_bytes(
            "abandoned-cleanup-preparing",
            payload,
        )
        preparing.write_bytes(preparing_raw)
        preparing.chmod(0o600)
        committed_payload = dict(
            verifier._verify_signed(
                committed.read_bytes(),
                "abandoned-cleanup-committed",
            )
        )
        committed_payload["preparing_digest"] = canonical_digest(preparing_raw)
        committed.write_bytes(
            signer._signed_bytes(
                "abandoned-cleanup-committed",
                committed_payload,
            )
        )
        committed.chmod(0o600)
    elif attack == "extra":
        extra = stage / "extra"
        extra.write_bytes(b"extra")
        extra.chmod(0o600)
    before = _v14_tree_inventory(root)
    unrelated_head = (root / "journal.rollback-v14-unrelated.head").read_bytes()
    with pytest.raises(RollbackCorruptionError) as caught:
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    if attack == "candidate-symlink":
        assert isinstance(caught.value.__cause__, OSError)
        assert caught.value.__cause__.errno is not None
    assert _v14_tree_inventory(root) == before
    assert (root / "journal.rollback-v14-unrelated.head").read_bytes() == unrelated_head
    assert not (root / f"journal.{rollback_id}.blocked").exists()


def test_v15_recovery_intent_same_bytes_new_inode_rejects_before_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "recovery-intent-same-bytes"
    rollback_id, _ = _v14_leave_committed_mixed(root)
    stage = root / ".rollback-journal.cleanup-staging"
    intent = next(stage.glob("*.transaction-rollback"))
    _replace_with_same_bytes(intent)
    before = _v14_tree_inventory(root)
    unrelated_head = (root / "journal.rollback-v14-unrelated.head").read_bytes()

    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)

    assert _v14_tree_inventory(root) == before
    assert (root / "journal.rollback-v14-unrelated.head").read_bytes() == unrelated_head
    assert not (root / f"journal.{rollback_id}.blocked").exists()


@pytest.mark.parametrize("authority", ("preparing", "committed", "receipt"))
@pytest.mark.parametrize(
    "phase",
    (
        "create",
        "zero",
        "short-0",
        "short-1",
        "short-2",
        "full-before-fsync",
        "after-fsync",
        "post",
    ),
)
def test_v15_partial_authority_temps_are_disposable_and_reopen_converges(
    tmp_path: Path,
    authority: str,
    phase: str,
) -> None:
    root = tmp_path / f"{authority}-{phase}"
    store, names, payloads = _v14_seed_ordinary(root, 1)
    store.close()
    process = multiprocessing.get_context("spawn").Process(
        target=_v15_process_authority_crash,
        args=(str(root), authority, phase),
    )
    process.start()
    process.join(30)
    assert not process.is_alive()
    assert process.exitcode not in (None, 0)

    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    if authority == "receipt" or (authority == "committed" and phase == "post"):
        assert all(not (root / name).exists() for name in names)
    else:
        assert {name: (root / name).read_bytes() for name in names} == payloads
    reopened.close()
    reopened = FilesystemRollbackJournalStore(root, authority_key=KEY)
    reopened.close()
    assert all(not (root / name).exists() for name in names)
    assert _owned_temps(root, "rollback-journal") == ()
    assert not (root / ".rollback-journal.cleanup-staging").exists()


def test_v15_late_ordinary_replacement_after_processing_is_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "late-ordinary-replacement"
    _, ordinary_names = _v14_leave_committed_mixed(root)
    target = root / ".rollback-journal.cleanup-staging" / ordinary_names[0]
    replacement_snapshot: dict[
        str,
        tuple[str, bytes | None, tuple[int, ...], str | None],
    ] = {}

    def replace_after_processing(boundary: str) -> None:
        if boundary == "authority.committed.g3.after_stage_fsync":
            _replace_with_same_bytes(target)
            replacement_snapshot.update(_v15_tree_snapshot(root))

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = replace_after_processing
    try:
        with pytest.raises(RollbackCorruptionError):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    assert target.is_file()
    assert _v15_tree_snapshot(root) == replacement_snapshot
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v15_tree_snapshot(root) == replacement_snapshot


def test_v15_late_recovery_replacement_after_publication_is_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "late-recovery-replacement"
    _v14_leave_committed_mixed(root)
    stage = root / ".rollback-journal.cleanup-staging"
    intent = next(stage.glob("*.transaction-rollback"))
    intent_raw = intent.read_bytes()
    replacement_snapshot: dict[
        str,
        tuple[str, bytes | None, tuple[int, ...], str | None],
    ] = {}

    def replace_after_recovery(boundary: str) -> None:
        if boundary.startswith("forward.recovery.after."):
            intent.write_bytes(intent_raw)
            intent.chmod(0o600)
            replacement_snapshot.update(_v15_tree_snapshot(root))

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = replace_after_recovery
    try:
        with pytest.raises(RollbackCorruptionError):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    assert intent.read_bytes() == intent_raw
    assert _v15_tree_snapshot(root) == replacement_snapshot
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v15_tree_snapshot(root) == replacement_snapshot


@pytest.mark.parametrize(
    ("field", "identity_index"),
    (
        ("dev", 0),
        ("inode", 1),
        ("uid", 2),
        ("gid", 3),
        ("mode", 4),
        ("nlink", 5),
    ),
)
def test_v15_signed_stage_identity_tamper_fails_without_mutation(
    tmp_path: Path,
    field: str,
    identity_index: int,
) -> None:
    root = tmp_path / f"stage-identity-{field}"
    _v14_leave_preparing(root, 2)
    stage = root / ".rollback-journal.cleanup-staging"
    preparing = stage / "preparing"
    verifier = object.__new__(FilesystemRollbackJournalStore)
    verifier._authority_key = KEY
    verifier._domain = "rollback-journal"
    payload = dict(
        verifier._verify_signed(
            preparing.read_bytes(),
            "abandoned-cleanup-preparing",
        )
    )
    payload["stage_identity"] = list(payload["stage_identity"])
    payload["stage_identity"][identity_index] += 1
    preparing.write_bytes(
        verifier._signed_bytes(
            "abandoned-cleanup-preparing",
            payload,
        )
    )
    preparing.chmod(0o600)
    attacked = _v15_tree_snapshot(root)
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v15_tree_snapshot(root) == attacked


def test_v15_stage_path_replacement_preserves_replacement_and_orphan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "stage-replacement"
    _v14_leave_committed_mixed(root)
    stage = root / ".rollback-journal.cleanup-staging"
    orphan = root / ".rollback-journal.cleanup-staging.orphan"
    stage.rename(orphan)
    stage.mkdir(mode=0o700)
    for source in orphan.iterdir():
        replacement = stage / source.name
        replacement.write_bytes(source.read_bytes())
        replacement.chmod(stat.S_IMODE(source.stat().st_mode))
    attacked = _v15_tree_snapshot(root)
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert stage.is_dir()
    assert orphan.is_dir()
    assert _v15_tree_snapshot(root) == attacked


def test_v16_processing_candidate_deletion_rejects_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "processing-candidate-deletion"
    store, ordinary_names, _ = _v14_seed_ordinary(root, 1)

    def leave_committed(boundary: str) -> None:
        if boundary == "authority.committed.g0.after_stage_fsync":
            raise _V14ProcessCrash(boundary)

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = leave_committed
    try:
        with pytest.raises(rollback_store_module._CleanupInjectedCrash):
            store._cleanup_abandoned_temps()
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
        store.close()
    target = root / ".rollback-journal.cleanup-staging" / ordinary_names[0]
    attacked: dict[
        str,
        tuple[str, bytes | None, tuple[int, ...], str | None],
    ] = {}

    def delete_after_processing(boundary: str) -> None:
        if boundary == "authority.committed.g1.after_stage_fsync":
            target.unlink()
            attacked.update(_v15_tree_snapshot(root))

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = delete_after_processing
    try:
        with pytest.raises(RollbackCorruptionError):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    assert _v15_tree_snapshot(root) == attacked
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v15_tree_snapshot(root) == attacked


@pytest.mark.parametrize(
    ("suffix", "checkpoint"),
    (
        ("transaction-rollback", "authority.committed.g1.after_stage_fsync"),
        (
            "displaced-head",
            "forward.recovery.successor_displacement.after_durable",
        ),
        (
            "prior-candidate",
            "forward.recovery.prior_candidate.after_publish",
        ),
    ),
)
def test_v16_recovery_processing_same_bytes_new_inode_rejects_exactly(
    tmp_path: Path,
    suffix: str,
    checkpoint: str,
) -> None:
    root = tmp_path / f"processing-recovery-{suffix}"
    _v14_leave_committed_mixed(root)

    def stop_after_processing(boundary: str) -> None:
        if boundary == checkpoint:
            raise _V14ProcessCrash(boundary)

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = stop_after_processing
    try:
        with pytest.raises(rollback_store_module._CleanupInjectedCrash):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    stage = root / ".rollback-journal.cleanup-staging"
    matches = (*stage.glob(f"*.{suffix}"), *root.glob(f"*.{suffix}"))
    assert len(matches) == 1
    target = matches[0]
    _replace_with_same_bytes(target)
    attacked = _v15_tree_snapshot(root)
    with pytest.raises(RollbackCorruptionError):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v15_tree_snapshot(root) == attacked


def test_v16_unrelated_temp_after_recovery_is_preserved_and_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "unrelated-recovery-temp"
    _v14_leave_committed_mixed(root)
    injected = root / f".rollback-journal.{'f' * 32}.tmp"
    attacked: dict[
        str,
        tuple[str, bytes | None, tuple[int, ...], str | None],
    ] = {}

    def inject_after_recovery(boundary: str) -> None:
        if boundary.startswith("forward.recovery.after."):
            injected.write_bytes(b"unrelated-newer-temp")
            injected.chmod(0o600)
            attacked.update(_v15_tree_snapshot(root))

    rollback_store_module._TEST_CLEANUP_FAULT_HOOK = inject_after_recovery
    try:
        with pytest.raises(RollbackCorruptionError):
            FilesystemRollbackJournalStore(root, authority_key=KEY)
    finally:
        rollback_store_module._TEST_CLEANUP_FAULT_HOOK = None
    assert injected.read_bytes() == b"unrelated-newer-temp"
    assert _v15_tree_snapshot(root) == attacked


@pytest.mark.parametrize(
    "attack",
    ("malformed", "non-post", "identity-mismatch", "temp-present"),
)
def test_v16_terminal_receipt_replacement_proof_negatives_are_nonmutating(
    tmp_path: Path,
    attack: str,
) -> None:
    root = tmp_path / f"receipt-replacement-{attack}"
    store, _, _, _, _ = _v14_seed_mixed(root)
    _v14_run_cleanup_with_fault(
        store,
        crash_at="authority.receipt.after_rename",
    )
    store.close()
    stage = root / ".rollback-journal.cleanup-staging"
    receipt_path = stage / "receipt"
    signer = object.__new__(FilesystemRollbackJournalStore)
    signer._authority_key = KEY
    signer._domain = "rollback-journal"
    receipt = dict(
        signer._verify_signed(
            receipt_path.read_bytes(),
            "abandoned-cleanup-receipt",
        )
    )
    recovery = receipt["recovery_proof"]
    assert type(recovery) is dict
    destination = next(
        item for item in recovery["objects"] if item["location"] == "root"
    )
    destination_raw = (root / destination["path"]).read_bytes()
    replacement = {
        "destination": destination["path"],
        "destination_digest": None,
        "destination_identity": None,
        "expected_digest": destination["raw_sha256"],
        "expected_size": len(destination_raw),
        "expected_payload": destination_raw.decode("utf-8"),
        "identity": list(destination["identity"]),
        "observed_digest": destination["raw_sha256"],
        "state": "post",
        "temp": f".rollback-journal.{'e' * 32}.tmp",
    }
    if attack == "malformed":
        replacement["extra"] = True
    elif attack == "non-post":
        replacement["state"] = "ready"
    elif attack == "identity-mismatch":
        replacement["identity"] = list(replacement["identity"])
        replacement["identity"][1] += 1
    else:
        temp = root / replacement["temp"]
        temp.write_bytes(replacement["expected_payload"].encode("utf-8"))
        temp.chmod(0o600)
    receipt["terminal_replacement_proof"] = replacement
    receipt_path.write_bytes(
        signer._signed_bytes("abandoned-cleanup-receipt", receipt)
    )
    receipt_path.chmod(0o600)
    attacked = _v15_tree_snapshot(root)
    with pytest.raises(
        (RollbackCorruptionError, rollback_store_module.RollbackValidationError)
    ):
        FilesystemRollbackJournalStore(root, authority_key=KEY)
    assert _v15_tree_snapshot(root) == attacked

