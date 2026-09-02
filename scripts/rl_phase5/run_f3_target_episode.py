from __future__ import annotations
from builtins import BaseExceptionGroup

import argparse
import base64
import asyncio
import ctypes
import dataclasses
import http.server
import errno
import fcntl
import os
import socket
import ssl
import stat
import sys
import threading
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation.contracts import (
    canonical_json_bytes,
    canonical_json_loads,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.materialization import (
    DirectoryStorageBackend,
    PreMountedTmpfsQuotaStorageBackend,
    TmpfsQuotaRootAuthority,
)
from breadboard.rl.harness.runners.terminal import TERMINAL_ADAPTER_ID
from breadboard.rl.phase5.f3_authority_authoring import ImmutableAuthorityRef
from breadboard.rl.phase5.f3_composition import (
    F3CompositionBuildResult,
    F3ProductionCompositionInput,
    SourceArtifact,
    build_f3_production_composition,
    load_f3_production_composition,
    sha256_bytes,
)

_CLAIM_BOUNDARY = (
    "One R-SWE-001 episode was executed under the joined F3 authority and its admitted "
    "verifier; the reported reward is only that verifier's result for this episode, and "
    "does not claim correctness for unseen tasks or broader model quality."
)
_FORBIDDEN_POLICY_KEYS = frozenset(
    {"gold", "gold_patch", "reference_patch", "control", "control_artifact", "solution"}
)


class F3TargetEpisodeError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: str) -> str:
    if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError("authority requires a lowercase sha256 digest")
    try:
        bytes.fromhex(value[7:])
    except ValueError as exc:
        raise ValueError("authority requires a lowercase sha256 digest") from exc
    if value != value.lower():
        raise ValueError("authority requires a lowercase sha256 digest")
    return value


def _absolute(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
    ):
        raise ValueError("path must be absolute and normalized")
    return value


def _contains_forbidden_role(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            type(key) is str
            and (
                key.lower() in _FORBIDDEN_POLICY_KEYS
                or key.lower().startswith("gold_")
                or key.lower().startswith("control_")
            )
            or _contains_forbidden_role(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_role(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(
            token in lowered
            for token in (
                "gold-patch",
                "gold_patch",
                "control-artifact",
                "control_artifact",
                "reference-patch",
                "reference_patch",
            )
        )
    return False


class F3PolicyGenerationEvidence(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f3-policy-generation-evidence.v1"]
    generator_role: Literal["agent-candidate"]
    independent: Literal[True]
    task_id: Literal["R-SWE-001"]
    repository_snapshot_digest: str
    command_sha256: str
    model: c.ModelIdentity

    _digests = field_validator("repository_snapshot_digest", "command_sha256")(_digest)


class F3FixedPolicyAuthority(_ExactModel):
    slot_id: Literal["responses-policy"]
    route_id: str = Field(min_length=1, max_length=128)
    route_revision_digest: str
    policy_capability_observation_digest: str
    model: c.ModelIdentity
    patch_application_command: str = Field(min_length=1, max_length=65536)
    patch_application_command_sha256: str
    generation_evidence: SourceArtifact

    _digests = field_validator(
        "route_revision_digest",
        "policy_capability_observation_digest",
        "patch_application_command_sha256",
    )(_digest)

    @model_validator(mode="after")
    def exact_command(self) -> "F3FixedPolicyAuthority":
        command = self.patch_application_command
        if command != command.strip() or "\x00" in command:
            raise ValueError("patch application command is not normalized")
        if (
            sha256_bytes(command.encode("utf-8"))
            != self.patch_application_command_sha256
        ):
            raise ValueError("patch application command digest mismatch")
        lowered = command.lower()
        if any(
            token in lowered
            for token in (
                "gold-patch",
                "gold_patch",
                "reference-patch",
                "reference_patch",
                "control-artifact",
                "control_artifact",
            )
        ):
            raise ValueError(
                "policy command may not reference gold or control artifacts"
            )
        if (
            self.generation_evidence.media_type
            != "application/vnd.breadboard.rl.phase5-f3-policy-generation-evidence+json;version=1"
        ):
            raise ValueError("policy generation evidence media type mismatch")
        return self


class F3AuthorityRefs(_ExactModel):
    task: ImmutableAuthorityRef
    repository: ImmutableAuthorityRef
    generation: ImmutableAuthorityRef



class F3CleanupFileAuthority(_ExactModel):
    path: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    sha256: str

    _path = field_validator("path")(_absolute)
    _sha256 = field_validator("sha256")(_digest)


class F3EpisodeCleanupAuthority(_ExactModel):
    run_root: str
    run_root_device: int = Field(ge=0)
    run_root_inode: int = Field(gt=0)
    run_parent_device: int = Field(ge=0)
    run_parent_inode: int = Field(gt=0)
    run_parent_owner_uid: int = Field(ge=0)
    run_parent_mode: int = Field(ge=0, le=0o777)
    daemon_root: str
    daemon_root_device: int = Field(ge=0)
    daemon_root_inode: int = Field(gt=0)
    daemon_parent_device: int = Field(ge=0)
    daemon_parent_inode: int = Field(gt=0)
    secret_files: tuple[F3CleanupFileAuthority, ...]

    _roots = field_validator("run_root", "daemon_root")(_absolute)

    @model_validator(mode="after")
    def exact_files(self) -> "F3EpisodeCleanupAuthority":
        paths = tuple(item.path for item in self.secret_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("cleanup secret authorities must be sorted and unique")
        if self.run_root == self.daemon_root or os.path.commonpath(
            (self.run_root, self.daemon_root)
        ) in {self.run_root, self.daemon_root}:
            raise ValueError("cleanup roots must be distinct and non-nested")
        if (
            self.run_parent_mode & 0o300 != 0o300
            or self.run_parent_mode & 0o022
        ):
            raise ValueError(
                "shared cleanup parent must be owner writable/searchable and "
                "group/other non-writable"
            )
        return self


class F3EvidenceExportAuthority(_ExactModel):
    path: str
    final_path: str
    cleanup_failure_path: str
    lease_path: str
    lease_device: int = Field(ge=0)
    lease_inode: int = Field(gt=0)
    parent_device: int = Field(ge=0)
    parent_inode: int = Field(gt=0)

    _paths = field_validator(
        "path", "final_path", "cleanup_failure_path", "lease_path"
    )(_absolute)

    @model_validator(mode="after")
    def exact_paths(self) -> "F3EvidenceExportAuthority":
        evidence_paths = {
            self.path,
            self.final_path,
            self.cleanup_failure_path,
        }
        if len(evidence_paths) != 3:
            raise ValueError(
                "pre-cleanup, final, and cleanup-failure evidence paths must be distinct"
            )
        if any(
            os.path.dirname(path) != os.path.dirname(self.path)
            for path in evidence_paths
        ):
            raise ValueError("evidence exports must share the pinned parent")
        if os.path.dirname(self.path) != os.path.dirname(self.lease_path):
            raise ValueError("execution lease must use the pinned export parent")
        if self.lease_path in evidence_paths:
            raise ValueError("execution lease path must be distinct")
        return self

class F3TargetEpisodeInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f3-target-episode-input.v1"]
    composition: F3ProductionCompositionInput
    composition_output_dir: str
    workspace_quota_bytes: int = Field(gt=0)
    episode_id: str = Field(min_length=1, max_length=128)
    task_id: Literal["R-SWE-001"]
    policy_visible_prompt: str = Field(min_length=1, max_length=32768)
    policy: F3FixedPolicyAuthority
    refs: F3AuthorityRefs
    cleanup_authority: F3EpisodeCleanupAuthority

    evidence_export: F3EvidenceExportAuthority
    _output = field_validator("composition_output_dir")(_absolute)

    @field_validator("workspace_quota_bytes")
    @classmethod
    def page_aligned_quota(cls, value: int) -> int:
        if value % os.sysconf("SC_PAGE_SIZE"):
            raise ValueError("workspace quota must be page aligned")
        return value

    @model_validator(mode="after")
    def exact_joins(self) -> "F3TargetEpisodeInput":
        manifest = self.composition
        repository_digest = manifest.resolution_task.artifacts[0].digest
        if self.refs.task.digest != manifest.resolution_task.canonical_digest():
            raise ValueError("task reference does not bind the resolution task")
        if self.refs.repository.digest != repository_digest:
            raise ValueError(
                "repository reference does not bind the admitted workspace"
            )
        if self.refs.generation.digest != self.policy.generation_evidence.sha256:
            raise ValueError("generation reference does not bind policy evidence")
        if self.policy.route_id != manifest.policy_tls.route_id:
            raise ValueError("fixed policy and composition TLS routes differ")
        visible = {
            "task_id": self.task_id,
            "prompt": self.policy_visible_prompt,
            "repository_snapshot_digest": repository_digest,
        }
        if _contains_forbidden_role(visible):
            raise ValueError("policy-visible input contains a forbidden authority role")
        cleanup = self.cleanup_authority
        daemon = manifest.installed.private_docker_daemon
        if daemon is None or cleanup.daemon_root != daemon.daemon_root:
            raise ValueError("cleanup authority does not bind the private daemon root")
        attempt_paths = (
            self.composition_output_dir,
            manifest.authority_manifest.path,
            manifest.stores.cas,
            manifest.stores.locator,
            manifest.stores.materialization_cache,
            manifest.stores.workspace,
            manifest.stores.lease,
            manifest.stores.security_profile,
            manifest.stores.service_output_root,
        )
        if any(
            path == cleanup.run_root
            or os.path.commonpath((cleanup.run_root, path)) != cleanup.run_root
            for path in attempt_paths
        ):
            raise ValueError("F3 attempt-owned path escapes the exact cleanup root")
        required_secrets = set(manifest.secrets.files.values()) | {
            manifest.policy_tls.leaf_private_key.path
        }
        if {item.path for item in cleanup.secret_files} != required_secrets:
            raise ValueError("cleanup authorities do not exactly cover F3 secrets")
        if any(
            os.path.commonpath((cleanup.run_root, path)) != cleanup.run_root
            for path in required_secrets
        ):
            raise ValueError("F3 cleanup secret escapes the cleanup root")
        leaf_cleanup = next(
            item
            for item in cleanup.secret_files
            if item.path == manifest.policy_tls.leaf_private_key.path
        )
        if leaf_cleanup.sha256 != manifest.policy_tls.leaf_private_key.sha256:
            raise ValueError("cleanup authority does not bind the TLS private-key digest")
        export = self.evidence_export
        external_paths = (
            ("pre_cleanup_evidence", export.path),
            ("final_evidence", export.final_path),
            ("terminal_cleanup_failure", export.cleanup_failure_path),
            ("execution_lease", export.lease_path),
        )
        cleanup_paths = (
            ("run_root", cleanup.run_root),
            ("private_daemon_root", cleanup.daemon_root),
            *(
                (f"secret_file[{index}]", item.path)
                for index, item in enumerate(cleanup.secret_files)
            ),
        )
        for external_role, external_path in external_paths:
            for cleanup_role, cleanup_path in cleanup_paths:
                if (
                    external_path == cleanup_path
                    or os.path.commonpath((cleanup_path, external_path))
                    == cleanup_path
                ):
                    raise ValueError(
                        f"{external_role} must be outside cleanup path {cleanup_role}"
                    )
        return self


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_ANCESTRY_DEPTH_LIMIT = 256
_CLEANUP_IDENTITY_SEARCH_DEPTH_LIMIT = 64
_CLEANUP_IDENTITY_SEARCH_ENTRY_LIMIT = 100_000
_CLEANUP_IDENTITY_SEARCH_NAME_BYTES_LIMIT = 8 * 1024 * 1024


def _open_absolute_directory(path: str) -> int:
    if not path.startswith("/") or os.path.normpath(path) != path:
        raise F3TargetEpisodeError("directory authority path is not absolute")
    relative = path.removeprefix("/")
    root_fd = os.open("/", _DIRECTORY_OPEN_FLAGS)
    if not relative:
        return root_fd
    if sys.platform.startswith("linux"):
        class OpenHow(ctypes.Structure):
            _fields_ = (
                ("flags", ctypes.c_uint64),
                ("mode", ctypes.c_uint64),
                ("resolve", ctypes.c_uint64),
            )

        how = OpenHow(
            flags=_DIRECTORY_OPEN_FLAGS,
            mode=0,
            resolve=0x08 | 0x04 | 0x02,
        )
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            descriptor = libc.syscall(
                437,
                root_fd,
                relative.encode("utf-8"),
                ctypes.byref(how),
                ctypes.sizeof(how),
            )
            if descriptor < 0:
                error = ctypes.get_errno()
                raise F3TargetEpisodeError(
                    "absolute directory authority requires a symlink-free "
                    f"openat2 path: {path}"
                ) from OSError(error, os.strerror(error))
            return int(descriptor)
        finally:
            os.close(root_fd)
    descriptor = root_fd
    try:
        for component in relative.split("/"):
            child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException as exc:
        os.close(descriptor)
        if isinstance(exc, OSError):
            raise F3TargetEpisodeError(
                "absolute directory authority requires a symlink-free "
                f"component path: {path}"
            ) from exc
        raise


def _directory_ancestor_identities(
    descriptor: int,
) -> tuple[tuple[int, int], ...]:
    current = os.dup(descriptor)
    identities: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    try:
        for _ in range(_ANCESTRY_DEPTH_LIMIT):
            metadata = os.fstat(current)
            identity = (metadata.st_dev, metadata.st_ino)
            if identity in seen:
                raise F3TargetEpisodeError("directory ancestry contains a cycle")
            identities.append(identity)
            seen.add(identity)
            parent = os.open("..", _DIRECTORY_OPEN_FLAGS, dir_fd=current)
            parent_metadata = os.fstat(parent)
            parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
            if parent_identity == identity:
                os.close(parent)
                return tuple(identities)
            os.close(current)
            current = parent
    finally:
        os.close(current)
    raise F3TargetEpisodeError("directory ancestry exceeds the authority limit")


class _EpisodeExternalAuthority:
    def __init__(self, spec: F3TargetEpisodeInput) -> None:
        self.spec = spec
        self.authority = spec.evidence_export
        self.parent_path = os.path.dirname(self.authority.path)
        self._parent_fd = _open_absolute_directory(self.parent_path)
        self._closed = False
        try:
            self.revalidate_parent()
        except BaseException:
            os.close(self._parent_fd)
            self._closed = True
            raise

    @property
    def parent_fd(self) -> int:
        if self._closed:
            raise F3TargetEpisodeError("external authority is closed")
        return self._parent_fd

    def name(self, path: str) -> str:
        if os.path.dirname(path) != self.parent_path:
            raise F3TargetEpisodeError(
                "external file does not use the retained authority parent"
            )
        return os.path.basename(path)

    def revalidate_parent(self) -> None:
        if self._closed:
            raise F3TargetEpisodeError("external authority is closed")
        metadata = os.fstat(self._parent_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (
                self.authority.parent_device,
                self.authority.parent_inode,
            )
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.getuid()
        ):
            raise F3TargetEpisodeError(
                "external evidence parent authority mismatch"
            )
        reopened = _open_absolute_directory(self.parent_path)
        try:
            observed = os.fstat(reopened)
            if (observed.st_dev, observed.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise F3TargetEpisodeError(
                    "external evidence parent path identity changed"
                )
        finally:
            os.close(reopened)

    def revalidate(self, cleanup_owner: _EpisodeCleanupOwner) -> None:
        self.revalidate_parent()
        cleanup_owner.revalidate()
        forbidden = cleanup_owner.owned_identities()
        if forbidden.intersection(
            _directory_ancestor_identities(self._parent_fd)
        ):
            raise F3TargetEpisodeError(
                "external evidence parent descends from cleanup authority"
            )

    def close(self) -> None:
        if self._closed:
            return
        os.close(self._parent_fd)
        self._closed = True


class _EpisodeExecutionLease:
    def __init__(
        self,
        spec: F3TargetEpisodeInput,
        input_digest: str,
        external_authority: _EpisodeExternalAuthority,
        cleanup_owner: _EpisodeCleanupOwner,
    ) -> None:
        authority = spec.evidence_export
        external_authority.revalidate(cleanup_owner)
        parent_fd = external_authority.parent_fd
        descriptor = -1
        try:
            descriptor = os.open(
                external_authority.name(authority.lease_path),
                os.O_RDWR
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino)
                != (authority.lease_device, authority.lease_inode)
            ):
                raise F3TargetEpisodeError("execution lease authority mismatch")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise F3TargetEpisodeError(
                    "F3 execution roots are already leased"
                ) from exc
            binding = canonical_json_bytes(
                {
                    "schema_version": "bb.rl.phase5-f3-execution-lease.v1",
                    "input_sha256": input_digest,
                    "run_root_device": spec.cleanup_authority.run_root_device,
                    "run_root_inode": spec.cleanup_authority.run_root_inode,
                    "daemon_root_device": spec.cleanup_authority.daemon_root_device,
                    "daemon_root_inode": spec.cleanup_authority.daemon_root_inode,
                    "workspace_quota_bytes": spec.workspace_quota_bytes,
                }
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
            existing = os.read(descriptor, 8192)
            if existing and existing != binding:
                raise F3TargetEpisodeError("execution lease binding mismatch")
            if not existing:
                os.lseek(descriptor, 0, os.SEEK_SET)
                offset = 0
                while offset < len(binding):
                    written = os.write(descriptor, binding[offset:])
                    if written <= 0:
                        raise F3TargetEpisodeError(
                            "execution lease write made no progress"
                        )
                    offset += written
                os.fsync(descriptor)
            current = os.stat(
                external_authority.name(authority.lease_path),
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (current.st_dev, current.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise F3TargetEpisodeError("execution lease path was replaced")
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        self._descriptor = descriptor
        self._external_authority = external_authority

    def close(self) -> None:
        if self._descriptor < 0:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = -1
            self._external_authority = None


_SHARED_PARENT_POLICY = {
    "owner_write_execute_required": True,
    "group_other_write_forbidden": True,
    "mode_mask": "0022",
    "stable_identity_fields": ["device", "inode", "owner_uid", "mode"],
    "unstable_namespace_fields_excluded": ["ctime_ns", "nlink"],
}


def _shared_parent_tuple(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
    )


def _shared_parent_record(path: str, metadata: os.stat_result) -> dict[str, Any]:
    return {
        "path": path,
        "type": "directory",
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "owner_uid": metadata.st_uid,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "mode_int": stat.S_IMODE(metadata.st_mode),
    }


def _validate_shared_parent_metadata(
    authority: F3EpisodeCleanupAuthority,
    metadata: os.stat_result,
) -> None:
    path = os.path.dirname(authority.run_root)
    expected = (
        authority.run_parent_device,
        authority.run_parent_inode,
        authority.run_parent_owner_uid,
        authority.run_parent_mode,
    )
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or _shared_parent_tuple(metadata) != expected
        or metadata.st_uid != os.getuid()
        or mode & 0o300 != 0o300
        or mode & 0o022
    ):
        raise F3TargetEpisodeError(
            f"F3 shared cleanup parent authority mismatch: {path}"
        )


class _EpisodeCleanupOwner:
    def __init__(self, authority: F3EpisodeCleanupAuthority) -> None:
        self.authority = authority
        self._closed = False
        self._receipt: dict[str, Any] | None = None
        self._failure: BaseExceptionGroup | None = None
        self._expected_absent = False
        self._fds: dict[str, int] = {}
        self._identities: dict[str, tuple[int, int]] = {
            authority.run_root: (
                authority.run_root_device,
                authority.run_root_inode,
            ),
            authority.daemon_root: (
                authority.daemon_root_device,
                authority.daemon_root_inode,
            ),
        }
        self._parent_fds: dict[str, int] = {}
        self._secret_parent_fds: dict[str, int] = {}
        self._secret_fds: dict[str, int] = {}
        self._removed_roots: set[str] = set()
        self._shared_parent_observations: dict[str, dict[str, Any]] = {}
        opened: list[int] = []
        try:
            run_parent_path = os.path.dirname(authority.run_root)
            daemon_parent_path = os.path.dirname(authority.daemon_root)
            for path in (run_parent_path, daemon_parent_path):
                descriptor = self._parent_fds.get(path)
                if descriptor is None:
                    descriptor = _open_absolute_directory(path)
                    opened.append(descriptor)
                    self._parent_fds[path] = descriptor
                metadata = os.fstat(descriptor)
                if path == run_parent_path:
                    _validate_shared_parent_metadata(authority, metadata)
                    self._shared_parent_observations["at_open"] = (
                        _shared_parent_record(path, metadata)
                    )
                if path == daemon_parent_path and (
                    not stat.S_ISDIR(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino)
                    != (
                        authority.daemon_parent_device,
                        authority.daemon_parent_inode,
                    )
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                    or metadata.st_uid != os.getuid()
                ):
                    raise F3TargetEpisodeError(
                        f"F3 cleanup parent authority mismatch: {path}"
                    )
            missing_roots: list[str] = []
            for path, device, inode in (
                (
                    authority.run_root,
                    authority.run_root_device,
                    authority.run_root_inode,
                ),
                (
                    authority.daemon_root,
                    authority.daemon_root_device,
                    authority.daemon_root_inode,
                ),
            ):
                parent_descriptor = self._parent_fds[os.path.dirname(path)]
                try:
                    descriptor = os.open(
                        os.path.basename(path),
                        _DIRECTORY_OPEN_FLAGS,
                        dir_fd=parent_descriptor,
                    )
                except FileNotFoundError:
                    missing_roots.append(path)
                    continue
                opened.append(descriptor)
                metadata = os.fstat(descriptor)
                ancestry = _directory_ancestor_identities(descriptor)
                parent = os.fstat(parent_descriptor)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != (device, inode)
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                    or metadata.st_uid != os.getuid()
                    or len(ancestry) < 2
                    or ancestry[1] != (parent.st_dev, parent.st_ino)
                ):
                    raise F3TargetEpisodeError(
                        f"F3 cleanup root authority mismatch: {path}"
                    )
                self._fds[path] = descriptor
            if missing_roots:
                if len(missing_roots) != 2:
                    raise F3TargetEpisodeError(
                        "F3 cleanup roots are only partially absent"
                    )
                self._expected_absent = True
            else:
                run_identity = self._identities[authority.run_root]
                for item in authority.secret_files:
                    parent_path = os.path.dirname(item.path)
                    parent_descriptor = self._secret_parent_fds.get(parent_path)
                    if parent_descriptor is None:
                        parent_descriptor = _open_absolute_directory(parent_path)
                        opened.append(parent_descriptor)
                        if run_identity not in _directory_ancestor_identities(
                            parent_descriptor
                        ):
                            raise F3TargetEpisodeError(
                                "F3 cleanup secret parent escapes the run root: "
                                f"{item.path}"
                            )
                        self._secret_parent_fds[parent_path] = parent_descriptor
                    descriptor = os.open(
                        os.path.basename(item.path),
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_descriptor,
                    )
                    opened.append(descriptor)
                    metadata = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or stat.S_IMODE(metadata.st_mode) != 0o400
                        or metadata.st_nlink != 1
                        or (metadata.st_dev, metadata.st_ino)
                        != (item.device, item.inode)
                    ):
                        raise F3TargetEpisodeError(
                            f"F3 cleanup secret authority mismatch: {item.path}"
                        )
                    self._secret_fds[item.path] = descriptor
            self.revalidate()
        except BaseException:
            for descriptor in reversed(opened):
                os.close(descriptor)
            self._fds.clear()
            self._parent_fds.clear()
            self._secret_parent_fds.clear()
            self._secret_fds.clear()
            raise

    def owned_identities(self) -> set[tuple[int, int]]:
        return {
            *self._identities.values(),
            *(
                (item.device, item.inode)
                for item in self.authority.secret_files
            ),
        }

    @property
    def expected_absent(self) -> bool:
        return self._expected_absent

    @property
    def closed(self) -> bool:
        return self._closed

    @staticmethod
    def _scan_identity_links(
        parent_fds: tuple[int, ...],
        identity: tuple[int, int],
        *,
        unlink: bool,
    ) -> bool:
        visited: set[tuple[int, int]] = set()
        entry_count = 0
        name_bytes = 0
        found = False

        def visit(directory: int, depth: int) -> None:
            nonlocal entry_count, name_bytes, found
            if depth >= _CLEANUP_IDENTITY_SEARCH_DEPTH_LIMIT:
                raise F3TargetEpisodeError(
                    "cleanup authority identity search exceeded its depth limit"
                )
            current = os.fstat(directory)
            current_identity = (current.st_dev, current.st_ino)
            if current_identity in visited:
                return
            visited.add(current_identity)
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    name_bytes += len(os.fsencode(entry.name))
                    if entry_count > _CLEANUP_IDENTITY_SEARCH_ENTRY_LIMIT:
                        raise F3TargetEpisodeError(
                            "cleanup authority identity search exceeded its "
                            "entry limit"
                        )
                    if (
                        name_bytes
                        > _CLEANUP_IDENTITY_SEARCH_NAME_BYTES_LIMIT
                    ):
                        raise F3TargetEpisodeError(
                            "cleanup authority identity search exceeded its "
                            "name-byte limit"
                        )
                    metadata = entry.stat(follow_symlinks=False)
                    observed = (metadata.st_dev, metadata.st_ino)
                    if observed == identity:
                        if not stat.S_ISREG(metadata.st_mode):
                            raise F3TargetEpisodeError(
                                "cleanup secret identity is not a regular file"
                            )
                        found = True
                        if unlink:
                            current_entry = os.stat(
                                entry.name,
                                dir_fd=directory,
                                follow_symlinks=False,
                            )
                            if (
                                current_entry.st_dev,
                                current_entry.st_ino,
                            ) != identity:
                                raise F3TargetEpisodeError(
                                    "cleanup secret identity changed before "
                                    "unlink"
                                )
                            os.unlink(entry.name, dir_fd=directory)
                        continue
                    if (
                        stat.S_ISDIR(metadata.st_mode)
                        and metadata.st_dev == identity[0]
                    ):
                        child = os.open(
                            entry.name,
                            _DIRECTORY_OPEN_FLAGS,
                            dir_fd=directory,
                        )
                        try:
                            opened = os.fstat(child)
                            if (
                                opened.st_dev,
                                opened.st_ino,
                            ) != observed:
                                raise F3TargetEpisodeError(
                                    "cleanup identity search directory changed"
                                )
                            visit(child, depth + 1)
                        finally:
                            os.close(child)

        for parent_fd in parent_fds:
            visit(parent_fd, 0)
        return found

    @staticmethod
    def _contains_identity(
        parent_fd: int,
        identity: tuple[int, int],
    ) -> bool:
        return _EpisodeCleanupOwner._scan_identity_links(
            (parent_fd,),
            identity,
            unlink=False,
        )

    def _remove_owned_secret_links(
        self,
        item: F3CleanupFileAuthority,
    ) -> None:
        descriptor = self._secret_fds[item.path]
        held = os.fstat(descriptor)
        parent_fd = self._secret_parent_fds[os.path.dirname(item.path)]
        name = os.path.basename(item.path)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (item.device, item.inode):
            raise F3TargetEpisodeError(
                f"F3 cleanup secret path was substituted: {item.path}"
            )
        os.unlink(name, dir_fd=parent_fd)
        if os.fstat(descriptor).st_nlink != 0:
            raise F3TargetEpisodeError(
                f"F3 cleanup secret inode retains an external link: {item.path}"
            )

    def _observe_shared_parent(self, stage: str) -> dict[str, Any]:
        path = os.path.dirname(self.authority.run_root)
        descriptor = self._parent_fds[path]
        held = os.fstat(descriptor)
        _validate_shared_parent_metadata(self.authority, held)
        reopened = _open_absolute_directory(path)
        try:
            current = os.fstat(reopened)
            _validate_shared_parent_metadata(self.authority, current)
            if _shared_parent_tuple(current) != _shared_parent_tuple(held):
                raise F3TargetEpisodeError(
                    f"F3 shared cleanup parent path identity changed: {path}"
                )
        finally:
            os.close(reopened)
        observation = _shared_parent_record(path, held)
        observation["held_no_follow_fd"] = True
        observation["path_held_fd_identity_equal"] = True
        self._shared_parent_observations[stage] = observation
        return observation

    def revalidate(self) -> None:
        if self._closed:
            replaced = [
                path
                for path in (
                    self.authority.run_root,
                    self.authority.daemon_root,
                    *(item.path for item in self.authority.secret_files),
                )
                if os.path.lexists(path)
            ]
            if replaced:
                raise F3TargetEpisodeError(
                    "closed cleanup authority path was replaced: "
                    + ", ".join(replaced)
                )
            return
        errors: list[BaseException] = []
        try:
            self._observe_shared_parent("revalidate")
        except BaseException as exc:
            errors.append(exc)
        directory_fds = {
            **self._parent_fds,
            **self._secret_parent_fds,
            **{
                path: descriptor
                for path, descriptor in self._fds.items()
                if path not in self._removed_roots
            },
        }
        for path, descriptor in directory_fds.items():
            try:
                reopened = _open_absolute_directory(path)
                try:
                    held = os.fstat(descriptor)
                    current = os.fstat(reopened)
                    if (current.st_dev, current.st_ino) != (
                        held.st_dev,
                        held.st_ino,
                    ):
                        raise F3TargetEpisodeError(
                            f"F3 cleanup path identity changed: {path}"
                        )
                finally:
                    os.close(reopened)
            except BaseException as exc:
                errors.append(exc)
        if self._expected_absent:
            for path in (
                self.authority.run_root,
                self.authority.daemon_root,
            ):
                parent = self._parent_fds[os.path.dirname(path)]
                try:
                    os.stat(
                        os.path.basename(path),
                        dir_fd=parent,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                except BaseException as exc:
                    errors.append(exc)
                else:
                    errors.append(
                        F3TargetEpisodeError(
                            f"F3 cleanup absent root path was replaced: {path}"
                        )
                    )
            for item in self.authority.secret_files:
                if os.path.lexists(item.path):
                    errors.append(
                        F3TargetEpisodeError(
                            f"F3 cleanup absent secret path was replaced: {item.path}"
                        )
                    )
        else:
            run_identity = self._identities[self.authority.run_root]
            for item in self.authority.secret_files:
                try:
                    parent = self._secret_parent_fds[os.path.dirname(item.path)]
                    if run_identity not in _directory_ancestor_identities(parent):
                        raise F3TargetEpisodeError(
                            f"F3 cleanup secret parent moved: {item.path}"
                        )
                    current = os.stat(
                        os.path.basename(item.path),
                        dir_fd=parent,
                        follow_symlinks=False,
                    )
                    held = os.fstat(self._secret_fds[item.path])
                    if (current.st_dev, current.st_ino) != (
                        held.st_dev,
                        held.st_ino,
                    ):
                        raise F3TargetEpisodeError(
                            f"F3 cleanup secret path was substituted: {item.path}"
                        )
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise BaseExceptionGroup(
                "F3 cleanup authority revalidation failed", errors
            )

    @staticmethod
    def _descriptor_namespace_path(descriptor: int) -> str:
        if sys.platform == "darwin":
            raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
            return raw.split(b"\0", 1)[0].decode("utf-8")
        if sys.platform.startswith("linux"):
            value = os.readlink(f"/proc/self/fd/{descriptor}")
            if value.endswith(" (deleted)"):
                return ""
            return value
        raise F3TargetEpisodeError(
            "descriptor namespace-link proof is unavailable"
        )


    @staticmethod
    def _empty_directory(descriptor: int) -> None:
        errors: list[BaseException] = []
        for name in os.listdir(descriptor):
            try:
                metadata = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except BaseException as exc:
                errors.append(exc)
                continue
            if stat.S_ISDIR(metadata.st_mode):
                child = -1
                try:
                    child = os.open(
                        name,
                        _DIRECTORY_OPEN_FLAGS,
                        dir_fd=descriptor,
                    )
                    try:
                        _EpisodeCleanupOwner._empty_directory(child)
                    except BaseException as exc:
                        errors.append(exc)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    if child >= 0:
                        try:
                            os.close(child)
                        except BaseException as exc:
                            errors.append(exc)
                try:
                    os.rmdir(name, dir_fd=descriptor)
                except BaseException as exc:
                    errors.append(exc)
            else:
                try:
                    os.unlink(name, dir_fd=descriptor)
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise BaseExceptionGroup(
                "F3 cleanup recursive sanitization failed", errors
            )

    @staticmethod
    def _read_secret(
        item: F3CleanupFileAuthority,
        descriptor: int,
    ) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
            or before.st_size > 8192
            or (before.st_dev, before.st_ino) != (item.device, item.inode)
        ):
            raise F3TargetEpisodeError(
                f"F3 cleanup secret authority mismatch: {item.path}"
            )
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                raise F3TargetEpisodeError(
                    f"F3 cleanup secret changed while reading: {item.path}"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise F3TargetEpisodeError(
                f"F3 cleanup secret grew while reading: {item.path}"
            )
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise F3TargetEpisodeError(
                f"F3 cleanup secret changed while reading: {item.path}"
            )
        raw = b"".join(chunks)
        if sha256_bytes(raw) != item.sha256:
            raise F3TargetEpisodeError(
                f"F3 cleanup secret digest mismatch: {item.path}"
            )
        return raw

    def _validate_roots(self) -> None:
        errors: list[BaseException] = []
        for path, descriptor in self._fds.items():
            try:
                current = os.stat(path, follow_symlinks=False)
                held = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev, current.st_ino)
                    != (held.st_dev, held.st_ino)
                ):
                    raise F3TargetEpisodeError(
                        f"F3 cleanup root path was substituted: {path}"
                    )
            except FileNotFoundError:
                continue
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup("F3 cleanup root validation failed", errors)



    def _directory_is_unlinked(self, path: str, descriptor: int) -> bool:
        held = os.fstat(descriptor)
        namespace_path = self._descriptor_namespace_path(descriptor)
        if namespace_path:
            try:
                namespace_metadata = os.stat(
                    namespace_path, follow_symlinks=False
                )
            except FileNotFoundError:
                pass
            else:
                if (
                    namespace_metadata.st_dev,
                    namespace_metadata.st_ino,
                ) == (held.st_dev, held.st_ino):
                    return False
        parent_fd = self._parent_fds[os.path.dirname(path)]
        try:
            os.stat(
                os.path.basename(path),
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return True
        return False


    @staticmethod
    def _quarantine_root(
        path: str,
        name: str,
        descriptor: int,
        parent_fd: int,
    ) -> str:
        held = os.fstat(descriptor)
        source = os.fsencode(name)
        libc = ctypes.CDLL(None, use_errno=True)
        for _ in range(32):
            quarantine = f".bb-f3-cleanup-{uuid.uuid4().hex}"
            target = os.fsencode(quarantine)
            if sys.platform == "darwin":
                rename = libc.renameatx_np
                rename.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                ]
                rename.restype = ctypes.c_int
                result = rename(parent_fd, source, parent_fd, target, 0x00000004)
            elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
                rename = libc.renameat2
                rename.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                ]
                rename.restype = ctypes.c_int
                result = rename(parent_fd, source, parent_fd, target, 1)
            else:
                raise F3TargetEpisodeError(
                    "atomic no-replace cleanup quarantine is unavailable"
                )
            if result == 0:
                quarantined = os.stat(
                    quarantine, dir_fd=parent_fd, follow_symlinks=False
                )
                if (quarantined.st_dev, quarantined.st_ino) != (
                    held.st_dev,
                    held.st_ino,
                ):
                    raise F3TargetEpisodeError(
                        f"F3 cleanup quarantined wrong root inode: {path}"
                    )
                return quarantine
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                continue
            if error == errno.ENOENT:
                raise FileNotFoundError(path)
            raise OSError(error, os.strerror(error), path)
        raise F3TargetEpisodeError(
            f"F3 cleanup could not reserve a quarantine name: {path}"
        )

    def _locate_owned_directory(
        self,
        path: str,
        descriptor: int,
    ) -> tuple[int, str] | None:
        held = os.fstat(descriptor)
        parent_fd = self._parent_fds[os.path.dirname(path)]
        name = os.path.basename(path)
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino)
        ):
            raise F3TargetEpisodeError(
                f"F3 cleanup exact child authority mismatch: {path}"
            )
        return os.dup(parent_fd), name

    def _release_descriptors(self) -> list[BaseException]:
        errors: list[BaseException] = []
        for descriptors in (
            self._secret_fds,
            self._fds,
            self._secret_parent_fds,
            self._parent_fds,
        ):
            for path, descriptor in tuple(descriptors.items()):
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    del descriptors[path]
        return errors

    def release(self) -> None:
        if self._closed:
            if self._failure is not None:
                raise self._failure
            return
        errors = self._release_descriptors()
        self._closed = True
        if errors:
            failure = BaseExceptionGroup(
                "F3 cleanup descriptor release failed", errors
            )
            self._failure = failure
            raise failure

    def close(self) -> dict[str, Any]:
        if self._closed:
            if self._failure is not None:
                raise self._failure
            assert self._receipt is not None
            return self._receipt
        authority = self.authority
        errors: list[BaseException] = []

        def abort() -> None:
            errors.extend(self._release_descriptors())
            self._closed = True
            failure = BaseExceptionGroup("F3 cleanup failed", errors)
            self._failure = failure
            raise failure

        try:
            self._observe_shared_parent("before_cleanup")
        except BaseException as exc:
            errors.append(exc)
            abort()
        if self._expected_absent:
            errors.append(
                F3TargetEpisodeError(
                    "expected-absent cleanup authority cannot issue a new receipt"
                )
            )
            abort()

        try:
            self._validate_roots()
        except BaseException as exc:
            errors.append(exc)
        for item in authority.secret_files:
            try:
                current = os.stat(item.path, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (
                    item.device,
                    item.inode,
                ):
                    raise F3TargetEpisodeError(
                        f"F3 cleanup secret path was substituted: {item.path}"
                    )
                self._read_secret(item, self._secret_fds[item.path])
            except BaseException as exc:
                errors.append(exc)
        if errors:
            abort()

        for item in authority.secret_files:
            try:
                self._remove_owned_secret_links(item)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            abort()

        for descriptor in self._fds.values():
            try:
                self._empty_directory(descriptor)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            abort()

        for path, descriptor in self._fds.items():
            containing_fd = -1
            try:
                located = self._locate_owned_directory(path, descriptor)
                if located is None:
                    raise F3TargetEpisodeError(
                        f"F3 cleanup exact child escaped its pinned path: {path}"
                    )
                containing_fd, name = located
                quarantine = self._quarantine_root(
                    path,
                    name,
                    descriptor,
                    containing_fd,
                )
                os.rmdir(quarantine, dir_fd=containing_fd)
                if not self._directory_is_unlinked(path, descriptor):
                    raise F3TargetEpisodeError(
                        f"F3 cleanup root inode remains linked: {path}"
                    )
                self._removed_roots.add(path)
            except BaseException as exc:
                errors.append(exc)
            finally:
                if containing_fd >= 0:
                    try:
                        os.close(containing_fd)
                    except BaseException as exc:
                        errors.append(exc)
        for path, descriptor in self._fds.items():
            try:
                if os.path.lexists(path) or not self._directory_is_unlinked(
                    path, descriptor
                ):
                    raise F3TargetEpisodeError(
                        f"F3 cleanup root exact absence proof failed: {path}"
                    )
            except BaseException as exc:
                errors.append(exc)
        for item in authority.secret_files:
            try:
                descriptor = self._secret_fds[item.path]
                if (
                    os.path.lexists(item.path)
                    or os.fstat(descriptor).st_nlink != 0
                ):
                    raise F3TargetEpisodeError(
                        "F3 cleanup secret exact absence proof failed: "
                        f"{item.path}"
                    )
            except BaseException as exc:
                errors.append(exc)
        try:
            self._observe_shared_parent("after_cleanup")
        except BaseException as exc:
            errors.append(exc)

        receipt = {
            "schema_version": "bb.rl.phase5-f3-episode-cleanup-receipt.v1",
            "roots": [
                {
                    "role": role,
                    "path": path,
                    "device": self._identities[path][0],
                    "inode": self._identities[path][1],
                    "absent": True,
                }
                for role, path in (
                    ("run_root", authority.run_root),
                    ("private_daemon_root", authority.daemon_root),
                )
            ],
            "secret_files": [
                {
                    "path": item.path,
                    "device": item.device,
                    "inode": item.inode,
                    "sha256": item.sha256,
                    "absent": True,
                }
                for item in authority.secret_files
            ],
            "shared_parent_authority": {
                "policy": dict(_SHARED_PARENT_POLICY),
                "expected": {
                    "path": os.path.dirname(authority.run_root),
                    "type": "directory",
                    "device": authority.run_parent_device,
                    "inode": authority.run_parent_inode,
                    "owner_uid": authority.run_parent_owner_uid,
                    "mode": f"{authority.run_parent_mode:04o}",
                    "mode_int": authority.run_parent_mode,
                },
                "observed": dict(self._shared_parent_observations),
                "authorized_child_inventory": [
                    {
                        "basename": os.path.basename(authority.run_root),
                        "path": authority.run_root,
                        "device": authority.run_root_device,
                        "inode": authority.run_root_inode,
                        "owner_uid": os.getuid(),
                        "mode": "0700",
                        "absent": True,
                    }
                ],
                "siblings_inspected": False,
                "siblings_deleted": False,
                "parent_mutated": False,
            },
            "exact_absence": True,
        }
        errors.extend(self._release_descriptors())
        self._closed = True
        if errors:
            failure = BaseExceptionGroup("F3 cleanup failed", errors)
            self._failure = failure
            raise failure
        self._receipt = receipt
        return receipt


_EVIDENCE_EXPORT_FILE_LIMIT = 64 * 1024 * 1024
_EVIDENCE_EXPORT_TOTAL_LIMIT = 512 * 1024 * 1024
_EVIDENCE_EXPORT_ENTRY_LIMIT = 100_000
_FAILURE_MAX_DEPTH = 8
_FAILURE_MAX_LEAVES = 32
_FAILURE_MAX_BYTES = 64 * 1024
_FAILURE_DETAIL_MAX_DEPTH = 8
_FAILURE_DETAIL_MAX_ITEMS = 32
_FAILURE_DETAIL_MAX_NODES = 256
_FAILURE_DETAIL_STRING_LIMIT = 512
_FAILURE_REDACTED_KEYS = (
    "secret",
    "token",
    "password",
    "credential",
    "private_key",
    "stdout",
    "stderr",
    "bytes_base64",
)


def _safe_failure_detail(
    value: Any,
    *,
    depth: int = 0,
    state: dict[str, int] | None = None,
) -> Any:
    if state is None:
        state = {"nodes": 0}
    state["nodes"] += 1
    if (
        depth >= _FAILURE_DETAIL_MAX_DEPTH
        or state["nodes"] > _FAILURE_DETAIL_MAX_NODES
    ):
        return {"truncated": True}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        items = sorted(
            ((str(key), item) for key, item in value.items()),
            key=lambda pair: pair[0],
        )
        for key, item in items[:_FAILURE_DETAIL_MAX_ITEMS]:
            if any(redacted in key.lower() for redacted in _FAILURE_REDACTED_KEYS):
                result[key] = "[redacted]"
                continue
            result[key] = _safe_failure_detail(
                item, depth=depth + 1, state=state
            )
        if len(value) > _FAILURE_DETAIL_MAX_ITEMS:
            result["truncated"] = True
        return result
    if isinstance(value, (tuple, list)):
        items = [
            _safe_failure_detail(item, depth=depth + 1, state=state)
            for item in value[:_FAILURE_DETAIL_MAX_ITEMS]
        ]
        if len(value) > _FAILURE_DETAIL_MAX_ITEMS:
            items.append({"truncated": True})
        return items
    if value is None or type(value) in (bool, int, float):
        return value
    if type(value) is str:
        return value[:_FAILURE_DETAIL_STRING_LIMIT]
    return {"unserializable_type": type(value).__name__}


def _failure_code(failure: BaseException) -> str:
    code = getattr(failure, "code", None)
    if type(code) is str and code:
        return code[:_FAILURE_DETAIL_STRING_LIMIT]
    if isinstance(failure, asyncio.CancelledError):
        return "cancelled"
    if isinstance(failure, F3TargetEpisodeError):
        return "f3_target_episode_failed"
    if isinstance(failure, OSError):
        return "os_error"
    return "unknown_failure"


def _safe_export_failure(failure: BaseException | None) -> dict[str, Any] | None:
    if failure is None:
        return None
    leaves: list[dict[str, Any]] = []
    projected_bytes = 0
    truncated = False

    def append_projection(projection: dict[str, Any]) -> None:
        nonlocal projected_bytes, truncated
        encoded_size = len(canonical_json_bytes(projection))
        if (
            len(leaves) >= _FAILURE_MAX_LEAVES
            or projected_bytes + encoded_size > _FAILURE_MAX_BYTES
        ):
            if not truncated:
                truncated = True
                leaves.append(
                    {
                        "code": "error_projection_truncated",
                        "type": "ErrorProjectionLimit",
                        "message": sha256_bytes(
                            b"nested F3 errors exceeded the bounded projection"
                        ),
                        "operation": None,
                        "details": {"truncated": True},
                    }
                )
            return
        leaves.append(projection)
        projected_bytes += encoded_size

    def visit(
        current: BaseException,
        group_path: tuple[int, ...],
        depth: int,
    ) -> None:
        if truncated:
            return
        children = getattr(current, "exceptions", ())
        if children:
            if depth >= _FAILURE_MAX_DEPTH:
                append_projection(
                    {
                        "code": "error_projection_truncated",
                        "type": type(current).__name__,
                        "message": sha256_bytes(b"nested F3 error depth exceeded"),
                        "operation": None,
                        "details": {
                            "group_path": list(group_path),
                            "truncated": True,
                        },
                    }
                )
                return
            for index, child in enumerate(children):
                visit(child, (*group_path, index), depth + 1)
            return
        raw_details = getattr(current, "details", None)
        details = _safe_failure_detail(raw_details)
        if not isinstance(details, dict):
            details = {"value": details}
        details["group_path"] = list(group_path)
        operation = details.get("operation")
        if type(operation) is not str:
            nested = details.get("details")
            operation = (
                nested.get("operation")
                if isinstance(nested, dict)
                and type(nested.get("operation")) is str
                else None
            )
        append_projection(
            {
                "code": _failure_code(current),
                "type": type(current).__name__[:_FAILURE_DETAIL_STRING_LIMIT],
                "message": sha256_bytes(
                    str(current).encode("utf-8", "replace")
                ),
                "operation": operation,
                "details": details,
            }
        )

    visit(failure, (), 0)
    return {
        "code": (
            "exception_group"
            if isinstance(failure, BaseExceptionGroup)
            else _failure_code(failure)
        ),
        "type": type(failure).__name__[:_FAILURE_DETAIL_STRING_LIMIT],
        "message": sha256_bytes(str(failure).encode("utf-8", "replace")),
        "operation": None,
        "details": {
            "leaves": leaves,
            "leaf_count": len(leaves),
            "truncated": truncated,
        },
    }


def _evidence_tree_entries(
    root_fd: int,
    relative_root: str,
    role: str,
    secret_identities: set[tuple[int, int]],
    secret_representations: tuple[bytes, ...],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    total_bytes = 0

    def visit(descriptor: int, prefix: str) -> None:
        nonlocal total_bytes
        for name in sorted(os.listdir(descriptor)):
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            relative = f"{prefix}/{name}" if prefix else name
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
                try:
                    visit(child, relative)
                finally:
                    os.close(child)
                continue
            if len(entries) >= _EVIDENCE_EXPORT_ENTRY_LIMIT:
                raise F3TargetEpisodeError(
                    "durable evidence export entry limit exceeded"
                )
            if not stat.S_ISREG(metadata.st_mode):
                raise F3TargetEpisodeError(
                    f"non-file entered durable evidence closure: {role}/{relative}"
                )
            if (metadata.st_dev, metadata.st_ino) in secret_identities:
                raise F3TargetEpisodeError("secret entered durable evidence closure")
            source = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                before = os.fstat(source)
                if before.st_size > _EVIDENCE_EXPORT_FILE_LIMIT:
                    raise F3TargetEpisodeError(
                        f"durable evidence file limit exceeded: {role}/{relative}"
                    )
                if total_bytes + before.st_size > _EVIDENCE_EXPORT_TOTAL_LIMIT:
                    raise F3TargetEpisodeError(
                        "durable evidence export total limit exceeded"
                    )
                chunks: list[bytes] = []
                remaining = before.st_size
                while remaining:
                    chunk = os.read(source, min(remaining, 1024 * 1024))
                    if not chunk:
                        raise F3TargetEpisodeError(
                            f"evidence source changed while reading: {role}/{relative}"
                        )
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(source, 1):
                    raise F3TargetEpisodeError(
                        f"evidence source grew while reading: {role}/{relative}"
                    )
                after = os.fstat(source)
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise F3TargetEpisodeError(
                        f"evidence source changed while reading: {role}/{relative}"
                    )
                raw = b"".join(chunks)
                if any(secret in raw for secret in secret_representations):
                    raise F3TargetEpisodeError(
                        f"secret content entered evidence closure: {role}/{relative}"
                    )
                total_bytes += len(raw)
            finally:
                os.close(source)
            entries.append(
                {
                    "role": role,
                    "path": relative,
                    "size_bytes": len(raw),
                    "sha256": sha256_bytes(raw),
                }
            )

    source_root = os.open(
        relative_root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_fd,
    )
    try:
        visit(source_root, "")
    finally:
        os.close(source_root)
    return entries


def _recover_interrupted_link_publication(
    parent_fd: int,
    name: str,
    raw: bytes,
) -> None:
    prefix = f".{name}."
    candidates = sorted(
        entry
        for entry in os.listdir(parent_fd)
        if entry.startswith(prefix) and entry.endswith(".tmp")
    )
    changed = False
    for candidate in candidates:
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            observed = b""
            while chunk := os.read(descriptor, 1024 * 1024):
                observed += chunk
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o440
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink not in {1, 2}
            or observed != raw
        ):
            raise F3TargetEpisodeError(
                "interrupted durable evidence publication conflict"
            )
        try:
            published = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.link(
                candidate,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        else:
            if (published.st_dev, published.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise F3TargetEpisodeError(
                    "interrupted durable evidence publication identity conflict"
                )
        os.unlink(candidate, dir_fd=parent_fd)
        changed = True
    if changed:
        os.fsync(parent_fd)


def _export_durable_evidence(
    spec: F3TargetEpisodeInput,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
    *,
    report: F3TargetEpisodeReport | None,
    failure: BaseException | None,
) -> dict[str, Any]:
    authority = spec.evidence_export
    external_authority.revalidate(cleanup_owner)
    parent_fd = external_authority.parent_fd
    temporary = (
        f".{external_authority.name(authority.path)}.{uuid.uuid4().hex}.tmp"
    )
    try:
        parent = os.fstat(parent_fd)
        if (
            (parent.st_dev, parent.st_ino)
            != (authority.parent_device, authority.parent_inode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()
        ):
            raise F3TargetEpisodeError("durable evidence parent authority mismatch")
        run_fd = cleanup_owner._fds[spec.cleanup_authority.run_root]
        secret_identities = {
            (item.device, item.inode) for item in spec.cleanup_authority.secret_files
        }
        secret_representations: set[bytes] = set()
        for item in spec.cleanup_authority.secret_files:
            raw_secret = cleanup_owner._read_secret(
                item, cleanup_owner._secret_fds[item.path]
            )
            for representation in (
                raw_secret,
                raw_secret.strip(),
                base64.b64encode(raw_secret),
                raw_secret.hex().encode("ascii"),
            ):
                if representation:
                    secret_representations.add(representation)
        entries: list[dict[str, Any]] = []
        for role, path in (
            ("cas", spec.composition.stores.cas),
            ("locator", spec.composition.stores.locator),
            ("service_output", spec.composition.stores.service_output_root),
        ):
            entries.extend(
                _evidence_tree_entries(
                    run_fd,
                    os.path.relpath(path, spec.cleanup_authority.run_root),
                    role,
                    secret_identities,
                    tuple(sorted(secret_representations)),
                )
            )
        payload = {
            "schema_version": "bb.rl.phase5-f3-durable-evidence-export.v1",
            "episode_id": spec.episode_id,
            "report": None if report is None else report.model_dump(mode="json"),
            "failure": _safe_export_failure(failure),
            "entries": sorted(entries, key=lambda item: (item["role"], item["path"])),
        }
        raw = canonical_json_bytes(payload)
        digest = sha256_bytes(raw)
        name = external_authority.name(authority.path)
        _recover_interrupted_link_publication(parent_fd, name, raw)
        try:
            existing = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            existing = -1
        if existing >= 0:
            try:
                observed = b""
                while chunk := os.read(existing, 1024 * 1024):
                    observed += chunk
            finally:
                os.close(existing)
            if observed != raw:
                raise F3TargetEpisodeError("durable evidence export conflict")
        else:
            output = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0),
                0o440,
                dir_fd=parent_fd,
            )
            try:
                offset = 0
                while offset < len(raw):
                    written = os.write(output, raw[offset:])
                    if written <= 0:
                        raise F3TargetEpisodeError(
                            "durable evidence export write made no progress"
                        )
                    offset += written
                os.fsync(output)
            finally:
                os.close(output)
            os.link(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=parent_fd)
            os.fsync(parent_fd)
        verified = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            observed = b""
            while chunk := os.read(verified, 1024 * 1024):
                observed += chunk
        finally:
            os.close(verified)
        if sha256_bytes(observed) != digest or observed != raw:
            raise F3TargetEpisodeError("durable evidence export verification failed")
        external_authority.revalidate(cleanup_owner)
        return {
            "path": authority.path,
            "sha256": digest,
            "size_bytes": len(raw),
            "entry_count": len(entries),
            "verified": True,
        }
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _publish_final_evidence(
    spec: F3TargetEpisodeInput,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
    report: F3TargetEpisodeReport | None,
    cleanup_receipt: dict[str, Any],
    pre_cleanup_receipt: dict[str, Any],
) -> dict[str, Any]:
    authority = spec.evidence_export
    payload = {
        "schema_version": "bb.rl.phase5-f3-final-evidence-export.v1",
        "episode_id": spec.episode_id,
        "pre_cleanup_export": pre_cleanup_receipt,
        "cleanup_receipt": cleanup_receipt,
        "cleanup_receipt_sha256": sha256_bytes(
            canonical_json_bytes(cleanup_receipt)
        ),
        "report": None if report is None else report.model_dump(mode="json"),
    }
    raw = canonical_json_bytes(payload)
    digest = sha256_bytes(raw)
    external_authority.revalidate(cleanup_owner)
    parent_fd = external_authority.parent_fd
    name = external_authority.name(authority.final_path)
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        parent = os.fstat(parent_fd)
        if (
            (parent.st_dev, parent.st_ino)
            != (authority.parent_device, authority.parent_inode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()
        ):
            raise F3TargetEpisodeError("final evidence parent authority mismatch")
        _recover_interrupted_link_publication(parent_fd, name, raw)
        try:
            existing = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            existing = -1
        if existing >= 0:
            try:
                observed = b""
                while chunk := os.read(existing, 1024 * 1024):
                    observed += chunk
            finally:
                os.close(existing)
            if observed != raw:
                raise F3TargetEpisodeError("final evidence export conflict")
        else:
            output = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0),
                0o440,
                dir_fd=parent_fd,
            )
            try:
                offset = 0
                while offset < len(raw):
                    written = os.write(output, raw[offset:])
                    if written <= 0:
                        raise F3TargetEpisodeError(
                            "final evidence export write made no progress"
                        )
                    offset += written
                os.fsync(output)
            finally:
                os.close(output)
            os.link(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=parent_fd)
            os.fsync(parent_fd)
        verified = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            observed = b""
            while chunk := os.read(verified, 1024 * 1024):
                observed += chunk
        finally:
            os.close(verified)
        if observed != raw or sha256_bytes(observed) != digest:
            raise F3TargetEpisodeError("final evidence export verification failed")
        external_authority.revalidate(cleanup_owner)
        return {
            "path": authority.final_path,
            "sha256": digest,
            "size_bytes": len(raw),
            "verified": True,
        }
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
def _publish_terminal_cleanup_failure(
    spec: F3TargetEpisodeInput,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
    input_digest: str,
    primary_export: dict[str, Any],
    failure: BaseException,
    cleanup_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    authority = spec.evidence_export
    if (
        set(primary_export)
        != {"path", "sha256", "size_bytes", "entry_count", "verified"}
        or primary_export.get("path") != authority.path
        or primary_export.get("verified") is not True
        or type(primary_export.get("size_bytes")) is not int
        or primary_export["size_bytes"] < 0
        or type(primary_export.get("entry_count")) is not int
        or primary_export["entry_count"] < 0
    ):
        raise F3TargetEpisodeError(
            "terminal cleanup failure primary export binding is invalid"
        )
    _digest(primary_export["sha256"])
    _digest(input_digest)
    if cleanup_receipt is None:
        absence_status = "unverified"
    elif cleanup_receipt.get("exact_absence") is True:
        absence_status = "verified_absent"
    else:
        absence_status = "not_absent"
    payload = {
        "schema_version": "bb.rl.phase5-f3-terminal-cleanup-failure.v1",
        "episode_id": spec.episode_id,
        "target_episode_input_sha256": input_digest,
        "primary_export": dict(primary_export),
        "primary_export_sha256": primary_export["sha256"],
        "cleanup_failure": _safe_export_failure(failure),
        "exact_absence_status": absence_status,
        "exact_absence_receipt": cleanup_receipt,
        "exact_absence_receipt_sha256": (
            None
            if cleanup_receipt is None
            else sha256_bytes(canonical_json_bytes(cleanup_receipt))
        ),
    }
    raw = canonical_json_bytes(payload)
    digest = sha256_bytes(raw)
    path = authority.cleanup_failure_path
    external_authority.revalidate(cleanup_owner)
    parent_fd = external_authority.parent_fd
    name = external_authority.name(path)
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        parent = os.fstat(parent_fd)
        if (
            (parent.st_dev, parent.st_ino)
            != (authority.parent_device, authority.parent_inode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()
        ):
            raise F3TargetEpisodeError(
                "terminal cleanup failure parent authority mismatch"
            )
        _recover_interrupted_link_publication(parent_fd, name, raw)
        try:
            existing = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            existing = -1
        if existing >= 0:
            try:
                metadata = os.fstat(existing)
                observed = b""
                while chunk := os.read(existing, 1024 * 1024):
                    observed += chunk
            finally:
                os.close(existing)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o440
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or observed != raw
            ):
                raise F3TargetEpisodeError(
                    "terminal cleanup failure export conflict"
                )
        else:
            output = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0),
                0o440,
                dir_fd=parent_fd,
            )
            try:
                offset = 0
                while offset < len(raw):
                    written = os.write(output, raw[offset:])
                    if written <= 0:
                        raise F3TargetEpisodeError(
                            "terminal cleanup failure write made no progress"
                        )
                    offset += written
                os.fsync(output)
            finally:
                os.close(output)
            os.link(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=parent_fd)
            os.fsync(parent_fd)
        verified = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(verified)
            observed = b""
            while chunk := os.read(verified, 1024 * 1024):
                observed += chunk
        finally:
            os.close(verified)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o440
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or observed != raw
            or sha256_bytes(observed) != digest
        ):
            raise F3TargetEpisodeError(
                "terminal cleanup failure export verification failed"
            )
        external_authority.revalidate(cleanup_owner)
        return {
            "path": path,
            "sha256": digest,
            "size_bytes": len(raw),
            "verified": True,
        }
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass




def _cleanup_bound_report(
    report: F3TargetEpisodeReport,
    cleanup_receipt: dict[str, Any],
    evidence_export: dict[str, Any],
) -> F3TargetEpisodeReport:
    cleanup = dict(report.cleanup)
    cleanup.update(
        {
            "local_composition_retained_for_evidence": False,
            "local_cleanup_released": True,
            "exact_absence_receipt": cleanup_receipt,
            "exact_absence_receipt_sha256": sha256_bytes(
                canonical_json_bytes(cleanup_receipt)
            ),
        }
    )
    payload = report.model_dump(mode="json")
    payload["cleanup"] = cleanup
    payload["evidence_export"] = evidence_export
    return F3TargetEpisodeReport.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )


def _read_external_record(
    spec: F3TargetEpisodeInput,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
    path: str,
    schema_version: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    authority = spec.evidence_export
    external_authority.revalidate(cleanup_owner)
    parent_fd = external_authority.parent_fd
    try:
        parent = os.fstat(parent_fd)
        if (
            (parent.st_dev, parent.st_ino)
            != (authority.parent_device, authority.parent_inode)
            or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid()
        ):
            raise F3TargetEpisodeError("evidence parent authority mismatch")
        try:
            descriptor = os.open(
                external_authority.name(path),
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            external_authority.revalidate(cleanup_owner)
            return None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o440
                or metadata.st_nlink != 1
                or metadata.st_size > _EVIDENCE_EXPORT_TOTAL_LIMIT
            ):
                raise F3TargetEpisodeError("durable evidence record is unsafe")
            raw = b""
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    raise F3TargetEpisodeError(
                        "durable evidence record changed while reading"
                    )
                raw += chunk
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise F3TargetEpisodeError("durable evidence record grew")
        finally:
            os.close(descriptor)
    finally:
        external_authority.revalidate(cleanup_owner)
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw or not isinstance(value, dict):
        raise F3TargetEpisodeError("durable evidence record is not canonical")
    if (
        value.get("schema_version") != schema_version
        or value.get("episode_id") != spec.episode_id
    ):
        raise F3TargetEpisodeError("durable evidence record identity mismatch")
    return value, {
        "path": path,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
        "verified": True,
    }


def _validate_failure_projection(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "code",
        "type",
        "message",
        "operation",
        "details",
    }:
        raise F3TargetEpisodeError("durable failure projection is inexact")
    for field in ("code", "type"):
        item = value[field]
        if (
            type(item) is not str
            or not item
            or len(item) > _FAILURE_DETAIL_STRING_LIMIT
        ):
            raise F3TargetEpisodeError("durable failure identity is invalid")
    _digest(value["message"])
    operation = value["operation"]
    if operation is not None and (
        type(operation) is not str
        or not operation
        or len(operation) > _FAILURE_DETAIL_STRING_LIMIT
    ):
        raise F3TargetEpisodeError("durable failure operation is invalid")
    details = value["details"]
    if not isinstance(details, dict) or set(details) != {
        "leaves",
        "leaf_count",
        "truncated",
    }:
        raise F3TargetEpisodeError("durable failure details are inexact")
    leaves = details["leaves"]
    if (
        not isinstance(leaves, list)
        or len(leaves) > _FAILURE_MAX_LEAVES + 1
        or details["leaf_count"] != len(leaves)
        or type(details["truncated"]) is not bool
    ):
        raise F3TargetEpisodeError("durable failure leaf inventory is invalid")
    for leaf in leaves:
        if not isinstance(leaf, dict) or set(leaf) != {
            "code",
            "type",
            "message",
            "operation",
            "details",
        }:
            raise F3TargetEpisodeError("durable failure leaf is inexact")
        for field in ("code", "type"):
            item = leaf[field]
            if (
                type(item) is not str
                or not item
                or len(item) > _FAILURE_DETAIL_STRING_LIMIT
            ):
                raise F3TargetEpisodeError(
                    "durable failure leaf identity is invalid"
                )
        _digest(leaf["message"])
        leaf_operation = leaf["operation"]
        if leaf_operation is not None and (
            type(leaf_operation) is not str
            or not leaf_operation
            or len(leaf_operation) > _FAILURE_DETAIL_STRING_LIMIT
        ):
            raise F3TargetEpisodeError(
                "durable failure leaf operation is invalid"
            )
        if not isinstance(leaf["details"], dict):
            raise F3TargetEpisodeError("durable failure leaf details are invalid")
    if len(canonical_json_bytes(value)) > _FAILURE_MAX_BYTES + 4096:
        raise F3TargetEpisodeError("durable failure projection is oversized")


def _validate_terminal_cleanup_failure(
    spec: F3TargetEpisodeInput,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
    record: dict[str, Any],
    input_digest: str,
) -> None:
    if set(record) != {
        "schema_version",
        "episode_id",
        "target_episode_input_sha256",
        "primary_export",
        "primary_export_sha256",
        "cleanup_failure",
        "exact_absence_status",
        "exact_absence_receipt",
        "exact_absence_receipt_sha256",
    }:
        raise F3TargetEpisodeError(
            "terminal cleanup failure record schema is inexact"
        )
    if record["target_episode_input_sha256"] != input_digest:
        raise F3TargetEpisodeError(
            "terminal cleanup failure input digest mismatch"
        )
    primary = record["primary_export"]
    if not isinstance(primary, dict) or set(primary) != {
        "path",
        "sha256",
        "size_bytes",
        "entry_count",
        "verified",
    }:
        raise F3TargetEpisodeError(
            "terminal cleanup failure primary export is inexact"
        )
    if (
        primary["path"] != spec.evidence_export.path
        or primary["verified"] is not True
        or type(primary["size_bytes"]) is not int
        or primary["size_bytes"] < 0
        or type(primary["entry_count"]) is not int
        or primary["entry_count"] < 0
        or record["primary_export_sha256"] != primary["sha256"]
    ):
        raise F3TargetEpisodeError(
            "terminal cleanup failure primary export binding is invalid"
        )
    _digest(primary["sha256"])
    pre = _read_external_record(
        spec,
        cleanup_owner,
        external_authority,
        spec.evidence_export.path,
        "bb.rl.phase5-f3-durable-evidence-export.v1",
    )
    if pre is None:
        raise F3TargetEpisodeError(
            "terminal cleanup failure primary export is absent"
        )
    pre_record, pre_receipt = pre
    if (
        primary["sha256"] != pre_receipt["sha256"]
        or primary["size_bytes"] != pre_receipt["size_bytes"]
        or primary["entry_count"] != len(pre_record.get("entries", ()))
    ):
        raise F3TargetEpisodeError(
            "terminal cleanup failure primary export digest mismatch"
        )
    _validate_failure_projection(record["cleanup_failure"])
    receipt = record["exact_absence_receipt"]
    receipt_digest = record["exact_absence_receipt_sha256"]
    status = record["exact_absence_status"]
    if receipt is None:
        if receipt_digest is not None or status != "unverified":
            raise F3TargetEpisodeError(
                "terminal cleanup failure absence status is contradictory"
            )
    else:
        if (
            not isinstance(receipt, dict)
            or receipt_digest
            != sha256_bytes(canonical_json_bytes(receipt))
            or status
            != (
                "verified_absent"
                if receipt.get("exact_absence") is True
                else "not_absent"
            )
        ):
            raise F3TargetEpisodeError(
                "terminal cleanup failure absence receipt is invalid"
            )


def _validate_pre_cleanup_record(
    spec: F3TargetEpisodeInput,
    record: dict[str, Any],
    input_digest: str,
) -> tuple[F3TargetEpisodeReport, dict[str, Any]]:
    if set(record) != {
        "schema_version",
        "episode_id",
        "report",
        "failure",
        "entries",
    }:
        raise F3TargetEpisodeError("durable evidence record schema is inexact")
    failure = record.get("failure")
    if failure is not None:
        _validate_failure_projection(failure)
    entries = record.get("entries")
    if not isinstance(entries, list) or len(entries) > _EVIDENCE_EXPORT_ENTRY_LIMIT:
        raise F3TargetEpisodeError("durable evidence entry inventory is invalid")
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "role",
            "path",
            "size_bytes",
            "sha256",
        }:
            raise F3TargetEpisodeError("durable evidence entry is invalid")
        if entry["role"] not in {"cas", "locator", "service_output"}:
            raise F3TargetEpisodeError("durable evidence role is invalid")
        relative = entry["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or os.path.isabs(relative)
            or ".." in Path(relative).parts
        ):
            raise F3TargetEpisodeError("durable evidence path is invalid")
        size = entry["size_bytes"]
        digest = entry["sha256"]
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > _EVIDENCE_EXPORT_FILE_LIMIT
            or not isinstance(digest, str)
        ):
            raise F3TargetEpisodeError("durable evidence entry digest is invalid")
        _digest(digest)
        total += size
        if total > _EVIDENCE_EXPORT_TOTAL_LIMIT:
            raise F3TargetEpisodeError("durable evidence inventory is oversized")
    report_payload = record.get("report")
    if not isinstance(report_payload, dict):
        raise F3TargetEpisodeError(
            "pre-cleanup evidence does not contain a successful report"
        )
    report = F3TargetEpisodeReport.model_validate_json(
        canonical_json_bytes(report_payload), strict=True
    )
    if report.inputs.get("target_episode_input_sha256") != input_digest:
        raise F3TargetEpisodeError("durable report input digest mismatch")
    return report, record




def _recover_after_cleanup(
    spec: F3TargetEpisodeInput,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
    input_digest: str,
) -> F3TargetEpisodeReport | None:
    terminal = _read_external_record(
        spec,
        cleanup_owner,
        external_authority,
        spec.evidence_export.cleanup_failure_path,
        "bb.rl.phase5-f3-terminal-cleanup-failure.v1",
    )
    if terminal is not None:
        record, _terminal_receipt = terminal
        _validate_terminal_cleanup_failure(
            spec, cleanup_owner, external_authority, record, input_digest
        )
        raise F3TargetEpisodeError(
            "the prior F3 attempt durably failed during terminal cleanup"
        )
    final = _read_external_record(
        spec,
        cleanup_owner,
        external_authority,
        spec.evidence_export.final_path,
        "bb.rl.phase5-f3-final-evidence-export.v1",
    )
    if final is not None:
        record, final_receipt = final
        if set(record) != {
            "schema_version",
            "episode_id",
            "pre_cleanup_export",
            "cleanup_receipt",
            "cleanup_receipt_sha256",
            "report",
        }:
            raise F3TargetEpisodeError(
                "final durable evidence schema is inexact"
            )
        cleanup_receipt = record.get("cleanup_receipt")
        if (
            not isinstance(cleanup_receipt, dict)
            or record.get("cleanup_receipt_sha256")
            != sha256_bytes(canonical_json_bytes(cleanup_receipt))
        ):
            raise F3TargetEpisodeError("final cleanup receipt digest mismatch")
        if record.get("report") is None:
            raise F3TargetEpisodeError(
                "the prior F3 attempt durably failed before execution"
            )
        report = F3TargetEpisodeReport.model_validate_json(
            canonical_json_bytes(record["report"]), strict=True
        )
        if report.inputs.get("target_episode_input_sha256") != input_digest:
            raise F3TargetEpisodeError("final durable report input digest mismatch")
        return _cleanup_bound_report(
            report,
            cleanup_receipt,
            {
                "pre_cleanup": record["pre_cleanup_export"],
                "final": final_receipt,
            },
        )
    return None
async def _resume_pre_cleanup_export(
    spec: F3TargetEpisodeInput,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
    input_digest: str,
) -> F3TargetEpisodeReport | None:
    pre = _read_external_record(
        spec,
        cleanup_owner,
        external_authority,
        spec.evidence_export.path,
        "bb.rl.phase5-f3-durable-evidence-export.v1",
    )
    if pre is None:
        return None
    record, pre_receipt = pre
    report, _ = _validate_pre_cleanup_record(spec, record, input_digest)
    root = Path(spec.composition_output_dir)
    artifacts = root / "artifacts"
    build = F3CompositionBuildResult(
        composition_ref_path=os.fspath(
            (artifacts / "composition-ref.json").resolve()
        ),
        composition_manifest_path=os.fspath(
            (artifacts / "composition-manifest.json").resolve()
        ),
        authority_bundle_path=os.fspath(
            (artifacts / "authority-bundle.json").resolve()
        ),
        inventory_path=os.fspath((root / "inventory.json").resolve()),
        service_output_root=spec.composition.stores.service_output_root,
        authority_manifest_sha256=spec.composition.authority_manifest.sha256,
    )
    composition = None
    errors: list[BaseException] = []
    try:
        composition = load_f3_production_composition(
            build, spec.composition.secrets.files
        )
    except BaseException as exc:
        errors.append(exc)
    if composition is not None:
        try:
            await composition.close()
        except BaseException as exc:
            errors.append(exc)
    try:
        _WorkspaceQuotaRoot(
            spec.composition.stores.workspace,
            spec.workspace_quota_bytes,
        ).close()
    except BaseException as exc:
        errors.append(exc)
    if errors:
        raise BaseExceptionGroup(
            "F3 interrupted pre-cleanup recovery failed before cleanup", errors
        )
    cleanup_receipt: dict[str, Any] | None = None
    try:
        cleanup_receipt = cleanup_owner.close()
    except BaseException as exc:
        errors.append(exc)
    if errors:
        raise BaseExceptionGroup(
            "F3 interrupted pre-cleanup recovery failed", errors
        )
    assert cleanup_receipt is not None
    _publish_final_evidence(
        spec,
        cleanup_owner,
        external_authority,
        report,
        cleanup_receipt,
        pre_receipt,
    )
    recovered = _recover_after_cleanup(
        spec, cleanup_owner, external_authority, input_digest
    )
    if recovered is None:
        raise F3TargetEpisodeError(
            "F3 interrupted pre-cleanup recovery did not publish final evidence"
        )
    return recovered




class F3TargetEpisodeReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f3-target-episode-report.v1"]
    scheduler: dict[str, Any]
    inputs: dict[str, Any]
    authorities: dict[str, Any]
    images: tuple[dict[str, Any], ...]
    resolution: dict[str, Any]
    lifecycle: dict[str, Any]
    artifacts: dict[str, Any]
    verifier: dict[str, Any]
    cleanup: dict[str, Any]
    claim_boundary: Literal[
        "One R-SWE-001 episode was executed under the joined F3 authority and its admitted verifier; the reported reward is only that verifier's result for this episode, and does not claim correctness for unseen tasks or broader model quality."
    ]
    evidence_export: dict[str, Any] | None = None

    @model_validator(mode="after")
    def successful_closed_episode(self) -> "F3TargetEpisodeReport":
        if self.verifier.get("reward") != 1 or self.verifier.get("passed") is not True:
            raise ValueError("F3 verifier did not return the exact successful reward")
        if (
            self.cleanup.get("released") is not True
            or self.cleanup.get("no_orphan") is not True
        ):
            raise ValueError("F3 local cleanup or no-orphan proof failed")
        return self


def _wire(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return _wire(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _wire(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def _read_generation_evidence(spec: F3TargetEpisodeInput) -> F3PolicyGenerationEvidence:
    source = spec.policy.generation_evidence
    raw = Path(source.path).read_bytes()
    if sha256_bytes(raw) != source.sha256:
        raise F3TargetEpisodeError("policy generation evidence digest mismatch")
    try:
        value = canonical_json_loads(raw)
    except Exception as exc:
        raise F3TargetEpisodeError(
            "policy generation evidence is not canonical JSON"
        ) from exc
    if canonical_json_bytes(value) != raw:
        raise F3TargetEpisodeError("policy generation evidence is not canonical JSON")
    evidence = F3PolicyGenerationEvidence.model_validate_json(raw, strict=True)
    if (
        evidence.command_sha256 != spec.policy.patch_application_command_sha256
        or evidence.repository_snapshot_digest != spec.refs.repository.digest
        or evidence.model != spec.policy.model
    ):
        raise F3TargetEpisodeError("policy generation evidence identity mismatch")
    return evidence


class _FixedPolicyServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        path: str,
        certificate: str,
        private_key: str,
        credential: str,
        expected_episode_id: str,
        expected_effective_plan_digest: str,
        expected_binding_digest: str,
        expected_slot_id: str,
        expected_model_id: str,
        response_payload: Mapping[str, Any],
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                try:
                    if self.path != path:
                        raise F3TargetEpisodeError("policy request path mismatch")
                    if self.headers.get("Authorization") != f"Bearer {credential}":
                        raise F3TargetEpisodeError("policy credential mismatch")
                    raw_length = self.headers.get("Content-Length", "")
                    if not raw_length.isdigit() or len(raw_length) > 10:
                        raise F3TargetEpisodeError("policy request length is invalid")
                    body = self.rfile.read(int(raw_length))
                    request = canonical_json_loads(body)
                    if (
                        canonical_json_bytes(request) != body
                        or type(request) is not dict
                    ):
                        raise F3TargetEpisodeError("policy request is not canonical")
                    if set(request) != {
                        "schema_version",
                        "episode_id",
                        "effective_plan_digest",
                        "binding_digest",
                        "policy_slot_id",
                        "request_digest",
                        "request_payload",
                        "turn",
                        "attempt",
                    }:
                        raise F3TargetEpisodeError("policy request shape mismatch")
                    payload = request["request_payload"]
                    if (
                        request["schema_version"] != "bb.rl.policy-http-request.v1"
                        or request["episode_id"] != expected_episode_id
                        or request["effective_plan_digest"]
                        != expected_effective_plan_digest
                        or request["binding_digest"] != expected_binding_digest
                        or request["policy_slot_id"] != expected_slot_id
                        or request["turn"] != 1
                        or request["attempt"] != 1
                        or type(payload) is not dict
                        or payload.get("model") != expected_model_id
                        or _contains_forbidden_role(payload)
                    ):
                        raise F3TargetEpisodeError("policy request identity mismatch")
                    owner.requests.append(request)
                    envelope = {
                        "response_digest": sha256_bytes(
                            canonical_json_bytes(response_payload)
                        ),
                        "response_payload": dict(response_payload),
                    }
                    response = canonical_json_bytes(envelope)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(response)
                    self.close_connection = True
                except BaseException as exc:
                    owner.failure = exc
                    body = b'{"error":"closed"}'
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:
                return

        self.failure: BaseException | None = None
        self._server = http.server.ThreadingHTTPServer((host, port), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(certificate, private_key)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="f3-fixed-policy", daemon=False
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            raise F3TargetEpisodeError("fixed policy server did not terminate")
        if self.failure is not None:
            raise F3TargetEpisodeError(
                "fixed policy server rejected a request"
            ) from self.failure


def _resolution_request(
    spec: F3TargetEpisodeInput,
    build: F3CompositionBuildResult,
) -> tuple[c.ResolveEpisodeRequest, c.AdmissionReceipt, c.DirectSelector]:
    manifest_path = Path(build.composition_manifest_path)
    artifacts = manifest_path.parent
    receipt_raw = (artifacts / "admission-receipt.json").read_bytes()
    selector_raw = (artifacts / "direct-selector.json").read_bytes()
    receipt = c.AdmissionReceipt.model_validate_json(receipt_raw, strict=True)
    selector = c.DirectSelector.model_validate_json(selector_raw, strict=True)
    selector_digest = sha256_bytes(selector_raw)
    selector_ref = c.DirectSelectorRef(
        digest=selector_digest,
        ref=c.ArtifactRef(
            artifact_id=selector_digest,
            sha256=selector_digest,
            size_bytes=len(selector_raw),
            media_type="application/vnd.breadboard.direct-selector+json;version=1",
        ),
    )
    request = c.ResolveEpisodeRequest(
        episode_id=spec.episode_id,
        subject=receipt.subject,
        selector=selector_ref,
        selection_nonce=None,
        task=spec.composition.resolution_task,
        policy_binding=receipt.policy_binding_ref,
        episode_overlays=(),
    )
    return request, receipt, selector


def _validate_resolved_policy(
    spec: F3TargetEpisodeInput,
    composition: Any,
    create: Any,
) -> c.EffectiveExecutionPlan:
    response = create.response
    effective_ref = response.effective_plan_ref
    raw = composition.authority_graph.store.load(
        effective_ref.sha256,
        kind=c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN,
        max_bytes=16 * 1024 * 1024,
    )
    plan = c.EffectiveExecutionPlan.model_validate_json(raw, strict=True)
    if plan.canonical_digest() != response.effective_plan_digest:
        raise F3TargetEpisodeError("created effective plan digest mismatch")
    if plan.runner.adapter_id != TERMINAL_ADAPTER_ID or len(plan.policy_slots) != 1:
        raise F3TargetEpisodeError(
            "resolved F3 plan is not the exact terminal policy plan"
        )
    slot = plan.policy_slots[0]
    expected_model = spec.policy.model
    if (
        slot.slot_id != spec.policy.slot_id
        or slot.route_id != spec.policy.route_id
        or (slot.model_digest, slot.tokenizer_digest, slot.checkpoint_digest)
        != (
            expected_model.model_digest,
            expected_model.tokenizer_digest,
            expected_model.checkpoint_digest,
        )
        or response.policy_capability_observation_digest
        != spec.policy.policy_capability_observation_digest
    ):
        raise F3TargetEpisodeError(
            "resolved policy model/checkpoint/slot/observation identity mismatch"
        )
    authority = composition.authority_graph
    routes = [
        item
        for item in authority.policy_http.routes
        if item.grant.route_id == slot.route_id
    ]
    observations = [
        item
        for item in authority.policy_http.observations
        if item.route_id == slot.route_id
    ]
    if (
        len(routes) != 1
        or len(observations) != 1
        or routes[0].grant.route_revision_digest != spec.policy.route_revision_digest
        or observations[0].canonical_digest()
        != spec.policy.policy_capability_observation_digest
        or observations[0].model_id != expected_model.model_id
    ):
        raise F3TargetEpisodeError(
            "resolved route or capability observation authority mismatch"
        )
    return plan


def _image_joins(
    spec: F3TargetEpisodeInput, plan: c.EffectiveExecutionPlan
) -> tuple[dict[str, Any], ...]:
    installed = spec.composition.installed
    daemon = installed.private_docker_daemon
    if daemon is None:
        raise F3TargetEpisodeError("private Docker authority is absent")
    by_source = {item.source_image_digest: item for item in daemon.images}
    roles = (
        ("primary", plan.sandbox.image_digest),
        ("verifier", plan.verifier.image_digest),
    )
    joins: list[dict[str, Any]] = []
    for role, digest in roles:
        offline = by_source.get(digest)
        image = next(
            (item for item in installed.images if item.image_digest == digest), None
        )
        if offline is None or image is None:
            raise F3TargetEpisodeError(f"{role} image/archive authority is absent")
        joins.append(
            {
                "role": role,
                "source_image_digest": digest,
                "immutable_reference": image.immutable_reference,
                "loaded_image_id": offline.image_id,
                "archive_path": offline.archive.path,
                "archive_sha256": offline.archive.digest,
            }
        )
    return tuple(joins)


_WorkspaceQuotaRoot = TmpfsQuotaRootAuthority
_InheritedTmpfsQuotaStorageBackend = PreMountedTmpfsQuotaStorageBackend


def _install_exact_quota_backend(
    service: Any,
    quota_root: _WorkspaceQuotaRoot,
) -> None:
    runtime = service._dependencies.sandbox_runtime
    store = runtime.materialization_store
    current = store.storage_backend
    if not isinstance(current, DirectoryStorageBackend):
        raise F3TargetEpisodeError(
            "F3 materialization storage backend is not replaceable"
        )
    workspace_root_fd = getattr(store, "_workspace_root_fd", None)
    if type(workspace_root_fd) is not int:
        raise F3TargetEpisodeError("F3 workspace root descriptor authority is absent")
    replacement = _InheritedTmpfsQuotaStorageBackend(quota_root)
    try:
        replacement.bind_root(workspace_root_fd)
        replacement._verify_root()
        current.close_root()
    except BaseException as primary:
        try:
            replacement.close_root()
        except BaseException as cleanup:
            raise BaseExceptionGroup(
                "F3 quota backend installation and cleanup failed",
                [primary, cleanup],
            ) from None
        raise
    store.storage_backend = replacement


async def _run_f3_target_episode_under_lease(
    spec: F3TargetEpisodeInput,
    input_digest: str,
    cleanup_owner: _EpisodeCleanupOwner,
    external_authority: _EpisodeExternalAuthority,
) -> F3TargetEpisodeReport:
    job_id = os.environ.get("SLURM_JOB_ID", "")
    node_list = os.environ.get(
        "SLURM_JOB_NODELIST", os.environ.get("SLURM_NODELIST", "")
    )
    if not job_id.isdigit() or not node_list:
        raise F3TargetEpisodeError(
            "numeric SLURM_JOB_ID and nonempty Slurm node observation are required"
        )
    recovered = _recover_after_cleanup(
        spec, cleanup_owner, external_authority, input_digest
    )
    if recovered is not None:
        return recovered
    recovered = await _resume_pre_cleanup_export(
        spec, cleanup_owner, external_authority, input_digest
    )
    if recovered is not None:
        return recovered
    evidence = _read_generation_evidence(spec)
    quota_root = _WorkspaceQuotaRoot(
        spec.composition.stores.workspace,
        spec.workspace_quota_bytes,
    )
    composition = None
    try:
        quota_root.mount()
        build = build_f3_production_composition(
            spec.composition,
            spec.composition_output_dir,
        )
        composition = load_f3_production_composition(
            build,
            spec.composition.secrets.files,
        )
        service = composition.app.state.episode_service
        _install_exact_quota_backend(service, quota_root)
        request, receipt, selector = _resolution_request(spec, build)
    except BaseException as primary:
        cleanup_errors: list[BaseException] = []
        pre_cleanup_receipt: dict[str, Any] | None = None
        setup_cleanup_receipt: dict[str, Any] | None = None
        try:
            pre_cleanup_receipt = _export_durable_evidence(
                spec,
                cleanup_owner,
                external_authority,
                report=None,
                failure=primary,
            )
        except BaseException as exc:
            cleanup_errors.append(exc)
        if composition is not None:
            try:
                await composition.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            quota_root.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if pre_cleanup_receipt is not None:
            try:
                external_authority.revalidate(cleanup_owner)
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                setup_cleanup_receipt = cleanup_owner.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if pre_cleanup_receipt is None:
            cleanup_errors.append(
                F3TargetEpisodeError(
                    "durable evidence export failed; cleanup was intentionally withheld"
                )
            )
        terminal_failure: BaseException = (
            BaseExceptionGroup(
                "F3 target episode setup and cleanup failed",
                [primary, *cleanup_errors],
            )
            if cleanup_errors
            else primary
        )
        if pre_cleanup_receipt is not None:
            try:
                _publish_terminal_cleanup_failure(
                    spec,
                    cleanup_owner,
                    external_authority,
                    input_digest,
                    pre_cleanup_receipt,
                    terminal_failure,
                    setup_cleanup_receipt,
                )
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise BaseExceptionGroup(
                "F3 target episode setup and cleanup failed",
                [primary, *cleanup_errors],
            ) from None
        raise
    created: Any | None = None
    closed: Any | None = None
    policy_server: _FixedPolicyServer | None = None
    run: Any | None = None
    policy_requests: tuple[dict[str, Any], ...] = ()
    report: F3TargetEpisodeReport | None = None
    cleanup_receipt: dict[str, Any] | None = None
    evidence_export_receipt: dict[str, Any] | None = None
    try:
        await service.start()
        created = await service.create(request)
        plan = _validate_resolved_policy(spec, composition, created)
        resources = plan.effective_semantics.get("resources")
        if (
            not isinstance(resources, Mapping)
            or resources.get("storage_bytes") != spec.workspace_quota_bytes
        ):
            raise F3TargetEpisodeError(
                "F3 effective plan quota does not bind quota root"
            )
        route = composition.authority_graph.policy_http.routes[0]
        parsed_authority = route.authority.rsplit(":", 1)
        if len(parsed_authority) != 2 or not parsed_authority[1].isdigit():
            raise F3TargetEpisodeError(
                "policy route authority does not contain one exact port"
            )
        host, port_text = parsed_authority
        policy_handle = next(
            item
            for item in spec.composition.secrets.handles.records
            if item.purpose == "policy_callback"
        )
        credential = (
            Path(spec.composition.secrets.files[policy_handle.handle_id])
            .read_text(encoding="utf-8")
            .strip()
        )
        response_payload = {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "apply-agent-candidate-patch",
                    "name": "shell",
                    "arguments": canonical_json_bytes(
                        {"command": spec.policy.patch_application_command}
                    ).decode("utf-8"),
                },
                {
                    "type": "function_call",
                    "call_id": "submit-agent-candidate-patch",
                    "name": "submit",
                    "arguments": '{"result":"agent-candidate patch applied"}',
                },
            ]
        }
        policy_server = _FixedPolicyServer(
            host=host,
            port=int(port_text),
            path=route.paths[0],
            certificate=spec.composition.policy_tls.leaf_certificate.path,
            private_key=spec.composition.policy_tls.leaf_private_key.path,
            credential=credential,
            expected_episode_id=spec.episode_id,
            expected_effective_plan_digest=created.response.effective_plan_digest,
            expected_binding_digest=created.response.policy_binding_digest,
            expected_slot_id=spec.policy.slot_id,
            expected_model_id=spec.policy.model.model_id,
            response_payload=response_payload,
        )
        policy_server.start()
        visible_input = {
            "task_id": spec.task_id,
            "repository_snapshot_digest": spec.refs.repository.digest,
            "prompt": spec.policy_visible_prompt,
        }
        if _contains_forbidden_role(visible_input):
            raise F3TargetEpisodeError(
                "policy-visible task input contains a forbidden role"
            )
        run = await service.run(
            spec.episode_id,
            create_fingerprint=created.response.create_fingerprint,
            task_input={
                "responses_create_params": {
                    "model": spec.policy.model.model_id,
                    "input": canonical_json_bytes(visible_input).decode("utf-8"),
                }
            },
            context={
                "task_ref": spec.refs.task.immutable_reference,
                "repository_ref": spec.refs.repository.immutable_reference,
                "generation_ref": spec.refs.generation.immutable_reference,
            },
        )
        if policy_server is not None:
            policy_requests = tuple(policy_server.requests)
            policy_server.close()
            policy_server = None
        closed = await service.close_episode(spec.episode_id)
        run_wire = _wire(run.response)
        closed_wire = _wire(closed.response)
        if run_wire.get("primary_disposition") != "succeeded":
            raise F3TargetEpisodeError("F3 primary lifecycle did not succeed")
        if run_wire.get("reward") != 1:
            raise F3TargetEpisodeError("F3 verifier returned a false reward")
        if len(policy_requests) != 1:
            raise F3TargetEpisodeError(
                "fixed policy did not receive exactly one terminal request"
            )
        lease_entries = tuple(
            sorted(item.name for item in os.scandir(spec.composition.stores.lease))
        )
        cleanup_disposition = closed_wire.get("cleanup_disposition")
        released = cleanup_disposition == "released"
        no_orphan = released and not lease_entries
        images = _image_joins(spec, plan)
        create_wire = _wire(created.response)
        report = F3TargetEpisodeReport(
            schema_version="bb.rl.phase5-f3-target-episode-report.v1",
            scheduler={
                "slurm_job_id": job_id,
                "slurm_node_list": node_list,
                "hostname": socket.gethostname(),
            },
            inputs={
                "target_episode_input_sha256": input_digest,
                "authority_manifest_sha256": spec.composition.authority_manifest.sha256,
                "composition_manifest_sha256": composition.manifest.input_manifest_digest,
            },
            authorities={
                "task_id": spec.task_id,
                "task_contract_digest": spec.refs.task.digest,
                "task_ref": spec.refs.task.immutable_reference,
                "repository_snapshot_digest": spec.refs.repository.digest,
                "repository_ref": spec.refs.repository.immutable_reference,
                "generation_evidence_sha256": spec.policy.generation_evidence.sha256,
                "patch_application_command_sha256": evidence.command_sha256,
                "generation_ref": spec.refs.generation.immutable_reference,
                "authority_bundle_digest": composition.manifest.authority_bundle_digest,
                "admission_receipt_digest": receipt.canonical_digest(),
                "admitted_set_root": selector.admitted_set_root,
            },
            images=images,
            resolution={
                "selector_digest": plan.selector_digest,
                "selection_record_ref": create_wire["selection_record_ref"],
                "selection_commit": create_wire["selection_commit"],
                "effective_plan_ref": create_wire["effective_plan_ref"],
                "effective_plan_digest": create_wire["effective_plan_digest"],
                "policy_capability_observation_digest": create_wire[
                    "policy_capability_observation_digest"
                ],
                "runner": _wire(plan.runner),
                "policy_slot": _wire(plan.policy_slots[0]),
            },
            lifecycle={
                "create": create_wire,
                "run": run_wire,
                "close": closed_wire,
                "terminal_request_count": len(policy_requests),
            },
            artifacts={
                "result_ref": run_wire.get("result_ref"),
                "evidence_manifest_ref": run_wire.get("evidence_manifest_ref"),
                "evidence_root": run_wire.get("evidence_root"),
                "artifact_manifest_ref": run_wire.get("artifact_manifest_ref"),
                "completed_envelope_ref": run_wire.get("completed_envelope_ref"),
                "closed_envelope_ref": closed_wire.get("closed_envelope_ref"),
            },
            verifier={
                "passed": run_wire.get("reward") == 1,
                "reward": run_wire.get("reward"),
                "reward_components": run_wire.get("reward_components", {}),
                "verifier_result_digest": run_wire.get("verifier_result_digest"),
                "primary_measurement_digest": run_wire.get(
                    "primary_measurement_digest"
                ),
                "verifier_measurement_digest": run_wire.get(
                    "verifier_measurement_digest"
                ),
            },
            cleanup={
                "cleanup_disposition": cleanup_disposition,
                "released": released,
                "lease_root_entries": list(lease_entries),
                "local_composition_retained_for_evidence": True,
                "no_orphan": no_orphan,
            },
            evidence_export=None,
            claim_boundary=_CLAIM_BOUNDARY,
        )
        output = (
            Path(spec.composition.stores.service_output_root)
            / f"{spec.episode_id}.report.json"
        )
        fd = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o440,
        )
        try:
            raw = canonical_json_bytes(report.model_dump(mode="json"))
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        active_error = sys.exception()
        errors: list[BaseException] = []
        if policy_server is not None:
            try:
                policy_server.close()
            except BaseException as exc:
                errors.append(exc)
        if created is not None and closed is None:
            try:
                await service.close_episode(spec.episode_id)
            except BaseException as exc:
                errors.append(exc)
        export_failure = active_error if active_error is not None else (
            errors[0] if errors else None
        )
        try:
            evidence_export_receipt = _export_durable_evidence(
                spec,
                cleanup_owner,
                external_authority,
                report=report,
                failure=export_failure,
            )
        except BaseException as exc:
            errors.append(exc)
        try:
            await composition.close()
        except BaseException as exc:
            errors.append(exc)
        try:
            quota_root.close()
        except BaseException as exc:
            errors.append(exc)
        if evidence_export_receipt is not None:
            try:
                external_authority.revalidate(cleanup_owner)
            except BaseException as exc:
                errors.append(exc)
            try:
                cleanup_receipt = cleanup_owner.close()
            except BaseException as exc:
                errors.append(exc)
        else:
            errors.append(
                F3TargetEpisodeError(
                    "durable evidence export failed; cleanup was intentionally withheld"
                )
            )
        terminal_failure: BaseException | None = None
        if active_error is not None:
            terminal_failure = (
                BaseExceptionGroup(
                    "F3 target episode execution and cleanup failed",
                    [active_error, *errors],
                )
                if errors
                else active_error
            )
        elif errors:
            terminal_failure = BaseExceptionGroup(
                "F3 target episode cleanup failed", errors
            )
        if (
            terminal_failure is not None
            and evidence_export_receipt is not None
        ):
            try:
                _publish_terminal_cleanup_failure(
                    spec,
                    cleanup_owner,
                    external_authority,
                    input_digest,
                    evidence_export_receipt,
                    terminal_failure,
                    cleanup_receipt,
                )
            except BaseException as exc:
                errors.append(exc)
        if errors and active_error is not None:
            raise BaseExceptionGroup(
                "F3 target episode execution and cleanup failed",
                [active_error, *errors],
            )
        if errors:
            raise BaseExceptionGroup("F3 target episode cleanup failed", errors)
    if (
        report is None
        or cleanup_receipt is None
        or evidence_export_receipt is None
    ):
        raise F3TargetEpisodeError(
            "F3 target episode did not produce a cleanup-bound durable report"
        )
    cleanup_bound = _cleanup_bound_report(
        report,
        cleanup_receipt,
        evidence_export_receipt,
    )
    final_evidence_receipt = _publish_final_evidence(
        spec,
        cleanup_owner,
        external_authority,
        cleanup_bound,
        cleanup_receipt,
        evidence_export_receipt,
    )
    return _cleanup_bound_report(
        report,
        cleanup_receipt,
        {
            "pre_cleanup": evidence_export_receipt,
            "final": final_evidence_receipt,
        },
    )


async def _run_f3_target_episode(
    spec: F3TargetEpisodeInput, input_digest: str
) -> F3TargetEpisodeReport:
    cleanup_owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    external_authority: _EpisodeExternalAuthority | None = None
    lease: _EpisodeExecutionLease | None = None
    report: F3TargetEpisodeReport | None = None
    primary: BaseException | None = None
    try:
        external_authority = _EpisodeExternalAuthority(spec)
        external_authority.revalidate(cleanup_owner)
        lease = _EpisodeExecutionLease(
            spec,
            input_digest,
            external_authority,
            cleanup_owner,
        )
        report = await _run_f3_target_episode_under_lease(
            spec,
            input_digest,
            cleanup_owner,
            external_authority,
        )
    except BaseException as exc:
        primary = exc
    finalizer_errors: list[BaseException] = []
    if lease is not None:
        try:
            lease.close()
        except BaseException as exc:
            finalizer_errors.append(exc)
    if external_authority is not None:
        try:
            external_authority.close()
        except BaseException as exc:
            finalizer_errors.append(exc)
    if not cleanup_owner.closed:
        try:
            cleanup_owner.release()
        except BaseException as exc:
            finalizer_errors.append(exc)
    if finalizer_errors:
        if primary is not None:
            finalizer_errors.insert(0, primary)
        raise BaseExceptionGroup(
            "F3 target episode lifecycle finalization failed",
            finalizer_errors,
        ) from None
    if primary is not None:
        raise primary.with_traceback(primary.__traceback__)
    assert report is not None
    return report


def run_f3_target_episode(
    spec: F3TargetEpisodeInput, *, input_digest: str
) -> F3TargetEpisodeReport:
    if type(spec) is not F3TargetEpisodeInput:
        raise TypeError("spec must be an exact F3TargetEpisodeInput")
    _digest(input_digest)
    return asyncio.run(_run_f3_target_episode(spec, input_digest))


def _read_input(path: str) -> tuple[F3TargetEpisodeInput, str]:
    source = Path(path).resolve(strict=True)
    raw = source.read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F3TargetEpisodeError("target episode input is not canonical JSON")
    return F3TargetEpisodeInput.model_validate_json(raw, strict=True), sha256_bytes(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one closed F3 R-SWE-001 production episode"
    )
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    spec, input_digest = _read_input(args.input)
    report = run_f3_target_episode(spec, input_digest=input_digest)
    os.write(1, canonical_json_bytes(report.model_dump(mode="json")) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
