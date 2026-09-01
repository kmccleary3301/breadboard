from __future__ import annotations

import asyncio
from dataclasses import replace
import fcntl
import json
import os
import shlex
import signal
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from breadboard.rl.harness.materialization import (
    CleanupState,
    CleanupStepReceipt,
    IsolationDisposition,
)
from breadboard.rl.harness.runners.base import RunnerOpenRequest, RunnerTermination
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_IMPLEMENTATION_DIGEST,
    TERMINAL_RUNTIME_ABI,
    TERMINAL_TOOL_DEFINITIONS,
    TerminalLoopLimits,
    TerminalResponsesAdapter,
    TerminalRunRequest,
)
from breadboard.rl.harness.sandbox import (
    SandboxLaunchError,
    RuntimeLaunchContext,
    SandboxRuntimeManager,
    TrustedProcessBackend,
    TrustedProcessHandle,
    VerifierExecutionError,
    VerifierSnapshotError,
    WorkspaceStateError,
    WorkspaceStorageIdentity,
    build_sandbox_execution_plan,
    _sealed_repository_diff,
    _snapshot_installed_executable,
)
from tests.rl.harness.test_runner_terminal import (
    RecordingEventSink,
    ScriptedCancellationProbe,
    ScriptedPolicy,
    _call,
)
from tests.rl.harness.test_sandbox_runtime import RuntimeHarness
from tests.rl.harness.wp7_fixtures import (
    DeterministicRandom,
    make_runtime_fixture,
)
pytestmark = pytest.mark.local_process


RUNTIME_ABI = TERMINAL_RUNTIME_ABI
RUNNER_DIGEST = TERMINAL_IMPLEMENTATION_DIGEST

def _sealed_execution_supported() -> bool:
    required_fcntl = (
        "F_ADD_SEALS",
        "F_GET_SEALS",
        "F_SEAL_WRITE",
        "F_SEAL_SHRINK",
        "F_SEAL_GROW",
        "F_SEAL_SEAL",
    )
    return (
        sys.platform == "linux"
        and hasattr(os, "memfd_create")
        and hasattr(os, "MFD_ALLOW_SEALING")
        and all(hasattr(fcntl, name) for name in required_fcntl)
        and os.path.isdir("/proc/self/fd")
    )


requires_sealed_execution = pytest.mark.skipif(
    not _sealed_execution_supported(),
    reason="requires Linux sealed-memfd descriptor execution",
)


def test_sealed_repository_diff_includes_ignored_untracked_and_binary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "source"
    git_path = shutil.which("git")
    assert git_path is not None

    class PinnedGit:
        def __init__(self) -> None:
            self.fd = os.open(git_path, os.O_RDONLY)
            self.proc_fd_path = git_path
            self.digest = "sha256:" + "0" * 64

        def close(self) -> None:
            os.close(self.fd)

    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox._snapshot_installed_executable",
        lambda path, expected_digest: PinnedGit(),
    )
    repository.mkdir()

    def git(*arguments: str, cwd: Path = repository) -> str:
        completed = subprocess.run(
            ("git", *arguments), cwd=cwd, check=True, capture_output=True, text=True
        )
        return completed.stdout.strip()

    git("init", "--quiet")
    (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repository / ".gitattributes").write_text(
        "*.txt diff=hide filter=forge\n", encoding="utf-8"
    )
    (repository / "tracked.txt").write_text("before\n", encoding="utf-8")
    git("add", ".")
    git(
        "-c", "user.name=BreadBoard",
        "-c", "user.email=breadboard@example.invalid",
        "commit", "--quiet", "-m", "base",
    )
    base_commit = git("rev-parse", "HEAD")
    git("config", "diff.hide.command", "/usr/bin/true")
    git("config", "filter.forge.clean", "sed s/after/forged/")
    git("config", "filter.forge.smudge", "cat")
    (repository / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repository / "ignored.txt").write_text("included\n", encoding="utf-8")
    binary = b"\x00\x01\xffbinary\n"
    (repository / "new.bin").write_bytes(binary)
    raw_binary = b"\xffnon-UTF-8-without-NUL\n"
    (repository / "raw.bin").write_bytes(raw_binary)
    plan = type(
        "SealedDiffPlan", (),
        {
            "runtime": type(
                "Runtime", (), {"fixed_environment": (("PATH", os.environ["PATH"]),)}
            )(),
            "limits": type(
                "Limits", (),
                {"action_timeout_ms": 10_000, "artifact_bytes_each": 1024 * 1024},
            )(),
        },
    )()
    result = _sealed_repository_diff(
        repository=repository,
        scratch_directory=tmp_path,
        base_commit=base_commit,
        plan=plan,
    )
    reconstruction = tmp_path / "reconstruction"
    subprocess.run(
        ("git", "clone", "--quiet", str(repository), str(reconstruction)), check=True
    )
    subprocess.run(
        ("git", "apply", "--binary", "-"), cwd=reconstruction,
        input=result["stdout"].encode(), check=True,
    )
    assert (reconstruction / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert (reconstruction / "ignored.txt").read_text(encoding="utf-8") == "included\n"
    assert (reconstruction / "new.bin").read_bytes() == binary
    assert (reconstruction / "raw.bin").read_bytes() == raw_binary
    (repository / "nested" / ".git" / "objects").mkdir(parents=True)
    with pytest.raises(
        VerifierSnapshotError, match="embedded Git repository"
    ):
        _sealed_repository_diff(
            repository=repository,
            scratch_directory=tmp_path,
            base_commit=base_commit,
            plan=plan,
        )
    shutil.rmtree(repository / "nested")
    alternates = repository / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(exist_ok=True)
    alternates.write_text("/tmp/attacker-objects\n", encoding="utf-8")
    with pytest.raises(
        VerifierSnapshotError, match="external Git object authority"
    ):
        _sealed_repository_diff(
            repository=repository,
            scratch_directory=tmp_path,
            base_commit=base_commit,
            plan=plan,
        )

async def test_process_backend_binds_identity_recorder_before_base_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    plan = build_sandbox_execution_plan(
        fixture.request, fixture.registries, fixture.authorities
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    workspace_identity = os.fstat(workspace_fd)
    pinned_fd = os.open(os.devnull, os.O_RDONLY)

    class PinnedExecutable:
        source_path = plan.runtime.executable_path
        proc_fd_path = plan.runtime.executable_path
        digest = plan.runtime.measured_binary_digest
        size = 0
        fd = pinned_fd

        def close(self) -> None:
            os.close(self.fd)

    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox._snapshot_installed_executable",
        lambda path, expected_digest: PinnedExecutable(),
    )
    recorder_calls: list[tuple[str, object]] = []

    def recorder(resource_id: str, identity: object) -> None:
        recorder_calls.append((resource_id, identity))

    async def measure(handle: TrustedProcessHandle) -> None:
        assert handle._identity_recorder is recorder
        return None

    monkeypatch.setattr(
        TrustedProcessHandle, "measure_repository_base_commit", measure
    )

    async def publish(_: object) -> None:
        return None

    context = RuntimeLaunchContext(
        role="primary",
        lease_id="lease-recorder-order",
        workspace_id="workspace-recorder-order",
        epoch=1,
        storage=WorkspaceStorageIdentity(
            authority_id="test-storage",
            quota_enforced=False,
            quota_bytes=plan.resources.storage_bytes,
            owner_uid=os.getuid(),
            owner_gid=os.getgid(),
        ),
        snapshot_relative_path=None,
        result_relative_path=None,
        publish_prepared_identity=publish,
        workspace_fd=workspace_fd,
        workspace_identity=(workspace_identity.st_dev, workspace_identity.st_ino),
        owner_token="owner-token",
        record_process_identity=recorder,
    )
    handle, _ = await TrustedProcessBackend().launch(
        plan, workspace, context=context
    )

    assert handle._identity_recorder is recorder
    assert recorder_calls == []
    await handle.terminate()



async def test_run_shell_delegates_pinned_descriptor_as_workload_argv() -> None:
    handle = object.__new__(TrustedProcessHandle)
    handle._executable = type(
        "ScriptedPinnedExecutable",
        (),
        {"proc_fd_path": "/proc/self/fd/71"},
    )()
    handle.plan = type(
        "ScriptedPlan",
        (),
        {
            "runtime": type(
                "ScriptedRuntime",
                (),
                {"executable_path": "/catalog/runtime/shell"},
            )()
        },
    )()
    calls: list[tuple[tuple[str, ...], int, int]] = []
    expected = {"returncode": 0, "stdout": "delegated", "stderr": ""}

    async def scripted_run_argv(
        argv: tuple[str, ...], *, timeout_ms: int, output_limit: int
    ) -> dict[str, object]:
        calls.append((argv, timeout_ms, output_limit))
        return expected

    handle._run_pinned_argv = scripted_run_argv  # type: ignore[method-assign]

    result = await handle.run_shell(
        "printf delegated",
        timeout_ms=1_234,
        output_limit=5_678,
    )

    assert result is expected
    assert calls == [
        (("/proc/self/fd/71", "-lc", "printf delegated"), 1_234, 5_678)
    ]


async def test_run_argv_executes_requested_command_through_pinned_shell() -> None:
    handle = object.__new__(TrustedProcessHandle)
    handle._executable = type(
        "ScriptedPinnedExecutable",
        (),
        {"proc_fd_path": "/proc/self/fd/71"},
    )()
    handle._command_executable = None
    calls: list[tuple[tuple[str, ...], int, int]] = []
    expected = {"returncode": 0, "stdout": "ok\n", "stderr": ""}

    async def scripted_pinned_argv(
        argv: tuple[str, ...], *, timeout_ms: int, output_limit: int
    ) -> dict[str, object]:
        calls.append((argv, timeout_ms, output_limit))
        return expected

    handle._run_pinned_argv = scripted_pinned_argv  # type: ignore[method-assign]

    result = await handle.run_argv(
        ("/bin/echo", "ok"),
        timeout_ms=1_234,
        output_limit=5_678,
    )

    assert result is expected
    assert calls == [
        (
            (
                "/proc/self/fd/71",
                "-lc",
                'exec "$@"',
                "breadboard-execute",
                "/bin/echo",
                "ok",
            ),
            1_234,
            5_678,
        )
    ]


async def test_workspace_diff_uses_nested_repository_and_types_missing_git() -> None:
    handle = object.__new__(TrustedProcessHandle)
    handle._executable = type(
        "ScriptedPinnedExecutable",
        (),
        {"proc_fd_path": "/proc/self/fd/71"},
    )()
    handle._git_executable = "/usr/bin/git"
    handle.lease_id = "lease-workspace-diff"
    handle.plan = type(
        "ScriptedPlan",
        (),
        {
            "materialization_plan": type(
                "ScriptedMaterializationPlan",
                (),
                {
                    "entries": (
                        type(
                            "ScriptedEntry",
                            (),
                            {
                                "role": "repository",
                                "target_logical_path": "nested/repository",
                            },
                        )(),
                    )
                },
            )(),
            "limits": type(
                "ScriptedLimits",
                (),
                {"action_timeout_ms": 1_234, "observation_bytes": 5_678},
            )(),
        },
    )()
    calls: list[tuple[tuple[str, ...], int, int]] = []
    results = [
        {"returncode": 0, "stdout": "diff", "stderr": ""},
        {"returncode": 127, "stdout": "", "stderr": "git: not found"},
    ]

    async def scripted_run_argv(
        argv: tuple[str, ...], *, timeout_ms: int, output_limit: int
    ) -> dict[str, object]:
        calls.append((argv, timeout_ms, output_limit))
        return results.pop(0)

    handle._run_pinned_argv = scripted_run_argv  # type: ignore[method-assign]

    assert (await handle.workspace_diff())["stdout"] == "diff"
    assert calls[0] == (
        (
            "/proc/self/fd/71",
            "-lc",
            'exec "$2" -C "$1" diff --no-ext-diff --binary',
            "breadboard-workspace-diff",
            "nested/repository",
            "/usr/bin/git",
        ),
        1_234,
        5_678,
    )
    with pytest.raises(SandboxLaunchError) as captured:
        await handle.workspace_diff()
    assert captured.value.code == "runtime_unsupported"


@requires_sealed_execution
async def test_missing_host_git_refuses_before_trusted_process_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True,
        runtime_install_root=tmp_path / "runtime",
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox.shutil.which",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.code == "runtime_unsupported"


async def test_unsupported_host_refuses_before_subprocess_recorder_or_workload_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if _sealed_execution_supported():
        pytest.skip("unsupported-host contract is exercised only without sealed execution")
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path
    )
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    calls: list[str] = []

    async def forbidden_subprocess(*args: object, **kwargs: object) -> None:
        calls.append("subprocess")
        raise AssertionError("unsupported launch attempted subprocess creation")

    def forbidden_recorder(*args: object, **kwargs: object) -> None:
        calls.append("recorder")
        raise AssertionError("unsupported launch attempted durable recording")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_subprocess)
    monkeypatch.setattr(
        harness.manager,
        "_record_process_identity",
        forbidden_recorder,
    )

    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.code == "runtime_unsupported"
    assert calls == []
    assert not any(
        path.name == "workload-effect"
        for path in harness.workspace_root.rglob("*")
    )


@requires_sealed_execution
@pytest.mark.parametrize("mutation", ["rename-replacement", "same-inode"])
async def test_pinned_shell_executes_admitted_bytes_after_source_mutation(
    tmp_path: Path, mutation: str
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    runtime_path = Path(
        next(
            runtime.executable_path
            for runtime in fixture.authorities.runtimes
            if runtime.runtime_id == fixture.plan.sandbox.runtime_id
        )
    )
    admitted_bytes = runtime_path.read_bytes()
    original_identity = runtime_path.stat()
    primary = await harness.manager.open(fixture.request)

    replacement_bytes = Path("/usr/bin/false").read_bytes()
    assert replacement_bytes != admitted_bytes
    if mutation == "rename-replacement":
        replacement = runtime_path.with_name("replacement")
        replacement.write_bytes(replacement_bytes)
        replacement.chmod(0o500)
        os.replace(replacement, runtime_path)
        mutated_identity = runtime_path.stat()
        assert (mutated_identity.st_dev, mutated_identity.st_ino) != (
            original_identity.st_dev,
            original_identity.st_ino,
        )
    else:
        runtime_path.write_bytes(replacement_bytes)
        runtime_path.chmod(0o500)
        mutated_identity = runtime_path.stat()
        assert (mutated_identity.st_dev, mutated_identity.st_ino) == (
            original_identity.st_dev,
            original_identity.st_ino,
        )
    assert runtime_path.read_bytes() == replacement_bytes

    result = await primary._runtime.run_shell(
        "printf admitted-snapshot",
        timeout_ms=1_000,
        output_limit=4_096,
    )

    assert result["returncode"] == 0
    assert result["stdout"] == "admitted-snapshot"
    assert (await primary.close()).state is CleanupState.RELEASED


@requires_sealed_execution
async def test_pinned_shell_bootstrap_does_not_require_inherited_marker_fd(
    tmp_path: Path,
) -> None:
    held_descriptors: list[int] = []
    try:
        while not held_descriptors or held_descriptors[-1] < 32:
            held_descriptors.append(os.open("/dev/null", os.O_RDONLY))
        fixture = make_runtime_fixture(
            with_writable_mount=True,
            runtime_install_root=tmp_path / "runtime",
        )
        (tmp_path / "harness").mkdir()
        harness = RuntimeHarness(tmp_path / "harness", fixture)
        harness.manager.process_backend = TrustedProcessBackend()
        primary = await harness.manager.open(fixture.request)
        try:
            result = await primary._runtime.run_shell(
                "printf high-descriptor-bootstrap",
                timeout_ms=1_000,
                output_limit=4_096,
            )
        finally:
            cleanup = await primary.close()
    finally:
        for descriptor in held_descriptors:
            os.close(descriptor)

    assert result["returncode"] == 0
    assert result["stdout"] == "high-descriptor-bootstrap"
    assert cleanup.state is CleanupState.RELEASED

@requires_sealed_execution
async def test_pinned_verifier_executes_admitted_bytes_after_source_replacement(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True,
        runtime_install_root=tmp_path / "runtime",
    )
    (tmp_path / "harness").mkdir()
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    verifier_path = tmp_path / "verifier"
    verifier_path.write_bytes(b"#!/bin/sh\nprintf admitted-verifier\n")
    verifier_path.chmod(0o500)
    verifier_digest = "sha256:" + __import__("hashlib").sha256(
        verifier_path.read_bytes()
    ).hexdigest()
    pinned = _snapshot_installed_executable(str(verifier_path), verifier_digest)
    primary._runtime._command_executable = pinned
    replacement = tmp_path / "replacement-verifier"
    replacement.write_bytes(b"#!/bin/sh\nprintf attacker-controlled\n")
    replacement.chmod(0o500)
    os.replace(replacement, verifier_path)

    result = await primary._runtime.run_argv(
        (str(verifier_path),),
        timeout_ms=1_000,
        output_limit=4_096,
    )

    assert result["returncode"] == 0
    assert result["stdout"] == "admitted-verifier"
    assert (await primary.close()).state is CleanupState.RELEASED
    assert pinned.closed is True

@requires_sealed_execution
async def test_pinned_binary_verifier_preserves_direct_execution(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True,
        runtime_install_root=tmp_path / "runtime",
    )
    (tmp_path / "harness").mkdir()
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    verifier_path = tmp_path / "binary-verifier"
    shutil.copyfile(Path(os.path.realpath("/bin/sh")), verifier_path)
    verifier_path.chmod(0o500)
    verifier_digest = "sha256:" + __import__("hashlib").sha256(
        verifier_path.read_bytes()
    ).hexdigest()
    pinned = _snapshot_installed_executable(str(verifier_path), verifier_digest)
    primary._runtime._command_executable = pinned
    replacement = tmp_path / "replacement-binary-verifier"
    replacement.write_bytes(b"#!/bin/sh\nprintf attacker-controlled\n")
    replacement.chmod(0o500)
    os.replace(replacement, verifier_path)

    result = await primary._runtime.run_argv(
        (str(verifier_path), "-c", "printf admitted-binary"),
        timeout_ms=1_000,
        output_limit=4_096,
    )

    assert result["returncode"] == 0
    assert result["stdout"] == "admitted-binary"
    assert (await primary.close()).state is CleanupState.RELEASED
    assert pinned.closed is True


@requires_sealed_execution
async def test_process_lease_execute_runs_requested_argv(tmp_path: Path) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True,
        runtime_install_root=tmp_path / "runtime",
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)

    result = await primary.execute(("/usr/bin/printf", "requested-argv"))

    assert result["returncode"] == 0
    assert result["stdout"] == "requested-argv"
    assert (await primary.close()).state is CleanupState.RELEASED


@requires_sealed_execution
async def test_symlinked_runtime_ancestor_is_rejected_before_child_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(runtime_install_root=tmp_path / "real")
    real_runtime = next(
        runtime
        for runtime in fixture.authorities.runtimes
        if runtime.runtime_id == fixture.plan.sandbox.runtime_id
    )
    alias = tmp_path / "alias"
    alias.symlink_to(Path(real_runtime.executable_path).parent, target_is_directory=True)
    aliased_runtime = replace(
        real_runtime, executable_path=str(alias / Path(real_runtime.executable_path).name)
    )
    authorities = replace(
        fixture.authorities,
        runtimes=tuple(
            aliased_runtime if runtime.runtime_id == aliased_runtime.runtime_id else runtime
            for runtime in fixture.authorities.runtimes
        ),
    )
    harness = RuntimeHarness(
        tmp_path / "harness", replace(fixture, authorities=authorities)
    )
    harness.manager.process_backend = TrustedProcessBackend()
    subprocess_calls: list[tuple[object, ...]] = []

    async def forbidden_subprocess(*args: object, **kwargs: object) -> None:
        subprocess_calls.append(args)
        raise AssertionError("symlinked authority attempted child creation")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_subprocess)

    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)

    assert captured.value.code == "runtime_preflight_failed"
    assert subprocess_calls == []


@requires_sealed_execution
async def test_catalog_argv0_and_proc_exe_bind_different_objects_at_private_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    runtime_path = next(
        runtime.executable_path
        for runtime in fixture.authorities.runtimes
        if runtime.runtime_id == fixture.plan.sandbox.runtime_id
    )
    observed: dict[str, object] = {}
    original = harness.manager._record_process_identity

    def inspect_stopped_process(
        lease_id: str, resource_id: str, identity: dict[str, object] | None
    ) -> None:
        assert identity is not None
        pid = int(identity["process_pid"])
        observed["cmdline"] = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        observed["exe"] = os.readlink(f"/proc/{pid}/exe")
        observed["state"] = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        original(lease_id, resource_id, identity)

    monkeypatch.setattr(
        harness.manager, "_record_process_identity", inspect_stopped_process
    )
    result = await primary._runtime.run_shell(
        "printf argv-proof", timeout_ms=1_000, output_limit=4_096
    )

    assert observed["cmdline"][0].decode() == runtime_path
    assert observed["state"] in {"T", "t"}
    assert observed["exe"].startswith("/memfd:breadboard-runtime")
    assert result["stdout"] == "argv-proof"
    assert (await primary.close()).state is CleanupState.RELEASED


@requires_sealed_execution
async def test_cancellation_at_private_barrier_reaps_group_and_handle_remains_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    original = harness.manager._record_process_identity
    attempted_groups: list[int] = []

    def cancel_at_recorder(
        lease_id: str, resource_id: str, identity: dict[str, object] | None
    ) -> None:
        assert identity is not None
        attempted_groups.append(int(identity["process_group_id"]))
        raise asyncio.CancelledError

    monkeypatch.setattr(harness.manager, "_record_process_identity", cancel_at_recorder)
    with pytest.raises(asyncio.CancelledError):
        await primary._runtime.run_shell(
            ": > work/forbidden-effect",
            timeout_ms=1_000,
            output_limit=4_096,
        )

    assert len(attempted_groups) == 1
    with pytest.raises(ProcessLookupError):
        os.killpg(attempted_groups[0], 0)
    assert not (
        primary._materialized.workspace_path / "work/forbidden-effect"
    ).exists()

    monkeypatch.setattr(harness.manager, "_record_process_identity", original)
    later = await primary._runtime.run_shell(
        "printf later", timeout_ms=1_000, output_limit=4_096
    )
    assert later["stdout"] == "later"
    assert (await primary.close()).state is CleanupState.RELEASED






@requires_sealed_execution
async def test_terminate_racing_barrier_fences_launch_and_closes_snapshot_fd_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    handle = primary._runtime
    executable_fd = handle._executable.fd
    recorder_entered = asyncio.Event()
    termination_tasks: list[asyncio.Task[object]] = []
    original = harness.manager._record_process_identity

    def terminate_from_barrier(
        lease_id: str, resource_id: str, identity: dict[str, object] | None
    ) -> None:
        original(lease_id, resource_id, identity)
        recorder_entered.set()
        termination_tasks.append(asyncio.create_task(handle.terminate()))

    monkeypatch.setattr(
        harness.manager, "_record_process_identity", terminate_from_barrier
    )
    first = asyncio.create_task(
        handle.run_shell(
            "sleep 10; : > work/late-effect",
            timeout_ms=20_000,
            output_limit=4_096,
        )
    )
    await asyncio.wait_for(recorder_entered.wait(), 1)
    await asyncio.sleep(0)

    with pytest.raises(WorkspaceStateError) as captured:
        await handle.run_shell(
            "printf forbidden",
            timeout_ms=1_000,
            output_limit=4_096,
        )
    assert captured.value.code == "lease_not_active"

    assert len(termination_tasks) == 1
    await asyncio.wait_for(termination_tasks[0], 2)
    first_result = await asyncio.wait_for(first, 2)
    assert first_result["returncode"] == -signal.SIGKILL
    assert not (primary._materialized.workspace_path / "work/late-effect").exists()
    assert handle._executable.closed is True
    with pytest.raises(OSError):
        os.fstat(executable_fd)
    await handle.terminate()
    with pytest.raises(OSError):
        os.fstat(executable_fd)
    assert (await primary.close()).state is CleanupState.RELEASED


@requires_sealed_execution
async def test_real_process_plan_runs_through_wp5_port_seals_snapshot_and_cleans(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True,
        runner_adapter_id=TERMINAL_ADAPTER_ID,
        runner_runtime_abi=RUNTIME_ABI,
        runner_implementation_digest=RUNNER_DIGEST,
    )
    harness = RuntimeHarness(tmp_path, fixture)
    real_backend = TrustedProcessBackend()
    harness.manager.process_backend = real_backend
    primary = await harness.manager.open(fixture.request)
    policy = ScriptedPolicy(
        [
            {
                "output": [
                    _call(
                        "write",
                        "write_file",
                        json.dumps(
                            {"path": "work/candidate.txt", "content": "candidate"}
                        ),
                    ),
                    _call(
                        "shell",
                        "shell",
                        json.dumps(
                            {
                                "command": "cat work/candidate.txt > work/copied.txt && printf shell-ok",
                                "timeout_seconds": 2,
                            }
                        ),
                    ),
                    _call(
                        "read",
                        "read_file",
                        json.dumps({"path": "work/copied.txt"}),
                    ),
                    _call("submit", "submit", json.dumps({"result": "done"})),
                ]
            }
        ]
    )
    cancellation = ScriptedCancellationProbe()
    events = RecordingEventSink()
    adapter = TerminalResponsesAdapter(RUNTIME_ABI)
    session = await adapter.open(
        RunnerOpenRequest(fixture.request.episode_id, fixture.plan),
        policy=policy,
        workspace=primary.runner_workspace,
        cancellation=cancellation,
        events=events,
    )
    run_request = TerminalRunRequest(
        responses_create_params={"input": "solve"},
        tools=TERMINAL_TOOL_DEFINITIONS,
        limits=TerminalLoopLimits(
            max_turns=fixture.plan.effective_capabilities.limits.max_turns,
            action_timeout_seconds=2,
            max_observation_chars=fixture.plan.effective_capabilities.limits.observation_bytes,
        ),
    )

    result = await session.run(run_request)

    assert result.termination is RunnerTermination.SUBMITTED
    assert result.effective_plan_digest == fixture.plan.canonical_digest()
    assert (await primary.runner_workspace.read_text("work/copied.txt"))["content"] == "candidate"
    assert primary.measurement.isolation_disposition is IsolationDisposition.TRUSTED_PROCESS
    assert primary.measurement.isolated is False
    assert primary.measurement.reward_eligible is False
    snapshot = await primary.seal_for_verifier()
    immutable = harness.cache_root / "snapshot-objects" / snapshot.root_digest.removeprefix(
        "sha256:"
    )
    assert (immutable / "work" / "candidate.txt").read_bytes() == b"candidate"
    assert (immutable / "work" / "copied.txt").read_bytes() == b"candidate"

    verifier = await harness.manager.open_verifier(primary, snapshot)
    assert verifier.measurement.isolation_disposition is IsolationDisposition.TRUSTED_PROCESS
    with pytest.raises(VerifierExecutionError) as captured:
        await verifier.execute()
    assert captured.value.code == "verifier_result_malformed"
    child = await verifier.close()
    assert child.state is CleanupState.RELEASED
    parent = await primary.close()
    assert parent.state is CleanupState.RELEASED
    assert await primary.close() == parent
    assert await harness.manager.close() == ()
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []



@requires_sealed_execution
async def test_real_process_leader_exit_keeps_exact_descendant_cleanup_authority(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    descendant_command = (
        "for descriptor in /proc/self/fd/*; do "
        "descriptor=${descriptor##*/}; "
        "case \"$descriptor\" in 0|1|2) ;; "
        "*) eval \"exec ${descriptor}>&-\" ;; esac; "
        "done; "
        "printf '%s' \"$$\" > work/.descendant.tmp && "
        "mv work/.descendant.tmp work/descendant.pid; "
        "exec 1>&- 2>&-; "
        "sleep 10"
    )
    command = (
        f"/bin/sh -c {shlex.quote(descendant_command)} & "
        "while [ ! -f work/descendant.pid ]; do :; done"
    )
    descendant_pid: int | None = None
    try:
        await primary.runner_workspace.run_shell(command, timeout=2)
        descendant = await primary.runner_workspace.read_text(
            "work/descendant.pid"
        )
        descendant_pid = int(descendant["content"])
        async with asyncio.timeout(1):
            while True:
                try:
                    os.kill(descendant_pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
        receipt = await primary.close()
        assert receipt.state is CleanupState.RELEASED
        assert await harness.manager.close() == ()
    finally:
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

@requires_sealed_execution
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_real_process_closed_stream_timeout_or_cancellation_kills_descendant(
    tmp_path: Path, mode: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    ready_fifo = tmp_path / "descendant-ready.fifo"
    os.mkfifo(ready_fifo)
    ready_fd = os.open(ready_fifo, os.O_RDWR | os.O_NONBLOCK)
    quoted_ready = shlex.quote(str(ready_fifo))
    descendant_command = (
        "trap '' TERM; "
        "printf '%s' \"$$\" > work/.descendant.tmp && "
        "mv work/.descendant.tmp work/descendant.pid; "
        "exec 1>&- 2>&-; "
        "sleep 10; "
        "printf late > work/late"
    )
    command = (
        "printf '%s' \"$$\" > work/.leader.tmp && "
        "mv work/.leader.tmp work/leader.pid; "
        f"/bin/sh -c {shlex.quote(descendant_command)} & "
        "descendant=$!; "
        "while [ ! -f work/descendant.pid ]; do :; done; "
        "printf '%s' \"$descendant\" > work/.spawned.tmp && "
        "mv work/.spawned.tmp work/spawned.pid; "
        f"printf ready > {quoted_ready}; "
        "exec 1>&- 2>&-; "
        "wait \"$descendant\""
    )
    descendant_pid: int | None = None
    receipt = None
    try:
        action = asyncio.create_task(
            primary.runner_workspace.run_shell(command, timeout=1)
        )
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()

        def signal_ready() -> None:
            if not ready.done():
                ready.set_result(None)

        loop.add_reader(ready_fd, signal_ready)
        try:
            await asyncio.wait_for(ready, 1)
            assert os.read(ready_fd, 5) == b"ready"
        finally:
            loop.remove_reader(ready_fd)

        started = loop.time()
        if mode == "cancel":
            action.cancel()
            with pytest.raises(asyncio.CancelledError):
                async with asyncio.timeout(3):
                    await action
        else:
            with pytest.raises(SandboxLaunchError) as captured:
                async with asyncio.timeout(3):
                    await action
            assert captured.value.code == "runtime_launch_failed"
            assert captured.value.lease_id == primary.lease_id
        assert loop.time() - started < 3

        spawned = await primary.runner_workspace.read_text("work/spawned.pid")
        descendant = await primary.runner_workspace.read_text("work/descendant.pid")
        descendant_pid = int(descendant["content"])
        assert descendant_pid == int(spawned["content"])

        async with asyncio.timeout(1):
            while True:
                try:
                    os.kill(descendant_pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)

        with pytest.raises(FileNotFoundError):
            await primary.runner_workspace.read_text("work/late")
        receipt = await primary.close()
        assert receipt.lease_id == primary.lease_id
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
        assert await primary.close() == receipt
        assert await harness.manager.close() == ()
        assert list(harness.workspace_root.iterdir()) == []
        assert list(harness.lease_root.iterdir()) == []
    finally:
        os.close(ready_fd)
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if receipt is None:
            async with asyncio.timeout(2):
                await primary.close()


@requires_sealed_execution
async def test_trusted_process_handle_enforces_exact_500ms_deadline_and_cleans_descendant(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    handle = primary._runtime
    output_limit = fixture.plan.effective_capabilities.limits.observation_bytes
    loop = asyncio.get_running_loop()

    under_started = loop.time()
    under = await handle.run_shell(
        "sleep 0.05; printf under > work/under",
        timeout_ms=500,
        output_limit=output_limit,
    )
    under_elapsed = loop.time() - under_started
    assert under["returncode"] == 0
    assert 0 <= under_elapsed < 0.75
    assert (await primary.runner_workspace.read_text("work/under"))["content"] == "under"

    descendant_command = (
        "trap '' TERM; "
        "printf '%s' \"$$\" > work/.deadline-child.tmp && "
        "mv work/.deadline-child.tmp work/deadline-child.pid; "
        "exec 1>&- 2>&-; "
        "sleep 2; "
        "printf late > work/deadline-late"
    )
    over_command = (
        f"/bin/sh -c {shlex.quote(descendant_command)} & "
        "child=$!; "
        "while [ ! -f work/deadline-child.pid ]; do :; done; "
        "printf '%s' \"$child\" > work/.deadline-spawned.tmp && "
        "mv work/.deadline-spawned.tmp work/deadline-spawned.pid; "
        "exec 1>&- 2>&-; "
        "wait \"$child\""
    )
    over_started = loop.time()
    with pytest.raises(SandboxLaunchError) as captured:
        async with asyncio.timeout(2):
            await handle.run_shell(
                over_command,
                timeout_ms=500,
                output_limit=output_limit,
            )
    over_elapsed = loop.time() - over_started

    assert captured.value.code == "runtime_launch_failed"
    assert 0.4 <= over_elapsed < 2
    assert under_elapsed < over_elapsed
    spawned = await primary.runner_workspace.read_text("work/deadline-spawned.pid")
    descendant = await primary.runner_workspace.read_text("work/deadline-child.pid")
    descendant_pid = int(descendant["content"])
    assert descendant_pid == int(spawned["content"])
    async with asyncio.timeout(1):
        while True:
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.01)
    with pytest.raises(FileNotFoundError):
        await primary.runner_workspace.read_text("work/deadline-late")

    receipt = await primary.close()
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


@requires_sealed_execution
@pytest.mark.parametrize(
    "identity_mutation",
    ["matching", "start", "pgid", "session", "cgroup"],
)
async def test_real_process_restart_never_signals_from_stale_lease_record(
    tmp_path: Path, identity_mutation: str
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path
    )
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    ready_fifo = tmp_path / "restart-ready.fifo"
    os.mkfifo(ready_fifo)
    ready_fd = os.open(ready_fifo, os.O_RDWR | os.O_NONBLOCK)
    command = (
        "trap '' TERM; "
        "printf '%s' \"$$\" > work/.action.tmp && "
        "mv work/.action.tmp work/action.pid; "
        f"printf ready > {shlex.quote(str(ready_fifo))}; "
        "exec 1>&- 2>&-; "
        "sleep 10"
    )
    action = asyncio.create_task(
        primary.runner_workspace.run_shell(command, timeout=2)
    )
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()

    def signal_ready() -> None:
        if not ready.done():
            ready.set_result(None)

    loop.add_reader(ready_fd, signal_ready)
    process_pid: int | None = None
    try:
        try:
            await asyncio.wait_for(ready, 1)
            assert os.read(ready_fd, 5) == b"ready"
        finally:
            loop.remove_reader(ready_fd)

        record_path = harness.lease_root / f"{primary.lease_id}.json"
        record = dict(harness.manager._read_lease_record(record_path))
        assert len(record["process_identities"]) == 1
        identity = dict(record["process_identities"][0])
        process_pid = identity["process_pid"]
        process_group = identity["process_group_id"]
        assert identity["resource_id"] == f"process-group-{process_group}"
        assert process_pid == process_group == os.getpgid(process_pid)
        assert identity["process_session_id"] == process_group
        assert identity["process_start_identity"].startswith("linux-proc-start:")
        assert identity["process_cgroup_identity"].startswith("sha256:")
        assert record["runtime_resource_id"] == f"process-group-{primary.lease_id}"

        if identity_mutation == "start":
            identity["process_start_identity"] = "linux-proc-start:forged"
        elif identity_mutation == "pgid":
            identity["process_group_id"] = process_group + 1
        elif identity_mutation == "session":
            identity["process_session_id"] = process_group + 1
        elif identity_mutation == "cgroup":
            identity["process_cgroup_identity"] = "sha256:" + "0" * 64
        record["process_identities"] = [identity]
        if identity_mutation != "matching":
            harness.manager._write_lease_record(primary.lease_id, record)

        installed_path = Path(
            next(
                runtime.executable_path
                for runtime in fixture.authorities.runtimes
                if runtime.runtime_id == fixture.plan.sandbox.runtime_id
            )
        )
        replacement = installed_path.with_name("post-crash-replacement")
        replacement.write_bytes(Path("/usr/bin/false").read_bytes())
        replacement.chmod(0o500)
        os.replace(replacement, installed_path)

        recovery = SandboxRuntimeManager(
            registries=fixture.registries,
            installed_authorities=fixture.authorities,
            materialization_store=harness.store,
            lease_root=harness.lease_root,
            process_backend=TrustedProcessBackend(),
            docker_backend=None,
            random_bytes=DeterministicRandom(50_000),
        )
        harness.clock.advance(minutes=5)
        receipts = await asyncio.wait_for(recovery.reconcile_stale(), 2)
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.lease_id == primary.lease_id

        assert receipt.steps == (
            CleanupStepReceipt(
                "child_verifier",
                CleanupState.ALREADY_RELEASED,
            ),
            CleanupStepReceipt(
                "runtime", CleanupState.QUARANTINED, "stale_identity_uncertain"
            ),
            CleanupStepReceipt(
                "workspace", CleanupState.QUARANTINED, "stale_identity_uncertain"
            ),
            CleanupStepReceipt(
                "cache_holder", CleanupState.QUARANTINED, "stale_identity_uncertain"
            ),
            CleanupStepReceipt(
                "lease_record", CleanupState.QUARANTINED, "stale_identity_uncertain"
            ),
        )
        os.kill(process_pid, 0)
        action.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(action, 1)
        async with asyncio.timeout(1):
            while True:
                try:
                    os.kill(process_pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
        close_receipt = await primary.close()
        assert close_receipt.state is CleanupState.RELEASED
        assert not record_path.exists()
        assert list(harness.workspace_root.iterdir()) == []
    finally:
        os.close(ready_fd)
        if not action.done():
            action.cancel()
            await asyncio.gather(action, return_exceptions=True)
        if process_pid is not None:
            try:
                os.kill(process_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@requires_sealed_execution
async def test_concurrent_trusted_actions_persist_distinct_identities_and_reconcile_independently(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    record_path = harness.lease_root / f"{primary.lease_id}.json"

    first = asyncio.create_task(
        primary.runner_workspace.run_shell(
            "trap '' TERM; : > work/first-ready; exec 1>&- 2>&-; sleep 10",
            timeout=2,
        )
    )
    second = asyncio.create_task(
        primary.runner_workspace.run_shell(
            "trap '' TERM; : > work/second-ready; exec 1>&- 2>&-; sleep 10",
            timeout=2,
        )
    )
    process_ids: tuple[int, int] | None = None
    try:
        async with asyncio.timeout(1):
            while True:
                record = dict(harness.manager._read_lease_record(record_path))
                identities = tuple(record.get("process_identities", ()))
                if (
                    len(identities) == 2
                    and (primary._materialized.workspace_path / "work/first-ready").exists()
                    and (primary._materialized.workspace_path / "work/second-ready").exists()
                ):
                    break
                await asyncio.sleep(0.005)

        resource_ids = tuple(identity["resource_id"] for identity in identities)
        process_ids = tuple(identity["process_pid"] for identity in identities)
        assert resource_ids == tuple(sorted(resource_ids))
        assert len(set(resource_ids)) == len(set(process_ids)) == 2
        assert all(
            identity["process_pid"] == identity["process_group_id"]
            and identity["resource_id"]
            == f"process-group-{identity['process_group_id']}"
            for identity in identities
        )

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 1)
        async with asyncio.timeout(1):
            while True:
                surviving = tuple(
                    harness.manager._read_lease_record(record_path).get(
                        "process_identities", ()
                    )
                )
                if len(surviving) == 1:
                    break
                await asyncio.sleep(0.005)
        surviving_pid = surviving[0]["process_pid"]
        removed_pid = next(pid for pid in process_ids if pid != surviving_pid)
        os.kill(surviving_pid, 0)
        with pytest.raises(ProcessLookupError):
            os.kill(removed_pid, 0)

        recovery = SandboxRuntimeManager(
            registries=fixture.registries,
            installed_authorities=fixture.authorities,
            materialization_store=harness.store,
            lease_root=harness.lease_root,
            process_backend=TrustedProcessBackend(),
            docker_backend=None,
            random_bytes=DeterministicRandom(60_000),
        )
        harness.clock.advance(minutes=5)
        receipts = await asyncio.wait_for(recovery.reconcile_stale(), 2)
        assert len(receipts) == 1
        assert receipts[0].steps == (
            CleanupStepReceipt("runtime", CleanupState.RELEASED),
            CleanupStepReceipt("workspace", CleanupState.RELEASED),
            CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
            CleanupStepReceipt("lease_record", CleanupState.RELEASED),
        )
        result = await asyncio.wait_for(second, 1)
        assert result["returncode"] == -signal.SIGKILL
        assert not record_path.exists()
    finally:
        for action in (first, second):
            if not action.done():
                action.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        if process_ids is not None:
            for pid in process_ids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@requires_sealed_execution
async def test_identity_persistence_failure_kills_suspended_action_before_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    record_path = harness.lease_root / f"{primary.lease_id}.json"
    attempted: list[tuple[str, dict[str, object] | None]] = []

    def fail_persistence(
        lease_id: str, resource_id: str, identity: dict[str, object] | None
    ) -> None:
        assert lease_id == primary.lease_id
        attempted.append((resource_id, identity))
        raise OSError("injected durable write failure")

    monkeypatch.setattr(harness.manager, "_record_process_identity", fail_persistence)

    with pytest.raises(OSError, match="injected durable write failure"):
        await asyncio.wait_for(
            primary.runner_workspace.run_shell(
                ": > work/effect-after-resume",
                timeout=1,
            ),
            2,
        )

    assert len(attempted) == 1
    resource_id, identity = attempted[0]
    assert identity is not None
    assert resource_id == f"process-group-{identity['process_group_id']}"
    assert not (
        primary._materialized.workspace_path / "work/effect-after-resume"
    ).exists()
    record = harness.manager._read_lease_record(record_path)
    assert tuple(record.get("process_identities", ())) == ()
    assert (await primary.close()).state is CleanupState.RELEASED
    assert list(harness.workspace_root.iterdir()) == []
    assert list(harness.lease_root.iterdir()) == []
