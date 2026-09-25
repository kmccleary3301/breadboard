from __future__ import annotations

import contextlib
import asyncio
from dataclasses import replace
import errno
import fcntl
import hashlib
import json
import os
import socket
import shlex
import signal
import struct
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Callable

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
    RuntimeContainment,
    SandboxFault,
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
from breadboard.rl.harness.lease_envelope import _spawn_one
from breadboard.rl.harness import lease_envelope
from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.composition import HmacSha256ReceiptAuthenticator
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

def _sealed_execution_refusal() -> str | None:
    if not (
        sys.platform == "linux"
        and hasattr(os, "memfd_create")
        and hasattr(os, "MFD_ALLOW_SEALING")
        and os.path.isdir("/proc/self/fd")
    ):
        return "runtime_unsupported: requires Linux sealed-memfd descriptor execution"
    try:
        lease_envelope.preflight_host_containment()
    except lease_envelope.EnvelopeUnsupportedHostError as exc:
        return f"runtime_unsupported: {exc}"
    return None


_sealed_refusal = _sealed_execution_refusal()
requires_sealed_execution = pytest.mark.skipif(
    _sealed_refusal is not None,
    reason=_sealed_refusal or "",
)

@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux namespaces")
@pytest.mark.parametrize("denied_map", ("setgroups", "uid_map"))
async def test_denied_user_namespace_mapping_refuses_lease_before_child_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied_map: str
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path / "runtime"
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()

    def unshare(flags: int) -> None:
        if not flags & lease_envelope._CLONE_NEWUSER:
            raise OSError(errno.EPERM, "privileged namespaces unavailable")

    def write_map(path: str, value: str) -> None:
        if path.endswith("/" + denied_map):
            raise OSError(errno.EACCES, "namespace mapping denied", path)

    monkeypatch.setattr(lease_envelope, "_unshare", unshare)
    monkeypatch.setattr(lease_envelope, "_write_map", write_map)
    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)
    assert captured.value.code == "runtime_unsupported"
    assert "namespace" in str(captured.value).lower()
    assert list(harness.workspace_root.iterdir()) == []



@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux namespaces")
def test_preflight_fork_exhaustion_is_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exhausted() -> int:
        raise BlockingIOError(errno.EAGAIN, "process quota exhausted")

    monkeypatch.setattr(lease_envelope.os, "fork", exhausted)
    with pytest.raises(lease_envelope.EnvelopeLaunchError) as captured:
        lease_envelope.preflight_host_containment()
    assert captured.value.code == "envelope_resources_exhausted"
    assert captured.value.errno == errno.EAGAIN


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux namespaces")
def test_launch_envelope_scratch_identity_mismatch_fails_before_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fork_called() -> int:
        pytest.fail("os.fork was called despite scratch identity mismatch")

    monkeypatch.setattr(lease_envelope.os, "fork", fork_called)

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()

    workspace_fd = os.open(workspace_dir, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    scratch_fd = os.open(scratch_dir, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        real_stat = os.fstat(scratch_fd)
        mismatched_identity = (real_stat.st_dev, real_stat.st_ino + 1)
        authenticator = HmacSha256ReceiptAuthenticator(b"k" * 32)
        with pytest.raises(lease_envelope.EnvelopeLaunchError) as exc_info:
            lease_envelope.launch_envelope(
                lease_id="test-lease-id",
                runtime_id="test-runtime-id",
                workspace=workspace_dir,
                scratch=scratch_dir,
                workspace_fd=workspace_fd,
                scratch_fd=scratch_fd,
                scratch_identity=mismatched_identity,
                authenticator=authenticator,
                tmpfs_size_bytes=1024 * 1024,
            )
        assert exc_info.value.code == "envelope_scratch_mismatch"
        assert exc_info.value.phase == "scratch_verify"
    finally:
        os.close(workspace_fd)
        os.close(scratch_fd)

@requires_sealed_execution
async def test_mount_denial_is_not_namespace_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path / "runtime"
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    def denied(*args: object) -> None:
        raise PermissionError(errno.EACCES, "read-only mount denied by policy")

    monkeypatch.setattr(lease_envelope, "_setup_mount_view", denied)
    with pytest.raises(SandboxLaunchError) as captured:
        await harness.manager.open(fixture.request)
    assert captured.value.code == "envelope_mount_denied"
    assert "mount_view" in str(captured.value)
    assert list(harness.workspace_root.iterdir()) == []


def test_envelope_rejects_non_string_environment_before_fork() -> None:
    message = {
        "fd_count": 0,
        "status_index": 0,
        "stdio_indices": [0, 0, 0],
        "cwd_index": 0,
        "executable_index": 0,
        "exec_index": 0,
        "gate_index": 0,
        "extra_indices": [],
        "environment": {"PATH": 1},
    }
    with pytest.raises(OSError, match="environment is invalid"):
        _spawn_one(None, message, [], object())

def test_exec_handshake_requires_readiness_then_close_on_exec() -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"R")
        os.close(write_fd)
        assert lease_envelope._read_exec_pipe(read_fd) is True
    finally:
        os.close(read_fd)
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"RE")
        os.close(write_fd)
        with pytest.raises(OSError, match="execveat"):
            lease_envelope._read_exec_pipe(read_fd)
    finally:
        os.close(read_fd)


def test_spawn_worker_releases_exec_readiness_writer_before_waiting(
    tmp_path: Path,
) -> None:
    status_parent, status_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    ready_r, ready_w = os.pipe()
    gate_r, gate_w = os.pipe()
    os.write(gate_w, b"X")
    null_r = os.open(os.devnull, os.O_RDONLY)
    null_w = os.open(os.devnull, os.O_WRONLY)
    exec_fd = os.open(os.devnull, os.O_RDONLY)
    cwd_fd = os.open(tmp_path, os.O_RDONLY)
    fds = [status_child.detach(), null_r, null_w, null_w, cwd_fd, exec_fd, exec_fd, gate_r, ready_w]
    message = {
        "fd_count": len(fds),
        "status_index": 0,
        "stdio_indices": [1, 2, 3],
        "cwd_index": 4,
        "executable_index": 5,
        "exec_index": 6,
        "gate_index": 7,
        "exec_ready_index": 8,
        "environment": {},
        "argv": ["/nonexistent"],
    }
    observed: list[bytes | None] = []

    class _Reaper:
        def set_leader(self, pid: int) -> None:
            self.pid = pid

        def wait_for_leader(self, pid: int) -> int:
            _, status = os.waitpid(pid, 0)
            os.set_blocking(ready_r, False)
            observed.append(os.read(ready_r, 1))
            try:
                observed.append(os.read(ready_r, 1))
            except BlockingIOError:
                observed.append(None)
            return status

    try:
        _spawn_one(None, message, fds, _Reaper())
    finally:
        for fd in (fds[0], ready_r, gate_w, null_r, null_w, exec_fd, cwd_fd, gate_r):
            os.close(fd)
        status_parent.close()
    # Child reported failure, then the pipe reaches EOF: no worker-held writer.
    assert observed == [b"E", b""]


def test_lease_mountpoints_are_recreated_only_inside_private_tmp(tmp_path: Path) -> None:
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    workspace = tmp_root / "pytest-of-root" / "workspaces" / "lease"
    lease_envelope._prepare_lease_mountpoint(str(workspace), str(tmp_root))
    assert workspace.is_dir()
    assert workspace.stat().st_mode & 0o777 == 0o700
    outside = tmp_path / "host" / "workspace"
    lease_envelope._prepare_lease_mountpoint(str(outside), str(tmp_root))
    lease_envelope._prepare_lease_mountpoint(str(tmp_root), str(tmp_root))
    assert not outside.exists()

def test_only_launcher_descriptor_positions_reach_the_child() -> None:
    # The reviewer's attack: model text naming the status channel's index and
    # an unrelated whole-token literal must both reach exec verbatim.
    status_r, status_w = os.pipe()
    ready_r, ready_w = os.pipe()
    exec_source = os.open(os.devnull, os.O_RDONLY)
    child = os.fork()
    if child == 0:
        try:
            exec_fd, argv = lease_envelope._prepare_exec_descriptors(
                [exec_source, status_w, ready_w],
                exec_fd=exec_source,
                status_fd=status_w,
                exec_ready_fd=ready_w,
                argv=[
                    "/proc/self/fd/77",
                    "-lc",
                    "cat /proc/self/fd/3",
                    "/proc/self/fd/24",
                    f"/proc/self/fd/{status_w}",
                ],
                descriptor_arguments={0: exec_source},
            )
            report = {
                "argv": argv,
                "exec_fd": exec_fd,
                "status_is_exec": os.path.sameopenfile(exec_fd, status_w),
            }
            os.write(status_w, json.dumps(report).encode())
        finally:
            os._exit(0)
    os.close(status_w)
    os.close(ready_w)
    os.close(exec_source)
    os.waitpid(child, 0)
    with os.fdopen(status_r, "rb") as stream:
        report = json.loads(stream.read())
    os.close(ready_r)
    assert report["argv"] == [
        f"/proc/self/fd/{report['exec_fd']}",
        "-lc",
        "cat /proc/self/fd/3",
        "/proc/self/fd/24",
        f"/proc/self/fd/{status_w}",
    ]
    assert report["status_is_exec"] is False


def test_prepare_exec_descriptors_script_format_argv_fd_mapping() -> None:
    report_r, report_w = os.pipe()
    status_r, status_w = os.pipe()
    ready_r, ready_w = os.pipe()
    exec_source = os.open(os.devnull, os.O_RDONLY)
    command_source = os.open(os.devnull, os.O_RDONLY)
    child = os.fork()
    if child == 0:
        try:
            os.close(report_r)
            raw_argv = [
                f"/proc/self/fd/{exec_source}",
                "-lc",
                'exec "$@"',
                "breadboard-execute",
                f"/proc/self/fd/{exec_source}",
                f"/proc/self/fd/{command_source}",
                "extra_arg",
            ]
            descriptor_arguments = {
                0: exec_source,
                4: exec_source,
                5: command_source,
            }
            exec_fd, rewritten_argv = lease_envelope._prepare_exec_descriptors(
                [exec_source, command_source, status_w, ready_w],
                exec_fd=exec_source,
                status_fd=status_w,
                exec_ready_fd=ready_w,
                argv=raw_argv,
                descriptor_arguments=descriptor_arguments,
            )
            arg_shell_fd = int(rewritten_argv[4].removeprefix("/proc/self/fd/"))
            arg_cmd_fd = int(rewritten_argv[5].removeprefix("/proc/self/fd/"))
            os.set_inheritable(exec_fd, False)
            report = {
                "exec_fd": exec_fd,
                "rewritten_argv": rewritten_argv,
                "arg_shell_fd": arg_shell_fd,
                "arg_cmd_fd": arg_cmd_fd,
                "exec_inheritable": os.get_inheritable(exec_fd),
                "shell_inheritable": os.get_inheritable(arg_shell_fd),
                "cmd_inheritable": os.get_inheritable(arg_cmd_fd),
            }
            os.write(report_w, json.dumps(report).encode())
        finally:
            os._exit(0)
    os.close(report_w)
    os.close(status_r)
    os.close(status_w)
    os.close(ready_r)
    os.close(ready_w)
    os.close(exec_source)
    os.close(command_source)
    _, exit_status = os.waitpid(child, 0)
    assert os.WIFEXITED(exit_status) and os.WEXITSTATUS(exit_status) == 0
    with os.fdopen(report_r, "rb") as stream:
        report = json.loads(stream.read())
    exec_fd = report["exec_fd"]
    rewritten_argv = report["rewritten_argv"]
    arg_shell_fd = report["arg_shell_fd"]
    arg_cmd_fd = report["arg_cmd_fd"]
    assert rewritten_argv[0] == f"/proc/self/fd/{exec_fd}"
    assert arg_shell_fd != exec_fd
    assert arg_cmd_fd != exec_fd
    assert arg_cmd_fd != arg_shell_fd
    assert report["shell_inheritable"] is True
    assert report["cmd_inheritable"] is True
    assert report["exec_inheritable"] is False
    assert rewritten_argv[1:4] == ["-lc", 'exec "$@"', "breadboard-execute"]
    assert rewritten_argv[6] == "extra_arg"

def test_prepare_exec_descriptors_high_source_collision_resistance(
    tmp_path: Path,
) -> None:
    path_exec = tmp_path / "exec_source.bin"
    path_exec.write_bytes(b"exec_target")
    path_arg1 = tmp_path / "arg1.txt"
    path_arg1.write_bytes(b"descriptor_arg_1")
    path_arg2 = tmp_path / "arg2.txt"
    path_arg2.write_bytes(b"descriptor_arg_2")

    report_r, report_w = os.pipe()
    status_r, status_w = os.pipe()
    ready_r, ready_w = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(report_r)
            os.close(status_r)
            os.close(ready_r)
            # Open several low file descriptors to fill up the lowest numbers
            low_holes = [os.open(os.devnull, os.O_RDONLY) for _ in range(25)]

            # Open sources at high descriptor numbers
            exec_source = os.open(str(path_exec), os.O_RDONLY)
            arg1_source = os.open(str(path_arg1), os.O_RDONLY)
            arg2_source = os.open(str(path_arg2), os.O_RDONLY)

            # Close the low descriptors to create free holes at low numbers
            for fd in low_holes:
                os.close(fd)

            raw_argv = [
                f"/proc/self/fd/{exec_source}",
                "-lc",
                'exec "$@"',
                "breadboard-execute",
                f"/proc/self/fd/{exec_source}",
                f"/proc/self/fd/{arg1_source}",
                f"/proc/self/fd/{arg2_source}",
            ]
            descriptor_arguments = {
                0: exec_source,
                4: exec_source,
                5: arg1_source,
                6: arg2_source,
            }
            exec_fd, rewritten_argv = lease_envelope._prepare_exec_descriptors(
                [exec_source, arg1_source, arg2_source, status_w, ready_w],
                exec_fd=exec_source,
                status_fd=status_w,
                exec_ready_fd=ready_w,
                argv=raw_argv,
                descriptor_arguments=descriptor_arguments,
            )

            # Extract rewritten descriptor integers
            fd_exec_target = int(rewritten_argv[0].removeprefix("/proc/self/fd/"))
            fd_extra = int(rewritten_argv[4].removeprefix("/proc/self/fd/"))
            fd_arg1 = int(rewritten_argv[5].removeprefix("/proc/self/fd/"))
            fd_arg2 = int(rewritten_argv[6].removeprefix("/proc/self/fd/"))

            stat_exec = os.fstat(fd_exec_target)
            stat_extra = os.fstat(fd_extra)
            stat_arg1 = os.fstat(fd_arg1)
            stat_arg2 = os.fstat(fd_arg2)

            os.set_inheritable(exec_fd, False)

            report = {
                "exec_fd": exec_fd,
                "fd_exec_target": fd_exec_target,
                "fd_extra": fd_extra,
                "fd_arg1": fd_arg1,
                "fd_arg2": fd_arg2,
                "rewritten_argv": rewritten_argv,
                "exec_target_identity": (stat_exec.st_dev, stat_exec.st_ino),
                "extra_identity": (stat_extra.st_dev, stat_extra.st_ino),
                "arg1_identity": (stat_arg1.st_dev, stat_arg1.st_ino),
                "arg2_identity": (stat_arg2.st_dev, stat_arg2.st_ino),
                "exec_inheritable": os.get_inheritable(exec_fd),
                "extra_inheritable": os.get_inheritable(fd_extra),
                "arg1_inheritable": os.get_inheritable(fd_arg1),
                "arg2_inheritable": os.get_inheritable(fd_arg2),
            }
            os.write(report_w, json.dumps(report).encode())
        except BaseException as exc:
            try:
                os.write(report_w, json.dumps({"error": str(exc)}).encode())
            except Exception:
                pass
            os._exit(1)
        finally:
            os._exit(0)

    os.close(report_w)
    os.close(status_r)
    os.close(status_w)
    os.close(ready_r)
    os.close(ready_w)
    _, exit_status = os.waitpid(child, 0)
    assert os.WIFEXITED(exit_status) and os.WEXITSTATUS(exit_status) == 0
    with os.fdopen(report_r, "rb") as stream:
        report = json.loads(stream.read())
    assert "error" not in report, report.get("error")

    expected_exec_identity = (path_exec.stat().st_dev, path_exec.stat().st_ino)
    expected_arg1_identity = (path_arg1.stat().st_dev, path_arg1.stat().st_ino)
    expected_arg2_identity = (path_arg2.stat().st_dev, path_arg2.stat().st_ino)

    # Exec target and extra must be distinct numbers
    assert report["exec_fd"] == report["fd_exec_target"]
    assert report["fd_exec_target"] != report["fd_extra"]

    # Each rewritten descriptor refers to the same file as its source (st_ino/st_dev equality)
    assert tuple(report["exec_target_identity"]) == expected_exec_identity
    assert tuple(report["extra_identity"]) == expected_exec_identity
    assert tuple(report["arg1_identity"]) == expected_arg1_identity
    assert tuple(report["arg2_identity"]) == expected_arg2_identity

    # Inheritability across exec
    assert report["exec_inheritable"] is False
    assert report["extra_inheritable"] is True
    assert report["arg1_inheritable"] is True
    assert report["arg2_inheritable"] is True


def _spawn_message(**overrides: object) -> dict[str, object]:
    message: dict[str, object] = {
        "fd_count": 10,
        "status_index": 3,
        "stdio_indices": [0, 1, 2],
        "executable_index": 4,
        "exec_index": 4,
        "command_index": None,
        "extra_indices": [5],
        "gate_index": 6,
        "cwd_index": 7,
        "exec_ready_index": 8,
        "environment": {},
        "argv": ["/proc/self/fd/4", "-lc", "cat /proc/self/fd/3"],
        "descriptor_arguments": [[0, 4]],
    }
    message.update(overrides)
    return message


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"extra_indices": [3]}, "control channel"),
        ({"descriptor_arguments": [[2, 3]]}, "descriptor arguments"),
        ({"descriptor_arguments": [[2, 6]]}, "descriptor arguments"),
        ({"descriptor_arguments": [[3, 4]]}, "descriptor arguments"),
        ({"descriptor_arguments": [[0, 4], [0, 5]]}, "descriptor arguments"),
    ],
)
def test_spawn_refuses_to_name_control_channels_in_argv(
    overrides: dict[str, object], reason: str
) -> None:
    with pytest.raises(OSError, match=reason):
        _spawn_one(None, _spawn_message(**overrides), list(range(40, 50)), object())


@pytest.mark.skipif(
    not hasattr(socket, "SCM_CREDENTIALS"), reason="SCM_CREDENTIALS is Linux-only"
)
def test_admission_without_a_self_pinned_pidfd_is_refused() -> None:
    # A bare PID can be recycled after an adversarial SIGKILL and reap; only
    # the child's own pidfd identifies it.
    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with host, child:
        host.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        child.sendmsg(
            [b"B"],
            [(
                socket.SOL_SOCKET,
                socket.SCM_CREDENTIALS,
                struct.pack("3i", os.getpid(), os.getuid(), os.getgid()),
            )],
        )
        with pytest.raises(OSError, match="pin its identity"):
            lease_envelope._recv_ready_status(host)



@requires_sealed_execution
async def test_fast_direct_elf_exec_without_sleep(tmp_path: Path) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True, runtime_install_root=tmp_path / "runtime")
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    verifier_path = tmp_path / "fast-verifier"
    shutil.copyfile(Path(os.path.realpath("/usr/bin/true")), verifier_path)
    verifier_path.chmod(0o500)
    verifier_digest = "sha256:" + __import__("hashlib").sha256(verifier_path.read_bytes()).hexdigest()
    primary._runtime._command_executable = _snapshot_installed_executable(str(verifier_path), verifier_digest)
    try:
        for _ in range(12):
            result = await primary._runtime.run_argv(
                (str(verifier_path),), timeout_ms=2_000, output_limit=4_096,
            )
            assert result["returncode"] == 0, result
    finally:
        await primary.close()


def test_containment_receipt_preserves_writable_mounts_through_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lease_envelope,
        "_namespace_inodes",
        lambda: {"pid": 1, "mnt": 2, "user": 3, "net": 4},
    )
    monkeypatch.setattr(lease_envelope, "_mountinfo", lambda: b"mount observation")
    authenticator = HmacSha256ReceiptAuthenticator(
        key_id="test-containment", key=b"k" * 32
    )
    receipt = lease_envelope.mint_containment_receipt(
        lease_id="lease", runtime_id="runtime", mode="userns",
        writable_mounts=(
            lease_envelope.WritableMount("/scratch", "tmpfs", 500_000, "lease_tmpfs"),
            lease_envelope.WritableMount("/workspace", "bind", None, "workspace_bind"),
        ),
        authenticator=authenticator,
    )
    assert tuple(mount.path for mount in receipt.writable_mounts) == ("/scratch", "/workspace")
    assert lease_envelope.verify_containment_receipt(
        receipt.to_mapping(), lease_id="lease", runtime_id="runtime",
        authenticator=authenticator,
    ).writable_mounts == receipt.writable_mounts
    completed = lease_envelope.add_teardown_outcome(
        receipt, pid1_reaped=True, all_dead=True, authenticator=authenticator,
    )
    assert completed.writable_mounts == receipt.writable_mounts

def test_envelope_rejects_writable_inherited_child_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountinfo = (
        b"1 0 1:1 / / ro - ext4 root ro\n"
        b"2 1 1:2 / /dev/shm rw - tmpfs shm rw\n"
        b"3 1 1:3 / /workspace rw - ext4 workspace rw\n"
        b"4 1 1:4 / /scratch rw - tmpfs scratch rw\n"
        b"5 1 1:5 / /tmp rw - tmpfs tmp rw\n"
    )
    monkeypatch.setattr(lease_envelope, "_mountinfo", lambda: mountinfo)
    with pytest.raises(OSError, match="/dev/shm"):
        lease_envelope._verify_mount_view("/workspace", "/scratch", 1_000_000)


def _stacked_tmp_mountinfo(covered_tmp_options: bytes, lease_tmp_parent: bytes) -> bytes:
    # A container runtime mounted /tmp (id 5) before the envelope stacked its
    # lease tmpfs (id 6) over it.
    return (
        b"1 0 1:1 / / ro - overlay root ro\n"
        b"3 1 1:3 / /workspace rw - ext4 workspace rw\n"
        b"4 1 1:4 / /scratch rw,nosuid,nodev - tmpfs scratch rw\n"
        b"5 1 1:5 / /tmp " + covered_tmp_options + b" - tmpfs tmpfs rw\n"
        b"6 " + lease_tmp_parent + b" 1:6 / /tmp rw,nosuid,nodev - tmpfs lease rw\n"
    )


@pytest.fixture
def _bounded_statvfs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lease_envelope.os,
        "statvfs",
        lambda path: types.SimpleNamespace(f_blocks=1, f_frsize=4096),
    )


@pytest.mark.usefixtures("_bounded_statvfs")
def test_envelope_accepts_lease_tmpfs_stacked_over_runtime_tmp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountinfo = _stacked_tmp_mountinfo(b"ro,nosuid,nodev", b"5")
    monkeypatch.setattr(lease_envelope, "_mountinfo", lambda: mountinfo)
    digest, mounts = lease_envelope._verify_mount_view("/workspace", "/scratch", 1_000_000)
    assert digest == "sha256:" + hashlib.sha256(mountinfo).hexdigest()
    assert [(mount.path, mount.source) for mount in mounts] == [
        ("/scratch", "lease_tmpfs"), ("/tmp", "lease_tmpfs"), ("/workspace", "workspace_bind"),
    ]


@pytest.mark.usefixtures("_bounded_statvfs")
def test_envelope_rejects_writable_mount_covered_by_lease_tmpfs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountinfo = _stacked_tmp_mountinfo(b"rw,nosuid,nodev", b"5")
    monkeypatch.setattr(lease_envelope, "_mountinfo", lambda: mountinfo)
    with pytest.raises(OSError, match="inherited mount is writable: /tmp"):
        lease_envelope._verify_mount_view("/workspace", "/scratch", 1_000_000)


@pytest.mark.usefixtures("_bounded_statvfs")
def test_envelope_rejects_two_visible_mounts_for_one_lease_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both /tmp mounts hang off the root mount, so neither covers the other.
    mountinfo = _stacked_tmp_mountinfo(b"rw,nosuid,nodev", b"1")
    monkeypatch.setattr(lease_envelope, "_mountinfo", lambda: mountinfo)
    with pytest.raises(OSError, match="writable mount is invalid: /tmp"):
        lease_envelope._verify_mount_view("/workspace", "/scratch", 1_000_000)


def _patch_mount_view_syscalls(
    monkeypatch: pytest.MonkeyPatch,
    mounted: list[tuple[str, int, tuple[int, int]]],
    *,
    move_mount: Callable[[int, str], None] = lambda fd, path: None,
) -> None:
    def mount_tmpfs(path: str, size: int, **kwargs: object) -> None:
        meta = os.stat(path)
        mounted.append((path, size, (meta.st_dev, meta.st_ino)))

    monkeypatch.setattr(lease_envelope, "_enter_private_mount_namespace", lambda: None)
    monkeypatch.setattr(lease_envelope, "_remount_tree_readonly", lambda path: None)
    monkeypatch.setattr(lease_envelope, "_open_tree", lambda path: os.open(path, os.O_RDONLY))
    monkeypatch.setattr(lease_envelope, "_move_mount", move_mount)
    monkeypatch.setattr(lease_envelope, "_mount_proc", lambda: None)
    monkeypatch.setattr(lease_envelope, "_mount_tmpfs", mount_tmpfs)
    # The /tmp tmpfs is not mounted here, so no lease root is hidden by it:
    # this is the lease-outside-/tmp view (e.g. a bound /out lease root).
    monkeypatch.setattr(lease_envelope, "_prepare_lease_mountpoint", lambda target: False)
    monkeypatch.setattr(lease_envelope, "_verify_mount_view", lambda workspace, scratch, size: ("sha256:" + "0" * 64, ()))


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux O_PATH descriptors")
def test_envelope_mounts_scratch_on_bounded_private_tmpfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, scratch = tmp_path / "workspace", tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    mounted: list[tuple[str, int, tuple[int, int]]] = []
    _patch_mount_view_syscalls(monkeypatch, mounted)
    workspace_fd = os.open(workspace, os.O_RDONLY)
    scratch_fd = os.open(scratch, os.O_RDONLY)
    scratch_meta = os.fstat(scratch_fd)
    try:
        lease_envelope._setup_mount_view(
            str(workspace), str(scratch), workspace_fd, scratch_fd, 1_000_000,
        )
    finally:
        os.close(workspace_fd)
        os.close(scratch_fd)
    assert [path for path, _, _ in mounted][0] == "/tmp"
    scratch_target, _, scratch_identity = mounted[1]
    # A descriptor inherited across unshare(CLONE_NEWNS) names a parent-
    # namespace mount, where mount(2) fails with EINVAL; the scratch tmpfs
    # targets a descriptor reopened in the namespace for the verified inode.
    assert scratch_target.startswith("/proc/self/fd/")
    assert scratch_target != f"/proc/self/fd/{scratch_fd}"
    assert scratch_identity == (scratch_meta.st_dev, scratch_meta.st_ino)
    assert sum(size for _, size, _ in mounted) == 1_000_000


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux O_PATH descriptors")
@pytest.mark.parametrize("replacement", ("directory", "symlink"))
def test_envelope_refuses_scratch_replaced_before_its_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    workspace, scratch = tmp_path / "workspace", tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def swap_scratch(fd: int, path: str) -> None:
        scratch.rename(tmp_path / "verified-scratch")
        if replacement == "directory":
            scratch.mkdir()
        else:
            scratch.symlink_to(elsewhere, target_is_directory=True)

    mounted: list[tuple[str, int, tuple[int, int]]] = []
    _patch_mount_view_syscalls(monkeypatch, mounted, move_mount=swap_scratch)
    workspace_fd = os.open(workspace, os.O_RDONLY)
    scratch_fd = os.open(scratch, os.O_RDONLY)
    try:
        with pytest.raises(OSError) as captured:
            lease_envelope._setup_mount_view(
                str(workspace), str(scratch), workspace_fd, scratch_fd, 1_000_000,
            )
    finally:
        os.close(workspace_fd)
        os.close(scratch_fd)
    assert captured.value.errno in {errno.ESTALE, errno.ELOOP, errno.ENOTDIR}
    assert [path for path, _, _ in mounted] == ["/tmp"]


@requires_sealed_execution
async def test_envelope_scratch_and_tmp_are_size_limited_tmpfs(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    try:
        scratch = primary._runtime._envelope.scratch
        result = await primary._runtime.run_shell(
            f"stat -f -c '%T:%S:%b' /tmp {shlex.quote(scratch)}",
            timeout_ms=2_000, output_limit=4_096,
        )
        assert result["returncode"] == 0, result
        mounts = result["stdout"].splitlines()
        assert len(mounts) == 2
        assert all(line.startswith("tmpfs:") for line in mounts)
        sizes = [
            int(block_size) * int(blocks)
            for line in mounts
            for _, block_size, blocks in [line.split(":")]
        ]
        storage_bytes = primary._runtime.plan.resources.storage_bytes
        assert 0 < sum(sizes) <= storage_bytes + 8192
        assert all(size <= storage_bytes // 2 + 4096 for size in sizes)
    finally:
        await primary.close()


@requires_sealed_execution
async def test_envelope_child_mount_is_read_only_inside_lease(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    try:
        command = (
            "/usr/bin/python3 -c 'import errno,os; "
            "p=\"/dev/shm/breadboard-forbidden-write\"; "
            "try_write=lambda: os.open(p,os.O_CREAT|os.O_WRONLY,0o600); "
            "import sys; "
            "exec(\"try:\\n try_write()\\nexcept OSError as e:\\n "
            "sys.exit(0 if e.errno == errno.EROFS else 5)\\nsys.exit(6)\")'"
        )
        result = await primary._runtime.run_shell(
            command, timeout_ms=2_000, output_limit=4_096
        )
        assert result["returncode"] == 0, result
        receipt = primary._runtime.containment_receipt
        assert receipt is not None
        assert receipt.mountinfo_sha256.startswith("sha256:")
        assert {entry.path for entry in receipt.writable_mounts} == {
            "/tmp", str(primary._materialized.workspace_path),
            str(primary._runtime._envelope.scratch),
        }
    finally:
        await primary.close()




@requires_sealed_execution
async def test_envelope_descendants_do_not_hold_lease_owner_lock(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    envelope = primary._runtime._envelope
    assert envelope is not None
    lock_path = str(harness.lease_root / f"{primary.lease_id}.owner.lock")
    action = asyncio.create_task(
        primary.runner_workspace.run_shell(
            ": > work/lock-ready; exec 1>&- 2>&-; sleep 10",
            timeout=2,
        )
    )
    try:
        for _ in range(200):
            if (primary._materialized.workspace_path / "work/lock-ready").exists():
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("envelope process did not become ready")
        roots = {envelope.launcher_pid, envelope.pid1}
        pending = list(roots)
        while pending:
            parent = pending.pop()
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    status = (entry / "status").read_text(encoding="ascii")
                except (OSError, UnicodeError):
                    continue
                if any(
                    line == f"PPid:\t{parent}"
                    for line in status.splitlines()
                ):
                    child = int(entry.name)
                    if child not in roots:
                        roots.add(child)
                        pending.append(child)
        for pid in roots:
            for entry in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    assert os.readlink(entry) != lock_path
                except (OSError, UnicodeError):
                    continue
    finally:
        if not action.done():
            action.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await action
        await primary.close()
        await harness.manager.close()


@requires_sealed_execution
def test_sealed_executable_works_without_python_exported_seal_constants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "F_ADD_SEALS", "F_GET_SEALS", "F_SEAL_WRITE",
        "F_SEAL_SHRINK", "F_SEAL_GROW", "F_SEAL_SEAL",
    ):
        monkeypatch.delattr(fcntl, name, raising=False)
    executable = tmp_path / "shell"
    shutil.copyfile(os.path.realpath("/bin/sh"), executable)
    executable.chmod(0o500)

    pinned = _snapshot_installed_executable(str(executable), None)
    try:
        with pytest.raises(OSError) as captured:
            os.write(pinned.fd, b"tampered")
        assert captured.value.errno == errno.EPERM
        result = subprocess.run(
            [f"/proc/self/fd/{pinned.fd}", "-c", "printf sealed-execution"],
            pass_fds=(pinned.fd,),
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0
        assert result.stdout == b"sealed-execution"
    finally:
        pinned.close()


@requires_sealed_execution
def test_sealed_repository_diff_includes_ignored_untracked_and_binary_files(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    git_path = shutil.which("git")
    assert git_path is not None

    git_directory = tmp_path / "git-bin"
    git_directory.symlink_to(
        Path(os.path.realpath(git_path)).parent, target_is_directory=True
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
                "Runtime", (), {"fixed_environment": (("PATH", str(git_directory)),)}
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




def test_sealed_repository_diff_repository_mode_binds_alternate_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    scratch = tmp_path / "scratch"
    repository.mkdir()
    scratch.mkdir()
    git_path = shutil.which("git")
    assert git_path is not None

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    git("init", "--quiet")
    (repository / "tracked.txt").write_text("before\n", encoding="utf-8")
    git("add", ".")
    git(
        "-c",
        "user.name=BreadBoard",
        "-c",
        "user.email=breadboard@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "base",
    )
    base_commit = git("rev-parse", "HEAD")
    (repository / "binary.bin").write_bytes(b"\x00\x01\xffbinary\n")
    (repository / "tracked.txt").write_text("after\n", encoding="utf-8")
    class PinnedGit:
        proc_fd_path = git_path
        digest = "sha256:" + "0" * 64

        def __init__(self) -> None:
            self.fd = os.open(git_path, os.O_RDONLY)

        def close(self) -> None:
            os.close(self.fd)

    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox._snapshot_installed_executable",
        lambda _path, _expected_digest: PinnedGit(),
    )
    plan = type(
        "RepositoryDiffPlan",
        (),
        {
            "runtime": type(
                "Runtime",
                (),
                {"fixed_environment": (("PATH", str(Path(git_path).parent)),)},
            )(),
            "limits": type(
                "Limits",
                (),
                {"action_timeout_ms": 10_000, "artifact_bytes_each": 1024 * 1024},
            )(),
        },
    )()
    result = _sealed_repository_diff(
        repository=repository,
        scratch_directory=scratch,
        base_commit=base_commit,
        plan=plan,
    )
    assert result["returncode"] == 0
    assert "diff --git a/binary.bin b/binary.bin\n" in result["stdout"]


def test_sealed_workspace_seed_diff_uses_real_git_without_sealed_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    baseline = tmp_path / "baseline"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    baseline.mkdir()
    scratch.mkdir()
    (baseline / "seed.txt").write_text("before\n", encoding="utf-8")
    shutil.copy2(baseline / "seed.txt", workspace / "seed.txt")
    (workspace / "seed.txt").write_text("after\n", encoding="utf-8")
    (workspace / "marker.txt").write_text("marker\n", encoding="utf-8")
    git_path = shutil.which("git")
    assert git_path is not None

    class PinnedGit:
        proc_fd_path = git_path
        digest = "sha256:" + "0" * 64

        def __init__(self) -> None:
            self.fd = os.open(git_path, os.O_RDONLY)

        def close(self) -> None:
            os.close(self.fd)

    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox._snapshot_installed_executable",
        lambda _path, _expected_digest: PinnedGit(),
    )
    plan = type(
        "SeedDiffPlan",
        (),
        {
            "runtime": type(
                "Runtime",
                (),
                {"fixed_environment": (("PATH", str(Path(git_path).parent)),)},
            )(),
            "limits": type(
                "Limits",
                (),
                {"action_timeout_ms": 10_000, "artifact_bytes_each": 1024 * 1024},
            )(),
        },
    )()
    result = _sealed_repository_diff(
        repository=workspace,
        scratch_directory=scratch,
        base_commit="sha256:" + "1" * 64,
        plan=plan,
        seed_baseline=baseline,
    )
    assert result["returncode"] == 0
    assert "diff --git a/seed.txt b/seed.txt\n" in result["stdout"]
    assert "diff --git a/marker.txt b/marker.txt\n" in result["stdout"]
@requires_sealed_execution
def test_sealed_workspace_seed_diff_captures_modification_and_marker_addition(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    baseline = tmp_path / "seed-baseline"
    scratch = tmp_path / "scratch"
    repository.mkdir()
    workspace.mkdir()
    baseline.mkdir()
    scratch.mkdir()
    (baseline / "seed.txt").write_text("before\n", encoding="utf-8")
    shutil.copy2(baseline / "seed.txt", workspace / "seed.txt")
    (workspace / "seed.txt").write_text("after\n", encoding="utf-8")
    (workspace / "marker.txt").write_text("marker\n", encoding="utf-8")
    git_path = shutil.which("git")
    assert git_path is not None

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    git("init", "--quiet")
    shutil.copy2(baseline / "seed.txt", repository / "seed.txt")
    git("add", ".")
    git(
        "-c",
        "user.name=BreadBoard",
        "-c",
        "user.email=breadboard@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "base",
    )
    base_commit = git("rev-parse", "HEAD")
    (repository / "seed.txt").write_text("after\n", encoding="utf-8")
    (repository / "marker.txt").write_text("marker\n", encoding="utf-8")
    plan = type(
        "SeedDiffPlan",
        (),
        {
            "runtime": type(
                "Runtime",
                (),
                {"fixed_environment": (("PATH", str(Path(git_path).parent)),)},
            )(),
            "limits": type(
                "Limits",
                (),
                {"action_timeout_ms": 10_000, "artifact_bytes_each": 1024 * 1024},
            )(),
        },
    )()
    repository_result = _sealed_repository_diff(
        repository=repository,
        scratch_directory=scratch,
        base_commit=base_commit,
        plan=plan,
    )
    seed_result = _sealed_repository_diff(
        repository=workspace,
        scratch_directory=scratch,
        base_commit="sha256:" + "1" * 64,
        plan=plan,
        seed_baseline=baseline,
    )
    assert seed_result["stdout"] == repository_result["stdout"]
    patch = seed_result["stdout"]
    seed_section = patch.split("diff --git a/seed.txt b/seed.txt\n", 1)[1].split(
        "diff --git a/marker.txt b/marker.txt\n", 1
    )[0]
    index_line = next(line for line in seed_section.splitlines() if line.startswith("index "))
    old_blob, new_blob = index_line.split()[1].split("..", 1)
    assert old_blob.strip("0") and new_blob.strip("0")


@requires_sealed_execution
def test_sealed_workspace_seed_diff_rejects_workspace_git_metadata(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    baseline = tmp_path / "seed-baseline"
    workspace.mkdir()
    baseline.mkdir()
    (workspace / ".git").mkdir()
    git_path = shutil.which("git")
    assert git_path is not None
    plan = type(
        "SeedDiffPlan", (),
        {
            "runtime": type(
                "Runtime", (), {"fixed_environment": (("PATH", str(Path(git_path).parent)),)}
            )(),
            "limits": type(
                "Limits", (),
                {"action_timeout_ms": 10_000, "artifact_bytes_each": 1024 * 1024},
            )(),
        },
    )()
    with pytest.raises(VerifierSnapshotError, match="embedded Git repository"):
        _sealed_repository_diff(
            repository=workspace,
            scratch_directory=tmp_path / "scratch",
            base_commit="sha256:" + "2" * 64,
            plan=plan,
            seed_baseline=baseline,
        )

async def test_process_backend_binds_identity_recorder_before_base_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    plan = replace(
        build_sandbox_execution_plan(
            fixture.request, fixture.registries, fixture.authorities
        ),
        containment=RuntimeContainment.UNCONFINED_TEST_ONLY,
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
        execution_format = "elf"

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
    if _sealed_refusal is None:
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
        runtime_path.chmod(0o700)
        runtime_path.write_bytes(replacement_bytes)
        runtime_path.chmod(0o500)
        mutated_identity = runtime_path.stat()
        assert (mutated_identity.st_dev, mutated_identity.st_ino) == (
            original_identity.st_dev,
            original_identity.st_ino,
        )
    assert runtime_path.read_bytes() == replacement_bytes
    result = await primary._runtime.run_shell(
        "sleep 0.05; printf admitted-snapshot",
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
    shutil.copyfile(Path(os.path.realpath("/bin/sh")), verifier_path)
    verifier_path.chmod(0o500)
    verifier_digest = "sha256:" + __import__("hashlib").sha256(
        verifier_path.read_bytes()
    ).hexdigest()
    pinned = _snapshot_installed_executable(str(verifier_path), verifier_digest)
    primary._runtime._command_executable = pinned
    replacement = tmp_path / "replacement-verifier"
    shutil.copyfile(Path(os.path.realpath("/bin/false")), replacement)
    replacement.chmod(0o500)
    os.replace(replacement, verifier_path)

    result = await primary._runtime.run_argv(
        (str(verifier_path), "-c", "printf admitted-verifier"),
        timeout_ms=1_000,
        output_limit=4_096,
    )

    assert result["returncode"] == 0
    assert result["stdout"] == "admitted-verifier"
    assert (await primary.close()).state is CleanupState.RELEASED
    assert pinned.closed is True


@requires_sealed_execution
async def test_pinned_script_verifier_in_envelope_executes_with_open_descriptor_argv(
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
    verifier_path = tmp_path / "script-verifier.sh"
    verifier_script = (
        "#!/bin/sh\n"
        "for arg in \"$@\"; do\n"
        "    case \"$arg\" in\n"
        "        /proc/self/fd/*)\n"
        "            if [ ! -e \"$arg\" ]; then\n"
        "                echo \"closed fd path in argv: $arg\" >&2\n"
        "                exit 42\n"
        "            fi\n"
        "            ;;\n"
        "    esac\n"
        "done\n"
        "printf script-verifier-ok\n"
    )
    verifier_path.write_bytes(verifier_script.encode("utf-8"))
    verifier_path.chmod(0o500)
    verifier_digest = "sha256:" + __import__("hashlib").sha256(
        verifier_path.read_bytes()
    ).hexdigest()
    pinned = _snapshot_installed_executable(str(verifier_path), verifier_digest)
    assert pinned.execution_format == "script"
    primary._runtime._command_executable = pinned

    result = await primary._runtime.run_argv(
        (str(verifier_path), "check-arg"),
        timeout_ms=1_000,
        output_limit=4_096,
    )

    assert result["returncode"] == 0
    assert result["stdout"] == "script-verifier-ok"
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
        if identity is None:
            original(lease_id, resource_id, identity)
            return
        pid = int(identity["process_pid"])
        status = Path(f"/proc/{pid}/status").read_text()
        observed["tracer_pid"] = next(
            line.split()[1] for line in status.splitlines() if line.startswith("TracerPid:")
        )
        observed["state"] = next(
            line.split()[1] for line in status.splitlines() if line.startswith("State:")
        )[0]
        observed["cmdline"] = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        observed["exe"] = os.readlink(f"/proc/{pid}/exe")
        original(lease_id, resource_id, identity)

    monkeypatch.setattr(
        harness.manager, "_record_process_identity", inspect_stopped_process
    )
    result = await primary._runtime.run_shell(
        "sleep 5; printf argv-proof", timeout_ms=10_000, output_limit=4_096
    )

    assert observed["cmdline"][0].decode() == runtime_path
    assert observed["tracer_pid"] == "0"
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
        if identity is None:
            return
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
    assert handle.teardown_receipt is not None
    assert handle.teardown_receipt.outcome == {
        "pid1_reaped": True,
        "all_dead": True,
    }
    assert handle._executable.closed is True
    with pytest.raises(OSError):
        os.fstat(executable_fd)
    await handle.terminate()
    with pytest.raises(OSError):
        os.fstat(executable_fd)
    assert (await primary.close()).state is CleanupState.RELEASED


@requires_sealed_execution
async def test_cancelled_termination_retains_signed_teardown_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path / "runtime"
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    handle = primary._runtime
    executable_fd = handle._executable.fd
    intercepted, release = threading.Event(), threading.Event()
    original = lease_envelope._recv_frame

    def intercept(sock: socket.socket):
        frame = original(sock)
        if frame[0].get("kind") == "teardown":
            intercepted.set()
            release.wait(3)
        return frame

    monkeypatch.setattr(lease_envelope, "_recv_frame", intercept)
    try:
        first = asyncio.create_task(handle.terminate())
        assert await asyncio.wait_for(asyncio.to_thread(intercepted.wait), 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        second = await asyncio.wait_for(handle.terminate(), 2)
        third = await asyncio.wait_for(handle.terminate(), 2)
        assert second == third == (
            CleanupStepReceipt("runtime", CleanupState.RELEASED),
        )
        assert handle.teardown_receipt is not None
        assert handle.teardown_receipt.outcome == {
            "pid1_reaped": True, "all_dead": True,
        }
        assert handle._envelope.pid1_fd == -1
        assert handle._executable.closed is True
        with pytest.raises(OSError):
            os.fstat(executable_fd)
    finally:
        release.set()
        receipt = await primary.close()
    assert receipt.state is CleanupState.RELEASED
    assert not (harness.lease_root / f"{primary.lease_id}.json").exists()


@requires_sealed_execution
async def test_cancelled_termination_waiting_for_launch_lock_fences_later_launch(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path / "runtime"
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    handle = primary._runtime
    try:
        async with handle._launch_lock:
            first = asyncio.create_task(handle.terminate())
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

        with pytest.raises(WorkspaceStateError) as refused:
            await handle.run_shell(
                "printf forbidden", timeout_ms=2_000, output_limit=4_096
            )
        assert refused.value.code == "lease_not_active"
        second = await asyncio.wait_for(handle.terminate(), 4)
        assert await handle.terminate() == second == (
            CleanupStepReceipt("runtime", CleanupState.RELEASED),
        )
        assert handle.teardown_receipt is not None
        assert handle.teardown_receipt.outcome == {
            "pid1_reaped": True, "all_dead": True,
        }
        assert handle._executable.closed is True
    finally:
        closed = await primary.close()
        await harness.manager.close()
    assert closed.state is CleanupState.RELEASED
    assert not (harness.lease_root / f"{primary.lease_id}.json").exists()


@requires_sealed_execution
async def test_native_close_inner_cancellation_records_typed_runtime_failure(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path / "runtime"
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    handle = primary._runtime

    class CancelledNativeSession:
        async def close(self) -> None:
            raise asyncio.CancelledError("native close cancelled")

    handle._native_session = CancelledNativeSession()
    try:
        first = await handle.terminate()
        assert first == (
            CleanupStepReceipt(
                "runtime", CleanupState.FAILED, "native_session:CancelledError"
            ),
        )
        assert await handle.terminate() == first
        assert handle.teardown_receipt is not None
        assert handle.teardown_receipt.outcome == {
            "pid1_reaped": True, "all_dead": True,
        }
        assert (await primary.close()).state is CleanupState.QUARANTINED
        assert (harness.lease_root / f"{primary.lease_id}.json").exists()
    finally:
        await harness.manager.close()


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
async def test_real_process_preserves_absolute_workspace_and_scratch_roots(
    tmp_path: Path,
) -> None:
    staging_before = set(Path("/dev/shm").glob(".breadboard-envelope-*"))
    shm_mountinfo_before = tuple(
        line
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
        if " /dev/shm " in line
    )
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    envelope = primary._runtime._envelope
    assert envelope is not None
    workspace = primary._materialized.workspace_path
    scratch = Path(envelope.scratch)
    workspace_probe = workspace / "absolute-host-probe"
    scratch_probe = scratch / "absolute-host-probe"
    workspace_probe.write_text("workspace-host", encoding="utf-8")
    scratch_probe.write_text("host-invisible", encoding="utf-8")
    command = (
        f"cat {shlex.quote(str(workspace_probe))} > work/workspace-read; "
        f"test ! -e {shlex.quote(str(scratch_probe))}; "
        f"printf scratch-inside > {shlex.quote(str(scratch / 'absolute-inside'))}; "
        f"cat {shlex.quote(str(scratch / 'absolute-inside'))} > work/scratch-read; "
        f"printf workspace-inside > {shlex.quote(str(workspace / 'absolute-inside'))}"
    )
    result = await primary.runner_workspace.run_shell(command, timeout=2)
    assert result["returncode"] == 0
    assert (workspace / "work/workspace-read").read_text(encoding="utf-8") == (
        "workspace-host"
    )
    assert (workspace / "work/scratch-read").read_text(encoding="utf-8") == (
        "scratch-inside"
    )
    assert (workspace / "absolute-inside").read_text(encoding="utf-8") == (
        "workspace-inside"
    )
    assert scratch_probe.read_text(encoding="utf-8") == "host-invisible"
    assert not (scratch / "absolute-inside").exists()
    receipt = await primary.close()
    assert receipt.state is CleanupState.RELEASED
    assert await harness.manager.close() == ()
    assert tuple(
        line
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
        if " /dev/shm " in line
    ) == shm_mountinfo_before
    assert set(Path("/dev/shm").glob(".breadboard-envelope-*")) == staging_before


def _namespace_processes(pid_namespace_inode: int) -> list[tuple[int, str]]:
    processes: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if os.stat(entry / "ns/pid").st_ino != pid_namespace_inode:
                continue
            fields = (entry / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            processes.append((int(entry.name), fields[0]))
        except (OSError, UnicodeError, ValueError, IndexError):
            continue
    return processes


def _resolve_namespace_pid(namespace_pid: int, pid_namespace_inode: int) -> int | None:
    target = str(namespace_pid)
    for host_pid, _state in _namespace_processes(pid_namespace_inode):
        try:
            status = Path(f"/proc/{host_pid}/status").read_text(encoding="ascii")
        except (OSError, UnicodeError):
            continue
        for line in status.splitlines():
            if line.startswith("NSpid:") and line.split()[-1] == target:
                return host_pid
    return None


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
        "while [ ! -f work/descendant.pid ]; do :; done; "
        "sleep 1"
    )
    action = asyncio.create_task(
        primary.runner_workspace.run_shell(command, timeout=2)
    )
    host_pid: int | None = None
    try:
        descendant: dict[str, object] | None = None
        for _ in range(200):
            try:
                candidate = await primary.runner_workspace.read_text(
                    "work/descendant.pid"
                )
            except FileNotFoundError:
                await asyncio.sleep(0.01)
            else:
                descendant = candidate
                break
        assert descendant is not None
        namespace_pid = int(str(descendant["content"]))
        receipt = primary._runtime.containment_receipt
        assert receipt is not None
        host_pid = _resolve_namespace_pid(namespace_pid, receipt.pid_namespace_inode)
        assert host_pid is not None
        assert Path(f"/proc/{host_pid}").exists()
        await action
        assert not Path(f"/proc/{host_pid}").exists()
        assert not any(
            state == "Z"
            for _pid, state in _namespace_processes(receipt.pid_namespace_inode)
        )
        cleanup_receipt = await primary.close()
        assert cleanup_receipt.state is CleanupState.RELEASED
        assert await harness.manager.close() == ()
    finally:
        if not action.done():
            action.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await action
        if host_pid is not None and Path(f"/proc/{host_pid}").exists():
            os.kill(host_pid, signal.SIGKILL)

@requires_sealed_execution
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_real_process_closed_stream_timeout_or_cancellation_kills_descendant(
    tmp_path: Path, mode: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    ready_fifo = primary._materialized.workspace_path / "descendant-ready.fifo"
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
    containment_receipt = None
    host_pid: int | None = None
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

        containment_receipt = primary._runtime.containment_receipt
        assert containment_receipt is not None
        descendant = await primary.runner_workspace.read_text("work/descendant.pid")
        namespace_descendant_pid = int(descendant["content"])
        host_pid = _resolve_namespace_pid(
            namespace_descendant_pid, containment_receipt.pid_namespace_inode
        )
        assert host_pid is not None
        descendant_pid = host_pid

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
        assert namespace_descendant_pid == int(spawned["content"])

        async with asyncio.timeout(1):
            while True:
                try:
                    os.kill(host_pid, 0)
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
            CleanupStepReceipt("native_scratch", CleanupState.RELEASED),
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
    containment_receipt = primary._runtime.containment_receipt
    assert containment_receipt is not None
    over_started = loop.time()
    over_action = asyncio.create_task(
        handle.run_shell(
            over_command,
            timeout_ms=500,
            output_limit=output_limit,
        )
    )
    namespace_descendant_pid: int | None = None
    descendant_pid: int | None = None
    async with asyncio.timeout(1):
        while descendant_pid is None:
            try:
                descendant = await primary.runner_workspace.read_text(
                    "work/deadline-child.pid"
                )
            except FileNotFoundError:
                await asyncio.sleep(0.01)
                continue
            namespace_descendant_pid = int(descendant["content"])
            descendant_pid = _resolve_namespace_pid(
                namespace_descendant_pid, containment_receipt.pid_namespace_inode
            )
            if descendant_pid is None:
                await asyncio.sleep(0.01)

    with pytest.raises(SandboxLaunchError) as captured:
        async with asyncio.timeout(2):
            await over_action
    over_elapsed = loop.time() - over_started

    assert captured.value.code == "runtime_launch_failed"
    assert 0.4 <= over_elapsed < 2
    assert under_elapsed < over_elapsed
    assert namespace_descendant_pid is not None
    spawned = await primary.runner_workspace.read_text("work/deadline-spawned.pid")
    assert namespace_descendant_pid == int(spawned["content"])
    assert descendant_pid is not None
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
        CleanupStepReceipt("native_scratch", CleanupState.RELEASED),
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
    ready_fifo = primary._materialized.workspace_path / "restart-ready.fifo"
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
        # The original manager is still in this test process. Release only its
        # ownership lock to model the crashed manager before cold recovery.
        harness.manager._release_lease_owner_lock(primary.lease_id, unlink=False)
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
                "native_scratch", CleanupState.QUARANTINED,
                "dependent runtime cleanup incomplete",
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
        harness.manager._release_lease_owner_lock(primary.lease_id, unlink=False)
        harness.clock.advance(minutes=5)
        receipts = await asyncio.wait_for(recovery.reconcile_stale(), 2)
        assert len(receipts) == 1
        assert receipts[0].steps == (
            CleanupStepReceipt("child_verifier", CleanupState.ALREADY_RELEASED),
            CleanupStepReceipt("runtime", CleanupState.QUARANTINED, "stale_identity_uncertain"),
            CleanupStepReceipt(
                "native_scratch", CleanupState.QUARANTINED,
                "dependent runtime cleanup incomplete",
            ),
            CleanupStepReceipt("workspace", CleanupState.QUARANTINED, "stale_identity_uncertain"),
            CleanupStepReceipt("cache_holder", CleanupState.QUARANTINED, "stale_identity_uncertain"),
            CleanupStepReceipt("lease_record", CleanupState.QUARANTINED, "stale_identity_uncertain"),
        )
        os.kill(surviving_pid, 0)
        assert record_path.exists()
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(second, 1)
        with pytest.raises(ProcessLookupError):
            os.kill(surviving_pid, 0)
        receipts = await recovery.reconcile_stale()
        assert len(receipts) == 1
        assert receipts[0].state is CleanupState.RELEASED
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

    nonempty_attempts = [(resource_id, identity) for resource_id, identity in attempted if identity is not None]
    assert len(nonempty_attempts) == 1
    resource_id, identity = nonempty_attempts[0]
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


@requires_sealed_execution
async def test_trusted_process_enforces_network_isolation_and_records_netns(
    tmp_path: Path,
) -> None:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    server_port = server.sockets[0].getsockname()[1]
    fixture = make_runtime_fixture(
        with_writable_mount=True, runtime_install_root=tmp_path
    )
    harness = RuntimeHarness(tmp_path / "harness", fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    try:
        receipt = primary._runtime.containment_receipt
        assert receipt is not None
        assert receipt.network_namespace_inode > 0
        if Path("/proc/self/ns/net").exists():
            from breadboard.rl.harness.lease_envelope import _ns_inode

            assert receipt.network_namespace_inode != _ns_inode(
                os.readlink("/proc/self/ns/net")
            )
        cmd = (
            f"/usr/bin/python3 -c \"import socket; s = socket.socket(); "
            f"s.settimeout(0.5); s.connect(('127.0.0.1', {server_port}))\""
        )
        result = await primary._runtime.run_shell(
            cmd, timeout_ms=2_000, output_limit=4_096
        )
        assert result["returncode"] != 0
        interfaces = await primary._runtime.run_shell(
            "/usr/bin/python3 -c 'import socket; print(\",\".join(name for _, name in socket.if_nameindex()))'",
            timeout_ms=2_000, output_limit=4_096,
        )
        assert interfaces["returncode"] == 0, interfaces
        assert interfaces["stdout"].strip() == "lo"
    finally:
        server.close()
        await server.wait_closed()
        await primary.close()


@requires_sealed_execution
@pytest.mark.asyncio
async def test_trusted_process_handle_rejects_workspace_descriptor_identity_mismatch(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    handle = primary._runtime
    assert isinstance(handle, TrustedProcessHandle)
    original_identity = handle._workspace_identity
    handle._workspace_identity = (original_identity[0], original_identity[1] + 9999)
    with pytest.raises(WorkspaceStateError) as exc_info:
        await handle._start_stopped_process(["/bin/echo", "test"], timeout_ms=1000)
    assert exc_info.value.code == "workspace_authority_mismatch"
    assert "workspace descriptor identity changed" in str(exc_info.value)
    handle._workspace_identity = original_identity
    await primary.close()



@requires_sealed_execution
@pytest.mark.asyncio
async def test_sealed_attested_launch_rejects_preexisting_scratch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    nonce = "preexist-scratch-nonce"
    lease_id = f"lease-{nonce}"
    scratch_dir = harness.manager.lease_root / f"{lease_id}.native-scratch"
    os.mkdir(scratch_dir, mode=0o700)
    sentinel = scratch_dir / "sentinel.txt"
    sentinel.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(harness.manager, "_nonce", lambda: nonce)
    try:
        with pytest.raises(SandboxFault) as exc_info:
            await harness.manager.open(fixture.request)
        assert isinstance(exc_info.value.primary, SandboxLaunchError)
        assert exc_info.value.primary.code == "runtime_preflight_failed"
        assert "already exists" in str(exc_info.value.primary)
        assert scratch_dir.is_dir()
        assert sentinel.is_file()
        assert sentinel.read_text(encoding="utf-8") == "preserved"
        scratch_receipt = next(
            s for s in exc_info.value.cleanup_receipt.steps if s.resource == "native_scratch"
        )
        assert scratch_receipt.state is CleanupState.QUARANTINED
        assert scratch_receipt.detail == "preexisting_scratch_preserved"
    finally:
        if sentinel.exists():
            sentinel.unlink()
        if scratch_dir.is_dir():
            scratch_dir.rmdir()

@requires_sealed_execution
@pytest.mark.asyncio
async def test_sealed_attested_launch_and_native_scratch_lifecycle(
    tmp_path: Path,
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    primary = await harness.manager.open(fixture.request)
    try:
        handle = primary._runtime
        assert isinstance(handle, TrustedProcessHandle)
        assert handle.native_scratch_identity is not None
        scratch_dir = harness.manager.lease_root / f"{primary.lease_id}.native-scratch"
        assert scratch_dir.is_dir()
        assert handle.native_scratch_identity == (scratch_dir.stat().st_dev, scratch_dir.stat().st_ino)

        adopted = sandbox_module._create_native_scratch(
            harness.manager, primary.lease_id, expected_identity=handle.native_scratch_identity
        )
        assert adopted == scratch_dir
    finally:
        await primary.close()



@requires_sealed_execution
@pytest.mark.asyncio
async def test_sealed_attested_launch_rejects_lease_root_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    nonce = "mismatch-root-sealed"
    harness.manager._nonce = lambda: nonce
    real_id = harness.manager._lease_root_identity
    assert real_id is not None
    harness.manager._lease_root_identity = (real_id[0], real_id[1] + 9999)
    scratch_dir = harness.manager.lease_root / f"lease-{nonce}.native-scratch"

    with pytest.raises(SandboxLaunchError) as exc_info:
        await harness.manager.open(fixture.request)
    assert exc_info.value.code == "runtime_preflight_failed"
    assert "lease root authority is invalid" in str(exc_info.value)
    assert not scratch_dir.exists()


@requires_sealed_execution
@pytest.mark.asyncio
async def test_sealed_attested_launch_rejects_non_empty_scratch_preserves_directory_in_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()
    nonce = "nonempty-scratch-sealed"
    harness.manager._nonce = lambda: nonce
    scratch_dir = harness.manager.lease_root / f"lease-{nonce}.native-scratch"

    real_mkdir = os.mkdir
    def rogue_mkdir(name, mode=0o700, *, dir_fd=None):
        real_mkdir(name, mode=mode, dir_fd=dir_fd)
        if str(name).endswith(".native-scratch"):
            (scratch_dir / "rogue.txt").write_text("rogue-payload", encoding="utf-8")
    monkeypatch.setattr(os, "mkdir", rogue_mkdir)

    with pytest.raises(SandboxFault) as exc_info:
        await harness.manager.open(fixture.request)
    assert exc_info.value.primary.code == "runtime_preflight_failed"
    assert "native scratch is not empty" in str(exc_info.value.primary)
    assert scratch_dir.is_dir()
    assert (scratch_dir / "rogue.txt").is_file()
    receipt = next(s for s in exc_info.value.cleanup_receipt.steps if s.resource == "native_scratch")
    assert receipt.state is CleanupState.QUARANTINED
    assert receipt.detail == "preexisting_scratch_preserved"


@requires_sealed_execution
@pytest.mark.asyncio
async def test_sealed_attested_launch_envelope_failure_cleans_up_or_quarantines_replaced_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    harness.manager.process_backend = TrustedProcessBackend()

    # 1. Envelope failure after identity recorded -> manager removes scratch (RELEASED)
    nonce_a = "envelope-fail-sealed"
    harness.manager._nonce = lambda: nonce_a
    scratch_a = harness.manager.lease_root / f"lease-{nonce_a}.native-scratch"

    def fail_envelope(**kwargs):
        raise sandbox_module.EnvelopeLaunchError("envelope boom", code="envelope_launch_failed", phase="launch")
    monkeypatch.setattr(sandbox_module, "launch_envelope", fail_envelope)

    cleanup_steps = []
    real_cleanup = sandbox_module._cleanup_native_scratch_step
    def recording_cleanup(*args, **kwargs):
        step = real_cleanup(*args, **kwargs)
        cleanup_steps.append(step)
        return step
    monkeypatch.setattr(sandbox_module, "_cleanup_native_scratch_step", recording_cleanup)

    with pytest.raises(SandboxLaunchError) as exc_info:
        await harness.manager.open(fixture.request)
    assert exc_info.value.code == "envelope_launch_failed"
    assert not scratch_a.exists()
    assert cleanup_steps[0].state is CleanupState.RELEASED
    assert cleanup_steps[0].resource == "native_scratch"

    # 2. Envelope failure after identity recorded, replaced before cleanup -> QUARANTINED scratch_identity_mismatch
    monkeypatch.setattr(sandbox_module, "_cleanup_native_scratch_step", real_cleanup)
    nonce_b = "envelope-swap-sealed"
    harness.manager._nonce = lambda: nonce_b
    scratch_b = harness.manager.lease_root / f"lease-{nonce_b}.native-scratch"

    def swap_and_fail_envelope(**kwargs):
        scratch_b.rmdir()
        scratch_b.mkdir(mode=0o700)
        raise sandbox_module.EnvelopeLaunchError("envelope boom", code="envelope_launch_failed", phase="launch")
    monkeypatch.setattr(sandbox_module, "launch_envelope", swap_and_fail_envelope)

    with pytest.raises(SandboxFault) as exc_info:
        await harness.manager.open(fixture.request)
    receipt = next(s for s in exc_info.value.cleanup_receipt.steps if s.resource == "native_scratch")
    assert receipt.state is CleanupState.QUARANTINED
    assert receipt.detail == "scratch_identity_mismatch"
    assert scratch_b.is_dir()
