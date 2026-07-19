from __future__ import annotations
from builtins import BaseExceptionGroup

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness import materialization as materialization_module
from breadboard.rl.harness.materialization import (
    CacheLeaseState,
    CleanupState,
    CleanupStepReceipt,
    FilesystemMaterializationStore,
    MaterializationEntry,
    MaterializationKey,
    SourceManifestEntry,
)
from tests.rl.harness.wp7_fixtures import (
    DeterministicRandom,
    FrozenClock,
    MemorySourceReader,
    digest,
    directory_storage,
    independent_digest,
    make_effective_plan,
    make_materialization_plan,
    make_store_roots,
)


def _entry(source_digest: str, target: str = "task") -> MaterializationEntry:
    return MaterializationEntry(
        source_digest=source_digest,
        target_logical_path=target,
        access=c.MountAccess.READ_WRITE,
        max_bytes=4_096,
        role="input",
    )


def _store(
    tmp_path: Path,
    reader: MemorySourceReader,
    clock: FrozenClock,
    *,
    namespace: int = 1,
) -> tuple[FilesystemMaterializationStore, Path, Path]:
    cache_root, workspace_root = make_store_roots(tmp_path)
    return (
        FilesystemMaterializationStore(
            cache_root=cache_root,
            workspace_root=workspace_root,
            source_reader=reader,
            clock=clock,
            lease_ttl=timedelta(minutes=5),
            storage_backend=directory_storage(),
            random_bytes=DeterministicRandom(namespace),
        ),
        cache_root,
        workspace_root,
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_lease_record(
    cache_root: Path,
    key: MaterializationKey,
    *,
    now: str,
    expires_at: str,
    schema_version: str = "bb.rl.cache-lease.v1",
    state: str = "building",
    epoch: int = 4,
) -> Path:
    payload = {
        "schema_version": schema_version,
        "key": key.digest,
        "lease_id": "cache-stale",
        "holder_id": "holder-stale",
        "owner_token": "owner-stale",
        "epoch": epoch,
        "issued_at": now,
        "expires_at": expires_at,
        "state": state,
    }
    envelope = {
        "payload": payload,
        "checksum": digest(_canonical_bytes(payload)),
    }
    path = cache_root / "leases" / key.digest.removeprefix("sha256:")
    path.write_bytes(_canonical_bytes(envelope))
    return path


def _empty_materialized_workspace(
    tmp_path: Path, *, namespace: int = 30_000
) -> tuple[FilesystemMaterializationStore, Any, Path, Path]:
    store, cache_root, workspace_root = _store(
        tmp_path, MemorySourceReader({}), FrozenClock(), namespace=namespace
    )
    workspace = store.materialize(make_materialization_plan(make_effective_plan()))
    return store, workspace, cache_root, workspace_root


def _seal_snapshot(
    store: FilesystemMaterializationStore,
    workspace: Any,
    *,
    max_depth: int,
    max_files: int,
    max_inodes: int,
    max_bytes: int,
) -> tuple[Any, Path]:
    return store.seal_snapshot(
        workspace,
        source_lease_id=workspace.cache_token.lease_id,
        effective_plan_digest=workspace.receipt.effective_plan_digest,
        task_digest=digest("snapshot-task"),
        verifier_digest=digest("snapshot-verifier"),
        max_depth=max_depth,
        max_files=max_files,
        max_inodes=max_inodes,
        max_bytes=max_bytes,
    )


def _opened_target(path: Any, target: Path, dir_fd: int | None) -> bool:
    try:
        rendered = Path(os.fspath(path))
    except TypeError:
        return False
    return rendered == target or (
        dir_fd is not None and len(rendered.parts) == 1 and rendered.name == target.name
    )


class _LeaseRecordHandle:
    def __init__(self, handle: Any, probe: "_LeaseDurabilityProbe", fd: int) -> None:
        self._handle = handle
        self._probe = probe
        self._fd = fd

    def __enter__(self) -> "_LeaseRecordHandle":
        self._handle.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        try:
            return self._handle.__exit__(*args)
        finally:
            self._probe.file_names.pop(self._fd, None)
            self._probe.file_states.pop(self._fd, None)

    def write(self, data: bytes) -> int:
        state = json.loads(data)["payload"]["state"]
        self._probe.events.append(f"{state}-write")
        self._probe.file_states[self._fd] = state
        self._probe.states_by_name[self._probe.file_names[self._fd]] = state
        return self._handle.write(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


class _LeaseDurabilityProbe:
    def __init__(
        self,
        leases: Path,
        *,
        fail_directory_fsync_number: int | None = None,
    ) -> None:
        self.leases = leases
        self.fail_directory_fsync_number = fail_directory_fsync_number
        self.events: list[str] = []
        self.file_names: dict[int, str] = {}
        self.file_states: dict[int, str] = {}
        self.states_by_name: dict[str, str] = {}
        self.directory_fsyncs = 0
        self._os_open = materialization_module.os.open
        self._fdopen = materialization_module.os.fdopen
        self._fsync = materialization_module.os.fsync
        self._replace = materialization_module.os.replace

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(materialization_module.os, "open", self.os_open)
        monkeypatch.setattr(materialization_module.os, "fdopen", self.fdopen)
        monkeypatch.setattr(materialization_module.os, "fsync", self.fsync)
        monkeypatch.setattr(materialization_module.os, "replace", self.replace)

    def _is_leases_fd(self, fd: int) -> bool:
        actual = os.fstat(fd)
        expected = self.leases.stat()
        return stat.S_ISDIR(actual.st_mode) and (
            actual.st_dev,
            actual.st_ino,
        ) == (expected.st_dev, expected.st_ino)

    def os_open(self, path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = self._os_open(path, flags, *args, **kwargs)
        dir_fd = kwargs.get("dir_fd")
        if (
            dir_fd is not None
            and self._is_leases_fd(dir_fd)
            and ".tmp-" in os.fspath(path)
        ):
            self.events.extend(("directory-open", "file-open"))
            self.file_names[fd] = os.fspath(path)
        return fd

    def fdopen(self, fd: int, *args: Any, **kwargs: Any) -> Any:
        handle = self._fdopen(fd, *args, **kwargs)
        if fd in self.file_names:
            return _LeaseRecordHandle(handle, self, fd)
        return handle

    def fsync(self, fd: int) -> None:
        if self._is_leases_fd(fd):
            self.directory_fsyncs += 1
            self.events.append("directory-fsync")
            if self.directory_fsyncs == self.fail_directory_fsync_number:
                raise OSError("cache lease directory durability fault")
        elif fd in self.file_states:
            self.events.append(f"{self.file_states[fd]}-file-fsync")
        self._fsync(fd)

    def replace(self, source: Any, destination: Any, *args: Any, **kwargs: Any) -> None:
        source_name = os.fspath(source)
        state = None
        src_dir_fd = kwargs.get("src_dir_fd")
        dst_dir_fd = kwargs.get("dst_dir_fd")
        if (
            src_dir_fd is not None
            and dst_dir_fd is not None
            and self._is_leases_fd(src_dir_fd)
            and self._is_leases_fd(dst_dir_fd)
            and ".tmp-" in source_name
        ):
            state = self.states_by_name[source_name]
        self._replace(source, destination, *args, **kwargs)
        if state is not None:
            self.events.append(f"{state}-replace")


class RecordingStorageBackend:
    def __init__(self) -> None:
        self._delegate = directory_storage()
        self.release_calls: list[Path] = []
        self.absence_checks: list[Path] = []

    def allocate(self, *, workspace_id: str, root: Path, max_bytes: int) -> Path:
        return self._delegate.allocate(
            workspace_id=workspace_id, root=root, max_bytes=max_bytes
        )

    def measure(self, backing: Path) -> Any:
        return self._delegate.measure(backing)

    def release(self, backing: Path) -> None:
        self.release_calls.append(backing)
        self._delegate.release(backing)

    def verify_absent(self, backing: Path) -> bool:
        self.absence_checks.append(backing)
        return self._delegate.verify_absent(backing)


def _stale_cache_holder_fixture(
    tmp_path: Path,
) -> tuple[
    FilesystemMaterializationStore,
    dict[str, Any],
    Path,
    RecordingStorageBackend,
    Path,
]:
    source_a = digest("stale-source-a")
    source_b = digest("stale-source-b")
    reader = MemorySourceReader(
        {
            source_a: {"a.txt": b"a"},
            source_b: {"b.txt": b"b"},
        }
    )
    cache_root, workspace_root = make_store_roots(tmp_path)
    storage = RecordingStorageBackend()
    store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        source_reader=reader,
        clock=FrozenClock(),
        lease_ttl=timedelta(minutes=5),
        storage_backend=storage,
        random_bytes=DeterministicRandom(40_000),
    )
    plan = make_materialization_plan(
        make_effective_plan(),
        entries=(
            _entry(source_a, "input-a"),
            _entry(source_b, "input-b"),
        ),
    )
    workspace = store.materialize(plan)
    record = {
        "cache_lease_id": workspace.cache_token.lease_id,
        "cache_holder_id": workspace.cache_token.holder_id,
        "cache_token_value": workspace.cache_token.owner_token,
        "cache_epoch": workspace.cache_token.epoch,
        "cache_key_digest": workspace.cache_token.cache_key.digest,
        "cache_manifest_digest": (
            workspace.cache_receipt.immutable_object_manifest_digest
        ),
        "cache_source_digests": [entry.source_digest for entry in plan.entries],
        "workspace_id": workspace.receipt.workspace_id,
        "workspace_path": str(workspace.workspace_path),
        "effective_plan_digest": workspace.receipt.effective_plan_digest,
    }
    record_path = (
        cache_root
        / "leases"
        / workspace.cache_token.cache_key.digest.removeprefix("sha256:")
    )
    storage.release(workspace.workspace_path)
    storage.release_calls.clear()
    return store, record, record_path, storage, cache_root


def test_materialization_key_binds_every_execution_authority_with_independent_oracle() -> (
    None
):
    source_digest = digest("source")
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    baseline = MaterializationKey.from_plan(plan)

    assert baseline.digest == independent_digest(plan.projection())

    mutations = {
        "episode": replace(plan, episode_id="episode-two"),
        "subject": replace(plan, subject_digest=digest("other-subject")),
        "receipt": replace(plan, final_receipt_digest=digest("other-receipt")),
        "effective-plan": replace(
            plan, effective_plan_digest=digest("other-effective-plan")
        ),
        "sandbox": replace(
            plan,
            sandbox_projection={
                **dict(plan.sandbox_projection),
                "runtime_binary_digest": digest("other-runtime"),
            },
        ),
        "task": replace(
            plan,
            task_projection={
                **dict(plan.task_projection),
                "task_binding_digest": digest("other-task"),
            },
        ),
        "setup": replace(
            plan,
            setup_projections=(
                {
                    "schema_version": "bb.rl.setup-plan.v1",
                    "setup_id": "setup-one",
                    "implementation_digest": digest("setup-implementation"),
                    "argv": ["/bin/setup", "--fixed"],
                    "input_digests": [source_digest],
                    "writable_output_subtrees": ["work/setup"],
                    "writable_output_slots": [],
                    "route_ids": [],
                    "secret_handle_ids": [],
                    "timeout_ms": 1_000,
                    "expected_outputs": [],
                },
            ),
        ),
        "source": replace(
            plan,
            entries=(_entry(digest("other-source")),),
        ),
        "mount-access": replace(
            plan,
            entries=(replace(plan.entries[0], access=c.MountAccess.READ_ONLY),),
        ),
        "tools": replace(
            plan,
            tool_bindings=(
                replace(
                    plan.tool_bindings[0],
                    implementation_digest=digest("other-tool"),
                ),
                *plan.tool_bindings[1:],
            ),
        ),
        "resource": replace(
            plan,
            resources_projection={
                **dict(plan.resources_projection),
                "storage_bytes": int(plan.resources_projection["storage_bytes"]) - 1,
            },
        ),
        "limit": replace(
            plan,
            limits_projection={
                **dict(plan.limits_projection),
                "observation_bytes": int(plan.limits_projection["observation_bytes"])
                - 1,
            },
        ),
    }

    assert {
        name: MaterializationKey.from_plan(candidate).digest
        for name, candidate in mutations.items()
    }.keys() == mutations.keys()
    assert all(
        MaterializationKey.from_plan(candidate).digest != baseline.digest
        for candidate in mutations.values()
    )


def test_materialization_plan_is_recursively_immutable() -> None:
    effective_plan = make_effective_plan()
    plan = make_materialization_plan(effective_plan)

    assert isinstance(plan.sandbox_projection, MappingProxyType)
    with pytest.raises((TypeError, AttributeError, FrozenInstanceError)):
        plan.sandbox_projection["runtime_id"] = "mutated"  # type: ignore[index]
    nested_mounts = plan.sandbox_projection["mounts"]
    with pytest.raises((TypeError, AttributeError, FrozenInstanceError)):
        nested_mounts.append({"target_logical_path": "escape"})

    assert plan.projection()["sandbox"] == effective_plan.sandbox.model_dump(
        mode="json"
    )


@pytest.mark.parametrize(
    ("logical_path", "kind"),
    [
        ("/absolute", "file"),
        ("../parent", "file"),
        ("a/../../parent", "file"),
        ("a\\windows", "file"),
        ("nul\x00name", "file"),
        ("./noncanonical", "file"),
        ("symlink", "symlink"),
        ("hardlink", "hardlink"),
        ("device", "device"),
        ("fifo", "fifo"),
    ],
)
def test_hostile_archive_member_shapes_reject_before_store_effects(
    logical_path: str, kind: str
) -> None:
    with pytest.raises(ValueError):
        SourceManifestEntry(
            logical_path=logical_path,
            kind=kind,
            byte_count=1,
            mode=0o644,
            content_digest=digest(b"x"),
        )


@pytest.mark.parametrize(
    "targets",
    [
        ("Task", "task"),
        ("a", "a/b"),
        ("a/b", "a"),
    ],
)
def test_colliding_mount_targets_reject_before_cache_or_workspace_effects(
    tmp_path: Path, targets: tuple[str, str]
) -> None:
    source_a = digest("source-a")
    source_b = digest("source-b")
    reader = MemorySourceReader({source_a: {"a.txt": b"a"}, source_b: {"b.txt": b"b"}})
    _, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())

    entries = tuple(
        sorted(
            (_entry(source_a, targets[0]), _entry(source_b, targets[1])),
            key=lambda entry: (
                entry.target_logical_path,
                entry.source_digest,
                entry.role,
            ),
        )
    )
    with pytest.raises(ValueError, match="mount_collision"):
        make_materialization_plan(make_effective_plan(), entries=entries)

    assert list((cache_root / "leases").iterdir()) == []
    assert list(workspace_root.iterdir()) == []
    assert reader.loads == []


def test_full_ancestor_collision_rejects_before_cache_or_workspace_effects(
    tmp_path: Path,
) -> None:
    source_a = digest("source-a")
    source_a_dash = digest("source-a-dash")
    source_a_child = digest("source-a-child")
    reader = MemorySourceReader(
        {
            source_a: {"a.txt": b"a"},
            source_a_dash: {"a-dash.txt": b"a-dash"},
            source_a_child: {"a-child.txt": b"a-child"},
        }
    )
    _, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    entries = tuple(
        sorted(
            (
                _entry(source_a, "a"),
                _entry(source_a_dash, "a-b"),
                replace(
                    _entry(source_a_child, "a/b"),
                    access=c.MountAccess.READ_ONLY,
                ),
            ),
            key=lambda entry: (
                entry.target_logical_path,
                entry.source_digest,
                entry.role,
            ),
        )
    )

    with pytest.raises(ValueError, match="^mount_collision$"):
        make_materialization_plan(make_effective_plan(), entries=entries)

    assert list((cache_root / "leases").iterdir()) == []
    assert list((cache_root / "objects").iterdir()) == []
    assert list(workspace_root.iterdir()) == []
    assert reader.loads == []


def test_materialization_verifies_source_closure_and_creates_private_episode_workspaces(
    tmp_path: Path,
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader(
        {source_digest: {"answer.txt": b"sealed", "nested/value.txt": b"nested"}}
    )
    store, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )

    first = store.materialize(plan)
    second = store.materialize(replace(plan, episode_id="episode-two"))

    assert first.cache_receipt.acquisition == "built"
    assert second.cache_receipt.acquisition == "built"
    assert first.receipt.workspace_id != second.receipt.workspace_id
    assert first.workspace_path != second.workspace_path
    assert stat.S_IMODE(first.workspace_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(second.workspace_path.stat().st_mode) == 0o700
    (first.workspace_path / "task" / "answer.txt").write_text(
        "episode-one", encoding="utf-8"
    )
    assert (second.workspace_path / "task" / "answer.txt").read_bytes() == b"sealed"
    cached_files = tuple((cache_root / "objects").glob("*/source-0/answer.txt"))
    assert len(cached_files) == 2
    assert all(path.read_bytes() == b"sealed" for path in cached_files)

    first_release = first.close()
    assert first.close() == first_release
    assert first_release.release_state is CacheLeaseState.RELEASED
    assert not first.workspace_path.exists()
    assert second.workspace_path.exists()
    second.close()
    assert list(workspace_root.iterdir()) == []


def test_same_materialization_key_reuses_verified_cache_but_never_mutable_workspace(
    tmp_path: Path,
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, _ = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )

    first = store.materialize(plan)
    second = store.materialize(plan)

    assert first.cache_receipt.acquisition == "built"
    assert second.cache_receipt.acquisition == "hit"
    assert first.receipt.materialization_digest == second.receipt.materialization_digest
    assert first.workspace_path != second.workspace_path
    (first.workspace_path / "task" / "answer.txt").write_bytes(b"mutated")
    assert (second.workspace_path / "task" / "answer.txt").read_bytes() == b"sealed"
    object_file = next((cache_root / "objects").glob("*/source-0/answer.txt"))
    assert object_file.read_bytes() == b"sealed"
    with pytest.raises(RuntimeError, match="^cache_lease_fenced$"):
        first.close()
    second.close()


@pytest.mark.parametrize(
    "poison", ["content", "mode", "missing", "extra", "symlink", "hardlink", "fifo"]
)
def test_cache_hit_revalidates_every_member_and_rejects_poison_before_workspace(
    tmp_path: Path, poison: str
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    built = store.materialize(plan)
    built.close()
    source_root = next((cache_root / "objects").glob("*/source-0"))
    target = source_root / "answer.txt"

    if poison == "content":
        target.write_bytes(b"poison")
    elif poison == "mode":
        target.chmod(0o600)
    elif poison == "missing":
        target.unlink()
    elif poison == "extra":
        (source_root / "extra.txt").write_bytes(b"poison")
    elif poison == "symlink":
        target.unlink()
        target.symlink_to("outside")
    elif poison == "hardlink":
        os.link(target, source_root / "second-link")
    elif poison == "fifo":
        target.unlink()
        os.mkfifo(target)

    with pytest.raises(RuntimeError, match="materialization_tampered"):
        store.materialize(plan)

    assert list(workspace_root.iterdir()) == []


def test_sparse_oversized_cache_member_rejects_before_open_read_or_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    built = store.materialize(plan)
    built.close()
    target = next((cache_root / "objects").glob("*/source-0/answer.txt"))
    os.truncate(target, 1 << 40)
    objects_before = {path.name for path in (cache_root / "objects").iterdir()}
    original_read_bytes = Path.read_bytes
    original_open = os.open

    def guarded_read_bytes(path: Path) -> bytes:
        if path == target:
            raise AssertionError("oversized cache poison was read")
        return original_read_bytes(path)

    def guarded_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if _opened_target(path, target, dir_fd):
            raise AssertionError("oversized cache poison was opened")
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(os, "open", guarded_open)

    with pytest.raises(RuntimeError, match="^materialization_tampered$"):
        store.materialize(plan)

    assert target.stat().st_size == 1 << 40
    assert {path.name for path in (cache_root / "objects").iterdir()} == objects_before
    assert list(workspace_root.iterdir()) == []


@pytest.mark.parametrize("swap", ["symlink", "hardlink", "fifo", "directory"])
def test_cache_hit_rejects_member_swapped_after_walk_before_descriptor_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    built = store.materialize(plan)
    built.close()
    target = next((cache_root / "objects").glob("*/source-0/answer.txt"))
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside-secret")
    original_read_bytes = Path.read_bytes
    original_open = os.open
    swapped = False

    def reject_path_follow(path: Path) -> bytes:
        if path == target:
            raise AssertionError("cache verifier followed a raced pathname")
        return original_read_bytes(path)

    def swap_before_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and _opened_target(path, target, dir_fd):
            target.unlink()
            if swap == "symlink":
                assert flags & getattr(os, "O_NOFOLLOW", 0)
                target.symlink_to(outside)
            elif swap == "hardlink":
                os.link(outside, target)
            elif swap == "fifo":
                assert flags & getattr(os, "O_NONBLOCK", 0)
                os.mkfifo(target)
            else:
                target.mkdir()
            swapped = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", reject_path_follow)
    monkeypatch.setattr(os, "open", swap_before_open)

    with pytest.raises(RuntimeError, match="^materialization_tampered$"):
        store.materialize(plan)

    assert swapped is True
    assert outside.read_bytes() == b"outside-secret"
    assert list(workspace_root.iterdir()) == []


def test_cache_member_swapped_between_hit_verification_and_workspace_copy_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    built = store.materialize(plan)
    built.close()
    target = next((cache_root / "objects").glob("*/source-0/answer.txt"))
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside-secret")
    original_read_bytes = Path.read_bytes
    original_open = os.open
    target_opens = 0
    swapped = False

    def reject_path_copy(path: Path) -> bytes:
        if path == target:
            raise AssertionError("workspace copy followed the cache pathname")
        return original_read_bytes(path)

    def swap_at_copy_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal target_opens, swapped
        if _opened_target(path, target, dir_fd):
            target_opens += 1
            if target_opens == 2:
                assert flags & getattr(os, "O_NOFOLLOW", 0)
                target.unlink()
                target.symlink_to(outside)
                swapped = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", reject_path_copy)
    monkeypatch.setattr(os, "open", swap_at_copy_open)

    with pytest.raises(RuntimeError, match="^materialization_tampered$"):
        store.materialize(plan)

    assert target_opens == 2
    assert swapped is True
    assert outside.read_bytes() == b"outside-secret"
    assert list(workspace_root.iterdir()) == []


def test_cache_lease_publications_fsync_file_replace_and_exact_parent_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_digest = digest("durable-source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, _ = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    probe = _LeaseDurabilityProbe(cache_root / "leases")
    probe.install(monkeypatch)

    workspace = store.materialize(plan)
    release_receipt = workspace.close()

    def transition(state: str) -> list[str]:
        return [
            "directory-open",
            "file-open",
            f"{state}-write",
            f"{state}-file-fsync",
            f"{state}-replace",
            "directory-fsync",
        ]

    assert probe.events == (
        transition(CacheLeaseState.BUILDING.value)
        + transition(CacheLeaseState.ACTIVE.value)
        + transition(CacheLeaseState.RELEASED.value)
    )
    assert release_receipt.release_state is CacheLeaseState.RELEASED


def test_active_lease_directory_fsync_failure_returns_no_workspace_and_retry_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_digest = digest("directory-fsync-fault-source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, _ = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    leases = cache_root / "leases"
    probe = _LeaseDurabilityProbe(leases, fail_directory_fsync_number=2)
    probe.install(monkeypatch)

    with pytest.raises(OSError, match="^cache lease directory durability fault$"):
        store.materialize(plan)

    assert not any(".tmp-" in path.name for path in leases.iterdir())
    probe.fail_directory_fsync_number = None
    recovered = store.materialize(plan)
    assert recovered.cache_receipt.acquisition == "hit"
    recovered.close()


def test_release_directory_fsync_failure_propagates_without_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_digest = digest("release-directory-fsync-fault-source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, _ = _store(tmp_path, reader, FrozenClock())
    workspace = store.materialize(
        make_materialization_plan(
            make_effective_plan(), entries=(_entry(source_digest),)
        )
    )
    leases = cache_root / "leases"
    probe = _LeaseDurabilityProbe(leases, fail_directory_fsync_number=1)
    probe.install(monkeypatch)

    with pytest.raises(OSError, match="^cache lease directory durability fault$"):
        workspace.close()

    released_transition = [
        "directory-open",
        "file-open",
        "released-write",
        "released-file-fsync",
        "released-replace",
        "directory-fsync",
    ]
    assert probe.events == released_transition
    assert not any(".tmp-" in path.name for path in leases.iterdir())
    record_path = leases / workspace.cache_token.cache_key.digest.removeprefix(
        "sha256:"
    )
    assert json.loads(record_path.read_bytes())["payload"]["state"] == "released"

    probe.fail_directory_fsync_number = None
    receipt = workspace.close()

    assert receipt.release_state is CacheLeaseState.RELEASED
    assert probe.events == released_transition * 2


@pytest.mark.parametrize(
    ("record_fault", "failure"),
    [
        ("missing", "cache_lease_fenced"),
        ("binding-mismatch", "cache_lease_fenced"),
        ("corrupt", "cache_record_corrupt"),
    ],
)
def test_release_fails_closed_when_active_record_is_not_exact(
    tmp_path: Path,
    record_fault: str,
    failure: str,
) -> None:
    source_digest = digest(f"release-{record_fault}-source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, _ = _store(tmp_path, reader, FrozenClock())
    workspace = store.materialize(
        make_materialization_plan(
            make_effective_plan(), entries=(_entry(source_digest),)
        )
    )
    record_path = (
        cache_root
        / "leases"
        / workspace.cache_token.cache_key.digest.removeprefix("sha256:")
    )
    if record_fault == "missing":
        record_path.unlink()
    elif record_fault == "corrupt":
        record_path.write_bytes(b"{")
    else:
        envelope = json.loads(record_path.read_bytes())
        envelope["payload"]["owner_token"] = "owner-foreign"
        envelope["checksum"] = digest(_canonical_bytes(envelope["payload"]))
        record_path.write_bytes(_canonical_bytes(envelope))

    with pytest.raises(RuntimeError, match=f"^{failure}$"):
        workspace.close()


def test_release_holds_exact_key_lock_until_released_record_is_durable(
    tmp_path: Path,
) -> None:
    import fcntl
    import multiprocessing

    context = multiprocessing.get_context("fork")
    source_digest = digest("release-key-lock-source")
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, _ = _store(tmp_path, reader, FrozenClock())
    workspace = store.materialize(plan)
    successor_store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=store.workspace_root,
        source_reader=reader,
        clock=FrozenClock(),
        lease_ttl=timedelta(minutes=5),
        storage_backend=directory_storage(),
        random_bytes=DeterministicRandom(90_000),
    )
    release_read = context.Event()
    allow_release_write = context.Event()
    successor_ready = context.Barrier(2)
    successor_attempting_lock = context.Event()
    outcomes = context.Queue()

    def release_worker() -> None:
        original_read = store._read_record

        def blocking_read(path: Path) -> dict[str, Any] | None:
            payload = original_read(path)
            release_read.set()
            if not allow_release_write.wait(timeout=10):
                raise TimeoutError("release write barrier timed out")
            return payload

        store._read_record = blocking_read  # type: ignore[method-assign]
        try:
            receipt = workspace.close()
            outcomes.put(("release", receipt.release_state.value))
        except BaseException as exc:
            outcomes.put(("release-error", type(exc).__name__, str(exc)))

    def successor_worker() -> None:
        real_flock = fcntl.flock

        def recording_flock(fd: int, operation: int) -> Any:
            if operation == fcntl.LOCK_EX:
                successor_attempting_lock.set()
            return real_flock(fd, operation)

        fcntl.flock = recording_flock
        try:
            successor_ready.wait(timeout=10)
            successor = successor_store.materialize(plan)
            outcomes.put(
                (
                    "successor",
                    successor.cache_token.owner_token,
                    successor.cache_token.state.value,
                )
            )
        except BaseException as exc:
            outcomes.put(("successor-error", type(exc).__name__, str(exc)))

    release_process = context.Process(target=release_worker)
    release_process.start()
    assert release_read.wait(timeout=10)

    lock_path = (
        cache_root
        / "leases"
        / (workspace.cache_token.cache_key.digest.removeprefix("sha256:") + ".lock")
    )
    lock_fd = os.open(lock_path, os.O_RDWR)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            release_holds_lock = True
        else:
            release_holds_lock = False
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)

    if not release_holds_lock:
        allow_release_write.set()
        release_process.join(timeout=10)
        assert release_process.exitcode == 0
        pytest.fail("release did not hold the materialization key lock")

    successor_process = context.Process(target=successor_worker)
    successor_process.start()
    successor_ready.wait(timeout=10)
    assert successor_attempting_lock.wait(timeout=10)
    allow_release_write.set()
    release_process.join(timeout=10)
    successor_process.join(timeout=10)

    assert release_process.exitcode == 0
    assert successor_process.exitcode == 0
    observed = {result[0]: result[1:] for result in (outcomes.get(), outcomes.get())}
    assert observed["release"] == (CacheLeaseState.RELEASED.value,)
    successor_owner, successor_state = observed["successor"]
    assert successor_state == CacheLeaseState.ACTIVE.value
    record_path = (
        cache_root
        / "leases"
        / workspace.cache_token.cache_key.digest.removeprefix("sha256:")
    )
    persisted = json.loads(record_path.read_bytes())["payload"]
    assert persisted["state"] == CacheLeaseState.ACTIVE.value
    assert persisted["owner_token"] == successor_owner


def test_partial_source_failure_publishes_no_hit_and_retry_builds_cleanly(
    tmp_path: Path,
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    reader.fail_read = OSError("injected source read fault")

    with pytest.raises(OSError, match="injected source read fault"):
        store.materialize(plan)

    assert list((cache_root / "objects").iterdir()) == []
    assert list((cache_root / "staging").iterdir()) == []
    assert list(workspace_root.iterdir()) == []

    reader.fail_read = None
    recovered = store.materialize(plan)
    assert recovered.cache_receipt.acquisition == "built"
    assert (recovered.workspace_path / "task" / "answer.txt").read_bytes() == b"sealed"
    recovered.close()


def test_unexpired_builder_is_not_stolen_and_exact_expiry_reclaims_with_next_epoch(
    tmp_path: Path,
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    clock = FrozenClock()
    store, cache_root, workspace_root = _store(tmp_path, reader, clock)
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    key = MaterializationKey.from_plan(plan)
    record_path = _write_lease_record(
        cache_root,
        key,
        now=clock.current().isoformat(),
        expires_at=(clock.current() + timedelta(minutes=5)).isoformat(),
    )

    with pytest.raises(RuntimeError, match="cache_lease_busy"):
        store.materialize(plan)
    assert reader.loads == []
    assert list(workspace_root.iterdir()) == []

    clock.advance(minutes=5)
    workspace = store.materialize(plan)
    persisted = json.loads(record_path.read_bytes())["payload"]
    assert workspace.cache_token.epoch == 5
    assert persisted["epoch"] == 5
    assert workspace.cache_receipt.acquisition == "built"
    workspace.close()


@pytest.mark.parametrize(
    "raw_record",
    [
        b"{",
        b"{}",
        _canonical_bytes(
            {
                "payload": {"schema_version": "bb.rl.cache-lease.v999"},
                "checksum": digest(
                    _canonical_bytes({"schema_version": "bb.rl.cache-lease.v999"})
                ),
            }
        ),
    ],
)
def test_corrupt_truncated_or_unknown_lease_record_is_quarantined_without_effects(
    tmp_path: Path, raw_record: bytes
) -> None:
    source_digest = digest("source")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    store, cache_root, workspace_root = _store(tmp_path, reader, FrozenClock())
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    key = MaterializationKey.from_plan(plan)
    record_path = cache_root / "leases" / key.digest.removeprefix("sha256:")
    record_path.write_bytes(raw_record)

    with pytest.raises(RuntimeError, match="cache_record_corrupt"):
        store.materialize(plan)

    assert not record_path.exists()
    assert len(list((cache_root / "quarantine").iterdir())) == 1
    assert list((cache_root / "objects").iterdir()) == []
    assert list(workspace_root.iterdir()) == []
    assert reader.loads == []


def test_concurrent_same_key_build_has_one_publisher_and_waiters_get_private_workspaces(
    tmp_path: Path,
) -> None:
    source_digest = digest("source")
    clock = FrozenClock()
    cache_root, workspace_root = make_store_roots(tmp_path)
    first_entered = Event()
    release_first = Event()
    first_reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    first_reader.load_entered = first_entered
    first_reader.release_load = release_first
    second_reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})
    first_store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        source_reader=first_reader,
        clock=clock,
        lease_ttl=timedelta(minutes=5),
        storage_backend=directory_storage(),
        random_bytes=DeterministicRandom(1),
    )
    second_store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        source_reader=second_reader,
        clock=clock,
        lease_ttl=timedelta(minutes=5),
        storage_backend=directory_storage(),
        random_bytes=DeterministicRandom(10_000),
    )
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        builder = pool.submit(first_store.materialize, plan)
        assert first_entered.wait(timeout=10)
        waiter = pool.submit(second_store.materialize, plan)
        release_first.set()
        first = builder.result(timeout=10)
        second = waiter.result(timeout=10)

    assert {first.cache_receipt.acquisition, second.cache_receipt.acquisition} == {
        "built",
        "hit",
    }
    assert first.receipt.materialization_digest == second.receipt.materialization_digest
    assert first.cache_token.owner_token != second.cache_token.owner_token
    assert first.workspace_path != second.workspace_path
    assert len(list((cache_root / "objects").iterdir())) == 1
    assert len(list(workspace_root.iterdir())) == 2
    assert (first.workspace_path / "task" / "answer.txt").read_bytes() == b"sealed"
    assert (second.workspace_path / "task" / "answer.txt").read_bytes() == b"sealed"
    with pytest.raises(RuntimeError, match="^cache_lease_fenced$"):
        first.close()
    second.close()
    assert list(workspace_root.iterdir()) == []


def test_reclaimed_builder_identity_fences_stale_publication_and_workspace(
    tmp_path: Path,
) -> None:
    source_digest = digest("source")
    clock = FrozenClock()
    cache_root, workspace_root = make_store_roots(tmp_path)
    plan = make_materialization_plan(
        make_effective_plan(), entries=(_entry(source_digest),)
    )
    key = MaterializationKey.from_plan(plan)

    class ReclaimingReader(MemorySourceReader):
        def read_member(
            self, digest_value: str, logical_path: str, *, max_bytes: int
        ) -> bytes:
            content = super().read_member(
                digest_value, logical_path, max_bytes=max_bytes
            )
            _write_lease_record(
                cache_root,
                key,
                now=clock.current().isoformat(),
                expires_at=(clock.current() + timedelta(minutes=5)).isoformat(),
                epoch=2,
            )
            return content

    store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        source_reader=ReclaimingReader({source_digest: {"answer.txt": b"sealed"}}),
        clock=clock,
        lease_ttl=timedelta(minutes=5),
        storage_backend=directory_storage(),
        random_bytes=DeterministicRandom(20_000),
    )

    with pytest.raises(RuntimeError, match="cache_lease_fenced"):
        store.materialize(plan)

    assert list((cache_root / "objects").iterdir()) == []
    assert list((cache_root / "staging").iterdir()) == []
    assert list(workspace_root.iterdir()) == []


def test_snapshot_exact_depth_file_inode_and_byte_boundaries_are_inclusive(
    tmp_path: Path,
) -> None:
    store, workspace, _, _ = _empty_materialized_workspace(tmp_path)
    nested = workspace.workspace_path / "a"
    nested.mkdir()
    (nested / "x").write_bytes(b"ab")
    (workspace.workspace_path / "y").write_bytes(b"cde")

    receipt, snapshot_path = _seal_snapshot(
        store,
        workspace,
        max_depth=1,
        max_files=2,
        max_inodes=3,
        max_bytes=5,
    )

    assert receipt.file_count == 2
    assert receipt.inode_count == 3
    assert receipt.byte_count == 5
    assert (snapshot_path / "a" / "x").read_bytes() == b"ab"
    assert (snapshot_path / "y").read_bytes() == b"cde"
    workspace.close()


@pytest.mark.parametrize(
    ("limit_name", "limits"),
    [
        ("depth", {"max_depth": 0, "max_files": 2, "max_inodes": 3, "max_bytes": 5}),
        ("files", {"max_depth": 1, "max_files": 1, "max_inodes": 3, "max_bytes": 5}),
        ("inodes", {"max_depth": 1, "max_files": 2, "max_inodes": 2, "max_bytes": 5}),
        ("bytes", {"max_depth": 1, "max_files": 2, "max_inodes": 3, "max_bytes": 4}),
    ],
)
def test_snapshot_rejects_one_past_each_exact_budget_before_reading_rejected_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limits: dict[str, int],
) -> None:
    store, workspace, cache_root, _ = _empty_materialized_workspace(tmp_path)
    nested = workspace.workspace_path / "a"
    nested.mkdir()
    deep_file = nested / "x"
    deep_file.write_bytes(b"ab")
    root_file = workspace.workspace_path / "y"
    root_file.write_bytes(b"cde")
    rejected = deep_file if limit_name == "depth" else root_file
    objects_before = {path.name for path in (cache_root / "objects").iterdir()}
    original_read_bytes = Path.read_bytes
    original_open = os.open

    def guard_rejected_read(path: Path) -> bytes:
        if path == rejected:
            raise AssertionError(
                f"{limit_name} budget was checked after reading the rejected file"
            )
        return original_read_bytes(path)

    def guard_rejected_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if _opened_target(path, rejected, dir_fd):
            raise AssertionError(
                f"{limit_name} budget was checked after opening the rejected file"
            )
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", guard_rejected_read)
    monkeypatch.setattr(os, "open", guard_rejected_open)

    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        _seal_snapshot(store, workspace, **limits)

    assert list((cache_root / "staging").iterdir()) == []
    assert {path.name for path in (cache_root / "objects").iterdir()} == objects_before
    workspace.close()


def test_snapshot_directory_only_inode_bomb_rejects_before_copying_excess_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, cache_root, _ = _empty_materialized_workspace(tmp_path)
    for index in range(16):
        (workspace.workspace_path / f"dir-{index:02d}").mkdir()
    rejected_name = "zz-over-budget"
    (workspace.workspace_path / rejected_name).mkdir()
    objects_before = {path.name for path in (cache_root / "objects").iterdir()}
    original_mkdir = Path.mkdir
    original_os_mkdir = os.mkdir

    def guard_excess_copy(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path.name == rejected_name and cache_root / "staging" in path.parents:
            raise AssertionError("over-budget directory was copied before rejection")
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    def guard_excess_os_mkdir(
        path: Any,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if Path(os.fspath(path)).name == rejected_name:
            raise AssertionError("over-budget directory was copied before rejection")
        if dir_fd is None:
            original_os_mkdir(path, mode)
        else:
            original_os_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "mkdir", guard_excess_copy)
    monkeypatch.setattr(os, "mkdir", guard_excess_os_mkdir)

    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        _seal_snapshot(
            store,
            workspace,
            max_depth=0,
            max_files=1,
            max_inodes=16,
            max_bytes=1,
        )

    assert list((cache_root / "staging").iterdir()) == []
    assert {path.name for path in (cache_root / "objects").iterdir()} == objects_before
    workspace.close()


def test_sparse_oversized_snapshot_file_rejects_before_open_read_or_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, cache_root, _ = _empty_materialized_workspace(tmp_path)
    target = workspace.workspace_path / "bomb"
    target.touch()
    os.truncate(target, 1 << 40)
    objects_before = {path.name for path in (cache_root / "objects").iterdir()}
    original_read_bytes = Path.read_bytes
    original_open = os.open

    def guarded_read_bytes(path: Path) -> bytes:
        if path == target:
            raise AssertionError("oversized sparse snapshot member was read")
        return original_read_bytes(path)

    def guarded_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if _opened_target(path, target, dir_fd):
            raise AssertionError("oversized sparse snapshot member was opened")
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(os, "open", guarded_open)

    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        _seal_snapshot(
            store,
            workspace,
            max_depth=0,
            max_files=1,
            max_inodes=1,
            max_bytes=4_096,
        )

    assert target.stat().st_size == 1 << 40
    assert list((cache_root / "staging").iterdir()) == []
    assert {path.name for path in (cache_root / "objects").iterdir()} == objects_before
    workspace.close()


@pytest.mark.parametrize("swap", ["symlink", "fifo"])
def test_snapshot_rejects_symlink_or_special_swap_at_no_follow_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:
    store, workspace, cache_root, _ = _empty_materialized_workspace(tmp_path)
    target = workspace.workspace_path / "candidate"
    target.write_bytes(b"candidate")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside-secret")
    objects_before = {path.name for path in (cache_root / "objects").iterdir()}
    original_read_bytes = Path.read_bytes
    original_open = os.open
    swapped = False

    def reject_path_follow(path: Path) -> bytes:
        if path == target:
            raise AssertionError("snapshotter followed a raced pathname")
        return original_read_bytes(path)

    def swap_before_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and _opened_target(path, target, dir_fd):
            target.unlink()
            if swap == "symlink":
                assert flags & getattr(os, "O_NOFOLLOW", 0)
                target.symlink_to(outside)
            else:
                assert flags & getattr(os, "O_NONBLOCK", 0)
                os.mkfifo(target)
            swapped = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", reject_path_follow)
    monkeypatch.setattr(os, "open", swap_before_open)

    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        _seal_snapshot(
            store,
            workspace,
            max_depth=0,
            max_files=1,
            max_inodes=1,
            max_bytes=32,
        )

    assert swapped is True
    assert outside.read_bytes() == b"outside-secret"
    assert list((cache_root / "staging").iterdir()) == []
    assert {path.name for path in (cache_root / "objects").iterdir()} == objects_before
    workspace.close()


def test_snapshot_verification_rejects_forged_receipt_and_post_seal_mutation(
    tmp_path: Path,
) -> None:
    store, workspace, _, _ = _empty_materialized_workspace(tmp_path)
    source = workspace.workspace_path / "candidate"
    source.write_bytes(b"sealed")
    limits = {
        "max_depth": 0,
        "max_files": 1,
        "max_inodes": 1,
        "max_bytes": 6,
    }
    receipt, snapshot_path = _seal_snapshot(store, workspace, **limits)

    assert store.verify_snapshot(receipt, snapshot_path, **limits) is None

    forged = replace(
        receipt,
        root_digest=digest("forged-root"),
        immutable_storage_object_id="snapshot-object-forged",
    )
    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        store.verify_snapshot(forged, snapshot_path, **limits)

    sealed_member = snapshot_path / "candidate"
    sealed_member.chmod(0o600)
    sealed_member.write_bytes(b"forged")
    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        store.verify_snapshot(receipt, snapshot_path, **limits)

    assert source.read_bytes() == b"sealed"
    workspace.close()


def test_snapshot_copy_authenticates_receipt_and_tree_and_cleans_failed_destination(
    tmp_path: Path,
) -> None:
    store, workspace, _, _ = _empty_materialized_workspace(tmp_path)
    source = workspace.workspace_path / "candidate"
    source.write_bytes(b"sealed")
    limits = {
        "max_depth": 0,
        "max_files": 1,
        "max_inodes": 1,
        "max_bytes": 6,
    }
    receipt, snapshot_path = _seal_snapshot(store, workspace, **limits)

    valid_destination = workspace.workspace_path / "valid-copy"
    assert (
        store.copy_snapshot(receipt, snapshot_path, valid_destination, **limits) is None
    )
    copied_member = valid_destination / "candidate"
    assert copied_member.read_bytes() == b"sealed"
    assert stat.S_IMODE(copied_member.stat().st_mode) == 0o444

    forged_destination = workspace.workspace_path / "forged-copy"
    forged = replace(
        receipt,
        root_digest=digest("forged-root"),
        immutable_storage_object_id="snapshot-object-forged",
    )
    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        store.copy_snapshot(forged, snapshot_path, forged_destination, **limits)
    assert not forged_destination.exists()

    sealed_member = snapshot_path / "candidate"
    sealed_member.chmod(0o644)
    sealed_member.write_bytes(b"forged")
    sealed_member.chmod(0o444)
    tampered_destination = workspace.workspace_path / "tampered-copy"
    with pytest.raises(RuntimeError, match="^snapshot_tampered$"):
        store.copy_snapshot(receipt, snapshot_path, tampered_destination, **limits)

    assert not tampered_destination.exists()
    assert copied_member.read_bytes() == b"sealed"
    assert source.read_bytes() == b"sealed"
    workspace.close()


def test_exact_stale_cache_holder_identity_releases_durably_and_idempotently(
    tmp_path: Path,
) -> None:
    store, record, record_path, storage, _ = _stale_cache_holder_fixture(tmp_path)
    before_payload = json.loads(record_path.read_bytes())["payload"]

    first = store.recover_stale_cache_holder(record)
    second = store.recover_stale_cache_holder(record)

    assert first == CleanupStepReceipt("cache_holder", CleanupState.RELEASED)
    assert second == CleanupStepReceipt("cache_holder", CleanupState.ALREADY_RELEASED)
    after_payload = json.loads(record_path.read_bytes())["payload"]
    assert after_payload == {**before_payload, "state": "released"}
    assert storage.release_calls == []
    assert not Path(record["workspace_path"]).exists()


@pytest.mark.parametrize(
    "mismatch",
    [
        "missing-field",
        "unknown-lease",
        "stale-owner",
        "wrong-holder",
        "wrong-key",
        "wrong-epoch",
        "wrong-path",
        "wrong-workspace",
        "wrong-manifest",
        "wrong-effective-plan",
        "wrong-source",
        "reordered-source",
        "source-tuple",
        "malformed-source",
    ],
)
def test_uncertain_stale_cache_holder_identity_quarantines_without_mutation(
    tmp_path: Path, mismatch: str
) -> None:
    store, record, record_path, storage, cache_root = _stale_cache_holder_fixture(
        tmp_path
    )
    durable_before = record_path.read_bytes()
    objects_before = {path.name for path in (cache_root / "objects").iterdir()}
    candidate = dict(record)
    if mismatch == "missing-field":
        candidate.pop("cache_lease_id")
    elif mismatch == "unknown-lease":
        candidate["cache_lease_id"] = "cache-unknown"
    elif mismatch == "stale-owner":
        candidate["cache_token_value"] = "stale-owner-token"
    elif mismatch == "wrong-holder":
        candidate["cache_holder_id"] = "holder-foreign"
    elif mismatch == "wrong-key":
        candidate["cache_key_digest"] = digest("unknown-cache-key")
    elif mismatch == "wrong-epoch":
        candidate["cache_epoch"] = int(record["cache_epoch"]) + 1
    elif mismatch == "wrong-path":
        candidate["workspace_path"] = str(
            Path(record["workspace_path"]).parent / "workspace-foreign"
        )
    elif mismatch == "wrong-workspace":
        candidate["workspace_id"] = "workspace-foreign"
    elif mismatch == "wrong-manifest":
        candidate["cache_manifest_digest"] = digest("foreign-manifest")
    elif mismatch == "wrong-effective-plan":
        candidate["effective_plan_digest"] = digest("foreign-plan")
    elif mismatch == "wrong-source":
        sources = list(record["cache_source_digests"])
        candidate["cache_source_digests"] = [
            digest("foreign-source"),
            sources[1],
        ]
    elif mismatch == "reordered-source":
        candidate["cache_source_digests"] = list(
            reversed(record["cache_source_digests"])
        )
    elif mismatch == "source-tuple":
        candidate["cache_source_digests"] = tuple(record["cache_source_digests"])
    else:
        candidate["cache_source_digests"] = ["not-a-digest"]

    outcome = store.recover_stale_cache_holder(candidate)

    assert outcome == CleanupStepReceipt(
        "cache_holder",
        CleanupState.QUARANTINED,
        "stale_identity_uncertain",
    )
    assert record_path.read_bytes() == durable_before
    assert {path.name for path in (cache_root / "objects").iterdir()} == objects_before
    assert storage.release_calls == []
    assert not Path(record["workspace_path"]).exists()


def test_stale_cache_holder_with_unreleased_workspace_quarantines_without_deletion(
    tmp_path: Path,
) -> None:
    store, record, record_path, storage, _ = _stale_cache_holder_fixture(tmp_path)
    workspace_path = Path(record["workspace_path"])
    workspace_path.mkdir(mode=0o700)
    marker = workspace_path / "owned-by-live-holder"
    marker.write_bytes(b"retain")
    durable_before = record_path.read_bytes()

    outcome = store.recover_stale_cache_holder(record)

    assert outcome == CleanupStepReceipt(
        "cache_holder",
        CleanupState.QUARANTINED,
        "stale_identity_uncertain",
    )
    assert record_path.read_bytes() == durable_before
    assert marker.read_bytes() == b"retain"
    assert storage.release_calls == []


def test_concurrent_exact_stale_cache_retries_have_one_durable_release_outcome(
    tmp_path: Path,
) -> None:
    store, record, record_path, storage, _ = _stale_cache_holder_fixture(tmp_path)
    barrier = Barrier(3)

    def recover() -> CleanupStepReceipt:
        barrier.wait(timeout=10)
        return store.recover_stale_cache_holder(record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recover)
        second = pool.submit(recover)
        barrier.wait(timeout=10)
        outcomes = (first.result(timeout=10), second.result(timeout=10))

    assert {outcome.state for outcome in outcomes} == {
        CleanupState.RELEASED,
        CleanupState.ALREADY_RELEASED,
    }
    assert all(outcome.resource == "cache_holder" for outcome in outcomes)
    assert json.loads(record_path.read_bytes())["payload"]["state"] == "released"
    assert storage.release_calls == []
    assert not Path(record["workspace_path"]).exists()


def test_descriptor_roots_keep_constructor_mutations_on_admitted_inodes(
    tmp_path: Path,
) -> None:
    cache_root, workspace_root = make_store_roots(tmp_path)
    admitted_cache = tmp_path / "admitted-cache"
    admitted_workspace = tmp_path / "admitted-workspace"
    replacement_cache = tmp_path / "replacement-cache"
    replacement_workspace = tmp_path / "replacement-workspace"

    def swap_roots() -> None:
        cache_root.rename(admitted_cache)
        workspace_root.rename(admitted_workspace)
        replacement_cache.mkdir(mode=0o700)
        replacement_workspace.mkdir(mode=0o700)
        replacement_cache.rename(cache_root)
        replacement_workspace.rename(workspace_root)

    FilesystemMaterializationStore._authority_admitted_hook = swap_roots
    store: FilesystemMaterializationStore | None = None
    try:
        store = FilesystemMaterializationStore(
            cache_root=cache_root,
            workspace_root=workspace_root,
            source_reader=MemorySourceReader({}),
            clock=FrozenClock(),
            lease_ttl=timedelta(minutes=5),
            storage_backend=directory_storage(),
            random_bytes=DeterministicRandom(90),
        )
        assert set(os.listdir(store._cache_root_fd)) == {
            "leases",
            "objects",
            "quarantine",
            "staging",
        }
        assert list(cache_root.iterdir()) == []
        assert list(workspace_root.iterdir()) == []
    finally:
        FilesystemMaterializationStore._authority_admitted_hook = None
        if store is not None:
            store.close()


def test_descriptor_roots_reject_same_inode_alias_and_close_duplicates(
    tmp_path: Path,
) -> None:
    cache_root, workspace_root = make_store_roots(tmp_path)
    cache_fd = os.open(cache_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    workspace_fd = os.open(workspace_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        cache_root_fd=cache_fd,
        workspace_root_fd=workspace_fd,
        source_reader=MemorySourceReader({}),
        clock=FrozenClock(),
        lease_ttl=timedelta(minutes=5),
        storage_backend=directory_storage(),
        random_bytes=DeterministicRandom(91),
    )
    owned = (store._cache_root_fd, store._workspace_root_fd)
    store.close()
    store.close()
    for descriptor in owned:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    os.fstat(cache_fd)
    os.fstat(workspace_fd)
    os.close(cache_fd)
    os.close(workspace_fd)

    alias_fd = os.open(cache_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError, match="roots must differ"):
            FilesystemMaterializationStore(
                cache_root=cache_root,
                workspace_root=cache_root,
                cache_root_fd=alias_fd,
                workspace_root_fd=alias_fd,
                source_reader=MemorySourceReader({}),
                clock=FrozenClock(),
                lease_ttl=timedelta(minutes=5),
                storage_backend=directory_storage(),
            )
        os.fstat(alias_fd)
    finally:
        os.close(alias_fd)


def test_descriptor_roots_keep_full_lifecycle_on_admitted_inodes_after_swap(
    tmp_path: Path,
) -> None:
    cache_root, workspace_root = make_store_roots(tmp_path)
    admitted_cache = tmp_path / "admitted-cache"
    admitted_workspace = tmp_path / "admitted-workspace"
    source_digest = digest("descriptor-root-swap")
    reader = MemorySourceReader({source_digest: {"answer.txt": b"sealed"}})

    def swap_roots() -> None:
        cache_root.rename(admitted_cache)
        workspace_root.rename(admitted_workspace)
        cache_root.mkdir(mode=0o700)
        workspace_root.mkdir(mode=0o700)

    FilesystemMaterializationStore._authority_admitted_hook = swap_roots
    store: FilesystemMaterializationStore | None = None
    try:
        store = FilesystemMaterializationStore(
            cache_root=cache_root,
            workspace_root=workspace_root,
            source_reader=reader,
            clock=FrozenClock(),
            lease_ttl=timedelta(minutes=5),
            storage_backend=directory_storage(),
            random_bytes=DeterministicRandom(92),
        )
        workspace = store.materialize(
            make_materialization_plan(
                make_effective_plan(), entries=(_entry(source_digest),)
            )
        )
        admitted_workspace_path = admitted_workspace / workspace.receipt.workspace_id
        assert (
            admitted_workspace_path / "task" / "answer.txt"
        ).read_bytes() == b"sealed"
        assert list(cache_root.iterdir()) == []
        assert list(workspace_root.iterdir()) == []
        assert any((admitted_cache / "objects").iterdir())
        workspace.close()
        assert not admitted_workspace_path.exists()
        assert list(cache_root.iterdir()) == []
        assert list(workspace_root.iterdir()) == []
    finally:
        FilesystemMaterializationStore._authority_admitted_hook = None
        if store is not None:
            store.close()


def test_constructor_fstat_failure_closes_exact_duplicates_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_root, workspace_root = make_store_roots(tmp_path)
    cache_fd = os.open(cache_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    workspace_fd = os.open(workspace_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original_dup = os.dup
    original_fstat = os.fstat
    duplicates: list[int] = []

    def tracked_dup(descriptor: int) -> int:
        duplicate = original_dup(descriptor)
        duplicates.append(duplicate)
        return duplicate

    def fail_second_owned_fstat(descriptor: int) -> os.stat_result:
        if len(duplicates) >= 2 and descriptor == duplicates[1]:
            raise OSError("injected workspace descriptor stat failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(materialization_module.os, "dup", tracked_dup)
    monkeypatch.setattr(materialization_module.os, "fstat", fail_second_owned_fstat)
    with pytest.raises(OSError, match="^injected workspace descriptor stat failure$"):
        FilesystemMaterializationStore(
            cache_root=cache_root,
            workspace_root=workspace_root,
            cache_root_fd=cache_fd,
            workspace_root_fd=workspace_fd,
            source_reader=MemorySourceReader({}),
            clock=FrozenClock(),
            lease_ttl=timedelta(minutes=5),
            storage_backend=directory_storage(),
        )
    monkeypatch.setattr(materialization_module.os, "fstat", original_fstat)
    assert len(duplicates) == 2
    for descriptor in duplicates:
        with pytest.raises(OSError):
            original_fstat(descriptor)
    original_fstat(cache_fd)
    original_fstat(workspace_fd)
    os.close(cache_fd)
    os.close(workspace_fd)


def test_snapshot_copy_after_workspace_root_swap_targets_pinned_workspace(
    tmp_path: Path,
) -> None:
    store, workspace, _, workspace_root = _empty_materialized_workspace(tmp_path)
    source = workspace.workspace_path / "candidate"
    source.write_bytes(b"sealed")
    limits = {
        "max_depth": 0,
        "max_files": 1,
        "max_inodes": 1,
        "max_bytes": 6,
    }
    receipt, snapshot_path = _seal_snapshot(store, workspace, **limits)
    admitted_workspace_root = tmp_path / "admitted-workspaces"
    workspace_root.rename(admitted_workspace_root)
    workspace_root.mkdir(mode=0o700)
    destination = workspace.workspace_path / "verified-copy"

    store.copy_snapshot(receipt, snapshot_path, destination, **limits)

    admitted_copy = (
        admitted_workspace_root
        / workspace.receipt.workspace_id
        / "verified-copy"
        / "candidate"
    )
    assert admitted_copy.read_bytes() == b"sealed"
    assert list(workspace_root.iterdir()) == []
    workspace.close()


def test_pre_mounted_quota_release_failure_is_retryable_and_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspaces"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    capacity = os.fstatvfs(root_fd).f_blocks * os.fstatvfs(root_fd).f_frsize

    class Authority:
        path = root
        max_bytes = capacity
        mount_id = 77
        mounted_identity = materialization_module._stable_directory_identity(
            os.fstat(root_fd)
        )

        @staticmethod
        def verify() -> None:
            pass

    backend = materialization_module.PreMountedTmpfsQuotaStorageBackend(Authority())
    backend.bind_root(root_fd)
    os.close(root_fd)
    first = backend.allocate(
        workspace_id="first",
        root=root,
        max_bytes=capacity,
    )
    second = backend.allocate(
        workspace_id="second",
        root=root,
        max_bytes=capacity,
    )
    original_remove_tree = materialization_module._DirFd.remove_tree
    rejected = False

    def fail_first_release(
        owner: materialization_module._DirFd,
        relative: str,
    ) -> None:
        nonlocal rejected
        if relative == "first" and not rejected:
            rejected = True
            raise OSError("injected release failure")
        original_remove_tree(owner, relative)

    monkeypatch.setattr(
        materialization_module._DirFd,
        "remove_tree",
        fail_first_release,
    )
    with pytest.raises(OSError, match="injected release failure"):
        backend.release(first)
    assert backend.measure(first)["quota_bytes"] == capacity
    assert backend.measure(second)["quota_bytes"] == capacity

    backend.release(first)
    assert backend.measure(second)["quota_bytes"] == capacity
    backend.release(second)
    backend.close_root()
    assert list(root.iterdir()) == []


def test_pre_mounted_quota_allocation_failure_removes_new_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspaces"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    capacity = os.fstatvfs(root_fd).f_blocks * os.fstatvfs(root_fd).f_frsize

    class Authority:
        path = root
        max_bytes = capacity
        mount_id = 78
        mounted_identity = materialization_module._stable_directory_identity(
            os.fstat(root_fd)
        )

        @staticmethod
        def verify() -> None:
            pass

    backend = materialization_module.PreMountedTmpfsQuotaStorageBackend(Authority())
    backend.bind_root(root_fd)
    os.close(root_fd)
    monkeypatch.setattr(
        materialization_module.os,
        "fstatvfs",
        lambda _fd: (_ for _ in ()).throw(OSError("injected statvfs failure")),
    )
    with pytest.raises(OSError, match="injected statvfs failure"):
        backend.allocate(
            workspace_id="failed",
            root=root,
            max_bytes=capacity,
        )
    assert list(root.iterdir()) == []
    assert backend._mounts == {}
    backend.close_root()


def test_tmpfs_unmount_uses_detach_only_when_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, int]] = []

    class Umount:
        argtypes: object = None
        restype: object = None

        def __call__(self, target: bytes, flags: int) -> int:
            calls.append((target, flags))
            return 0

    libc = SimpleNamespace(umount2=Umount())
    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_libc",
        staticmethod(lambda: libc),
    )
    materialization_module.TmpfsQuotaStorageBackend._unmount_tmpfs("/workspace")
    materialization_module.TmpfsQuotaStorageBackend._unmount_tmpfs(
        "/proc/self/fd/7", detach=True
    )
    assert calls == [
        (b"/workspace", 0),
        (
            b"/proc/self/fd/7",
            materialization_module.TmpfsQuotaStorageBackend._MNT_DETACH,
        ),
    ]


def test_tmpfs_quota_root_rejects_path_substitution_without_unmounting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "quota-root"
    root.mkdir(mode=0o700)
    page_size = os.sysconf("SC_PAGE_SIZE")
    capacity = 2 * page_size
    filesystem = SimpleNamespace(f_blocks=2, f_frsize=page_size)
    unmounted: list[tuple[str, bool]] = []

    def record(path: str) -> materialization_module._TmpfsMountRecord:
        metadata = os.stat(path, follow_symlinks=False)
        return materialization_module._TmpfsMountRecord(
            mount_id=79,
            device=(f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}").encode(
                "ascii"
            ),
            root=b"/",
            mount_options=frozenset({b"rw", b"nosuid", b"nodev"}),
            filesystem_type=b"tmpfs",
            source=b"breadboard-workspace",
            super_options=frozenset({b"rw", b"size=8k"}),
        )

    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_mount_tmpfs",
        classmethod(lambda _cls, _target, _size: None),
    )
    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_unmount_tmpfs",
        classmethod(
            lambda _cls, target, *, detach=False: unmounted.append((target, detach))
        ),
    )
    monkeypatch.setattr(
        materialization_module,
        "_tmpfs_mount_record",
        record,
    )
    monkeypatch.setattr(
        materialization_module,
        "_mount_id_present",
        lambda _mount_id: False,
    )
    monkeypatch.setattr(
        materialization_module.os,
        "fstatvfs",
        lambda _fd: filesystem,
    )
    monkeypatch.setattr(
        materialization_module.os,
        "statvfs",
        lambda _path: filesystem,
    )

    authority = materialization_module.TmpfsQuotaRootAuthority(
        root,
        capacity,
    )
    authority.mount()
    covered = tmp_path / "covered"
    root.rename(covered)
    root.mkdir(mode=0o700)

    with pytest.raises(RuntimeError, match="authority changed"):
        authority.verify()
    with pytest.raises(BaseExceptionGroup, match="verification and cleanup"):
        authority.close()

    assert len(unmounted) == 1
    assert unmounted[0][0].startswith("/proc/self/fd/")
    assert unmounted[0][1] is True
    assert root.is_dir()
    assert authority._mounted is False
    root.rmdir()
    covered.rename(root)


def test_tmpfs_quota_mount_failure_retains_retryable_cleanup_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "quota-root"
    root.mkdir(mode=0o700)
    capacity = 2 * os.sysconf("SC_PAGE_SIZE")
    authority = materialization_module.TmpfsQuotaRootAuthority(
        root,
        capacity,
    )
    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_mount_tmpfs",
        classmethod(lambda _cls, _target, _size: None),
    )
    monkeypatch.setattr(
        authority,
        "_capture",
        lambda: (_ for _ in ()).throw(RuntimeError("capture failed")),
    )
    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_unmount_tmpfs",
        classmethod(
            lambda _cls, _target, *, detach=False: (_ for _ in ()).throw(
                OSError("unmount failed")
            )
        ),
    )

    with pytest.raises(BaseExceptionGroup) as raised:
        authority.mount()
    assert [str(item) for item in raised.value.exceptions] == [
        "capture failed",
        "unmount failed",
    ]
    assert authority._mounted is True

    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_unmount_tmpfs",
        classmethod(lambda _cls, _target, *, detach=False: None),
    )
    with pytest.raises(RuntimeError, match="authority is not mounted"):
        authority.close()
    assert authority._mounted is False


@pytest.mark.parametrize("swap", ["final", "ancestor"])
def test_tmpfs_quota_root_rejects_pre_mount_path_swap(
    tmp_path: Path,
    swap: str,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    root = parent / "quota-root"
    root.mkdir(mode=0o700)
    authority = materialization_module.TmpfsQuotaRootAuthority(
        root,
        2 * os.sysconf("SC_PAGE_SIZE"),
    )
    if swap == "final":
        root.rename(parent / "covered")
        root.mkdir(mode=0o700)
    else:
        parent.rename(tmp_path / "covered-parent")
        parent.mkdir(mode=0o700)
        root.mkdir(mode=0o700)

    with pytest.raises(
        RuntimeError,
        match="covered root authority changed",
    ):
        authority.mount()
    authority.close()


def test_pre_mounted_quota_allocation_cleanup_retries_same_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspaces"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    original_fstatvfs = materialization_module.os.fstatvfs
    capacity = original_fstatvfs(root_fd).f_blocks * original_fstatvfs(root_fd).f_frsize

    class Authority:
        path = root
        max_bytes = capacity
        mount_id = 80
        mounted_identity = materialization_module._stable_directory_identity(
            os.fstat(root_fd)
        )

        @staticmethod
        def verify() -> None:
            pass

    backend = materialization_module.PreMountedTmpfsQuotaStorageBackend(Authority())
    backend.bind_root(root_fd)
    os.close(root_fd)
    original_remove_tree = materialization_module._DirFd.remove_tree
    stat_failed = False
    cleanup_failed = False

    def fail_measure_once(fd: int) -> os.statvfs_result:
        nonlocal stat_failed
        if not stat_failed:
            stat_failed = True
            raise OSError("injected allocation measurement failure")
        return original_fstatvfs(fd)

    def fail_cleanup_once(
        owner: materialization_module._DirFd,
        relative: str,
    ) -> None:
        nonlocal cleanup_failed
        if relative == "retry" and not cleanup_failed:
            cleanup_failed = True
            raise OSError("injected allocation cleanup failure")
        original_remove_tree(owner, relative)

    monkeypatch.setattr(
        materialization_module.os,
        "fstatvfs",
        fail_measure_once,
    )
    monkeypatch.setattr(
        materialization_module._DirFd,
        "remove_tree",
        fail_cleanup_once,
    )
    with pytest.raises(BaseExceptionGroup) as raised:
        backend.allocate(
            workspace_id="retry",
            root=root,
            max_bytes=capacity,
        )
    assert [str(item) for item in raised.value.exceptions] == [
        "injected allocation measurement failure",
        "injected allocation cleanup failure",
    ]
    assert (root / "retry").is_dir()
    assert backend._pending_cleanup == {"retry"}

    backing = backend.allocate(
        workspace_id="retry",
        root=root,
        max_bytes=capacity,
    )
    assert backend._pending_cleanup == set()
    backend.release(backing)
    backend.close_root()
    assert list(root.iterdir()) == []


def test_pre_mounted_quota_release_retries_post_rmdir_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspaces"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    capacity = os.fstatvfs(root_fd).f_blocks * os.fstatvfs(root_fd).f_frsize
    root_identity = (
        os.fstat(root_fd).st_dev,
        os.fstat(root_fd).st_ino,
    )

    class Authority:
        path = root
        max_bytes = capacity
        mount_id = 81
        mounted_identity = materialization_module._stable_directory_identity(
            os.fstat(root_fd)
        )

        @staticmethod
        def verify() -> None:
            pass

    backend = materialization_module.PreMountedTmpfsQuotaStorageBackend(Authority())
    backend.bind_root(root_fd)
    os.close(root_fd)
    backing = backend.allocate(
        workspace_id="durable",
        root=root,
        max_bytes=capacity,
    )
    original_fsync = materialization_module.os.fsync
    failed = False

    def fail_parent_fsync_once(fd: int) -> None:
        nonlocal failed
        metadata = os.fstat(fd)
        if (
            not failed
            and (metadata.st_dev, metadata.st_ino) == root_identity
            and not backing.exists()
        ):
            failed = True
            raise OSError("injected parent fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(
        materialization_module.os,
        "fsync",
        fail_parent_fsync_once,
    )
    with pytest.raises(OSError, match="injected parent fsync failure"):
        backend.release(backing)
    assert not backing.exists()
    assert "durable" in backend._mounts

    backend.release(backing)
    assert "durable" not in backend._mounts
    backend.close_root()


def test_tmpfs_quota_mount_path_race_unmounts_only_covered_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "quota-root"
    root.mkdir(mode=0o700)
    capacity = 2 * os.sysconf("SC_PAGE_SIZE")
    authority = materialization_module.TmpfsQuotaRootAuthority(
        root,
        capacity,
    )
    unmounted: list[tuple[str, bool]] = []

    def swap_during_mount(_target: str, _size: int) -> None:
        root.rename(tmp_path / "covered")
        root.mkdir(mode=0o700)

    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_mount_tmpfs",
        classmethod(lambda _cls, target, size: swap_during_mount(target, size)),
    )
    monkeypatch.setattr(
        materialization_module.TmpfsQuotaStorageBackend,
        "_unmount_tmpfs",
        classmethod(
            lambda _cls, target, *, detach=False: unmounted.append((target, detach))
        ),
    )
    monkeypatch.setattr(
        materialization_module,
        "_tmpfs_mount_record",
        lambda _path: (_ for _ in ()).throw(
            RuntimeError("mounted path was substituted")
        ),
    )

    with pytest.raises(BaseExceptionGroup) as raised:
        authority.mount()

    assert str(raised.value.exceptions[0]) == "mounted path was substituted"
    assert len(unmounted) == 1
    assert unmounted[0][0].startswith("/proc/self/fd/")
    assert unmounted[0][1] is True
    assert all(target != os.fspath(root) for target, _detach in unmounted)
    assert authority._mounted is False
    assert authority._covered_descriptor == -1
