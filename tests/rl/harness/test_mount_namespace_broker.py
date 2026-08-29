from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import signal
import socket
import stat
import sys
from builtins import BaseExceptionGroup
from pathlib import Path
from types import SimpleNamespace

import pytest

import breadboard.rl.harness.mount_namespace_broker as broker_module
from breadboard.rl.harness.mount_namespace_broker import (
    _MAX_MESSAGE,
    MountNamespaceBroker,
    MountNamespaceBrokerError,
    _canonical,
    _decode,
    _receive,
    _send,
)
from breadboard.rl.harness.sandbox_docker import (
    DockerAdapterError,
    ExecutableInvocation,
)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "memfd_create"),
    reason="sealed memfd transport requires Linux",
)
async def test_broker_executor_transports_stdin_by_sealed_descriptor(
    tmp_path: Path,
) -> None:
    executable_path = tmp_path / "docker"
    executable_path.write_bytes(b"docker")
    executable_fd = os.open(executable_path, os.O_RDONLY)
    invocation = ExecutableInvocation(
        argv0=str(executable_path),
        executable_fd=executable_fd,
        executable_descriptor_path=f"/proc/self/fd/{executable_fd}",
        digest="sha256:" + hashlib.sha256(b"docker").hexdigest(),
    )
    payload = b"x" * (512 * 1024)
    output_payload = b"o" * (3 * 1024 * 1024)
    observed: dict[str, object] = {}

    class Broker:
        def _call(
            self,
            operation,
            request,
            descriptors,
            *,
            expected_return_fds,
        ):
            observed["operation"] = operation
            observed["request"] = request
            observed["descriptors"] = descriptors
            observed["expected_return_fds"] = expected_return_fds
            payload_fd = descriptors[1]
            observed["payload"] = os.pread(payload_fd, len(payload) + 1, 0)
            observed["seals"] = broker_module.fcntl.fcntl(
                payload_fd, broker_module.fcntl.F_GET_SEALS
            )
            stdout_fd = broker_module._sealed_payload_fd(output_payload)
            stderr_fd = broker_module._sealed_payload_fd(b"")
            return (
                {
                    "returncode": 0,
                    "stdout_size": len(output_payload),
                    "stdout_digest": (
                        "sha256:" + hashlib.sha256(output_payload).hexdigest()
                    ),
                    "stderr_size": 0,
                    "stderr_digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
                    "timed_out": False,
                    "output_limited": False,
                },
                (stdout_fd, stderr_fd),
            )

    try:
        result = await broker_module.BrokerDockerCliExecutor(Broker()).execute(
            invocation,
            ("exec", "-i", "container", "cat"),
            timeout_ms=1_000,
            output_limit=len(output_payload) + 1,
            environment=(),
            input_bytes=payload,
        )
    finally:
        os.close(executable_fd)

    request = observed["request"]
    assert observed["operation"] == "execute"
    assert observed["payload"] == payload
    assert request["input_size"] == len(payload)
    assert request["input_digest"] == ("sha256:" + hashlib.sha256(payload).hexdigest())
    required = (
        broker_module.fcntl.F_SEAL_SEAL
        | broker_module.fcntl.F_SEAL_SHRINK
        | broker_module.fcntl.F_SEAL_GROW
        | broker_module.fcntl.F_SEAL_WRITE
    )
    assert observed["seals"] & required == required
    assert observed["expected_return_fds"] == 2
    assert result.returncode == 0
    assert result.stdout == output_payload
    assert result.stderr == b""


def test_broker_wire_budget_covers_encoded_maximum_observation() -> None:
    expected = 4 * ((broker_module._MAX_ADMITTED_OUTPUT + 1 + 2) // 3) + 128

    assert broker_module._MAX_OUTPUT == expected
    assert broker_module._MAX_OUTPUT > broker_module._MAX_ADMITTED_OUTPUT


@pytest.mark.skipif(
    not hasattr(os, "memfd_create"),
    reason="sealed memfd transport requires Linux",
)
def test_direct_docker_execution_reads_sealed_output_descriptors(
    tmp_path: Path,
) -> None:
    executable_path = tmp_path / "docker"
    executable_path.write_bytes(b"docker")
    executable_fd = os.open(executable_path, os.O_RDONLY)
    broker = object.__new__(MountNamespaceBroker)
    broker.daemon_binding = SimpleNamespace(socket_path=str(tmp_path / "docker.sock"))
    broker._daemon_authority = SimpleNamespace(
        docker=SimpleNamespace(
            path=str(executable_path),
            digest="sha256:" + hashlib.sha256(b"docker").hexdigest(),
        )
    )
    broker._authority_fds = {"docker": executable_fd}
    output_payload = b"descriptor-output"
    returned: list[int] = []

    def call(
        operation: str,
        request: dict[str, object],
        descriptors: tuple[int, ...],
        *,
        expected_return_fds: int,
    ) -> tuple[dict[str, object], tuple[int, int]]:
        assert operation == "execute"
        assert request["output_limit"] == 1024
        assert descriptors == (executable_fd,)
        assert expected_return_fds == 2
        stdout_fd = broker_module._sealed_payload_fd(output_payload)
        stderr_fd = broker_module._sealed_payload_fd(b"")
        returned.extend((stdout_fd, stderr_fd))
        return (
            {
                "returncode": 0,
                "stdout_size": len(output_payload),
                "stdout_digest": (
                    "sha256:" + hashlib.sha256(output_payload).hexdigest()
                ),
                "stderr_size": 0,
                "stderr_digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
                "timed_out": False,
                "output_limited": False,
            },
            (stdout_fd, stderr_fd),
        )

    broker._call = call
    try:
        result = broker.execute_docker(("version",), output_limit=1024)
    finally:
        os.close(executable_fd)

    assert result.stdout == output_payload
    assert result.stderr == b""
    for descriptor in returned:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir() or not hasattr(os, "memfd_create"),
    reason="descriptor execution transport requires Linux procfs",
)
def test_broker_bounded_executor_reads_sealed_stdin_descriptor() -> None:
    payload = b"y" * (512 * 1024)
    executable_fd = os.open("/bin/cat", os.O_RDONLY)
    input_fd = broker_module._sealed_payload_fd(payload)
    try:
        result = broker_module._execute_bounded(
            ("/bin/cat",),
            executable_fd=executable_fd,
            timeout_ms=1_000,
            output_limit=len(payload) + 1,
            input_fd=input_fd,
        )
    finally:
        os.close(input_fd)
        os.close(executable_fd)

    returncode, stdout, stderr, timed_out, output_limited = result
    assert returncode == 0
    assert stdout == payload
    assert stderr == b""
    assert timed_out is False
    assert output_limited is False


def test_exception_projection_retains_all_daemon_cleanup_leaves_without_secrets() -> (
    None
):
    failures = [
        DockerAdapterError(
            f"daemon_cleanup_{index}",
            f"daemon cleanup leaf {index}",
            details={
                "errno": index + 1,
                "secret_value": "PRIVATE-DAEMON-TOKEN",
            },
        )
        for index in range(4)
    ]

    leaves = broker_module._exception_leaves(
        BaseExceptionGroup("private Docker daemon cleanup failed", failures),
        operation="shutdown",
    )

    assert len(leaves) == 4
    assert [leaf["code"] for leaf in leaves] == [
        f"daemon_cleanup_{index}" for index in range(4)
    ]
    assert all(
        set(leaf) == {"code", "type", "message", "operation", "details"}
        for leaf in leaves
    )
    assert all(leaf["operation"] == "shutdown" for leaf in leaves)
    assert [leaf["details"]["group_path"] for leaf in leaves] == [
        [index] for index in range(4)
    ]
    assert all(leaf["details"]["secret_value"] == "[redacted]" for leaf in leaves)
    assert b"PRIVATE-DAEMON-TOKEN" not in broker_module._canonical({"leaves": leaves})


def test_exception_projection_is_depth_count_and_byte_bounded() -> None:
    failures = [
        RuntimeError(f"cleanup leaf {index}: " + "x" * 2048) for index in range(64)
    ]

    leaves = broker_module._exception_leaves(
        BaseExceptionGroup("oversized cleanup", failures),
        operation="shutdown",
    )

    assert len(leaves) <= broker_module._ERROR_PROJECTION_MAX_LEAVES + 1
    assert leaves[-1]["code"] == "error_projection_truncated"
    assert len(broker_module._canonical({"leaves": leaves})) <= (
        broker_module._ERROR_PROJECTION_MAX_BYTES + 1024
    )


def test_progress_journal_is_canonical_fsynced_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "progress.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
    fsynced: list[int] = []
    monkeypatch.setattr(broker_module.os, "fsync", fsynced.append)
    try:
        journal = broker_module._ProgressJournal(fd, writer="test", limit=256)
        journal({"phase": "startup", "details": {"pid": 7}, "event": "spawn"})
        with pytest.raises(OSError) as raised:
            journal({"event": "x" * 512})
        assert raised.value.errno == 27
    finally:
        os.close(fd)
    assert fsynced == [fd]
    assert path.read_bytes() == (
        b'{"event":{"details":{"pid":7},"event":"spawn","phase":"startup"},'
        b'"sequence":1,"writer":"test"}\n'
    )


def test_large_daemon_logs_are_reconstructed_from_descriptors_not_rpc(
    tmp_path: Path,
) -> None:
    raw_by_role = {
        "containerd": b"c" * (512 * 1024),
        "dockerd": b"d" * (512 * 1024),
    }
    receipts: dict[str, dict[str, object]] = {}
    fds: list[int] = []
    for role, raw in raw_by_role.items():
        path = tmp_path / (role + ".log")
        path.write_bytes(raw)
        path.chmod(0o600)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        fds.append(fd)
        metadata = os.fstat(fd)
        receipts[role] = {
            "role": role,
            "size_bytes": len(raw),
            "mode": stat.S_IMODE(metadata.st_mode),
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
    try:
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    except OSError:
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        parent.settimeout(0.25)
        child.settimeout(0.25)
        broker_module._send(
            parent,
            {"logs": receipts, "ok": True},
            fds,
        )
        response, received = broker_module._receive(child)
        assert response["logs"] == receipts
        complete = broker_module._consume_log_fds(
            response["logs"], received, limit=1024 * 1024
        )
    finally:
        parent.close()
        child.close()
        for fd in fds:
            os.close(fd)
    for role, raw in raw_by_role.items():
        assert base64.b64decode(complete[role]["bytes_base64"], validate=True) == raw


def test_rpc_requires_bounded_canonical_documents() -> None:
    assert _canonical({"z": 1, "a": [True]}) == b'{"a":[true],"z":1}'
    assert _decode(b'{"a":[true],"z":1}') == {"a": [True], "z": 1}
    with pytest.raises(ValueError, match="canonical"):
        _decode(b'{"z":1, "a":[true]}')
    with pytest.raises(MountNamespaceBrokerError, match="fixed bound"):
        _canonical({"payload": "x" * _MAX_MESSAGE})


def test_parent_binding_rewrites_child_proc_paths_before_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    sentinel = object()

    def construct(**payload: object) -> object:
        calls.append(payload)
        return sentinel

    monkeypatch.setattr(broker_module, "PrivateDockerDaemonBinding", construct)
    result = broker_module._construct_parent_binding(
        {
            "config_fd": 91,
            "config_proc_path": "/proc/99991/fd/91",
            "runtime_fd": 92,
            "runtime_proc_path": "/proc/99991/fd/92",
            "runtime_registered_path": "/private/.runtime-bin/runc",
            "other": "preserved",
        },
        config_fd=11,
        runtime_fd=12,
        parent_pid=4242,
    )
    assert result is sentinel
    assert calls == [
        {
            "config_fd": 11,
            "config_proc_path": "/proc/4242/fd/11",
            "runtime_fd": 12,
            "runtime_proc_path": "/proc/4242/fd/12",
            "runtime_registered_path": "/private/.runtime-bin/runc",
            "other": "preserved",
        }
    ]


def test_rpc_scm_rights_preserves_descriptor_identity(tmp_path: Path) -> None:
    source = tmp_path / "authority"
    source.write_bytes(b"authority")
    descriptor = os.open(source, os.O_RDONLY)
    message_socket = (
        socket.SOCK_SEQPACKET if sys.platform == "linux" else socket.SOCK_DGRAM
    )
    left, right = socket.socketpair(socket.AF_UNIX, message_socket)
    try:
        _send(left, {"operation": "test"}, (descriptor,))
        document, received = _receive(right)
        assert document == {"operation": "test"}
        assert len(received) == 1
        assert (os.fstat(received[0]).st_dev, os.fstat(received[0]).st_ino) == (
            os.fstat(descriptor).st_dev,
            os.fstat(descriptor).st_ino,
        )
        os.close(received[0])
    finally:
        left.close()
        right.close()
        os.close(descriptor)


def _real_broker(stage_root: Path) -> MountNamespaceBroker:
    if sys.platform != "linux" or os.geteuid() != 0:
        pytest.skip("requires Linux mount namespace capability")
    try:
        return MountNamespaceBroker(stage_root)
    except MountNamespaceBrokerError as exc:
        if exc.details.get(
            "error"
        ) == "PermissionError" or "Operation not permitted" in str(exc.details):
            pytest.skip("kernel denied CLONE_NEWNS or mount capability")
        raise


def test_private_namespace_stage_validate_release_and_host_absence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workspace"
    source.mkdir(mode=0o700)
    (source / "sentinel").write_text("held", encoding="ascii")
    descriptor = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
    broker = _real_broker(tmp_path / "stages")
    try:
        parent_namespace = os.readlink("/proc/self/ns/mnt")
        assert broker.observation.mount_namespace != parent_namespace
        metadata = os.fstat(descriptor)
        staged = asyncio.run(
            broker.stage(
                descriptor,
                expected_device=metadata.st_dev,
                expected_inode=metadata.st_ino,
                directory=True,
                lease_id="lease-1",
                destination="/workspace",
            )
        )
        # The pathname exists globally because dentries are shared, but the bind mount is
        # private: the host sees the empty staging directory, never descriptor contents.
        assert os.path.isdir(staged.source_path)
        assert os.listdir(staged.source_path) == []
        asyncio.run(broker.validate(staged, descriptor))
        asyncio.run(broker.release(staged))
        assert not os.path.lexists(staged.source_path)
        broker.close()
        assert not os.path.lexists(broker.observation.stage_root)
    finally:
        if not broker._closed:
            try:
                broker.close()
            except MountNamespaceBrokerError:
                os.kill(broker.pid, 9)
                os.waitpid(broker.pid, 0)
        os.close(descriptor)


def test_stage_replacement_is_rejected_and_cleanup_is_quarantined(
    tmp_path: Path,
) -> None:
    source = tmp_path / "profile"
    source.write_bytes(b"profile")
    descriptor = os.open(source, os.O_RDONLY)
    broker = _real_broker(tmp_path / "stages")
    staged = None
    try:
        metadata = os.fstat(descriptor)
        staged = asyncio.run(
            broker.stage(
                descriptor,
                expected_device=metadata.st_dev,
                expected_inode=metadata.st_ino,
                directory=False,
                lease_id="lease-2",
                destination="/profile",
            )
        )
        replacement = Path(staged.source_path + ".replacement")
        replacement.write_bytes(b"replacement")
        os.rename(replacement, staged.source_path)
        with pytest.raises(MountNamespaceBrokerError, match="rejected"):
            asyncio.run(broker.validate(staged, descriptor))
        with pytest.raises(BaseExceptionGroup, match="broker cleanup failed"):
            broker.close()
        assert broker._resources_closed is True
        assert not os.path.lexists(broker.observation.stage_root)
    finally:
        if not broker._resources_closed:
            try:
                broker.close()
            except BaseException:
                pass
        os.close(descriptor)


@pytest.mark.parametrize(
    "mutation",
    ["mode", "size", "same-size-content", "link-count", "owner", "path-swap"],
)
def test_stage_publication_rejects_bind_time_authority_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    source = tmp_path / "authority"
    source.write_bytes(b"content")
    descriptor = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    source_identity = (
        os.fstat(descriptor).st_dev,
        os.fstat(descriptor).st_ino,
    )
    original_bind = broker_module._bind

    def racing_bind(
        source_fd: int,
        target: str,
        *,
        readonly: bool,
    ) -> None:
        metadata = os.fstat(source_fd)
        if (metadata.st_dev, metadata.st_ino) == source_identity:
            if mutation == "mode":
                source.chmod(0o640)
            elif mutation == "size":
                source.write_bytes(b"content-expanded")
            elif mutation == "same-size-content":
                source.write_bytes(b"changed")
            elif mutation == "link-count":
                os.link(source, tmp_path / "authority-link")
            elif mutation == "owner":
                os.chown(source, 1, 1)
            else:
                source.rename(tmp_path / "authority-original")
                source.write_bytes(b"content")
        original_bind(source_fd, target, readonly=readonly)

    monkeypatch.setattr(broker_module, "_bind", racing_bind)
    broker = _real_broker(tmp_path / "stages")
    try:
        metadata = os.fstat(descriptor)
        with pytest.raises(MountNamespaceBrokerError) as raised:
            asyncio.run(
                broker.stage(
                    descriptor,
                    expected_device=metadata.st_dev,
                    expected_inode=metadata.st_ino,
                    directory=False,
                    lease_id="race",
                    destination="/authority",
                )
            )
        assert raised.value.code == "workspace_authority_mismatch"
        assert (
            raised.value.details["message"]
            == "mounted stage authority identity changed"
        )
        broker.close()
        assert broker._resources_closed is True
        assert not os.path.lexists(broker.observation.stage_root)
    finally:
        if not broker._resources_closed:
            try:
                broker.close()
            except BaseException:
                pass
        os.close(descriptor)


def test_wrong_descriptor_and_broker_crash_fail_closed(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_fd = os.open(first, os.O_RDONLY | os.O_DIRECTORY)
    second_fd = os.open(second, os.O_RDONLY | os.O_DIRECTORY)
    broker = _real_broker(tmp_path / "stages")
    try:
        metadata = os.fstat(first_fd)
        staged = asyncio.run(
            broker.stage(
                first_fd,
                expected_device=metadata.st_dev,
                expected_inode=metadata.st_ino,
                directory=True,
                lease_id="lease-3",
                destination="/workspace",
            )
        )
        with pytest.raises(DockerAdapterError) as raised:
            asyncio.run(broker.validate(staged, second_fd))
        assert raised.value.code == "workspace_authority_mismatch"
        assert str(raised.value) == "staged Docker descriptor identity changed"
        os.kill(broker.pid, 9)
        with pytest.raises(MountNamespaceBrokerError, match="crashed|quarantined"):
            asyncio.run(broker.validate(staged, first_fd))
        with pytest.raises(BaseExceptionGroup, match="broker cleanup failed"):
            broker.close()
        assert broker._resources_closed is True
        assert not os.path.lexists(broker.observation.stage_root)
    finally:
        if not broker._resources_closed:
            broker._socket.close()
        os.close(first_fd)
        os.close(second_fd)


def test_runtime_tmpfs_details_require_exact_separate_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    size = 2 * 1024 * 1024
    monkeypatch.setattr(
        broker_module,
        "_mountinfo",
        lambda: (
            b"42 1 0:7 / /private/runtime\\040dir "
            b"ro,nosuid,nodev,relatime - tmpfs tmpfs "
            b"ro,size=2097152,nr_inodes=16,mode=755,inode64\n"
        ),
    )
    assert broker_module._runtime_tmpfs_details(
        "/private/runtime dir", expected_size=size, expected_readonly=True
    ) == (
        42,
        True,
        "tmpfs",
        "nodev,nosuid,relatime,ro",
        "inode64,mode=755,nr_inodes=16,ro,size=2097152",
    )
    for changed in (
        b"ro,nosuid,nodev,noexec",
        b"ro,size=2097151,nr_inodes=16,mode=755",
        b"ro,size=2097152,nr_inodes=15,mode=755",
        b"ro,size=2097152,nr_inodes=16,mode=700",
    ):
        monkeypatch.setattr(
            broker_module,
            "_mountinfo",
            lambda changed=changed: (
                b"42 1 0:7 / /private/runtime\\040dir "
                + (
                    changed
                    if b"noexec" in changed
                    else b"ro,nosuid,nodev,relatime - tmpfs tmpfs " + changed
                )
                + (
                    b" - tmpfs tmpfs ro,size=2097152,nr_inodes=16,mode=755\n"
                    if b"noexec" in changed
                    else b"\n"
                )
            ),
        )
        with pytest.raises(OSError, match="options"):
            broker_module._runtime_tmpfs_details(
                "/private/runtime dir", expected_size=size, expected_readonly=True
            )


@pytest.mark.parametrize(
    ("mount_access", "super_access", "expected_readonly", "accepted"),
    [
        ("rw", "rw", False, True),
        ("ro", "ro", True, True),
        ("ro", "rw", True, False),
        ("rw", "ro", False, False),
        ("rw,rw", "rw", False, False),
        ("ro", "ro,ro", True, False),
        ("", "rw", False, False),
        ("rw", "", False, False),
    ],
)
def test_runtime_tmpfs_access_state_matches_both_option_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    mount_access: str,
    super_access: str,
    expected_readonly: bool,
    accepted: bool,
) -> None:
    monkeypatch.setattr(
        broker_module,
        "_mountinfo",
        lambda: (
            "51 1 0:9 / /runtime "
            f"{mount_access},nosuid,nodev,relatime - tmpfs tmpfs "
            f"{super_access},size=2048k,nr_inodes=16,mode=755,inode64\n"
        ).encode("ascii"),
    )
    if accepted:
        details = broker_module._runtime_tmpfs_details(
            "/runtime",
            expected_size=2 * 1024 * 1024,
            expected_readonly=expected_readonly,
        )
        assert details[1] is expected_readonly
    else:
        with pytest.raises(OSError, match="options"):
            broker_module._runtime_tmpfs_details(
                "/runtime",
                expected_size=2 * 1024 * 1024,
                expected_readonly=expected_readonly,
            )


def test_runtime_copy_retries_interrupts_and_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"runtime-authority"
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_bytes(payload)
    source_fd = os.open(source, os.O_RDONLY)
    real_pread = os.pread
    real_write = os.write
    pread_calls = 0
    write_calls = 0

    def interrupted_pread(fd: int, size: int, offset: int) -> bytes:
        nonlocal pread_calls
        pread_calls += 1
        if pread_calls == 1:
            raise InterruptedError
        return real_pread(fd, min(size, 3), offset)

    def short_write(fd: int, value: bytes) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            raise InterruptedError
        return real_write(fd, value[:2])

    monkeypatch.setattr(broker_module.os, "pread", interrupted_pread)
    monkeypatch.setattr(broker_module.os, "write", short_write)
    try:
        broker_module._copy_runtime_authority(
            source_fd, str(target), len(payload), 0o751
        )
    finally:
        os.close(source_fd)
    assert target.read_bytes() == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o751
    assert pread_calls > 1 and write_calls > 1


@pytest.mark.parametrize("failure", ["open", "chmod", "fsync"])
def test_runtime_copy_failures_close_descriptors_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"runc")
    source_fd = os.open(source, os.O_RDONLY)
    before = len(os.listdir("/dev/fd"))
    if failure == "open":
        monkeypatch.setattr(
            broker_module.os,
            "open",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("open")),
        )
    elif failure == "chmod":
        monkeypatch.setattr(
            broker_module.os,
            "fchmod",
            lambda *args: (_ for _ in ()).throw(OSError("chmod")),
        )
    else:
        monkeypatch.setattr(
            broker_module.os,
            "fsync",
            lambda *args: (_ for _ in ()).throw(OSError("fsync")),
        )
    with pytest.raises(OSError, match=failure):
        broker_module._copy_runtime_authority(
            source_fd, str(tmp_path / "target"), 4, 0o755
        )
    assert len(os.listdir("/dev/fd")) == before
    os.close(source_fd)


def test_runtime_cleanup_detaches_failed_unmount_and_proves_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    mount_ids = iter((73, OSError("absent")))
    monkeypatch.setattr(
        broker_module,
        "_mount_id",
        lambda *_args: (
            (_ for _ in ()).throw(value)
            if isinstance((value := next(mount_ids)), OSError)
            else value
        ),
    )
    monkeypatch.setattr(
        broker_module,
        "_unmount",
        lambda *_args: (_ for _ in ()).throw(OSError("busy")),
    )
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(broker_module, "_libc_call", lambda *args: calls.append(args))
    broker_module._remove_runtime_bind(str(runtime_dir / "runc"), str(runtime_dir))
    assert calls[0][0] == "umount2"
    assert not runtime_dir.exists()


def test_runtime_validation_rejects_snapshot_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    runtime = runtime_dir / "runc"
    runtime.write_bytes(b"changed")
    runtime.chmod(0o755)
    metadata = runtime.stat()
    monkeypatch.setattr(
        broker_module, "_runtime_tmpfs_observation", lambda *_a, **_k: (41, True)
    )
    with pytest.raises(OSError, match="authority changed"):
        broker_module._validate_runtime_copy(
            str(runtime),
            str(runtime_dir),
            expected_device=metadata.st_dev,
            expected_inode=metadata.st_ino,
            expected_size=metadata.st_size,
            expected_mode=0o755,
            expected_digest="sha256:" + hashlib.sha256(b"original").hexdigest(),
            expected_mount_id=41,
            expected_tmpfs_size=2 * 1024 * 1024,
        )


def test_runtime_tmpfs_mount_uses_rounded_bound_and_exact_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(broker_module.os, "sysconf", lambda _name: 4096)
    monkeypatch.setattr(broker_module, "_libc_call", lambda *args: calls.append(args))
    monkeypatch.setattr(
        broker_module,
        "_runtime_tmpfs_observation",
        lambda target, *, expected_size, expected_readonly: (99, False),
    )
    size = broker_module._mount_runtime_tmpfs("/runtime", 4097)
    assert size == 4097 + broker_module._RUNTIME_TMPFS_OVERHEAD + 4095 >> 12 << 12
    assert calls[0][0] == "mount"
    assert calls[0][4].value == broker_module._MS_NOSUID | broker_module._MS_NODEV
    assert f"size={size}".encode() in calls[0][5].value
    assert b"nr_inodes=16" in calls[0][5].value


def test_runtime_tmpfs_mount_and_seal_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(broker_module.os, "sysconf", lambda _name: 4096)
    monkeypatch.setattr(
        broker_module,
        "_libc_call",
        lambda *args: (_ for _ in ()).throw(OSError("mount")),
    )
    with pytest.raises(OSError, match="mount"):
        broker_module._mount_runtime_tmpfs("/runtime", 1)
    monkeypatch.setattr(broker_module, "_libc_call", lambda *args: None)
    monkeypatch.setattr(
        broker_module,
        "_runtime_tmpfs_observation",
        lambda *_args, **_kwargs: (99, False),
    )
    with pytest.raises(OSError, match="not read-only"):
        broker_module._seal_runtime_tmpfs("/runtime", expected_size=4096)


def test_runtime_mount_and_seal_pass_explicit_access_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: list[bool] = []
    monkeypatch.setattr(broker_module.os, "sysconf", lambda _name: 4096)
    monkeypatch.setattr(broker_module, "_libc_call", lambda *args: None)

    def observe(
        _target: str, *, expected_size: int, expected_readonly: bool
    ) -> tuple[int, bool]:
        states.append(expected_readonly)
        return 91, expected_readonly

    monkeypatch.setattr(broker_module, "_runtime_tmpfs_observation", observe)
    size = broker_module._mount_runtime_tmpfs("/runtime", 1)
    broker_module._seal_runtime_tmpfs("/runtime", expected_size=size)
    assert states == [False, True]


def test_startup_snapshot_fd_cardinality_transfers_or_closes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot"
    path.write_bytes(b"snapshot")
    first = os.open(path, os.O_RDONLY)
    assert broker_module._accept_startup_snapshot_fd((first,), required=True) == first
    assert os.fstat(first).st_size == 8
    os.close(first)
    assert broker_module._accept_startup_snapshot_fd((), required=False) is None
    invalid = os.open(path, os.O_RDONLY)
    with pytest.raises(MountNamespaceBrokerError, match="descriptor count"):
        broker_module._accept_startup_snapshot_fd((invalid,), required=False)
    with pytest.raises(OSError):
        os.fstat(invalid)


def test_startup_snapshot_fd_missing_is_rejected_without_leak() -> None:
    before = len(os.listdir("/dev/fd"))
    with pytest.raises(MountNamespaceBrokerError, match="descriptor count"):
        broker_module._accept_startup_snapshot_fd((), required=True)
    assert len(os.listdir("/dev/fd")) == before


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        (b"size=4096", 4096),
        (b"size=4k", 4096),
        (b"size=2m", 2 * 1024 * 1024),
    ],
)
def test_tmpfs_size_parser_normalizes_kernel_suffixes(
    option: bytes, expected: int
) -> None:
    assert broker_module._tmpfs_size_bytes(option) == expected


@pytest.mark.parametrize(
    "option",
    [b"size=0", b"size=", b"size=-1", b"size=1K", b"size=999999999g"],
)
def test_tmpfs_size_parser_rejects_invalid_or_overflowing_values(
    option: bytes,
) -> None:
    with pytest.raises(OSError, match="size option"):
        broker_module._tmpfs_size_bytes(option)


def test_runtime_tmpfs_details_accept_kernel_k_and_reject_duplicate_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = b"7 1 0:8 / /runtime ro,nosuid,nodev - tmpfs tmpfs "
    monkeypatch.setattr(
        broker_module,
        "_mountinfo",
        lambda: prefix + b"ro,size=2048k,nr_inodes=16,mode=755\n",
    )
    details = broker_module._runtime_tmpfs_details(
        "/runtime", expected_size=2 * 1024 * 1024, expected_readonly=True
    )
    assert details[0:2] == (7, True)
    monkeypatch.setattr(
        broker_module,
        "_mountinfo",
        lambda: prefix + b"ro,size=2048k,size=2097152,nr_inodes=16,mode=755\n",
    )
    with pytest.raises(OSError, match="options"):
        broker_module._runtime_tmpfs_details(
            "/runtime", expected_size=2 * 1024 * 1024, expected_readonly=True
        )


def test_runtime_tmpfs_details_reject_non_page_exact_effective_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        broker_module,
        "_mountinfo",
        lambda: (
            b"7 1 0:8 / /runtime ro,nosuid,nodev - tmpfs tmpfs "
            b"ro,size=2047k,nr_inodes=16,mode=755\n"
        ),
    )
    with pytest.raises(OSError, match="options"):
        broker_module._runtime_tmpfs_details(
            "/runtime", expected_size=2 * 1024 * 1024, expected_readonly=True
        )


def test_dead_broker_cleanup_removes_nested_runtime_placeholders(
    tmp_path: Path,
) -> None:
    stage_root = tmp_path / "stages"
    runtime_dir = stage_root / ".runtime-bin"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "runc").write_bytes(b"placeholder")
    broker = object.__new__(MountNamespaceBroker)
    broker._stage_root = str(stage_root)
    broker._receipts = {}
    broker._cleanup_dead_placeholders()
    assert not stage_root.exists()


def test_startup_cleanup_failure_preserves_primary_error() -> None:
    primary = ValueError("malformed observation")
    with pytest.raises(ValueError) as reraised:
        broker_module._raise_after_startup_cleanup(primary, lambda: None)
    assert reraised.value is primary

    def fail_cleanup() -> None:
        raise OSError("residue")

    with pytest.raises(BaseExceptionGroup) as raised:
        broker_module._raise_after_startup_cleanup(primary, fail_cleanup)
    assert raised.value.exceptions == (primary, raised.value.exceptions[1])
    assert isinstance(raised.value.exceptions[1], OSError)
    assert str(raised.value.exceptions[1]) == "residue"


def test_receive_closes_rights_before_later_unexpected_control(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fd"
    path.write_bytes(b"x")
    fd = os.open(path, os.O_RDONLY)

    class FakeSocket:
        def recvmsg(
            self, *_args: object
        ) -> tuple[bytes, list[tuple[int, int, bytes]], int, None]:
            packed = broker_module.array.array("i", [fd]).tobytes()
            return (
                b'{"ok":true}',
                [
                    (socket.SOL_SOCKET, socket.SCM_RIGHTS, packed),
                    (socket.SOL_SOCKET, -1, b""),
                ],
                0,
                None,
            )

    with pytest.raises(ValueError, match="unexpected"):
        broker_module._receive(FakeSocket())  # type: ignore[arg-type]
    with pytest.raises(OSError):
        os.fstat(fd)


def test_receive_closes_rights_on_control_truncation(tmp_path: Path) -> None:
    path = tmp_path / "fd"
    path.write_bytes(b"x")
    fd = os.open(path, os.O_RDONLY)

    class FakeSocket:
        def recvmsg(
            self, *_args: object
        ) -> tuple[bytes, list[tuple[int, int, bytes]], int, None]:
            packed = broker_module.array.array("i", [fd]).tobytes()
            return (
                b'{"ok":true}',
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, packed)],
                socket.MSG_CTRUNC,
                None,
            )

    with pytest.raises(ValueError, match="truncated"):
        broker_module._receive(FakeSocket())  # type: ignore[arg-type]
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.skipif(sys.platform != "linux", reason="requires procfd execution")
def test_bounded_executor_enforces_combined_dual_stream_limit() -> None:
    fd = os.open(sys.executable, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = broker_module._execute_bounded(
            (
                sys.executable,
                "-c",
                "import os; os.write(1,b'o'*65536); os.write(2,b'e'*65536)",
            ),
            executable_fd=fd,
            timeout_ms=5_000,
            output_limit=4096,
        )
    finally:
        os.close(fd)
    assert len(result[1]) + len(result[2]) == 4096
    assert result[4] is True


@pytest.mark.skipif(sys.platform != "linux", reason="requires procfd execution")
def test_bounded_executor_times_out_and_reaps_process_group() -> None:
    fd = os.open(sys.executable, os.O_RDONLY | os.O_CLOEXEC)
    try:
        result = broker_module._execute_bounded(
            (sys.executable, "-c", "import time; time.sleep(60)"),
            executable_fd=fd,
            timeout_ms=100,
            output_limit=4096,
        )
    finally:
        os.close(fd)
    assert result[3] is True
    assert result[0] < 0


@pytest.mark.skipif(sys.platform != "linux", reason="requires procfd execution")
def test_bounded_executor_does_not_wait_for_descendant_held_pipe() -> None:
    fd = os.open(sys.executable, os.O_RDONLY | os.O_CLOEXEC)
    started = broker_module.time.monotonic()
    try:
        result = broker_module._execute_bounded(
            (
                sys.executable,
                "-c",
                "import os,time; p=os.fork(); "
                "os.write(1,(str(p)+'\\n').encode()) if p else time.sleep(60); "
                "os._exit(0)",
            ),
            executable_fd=fd,
            timeout_ms=500,
            output_limit=4096,
        )
    finally:
        os.close(fd)
    assert broker_module.time.monotonic() - started < 2
    assert result[3] is False
    descendant_pid = int(result[1].strip())
    assert not os.path.lexists(f"/proc/{descendant_pid}")


def test_close_after_shutdown_failure_reaps_closes_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Socket:
        closed = False

        def close(self) -> None:
            self.closed = True

    stage_root = tmp_path / "stages"
    stage_root.mkdir()
    authority_path = tmp_path / "authority"
    authority_path.write_bytes(b"x")
    authority_fd = os.open(authority_path, os.O_RDONLY | os.O_CLOEXEC)
    broker = object.__new__(MountNamespaceBroker)
    broker._resources_closed = False
    broker._receipts = {}
    broker._closed = False
    broker._reaped = False
    broker._wait_status = None
    broker._pid = 777
    broker._daemon_authority = None
    broker._progress_sink = None
    broker._stage_root = os.fspath(stage_root)
    broker._socket = Socket()
    broker._authority_fds = {"authority": authority_fd}
    broker._call = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("shutdown failed")
    )
    waits = iter(((0, 0), (777, signal.SIGKILL)))
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(broker_module.os, "waitpid", lambda *_args: next(waits))
    monkeypatch.setattr(
        broker_module.os,
        "kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(BaseExceptionGroup) as raised:
        broker.close()

    assert str(raised.value.exceptions[0]) == "shutdown failed"
    assert broker._resources_closed is True
    assert broker._reaped is True
    assert broker._socket.closed is True
    assert killed == [(777, signal.SIGKILL)]
    assert not stage_root.exists()
    with pytest.raises(OSError):
        os.fstat(authority_fd)
    assert broker.close() is None


def test_close_after_log_receipt_failure_reaps_and_closes_returned_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Socket:
        closed = False

        def close(self) -> None:
            self.closed = True

    stage_root = tmp_path / "stages"
    stage_root.mkdir()
    log_fds: list[int] = []
    for name in ("containerd", "dockerd"):
        path = tmp_path / f"{name}.log"
        path.write_bytes(b"log")
        log_fds.append(os.open(path, os.O_RDONLY | os.O_CLOEXEC))
    broker = object.__new__(MountNamespaceBroker)
    broker._resources_closed = False
    broker._receipts = {}
    broker._closed = False
    broker._reaped = False
    broker._wait_status = None
    broker._pid = 778
    broker._daemon_authority = SimpleNamespace(
        log_limit_bytes=1024,
        socket_path=os.fspath(tmp_path / "missing-docker.sock"),
        containerd_socket_path=os.fspath(tmp_path / "missing-containerd.sock"),
        pid_file=os.fspath(tmp_path / "missing.pid"),
        config_path=os.fspath(tmp_path / "missing.toml"),
        exec_root=os.fspath(tmp_path / "missing-exec"),
        data_root=os.fspath(tmp_path / "missing-data"),
        containerd_root=os.fspath(tmp_path / "missing-containerd-root"),
        containerd_state=os.fspath(tmp_path / "missing-containerd-state"),
        log_root=os.fspath(tmp_path / "missing-logs"),
    )
    broker._progress_sink = None
    broker._stage_root = os.fspath(stage_root)
    broker._socket = Socket()
    broker._authority_fds = {}
    broker._call = lambda *_args, **_kwargs: (
        {"daemon_logs": {}},
        tuple(log_fds),
    )
    monkeypatch.setattr(
        broker_module.os,
        "waitpid",
        lambda *_args: (778, 0),
    )

    with pytest.raises(BaseExceptionGroup) as raised:
        broker.close()

    assert "descriptor order is invalid" in str(raised.value.exceptions[0])
    assert broker._resources_closed is True
    assert broker._reaped is True
    assert broker._socket.closed is True
    assert not stage_root.exists()
    for fd in log_fds:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert broker.close() is None


@pytest.mark.parametrize("failure", ["progress", "wait", "sweep"])
def test_close_faults_still_release_parent_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    class Socket:
        closed = False

        def close(self) -> None:
            self.closed = True

    stage_root = tmp_path / "stages"
    stage_root.mkdir()
    broker = object.__new__(MountNamespaceBroker)
    broker._resources_closed = False
    broker._receipts = {}
    broker._closed = False
    broker._reaped = False
    broker._wait_status = None
    broker._pid = 779
    broker._daemon_authority = None
    broker._progress_sink = None
    broker._stage_root = os.fspath(stage_root)
    broker._socket = Socket()
    broker._authority_fds = {}
    broker._call = lambda *_args, **_kwargs: {}
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        broker_module.os,
        "kill",
        lambda pid, sig: killed.append((pid, sig)),
    )
    monkeypatch.setattr(
        broker_module.os,
        "waitpid",
        lambda *_args: (0, 0),
    )
    if failure == "progress":
        broker._progress_sink = lambda _event: (_ for _ in ()).throw(
            OSError("progress failed")
        )
        monkeypatch.setattr(
            broker_module,
            "_waitpid_bounded",
            lambda _pid, _timeout: (779, signal.SIGKILL),
        )
    elif failure == "wait":
        waits = iter(
            (
                TimeoutError("wait failed"),
                (779, signal.SIGKILL),
            )
        )

        def fail_wait_once(
            _pid: int,
            _timeout: float,
        ) -> tuple[int, int]:
            result = next(waits)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(
            broker_module,
            "_waitpid_bounded",
            fail_wait_once,
        )
    else:
        monkeypatch.setattr(
            broker,
            "_cleanup_dead_placeholders",
            lambda: (_ for _ in ()).throw(OSError("sweep failed")),
        )
        monkeypatch.setattr(
            broker_module,
            "_waitpid_bounded",
            lambda _pid, _timeout: (779, 0),
        )

    with pytest.raises(BaseExceptionGroup):
        broker.close()

    assert broker._resources_closed is True
    assert broker._socket.closed is True
    assert broker.close() is None
    if stage_root.exists():
        stage_root.rmdir()


def test_non_graceful_close_identity_terminates_both_daemon_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Socket:
        closed = False

        def close(self) -> None:
            self.closed = True

    def missing(name: str) -> str:
        return os.fspath(tmp_path / name)

    broker = object.__new__(MountNamespaceBroker)
    broker._resources_closed = False
    broker._receipts = {}
    broker._closed = False
    broker._reaped = False
    broker._wait_status = None
    broker._pid = 780
    broker._daemon_authority = SimpleNamespace(
        socket_path=missing("docker.sock"),
        containerd_socket_path=missing("containerd.sock"),
        pid_file=missing("docker.pid"),
        config_path=missing("docker.toml"),
        exec_root=missing("exec"),
        data_root=missing("data"),
        containerd_root=missing("containerd-root"),
        containerd_state=missing("containerd-state"),
        log_root=missing("logs"),
    )
    broker.daemon_binding = object()
    broker.containerd_observation = {"pid": 702}
    broker._progress_sink = lambda _event: (_ for _ in ()).throw(
        OSError("progress failed")
    )
    stage_root = tmp_path / "stages"
    stage_root.mkdir()
    broker._stage_root = os.fspath(stage_root)
    broker._socket = Socket()
    broker._authority_fds = {}
    broker._call = lambda *_args, **_kwargs: {}
    terminated: list[tuple[dict[str, int], bool]] = []
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(
        broker_module,
        "asdict",
        lambda _binding: {"daemon_pid": 701},
    )
    monkeypatch.setattr(
        broker_module,
        "_terminate_identity_group",
        lambda observation, *, broker_pid, daemon: terminated.append(
            (dict(observation), daemon)
        ),
    )
    monkeypatch.setattr(
        broker_module.os,
        "waitpid",
        lambda *_args: (0, 0),
    )
    monkeypatch.setattr(
        broker_module,
        "_waitpid_bounded",
        lambda _pid, _timeout: (780, signal.SIGKILL),
    )
    monkeypatch.setattr(
        broker_module.os,
        "kill",
        lambda pid, sig: events.append(("kill", pid)),
    )

    with pytest.raises(BaseExceptionGroup):
        broker.close()

    assert terminated == [
        ({"daemon_pid": 701}, True),
        ({"pid": 702}, False),
    ]
    assert events == [("kill", 780)]
    assert broker._resources_closed is True
    assert not stage_root.exists()


def test_failed_startup_uses_authenticated_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        timeout: float | None = None
        closed = False

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def close(self) -> None:
            self.closed = True

    sock = FakeSocket()
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        broker_module,
        "_send",
        lambda _sock, document, _fds=(): sent.append(dict(document)),
    )
    monkeypatch.setattr(
        broker_module,
        "_receive",
        lambda _sock: ({"ok": True, "token": "t", "sequence": 1}, ()),
    )
    monkeypatch.setattr(broker_module, "_wait_reaped", lambda pid, timeout: True)
    broker_module._cleanup_failed_broker_process(
        sock,  # type: ignore[arg-type]
        pid=77,
        token="t",
        response={"ok": True, "token": "t", "sequence": 0},
    )
    assert sent == [{"operation": "shutdown", "sequence": 1, "token": "t"}]
    assert sock.timeout == 30.0
    assert sock.closed


def test_failed_startup_fallback_pins_both_descendant_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        def settimeout(self, _value: float) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        broker_module,
        "_send",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("send")),
    )
    waits = iter((False, True))
    monkeypatch.setattr(broker_module, "_wait_reaped", lambda pid, timeout: next(waits))
    terminated: list[tuple[dict[str, object], int, bool]] = []
    monkeypatch.setattr(
        broker_module,
        "_terminate_identity_group",
        lambda observation, *, broker_pid, daemon: terminated.append(
            (dict(observation), broker_pid, daemon)
        ),
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        broker_module.os, "kill", lambda pid, sig: killed.append((pid, sig))
    )
    binding = {
        "daemon_pid": 81,
        "daemon_starttime": "1",
        "daemon_executable_device": 2,
        "daemon_executable_inode": 3,
    }
    containerd = {
        "pid": 82,
        "starttime": "2",
        "executable_device": 4,
        "executable_inode": 5,
    }
    broker_module._cleanup_failed_broker_process(
        FakeSocket(),  # type: ignore[arg-type]
        pid=77,
        token="t",
        response={
            "ok": True,
            "token": "t",
            "sequence": 0,
            "daemon": {"binding": binding, "containerd": containerd},
        },
    )
    assert terminated == [(binding, 77, True), (containerd, 77, False)]
    assert killed == [(77, signal.SIGKILL)]


def test_lifecycle_probe_supplied_binary_excludes_system_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.rl_phase5 import f2_private_broker_lifecycle_probe as probe

    supplied = tmp_path / "dockerd"
    supplied.write_bytes(b"operator-pinned")
    system = Path("/usr/bin/dockerd")
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: self in {supplied, system},
    )
    monkeypatch.setattr(Path, "is_symlink", lambda _self: False)
    assert probe.fixed_binary("dockerd", str(supplied)) == supplied


def test_lifecycle_probe_scratch_cleanup_failure_is_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.rl_phase5 import f2_private_broker_lifecycle_probe as probe

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(
        probe.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("remove failed")),
    )
    with pytest.raises(OSError, match="remove failed"):
        probe.remove_scratch_exact(scratch)
    assert scratch.exists()


def _probe_setup_args(tmp_path: Path) -> object:
    from scripts.rl_phase5 import f2_private_broker_lifecycle_probe as probe

    binaries: dict[str, str] = {}
    for name in ("dockerd", "docker", "containerd", "runc"):
        path = tmp_path / name
        path.write_bytes(b"binary-" + name.encode())
        path.chmod(0o755)
        binaries[name] = str(path)
    archive = tmp_path / "archive.tar"
    archive.write_bytes(b"unused-stage-diagnostic")
    progress_parent = tmp_path / "progress"
    progress_parent.mkdir(mode=0o700)
    return probe.argparse.Namespace(
        attempt_id="early-cleanup-test",
        offline_image_tar=str(archive),
        image_id=None,
        source_image_digest=None,
        storage_driver="vfs",
        scratch_parent=str(tmp_path),
        progress_path=str(progress_parent / "journal.jsonl"),
        stage_diagnostic=True,
        **binaries,
    )


@pytest.mark.skipif(os.geteuid() != 0, reason="requires root-owned probe fixtures")
def test_probe_failure_after_progress_open_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.rl_phase5 import f2_private_broker_lifecycle_probe as probe

    args = _probe_setup_args(tmp_path)
    before = len(os.listdir("/dev/fd"))
    monkeypatch.setattr(
        probe,
        "_ProgressJournal",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("journal")),
    )
    with pytest.raises(OSError, match="journal"):
        asyncio.run(probe.lifecycle(args))
    assert len(os.listdir("/dev/fd")) == before
    assert not tuple(tmp_path.glob("f2-private-broker-*"))


@pytest.mark.skipif(os.geteuid() != 0, reason="requires root-owned probe fixtures")
def test_probe_failure_after_scratch_creation_removes_exact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.rl_phase5 import f2_private_broker_lifecycle_probe as probe

    args = _probe_setup_args(tmp_path)
    before = len(os.listdir("/dev/fd"))
    monkeypatch.setattr(
        probe,
        "executor_self_probes",
        lambda: (_ for _ in ()).throw(OSError("self-probe")),
    )
    with pytest.raises(OSError, match="self-probe"):
        asyncio.run(probe.lifecycle(args))
    assert len(os.listdir("/dev/fd")) == before
    assert not tuple(tmp_path.glob("f2-private-broker-*"))


def test_recovery_authenticates_lease_and_finalizes_observed_absence(
    tmp_path: Path,
) -> None:
    journal_root = tmp_path / "journals"
    lease_root = tmp_path / "leases"
    journal_root.mkdir(mode=0o700)
    lease_root.mkdir(mode=0o700)
    lease_id = "lease-recovery"
    owner_token = "owner-token"
    digest = "sha256:" + "1" * 64
    process = {
        "pid": 9_999_999,
        "starttime": "1",
        "pgid": 9_999_999,
        "executable_device": 1,
        "executable_inode": 1,
        "executable_ctime_ns": 1,
        "executable_size": 1,
        "executable_digest": digest,
        "namespace_device": 1,
        "namespace_inode": 1,
    }
    parent = tmp_path.stat()
    daemon_root_path = tmp_path / "absent-daemon"
    daemon_root = {
        "path": str(daemon_root_path),
        "device": 1,
        "inode": 1,
        "mode": 0o700,
        "digest": digest,
        "parent_path": str(tmp_path),
        "parent_device": parent.st_dev,
        "parent_inode": parent.st_ino,
    }
    path = {
        "path": str(daemon_root_path / "config.json"),
        "device": 1,
        "inode": 1,
        "mode": 0o600,
        "digest": digest,
        "parent_path": str(daemon_root_path),
        "parent_device": 1,
        "parent_inode": 1,
    }
    payload = {
        "schema_version": broker_module.SUPERVISOR_JOURNAL_SCHEMA_VERSION,
        "state": "ACTIVE",
        "generation": (f"{lease_id}:workspace-recovery:1:primary:{digest}"),
        "generation_digest": broker_module._journal_digest(
            f"{lease_id}:workspace-recovery:1:primary:{digest}".encode("utf-8")
        ),
        "owner_token_digest": broker_module._journal_digest(
            owner_token.encode("utf-8")
        ),
        "lease_id": lease_id,
        "workspace_id": "workspace-recovery",
        "epoch": 1,
        "role": "primary",
        "plan_digest": digest,
        "broker": process,
        "daemon": None,
        "containerd": None,
        "runtime": None,
        "config": path,
        "daemon_root": daemon_root,
        "stage_root": {
            **daemon_root,
            "path": str(tmp_path / "absent-stages"),
            "mode": 0o700,
        },
        "stages": [],
        "container": {"id": None, "name": "", "labels": {}},
        "proof": {
            "container_absence": False,
            "stages_absence": False,
            "daemon_absence": False,
            "containerd_absence": False,
            "runtime_absence": False,
            "config_absence": False,
            "root_absence": False,
        },
    }
    lease_payload = {
        "lease_id": lease_id,
        "workspace_id": "workspace-recovery",
        "epoch": 1,
        "role": "primary",
        "effective_plan_digest": digest,
        "owner_token": owner_token,
    }
    lease_envelope = {
        "payload": lease_payload,
        "checksum": broker_module._journal_digest(_canonical(lease_payload)),
    }
    (lease_root / f"{lease_id}.json").write_bytes(_canonical(lease_envelope))
    secret = b"journal-test-key"

    def sign(value: bytes) -> bytes:
        return hmac.digest(secret, value, "sha256")

    authenticator = SimpleNamespace(
        key_id="journal-test",
        algorithm="hmac-sha256-v1",
        sign=sign,
        verify=lambda value, signature: hmac.compare_digest(sign(value), signature),
    )
    journal_fd = os.open(journal_root, os.O_RDONLY | os.O_DIRECTORY)
    lease_fd = os.open(lease_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        broker_module._atomic_journal_write(
            journal_fd,
            lease_id,
            payload,
            authenticator=authenticator,
        )
        receipts = broker_module.recover_supervisor_journals(
            journal_fd,
            lease_fd,
            authenticator=authenticator,
        )
    finally:
        os.close(lease_fd)
        os.close(journal_fd)

    assert len(receipts) == 1
    receipt_payload = receipts[0]["payload"]
    assert receipt_payload["state"] == "FINAL"
    assert all(receipt_payload["proof"].values())
    assert broker_module.validate_supervisor_receipt(
        dict(receipts[0]),
        authenticator=authenticator,
        expected_lease_id=lease_id,
        expected_generation_digest=payload["generation_digest"],
        expected_owner_token_digest=payload["owner_token_digest"],
    )
    tampered = {
        **dict(receipts[0]),
        "payload": {**receipt_payload, "state": "ACTIVE"},
    }
    assert not broker_module.validate_supervisor_receipt(
        tampered,
        authenticator=authenticator,
        expected_lease_id=lease_id,
    )


def test_process_observation_error_is_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_read(
        self: Path,
        *,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", deny_read)
    with pytest.raises(PermissionError, match="denied"):
        broker_module._journal_process_absent({"pid": 123})


def test_path_observation_error_is_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_lstat(path: str) -> os.stat_result:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "lstat", deny_lstat)
    with pytest.raises(PermissionError, match="denied"):
        broker_module._journal_path_name_absent("/unobservable")


def test_path_absence_requires_unchanged_parent_authority(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    parent = trusted.stat()
    observation = {
        "path": str(trusted / "absent"),
        "device": 1,
        "inode": 1,
        "mode": 0o600,
        "parent_path": str(trusted),
        "parent_device": parent.st_dev,
        "parent_inode": parent.st_ino,
    }
    assert broker_module._journal_path_absent(observation)

    trusted.rename(tmp_path / "moved")
    trusted.mkdir()
    with pytest.raises(OSError, match="parent authority changed"):
        broker_module._journal_path_absent(observation)


def test_completed_lease_journals_compact_to_one_global_sentinel() -> None:
    broker = object.__new__(MountNamespaceBroker)
    broker._global_journal_lease_id = None
    broker._journal_bindings = {"lease-a": {}, "lease-b": {}}
    updates: list[str] = []
    unlinked: list[str] = []

    def update(lease_id: str, **changes: object) -> None:
        del changes
        updates.append(lease_id)

    broker._journal_update = update
    broker.unlink_supervisor_receipt = unlinked.append
    proof = {
        "container_absence": True,
        "stages_absence": True,
        "daemon_absence": False,
        "containerd_absence": False,
        "runtime_absence": False,
        "config_absence": False,
        "root_absence": False,
    }

    broker.record_cleanup_receipt("lease-a", proof=proof, state="ACTIVE")
    broker.record_cleanup_receipt("lease-b", proof=proof, state="ACTIVE")

    assert updates == ["lease-a", "lease-b"]
    assert broker._global_journal_lease_id == "lease-a"
    assert unlinked == ["lease-b"]
    assert set(broker._journal_bindings) == {"lease-a"}
