from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import breadboard.artifacts.cas as cas_module
from breadboard.artifacts import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactRef,
    ArtifactStoreError,
    CASReader,
    FilesystemCAS,
    InMemoryCAS,
)


def _record_path(cas: FilesystemCAS, artifact_id: str) -> Path:
    key = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
    return cas.records / f"{key}.json"


@pytest.fixture(params=["memory", "filesystem"])
def cas(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    if request.param == "memory":
        yield InMemoryCAS()
    else:
        yield FilesystemCAS(tmp_path / "cas")


def test_cas_refs_are_hash_addressed_and_retrievable(cas: Any) -> None:
    ref = cas.put_bytes(b"hello", media_type="text/plain")

    assert ref.sha256.startswith("sha256:")
    assert ref.size_bytes == 5
    assert cas.has(ref)
    assert cas.get_bytes(ref) == b"hello"


def test_cas_rejects_overwrite_for_existing_artifact_id(cas: Any) -> None:
    cas.put_bytes(b"first", artifact_id="artifact-1")

    with pytest.raises(ArtifactConflictError, match="overwrite rejected"):
        cas.put_bytes(b"second", artifact_id="artifact-1")


@pytest.mark.parametrize(
    "changed",
    [
        {"media_type": "application/x-rebound", "metadata": {"owner": "original"}},
        {"media_type": "text/plain", "metadata": {"owner": "rebound"}},
    ],
    ids=["media-type", "metadata"],
)
def test_same_bytes_cannot_rebind_an_existing_artifact_record(
    cas: Any, changed: dict[str, object]
) -> None:
    original = cas.put_bytes(
        b"same",
        artifact_id="artifact-1",
        media_type="text/plain",
        metadata={"owner": "original"},
    )
    replayed = cas.put_bytes(
        b"same",
        artifact_id="artifact-1",
        media_type="text/plain",
        metadata={"owner": "original"},
    )
    assert replayed == original

    with pytest.raises(ArtifactConflictError, match="overwrite rejected|rebind"):
        cas.put_bytes(b"same", artifact_id="artifact-1", **changed)

    assert cas.get_ref("artifact-1") == original
    assert cas.get_bytes("artifact-1") == b"same"


def test_mutating_input_or_returned_metadata_cannot_change_stored_record(cas: Any) -> None:
    supplied = {"owner": "original", "nested": {"roles": ["reader"]}}
    returned = cas.put_bytes(b"same", artifact_id="artifact-1", metadata=supplied)
    supplied["owner"] = "caller-rebound"
    supplied["nested"]["roles"].append("writer")

    try:
        returned.metadata["owner"] = "returned-ref-rebound"
        returned.metadata["nested"]["roles"].append("admin")
    except (AttributeError, TypeError):
        pass

    stored = cas.get_ref("artifact-1")
    assert stored.metadata["owner"] == "original"
    assert list(stored.metadata["nested"]["roles"]) == ["reader"]

    try:
        stored.metadata["owner"] = "get-ref-rebound"
        stored.metadata["nested"]["roles"].append("admin")
    except (AttributeError, TypeError):
        pass

    reread = cas.get_ref("artifact-1")
    assert reread.metadata["owner"] == "original"
    assert list(reread.metadata["nested"]["roles"]) == ["reader"]


def test_forged_artifact_ref_cannot_select_another_artifacts_blob(cas: Any) -> None:
    first = cas.put_bytes(b"first", artifact_id="artifact-a")
    second = cas.put_bytes(b"second payload", artifact_id="artifact-b")
    forged = replace(
        first,
        sha256=second.sha256,
        size_bytes=second.size_bytes,
        media_type=second.media_type,
        metadata=second.metadata,
    )

    with pytest.raises(ArtifactIntegrityError, match="reference|record|rebound|integrity"):
        cas.get_bytes(forged)

    assert cas.get_bytes("artifact-a") == b"first"
    assert cas.get_bytes("artifact-b") == b"second payload"


def test_cas_read_bound_accepts_exact_size_and_rejects_one_less(cas: Any) -> None:
    ref = cas.put_bytes(b"hello", artifact_id="artifact-1")

    assert cas.get_bytes(ref, max_bytes=5) == b"hello"
    with pytest.raises(ArtifactIntegrityError, match="bound|size|integrity"):
        cas.get_bytes(ref, max_bytes=4)


def test_filesystem_cas_rejects_oversized_blob_before_path_read_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    ref = cas.put_bytes(b"x", artifact_id="artifact-1")
    blob = cas.blobs / ref.sha256.removeprefix("sha256:")
    blob.write_bytes(b"x" * 4096)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == blob:
            raise AssertionError("oversized blob was read without a bound")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(ArtifactIntegrityError, match="bound|size|integrity"):
        cas.get_bytes(ref, max_bytes=1)


def test_filesystem_cas_serializes_same_root_record_publication_across_instances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "cas"
    stores = (FilesystemCAS(root), FilesystemCAS(root))
    publication_barrier = threading.Barrier(2)
    original_atomic_write = FilesystemCAS._atomic_write

    def coordinated_atomic_write(path: Path, data: bytes) -> None:
        if path.parent.name == "records":
            try:
                publication_barrier.wait(timeout=0.25)
            except threading.BrokenBarrierError:
                pass
        original_atomic_write(path, data)

    monkeypatch.setattr(
        FilesystemCAS,
        "_atomic_write",
        staticmethod(coordinated_atomic_write),
    )

    def publish(index: int) -> tuple[str, str]:
        media_type = f"application/x-candidate-{index}"
        try:
            stores[index].put_bytes(
                b"same",
                artifact_id="shared-id",
                media_type=media_type,
                metadata={"candidate": index},
            )
        except ArtifactConflictError:
            return "conflict", media_type
        return "published", media_type

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(publish, range(2)))

    assert sorted(status for status, _ in results) == ["conflict", "published"]
    winner_media_type = next(media_type for status, media_type in results if status == "published")
    stored = FilesystemCAS(root).get_ref("shared-id")
    assert stored.media_type == winner_media_type
    assert stored.metadata == {
        "candidate": int(winner_media_type.removeprefix("application/x-candidate-"))
    }


def test_filesystem_cas_serializes_same_id_publication_across_processes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cas"
    ready = tmp_path / "ready"
    ready.mkdir()
    go = tmp_path / "go"
    worker = """
import sys
import time
from pathlib import Path
from breadboard.artifacts import ArtifactConflictError, FilesystemCAS

root, ready, go, index = sys.argv[1:]
Path(ready, index).touch()
while not Path(go).exists():
    time.sleep(0.001)
try:
    FilesystemCAS(root).put_bytes(
        b'same',
        artifact_id='shared-process-id',
        media_type=f'application/x-process-{index}',
        metadata={'candidate': int(index)},
    )
except ArtifactConflictError:
    print(f'conflict:{index}')
else:
    print(f'published:{index}')
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(root), str(ready), str(go), str(index)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(4)
    ]
    try:
        deadline = time.monotonic() + 10
        while len(tuple(ready.iterdir())) < len(processes) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(tuple(ready.iterdir())) == len(processes)
        go.touch()
        completed = [process.communicate(timeout=10) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert all(process.returncode == 0 for process in processes), completed
    results = [stdout.strip() for stdout, _ in completed]
    published = [result for result in results if result.startswith("published:")]
    assert len(published) == 1, results
    assert len([result for result in results if result.startswith("conflict:")]) == 3
    winner = int(published[0].partition(":")[2])
    stored = FilesystemCAS(root).get_ref("shared-process-id")
    assert stored.media_type == f"application/x-process-{winner}"
    assert stored.metadata == {"candidate": winner}


def test_filesystem_cas_rejects_oversized_record_before_unbounded_text_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    cas.put_bytes(b"x", artifact_id="artifact-1")
    record = _record_path(cas, "artifact-1")
    record.write_bytes(b"{" + b" " * (4 * 1024 * 1024) + b"}")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == record:
            raise AssertionError("oversized CAS record was read without a bound")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    with pytest.raises(ArtifactIntegrityError, match="record|bound|size|integrity"):
        cas.get_ref("artifact-1")


@pytest.mark.parametrize("attack", ["symlink", "directory", "hardlink"])
def test_filesystem_cas_rejects_unsafe_record_inode_types_and_links(
    tmp_path: Path, attack: str
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    cas.put_bytes(b"x", artifact_id="artifact-1")
    record = _record_path(cas, "artifact-1")
    original = record.read_bytes()

    if attack == "symlink":
        outside = tmp_path / "outside-record.json"
        outside.write_bytes(original)
        record.unlink()
        record.symlink_to(outside)
    elif attack == "directory":
        record.unlink()
        record.mkdir()
    else:
        os.link(record, tmp_path / "second-record-link.json")

    with pytest.raises(ArtifactIntegrityError, match="record|safe|integrity"):
        cas.get_ref("artifact-1")


def test_persisted_digest_traversal_is_rejected_before_blob_path_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    cas.put_bytes(b"x", artifact_id="artifact-1")
    record = _record_path(cas, "artifact-1")
    payload = json.loads(record.read_text(encoding="utf-8"))
    escape = cas.root / "escape"
    escape.write_bytes(b"outside")
    payload["sha256"] = "sha256:../escape"
    payload["size_bytes"] = len(b"outside")
    record.write_text(json.dumps(payload), encoding="utf-8")
    original_open = cas_module.os.open

    def guarded_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        candidate = Path(path)  # type: ignore[arg-type]
        if candidate.resolve() == escape.resolve():
            raise AssertionError("persisted digest escaped the CAS blob directory")
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cas_module.os, "open", guarded_open)

    with pytest.raises(ArtifactIntegrityError, match="digest|record|integrity"):
        cas.get_bytes("artifact-1")


@pytest.mark.parametrize("target_kind", ["record", "blob"])
def test_filesystem_cas_fifo_swap_is_opened_nonblocking_and_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target_kind: str
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    ref = cas.put_bytes(b"x", artifact_id="artifact-1")
    target = (
        _record_path(cas, "artifact-1")
        if target_kind == "record"
        else cas.blobs / ref.sha256.removeprefix("sha256:")
    )
    original_open = cas_module.os.open
    swapped = False

    def fifo_swapping_open(
        path: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal swapped
        leaf = os.fspath(path).rstrip("/").rsplit("/", 1)[-1]  # type: ignore[arg-type]
        if leaf == target.name and not swapped:
            target.unlink()
            os.mkfifo(target)
            swapped = True
            if not flags & getattr(os, "O_NONBLOCK", 0):
                raise AssertionError("FIFO CAS file was opened without O_NONBLOCK")
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cas_module.os, "open", fifo_swapping_open)

    with pytest.raises(ArtifactIntegrityError, match="record|blob|safe|integrity"):
        if target_kind == "record":
            cas.get_ref("artifact-1")
        else:
            cas.get_bytes(ref)

    assert swapped



@pytest.mark.parametrize(
    ("swapped_directory", "operation"),
    [
        ("root", "read-record"),
        ("blobs", "read-blob"),
        ("blobs", "publish-blob"),
        ("records", "publish-record"),
        ("locks", "create-lock"),
    ],
)
def test_filesystem_cas_post_construction_directory_swap_fails_closed_without_outside_effects(
    tmp_path: Path,
    swapped_directory: str,
    operation: str,
) -> None:
    root = tmp_path / "cas"
    cas = FilesystemCAS(root)
    existing = cas.put_bytes(b"trusted", artifact_id="existing")
    outside = tmp_path / f"outside-{swapped_directory}-{operation}"
    outside.mkdir()
    canary = outside / "attacker-canary"
    canary.write_bytes(b"must remain")

    linked_path = root if swapped_directory == "root" else root / swapped_directory
    retained = tmp_path / f"retained-{swapped_directory}-{operation}"
    linked_path.rename(retained)
    linked_path.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactIntegrityError, match="directory|identity|safe|integrity"):
        if operation == "read-record":
            cas.get_ref("existing")
        elif operation == "read-blob":
            cas.get_bytes(existing)
        else:
            cas.put_bytes(b"untrusted", artifact_id=f"new-{operation}")

    assert canary.read_bytes() == b"must remain"
    assert tuple(outside.iterdir()) == (canary,)


def test_filesystem_cas_publish_cleanup_stays_anchored_after_ancestor_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "cas"
    cas = FilesystemCAS(root)
    outside = tmp_path / "outside-cleanup"
    outside.mkdir()
    retained = tmp_path / "retained-blobs-cleanup"
    original_link = cas_module.os.link
    swapped = False
    outside_canary: Path | None = None

    def swap_before_failed_publish(
        source: object,
        destination: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal swapped, outside_canary
        if kwargs.get("src_dir_fd") == cas._blobs_fd and not swapped:
            swapped = True
            (root / "blobs").rename(retained)
            (root / "blobs").symlink_to(outside, target_is_directory=True)
            outside_canary = outside / os.fspath(source)
            outside_canary.write_bytes(b"attacker-owned")
            raise OSError("injected publication failure")
        original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(cas_module.os, "link", swap_before_failed_publish)

    with pytest.raises(ArtifactStoreError, match="publish|store|artifact|safe"):
        cas.put_bytes(b"candidate", artifact_id="cleanup-race")

    assert swapped is True
    assert outside_canary is not None
    assert outside_canary.read_bytes() == b"attacker-owned"
    assert tuple(retained.iterdir()) == ()

class MinimalReader:
    def get_ref(self, artifact_id: str) -> ArtifactRef:
        raise KeyError(artifact_id)

    def get_bytes(
        self,
        artifact_ref: ArtifactRef | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        raise KeyError(str(artifact_ref))


def test_minimal_read_only_implementation_without_has_satisfies_protocol() -> None:
    assert isinstance(MinimalReader(), CASReader)


def test_existing_cas_implementations_satisfy_exported_read_protocol(tmp_path: Path) -> None:
    assert isinstance(InMemoryCAS(), CASReader)
    assert isinstance(FilesystemCAS(tmp_path / "cas"), CASReader)

def test_filesystem_cas_close_is_idempotent(tmp_path: Path) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    descriptors = (cas._root_fd, cas._blobs_fd, cas._records_fd, cas._locks_fd)
    cas.close()
    cas.close()
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(ArtifactStoreError, match="closed"):
        cas.has("artifact")
    with pytest.raises(ArtifactStoreError, match="closed"):
        cas.get_ref("artifact")
    with pytest.raises(ArtifactStoreError, match="closed"):
        cas.put_bytes(b"value")

def test_filesystem_cas_close_waits_for_active_descriptor_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    entered = threading.Event()
    release = threading.Event()
    original = cas._validate_directories

    def paused_validate() -> None:
        entered.set()
        assert release.wait(timeout=5)
        original()

    monkeypatch.setattr(cas, "_validate_directories", paused_validate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        operation = pool.submit(cas.put_bytes, b"value")
        assert entered.wait(timeout=5)
        closing = pool.submit(cas.close)
        time.sleep(0.02)
        assert not closing.done()
        release.set()
        assert operation.result(timeout=5).size_bytes == 5
        closing.result(timeout=5)
    with pytest.raises(ArtifactStoreError, match="closed"):
        cas.has("artifact")
