from __future__ import annotations

import builtins
import glob
import os
import socket
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from agentic_coder_prototype.compilation.bundle import (
    ManifestReader,
    build_dependency_closure,
    ingest_member_map,
)
from agentic_coder_prototype.compilation.contracts import (
    BundleIntegrityError,
    BundleLimitError,
    BundleLimits,
    BundleValidationError,
    ClosureMember,
    DependencyClosureManifest,
    DependencyEdge,
    UndeclaredMemberError,
)
from breadboard.rl.state import ArtifactRef, InMemoryCAS


class MutableProtocolCAS:
    """A CASReader test double that can become hostile after admission."""

    def __init__(self) -> None:
        self.backing = InMemoryCAS()
        self.ref_mutation: dict[str, object] | None = None
        self.payload_override: object | None = None
        self.missing_record = False
        self.missing_blob = False

    def put_bytes(self, data: bytes, **kwargs: object) -> ArtifactRef:
        return self.backing.put_bytes(data, **kwargs)

    def has(self, artifact_ref: ArtifactRef | str) -> bool:
        return self.backing.has(artifact_ref)

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        if self.missing_record:
            raise KeyError(artifact_id)
        ref = self.backing.get_ref(artifact_id)
        return replace(ref, **(self.ref_mutation or {}))

    def get_bytes(
        self,
        artifact_ref: ArtifactRef | str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        if self.missing_blob:
            raise KeyError(str(artifact_ref))
        if self.payload_override is not None:
            return self.payload_override  # type: ignore[return-value]
        return self.backing.get_bytes(artifact_ref, max_bytes=max_bytes)


def _reader_fixture(
    members: dict[str, bytes] | None = None,
    *,
    limits: BundleLimits | None = None,
) -> tuple[MutableProtocolCAS, object, object, ManifestReader]:
    payloads = members or {
        "config.yaml": b"version: 2\n",
        "prompts/system.txt": b"system\n",
        "tools/z.yaml": b"name: z\n",
        "tools/a.yml": b"name: a\n",
        "tools/ignore.txt": b"not yaml\n",
    }
    cas = MutableProtocolCAS()
    bundle = ingest_member_map(
        payloads,
        cas,
        entrypoints={"main": "config.yaml"},
        limits=limits,
    )
    edges = tuple(
        DependencyEdge(
            from_path="config.yaml",
            kind="member",
            raw_ref=path,
            logical_path=path,
            ordinal=index,
        )
        for index, path in enumerate(sorted(set(payloads) - {"config.yaml"}))
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main", edges=edges)
    return cas, bundle, closure, ManifestReader(cas=cas, bundle=bundle, closure=closure)


def test_reader_returns_exact_bytes_and_sorted_suffix_membership() -> None:
    _, _, _, reader = _reader_fixture()

    assert reader.read_bytes("config.yaml") == b"version: 2\n"
    assert reader.members() == (
        "config.yaml",
        "prompts/system.txt",
        "tools/a.yml",
        "tools/ignore.txt",
        "tools/z.yaml",
    )
    assert reader.members("tools", suffixes=(".yaml", ".yml")) == (
        "tools/a.yml",
        "tools/z.yaml",
    )
    assert reader.members("missing") == ()


@pytest.mark.parametrize(
    "requested",
    ["undeclared.txt", "../config.yaml", "/config.yaml", "tools\\a.yml", "config.yaml\x00"],
)
def test_reader_exposes_only_exact_declared_closure_members(requested: str) -> None:
    cas = InMemoryCAS()
    bundle = ingest_member_map(
        {"config.yaml": b"root", "not-reachable.txt": b"secret"},
        cas,
        entrypoints={"main": "config.yaml"},
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main")
    reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)

    assert reader.members() == ("config.yaml",)
    with pytest.raises(UndeclaredMemberError):
        reader.read_bytes(requested)


def test_reader_rejects_missing_cas_record_before_becoming_usable() -> None:
    source_cas = InMemoryCAS()
    bundle = ingest_member_map(
        {"config.yaml": b"root"}, source_cas, entrypoints={"main": "config.yaml"}
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main")

    with pytest.raises(BundleIntegrityError, match="record.*missing"):
        ManifestReader(cas=InMemoryCAS(), bundle=bundle, closure=closure)


@pytest.mark.parametrize(
    "mutation",
    [
        {"sha256": "sha256:" + "0" * 64},
        {"size_bytes": 999},
        {"media_type": "application/x-rebound"},
        {"artifact_id": "rebound-artifact"},
    ],
)
def test_reader_rechecks_record_identity_on_every_read(mutation: dict[str, object]) -> None:
    cas, _, _, reader = _reader_fixture({"config.yaml": b"root"})
    cas.ref_mutation = mutation

    with pytest.raises(BundleIntegrityError, match="rebound|does not match"):
        reader.read_bytes("config.yaml")


@pytest.mark.parametrize("payload", [b"evil", b"roots", bytearray(b"root")])
def test_reader_rechecks_blob_digest_size_and_type_on_every_read(payload: object) -> None:
    cas, _, _, reader = _reader_fixture({"config.yaml": b"root"})
    cas.payload_override = payload

    with pytest.raises(BundleIntegrityError, match="bytes failed"):
        reader.read_bytes("config.yaml")


def test_reader_reports_blob_disappearance_after_construction() -> None:
    cas, _, _, reader = _reader_fixture({"config.yaml": b"root"})
    cas.missing_blob = True

    with pytest.raises(BundleIntegrityError, match="blob.*missing"):
        reader.read_bytes("config.yaml")


def test_reader_enforces_per_read_and_aggregate_byte_limits_at_boundaries() -> None:
    limits = BundleLimits(
        max_member_bytes=1,
        max_total_bytes=2,
        max_members=2,
        max_path_bytes=64,
        max_path_depth=4,
        max_archive_bytes=1024,
        max_compression_ratio=10,
    )
    _, _, _, reader = _reader_fixture(
        {"config.yaml": b"a", "dep.txt": b"b"}, limits=limits
    )

    assert reader.read_bytes("config.yaml") == b"a"
    assert reader.read_bytes("dep.txt") == b"b"
    with pytest.raises(BundleLimitError, match="aggregate|total"):
        reader.read_bytes("config.yaml")


def test_closure_rejects_missing_edge_cycle_and_undeclared_reference() -> None:
    cas = InMemoryCAS()
    bundle = ingest_member_map(
        {"config.yaml": b"root", "dep.yaml": b"dep"},
        cas,
        entrypoints={"main": "config.yaml"},
    )
    members = tuple(ClosureMember.from_bundle_entry(entry) for entry in bundle.entries)

    with pytest.raises(BundleValidationError, match="unreachable"):
        DependencyClosureManifest(
            bundle_digest=bundle.bundle_digest,
            root_entrypoint="config.yaml",
            members=members,
            limits=bundle.limits,
        )

    with pytest.raises(BundleValidationError, match="cycle"):
        DependencyClosureManifest(
            bundle_digest=bundle.bundle_digest,
            root_entrypoint="config.yaml",
            members=members,
            edges=(
                DependencyEdge("config.yaml", "extends", "dep.yaml", "dep.yaml"),
                DependencyEdge("dep.yaml", "extends", "config.yaml", "config.yaml"),
            ),
            limits=bundle.limits,
        )

    with pytest.raises(BundleValidationError, match="undeclared"):
        DependencyClosureManifest(
            bundle_digest=bundle.bundle_digest,
            root_entrypoint="config.yaml",
            members=(members[0],),
            edges=(
                DependencyEdge("config.yaml", "extends", "ghost.yaml", "ghost.yaml"),
            ),
            limits=bundle.limits,
        )


def test_closure_rejects_bundle_members_not_named_by_the_bundle() -> None:
    cas, bundle, closure, _ = _reader_fixture({"config.yaml": b"root"})
    forged = replace(
        closure,
        members=(
            replace(
                closure.members[0],
                artifact_id="forged",
                blob_digest="sha256:" + "0" * 64,
            ),
        ),
        closure_digest="",
    )

    with pytest.raises(BundleIntegrityError, match="differs"):
        ManifestReader(cas=cas, bundle=bundle, closure=forged)


def test_reader_has_no_ambient_cwd_home_env_network_or_glob_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, _, reader = _reader_fixture()
    monkeypatch.chdir(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"ambient access attempted: {args!r} {kwargs!r}")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getcwd", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(os, "listdir", forbidden)
        guard.setattr(os, "scandir", forbidden)
        guard.setattr(Path, "home", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "read_bytes", forbidden)
        guard.setattr(Path, "read_text", forbidden)
        guard.setattr(Path, "exists", forbidden)
        guard.setattr(Path, "resolve", forbidden)
        guard.setattr(Path, "glob", forbidden)
        guard.setattr(Path, "rglob", forbidden)
        guard.setattr(glob, "glob", forbidden)
        guard.setattr(glob, "iglob", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        guard.setattr(urllib.request, "urlopen", forbidden)

        assert reader.read_bytes("prompts/system.txt") == b"system\n"
        assert reader.members("tools", suffixes=(".yaml", ".yml")) == (
            "tools/a.yml",
            "tools/z.yaml",
        )
