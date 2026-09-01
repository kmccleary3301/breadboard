from __future__ import annotations

import gzip
import io
import os
import stat
import struct
import tarfile
import tracemalloc
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import breadboard_engine.compilation.bundle as bundle_module
from breadboard_engine.compilation.bundle import (
    ManifestReader,
    build_dependency_closure,
    ingest_directory,
    ingest_member_map,
    ingest_tar,
    ingest_zip,
)
from breadboard_engine.compilation.contracts import (
    BundleEntry,
    BundleEntrypoint,
    BundleIntegrityError,
    BundleLimitError,
    BundleLimits,
    BundleProvenance,
    BundleSecurityError,
    BundleValidationError,
    CanonicalJSONError,
    ClosureMember,
    ConfigBundleManifest,
    DependencyClosureManifest,
    DependencyEdge,
    bytes_sha256,
    canonical_json_bytes,
    canonical_json_loads,
)
from breadboard.artifacts import ArtifactRef, FilesystemCAS, InMemoryCAS


class CountingCAS:
    def __init__(self) -> None:
        self.backing = InMemoryCAS()
        self.put_calls = 0

    def put_bytes(self, data: bytes, **kwargs: object) -> ArtifactRef:
        self.put_calls += 1
        return self.backing.put_bytes(data, **kwargs)

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        return self.backing.get_ref(artifact_id)

    def get_bytes(self, artifact_ref: ArtifactRef | str, **kwargs: object) -> bytes:
        return self.backing.get_bytes(artifact_ref, **kwargs)

    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        return self.backing.has(artifact_ref)


class CopyBomb(bytearray):
    def __bytes__(self) -> bytes:
        raise AssertionError("rejected mutable input was copied")


class SortBomb(str):
    def __lt__(self, other: object) -> bool:
        raise AssertionError("over-limit collection was sorted")


class BoundedEntrypoints(Mapping[str, str]):
    def __init__(self, count: int, max_yields: int) -> None:
        self.count = count
        self.max_yields = max_yields
        self.yielded = 0

    def __getitem__(self, key: str) -> str:
        if key.startswith("entry-"):
            return "a"
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        for index in range(self.count):
            self.yielded += 1
            if self.yielded > self.max_yields:
                raise AssertionError("entrypoint mapping consumed beyond limit plus one")
            yield f"entry-{index}"

    def __len__(self) -> int:
        return self.count

    def items(self):  # type: ignore[override]
        return ((name, self[name]) for name in self)


class HostileWriter(CountingCAS):
    def __init__(self, mutation: dict[str, object] | None, payload: bytes | None) -> None:
        super().__init__()
        self.mutation = mutation
        self.payload = payload

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        ref = super().get_ref(artifact_id)
        return replace(ref, **(self.mutation or {}))

    def get_bytes(self, artifact_ref: ArtifactRef | str, **kwargs: object) -> bytes:
        if self.payload is not None:
            return self.payload
        return super().get_bytes(artifact_ref, **kwargs)


def _limits(**changes: int) -> BundleLimits:
    values = {
        "max_member_bytes": 1024 * 1024,
        "max_total_bytes": 4 * 1024 * 1024,
        "max_members": 64,
        "max_path_bytes": 8192,
        "max_path_depth": 64,
        "max_archive_bytes": 4 * 1024 * 1024,
        "max_compression_ratio": 1000,
        "max_dependency_edges": 16_384,
        "max_dependency_depth": 4096,
    }
    values.update(changes)
    return BundleLimits(**values)


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return stream.getvalue()


def _tar_record(name: str, payload: bytes, type_flag: bytes) -> bytes:
    info = tarfile.TarInfo(name)
    info.type = type_flag
    info.mode = 0o755 if type_flag == tarfile.DIRTYPE else 0o644
    info.mtime = 0
    info.size = len(payload)
    header = info.tobuf(format=tarfile.GNU_FORMAT)
    padding = b"\0" * (-len(payload) % tarfile.BLOCKSIZE)
    return header + payload + padding


def _compressed_tar(*records: bytes) -> bytes:
    return gzip.compress(b"".join(records) + b"\0" * (tarfile.BLOCKSIZE * 2), mtime=0)


def _pax_record(name: str, value: str, declared_size: int) -> bytes:
    body = f" path={value}\n".encode()
    while True:
        candidate = str(len(str(len(body))) + len(body)).encode() + body
        if int(candidate.split(b" ", 1)[0]) == len(candidate):
            break
        body = candidate.split(b" ", 1)[1]
    assert len(candidate) <= declared_size
    return _tar_record(name, candidate + b"\0" * (declared_size - len(candidate)), tarfile.XHDTYPE)


def _external(path: str, payload: bytes = b"external") -> ClosureMember:
    return ClosureMember(
        logical_path=path,
        artifact_id="external:" + path,
        blob_digest=bytes_sha256(payload),
        size_bytes=len(payload),
        media_type="application/octet-stream",
        source="external",
    )


def _bundle_with_root(path: str = "config.yaml") -> ConfigBundleManifest:
    return ingest_member_map(
        {path: b"root"},
        InMemoryCAS(),
        entrypoints={"main": path},
    )


def test_directory_swap_to_external_symlink_is_rejected_without_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "bundle"
    child = root / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()
    (child / "config.yaml").write_bytes(b"admitted")
    (outside / "config.yaml").write_bytes(b"external secret")
    parked = root / "parked"
    original_open = os.open
    swapped = False

    def racing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        leaf = os.fspath(path).rstrip("/").rsplit("/", 1)[-1]
        if leaf == "child" and not swapped:
            child.rename(parked)
            child.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(bundle_module.os, "open", racing_open)
    cas = CountingCAS()

    with pytest.raises(BundleSecurityError):
        ingest_directory(root, cas, entrypoints={"main": "child/config.yaml"})

    assert swapped
    assert cas.put_calls == 0


@pytest.mark.parametrize("source_kind", ["directory-member", "archive-source"])
def test_ingestion_fifo_swap_is_opened_nonblocking_and_never_published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source_kind: str
) -> None:
    if source_kind == "directory-member":
        root = tmp_path / "bundle"
        root.mkdir()
        target = root / "config.yaml"
        target.write_bytes(b"root")
    else:
        target = tmp_path / "bundle.zip"
        target.write_bytes(_zip_bytes([("config.yaml", b"root")]))
    original_open = os.open
    swapped = False

    def fifo_swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        leaf = os.fspath(path).rstrip("/").rsplit("/", 1)[-1]
        if leaf == target.name and not swapped:
            target.unlink()
            os.mkfifo(target)
            swapped = True
            if not flags & getattr(os, "O_NONBLOCK", 0):
                raise AssertionError("FIFO source was opened without O_NONBLOCK")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(bundle_module.os, "open", fifo_swapping_open)
    cas = CountingCAS()

    with pytest.raises(BundleSecurityError):
        if source_kind == "directory-member":
            ingest_directory(root, cas, entrypoints={"main": "config.yaml"})
        else:
            ingest_zip(target, cas, entrypoints={"main": "config.yaml"})

    assert swapped
    assert cas.put_calls == 0


@pytest.mark.parametrize("race", ["hardlink", "setuid"])
@pytest.mark.parametrize("timing", ["before-open", "after-read"])
def test_directory_revalidates_link_and_privilege_metadata_after_open_and_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, race: str, timing: str
) -> None:
    source = tmp_path / "config.yaml"
    source.write_bytes(b"root")
    original_open = os.open
    original_read = os.read
    raced = False
    target_fd: int | None = None

    def mutate() -> None:
        nonlocal raced
        if race == "hardlink":
            os.link(source, tmp_path / "second-link")
        else:
            source.chmod(source.stat().st_mode | stat.S_ISUID)
        raced = True

    def racing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal target_fd
        leaf = os.fspath(path).rstrip("/").rsplit("/", 1)[-1]
        if leaf == source.name and not flags & getattr(os, "O_DIRECTORY", 0):
            if timing == "before-open" and not raced:
                mutate()
            target_fd = original_open(path, flags, *args, **kwargs)
            return target_fd
        return original_open(path, flags, *args, **kwargs)

    def racing_read(fd: int, size: int) -> bytes:
        chunk = original_read(fd, size)
        if fd == target_fd and timing == "after-read" and chunk and not raced:
            mutate()
        return chunk

    monkeypatch.setattr(bundle_module.os, "open", racing_open)
    monkeypatch.setattr(bundle_module.os, "read", racing_read)
    cas = CountingCAS()

    with pytest.raises(BundleSecurityError):
        ingest_directory(tmp_path, cas, entrypoints={"main": "config.yaml"})

    assert raced
    assert cas.put_calls == 0


def test_directory_total_limit_rejects_before_reading_member_that_crosses_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_bytes(b"1234")
    (tmp_path / "b.txt").write_bytes(b"5")
    original_open = os.open
    original_read = os.read
    b_fd: int | None = None
    read_b = False

    def tracking_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal b_fd
        fd = original_open(path, flags, *args, **kwargs)
        leaf = os.fspath(path).rstrip("/").rsplit("/", 1)[-1]
        if leaf == "b.txt" and not flags & getattr(os, "O_DIRECTORY", 0):
            b_fd = fd
        return fd

    def guarded_read(fd: int, size: int) -> bytes:
        nonlocal read_b
        if fd == b_fd:
            read_b = True
            raise AssertionError("over-total member payload was read")
        return original_read(fd, size)

    monkeypatch.setattr(bundle_module.os, "open", tracking_open)
    monkeypatch.setattr(bundle_module.os, "read", guarded_read)
    cas = CountingCAS()

    with pytest.raises(BundleLimitError, match="total"):
        ingest_directory(
            tmp_path,
            cas,
            entrypoints={"main": "a.txt"},
            limits=_limits(max_member_bytes=4, max_total_bytes=4),
        )

    assert not read_b
    assert cas.put_calls == 0


def test_directory_node_limit_stops_enumeration_before_sort_or_file_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("a", "b", "c", "d"):
        (tmp_path / name).write_bytes(b"")
    original_scandir = os.scandir
    yielded = 0

    class CountingScandir(Iterator[os.DirEntry[str]]):
        def __init__(self, target: Any) -> None:
            self._inner = original_scandir(target)

        def __iter__(self) -> CountingScandir:
            return self

        def __next__(self) -> os.DirEntry[str]:
            nonlocal yielded
            yielded += 1
            if yielded > 3:
                raise AssertionError("directory iterator consumed beyond limit plus one")
            return next(self._inner)

        def __enter__(self) -> CountingScandir:
            return self

        def __exit__(self, *args: object) -> None:
            self._inner.close()

        def close(self) -> None:
            self._inner.close()

    monkeypatch.setattr(bundle_module.os, "scandir", CountingScandir)
    cas = CountingCAS()

    with pytest.raises(BundleLimitError, match="node|member"):
        ingest_directory(
            tmp_path,
            cas,
            entrypoints={"main": "a"},
            limits=_limits(max_member_bytes=1, max_total_bytes=1, max_members=2),
        )

    assert yielded == 3
    assert cas.put_calls == 0


@pytest.mark.parametrize(
    ("members", "limits", "message"),
    [
        (
            {"a": CopyBomb(b"12")},
            _limits(max_member_bytes=1, max_total_bytes=1),
            "member",
        ),
        (
            {"a": b"1", "b": CopyBomb(b"2")},
            _limits(max_member_bytes=1, max_total_bytes=1),
            "total",
        ),
        (
            {"a": b"", "b": CopyBomb(b"")},
            _limits(max_member_bytes=1, max_total_bytes=1, max_members=1),
            "member|node",
        ),
    ],
)
def test_member_map_rejects_mutable_input_before_copy(
    members: dict[str, bytes | bytearray], limits: BundleLimits, message: str
) -> None:
    cas = CountingCAS()

    with pytest.raises(BundleLimitError, match=message):
        ingest_member_map(members, cas, entrypoints={"main": "a"}, limits=limits)

    assert cas.put_calls == 0


def test_entrypoint_mapping_stops_at_limit_plus_one_without_cas_mutation() -> None:
    entrypoints = BoundedEntrypoints(count=4, max_yields=3)
    cas = CountingCAS()

    with pytest.raises(BundleLimitError, match="entrypoint|member|node|count"):
        ingest_member_map(
            {"a": b"x"},
            cas,
            entrypoints=entrypoints,
            limits=_limits(max_members=2, max_member_bytes=1, max_total_bytes=1),
        )

    assert entrypoints.yielded <= 3
    assert cas.put_calls == 0


@pytest.mark.parametrize("source_factory", [CopyBomb, memoryview])
def test_archive_rejects_mutable_input_before_copy(
    monkeypatch: pytest.MonkeyPatch,
    source_factory: type[CopyBomb] | type[memoryview],
) -> None:
    source = source_factory(b"x" * 9)
    if isinstance(source, memoryview):
        native_bytes = bytes
        guarded = source

        class BytesMeta(type):
            def __instancecheck__(cls, instance: object) -> bool:
                return isinstance(instance, native_bytes)

        class GuardedBytes(metaclass=BytesMeta):
            def __new__(cls, value: object = b"") -> bytes:
                if value is guarded:
                    raise AssertionError("rejected memoryview was copied")
                return native_bytes(value)

        monkeypatch.setattr(bundle_module, "bytes", GuardedBytes, raising=False)
    cas = CountingCAS()

    with pytest.raises(BundleLimitError, match="archive"):
        ingest_zip(
            source,
            cas,
            entrypoints={"main": "config.yaml"},
            limits=_limits(max_archive_bytes=8),
        )

    assert cas.put_calls == 0


def _classic_count_overflow_zip() -> bytes:
    payload = bytearray(_zip_bytes([("config.yaml", b"root")]))
    eocd = payload.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<HH", payload, eocd + 8, 3, 3)
    return bytes(payload)


def _zip64_count_overflow_zip() -> bytes:
    payload = bytearray(_zip_bytes([("config.yaml", b"root")]))
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    classic = bytearray(payload[eocd_offset:])
    struct.pack_into("<HHII", classic, 8, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    central_offset = struct.unpack_from("<I", payload, eocd_offset + 16)[0]
    central_size = struct.unpack_from("<I", payload, eocd_offset + 12)[0]
    zip64_eocd = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50,
        44,
        45,
        45,
        0,
        0,
        3,
        3,
        central_size,
        central_offset,
    )
    locator = struct.pack("<IIQI", 0x07064B50, 0, eocd_offset, 1)
    return bytes(payload[:eocd_offset] + zip64_eocd + locator + classic)


@pytest.mark.parametrize("archive", [_classic_count_overflow_zip(), _zip64_count_overflow_zip()])
def test_zip_eocd_count_limit_is_checked_before_zipfile_allocates_entry_table(
    monkeypatch: pytest.MonkeyPatch, archive: bytes
) -> None:
    def forbidden_zipfile(*args: object, **kwargs: object) -> object:
        raise AssertionError("ZipFile constructed before EOCD count preflight")

    monkeypatch.setattr(bundle_module.zipfile, "ZipFile", forbidden_zipfile)
    cas = CountingCAS()

    with pytest.raises(BundleLimitError, match="node|member|entry"):
        ingest_zip(
            archive,
            cas,
            entrypoints={"main": "config.yaml"},
            limits=_limits(max_members=2),
        )

    assert cas.put_calls == 0


def test_tar_nonempty_directory_rejects_before_advancing_to_its_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _compressed_tar(
        _tar_record("data/", b"x" * (4 * 1024 * 1024), tarfile.DIRTYPE),
        _tar_record("config.yaml", b"root", tarfile.REGTYPE),
    )
    original_next = tarfile.TarFile.next
    next_calls = 0

    def guarded_next(instance: tarfile.TarFile) -> tarfile.TarInfo | None:
        nonlocal next_calls
        next_calls += 1
        if next_calls > 1:
            raise AssertionError("TAR advanced into a rejected directory payload")
        return original_next(instance)

    monkeypatch.setattr(bundle_module.tarfile.TarFile, "next", guarded_next)
    cas = CountingCAS()

    with pytest.raises((BundleSecurityError, BundleLimitError)):
        ingest_tar(
            archive,
            cas,
            entrypoints={"main": "config.yaml"},
            limits=_limits(max_member_bytes=64, max_total_bytes=64),
        )

    assert next_calls <= 1
    assert cas.put_calls == 0


@pytest.mark.parametrize(
    "metadata_record",
    [
        _tar_record(
            "././@LongLink",
            b"config.yaml\0" + b"\0" * (4 * 1024 * 1024 - len(b"config.yaml\0")),
            tarfile.GNUTYPE_LONGNAME,
        ),
        _pax_record("PaxHeader", "config.yaml", 4 * 1024 * 1024),
    ],
    ids=["gnu-longname", "pax-header"],
)
def test_compressed_tar_metadata_is_charged_before_tarfile_parses_it(
    metadata_record: bytes,
) -> None:
    archive = _compressed_tar(
        metadata_record,
        _tar_record("placeholder", b"root", tarfile.REGTYPE),
    )
    assert len(archive) < 16 * 1024
    cas = CountingCAS()

    tracemalloc.start()
    try:
        with pytest.raises(BundleLimitError):
            ingest_tar(
                archive,
                cas,
                entrypoints={"main": "config.yaml"},
                limits=_limits(max_member_bytes=64, max_total_bytes=64),
            )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak_bytes < 1024 * 1024
    assert cas.put_calls == 0


def test_logical_bundle_and_closure_identity_are_transport_independent() -> None:
    limits = _limits()
    member_map = ingest_member_map(
        {"config.yaml": b"root"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        limits=limits,
        source_label="member-map-source",
    )
    zip_bundle = ingest_zip(
        _zip_bytes([("config.yaml", b"root")]),
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        limits=limits,
        source_label="zip-source",
    )
    tar_bundle = ingest_tar(
        _compressed_tar(_tar_record("config.yaml", b"root", tarfile.REGTYPE)),
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        limits=limits,
        source_label="tar-source",
    )

    assert {bundle.provenance.source_kind for bundle in (member_map, zip_bundle, tar_bundle)} == {
        "member_map",
        "zip",
        "tar",
    }
    assert len(
        {bundle.provenance.raw_source_digest for bundle in (member_map, zip_bundle, tar_bundle)}
    ) == 3
    assert len({bundle.bundle_digest for bundle in (member_map, zip_bundle, tar_bundle)}) == 1
    assert len(
        {
            build_dependency_closure(bundle, root_entrypoint="main").closure_digest
            for bundle in (member_map, zip_bundle, tar_bundle)
        }
    ) == 1


def test_bundle_manifest_rejects_casefolded_file_directory_prefix_collision() -> None:
    digest = bytes_sha256(b"x")
    entries = (
        BundleEntry("Foo", digest, 1),
        BundleEntry("foo/bar.yaml", digest, 1),
    )

    with pytest.raises(BundleValidationError, match="shadow|collision"):
        ConfigBundleManifest(
            entries=entries,
            entrypoints=(BundleEntrypoint("main", "Foo"),),
            provenance=BundleProvenance("test", digest),
        )


@pytest.mark.parametrize(
    ("root", "external_paths"),
    [
        ("Config.yaml", ("config.yaml",)),
        ("assets", ("ASSETS/tool.yaml",)),
        ("config.yaml", ("Tools", "tools/a.yaml")),
    ],
    ids=["bundle-case-alias", "bundle-prefix-alias", "external-prefix-alias"],
)
def test_dependency_closure_rejects_folded_external_aliases_through_both_apis(
    root: str, external_paths: tuple[str, ...]
) -> None:
    bundle = _bundle_with_root(root)
    externals = tuple(_external(path) for path in external_paths)
    edges = tuple(
        DependencyEdge(root, "external", path, path, ordinal)
        for ordinal, path in enumerate(external_paths)
    )

    with pytest.raises(BundleValidationError, match="shadow|collid"):
        build_dependency_closure(
            bundle,
            root_entrypoint="main",
            edges=edges,
            external_members=externals,
        )

    payload = DependencyClosureManifest(
        bundle_digest=bundle.bundle_digest,
        root_entrypoint=root,
        members=(ClosureMember.from_bundle_entry(bundle.entries[0]),),
        limits=bundle.limits,
    ).to_dict()
    payload["members"].extend(member.to_dict() for member in externals)
    payload["edges"] = [edge.to_dict() for edge in edges]
    payload["total_bytes"] += sum(member.size_bytes for member in externals)
    payload["total_members"] += len(externals)

    with pytest.raises(BundleValidationError, match="shadow|collid"):
        DependencyClosureManifest.from_dict(payload)


def test_dependency_closure_requires_a_declared_entrypoint_name() -> None:
    bundle = ingest_member_map(
        {"config.yaml": b"root", "private.yaml": b"private"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
    )

    with pytest.raises(BundleValidationError, match="entrypoint"):
        build_dependency_closure(bundle, root_entrypoint="private.yaml")


def test_over_limit_bundle_nodes_are_rejected_before_sorting() -> None:
    digest = bytes_sha256(b"")
    entries = (
        BundleEntry(SortBomb("b"), digest, 0),
        BundleEntry(SortBomb("a"), digest, 0),
    )

    with pytest.raises(BundleLimitError, match="member|node"):
        ConfigBundleManifest(
            entries=entries,
            entrypoints=(BundleEntrypoint("main", "a"),),
            provenance=BundleProvenance("test", digest),
            limits=_limits(max_members=1, max_member_bytes=1, max_total_bytes=1),
        )


def test_over_limit_closure_nodes_are_rejected_before_sorting() -> None:
    digest = bytes_sha256(b"")
    members = (
        ClosureMember(SortBomb("b"), "b", digest, 0, "text/plain"),
        ClosureMember(SortBomb("a"), "a", digest, 0, "text/plain"),
    )

    with pytest.raises(BundleLimitError, match="member|node"):
        DependencyClosureManifest(
            bundle_digest=digest,
            root_entrypoint="a",
            members=members,
            limits=_limits(max_members=1, max_member_bytes=1, max_total_bytes=1),
        )


@pytest.mark.parametrize(
    ("sizes", "limits", "message"),
    [
        ((2, 0), _limits(max_member_bytes=1, max_total_bytes=2), "member"),
        ((1, 1), _limits(max_member_bytes=1, max_total_bytes=1), "total"),
    ],
)
def test_bundle_member_and_total_limits_are_checked_before_sorting(
    sizes: tuple[int, int], limits: BundleLimits, message: str
) -> None:
    digest = bytes_sha256(b"")
    entries = (
        BundleEntry(SortBomb("b"), digest, sizes[0]),
        BundleEntry(SortBomb("a"), digest, sizes[1]),
    )

    with pytest.raises(BundleLimitError, match=message):
        ConfigBundleManifest(
            entries=entries,
            entrypoints=(BundleEntrypoint("main", "a"),),
            provenance=BundleProvenance("test", digest),
            limits=limits,
        )


@pytest.mark.parametrize(
    ("sizes", "limits", "message"),
    [
        ((2, 0), _limits(max_member_bytes=1, max_total_bytes=2), "member"),
        ((1, 1), _limits(max_member_bytes=1, max_total_bytes=1), "total"),
    ],
)
def test_closure_member_and_total_limits_are_checked_before_sorting(
    sizes: tuple[int, int], limits: BundleLimits, message: str
) -> None:
    digest = bytes_sha256(b"")
    members = (
        ClosureMember(SortBomb("b"), "b", digest, sizes[0], "text/plain"),
        ClosureMember(SortBomb("a"), "a", digest, sizes[1], "text/plain"),
    )

    with pytest.raises(BundleLimitError, match=message):
        DependencyClosureManifest(
            bundle_digest=digest,
            root_entrypoint="a",
            members=members,
            limits=limits,
        )


def test_dependency_edge_limit_accepts_boundary_and_rejects_before_sorting() -> None:
    members = {"root": b"", "a": b"", "b": b""}
    limits = _limits(
        max_member_bytes=1,
        max_total_bytes=1,
        max_members=3,
        max_dependency_edges=2,
    )
    bundle = ingest_member_map(
        members,
        InMemoryCAS(),
        entrypoints={"main": "root"},
        limits=limits,
    )
    accepted = (
        DependencyEdge("root", "member", "a", "a", 0),
        DependencyEdge("root", "member", "b", "b", 1),
    )

    closure = build_dependency_closure(bundle, root_entrypoint="main", edges=accepted)
    assert len(closure.edges) == 2

    over_limit = tuple(
        replace(edge, kind=SortBomb(kind), ordinal=0)
        for edge, kind in zip(accepted, ("z", "a"), strict=True)
    ) + (DependencyEdge("root", SortBomb("m"), "root", "a", 0),)
    tight_bundle = replace(
        bundle,
        limits=replace(bundle.limits, max_dependency_edges=1),
        bundle_digest="",
    )
    with pytest.raises(BundleLimitError, match="edge count"):
        build_dependency_closure(tight_bundle, root_entrypoint="main", edges=over_limit)


def _chain_bundle(node_count: int, *, max_depth: int) -> ConfigBundleManifest:
    digest = bytes_sha256(b"")
    paths = tuple(f"node/{index:04d}" for index in range(node_count))
    return ConfigBundleManifest(
        entries=tuple(BundleEntry(path, digest, 0) for path in paths),
        entrypoints=(BundleEntrypoint("main", paths[0]),),
        provenance=BundleProvenance("test", digest),
        limits=_limits(
            max_member_bytes=1,
            max_total_bytes=1,
            max_members=node_count,
            max_dependency_edges=node_count,
            max_dependency_depth=max_depth,
        ),
    )


def _chain_edges(node_count: int) -> tuple[DependencyEdge, ...]:
    return tuple(
        DependencyEdge(
            f"node/{index:04d}",
            "next",
            f"node/{index + 1:04d}",
            f"node/{index + 1:04d}",
        )
        for index in range(node_count - 1)
    )


def test_dependency_depth_limit_rejects_a_chain_beyond_the_bound() -> None:
    bundle = _chain_bundle(6, max_depth=3)

    with pytest.raises(BundleLimitError, match="depth"):
        build_dependency_closure(bundle, root_entrypoint="main", edges=_chain_edges(6))


def test_deep_dependency_chain_and_cycle_use_iterative_validation() -> None:
    node_count = 1100
    bundle = _chain_bundle(node_count, max_depth=node_count)
    chain = _chain_edges(node_count)

    closure = build_dependency_closure(bundle, root_entrypoint="main", edges=chain)
    assert len(closure.members) == node_count

    cycle = chain + (
        DependencyEdge(
            f"node/{node_count - 1:04d}",
            "next",
            "node/0000",
            "node/0000",
        ),
    )
    with pytest.raises(BundleValidationError, match="cycle"):
        build_dependency_closure(bundle, root_entrypoint="main", edges=cycle)


@pytest.mark.parametrize("field", ["total_bytes", "total_members"])
def test_bundle_boolean_totals_are_rejected_under_unchanged_digest(field: str) -> None:
    bundle = ingest_member_map(
        {"config.yaml": b"x"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
    )
    payload = bundle.to_dict()
    assert payload[field] == 1
    payload[field] = True

    with pytest.raises(BundleValidationError):
        ConfigBundleManifest.from_dict(payload)


@pytest.mark.parametrize("field", ["total_bytes", "total_members"])
def test_closure_boolean_totals_are_rejected_under_unchanged_digest(field: str) -> None:
    bundle = ingest_member_map(
        {"config.yaml": b"x"},
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main")
    payload = closure.to_dict()
    assert payload[field] == 1
    payload[field] = True

    with pytest.raises(BundleValidationError):
        DependencyClosureManifest.from_dict(payload)


@pytest.mark.parametrize(
    ("provenance", "replacement"),
    [([], ""), (["source"], "source")],
)
def test_closure_string_provenance_is_rejected_under_unchanged_digest(
    provenance: list[str], replacement: str
) -> None:
    bundle = _bundle_with_root()
    closure = build_dependency_closure(
        bundle,
        root_entrypoint="main",
        provenance=provenance,
    )
    payload = closure.to_dict()
    payload["provenance"] = replacement

    with pytest.raises(BundleValidationError):
        DependencyClosureManifest.from_dict(payload)


def test_closure_mapping_edges_are_rejected_under_unchanged_digest() -> None:
    bundle = _bundle_with_root()
    closure = build_dependency_closure(bundle, root_entrypoint="main")
    payload = closure.to_dict()
    assert payload["edges"] == []
    payload["edges"] = {}

    with pytest.raises(BundleValidationError):
        DependencyClosureManifest.from_dict(payload)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (9_007_199_254_740_992, b"9007199254740992"),
        (-9_007_199_254_740_992, b"-9007199254740992"),
        (1e20, b"100000000000000000000"),
        (1e21, b"1e+21"),
        (5e-324, b"5e-324"),
        (2.2250738585072014e-308, b"2.2250738585072014e-308"),
        (1.7976931348623157e308, b"1.7976931348623157e+308"),
    ],
)
def test_rfc8785_finite_binary64_boundaries(value: int | float, expected: bytes) -> None:
    assert canonical_json_bytes(value) == expected
    assert canonical_json_bytes(canonical_json_loads(expected)) == expected


def test_rfc8785_rejects_integers_outside_finite_binary64_domain() -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json_bytes(10**309)


@pytest.mark.parametrize(
    ("mutation", "payload"),
    [
        ({"artifact_id": "rebound"}, None),
        ({"sha256": "sha256:" + "0" * 64}, None),
        ({"size_bytes": 999}, None),
        ({"media_type": "application/x-rebound"}, None),
        ({"metadata": {"rebound": True}}, None),
        (None, b"corrupt"),
    ],
    ids=["artifact-id", "digest", "size", "media-type", "metadata", "payload"],
)
def test_ingestion_verifies_hostile_cas_writer_after_put(
    mutation: dict[str, object] | None, payload: bytes | None
) -> None:
    cas = HostileWriter(mutation, payload)

    with pytest.raises(BundleIntegrityError):
        ingest_member_map(
            {"config.yaml": b"root"},
            cas,
            entrypoints={"main": "config.yaml"},
        )

    assert cas.put_calls == 1


def test_filesystem_reader_rejects_oversized_blob_before_unbounded_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    bundle = ingest_member_map(
        {"config.yaml": b"x"},
        cas,
        entrypoints={"main": "config.yaml"},
        limits=_limits(max_member_bytes=1, max_total_bytes=1),
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main")
    reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)
    blob = cas.blobs / bundle.entries[0].blob_digest.removeprefix("sha256:")
    blob.write_bytes(b"x" * 4096)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == blob:
            raise AssertionError("oversized blob was read without a bound")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises((BundleIntegrityError, BundleLimitError)):
        reader.read_bytes("config.yaml")
