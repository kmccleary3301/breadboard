from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    InstalledV1,
    OfflineImageAuthorityV1,
    PinnedFileAuthorityV1,
    PrivateDockerDaemonAuthorityV1,
)
from breadboard.rl.harness.runners.base import RunnerAdapterDescriptor
from breadboard.rl.harness.sandbox import (
    InstalledImage,
    InstalledRuntime,
    InstalledVerifier,
    SandboxNetworkPolicy,
    SandboxSecurityPolicy,
)
from breadboard.rl.phase5.f2_authority_authoring import (
    F2C4TargetDynamicObservations,
    F2C4TargetDynamicPlanInput,
    author_f2_target_dynamic_authority,
)

_DIGEST_PREFIX = "sha256:"
_EXECUTABLE_PATHS = {
    "containerd": "/usr/bin/containerd",
    "docker": "/usr/bin/docker",
    "dockerd": "/usr/bin/dockerd",
    "runc": "/usr/bin/runc",
}
_PRIMARY_RUNTIME_ID = "primary"
_VERIFIER_RUNTIME_ID = "verifier-runtime"
_VERIFIER_ID = "exact-output"
_VERIFIER_ARGV = ("/opt/breadboard-f2/verifier",)
_RESULT_RELATIVE_PATH = "result.txt"
_RESULT_ABSOLUTE_PATH = "/workspace/work/result.txt"
_TASK_OUTPUT = b"breadboard-f2-terminal-ok\n"


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _is_digest(value: str) -> bool:
    return (
        type(value) is str
        and value.startswith(_DIGEST_PREFIX)
        and len(value) == len(_DIGEST_PREFIX) + 64
        and all(character in "0123456789abcdef" for character in value[len(_DIGEST_PREFIX) :])
    )


def _digest_bytes(value: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value).hexdigest()


def _normalized_absolute(value: str, *, label: str) -> str:
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(value) != value:
        raise ValueError(f"{label} must be an absolute normalized path")
    return value


class ExecutableAuthoritiesInput(_ExactModel):
    containerd: PinnedFileAuthorityV1
    docker: PinnedFileAuthorityV1
    dockerd: PinnedFileAuthorityV1
    runc: PinnedFileAuthorityV1

    @model_validator(mode="after")
    def exact_paths(self) -> "ExecutableAuthoritiesInput":
        for name, expected_path in _EXECUTABLE_PATHS.items():
            authority = getattr(self, name)
            if authority.path != expected_path or not authority.executable:
                raise ValueError(f"{name} executable authority is not exact")
        return self


class RuntimeInput(_ExactModel):
    runtime_id: Literal["primary", "verifier-runtime"]
    driver_implementation_digest: str
    oci_runtime_name: Literal["breadboard-runc"]
    supported_platform_versions: tuple[str, ...]
    fixed_environment: tuple[tuple[str, str], ...]
    idle_argv: tuple[str, ...]

    @field_validator("driver_implementation_digest")
    @classmethod
    def digest(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("runtime driver implementation digest is invalid")
        return value

    @model_validator(mode="after")
    def exact_runtime(self) -> "RuntimeInput":
        if not self.supported_platform_versions or not self.idle_argv:
            raise ValueError("runtime platform and idle argv authorities must be explicit")
        if self.fixed_environment != tuple(sorted(self.fixed_environment)):
            raise ValueError("runtime fixed environment must be sorted")
        return self


class ImageInput(_ExactModel):
    runtime_id: Literal["primary", "verifier-runtime"]
    observed_image_id: str
    immutable_reference: str

    @field_validator("observed_image_id")
    @classmethod
    def digest(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("observed image ID is invalid")
        return value

    @model_validator(mode="after")
    def exact_reference(self) -> "ImageInput":
        if not self.immutable_reference.endswith("@" + self.observed_image_id):
            raise ValueError("immutable image reference does not bind the observed image ID")
        return self


class SecurityPolicyInput(_ExactModel):
    expected_policy_digest: str
    uid: int = Field(ge=0)
    gid: int = Field(ge=0)
    read_only_root: bool
    drop_all_capabilities: bool
    no_new_privileges: bool
    seccomp_document: dict[str, Any]
    expected_seccomp_digest: str
    apparmor_profile: str | None
    selinux_label: str | None
    namespace_flags: tuple[str, ...]
    privileged: bool
    devices: tuple[str, ...]
    docker_socket_forbidden: bool
    tmpfs_mounts: tuple[tuple[str, str], ...]
    snapshot_max_depth: int = Field(ge=0)
    snapshot_max_files: int = Field(ge=0)
    snapshot_max_inodes: int = Field(ge=0)

    @field_validator("expected_policy_digest", "expected_seccomp_digest")
    @classmethod
    def digests(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("security policy digest is invalid")
        return value


class NetworkPolicyInput(_ExactModel):
    expected_policy_digest: str
    mode: Literal["none"]
    docker_network: Literal["none"]
    egress_route_ids: tuple[()] = ()
    default_deny: Literal[True]

    @field_validator("expected_policy_digest")
    @classmethod
    def digest(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("network policy digest is invalid")
        return value


class VerifierInput(_ExactModel):
    grant: c.VerifierGrant
    runtime_id: Literal["verifier-runtime"]
    security_policy_digest: str
    argv: tuple[str, ...]
    result_relative_path: str

    @field_validator("security_policy_digest")
    @classmethod
    def digest(cls, value: str) -> str:
        if not _is_digest(value):
            raise ValueError("verifier security policy digest is invalid")
        return value

    @model_validator(mode="after")
    def exact_verifier(self) -> "VerifierInput":
        if (
            self.grant.verifier_id != _VERIFIER_ID
            or self.argv != _VERIFIER_ARGV
            or self.result_relative_path != _RESULT_RELATIVE_PATH
        ):
            raise ValueError("verifier command and output authority are not exact")
        return self


class PrivateDaemonInput(_ExactModel):
    storage_driver: Literal["vfs", "overlay2"]
    log_limit_bytes: int = Field(ge=4096, le=1024 * 1024)
    runtime_name: Literal["breadboard-runc"]


class InstalledAuthoritiesInput(_ExactModel):
    runner_adapters: tuple[RunnerAdapterDescriptor, ...]
    executables: ExecutableAuthoritiesInput
    combined_image_archive: PinnedFileAuthorityV1
    primary_runtime: RuntimeInput
    verifier_runtime: RuntimeInput
    primary_image: ImageInput
    verifier_image: ImageInput
    primary_security_policy: SecurityPolicyInput
    verifier_security_policy: SecurityPolicyInput
    network_policy: NetworkPolicyInput
    verifier: VerifierInput
    private_daemon: PrivateDaemonInput

    @model_validator(mode="after")
    def exact_roles(self) -> "InstalledAuthoritiesInput":
        if (
            self.primary_runtime.runtime_id != _PRIMARY_RUNTIME_ID
            or self.verifier_runtime.runtime_id != _VERIFIER_RUNTIME_ID
            or self.primary_image.runtime_id != _PRIMARY_RUNTIME_ID
            or self.verifier_image.runtime_id != _VERIFIER_RUNTIME_ID
            or self.verifier.runtime_id != _VERIFIER_RUNTIME_ID
        ):
            raise ValueError("installed runtime and image roles are not exact")
        primary_mechanics = self.primary_runtime.model_dump(exclude={"runtime_id"})
        verifier_mechanics = self.verifier_runtime.model_dump(exclude={"runtime_id"})
        if primary_mechanics != verifier_mechanics:
            raise ValueError("hardened runtimes must share private daemon mechanics")
        if self.combined_image_archive.executable:
            raise ValueError("combined image archive must be non-executable")
        if len(self.runner_adapters) != 1:
            raise ValueError("F2 requires exactly one runner adapter authority")
        return self


class TargetDynamicPacketAuthoringInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-c4-target-dynamic-packet-authoring-input.v1"]
    task_output_path: str
    task_output_utf8: str
    plan: F2C4TargetDynamicPlanInput
    observations_template: dict[str, Any]
    installed: InstalledAuthoritiesInput

    @model_validator(mode="after")
    def exact_observation_template(self) -> "TargetDynamicPacketAuthoringInput":
        expected = set(F2C4TargetDynamicObservations.model_fields) - {"installed"}
        actual = set(self.observations_template)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"observation template fields mismatch: missing={missing}, extra={extra}")
        if self.observations_template.get("attempt_id") != self.plan.attempt_id:
            raise ValueError("plan and observation template attempt IDs differ")
        if (
            self.task_output_path != _RESULT_ABSOLUTE_PATH
            or self.task_output_utf8.encode("utf-8") != _TASK_OUTPUT
        ):
            raise ValueError("task output authority is not exact")
        return self


def _observe_regular(
    path: Path, *, label: str, capture: bool
) -> tuple[bytes | None, str, os.stat_result]:
    raw_path = os.fspath(path)
    _normalized_absolute(raw_path, label=label)
    if path.parent.resolve(strict=True) != path.parent:
        raise ValueError(f"{label} path cannot traverse a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(raw_path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be an exact regular file")
        digest = hashlib.sha256()
        captured = bytearray() if capture else None
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if captured is not None:
                captured.extend(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            stat.S_IMODE(value.st_mode),
            value.st_uid,
        )
        if identity(before) != identity(after):
            raise ValueError(f"{label} changed while it was observed")
        current = os.lstat(raw_path)
        if stat.S_ISLNK(current.st_mode) or identity(current) != identity(after):
            raise ValueError(f"{label} path identity changed while it was observed")
        raw = bytes(captured) if captured is not None else None
        return raw, _DIGEST_PREFIX + digest.hexdigest(), after
    finally:
        os.close(descriptor)


def _observe_expected_file(expected: PinnedFileAuthorityV1, *, label: str) -> PinnedFileAuthorityV1:
    _raw, digest, observed = _observe_regular(Path(expected.path), label=label, capture=False)
    mode = stat.S_IMODE(observed.st_mode)
    executable = bool(mode & 0o111)
    actual = PinnedFileAuthorityV1(
        path=expected.path,
        digest=digest,
        owner_uid=observed.st_uid,
        mode=mode,
        executable=executable,
    )
    if actual != expected:
        raise ValueError(f"{label} authority mismatch")
    return actual


def _security_policy(source: SecurityPolicyInput) -> SandboxSecurityPolicy:
    seccomp_bytes = canonical_json_bytes(source.seccomp_document)
    seccomp_digest = _digest_bytes(seccomp_bytes)
    if seccomp_digest != source.expected_seccomp_digest:
        raise ValueError("seccomp document digest mismatch")
    values = {
        "uid": source.uid,
        "gid": source.gid,
        "read_only_root": source.read_only_root,
        "drop_all_capabilities": source.drop_all_capabilities,
        "no_new_privileges": source.no_new_privileges,
        "seccomp_digest": seccomp_digest,
        "apparmor_profile": source.apparmor_profile,
        "selinux_label": source.selinux_label,
        "namespace_flags": list(source.namespace_flags),
        "privileged": source.privileged,
        "devices": list(source.devices),
        "docker_socket_forbidden": source.docker_socket_forbidden,
        "tmpfs_mounts": [list(item) for item in source.tmpfs_mounts],
        "snapshot_max_depth": source.snapshot_max_depth,
        "snapshot_max_files": source.snapshot_max_files,
        "snapshot_max_inodes": source.snapshot_max_inodes,
    }
    policy_digest = SandboxSecurityPolicy.derive_digest(values)
    if policy_digest != source.expected_policy_digest:
        raise ValueError("security policy derived digest mismatch")
    return SandboxSecurityPolicy(
        policy_digest=policy_digest,
        uid=source.uid,
        gid=source.gid,
        read_only_root=source.read_only_root,
        drop_all_capabilities=source.drop_all_capabilities,
        no_new_privileges=source.no_new_privileges,
        seccomp_bytes=seccomp_bytes,
        seccomp_digest=seccomp_digest,
        apparmor_profile=source.apparmor_profile,
        selinux_label=source.selinux_label,
        namespace_flags=source.namespace_flags,
        privileged=source.privileged,
        devices=source.devices,
        docker_socket_forbidden=source.docker_socket_forbidden,
        tmpfs_mounts=source.tmpfs_mounts,
        snapshot_max_depth=source.snapshot_max_depth,
        snapshot_max_files=source.snapshot_max_files,
        snapshot_max_inodes=source.snapshot_max_inodes,
    )


def _network_policy(source: NetworkPolicyInput) -> SandboxNetworkPolicy:
    values = {
        "mode": source.mode,
        "docker_network": source.docker_network,
        "egress_route_ids": list(source.egress_route_ids),
        "default_deny": source.default_deny,
    }
    policy_digest = SandboxNetworkPolicy.derive_digest(values)
    if policy_digest != source.expected_policy_digest:
        raise ValueError("network policy derived digest mismatch")
    return SandboxNetworkPolicy(
        policy_digest=policy_digest,
        mode=source.mode,
        docker_network=source.docker_network,
        egress_route_ids=source.egress_route_ids,
        default_deny=source.default_deny,
    )


def _runtime(source: RuntimeInput, docker: PinnedFileAuthorityV1, runc: PinnedFileAuthorityV1) -> InstalledRuntime:
    return InstalledRuntime(
        runtime_id=source.runtime_id,
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest=source.driver_implementation_digest,
        executable_path=docker.path,
        measured_binary_digest=docker.digest,
        oci_runtime_name=source.oci_runtime_name,
        supported_platform_versions=source.supported_platform_versions,
        fixed_environment=source.fixed_environment,
        idle_argv=source.idle_argv,
        oci_runtime_binary_path=runc.path,
        oci_runtime_binary_digest=runc.digest,
    )


def _private_paths(private_root: Path, attempt_id: str) -> dict[str, str]:
    root = private_root / ("f2-docker-" + attempt_id)
    names = {
        "config_path": "daemon.json",
        "socket_path": "docker.sock",
        "pid_file": "dockerd.pid",
        "data_root": "data",
        "exec_root": "exec",
        "mount_stage_root": "mount-stage",
        "containerd_socket_path": "containerd.sock",
        "containerd_root": "containerd-root",
        "containerd_state": "containerd-state",
        "log_root": "logs",
    }
    values = {key: os.fspath(root / suffix) for key, suffix in names.items()}
    if len(set(values.values())) != len(values):
        raise AssertionError("private Docker path construction is not unique")
    return values


def _build_installed(source: InstalledAuthoritiesInput, *, private_root: Path, attempt_id: str) -> InstalledV1:
    observed_executables = {
        name: _observe_expected_file(getattr(source.executables, name), label=name)
        for name in sorted(_EXECUTABLE_PATHS)
    }
    archive = _observe_expected_file(source.combined_image_archive, label="combined image archive")
    primary_security = _security_policy(source.primary_security_policy)
    verifier_security = _security_policy(source.verifier_security_policy)
    network = _network_policy(source.network_policy)
    if primary_security.policy_digest == verifier_security.policy_digest:
        raise ValueError("primary and verifier security policies must be distinct")
    if source.verifier.security_policy_digest != verifier_security.policy_digest:
        raise ValueError("verifier security grant does not bind the verifier policy")
    primary_image = InstalledImage(
        image_digest=source.primary_image.observed_image_id,
        runtime_id=source.primary_image.runtime_id,
        immutable_reference=source.primary_image.immutable_reference,
    )
    verifier_image = InstalledImage(
        image_digest=source.verifier_image.observed_image_id,
        runtime_id=source.verifier_image.runtime_id,
        immutable_reference=source.verifier_image.immutable_reference,
    )
    if primary_image.image_digest == verifier_image.image_digest:
        raise ValueError("primary and verifier observed image IDs must be distinct")
    grant = source.verifier.grant
    if (
        grant.image_digest != verifier_image.image_digest
        or grant.network_policy_digest != network.policy_digest
    ):
        raise ValueError("verifier grant does not bind verifier image and network policy")
    verifier = InstalledVerifier(
        grant=grant,
        runtime_id=source.verifier.runtime_id,
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        security_policy_digest=verifier_security.policy_digest,
        argv=source.verifier.argv,
        result_relative_path=source.verifier.result_relative_path,
        executable_digest=grant.executable_digest,
        code_digest=grant.code_digest,
        input_schema_digest=grant.input_schema_digest,
        result_schema_digest=grant.result_schema_digest,
    )
    offline_images = tuple(
        sorted(
            (
                OfflineImageAuthorityV1(
                    archive=archive,
                    image_id=primary_image.image_digest,
                    source_image_digest=primary_image.image_digest,
                ),
                OfflineImageAuthorityV1(
                    archive=archive,
                    image_id=verifier_image.image_digest,
                    source_image_digest=verifier_image.image_digest,
                ),
            ),
            key=lambda value: value.image_id,
        )
    )
    daemon = PrivateDockerDaemonAuthorityV1(
        daemon_instance_id="f2-docker-" + attempt_id,
        dockerd=observed_executables["dockerd"],
        docker=observed_executables["docker"],
        runc=observed_executables["runc"],
        containerd=observed_executables["containerd"],
        **_private_paths(private_root, attempt_id),
        log_limit_bytes=source.private_daemon.log_limit_bytes,
        storage_driver=source.private_daemon.storage_driver,
        runtime_name=source.private_daemon.runtime_name,
        images=offline_images,
    )
    runtimes = tuple(
        sorted(
            (
                _runtime(source.primary_runtime, observed_executables["docker"], observed_executables["runc"]),
                _runtime(source.verifier_runtime, observed_executables["docker"], observed_executables["runc"]),
            ),
            key=lambda value: value.runtime_id,
        )
    )
    return InstalledV1(
        runner_adapters=tuple(sorted(source.runner_adapters, key=lambda value: (value.adapter_id, value.runtime_abi))),
        runtimes=runtimes,
        images=tuple(sorted((primary_image, verifier_image), key=lambda value: value.image_digest)),
        security_policies=tuple(sorted((primary_security, verifier_security), key=lambda value: value.policy_digest)),
        network_policies=(network,),
        verifiers=(verifier,),
        private_docker_daemon=daemon,
    )


def _validate_external_root(path: Path, *, label: str) -> Path:
    raw = os.fspath(path)
    _normalized_absolute(raw, label=label)
    if path == Path(path.anchor):
        raise ValueError(f"{label} cannot be the filesystem root")
    resolved_parent = path.parent.resolve(strict=True)
    if resolved_parent != path.parent:
        raise ValueError(f"{label} cannot traverse a symlink")
    if os.path.lexists(path):
        raise FileExistsError(f"{label} already exists")
    resolved = path
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ValueError(f"{label} must be outside the source bundle")
    return resolved


def _write_new_packet(output_path: Path, payload: bytes) -> None:
    parent_fd = os.open(output_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    descriptor = -1
    try:
        descriptor = os.open(
            output_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short write while authoring target dynamic packet")
            offset += written
        os.fsync(descriptor)
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or stat.S_IMODE(observed.st_mode) != 0o600:
            raise ValueError("target dynamic packet output mode is not 0600")
        os.fsync(parent_fd)
    except BaseException:
        if descriptor >= 0:
            try:
                os.unlink(output_path.name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def author_target_dynamic_packet(*, input_path: Path, output_path: Path, private_root: Path) -> Path:
    input_raw, _input_digest, input_stat = _observe_regular(
        input_path, label="authoring input", capture=True
    )
    if input_raw is None:
        raise AssertionError("authoring input capture failed")
    if stat.S_IMODE(input_stat.st_mode) != 0o400:
        raise ValueError("authoring input must be immutable mode 0400")
    try:
        input_value = json.loads(input_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("authoring input must be UTF-8 JSON") from exc
    if canonical_json_bytes(input_value) != input_raw:
        raise ValueError("authoring input must be exact canonical JSON")
    source = TargetDynamicPacketAuthoringInput.model_validate_json(input_raw, strict=True)
    output = _validate_external_root(output_path, label="output path")
    private = _validate_external_root(private_root, label="private root")
    if output == private:
        raise ValueError("output path and private root must be distinct")
    if output.exists() or output.is_symlink():
        raise FileExistsError("target dynamic packet output already exists")
    installed = _build_installed(source.installed, private_root=private, attempt_id=source.plan.attempt_id)
    observation_value = dict(source.observations_template)
    observation_value["installed"] = installed.model_dump(mode="json")
    observations = F2C4TargetDynamicObservations.model_validate_json(
        canonical_json_bytes(observation_value), strict=True
    )
    plan = F2C4TargetDynamicPlanInput.model_validate_json(
        canonical_json_bytes(source.plan.model_dump(mode="json")), strict=True
    )
    author_f2_target_dynamic_authority(plan, observations)
    packet = {
        "plan": plan.model_dump(mode="json"),
        "observations": observations.model_dump(mode="json"),
    }
    payload = canonical_json_bytes(packet)
    if set(packet) != {"plan", "observations"} or canonical_json_bytes(json.loads(payload)) != payload:
        raise AssertionError("target dynamic packet serialization is not canonical")
    _write_new_packet(output, payload)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Author one same-job F2 target dynamic packet.")
    parser.add_argument("--input", help="Absolute path to immutable mode-0400 canonical authoring JSON")
    parser.add_argument("--output", help="New absolute packet path outside the source bundle")
    parser.add_argument("--private-root", help="Absolute private root for per-attempt Docker authority paths")
    parser.add_argument("--print-schema", action="store_true", help="Print the canonical authoring-input JSON Schema")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_schema:
        sys.stdout.buffer.write(canonical_json_bytes(TargetDynamicPacketAuthoringInput.model_json_schema()) + b"\n")
        return 0
    if args.input is None or args.output is None or args.private_root is None:
        parser.error("--input, --output, and --private-root are required")
    authored = author_target_dynamic_packet(
        input_path=Path(args.input),
        output_path=Path(args.output),
        private_root=Path(args.private_root),
    )
    sys.stdout.buffer.write(canonical_json_bytes({"packet_path": os.fspath(authored)}) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
