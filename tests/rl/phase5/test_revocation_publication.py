from __future__ import annotations

import array
import hashlib
import ctypes
import fcntl
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import tempfile
import sys
import stat
import threading
from pathlib import Path

import pytest
from pydantic import BaseModel
from scripts.rl_phase5.g4_bind_mount_attack import (
    request_preconfigured_bind_replace,
)

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import HmacSha256ReceiptAuthenticator
from breadboard.rl.phase5.f2_authority_authoring import (
    F2C4DynamicAuthorityInput,
    F2C4TargetDynamicObservations,
    F2C4TargetDynamicPlanInput,
    author_f2_target_dynamic_authority,
)
from breadboard.rl.phase5.revocation_publication import (
    _FS_APPEND_FL,
    _FS_IMMUTABLE_FL,
    _FS_IOC_GETFLAGS,
    _FS_IOC_SETFLAGS,
    _DarwinKernelAuthorityFlags,
    _LinuxKernelAuthorityFlags,
    _kernel_authority_flags,
    FilesystemRevocationSnapshotPublisher,
    MonotonicRevocationAuthorityIdentity,
    PreprovisionedAppendOnlyMonotonicRevocationAuthority,
    RevocationPublicationConflictError,
    RevocationPublicationIntegrityError,
    RevocationSnapshotPublishRequest,
    MonotonicRevocationWitness,
)
from breadboard.rl.state.cas import ArtifactIntegrityError, FilesystemCAS
from tests.rl.harness.test_config_admission import (
    PrivilegedEffectProbe,
    _admission_fixture,
    _d,
    _runtime_like,
)
from tests.rl.phase5.test_f2_target_dynamic_packet_authoring import (
    _author as author_real_f2_packet,
    _authoring_value as real_f2_authoring_value,
)
from tests.rl.harness.test_config_runtime_persistence import _denial
from tests.rl.phase5.test_f3_authority_authoring import _spec as f3_authority_spec


class _TestMonotonicAuthority:
    def __init__(self, root: Path) -> None:
        self._lock = threading.Lock()
        self._root = root
        self._root.mkdir(exist_ok=True)

    def _values(self) -> list[MonotonicRevocationWitness]:
        names = sorted(
            self._root.iterdir(),
            key=lambda path: int(path.stem),
        )
        return [
            MonotonicRevocationWitness.model_validate_json(
                path.read_bytes(),
                strict=True,
            )
            for path in names
        ]

    def latest(self) -> MonotonicRevocationWitness | None:
        with self._lock:
            values = self._values()
            return None if not values else values[-1]

    def compare_and_append(
        self,
        expected: MonotonicRevocationWitness | None,
        successor: MonotonicRevocationWitness,
    ) -> MonotonicRevocationWitness:
        with self._lock:
            values = self._values()
            latest = None if not values else values[-1]
            if latest != expected or successor.generation != len(values) + 1:
                raise RevocationPublicationConflictError("test monotonic CAS conflict")
            target = self._root / f"{successor.generation}.json"
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                payload = successor.canonical_bytes()
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return successor


def _preprovision(tmp_path: Path) -> tuple[Path, Path, int]:
    pointer = tmp_path / "revocation-pointer"
    high_water = tmp_path / "revocation-high-water"
    pointer.mkdir(parents=True)
    (pointer / "history").mkdir()
    (pointer / "operations").mkdir()
    high_water.mkdir()
    lock_path = tmp_path / "revocation-publication.lock"
    lock_path.write_bytes(b"")
    lock_fd = os.open(lock_path, os.O_RDWR)
    return pointer, high_water, lock_fd


def _process_publish(
    cas_root: str,
    pointer_root: str,
    high_water_root: str,
    request_payload: dict[str, object],
    barrier: object,
    queue: object,
) -> None:
    cas = FilesystemCAS(cas_root)
    publisher = _open_publisher(
        cas,
        Path(pointer_root),
        Path(high_water_root),
    )
    try:
        barrier.wait()
        try:
            receipt = publisher.publish(
                RevocationSnapshotPublishRequest.model_validate(
                    request_payload, strict=True
                )
            )
            queue.put(("published", receipt.generation))
        except RevocationPublicationConflictError:
            queue.put(("conflict", None))
    finally:
        publisher.close()
        cas.close()


def _binding(character: str, *, epoch: int) -> c.RevocationBinding:
    return c.RevocationBinding(
        scope_digest=_d("1"),
        epoch=epoch,
        state_digest=_d(character),
    )


_TRUSTED_ROOT = Path(tempfile.mkdtemp(prefix="bb-revocation-trusted-f3-"))
_TRUSTED_BASE = f3_authority_spec(_TRUSTED_ROOT)


def _trusted_f3(binding: c.RevocationBinding):
    subject = c.AuthenticatedSubject(
        tenant_id=_TRUSTED_BASE.policy.subject.tenant_id,
        principal_id=_TRUSTED_BASE.policy.subject.principal_id,
        authority_scope_digest=binding.scope_digest,
    )
    policy = _TRUSTED_BASE.policy.model_copy(
        update={"subject": subject, "revocation": binding}
    )
    candidate = _TRUSTED_BASE.model_copy(update={"policy": policy})
    return type(candidate).model_validate(
        {name: getattr(candidate, name) for name in type(candidate).model_fields},
        strict=True,
    )


def _request(
    operation_id: str,
    binding: c.RevocationBinding,
    *,
    expected_generation: int | None,
    expected_epoch: int | None,
    predecessor: BaseModel | None = None,
) -> RevocationSnapshotPublishRequest:
    if expected_generation is not None and predecessor is None:
        predecessor = _trusted_f3(
            _binding(
                str(expected_generation + 1),
                epoch=expected_generation + 6,
            )
        )
    return RevocationSnapshotPublishRequest(
        operation_id=operation_id,
        scope_digest=binding.scope_digest,
        expected_generation=expected_generation,
        expected_epoch=expected_epoch,
        binding=binding,
        predecessor_authority=predecessor,
    )


def _authenticator() -> HmacSha256ReceiptAuthenticator:
    return HmacSha256ReceiptAuthenticator(
        key_id="revocation-publication-test-key",
        key=b"revocation-publication-test-key-material-32-bytes",
    )


def _open_publisher(
    cas: FilesystemCAS,
    pointer: Path,
    high_water: Path,
    *,
    authenticator: HmacSha256ReceiptAuthenticator | None = None,
) -> FilesystemRevocationSnapshotPublisher:
    authority_variable = (
        "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_ROOT"
        if sys.platform == "linux"
        else "BREADBOARD_PRIVILEGED_REVOCATION_AUTHORITY_ROOT"
    )
    authority_root = os.environ.get(authority_variable)
    if authority_root is None:
        pytest.skip(
            "production publication requires a privileged preprovisioned kernel authority"
        )
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(
        Path(authority_root)
    )
    return FilesystemRevocationSnapshotPublisher(
        cas,
        pointer,
        high_water_root=high_water,
        monotonic_authority=authority,
        authenticator=authenticator or _authenticator(),
    )


def _publisher(
    tmp_path: Path,
) -> tuple[FilesystemCAS, FilesystemRevocationSnapshotPublisher]:
    cas = FilesystemCAS(tmp_path / "cas")
    pointer, high_water, lock_fd = _preprovision(tmp_path)
    os.close(lock_fd)
    publisher = _open_publisher(cas, pointer, high_water)
    return cas, publisher


def test_publication_is_exact_idempotent_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    cas, publisher = _publisher(tmp_path)
    initial = _request(
        "rollback-1:revoke",
        _binding("2", epoch=7),
        expected_generation=None,
        expected_epoch=None,
    )
    first = publisher.publish(initial)
    assert publisher.publish(initial) == first
    assert first.generation == 1
    assert first.previous_snapshot_ref is None
    assert first.request_digest == initial.canonical_digest()
    assert first.snapshot_ref.sha256 == "sha256:" + hashlib.sha256(
        publisher.snapshot_bytes(first)
    ).hexdigest()

    replacement = _request(
        "rollback-2:revoke",
        _binding("3", epoch=8),
        expected_generation=1,
        expected_epoch=7,
    )
    second = publisher.publish(replacement)
    assert second.generation == 2
    assert second.previous_snapshot_ref == first.snapshot_ref
    assert second.snapshot_ref != first.snapshot_ref
    assert publisher.load(_d("1")) == replacement.binding

    operation_name = hashlib.sha256(replacement.operation_id.encode()).hexdigest()
    (tmp_path / "revocation-pointer" / "operations" / f"{operation_name}.json").unlink()
    publisher.close()

    restarted = _open_publisher(
        cas,
        tmp_path / "revocation-pointer",
        tmp_path / "revocation-high-water",
    )
    try:
        assert restarted.publish(replacement) == second
        assert restarted.load(_d("1")) == replacement.binding
        assert restarted.validate_receipt(first) == initial.binding
        assert restarted.validate_receipt(second) == replacement.binding
        assert restarted.snapshot_bytes(second) == (
            b'[{"epoch":8,"scope_digest":"'
            + _d("1").encode()
            + b'","state_digest":"'
            + _d("3").encode()
            + b'"}]'
        )
    finally:
        restarted.close()
        cas.close()


def test_old_admission_receipt_is_denied_after_publication(tmp_path: Path) -> None:
    fixture = _admission_fixture()
    old_receipt = fixture.runtime.admit(fixture.request)
    cas, publisher = _publisher(tmp_path)
    try:
        initial = _request(
            "bootstrap-revocation",
            fixture.policy.revocation,
            expected_generation=None,
            expected_epoch=None,
        )
        publisher.publish(initial)
        publisher.publish(
            _request(
                "rollback-revocation",
                c.RevocationBinding(
                    scope_digest=fixture.policy.revocation.scope_digest,
                    epoch=fixture.policy.revocation.epoch + 1,
                    state_digest=_d("f"),
                ),
                expected_generation=1,
                expected_epoch=fixture.policy.revocation.epoch,
                predecessor=_trusted_f3(fixture.policy.revocation),
            )
        )
        effects = PrivilegedEffectProbe()
        runtime = _runtime_like(fixture, revocations=publisher)
        _denial(
            lambda: runtime.verify_receipt(
                old_receipt,
                subject=fixture.request.subject,
                checkpoint=c.PrivilegedCheckpoint.BEFORE_ALLOCATION,
            ),
            stage="receipt_recheck",
            code="receipt_revoked",
            pointer="/revocation/epoch",
            effects=effects,
        )
    finally:
        publisher.close()
        cas.close()


def test_generation_epoch_scope_and_operation_reuse_conflicts(tmp_path: Path) -> None:
    cas, publisher = _publisher(tmp_path)
    try:
        initial = _request(
            "operation-1",
            _binding("2", epoch=7),
            expected_generation=None,
            expected_epoch=None,
        )
        publisher.publish(initial)

        with pytest.raises(RevocationPublicationConflictError, match="generation"):
            publisher.publish(
                _request(
                    "stale-generation",
                    _binding("3", epoch=8),
                    expected_generation=2,
                    expected_epoch=7,
                    predecessor=_trusted_f3(_binding("2", epoch=7)),
                )
            )
        with pytest.raises(RevocationPublicationConflictError, match="epoch"):
            publisher.publish(
                _request(
                    "stale-epoch",
                    _binding("3", epoch=8),
                    expected_generation=1,
                    expected_epoch=6,
                )
            )
        for operation_id, epoch in (("epoch-rollback", 7), ("epoch-skip", 9)):
            with pytest.raises(
                RevocationPublicationConflictError, match="advance exactly"
            ):
                publisher.publish(
                    _request(
                        operation_id,
                        _binding("3", epoch=epoch),
                        expected_generation=1,
                        expected_epoch=7,
                    )
                )
        with pytest.raises(ValueError, match="scope drift"):
            RevocationSnapshotPublishRequest(
                operation_id="scope-drift",
                scope_digest=_d("1"),
                expected_generation=1,
                expected_epoch=7,
                binding=c.RevocationBinding(
                    scope_digest=_d("0"), epoch=8, state_digest=_d("3")
                ),
            )
        with pytest.raises(RevocationPublicationConflictError):
            publisher.publish(
                _request(
                    initial.operation_id,
                    _binding("3", epoch=8),
                    expected_generation=1,
                    expected_epoch=7,
                )
            )
    finally:
        publisher.close()
        cas.close()


def test_two_filesystem_publishers_have_one_generation_cas_winner(
    tmp_path: Path,
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    pointer, high_water, lock_fd = _preprovision(tmp_path)
    os.close(lock_fd)
    first = _open_publisher(cas, pointer, high_water)
    second = _open_publisher(cas, pointer, high_water)
    try:
        first.publish(
            _request(
                "initial",
                _binding("2", epoch=7),
                expected_generation=None,
                expected_epoch=None,
            )
        )
        requests = (
            _request(
                "writer-a",
                _binding("3", epoch=8),
                expected_generation=1,
                expected_epoch=7,
            ),
            _request(
                "writer-b",
                _binding("4", epoch=8),
                expected_generation=1,
                expected_epoch=7,
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(first.publish, requests[0]),
                executor.submit(second.publish, requests[1]),
            )
            outcomes: list[object] = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except RevocationPublicationConflictError as exc:
                    outcomes.append(exc)
        receipts = [item for item in outcomes if not isinstance(item, Exception)]
        conflicts = [item for item in outcomes if isinstance(item, Exception)]
        assert len(receipts) == len(conflicts) == 1
        assert receipts[0].generation == 2
        assert first.load(_d("1")).epoch == 8
        assert second.load(_d("1")) == first.load(_d("1"))
    finally:
        second.close()
        first.close()
        cas.close()


def test_valid_old_pointer_replacement_and_unsigned_pointer_are_rejected(
    tmp_path: Path,
) -> None:
    cas, publisher = _publisher(tmp_path)
    active_path = tmp_path / "revocation-pointer" / "active.json"
    try:
        publisher.publish(
            _request(
                "initial",
                _binding("2", epoch=7),
                expected_generation=None,
                expected_epoch=None,
            )
        )
        old_pointer = active_path.read_bytes()
        publisher.publish(
            _request(
                "replacement",
                _binding("3", epoch=8),
                expected_generation=1,
                expected_epoch=7,
            )
        )
        active_path.write_bytes(old_pointer)
        with pytest.raises(
            RevocationPublicationIntegrityError, match="high-water|replaced"
        ):
            publisher.load(_d("1"))

        active_path.write_bytes(b"{}")
        with pytest.raises(RevocationPublicationIntegrityError, match="schema"):
            publisher.load(_d("1"))
    finally:
        publisher.close()
        cas.close()


def test_tampered_immutable_snapshot_is_rejected_without_fallback(
    tmp_path: Path,
) -> None:
    cas, publisher = _publisher(tmp_path)
    try:
        receipt = publisher.publish(
            _request(
                "initial",
                _binding("2", epoch=7),
                expected_generation=None,
                expected_epoch=None,
            )
        )
        with pytest.raises(ValueError, match="not published"):
            publisher.load(_d("0"))
        blob = tmp_path / "cas" / "blobs" / receipt.snapshot_ref.sha256.removeprefix(
            "sha256:"
        )
        payload = blob.read_bytes()
        blob.write_bytes(bytes((payload[0] ^ 1,)) + payload[1:])
        with pytest.raises(ArtifactIntegrityError):
            publisher.load(_d("1"))
    finally:
        publisher.close()
        cas.close()



def test_authoring_hook_preserves_frozen_f3_validation_and_exact_ref(
    tmp_path: Path,
) -> None:
    f3 = f3_authority_spec(tmp_path)
    cas, publisher = _publisher(tmp_path)
    try:
        initial = publisher.publish(
            _request(
                "initial",
                f3.policy.revocation,
                expected_generation=None,
                expected_epoch=None,
            )
        )
        replacement_binding = c.RevocationBinding(
            scope_digest=f3.policy.revocation.scope_digest,
            epoch=f3.policy.revocation.epoch + 1,
            state_digest=_d("f"),
        )
        replacement = publisher.publish(
            _request(
                "replacement",
                replacement_binding,
                expected_generation=initial.generation,
                expected_epoch=f3.policy.revocation.epoch,
                predecessor=f3,
            )
        )

        rebound_f3 = publisher.bind_authoring_input(f3, replacement)
        assert type(rebound_f3) is type(f3)
        assert rebound_f3.policy.revocation == replacement_binding
        assert rebound_f3.model_dump(mode="json", exclude={"policy"}) == f3.model_dump(
            mode="json", exclude={"policy"}
        )


        snapshot = publisher.snapshot_bytes(replacement)
        assert replacement.snapshot_ref.sha256 == "sha256:" + hashlib.sha256(
            snapshot
        ).hexdigest()
        assert snapshot == (
            b'[{"epoch":'
            + str(replacement_binding.epoch).encode()
            + b',"scope_digest":"'
            + replacement_binding.scope_digest.encode()
            + b'","state_digest":"'
            + replacement_binding.state_digest.encode()
            + b'"}]'
        )
    finally:
        publisher.close()
        cas.close()

def test_true_process_writers_have_one_generation_cas_winner(tmp_path: Path) -> None:
    cas, publisher = _publisher(tmp_path)
    publisher.publish(
        _request(
            "process-initial",
            _binding("2", epoch=7),
            expected_generation=None,
            expected_epoch=None,
        )
    )
    publisher.close()
    cas.close()
    requests = (
        _request("process-a", _binding("3", epoch=8), expected_generation=1, expected_epoch=7),
        _request("process-b", _binding("4", epoch=8), expected_generation=1, expected_epoch=7),
    )
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=_process_publish,
            args=(
                os.fspath(tmp_path / "cas"),
                os.fspath(tmp_path / "revocation-pointer"),
                os.fspath(tmp_path / "revocation-high-water"),
                request.model_dump(mode="python"),
                barrier,
                queue,
            ),
        )
        for request in requests
    ]
    for process in processes:
        process.start()
    outcomes = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "published"]
    assert [outcome[1] for outcome in outcomes if outcome[0] == "published"] == [2]


@pytest.mark.parametrize("mutation", ["truncate", "delete", "swap"])
def test_full_historical_chain_tamper_blocks_all_read_apis(
    tmp_path: Path, mutation: str
) -> None:
    cas, publisher = _publisher(tmp_path)
    try:
        receipts = [
            publisher.publish(
                _request("chain-1", _binding("2", epoch=7), expected_generation=None, expected_epoch=None)
            )
        ]
        for generation, character in ((2, "3"), (3, "4")):
            receipts.append(
                publisher.publish(
                    _request(
                        f"chain-{generation}",
                        _binding(character, epoch=6 + generation),
                        expected_generation=generation - 1,
                        expected_epoch=5 + generation,
                    )
                )
            )
        history = tmp_path / "revocation-pointer" / "history" / "1.json"
        if mutation == "truncate":
            history.write_bytes(history.read_bytes()[:32])
        elif mutation == "delete":
            history.unlink()
        else:
            history.write_bytes(
                (tmp_path / "revocation-pointer" / "history" / "2.json").read_bytes()
            )
        for action in (
            lambda: publisher.load(_d("1")),
            lambda: publisher.validate_receipt(receipts[-1]),
            lambda: publisher.snapshot_bytes(receipts[-1]),
        ):
            with pytest.raises(RevocationPublicationIntegrityError):
                action()
    finally:
        publisher.close()
        cas.close()


def test_root_alias_lock_replacement_and_wrong_key_fail_closed(tmp_path: Path) -> None:
    real_root = tmp_path / "real-pointer"
    real_root.mkdir()
    alias = tmp_path / "alias-pointer"
    alias.symlink_to(real_root, target_is_directory=True)
    cas = FilesystemCAS(tmp_path / "cas")
    with pytest.raises(RevocationPublicationIntegrityError, match="non-aliased"):
        _open_publisher(cas, alias, tmp_path / "alias-high-water")
    (real_root / "history").mkdir()
    (real_root / "operations").mkdir()
    real_high_water = tmp_path / "real-high-water"
    real_high_water.mkdir()
    publisher = _open_publisher(cas, real_root, real_high_water)
    publisher.publish(
        _request("root-initial", _binding("2", epoch=7), expected_generation=None, expected_epoch=None)
    )
    publisher.close()
    foreign = _open_publisher(
        cas,
        real_root,
        real_high_water,
        authenticator=HmacSha256ReceiptAuthenticator(
            key_id="foreign-key",
            key=b"foreign-revocation-key-material-at-least-32-bytes",
        ),
    )
    try:
        with pytest.raises(RevocationPublicationIntegrityError):
            foreign.load(_d("1"))
    finally:
        foreign.close()
        cas.close()


def test_partial_write_and_directory_fsync_failures_are_retry_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas, publisher = _publisher(tmp_path)
    initial = publisher.publish(
        _request("fault-initial", _binding("2", epoch=7), expected_generation=None, expected_epoch=None)
    )
    replacement = _request(
        "fault-replacement", _binding("3", epoch=8), expected_generation=initial.generation, expected_epoch=7
    )
    original_write = publisher._write_file
    failed_once = False
    def partial_write(directory_fd: int, name: str, payload: bytes) -> None:
        nonlocal failed_once
        if not failed_once and name.startswith(".immutable."):
            failed_once = True
            original_write(directory_fd, name, payload[: len(payload) // 2])
            raise OSError("injected partial write")
        original_write(directory_fd, name, payload)
    monkeypatch.setattr(publisher, "_write_file", partial_write)
    with pytest.raises(OSError, match="partial"):
        publisher.publish(replacement)
    monkeypatch.setattr(publisher, "_write_file", original_write)
    original_fsync = os.fsync
    fsync_failed = False

    def fail_history_fsync(fd: int) -> None:
        nonlocal fsync_failed
        if not fsync_failed and fd == publisher._history_fd:
            fsync_failed = True
            raise OSError("injected history directory fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_history_fsync)
    with pytest.raises(OSError, match="fsync"):
        publisher.publish(replacement)
    monkeypatch.setattr(os, "fsync", original_fsync)
    receipt = publisher.publish(replacement)
    assert receipt.generation == 2
    assert publisher.load(_d("1")) == replacement.binding
    publisher.close()
    cas.close()


def test_authoring_rebind_rejects_predecessor_state_and_config_drift(
    tmp_path: Path,
) -> None:
    f3 = f3_authority_spec(tmp_path)
    cas, publisher = _publisher(tmp_path)
    try:
        initial = publisher.publish(
            _request("authoring-initial", f3.policy.revocation, expected_generation=None, expected_epoch=None)
        )
        replacement = publisher.publish(
            _request(
                "authoring-replacement",
                c.RevocationBinding(
                    scope_digest=f3.policy.revocation.scope_digest,
                    epoch=f3.policy.revocation.epoch + 1,
                    state_digest=_d("f"),
                ),
                expected_generation=initial.generation,
                expected_epoch=f3.policy.revocation.epoch,
                predecessor=f3,
            )
        )
        drift_binding = c.RevocationBinding(
            scope_digest=f3.policy.revocation.scope_digest,
            epoch=f3.policy.revocation.epoch,
            state_digest=_d("0"),
        )
        state_drift = f3.model_copy(
            update={"policy": f3.policy.model_copy(update={"revocation": drift_binding})}
        )
        with pytest.raises(RevocationPublicationConflictError, match="predecessor identity"):
            publisher.bind_authoring_input(state_drift, replacement)
        config_drift = f3.model_copy(update={"attempt_id": "drifted-attempt"})
        with pytest.raises(RevocationPublicationConflictError, match="predecessor identity"):
            publisher.bind_authoring_input(config_drift, replacement)
        with pytest.raises(RevocationPublicationConflictError):
            publisher.bind_authoring_input(f3, initial)
    finally:
        publisher.close()
        cas.close()

def test_deleting_all_mutable_witnesses_is_fail_closed_nonclaim(
    tmp_path: Path,
) -> None:
    cas, publisher = _publisher(tmp_path)
    receipt = publisher.publish(
        _request(
            "deletion-initial",
            _binding("2", epoch=7),
            expected_generation=None,
            expected_epoch=None,
        )
    )
    root = tmp_path / "revocation-pointer"
    for path in tuple((root / "history").iterdir()):
        path.unlink()
    for path in tuple((root / "operations").iterdir()):
        path.unlink()
    for path in tuple(root.glob("pointer-*.json")):
        path.unlink()
    (root / "active.json").unlink()
    with pytest.raises(RevocationPublicationIntegrityError):
        publisher.load(_d("1"))
    for action in (
        lambda: publisher.validate_receipt(receipt),
        lambda: publisher.snapshot_bytes(receipt),
    ):
        with pytest.raises(RevocationPublicationIntegrityError):
            action()
    publisher.close()
    cas.close()


def test_unexpected_future_pointer_and_operation_records_are_rejected(
    tmp_path: Path,
) -> None:
    cas, publisher = _publisher(tmp_path)
    publisher.publish(
        _request(
            "unexpected-initial",
            _binding("2", epoch=7),
            expected_generation=None,
            expected_epoch=None,
        )
    )
    root = tmp_path / "revocation-pointer"
    (root / "pointer-9.json").write_bytes((root / "pointer-1.json").read_bytes())
    with pytest.raises(RevocationPublicationIntegrityError, match="high-water"):
        publisher.load(_d("1"))
    (root / "pointer-9.json").unlink()
    operation = next((root / "operations").iterdir())
    (root / "operations" / ("0" * 64 + ".json")).write_bytes(operation.read_bytes())
    with pytest.raises(RevocationPublicationIntegrityError, match="unexpected"):
        publisher.load(_d("1"))
    publisher.close()
    cas.close()

def test_external_high_water_rejects_complete_newest_tuple_rollback(
    tmp_path: Path,
) -> None:
    cas, publisher = _publisher(tmp_path)
    requests = [
        _request("water-1", _binding("2", epoch=7), expected_generation=None, expected_epoch=None),
        _request("water-2", _binding("3", epoch=8), expected_generation=1, expected_epoch=7),
        _request("water-3", _binding("4", epoch=9), expected_generation=2, expected_epoch=8),
    ]
    receipts = [publisher.publish(request) for request in requests]
    root = tmp_path / "revocation-pointer"
    pointer_two = (root / "pointer-2.json").read_bytes()
    (root / "history" / "3.json").unlink()
    (root / "pointer-3.json").unlink()
    operation_three = hashlib.sha256(b"water-3").hexdigest() + ".json"
    (root / "operations" / operation_three).unlink()
    (root / "active.json").write_bytes(pointer_two)
    publisher.close()
    restarted = _open_publisher(
        cas,
        root,
        tmp_path / "revocation-high-water",
    )
    try:
        for action in (
            lambda: restarted.load(_d("1")),
            lambda: restarted.validate_receipt(receipts[1]),
            lambda: restarted.snapshot_bytes(receipts[1]),
            lambda: restarted.publish(requests[2]),
        ):
            with pytest.raises(RevocationPublicationIntegrityError):
                action()
    finally:
        restarted.close()
        cas.close()


def test_missing_active_operation_allows_only_exact_repair(tmp_path: Path) -> None:
    cas, publisher = _publisher(tmp_path)
    initial = _request(
        "missing-operation-1",
        _binding("2", epoch=7),
        expected_generation=None,
        expected_epoch=None,
    )
    receipt = publisher.publish(initial)
    operation = hashlib.sha256(initial.operation_id.encode()).hexdigest() + ".json"
    (tmp_path / "revocation-pointer" / "operations" / operation).unlink()
    successor = _request(
        "missing-operation-2",
        _binding("3", epoch=8),
        expected_generation=1,
        expected_epoch=7,
    )
    with pytest.raises(RevocationPublicationConflictError, match="exact incomplete"):
        publisher.publish(successor)
    assert publisher.publish(initial) == receipt
    assert publisher.load(_d("1")) == initial.binding
    publisher.close()
    cas.close()

def test_pointer_directory_fsync_failure_recovers_exactly_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas, publisher = _publisher(tmp_path)
    initial = publisher.publish(
        _request("pointer-fsync-1", _binding("2", epoch=7), expected_generation=None, expected_epoch=None)
    )
    replacement = _request(
        "pointer-fsync-2",
        _binding("3", epoch=8),
        expected_generation=1,
        expected_epoch=7,
    )
    original_fsync = os.fsync
    failed = False
    pointer_two = tmp_path / "revocation-pointer" / "pointer-2.json"
    active = tmp_path / "revocation-pointer" / "active.json"
    active_one = active.read_bytes()

    def fail_pointer_directory(fd: int) -> None:
        nonlocal failed
        if (
            not failed
            and fd == publisher._root_fd
            and pointer_two.exists()
            and active.read_bytes() == active_one
        ):
            failed = True
            raise OSError("injected pointer directory fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_pointer_directory)
    with pytest.raises(OSError, match="pointer directory"):
        publisher.publish(replacement)
    monkeypatch.setattr(os, "fsync", original_fsync)
    publisher.close()
    restarted = _open_publisher(
        cas,
        tmp_path / "revocation-pointer",
        tmp_path / "revocation-high-water",
    )
    try:
        receipt = restarted.publish(replacement)
        assert receipt.generation == initial.generation + 1
        assert restarted.load(_d("1")) == replacement.binding
    finally:
        restarted.close()
        cas.close()

def test_authoring_hook_binds_real_f2_dynamic_and_rejects_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f2_root = tmp_path / "f2-real"
    f2_root.mkdir()
    source, _paths = real_f2_authoring_value(f2_root, monkeypatch)
    packet = json.loads(author_real_f2_packet(f2_root, monkeypatch, source).read_bytes())
    plan = F2C4TargetDynamicPlanInput.model_validate(packet["plan"], strict=True)
    observations = F2C4TargetDynamicObservations.model_validate_json(
        json.dumps(
            packet["observations"],
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        strict=True,
    )
    f2 = author_f2_target_dynamic_authority(plan, observations)
    assert type(f2) is F2C4DynamicAuthorityInput

    cas, publisher = _publisher(tmp_path)
    initial_binding = f2.revocation
    replacement_binding = c.RevocationBinding(
        scope_digest=initial_binding.scope_digest,
        epoch=initial_binding.epoch + 1,
        state_digest=_d("e"),
    )
    try:
        initial = publisher.publish(
            _request(
                "real-f2-initial",
                initial_binding,
                expected_generation=None,
                expected_epoch=None,
            )
        )
        replacement = publisher.publish(
            _request(
                "real-f2-replacement",
                replacement_binding,
                expected_generation=initial.generation,
                expected_epoch=initial_binding.epoch,
                predecessor=f2,
            )
        )
        rebound = publisher.bind_authoring_input(f2, replacement)
        assert type(rebound) is F2C4DynamicAuthorityInput
        assert rebound.revocation == replacement_binding
        with pytest.raises(TypeError, match="trusted F2/F3 authority"):
            publisher.bind_authoring_input(
                {"schema_version": "bb.rl.phase5-f2-c4-semantic-input.v1"},
                replacement,
            )
        drift = f2.model_copy(
            update={
                "server_request_timeout_seconds":
                f2.server_request_timeout_seconds + 1.0
            }
        )
        with pytest.raises(
            RevocationPublicationConflictError, match="predecessor identity"
        ):
            publisher.bind_authoring_input(drift, replacement)
        f3_root = tmp_path / "f3-cross-type"
        f3_root.mkdir()
        with pytest.raises(
            RevocationPublicationConflictError, match="predecessor identity"
        ):
            publisher.bind_authoring_input(f3_authority_spec(f3_root), replacement)
    finally:
        publisher.close()
        cas.close()

def _cleanup_fixture(
    tmp_path: Path,
) -> tuple[
    FilesystemRevocationSnapshotPublisher,
    dict[str, int],
    dict[str, Path],
]:
    pointer, high_water, lock_fd = _preprovision(tmp_path)
    paths = {
        "root": pointer,
        "history": pointer / "history",
        "operations": pointer / "operations",
        "high-water": high_water,
    }
    directories = {
        "root": os.open(pointer, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
        "history": os.open(
            pointer / "history", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        ),
        "operations": os.open(
            pointer / "operations", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        ),
        "high-water": os.open(
            high_water, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        ),
    }
    publisher = object.__new__(FilesystemRevocationSnapshotPublisher)
    publisher._root_fd = directories["root"]
    publisher._history_fd = directories["history"]
    publisher._operations_fd = directories["operations"]
    publisher._high_water_fd = directories["high-water"]
    publisher._lock_fd = lock_fd
    publisher._validate_directories = lambda: None
    return publisher, directories, paths


def _close_cleanup_fixture(
    publisher: FilesystemRevocationSnapshotPublisher,
    directories: dict[str, int],
) -> None:
    fcntl.flock(publisher._lock_fd, fcntl.LOCK_UN)
    os.close(publisher._lock_fd)
    for descriptor in directories.values():
        os.close(descriptor)


def _stat_with_uid(value: os.stat_result, uid: int) -> os.stat_result:
    fields = list(value)
    fields[4] = uid
    return os.stat_result(fields)


@pytest.mark.parametrize(
    ("directory_name", "temporary_name"),
    [
        ("root", f".immutable.{'1' * 32}.tmp"),
        ("history", f".immutable.{'2' * 32}.tmp"),
        ("operations", f".immutable.{'3' * 32}.tmp"),
        ("high-water", f".immutable.{'4' * 32}.tmp"),
        ("root", f".active.{'5' * 32}.tmp"),
        ("root", f".active.{'6' * 32}.rollback"),
    ],
)
def test_exclusive_recovery_removes_only_canonical_root_owned_orphans_and_fsyncs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_name: str,
    temporary_name: str,
) -> None:
    publisher, directories, paths = _cleanup_fixture(tmp_path)
    directory = paths[directory_name]
    temporary = directory / temporary_name
    temporary.write_bytes(b"crash remnant")
    temporary.chmod(0o600)
    identity = temporary.stat()
    original_fstat = os.fstat
    original_fsync = os.fsync
    fsynced: list[int] = []

    def root_owned_fstat(fd: int) -> os.stat_result:
        observed = original_fstat(fd)
        if (observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino):
            return _stat_with_uid(observed, 0)
        return observed

    def record_fsync(fd: int) -> None:
        fsynced.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(os, "fstat", root_owned_fstat)
    monkeypatch.setattr(os, "fsync", record_fsync)
    try:
        publisher._flock_verified(fcntl.LOCK_EX)
        assert not temporary.exists()
        assert directories[directory_name] in fsynced
    finally:
        _close_cleanup_fixture(publisher, directories)


@pytest.mark.parametrize("directory_name", ["root", "history", "operations", "high-water"])
@pytest.mark.parametrize("attack", ["directory", "symlink", "mode", "owner", "link"])
def test_reserved_temporary_metadata_attacks_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_name: str,
    attack: str,
) -> None:
    publisher, directories, paths = _cleanup_fixture(tmp_path)
    directory = paths[directory_name]
    temporary = directory / f".immutable.{'7' * 32}.tmp"
    if attack == "directory":
        temporary.mkdir()
    elif attack == "symlink":
        target = tmp_path / "symlink-target"
        target.write_bytes(b"target")
        temporary.symlink_to(target)
    else:
        temporary.write_bytes(b"reserved")
        temporary.chmod(0o640 if attack == "mode" else 0o600)
        if attack == "link":
            os.link(temporary, directory / "second-link")
    identity = temporary.lstat()
    original_fstat = os.fstat

    def attacked_fstat(fd: int) -> os.stat_result:
        observed = original_fstat(fd)
        if (observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino):
            return _stat_with_uid(observed, 1 if attack == "owner" else 0)
        return observed

    monkeypatch.setattr(os, "fstat", attacked_fstat)
    try:
        with pytest.raises(RevocationPublicationIntegrityError):
            publisher._flock_verified(fcntl.LOCK_EX)
        assert os.path.lexists(temporary)
    finally:
        _close_cleanup_fixture(publisher, directories)


@pytest.mark.parametrize(
    ("directory_name", "temporary_name"),
    [
        ("root", ".immutable.not-a-uuid.tmp"),
        ("root", f".active.{'8' * 32}.partial"),
        ("history", f".active.{'9' * 32}.tmp"),
        ("operations", f".active.{'a' * 32}.rollback"),
        ("high-water", ".active.bad.tmp"),
    ],
)
def test_malformed_or_misplaced_reserved_temporary_names_fail_closed(
    tmp_path: Path,
    directory_name: str,
    temporary_name: str,
) -> None:
    publisher, directories, paths = _cleanup_fixture(tmp_path)
    temporary = paths[directory_name] / temporary_name
    temporary.write_bytes(b"malformed reserved remnant")
    temporary.chmod(0o600)
    try:
        with pytest.raises(
            RevocationPublicationIntegrityError, match="name is not canonical"
        ):
            publisher._flock_verified(fcntl.LOCK_EX)
        assert temporary.exists()
    finally:
        _close_cleanup_fixture(publisher, directories)


def test_mixed_valid_and_malformed_orphans_fail_before_unlink_or_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, directories, paths = _cleanup_fixture(tmp_path)
    valid_name = f".active.{'b' * 32}.tmp"
    malformed_name = ".immutable.malformed.tmp"
    valid = paths["root"] / valid_name
    malformed = paths["history"] / malformed_name
    valid.write_bytes(b"valid crash remnant")
    malformed.write_bytes(b"malformed crash remnant")
    valid.chmod(0o600)
    malformed.chmod(0o600)
    identity = valid.stat()
    original_fstat = os.fstat
    original_listdir = os.listdir
    original_fsync = os.fsync
    fsynced: list[int] = []
    publication_advanced = False

    def root_owned_fstat(fd: int) -> os.stat_result:
        observed = original_fstat(fd)
        if (observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino):
            return _stat_with_uid(observed, 0)
        return observed

    def ordered_listdir(path: int | str = ".") -> list[str]:
        if path == directories["root"]:
            return [valid_name]
        if path == directories["history"]:
            return [malformed_name]
        return original_listdir(path)

    def record_fsync(fd: int) -> None:
        fsynced.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(os, "fstat", root_owned_fstat)
    monkeypatch.setattr(os, "listdir", ordered_listdir)
    monkeypatch.setattr(os, "fsync", record_fsync)
    try:
        with pytest.raises(
            RevocationPublicationIntegrityError, match="name is not canonical"
        ):
            publisher._flock_verified(fcntl.LOCK_EX)
            publication_advanced = True
        assert valid.exists()
        assert malformed.exists()
        assert not publication_advanced
        assert not fsynced
    finally:
        _close_cleanup_fixture(publisher, directories)


def test_successful_multi_orphan_cleanup_fsyncs_after_all_unlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, directories, paths = _cleanup_fixture(tmp_path)
    candidates = [
        ("root", f".active.{'c' * 32}.rollback"),
        ("history", f".immutable.{'d' * 32}.tmp"),
    ]
    identities: set[tuple[int, int]] = set()
    for directory_name, name in candidates:
        candidate = paths[directory_name] / name
        candidate.write_bytes(b"verified crash remnant")
        candidate.chmod(0o600)
        observed = candidate.stat()
        identities.add((observed.st_dev, observed.st_ino))
    original_fstat = os.fstat
    original_listdir = os.listdir
    original_unlink = os.unlink
    original_fsync = os.fsync
    events: list[tuple[str, object]] = []

    def root_owned_fstat(fd: int) -> os.stat_result:
        observed = original_fstat(fd)
        if (observed.st_dev, observed.st_ino) in identities:
            return _stat_with_uid(observed, 0)
        return observed

    def ordered_listdir(path: int | str = ".") -> list[str]:
        for directory_name, name in candidates:
            if path == directories[directory_name]:
                return [name]
        return original_listdir(path)

    def record_unlink(
        path: str,
        *,
        dir_fd: int | None = None,
    ) -> None:
        events.append(("unlink", path))
        original_unlink(path, dir_fd=dir_fd)

    def record_fsync(fd: int) -> None:
        events.append(("fsync", fd))
        original_fsync(fd)

    monkeypatch.setattr(os, "fstat", root_owned_fstat)
    monkeypatch.setattr(os, "listdir", ordered_listdir)
    monkeypatch.setattr(os, "unlink", record_unlink)
    monkeypatch.setattr(os, "fsync", record_fsync)
    try:
        publisher._flock_verified(fcntl.LOCK_EX)
        assert events == [
            ("unlink", candidates[0][1]),
            ("unlink", candidates[1][1]),
            ("fsync", directories["root"]),
            ("fsync", directories["history"]),
        ]
    finally:
        _close_cleanup_fixture(publisher, directories)


def test_shared_read_preserves_live_temp_and_exclusive_peer_recovers_it(
    tmp_path: Path,
) -> None:
    cas, publisher = _publisher(tmp_path)
    peer = _open_publisher(
        cas,
        tmp_path / "revocation-pointer",
        tmp_path / "revocation-high-water",
    )
    try:
        first = publisher.publish(
            _request(
                "temp-owner-initial",
                _binding("2", epoch=7),
                expected_generation=None,
                expected_epoch=None,
            )
        )
        temporary = (
            tmp_path
            / "revocation-pointer"
            / f".immutable.{'a' * 32}.tmp"
        )
        temporary_fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            os.write(temporary_fd, b"live peer publication")
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        assert publisher.load(_d("1")) == _binding("2", epoch=7)
        assert temporary.read_bytes() == b"live peer publication"
        fcntl.flock(publisher._lock_fd, fcntl.LOCK_EX)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                peer.publish,
                _request(
                    "temp-owner-next",
                    _binding("3", epoch=8),
                    expected_generation=first.generation,
                    expected_epoch=7,
                ),
            )
            with pytest.raises(TimeoutError):
                future.result(timeout=0.05)
            assert temporary.exists()
            fcntl.flock(publisher._lock_fd, fcntl.LOCK_UN)
            assert future.result(timeout=5).generation == 2
        assert not temporary.exists()
    finally:
        fcntl.flock(publisher._lock_fd, fcntl.LOCK_UN)
        peer.close()
        publisher.close()
        cas.close()


def test_post_flock_lock_replacement_rejects_and_releases_old_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas, publisher = _publisher(tmp_path)
    authority_lock_fd = publisher._monotonic_authority._lock_fd
    lock_path = publisher._monotonic_authority._root / "authority.lock"
    original_fstat = os.fstat
    original_flock = fcntl.flock
    inject_lock_identity = False

    def replace_lock_identity(fd: int) -> os.stat_result:
        nonlocal inject_lock_identity
        observed = original_fstat(fd)
        if inject_lock_identity and fd == authority_lock_fd:
            inject_lock_identity = False
            values = list(observed)
            values[1] += 1
            return os.stat_result(values)
        return observed

    def flock_then_inject(fd: int, operation: int) -> None:
        nonlocal inject_lock_identity
        original_flock(fd, operation)
        if fd == publisher._lock_fd and operation == fcntl.LOCK_SH:
            inject_lock_identity = True

    def acquire_independent_lock(path: str, outcomes: object) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                outcomes.put("blocked")
            else:
                outcomes.put("acquired")
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    monkeypatch.setattr(os, "fstat", replace_lock_identity)
    monkeypatch.setattr(fcntl, "flock", flock_then_inject)
    try:
        with pytest.raises(RevocationPublicationIntegrityError) as exc_info:
            publisher._flock_verified(fcntl.LOCK_SH)
        assert (
            str(exc_info.value)
            == "monotonic authority lock identity or kernel flags changed"
        )
        inject_lock_identity = False
        monkeypatch.setattr(os, "fstat", original_fstat)
        monkeypatch.setattr(fcntl, "flock", original_flock)
        context = multiprocessing.get_context("fork")
        outcomes = context.Queue()
        process = context.Process(
            target=acquire_independent_lock,
            args=(os.fspath(lock_path), outcomes),
        )
        process.start()
        try:
            outcome = outcomes.get(timeout=5)
            process.join(timeout=5)
            assert process.exitcode == 0
            assert outcome == "acquired"
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            outcomes.close()
            outcomes.join_thread()
    finally:
        publisher.close()
        cas.close()


class _FakeFchflags:
    argtypes: object = None
    restype: object = None

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[int, int]] = []

    def __call__(self, descriptor: int, flags: int) -> int:
        self.calls.append((descriptor, flags))
        return self.result


def test_darwin_kernel_authority_fchflags_abi_preserves_unrelated_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fchflags = _FakeFchflags()
    monkeypatch.setattr(stat, "SF_APPEND", 0x00040000, raising=False)
    monkeypatch.setattr(stat, "SF_IMMUTABLE", 0x00020000, raising=False)
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda _name, *, use_errno: type("_Libc", (), {"fchflags": fchflags})(),
    )
    backend = _DarwinKernelAuthorityFlags()
    monkeypatch.setattr(backend, "read", lambda descriptor: 0x40)
    backend.set_immutable(73)
    assert fchflags.argtypes == (ctypes.c_int, ctypes.c_uint)
    assert fchflags.restype is ctypes.c_int
    assert fchflags.calls == [(73, 0x40 | stat.SF_IMMUTABLE)]


def test_darwin_kernel_authority_fchflags_propagates_errno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fchflags = _FakeFchflags(result=-1)
    monkeypatch.setattr(stat, "SF_APPEND", 0x00040000, raising=False)
    monkeypatch.setattr(stat, "SF_IMMUTABLE", 0x00020000, raising=False)
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda _name, *, use_errno: type("_Libc", (), {"fchflags": fchflags})(),
    )
    monkeypatch.setattr(ctypes, "get_errno", lambda: 13)
    backend = _DarwinKernelAuthorityFlags()
    monkeypatch.setattr(backend, "read", lambda descriptor: 0x80)
    with pytest.raises(OSError) as error:
        backend.set_immutable(91)
    assert error.value.errno == 13
    assert fchflags.calls == [(91, 0x80 | stat.SF_IMMUTABLE)]


def test_darwin_privileged_flag_helper_uses_descriptor_abi_and_errno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fchflags = _FakeFchflags()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda _name, *, use_errno: type("_Libc", (), {"fchflags": fchflags})(),
    )
    _platform_write_file_flags(117, 0x00060040)
    assert fchflags.argtypes == (ctypes.c_int, ctypes.c_uint)
    assert fchflags.restype is ctypes.c_int
    assert fchflags.calls == [(117, 0x00060040)]

    fchflags.result = -1
    monkeypatch.setattr(ctypes, "get_errno", lambda: 22)
    with pytest.raises(OSError) as error:
        _platform_write_file_flags(118, 0x00020000)
    assert error.value.errno == 22
    assert fchflags.calls[-1] == (118, 0x00020000)


def test_linux_kernel_authority_uses_exact_getflags_setflags_ioctls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = os.open(tmp_path / "linux-flags", os.O_RDWR | os.O_CREAT, 0o600)
    observed = _FS_APPEND_FL
    calls: list[int] = []

    def ioctl(
        received_fd: int,
        operation: int,
        value: object,
        mutate: bool,
    ) -> int:
        nonlocal observed
        assert isinstance(value, array.array)
        assert received_fd == descriptor
        assert mutate is True
        calls.append(operation)
        if operation == _FS_IOC_GETFLAGS:
            value[0] = observed  # type: ignore[index]
        elif operation == _FS_IOC_SETFLAGS:
            observed = value[0]  # type: ignore[index]
        else:
            raise AssertionError(f"unexpected ioctl {operation}")
        return 0

    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    backend = _LinuxKernelAuthorityFlags()
    try:
        assert backend.read(descriptor) == _FS_APPEND_FL
        backend.set_immutable(descriptor)
        assert observed == _FS_APPEND_FL | _FS_IMMUTABLE_FL
        assert calls == [
            _FS_IOC_GETFLAGS,
            _FS_IOC_GETFLAGS,
            _FS_IOC_SETFLAGS,
        ]
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("effective_uid", "effective_capabilities", "accepted"),
    [
        (0, 0, False),
        (501, 1 << 9, True),
    ],
)
def test_linux_kernel_authority_dispatch_requires_effective_capability_bit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    effective_uid: int,
    effective_capabilities: int,
    accepted: bool,
) -> None:
    status = tmp_path / "status"
    status.write_bytes(f"Name:\tpytest\nCapEff:\t{effective_capabilities:016x}\n".encode())
    monkeypatch.setattr(
        "breadboard.rl.phase5.revocation_publication.sys.platform", "linux"
    )
    monkeypatch.setattr(
        "breadboard.rl.phase5.revocation_publication._PROC_STATUS_PATH",
        os.fspath(status),
    )
    monkeypatch.setattr(os, "geteuid", lambda: effective_uid)
    if not accepted:
        with pytest.raises(PermissionError, match="CAP_LINUX_IMMUTABLE"):
            _kernel_authority_flags()
    else:
        assert type(_kernel_authority_flags()) is _LinuxKernelAuthorityFlags


def test_production_publisher_rejects_structural_authority_and_external_lock(
    tmp_path: Path,
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    pointer, high_water, lock_fd = _preprovision(tmp_path)
    os.close(lock_fd)
    provider = _TestMonotonicAuthority(tmp_path / "ordinary-provider")
    with pytest.raises(TypeError, match="exact preprovisioned"):
        FilesystemRevocationSnapshotPublisher(
            cas,
            pointer,
            high_water_root=high_water,
            monotonic_authority=provider,  # type: ignore[arg-type]
            authenticator=_authenticator(),
        )
    with pytest.raises(TypeError, match="lock_fd"):
        FilesystemRevocationSnapshotPublisher(
            cas,
            pointer,
            high_water_root=high_water,
            lock_fd=-1,  # type: ignore[call-arg]
            monotonic_authority=provider,  # type: ignore[arg-type]
            authenticator=_authenticator(),
        )
    cas.close()


def test_monotonic_witness_requires_exact_pinned_authority_identity() -> None:
    identity = MonotonicRevocationAuthorityIdentity(
        schema_version="bb.rl.monotonic-revocation-authority-identity.v1",
        authority_id=_d("a"),
        root_device=1,
        root_inode=2,
        root_uid=0,
        root_gid=0,
        root_flags=4,
        config_device=1,
        config_inode=4,
        config_uid=0,
        config_gid=0,
        config_flags=8,
        lock_device=1,
        lock_inode=3,
        lock_uid=0,
        lock_gid=0,
        lock_flags=8,
        config_digest=_d("b"),
    )
    witness = MonotonicRevocationWitness(
        monotonic_authority=identity,
        generation=10,
        record_digest=_d("c"),
    )
    assert MonotonicRevocationWitness.model_validate_json(
        witness.canonical_bytes(), strict=True
    ) == witness
    document = witness.model_dump(mode="json")
    document.pop("monotonic_authority")
    with pytest.raises(ValueError):
        MonotonicRevocationWitness.model_validate(document, strict=True)


def test_privileged_concrete_authority_commits_1_through_11_and_restarts() -> None:
    authority_variable = (
        "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_GEN11_ROOT"
        if sys.platform == "linux"
        else "BREADBOARD_PRIVILEGED_REVOCATION_AUTHORITY_GEN11_ROOT"
    )
    configured = os.environ.get(authority_variable)
    if configured is None:
        pytest.skip("requires a fresh privileged append-only authority root")
    root = Path(configured)
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    identity = authority.identity
    expected = None
    try:
        for generation in range(1, 11):
            successor = MonotonicRevocationWitness(
                monotonic_authority=identity,
                generation=generation,
                record_digest="sha256:" + f"{generation:064x}",
            )
            assert authority.compare_and_append(expected, successor) == successor
            expected = successor
        assert authority.latest() == expected
    finally:
        authority.close()
    incomplete_fd = os.open(
        root / "11.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o400,
    )
    os.close(incomplete_fd)
    restarted = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    try:
        assert restarted.identity == identity
        assert restarted.latest() == expected
        successor = MonotonicRevocationWitness(
            monotonic_authority=identity,
            generation=11,
            record_digest="sha256:" + f"{11:064x}",
        )
        assert restarted.compare_and_append(expected, successor) == successor
        assert restarted.latest() == successor
    finally:
        restarted.close()


def test_privileged_concrete_authority_publishes_and_recovers_exact_receipt(
    tmp_path: Path,
) -> None:
    authority_variable = (
        "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_PUBLISH_ROOT"
        if sys.platform == "linux"
        else "BREADBOARD_PRIVILEGED_REVOCATION_AUTHORITY_PUBLISH_ROOT"
    )
    configured = os.environ.get(authority_variable)
    if configured is None:
        pytest.skip("requires a fresh privileged publisher authority root")
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(
        Path(configured)
    )
    cas = FilesystemCAS(tmp_path / "cas")
    pointer = tmp_path / "revocation-pointer"
    pointer.mkdir()
    (pointer / "history").mkdir()
    (pointer / "operations").mkdir()
    high_water = tmp_path / "revocation-high-water"
    high_water.mkdir()
    publisher = FilesystemRevocationSnapshotPublisher(
        cas,
        pointer,
        high_water_root=high_water,
        monotonic_authority=authority,
        authenticator=_authenticator(),
    )
    request = _request(
        "privileged-authority-publication-1",
        _binding("2", epoch=7),
        expected_generation=None,
        expected_epoch=None,
    )
    receipt = publisher.publish(request)
    for generation in range(2, 11):
        previous_binding = request.binding
        request = _request(
            f"privileged-authority-publication-{generation}",
            _binding(format(generation + 1, "x"), epoch=generation + 6),
            expected_generation=generation - 1,
            expected_epoch=previous_binding.epoch,
            predecessor=_trusted_f3(previous_binding),
        )
        receipt = publisher.publish(request)
    assert receipt.generation == 10
    assert receipt.monotonic_authority == authority.identity
    assert publisher.load(request.scope_digest) == request.binding
    publisher.close()
    restarted = FilesystemRevocationSnapshotPublisher(
        cas,
        pointer,
        high_water_root=high_water,
        monotonic_authority=authority,
        authenticator=_authenticator(),
    )
    contender = FilesystemRevocationSnapshotPublisher(
        cas,
        pointer,
        high_water_root=high_water,
        monotonic_authority=authority,
        authenticator=_authenticator(),
    )
    try:
        assert authority.latest() is not None
        assert authority.latest().generation == 10
        assert restarted.validate_receipt(receipt) == request.binding
        generation_11 = _request(
            "privileged-authority-publication-11",
            _binding("c", epoch=17),
            expected_generation=10,
            expected_epoch=request.binding.epoch,
            predecessor=_trusted_f3(request.binding),
        )
        receipt_11 = restarted.publish(generation_11)
        assert receipt_11.generation == 11
        assert restarted.validate_receipt(receipt_11) == generation_11.binding
        race_requests = (
            _request(
                "privileged-authority-writer-a",
                _binding("d", epoch=18),
                expected_generation=11,
                expected_epoch=17,
                predecessor=_trusted_f3(generation_11.binding),
            ),
            _request(
                "privileged-authority-writer-b",
                _binding("e", epoch=18),
                expected_generation=11,
                expected_epoch=17,
                predecessor=_trusted_f3(generation_11.binding),
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(restarted.publish, race_requests[0]),
                executor.submit(contender.publish, race_requests[1]),
            )
            outcomes: list[object] = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except RevocationPublicationConflictError as exc:
                    outcomes.append(exc)
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert sum(isinstance(item, RevocationPublicationConflictError) for item in outcomes) == 1
        assert authority.latest() is not None
        assert authority.latest().generation == 12
    finally:
        contender.close()
        restarted.close()
        cas.close()
        authority.close()


def test_privileged_linux_live_root_config_lock_metadata_drift_fails_closed() -> None:
    if sys.platform != "linux":
        pytest.skip("Linux file-attribute authority test")
    configured = os.environ.get(
        "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_DRIFT_ROOT"
    )
    if configured is None:
        pytest.skip("requires a fresh privileged Linux drift authority root")
    root = Path(configured)
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    descriptors = (
        os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
        os.open(root / "authority.json", os.O_RDONLY),
        os.open(root / "authority.lock", os.O_RDONLY),
    )
    required_flags = (_FS_APPEND_FL, _FS_IMMUTABLE_FL, _FS_IMMUTABLE_FL)
    try:
        for descriptor, required in zip(descriptors, required_flags):
            original_flags = _linux_read_file_flags(descriptor)
            _linux_write_file_flags(descriptor, original_flags & ~required)
            try:
                with pytest.raises(
                    RevocationPublicationIntegrityError,
                    match="identity|kernel flags",
                ):
                    authority.latest()
            finally:
                _linux_write_file_flags(descriptor, original_flags)
        for descriptor, required in zip(descriptors, required_flags):
            original = os.fstat(descriptor)
            original_flags = _linux_read_file_flags(descriptor)
            drift_gid = original.st_gid + 1
            _linux_write_file_flags(descriptor, original_flags & ~required)
            os.fchown(descriptor, original.st_uid, drift_gid)
            _linux_write_file_flags(descriptor, original_flags)
            try:
                with pytest.raises(
                    RevocationPublicationIntegrityError,
                    match="identity|kernel flags",
                ):
                    authority.latest()
            finally:
                _linux_write_file_flags(descriptor, original_flags & ~required)
                os.fchown(descriptor, original.st_uid, original.st_gid)
                _linux_write_file_flags(descriptor, original_flags)
        for descriptor, required in zip(descriptors, required_flags):
            original = os.fstat(descriptor)
            original_flags = _linux_read_file_flags(descriptor)
            drift_uid = 1 if original.st_uid == 0 else 0
            _linux_write_file_flags(descriptor, original_flags & ~required)
            os.fchown(descriptor, drift_uid, original.st_gid)
            _linux_write_file_flags(descriptor, original_flags)
            try:
                with pytest.raises(
                    RevocationPublicationIntegrityError,
                    match="identity|kernel flags",
                ):
                    authority.latest()
            finally:
                _linux_write_file_flags(descriptor, original_flags & ~required)
                os.fchown(descriptor, original.st_uid, original.st_gid)
                _linux_write_file_flags(descriptor, original_flags)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        authority.close()


def _linux_read_file_flags(descriptor: int) -> int:
    value = array.array("I", [0])
    fcntl.ioctl(descriptor, _FS_IOC_GETFLAGS, value, True)
    return int(value[0])


def _linux_write_file_flags(descriptor: int, flags: int) -> None:
    value = array.array("I", [flags])
    fcntl.ioctl(descriptor, _FS_IOC_SETFLAGS, value, True)


def test_privileged_linux_same_byte_config_inode_replacement_rejects_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from builtins import BaseExceptionGroup
    from errno import EBADF

    if sys.platform != "linux":
        pytest.skip("Linux file-attribute authority test")
    configured = os.environ.get(
        "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_METADATA_ROOT"
    )
    if configured is None:
        pytest.skip("requires a fresh privileged Linux metadata authority root")
    root = Path(configured)
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    identity = authority.identity
    config_path = root / "authority.json"
    payload = config_path.read_bytes()
    successor = MonotonicRevocationWitness(
        monotonic_authority=identity,
        generation=1,
        record_digest=_d("f"),
    )
    authority.compare_and_append(None, successor)
    authority.close()

    def replace_config() -> None:
        root_fd = -1
        config_fd = -1
        replacement_fd = -1
        root_flags: int | None = None
        config_flags: int | None = None
        primary_error: BaseException | None = None
        try:
            root_fd = os.open(
                root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            config_fd = os.open(config_path, os.O_RDONLY)
            root_flags = _linux_read_file_flags(root_fd)
            config_flags = _linux_read_file_flags(config_fd)
            config_stat = os.fstat(config_fd)
            _linux_write_file_flags(
                config_fd, config_flags & ~_FS_IMMUTABLE_FL
            )
            _linux_write_file_flags(root_fd, root_flags & ~_FS_APPEND_FL)
            os.unlink(config_path)
            replacement_fd = os.open(
                config_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL,
                stat.S_IMODE(config_stat.st_mode),
            )
            os.fchown(replacement_fd, config_stat.st_uid, config_stat.st_gid)
            os.fchmod(replacement_fd, stat.S_IMODE(config_stat.st_mode))
            os.write(replacement_fd, payload)
            os.fsync(replacement_fd)
            _linux_write_file_flags(replacement_fd, config_flags)
            replacement_stat = os.fstat(replacement_fd)
            assert replacement_stat.st_ino != config_stat.st_ino
            assert stat.S_IMODE(replacement_stat.st_mode) == stat.S_IMODE(
                config_stat.st_mode
            )
            assert replacement_stat.st_uid == config_stat.st_uid
            assert replacement_stat.st_gid == config_stat.st_gid
            assert _linux_read_file_flags(replacement_fd) == config_flags
            assert os.pread(replacement_fd, len(payload) + 1, 0) == payload
            os.fsync(root_fd)
            _linux_write_file_flags(root_fd, root_flags)
        except BaseException as error:
            primary_error = error

        cleanup_errors: list[BaseException] = []
        if replacement_fd >= 0 and config_flags is not None:
            try:
                _linux_write_file_flags(replacement_fd, config_flags)
            except BaseException as error:
                cleanup_errors.append(error)
        if config_fd >= 0 and config_flags is not None:
            try:
                _linux_write_file_flags(config_fd, config_flags)
            except BaseException as error:
                cleanup_errors.append(error)
        if root_fd >= 0 and root_flags is not None:
            try:
                _linux_write_file_flags(root_fd, root_flags)
            except BaseException as error:
                cleanup_errors.append(error)
        for descriptor in (replacement_fd, config_fd, root_fd):
            if descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except BaseException as error:
                cleanup_errors.append(error)
        if primary_error is not None:
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "config replacement and cleanup failed",
                    [primary_error, *cleanup_errors],
                )
            raise primary_error
        if cleanup_errors:
            raise BaseExceptionGroup(
                "config replacement cleanup failed", cleanup_errors
            )

    original_open = os.open
    original_read_file_flags = _linux_read_file_flags
    fault_cases = (
        ("config-open", None),
        ("root-getflags", 1),
        ("config-getflags", 2),
    )
    for fault_name, failed_flag_read in fault_cases:
        injected = RuntimeError(f"injected {fault_name} failure")
        acquired_descriptors: list[int] = []
        flag_reads = 0

        def tracking_open(
            path: str | Path,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if fault_name == "config-open" and Path(path) == config_path:
                raise injected
            if dir_fd is None:
                descriptor = original_open(path, flags, mode)
            else:
                descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
            acquired_descriptors.append(descriptor)
            return descriptor

        def failing_read_file_flags(descriptor: int) -> int:
            nonlocal flag_reads
            flag_reads += 1
            if flag_reads == failed_flag_read:
                raise injected
            return original_read_file_flags(descriptor)

        with monkeypatch.context() as fault_patch:
            fault_patch.setattr(os, "open", tracking_open)
            fault_patch.setitem(
                globals(), "_linux_read_file_flags", failing_read_file_flags
            )
            with pytest.raises(RuntimeError) as exc_info:
                replace_config()
            assert exc_info.value is injected
        for descriptor in acquired_descriptors:
            with pytest.raises(OSError) as exc_info:
                os.fstat(descriptor)
            assert exc_info.value.errno == EBADF

    replace_config()
    restarted = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    try:
        assert restarted.identity.config_inode != identity.config_inode
        with pytest.raises(
            RevocationPublicationIntegrityError,
            match="authority or generation",
        ):
            restarted.latest()
    finally:
        restarted.close()

def _platform_read_file_flags(descriptor: int) -> int:
    if sys.platform == "linux":
        return _linux_read_file_flags(descriptor)
    if sys.platform == "darwin":
        return int(os.fstat(descriptor).st_flags)
    raise RuntimeError("unsupported privileged authority platform")


def _platform_write_file_flags(descriptor: int, flags: int) -> None:
    if sys.platform == "linux":
        _linux_write_file_flags(descriptor, flags)
        return
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        fchflags = libc.fchflags
        fchflags.argtypes = (ctypes.c_int, ctypes.c_uint)
        fchflags.restype = ctypes.c_int
        if fchflags(descriptor, flags) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return
    raise RuntimeError("unsupported privileged authority platform")


def _authority_required_flag(target_name: str) -> int:
    if sys.platform == "linux":
        return _FS_APPEND_FL if target_name == "root" else _FS_IMMUTABLE_FL
    if sys.platform == "darwin":
        return stat.SF_APPEND if target_name == "root" else stat.SF_IMMUTABLE
    raise RuntimeError("unsupported privileged authority platform")


def _fresh_privileged_root(case_name: str) -> Path:
    platform_name = sys.platform.upper()
    configured = os.environ.get(
        f"BREADBOARD_PRIVILEGED_{platform_name}_REVOCATION_AUTHORITY_"
        f"{case_name.upper()}_ROOT"
    )
    if configured is None:
        pytest.skip(
            f"requires a fresh privileged {sys.platform} {case_name} authority root"
        )
    return Path(configured)


def _seed_privileged_authority(
    authority: PreprovisionedAppendOnlyMonotonicRevocationAuthority,
) -> MonotonicRevocationWitness:
    assert authority.latest() is None
    successor = MonotonicRevocationWitness(
        monotonic_authority=authority.identity,
        generation=1,
        record_digest=_d("f"),
    )
    assert authority.compare_and_append(None, successor) == successor
    return successor


def _assert_live_authority_fails_before_generation_io(
    authority: PreprovisionedAppendOnlyMonotonicRevocationAuthority,
    generation_one: MonotonicRevocationWitness,
    root: Path,
) -> None:
    generation_stat = (root / "1.json").stat()
    with pytest.raises(RevocationPublicationIntegrityError):
        authority.latest()
    generation_two = MonotonicRevocationWitness(
        monotonic_authority=generation_one.monotonic_authority,
        generation=2,
        record_digest=_d("e"),
    )
    with pytest.raises(RevocationPublicationIntegrityError):
        authority.compare_and_append(generation_one, generation_two)
    assert (root / "1.json").stat() == generation_stat
    assert not (root / "2.json").exists()


def _assert_restarted_authority_fails_before_generation_io(
    root: Path,
    generation_one: MonotonicRevocationWitness,
) -> None:
    try:
        restarted = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    except (ValueError, RevocationPublicationIntegrityError):
        return
    try:
        _assert_live_authority_fails_before_generation_io(
            restarted, generation_one, root
        )
    finally:
        restarted.close()


@pytest.mark.parametrize("target_name", ["root", "config", "lock"])
@pytest.mark.parametrize("identity_field", ["uid", "gid", "flags"])
def test_privileged_platform_root_config_lock_identity_drift_fails_closed(
    target_name: str,
    identity_field: str,
) -> None:
    if sys.platform not in {"darwin", "linux"}:
        pytest.skip("Darwin or Linux kernel authority test")
    case_name = f"{target_name}_{identity_field}"
    root = _fresh_privileged_root(case_name)
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    generation_one = _seed_privileged_authority(authority)
    target = {
        "root": root,
        "config": root / "authority.json",
        "lock": root / "authority.lock",
    }[target_name]
    descriptor = os.open(
        target,
        os.O_RDONLY
        | (getattr(os, "O_DIRECTORY", 0) if target_name == "root" else 0),
    )
    original = os.fstat(descriptor)
    original_flags = _platform_read_file_flags(descriptor)
    required_flag = _authority_required_flag(target_name)
    try:
        if identity_field == "flags":
            _platform_write_file_flags(
                descriptor, original_flags & ~required_flag
            )
        else:
            _platform_write_file_flags(
                descriptor, original_flags & ~required_flag
            )
            if identity_field == "uid":
                drift_uid = 1 if original.st_uid == 0 else 0
                os.fchown(descriptor, drift_uid, original.st_gid)
            else:
                os.fchown(descriptor, original.st_uid, original.st_gid + 1)
            _platform_write_file_flags(descriptor, original_flags)
        _assert_live_authority_fails_before_generation_io(
            authority, generation_one, root
        )
        _assert_restarted_authority_fails_before_generation_io(
            root, generation_one
        )
    finally:
        current = _platform_read_file_flags(descriptor)
        _platform_write_file_flags(descriptor, current & ~required_flag)
        os.fchown(descriptor, original.st_uid, original.st_gid)
        _platform_write_file_flags(descriptor, original_flags)
        os.close(descriptor)
        authority.close()


def _write_exact_file(
    path: Path,
    payload: bytes,
    metadata: os.stat_result,
    flags: int,
) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        stat.S_IMODE(metadata.st_mode),
    )
    try:
        os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            assert written > 0
            view = view[written:]
        os.fsync(descriptor)
        _platform_write_file_flags(descriptor, flags)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("target_name", ["root", "config", "lock"])
def test_privileged_platform_same_byte_inode_replacement_fails_live_and_restart(
    target_name: str,
) -> None:
    if sys.platform not in {"darwin", "linux"}:
        pytest.skip("Darwin or Linux kernel authority test")
    root = _fresh_privileged_root(f"{target_name}_inode")
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    generation_one = _seed_privileged_authority(authority)
    root_descriptor = os.open(
        root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    root_metadata = os.fstat(root_descriptor)
    root_flags = _platform_read_file_flags(root_descriptor)
    try:
        if target_name == "root":
            parent_descriptor = os.open(
                root.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            config = root / "authority.json"
            lock = root / "authority.lock"
            generation = root / "1.json"
            copies = [
                (
                    "authority.json",
                    config.read_bytes(),
                    config.stat(),
                    _platform_read_file_flags(authority._config_fd),
                ),
                (
                    "authority.lock",
                    lock.read_bytes(),
                    lock.stat(),
                    _platform_read_file_flags(authority._lock_fd),
                ),
            ]
            generation_descriptor = os.open(generation, os.O_RDONLY)
            try:
                copies.append(
                    (
                        "1.json",
                        generation.read_bytes(),
                        generation.stat(),
                        _platform_read_file_flags(generation_descriptor),
                    )
                )
            finally:
                os.close(generation_descriptor)
            backup = root.with_name(f"{root.name}.replaced")
            try:
                _platform_write_file_flags(
                    root_descriptor,
                    root_flags & ~_authority_required_flag("root"),
                )
                os.rename(root, backup)
                os.mkdir(root, stat.S_IMODE(root_metadata.st_mode))
                os.chown(root, root_metadata.st_uid, root_metadata.st_gid)
                for name, payload, metadata, flags in copies:
                    _write_exact_file(root / name, payload, metadata, flags)
                replacement_descriptor = os.open(
                    root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    _platform_write_file_flags(replacement_descriptor, root_flags)
                    os.fsync(replacement_descriptor)
                finally:
                    os.close(replacement_descriptor)
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        else:
            target = (
                root / "authority.json"
                if target_name == "config"
                else root / "authority.lock"
            )
            target_descriptor = os.open(target, os.O_RDONLY)
            try:
                payload = target.read_bytes()
                metadata = os.fstat(target_descriptor)
                target_flags = _platform_read_file_flags(target_descriptor)
                _platform_write_file_flags(
                    target_descriptor,
                    target_flags & ~_authority_required_flag(target_name),
                )
                _platform_write_file_flags(
                    root_descriptor,
                    root_flags & ~_authority_required_flag("root"),
                )
                os.unlink(target)
                _write_exact_file(target, payload, metadata, target_flags)
                os.fsync(root_descriptor)
                _platform_write_file_flags(root_descriptor, root_flags)
            finally:
                os.close(target_descriptor)
        _assert_live_authority_fails_before_generation_io(
            authority, generation_one, root
        )
        _assert_restarted_authority_fails_before_generation_io(
            root, generation_one
        )
    finally:
        os.close(root_descriptor)
        authority.close()


def test_privileged_linux_bind_mount_device_replacement_fails_live_and_restart() -> None:
    if sys.platform != "linux":
        pytest.skip("Linux bind-mount device identity test")
    root = _fresh_privileged_root("device")
    replacement_parent_value = os.environ.get(
        "BREADBOARD_PRIVILEGED_LINUX_REVOCATION_AUTHORITY_DEVICE_PARENT"
    )
    if replacement_parent_value is None:
        pytest.skip("requires a privileged replacement parent on another device")
    replacement_parent = Path(replacement_parent_value)
    if replacement_parent.stat().st_dev == root.stat().st_dev:
        pytest.skip("replacement parent must be on another device")
    authority = PreprovisionedAppendOnlyMonotonicRevocationAuthority(root)
    generation_one = _seed_privileged_authority(authority)
    replacement = replacement_parent / f"revocation-device-{os.getpid()}"
    replacement.mkdir(mode=stat.S_IMODE(root.stat().st_mode))
    os.chown(replacement, root.stat().st_uid, root.stat().st_gid)
    root_descriptor = os.open(
        root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    root_flags = _platform_read_file_flags(root_descriptor)
    generation_descriptor = os.open(root / "1.json", os.O_RDONLY)
    try:
        for name, descriptor in (
            ("authority.json", authority._config_fd),
            ("authority.lock", authority._lock_fd),
            ("1.json", generation_descriptor),
        ):
            source = root / name
            _write_exact_file(
                replacement / name,
                source.read_bytes(),
                source.stat(),
                _platform_read_file_flags(descriptor),
            )
        replacement_descriptor = os.open(
            replacement, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            _platform_write_file_flags(replacement_descriptor, root_flags)
        finally:
            os.close(replacement_descriptor)
    finally:
        os.close(generation_descriptor)
        os.close(root_descriptor)
    try:
        attack = request_preconfigured_bind_replace()
        assert attack.source_before.device == replacement.stat().st_dev
        assert attack.source_before.inode == replacement.stat().st_ino
        assert attack.target_before.device != attack.source_before.device
        assert root.stat().st_dev == attack.source_before.device
        assert root.stat().st_ino == attack.source_before.inode
        _assert_live_authority_fails_before_generation_io(
            authority, generation_one, root
        )
        _assert_restarted_authority_fails_before_generation_io(
            root, generation_one
        )
    finally:
        authority.close()
