from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Any, Mapping

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.runners.base import RunnerToolBinding
from breadboard.rl.harness.materialization import (
    DirectoryStorageBackend,
    MaterializationEntry,
    SealedSourceManifest,
    SourceManifestEntry,
    WorkspaceMaterializationPlan,
)
from breadboard.rl.harness.sandbox import (
    InstalledImage,
    InstalledRuntime,
    InstalledSandboxAuthoritySet,
    InstalledVerifier,
    SandboxMeasurement,
    SandboxNetworkPolicy,
    SandboxSecurityPolicy,
)


def digest(value: str | bytes) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def independent_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def plan_tool_bindings(plan: c.EffectiveExecutionPlan) -> tuple[RunnerToolBinding, ...]:
    return tuple(
        RunnerToolBinding(
            tool_id=tool.tool_id,
            implementation_digest=tool.implementation_digest,
            capability_ids=tool.capability_ids,
        )
        for tool in plan.effective_capabilities.tools
    )


def make_effective_plan(
    *,
    runtime_id: str = "trusted-process",
    runtime_class: c.RuntimeClass = c.RuntimeClass.TRUSTED_PROCESS,
    mounts: tuple[c.MountGrant, ...] = (),
    repository_snapshot_digest: str | None = None,
    dataset_digests: tuple[str, ...] = (),
    input_artifact_digests: tuple[str, ...] = (),
    action_timeout_ms: int = 2_000,
    observation_bytes: int = 4_096,
    storage_bytes: int = 1_000_000,
    security_policy_digest: str | None = None,
    network_policy_digest: str | None = None,
    verifier_network_policy_digest: str | None = None,
    runtime_binary_digest: str | None = None,
    verifier_executable_digest: str | None = None,
    runner_adapter_id: str = "terminal-responses",
    runner_runtime_abi: str = "responses-v1",
    runner_implementation_digest: str | None = None,
) -> c.EffectiveExecutionPlan:
    runner = c.RunnerGrant(
        adapter_id=runner_adapter_id,
        runtime_abi=runner_runtime_abi,
        implementation_digest=runner_implementation_digest or digest("runner"),
    )
    tools = tuple(
        c.ToolGrant(
            tool_id=tool_id,
            implementation_digest=digest(f"tool:{tool_id}"),
            capability_ids=(),
        )
        for tool_id in ("list_files", "read_file", "shell", "submit", "write_file")
    )
    sandbox = c.SandboxGrant(
        runtime_id=runtime_id,
        runtime_class=runtime_class,
        driver_implementation_digest=digest(f"driver:{runtime_id}"),
        runtime_binary_digest=runtime_binary_digest or digest(f"binary:{runtime_id}"),
        security_policy_digest=security_policy_digest or digest(f"security:{runtime_id}"),
        image_digest=digest(f"image:{runtime_id}"),
        network_policy_digest=network_policy_digest or digest(f"network:{runtime_id}"),
        egress_route_ids=(),
        mounts=mounts,
    )
    resources = c.ResourceLimits(
        cpu_millis=1_000,
        memory_bytes=32_000_000,
        pids=32,
        storage_bytes=storage_bytes,
        open_files=128,
        wall_time_ms=30_000,
    )
    limits = c.ExecutionLimits(
        max_turns=4,
        action_timeout_ms=action_timeout_ms,
        observation_bytes=observation_bytes,
        response_bytes=16_000,
        artifact_bytes_each=8_000,
        artifact_bytes_total=16_000,
        transcript_bytes=32_000,
        setup_timeout_ms=5_000,
        verifier_timeout_ms=7_000,
    )
    task = c.TaskGrant(
        task_contract_digest=digest("task-contract"),
        task_binding_digest=digest("task-binding"),
        repository_snapshot_digest=repository_snapshot_digest,
        dataset_digests=dataset_digests,
        input_artifact_digests=input_artifact_digests,
    )
    verifier = c.VerifierGrant(
        verifier_id="verifier",
        implementation_digest=digest("verifier-implementation"),
        image_digest=digest("verifier-image"),
        executable_digest=verifier_executable_digest
        or digest("verifier-executable"),
        code_digest=digest("verifier-code"),
        input_schema_digest=digest("verifier-input-schema"),
        result_schema_digest=digest("verifier-result-schema"),
        network_policy_digest=verifier_network_policy_digest or digest("verifier-network"),
        secret_handle_ids=(),
    )
    artifacts = c.ArtifactPolicyGrant(
        allowed_roles=("patch",),
        max_each_bytes=8_000,
        max_total_bytes=16_000,
    )
    evidence = c.PolicyRef(
        policy_id="evidence", revision_digest=digest("evidence-policy")
    )
    retention = c.PolicyRef(
        policy_id="retention", revision_digest=digest("retention-policy")
    )
    capabilities = c.CapabilityVector(
        runner=runner,
        tools=tools,
        setup_plans=(),
        routes=(),
        secret_handles=(),
        sandbox=sandbox,
        resources=resources,
        limits=limits,
        task=task,
        policy_slots=(),
        verifier=verifier,
        mutable_pointers=(),
        artifacts=artifacts,
        evidence=evidence,
        retention=retention,
    )
    semantic: dict[str, Any] = {}
    semantic_digest = independent_digest(
        {"schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID, "config": semantic}
    )
    compiler = c.CompilerIdentity(
        compiler_id="compiler",
        semantic_version="1.0.0",
        code_digest=digest("compiler-code"),
        source_schema_id="source-v1",
        source_schema_digest=digest("source-schema"),
        manifest_schema_digest=digest("manifest-schema"),
        canonicalizer_id="canonical-json-v1",
        runtime_abi="responses-v1",
    )
    compiled = c.CompiledArtifactIdentity(
        manifest_digest=digest("compiled-manifest"),
        bundle_digest=digest("bundle"),
        closure_digest=digest("closure"),
        compiler_input_digest=digest("compiler-input"),
        semantic_digest=semantic_digest,
        compiler=compiler,
        provenance_digest=digest("provenance"),
        diagnostics_digest=digest("diagnostics"),
    )
    return c.EffectiveExecutionPlan(
        subject_digest=digest("subject"),
        base_compiled=compiled,
        base_receipt_digest=digest("receipt"),
        selector_digest=digest("selector"),
        config_set_digest=None,
        admitted_set_root=digest("admitted-set"),
        selection_record_digest=digest("selection"),
        task_eligibility_digest=digest("task-eligibility"),
        policy_capability_observation_digest=digest("policy-observation"),
        policy_capability_digest=digest("policy-capability"),
        overlay_applications=(),
        final_receipt_digest=digest("receipt"),
        final_semantic_digest=semantic_digest,
        effective_semantics=semantic,
        effective_capabilities=capabilities,
        effective_capability_digest=capabilities.canonical_digest(),
        pins=(),
        runner=runner,
        policy_slots=(),
        sandbox=sandbox,
        verifier=verifier,
        task=task,
        artifacts=artifacts,
        evidence=evidence,
        retention=retention,
        revocation=c.RevocationBinding(
            scope_digest=digest("revocation-scope"),
            epoch=1,
            state_digest=digest("revocation-state"),
        ),
    )


def replace_plan_capabilities(
    plan: c.EffectiveExecutionPlan,
    *,
    sandbox: c.SandboxGrant | None = None,
    task: c.TaskGrant | None = None,
    limits: c.ExecutionLimits | None = None,
    resources: c.ResourceLimits | None = None,
) -> c.EffectiveExecutionPlan:
    next_sandbox = sandbox or plan.sandbox
    next_task = task or plan.task
    next_limits = limits or plan.effective_capabilities.limits
    next_resources = resources or plan.effective_capabilities.resources
    capability_payload = plan.effective_capabilities.model_dump(mode="python")
    capability_payload.update(
        {
            "sandbox": next_sandbox,
            "task": next_task,
            "limits": next_limits,
            "resources": next_resources,
        }
    )
    capabilities = c.CapabilityVector.model_validate(capability_payload)
    plan_payload = plan.model_dump(mode="python")
    plan_payload.update(
        {
            "sandbox": next_sandbox,
            "task": next_task,
            "effective_capabilities": capabilities,
            "effective_capability_digest": capabilities.canonical_digest(),
        }
    )
    return c.EffectiveExecutionPlan.model_validate(plan_payload)


class FrozenClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.value = current or datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    def current(self) -> datetime:
        return self.value

    def advance(self, **delta: int) -> None:
        self.value += timedelta(**delta)


class DeterministicRandom:
    def __init__(self, namespace: int = 1) -> None:
        self._value = namespace

    def __call__(self, size: int) -> bytes:
        value = self._value
        self._value += 1
        return value.to_bytes(size, "big")


class MemorySourceReader:
    def __init__(
        self,
        sources: Mapping[str, Mapping[str, bytes]],
        *,
        modes: Mapping[tuple[str, str], int] | None = None,
    ) -> None:
        self.sources = {digest_value: dict(members) for digest_value, members in sources.items()}
        self.modes = dict(modes or {})
        self.loads: list[tuple[str, int]] = []
        self.reads: list[tuple[str, str, int]] = []
        self.fail_read: BaseException | None = None
        self.load_entered: Event | None = None
        self.release_load: Event | None = None

    def load_manifest(self, digest_value: str, *, max_bytes: int) -> SealedSourceManifest:
        self.loads.append((digest_value, max_bytes))
        if self.load_entered is not None:
            self.load_entered.set()
        if self.release_load is not None:
            if not self.release_load.wait(timeout=10):
                raise AssertionError("source-reader test barrier was never released")
        try:
            members = self.sources[digest_value]
        except KeyError as exc:
            raise RuntimeError("source_missing") from exc
        directories: set[str] = set()
        for logical_path in members:
            path = PurePosixPath(logical_path)
            directories.update(parent.as_posix() for parent in path.parents if parent.as_posix() != ".")
        entries = [
            SourceManifestEntry(
                logical_path=logical_path,
                kind="directory",
                byte_count=0,
                mode=self.modes.get((digest_value, logical_path), 0o755),
            )
            for logical_path in directories
        ]
        entries.extend(
            SourceManifestEntry(
                logical_path=logical_path,
                kind="file",
                byte_count=len(content),
                mode=self.modes.get((digest_value, logical_path), 0o644),
                content_digest=digest(content),
            )
            for logical_path, content in members.items()
        )
        entries.sort(key=lambda entry: entry.logical_path)
        total_bytes = sum(len(content) for content in members.values())
        if total_bytes > max_bytes:
            raise RuntimeError("source_limit_exceeded")
        return SealedSourceManifest(
            source_digest=digest_value,
            schema_identity="bb.test.source-manifest.v1",
            media_identity="application/vnd.bb.test-tree",
            entries=tuple(entries),
            total_bytes=total_bytes,
            total_files=len(members),
        )

    def read_member(
        self, digest_value: str, logical_path: str, *, max_bytes: int
    ) -> bytes:
        self.reads.append((digest_value, logical_path, max_bytes))
        if self.fail_read is not None:
            raise self.fail_read
        content = self.sources[digest_value][logical_path]
        if len(content) > max_bytes:
            raise RuntimeError("source_limit_exceeded")
        return content


def make_materialization_plan(
    plan: c.EffectiveExecutionPlan,
    *,
    episode_id: str = "episode-one",
    entries: tuple[MaterializationEntry, ...] = (),
) -> WorkspaceMaterializationPlan:
    return WorkspaceMaterializationPlan(
        episode_id=episode_id,
        subject_digest=plan.subject_digest,
        final_receipt_digest=plan.final_receipt_digest,
        effective_plan_digest=plan.canonical_digest(),
        sandbox_projection=plan.sandbox.model_dump(mode="json"),
        task_projection=plan.task.model_dump(mode="json"),
        setup_projections=(),
        entries=entries,
        tool_bindings=plan_tool_bindings(plan),
        resources_projection=plan.effective_capabilities.resources.model_dump(mode="json"),
        limits_projection=plan.effective_capabilities.limits.model_dump(mode="json"),
    )


def make_store_roots(tmp_path: Path) -> tuple[Path, Path]:
    cache_root = tmp_path / "cache"
    workspace_root = tmp_path / "workspaces"
    cache_root.mkdir(mode=0o700)
    workspace_root.mkdir(mode=0o700)
    return cache_root, workspace_root


def directory_storage() -> DirectoryStorageBackend:
    return DirectoryStorageBackend()


@dataclass(frozen=True, slots=True)
class RuntimeFixture:
    plan: c.EffectiveExecutionPlan
    request: Any
    registries: c.RegistrySnapshotSet
    authorities: InstalledSandboxAuthoritySet


def _registry_snapshot(**records: tuple[Any, ...]) -> c.RegistrySnapshotSet:
    names = (
        "runners",
        "tools",
        "setups",
        "routes",
        "secret_handles",
        "sandbox_runtimes",
        "images",
        "repository_bindings",
        "task_datasets",
        "models",
        "verifiers",
        "evidence_policies",
        "retention_policies",
        "policy_capability_attestations",
    )
    values = {name: tuple(records.get(name, ())) for name in names}
    digest_fields = {
        "runners": "runner_registry_digest",
        "tools": "tool_registry_digest",
        "setups": "setup_registry_digest",
        "routes": "route_registry_digest",
        "secret_handles": "secret_handle_registry_digest",
        "sandbox_runtimes": "sandbox_runtime_registry_digest",
        "images": "image_registry_digest",
        "repository_bindings": "repository_binding_registry_digest",
        "task_datasets": "task_dataset_registry_digest",
        "models": "model_registry_digest",
        "verifiers": "verifier_registry_digest",
        "evidence_policies": "evidence_policy_registry_digest",
        "retention_policies": "retention_policy_registry_digest",
        "policy_capability_attestations": "policy_capability_registry_digest",
    }
    component_digests = {
        digest_fields[name]: c.RegistrySnapshotSet.derive_component_digest(
            name, values[name]
        )
        for name in names
    }
    digests = c.RegistryDigestSet(
        **component_digests,
        snapshot_digest=c.RegistrySnapshotSet.derive_snapshot_digest(component_digests),
    )
    return c.RegistrySnapshotSet(digests=digests, **values)


@lru_cache(maxsize=1)
def _shared_private_runtime_executables() -> tuple[str, str]:
    installation = Path(
        tempfile.mkdtemp(prefix="breadboard-wp7-runtime-")
    ).resolve(strict=True)
    return _install_private_runtime_executables(installation)


def _install_private_runtime_executables(
    installation: Path,
) -> tuple[str, str]:
    source = Path(os.path.realpath("/bin/sh"))
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise RuntimeError("canonical test shell must be an absolute regular file")
    installation.mkdir(mode=0o700, parents=True, exist_ok=True)
    canonical_installation = installation.resolve(strict=True)
    shell = canonical_installation / "shell"
    shutil.copyfile(source, shell)
    shell.chmod(0o500)
    canonical_shell = shell.resolve(strict=True)
    if canonical_shell != shell or canonical_shell.is_symlink():
        raise RuntimeError("private test shell must not contain a symlink")
    verifier = canonical_installation / "verifier"
    verifier.write_bytes(b"#!/bin/sh\nprintf verifier\n")
    verifier.chmod(0o500)
    canonical_verifier = verifier.resolve(strict=True)
    if canonical_verifier != verifier or canonical_verifier.is_symlink():
        raise RuntimeError("private test verifier must not contain a symlink")
    return str(canonical_shell), str(canonical_verifier)


def make_runtime_fixture(
    *,
    runtime_class: c.RuntimeClass = c.RuntimeClass.TRUSTED_PROCESS,
    with_writable_mount: bool = False,
    repository_mount: bool = False,
    episode_id: str = "episode-one",
    runner_adapter_id: str = "terminal-responses",
    runner_runtime_abi: str = "responses-v1",
    runner_implementation_digest: str | None = None,
    runtime_install_root: Path | None = None,
) -> RuntimeFixture:
    source_digest = digest("workspace-source")
    mounts = (
        c.MountGrant(
            source_artifact_digest=source_digest,
            target_logical_path="work",
            access=c.MountAccess.READ_WRITE,
            max_bytes=4_096,
        ),
    ) if with_writable_mount else ()
    runtime_id = {
        c.RuntimeClass.TRUSTED_PROCESS: "trusted-process",
        c.RuntimeClass.HARDENED_DOCKER: "hardened-docker",
        c.RuntimeClass.HARDENED_GVISOR: "hardened-gvisor",
    }[runtime_class]
    seccomp = b"{}"
    primary_security_projection = {
        "uid": 65_534,
        "gid": 65_534,
        "read_only_root": True,
        "drop_all_capabilities": True,
        "no_new_privileges": True,
        "seccomp_digest": digest(seccomp),
        "apparmor_profile": "bb-test",
        "selinux_label": None,
        "namespace_flags": [],
        "privileged": False,
        "devices": [],
        "docker_socket_forbidden": True,
        "tmpfs_mounts": [["/tmp", "rw,noexec,nosuid,size=1048576"]],
        "snapshot_max_depth": 8,
        "snapshot_max_files": 64,
        "snapshot_max_inodes": 128,
    }
    verifier_security_projection = {
        **primary_security_projection,
        "uid": 65_533,
        "gid": 65_533,
    }
    network_projection = {
        "mode": "none",
        "docker_network": "none",
        "egress_route_ids": [],
        "default_deny": True,
    }
    primary_security_digest = SandboxSecurityPolicy.derive_digest(primary_security_projection)
    verifier_security_digest = SandboxSecurityPolicy.derive_digest(verifier_security_projection)
    network_digest = SandboxNetworkPolicy.derive_digest(network_projection)
    shell_path, verifier_path = (
        _install_private_runtime_executables(
            runtime_install_root / "runtime-install"
        )
        if runtime_install_root is not None
        else _shared_private_runtime_executables()
    )
    shell_binary_digest = digest(Path(shell_path).read_bytes())
    verifier_binary_digest = digest(Path(verifier_path).read_bytes())
    plan = make_effective_plan(
        runtime_id=runtime_id,
        runtime_class=runtime_class,
        mounts=mounts,
        repository_snapshot_digest=source_digest if repository_mount else None,
        security_policy_digest=primary_security_digest,
        network_policy_digest=network_digest,
        verifier_network_policy_digest=network_digest,
        runtime_binary_digest=shell_binary_digest,
        verifier_executable_digest=verifier_binary_digest,
        runner_adapter_id=runner_adapter_id,
        runner_runtime_abi=runner_runtime_abi,
        runner_implementation_digest=runner_implementation_digest,
    )
    primary_binding = c.SandboxBinding(
        runtime_id=plan.sandbox.runtime_id,
        runtime_class=plan.sandbox.runtime_class,
        driver_implementation_digest=plan.sandbox.driver_implementation_digest,
        runtime_binary_digest=plan.sandbox.runtime_binary_digest,
        security_policy_digest=plan.sandbox.security_policy_digest,
        image_digest=plan.sandbox.image_digest,
        network_policy_digest=plan.sandbox.network_policy_digest,
    )
    verifier_runtime_id = "verifier-runtime"
    verifier_binding = c.SandboxBinding(
        runtime_id=verifier_runtime_id,
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        driver_implementation_digest=digest("verifier-driver"),
        runtime_binary_digest=shell_binary_digest,
        security_policy_digest=verifier_security_digest,
        image_digest=plan.verifier.image_digest,
        network_policy_digest=plan.verifier.network_policy_digest,
    )
    runtime_records = tuple(
        sorted(
            (
                c.SandboxRuntimeRegistryRecord(binding=primary_binding),
                c.SandboxRuntimeRegistryRecord(binding=verifier_binding),
            ),
            key=lambda record: record.binding.runtime_id,
        )
    )
    image_records = tuple(
        sorted(
            (
                c.ImageRegistryRecord(
                    image_digest=plan.sandbox.image_digest,
                    runtime_id=runtime_id,
                    repository_binding_digests=(),
                ),
                c.ImageRegistryRecord(
                    image_digest=plan.verifier.image_digest,
                    runtime_id=verifier_runtime_id,
                    repository_binding_digests=(),
                ),
            ),
            key=lambda record: record.image_digest,
        )
    )
    verifier_record = c.VerifierRegistryRecord(
        grant=plan.verifier,
        runtime_id=verifier_runtime_id,
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        security_policy_digest=verifier_security_digest,
    )
    registries = _registry_snapshot(
        sandbox_runtimes=runtime_records,
        images=image_records,
        verifiers=(verifier_record,),
    )
    primary_runtime = InstalledRuntime(
        runtime_id=runtime_id,
        runtime_class=runtime_class,
        driver_implementation_digest=plan.sandbox.driver_implementation_digest,
        executable_path=shell_path,
        measured_binary_digest=plan.sandbox.runtime_binary_digest,
        oci_runtime_name=("runsc" if runtime_class is c.RuntimeClass.HARDENED_GVISOR else "runc"),
        supported_platform_versions=("test",),
        fixed_environment=(("PATH", "/usr/bin:/bin"),),
        runsc_binary_path=(
            shell_path
            if runtime_class is c.RuntimeClass.HARDENED_GVISOR
            else None
        ),
        runsc_binary_digest=(
            shell_binary_digest
            if runtime_class is c.RuntimeClass.HARDENED_GVISOR
            else None
        ),
        oci_runtime_binary_path=(
            shell_path
            if runtime_class
            in {c.RuntimeClass.HARDENED_DOCKER, c.RuntimeClass.HARDENED_GVISOR}
            else None
        ),
        oci_runtime_binary_digest=(
            shell_binary_digest
            if runtime_class
            in {c.RuntimeClass.HARDENED_DOCKER, c.RuntimeClass.HARDENED_GVISOR}
            else None
        ),
    )
    verifier_runtime = InstalledRuntime(
        runtime_id=verifier_runtime_id,
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        driver_implementation_digest=verifier_binding.driver_implementation_digest,
        executable_path=shell_path,
        measured_binary_digest=verifier_binding.runtime_binary_digest,
        oci_runtime_name="process",
        supported_platform_versions=("test",),
        fixed_environment=(("PATH", "/usr/bin:/bin"),),
    )

    def security_policy(
        policy_digest: str, *, uid: int, gid: int
    ) -> SandboxSecurityPolicy:
        return SandboxSecurityPolicy(
            policy_digest=policy_digest,
            uid=uid,
            gid=gid,
            read_only_root=True,
            drop_all_capabilities=True,
            no_new_privileges=True,
            seccomp_bytes=seccomp,
            seccomp_digest=digest(seccomp),
            apparmor_profile="bb-test",
            selinux_label=None,
            namespace_flags=(),
            privileged=False,
            devices=(),
            docker_socket_forbidden=True,
            tmpfs_mounts=(("/tmp", "rw,noexec,nosuid,size=1048576"),),
            snapshot_max_depth=8,
            snapshot_max_files=64,
            snapshot_max_inodes=128,
        )

    primary_security = security_policy(primary_security_digest, uid=65_534, gid=65_534)
    verifier_security = security_policy(verifier_security_digest, uid=65_533, gid=65_533)
    primary_network = SandboxNetworkPolicy(
        policy_digest=network_digest,
        mode="none",
        docker_network="none",
        egress_route_ids=(),
        default_deny=True,
    )
    verifier_network = primary_network
    installed_verifier = InstalledVerifier(
        grant=plan.verifier,
        runtime_id=verifier_runtime_id,
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        security_policy_digest=verifier_security_digest,
        argv=(verifier_path,),
        result_relative_path="result.json",
        executable_digest=plan.verifier.executable_digest,
        code_digest=plan.verifier.code_digest,
        input_schema_digest=plan.verifier.input_schema_digest,
        result_schema_digest=plan.verifier.result_schema_digest,
    )
    authorities = InstalledSandboxAuthoritySet(
        runtimes=tuple(sorted((primary_runtime, verifier_runtime), key=lambda item: item.runtime_id)),
        images=tuple(
            sorted(
                (
                    InstalledImage(
                        plan.sandbox.image_digest,
                        runtime_id,
                        "bb/test@" + plan.sandbox.image_digest,
                    ),
                    InstalledImage(
                        plan.verifier.image_digest,
                        verifier_runtime_id,
                        "bb/verifier@" + plan.verifier.image_digest,
                    ),
                ),
                key=lambda item: item.image_digest,
            )
        ),
        security_policies=tuple(
            sorted((primary_security, verifier_security), key=lambda item: item.policy_digest)
        ),
        network_policies=(primary_network,),
        verifiers=(installed_verifier,),
    )
    from breadboard.rl.harness.materialization import WorkspaceOpenRequest

    return RuntimeFixture(
        plan=plan,
        request=WorkspaceOpenRequest(episode_id, plan),
        registries=registries,
        authorities=authorities,
    )
