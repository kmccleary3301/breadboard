from __future__ import annotations

from contextlib import contextmanager
import ast
import fcntl
import hashlib
import hmac
import json
import multiprocessing
import os
import resource
import stat
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, cast

import pytest

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness.materialization import _DirFd
from breadboard.rl.phase5 import g4_source_deletion as deletion_module
from breadboard.rl.phase5.g4_source_deletion import (
    FinalReceiptVerifier,
    GateKind,
    RollbackStoreSourceOwnershipAuthority,
    SourceDeletionConflict,
    SourceDeletionError,
    SourceDeletionGateReceipt,
    SourceDeletionGateReceipts,
    SourceDeletionGuard,
    SourceDeletionRequest,
    SourceOwnershipIdentity,
    VerifiedGateOutcome,
)
from breadboard.rl.phase5.rollback_store import (
    DependentObjectKind,
    DependentOwnership,
    FilesystemDependentQuarantineStore,
    ImmutableObjectRef,
)

_DIGEST_PREFIX = "sha256:"
_ROLLBACK_ID = "rollback-g4-001"
_JOURNAL_DIGEST = _DIGEST_PREFIX + "1" * 64
_STORE_KEY = bytes(range(32))
_RECEIPT_KEY = bytes(range(32, 64))
_GATE_KEY = bytes(range(64, 96))
_TUPLE_DIGEST = _DIGEST_PREFIX + "a" * 64
_OUTCOMES = {
    "episode_terminal": "closed_cleanup_released",
    "revocation_published": "published_active",
    "dependent_quarantined": "quarantined_export_blocked",
    "active_tuple_restored": "cas_committed",
    "rerun_recorded": "succeeded_recorded",
}
_SCHEMAS = {
    "episode_terminal": "bb.rl.phase5.episode-terminal-receipt.v1",
    "revocation_published": "bb.rl.phase5.revocation-publication-receipt.v1",
    "dependent_quarantined": "bb.rl.phase5.dependent-quarantine-receipt.v1",
    "active_tuple_restored": "bb.rl.phase5.active-approved-tuple-history.v1",
    "rerun_recorded": "bb.rl.phase5.exact-rerun-receipt.v1",
}
@pytest.fixture(autouse=True)
def _restore_private_test_directory_modes(tmp_path: Path):
    yield
    for current, directories, _ in os.walk(tmp_path):
        for directory in directories:
            try:
                os.chmod(Path(current) / directory, 0o700)
            except FileNotFoundError:
                pass




_TEST_FINAL_UNLINK_RACE = ""
_TEST_FINAL_UNLINK_GROUP = -1
_TEST_FINAL_UNLINK_DESTINATION = ""


def _unconfigured_unlinkat(*args: object, **kwargs: object) -> None:
    raise AssertionError("unconfigured unlinkat attack")


_TEST_ORIGINAL_RAW_UNLINKAT: Callable[..., int] | None = None


def _final_raw_unlinkat_directory_escape(
    parent_fd: int,
    name: object,
    flags: int,
) -> int:
    name_value = getattr(name, "value", None)
    if type(name_value) is not bytes:
        raise AssertionError("raw unlinkat name is not bytes")
    entry_name = name_value.decode("ascii")
    os.rename(
        entry_name,
        _TEST_FINAL_UNLINK_DESTINATION,
        src_dir_fd=parent_fd,
    )
    os.mkdir(entry_name, 0o700, dir_fd=parent_fd)
    if _TEST_ORIGINAL_RAW_UNLINKAT is None:
        raise AssertionError("unconfigured raw unlinkat attack")
    return _TEST_ORIGINAL_RAW_UNLINKAT(parent_fd, name, flags)


_TEST_ORIGINAL_UNLINKAT: Callable[..., None] = _unconfigured_unlinkat


def _final_unlink_attack(
    parent_fd: int,
    name: str,
    *,
    directory: bool,
    expected_metadata: tuple[int, int, int, int, int, int, int],
) -> None:
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if _TEST_FINAL_UNLINK_RACE == "chmod":
        os.chmod(
            name,
            stat.S_IMODE(metadata.st_mode) ^ stat.S_IXUSR,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    elif _TEST_FINAL_UNLINK_RACE == "fchmod":
        descriptor = os.open(name, os.O_RDONLY, dir_fd=parent_fd)
        try:
            os.fchmod(
                descriptor,
                stat.S_IMODE(metadata.st_mode) ^ stat.S_IXUSR,
            )
        finally:
            os.close(descriptor)
    elif _TEST_FINAL_UNLINK_RACE == "chown":
        os.chown(
            name,
            -1,
            _TEST_FINAL_UNLINK_GROUP,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    elif _TEST_FINAL_UNLINK_RACE == "directory_escape":
        os.rename(name, _TEST_FINAL_UNLINK_DESTINATION, src_dir_fd=parent_fd)
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    else:
        os.utime(
            name,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1),
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    _TEST_ORIGINAL_UNLINKAT(
        parent_fd,
        name,
        directory=directory,
        expected_metadata=expected_metadata,
    )


def _digest(raw: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class _CurrentGate:
    refs: tuple[SourceDeletionGateReceipt, ...]
    rollback_id: str
    journal_request_digest: str
    subjects: tuple[str, ...]
    outcome: str
    generation: int


class SignedCurrentGateRepository(FinalReceiptVerifier):
    """Schema-specific signed repository with one authoritative current head per gate."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True)
        self.root = root
        self._current: dict[GateKind, _CurrentGate] = {}
        self._published = 0
        self._lock = threading.RLock()

    def publish(
        self,
        gate: GateKind,
        subjects: tuple[str, ...],
        **kwargs: object,
    ) -> SourceDeletionGateReceipt:
        with self._lock:
            return self._publish_unlocked(gate, subjects, **kwargs)

    def _publish_unlocked(
        self,
        gate: GateKind,
        subjects: tuple[str, ...],
        *,
        rollback_id: str = _ROLLBACK_ID,
        journal_request_digest: str = _JOURNAL_DIGEST,
        outcome: str | None = None,
        generation: int = 1,
        make_current: bool = True,
    ) -> SourceDeletionGateReceipt:
        unsigned = {
            "authority_generation": generation,
            "gate": gate,
            "journal_request_digest": journal_request_digest,
            "rollback_id": rollback_id,
            "schema_version": _SCHEMAS[gate],
            "subjects": list(subjects),
            "terminal_outcome": outcome or _OUTCOMES[gate],
        }
        signature = hmac.new(_GATE_KEY, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
        raw = canonical_json_bytes({**unsigned, "signature": signature})
        self._published += 1
        path = self.root / f"{gate}-{self._published}.json"
        path.write_bytes(raw)
        ref = SourceDeletionGateReceipt(os.fspath(path), _digest(raw))
        if make_current:
            self._current[gate] = _CurrentGate(
                (ref,),
                rollback_id,
                journal_request_digest,
                subjects,
                outcome or _OUTCOMES[gate],
                generation,
            )
        return ref

    def verify(
        self,
        *,
        refs: tuple[SourceDeletionGateReceipt, ...],
        expected_gate: GateKind,
        rollback_id: str,
        journal_request_digest: str,
        canonical_receipts: tuple[bytes, ...],
    ) -> VerifiedGateOutcome:
        current = self._current.get(expected_gate)
        if current is None:
            raise SourceDeletionError("authoritative_gate_inventory_missing")
        if (
            refs != current.refs
            or rollback_id != current.rollback_id
            or journal_request_digest != current.journal_request_digest
            or len(canonical_receipts) != len(refs)
        ):
            raise SourceDeletionError("authoritative_gate_inventory_not_current")
        for raw in canonical_receipts:
            document = json.loads(raw)
            if set(document) != {
                "authority_generation",
                "gate",
                "journal_request_digest",
                "rollback_id",
                "schema_version",
                "signature",
                "subjects",
                "terminal_outcome",
            }:
                raise SourceDeletionError("authoritative_gate_schema_invalid")
            signature = document.pop("signature")
            expected_signature = hmac.new(
                _GATE_KEY, canonical_json_bytes(document), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_signature):
                raise SourceDeletionError("authoritative_gate_signature_invalid")
            if (
                document["schema_version"] != _SCHEMAS[expected_gate]
                or document["gate"] != expected_gate
                or document["rollback_id"] != current.rollback_id
                or document["journal_request_digest"] != current.journal_request_digest
                or tuple(document["subjects"]) != current.subjects
                or document["terminal_outcome"] != current.outcome
                or document["authority_generation"] != current.generation
            ):
                raise SourceDeletionError("authoritative_gate_schema_join_invalid")
        receipt_sha256s = tuple(ref.sha256 for ref in refs)
        inventory_digest = _digest(
            canonical_json_bytes(
                {
                    "authority_generation": current.generation,
                    "gate": expected_gate,
                    "journal_request_digest": journal_request_digest,
                    "receipt_sha256s": list(receipt_sha256s),
                    "rollback_id": rollback_id,
                    "subjects": list(current.subjects),
                }
            )
        )
        return VerifiedGateOutcome(
            gate=expected_gate,
            rollback_id=rollback_id,
            journal_request_digest=journal_request_digest,
            subjects=current.subjects,
            receipt_sha256s=receipt_sha256s,
            terminal_outcome=cast(object, current.outcome),  # type: ignore[arg-type]
            authority_generation=current.generation,
            inventory_digest=inventory_digest,
            current=True,
        )

    @contextmanager
    def acquire(
        self,
        *,
        receipt_sets: dict[
            GateKind,
            tuple[tuple[SourceDeletionGateReceipt, bytes], ...],
        ],
        rollback_id: str,
        journal_request_digest: str,
    ):
        with self._lock:
            outcomes = {
                gate: self.verify(
                    refs=tuple(item[0] for item in supplied),
                    expected_gate=gate,
                    rollback_id=rollback_id,
                    journal_request_digest=journal_request_digest,
                    canonical_receipts=tuple(item[1] for item in supplied),
                )
                for gate, supplied in receipt_sets.items()
            }
            snapshot = dict(self._current)

            class Lease:
                def __init__(self) -> None:
                    self.outcomes = outcomes

                def assert_current(inner_self) -> None:
                    if self._current != snapshot:
                        raise SourceDeletionError("authoritative_gate_inventory_not_current")

            yield Lease()


def _source_identity(
    root: Path, relative: str, *, authority_id: str = "legacy-source-root"
) -> SourceOwnershipIdentity:
    path = root / relative
    metadata = os.stat(path, follow_symlinks=False)
    if stat.S_ISREG(metadata.st_mode):
        kind = "file"
        source_digest = _digest(path.read_bytes())
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            source_digest = SourceDeletionGuard._directory_digest(descriptor)
        finally:
            os.close(descriptor)
    else:
        raise AssertionError("source fixture must be regular")
    return SourceOwnershipIdentity(
        root_authority_id=authority_id,
        root_path=os.fspath(root),
        relative_path=relative,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        ctime_ns=metadata.st_ctime_ns,
        size_bytes=metadata.st_size,
        sha256=source_digest,
        kind=kind,
    )


def _gate_bundle(
    authority: SignedCurrentGateRepository,
    *,
    dependent_subjects: tuple[str, ...],
    episode_subjects: tuple[str, ...] = ("episode-1",),
) -> SourceDeletionGateReceipts:
    return SourceDeletionGateReceipts(
        episode_terminal_refs=(authority.publish("episode_terminal", episode_subjects),),
        revocation_snapshot_ref=authority.publish("revocation_published", ("snapshot-8",)),
        dependent_quarantine_refs=(
            authority.publish("dependent_quarantined", dependent_subjects),
        ),
        active_tuple_history_ref=authority.publish(
            "active_tuple_restored", ("tuple-generation-9",)
        ),
        rerun_receipt_ref=authority.publish("rerun_recorded", ("rerun-episode-2",)),
    )


@dataclass(slots=True)
class Scenario:
    tmp_path: Path
    root: Path
    sources: tuple[SourceOwnershipIdentity, ...]
    store: FilesystemDependentQuarantineStore
    gate_authority: SignedCurrentGateRepository
    ownership_authority: RollbackStoreSourceOwnershipAuthority
    request: SourceDeletionRequest
    guard: SourceDeletionGuard
    object_refs: tuple[ImmutableObjectRef, ...]


def _scenario(
    tmp_path: Path,
    *,
    files: tuple[tuple[str, bytes], ...] = (("legacy.json", b"legacy-source"),),
    directories: tuple[str, ...] = (),
    quarantine: bool = True,
    quarantine_rollback_id: str = _ROLLBACK_ID,
    operation_id: str = "delete-op-001",
) -> Scenario:
    root = tmp_path / "sources"
    root.mkdir(parents=True)
    for relative, raw in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for relative in directories:
        (root / relative).mkdir(parents=True)
    sources = tuple(
        _source_identity(root, relative)
        for relative in (
            *(relative for relative, _ in files),
            *directories,
        )
    )
    store = FilesystemDependentQuarantineStore(
        tmp_path / "dependent-store", authority_key=_STORE_KEY
    )
    object_refs: list[ImmutableObjectRef] = []
    for index, source in enumerate(sources):
        object_ref = ImmutableObjectRef(source.key, source.sha256)
        object_refs.append(object_ref)
        store.register(
            DependentOwnership(
                f"register-source-{index}",
                _TUPLE_DIGEST,
                "episode-1",
                "run-1",
                DependentObjectKind.EVIDENCE,
                object_ref,
                (),
            )
        )
    if quarantine:
        store.quarantine_causal(
            quarantine_rollback_id,
            _JOURNAL_DIGEST,
            tuple(object_refs),
        )
    gate_authority = SignedCurrentGateRepository(tmp_path / "gate-authority")
    gates = _gate_bundle(
        gate_authority,
        dependent_subjects=tuple(ref.reference for ref in object_refs),
    )
    request = SourceDeletionRequest(
        operation_id=operation_id,
        rollback_id=_ROLLBACK_ID,
        journal_request_digest=_JOURNAL_DIGEST,
        owned_sources=sources,
        gates=gates,
    )
    ownership_authority = RollbackStoreSourceOwnershipAuthority(
        store,
        object_refs_by_rollback={
            _ROLLBACK_ID: {
                source.key: object_ref
                for source, object_ref in zip(sources, object_refs, strict=True)
            }
        },
    )
    guard = SourceDeletionGuard(
        receipt_root=tmp_path / "deletion-receipts",
        receipt_authority_key=_RECEIPT_KEY,
        root_authorities={"legacy-source-root": root},
        final_receipt_verifier=gate_authority,
        source_ownership_authority=ownership_authority,
        clock=lambda: datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
    )
    return Scenario(
        tmp_path,
        root,
        sources,
        store,
        gate_authority,
        ownership_authority,
        request,
        guard,
        tuple(object_refs),
    )


def _restarted_guard(scenario: Scenario) -> SourceDeletionGuard:
    return SourceDeletionGuard(
        receipt_root=scenario.tmp_path / "deletion-receipts",
        receipt_authority_key=_RECEIPT_KEY,
        root_authorities={"legacy-source-root": scenario.root},
        final_receipt_verifier=scenario.gate_authority,
        source_ownership_authority=scenario.ownership_authority,
        clock=lambda: datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "missing_gate",
    [
        "episode_terminal",
        "revocation_published",
        "dependent_quarantined",
        "active_tuple_restored",
        "rerun_recorded",
    ],
)
def test_refuses_each_missing_final_receipt_file(
    tmp_path: Path, missing_gate: GateKind
) -> None:
    scenario = _scenario(tmp_path)
    refs = dict(scenario.request.gates.groups())
    Path(refs[missing_gate][0].path).unlink()

    with pytest.raises(SourceDeletionError, match="gate_receipt_unavailable"):
        scenario.guard.delete(scenario.request)

    assert (scenario.root / "legacy.json").read_bytes() == b"legacy-source"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("episode_terminal_refs", (), "episode_terminal_receipts_missing"),
        ("dependent_quarantine_refs", (), "dependent_quarantine_receipts_missing"),
        ("revocation_snapshot_ref", None, "revocation_snapshot_receipt_missing"),
        ("active_tuple_history_ref", None, "active_tuple_history_receipt_missing"),
        ("rerun_receipt_ref", None, "rerun_receipt_missing"),
    ],
)
def test_gate_bundle_cannot_omit_any_receipt(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    scenario = _scenario(tmp_path)
    values = {
        "episode_terminal_refs": scenario.request.gates.episode_terminal_refs,
        "revocation_snapshot_ref": scenario.request.gates.revocation_snapshot_ref,
        "dependent_quarantine_refs": scenario.request.gates.dependent_quarantine_refs,
        "active_tuple_history_ref": scenario.request.gates.active_tuple_history_ref,
        "rerun_receipt_ref": scenario.request.gates.rerun_receipt_ref,
    }
    values[field] = value
    with pytest.raises(ValueError, match=f"^{error}$"):
        SourceDeletionGateReceipts(**values)  # type: ignore[arg-type]


def test_exact_deletion_is_authenticated_idempotent_and_proves_absence(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    first = scenario.guard.delete(scenario.request)
    assert first.deleted == (scenario.sources[0].key,)
    assert first.already_absent == ()
    assert len(first.authority_signature) == len("hmac-sha256:") + 64
    assert not (scenario.root / "legacy.json").exists()

    second = _restarted_guard(scenario).delete(scenario.request)
    assert second == first
    assert second.absence_proofs[0].prior_sha256 == scenario.sources[0].sha256


def test_refuses_content_inode_symlink_hardlink_and_root_drift(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    target = scenario.root / "legacy.json"
    target.write_bytes(b"changed")
    with pytest.raises(SourceDeletionError, match="source_identity_or_digest_drift"):
        scenario.guard.delete(scenario.request)
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    with pytest.raises(SourceDeletionError, match="source_kind_or_symlink_drift"):
        scenario.guard.delete(replace(scenario.request, operation_id="delete-op-symlink"))

    second = _scenario(tmp_path / "hardlink")
    target_2 = second.root / "legacy.json"
    os.link(target_2, second.root / "hard-copy.json")
    with pytest.raises(SourceDeletionError, match="source_hardlink_drift"):
        second.guard.delete(second.request)
    assert target_2.exists() and (second.root / "hard-copy.json").exists()

    third = _scenario(tmp_path / "root-swap")
    displaced = third.tmp_path / "displaced"
    third.root.rename(displaced)
    third.root.mkdir()
    (third.root / "legacy.json").write_bytes(b"replacement")
    with pytest.raises(SourceDeletionError, match="source_root_substituted"):
        third.guard.delete(third.request)
    assert (displaced / "legacy.json").read_bytes() == b"legacy-source"
    assert (third.root / "legacy.json").read_bytes() == b"replacement"


def test_rejects_duplicate_physical_sources_and_aliased_or_nested_roots(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "source").write_bytes(b"source")
    source = _source_identity(root, "source", authority_id="root-a")
    duplicate = replace(source, root_authority_id="root-b")
    authority = SignedCurrentGateRepository(tmp_path / "gates")
    gates = _gate_bundle(authority, dependent_subjects=("object",))
    with pytest.raises(ValueError, match="owned_source_physical_identity_duplicate"):
        SourceDeletionRequest(
            "operation",
            _ROLLBACK_ID,
            _JOURNAL_DIGEST,
            (source, duplicate),
            gates,
        )

    store = FilesystemDependentQuarantineStore(tmp_path / "store", authority_key=_STORE_KEY)
    ownership = RollbackStoreSourceOwnershipAuthority(
        store,
        object_refs_by_rollback={_ROLLBACK_ID: {source.key: ImmutableObjectRef(source.key, source.sha256)}},
    )
    with pytest.raises(ValueError, match="root_authority_physical_alias"):
        SourceDeletionGuard(
            receipt_root=tmp_path / "receipts-a",
            receipt_authority_key=_RECEIPT_KEY,
            root_authorities={"root-a": root, "root-b": root},
            final_receipt_verifier=authority,
            source_ownership_authority=ownership,
        )
    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="root_authorities_overlap"):
        SourceDeletionGuard(
            receipt_root=tmp_path / "receipts-b",
            receipt_authority_key=_RECEIPT_KEY,
            root_authorities={"root-a": root, "root-b": nested},
            final_receipt_verifier=authority,
            source_ownership_authority=ownership,
        )
    assert (root / "source").exists()


def test_unrelated_sibling_survives_and_unrelated_descendant_blocks_tree(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    unrelated = scenario.root / "unrelated.txt"
    unrelated.write_bytes(b"unrelated")
    scenario.guard.delete(scenario.request)
    assert unrelated.read_bytes() == b"unrelated"

    tree_scenario_root = tmp_path / "tree"
    tree_scenario = _scenario(
        tree_scenario_root,
        files=(("legacy-tree/owned.txt", b"owned"),),
        operation_id="tree-delete",
    )
    tree = tree_scenario.root / "legacy-tree"
    (tree / "unrelated.txt").write_bytes(b"unrelated")
    directory = _source_identity(tree_scenario.root, "legacy-tree")
    request = replace(
        tree_scenario.request,
        owned_sources=(directory, *tree_scenario.sources),
    )
    with pytest.raises(SourceDeletionError, match="source_ownership_authoritative_inventory_mismatch"):
        tree_scenario.guard.delete(request)
    assert (tree / "owned.txt").exists() and (tree / "unrelated.txt").exists()


def test_exact_tree_delete_never_calls_recursive_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    tree = root / "legacy-tree"
    tree.mkdir()
    (tree / "source.txt").write_bytes(b"source")
    sources = (
        _source_identity(root, "legacy-tree"),
        _source_identity(root, "legacy-tree/source.txt"),
    )
    store = FilesystemDependentQuarantineStore(tmp_path / "store", authority_key=_STORE_KEY)
    refs: list[ImmutableObjectRef] = []
    for index, source in enumerate(sources):
        ref = ImmutableObjectRef(source.key, source.sha256)
        refs.append(ref)
        store.register(
            DependentOwnership(
                f"registration-{index}",
                _TUPLE_DIGEST,
                "episode-1",
                "run-1",
                DependentObjectKind.EVIDENCE,
                ref,
                (),
            )
        )
    store.quarantine_causal(_ROLLBACK_ID, _JOURNAL_DIGEST, tuple(refs))
    gate_authority = SignedCurrentGateRepository(tmp_path / "gates")
    request = SourceDeletionRequest(
        "tree-operation",
        _ROLLBACK_ID,
        _JOURNAL_DIGEST,
        sources,
        _gate_bundle(gate_authority, dependent_subjects=tuple(ref.reference for ref in refs)),
    )
    ownership = RollbackStoreSourceOwnershipAuthority(
        store,
        object_refs_by_rollback={
            _ROLLBACK_ID: {source.key: ref for source, ref in zip(sources, refs, strict=True)}
        },
    )
    guard = SourceDeletionGuard(
        receipt_root=tmp_path / "receipts",
        receipt_authority_key=_RECEIPT_KEY,
        root_authorities={"legacy-source-root": root},
        final_receipt_verifier=gate_authority,
        source_ownership_authority=ownership,
    )
    monkeypatch.setattr(
        _DirFd,
        "remove_tree",
        lambda *_args, **_kwargs: pytest.fail("recursive deletion called"),
    )
    receipt = guard.delete(request)
    assert not tree.exists()
    assert set(receipt.deleted) == {source.key for source in sources}


def test_partial_delete_and_quarantine_rename_resume_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario(
        tmp_path,
        files=(("a.txt", b"a"), ("b.txt", b"b")),
    )
    original = scenario.guard._quarantine_and_delete
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected interruption")
        original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(scenario.guard, "_quarantine_and_delete", fail_second)
    with pytest.raises(OSError, match="injected interruption"):
        scenario.guard.delete(scenario.request)
    assert not (scenario.root / "a.txt").exists()
    assert (scenario.root / "b.txt").exists()
    monkeypatch.setattr(scenario.guard, "_quarantine_and_delete", original)

    receipt = _restarted_guard(scenario).delete(scenario.request)
    assert receipt.already_absent == (scenario.sources[0].key,)
    assert receipt.deleted == (scenario.sources[1].key,)
    assert not (scenario.root / "b.txt").exists()


def test_namespace_swap_before_quarantine_preserves_unrelated_and_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario(tmp_path)
    original_rename = SourceDeletionGuard._rename_noreplace
    owned_saved = scenario.root / "owned-saved.json"
    swapped = False

    def swap_then_rename(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
        nonlocal swapped
        if source == "legacy.json" and destination == "owned" and not swapped:
            swapped = True
            os.rename("legacy.json", "owned-saved.json", src_dir_fd=source_fd, dst_dir_fd=source_fd)
            descriptor = os.open(
                "legacy.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=source_fd
            )
            os.write(descriptor, b"unrelated")
            os.close(descriptor)
        original_rename(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(SourceDeletionGuard, "_rename_noreplace", staticmethod(swap_then_rename))
    with pytest.raises(SourceDeletionError, match="source_rename_transition_invalid"):
        scenario.guard.delete(scenario.request)

    assert owned_saved.read_bytes() == b"legacy-source"
    assert (scenario.root / "legacy.json").read_bytes() == b"unrelated"
    assert not any(path.name.endswith(".receipt.json") for path in (tmp_path / "deletion-receipts").iterdir())


@pytest.mark.parametrize("race", ["hardlink", "content"])
def test_hardlink_or_content_race_before_quarantine_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    scenario = _scenario(tmp_path)
    original_rename = SourceDeletionGuard._rename_noreplace
    raced = False

    def race_then_rename(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
        nonlocal raced
        if source == "legacy.json" and destination == "owned" and not raced:
            raced = True
            if race == "hardlink":
                os.link("legacy.json", "raced-link", src_dir_fd=source_fd, dst_dir_fd=source_fd)
            else:
                descriptor = os.open("legacy.json", os.O_WRONLY, dir_fd=source_fd)
                os.write(descriptor, b"changed-source")
                os.close(descriptor)
        original_rename(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(SourceDeletionGuard, "_rename_noreplace", staticmethod(race_then_rename))
    with pytest.raises(SourceDeletionError, match="source_rename_transition_invalid"):
        scenario.guard.delete(scenario.request)
    assert (scenario.root / "legacy.json").exists()
    if race == "hardlink":
        assert (scenario.root / "raced-link").exists()
    else:
        assert (scenario.root / "legacy.json").read_bytes().startswith(b"changed-source")

@pytest.mark.parametrize("kind", ["file", "directory"])
def test_private_final_remove_bypasses_source_namespace_unlink_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    scenario = (
        _scenario(tmp_path)
        if kind == "file"
        else _scenario(tmp_path, files=(), directories=("legacy-dir",))
    )

    original_unlink = deletion_module.os.unlink
    original_rmdir = deletion_module.os.rmdir
    intercepted: list[str] = []
    def attempt_source_swap() -> None:
        replacement = scenario.root / scenario.sources[0].relative_path
        if replacement.exists():
            return
        if kind == "file":
            replacement.write_bytes(b"unrelated-syscall-swap")
        else:
            replacement.mkdir()


    def guarded_unlink(name: object, *args: object, **kwargs: object) -> None:
        if str(name).endswith(".quarantine"):
            attempt_source_swap()
            intercepted.append(str(name))
        original_unlink(name, *args, **kwargs)

    def guarded_rmdir(name: object, *args: object, **kwargs: object) -> None:
        if str(name).endswith(".quarantine"):
            attempt_source_swap()
            intercepted.append(str(name))
        original_rmdir(name, *args, **kwargs)

    monkeypatch.setattr(deletion_module.os, "unlink", guarded_unlink)
    monkeypatch.setattr(deletion_module.os, "rmdir", guarded_rmdir)
    receipt = scenario.guard.delete(scenario.request)
    assert receipt.deleted == (scenario.sources[0].key,)
    assert intercepted == []


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_source_path_replacement_during_fork_handoff_survives_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    scenario = (
        _scenario(tmp_path)
        if kind == "file"
        else _scenario(tmp_path, files=(), directories=("legacy-dir",))
    )
    relative = scenario.sources[0].relative_path
    original_start = scenario.guard._broker.start
    replaced = False

    def replace_then_start() -> tuple[bytes, int]:
        nonlocal replaced
        if not replaced:
            replaced = True
            path = scenario.root / relative
            if kind == "file":
                path.write_bytes(b"unrelated-replacement")
            else:
                path.mkdir()
        return original_start()

    monkeypatch.setattr(scenario.guard._broker, "start", replace_then_start)
    with pytest.raises(SourceDeletionError, match="deleted_source_reappeared"):
        scenario.guard.delete(scenario.request)
    replacement = scenario.root / relative
    assert replacement.exists()
    if kind == "file":
        assert replacement.read_bytes() == b"unrelated-replacement"
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


@pytest.mark.parametrize("race", ["hardlink", "content", "directory_child"])
def test_forked_helper_reverifies_late_link_content_and_directory_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    scenario = (
        _scenario(tmp_path, files=(), directories=("legacy-dir",))
        if race == "directory_child"
        else _scenario(tmp_path)
    )
    original_child = SourceDeletionGuard._forked_helper_child

    def mutate_then_delete(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        capability_digest: bytes,
    ) -> None:
        os.fchmod(capsule_fd, 0o700)
        try:
            if race == "hardlink":
                os.link(
                    "owned",
                    "late-hardlink",
                    src_dir_fd=capsule_fd,
                    dst_dir_fd=capsule_fd,
                )
            elif race == "content":
                descriptor = os.open("owned", os.O_WRONLY, dir_fd=capsule_fd)
                try:
                    os.write(descriptor, b"late-content-mutation")
                finally:
                    os.close(descriptor)
            else:
                directory_fd = os.open(
                    "owned",
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    dir_fd=capsule_fd,
                )
                try:
                    descriptor = os.open(
                        "late-child",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    os.close(descriptor)
                finally:
                    os.close(directory_fd)
        finally:
            os.fchmod(capsule_fd, 0)
        original_child(
            capsule_fd,
            start_fd,
            result_fd,
            request_raw,
            capability_digest,
        )

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_forked_helper_child",
        staticmethod(mutate_then_delete),
    )
    scenario.guard = _restarted_guard(scenario)
    with pytest.raises(
        SourceDeletionError,
        match="isolated_delete_helper_(failed|capsule_invalid)",
    ):
        scenario.guard.delete(scenario.request)
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )
    with pytest.raises(SourceDeletionError, match="source_deletion_operation_blocked"):
        _restarted_guard(scenario).delete(scenario.request)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_parent_namespace_authorities_are_closed_before_final_swap_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    scenario = (
        _scenario(tmp_path)
        if kind == "file"
        else _scenario(tmp_path, files=(), directories=("legacy-dir",))
    )
    original_run = scenario.guard._run_delete_helper
    capsule_ref: _DirFd | None = None
    namespace_refs: tuple[_DirFd, ...] = ()
    swap_blocked = False
    original_start = scenario.guard._broker.start

    def capture_authorities(*args: object, **kwargs: object) -> None:
        nonlocal capsule_ref, namespace_refs
        roots = cast(dict[str, _DirFd], args[1])
        quarantines = cast(dict[str, _DirFd], args[2])
        capsule_ref = cast(_DirFd, args[3])
        namespace_refs = (*roots.values(), *quarantines.values())
        original_run(*args, **kwargs)

    def attempt_final_swap() -> tuple[bytes, int]:
        nonlocal swap_blocked
        assert capsule_ref is not None
        assert capsule_ref.fd == -1
        assert all(owner.fd == -1 for owner in namespace_refs)
        with pytest.raises(OSError):
            os.rename(
                "owned",
                "saved-owned",
                src_dir_fd=capsule_ref.fd,
                dst_dir_fd=capsule_ref.fd,
            )
        swap_blocked = True
        return original_start()

    monkeypatch.setattr(scenario.guard, "_run_delete_helper", capture_authorities)
    monkeypatch.setattr(scenario.guard._broker, "start", attempt_final_swap)
    receipt = scenario.guard.delete(scenario.request)
    assert receipt.deleted == (scenario.sources[0].key,)
    assert swap_blocked


def test_open_file_lease_conflict_refuses_before_removal(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    descriptor = os.open(scenario.root / "legacy.json", os.O_RDONLY)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(SourceDeletionError, match="source_exclusive_lease_conflict"):
            scenario.guard.delete(scenario.request)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert (scenario.root / "legacy.json").exists()


def test_real_store_rejects_nonquarantined_and_wrong_rollback_records(
    tmp_path: Path,
) -> None:
    eligible = _scenario(tmp_path / "eligible", quarantine=False)
    with pytest.raises(
        SourceDeletionError,
        match="source_ownership_authoritative_inventory_mismatch",
    ):
        eligible.guard.delete(eligible.request)
    assert (eligible.root / "legacy.json").exists()

    wrong = _scenario(
        tmp_path / "wrong",
        quarantine=True,
        quarantine_rollback_id="rollback-other",
    )
    with pytest.raises(
        SourceDeletionError,
        match="source_ownership_authoritative_inventory_mismatch",
    ):
        wrong.guard.delete(wrong.request)
    assert (wrong.root / "legacy.json").exists()


def test_store_read_fence_blocks_new_registration_until_receipt_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario(tmp_path)
    entered = threading.Event()
    registered = threading.Event()
    original = scenario.guard._quarantine_and_delete

    def pause_inside_fence(*args: object, **kwargs: object) -> None:
        entered.set()
        time.sleep(0.05)
        original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(scenario.guard, "_quarantine_and_delete", pause_inside_fence)
    extra_ref = ImmutableObjectRef("extra-object", _DIGEST_PREFIX + "e" * 64)
    extra = DependentOwnership(
        "register-extra",
        _TUPLE_DIGEST,
        "episode-1",
        "run-1",
        DependentObjectKind.EVIDENCE,
        extra_ref,
        (),
    )

    def register() -> None:
        entered.wait()
        scenario.store.register(extra)
        registered.set()

    thread = threading.Thread(target=register)
    thread.start()
    receipt = scenario.guard.delete(scenario.request)
    thread.join(timeout=2)
    assert receipt.operation_id == scenario.request.operation_id
    assert registered.is_set()


def test_authentic_cross_subject_subset_extra_reordered_and_superseded_gates_reject(
    tmp_path: Path,
) -> None:
    for name in ("cross", "subset", "extra", "reordered"):
        scenario = _scenario(
            tmp_path / name,
            files=(("first.json", b"first"), ("second.json", b"second")),
            operation_id=f"operation-{name}",
        )
        baseline = tuple(ref.reference for ref in scenario.object_refs)
        subjects = {
            "cross": ("different-first", "different-second"),
            "subset": baseline[:1],
            "extra": (*baseline, "extra-object"),
            "reordered": tuple(reversed(baseline)),
        }[name]
        alternate = scenario.gate_authority.publish(
            "dependent_quarantined",
            subjects,
            make_current=False,
        )
        attacked = replace(
            scenario.request,
            gates=replace(
                scenario.request.gates,
                dependent_quarantine_refs=(alternate,),
            ),
        )
        with pytest.raises(SourceDeletionError, match="authoritative_gate_inventory_not_current"):
            scenario.guard.delete(attacked)
        assert all((scenario.root / source.relative_path).exists() for source in scenario.sources)

    superseded = _scenario(tmp_path / "superseded", operation_id="operation-superseded")
    superseded.gate_authority.publish(
        "active_tuple_restored",
        ("tuple-generation-10",),
        generation=2,
        make_current=True,
    )
    with pytest.raises(SourceDeletionError, match="authoritative_gate_inventory_not_current"):
        superseded.guard.delete(superseded.request)


def test_signed_failed_cleanup_export_or_rerun_outcome_rejects(tmp_path: Path) -> None:
    for gate, outcome in (
        ("episode_terminal", "quarantined_cleanup"),
        ("dependent_quarantined", "quarantined_export_allowed"),
        ("rerun_recorded", "failed_recorded"),
    ):
        scenario = _scenario(tmp_path / gate, operation_id=f"operation-{gate}")
        subjects = scenario.gate_authority._current[gate].subjects
        bad = scenario.gate_authority.publish(
            cast(GateKind, gate), subjects, outcome=outcome, generation=2
        )
        if gate == "episode_terminal":
            gates = replace(scenario.request.gates, episode_terminal_refs=(bad,))
        elif gate == "dependent_quarantined":
            gates = replace(scenario.request.gates, dependent_quarantine_refs=(bad,))
        else:
            gates = replace(scenario.request.gates, rerun_receipt_ref=bad)
        with pytest.raises(SourceDeletionError, match=f"{gate}_receipt_outcome_invalid"):
            scenario.guard.delete(replace(scenario.request, gates=gates))
        assert (scenario.root / "legacy.json").exists()


def test_self_authored_or_other_journal_gate_receipt_rejects(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    ref = scenario.request.gates.rerun_receipt_ref
    document = json.loads(Path(ref.path).read_bytes())
    document["journal_request_digest"] = _DIGEST_PREFIX + "2" * 64
    unsigned = {key: value for key, value in document.items() if key != "signature"}
    document["signature"] = hmac.new(
        b"attacker" * 8, canonical_json_bytes(unsigned), hashlib.sha256
    ).hexdigest()
    raw = canonical_json_bytes(document)
    forged_path = scenario.gate_authority.root / "self-authored.json"
    forged_path.write_bytes(raw)
    forged = SourceDeletionGateReceipt(os.fspath(forged_path), _digest(raw))
    attacked = replace(
        scenario.request,
        gates=replace(scenario.request.gates, rerun_receipt_ref=forged),
    )
    with pytest.raises(SourceDeletionError, match="authoritative_gate_inventory_not_current"):
        scenario.guard.delete(attacked)


@pytest.mark.parametrize(
    "record_suffix",
    [
        ".request.json",
        ".preflight.json",
        ".intent.00000000.json",
        ".intent.00000001.json",
        ".completion.json",
        ".receipt.json",
    ],
)
@pytest.mark.parametrize("boundary", ["after_install", "after_parent_fsync"])
def test_process_death_at_each_atomic_record_boundary_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_suffix: str,
    boundary: str,
) -> None:
    scenario = _scenario(
        tmp_path,
        files=(("a.txt", b"a"), ("b.txt", b"b")),
        operation_id=(
            "process-death-"
            + boundary
            + "-"
            + record_suffix.removeprefix(".").replace(".", "-")
        ),
    )
    original_rename = SourceDeletionGuard._rename_noreplace
    original_parent_fsync = _DirFd.fsync_dir

    if boundary == "after_install":

        def crash_after_install(
            source_fd: int,
            source: str,
            destination_fd: int,
            destination: str,
        ) -> None:
            original_rename(source_fd, source, destination_fd, destination)
            if destination.endswith(record_suffix):
                os._exit(91)

        monkeypatch.setattr(
            SourceDeletionGuard,
            "_rename_noreplace",
            staticmethod(crash_after_install),
        )
    else:

        def crash_after_parent_fsync(owner: _DirFd, relative: str = "") -> None:
            original_parent_fsync(owner, relative)
            if any(name.endswith(record_suffix) for name in os.listdir(owner.fd)):
                os._exit(92)

        monkeypatch.setattr(_DirFd, "fsync_dir", crash_after_parent_fsync)

    process = multiprocessing.get_context("fork").Process(
        target=scenario.guard.delete,
        args=(scenario.request,),
    )
    process.start()
    process.join(10)
    assert process.exitcode == (91 if boundary == "after_install" else 92)
    monkeypatch.undo()

    receipt = _restarted_guard(scenario).delete(scenario.request)
    assert receipt.request_digest == scenario.request.request_digest
    assert all(
        not (scenario.root / source.relative_path).exists()
        for source in scenario.sources
    )
    records = tuple((tmp_path / "deletion-receipts").iterdir())
    assert not any(path.name.endswith(".tmp") for path in records)
    for path in records:
        if path.name.endswith(record_suffix):
            assert path.stat(follow_symlinks=False).st_nlink == 1


@pytest.mark.parametrize("boundary", ["after_install", "after_parent_fsync"])
def test_process_death_installing_blocked_record_remains_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    scenario = _scenario(
        tmp_path,
        operation_id="blocked-process-death-" + boundary,
    )
    original_rename = SourceDeletionGuard._rename_noreplace
    original_parent_fsync = _DirFd.fsync_dir

    def fail_child(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        capability_digest: bytes,
    ) -> None:
        os._exit(1)

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_forked_helper_child",
        staticmethod(fail_child),
    )
    scenario.guard = _restarted_guard(scenario)
    if boundary == "after_install":

        def crash_after_block_install(
            source_fd: int,
            source: str,
            destination_fd: int,
            destination: str,
        ) -> None:
            original_rename(source_fd, source, destination_fd, destination)
            if destination.endswith(".blocked.json"):
                os._exit(93)

        monkeypatch.setattr(
            SourceDeletionGuard,
            "_rename_noreplace",
            staticmethod(crash_after_block_install),
        )
    else:

        def crash_after_block_fsync(owner: _DirFd, relative: str = "") -> None:
            original_parent_fsync(owner, relative)
            if any(name.endswith(".blocked.json") for name in os.listdir(owner.fd)):
                os._exit(94)

        monkeypatch.setattr(_DirFd, "fsync_dir", crash_after_block_fsync)

    process = multiprocessing.get_context("fork").Process(
        target=scenario.guard.delete,
        args=(scenario.request,),
    )
    process.start()
    process.join(10)
    assert process.exitcode == (93 if boundary == "after_install" else 94)
    monkeypatch.undo()

    blocked = next(
        path
        for path in (tmp_path / "deletion-receipts").iterdir()
        if path.name.endswith(".blocked.json")
    )
    assert blocked.stat(follow_symlinks=False).st_nlink == 1
    assert (scenario.root / "legacy.json").exists()
    with pytest.raises(SourceDeletionError, match="source_deletion_operation_blocked"):
        _restarted_guard(scenario).delete(scenario.request)


def test_atomic_record_partial_write_file_fsync_rename_and_parent_fsync_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    injectors = ("write", "file_fsync", "rename", "parent_fsync")
    for injector in injectors:
        scenario = _scenario(tmp_path / injector, operation_id=f"operation-{injector}")
        if injector == "write":
            original = deletion_module.os.write
            failed = False

            def fail_write(fd: int, raw: object) -> int:
                nonlocal failed
                if not failed:
                    failed = True
                    view = bytes(raw)
                    original(fd, view[: max(1, len(view) // 2)])
                    raise OSError("partial write")
                return original(fd, raw)

            monkeypatch.setattr(deletion_module.os, "write", fail_write)
        elif injector == "file_fsync":
            original = deletion_module.os.fsync
            failed = False

            def fail_file_fsync(fd: int) -> None:
                nonlocal failed
                if not failed and stat.S_ISREG(os.fstat(fd).st_mode):
                    failed = True
                    raise OSError("file fsync")
                original(fd)

            monkeypatch.setattr(deletion_module.os, "fsync", fail_file_fsync)
        elif injector == "rename":
            original = SourceDeletionGuard._rename_noreplace
            failed = False

            def fail_rename(
                source_fd: int,
                source: str,
                destination_fd: int,
                destination: str,
            ) -> None:
                nonlocal failed
                if not failed and source.endswith(".tmp"):
                    failed = True
                    raise OSError("rename install")
                original(source_fd, source, destination_fd, destination)

            monkeypatch.setattr(
                SourceDeletionGuard,
                "_rename_noreplace",
                staticmethod(fail_rename),
            )
        else:
            original = _DirFd.fsync_dir
            failed = False

            def fail_parent(owner: _DirFd, relative: str = "") -> None:
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("parent fsync")

                original(owner, relative)

            monkeypatch.setattr(_DirFd, "fsync_dir", fail_parent)
        with pytest.raises(OSError):
            _restarted_guard(scenario).delete(scenario.request)
        monkeypatch.undo()
        receipt = _restarted_guard(scenario).delete(scenario.request)
        assert receipt.request_digest == scenario.request.request_digest
        assert not (scenario.root / "legacy.json").exists()


def test_fork_handoff_uses_only_in_memory_helper_and_fixed_private_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario(tmp_path)
    original_closerange = deletion_module.os.closerange

    def inspect_closerange(first: int, last: int) -> None:
        original_closerange(first, last)
        assert (first, last) == (
            6,
            max(
                6,
                int(os.sysconf("SC_OPEN_MAX")),
                *[
                    int(limit)
                    for limit in resource.getrlimit(resource.RLIMIT_NOFILE)
                    if limit != resource.RLIM_INFINITY
                ],
            ),
        )
        for descriptor in range(6, 64):
            with pytest.raises(OSError):
                os.fstat(descriptor)
        assert stat.S_ISDIR(os.fstat(3).st_mode)
        assert stat.S_ISFIFO(os.fstat(4).st_mode)
        assert stat.S_ISFIFO(os.fstat(5).st_mode)

    monkeypatch.setattr(deletion_module.os, "closerange", inspect_closerange)
    receipt = scenario.guard.delete(scenario.request)
    assert receipt.deleted == (scenario.sources[0].key,)


def test_helper_capability_ignores_module_path_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario(tmp_path)
    assert not hasattr(scenario.guard, "_delete_capsule")
    monkeypatch.setattr(
        deletion_module._deletion_helper,
        "__file__",
        os.fspath(tmp_path / "replaced-helper.py"),
    )
    receipt = scenario.guard.delete(scenario.request)
    assert receipt.deleted == (scenario.sources[0].key,)


@pytest.mark.parametrize(
    "attack",
    ["code", "defaults", "kwdefaults", "closure", "globals", "dependency"],
)
def test_helper_capability_rejects_post_init_semantic_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    scenario = _scenario(tmp_path, operation_id="helper-mutation-" + attack)
    helper = deletion_module._deletion_helper
    if attack == "code":
        monkeypatch.setattr(
            helper.delete_capsule,
            "__code__",
            helper._digest.__code__,
        )
    elif attack == "defaults":
        monkeypatch.setattr(helper.delete_capsule, "__defaults__", (None,))
    elif attack == "kwdefaults":
        monkeypatch.setattr(helper._unlinkat, "__kwdefaults__", {"directory": False})
    elif attack == "closure":
        marker = object()

        def substituted(capsule_fd: int, request_raw: bytes) -> bytes:
            return b"" if marker is not None else request_raw

        monkeypatch.setattr(helper, "delete_capsule", substituted)
    elif attack == "globals":
        monkeypatch.setattr(helper, "_ENTRY_NAME", "mutated-owned")
    else:
        monkeypatch.setattr(helper, "_UNLINKAT", lambda *args: 0)

    with pytest.raises(SourceDeletionError, match="deletion_helper_capability_changed"):
        scenario.guard.delete(scenario.request)
    assert (scenario.root / "legacy.json").read_bytes() == b"legacy-source"


def test_broker_construction_refuses_after_threads_start(
    tmp_path: Path,
) -> None:
    stop = threading.Event()
    started = threading.Event()

    def unrelated_thread() -> None:
        started.set()
        stop.wait(5)

    thread = threading.Thread(target=unrelated_thread)
    thread.start()
    started.wait(5)
    try:
        with pytest.raises(
            SourceDeletionError,
            match="deletion_broker_requires_single_threaded_construction",
        ):
            _scenario(tmp_path, operation_id="broker-after-threads")
    finally:
        stop.set()
        thread.join(5)


@pytest.mark.parametrize("race", ["chmod", "fchmod", "chown", "ctime"])
def test_final_helper_request_rejects_exact_metadata_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    scenario = _scenario(tmp_path, operation_id="metadata-race-" + race)
    original_child = SourceDeletionGuard._forked_helper_child

    def mutate_metadata(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        capability_digest: bytes,
    ) -> None:
        os.fchmod(capsule_fd, 0o700)
        try:
            before = os.stat("owned", dir_fd=capsule_fd, follow_symlinks=False)
            if race == "chmod":
                os.chmod(
                    "owned",
                    stat.S_IMODE(before.st_mode) ^ stat.S_IXUSR,
                    dir_fd=capsule_fd,
                    follow_symlinks=False,
                )
            elif race == "fchmod":
                descriptor = os.open("owned", os.O_RDONLY, dir_fd=capsule_fd)
                try:
                    os.fchmod(
                        descriptor,
                        stat.S_IMODE(before.st_mode) ^ stat.S_IXUSR,
                    )
                finally:
                    os.close(descriptor)
            elif race == "chown":
                os.chown(
                    "owned",
                    before.st_uid,
                    before.st_gid,
                    dir_fd=capsule_fd,
                    follow_symlinks=False,
                )
            else:
                os.utime(
                    "owned",
                    ns=(before.st_atime_ns, before.st_mtime_ns + 1),
                    dir_fd=capsule_fd,
                    follow_symlinks=False,
                )
        finally:
            os.fchmod(capsule_fd, 0)
        original_child(
            capsule_fd,
            start_fd,
            result_fd,
            request_raw,
            capability_digest,
        )

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_forked_helper_child",
        staticmethod(mutate_metadata),
    )
    scenario.guard = _restarted_guard(scenario)
    with pytest.raises(SourceDeletionError, match="isolated_delete_helper_failed"):
        scenario.guard.delete(scenario.request)
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_directory_post_rmdir_replacement_is_preserved_and_blocks_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = deletion_module._deletion_helper
    original_fsync = helper.os.fsync

    def replace_after_unlink(descriptor: int) -> None:
        original_fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o300:
            return
        try:
            os.stat("owned", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            os.mkdir("owned", mode=0o700, dir_fd=descriptor)

    monkeypatch.setattr(helper.os, "fsync", replace_after_unlink)
    scenario = _scenario(
        tmp_path,
        files=(),
        directories=("legacy-dir",),
        operation_id="directory-post-rmdir-replacement",
    )
    with pytest.raises(SourceDeletionError, match="isolated_delete_helper_failed"):
        scenario.guard.delete(scenario.request)

    preserved = False
    for private in scenario.root.iterdir():
        if not private.name.startswith(".bb-g4-private-"):
            continue
        private.chmod(0o700)
        for capsule in private.iterdir():
            capsule.chmod(0o700)
            if (capsule / "owned").is_dir():
                preserved = True
    assert preserved
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


@pytest.mark.skipif(
    deletion_module.sys.platform != "darwin",
    reason="exercises Darwin directory inode-link reporting against Linux policy",
)
def test_directory_terminal_link_count_policy_rejects_wrong_platform_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LinuxPlatform:
        platform = "linux"

    monkeypatch.setattr(
        deletion_module._deletion_helper,
        "sys",
        LinuxPlatform(),
    )
    scenario = _scenario(
        tmp_path,
        files=(),
        directories=("legacy-dir",),
        operation_id="directory-terminal-link-count",
    )
    with pytest.raises(
        SourceDeletionError,
        match="isolated_delete_helper_success_missing_after_delete",
    ):
        scenario.guard.delete(scenario.request)
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_child_fd_ceiling_is_queried_after_runtime_rlimit_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    high_fd = -1
    pipe_read = -1
    pipe_write = -1
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (1024, original_limits[1]))
        scenario = _scenario(tmp_path, operation_id="fresh-child-fd-ceiling")
        resource.setrlimit(resource.RLIMIT_NOFILE, (2048, original_limits[1]))
        pipe_read, pipe_write = os.pipe()
        high_fd = fcntl.fcntl(
            pipe_read,
            getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD),
            1300,
        )
        original_closerange = deletion_module.os.closerange

        def assert_high_fd_closed(first: int, last: int) -> None:
            original_closerange(first, last)
            assert last >= 2048
            with pytest.raises(OSError):
                os.fstat(high_fd)

        monkeypatch.setattr(
            deletion_module.os,
            "closerange",
            assert_high_fd_closed,
        )
        receipt = scenario.guard.delete(scenario.request)
        assert receipt.deleted == (scenario.sources[0].key,)
    finally:
        for descriptor in (high_fd, pipe_read, pipe_write):
            if descriptor >= 0:
                os.close(descriptor)
        resource.setrlimit(resource.RLIMIT_NOFILE, original_limits)


def test_false_helper_result_cannot_claim_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario(tmp_path)
    claimed = canonical_json_bytes(
        {
            "schema_version": "bb.rl.g4.source-deletion-helper-result.v2",
            "status": "deleted",
        }
    )

    def false_success(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        capability_digest: bytes,
    ) -> None:
        assert os.read(start_fd, 2) == b"\x01"
        os.write(result_fd, claimed)
        os._exit(0)

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_forked_helper_child",
        staticmethod(false_success),
    )
    scenario.guard = _restarted_guard(scenario)
    with pytest.raises(SourceDeletionError, match="isolated_delete_helper_protocol_invalid"):
        scenario.guard.delete(scenario.request)
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


@pytest.mark.parametrize("failure", ["oversize", "hang", "signal", "partial"])
def test_fork_result_transport_is_bounded_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    scenario = _scenario(tmp_path, operation_id=f"fork-transport-{failure}")
    monkeypatch.setattr(deletion_module, "_HELPER_TIMEOUT_SECONDS", 0.2)

    def failed_child(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        capability_digest: bytes,
    ) -> None:
        assert os.read(start_fd, 2) == b"\x01"
        if failure == "oversize":
            os.write(result_fd, b"x" * 8192)
            time.sleep(10)
        elif failure == "hang":
            time.sleep(10)
        elif failure == "signal":
            os.kill(os.getpid(), 9)
        else:
            os.write(result_fd, b'{"status":"deleted"')
            os._exit(0)
        os._exit(0)

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_forked_helper_child",
        staticmethod(failed_child),
    )
    scenario.guard = _restarted_guard(scenario)
    with pytest.raises(SourceDeletionError, match="source_deletion_blocked"):
        scenario.guard.delete(scenario.request)
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_restart_after_post_unlink_failure_rejects_legacy_receipt_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario(tmp_path)
    original_proof = scenario.guard._absence_proof
    failed = False

    def fail_proof(*args: object, **kwargs: object) -> object:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("after unlink")
        return original_proof(*args, **kwargs)

    monkeypatch.setattr(scenario.guard, "_absence_proof", fail_proof)
    with pytest.raises(OSError, match="after unlink"):
        scenario.guard.delete(scenario.request)
    assert not (scenario.root / "legacy.json").exists()
    monkeypatch.setattr(scenario.guard, "_absence_proof", original_proof)

    original_write_once = SourceDeletionGuard._write_once
    wrote_prefix = False

    def write_receipt_prefix(owner: _DirFd, name: str, raw: bytes) -> None:
        nonlocal wrote_prefix
        if name.endswith(".receipt.json") and not wrote_prefix:
            wrote_prefix = True
            descriptor = owner.open_file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(descriptor, raw[:17])
            os.fsync(descriptor)
            os.close(descriptor)
            owner.fsync_dir()
            raise OSError("legacy receipt prefix")
        original_write_once(owner, name, raw)

    monkeypatch.setattr(SourceDeletionGuard, "_write_once", staticmethod(write_receipt_prefix))
    with pytest.raises(OSError, match="legacy receipt prefix"):
        _restarted_guard(scenario).delete(scenario.request)
    monkeypatch.setattr(SourceDeletionGuard, "_write_once", original_write_once)
    with pytest.raises(SourceDeletionError, match="deletion_receipt_corrupt"):
        _restarted_guard(scenario).delete(scenario.request)
    assert not (scenario.root / "legacy.json").exists()


@pytest.mark.parametrize("record", ["preflight", "later_intent"])
@pytest.mark.parametrize("fragment", ["empty", "truncated"])
def test_partial_final_records_fail_before_any_multi_source_deletion(
    tmp_path: Path,
    record: str,
    fragment: str,
) -> None:
    scenario = _scenario(
        tmp_path,
        files=(("a.txt", b"a"), ("b.txt", b"b")),
        operation_id=f"strict-final-{record}-{fragment}",
    )
    operation_key = hashlib.sha256(scenario.request.operation_id.encode()).hexdigest()
    if record == "preflight":
        name = f"{operation_key}.preflight.json"
        expected = canonical_json_bytes(
            {
                "owned_source_digests": [
                    _digest(canonical_json_bytes(deletion_module._identity_projection(source)))
                    for source in scenario.request.owned_sources
                ],
                "operation_id": scenario.request.operation_id,
                "request_digest": scenario.request.request_digest,
                "schema_version": "bb.rl.g4.source-deletion-preflight.v2",
            }
        )
        expected_error = "deletion_preflight_conflict"
    else:
        ordered = scenario.guard._ordered_sources(scenario.request)
        expected_intent = scenario.guard._intent_for(
            scenario.request,
            scenario.request.request_digest,
            operation_key,
            1,
            ordered[1],
        )
        name = f"{operation_key}.intent.00000001.json"
        expected = expected_intent.raw
        expected_error = "deletion_intent_corrupt"
    raw = b"" if fragment == "empty" else expected[: max(1, len(expected) // 2)]
    record_path = tmp_path / "deletion-receipts" / name
    record_path.write_bytes(raw)
    record_path.chmod(0o600)

    with pytest.raises(SourceDeletionError, match=expected_error):
        scenario.guard.delete(scenario.request)
    assert (scenario.root / "a.txt").read_bytes() == b"a"
    assert (scenario.root / "b.txt").read_bytes() == b"b"


@pytest.mark.parametrize(
    "record_suffix",
    [
        ".request.json",
        ".preflight.json",
        ".intent.00000000.json",
        ".intent.00000001.json",
        ".completion.json",
        ".receipt.json",
        ".blocked.json",
    ],
)
@pytest.mark.parametrize("boundary", ["create", "partial", "full", "file_fsync"])
def test_sigkill_before_final_record_install_cleans_every_temp_and_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_suffix: str,
    boundary: str,
) -> None:
    scenario = _scenario(
        tmp_path,
        files=(("a.txt", b"a"), ("b.txt", b"b")),
        operation_id=(
            "preinstall-kill-"
            + boundary
            + "-"
            + record_suffix.removeprefix(".").replace(".", "-")
        ),
    )
    original_open = _DirFd.open_file
    original_write = os.write
    original_fsync = os.fsync
    tracked = -1

    def open_and_maybe_kill(
        owner: _DirFd,
        relative: str,
        flags: int,
        mode: int = 0o600,
    ) -> int:
        nonlocal tracked
        descriptor = original_open(owner, relative, flags, mode)
        if (
            relative.startswith(".")
            and f"{record_suffix}." in relative
            and relative.endswith(".tmp")
        ):
            tracked = descriptor
            if boundary == "create":
                os._exit(101)
        return descriptor

    def write_and_maybe_kill(descriptor: int, raw: object) -> int:
        if descriptor != tracked:
            return original_write(descriptor, raw)
        data = bytes(raw)
        if boundary == "partial":
            original_write(descriptor, data[: max(1, len(data) // 2)])
            os._exit(102)
        written = original_write(descriptor, data)
        if boundary == "full":
            os._exit(103)
        return written

    def fsync_and_maybe_kill(descriptor: int) -> None:
        original_fsync(descriptor)
        if descriptor == tracked and boundary == "file_fsync":
            os._exit(104)

    monkeypatch.setattr(_DirFd, "open_file", open_and_maybe_kill)
    monkeypatch.setattr(deletion_module.os, "write", write_and_maybe_kill)
    monkeypatch.setattr(deletion_module.os, "fsync", fsync_and_maybe_kill)
    scenario.guard = _restarted_guard(scenario)
    if record_suffix == ".blocked.json":
        def fail_child(
            capsule_fd: int,
            start_fd: int,
            result_fd: int,
            request_raw: bytes,
            capability_digest: bytes,
        ) -> None:
            os._exit(1)

        monkeypatch.setattr(
            SourceDeletionGuard,
            "_forked_helper_child",
            staticmethod(fail_child),
        )
        scenario.guard = _restarted_guard(scenario)

    process = multiprocessing.get_context("fork").Process(
        target=scenario.guard.delete,
        args=(scenario.request,),
    )
    process.start()
    process.join(15)
    expected_exit = {
        "create": 101,
        "partial": 102,
        "full": 103,
        "file_fsync": 104,
    }[boundary]
    assert process.exitcode == expected_exit
    monkeypatch.undo()

    receipt = _restarted_guard(scenario).delete(scenario.request)
    assert receipt.request_digest == scenario.request.request_digest
    assert all(
        not (scenario.root / source.relative_path).exists()
        for source in scenario.sources
    )
    assert not any(
        path.name.endswith(".tmp")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_authenticated_receipt_replay_rejects_disposition_and_proof_corruption(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    scenario.guard.delete(scenario.request)
    receipt_path = next((tmp_path / "deletion-receipts").glob("*.receipt.json"))
    original = json.loads(receipt_path.read_bytes())
    corruptions = []
    omitted = dict(original)
    omitted["deleted"] = []
    corruptions.append(omitted)
    extra = dict(original)
    extra["deleted"] = [*original["deleted"], "extra:path"]
    corruptions.append(extra)
    duplicate = dict(original)
    duplicate["deleted"] = [*original["deleted"], *original["deleted"]]
    corruptions.append(duplicate)
    overlap = dict(original)
    overlap["already_absent"] = list(original["deleted"])
    corruptions.append(overlap)
    malformed = dict(original)
    malformed["absence_proofs"] = []
    corruptions.append(malformed)

    for index, document in enumerate(corruptions):
        receipt_path.write_bytes(canonical_json_bytes(document))
        with pytest.raises(SourceDeletionError):
            _restarted_guard(scenario).delete(scenario.request)
        if index != len(corruptions) - 1:
            receipt_path.write_bytes(canonical_json_bytes(original))


def test_same_operation_id_with_different_request_digest_conflicts(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    scenario.guard.delete(scenario.request)
    conflicting = replace(
        scenario.request,
        journal_request_digest=_DIGEST_PREFIX + "2" * 64,
    )
    with pytest.raises(SourceDeletionConflict, match="durable_deletion_record_conflict"):
        scenario.guard.delete(conflicting)


@pytest.mark.parametrize(
    "boundary",
    [
        "pre_helper",
        "post_helper",
        "pre_completion",
        "pre_receipt",
        "post_parent_fsync",
    ],
)
def test_gate_supersession_blocks_at_every_destructive_receipt_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    scenario = _scenario(
        tmp_path,
        operation_id=f"gate-supersession-{boundary}",
    )
    entered = threading.Event()
    attempted = threading.Event()
    published = threading.Event()
    rendezvoused = False

    def rendezvous() -> None:
        nonlocal rendezvoused
        if rendezvoused:
            return
        rendezvoused = True
        entered.set()
        assert attempted.wait(5)
        time.sleep(0.05)
        assert not published.is_set()

    if boundary in {"pre_helper", "post_helper"}:
        original_helper = scenario.guard._run_delete_helper

        def helper_boundary(*args: object, **kwargs: object) -> None:
            if boundary == "pre_helper":
                rendezvous()
            original_helper(*args, **kwargs)
            if boundary == "post_helper":
                rendezvous()

        monkeypatch.setattr(scenario.guard, "_run_delete_helper", helper_boundary)
    elif boundary == "pre_completion":
        original_proof = scenario.guard._absence_proof

        def proof_boundary(*args: object, **kwargs: object) -> object:
            rendezvous()
            return original_proof(*args, **kwargs)

        monkeypatch.setattr(scenario.guard, "_absence_proof", proof_boundary)
    elif boundary == "pre_receipt":
        original_sign = scenario.guard._sign_receipt

        def sign_boundary(unsigned: object) -> str:
            rendezvous()
            return original_sign(cast(dict[str, object], unsigned))

        monkeypatch.setattr(scenario.guard, "_sign_receipt", sign_boundary)
    else:
        original_write_once = scenario.guard._write_once

        def receipt_fsync_boundary(owner: _DirFd, name: str, raw: bytes) -> None:
            original_write_once(owner, name, raw)
            if name.endswith(".receipt.json"):
                rendezvous()

        monkeypatch.setattr(scenario.guard, "_write_once", receipt_fsync_boundary)

    def supersede() -> None:
        assert entered.wait(5)
        attempted.set()
        subjects = scenario.gate_authority._current["active_tuple_restored"].subjects
        scenario.gate_authority.publish(
            "active_tuple_restored",
            subjects,
            generation=2,
        )
        published.set()

    publisher = threading.Thread(target=supersede)
    publisher.start()
    receipt = scenario.guard.delete(scenario.request)
    publisher.join(5)
    assert not publisher.is_alive()
    assert published.is_set()
    assert receipt.deleted == (scenario.sources[0].key,)


@pytest.mark.parametrize("guard_scope", ["same_instance", "distinct_instances"])
def test_simultaneous_same_operation_calls_serialize_to_one_receipt(
    tmp_path: Path,
    guard_scope: str,
) -> None:
    scenario = _scenario(
        tmp_path,
        operation_id=f"simultaneous-same-operation-{guard_scope}",
    )
    guards = (
        (scenario.guard, scenario.guard)
        if guard_scope == "same_instance"
        else (scenario.guard, _restarted_guard(scenario))
    )
    barrier = threading.Barrier(3)
    receipts: list[object] = []
    errors: list[str] = []

    def run(guard: SourceDeletionGuard) -> None:
        barrier.wait()
        try:
            receipts.append(guard.delete(scenario.request))
        except BaseException as exc:
            errors.append(f"{exc!r}\n{__import__('traceback').format_exc()}")

    callers = [
        threading.Thread(target=run, args=(guard,))
        for guard in guards
    ]
    for caller in callers:
        caller.start()
    barrier.wait()
    for caller in callers:
        caller.join(10)
    assert all(not caller.is_alive() for caller in callers)
    assert errors == []
    assert len(receipts) == 2
    assert receipts[0] == receipts[1]
    assert not (scenario.root / "legacy.json").exists()


@pytest.mark.parametrize("phase", ["before_rename", "after_rename"])
@pytest.mark.parametrize("race", ["chmod", "fchmod", "chown", "utime"])
def test_metadata_authority_races_across_rename_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    race: str,
) -> None:
    scenario = _scenario(
        tmp_path,
        operation_id=f"rename-metadata-{phase}-{race}",
    )
    original = SourceDeletionGuard._rename_noreplace

    def mutate(parent_fd: int, name: str) -> None:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if race == "chmod":
            os.chmod(
                name,
                stat.S_IMODE(metadata.st_mode) ^ stat.S_IXUSR,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        elif race == "fchmod":
            descriptor = os.open(name, os.O_RDONLY, dir_fd=parent_fd)
            try:
                os.fchmod(
                    descriptor,
                    stat.S_IMODE(metadata.st_mode) ^ stat.S_IXUSR,
                )
            finally:
                os.close(descriptor)
        elif race == "chown":
            alternate_groups = [
                group for group in os.getgroups() if group != metadata.st_gid
            ]
            if not alternate_groups:
                pytest.skip("no alternate authorized group for chown race")
            os.chown(
                name,
                -1,
                alternate_groups[0],
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        else:
            os.utime(
                name,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1),
                dir_fd=parent_fd,
                follow_symlinks=False,
            )

    def raced_rename(
        source_fd: int,
        source: str,
        destination_fd: int,
        destination: str,
    ) -> None:
        if source == "legacy.json" and destination == "owned":
            if phase == "before_rename":
                mutate(source_fd, source)
            original(source_fd, source, destination_fd, destination)
            if phase == "after_rename":
                mutate(destination_fd, destination)
            return
        original(source_fd, source, destination_fd, destination)

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_rename_noreplace",
        staticmethod(raced_rename),
    )
    with pytest.raises(SourceDeletionError, match="source_rename_transition_invalid"):
        scenario.guard.delete(scenario.request)
    assert (scenario.root / "legacy.json").exists()
    assert not any(
        path.name.endswith((".completion.json", ".receipt.json"))
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_real_delete_then_sigkill_before_result_recovers_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = deletion_module._deletion_helper.delete_capsule

    def delete_then_kill(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        capability_digest: bytes,
    ) -> None:
        assert os.read(start_fd, 2) == b"\x01"
        helper(capsule_fd, request_raw)
        os.kill(os.getpid(), 9)

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_forked_helper_child",
        staticmethod(delete_then_kill),
    )
    scenario = _scenario(tmp_path, operation_id="delete-then-sigkill")
    with pytest.raises(SourceDeletionError, match="deletion_helper_result_lost_after_delete"):
        scenario.guard.delete(scenario.request)
    monkeypatch.undo()
    receipt = _restarted_guard(scenario).delete(scenario.request)
    assert receipt.deleted == ()
    assert receipt.already_absent == (scenario.sources[0].key,)
    assert not any(
        path.name.endswith(".blocked.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


@pytest.mark.parametrize("race", ["chmod", "fchmod", "chown", "utime"])
def test_exact_metadata_race_at_final_unlink_is_rejected_and_source_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    helper_module = deletion_module._deletion_helper
    alternate_groups = [
        group for group in os.getgroups() if group != os.getgid()
    ]
    if race == "chown" and not alternate_groups:
        pytest.skip("no alternate authorized group for chown race")
    monkeypatch.setattr(
        helper_module,
        "_TEST_FINAL_UNLINK_RACE",
        race,
        raising=False,
    )
    monkeypatch.setattr(
        helper_module,
        "_TEST_FINAL_UNLINK_GROUP",
        alternate_groups[0] if alternate_groups else os.getgid(),
        raising=False,
    )
    monkeypatch.setattr(
        helper_module,
        "_TEST_ORIGINAL_UNLINKAT",
        helper_module._unlinkat,
        raising=False,
    )
    raced_unlinkat = deletion_module.types.FunctionType(
        _final_unlink_attack.__code__,
        vars(helper_module),
        "_unlinkat",
    )
    monkeypatch.setattr(helper_module, "_unlinkat", raced_unlinkat)
    scenario = _scenario(tmp_path, operation_id=f"final-unlink-{race}")
    with pytest.raises(SourceDeletionError, match="isolated_delete_helper_failed"):
        scenario.guard.delete(scenario.request)
    assert (scenario.root / "legacy.json").exists()
    assert not any(
        path.name.endswith((".completion.json", ".receipt.json"))
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_broker_authenticated_shutdown_is_idempotent_and_reaped(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, operation_id="broker-clean-shutdown")
    broker = scenario.guard._broker
    pid = broker._pid
    broker.close()
    broker.close()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def test_unresponsive_broker_shutdown_is_killed_and_reaped(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, operation_id="broker-stopped-shutdown")
    broker = scenario.guard._broker
    pid = broker._pid
    broker._socket.settimeout(0.05)
    os.kill(pid, getattr(__import__("signal"), "SIGSTOP"))
    broker.close()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def test_broker_repeated_close_does_not_leak_fds_or_zombies() -> None:
    before = len(os.listdir("/dev/fd"))
    for _ in range(4):
        broker = deletion_module._DeletionBroker(
            deletion_module._helper_semantics_digest()
        )
        broker.close()
        broker.close()
    assert len(os.listdir("/dev/fd")) <= before + 2


def test_cross_process_same_operation_serializes_to_one_receipt(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, operation_id="cross-process-same-operation")
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()

    def run() -> None:
        guard = _restarted_guard(scenario)
        start.wait(5)
        try:
            receipt = guard.delete(scenario.request)
            results.put(("ok", receipt.request_digest))
        except BaseException as exc:
            results.put(("error", repr(exc)))

    processes = [context.Process(target=run) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(15)
    assert all(process.exitcode == 0 for process in processes)
    observed = [results.get(timeout=2) for _ in processes]
    assert observed == [("ok", scenario.request.request_digest)] * 2
    receipts = list((tmp_path / "deletion-receipts").glob("*.receipt.json"))
    assert len(receipts) == 1


def test_operation_lock_unlink_recreate_is_rejected_under_root_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario(tmp_path, operation_id="operation-lock-replacement")
    original_flock = deletion_module.fcntl.flock
    operation_key = hashlib.sha256(
        scenario.request.operation_id.encode("utf-8")
    ).hexdigest()
    lock_path = tmp_path / "deletion-receipts" / f"{operation_key}.lock"
    parent_pid = os.getpid()
    armed = True

    def replace_lock(descriptor: int, operation: int) -> object:
        nonlocal armed
        metadata = os.fstat(descriptor)
        if (
            armed
            and os.getpid() == parent_pid
            and stat.S_ISREG(metadata.st_mode)
            and operation & fcntl.LOCK_EX
        ):
            armed = False
            lock_path.unlink()
            replacement = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(replacement)
        return original_flock(descriptor, operation)

    monkeypatch.setattr(deletion_module.fcntl, "flock", replace_lock)
    scenario.guard = _restarted_guard(scenario)
    with pytest.raises(SourceDeletionError, match="deletion_lock_path_substituted"):
        scenario.guard.delete(scenario.request)
    assert (scenario.root / "legacy.json").exists()


def test_forged_recovery_marker_is_rejected(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, operation_id="forged-recovery-marker")
    operation_key = hashlib.sha256(
        scenario.request.operation_id.encode("utf-8")
    ).hexdigest()
    forged = (
        tmp_path
        / "deletion-receipts"
        / f"{operation_key}.recovery.{'0' * 64}.json"
    )
    forged.write_bytes(b"{}")
    with pytest.raises(SourceDeletionError, match="source_recovery_marker_unrecognized"):
        scenario.guard.delete(scenario.request)
    assert (scenario.root / "legacy.json").exists()


def test_stale_recovery_marker_cannot_authorize_ctime_drift(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, operation_id="stale-recovery-marker")
    stale_operation = "0" * 64
    stale = (
        tmp_path
        / "deletion-receipts"
        / f"{stale_operation}.recovery.{'1' * 64}.json"
    )
    stale.write_bytes(b"{}")
    source_path = scenario.root / "legacy.json"
    mode = stat.S_IMODE(source_path.stat(follow_symlinks=False).st_mode)
    source_path.chmod(mode ^ stat.S_IXUSR)
    source_path.chmod(mode)
    with pytest.raises(SourceDeletionError, match="source_identity_or_digest_drift"):
        scenario.guard.delete(scenario.request)
    assert source_path.exists()


def test_valid_recovery_marker_cannot_authorize_wrong_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_child(
        capsule_fd: int,
        start_fd: int,
        result_fd: int,
        request_raw: bytes,
        capability_digest: bytes,
    ) -> None:
        os._exit(1)

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_forked_helper_child",
        staticmethod(fail_child),
    )
    scenario = _scenario(tmp_path, operation_id="recovery-marker-wrong-inode")

    def interrupt_block(*args: object, **kwargs: object) -> None:
        raise SourceDeletionError("injected_after_recovery")

    monkeypatch.setattr(scenario.guard, "_block_operation", interrupt_block)
    with pytest.raises(SourceDeletionError, match="injected_after_recovery"):
        scenario.guard.delete(scenario.request)
    markers = list((tmp_path / "deletion-receipts").glob("*.recovery.*.json"))
    assert len(markers) == 1
    monkeypatch.undo()
    source_path = scenario.root / "legacy.json"
    raw = source_path.read_bytes()
    source_path.unlink()
    source_path.write_bytes(raw)
    with pytest.raises(SourceDeletionError, match="source_inode_drift"):
        _restarted_guard(scenario).delete(scenario.request)
    assert not any(
        path.name.endswith((".completion.json", ".receipt.json"))
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_missing_source_and_replaced_private_capsule_require_signed_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario(tmp_path, operation_id="transition-required-after-rename")
    original_rename = SourceDeletionGuard._rename_noreplace

    def rename_then_exit(
        source_fd: int,
        source: str,
        destination_fd: int,
        destination: str,
    ) -> None:
        original_rename(source_fd, source, destination_fd, destination)
        if destination == "owned":
            os._exit(91)

    monkeypatch.setattr(
        SourceDeletionGuard,
        "_rename_noreplace",
        staticmethod(rename_then_exit),
    )
    process = multiprocessing.get_context("fork").Process(
        target=scenario.guard.delete,
        args=(scenario.request,),
    )
    process.start()
    process.join(15)
    assert process.exitcode == 91
    monkeypatch.undo()

    private_root = next(
        path
        for path in scenario.root.iterdir()
        if path.name.startswith(".bb-g4-private-")
    )
    private_root.chmod(0o700)
    capsule = next(private_root.iterdir())
    escaped_private_root = tmp_path / "escaped-private-root"
    os.rename(private_root, escaped_private_root)
    private_root.mkdir(mode=0o700)
    (private_root / capsule.name).mkdir(mode=0o700)
    private_root.chmod(0)

    with pytest.raises(
        SourceDeletionError,
        match="owned_source_absent_without_helper_success",
    ):
        _restarted_guard(scenario).delete(scenario.request)
    assert (escaped_private_root / capsule.name / "owned").exists()
    assert not any(
        path.name.endswith((".completion.json", ".receipt.json"))
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_directory_moved_outside_capsule_at_final_unlink_cannot_claim_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_module = deletion_module._deletion_helper
    escaped = tmp_path / "escaped-owned-directory"
    monkeypatch.setattr(
        helper_module,
        "_TEST_FINAL_UNLINK_RACE",
        "directory_escape",
        raising=False,
    )
    monkeypatch.setattr(
        helper_module,
        "_TEST_FINAL_UNLINK_DESTINATION",
        os.fspath(escaped),
        raising=False,
    )
    monkeypatch.setattr(
        helper_module,
        "_TEST_ORIGINAL_RAW_UNLINKAT",
        helper_module._UNLINKAT,
        raising=False,
    )
    raced_unlinkat = deletion_module.types.FunctionType(
        _final_raw_unlinkat_directory_escape.__code__,
        vars(helper_module),
        "_UNLINKAT",
    )
    monkeypatch.setattr(helper_module, "_UNLINKAT", raced_unlinkat)
    scenario = _scenario(
        tmp_path,
        files=(),
        directories=("legacy-dir",),
        operation_id="directory-final-unlink-escape",
    )
    original_inode = (scenario.root / "legacy-dir").stat().st_ino

    with pytest.raises(
        SourceDeletionError,
        match=(
            "isolated_delete_helper_failed"
            "|retained_(directory_path_changed|source_link_survived)"
            "|isolated_delete_helper_success_missing_after_delete"
        ),
    ):
        scenario.guard.delete(scenario.request)
    assert escaped.is_dir()
    assert escaped.stat().st_ino == original_inode
    assert not (scenario.root / "legacy-dir").exists()
    assert not any(
        path.name.endswith((".completion.json", ".receipt.json"))
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_successful_helper_result_cannot_override_retained_moved_inode(
    tmp_path: Path,
) -> None:
    scenario = _scenario(
        tmp_path,
        files=(),
        directories=("legacy-dir",),
        operation_id="retained-moved-inode",
    )
    source_path = scenario.root / "legacy-dir"
    descriptor = os.open(source_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        metadata = os.fstat(descriptor)
        expectation = deletion_module._HelperExpectation(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            ctime_ns=metadata.st_ctime_ns,
            atime_ns=metadata.st_atime_ns,
            mode=metadata.st_mode,
            uid=metadata.st_uid,
            gid=metadata.st_gid,
            link_count=metadata.st_nlink,
            mtime_ns=metadata.st_mtime_ns,
            size_bytes=metadata.st_size,
            sha256=scenario.sources[0].sha256,
            kind="directory",
        )
        descriptor_path = scenario.guard._descriptor_path(descriptor)
        escaped = tmp_path / "retained-escaped-directory"
        os.rename(source_path, escaped)
        observed = os.fstat(descriptor)
        success = deletion_module._HelperSuccess(
            source_key=scenario.sources[0].key,
            transition_digest=_DIGEST_PREFIX + "f" * 64,
            name=".success." + "e" * 64 + ".json",
            raw=b"authenticated-success",
        )
        successful_result = canonical_json_bytes(
            {
                "capsule_entries": [],
                "device": str(expectation.device),
                "gid": str(expectation.gid),
                "inode": str(expectation.inode),
                "kind": expectation.kind,
                "link_count": "0",
                "mode": str(expectation.mode),
                "observed_inode_link_count": str(observed.st_nlink),
                "parent_name_absent": True,
                "prior_ctime_ns": str(expectation.ctime_ns),
                "prior_link_count": str(expectation.link_count),
                "schema_version": "bb.rl.g4.source-deletion-helper-result.v2",
                "status": "deleted",
                "success_record_digest": _digest(success.raw),
                "success_record_name": success.name,
                "uid": str(expectation.uid),
            }
        )
        scenario.guard._validate_helper_result(
            successful_result,
            expectation,
            success,
        )
        with pytest.raises(
            SourceDeletionError,
            match="retained_(directory_path_changed|source_link_survived)",
        ):
            scenario.guard._verify_retained_after_helper(
                descriptor,
                descriptor_path,
                expectation,
            )
        assert escaped.stat().st_ino == expectation.inode
    finally:
        os.close(descriptor)


def test_digest_read_may_advance_atime_without_relaxing_mtime_or_ctime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_source_identity = _source_identity
    configured_atimes: list[int] = []

    def source_identity_with_stale_atime(
        root: Path,
        relative: str,
        *,
        authority_id: str = "legacy-source-root",
    ) -> SourceOwnershipIdentity:
        source_path = root / relative
        metadata = source_path.stat(follow_symlinks=False)
        stale_atime = max(
            0,
            min(
                metadata.st_atime_ns,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
            - 86_400_000_000_000,
        )
        configured_atimes.append(stale_atime)
        os.utime(
            source_path,
            ns=(stale_atime, metadata.st_mtime_ns),
            follow_symlinks=False,
        )
        return original_source_identity(
            root,
            relative,
            authority_id=authority_id,
        )

    monkeypatch.setitem(
        _scenario.__globals__,
        "_source_identity",
        source_identity_with_stale_atime,
    )
    scenario = _scenario(tmp_path, operation_id="digest-read-atime")
    before = (scenario.root / "legacy.json").stat(follow_symlinks=False)
    assert before.st_atime_ns > configured_atimes[0]

    receipt = scenario.guard.delete(scenario.request)

    assert receipt.deleted == (scenario.sources[0].key,)


def test_sigkill_after_delete_before_success_token_blocks_empty_capsule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = deletion_module._deletion_helper

    def kill_before_success_token(capsule_fd: int, name: str, raw: bytes) -> None:
        os.kill(os.getpid(), 9)

    monkeypatch.setattr(
        helper,
        "_write_success_record",
        kill_before_success_token,
    )
    scenario = _scenario(tmp_path, operation_id="kill-before-success-token")

    with pytest.raises(
        SourceDeletionError,
        match="isolated_delete_helper_success_missing_after_delete",
    ):
        scenario.guard.delete(scenario.request)

    private_root = next(
        path
        for path in scenario.root.iterdir()
        if path.name.startswith(".bb-g4-private-")
    )
    private_root.chmod(0o700)
    capsule = next(private_root.iterdir())
    capsule.chmod(0o700)
    assert list(capsule.iterdir()) == []
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )
    monkeypatch.undo()
    with pytest.raises(SourceDeletionError, match="source_deletion_operation_blocked"):
        _restarted_guard(scenario).delete(scenario.request)


def test_sigkill_during_success_token_temp_install_never_authenticates_partial_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = deletion_module._deletion_helper

    def partial_token_then_kill(capsule_fd: int, name: str, raw: bytes) -> None:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            name + ".tmp",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow,
            0o600,
            dir_fd=capsule_fd,
        )
        try:
            os.write(descriptor, raw[: max(1, len(raw) // 2)])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.kill(os.getpid(), 9)

    monkeypatch.setattr(
        helper,
        "_write_success_record",
        partial_token_then_kill,
    )
    scenario = _scenario(tmp_path, operation_id="kill-partial-success-token")

    with pytest.raises(
        SourceDeletionError,
        match="isolated_delete_helper_capsule_invalid",
    ):
        scenario.guard.delete(scenario.request)

    private_root = next(
        path
        for path in scenario.root.iterdir()
        if path.name.startswith(".bb-g4-private-")
    )
    private_root.chmod(0o700)
    capsule = next(private_root.iterdir())
    capsule.chmod(0o700)
    entries = list(capsule.iterdir())
    assert len(entries) == 1
    assert entries[0].name.endswith(".tmp")
    assert not any(
        path.name.endswith(".receipt.json")
        for path in (tmp_path / "deletion-receipts").iterdir()
    )


def test_final_legacy_guard_exposes_only_delete_and_no_recursive_delete() -> None:
    module_path = Path(__file__).parents[3] / "breadboard" / "rl" / "phase5" / "g4_source_deletion.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    guard = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SourceDeletionGuard"
    )
    public = {
        node.name
        for node in guard.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    }
    forbidden = {
        node.func.attr
        for node in ast.walk(guard)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"remove_tree", "rmtree", "walk", "rglob", "glob"}
    }
    assert public == {"delete"}
    assert forbidden == set()
