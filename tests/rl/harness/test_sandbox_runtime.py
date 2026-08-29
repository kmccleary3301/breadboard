from __future__ import annotations

import asyncio
import os
import stat
import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    DirectoryStorageBackend,
    FilesystemMaterializationStore,
    IsolationDisposition,
    WorkspaceLeaseState,
    WorkspaceOpenRequest,
)
from breadboard.rl.harness.runners.base import RunnerToolBinding
from breadboard.rl.harness.sandbox import (
    InstalledSandboxAuthoritySet,
    SandboxAttestationError,
    SandboxFault,
    SandboxLaunchError,
    SandboxMeasurement,
    SandboxPlanError,
    SandboxRuntimeManager,
    SandboxSecurityPolicy,
    TrustedProcessBackend,
    WorkspaceStateError,
    build_sandbox_execution_plan,
)
from tests.rl.harness.wp7_fixtures import (
    DeterministicRandom,
    FrozenClock,
    MemorySourceReader,
    RuntimeFixture,
    _registry_snapshot,
    digest,
    make_effective_plan,
    make_runtime_fixture,
    make_store_roots,
    plan_tool_bindings,
    replace_plan_capabilities,
)


class RecordingHandle:
    def __init__(self) -> None:
        self.runtime_id = "runtime-resource-test"
        self.actions: list[tuple[str, int, int]] = []
        self.argv_actions: list[tuple[tuple[str, ...], int, int]] = []
        self.terminate_calls = 0
        self.termination_states: list[CleanupState] = [CleanupState.RELEASED]
        self.termination_receipts: tuple[CleanupStepReceipt, ...] | None = None
        self.result: Mapping[str, Any] = {
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
        }
        self.run_entered: asyncio.Event | None = None
        self.release_run: asyncio.Event | None = None
        self.terminate_entered: asyncio.Event | None = None
        self.release_terminate: asyncio.Event | None = None

    async def run_shell(
        self, command: str, *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        self.actions.append((command, timeout_ms, output_limit))
        if self.run_entered is not None:
            self.run_entered.set()
        if self.release_run is not None:
            await self.release_run.wait()
        return self.result

    async def run_argv(
        self, argv: tuple[str, ...], *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        self.argv_actions.append((tuple(argv), timeout_ms, output_limit))
        return self.result

    async def terminate(self) -> tuple[Any, ...]:
        self.terminate_calls += 1
        if self.terminate_entered is not None:
            self.terminate_entered.set()
        if self.release_terminate is not None:
            await self.release_terminate.wait()
        if self.termination_receipts is not None:
            return self.termination_receipts
        index = min(self.terminate_calls - 1, len(self.termination_states) - 1)
        return (CleanupStepReceipt("runtime", self.termination_states[index]),)


class RecordingBackend:
    def __init__(self) -> None:
        self.launches: list[tuple[Any, Path, Any]] = []
        self.handles: list[RecordingHandle] = []
        self.failure: BaseException | None = None
        self.handle_termination_receipts: tuple[CleanupStepReceipt, ...] | None = None
        self.measurement_mismatch: tuple[str, ...] = ()
        self.launch_entered: asyncio.Event | None = None
        self.release_launch: asyncio.Event | None = None

    async def launch(
        self,
        plan: Any,
        workspace: Path,
        *,
        context: Any,
    ) -> tuple[RecordingHandle, SandboxMeasurement]:
        self.launches.append((plan, workspace, context))
        lease_id = context.lease_id
        workspace_id = context.workspace_id
        if self.launch_entered is not None:
            self.launch_entered.set()
        if self.release_launch is not None:
            await self.release_launch.wait()
        if self.failure is not None:
            raise self.failure
        handle = RecordingHandle()
        handle.termination_receipts = self.handle_termination_receipts
        self.handles.append(handle)
        requested = {
            "runtime": plan.runtime.runtime_id,
            "image": plan.image.image_digest,
            "network": plan.network_policy.mode,
            "storage_bytes": plan.resources.storage_bytes,
        }
        measurement = SandboxMeasurement(
            effective_plan_digest=plan.effective_plan_digest,
            lease_id=lease_id,
            workspace_id=workspace_id,
            runtime_id=plan.runtime.runtime_id,
            runtime_class=plan.runtime.runtime_class.value,
            driver_binary_digest=plan.runtime.measured_binary_digest,
            image_digest=plan.image.image_digest,
            requested=requested,
            effective=requested,
            measured=requested,
            runtime_resource_id=handle.runtime_id,
            mismatch=self.measurement_mismatch,
            isolation_disposition=plan.isolation_disposition,
            isolated=plan.isolation_disposition is IsolationDisposition.ISOLATED,
            reward_eligible=plan.isolation_disposition is IsolationDisposition.ISOLATED,
        )
        return handle, measurement


class RuntimeHarness:
    def __init__(
        self,
        tmp_path: Path,
        fixture: RuntimeFixture,
        *,
        backend: RecordingBackend | None = None,
    ) -> None:
        self.fixture = fixture
        self.clock = FrozenClock()
        self.source_digest = digest("workspace-source")
        self.reader = MemorySourceReader(
            {self.source_digest: {"seed.txt": b"seed"}}
            if fixture.plan.sandbox.mounts
            else {}
        )
        cache_root, workspace_root = make_store_roots(tmp_path)
        self.cache_root = cache_root
        self.workspace_root = workspace_root
        self.lease_root = tmp_path / "leases"
        self.lease_root.mkdir(mode=0o700)
        self.backend = backend or RecordingBackend()
        self.store = FilesystemMaterializationStore(
            cache_root=cache_root,
            workspace_root=workspace_root,
            source_reader=self.reader,
            clock=self.clock,
            lease_ttl=timedelta(minutes=5),
            storage_backend=DirectoryStorageBackend(),
            random_bytes=DeterministicRandom(1_000),
        )
        self.manager = SandboxRuntimeManager(
            registries=fixture.registries,
            installed_authorities=fixture.authorities,
            materialization_store=self.store,
            lease_root=self.lease_root,
            process_backend=self.backend,
            docker_backend=self.backend,
            random_bytes=DeterministicRandom(2_000),
        )


def _primary_runtime_index(fixture: RuntimeFixture) -> int:
    return next(
        index
        for index, runtime in enumerate(fixture.authorities.runtimes)
        if runtime.runtime_id == fixture.plan.sandbox.runtime_id
    )


def test_runtime_fixture_installs_exact_private_canonical_shell(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(runtime_install_root=tmp_path)
    runtimes = fixture.authorities.runtimes
    primary = runtimes[_primary_runtime_index(fixture)]
    installed = Path(primary.executable_path)

    assert installed.is_absolute()
    assert installed == installed.resolve(strict=True)
    assert installed.is_file()
    assert not installed.is_symlink()
    assert primary.measured_binary_digest == digest(installed.read_bytes())
    assert primary.measured_binary_digest == fixture.plan.sandbox.runtime_binary_digest
    assert all(
        runtime.executable_path == primary.executable_path
        for runtime in runtimes
    )


def test_exact_plan_projection_derives_runtime_materialization_and_runner_authority() -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)

    plan = build_sandbox_execution_plan(
        fixture.request, fixture.registries, fixture.authorities
    )

    assert plan.episode_id == fixture.request.episode_id
    assert plan.effective_plan_digest == fixture.plan.canonical_digest()
    assert plan.runtime.executable_path == fixture.authorities.runtimes[
        _primary_runtime_index(fixture)
    ].executable_path
    assert plan.runtime.measured_binary_digest == fixture.plan.sandbox.runtime_binary_digest
    assert plan.image.image_digest == fixture.plan.sandbox.image_digest
    assert plan.security_policy.policy_digest == fixture.plan.sandbox.security_policy_digest
    assert plan.network_policy.policy_digest == fixture.plan.sandbox.network_policy_digest
    assert plan.network_policy.mode == "none"
    assert plan.materialization_plan.entries[0].projection() == {
        "source_digest": digest("workspace-source"),
        "target_logical_path": "work",
        "access": "rw",
        "max_bytes": 4_096,
        "role": "mount",
    }
    assert plan.tool_bindings == plan_tool_bindings(fixture.plan)
    assert plan.isolation_disposition is IsolationDisposition.TRUSTED_PROCESS


@pytest.mark.parametrize("runtime_id", ["none", "light", "dev", "unknown"])
def test_legacy_or_unknown_runtime_names_never_fall_back_to_process(
    runtime_id: str,
) -> None:
    fixture = make_runtime_fixture()
    foreign_plan = make_effective_plan(runtime_id=runtime_id)
    request = WorkspaceOpenRequest("episode-foreign", foreign_plan)

    with pytest.raises(SandboxPlanError) as captured:
        build_sandbox_execution_plan(
            request, fixture.registries, fixture.authorities
        )

    assert captured.value.code == "runtime_authority_missing"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing-runtime", "runtime_authority_missing"),
        ("wrong-driver", "runtime_identity_mismatch"),
        ("wrong-binary", "runtime_identity_mismatch"),
        ("missing-image", "runtime_authority_missing"),
        ("missing-security", "runtime_authority_missing"),
        ("missing-network", "runtime_authority_missing"),
        ("missing-verifier", "verifier_authority_mismatch"),
    ],
)
def test_installed_authority_drift_is_typed_before_any_runtime_effect(
    mutation: str, expected_code: str
) -> None:
    fixture = make_runtime_fixture()
    authorities = fixture.authorities
    runtimes = list(authorities.runtimes)
    primary = _primary_runtime_index(fixture)
    images = list(authorities.images)
    policies = list(authorities.security_policies)
    networks = list(authorities.network_policies)
    verifiers = list(authorities.verifiers)
    if mutation == "missing-runtime":
        runtimes.pop(primary)
    elif mutation == "wrong-driver":
        runtimes[primary] = replace(
            runtimes[primary], driver_implementation_digest=digest("drift-driver")
        )
    elif mutation == "wrong-binary":
        runtimes[primary] = replace(
            runtimes[primary], measured_binary_digest=digest("drift-binary")
        )
    elif mutation == "missing-image":
        images = [
            image
            for image in images
            if image.image_digest != fixture.plan.sandbox.image_digest
        ]
    elif mutation == "missing-security":
        policies = [
            policy
            for policy in policies
            if policy.policy_digest != fixture.plan.sandbox.security_policy_digest
        ]
    elif mutation == "missing-network":
        networks = [
            policy
            for policy in networks
            if policy.policy_digest != fixture.plan.sandbox.network_policy_digest
        ]
    elif mutation == "missing-verifier":
        verifiers.clear()
    candidate = InstalledSandboxAuthoritySet(
        runtimes=tuple(runtimes),
        images=tuple(images),
        security_policies=tuple(policies),
        network_policies=tuple(networks),
        verifiers=tuple(verifiers),
    )

    with pytest.raises(SandboxPlanError) as captured:
        build_sandbox_execution_plan(
            fixture.request, fixture.registries, candidate
        )
    assert captured.value.code == expected_code

@pytest.mark.parametrize(
    "mutation",
    [
        "missing-path",
        "missing-digest",
        "relative-path",
        "path-type",
        "digest-type",
        "driver-digest-short",
        "driver-digest-uppercase",
        "executable-nul",
        "executable-dotdot",
        "oci-nul",
        "oci-dotdot",
        "gvisor-path-mismatch",
        "gvisor-digest-mismatch",
        "trusted-foreign-authority",
    ],
)
def test_oci_runtime_binary_authority_rejects_inexact_values_before_effects(
    mutation: str,
) -> None:
    fixture = make_runtime_fixture()
    runtime = fixture.authorities.runtimes[_primary_runtime_index(fixture)]
    admitted_digest = fixture.plan.sandbox.runtime_binary_digest
    updates: dict[str, Any] = {
        "runtime_class": c.RuntimeClass.HARDENED_DOCKER,
        "oci_runtime_name": "runc",
        "oci_runtime_binary_path": runtime.executable_path,
        "oci_runtime_binary_digest": admitted_digest,
    }
    if mutation == "missing-path":
        updates["oci_runtime_binary_path"] = None
    elif mutation == "missing-digest":
        updates["oci_runtime_binary_digest"] = None
    elif mutation == "relative-path":
        updates["oci_runtime_binary_path"] = "bin/runc"
    elif mutation == "path-type":
        updates["oci_runtime_binary_path"] = Path("/bin/sh")
    elif mutation == "digest-type":
        updates["oci_runtime_binary_digest"] = admitted_digest.encode("ascii")
    elif mutation == "driver-digest-short":
        updates["measured_binary_digest"] = "sha256:1234"
    elif mutation == "driver-digest-uppercase":
        updates["measured_binary_digest"] = admitted_digest.upper()
    elif mutation == "executable-nul":
        updates["executable_path"] = runtime.executable_path + "\x00foreign"
    elif mutation == "executable-dotdot":
        updates["executable_path"] = "/opt/runtime/../foreign"
    elif mutation == "oci-nul":
        updates["oci_runtime_binary_path"] = runtime.executable_path + "\x00foreign"
    elif mutation == "oci-dotdot":
        updates["oci_runtime_binary_path"] = "/opt/runtime/../foreign"
    elif mutation == "gvisor-path-mismatch":
        updates.update(
            {
                "runtime_class": c.RuntimeClass.HARDENED_GVISOR,
                "oci_runtime_name": "runsc",
                "runsc_binary_path": "/usr/bin/false",
                "runsc_binary_digest": admitted_digest,
            }
        )
    elif mutation == "gvisor-digest-mismatch":
        updates.update(
            {
                "runtime_class": c.RuntimeClass.HARDENED_GVISOR,
                "oci_runtime_name": "runsc",
                "runsc_binary_path": runtime.executable_path,
                "runsc_binary_digest": digest("foreign-runsc"),
            }
        )
    else:
        updates = {
            "oci_runtime_binary_path": runtime.executable_path,
            "oci_runtime_binary_digest": admitted_digest,
        }

    with pytest.raises(ValueError):
        replace(runtime, **updates)




async def test_unmapped_task_input_denies_before_source_cache_workspace_or_runtime(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture()
    task_payload = fixture.plan.task.model_dump(mode="python")
    task_payload["repository_snapshot_digest"] = digest("unmapped-repository")
    task = c.TaskGrant.model_validate(task_payload)
    denied_plan = replace_plan_capabilities(fixture.plan, task=task)
    request = WorkspaceOpenRequest("episode-unmapped", denied_plan)
    harness = RuntimeHarness(tmp_path, fixture)

    with pytest.raises(SandboxPlanError) as captured:
        await harness.manager.open(request)

    assert captured.value.code == "task_input_unmapped"
    assert harness.reader.loads == []
    assert harness.backend.launches == []
    assert list((harness.cache_root / "objects").iterdir()) == []
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_runner_workspace_enforces_exact_bindings_paths_and_action_bounds(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    port = lease.runner_workspace
    handle = harness.backend.handles[0]

    assert port.tool_bindings == plan_tool_bindings(fixture.plan)
    duplicated_binding = port.tool_bindings[0]
    duplicate_lease = SimpleNamespace(
        plan=SimpleNamespace(
            effective_plan_digest=lease.plan.effective_plan_digest,
            tool_bindings=(duplicated_binding, duplicated_binding),
        )
    )
    with pytest.raises(SandboxPlanError) as duplicate:
        sandbox_module.LeaseBackedRunnerWorkspace(
            duplicate_lease,
            lease.plan.effective_plan_digest,
            (duplicated_binding, duplicated_binding),
        )
    assert duplicate.value.code == "tool_binding_projection_mismatch"
    assert await port.write_text("work/candidate.txt", "candidate") == {
        "path": "work/candidate.txt",
        "bytes": 9,
    }
    assert await port.read_text("work/candidate.txt") == {
        "path": "work/candidate.txt",
        "content": "candidate",
        "offset": 0,
        "bytes": 9,
    }
    listing = await port.list_files("work", depth=2)
    assert listing["files"] == ["work/candidate.txt", "work/seed.txt"]
    result = await port.run_shell("printf ok", timeout=1)
    assert result["stdout"] == "ok"
    assert handle.actions == [
        ("printf ok", 1_000, fixture.plan.effective_capabilities.limits.observation_bytes)
    ]

    for path in ("../escape", "/absolute", "work/../../escape", ""):
        with pytest.raises(WorkspaceStateError) as captured:
            await port.write_text(path, "forbidden")
        assert captured.value.code == "workspace_escape"
    with pytest.raises(WorkspaceStateError) as captured:
        await port.write_text("outside.txt", "forbidden")
    assert captured.value.code == "workspace_escape"
    with pytest.raises(WorkspaceStateError) as captured:
        await port.run_shell("must-not-run", timeout=3)
    assert captured.value.code == "runtime_preflight_failed"
    assert handle.actions == [
        ("printf ok", 1_000, fixture.plan.effective_capabilities.limits.observation_bytes)
    ]
    assert not (tmp_path / "escape").exists()

    receipt = await lease.close()
    assert receipt.state is CleanupState.RELEASED
    assert await lease.close() == receipt
    assert lease.state is WorkspaceLeaseState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_conductor_tool_port_dispatches_only_exact_terminal_authority() -> None:
    binding = RunnerToolBinding(
        "terminal",
        digest("conductor-terminal"),
        ("read", "write"),
    )
    actions: list[tuple[str, int, int]] = []
    closed = False

    async def begin() -> None:
        if closed:
            raise WorkspaceStateError(
                "lease is not active",
                code="lease_not_active",
                lease_id="lease-conductor",
            )

    async def end() -> None:
        return None

    async def run_shell(
        command: str,
        *,
        timeout_ms: int,
        output_limit: int,
    ) -> Mapping[str, Any]:
        actions.append((command, timeout_ms, output_limit))
        return {"stdout": "conductor", "returncode": 0}

    lease = SimpleNamespace(
        lease_id="lease-conductor",
        plan=SimpleNamespace(
            effective_plan_digest=digest("conductor-plan"),
            tool_bindings=(binding,),
            limits=SimpleNamespace(action_timeout_ms=2_000, observation_bytes=4_096),
        ),
        _begin_operation=begin,
        _end_operation=end,
        _runtime=SimpleNamespace(run_shell=run_shell),
    )
    port = sandbox_module.LeaseBackedRunnerWorkspace(
        lease,
        lease.plan.effective_plan_digest,
        (binding,),
    )

    assert await port.invoke_tool(
        "terminal",
        {"command": "printf conductor"},
        timeout_ms=1_501,
    ) == {"stdout": "conductor", "returncode": 0}
    assert actions == [("printf conductor", 1_501, 4_096)]
    class HostileToolId(str):
        comparisons = 0

        def __eq__(self, other: object) -> bool:
            del other
            type(self).comparisons += 1
            return True

    with pytest.raises(WorkspaceStateError):
        await port.invoke_tool(
            HostileToolId("terminal"),
            {"command": "must-not-run"},
            timeout_ms=1_000,
        )
    assert HostileToolId.comparisons == 0
    assert actions == [("printf conductor", 1_501, 4_096)]

    class StatefulArguments(Mapping[str, Any]):
        reads = 0

        def __getitem__(self, key: str) -> Any:
            assert key == "command"
            type(self).reads += 1
            return (
                "printf snapshotted"
                if type(self).reads == 1
                else "must-not-run"
            )

        def __iter__(self):
            return iter(("command",))

        def __len__(self) -> int:
            return 1

    assert await port.invoke_tool(
        "terminal",
        StatefulArguments(),
        timeout_ms=1_502,
    ) == {"stdout": "conductor", "returncode": 0}
    assert StatefulArguments.reads == 1
    assert actions[-1] == ("printf snapshotted", 1_502, 4_096)

    invalid_invocations = (
        ("shell", {"command": "must-not-run"}, 1_000),
        ("unbound", {"command": "must-not-run"}, 1_000),
        ("terminal", {}, 1_000),
        ("terminal", {"command": ""}, 1_000),
        ("terminal", {"command": 1}, 1_000),
        ("terminal", {"command": "must-not-run", "extra": True}, 1_000),
        ("terminal", {"command": "must-not-run"}, True),
        ("terminal", {"command": "must-not-run"}, 2_001),
    )
    for tool_id, arguments, timeout_ms in invalid_invocations:
        with pytest.raises(WorkspaceStateError):
            await port.invoke_tool(tool_id, arguments, timeout_ms=timeout_ms)
    assert actions == [
        ("printf conductor", 1_501, 4_096),
        ("printf snapshotted", 1_502, 4_096),
    ]

    closed = True
    with pytest.raises(WorkspaceStateError) as captured:
        await port.invoke_tool(
            "terminal",
            {"command": "must-not-run"},
            timeout_ms=1_000,
        )
    assert captured.value.code == "lease_not_active"
    assert actions == [
        ("printf conductor", 1_501, 4_096),
        ("printf snapshotted", 1_502, 4_096),
    ]


@pytest.mark.parametrize(
    ("action_timeout_ms", "admitted"),
    [(1, False), (500, False), (999, False), (1_000, True)],
)
async def test_runner_action_timeout_uses_exact_milliseconds_before_dispatch(
    tmp_path: Path, action_timeout_ms: int, admitted: bool
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    limits_payload = fixture.plan.effective_capabilities.limits.model_dump(
        mode="python"
    )
    limits_payload["action_timeout_ms"] = action_timeout_ms
    limits = c.ExecutionLimits.model_validate(limits_payload)
    plan = replace_plan_capabilities(fixture.plan, limits=limits)
    candidate = replace(
        fixture,
        plan=plan,
        request=WorkspaceOpenRequest(fixture.request.episode_id, plan),
    )
    harness = RuntimeHarness(tmp_path, candidate)
    lease = await harness.manager.open(candidate.request)
    handle = harness.backend.handles[0]

    if admitted:
        result = await lease.runner_workspace.run_shell("exact-bound", timeout=1)
        assert result["returncode"] == 0
        assert handle.actions == [
            ("exact-bound", 1_000, plan.effective_capabilities.limits.observation_bytes)
        ]
    else:
        with pytest.raises(WorkspaceStateError) as captured:
            await lease.runner_workspace.run_shell("must-not-run", timeout=1)
        assert captured.value.code == "runtime_preflight_failed"
        assert handle.actions == []

    assert (await lease.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_sparse_workspace_read_is_bounded_before_any_path_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    sparse = lease._materialized.workspace_path / "work" / "sparse.bin"
    sparse.touch(mode=0o600)
    sparse_size = 8 * 1024**3
    os.truncate(sparse, sparse_size)
    original_read_bytes = Path.read_bytes

    def reject_unbounded_read(path: Path) -> bytes:
        if path == sparse:
            raise AssertionError("workspace read attempted an unbounded Path.read_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)

    with pytest.raises(WorkspaceStateError) as captured:
        await asyncio.wait_for(lease.runner_workspace.read_text("work/sparse.bin"), 1)

    assert captured.value.code == "output_limit_exceeded"
    assert sparse.stat().st_size == sparse_size
    assert harness.backend.handles[0].actions == []
    receipt = await lease.close()
    assert receipt.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize("operation", ["read", "write"])
async def test_workspace_descriptor_io_rejects_leaf_symlink_swap_before_external_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    target = lease._materialized.workspace_path / "work" / "target.txt"
    target.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    entered = asyncio.Event()
    release = asyncio.Event()
    original_to_thread = asyncio.to_thread
    first_effect = True

    async def gated_to_thread(
        function: Any, /, *args: Any, **kwargs: Any
    ) -> Any:
        nonlocal first_effect
        if first_effect:
            first_effect = False
            entered.set()
            await release.wait()
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(sandbox_module.asyncio, "to_thread", gated_to_thread)
    if operation == "read":
        action = asyncio.create_task(
            lease.runner_workspace.read_text("work/target.txt")
        )
    else:
        action = asyncio.create_task(
            lease.runner_workspace.write_text("work/target.txt", "candidate")
        )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        target.unlink()
        target.symlink_to(outside)
        release.set()
        with pytest.raises(WorkspaceStateError) as captured:
            await asyncio.wait_for(action, 1)
    finally:
        release.set()
        if not action.done():
            action.cancel()
            await asyncio.gather(action, return_exceptions=True)

    assert captured.value.code == "workspace_escape"
    assert outside.read_text(encoding="utf-8") == "outside"
    assert harness.backend.handles[0].actions == []
    receipt = await lease.close()
    assert receipt.state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize(
    "attack",
    ["start-symlink", "descendant-symlink", "descendant-inode"],
)
async def test_list_files_descriptor_walk_rejects_directory_identity_swaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    work = lease._materialized.workspace_path / "work"
    tree = work / "tree"
    child = tree / "child"
    child.mkdir(parents=True)
    (child / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "host-directory"
    outside.mkdir()
    (outside / "host-canary.txt").write_text("host", encoding="utf-8")
    replacement = work / "replacement"
    replacement.mkdir()
    (replacement / "replacement-canary.txt").write_text(
        "replacement", encoding="utf-8"
    )
    barrier_component = "tree" if attack == "start-symlink" else "child"
    entered = threading.Event()
    release = threading.Event()
    original_open = os.open
    blocked = False

    def gated_open(
        path: Any, flags: int, *args: Any, **kwargs: Any
    ) -> int:
        nonlocal blocked
        if (
            not blocked
            and path == barrier_component
            and kwargs.get("dir_fd") is not None
        ):
            blocked = True
            entered.set()
            if not release.wait(1):
                raise TimeoutError("list descriptor barrier was not released")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(sandbox_module.os, "open", gated_open)
    listing = asyncio.create_task(
        lease.runner_workspace.list_files("work/tree", depth=2)
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 2)
        if attack == "start-symlink":
            tree.rename(work / "parked-tree")
            tree.symlink_to(outside, target_is_directory=True)
        else:
            child.rename(tree / "parked-child")
            if attack == "descendant-symlink":
                child.symlink_to(outside, target_is_directory=True)
            else:
                replacement.rename(child)
        release.set()
        with pytest.raises(WorkspaceStateError) as captured:
            await asyncio.wait_for(listing, 1)
    finally:
        release.set()
        if not listing.done():
            listing.cancel()
            await asyncio.gather(listing, return_exceptions=True)

    assert captured.value.code == "workspace_escape"
    assert (outside / "host-canary.txt").read_text(encoding="utf-8") == "host"
    assert harness.backend.handles[0].actions == []
    receipt = await lease.close()
    assert receipt.state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize("special", ["fifo", "hardlink"])
async def test_list_files_rejects_special_or_aliased_entries(
    tmp_path: Path, special: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    tree = lease._materialized.workspace_path / "work" / "tree"
    tree.mkdir()
    if special == "fifo":
        os.mkfifo(tree / "special")
    else:
        original = tree / "original.txt"
        original.write_text("bound", encoding="utf-8")
        os.link(original, tree / "alias.txt")

    with pytest.raises(WorkspaceStateError) as captured:
        await asyncio.wait_for(
            lease.runner_workspace.list_files("work/tree", depth=1), 1
        )

    assert captured.value.code == "workspace_escape"
    assert (await lease.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_list_files_depth_is_exact_and_order_is_canonical(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    tree = lease._materialized.workspace_path / "work" / "tree"
    (tree / "child" / "grand").mkdir(parents=True)
    (tree / "root.txt").write_text("root", encoding="utf-8")
    (tree / "child" / "one.txt").write_text("one", encoding="utf-8")
    (tree / "child" / "grand" / "two.txt").write_text("two", encoding="utf-8")

    assert await lease.runner_workspace.list_files("work/tree", depth=0) == {
        "path": "work/tree",
        "files": ["work/tree/child", "work/tree/root.txt"],
    }
    assert await lease.runner_workspace.list_files("work/tree", depth=1) == {
        "path": "work/tree",
        "files": [
            "work/tree/child",
            "work/tree/root.txt",
            "work/tree/child/grand",
            "work/tree/child/one.txt",
        ],
    }

    assert (await lease.close()).state is CleanupState.RELEASED


@pytest.mark.parametrize("bound", ["entries", "output-bytes"])
async def test_list_files_enforces_entry_and_serialized_output_bounds(
    tmp_path: Path, bound: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    tree = lease._materialized.workspace_path / "work" / "tree"
    tree.mkdir()
    if bound == "entries":
        maximum = lease.plan.security_policy.snapshot_max_inodes
        for index in range(maximum):
            (tree / f"f{index:03d}").touch()
        admitted = await lease.runner_workspace.list_files("work/tree", depth=0)
        assert len(admitted["files"]) == maximum
        (tree / "overflow").touch()
    else:
        for index in range(30):
            (tree / (f"{index:02d}-" + "x" * 180)).touch()

    with pytest.raises(WorkspaceStateError) as captured:
        await asyncio.wait_for(
            lease.runner_workspace.list_files("work/tree", depth=0), 1
        )

    assert captured.value.code == "output_limit_exceeded"
    assert (await lease.close()).state is CleanupState.RELEASED


async def test_runner_port_rejects_every_action_after_quiesce_without_runtime_effects(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    port = lease.runner_workspace
    await port.write_text("work/before.txt", "before")

    snapshot = await lease.seal_for_verifier()

    assert snapshot.source_lease_id == lease.lease_id
    assert lease.state is WorkspaceLeaseState.QUIESCING
    action_count = len(harness.backend.handles[0].actions)
    operations = (
        lambda: port.run_shell("late", timeout=1),
        lambda: port.read_text("work/before.txt"),
        lambda: port.write_text("work/late.txt", "late"),
        lambda: port.list_files("work", depth=1),
    )
    for operation in operations:
        with pytest.raises(WorkspaceStateError) as captured:
            await operation()
        assert captured.value.code == "lease_not_active"
    assert len(harness.backend.handles[0].actions) == action_count
    assert not (lease._materialized.workspace_path / "work" / "late.txt").exists()
    await lease.close()


async def test_concurrent_episodes_have_unique_workspaces_and_zero_canary_cross_read(
    tmp_path: Path,
) -> None:
    first_fixture = make_runtime_fixture(
        with_writable_mount=True, episode_id="episode-one"
    )
    second_request = WorkspaceOpenRequest("episode-two", first_fixture.plan)
    harness = RuntimeHarness(tmp_path, first_fixture)

    first, second = await asyncio.gather(
        harness.manager.open(first_fixture.request),
        harness.manager.open(second_request),
    )
    await asyncio.gather(
        first.runner_workspace.write_text("work/canary.txt", "one"),
        second.runner_workspace.write_text("work/canary.txt", "two"),
    )

    assert first.lease_id != second.lease_id
    assert first.measurement.workspace_id != second.measurement.workspace_id
    assert await first.runner_workspace.read_text("work/canary.txt") == {
        "path": "work/canary.txt",
        "content": "one",
        "offset": 0,
        "bytes": 3,
    }
    assert await second.runner_workspace.read_text("work/canary.txt") == {
        "path": "work/canary.txt",
        "content": "two",
        "offset": 0,
        "bytes": 3,
    }
    receipts = await harness.manager.close()
    assert len(receipts) == 2
    assert all(receipt.state is CleanupState.RELEASED for receipt in receipts)
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_launch_failure_removes_workspace_cache_holder_and_durable_lease(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    backend.failure = SandboxLaunchError("injected launch fault", code="runtime_launch_failed")
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)

    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.code == "runtime_launch_failed"
    assert len(backend.launches) == 1
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []



async def test_post_launch_setup_failure_releases_after_complete_detailed_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    backend.handle_termination_receipts = (
        CleanupStepReceipt("runtime_stop", CleanupState.RELEASED),
        CleanupStepReceipt("runtime_remove", CleanupState.RELEASED),
        CleanupStepReceipt("runtime_absence", CleanupState.RELEASED),
    )
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    build_plan = sandbox_module.build_sandbox_execution_plan

    class FailingSetup:
        argv = ("injected-setup",)
        timeout_ms = 100

    def plan_with_setup(*args: Any, **kwargs: Any) -> Any:
        return replace(build_plan(*args, **kwargs), setups=(FailingSetup(),))

    async def fail_setup(
        self: RecordingHandle,
        argv: tuple[str, ...],
        *,
        timeout_ms: int,
        output_limit: int,
    ) -> Mapping[str, Any]:
        self.argv_actions.append((argv, timeout_ms, output_limit))
        return {"returncode": 23, "stdout": "", "stderr": "setup failed"}

    monkeypatch.setattr(
        sandbox_module, "build_sandbox_execution_plan", plan_with_setup
    )
    monkeypatch.setattr(RecordingHandle, "run_argv", fail_setup)

    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.code == "runtime_launch_failed"
    assert backend.handles[0].argv_actions == [
        (
            ("injected-setup",),
            100,
            fixture.plan.effective_capabilities.limits.observation_bytes,
        )
    ]
    assert backend.handles[0].terminate_calls == 1
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []

@pytest.mark.parametrize(
    "runtime_steps",
    [
        (
            CleanupStepReceipt("runtime_stop", CleanupState.RELEASED),
            CleanupStepReceipt("runtime_remove", CleanupState.RELEASED),
            CleanupStepReceipt("runtime_absence", CleanupState.RELEASED),
        ),
        (
            CleanupStepReceipt("runtime_stop", CleanupState.ALREADY_RELEASED),
            CleanupStepReceipt("runtime_absence", CleanupState.RELEASED),
        ),
    ],
)
async def test_post_launch_record_failure_releases_after_complete_nonempty_detailed_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_steps: tuple[CleanupStepReceipt, ...],
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    backend.handle_termination_receipts = runtime_steps
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    write_record = harness.manager._write_lease_record
    calls = 0

    def fail_active_record(lease_id: str, payload: Mapping[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("active record durability fault")
        write_record(lease_id, payload)

    monkeypatch.setattr(harness.manager, "_write_lease_record", fail_active_record)

    with pytest.raises(OSError, match="active record durability fault"):
        await harness.manager.open(fixture.request)

    assert calls == 2
    assert backend.handles[0].terminate_calls == 1
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize("incomplete_state", [CleanupState.FAILED, CleanupState.QUARANTINED])
async def test_post_launch_record_failure_retains_workspace_and_record_when_detailed_cleanup_is_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    incomplete_state: CleanupState,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    backend.handle_termination_receipts = (
        CleanupStepReceipt("runtime_stop", CleanupState.RELEASED),
        CleanupStepReceipt("runtime_remove", incomplete_state),
    )
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    write_record = harness.manager._write_lease_record
    calls = 0

    def fail_active_record(lease_id: str, payload: Mapping[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("active record durability fault")
        write_record(lease_id, payload)

    monkeypatch.setattr(harness.manager, "_write_lease_record", fail_active_record)

    with pytest.raises(SandboxFault) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.primary.args == ("active record durability fault",)
    assert captured.value.cleanup_receipt.steps[:2] == backend.handle_termination_receipts
    assert captured.value.cleanup_receipt.steps[2:4] == (
        CleanupStepReceipt(
            "workspace",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
        CleanupStepReceipt(
            "cache_holder",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
    )
    assert len(list(harness.workspace_root.iterdir())) == 1
    assert len(list(harness.lease_root.glob("*.json"))) == 1


async def test_lease_directory_is_fsynced_after_record_replace_and_before_backend_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    events: list[str] = []
    real_fsync = sandbox_module.os.fsync
    launch = backend.launch

    def recording_fsync(fd: int) -> None:
        events.append("directory-fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file-fsync")
        real_fsync(fd)

    async def recording_launch(
        plan: Any, workspace: Path, *, context: Any
    ) -> tuple[RecordingHandle, SandboxMeasurement]:
        events.append("backend-launch")
        return await launch(plan, workspace, context=context)

    monkeypatch.setattr(sandbox_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(backend, "launch", recording_launch)

    lease = await harness.manager.open(fixture.request)

    first_file = events.index("file-fsync")
    first_directory = events.index("directory-fsync", first_file + 1)
    assert first_file < first_directory < events.index("backend-launch")
    assert (await lease.close()).state is CleanupState.RELEASED


async def test_lease_directory_fsync_failure_prevents_backend_start_and_exact_cleans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    real_fsync = sandbox_module.os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("lease directory durability fault")
        real_fsync(fd)

    monkeypatch.setattr(sandbox_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(OSError, match="lease directory durability fault"):
        await harness.manager.open(fixture.request)

    assert backend.launches == []
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_measurement_mismatch_terminates_runtime_and_exposes_no_runner_port(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    backend.measurement_mismatch = ("uid",)
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)

    with pytest.raises(SandboxAttestationError) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.code == "runtime_measurement_mismatch"
    assert backend.handles[0].terminate_calls == 1
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize(
    "first_runtime_state",
    [CleanupState.FAILED, CleanupState.QUARANTINED],
)
async def test_unproven_runtime_cleanup_quarantines_dependents_until_safe_retry(
    tmp_path: Path, first_runtime_state: CleanupState
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    handle = harness.backend.handles[0]
    handle.termination_states = [first_runtime_state, CleanupState.RELEASED]

    first = await lease.close()

    assert first.state is CleanupState.QUARANTINED
    assert first.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", first_runtime_state),
        CleanupStepReceipt(
            "workspace",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
        CleanupStepReceipt(
            "cache_holder",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
        CleanupStepReceipt(
            "lease_record",
            CleanupState.QUARANTINED,
            "dependent cleanup incomplete",
        ),
    )
    assert lease.state is WorkspaceLeaseState.QUARANTINED
    assert lease.cleanup_receipt is first
    assert handle.terminate_calls == 1
    assert lease._materialized.workspace_path.exists()
    assert (harness.lease_root / f"{lease.lease_id}.json").exists()

    second = await lease.close()
    assert second.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert lease.state is WorkspaceLeaseState.RELEASED
    assert lease.cleanup_receipt is second
    assert handle.terminate_calls == 2
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []
    assert await lease.close() == second
    assert handle.terminate_calls == 2



@pytest.mark.asyncio
async def test_execute_rejects_malformed_argv_without_fencing_active_lease(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    handle = harness.backend.handles[0]

    for argv in ((), ("",), ("bad\x00argument",), (object(),)):
        with pytest.raises(WorkspaceStateError) as captured:
            await lease.execute(argv)
        assert captured.value.code == "runtime_preflight_failed"

    assert lease.state is WorkspaceLeaseState.ACTIVE
    assert handle.argv_actions == []

@pytest.mark.parametrize("completion", ["finish", "cancel"])
async def test_close_fences_new_operations_and_drains_an_active_operation(
    tmp_path: Path, completion: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    port = lease.runner_workspace
    handle = harness.backend.handles[0]
    handle.run_entered = asyncio.Event()
    handle.release_run = asyncio.Event()
    operation = asyncio.create_task(port.run_shell("blocked", timeout=1))

    await asyncio.wait_for(handle.run_entered.wait(), 1)
    closing = asyncio.create_task(lease.close())
    await asyncio.sleep(0)
    assert not closing.done()

    if completion == "finish":
        handle.release_run.set()
        assert await asyncio.wait_for(operation, 1) == {
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
        }
    else:
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 1)

    receipt = await asyncio.wait_for(closing, 1)
    assert receipt.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert lease.state is WorkspaceLeaseState.RELEASED
    assert handle.actions == [
        ("blocked", 1_000, fixture.plan.effective_capabilities.limits.observation_bytes)
    ]

    with pytest.raises(WorkspaceStateError) as captured:
        await port.write_text("work/recreated.txt", "late")
    assert captured.value.code == "lease_not_active"
    with pytest.raises(WorkspaceStateError) as list_captured:
        await port.list_files("work", depth=1)
    assert list_captured.value.code == "lease_not_active"
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_cancel_preempts_active_operation_before_cleanup(tmp_path: Path) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    port = lease.runner_workspace
    handle = harness.backend.handles[0]
    handle.run_entered = asyncio.Event()
    handle.release_run = asyncio.Event()
    operation = asyncio.create_task(port.run_shell("blocked", timeout=1))

    await asyncio.wait_for(handle.run_entered.wait(), 1)
    receipt = await asyncio.wait_for(lease.cancel(), 1)

    with pytest.raises(asyncio.CancelledError):
        await operation
    assert receipt.state is CleanupState.RELEASED
    assert handle.terminate_calls == 1
    assert lease.state is WorkspaceLeaseState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize("method_name", ["close", "cancel"])
async def test_direct_lease_cleanup_survives_caller_cancellation(
    tmp_path: Path, method_name: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    handle = harness.backend.handles[0]
    handle.terminate_entered = asyncio.Event()
    handle.release_terminate = asyncio.Event()

    first = asyncio.create_task(getattr(lease, method_name)())
    await asyncio.wait_for(handle.terminate_entered.wait(), 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, 1)

    follower = asyncio.create_task(lease.close())
    await asyncio.sleep(0)
    assert follower.done() is False
    handle.release_terminate.set()
    receipt = await asyncio.wait_for(follower, 1)

    assert receipt.state is CleanupState.RELEASED
    assert handle.terminate_calls == 1
    assert lease.state is WorkspaceLeaseState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize(
    "first_state",
    [CleanupState.RELEASED, CleanupState.QUARANTINED],
)
async def test_manager_close_cancellation_preserves_whole_shared_cleanup_outcome(
    tmp_path: Path, first_state: CleanupState
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    leases = [
        await harness.manager.open(
            WorkspaceOpenRequest(f"episode-close-{index}", fixture.plan)
        )
        for index in range(3)
    ]
    first_handle = harness.backend.handles[0]
    first_handle.termination_states = [first_state, CleanupState.RELEASED]
    first_handle.terminate_entered = asyncio.Event()
    first_handle.release_terminate = asyncio.Event()

    first_caller = asyncio.create_task(harness.manager.close())
    await asyncio.wait_for(first_handle.terminate_entered.wait(), 1)
    assert [handle.terminate_calls for handle in harness.backend.handles] == [1, 1, 1]
    first_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first_caller, 1)

    later_caller = asyncio.create_task(harness.manager.close())
    await asyncio.sleep(0)
    assert not later_caller.done()
    first_handle.release_terminate.set()
    receipts = await asyncio.wait_for(later_caller, 1)

    assert tuple(receipt.lease_id for receipt in receipts) == tuple(
        lease.lease_id for lease in leases
    )
    assert receipts[0].state is first_state
    assert all(
        receipt.state is CleanupState.RELEASED for receipt in receipts[1:]
    )
    assert [handle.terminate_calls for handle in harness.backend.handles] == [1, 1, 1]
    if first_state is CleanupState.RELEASED:
        assert list(harness.workspace_root.iterdir()) == []
        assert await harness.manager.close() == receipts
        assert list(harness.lease_root.iterdir()) == []
    else:
        assert receipts[0].steps == (
            CleanupStepReceipt(
                "child_verifier",
                CleanupState.ALREADY_RELEASED,
            ),
            CleanupStepReceipt("runtime", CleanupState.QUARANTINED),
            CleanupStepReceipt(
                "workspace",
                CleanupState.QUARANTINED,
                "dependent runtime cleanup incomplete",
            ),
            CleanupStepReceipt(
                "cache_holder",
                CleanupState.QUARANTINED,
                "dependent runtime cleanup incomplete",
            ),
            CleanupStepReceipt(
                "lease_record",
                CleanupState.QUARANTINED,
                "dependent cleanup incomplete",
            ),
        )
        assert leases[0]._materialized.workspace_path.exists()
        assert len(list(harness.workspace_root.iterdir())) == 1
        assert len(list(harness.lease_root.iterdir())) == 2
        retry_receipts = await harness.manager.close()
        assert retry_receipts[0].state is CleanupState.RELEASED
        assert first_handle.terminate_calls == 2
        assert list(harness.workspace_root.iterdir()) == []
        assert list(harness.lease_root.iterdir()) == []





async def test_manager_close_racing_open_includes_registered_lease_and_rejects_late_open(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    backend.launch_entered = asyncio.Event()
    backend.release_launch = asyncio.Event()
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)

    open_task = asyncio.create_task(harness.manager.open(fixture.request))
    await backend.launch_entered.wait()
    close_task = asyncio.create_task(harness.manager.close())
    backend.release_launch.set()
    lease = await open_task
    receipts = await close_task

    assert len(receipts) == 1
    assert receipts[0].lease_id == lease.lease_id
    assert receipts[0].state is CleanupState.RELEASED
    assert lease.state is WorkspaceLeaseState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []
    with pytest.raises(WorkspaceStateError) as captured:
        await harness.manager.open(fixture.request)
    assert captured.value.code == "lease_manager_closed"
    assert len(backend.launches) == 1


class ReconcileBackend(RecordingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.reconciled: list[Mapping[str, Any]] = []

    async def reconcile(
        self, record: Mapping[str, Any]
    ) -> tuple[CleanupStepReceipt, ...]:
        self.reconciled.append(record)
        assert record["owner_token"]
        assert record["epoch"] == 1
        return (CleanupStepReceipt("runtime", CleanupState.RELEASED),)

async def test_lease_record_payload_identity_must_match_record_filename(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    record_path = harness.lease_root / f"{lease.lease_id}.json"
    alias = harness.lease_root / ("lease-" + "d" * 32 + ".json")
    alias.write_bytes(record_path.read_bytes())

    with pytest.raises(WorkspaceStateError) as captured:
        harness.manager._read_lease_record(alias)

    assert captured.value.code == "stale_identity_uncertain"
    alias.unlink()
    assert (await lease.close()).state is CleanupState.RELEASED


async def test_stale_reconcile_rejects_absolute_workspace_identity(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    original = RuntimeHarness(tmp_path, fixture)
    lease = await original.manager.open(fixture.request)
    record_path = original.lease_root / f"{lease.lease_id}.json"
    record = dict(original.manager._read_lease_record(record_path))
    outside = tmp_path / "outside-workspace"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("retained")
    record["workspace_id"] = str(outside)
    record["workspace_path"] = str(outside)
    original.manager._write_lease_record(lease.lease_id, record)
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=ReconcileBackend(),
        docker_backend=None,
        random_bytes=DeterministicRandom(8_000),
    )
    original.clock.advance(minutes=5)
    original.manager._release_lease_owner_lock(lease.lease_id, unlink=False)

    receipt = (await recovery.reconcile_stale())[0]

    assert any(
        step.resource == "workspace"
        and step.state is CleanupState.QUARANTINED
        and step.detail == "stale_identity_uncertain"
        for step in receipt.steps
    )
    assert sentinel.read_text() == "retained"
    assert await recovery.close() == ()
    assert (await lease.close()).state is CleanupState.RELEASED


async def test_invalid_owner_lock_quarantines_only_its_stale_record(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    original = RuntimeHarness(tmp_path, fixture)
    first = await original.manager.open(fixture.request)
    second = await original.manager.open(fixture.request)
    original.clock.advance(minutes=5)
    for lease in (first, second):
        original.manager._release_lease_owner_lock(
            lease.lease_id,
            unlink=False,
        )
    invalid_lock = original.lease_root / f"{first.lease_id}.owner.lock"
    invalid_lock.unlink()
    invalid_lock.symlink_to(original.lease_root)
    backend = ReconcileBackend()
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=backend,
        docker_backend=None,
        random_bytes=DeterministicRandom(8_500),
    )

    receipts = await recovery.reconcile_stale()

    invalid = next(item for item in receipts if item.lease_id == first.lease_id)
    assert invalid.steps == (
        CleanupStepReceipt(
            "lease_record",
            CleanupState.QUARANTINED,
            "owner_lock_invalid",
        ),
    )
    assert [item["lease_id"] for item in backend.reconciled] == [second.lease_id]
    assert await recovery.close() == ()
    invalid_lock.unlink()
    assert original.manager._claim_lease_owner_lock(first.lease_id)
    await first.close()
    await second.close()


async def test_reconcile_and_close_serialize_owner_lock_lifecycle(
    tmp_path: Path,
) -> None:
    class BlockingReconcileBackend(ReconcileBackend):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def reconcile(
            self,
            record: Mapping[str, Any],
        ) -> tuple[CleanupStepReceipt, ...]:
            self.entered.set()
            await self.release.wait()
            return await super().reconcile(record)

    fixture = make_runtime_fixture(with_writable_mount=True)
    original = RuntimeHarness(tmp_path, fixture)
    lease = await original.manager.open(fixture.request)
    original.clock.advance(minutes=5)
    original.manager._release_lease_owner_lock(lease.lease_id, unlink=False)
    backend = BlockingReconcileBackend()
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=backend,
        docker_backend=None,
        random_bytes=DeterministicRandom(8_750),
    )
    first = asyncio.create_task(recovery.reconcile_stale())
    await asyncio.wait_for(backend.entered.wait(), 1)
    second = asyncio.create_task(recovery.reconcile_stale())
    closing = asyncio.create_task(recovery.close())
    await asyncio.sleep(0)
    assert not second.done()
    assert not closing.done()

    backend.release.set()
    first_receipts = await asyncio.wait_for(first, 1)
    second_receipts = await asyncio.wait_for(second, 1)
    close_receipts = await asyncio.wait_for(closing, 1)

    assert len(first_receipts) == 1
    assert second_receipts == ()
    assert close_receipts == ()
    assert [item["lease_id"] for item in backend.reconciled] == [lease.lease_id]
    assert recovery._lease_owner_locks == {}
    assert recovery._lease_root_fd is None
    assert (await lease.close()).state in {
        CleanupState.RELEASED,
        CleanupState.ALREADY_RELEASED,
    }


async def test_restart_reconciliation_leaves_live_foreign_lease_then_reclaims_exact_expiry(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    original = RuntimeHarness(tmp_path, fixture)
    lease = await original.manager.open(fixture.request)
    record_path = original.lease_root / f"{lease.lease_id}.json"
    workspace_path = lease._materialized.workspace_path
    record = dict(original.manager._read_lease_record(record_path))
    recovery_backend = ReconcileBackend()
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=recovery_backend,
        docker_backend=None,
        random_bytes=DeterministicRandom(9_000),
    )

    assert await recovery.reconcile_stale() == ()
    assert recovery_backend.reconciled == []
    assert record_path.exists()
    assert workspace_path.exists()

    original.clock.advance(minutes=5)
    blocked = await recovery.reconcile_stale()

    assert len(blocked) == 1
    assert blocked[0].lease_id == lease.lease_id
    assert blocked[0].steps == (
        CleanupStepReceipt(
            "lease_record",
            CleanupState.QUARANTINED,
            "live_owner",
        ),
    )
    assert recovery_backend.reconciled == []
    assert workspace_path.exists()
    assert record_path.exists()

    original.manager._release_lease_owner_lock(lease.lease_id, unlink=False)
    receipts = await recovery.reconcile_stale()

    assert len(receipts) == 1
    assert receipts[0].steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert len(recovery_backend.reconciled) == 1
    assert not workspace_path.exists()
    assert not record_path.exists()
    assert original.store.recover_stale_cache_holder(record) == CleanupStepReceipt(
        "cache_holder", CleanupState.ALREADY_RELEASED
    )


@pytest.mark.parametrize(
    "mutation",
    ["holder", "token", "epoch", "sources", "missing-token"],
)
async def test_stale_cache_identity_mismatch_quarantines_then_exact_retry_releases(
    tmp_path: Path, mutation: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    original = RuntimeHarness(tmp_path, fixture)
    lease = await original.manager.open(fixture.request)
    record_path = original.lease_root / f"{lease.lease_id}.json"
    exact_record = dict(original.manager._read_lease_record(record_path))
    forged = dict(exact_record)
    if mutation == "holder":
        forged["cache_holder_id"] = "foreign-holder"
    elif mutation == "token":
        forged["cache_token_value"] = "foreign-token"
    elif mutation == "epoch":
        forged["cache_epoch"] += 1
    elif mutation == "sources":
        forged["cache_source_digests"] = []
    else:
        forged.pop("cache_token_value")
    original.manager._write_lease_record(lease.lease_id, forged)
    recovery_backend = ReconcileBackend()
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=recovery_backend,
        docker_backend=None,
        random_bytes=DeterministicRandom(20_000),
    )
    original.clock.advance(minutes=5)
    original.manager._release_lease_owner_lock(lease.lease_id, unlink=False)

    first = (await recovery.reconcile_stale())[0]
    assert first.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt(
            "cache_holder", CleanupState.QUARANTINED, "stale_identity_uncertain"
        ),
        CleanupStepReceipt(
            "lease_record", CleanupState.QUARANTINED, "stale_identity_uncertain"
        ),
    )
    assert record_path.exists()
    first_record_bytes = record_path.read_bytes()

    second = (await recovery.reconcile_stale())[0]
    assert second.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.ALREADY_RELEASED),
        CleanupStepReceipt(
            "cache_holder", CleanupState.QUARANTINED, "stale_identity_uncertain"
        ),
        CleanupStepReceipt(
            "lease_record", CleanupState.QUARANTINED, "stale_identity_uncertain"
        ),
    )
    assert record_path.read_bytes() == first_record_bytes

    original.manager._write_lease_record(lease.lease_id, exact_record)
    third = (await recovery.reconcile_stale())[0]
    assert third.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.ALREADY_RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert not record_path.exists()
    assert list(original.workspace_root.iterdir()) == []


async def test_corrupt_restart_record_is_quarantined_without_runtime_or_workspace_effect(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    corrupt = harness.lease_root / "lease-corrupt.json"
    corrupt.write_bytes(b"{")

    receipts = await harness.manager.reconcile_stale()

    assert len(receipts) == 1
    assert receipts[0].lease_id == "lease-corrupt"
    assert receipts[0].state is CleanupState.QUARANTINED
    assert receipts[0].steps[0].detail == "stale_identity_uncertain"
    assert harness.backend.launches == []
    assert corrupt.exists()
    assert list(harness.workspace_root.iterdir()) == []


async def test_unreadable_verifier_record_blocks_primary_reconcile_until_removed(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    original = RuntimeHarness(tmp_path, fixture)
    lease = await original.manager.open(fixture.request)
    primary_record = original.lease_root / f"{lease.lease_id}.json"
    corrupt_child = original.lease_root / "verifier-lease-corrupt.json"
    corrupt_child.write_bytes(b"{")
    workspace = lease._materialized.workspace_path
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=ReconcileBackend(),
        docker_backend=None,
        random_bytes=DeterministicRandom(25_000),
    )
    original.clock.advance(minutes=5)
    original.manager._release_lease_owner_lock(lease.lease_id, unlink=False)

    receipts = await recovery.reconcile_stale()
    first = next(
        receipt
        for receipt in receipts
        if receipt.lease_id == lease.lease_id
    )

    assert first.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.QUARANTINED,
            "verifier-lease-corrupt",
        ),
        CleanupStepReceipt(
            "snapshot",
            CleanupState.QUARANTINED,
            "dependent verifier cleanup incomplete",
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt(
            "workspace",
            CleanupState.QUARANTINED,
            "stale_identity_uncertain",
        ),
        CleanupStepReceipt(
            "cache_holder",
            CleanupState.QUARANTINED,
            "stale_identity_uncertain",
        ),
        CleanupStepReceipt(
            "lease_record",
            CleanupState.QUARANTINED,
            "stale_identity_uncertain",
        ),
    )
    assert primary_record.exists()
    assert corrupt_child.exists()
    assert workspace.exists()

    corrupt_child.unlink()
    retry_receipts = await recovery.reconcile_stale()
    retry = next(
        receipt
        for receipt in retry_receipts
        if receipt.lease_id == lease.lease_id
    )

    assert retry.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert not primary_record.exists()
    assert not corrupt_child.exists()
    assert not workspace.exists()

@pytest.mark.parametrize("identity_field", ["uid", "gid"])
async def test_hardened_root_identity_is_rejected_before_materialization_or_launch(
    tmp_path: Path, identity_field: str
) -> None:
    fixture = make_runtime_fixture(runtime_class=c.RuntimeClass.HARDENED_DOCKER)
    original_policy = next(
        policy
        for policy in fixture.authorities.security_policies
        if policy.policy_digest == fixture.plan.sandbox.security_policy_digest
    )
    policy_projection = original_policy.projection()
    policy_projection[identity_field] = 0
    policy_digest = SandboxSecurityPolicy.derive_digest(policy_projection)
    root_policy = replace(
        original_policy,
        policy_digest=policy_digest,
        **{identity_field: 0},
    )
    sandbox_payload = fixture.plan.sandbox.model_dump(mode="python")
    sandbox_payload["security_policy_digest"] = policy_digest
    sandbox = c.SandboxGrant.model_validate(sandbox_payload)
    plan = replace_plan_capabilities(fixture.plan, sandbox=sandbox)
    runtime_records = tuple(
        c.SandboxRuntimeRegistryRecord(
            binding=c.SandboxBinding.model_validate(
                {
                    **record.binding.model_dump(mode="python"),
                    "security_policy_digest": policy_digest,
                }
            )
        )
        if record.binding.runtime_id == sandbox.runtime_id
        else record
        for record in fixture.registries.sandbox_runtimes
    )
    registry_names = (
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
    registry_values = {
        name: (
            runtime_records
            if name == "sandbox_runtimes"
            else getattr(fixture.registries, name)
        )
        for name in registry_names
    }
    registries = _registry_snapshot(**registry_values)
    authorities = replace(
        fixture.authorities,
        security_policies=tuple(
            sorted(
                (
                    root_policy
                    if policy.policy_digest == original_policy.policy_digest
                    else policy
                    for policy in fixture.authorities.security_policies
                ),
                key=lambda policy: policy.policy_digest,
            )
        ),
    )
    candidate = replace(
        fixture,
        plan=plan,
        request=WorkspaceOpenRequest(fixture.request.episode_id, plan),
        registries=registries,
        authorities=authorities,
    )
    harness = RuntimeHarness(tmp_path, candidate)

    with pytest.raises(SandboxPlanError) as captured:
        await harness.manager.open(candidate.request)

    assert captured.value.code == "runtime_identity_mismatch"
    assert harness.reader.loads == []
    assert harness.backend.launches == []
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_isolated_runtime_requires_measured_workspace_quota_before_launch(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        with_writable_mount=True,
    )
    harness = RuntimeHarness(tmp_path, fixture)

    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.code == "runtime_preflight_failed"
    assert harness.backend.launches == []
    assert harness.backend.handles == []
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_durable_lease_separates_runtime_authority_from_resource_identity(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    launch_context = harness.backend.launches[0][2]
    workspace_metadata = lease._materialized.workspace_path.stat()
    expected_storage_authority = (
        f"{DirectoryStorageBackend.__module__}."
        f"{DirectoryStorageBackend.__qualname__}"
    )
    record = harness.manager._read_lease_record(
        harness.lease_root / f"{lease.lease_id}.json"
    )

    assert record["runtime_authority_id"] == fixture.plan.sandbox.runtime_id
    assert record["runtime_resource_id"] == lease.measurement.runtime_resource_id
    assert record["runtime_authority_id"] != record["runtime_resource_id"]
    assert record["effective_plan_digest"] == fixture.plan.canonical_digest()
    assert record["workspace_id"] == lease.measurement.workspace_id
    assert launch_context.role == "primary"
    assert launch_context.lease_id == lease.lease_id
    assert launch_context.workspace_id == lease.measurement.workspace_id
    assert launch_context.storage.authority_id == expected_storage_authority
    assert launch_context.storage.quota_enforced is False
    assert (
        launch_context.storage.quota_bytes
        == fixture.plan.effective_capabilities.resources.storage_bytes
    )
    assert launch_context.storage.owner_uid == workspace_metadata.st_uid
    assert launch_context.storage.owner_gid == workspace_metadata.st_gid
    assert record["storage_authority_id"] == expected_storage_authority
    assert record["storage_quota_bytes"] == launch_context.storage.quota_bytes
    assert lease.measurement.requested["storage_bytes"] == launch_context.storage.quota_bytes
    assert lease.measurement.effective == lease.measurement.requested
    assert lease.measurement.measured == lease.measurement.requested

    receipt = await lease.close()
    assert receipt.steps == (
        CleanupStepReceipt(
            "child_verifier",
            CleanupState.ALREADY_RELEASED,
        ),
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


@pytest.mark.parametrize("carrier", ["request", "registries", "authorities"])
def test_plan_boundary_rejects_nonexact_carriers_before_authority_lookup(
    carrier: str,
) -> None:
    fixture = make_runtime_fixture()
    request: Any = fixture.request
    registries: Any = fixture.registries
    authorities: Any = fixture.authorities
    if carrier == "request":
        request = object()
    elif carrier == "registries":
        registries = object()
    else:
        authorities = object()

    with pytest.raises(SandboxPlanError) as captured:
        build_sandbox_execution_plan(request, registries, authorities)

    assert captured.value.code == "plan_type_invalid"


@pytest.mark.parametrize(
    "catalog",
    ["runtime", "image", "security-policy", "network-policy", "verifier"],
)
def test_installed_authority_catalog_rejects_duplicate_resolution(
    catalog: str,
) -> None:
    fixture = make_runtime_fixture()
    authorities = fixture.authorities
    values = {
        "runtimes": authorities.runtimes,
        "images": authorities.images,
        "security_policies": authorities.security_policies,
        "network_policies": authorities.network_policies,
        "verifiers": authorities.verifiers,
    }
    field = {
        "runtime": "runtimes",
        "image": "images",
        "security-policy": "security_policies",
        "network-policy": "network_policies",
        "verifier": "verifiers",
    }[catalog]
    duplicated = values[field] + (values[field][0],)

    with pytest.raises(ValueError):
        InstalledSandboxAuthoritySet(
            runtimes=duplicated if field == "runtimes" else authorities.runtimes,
            images=duplicated if field == "images" else authorities.images,
            security_policies=(
                duplicated
                if field == "security_policies"
                else authorities.security_policies
            ),
            network_policies=(
                duplicated
                if field == "network_policies"
                else authorities.network_policies
            ),
            verifiers=duplicated if field == "verifiers" else authorities.verifiers,
        )


async def test_materialization_cancellation_waits_for_owned_worker_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    entered = threading.Event()
    release = threading.Event()
    original_materialize = harness.store.materialize

    def blocked_materialize(plan: Any) -> Any:
        entered.set()
        if not release.wait(timeout=2):
            raise AssertionError("materialization worker was not released")
        return original_materialize(plan)

    monkeypatch.setattr(harness.store, "materialize", blocked_materialize)
    opening = asyncio.create_task(harness.manager.open(fixture.request))
    assert await asyncio.to_thread(entered.wait, 1)

    opening.cancel()
    await asyncio.sleep(0)
    assert not opening.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(opening, 1)

    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []
    assert await harness.manager.close() == ()


async def test_launch_cancellation_releases_materialization_and_durable_record(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = RecordingBackend()
    backend.launch_entered = asyncio.Event()
    backend.release_launch = asyncio.Event()
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)
    opening = asyncio.create_task(harness.manager.open(fixture.request))
    await backend.launch_entered.wait()

    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening

    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []
    assert await harness.manager.close() == ()


@pytest.mark.parametrize(
    "runtime_state", [CleanupState.FAILED, CleanupState.QUARANTINED]
)
async def test_primary_attestation_failure_retains_dependents_until_reconcile_retry(
    tmp_path: Path, runtime_state: CleanupState
) -> None:
    class FailedCleanupMeasurementBackend(RecordingBackend):
        async def launch(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
            handle, measurement = await super().launch(*args, **kwargs)
            handle.termination_states = [runtime_state, CleanupState.RELEASED]
            return handle, measurement

    fixture = make_runtime_fixture(with_writable_mount=True)
    backend = FailedCleanupMeasurementBackend()
    backend.measurement_mismatch = ("uid",)
    harness = RuntimeHarness(tmp_path, fixture, backend=backend)

    with pytest.raises(SandboxFault) as captured:
        await harness.manager.open(fixture.request)

    assert isinstance(captured.value.primary, SandboxAttestationError)
    assert captured.value.primary.code == "runtime_measurement_mismatch"
    assert captured.value.cleanup_receipt.state is CleanupState.QUARANTINED
    assert captured.value.cleanup_receipt.steps == (
        CleanupStepReceipt("runtime", runtime_state),
        CleanupStepReceipt(
            "workspace",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
        CleanupStepReceipt(
            "cache_holder",
            CleanupState.QUARANTINED,
            "dependent runtime cleanup incomplete",
        ),
        CleanupStepReceipt(
            "lease_record",
            CleanupState.QUARANTINED,
            "dependent cleanup incomplete",
        ),
    )
    assert backend.handles[0].terminate_calls == 1
    assert len(list(harness.workspace_root.iterdir())) == 1
    records = list(harness.lease_root.iterdir())
    assert len(records) == 2

    harness.clock.advance(minutes=5)
    receipts = await asyncio.wait_for(harness.manager.reconcile_stale(), 1)

    assert len(receipts) == 1
    assert receipts[0].steps == (
        CleanupStepReceipt("runtime", CleanupState.RELEASED),
        CleanupStepReceipt("workspace", CleanupState.RELEASED),
        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
        CleanupStepReceipt("lease_record", CleanupState.RELEASED),
    )
    assert backend.handles[0].terminate_calls == 2
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []


async def test_reconciliation_quarantines_foreign_runtime_identity_without_cleanup_effects(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    original = RuntimeHarness(tmp_path, fixture)
    lease = await original.manager.open(fixture.request)
    record_path = original.lease_root / f"{lease.lease_id}.json"
    record = dict(original.manager._read_lease_record(record_path))
    record["runtime_authority_id"] = "foreign-runtime"
    original.manager._write_lease_record(lease.lease_id, record)
    recovery_backend = ReconcileBackend()
    recovery = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=recovery_backend,
        docker_backend=None,
        random_bytes=DeterministicRandom(30_000),
    )
    original.clock.advance(minutes=5)
    original.manager._release_lease_owner_lock(lease.lease_id, unlink=False)

    receipts = await recovery.reconcile_stale()

    assert len(receipts) == 1
    assert receipts[0].state is CleanupState.QUARANTINED
    assert receipts[0].steps[0] == CleanupStepReceipt(
        "child_verifier",
        CleanupState.ALREADY_RELEASED,
    )
    assert {
        step.detail for step in receipts[0].steps[1:]
    } == {"stale_identity_uncertain"}
    assert recovery_backend.reconciled == []
    assert lease._materialized.workspace_path.exists()
    assert record_path.exists()
    assert lease.lease_id in recovery._lease_owner_locks
    assert await recovery.close() == ()
    assert recovery._lease_owner_locks == {}
    assert recovery._lease_root_fd is None
    successor = SandboxRuntimeManager(
        registries=fixture.registries,
        installed_authorities=fixture.authorities,
        materialization_store=original.store,
        lease_root=original.lease_root,
        process_backend=ReconcileBackend(),
        docker_backend=None,
        random_bytes=DeterministicRandom(31_000),
    )
    successor_receipts = await successor.reconcile_stale()
    assert len(successor_receipts) == 1
    assert all(
        step.detail != "live_owner"
        for step in successor_receipts[0].steps
    )
    assert await successor.close() == ()
    assert (await lease.close()).state is CleanupState.RELEASED


@pytest.mark.asyncio
async def test_lease_records_stay_on_admitted_inode_after_named_root_swap(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(runtime_install_root=tmp_path)
    harness = RuntimeHarness(tmp_path, fixture)
    admitted = tmp_path / "admitted-leases"
    replacement = tmp_path / "replacement-leases"
    harness.lease_root.rename(admitted)
    replacement.mkdir(mode=0o700)
    replacement.rename(harness.lease_root)

    harness.manager._write_lease_record(
        "lease-race",
        {
            "schema_version": "bb.rl.workspace-lease.v1",
            "lease_id": "lease-race",
        },
    )

    assert (admitted / "lease-race.json").is_file()
    assert list(harness.lease_root.iterdir()) == []
    owned_fd = harness.manager._lease_root_fd
    await harness.manager.close()
    assert owned_fd is not None
    with pytest.raises(OSError):
        os.fstat(owned_fd)
    await harness.manager.close()
    harness.store.close()


@pytest.mark.asyncio
async def test_process_preflight_failure_closes_workspace_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(runtime_install_root=tmp_path)
    plan = build_sandbox_execution_plan(
        fixture.request,
        fixture.registries,
        fixture.authorities,
    )
    workspace_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    identity = os.fstat(workspace_fd)
    context = SimpleNamespace(
        lease_id="lease-preflight",
        workspace_id="workspace-preflight",
        workspace_fd=workspace_fd,
        workspace_identity=(identity.st_dev, identity.st_ino),
    )

    def fail_snapshot(*_args: object, **_kwargs: object) -> None:
        raise SandboxLaunchError(
            "injected executable preflight failure",
            code="runtime_preflight_failed",
        )

    monkeypatch.setattr(
        sandbox_module,
        "_snapshot_installed_executable",
        fail_snapshot,
    )
    with pytest.raises(
        SandboxLaunchError,
        match="injected executable preflight failure",
    ):
        await TrustedProcessBackend().launch(
            plan,
            tmp_path,
            context=context,
        )
    with pytest.raises(OSError):
        os.fstat(workspace_fd)


def test_lease_constructor_closes_duplicate_when_identity_stat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(runtime_install_root=tmp_path)
    harness = RuntimeHarness(tmp_path, fixture)
    lease_fd = os.open(
        harness.lease_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    duplicated: list[int] = []
    real_dup = os.dup
    real_stat = os.stat

    def recording_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        duplicated.append(duplicate)
        return duplicate

    def failing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == harness.lease_root and kwargs.get("follow_symlinks") is False:
            raise OSError("injected lease root stat failure")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(sandbox_module.os, "dup", recording_dup)
    monkeypatch.setattr(sandbox_module.os, "stat", failing_stat)
    with pytest.raises(OSError, match="injected lease root stat failure"):
        SandboxRuntimeManager(
            registries=fixture.registries,
            installed_authorities=fixture.authorities,
            materialization_store=harness.store,
            lease_root=harness.lease_root,
            lease_root_fd=lease_fd,
            process_backend=harness.backend,
            docker_backend=harness.backend,
            random_bytes=DeterministicRandom(3_000),
        )
    assert len(duplicated) == 1
    with pytest.raises(OSError):
        os.fstat(duplicated[0])
    os.fstat(lease_fd)
    os.close(lease_fd)
