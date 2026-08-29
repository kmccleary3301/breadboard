from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

import breadboard.rl.harness.sandbox_docker as docker_module
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    DirectoryStorageBackend,
    FilesystemMaterializationStore,
)
from breadboard.rl.harness.sandbox import (
    RuntimeLaunchContext,
    RuntimePreparedIdentity,
    SandboxFault,
    SandboxMeasurement,
    SandboxRuntimeManager,
    WorkspaceStorageIdentity,
    build_sandbox_execution_plan,
)
from breadboard.rl.harness.sandbox_docker import (
    DockerAdapterError,
    DockerCommandResult,
    DockerRuntimeAdapter,
    DockerRuntimeHandle,
    DockerSandboxBackend,
    ExecutableInvocation,
    PrivateDockerDaemonBinding,
    SubprocessDockerCliExecutor,
    build_create_argv,
    decode_docker_inspect,
    measurement_mismatches,
    observe_binary_digest,
    requested_measurement,
)
from tests.rl.harness.wp7_fixtures import (
    DeterministicRandom,
    FrozenClock,
    MemorySourceReader,
    digest,
    make_runtime_fixture,
    make_store_roots,
)


class ScriptedDockerExecutor:
    def __init__(
        self,
        results: Sequence[DockerCommandResult] = (),
        *,
        trace: list[str] | None = None,
    ) -> None:
        self.results = list(results)
        self.trace = trace
        self.invocations: list[ExecutableInvocation] = []
        self.calls: list[tuple[tuple[str, ...], int, int, tuple[tuple[str, str], ...]]] = []
        self.inputs: list[bytes] = []

    async def execute(
        self,
        executable: ExecutableInvocation,
        argv_tail: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int,
        environment: tuple[tuple[str, str], ...],
        input_bytes: bytes = b"",
    ) -> DockerCommandResult:
        normalized = (executable.argv0, *argv_tail)
        self.invocations.append(executable)
        self.calls.append((normalized, timeout_ms, output_limit, environment))
        self.inputs.append(input_bytes)
        if self.trace is not None:
            self.trace.append(normalized[1])
        if not self.results:
            raise AssertionError(f"unexpected unscripted Docker command: {normalized!r}")
        result = self.results.pop(0)
        return replace(result, argv=normalized)


class CancellableExecDockerExecutor(ScriptedDockerExecutor):
    def __init__(self, results: Sequence[DockerCommandResult] = ()) -> None:
        super().__init__(results)
        self.exec_started = asyncio.Event()

    async def execute(
        self,
        executable: ExecutableInvocation,
        argv_tail: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int,
        environment: tuple[tuple[str, str], ...],
        input_bytes: bytes = b"",
    ) -> DockerCommandResult:
        normalized = (executable.argv0, *argv_tail)
        if len(normalized) > 1 and normalized[1] == "exec":
            self.invocations.append(executable)
            self.calls.append((normalized, timeout_ms, output_limit, environment))
            self.inputs.append(input_bytes)
            self.exec_started.set()
            await asyncio.wait_for(asyncio.Event().wait(), 1)
            raise AssertionError("cancelled Docker exec unexpectedly resumed")
        return await super().execute(
            executable,
            argv_tail,
            timeout_ms=timeout_ms,
            output_limit=output_limit,
            environment=environment,
            input_bytes=input_bytes,
        )


class RecordingMeasurementProvider:
    def __init__(self, measured: Mapping[str, Any]) -> None:
        self.measured = dict(measured)
        self.calls: list[tuple[Any, str, bytes]] = []

    async def measure(
        self, plan: Any, container_name: str, inspect_payload: bytes
    ) -> Mapping[str, Any]:
        self.calls.append((plan, container_name, inspect_payload))
        return dict(self.measured)


class LeaseOnlyHandle:
    runtime_id = "c" * 64

    async def terminate(self) -> tuple[CleanupStepReceipt, ...]:
        return (CleanupStepReceipt("runtime", CleanupState.RELEASED),)


class LeaseOnlyBackend:
    async def launch(
        self,
        plan: Any,
        workspace: Path,
        *,
        context: RuntimeLaunchContext,
    ) -> tuple[LeaseOnlyHandle, SandboxMeasurement]:
        observed = {"runtime_resource_id": LeaseOnlyHandle.runtime_id}
        isolated = plan.isolation_disposition.value == "isolated"
        return LeaseOnlyHandle(), SandboxMeasurement(
            effective_plan_digest=plan.effective_plan_digest,
            lease_id=context.lease_id,
            workspace_id=context.workspace_id,
            runtime_id=plan.runtime.runtime_id,
            runtime_class=plan.runtime.runtime_class.value,
            driver_binary_digest=plan.runtime.measured_binary_digest,
            image_digest=plan.image.image_digest,
            requested=observed,
            effective=observed,
            measured=observed,
            runtime_resource_id=LeaseOnlyHandle.runtime_id,
            mismatch=(),
            isolation_disposition=plan.isolation_disposition,
            isolated=isolated,
            reward_eligible=isolated,
        )


class PendingLaunchBackend:
    def __init__(self) -> None:
        self.cleanup_pending = True
        self.close_attempts = 0
        self.workspace_fd = -1

    async def launch(
        self,
        plan: Any,
        workspace: Path,
        *,
        context: RuntimeLaunchContext,
    ) -> tuple[LeaseOnlyHandle, SandboxMeasurement]:
        del plan, workspace
        self.workspace_fd = context.workspace_fd
        raise DockerAdapterError(
            "runtime_cleanup_pending",
            "launch cleanup retained by backend",
        )

    async def reconcile_quarantined(self) -> None:
        self.close_attempts += 1
        if self.close_attempts == 1:
            raise DockerAdapterError(
                "runtime_cleanup_pending",
                "launch cleanup still pending",
            )
        os.close(self.workspace_fd)
        self.workspace_fd = -1
        self.cleanup_pending = False


class RetainedLaunchHandle:
    runtime_id = "d" * 64

    def __init__(self, workspace_fd: int) -> None:
        self.workspace_fd = workspace_fd
        self.terminate_calls = 0

    async def terminate(self) -> tuple[CleanupStepReceipt, ...]:
        self.terminate_calls += 1
        if self.terminate_calls == 1:
            return (CleanupStepReceipt("runtime", CleanupState.QUARANTINED),)
        os.close(self.workspace_fd)
        self.workspace_fd = -1
        return (CleanupStepReceipt("runtime", CleanupState.RELEASED),)


class ReturnedPendingLaunchBackend:
    cleanup_pending = False

    def __init__(self) -> None:
        self.handle: RetainedLaunchHandle | None = None

    async def launch(
        self,
        plan: Any,
        workspace: Path,
        *,
        context: RuntimeLaunchContext,
    ) -> tuple[RetainedLaunchHandle, SandboxMeasurement]:
        _, measurement = await LeaseOnlyBackend().launch(
            plan, workspace, context=context
        )
        self.handle = RetainedLaunchHandle(context.workspace_fd)
        return self.handle, replace(
            measurement,
            runtime_resource_id=self.handle.runtime_id,
            measured={"runtime_resource_id": self.handle.runtime_id},
            mismatch=("forced post-launch admission failure",),
        )

class QuotaStorageBackend(DirectoryStorageBackend):
    def __init__(self) -> None:
        self.quotas: dict[Path, int] = {}

    def allocate(self, *, workspace_id: str, root: Path, max_bytes: int) -> Path:
        backing = super().allocate(
            workspace_id=workspace_id,
            root=root,
            max_bytes=max_bytes,
        )
        self.quotas[backing] = max_bytes
        return backing

    def measure(self, backing: Path) -> Mapping[str, Any]:
        measured = dict(super().measure(backing))
        measured.update(
            {
                "authority_id": "quota-test",
                "quota_enforced": True,
                "quota_bytes": self.quotas[backing],
            }
        )
        return measured

    def release(self, backing: Path) -> None:
        super().release(backing)
        self.quotas.pop(backing, None)


def _result(
    *,
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    timed_out: bool = False,
    output_limited: bool = False,
) -> DockerCommandResult:
    return DockerCommandResult(
        (), returncode, stdout, stderr, timed_out=timed_out, output_limited=output_limited
    )


def _mechanics_adapter(
    plan: Any,
    executor: ScriptedDockerExecutor,
    *,
    environment: tuple[tuple[str, str], ...] = (),
) -> DockerRuntimeAdapter:
    """Exercise adapter mechanics without claiming host Docker admissibility."""
    invocation = ExecutableInvocation(
        argv0=plan.runtime.executable_path,
        executable_fd=41,
        executable_descriptor_path="/test-only/mechanics/fd/41",
        digest=plan.runtime.measured_binary_digest,
    )
    return DockerRuntimeAdapter(
        executor=executor,
        cli_environment=environment,
        mechanics_invocation=invocation,
    )


def _assert_exact_mechanics_invocation(
    executor: ScriptedDockerExecutor,
    plan: Any,
) -> None:
    assert executor.invocations
    first = executor.invocations[0]
    assert first == ExecutableInvocation(
        argv0=plan.runtime.executable_path,
        executable_fd=41,
        executable_descriptor_path="/test-only/mechanics/fd/41",
        digest=plan.runtime.measured_binary_digest,
    )
    assert all(invocation is first for invocation in executor.invocations)


CONTAINER_ID = "c" * 64


def _binding_labels(
    plan: Any,
    *,
    lease_id: str = "lease-1",
    workspace_id: str = "workspace-1",
    epoch: int = 1,
    role: str = "primary",
) -> dict[str, str]:
    return {
        "bb.lease_id": lease_id,
        "bb.plan_digest": plan.effective_plan_digest,
        "bb.epoch": str(epoch),
        "bb.workspace_id": workspace_id,
        "bb.role": role,
    }


def _measurement_identity(
    plan: Any,
    *,
    workspace_id: str = "workspace-1",
    role: str = "primary",
) -> tuple[Any, ...]:
    labels = _binding_labels(plan, workspace_id=workspace_id, role=role)
    return (
        CONTAINER_ID,
        f"bb-{role}-{workspace_id}",
        tuple(labels.items()),
    )


def _identity_inspect(
    plan: Any,
    *,
    lease_id: str = "lease-1",
    workspace_id: str = "workspace-1",
    epoch: int = 1,
    role: str = "primary",
    container_id: str = CONTAINER_ID,
    labels: Mapping[str, str] | None = None,
) -> bytes:
    expected_labels = _binding_labels(
        plan,
        lease_id=lease_id,
        workspace_id=workspace_id,
        epoch=epoch,
        role=role,
    )
    if labels is not None:
        expected_labels = dict(labels)
    return json.dumps(
        [
            {
                "Id": container_id,
                "Name": f"/bb-{role}-{workspace_id}",
                "Config": {"Labels": expected_labels},
            }
        ]
    ).encode("utf-8")


def _docker_inspect_payload(
    plan: Any,
    skeleton: Path,
    profile: Path,
    mounts: Sequence[tuple[Path, str, bool]],
    *,
    role: str = "primary",
    workspace_id: str = "workspace-1",
) -> dict[str, Any]:
    return {
        "Id": CONTAINER_ID,
        "Name": f"/bb-{role}-{workspace_id}",
        "Image": plan.image.image_digest,
        "Config": {
            "User": f"{plan.security_policy.uid}:{plan.security_policy.gid}",
            "Image": plan.image.image_digest,
            "Labels": _binding_labels(
                plan,
                workspace_id=workspace_id,
                role=role,
            ),
        },
        "HostConfig": {
            "Runtime": plan.runtime.oci_runtime_name,
            "NetworkMode": "none",
            "CgroupParent": "",
            "CgroupnsMode": "private",
            "IpcMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapAdd": None,
            "CapDrop": ["ALL"],
            "Devices": None,
            "DeviceRequests": None,
            "DeviceCgroupRules": None,
            "SecurityOpt": [
                "no-new-privileges",
                f"seccomp={profile}",
                f"apparmor={plan.security_policy.apparmor_profile}",
            ],
            "CpuPeriod": 100_000,
            "CpuQuota": plan.resources.cpu_millis * 100,
            "Memory": plan.resources.memory_bytes,
            "MemorySwap": plan.resources.memory_bytes,
            "PidsLimit": plan.resources.pids,
            "Tmpfs": dict(plan.security_policy.tmpfs_mounts),
            "Ulimits": [
                {
                    "Name": "nofile",
                    "Soft": plan.resources.open_files,
                    "Hard": plan.resources.open_files,
                }
            ],
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(skeleton),
                "Destination": "/testbed",
                "RW": False,
            },
            *[
                {
                    "Type": "bind",
                    "Source": str(source),
                    "Destination": destination,
                    "RW": not readonly,
                }
                for source, destination, readonly in mounts
            ],
        ],
        "NetworkSettings": {"Networks": {"none": {}}},
    }


def _docker_inspect_bytes(inspected: Mapping[str, Any]) -> bytes:
    return json.dumps([inspected]).encode("utf-8")


def _not_found(reference: str) -> DockerCommandResult:
    return _result(
        returncode=1,
        stderr=f"Error: No such object: {reference}".encode("utf-8"),
    )


def _docker_plan(tmp_path: Path, *, gvisor: bool = False) -> tuple[Any, Path, Path, tuple[tuple[Path, str, bool], ...]]:
    runtime_class = (
        c.RuntimeClass.HARDENED_GVISOR if gvisor else c.RuntimeClass.HARDENED_DOCKER
    )
    fixture = make_runtime_fixture(
        runtime_class=runtime_class,
        with_writable_mount=True,
        repository_mount=True,
    )
    plan = build_sandbox_execution_plan(
        fixture.request, fixture.registries, fixture.authorities
    )
    executable = tmp_path / "docker-cli"
    executable.write_bytes(b"pinned docker client")
    oci_runtime = tmp_path / ("runsc" if gvisor else "runc")
    oci_runtime.write_bytes(b"pinned OCI runtime")
    runtime = replace(
        plan.runtime,
        executable_path=str(executable),
        measured_binary_digest=observe_binary_digest(executable),
        oci_runtime_name="runsc" if gvisor else "runc",
        runsc_binary_path=str(oci_runtime) if gvisor else None,
        runsc_binary_digest=observe_binary_digest(oci_runtime) if gvisor else None,
        oci_runtime_binary_path=str(oci_runtime),
        oci_runtime_binary_digest=observe_binary_digest(oci_runtime),
        supported_platform_versions=("bb-test/test",),
    )
    plan = replace(plan, runtime=runtime)
    skeleton = tmp_path / "skeleton"
    skeleton.mkdir(mode=0o500)
    mounted = tmp_path / "private-work"
    mounted.mkdir(mode=0o700)
    profile = tmp_path / "seccomp.json"
    profile.write_bytes(plan.security_policy.seccomp_bytes)
    mounts = ((mounted, "/testbed/work", False),)
    return plan, skeleton, profile, mounts


async def _accept_prepared_identity(_: RuntimePreparedIdentity) -> None:
    return None


def _launch_context(
    plan: Any,
    *,
    role: str = "primary",
    workspace_id: str = "workspace-1",
    storage_authority_id: str = "quota-test",
    quota_enforced: bool = True,
    quota_bytes: int | None = None,
    publish_prepared_identity: Any = _accept_prepared_identity,
) -> RuntimeLaunchContext:
    effective_quota = plan.resources.storage_bytes if quota_bytes is None else quota_bytes
    return RuntimeLaunchContext(
        role=role,
        lease_id="lease-1",
        workspace_id=workspace_id,
        epoch=1,
        storage=WorkspaceStorageIdentity(
            authority_id=storage_authority_id,
            quota_enforced=quota_enforced,
            quota_bytes=effective_quota,
            owner_uid=65534,
            owner_gid=65534,
        ),
        snapshot_relative_path="snapshot" if role == "verifier" else None,
        result_relative_path="result" if role == "verifier" else None,
        publish_prepared_identity=publish_prepared_identity,
    )


def _primary_workspace(
    plan: Any,
    tmp_path: Path,
) -> tuple[Path, tuple[tuple[Path, str, bool], ...]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mounts: list[tuple[Path, str, bool]] = []
    for entry in plan.materialization_plan.entries:
        source = workspace / entry.target_logical_path
        source.mkdir(parents=True)
        mounts.append(
            (
                source,
                "/testbed/" + entry.target_logical_path,
                entry.access.value == "ro",
            )
        )
    return workspace, tuple(mounts)


def _preflight_success(plan: Any) -> list[DockerCommandResult]:
    runtime = plan.runtime.oci_runtime_name
    registration = {
        "path": plan.runtime.oci_runtime_binary_path,
        "runtimeArgs": [],
    }
    results = [
        _result(
            stdout=json.dumps(
                {
                    "Server": {
                        "Platform": {"Name": "bb-test"},
                        "Version": "test",
                    }
                }
            ).encode("utf-8")
        ),
        _result(
            stdout=json.dumps(
                {
                    "Server": "test",
                    "Runtimes": {runtime: registration},
                }
            ).encode("utf-8")
        ),
    ]
    results.append(
        _result(
            stdout=json.dumps(
                {"RepoDigests": ["bb/test@" + plan.image.image_digest]}
            ).encode("utf-8")
        )
    )
    return results


async def _launch_docker_handle(
    tmp_path: Path,
    *,
    executor_type: type[ScriptedDockerExecutor] = ScriptedDockerExecutor,
) -> tuple[Any, ScriptedDockerExecutor, DockerRuntimeHandle]:
    """Construct an already-started handle for lower-level lifecycle mechanics."""
    plan, _, _, _ = _docker_plan(tmp_path)
    executor = executor_type()
    adapter = _mechanics_adapter(plan, executor)
    handle = DockerRuntimeHandle(
        adapter=adapter,
        plan=plan,
        container_id=CONTAINER_ID,
        container_name="bb-primary-workspace-1",
        labels=_binding_labels(plan),
    )
    return plan, executor, handle


@pytest.mark.asyncio
async def test_runtime_handle_exposes_persistent_testbed_file_and_diff_operations(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.extend(
        [
            _result(stdout=b'{"bytes":5,"content_base64":"aGVsbG8="}'),
            _result(),
            _result(stdout=b'["src/lib","src/main.py"]'),
            _result(stdout=b"diff --git a/src/main.py b/src/main.py\n"),
        ]
    )

    read_result = await handle.read_text("src/main.py", limit=5)
    write_result = await handle.write_text("src/main.py", "hello")
    list_result = await handle.list_files("src", depth=1)
    diff_result = await handle.workspace_diff()

    assert read_result == {
        "path": "src/main.py",
        "content": "hello",
        "offset": 0,
        "bytes": 5,
    }
    assert write_result == {"path": "src/main.py", "bytes": 5}
    assert list_result == {
        "path": "src",
        "files": ["src/lib", "src/main.py"],
    }
    assert diff_result["stdout"].startswith("diff --git")
    calls = [call[0][1:] for call in executor.calls]
    assert calls[0][:5] == (
        "exec",
        CONTAINER_ID,
        "python3",
        "-c",
        docker_module._WORKSPACE_PYTHON,
    )
    assert calls[0][5:] == ("read", "src/main.py", "0", "5", "0")
    assert calls[1][:6] == (
        "exec",
        "-i",
        CONTAINER_ID,
        "python3",
        "-c",
        docker_module._WORKSPACE_PYTHON,
    )
    assert calls[1][6:] == (
        "write",
        "src/main.py",
        "5",
        hashlib.sha256(b"hello").hexdigest(),
    )
    assert executor.inputs[1] == b"hello"
    assert calls[2][:5] == (
        "exec",
        CONTAINER_ID,
        "python3",
        "-c",
        docker_module._WORKSPACE_PYTHON,
    )
    assert calls[2][5:] == ("list", "src", "1", "128")
    assert calls[3] == (
        "exec",
        CONTAINER_ID,
        "git",
        "-C",
        "/testbed/work",
        "diff",
        "--no-ext-diff",
        "--binary",
    )


@pytest.mark.asyncio
async def test_workspace_diff_maps_missing_image_git_to_runtime_unsupported(
    tmp_path: Path,
) -> None:
    _, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.append(_result(returncode=127, stderr=b"git: not found"))

    with pytest.raises(DockerAdapterError) as captured:
        await handle.workspace_diff()

    assert captured.value.code == "runtime_unsupported"
    assert handle._fenced is False


@pytest.mark.asyncio
async def test_runtime_handle_reads_exact_observation_limit_through_json_protocol(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    content = b"x" * plan.limits.observation_bytes
    encoded = json.dumps(
        {
            "bytes": len(content),
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    executor.results.append(_result(stdout=encoded))

    result = await handle.read_text("at-limit.txt")

    assert result["content"].encode("utf-8") == content
    assert result["bytes"] == plan.limits.observation_bytes
    assert executor.calls[0][2] == (
        4 * ((plan.limits.observation_bytes + 3) // 3) + 128
    )
    assert executor.calls[0][2] > len(encoded)


@pytest.mark.asyncio
async def test_verifier_artifact_read_uses_artifact_ceiling_not_observation_ceiling(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    plan = replace(
        plan,
        limits=c.ExecutionLimits(
            **{
                **plan.limits.model_dump(),
                "artifact_bytes_each": 1024 * 1024 * 1024,
                "artifact_bytes_total": 1024 * 1024 * 1024,
            }
        ),
    )
    handle.plan = plan
    content = b'{"resolved":true}'
    executor.results.append(
        _result(
            stdout=json.dumps(
                {
                    "bytes": len(content),
                    "content_base64": base64.b64encode(content).decode("ascii"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
    )
    ceiling = docker_module.VERIFIER_RESULT_MAX_BYTES
    assert plan.limits.artifact_bytes_each > ceiling
    assert ceiling > plan.limits.observation_bytes

    result = await handle.read_artifact_text("result/verifier-result.json")

    assert result["content"] == content.decode("utf-8")
    assert executor.calls[0][0][-5:] == (
        "read",
        "result/verifier-result.json",
        "0",
        str(ceiling),
        "1",
    )
    assert executor.calls[0][2] == 4 * ((ceiling + 3) // 3) + 128


@pytest.mark.asyncio
async def test_runtime_handle_zero_byte_read_is_a_nonfencing_success(
    tmp_path: Path,
) -> None:
    _, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.extend(
        [
            _result(stdout=b'{"bytes":0,"content_base64":""}'),
            _result(stdout=b'{"bytes":0,"content_base64":""}'),
        ]
    )

    first = await handle.read_text("nonempty.txt", limit=0)
    second = await handle.read_text("still-active.txt", limit=0)

    assert first["content"] == second["content"] == ""
    assert all(call[0][-2:] == ("0", "0") for call in executor.calls)


@pytest.mark.asyncio
async def test_runtime_handle_retries_only_failed_stages_after_releasing_all_others(
    tmp_path: Path,
) -> None:
    _, _, handle = await _launch_docker_handle(tmp_path)
    descriptors: list[int] = []
    stages: list[docker_module.StagedDockerDescriptorMount] = []
    for index in range(3):
        path = tmp_path / f"stage-{index}"
        path.write_text(str(index), encoding="utf-8")
        descriptor = os.open(path, os.O_RDONLY)
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        stages.append(
            docker_module.StagedDockerDescriptorMount(
                source_path=f"/staged/{index}",
                source_device=metadata.st_dev,
                source_inode=metadata.st_ino,
                source_mode=stat.S_IFMT(metadata.st_mode),
                descriptor_device=metadata.st_dev,
                descriptor_inode=metadata.st_ino,
            )
        )
    failing_path = stages[-1].source_path

    class Stager:
        fail = True

        def __init__(self) -> None:
            self.releases: list[str] = []

        async def release(self, staged: Any) -> None:
            self.releases.append(staged.source_path)
            if self.fail and staged.source_path == failing_path:
                raise RuntimeError("permanent stage failure")

    stager = Stager()

    async def released_runtime() -> tuple[CleanupStepReceipt, ...]:
        return (CleanupStepReceipt("runtime", CleanupState.RELEASED),)

    handle._held_fds = list(descriptors)
    handle._staged_mounts = list(stages)
    handle._mount_stager = stager
    handle._terminate_bound = released_runtime

    first = await handle.terminate()
    second = await handle.terminate()

    assert first[-1].state is CleanupState.QUARANTINED
    assert second[-1].state is CleanupState.QUARANTINED
    assert handle._staged_mounts == [stages[-1]]
    assert stager.releases == [
        stages[2].source_path,
        stages[1].source_path,
        stages[0].source_path,
        stages[2].source_path,
    ]
    assert all(os.fstat(descriptor) for descriptor in descriptors)

    stager.fail = False
    final = await handle.terminate()

    assert final[-1].state is CleanupState.RELEASED
    assert stager.releases[-1] == stages[2].source_path
    assert handle._staged_mounts == []
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.asyncio
async def test_runtime_handle_rejects_workspace_escape_before_docker_exec(
    tmp_path: Path,
) -> None:
    _, executor, handle = await _launch_docker_handle(tmp_path)

    with pytest.raises(DockerAdapterError, match="workspace path is invalid"):
        await handle.read_text("../host-secret")

    assert executor.calls == []


@pytest.mark.asyncio
async def test_runtime_handle_rejects_read_symlink_before_read_effect(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.append(
        _result(returncode=125, stderr=b"bb-workspace-helper:authority\n")
    )

    with pytest.raises(DockerAdapterError) as captured:
        await handle.read_text("link")

    assert captured.value.code == "workspace_escape"
    argv = executor.calls[0][0]
    assert argv[1:6] == (
        "exec",
        CONTAINER_ID,
        "python3",
        "-c",
        docker_module._WORKSPACE_PYTHON,
    )
    assert argv[6:] == (
        "read",
        "link",
        "0",
        str(plan.limits.observation_bytes),
        "1",
    )


@pytest.mark.asyncio
async def test_runtime_handle_reports_missing_image_workspace_helper_as_unavailable(
    tmp_path: Path,
) -> None:
    _, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.append(
        _result(returncode=127, stderr=b"exec: python3: not found\n")
    )

    with pytest.raises(DockerAdapterError) as captured:
        await handle.read_text("src/main.py", limit=1)

    assert captured.value.code == "runtime_unsupported"
    assert handle._fenced is False


@pytest.mark.asyncio
async def test_runtime_handle_rejects_write_symlink_before_write_effect(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.append(
        _result(returncode=125, stderr=b"bb-workspace-helper:authority\n")
    )

    with pytest.raises(DockerAdapterError) as captured:
        await handle.write_text("link", "secret")

    assert captured.value.code == "workspace_escape"
    argv = executor.calls[0][0]
    assert argv[1:7] == (
        "exec",
        "-i",
        CONTAINER_ID,
        "python3",
        "-c",
        docker_module._WORKSPACE_PYTHON,
    )
    assert argv[7:] == (
        "write",
        "link",
        "6",
        hashlib.sha256(b"secret").hexdigest(),
    )
    assert executor.inputs == [b"secret"]


@pytest.mark.asyncio
async def test_runtime_handle_rejects_write_missing_leaf_under_symlink_parent(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.append(
        _result(returncode=125, stderr=b"bb-workspace-helper:authority\n")
    )

    with pytest.raises(DockerAdapterError) as captured:
        await handle.write_text("link/new.txt", "secret")

    assert captured.value.code == "workspace_escape"
    argv = executor.calls[0][0]
    assert argv[1:7] == (
        "exec",
        "-i",
        CONTAINER_ID,
        "python3",
        "-c",
        docker_module._WORKSPACE_PYTHON,
    )
    assert argv[7:] == (
        "write",
        "link/new.txt",
        "6",
        hashlib.sha256(b"secret").hexdigest(),
    )
    assert executor.inputs == [b"secret"]


@pytest.mark.asyncio
async def test_runtime_handle_rejects_list_entry_overflow_before_returning_evidence(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    handle.plan = replace(
        plan,
        security_policy=replace(plan.security_policy, snapshot_max_inodes=1),
    )
    executor.results.append(
        _result(returncode=124, stderr=b"bb-workspace-helper:output-limit\n")
    )

    with pytest.raises(DockerAdapterError) as captured:
        await handle.list_files("src", depth=1)

    assert captured.value.code == "output_limit_exceeded"
    argv = executor.calls[0][0]
    assert argv[1:6] == (
        "exec",
        CONTAINER_ID,
        "python3",
        "-c",
        docker_module._WORKSPACE_PYTHON,
    )
    assert argv[6:] == ("list", "src", "1", "1")
    assert len(executor.calls) == 1


def _run_workspace_helper(
    root: Path,
    *arguments: str,
    input_bytes: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    script = docker_module._WORKSPACE_PYTHON.replace(
        'ROOT = "/testbed"',
        f"ROOT = {str(root)!r}",
        1,
    )
    return subprocess.run(
        [sys.executable, "-c", script, *arguments],
        input=input_bytes,
        capture_output=True,
        check=False,
    )


def test_workspace_helper_performs_descriptor_bound_read_write_and_list(
    tmp_path: Path,
) -> None:
    root = tmp_path / "testbed"
    source = root / "src"
    source.mkdir(parents=True)
    target = source / "main.py"
    target.write_text("old", encoding="utf-8")

    payload = b"hello!"
    write_result = _run_workspace_helper(
        root,
        "write",
        "src/main.py",
        str(len(payload)),
        hashlib.sha256(payload).hexdigest(),
        input_bytes=payload,
    )
    assert write_result.returncode == 0
    assert target.read_text(encoding="utf-8") == "hello!"
    assert not tuple(source.glob(".breadboard-*"))

    nested_payload = b"nested"
    nested_write = _run_workspace_helper(
        root,
        "write",
        "new/deep/result.txt",
        str(len(nested_payload)),
        hashlib.sha256(nested_payload).hexdigest(),
        input_bytes=nested_payload,
    )
    assert nested_write.returncode == 0
    assert (root / "new/deep/result.txt").read_bytes() == nested_payload

    read_result = _run_workspace_helper(
        root, "read", "src/main.py", "1", "4", "0"
    )
    assert read_result.returncode == 0
    assert json.loads(read_result.stdout) == {
        "bytes": 4,
        "content_base64": "ZWxsbw==",
    }

    list_result = _run_workspace_helper(root, "list", "src", "0", "4")
    assert list_result.returncode == 0
    assert json.loads(list_result.stdout) == ["src/main.py"]


def test_workspace_helper_rejects_symlinks_hardlinks_and_inode_overflow(
    tmp_path: Path,
) -> None:
    root = tmp_path / "testbed"
    source = root / "src"
    source.mkdir(parents=True)
    outside = root / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (source / "link").symlink_to(outside)
    os.link(outside, source / "hardlink")

    for arguments in (
        ("read", "src/link", "0", "8", "0"),
        ("list", "src", "0", "8"),
    ):
        result = _run_workspace_helper(root, *arguments)
        assert result.returncode == 125
        assert result.stderr == b"bb-workspace-helper:authority\n"
    payload = b"secret"
    for logical_path in ("src/link", "src/hardlink"):
        result = _run_workspace_helper(
            root,
            "write",
            logical_path,
            str(len(payload)),
            hashlib.sha256(payload).hexdigest(),
            input_bytes=payload,
        )
        assert result.returncode == 125
        assert result.stderr == b"bb-workspace-helper:authority\n"
    assert outside.read_text(encoding="utf-8") == "outside"

    (source / "link").unlink()
    (source / "hardlink").unlink()
    (source / "one").write_text("1", encoding="utf-8")
    (source / "two").write_text("2", encoding="utf-8")
    overflow = _run_workspace_helper(root, "list", "src", "0", "1")
    assert overflow.returncode == 124
    assert overflow.stderr == b"bb-workspace-helper:output-limit\n"


async def _exercise_nonadmissible_prepare_publish_start(
    plan: Any,
    adapter: DockerRuntimeAdapter,
    *,
    skeleton: Path,
    mounts: tuple[tuple[Path, str, bool], ...],
    security_profile_path: Path,
    publish: Any,
) -> tuple[str, bytes]:
    """Exercise mechanics below the stock-Docker admissibility gate."""
    await adapter.preflight(plan)
    security_profile_path.write_bytes(plan.security_policy.seccomp_bytes)
    with security_profile_path.open("rb") as profile_stream:
        profile_metadata = os.fstat(profile_stream.fileno())
        container_id, container_name, _ = await adapter.prepare(
            plan,
            lease_id="lease-1",
            workspace_id="workspace-1",
            epoch=1,
            role="primary",
            skeleton_path=skeleton,
            mounts=mounts,
            security_profile_path=security_profile_path,
            security_profile_descriptor=profile_stream.fileno(),
            security_profile_metadata=profile_metadata,
        )
    labels = _binding_labels(plan)
    identity = RuntimePreparedIdentity(
        runtime_resource_id=container_id,
        labels=labels,
    )
    try:
        await publish(identity)
    except BaseException:
        await adapter.cleanup(
            plan,
            container_id,
            expected_id=container_id,
            expected_name=container_name,
            labels=labels,
        )
        raise
    await adapter.start(plan, container_id)
    return container_id, await adapter.inspect(plan, container_id)


def test_create_argv_exactly_projects_closed_policy_in_deterministic_order(
    tmp_path: Path,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    second = tmp_path / "readonly-input"
    second.mkdir()
    unsorted_mounts = (
        (mounts[0][0], "/testbed/z-output", False),
        (second, "/testbed/a-input", True),
    )

    argv = build_create_argv(
        plan,
        lease_id="lease-123",
        workspace_id="workspace-456",
        epoch=7,
        role="primary",
        skeleton_path=skeleton,
        mounts=unsorted_mounts,
        security_profile_path=profile,
    )

    assert argv == (
        str(plan.runtime.executable_path),
        "create",
        "--name",
        "bb-primary-workspace-456",
        "--label",
        "bb.lease_id=lease-123",
        "--label",
        f"bb.plan_digest={plan.effective_plan_digest}",
        "--label",
        "bb.epoch=7",
        "--label",
        "bb.workspace_id=workspace-456",
        "--label",
        "bb.role=primary",
        "--runtime",
        "runc",
        "--network",
        "none",
        "--cgroupns",
        "private",
        "--ipc",
        "private",
        "--user",
        "65534:65534",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--security-opt",
        f"seccomp={profile}",
        "--security-opt",
        "apparmor=bb-test",
        "--pids-limit",
        "32",
        "--memory",
        "32000000",
        "--memory-swap",
        "32000000",
        "--cpu-period",
        "100000",
        "--cpu-quota",
        "100000",
        "--ulimit",
        "nofile=128:128",
        "--mount",
        f"type=bind,src={skeleton},dst=/testbed,readonly",
        "--mount",
        f"type=bind,src={second},dst=/testbed/a-input,readonly",
        "--mount",
        f"type=bind,src={mounts[0][0]},dst=/testbed/z-output",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=1048576",
        "--workdir",
        "/testbed",
        "--env",
        "PATH=/usr/bin:/bin",
        "--pull",
        "never",
        plan.image.image_digest,
        *plan.runtime.idle_argv,
    )
    joined = "\0".join(argv)
    for forbidden in (
        "--privileged",
        "--device",
        "docker.sock",
        "--pid=host",
        "--ipc=host",
        "--rm",
        "host.docker.internal",
    ):
        assert forbidden not in joined


async def test_prepare_validates_held_seccomp_fd_while_argv_uses_wrong_staged_placeholder(
    tmp_path: Path,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    staged_placeholder = tmp_path / "parent-visible-staged-placeholder"
    staged_placeholder.write_bytes(b"wrong")
    executor = ScriptedDockerExecutor(
        [
            _result(stdout=CONTAINER_ID.encode("ascii")),
            _result(stdout=_identity_inspect(plan)),
        ]
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with profile.open("rb") as profile_stream:
        admitted = os.fstat(profile_stream.fileno())
        profile_stream.seek(0, os.SEEK_END)
        container_id, _, create_argv = await adapter.prepare(
            plan,
            lease_id="lease-1",
            workspace_id="workspace-1",
            epoch=1,
            role="primary",
            skeleton_path=skeleton,
            mounts=mounts,
            security_profile_path=staged_placeholder,
            security_profile_descriptor=profile_stream.fileno(),
            security_profile_metadata=admitted,
        )

    assert container_id == CONTAINER_ID
    assert f"seccomp={staged_placeholder}" in create_argv
    assert executor.results == []


@pytest.mark.parametrize("tamper", ["descriptor", "metadata", "oversize"])
async def test_prepare_rejects_seccomp_fd_or_bounded_metadata_tamper(
    tmp_path: Path,
    tamper: str,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    admitted_stream = profile.open("rb")
    descriptor_stream = admitted_stream
    admitted = os.fstat(admitted_stream.fileno())
    if tamper == "descriptor":
        replacement = tmp_path / "replacement-seccomp"
        replacement.write_bytes(b"x" * len(plan.security_policy.seccomp_bytes))
        descriptor_stream = replacement.open("rb")
    elif tamper == "metadata":
        profile.write_bytes(b"x" * len(plan.security_policy.seccomp_bytes))
    else:
        profile.write_bytes(plan.security_policy.seccomp_bytes + b"x")
        admitted = os.fstat(admitted_stream.fileno())
    adapter = _mechanics_adapter(plan, ScriptedDockerExecutor(), environment=())
    try:
        with pytest.raises(DockerAdapterError) as captured:
            await adapter.prepare(
                plan,
                lease_id="lease-1",
                workspace_id="workspace-1",
                epoch=1,
                role="primary",
                skeleton_path=skeleton,
                mounts=mounts,
                security_profile_path=tmp_path / "broker-only-staged-path",
                security_profile_descriptor=descriptor_stream.fileno(),
                security_profile_metadata=admitted,
            )
    finally:
        if descriptor_stream is not admitted_stream:
            descriptor_stream.close()
        admitted_stream.close()

    assert captured.value.code == "runtime_preflight_failed"


@pytest.mark.parametrize(
    ("case", "binds", "tmpfs_destinations", "expected_code", "expected_detail"),
    [
        ("bind-exact", (("/testbed/data", True), ("/testbed/data", False)), (), "runtime_preflight_failed", "duplicate"),
        ("bind-ancestor", (("/testbed/data", True), ("/testbed/data/out", True)), (), "runtime_preflight_failed", "nested"),
        ("bind-reverse-ancestor", (("/testbed/data/out", True), ("/testbed/data", True)), (), "runtime_preflight_failed", "nested"),
        ("bind-casefold-exact", (("/testbed/Data", True), ("/TESTBED/data", True)), (), "runtime_preflight_failed", "duplicate"),
        ("bind-casefold-ancestor", (("/testbed/Data", True), ("/TESTBED/data/Out", True)), (), "runtime_preflight_failed", "nested"),
        ("tmpfs-exact", (), ("/scratch", "/scratch"), "runtime_preflight_failed", "duplicate"),
        ("tmpfs-ancestor", (), ("/scratch", "/scratch/cache"), "runtime_preflight_failed", "nested"),
        ("tmpfs-reverse-ancestor", (), ("/scratch/cache", "/scratch"), "runtime_preflight_failed", "nested"),
        ("tmpfs-casefold-ancestor", (), ("/Scratch", "/scratch/Cache"), "runtime_preflight_failed", "nested"),
        ("bind-tmpfs-exact", (("/testbed/cache", True),), ("/testbed/cache",), "runtime_preflight_failed", "duplicate"),
        ("bind-above-tmpfs", (("/testbed/snapshot", True),), ("/testbed/snapshot/cache",), "runtime_preflight_failed", "writable child"),
        ("tmpfs-above-bind", (("/scratch/output", False),), ("/scratch",), "runtime_preflight_failed", "nested"),
        ("cross-kind-casefold", (("/TESTBED/Snapshot", True),), ("/testbed/snapshot/cache",), "runtime_preflight_failed", "writable child"),
        ("workspace-bind-alias", (("/TestBed", True),), (), "runtime_preflight_failed", "duplicate"),
        ("workspace-tmpfs-alias", (), ("/TESTBED",), "runtime_preflight_failed", "duplicate"),
        ("bind-lexical-interposition", (("/testbed/snapshot/../result", True),), (), "workspace_escape", "invalid Docker mount path"),
        ("tmpfs-lexical-interposition", (), ("/testbed/./result",), "runtime_preflight_failed", "invalid tmpfs destination"),
        ("verifier-snapshot-interposition", (("/testbed/snapshot", True), ("/testbed/result", False)), ("/testbed/snapshot/tmp",), "runtime_preflight_failed", "writable child"),
        ("verifier-result-ancestor", (("/testbed/snapshot", True), ("/testbed/result", False)), ("/testbed/result/tmp",), "runtime_preflight_failed", "nested"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_mount_ancestor_collision_matrix_rejects_before_create(
    tmp_path: Path,
    case: str,
    binds: tuple[tuple[str, bool], ...],
    tmpfs_destinations: tuple[str, ...],
    expected_code: str,
    expected_detail: str,
) -> None:
    plan, skeleton, profile, _ = _docker_plan(tmp_path)
    sources = []
    for index, (destination, readonly) in enumerate(binds):
        source = tmp_path / f"collision-source-{index}"
        source.mkdir()
        sources.append((source, destination, readonly))
    options = "rw,noexec,nosuid,size=4096"
    plan = replace(
        plan,
        security_policy=replace(
            plan.security_policy,
            tmpfs_mounts=tuple(
                (*plan.security_policy.tmpfs_mounts, *((destination, options) for destination in tmpfs_destinations))
            ),
        ),
    )

    with pytest.raises(DockerAdapterError) as captured:
        build_create_argv(
            plan,
            lease_id="lease",
            workspace_id="workspace",
            epoch=1,
            role="verifier" if "verifier" in case else "primary",
            skeleton_path=skeleton,
            mounts=tuple(sources),
            security_profile_path=profile,
        )

    assert captured.value.code == expected_code
    assert expected_detail in str(captured.value)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("host-network", "runtime_unsupported"),
        ("security-root-rw", "runtime_preflight_failed"),
        ("security-cap-add", "runtime_preflight_failed"),
        ("security-nnp-off", "runtime_preflight_failed"),
        ("invalid-role", "runtime_preflight_failed"),
        ("docker-socket", "runtime_preflight_failed"),
        ("comma-mount", "workspace_escape"),
    ],
)
def test_forbidden_runtime_authority_rejects_before_executor_spawn(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    role = "primary"
    candidate_mounts = mounts
    if mutation == "host-network":
        plan = replace(
            plan,
            network_policy=replace(
                plan.network_policy,
                mode="host",
                docker_network="host",
                default_deny=False,
            ),
        )
    elif mutation == "security-root-rw":
        plan = replace(
            plan,
            security_policy=replace(plan.security_policy, read_only_root=False),
        )
    elif mutation == "security-cap-add":
        plan = replace(
            plan,
            security_policy=replace(plan.security_policy, drop_all_capabilities=False),
        )
    elif mutation == "security-nnp-off":
        plan = replace(
            plan,
            security_policy=replace(plan.security_policy, no_new_privileges=False),
        )
    elif mutation == "invalid-role":
        role = "admin"
    elif mutation == "docker-socket":
        candidate_mounts = ((mounts[0][0], "/var/run/docker.sock", False),)
    elif mutation == "comma-mount":
        candidate_mounts = ((mounts[0][0], "/testbed/bad,name", False),)

    with pytest.raises(DockerAdapterError) as captured:
        build_create_argv(
            plan,
            lease_id="lease",
            workspace_id="workspace",
            epoch=1,
            role=role,
            skeleton_path=skeleton,
            mounts=candidate_mounts,
            security_profile_path=profile,
        )

    assert captured.value.code == expected_code


def test_effective_host_network_is_rejected_when_declared_mode_remains_none(
    tmp_path: Path,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    plan = replace(
        plan,
        network_policy=replace(
            plan.network_policy,
            mode="none",
            default_deny=True,
            egress_route_ids=(),
            docker_network="host",
        ),
    )

    with pytest.raises(DockerAdapterError) as captured:
        build_create_argv(
            plan,
            lease_id="lease",
            workspace_id="workspace",
            epoch=1,
            role="primary",
            skeleton_path=skeleton,
            mounts=mounts,
            security_profile_path=profile,
        )

    assert captured.value.code == "runtime_unsupported"


@pytest.mark.parametrize(
    "namespace_flags",
    [
        ("--privileged=true",),
        ("--cap-add=SYS_ADMIN",),
        ("--cap-add", "SYS_ADMIN"),
        ("--device=/dev/fuse",),
        ("--device", "/dev/fuse"),
        ("--mount=type=bind,src=/,dst=/host",),
        ("--mount", "type=bind,src=/,dst=/host"),
        ("--network=host",),
        ("--network", "host"),
        ("--pid=host",),
        ("--ipc=host",),
        ("--user=0:0",),
        ("--user", "0:0"),
        ("--runtime=runc",),
        ("--memory=0",),
        ("--memory", "0"),
        ("--memory", "32000000"),
        ("--pids-limit=-1",),
        ("--security-opt=no-new-privileges=false",),
        ("--read-only=false",),
        ("--network=none", "--network=host"),
        ("--memory=32000000", "--memory=0"),
    ],
)
def test_raw_namespace_flags_cannot_alias_duplicate_or_override_closed_controls(
    tmp_path: Path,
    namespace_flags: tuple[str, ...],
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    plan = replace(
        plan,
        security_policy=replace(
            plan.security_policy,
            namespace_flags=namespace_flags,
        ),
    )

    with pytest.raises(DockerAdapterError) as captured:
        build_create_argv(
            plan,
            lease_id="lease",
            workspace_id="workspace",
            epoch=1,
            role="primary",
            skeleton_path=skeleton,
            mounts=mounts,
            security_profile_path=profile,
        )

    assert captured.value.code == "runtime_preflight_failed"


@pytest.mark.parametrize(
    ("lsm", "value"),
    [
        ("apparmor", "disable"),
        ("apparmor", "DISABLED"),
        ("apparmor", "NoNe"),
        ("apparmor", "unconfined"),
        ("selinux", "disable"),
        ("selinux", "DISABLED"),
        ("selinux", "NoNe"),
        ("selinux", "unconfined"),
    ],
)
async def test_reserved_lsm_disabling_values_reject_before_any_docker_command(
    tmp_path: Path,
    lsm: str,
    value: str,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    if lsm == "apparmor":
        security = replace(
            plan.security_policy,
            apparmor_profile=value,
            selinux_label=None,
        )
        expected_message = "AppArmor authority is disabling or malformed"
    else:
        security = replace(
            plan.security_policy,
            apparmor_profile=None,
            selinux_label=value,
        )
        expected_message = "SELinux authority is disabling or malformed"
    plan = replace(plan, security_policy=security)
    workspace, _ = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    executor = ScriptedDockerExecutor()
    provider = RecordingMeasurementProvider({})
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor, environment=()),
        measurement_provider=provider,
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )

    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(workspace_fd)
    context = replace(
        _launch_context(plan),
        workspace_fd=workspace_fd,
        workspace_identity=(metadata.st_dev, metadata.st_ino),
    )

    with pytest.raises(DockerAdapterError) as captured:
        await backend.launch(plan, workspace, context=context)

    assert captured.value.code == "runtime_preflight_failed"
    assert str(captured.value) == expected_message
    assert executor.calls == []
    assert provider.calls == []
    with pytest.raises(OSError):
        os.fstat(workspace_fd)
    backend.close()


@pytest.mark.parametrize(("uid", "gid"), [(0, 65534), (65534, 0), (0, 0)])
def test_root_container_identity_is_rejected_before_create_argv(
    tmp_path: Path,
    uid: int,
    gid: int,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    plan = replace(
        plan,
        security_policy=replace(plan.security_policy, uid=uid, gid=gid),
    )

    with pytest.raises(DockerAdapterError) as captured:
        build_create_argv(
            plan,
            lease_id="lease",
            workspace_id="workspace",
            epoch=1,
            role="primary",
            skeleton_path=skeleton,
            mounts=mounts,
            security_profile_path=profile,
        )

    assert captured.value.code == "runtime_preflight_failed"


@pytest.mark.parametrize(
    ("storage_authority_id", "quota_enforced", "quota_bytes"),
    [
        ("directory", False, None),
        ("quota-test", True, 1),
    ],
)
async def test_unmeasured_or_wrong_workspace_quota_rejects_before_any_docker_command(
    tmp_path: Path,
    storage_authority_id: str,
    quota_enforced: bool,
    quota_bytes: int | None,
) -> None:
    plan, skeleton, _, mounts = _docker_plan(tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    executor = ScriptedDockerExecutor()
    provider = RecordingMeasurementProvider({})
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor, environment=()),
        measurement_provider=provider,
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )
    context = _launch_context(
        plan,
        storage_authority_id=storage_authority_id,
        quota_enforced=quota_enforced,
        quota_bytes=quota_bytes,
    )

    with pytest.raises(DockerAdapterError) as captured:
        await backend.launch(
            plan,
            mounts[0][0].parent,
            context=context,
        )

    assert captured.value.code == "runtime_preflight_failed"
    assert executor.calls == []
    assert provider.calls == []


def test_mutable_image_reference_is_rejected_at_installed_authority_boundary() -> None:
    from breadboard.rl.harness.sandbox import InstalledImage

    with pytest.raises(ValueError, match="immutable digest reference"):
        InstalledImage(
            image_digest="sha256:" + "1" * 64,
            runtime_id="hardened-docker",
            immutable_reference="registry.example/task:latest",
        )


def test_docker_mechanics_environment_rejects_route_or_loader_authority(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor()
    environment = (("DOCKER_CONTEXT", "bb-test"), ("DOCKER_HOST", "unix:///bb.sock"))

    with pytest.raises(ValueError, match="mechanics environment must be empty"):
        _mechanics_adapter(plan, executor, environment=environment)

    assert executor.calls == []


def _private_binding(plan: Any) -> PrivateDockerDaemonBinding:
    return PrivateDockerDaemonBinding(
        daemon_instance_id="daemon-1",
        socket_path="/private/docker.sock",
        socket_device=11,
        socket_inode=12,
        socket_mode=0o600,
        socket_uid=0,
        socket_gid=0,
        daemon_pid=4242,
        daemon_starttime="12345",
        daemon_pid_namespace="pid:[4026531836]",
        daemon_executable_digest="sha256:" + "b" * 64,
        daemon_executable_device=3,
        daemon_executable_inode=4,
        daemon_executable_ctime_ns=7,
        daemon_executable_size=8,
        data_root="/private/data",
        config_fd=78,
        config_proc_path=f"/proc/{os.getpid()}/fd/78",
        daemon_config_digest="sha256:" + "a" * 64,
        config_device=5,
        config_inode=6,
        config_ctime_ns=9,
        config_size=10,
        runtime_fd=77,
        runtime_proc_path=f"/proc/{os.getpid()}/fd/77",
        runtime_registered_path="/private/stage/.runtime-bin/runc",
        runtime_digest=plan.runtime.oci_runtime_binary_digest,
        runtime_device=2,
        runtime_inode=1,
        runtime_ctime_ns=11,
        runtime_size=12,
    )


async def test_private_binding_uses_fixed_unix_host_and_satisfies_exact_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    binding = _private_binding(plan)
    metadata = os.stat_result((0o100555, 1, 2, 1, 0, 0, 1, 0, 0, 0))
    monkeypatch.setattr(
        PrivateDockerDaemonBinding, "validate_live", lambda self: metadata
    )
    results = _preflight_success(plan)
    results[1] = _result(stdout=json.dumps({
        "Server": "test",
        "DockerRootDir": binding.data_root,
        "Runtimes": {
            plan.runtime.oci_runtime_name: {
                "path": binding.runtime_registered_path,
                "runtimeArgs": [],
            }
        },
    }).encode())
    results[2] = _result(stdout=json.dumps({"Id": plan.image.image_digest}).encode())
    executor = ScriptedDockerExecutor(results)
    adapter = DockerRuntimeAdapter(
        executor=executor,
        cli_environment=(),
        mechanics_invocation=ExecutableInvocation(
            argv0=plan.runtime.executable_path,
            executable_fd=41,
            executable_descriptor_path="/test-only/mechanics/fd/41",
            digest=plan.runtime.measured_binary_digest,
        ),
        daemon_binding=binding,
    )

    observation = await adapter.preflight(plan)
    docker_module._require_daemon_runtime_binding(observation, plan)

    assert observation.daemon_binding is binding
    assert all(
        call[0][1:4]
        == ("--host", "unix:///private/docker.sock", command)
        for call, command in zip(executor.calls, ("version", "info", "image"), strict=True)
    )
    assert all(call[3] == () for call in executor.calls)


def test_daemon_binding_gate_rejects_path_only_observation(tmp_path: Path) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    observation = docker_module.DockerPreflightObservation(
        docker_cli_digest=plan.runtime.measured_binary_digest,
        platform_version="bb-test/test",
        runtime_name=plan.runtime.oci_runtime_name,
        advertised_path=plan.runtime.oci_runtime_binary_path,
        observed_oci_digest=plan.runtime.oci_runtime_binary_digest,
        observed_oci_device=1,
        observed_oci_inode=2,
        version_payload=b"{}",
        info_payload=b"{}",
        image_payload=b"[]",
    )

    with pytest.raises(DockerAdapterError) as captured:
        docker_module._require_daemon_runtime_binding(observation, plan)

    assert captured.value.code == "runtime_unsupported"
    assert captured.value.details["reason"] == "oci_runtime_exact_execution_unavailable"


def test_docker_execution_source_uses_descriptor_only_and_never_resolves_paths() -> None:
    executor_source = inspect.getsource(SubprocessDockerCliExecutor.execute)
    preflight_source = inspect.getsource(DockerRuntimeAdapter.preflight)

    assert "executable=executable.executable_descriptor_path" in executor_source
    assert "pass_fds=(executable.executable_fd,)" in executor_source
    assert "*logical_argv" in executor_source
    assert "Path.resolve" not in executor_source
    assert "Path.resolve" not in preflight_source
    assert '"--version"' not in preflight_source


async def test_darwin_production_adapter_refuses_before_executor_effect(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor(_preflight_success(plan))
    adapter = DockerRuntimeAdapter(executor=executor, cli_environment=())

    if platform.system() == "Darwin":
        with pytest.raises(DockerAdapterError) as captured:
            await adapter.preflight(plan)
        assert captured.value.code == "runtime_unsupported"
        assert executor.calls == []
        assert executor.invocations == []
        assert len(executor.results) == 3
        return

    measured = await adapter.preflight(plan)
    assert measured.docker_cli_digest == plan.runtime.measured_binary_digest
    assert [call[0][1] for call in executor.calls] == ["version", "info", "image"]


@pytest.mark.parametrize("mutation", ["rename-replacement", "same-inode"])
async def test_linux_concrete_executor_observes_sealed_old_cli_bytes(
    tmp_path: Path,
    mutation: str,
) -> None:
    if platform.system() != "Linux" or not Path("/proc/self/fd").is_dir():
        assert platform.system() == "Darwin"
        return

    plan, _, _, _ = _docker_plan(tmp_path)
    executable = Path(plan.runtime.executable_path)
    executable.write_bytes(b"#!/bin/sh\nprintf old-cli-bytes\n")
    executable.chmod(0o700)
    plan = replace(
        plan,
        runtime=replace(
            plan.runtime,
            measured_binary_digest=observe_binary_digest(executable),
        ),
    )
    adapter = DockerRuntimeAdapter(executor=ScriptedDockerExecutor(), cli_environment=())
    invocation = adapter._pin(plan)
    try:
        if mutation == "rename-replacement":
            replacement = tmp_path / "replacement-cli"
            replacement.write_bytes(b"#!/bin/sh\nprintf replacement-bytes\n")
            replacement.chmod(0o700)
            replacement.replace(executable)
        else:
            with executable.open("r+b", buffering=0) as handle:
                handle.seek(0)
                handle.write(b"#!/bin/sh\nprintf mutated-bytes!!\n")
                handle.truncate()

        result = await SubprocessDockerCliExecutor().execute(
            invocation,
            (),
            timeout_ms=1_000,
            output_limit=128,
            environment=(("PATH", "/usr/bin:/bin"),),
        )
    finally:
        adapter.close()

    assert result.returncode == 0
    assert result.stdout == b"old-cli-bytes"
    assert result.stderr == b""
    assert result.argv == (plan.runtime.executable_path,)


async def test_subprocess_executor_streams_payload_above_exec_argument_budget() -> (
    None
):
    executable = Path("/bin/cat")
    descriptor = os.open(executable, os.O_RDONLY)
    invocation = ExecutableInvocation(
        argv0=str(executable),
        executable_fd=descriptor,
        executable_descriptor_path=str(executable),
        digest=observe_binary_digest(executable),
    )
    payload = b"x" * (512 * 1024)
    try:
        result = await SubprocessDockerCliExecutor().execute(
            invocation,
            (),
            timeout_ms=1_000,
            output_limit=len(payload) + 1,
            environment=(("PATH", "/usr/bin:/bin"),),
            input_bytes=payload,
        )
    finally:
        os.close(descriptor)

    assert result.returncode == 0
    assert result.stdout == payload
    assert result.stderr == b""
    assert result.output_limited is False


async def test_subprocess_executor_bounds_descendant_held_output_pipes() -> None:
    executable = Path("/bin/sh")
    descriptor = os.open(executable, os.O_RDONLY)
    invocation = ExecutableInvocation(
        argv0=str(executable),
        executable_fd=descriptor,
        executable_descriptor_path=str(executable),
        digest=observe_binary_digest(executable),
    )
    started = asyncio.get_running_loop().time()
    try:
        result = await SubprocessDockerCliExecutor().execute(
            invocation,
            ("-c", "(sleep 10) & exit 0"),
            timeout_ms=200,
            output_limit=128,
            environment=(("PATH", "/usr/bin:/bin"),),
        )
    finally:
        os.close(descriptor)
    elapsed = asyncio.get_running_loop().time() - started

    assert result.timed_out is True
    assert result.output_limited is False
    assert elapsed < 1.5


async def test_subprocess_executor_stops_process_group_at_output_limit() -> None:
    executable = Path("/bin/sh")
    descriptor = os.open(executable, os.O_RDONLY)
    invocation = ExecutableInvocation(
        argv0=str(executable),
        executable_fd=descriptor,
        executable_descriptor_path=str(executable),
        digest=observe_binary_digest(executable),
    )
    started = asyncio.get_running_loop().time()
    try:
        result = await SubprocessDockerCliExecutor().execute(
            invocation,
            ("-c", "while :; do printf 0123456789abcdef; done"),
            timeout_ms=5_000,
            output_limit=128,
            environment=(("PATH", "/usr/bin:/bin"),),
        )
    finally:
        os.close(descriptor)
    elapsed = asyncio.get_running_loop().time() - started

    assert result.output_limited is True
    assert len(result.stdout) + len(result.stderr) == 128
    assert result.timed_out is False
    assert elapsed < 1.5


async def test_oci_runtime_symlink_is_rejected_before_image_or_create(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    target = Path(plan.runtime.oci_runtime_binary_path)
    link = tmp_path / "runc-link"
    link.symlink_to(target)
    plan = replace(
        plan,
        runtime=replace(
            plan.runtime,
            oci_runtime_binary_path=str(link),
            oci_runtime_binary_digest=observe_binary_digest(target),
        ),
    )
    results = _preflight_success(plan)
    executor = ScriptedDockerExecutor(results[:2])
    adapter = _mechanics_adapter(plan, executor)

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    assert captured.value.code == "runtime_preflight_failed"
    assert [call[0][1] for call in executor.calls] == ["version", "info"]
    assert not any(call[0][1] in {"image", "create"} for call in executor.calls)

async def test_installed_catalog_oci_identity_reaches_docker_preflight_unchanged(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        with_writable_mount=True,
    )
    oci_runtime = tmp_path / "catalog-runc"
    oci_runtime.write_bytes(b"catalog-pinned OCI runtime")
    catalog_path = str(oci_runtime)
    catalog_digest = observe_binary_digest(oci_runtime)
    installed_runtime = next(
        runtime
        for runtime in fixture.authorities.runtimes
        if runtime.runtime_id == fixture.plan.sandbox.runtime_id
    )
    catalog_runtime = replace(
        installed_runtime,
        oci_runtime_binary_path=catalog_path,
        oci_runtime_binary_digest=catalog_digest,
        supported_platform_versions=("bb-test/test",),
    )
    authorities = replace(
        fixture.authorities,
        runtimes=tuple(
            catalog_runtime if runtime.runtime_id == catalog_runtime.runtime_id else runtime
            for runtime in fixture.authorities.runtimes
        ),
    )

    plan = build_sandbox_execution_plan(
        fixture.request,
        fixture.registries,
        authorities,
    )
    executor = ScriptedDockerExecutor(
        [
            _result(
                stdout=json.dumps(
                    {
                        "Server": {
                            "Platform": {"Name": "bb-test"},
                            "Version": "test",
                        }
                    }
                ).encode("utf-8")
            ),
            _result(
                stdout=json.dumps(
                    {
                        "Server": "test",
                        "Runtimes": {
                            "runc": {
                                "path": catalog_path,
                                "runtimeArgs": [],
                            }
                        },
                    }
                ).encode("utf-8")
            ),
            _result(
                stdout=json.dumps(
                    {
                        "RepoDigests": [
                            "bb/test@" + fixture.plan.sandbox.image_digest
                        ]
                    }
                ).encode("utf-8")
            ),
        ]
    )

    assert plan.runtime.oci_runtime_binary_path == catalog_path
    assert plan.runtime.oci_runtime_binary_digest == catalog_digest
    measured = await _mechanics_adapter(plan, executor, environment=()).preflight(plan)

    assert measured.docker_cli_digest == plan.runtime.measured_binary_digest
    assert [call[0][1] for call in executor.calls] == ["version", "info", "image"]
    assert executor.results == []


@pytest.mark.parametrize(
    "supported_platform_versions",
    [(), ("bb-test/other",)],
)
async def test_empty_or_unsupported_platform_authority_rejects_before_engine_or_create(
    tmp_path: Path,
    supported_platform_versions: tuple[str, ...],
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    plan = replace(
        plan,
        runtime=replace(
            plan.runtime,
            supported_platform_versions=supported_platform_versions,
        ),
    )
    executor = ScriptedDockerExecutor(_preflight_success(plan))
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    assert captured.value.code == "runtime_unsupported"
    assert [call[0][1] for call in executor.calls] == ["version"]


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("binary", "runtime_preflight_failed"),
        ("runtime", "runtime_unsupported"),
        ("image", "runtime_preflight_failed"),
        ("timeout", "runtime_preflight_failed"),
        ("output", "output_limit_exceeded"),
        ("nonzero", "runtime_preflight_failed"),
    ],
)
async def test_preflight_failure_is_exact_and_never_falls_back(
    tmp_path: Path, case: str, expected_code: str
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    results = _preflight_success(plan)
    adapter: DockerRuntimeAdapter | None = None
    if case == "binary":
        executor = ScriptedDockerExecutor()
        adapter = _mechanics_adapter(plan, executor)
        plan = replace(
            plan,
            runtime=replace(
                plan.runtime, measured_binary_digest="sha256:" + "0" * 64
            ),
        )
        results = []
    elif case == "runtime":
        results[0] = _result(stdout=b'{"Runtime":"other"}')
        results[1] = _result(stdout=b'{"Runtimes":["other"]}')
    elif case == "image":
        results[2] = _result(stdout=b'{"RepoDigests":[]}')
    elif case == "timeout":
        results[0] = _result(timed_out=True)
    elif case == "output":
        results[0] = _result(output_limited=True)
    elif case == "nonzero":
        results[0] = _result(returncode=7, stderr=b"engine failed")
    if adapter is None:
        executor = ScriptedDockerExecutor(results)
        adapter = _mechanics_adapter(plan, executor, environment=())
    assert adapter is not None

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    assert captured.value.code == expected_code
    if case == "binary":
        assert executor.calls == []
    assert not any("runc" in value for call in executor.calls for value in call[0][1:2])


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("runtime-error-field", "runtime_unsupported"),
        ("runtime-empty-error-field", "runtime_unsupported"),
        ("runtime-missing", "runtime_unsupported"),
        ("runtime-mixed-list", "runtime_unsupported"),
        ("runtime-mixed-map", "runtime_unsupported"),
        ("malformed-version", "runtime_preflight_failed"),
        ("version-list", "runtime_preflight_failed"),
        ("malformed-info", "runtime_preflight_failed"),
        ("info-list", "runtime_preflight_failed"),
        ("image-error-field", "runtime_preflight_failed"),
        ("image-empty-error-field", "runtime_preflight_failed"),
        ("image-mixed-digests", "runtime_preflight_failed"),
        ("image-invalid-id", "runtime_preflight_failed"),
        ("image-invalid-repo-digests", "runtime_preflight_failed"),
        ("image-multiple-objects", "runtime_preflight_failed"),
        ("image-scalar", "runtime_preflight_failed"),
        ("malformed-substrings", "runtime_preflight_failed"),
    ],
)
async def test_malicious_engine_output_cannot_spoof_runtime_or_image_by_substring(
    tmp_path: Path, case: str, expected_code: str
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    results = _preflight_success(plan)
    digest = plan.image.image_digest
    if case == "runtime-error-field":
        results[0] = _result(stdout=b'{"Error":"runc"}')
        results[1] = _result(stdout=b'{"Warnings":["runc"]}')
    elif case == "runtime-empty-error-field":
        results[1] = _result(stdout=b'{"Error":"","Runtimes":["runc"]}')
    elif case == "runtime-missing":
        results[1] = _result(stdout=b'{"DefaultRuntime":"runc"}')
    elif case == "runtime-mixed-list":
        results[1] = _result(stdout=b'{"Runtimes":["runc",7]}')
    elif case == "runtime-mixed-map":
        results[1] = _result(stdout=b'{"Runtimes":{"runc":{},"runsc":7}}')
    elif case == "malformed-version":
        results[0] = _result(stdout=b'not-json {"Runtime":"runc"}')
    elif case == "version-list":
        results[0] = _result(stdout=b'[{"Runtime":"runc"}]')
    elif case == "malformed-info":
        results[1] = _result(stdout=b'not-json {"Runtimes":["runc"]}')
    elif case == "info-list":
        results[1] = _result(stdout=b'[{"Runtimes":["runc"]}]')
    elif case == "image-error-field":
        results[2] = _result(
            stdout=(
                '{"Error":"requested identity was '
                + digest
                + ' but image is mutable"}'
            ).encode("utf-8")
        )
    elif case == "image-empty-error-field":
        results[2] = _result(
            stdout=json.dumps({"Error": "", "Id": digest}).encode("utf-8")
        )
    elif case == "image-mixed-digests":
        results[2] = _result(
            stdout=json.dumps({"RepoDigests": ["bb/test@" + digest, 7]}).encode(
                "utf-8"
            )
        )
    elif case == "image-invalid-id":
        results[2] = _result(
            stdout=json.dumps(
                {"Id": 7, "RepoDigests": ["bb/test@" + digest]}
            ).encode("utf-8")
        )
    elif case == "image-invalid-repo-digests":
        results[2] = _result(
            stdout=json.dumps(
                {"Id": digest, "RepoDigests": "bb/test@" + digest}
            ).encode("utf-8")
        )
    elif case == "image-multiple-objects":
        results[2] = _result(
            stdout=json.dumps([{"Id": digest}, {"Id": digest}]).encode("utf-8")
        )
    elif case == "image-scalar":
        results[2] = _result(stdout=json.dumps("bb/test@" + digest).encode("utf-8"))
    else:
        results[0] = _result(stdout=b"not-json runc")
        results[1] = _result(stdout=b"not-json")
        results[2] = _result(stdout=("not-json " + digest).encode())
    executor = ScriptedDockerExecutor(results)
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    assert captured.value.code == expected_code
    commands = [call[0][1] for call in executor.calls]
    if case in {"malformed-version", "version-list", "malformed-substrings", "runtime-error-field"}:
        assert commands == ["version"]
    elif case.startswith("runtime-") or case in {"malformed-info", "info-list"}:
        assert commands == ["version", "info"]
    else:
        assert commands == ["version", "info", "image"]


@pytest.mark.parametrize(
    "failure",
    ["substitute-path", "binary-digest", "default-name-only"],
)
async def test_runc_registration_requires_exact_authorized_path_and_binary(
    tmp_path: Path,
    failure: str,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    if failure == "binary-digest":
        plan = replace(
            plan,
            runtime=replace(
                plan.runtime,
                oci_runtime_binary_digest="sha256:" + "0" * 64,
            ),
        )
    successful = _preflight_success(plan)
    if failure == "substitute-path":
        substitute = tmp_path / "substitute-runc"
        substitute.write_bytes(b"pinned OCI runtime")
        info = {
            "Runtimes": {
                "runc": {
                    "path": str(substitute),
                    "runtimeArgs": [],
                }
            }
        }
    elif failure == "default-name-only":
        info = {
            "DefaultRuntime": "runc",
            "Runtimes": {"runc": {}},
        }
    else:
        info = json.loads(successful[1].stdout)
    executor = ScriptedDockerExecutor(
        [
            successful[0],
            _result(stdout=json.dumps(info).encode("utf-8")),
        ]
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    expected_code = (
        "runtime_preflight_failed"
        if failure == "binary-digest"
        else "runtime_unsupported"
    )
    assert captured.value.code == expected_code
    assert [call[0][1] for call in executor.calls] == ["version", "info"]
    assert executor.results == []


async def test_runsc_name_only_registration_fails_before_probe_image_or_create(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path, gvisor=True)
    successful = _preflight_success(plan)
    executor = ScriptedDockerExecutor(
        [
            successful[0],
            _result(
                stdout=json.dumps({"Runtimes": {"runsc": {}}}).encode("utf-8")
            ),
        ]
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    assert captured.value.code == "runtime_unsupported"
    assert [call[0][1] for call in executor.calls] == ["version", "info"]
    assert executor.results == []


async def test_runsc_registration_must_resolve_to_the_pinned_binary(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path, gvisor=True)
    pinned_runsc = tmp_path / "pinned-runsc"
    pinned_runsc.write_bytes(b"pinned runsc")
    registered_substitute = tmp_path / "registered-runsc"
    registered_substitute.write_bytes(b"not the admitted runtime")
    plan = replace(
        plan,
        runtime=replace(
            plan.runtime,
            runsc_binary_path=str(pinned_runsc),
            runsc_binary_digest=observe_binary_digest(pinned_runsc),
            oci_runtime_binary_path=str(pinned_runsc),
            oci_runtime_binary_digest=observe_binary_digest(pinned_runsc),
        ),
    )
    results = _preflight_success(plan)
    results[1] = _result(
        stdout=json.dumps(
            {
                "Server": "test",
                "Runtimes": {
                    "runsc": {
                        "path": str(registered_substitute),
                        "runtimeArgs": [],
                    },
                },
            }
        ).encode("utf-8")
    )
    executor = ScriptedDockerExecutor(results)
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    assert captured.value.code == "runtime_unsupported"
    assert [call[0][1] for call in executor.calls] == ["version", "info"]


async def test_complete_runsc_preflight_observes_binary_without_direct_probe(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path, gvisor=True)
    runsc = tmp_path / "runsc"
    runsc.write_bytes(b"pinned runsc")
    plan = replace(
        plan,
        runtime=replace(
            plan.runtime,
            runsc_binary_path=str(runsc),
            runsc_binary_digest=observe_binary_digest(runsc),
            oci_runtime_binary_path=str(runsc),
            oci_runtime_binary_digest=observe_binary_digest(runsc),
        ),
    )
    executor = ScriptedDockerExecutor(_preflight_success(plan))
    adapter = _mechanics_adapter(plan, executor, environment=())

    measured = await adapter.preflight(plan)

    assert measured.docker_cli_digest == plan.runtime.measured_binary_digest
    assert measured.observed_oci_digest == plan.runtime.runsc_binary_digest
    assert [call[0][1] for call in executor.calls] == [
        "version",
        "info",
        "image",
    ]
    assert all("--version" not in call[0] for call in executor.calls)
    assert all("runc" not in argument for call in executor.calls for argument in call[0])


async def test_runsc_digest_failure_never_substitutes_runc(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path, gvisor=True)
    runsc = tmp_path / "runsc"
    runsc.write_bytes(b"pinned runsc")
    plan = replace(
        plan,
        runtime=replace(
            plan.runtime,
            runsc_binary_path=str(runsc),
            runsc_binary_digest="sha256:" + "0" * 64,
            oci_runtime_binary_path=str(runsc),
            oci_runtime_binary_digest="sha256:" + "0" * 64,
        ),
    )
    executor = ScriptedDockerExecutor(_preflight_success(plan))
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.preflight(plan)

    assert captured.value.code == "runtime_preflight_failed"
    assert [call[0][1] for call in executor.calls] == ["version", "info"]
    assert all("runc" not in argument for call in executor.calls for argument in call[0])


async def test_prepared_identity_is_inspected_and_persisted_before_start(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, mounts = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    installed_profile = security_root / (
        plan.security_policy.seccomp_digest.removeprefix("sha256:") + ".json"
    )
    trace: list[str] = []
    published: list[RuntimePreparedIdentity] = []

    async def publish(identity: RuntimePreparedIdentity) -> None:
        assert trace == ["version", "info", "image", "create", "inspect"]
        assert identity.runtime_resource_id == CONTAINER_ID
        assert dict(identity.labels) == _binding_labels(plan)
        published.append(identity)
        trace.append("persist")

    inspect_bytes = _docker_inspect_bytes(
        _docker_inspect_payload(plan, skeleton, installed_profile, mounts)
    )
    executor = ScriptedDockerExecutor(
        [
            *_preflight_success(plan),
            _result(stdout=CONTAINER_ID.encode("ascii")),
            _result(stdout=_identity_inspect(plan)),
            _result(),
            _result(stdout=inspect_bytes),
        ],
        trace=trace,
    )
    adapter = _mechanics_adapter(plan, executor, environment=())
    container_id, observed = await _exercise_nonadmissible_prepare_publish_start(
        plan,
        adapter,
        skeleton=skeleton,
        mounts=mounts,
        security_profile_path=installed_profile,
        publish=publish,
    )

    assert trace == [
        "version",
        "info",
        "image",
        "create",
        "inspect",
        "persist",
        "start",
        "inspect",
    ]
    assert len(published) == 1
    assert container_id == published[0].runtime_resource_id
    assert observed == inspect_bytes
    _assert_exact_mechanics_invocation(executor, plan)
    assert executor.results == []


async def test_prepared_identity_persistence_failure_removes_exact_container_before_reraise(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, mounts = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    trace: list[str] = []
    failure = RuntimeError("durable identity write failed")

    async def fail_persistence(identity: RuntimePreparedIdentity) -> None:
        assert identity.runtime_resource_id == CONTAINER_ID
        assert dict(identity.labels) == _binding_labels(plan)
        trace.append("persist-failed")
        raise failure

    executor = ScriptedDockerExecutor(
        [
            *_preflight_success(plan),
            _result(stdout=CONTAINER_ID.encode("ascii")),
            _result(stdout=_identity_inspect(plan)),
            _result(stdout=_identity_inspect(plan)),
            _result(),
            _result(),
            _not_found(CONTAINER_ID),
        ],
        trace=trace,
    )
    installed_profile = security_root / (
        plan.security_policy.seccomp_digest.removeprefix("sha256:") + ".json"
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(RuntimeError) as captured:
        await _exercise_nonadmissible_prepare_publish_start(
            plan,
            adapter,
            skeleton=skeleton,
            mounts=mounts,
            security_profile_path=installed_profile,
            publish=fail_persistence,
        )

    assert captured.value is failure
    assert trace == [
        "version",
        "info",
        "image",
        "create",
        "inspect",
        "persist-failed",
        "inspect",
        "stop",
        "rm",
        "inspect",
    ]
    assert all(call[0][1] != "start" for call in executor.calls)
    assert [call[0] for call in executor.calls[-4:]] == [
        (plan.runtime.executable_path, "inspect", CONTAINER_ID),
        (plan.runtime.executable_path, "stop", "--time", "5", CONTAINER_ID),
        (plan.runtime.executable_path, "rm", "--force", CONTAINER_ID),
        (plan.runtime.executable_path, "inspect", CONTAINER_ID),
    ]
    assert executor.results == []


async def test_prepared_identity_persistence_failure_quarantines_identity_mismatch_without_effect(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, mounts = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    trace: list[str] = []
    failure = RuntimeError("durable identity write failed")

    async def fail_persistence(_: RuntimePreparedIdentity) -> None:
        trace.append("persist-failed")
        raise failure

    wrong_labels = _binding_labels(plan)
    wrong_labels["bb.epoch"] = "2"
    executor = ScriptedDockerExecutor(
        [
            *_preflight_success(plan),
            _result(stdout=CONTAINER_ID.encode("ascii")),
            _result(stdout=_identity_inspect(plan)),
            _result(stdout=_identity_inspect(plan, labels=wrong_labels)),
        ],
        trace=trace,
    )
    installed_profile = security_root / (
        plan.security_policy.seccomp_digest.removeprefix("sha256:") + ".json"
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(RuntimeError) as captured:
        await _exercise_nonadmissible_prepare_publish_start(
            plan,
            adapter,
            skeleton=skeleton,
            mounts=mounts,
            security_profile_path=installed_profile,
            publish=fail_persistence,
        )

    assert captured.value is failure
    assert trace == [
        "version",
        "info",
        "image",
        "create",
        "inspect",
        "persist-failed",
        "inspect",
    ]
    assert not {"start", "stop", "rm"} & {call[0][1] for call in executor.calls}
    assert executor.calls[-1][0] == (
        plan.runtime.executable_path,
        "inspect",
        CONTAINER_ID,
    )
    assert executor.results == []


async def test_non_linux_descriptor_mounts_fail_before_create_publish_or_measurement(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, _ = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    trace: list[str] = []
    published: list[RuntimePreparedIdentity] = []
    provider = RecordingMeasurementProvider({})
    executor = ScriptedDockerExecutor(_preflight_success(plan), trace=trace)
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor),
        measurement_provider=provider,
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )

    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(workspace_fd)
    context = replace(
        _launch_context(
            plan,
            publish_prepared_identity=lambda identity: published.append(identity),
        ),
        workspace_fd=workspace_fd,
        workspace_identity=(metadata.st_dev, metadata.st_ino),
    )
    with pytest.raises(DockerAdapterError) as captured:
        await backend.launch(plan, workspace, context=context)

    assert captured.value.code == "runtime_unsupported"
    assert captured.value.details == {"platform": sys.platform}
    assert trace == []
    assert published == []
    assert provider.calls == []
    assert executor.calls == []
    with pytest.raises(OSError):
        os.fstat(workspace_fd)


async def test_linux_without_private_mount_stager_fails_before_daemon_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, _ = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security-no-stager"
    security_root.mkdir()
    executor = ScriptedDockerExecutor(_preflight_success(plan))
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor),
        measurement_provider=RecordingMeasurementProvider({}),
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )
    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(workspace_fd)
    context = replace(
        _launch_context(plan),
        workspace_fd=workspace_fd,
        workspace_identity=(metadata.st_dev, metadata.st_ino),
    )
    monkeypatch.setattr(docker_module.sys, "platform", "linux")

    with pytest.raises(DockerAdapterError) as captured:
        await backend.launch(plan, workspace, context=context)

    assert captured.value.code == "runtime_unsupported"
    assert captured.value.details == {
        "reason": "descriptor_mount_staging_unavailable"
    }
    assert executor.calls == []
    with pytest.raises(OSError):
        os.fstat(workspace_fd)


async def test_launch_releases_every_staged_mount_and_reports_residual_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, _ = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security-stage-release"
    security_root.mkdir()
    released: list[str] = []

    class Adapter:
        async def preflight(self, _: Any) -> None:
            return None

    class Stager:
        def __init__(self) -> None:
            self.validations = 0

        async def stage(self, descriptor: int, **kwargs: Any) -> Any:
            metadata = os.fstat(descriptor)
            return docker_module.StagedDockerDescriptorMount(
                source_path=f"/staged/{descriptor}",
                source_device=metadata.st_dev,
                source_inode=metadata.st_ino,
                source_mode=stat.S_IFMT(metadata.st_mode),
                descriptor_device=metadata.st_dev,
                descriptor_inode=metadata.st_ino,
            )

        async def validate(self, staged: Any, descriptor: int) -> None:
            self.validations += 1
            if self.validations == 2:
                raise DockerAdapterError(
                    "runtime_preflight_failed", "staged validation failed"
                )

        async def release(self, staged: Any) -> None:
            released.append(staged.source_path)
            if len(released) == 1:
                raise RuntimeError("first release failed")

    def open_beneath(
        directory_fd: int,
        relative_path: str,
        *,
        readable_regular: bool = False,
    ) -> int:
        return os.open(relative_path, os.O_RDONLY, dir_fd=directory_fd)

    monkeypatch.setattr(docker_module.sys, "platform", "linux")
    monkeypatch.setattr(docker_module, "_openat2_beneath", open_beneath)
    monkeypatch.setattr(
        docker_module,
        "_require_daemon_runtime_binding",
        lambda observation, admitted_plan: None,
    )
    backend = DockerSandboxBackend(
        adapter=Adapter(),
        measurement_provider=RecordingMeasurementProvider({}),
        skeleton_path=skeleton,
        security_profile_root=security_root,
        mount_stager=Stager(),
    )
    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(workspace_fd)
    context = replace(
        _launch_context(plan),
        workspace_fd=workspace_fd,
        workspace_identity=(metadata.st_dev, metadata.st_ino),
    )

    with pytest.raises(DockerAdapterError) as captured:
        await backend.launch(plan, workspace, context=context)

    assert captured.value.code == "runtime_preflight_failed"
    assert len(released) == 2
    residual = backend._quarantined_stages[0]
    assert captured.value.details["staged_mount_cleanup"] == (
        (
            residual.source_device,
            residual.source_inode,
            "RuntimeError",
        ),
    )
    assert os.fstat(workspace_fd)
    with pytest.raises(RuntimeError, match="quarantined"):
        backend.close()

    await backend.close_runtime()

    assert len(released) == 3
    assert backend._quarantined_stages == []
    with pytest.raises(OSError):
        os.fstat(workspace_fd)


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux openat2/O_PATH")
def test_linux_openat2_profile_descriptor_is_readable_and_exact(
    tmp_path: Path,
) -> None:
    root = tmp_path / "security"
    root.mkdir()
    raw = b'{"defaultAction":"SCMP_ACT_ERRNO"}'
    profile = root / "profile.json"
    profile.write_bytes(raw)
    profile.chmod(0o400)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    path_fd = -1
    readable_fd = -1
    try:
        path_fd = docker_module._openat2_beneath(root_fd, profile.name)
        with pytest.raises(OSError) as captured:
            os.pread(path_fd, 1, 0)
        assert captured.value.errno == 9

        readable_fd = docker_module._openat2_beneath(
            root_fd,
            profile.name,
            readable_regular=True,
        )
        metadata = os.fstat(readable_fd)
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert (
            docker_module._bounded_regular_file_descriptor_bytes(
                readable_fd,
                expected_metadata=metadata,
                max_bytes=len(raw),
            )
            == raw
        )
    finally:
        for descriptor in (readable_fd, path_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


async def test_descriptor_success_retains_fds_until_exact_container_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, _ = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security-success"
    security_root.mkdir()
    trace: list[str] = []
    open_modes: list[bool] = []

    class Adapter:
        def __init__(self) -> None:
            self.mounts: tuple[tuple[Path, str, bool], ...] = ()
            self.skeleton = Path()
            self.profile = Path()

        async def preflight(self, _: Any) -> None:
            trace.append("preflight")

        async def prepare(self, _: Any, **kwargs: Any) -> tuple[str, str, tuple[str, ...]]:
            trace.append("create")
            self.mounts = tuple(kwargs["mounts"])
            self.skeleton = kwargs["skeleton_path"]
            self.profile = kwargs["security_profile_path"]
            profile = docker_module._bounded_regular_file_descriptor_bytes(
                kwargs["security_profile_descriptor"],
                expected_metadata=kwargs["security_profile_metadata"],
                max_bytes=len(plan.security_policy.seccomp_bytes),
            )
            assert profile == plan.security_policy.seccomp_bytes
            assert digest(profile) == plan.security_policy.seccomp_digest
            return CONTAINER_ID, "bb-primary-workspace-1", ()

        async def start(self, _: Any, container_id: str) -> None:
            assert trace[-1] == "persist"
            assert container_id == CONTAINER_ID
            trace.append("start")

        async def inspect(self, _: Any, container_id: str) -> bytes:
            assert container_id == CONTAINER_ID
            trace.append("inspect")
            return _docker_inspect_bytes(
                _docker_inspect_payload(plan, self.skeleton, self.profile, self.mounts)
            )

        async def cleanup(self, _: Any, reference: str, **kwargs: Any) -> tuple[tuple[str, str, str], ...]:
            assert reference == CONTAINER_ID
            trace.append("cleanup")
            return (
                ("runtime_remove", "released", ""),
                ("runtime_absence", "released", ""),
            )

    class Stager:
        async def stage(self, descriptor: int, **kwargs: Any) -> Any:
            metadata = os.fstat(descriptor)
            return docker_module.StagedDockerDescriptorMount(
                source_path=f"/staged/{descriptor}",
                source_device=metadata.st_dev,
                source_inode=metadata.st_ino,
                source_mode=stat.S_IFMT(metadata.st_mode),
                descriptor_device=metadata.st_dev,
                descriptor_inode=metadata.st_ino,
            )

        async def validate(self, staged: Any, descriptor: int) -> None:
            return None

        async def release(self, staged: Any) -> None:
            trace.append("stage-release")

    def open_beneath(
        directory_fd: int,
        relative_path: str,
        *,
        readable_regular: bool = False,
    ) -> int:
        open_modes.append(readable_regular)
        return os.open(relative_path, os.O_RDONLY, dir_fd=directory_fd)

    monkeypatch.setattr(docker_module.sys, "platform", "linux")
    monkeypatch.setattr(docker_module, "_openat2_beneath", open_beneath)
    monkeypatch.setattr(
        docker_module, "_require_daemon_runtime_binding", lambda observation, plan: None
    )
    adapter = Adapter()
    provider = RecordingMeasurementProvider({})
    backend = DockerSandboxBackend(
        adapter=adapter,
        measurement_provider=provider,
        skeleton_path=skeleton,
        security_profile_root=security_root,
        mount_stager=Stager(),
    )
    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    metadata = os.fstat(workspace_fd)

    async def publish(identity: RuntimePreparedIdentity) -> None:
        assert identity.runtime_resource_id == CONTAINER_ID
        trace.append("persist")

    context = replace(
        _launch_context(plan, publish_prepared_identity=publish),
        workspace_fd=workspace_fd,
        workspace_identity=(metadata.st_dev, metadata.st_ino),
    )
    handle, measurement = await backend.launch(plan, workspace, context=context)

    assert trace == ["preflight", "create", "persist", "start", "inspect"]
    assert open_modes[-1] is True
    assert all(mode is False for mode in open_modes[:-1])
    assert adapter.skeleton == Path(f"/staged/{workspace_fd}")
    assert all(
        str(source).startswith("/staged/")
        and destination.startswith("/testbed/")
        for source, destination, _ in adapter.mounts
    )
    assert measurement.isolated is True
    assert measurement.reward_eligible is True
    assert measurement.mismatch == ()
    held = tuple(handle._held_fds)
    assert workspace_fd in held
    assert all(os.fstat(descriptor) for descriptor in held)

    receipts = await handle.terminate()
    assert receipts[-1].resource == "descriptor_staging"
    assert trace[-1] == "stage-release"
    for descriptor in held:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    backend.close()


async def test_hardened_backend_refusal_does_not_install_security_profile(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace, _ = _primary_workspace(plan, tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    executor = ScriptedDockerExecutor(_preflight_success(plan))
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor),
        measurement_provider=RecordingMeasurementProvider({}),
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )

    with pytest.raises(DockerAdapterError, match="pinned workspace descriptor required"):
        await backend.launch(plan, workspace, context=_launch_context(plan))

    assert tuple(security_root.iterdir()) == ()
    assert executor.calls == []


async def test_verifier_backend_refuses_before_create_or_measurement(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    workspace = tmp_path / "verifier-workspace"
    (workspace / "snapshot").mkdir(parents=True)
    (workspace / "result").mkdir()
    context = _launch_context(
        plan,
        role="verifier",
        workspace_id="verifier-1",
        quota_bytes=min(
            plan.resources.storage_bytes,
            plan.limits.artifact_bytes_total,
        ),
    )
    security_root = tmp_path / "security"
    security_root.mkdir()
    provider = RecordingMeasurementProvider({})
    executor = ScriptedDockerExecutor(_preflight_success(plan))
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor),
        measurement_provider=provider,
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )

    with pytest.raises(DockerAdapterError) as captured:
        await backend.launch(plan, workspace, context=context)

    assert captured.value.code == "workspace_descriptor_required"
    assert executor.calls == []
    assert provider.calls == []


@pytest.mark.parametrize(
    ("case", "expected_mismatch"),
    [
        ("runtime", "runtime"),
        ("image", "image"),
        ("user", "user"),
        ("read-only-root", "read_only_root"),
        ("network", "network"),
        ("cpu-period", "cpu_period"),
        ("cpu-quota", "cpu_quota"),
        ("memory", "memory"),
        ("memory-swap", "memory_swap"),
        ("pids", "pids"),
        ("nofile", "nofile"),
        ("storage", "storage"),
        ("missing-runtime", "runtime"),
        ("missing-image", "image"),
        ("missing-user", "user"),
        ("missing-network", "network"),
        ("missing-memory", "memory"),
        ("missing-pids", "pids"),
    ],
)
def test_inspect_decoder_reports_each_effective_control_drift_from_observed_state(
    tmp_path: Path,
    case: str,
    expected_mismatch: str,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    inspected = _docker_inspect_payload(plan, skeleton, profile, mounts)
    host = inspected["HostConfig"]
    config = inspected["Config"]
    storage_bytes = plan.resources.storage_bytes
    if case == "runtime":
        host["Runtime"] = "substitute"
    elif case == "image":
        inspected["Image"] = "sha256:" + "0" * 64
    elif case == "user":
        config["User"] = "0:0"
    elif case == "read-only-root":
        host["ReadonlyRootfs"] = False
    elif case == "network":
        host["NetworkMode"] = "host"
    elif case == "cpu-period":
        host["CpuPeriod"] = 99_999
    elif case == "cpu-quota":
        host["CpuQuota"] = plan.resources.cpu_millis * 100 + 1
    elif case == "memory":
        host["Memory"] = plan.resources.memory_bytes + 1
    elif case == "memory-swap":
        host["MemorySwap"] = plan.resources.memory_bytes + 1
    elif case == "pids":
        host["PidsLimit"] = plan.resources.pids + 1
    elif case == "nofile":
        host["Ulimits"][0]["Hard"] = plan.resources.open_files + 1
    elif case == "storage":
        storage_bytes = 1
    elif case == "missing-runtime":
        del host["Runtime"]
    elif case == "missing-image":
        del inspected["Image"]
    elif case == "missing-user":
        del config["User"]
    elif case == "missing-network":
        del host["NetworkMode"]
    elif case == "missing-memory":
        del host["Memory"]
    elif case == "missing-pids":
        del host["PidsLimit"]

    measured = decode_docker_inspect(
        _docker_inspect_bytes(inspected),
        plan,
        container_id=CONTAINER_ID,
        container_name="bb-primary-workspace-1",
        labels=_binding_labels(plan),
        skeleton_path=skeleton,
        mounts=mounts,
        security_profile_path=profile,
        storage_bytes=storage_bytes,
    )

    assert measurement_mismatches(
        requested_measurement(
            plan,
            mounts,
            identity=_measurement_identity(plan),
        ),
        measured,
    ) == (expected_mismatch,)


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing-host-config", "runtime_measurement_mismatch"),
        ("privileged", "runtime_measurement_mismatch"),
        ("cap-add", "runtime_measurement_mismatch"),
        ("cap-drop", "runtime_measurement_mismatch"),
        ("missing-devices", "runtime_measurement_mismatch"),
        ("list-devices", "runtime_measurement_mismatch"),
        ("nonempty-devices", "runtime_measurement_mismatch"),
        ("missing-device-requests", "runtime_measurement_mismatch"),
        ("list-device-requests", "runtime_measurement_mismatch"),
        ("nonempty-device-requests", "runtime_measurement_mismatch"),
        ("missing-device-cgroup-rules", "runtime_measurement_mismatch"),
        ("list-device-cgroup-rules", "runtime_measurement_mismatch"),
        ("nonempty-device-cgroup-rules", "runtime_measurement_mismatch"),
        ("missing-tmpfs", "runtime_measurement_mismatch"),
        ("wrong-tmpfs", "runtime_measurement_mismatch"),
        ("extra-tmpfs", "runtime_measurement_mismatch"),
        ("no-new-privileges", "runtime_measurement_mismatch"),
        ("seccomp", "runtime_measurement_mismatch"),
        ("lsm", "runtime_measurement_mismatch"),
        ("mount-writable", "runtime_measurement_mismatch"),
        ("extra-network", "runtime_measurement_mismatch"),
        ("mutable-config-image", "runtime_measurement_mismatch"),
        ("unknown-binding-label", "stale_identity_uncertain"),
        ("missing-nofile", "runtime_measurement_mismatch"),
        ("cgroup-parent", "runtime_measurement_mismatch"),
        ("cgroup-namespace", "runtime_measurement_mismatch"),
        ("ipc-namespace", "runtime_measurement_mismatch"),
        ("pid-namespace", "runtime_measurement_mismatch"),
        ("uts-namespace", "runtime_measurement_mismatch"),
    ],
)
def test_inspect_decoder_rejects_malformed_or_contradictory_closed_schema(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    inspected = _docker_inspect_payload(plan, skeleton, profile, mounts)
    if case == "missing-host-config":
        del inspected["HostConfig"]
    elif case == "privileged":
        inspected["HostConfig"]["Privileged"] = True
    elif case == "cap-add":
        inspected["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    elif case == "cap-drop":
        inspected["HostConfig"]["CapDrop"] = []
    elif case.startswith(("missing-device", "list-device", "nonempty-device")):
        field = {
            "devices": "Devices",
            "device-requests": "DeviceRequests",
            "device-cgroup-rules": "DeviceCgroupRules",
        }[case.partition("-")[2]]
        if case.startswith("missing-"):
            del inspected["HostConfig"][field]
        elif case.startswith("list-"):
            inspected["HostConfig"][field] = []
        else:
            inspected["HostConfig"][field] = ["host-authority"]
    elif case == "missing-tmpfs":
        del inspected["HostConfig"]["Tmpfs"]
    elif case == "wrong-tmpfs":
        inspected["HostConfig"]["Tmpfs"]["/tmp"] = (
            "rw,noexec,nosuid,size=1048577"
        )
    elif case == "extra-tmpfs":
        inspected["HostConfig"]["Tmpfs"]["/host"] = "rw"
    elif case == "no-new-privileges":
        inspected["HostConfig"]["SecurityOpt"].remove("no-new-privileges")
    elif case == "seccomp":
        inspected["HostConfig"]["SecurityOpt"][1] = "seccomp=unconfined"
    elif case == "lsm":
        inspected["HostConfig"]["SecurityOpt"][2] = "apparmor=unconfined"
    elif case == "mount-writable":
        inspected["Mounts"][0]["RW"] = True
    elif case == "extra-network":
        inspected["NetworkSettings"]["Networks"]["bridge"] = {}
    elif case == "mutable-config-image":
        inspected["Config"]["Image"] = "bb/test:latest"
    elif case == "unknown-binding-label":
        inspected["Config"]["Labels"]["bb.unknown"] = "forged"
    elif case == "missing-nofile":
        inspected["HostConfig"]["Ulimits"] = []
    elif case == "cgroup-parent":
        inspected["HostConfig"]["CgroupParent"] = "host.slice"
    elif case == "cgroup-namespace":
        inspected["HostConfig"]["CgroupnsMode"] = "host"
    elif case == "ipc-namespace":
        inspected["HostConfig"]["IpcMode"] = "host"
    elif case == "pid-namespace":
        inspected["HostConfig"]["PidMode"] = "host"
    elif case == "uts-namespace":
        inspected["HostConfig"]["UTSMode"] = "host"

    with pytest.raises(DockerAdapterError) as captured:
        decode_docker_inspect(
            _docker_inspect_bytes(inspected),
            plan,
            container_id=CONTAINER_ID,
            container_name="bb-primary-workspace-1",
            labels=_binding_labels(plan),
            skeleton_path=skeleton,
            mounts=mounts,
            security_profile_path=profile,
            storage_bytes=plan.resources.storage_bytes,
        )

    assert captured.value.code == expected_code


def test_measurement_oracle_reports_missing_extra_and_wrong_fields_deterministically() -> None:
    requested = {"user": "1:1", "network": "none", "pids": 10}
    measured = {"user": "2:2", "network": "none", "extra": "ignored"}

    assert measurement_mismatches(requested, measured) == ("pids", "user")


async def test_output_boundary_failure_remains_typed(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor([_result(output_limited=True)])
    adapter = _mechanics_adapter(plan, executor, environment=())

    with pytest.raises(DockerAdapterError) as captured:
        await adapter.exec(plan, CONTAINER_ID, ("printf", "bomb"), timeout_ms=1_000)

    assert captured.value.code == "output_limit_exceeded"
    assert executor.results == []


@pytest.mark.parametrize(
    ("fault", "cleanup_mode", "expected_code"),
    [
        ("timeout", "released", "runtime_launch_failed"),
        ("output-limit", "released", "output_limit_exceeded"),
        ("timeout", "quarantined", "runtime_launch_failed"),
    ],
)
async def test_indeterminate_exec_fences_handle_and_caches_identity_safe_cleanup(
    tmp_path: Path,
    fault: str,
    cleanup_mode: str,
    expected_code: str,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    exec_result = (
        _result(timed_out=True)
        if fault == "timeout"
        else _result(output_limited=True)
    )
    if cleanup_mode == "released":
        stop_result = (
            _result(timed_out=True)
            if fault == "timeout"
            else _result()
        )
        executor.results.extend(
            [
                exec_result,
                _result(stdout=_identity_inspect(plan)),
                stop_result,
                _result(),
                _not_found(CONTAINER_ID),
            ]
        )
        expected_cleanup = [
            (
                "runtime_stop",
                "released",
                "final absence proven" if fault == "timeout" else "",
            ),
            ("runtime_remove", "released", ""),
            ("runtime_absence", "released", ""),
        ]
        expected_commands = ["exec", "inspect", "stop", "rm", "inspect"]
    else:
        wrong_labels = _binding_labels(plan)
        wrong_labels["bb.epoch"] = "2"
        executor.results.extend(
            [
                exec_result,
                _result(stdout=_identity_inspect(plan, labels=wrong_labels)),
            ]
        )
        expected_cleanup = [
            (
                "runtime_identity",
                "quarantined",
                "stale_identity_uncertain",
            )
        ]
        expected_commands = ["exec", "inspect"]
    calls_before_fault = len(executor.calls)

    with pytest.raises(DockerAdapterError) as captured:
        await handle.run_argv(
            ("sh", "-lc", "ambiguous"),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )

    assert captured.value.code == expected_code
    cleanup = captured.value.details["cleanup"]
    assert [
        (step.resource, step.state.value, step.detail)
        for step in cleanup
    ] == expected_cleanup
    assert [
        call[0][1] for call in executor.calls[calls_before_fault:]
    ] == expected_commands
    calls_after_cleanup = list(executor.calls)
    with pytest.raises(DockerAdapterError) as fenced:
        await handle.run_argv(
            ("printf", "must-not-run"),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )
    assert fenced.value.code == "lease_not_active"
    assert executor.calls == calls_after_cleanup
    if cleanup_mode == "released":
        assert await handle.terminate() == cleanup
        assert executor.calls == calls_after_cleanup
    else:
        executor.results.extend(
            [
                _result(stdout=_identity_inspect(plan)),
                _result(),
                _result(),
                _not_found(CONTAINER_ID),
            ]
        )
        retry = await handle.terminate()
        assert [step.state.value for step in retry] == [
            "released",
            "released",
            "released",
        ]
        assert [
            call[0][1] for call in executor.calls[len(calls_after_cleanup):]
        ] == ["inspect", "stop", "rm", "inspect"]
        calls_after_retry = list(executor.calls)
        assert await handle.terminate() == retry
        assert executor.calls == calls_after_retry
    assert executor.results == []


async def test_failed_indeterminate_exec_cleanup_retries_once_and_coalesces_callers(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    daemon_failure = _result(
        returncode=1,
        stderr=b"Cannot connect to the Docker daemon",
    )
    executor.results.extend(
        [
            _result(timed_out=True),
            _result(stdout=_identity_inspect(plan)),
            daemon_failure,
            daemon_failure,
            daemon_failure,
        ]
    )
    calls_before_fault = len(executor.calls)

    with pytest.raises(DockerAdapterError) as captured:
        await handle.run_argv(
            ("sh", "-lc", "ambiguous"),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )

    assert captured.value.code == "runtime_launch_failed"
    first_cleanup = captured.value.details["cleanup"]
    assert [
        (step.resource, step.state.value, step.detail)
        for step in first_cleanup
    ] == [
        ("runtime_stop", "failed", "runtime_termination_failed"),
        ("runtime_remove", "failed", "runtime_termination_failed"),
        ("runtime_absence", "failed", "runtime_termination_failed"),
    ]
    assert [
        call[0][1] for call in executor.calls[calls_before_fault:]
    ] == ["exec", "inspect", "stop", "rm", "inspect"]
    assert executor.results == []
    calls_after_failure = list(executor.calls)
    with pytest.raises(DockerAdapterError) as fenced:
        await handle.run_argv(
            ("printf", "must-not-run"),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )
    assert fenced.value.code == "lease_not_active"
    assert executor.calls == calls_after_failure
    executor.results.extend(
        [
            _result(stdout=_identity_inspect(plan)),
            _result(),
            _result(),
            _not_found(CONTAINER_ID),
        ]
    )

    first_retry, concurrent_retry = await asyncio.gather(
        handle.terminate(),
        handle.terminate(),
    )

    assert first_retry == concurrent_retry
    assert [
        (step.resource, step.state.value, step.detail)
        for step in first_retry
    ] == [
        ("runtime_stop", "released", ""),
        ("runtime_remove", "released", ""),
        ("runtime_absence", "released", ""),
    ]
    assert [
        call[0][1] for call in executor.calls[len(calls_after_failure):]
    ] == ["inspect", "stop", "rm", "inspect"]
    assert executor.results == []
    calls_after_release = list(executor.calls)
    assert await handle.terminate() == first_retry
    with pytest.raises(DockerAdapterError) as still_fenced:
        await handle.run_argv(
            ("printf", "still-must-not-run"),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )
    assert still_fenced.value.code == "lease_not_active"
    assert executor.calls == calls_after_release


async def test_cancellation_after_exec_dispatch_finishes_shielded_cleanup_before_return(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(
        tmp_path,
        executor_type=CancellableExecDockerExecutor,
    )
    assert isinstance(executor, CancellableExecDockerExecutor)
    executor.results.extend(
        [
            _result(stdout=_identity_inspect(plan)),
            _result(),
            _result(),
            _not_found(CONTAINER_ID),
        ]
    )
    calls_before_exec = len(executor.calls)
    action = asyncio.create_task(
        handle.run_argv(
            ("sh", "-lc", "cancel-after-dispatch"),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )
    )
    await asyncio.wait_for(executor.exec_started.wait(), 1)

    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(action, 1)

    assert [
        call[0][1] for call in executor.calls[calls_before_exec:]
    ] == ["exec", "inspect", "stop", "rm", "inspect"]
    assert executor.results == []
    calls_after_cleanup = list(executor.calls)
    cached = await handle.terminate()
    assert [
        (step.resource, step.state.value, step.detail)
        for step in cached
    ] == [
        ("runtime_stop", "released", ""),
        ("runtime_remove", "released", ""),
        ("runtime_absence", "released", ""),
    ]
    with pytest.raises(DockerAdapterError) as fenced:
        await handle.run_argv(
            ("printf", "must-not-run"),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )
    assert fenced.value.code == "lease_not_active"
    assert executor.calls == calls_after_cleanup


async def test_definite_exec_nonzero_does_not_fence_or_trigger_cleanup(
    tmp_path: Path,
) -> None:
    plan, executor, handle = await _launch_docker_handle(tmp_path)
    executor.results.extend(
        [
            _result(returncode=7, stderr=b"command rejected"),
            _result(stdout=b"still-active"),
            _result(stdout=_identity_inspect(plan)),
            _result(),
            _result(),
            _not_found(CONTAINER_ID),
        ]
    )
    calls_before_fault = len(executor.calls)

    failed = await handle.run_argv(
        ("false",),
        timeout_ms=plan.limits.action_timeout_ms,
        output_limit=plan.limits.observation_bytes,
    )

    assert failed == {
        "returncode": 7,
        "stdout": "",
        "stderr": "command rejected",
    }
    assert [call[0][1] for call in executor.calls[calls_before_fault:]] == ["exec"]
    result = await handle.run_argv(
        ("printf", "still-active"),
        timeout_ms=plan.limits.action_timeout_ms,
        output_limit=plan.limits.observation_bytes,
    )
    assert result == {
        "returncode": 0,
        "stdout": "still-active",
        "stderr": "",
    }
    cleanup = await handle.terminate()
    assert [step.state.value for step in cleanup] == [
        "released",
        "released",
        "released",
    ]
    assert [call[0][1] for call in executor.calls[calls_before_fault:]] == [
        "exec",
        "exec",
        "inspect",
        "stop",
        "rm",
        "inspect",
    ]
    assert executor.results == []


@pytest.mark.parametrize(
    "inspect_result",
    [
        _result(timed_out=True),
        _result(output_limited=True),
        _result(returncode=1, stderr=b"Cannot connect to the Docker daemon"),
        _result(returncode=1, stderr=b"permission denied"),
        _result(returncode=1, stderr=b"not found"),
    ],
)
async def test_unmeasurable_cleanup_inspect_quarantines_without_stop_or_remove(
    tmp_path: Path,
    inspect_result: DockerCommandResult,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor([inspect_result])
    adapter = _mechanics_adapter(plan, executor, environment=())

    cleanup = await adapter.cleanup(
        plan,
        CONTAINER_ID,
        expected_id=CONTAINER_ID,
        expected_name="bb-primary-workspace-1",
        labels=_binding_labels(plan),
    )

    assert cleanup == (
        ("runtime_identity", "quarantined", "stale_identity_uncertain"),
    )
    assert [call[0][1] for call in executor.calls] == ["inspect"]
    assert executor.results == []


async def test_exact_not_found_cleanup_is_already_released_without_destructive_calls(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor([_not_found(CONTAINER_ID)])
    adapter = _mechanics_adapter(plan, executor, environment=())

    cleanup = await adapter.cleanup(
        plan,
        CONTAINER_ID,
        expected_id=CONTAINER_ID,
        expected_name="bb-primary-workspace-1",
        labels=_binding_labels(plan),
    )

    assert cleanup == (
        ("runtime_identity", "already_released", ""),
        ("runtime_absence", "already_released", ""),
    )
    assert [call[0][1] for call in executor.calls] == ["inspect"]
    assert executor.results == []


async def test_concurrent_removal_normalizes_cleanup_and_retries_idempotently(
    tmp_path: Path,
) -> None:
    plan, _, _, _ = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor(
        [
            _result(stdout=_identity_inspect(plan)),
            _not_found(CONTAINER_ID),
            _not_found(CONTAINER_ID),
            _not_found(CONTAINER_ID),
            _not_found(CONTAINER_ID),
        ]
    )
    adapter = _mechanics_adapter(plan, executor, environment=())
    arguments = {
        "expected_id": CONTAINER_ID,
        "expected_name": "bb-primary-workspace-1",
        "labels": _binding_labels(plan),
    }

    first = await adapter.cleanup(plan, CONTAINER_ID, **arguments)
    second = await adapter.cleanup(plan, CONTAINER_ID, **arguments)

    assert first == (
        ("runtime_stop", "already_released", ""),
        ("runtime_remove", "already_released", ""),
        ("runtime_absence", "released", ""),
    )
    assert second == (
        ("runtime_identity", "already_released", ""),
        ("runtime_absence", "already_released", ""),
    )
    assert [call[0][1] for call in executor.calls] == [
        "inspect",
        "stop",
        "rm",
        "inspect",
        "inspect",
    ]
    assert executor.results == []


@pytest.mark.parametrize(
    ("create_result", "expected_code"),
    [
        (_result(timed_out=True), "runtime_launch_failed"),
        (_result(output_limited=True), "output_limit_exceeded"),
        (_result(returncode=7, stderr=b"daemon disconnected"), "runtime_launch_failed"),
    ],
)
async def test_ambiguous_create_cleans_only_the_exact_labeled_container_identity(
    tmp_path: Path,
    create_result: DockerCommandResult,
    expected_code: str,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor(
        [
            create_result,
            _result(stdout=_identity_inspect(plan)),
            _result(),
            _result(),
            _not_found(CONTAINER_ID),
        ]
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with profile.open("rb") as profile_stream:
        profile_metadata = os.fstat(profile_stream.fileno())
        with pytest.raises(DockerAdapterError) as captured:
            await adapter.create_start(
                plan,
                lease_id="lease-1",
                workspace_id="workspace-1",
                epoch=1,
                role="primary",
                skeleton_path=skeleton,
                mounts=mounts,
                security_profile_path=profile,
                security_profile_descriptor=profile_stream.fileno(),
                security_profile_metadata=profile_metadata,
            )

    assert captured.value.code == expected_code
    assert [call[0][1] for call in executor.calls] == [
        "create",
        "inspect",
        "stop",
        "rm",
        "inspect",
    ]
    create_argv = executor.calls[0][0]
    assert create_argv[create_argv.index("--name") + 1] == "bb-primary-workspace-1"
    assert tuple(
        create_argv[index + 1]
        for index, value in enumerate(create_argv)
        if value == "--label"
    ) == (
        "bb.lease_id=lease-1",
        f"bb.plan_digest={plan.effective_plan_digest}",
        "bb.epoch=1",
        "bb.workspace_id=workspace-1",
        "bb.role=primary",
    )
    assert executor.calls[1][0] == (
        plan.runtime.executable_path,
        "inspect",
        "bb-primary-workspace-1",
    )
    assert executor.calls[2][0][-1] == CONTAINER_ID
    assert executor.calls[3][0][-1] == CONTAINER_ID
    assert executor.calls[4][0][-1] == CONTAINER_ID
    assert executor.results == []


async def test_ambiguous_create_quarantines_a_name_bound_to_different_labels(
    tmp_path: Path,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    wrong_labels = {
        "bb.lease_id": "other-lease",
        "bb.plan_digest": plan.effective_plan_digest,
        "bb.epoch": "1",
        "bb.workspace_id": "workspace-1",
        "bb.role": "primary",
    }
    executor = ScriptedDockerExecutor(
        [
            _result(timed_out=True),
            _result(stdout=_identity_inspect(plan, labels=wrong_labels)),
        ]
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with profile.open("rb") as profile_stream:
        profile_metadata = os.fstat(profile_stream.fileno())
        with pytest.raises(DockerAdapterError) as captured:
            await adapter.create_start(
                plan,
                lease_id="lease-1",
                workspace_id="workspace-1",
                epoch=1,
                role="primary",
                skeleton_path=skeleton,
                mounts=mounts,
                security_profile_path=profile,
                security_profile_descriptor=profile_stream.fileno(),
                security_profile_metadata=profile_metadata,
            )

    assert captured.value.code == "runtime_launch_failed"
    assert captured.value.details["cleanup"] == (
        ("runtime_identity", "quarantined", "stale_identity_uncertain"),
    )
    assert [call[0][1] for call in executor.calls] == ["create", "inspect"]
    assert executor.results == []


async def test_start_failure_removes_created_container_and_verifies_absence(
    tmp_path: Path,
) -> None:
    plan, skeleton, profile, mounts = _docker_plan(tmp_path)
    executor = ScriptedDockerExecutor(
        [
            _result(stdout=CONTAINER_ID.encode("ascii")),
            _result(stdout=_identity_inspect(plan)),
            _result(returncode=9, stderr=b"start denied"),
            _result(stdout=_identity_inspect(plan)),
            _result(),
            _result(),
            _not_found(CONTAINER_ID),
        ]
    )
    adapter = _mechanics_adapter(plan, executor, environment=())

    with profile.open("rb") as profile_stream:
        profile_metadata = os.fstat(profile_stream.fileno())
        with pytest.raises(DockerAdapterError) as captured:
            await adapter.create_start(
                plan,
                lease_id="lease-1",
                workspace_id="workspace-1",
                epoch=1,
                role="primary",
                skeleton_path=skeleton,
                mounts=mounts,
                security_profile_path=profile,
                security_profile_descriptor=profile_stream.fileno(),
                security_profile_metadata=profile_metadata,
            )

    assert captured.value.code == "runtime_launch_failed"
    assert captured.value.details["cleanup"] == (
        ("runtime_stop", "released", ""),
        ("runtime_remove", "released", ""),
        ("runtime_absence", "released", ""),
    )
    assert [call[0][1] for call in executor.calls] == [
        "create",
        "inspect",
        "start",
        "inspect",
        "stop",
        "rm",
        "inspect",
    ]
    assert executor.results == []


async def test_legacy_reconcile_without_pinned_binding_quarantines_with_zero_executor_calls(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    executor = ScriptedDockerExecutor()
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor),
        measurement_provider=RecordingMeasurementProvider({}),
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )

    cleanup = await backend.reconcile(
        {
            "runtime_resource_id": CONTAINER_ID,
            "runtime_executable_path": plan.runtime.executable_path,
            "runtime_binary_digest": plan.runtime.measured_binary_digest,
        }
    )

    assert [(item.resource, item.state.value, item.detail) for item in cleanup] == [
        (
            "runtime",
            "quarantined",
            "runtime_identity=quarantined:stale_identity_uncertain",
        )
    ]
    assert executor.calls == []
    assert executor.invocations == []


async def test_legacy_reconcile_never_consumes_a_scripted_matching_identity(
    tmp_path: Path,
) -> None:
    plan, skeleton, _, _ = _docker_plan(tmp_path)
    security_root = tmp_path / "security"
    security_root.mkdir()
    executor = ScriptedDockerExecutor([_result(stdout=_identity_inspect(plan))])
    backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor),
        measurement_provider=RecordingMeasurementProvider({}),
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )

    cleanup = await backend.reconcile({"runtime_resource_id": CONTAINER_ID})

    assert cleanup[0].state.value == "quarantined"
    assert cleanup[0].detail == "runtime_identity=quarantined:stale_identity_uncertain"
    assert executor.calls == []
    assert executor.invocations == []
    assert len(executor.results) == 1


async def test_manager_retries_backend_launch_quarantine_before_workspace_release(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        with_writable_mount=True,
    )
    source_digest = digest("workspace-source")
    cache_root, workspace_root = make_store_roots(tmp_path)
    store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        source_reader=MemorySourceReader(
            {source_digest: {"seed.txt": b"seed"}}
        ),
        clock=FrozenClock(),
        lease_ttl=timedelta(minutes=5),
        storage_backend=QuotaStorageBackend(),
        random_bytes=DeterministicRandom(60_000),
    )
    lease_root = tmp_path / "leases"
    lease_root.mkdir(mode=0o700)
    backend = PendingLaunchBackend()
    manager = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=store,
        lease_root=lease_root,
        process_backend=None,
        docker_backend=backend,
        random_bytes=DeterministicRandom(60_500),
    )

    with pytest.raises(SandboxFault) as captured:
        await manager.open(fixture.request)

    receipt = captured.value.cleanup_receipt
    assert receipt.state is CleanupState.QUARANTINED
    assert len(manager._pending_launch_cleanups) == 1
    retained = next(iter(manager._pending_launch_cleanups.values()))
    assert retained.workspace.exists()
    assert (lease_root / f"{receipt.lease_id}.json").exists()

    first_close = await manager.close()
    assert first_close[0].state is CleanupState.QUARANTINED
    assert backend.close_attempts == 1
    assert retained.workspace.exists()
    assert (lease_root / f"{receipt.lease_id}.json").exists()

    second_close = await manager.close()
    assert second_close[0].state is CleanupState.RELEASED
    assert backend.close_attempts == 2
    assert not retained.workspace.exists()
    assert not (lease_root / f"{receipt.lease_id}.json").exists()
    assert manager._pending_launch_cleanups == {}


async def test_manager_retains_returned_nonterminal_handle_after_admission_failure(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        with_writable_mount=True,
    )
    source_digest = digest("workspace-source")
    cache_root, workspace_root = make_store_roots(tmp_path)
    store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        source_reader=MemorySourceReader(
            {source_digest: {"seed.txt": b"seed"}}
        ),
        clock=FrozenClock(),
        lease_ttl=timedelta(minutes=5),
        storage_backend=QuotaStorageBackend(),
        random_bytes=DeterministicRandom(60_600),
    )
    lease_root = tmp_path / "leases"
    lease_root.mkdir(mode=0o700)
    backend = ReturnedPendingLaunchBackend()
    manager = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=store,
        lease_root=lease_root,
        process_backend=None,
        docker_backend=backend,
        random_bytes=DeterministicRandom(60_700),
    )

    with pytest.raises(SandboxFault) as captured:
        await manager.open(fixture.request)

    receipt = captured.value.cleanup_receipt
    retained = manager._pending_launch_cleanups[receipt.lease_id]
    assert retained.runtime is backend.handle
    assert retained.workspace.exists()
    assert backend.handle is not None
    assert backend.handle.terminate_calls == 1
    assert (lease_root / f"{receipt.lease_id}.json").exists()

    close_receipts = await manager.close()

    assert close_receipts[0].state is CleanupState.RELEASED
    assert backend.handle.terminate_calls == 2
    assert backend.handle.workspace_fd == -1
    assert not retained.workspace.exists()
    assert not (lease_root / f"{receipt.lease_id}.json").exists()
    assert manager._pending_launch_cleanups == {}


@pytest.mark.parametrize("identity", ["matching", "mismatched"])
async def test_manager_reconcile_uses_docker_runtime_aggregate_to_gate_durable_cleanup(
    tmp_path: Path,
    identity: str,
) -> None:
    fixture = make_runtime_fixture(
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        with_writable_mount=True,
    )
    plan = build_sandbox_execution_plan(
        fixture.request,
        fixture.registries,
        fixture.authorities,
    )
    clock = FrozenClock()
    source_digest = digest("workspace-source")
    cache_root, workspace_root = make_store_roots(tmp_path)
    store = FilesystemMaterializationStore(
        cache_root=cache_root,
        workspace_root=workspace_root,
        source_reader=MemorySourceReader(
            {source_digest: {"seed.txt": b"seed"}}
        ),
        clock=clock,
        lease_ttl=timedelta(minutes=5),
        storage_backend=QuotaStorageBackend(),
        random_bytes=DeterministicRandom(61_000),
    )
    lease_root = tmp_path / "leases"
    lease_root.mkdir(mode=0o700)
    original = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=store,
        lease_root=lease_root,
        process_backend=None,
        docker_backend=LeaseOnlyBackend(),
        random_bytes=DeterministicRandom(62_000),
    )
    lease = await original.open(fixture.request)
    record_path = lease_root / f"{lease.lease_id}.json"
    record = dict(original._read_lease_record(record_path))
    executable = tmp_path / "reconcile-docker-cli"
    executable.write_bytes(b"reconcile pinned Docker CLI")
    record["runtime_executable_path"] = str(executable)
    record["runtime_binary_digest"] = observe_binary_digest(executable)
    original._write_lease_record(lease.lease_id, record)
    workspace = Path(record["workspace_path"])
    labels = _binding_labels(
        plan,
        lease_id=lease.lease_id,
        workspace_id=record["workspace_id"],
        epoch=record["epoch"],
        role=record["role"],
    )
    if identity == "mismatched":
        labels["bb.epoch"] = str(record["epoch"] + 1)
    executor = ScriptedDockerExecutor(
        (
            [
                _result(
                    stdout=_identity_inspect(
                        plan,
                        lease_id=lease.lease_id,
                        workspace_id=record["workspace_id"],
                        epoch=record["epoch"],
                        role=record["role"],
                        labels=labels,
                    )
                ),
                _result(),
                _result(),
                _not_found(CONTAINER_ID),
            ]
            if identity == "matching"
            else [
                _result(
                    stdout=_identity_inspect(
                        plan,
                        lease_id=lease.lease_id,
                        workspace_id=record["workspace_id"],
                        epoch=record["epoch"],
                        role=record["role"],
                        labels=labels,
                    )
                )
            ]
        )
    )
    skeleton = tmp_path / "reconcile-skeleton"
    security_root = tmp_path / "reconcile-security"
    skeleton.mkdir()
    security_root.mkdir()
    docker_backend = DockerSandboxBackend(
        adapter=_mechanics_adapter(plan, executor, environment=()),
        measurement_provider=RecordingMeasurementProvider({}),
        skeleton_path=skeleton,
        security_profile_root=security_root,
    )
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=store,
        lease_root=lease_root,
        process_backend=None,
        docker_backend=docker_backend,
        random_bytes=DeterministicRandom(63_000),
    )
    clock.advance(minutes=5)

    receipts = await recovery.reconcile_stale()

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.lease_id == lease.lease_id
    assert receipt.state.value == "quarantined"
    assert [(step.resource, step.state.value) for step in receipt.steps] == [
        ("child_verifier", "already_released"),
        ("runtime", "quarantined"),
        ("workspace", "quarantined"),
        ("cache_holder", "quarantined"),
        ("lease_record", "quarantined"),
    ]
    assert receipt.steps[1].detail == (
        "runtime_identity=quarantined:stale_identity_uncertain"
    )
    assert workspace.exists()
    assert record_path.exists()
    assert executor.calls == []
    assert executor.invocations == []


@pytest.mark.local_docker
def test_local_container_capability_probe_reports_compatible_host_only() -> None:
    system = platform.system()
    if system != "Linux":
        pytest.skip(
            "UNAVAILABLE: hardened local-container proof requires Linux; "
            f"observed {system}"
        )
    executable = shutil.which("docker")
    if executable is None:
        pytest.skip("UNAVAILABLE: Docker CLI is not installed")

    def probe(*arguments: str) -> Mapping[str, Any]:
        try:
            completed = subprocess.run(
                (executable, *arguments),
                check=False,
                capture_output=True,
                env={},
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"UNAVAILABLE: Docker capability probe failed: {type(exc).__name__}")
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            pytest.skip(
                "UNAVAILABLE: Docker engine is not usable"
                + (f": {detail}" if detail else "")
            )
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pytest.skip("UNAVAILABLE: Docker engine returned malformed capability JSON")
        if type(payload) is not dict:
            pytest.skip("UNAVAILABLE: Docker capability JSON is not an object")
        return payload

    version = probe("version", "--format", "{{json .}}")
    info = probe("info", "--format", "{{json .}}")
    runtimes_value = info.get("Runtimes")
    if type(runtimes_value) is dict:
        runtimes = frozenset(runtimes_value)
    elif type(runtimes_value) is list and all(
        type(value) is str for value in runtimes_value
    ):
        runtimes = frozenset(runtimes_value)
    else:
        pytest.skip("UNAVAILABLE: Docker runtime registration is unmeasurable")
    security_options = info.get("SecurityOptions")
    if type(security_options) is not list or not all(
        type(value) is str for value in security_options
    ):
        pytest.skip("UNAVAILABLE: Docker security options are unmeasurable")
    observed_security = "\n".join(security_options).lower()
    unavailable: list[str] = []
    if type(version.get("Server")) is not dict:
        unavailable.append("server-version")
    if info.get("OSType") != "linux":
        unavailable.append("linux-engine")
    if "runc" not in runtimes:
        unavailable.append("runc-registration")
    if "seccomp" not in observed_security:
        unavailable.append("seccomp")
    if not any(name in observed_security for name in ("apparmor", "selinux")):
        unavailable.append("lsm")
    if not info.get("CgroupDriver") or not info.get("CgroupVersion"):
        unavailable.append("cgroup-measurement")
    if unavailable:
        pytest.skip(
            "UNAVAILABLE: hardened local-container policy capability missing: "
            + ", ".join(unavailable)
        )

    assert info["OSType"] == "linux"
    assert "runc" in runtimes
    assert "seccomp" in observed_security
