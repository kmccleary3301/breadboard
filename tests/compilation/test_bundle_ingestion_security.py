from __future__ import annotations

import gzip
import io
import math
import os
import stat
import tarfile
import zipfile
import warnings
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from breadboard_engine.compilation.bundle import (
    ManifestReader,
    build_dependency_closure,
    ingest_directory,
    ingest_member_map,
    ingest_tar,
    ingest_zip,
)
from breadboard_engine.compilation.contracts import (
    BundleLimitError,
    BundleLimits,
    BundleSecurityError,
    BundleValidationError,
    ConfigBundleManifest,
    DependencyEdge,
)
from breadboard.artifacts import ArtifactRef, InMemoryCAS


class CountingCAS:
    def __init__(self) -> None:
        self.backing = InMemoryCAS()
        self.put_calls = 0

    def put_bytes(self, data: bytes, **kwargs: object) -> ArtifactRef:
        self.put_calls += 1
        return self.backing.put_bytes(data, **kwargs)

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        return self.backing.get_ref(artifact_id)

    def get_bytes(
        self,
        artifact_ref: ArtifactRef | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        return self.backing.get_bytes(artifact_ref, max_bytes=max_bytes)

    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        return self.backing.has(artifact_ref)


class DuplicateMembers(Mapping[str, bytes]):
    def __getitem__(self, key: str) -> bytes:
        if key == "config.yaml":
            return b"second"
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        yield "config.yaml"

    def __len__(self) -> int:
        return 1

    def items(self):  # type: ignore[override]
        return (("config.yaml", b"first"), ("config.yaml", b"second"))


def _zip_bytes(
    entries: list[tuple[str, bytes, int | None]],
    *,
    compression: int = zipfile.ZIP_STORED,
    comment: bytes = b"",
) -> bytes:
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=compression) as archive:
            archive.comment = comment
            for name, payload, mode in entries:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.compress_type = compression
                info.date_time = (1980, 1, 1, 0, 0, 0)
                if mode is not None:
                    info.external_attr = mode << 16
                archive.writestr(info, payload)
    return stream.getvalue()


def _tar_bytes(
    entries: list[tuple[str, bytes, bytes, str, int]],
    *,
    mode: str = "w:",
    mtime: int = 0,
) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode=mode) as archive:
        for name, payload, type_flag, linkname, permissions in entries:
            info = tarfile.TarInfo(name)
            info.type = type_flag
            info.linkname = linkname
            info.mode = permissions
            info.mtime = mtime
            info.size = len(payload) if type_flag in {tarfile.REGTYPE, tarfile.AREGTYPE} else 0
            archive.addfile(info, io.BytesIO(payload) if info.size else None)
    return stream.getvalue()


def _regular_tar(entries: list[tuple[str, bytes]], *, mode: str = "w:", mtime: int = 0) -> bytes:
    return _tar_bytes(
        [(name, payload, tarfile.REGTYPE, "", 0o644) for name, payload in entries],
        mode=mode,
        mtime=mtime,
    )


def _all_member_reader(manifest: ConfigBundleManifest, cas: CountingCAS) -> ManifestReader:
    root = next(
        entry.logical_path for entry in manifest.entries if entry.logical_path == "config.yaml"
    )
    edges = tuple(
        DependencyEdge(root, "member", entry.logical_path, entry.logical_path, index)
        for index, entry in enumerate(
            entry for entry in manifest.entries if entry.logical_path != root
        )
    )
    closure = build_dependency_closure(manifest, root_entrypoint="main", edges=edges)
    return ManifestReader(cas=cas, bundle=manifest, closure=closure)


def _limits(**changes: int) -> BundleLimits:
    values = {
        "max_member_bytes": 1024 * 1024,
        "max_total_bytes": 4 * 1024 * 1024,
        "max_members": 64,
        "max_path_bytes": 256,
        "max_path_depth": 16,
        "max_archive_bytes": 4 * 1024 * 1024,
        "max_compression_ratio": 100,
    }
    values.update(changes)
    return BundleLimits(**values)


def test_valid_directory_ingestion_preserves_bytes_media_and_read_only_modes(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "config.yaml").write_bytes(b"version: 2\n")
    script = tmp_path / "tools" / "run.txt"
    script.write_bytes(b"run\n")
    script.chmod(0o755)
    cas = CountingCAS()

    manifest = ingest_directory(
        tmp_path, cas, entrypoints={"main": "config.yaml"}, source_label="dir-A"
    )
    reader = _all_member_reader(manifest, cas)

    assert manifest.provenance.source_kind == "directory"
    assert reader.read_bytes("config.yaml") == b"version: 2\n"
    assert reader.read_bytes("tools/run.txt") == b"run\n"
    assert {entry.logical_path: entry.mode for entry in manifest.entries} == {
        "config.yaml": 0o444,
        "tools/run.txt": 0o555,
    }
    assert cas.put_calls == 2


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
def test_valid_zip_and_tar_ingestion_are_readable(archive_kind: str) -> None:
    members = [("config.yaml", b"version: 2\n"), ("prompts/system.txt", b"Be exact.\n")]
    cas = CountingCAS()
    if archive_kind == "zip":
        payload = _zip_bytes(
            [(name, content, stat.S_IFREG | 0o644) for name, content in members]
        )
        manifest = ingest_zip(payload, cas, entrypoints={"main": "config.yaml"})
    else:
        payload = _regular_tar(members)
        manifest = ingest_tar(payload, cas, entrypoints={"main": "config.yaml"})
    reader = _all_member_reader(manifest, cas)

    assert manifest.provenance.source_kind == archive_kind
    assert reader.members() == ("config.yaml", "prompts/system.txt")
    assert reader.read_bytes("prompts/system.txt") == b"Be exact.\n"
    assert cas.put_calls == 2


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../escape.yaml",
        "a/../../escape.yaml",
        "/absolute.yaml",
        "//server/share.yaml",
        "C:/drive.yaml",
        "a\\windows.yaml",
        "a\x00hidden.yaml",
        "a//empty.yaml",
        "a/./dot.yaml",
        "percent/%2e%2e.yaml",
    ],
)
def test_member_map_rejects_unsafe_paths_without_mutating_cas(unsafe_path: str) -> None:
    cas = CountingCAS()

    with pytest.raises(BundleSecurityError):
        ingest_member_map(
            {"config.yaml": b"root", unsafe_path: b"bad"},
            cas,
            entrypoints={"main": "config.yaml"},
        )

    assert cas.put_calls == 0


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    "unsafe_path",
    ["../escape.yaml", "/absolute.yaml", "C:/drive.yaml", "a\\windows.yaml", "a//empty.yaml"],
)
def test_archives_reject_unsafe_paths_before_cas_publication(
    archive_kind: str, unsafe_path: str
) -> None:
    cas = CountingCAS()
    if archive_kind == "zip":
        payload = _zip_bytes(
            [
                ("config.yaml", b"root", stat.S_IFREG | 0o644),
                (unsafe_path, b"bad", stat.S_IFREG | 0o644),
            ]
        )
        ingest = ingest_zip
    else:
        payload = _regular_tar([("config.yaml", b"root"), (unsafe_path, b"bad")])
        ingest = ingest_tar

    with pytest.raises(BundleSecurityError):
        ingest(payload, cas, entrypoints={"main": "config.yaml"})

    assert cas.put_calls == 0


def test_ingestion_publishes_only_nfc_logical_names() -> None:
    cas = CountingCAS()
    manifest = ingest_member_map(
        {"config.yaml": b"root", "prompts/cafe\u0301.txt": b"normalized"},
        cas,
        entrypoints={"main": "config.yaml"},
    )
    reader = _all_member_reader(manifest, cas)

    assert tuple(entry.logical_path for entry in manifest.entries) == (
        "config.yaml",
        "prompts/caf\u00e9.txt",
    )
    assert reader.read_bytes("prompts/caf\u00e9.txt") == b"normalized"
    assert reader.read_bytes("prompts/cafe\u0301.txt") == b"normalized"


def test_invalid_entrypoint_rejects_after_staging_without_cas_mutation() -> None:
    cas = CountingCAS()

    with pytest.raises(BundleValidationError, match="entrypoint"):
        ingest_member_map(
            {"config.yaml": b"root"},
            cas,
            entrypoints={"main": "missing.yaml"},
        )

    assert cas.put_calls == 0


@pytest.mark.parametrize(
    "members",
    [
        DuplicateMembers(),
        {"config.yaml": b"root", "Config.yaml": b"case"},
        {"config.yaml": b"root", "caf\u00e9.txt": b"nfc", "cafe\u0301.txt": b"nfd"},
        {"config.yaml": b"root", "Straße.txt": b"one", "STRASSE.txt": b"two"},
    ],
)
def test_member_map_rejects_duplicate_case_and_unicode_collisions_without_writes(
    members: Mapping[str, bytes],
) -> None:
    cas = CountingCAS()
    with pytest.raises(BundleSecurityError, match="duplicate|collid"):
        ingest_member_map(members, cas, entrypoints={"main": "config.yaml"})
    assert cas.put_calls == 0


@pytest.mark.parametrize("archive_kind", ["zip", "tar"])
@pytest.mark.parametrize(
    "names",
    [
        ("config.yaml", "config.yaml"),
        ("config.yaml", "Config.yaml"),
        ("config.yaml", "caf\u00e9.txt", "cafe\u0301.txt"),
    ],
)
def test_archives_reject_duplicate_case_and_unicode_collisions(
    archive_kind: str, names: tuple[str, ...]
) -> None:
    cas = CountingCAS()
    if archive_kind == "zip":
        payload = _zip_bytes(
            [(name, b"x", stat.S_IFREG | 0o644) for name in names]
        )
        ingest = ingest_zip
    else:
        payload = _regular_tar([(name, b"x") for name in names])
        ingest = ingest_tar

    with pytest.raises(BundleSecurityError, match="duplicate|collid"):
        ingest(payload, cas, entrypoints={"main": "config.yaml"})
    assert cas.put_calls == 0


@pytest.mark.parametrize(
    ("kind", "type_flag", "linkname"),
    [
        ("symlink", tarfile.SYMTYPE, "config.yaml"),
        ("hardlink", tarfile.LNKTYPE, "config.yaml"),
        ("character device", tarfile.CHRTYPE, ""),
        ("block device", tarfile.BLKTYPE, ""),
        ("fifo", tarfile.FIFOTYPE, ""),
    ],
)
def test_tar_rejects_links_and_special_files_without_publication(
    kind: str, type_flag: bytes, linkname: str
) -> None:
    cas = CountingCAS()
    payload = _tar_bytes(
        [
            ("config.yaml", b"root", tarfile.REGTYPE, "", 0o644),
            (f"bad-{kind}", b"", type_flag, linkname, 0o644),
        ]
    )

    with pytest.raises(BundleSecurityError, match="link|special"):
        ingest_tar(payload, cas, entrypoints={"main": "config.yaml"})
    assert cas.put_calls == 0


@pytest.mark.parametrize(
    "file_type",
    [stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK],
)
def test_zip_rejects_links_and_special_file_modes_without_publication(file_type: int) -> None:
    cas = CountingCAS()
    payload = _zip_bytes(
        [
            ("config.yaml", b"root", stat.S_IFREG | 0o644),
            ("special", b"target", file_type | 0o644),
        ]
    )

    with pytest.raises(BundleSecurityError, match="link|special"):
        ingest_zip(payload, cas, entrypoints={"main": "config.yaml"})
    assert cas.put_calls == 0


@pytest.mark.parametrize("bad_kind", ["symlink", "hardlink", "fifo", "setuid"])
def test_directory_rejects_links_special_files_and_privileged_modes_before_writes(
    tmp_path: Path, bad_kind: str
) -> None:
    (tmp_path / "config.yaml").write_bytes(b"root")
    bad = tmp_path / "z-bad"
    if bad_kind == "symlink":
        bad.symlink_to(tmp_path / "config.yaml")
    elif bad_kind == "hardlink":
        os.link(tmp_path / "config.yaml", bad)
    elif bad_kind == "fifo":
        os.mkfifo(bad)
    else:
        bad.write_bytes(b"bad")
        bad.chmod(0o4755)
    cas = CountingCAS()

    with pytest.raises(BundleSecurityError, match="link|special|setuid"):
        ingest_directory(tmp_path, cas, entrypoints={"main": "config.yaml"})
    assert cas.put_calls == 0


@pytest.mark.parametrize(
    ("accepted", "limits", "rejected", "expected_error"),
    [
        (
            {"config": b"1234"},
            _limits(max_member_bytes=4, max_total_bytes=10),
            {"config": b"12345"},
            "member byte",
        ),
        (
            {"a": b"12", "b": b"34"},
            _limits(max_member_bytes=3, max_total_bytes=4),
            {"a": b"12", "b": b"345"},
            "total byte",
        ),
        (
            {"a": b"", "b": b""},
            _limits(max_member_bytes=1, max_total_bytes=1, max_members=2),
            {"a": b"", "b": b"", "c": b""},
            "node count",
        ),
        (
            {"abcd": b"x"},
            _limits(max_member_bytes=1, max_total_bytes=1, max_path_bytes=4),
            {"abcde": b"x"},
            "path byte",
        ),
        (
            {"a/b": b"x"},
            _limits(max_member_bytes=1, max_total_bytes=1, max_path_depth=2),
            {"a/b/c": b"x"},
            "path depth",
        ),
    ],
)
def test_member_file_count_total_path_and_depth_limits_accept_boundary_then_reject_one_over(
    accepted: dict[str, bytes],
    limits: BundleLimits,
    rejected: dict[str, bytes],
    expected_error: str,
) -> None:
    ingest_member_map(
        accepted,
        InMemoryCAS(),
        entrypoints={"main": next(iter(accepted))},
        limits=limits,
    )

    cas = CountingCAS()
    with pytest.raises(BundleLimitError, match=expected_error):
        ingest_member_map(
            rejected,
            cas,
            entrypoints={"main": next(iter(rejected))},
            limits=limits,
        )
    assert cas.put_calls == 0


def test_archive_byte_limit_accepts_exact_size_and_rejects_one_byte_less() -> None:
    payload = _zip_bytes([("config.yaml", b"root", stat.S_IFREG | 0o644)])
    accepted_limits = _limits(max_archive_bytes=len(payload))
    ingest_zip(payload, InMemoryCAS(), entrypoints={"main": "config.yaml"}, limits=accepted_limits)

    cas = CountingCAS()
    with pytest.raises(BundleLimitError, match="archive byte"):
        ingest_zip(
            payload,
            cas,
            entrypoints={"main": "config.yaml"},
            limits=_limits(max_archive_bytes=len(payload) - 1),
        )
    assert cas.put_calls == 0


def test_zip_compression_ratio_accepts_ceiling_and_rejects_next_lower_integer() -> None:
    payload = _zip_bytes(
        [("config.yaml", b"A" * 64_000, stat.S_IFREG | 0o644)],
        compression=zipfile.ZIP_DEFLATED,
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        info = archive.getinfo("config.yaml")
        ratio = info.file_size / info.compress_size
    ceiling = math.ceil(ratio)
    assert ceiling > 2

    ingest_zip(
        payload,
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        limits=_limits(max_compression_ratio=ceiling),
    )
    cas = CountingCAS()
    with pytest.raises(BundleLimitError, match="compression ratio"):
        ingest_zip(
            payload,
            cas,
            entrypoints={"main": "config.yaml"},
            limits=_limits(max_compression_ratio=ceiling - 1),
        )
    assert cas.put_calls == 0


def test_tar_compression_ratio_accepts_ceiling_and_rejects_next_lower_integer() -> None:
    payload = _regular_tar([("config.yaml", b"A" * 64_000)], mode="w:gz")
    ratio = len(gzip.decompress(payload)) / len(payload)
    ceiling = math.ceil(ratio)
    assert ceiling > 2

    ingest_tar(
        payload,
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
        limits=_limits(max_compression_ratio=ceiling),
    )
    cas = CountingCAS()
    with pytest.raises(BundleLimitError, match="compression ratio"):
        ingest_tar(
            payload,
            cas,
            entrypoints={"main": "config.yaml"},
            limits=_limits(max_compression_ratio=ceiling - 1),
        )
    assert cas.put_calls == 0


def test_archive_node_count_includes_directories_not_only_files() -> None:
    payload = _zip_bytes(
        [
            ("dir/", b"", stat.S_IFDIR | 0o755),
            ("dir/config.yaml", b"root", stat.S_IFREG | 0o644),
        ]
    )
    cas = CountingCAS()

    with pytest.raises(BundleLimitError, match="node count"):
        ingest_zip(
            payload,
            cas,
            entrypoints={"main": "dir/config.yaml"},
            limits=_limits(max_members=1),
        )
    assert cas.put_calls == 0


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("nested.zip", b"ordinary bytes"),
        ("nested.bin", b"PK\x03\x04" + b"ordinary bytes"),
        ("nested.bin", _regular_tar([("inner.txt", b"x")])),
        ("nested.bin", b"\x1f\x8b" + b"ordinary bytes"),
    ],
)
def test_nested_archive_names_and_magic_are_rejected_before_publication(
    name: str, payload: bytes
) -> None:
    cas = CountingCAS()
    with pytest.raises(BundleSecurityError, match="nested archive"):
        ingest_member_map(
            {"config.yaml": b"root", name: payload},
            cas,
            entrypoints={"main": "config.yaml"},
        )
    assert cas.put_calls == 0


def test_archive_transport_bytes_do_not_bind_logical_bundle_identity() -> None:
    entries = [("config.yaml", b"same", stat.S_IFREG | 0o644)]
    first = ingest_zip(
        _zip_bytes(entries, comment=b"source-A"),
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
    )
    second = ingest_zip(
        _zip_bytes(entries, comment=b"source-B"),
        InMemoryCAS(),
        entrypoints={"main": "config.yaml"},
    )

    assert first.entries == second.entries
    assert first.provenance.raw_source_digest != second.provenance.raw_source_digest
    assert first.bundle_digest == second.bundle_digest
    first_closure = build_dependency_closure(first, root_entrypoint="main")
    second_closure = build_dependency_closure(second, root_entrypoint="main")
    assert first_closure.closure_digest == second_closure.closure_digest
