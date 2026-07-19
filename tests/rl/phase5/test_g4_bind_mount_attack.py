from __future__ import annotations

import ctypes
import errno
import os
import socket
import subprocess
import threading
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.rl_phase5 import g4_bind_mount_attack as attack


class _SentinelPrimaryError(RuntimeError):
    pass


class _SentinelCancellation(BaseException):
    pass


class _SimulatedAttack:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._transport = tempfile.TemporaryDirectory(
            prefix="g4-socket-", dir="/tmp"
        )
        self._private = tempfile.TemporaryDirectory(
            prefix="g4-private-", dir="/tmp"
        )
        self.mounted = False
        self.mount_calls: list[
            tuple[attack.NodeIdentity, attack.NodeIdentity]
        ] = []
        self.unmount_calls: list[attack.NodeIdentity] = []
        self.tree_fds: list[int] = []
        self.source = attack.NodeIdentity(
            device=22, inode=220, file_type="directory"
        )
        self.target = attack.NodeIdentity(
            device=11, inode=110, file_type="directory"
        )
        self.namespace = attack.NamespaceIdentity(device=4, inode=44)
        self.peer = attack.PeerIdentity(
            pid=os.getpid(), uid=os.getuid(), gid=os.getgid(), starttime="7"
        )
        self.fd_identities: dict[int, attack.NodeIdentity] = {}
        self.path_overrides: dict[str, attack.NodeIdentity] = {}
        monkeypatch.setattr(attack.sys, "platform", "linux")
        monkeypatch.setattr(
            attack,
            "_open_protocol_socket",
            lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM),
        )
        monkeypatch.setattr(attack, "_proc_starttime", lambda _pid: "7")
        monkeypatch.setattr(
            attack, "_namespace_identity", lambda _path: self.namespace
        )
        monkeypatch.setattr(
            attack, "_peer_identity", lambda _connection: self.peer
        )
        monkeypatch.setattr(
            attack,
            "_setns_exact",
            lambda _pid, _expected: os.open(
                self._private.name, os.O_RDONLY
            ),
        )
        monkeypatch.setattr(
            attack, "_require_exact_helper_capabilities", lambda: None
        )
        monkeypatch.setattr(attack, "_require_new_mount_api", lambda: None)

        def identity(path: str) -> attack.NodeIdentity:
            if path in self.path_overrides:
                return self.path_overrides[path]
            if path == "/source":
                return self.source
            if path == "/target":
                return self.source if self.mounted else self.target
            raise AssertionError(f"unexpected simulated path: {path}")

        def node(_pid: int, path: str) -> attack.NodeIdentity:
            return identity(path)

        def openat2(_root_fd: int, path: str) -> int:
            descriptor = os.open(os.devnull, os.O_RDONLY)
            self.fd_identities[descriptor] = identity(path)
            return descriptor

        def open_tree(source_fd: int) -> int:
            descriptor = os.dup(source_fd)
            self.fd_identities[descriptor] = self.fd_identities[source_fd]
            self.tree_fds.append(descriptor)
            return descriptor

        def attach(tree_fd: int, target_fd: int) -> None:
            self.mount_calls.append(
                (
                    self.fd_identities[tree_fd],
                    self.fd_identities[target_fd],
                )
            )
            self.mounted = True

        def unmount(tree_fd: int) -> None:
            self.unmount_calls.append(self.fd_identities[tree_fd])
            self.mounted = False

        monkeypatch.setattr(attack, "_subject_node_identity", node)
        monkeypatch.setattr(attack, "_openat2_path", openat2)
        monkeypatch.setattr(
            attack,
            "_node_identity_fd",
            lambda descriptor: self.fd_identities[descriptor],
        )
        monkeypatch.setattr(attack, "_open_tree_clone", open_tree)
        monkeypatch.setattr(attack, "_attach_mount_tree", attach)
        monkeypatch.setattr(attack, "_unmount_attached_mount_fd", unmount)
        monkeypatch.setattr(attack, "_fsync_pinned_node", lambda _fd: None)

    @staticmethod
    def _directory_identity(path: Path) -> attack.NamespaceIdentity:
        metadata = path.stat()
        return attack.NamespaceIdentity(
            device=metadata.st_dev, inode=metadata.st_ino
        )

    def manifest(
        self, tmp_path: Path, *, deadline: float = 2.0
    ) -> tuple[Path, attack.BindReplaceManifest]:
        del tmp_path
        nonce = "a" * 64
        transport = Path(self._transport.name)
        private = Path(self._private.name)
        manifest = attack.BindReplaceManifest(
            schema_version="bb.rl.g4-bind-replace-manifest.v1",
            operation="bind_replace",
            subject_pid=self.peer.pid,
            subject_starttime=self.peer.starttime,
            subject_mount_namespace=self.namespace,
            source_path="/source",
            target_path="/target",
            source_before=self.source,
            target_before=self.target,
            expected_peer=self.peer,
            nonce=nonce,
            request_digest=attack.bind_replace_request_digest(nonce),
            deadline_unix_ns=time.time_ns()
            + int(deadline * 1_000_000_000),
            socket_path=os.fspath(transport / "broker.sock"),
            state_path=os.fspath(private / "consumed.json"),
            socket_directory=self._directory_identity(transport),
            state_directory=self._directory_identity(private),
        )
        path = private / "manifest.json"
        path.write_bytes(manifest.canonical_bytes())
        return path, manifest


def _wait_for_socket(path: str) -> None:
    deadline = time.monotonic() + 2.0
    while not Path(path).exists():
        if time.monotonic() >= deadline:
            raise AssertionError("broker socket did not appear")
        time.sleep(0.005)


def _start_helper(
    executor: ThreadPoolExecutor,
    path: Path,
    manifest: attack.BindReplaceManifest,
) -> Future[attack.BindReplaceResult | attack.BindReplaceFailure]:
    future = executor.submit(attack.serve_once, path)
    _wait_for_socket(manifest.socket_path)
    return future


def _raw_exchange(manifest: attack.BindReplaceManifest, document: dict[str, Any]) -> bytes:
    connection = attack._open_protocol_socket()
    try:
        connection.connect(manifest.socket_path)
        connection.recv(attack._MAX_DOCUMENT_BYTES)
        connection.sendall(attack._canonical_bytes(document))
        return connection.recv(attack._MAX_DOCUMENT_BYTES)
    finally:
        connection.close()


def test_prepare_serves_creator_manifest_when_persisted_path_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    transport = Path(simulated._transport.name)
    private = Path(simulated._private.name)
    manifest_path = private / "created.json"
    served: list[
        tuple[attack.BindReplaceManifest, bytes, str]
    ] = []

    def replacing_write(path: Path, payload: bytes) -> None:
        original = attack._parse_exact(payload, attack.BindReplaceManifest)
        attacker = original.model_copy(update={"source_path": "/attacker"})
        path.write_bytes(attacker.canonical_bytes())

    def capture_serve(
        manifest: attack.BindReplaceManifest,
        *,
        creator_bytes: bytes,
        creator_digest: str,
    ) -> attack.BindReplaceFailure:
        served.append((manifest, creator_bytes, creator_digest))
        return attack._failure(manifest, "deadline", "captured")

    monkeypatch.setattr(attack, "_write_exclusive", replacing_write)
    monkeypatch.setattr(attack, "serve_manifest", capture_serve)
    monkeypatch.setattr(
        attack,
        "load_manifest",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("prepare must not reopen its manifest")
        ),
    )
    attack.prepare_and_serve(
        manifest_path=manifest_path,
        subject_pid=simulated.peer.pid,
        source_path="/source",
        target_path="/target",
        peer_uid=simulated.peer.uid,
        peer_gid=simulated.peer.gid,
        socket_path=os.fspath(transport / "prepared.sock"),
        state_path=os.fspath(private / "prepared-state.json"),
        deadline_seconds=1,
    )
    retained, creator_bytes, creator_digest = served[0]
    persisted = attack._parse_exact(
        manifest_path.read_bytes(), attack.BindReplaceManifest
    )
    assert retained.source_path == "/source"
    assert persisted.source_path == "/attacker"
    assert creator_bytes == retained.canonical_bytes()
    assert creator_digest == retained.digest


def test_socket_path_replacement_fails_without_unlinking_attacker_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    replacement = Path(manifest.socket_path)
    replacement.write_bytes(b"attacker-owned")
    with pytest.raises(
        attack.G4BindMountAttackError, match="broker socket path already exists"
    ):
        attack.serve_once(manifest_path)
    assert replacement.read_bytes() == b"attacker-owned"
    assert simulated.mount_calls == []


def test_socket_directory_replacement_fails_before_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    socket_parent = Path(manifest.socket_path).parent
    displaced = socket_parent.with_name(socket_parent.name + "-old")
    socket_parent.rename(displaced)
    socket_parent.mkdir()
    try:
        with pytest.raises(
            attack.G4BindMountAttackError,
            match="control directory identity drifted",
        ):
            attack.serve_once(manifest_path)
        assert simulated.mount_calls == []
    finally:
        socket_parent.rmdir()
        displaced.rename(socket_parent)


def test_state_symlink_replacement_is_replay_and_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    attacker_state = Path(simulated._private.name) / "attacker.json"
    attacker_state.write_bytes(b"attacker-owned")
    Path(manifest.state_path).symlink_to(attacker_state)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = _start_helper(executor, manifest_path, manifest)
        with pytest.raises(attack.G4BindMountAttackError, match="replay"):
            attack.request_preconfigured_bind_replace(manifest.socket_path)
        helper_result = future.result(timeout=2)
    assert isinstance(helper_result, attack.BindReplaceFailure)
    assert attacker_state.read_bytes() == b"attacker-owned"
    assert simulated.mount_calls == []


def test_simulated_helper_success_is_exact_one_shot_and_acknowledged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = _start_helper(executor, manifest_path, manifest)
        result = attack.request_preconfigured_bind_replace(manifest.socket_path)
        helper_result = future.result(timeout=2)
    assert result == helper_result
    assert result.target_after == manifest.source_before
    assert simulated.mount_calls == [(simulated.source, simulated.target)]
    assert simulated.unmount_calls == []
    for tree_fd in simulated.tree_fds:
        with pytest.raises(OSError) as closed:
            os.fstat(tree_fd)
        assert closed.value.errno == errno.EBADF
    assert Path(manifest.state_path).is_file()
    assert not Path(manifest.socket_path).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        {"source_path": "/caller-selected"},
        {"nonce": "b" * 64},
        {"request_digest": "sha256:" + "f" * 64},
        {"operation": "caller_operation"},
    ],
    ids=["wrong-path", "wrong-nonce", "wrong-digest", "wrong-operation"],
)
def test_closed_request_rejects_caller_selected_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, str],
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    request: dict[str, Any] = {
        "schema_version": "bb.rl.g4-bind-replace-request.v1",
        "operation": "bind_replace",
        "nonce": manifest.nonce,
        "request_digest": manifest.request_digest,
        "manifest_digest": manifest.digest,
    }
    request.update(mutation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = _start_helper(executor, manifest_path, manifest)
        payload = _raw_exchange(manifest, request)
        helper_result = future.result(timeout=2)
    failure = attack._parse_exact(payload, attack.BindReplaceFailure)
    assert failure.error_code == "protocol_invalid"
    assert helper_result == failure
    assert simulated.mount_calls == []
    assert not Path(manifest.state_path).exists()


def test_replay_is_consumed_and_never_mounts_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = _start_helper(executor, manifest_path, manifest)
        attack.request_preconfigured_bind_replace(manifest.socket_path)
        assert isinstance(first.result(timeout=2), attack.BindReplaceResult)
    simulated.mounted = False
    with ThreadPoolExecutor(max_workers=1) as executor:
        replay = _start_helper(executor, manifest_path, manifest)
        with pytest.raises(attack.G4BindMountAttackError, match="replay"):
            attack.request_preconfigured_bind_replace(manifest.socket_path)
        replay_result = replay.result(timeout=2)
    assert isinstance(replay_result, attack.BindReplaceFailure)
    assert replay_result.error_code == "replay"
    assert simulated.mount_calls == [(simulated.source, simulated.target)]


def test_helper_accept_deadline_expires_without_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, _manifest = simulated.manifest(tmp_path, deadline=0.05)
    result = attack.serve_once(manifest_path)
    assert isinstance(result, attack.BindReplaceFailure)
    assert result.error_code == "deadline"
    assert simulated.mount_calls == []


def test_peer_mismatch_fails_before_nonce_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    wrong_peer = simulated.peer.model_copy(update={"pid": simulated.peer.pid + 1})
    monkeypatch.setattr(attack, "_peer_identity", lambda _connection: wrong_peer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = _start_helper(executor, manifest_path, manifest)
        with pytest.raises(attack.G4BindMountAttackError, match="peer_mismatch"):
            attack.request_preconfigured_bind_replace(manifest.socket_path)
        helper_result = future.result(timeout=2)
    assert isinstance(helper_result, attack.BindReplaceFailure)
    assert not Path(manifest.state_path).exists()
    assert simulated.mount_calls == []


def test_exact_capability_gate_runs_before_listener_or_setns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, _manifest = simulated.manifest(tmp_path)
    expected = attack._HELPER_CAPABILITY_MASK
    assert attack._capability_masks(
        f"CapPrm:\t{expected:016x}\nCapEff:\t{expected:016x}\n"
    ) == (expected, expected)

    setns_called = False

    def forbidden_setns(
        _pid: int, _namespace: attack.NamespaceIdentity
    ) -> int:
        nonlocal setns_called
        setns_called = True
        raise AssertionError("setns must not run")

    monkeypatch.setattr(
        attack,
        "_require_exact_helper_capabilities",
        lambda: (_ for _ in ()).throw(
            attack.G4BindMountAttackError(
                "helper requires exactly CAP_SYS_ADMIN and CAP_SYS_CHROOT"
            )
        ),
    )
    monkeypatch.setattr(attack, "_setns_exact", forbidden_setns)
    with pytest.raises(
        attack.G4BindMountAttackError,
        match="exactly CAP_SYS_ADMIN and CAP_SYS_CHROOT",
    ):
        attack.serve_once(manifest_path)
    assert setns_called is False
    assert simulated.mount_calls == []


@pytest.mark.parametrize(
    ("effective", "permitted"),
    [
        (0, 0),
        (
            attack._HELPER_CAPABILITY_MASK | 1,
            attack._HELPER_CAPABILITY_MASK,
        ),
        (
            attack._HELPER_CAPABILITY_MASK,
            attack._HELPER_CAPABILITY_MASK | 1,
        ),
    ],
)
def test_wrong_effective_or_permitted_capability_mask_fails_closed(
    effective: int,
    permitted: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = f"CapPrm:\t{permitted:016x}\nCapEff:\t{effective:016x}\n"
    monkeypatch.setattr(
        attack.Path, "read_text", lambda _path, *, encoding: status
    )
    with pytest.raises(
        attack.G4BindMountAttackError,
        match="exactly CAP_SYS_ADMIN and CAP_SYS_CHROOT",
    ):
        attack._require_exact_helper_capabilities()


def test_blocked_new_mount_api_fails_before_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def blocked(number: int, *_arguments: object) -> int:
        calls.append(number)
        raise OSError(errno.EPERM, "seccomp")

    monkeypatch.setattr(attack, "_linux_syscall", blocked)
    with pytest.raises(
        attack.G4BindMountAttackError,
        match="open_tree/move_mount unavailable or blocked",
    ):
        attack._require_new_mount_api()
    assert calls == [attack._SYS_OPEN_TREE]


def test_namespace_drift_fails_before_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    drift = attack.NamespaceIdentity(device=4, inode=45)
    monkeypatch.setattr(attack, "_namespace_identity", lambda _path: drift)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = _start_helper(executor, manifest_path, manifest)
        with pytest.raises(attack.G4BindMountAttackError, match="namespace_drift"):
            attack.request_preconfigured_bind_replace(manifest.socket_path)
        helper_result = future.result(timeout=2)
    assert isinstance(helper_result, attack.BindReplaceFailure)
    assert simulated.mount_calls == []


def test_postcondition_failure_unmounts_before_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    original_openat2 = attack._openat2_path

    def stale_target_after_mount(root_fd: int, path: str) -> int:
        descriptor = original_openat2(root_fd, path)
        if simulated.mounted and path == "/target":
            simulated.fd_identities[descriptor] = simulated.target
        return descriptor

    monkeypatch.setattr(attack, "_openat2_path", stale_target_after_mount)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = _start_helper(executor, manifest_path, manifest)
        with pytest.raises(attack.G4BindMountAttackError, match="postcondition_failed"):
            attack.request_preconfigured_bind_replace(manifest.socket_path)
        helper_result = future.result(timeout=2)
    assert isinstance(helper_result, attack.BindReplaceFailure)
    assert simulated.unmount_calls == [simulated.source]
    assert simulated.mounted is False
    for tree_fd in simulated.tree_fds:
        with pytest.raises(OSError) as closed:
            os.fstat(tree_fd)
        assert closed.value.errno == errno.EBADF


def test_openat2_and_mount_use_only_fixed_descriptor_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_calls: list[tuple[int, int, bytes, int, int]] = []

    class FakeSyscall:
        restype: object = None

        def __call__(
            self,
            number: ctypes.c_long,
            root_fd: ctypes.c_int,
            path: ctypes.c_char_p,
            how_pointer: object,
            size: ctypes.c_size_t,
        ) -> int:
            how = ctypes.cast(
                how_pointer, ctypes.POINTER(attack._OpenHow)
            ).contents
            open_calls.append(
                (
                    number.value,
                    root_fd.value,
                    path.value or b"",
                    how.flags,
                    how.resolve,
                )
            )
            assert size.value == ctypes.sizeof(attack._OpenHow)
            return os.open(os.devnull, os.O_RDONLY)

    fake_syscall = FakeSyscall()
    monkeypatch.setattr(
        attack.ctypes,
        "CDLL",
        lambda _name, *, use_errno: SimpleNamespace(syscall=fake_syscall),
    )
    descriptor = attack._openat2_path(91, "/authorized/node")
    os.close(descriptor)
    assert open_calls == [
        (
            attack._SYS_OPENAT2,
            91,
            b"authorized/node",
            getattr(os, "O_PATH", 0x200000)
            | getattr(os, "O_CLOEXEC", 0),
            attack._RESOLVE_BENEATH
            | attack._RESOLVE_NO_SYMLINKS
            | attack._RESOLVE_NO_MAGICLINKS,
        )
    ]

    syscall_calls: list[tuple[int, tuple[object, ...]]] = []

    def syscall(number: int, *arguments: object) -> int:
        syscall_calls.append((number, arguments))
        return 71 if number == attack._SYS_OPEN_TREE else 0

    monkeypatch.setattr(attack, "_linux_syscall", syscall)
    tree_fd = attack._open_tree_clone(17)
    attack._attach_mount_tree(tree_fd, 23)
    assert tree_fd == 71
    open_tree_number, open_tree_arguments = syscall_calls[0]
    assert open_tree_number == attack._SYS_OPEN_TREE
    assert open_tree_arguments[0].value == 17
    assert open_tree_arguments[1].value == b""
    assert open_tree_arguments[2].value == (
        attack._AT_EMPTY_PATH
        | attack._OPEN_TREE_CLONE
        | attack._OPEN_TREE_CLOEXEC
    )
    move_number, move_arguments = syscall_calls[1]
    assert move_number == attack._SYS_MOVE_MOUNT
    assert move_arguments[0].value == tree_fd
    assert move_arguments[1].value == b""
    assert move_arguments[2].value == 23
    assert move_arguments[3].value == b""
    assert move_arguments[4].value == (
        attack._MOVE_MOUNT_F_EMPTY_PATH
        | attack._MOVE_MOUNT_T_EMPTY_PATH
    )

    unmount_calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        attack,
        "_libc_call",
        lambda name, *arguments: unmount_calls.append((name, arguments)),
    )
    attack._unmount_attached_mount_fd(tree_fd)
    unmount_name, unmount_arguments = unmount_calls[0]
    assert unmount_name == "umount2"
    assert unmount_arguments[0].value == b"./proc/self/fd/71"
    assert unmount_arguments[1].value == attack._MNT_DETACH


@pytest.mark.parametrize("swap_path", ["/source", "/target"])
@pytest.mark.parametrize(
    "swap_kind",
    [
        "ancestor-rename",
        "final-rename",
        "ancestor-symlink",
        "final-symlink",
        "ancestor-unlink",
        "final-unlink",
    ],
)
def test_descriptor_bound_mount_rejects_synchronized_path_swaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap_path: str,
    swap_kind: str,
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    original_attach = attack._attach_mount_tree
    original_openat2 = attack._openat2_path
    decoy = attack.NodeIdentity(device=99, inode=990, file_type="directory")
    swap_active = False

    def swapping_attach(tree_fd: int, target_fd: int) -> None:
        nonlocal swap_active
        swap_active = True
        if swap_kind.endswith("rename"):
            simulated.path_overrides[swap_path] = decoy
        original_attach(tree_fd, target_fd)

    def swapped_openat2(root_fd: int, path: str) -> int:
        if (
            swap_active
            and (
                swap_kind.endswith("symlink")
                or swap_kind.endswith("unlink")
            )
            and path == swap_path
        ):
            error = errno.ELOOP if swap_kind.endswith("symlink") else errno.ENOENT
            raise OSError(error, "synchronized path swap")
        return original_openat2(root_fd, path)

    monkeypatch.setattr(attack, "_attach_mount_tree", swapping_attach)
    monkeypatch.setattr(attack, "_openat2_path", swapped_openat2)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = _start_helper(executor, manifest_path, manifest)
        with pytest.raises(
            attack.G4BindMountAttackError, match="postcondition_failed"
        ):
            attack.request_preconfigured_bind_replace(manifest.socket_path)
        helper_result = future.result(timeout=2)
    assert isinstance(helper_result, attack.BindReplaceFailure)
    assert simulated.mount_calls == [(simulated.source, simulated.target)]
    assert decoy not in simulated.mount_calls[0]
    assert simulated.unmount_calls == [simulated.source]
    assert simulated.mounted is False
    assert simulated.mount_calls[0][1] == simulated.target
    for tree_fd in simulated.tree_fds:
        with pytest.raises(OSError) as closed:
            os.fstat(tree_fd)
        assert closed.value.errno == errno.EBADF


def test_helper_death_blocks_without_local_mount_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    _manifest_path, manifest = simulated.manifest(tmp_path, deadline=0.05)
    local_mount_attempted = False

    def forbidden_attach(_tree_fd: int, _target_fd: int) -> None:
        nonlocal local_mount_attempted
        local_mount_attempted = True

    monkeypatch.setattr(attack, "_attach_mount_tree", forbidden_attach)
    monkeypatch.setattr(attack, "_CLIENT_WAIT_SECONDS", 0.05)
    with pytest.raises(attack.G4BindMountAttackError, match="deadline"):
        attack.request_preconfigured_bind_replace(manifest.socket_path)
    assert local_mount_attempted is False


def test_tampered_result_blocks_and_is_not_acknowledged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulated = _SimulatedAttack(monkeypatch)
    manifest_path, manifest = simulated.manifest(tmp_path)
    listener = attack._open_protocol_socket()
    listener.bind(manifest.socket_path)
    listener.listen(1)
    acknowledged: list[bytes] = []

    def fake_helper() -> None:
        connection, _ = listener.accept()
        try:
            connection.sendall(attack._challenge(manifest).canonical_bytes())
            connection.recv(attack._MAX_DOCUMENT_BYTES)
            document = {
                "schema_version": "bb.rl.g4-bind-replace-result.v1",
                "status": "ok",
                "operation": "bind_replace",
                "nonce": manifest.nonce,
                "request_digest": manifest.request_digest,
                "manifest_digest": manifest.digest,
                "peer": manifest.expected_peer.model_dump(mode="json"),
                "mount_namespace": manifest.subject_mount_namespace.model_dump(mode="json"),
                "source_before": manifest.source_before.model_dump(mode="json"),
                "target_before": manifest.target_before.model_dump(mode="json"),
                "target_after": manifest.source_before.model_dump(mode="json"),
                "result_digest": "sha256:" + "0" * 64,
            }
            connection.sendall(attack._canonical_bytes(document))
            connection.settimeout(0.1)
            try:
                acknowledged.append(connection.recv(attack._MAX_DOCUMENT_BYTES))
            except (socket.timeout, ConnectionResetError):
                pass
        finally:
            connection.close()
            listener.close()

    thread = threading.Thread(target=fake_helper)
    thread.start()
    with pytest.raises(attack.G4BindMountAttackError, match="tampered"):
        attack.request_preconfigured_bind_replace(manifest.socket_path)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert acknowledged == [b""]
    assert simulated.mount_calls == []


def test_orchestration_isolates_sys_admin_and_pins_exact_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "payload"
    authority = tmp_path / "authority"
    different = tmp_path / "different"
    for path in (payload, authority, different):
        path.mkdir()
    docker_calls: list[list[str]] = []

    def docker_run(arguments: list[str], *, timeout: float) -> SimpleNamespace:
        assert timeout > 0
        docker_calls.append(arguments)
        if arguments[1] == "wait":
            return SimpleNamespace(stdout="0\n")
        return SimpleNamespace(stdout="container-id\n")

    cleanup_calls: list[list[str]] = []
    monkeypatch.setattr(attack, "_docker_run", docker_run)
    monkeypatch.setattr(
        attack.subprocess,
        "run",
        lambda arguments, **_kwargs: cleanup_calls.append(arguments)
        or SimpleNamespace(returncode=0),
    )
    assert (
        attack.orchestrate_target(
            docker="docker",
            subject_image="subject:image",
            helper_image="helper:image",
            payload_host=payload,
            authority_host=authority,
            different_device_host=different,
            timeout_seconds=5,
        )
        == 0
    )
    subject, helper = docker_calls[:2]
    subject_text = "\n".join(subject)
    helper_text = "\n".join(helper)
    assert "/g4-private" not in subject_text
    assert "BREADBOARD_G4_BIND_MOUNT_ATTACK_MANIFEST" not in subject_text
    assert (
        "BREADBOARD_G4_BIND_MOUNT_ATTACK_SOCKET=/g4-socket/broker.sock"
        in subject
    )
    subject_socket_mount = next(
        value for value in subject if "dst=/g4-socket" in value
    )
    assert subject_socket_mount.endswith(",readonly")
    helper_socket_mount = next(
        value for value in helper if "dst=/g4-socket" in value
    )
    assert not helper_socket_mount.endswith(",readonly")
    assert "dst=/g4-private" in helper_text
    assert "/g4-private/manifest.json" in helper
    assert "/g4-private/consumed.json" in helper
    assert "LINUX_IMMUTABLE" in subject
    assert "SYS_ADMIN" not in subject
    assert "--privileged" not in subject
    assert "test_privileged_linux_bind_mount_device_replacement_fails_live_and_restart" in subject[-1]
    assert "SYS_ADMIN" in helper
    assert "SYS_CHROOT" in helper
    cap_drop = helper.index("--cap-drop")
    assert helper[cap_drop : cap_drop + 6] == [
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SYS_ADMIN",
        "--cap-add",
        "SYS_CHROOT",
    ]
    assert "LINUX_IMMUTABLE" not in helper
    assert "--pid" in helper
    assert next(value for value in helper if value.startswith("container:bb-g4-bind-subject-"))
    assert "/different/revocation-device-1" in helper
    assert any(call[1:3] == ["rm", "-f"] for call in cleanup_calls)


def test_cleanup_timeout_still_attempts_both_container_removals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "payload"
    authority = tmp_path / "authority"
    different = tmp_path / "different"
    for path in (payload, authority, different):
        path.mkdir()
    monkeypatch.setattr(
        attack,
        "_docker_run",
        lambda arguments, *, timeout: SimpleNamespace(
            stdout="0\n" if arguments[1] == "wait" else "container-id\n"
        ),
    )
    cleanup_calls: list[list[str]] = []
    timeout = attack.subprocess.TimeoutExpired(
        ["/raw/cleanup-command", "SENTINEL"], 1
    )

    def cleanup(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        cleanup_calls.append(arguments)
        if arguments[-1].startswith("bb-g4-bind-helper-"):
            raise timeout
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(attack.subprocess, "run", cleanup)
    with pytest.raises(attack.G4BindMountOrchestrationError) as raised:
        attack.orchestrate_target(
            docker="docker",
            subject_image="subject:image",
            helper_image="helper:image",
            payload_host=payload,
            authority_host=authority,
            different_device_host=different,
            timeout_seconds=5,
        )
    assert [call[1:3] for call in cleanup_calls] == [
        ["rm", "-f"],
        ["rm", "-f"],
    ]
    assert cleanup_calls[0][-1].startswith("bb-g4-bind-helper-")
    assert cleanup_calls[1][-1].startswith("bb-g4-bind-subject-")
    failure = raised.value.cleanup_failures[0]
    assert isinstance(failure, attack.G4BindMountCleanupError)
    assert failure.container_name == cleanup_calls[0][-1]
    assert failure.detail == "timeout"
    assert raised.value.primary_error is timeout
    assert raised.value.__cause__ is timeout
    assert raised.value.primary_reason == "cleanup_interrupted"
    assert "SENTINEL" not in str(raised.value)
    assert "/raw/cleanup-command" not in str(raised.value)


@pytest.mark.parametrize(
    ("helper_code", "subject_code"),
    [(7, 0), (0, 8), (7, 8)],
)
def test_nonzero_cleanup_return_code_fails_orchestration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper_code: int,
    subject_code: int,
) -> None:
    payload = tmp_path / "payload"
    authority = tmp_path / "authority"
    different = tmp_path / "different"
    for path in (payload, authority, different):
        path.mkdir()
    monkeypatch.setattr(
        attack,
        "_docker_run",
        lambda arguments, *, timeout: SimpleNamespace(
            stdout="0\n" if arguments[1] == "wait" else "container-id\n"
        ),
    )
    cleanup_calls: list[list[str]] = []

    def cleanup(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        cleanup_calls.append(arguments)
        code = (
            helper_code
            if arguments[-1].startswith("bb-g4-bind-helper-")
            else subject_code
        )
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(attack.subprocess, "run", cleanup)
    with pytest.raises(attack.G4BindMountOrchestrationError) as raised:
        attack.orchestrate_target(
            docker="docker",
            subject_image="subject:image",
            helper_image="helper:image",
            payload_host=payload,
            authority_host=authority,
            different_device_host=different,
            timeout_seconds=5,
        )
    assert len(cleanup_calls) == 2
    failures = raised.value.cleanup_failures
    assert len(failures) == int(helper_code != 0) + int(subject_code != 0)
    assert all(
        isinstance(failure, attack.G4BindMountCleanupError)
        for failure in failures
    )


@pytest.mark.parametrize(
    "primary",
    [
        subprocess.CalledProcessError(
            17,
            ["/raw/execution-command", "SENTINEL"],
            stderr="SENTINEL stderr",
        ),
        _SentinelPrimaryError("SENTINEL /raw/execution-path"),
    ],
    ids=["called-process-error", "custom-exception"],
)
def test_primary_execution_error_remains_first_when_cleanup_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary: BaseException,
) -> None:
    payload = tmp_path / "payload"
    authority = tmp_path / "authority"
    different = tmp_path / "different"
    for path in (payload, authority, different):
        path.mkdir()
    assert "SENTINEL" in str(primary)
    monkeypatch.setattr(
        attack,
        "_docker_run",
        lambda _arguments, *, timeout: (_ for _ in ()).throw(primary),
    )
    cleanup_calls: list[list[str]] = []

    def cleanup(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        cleanup_calls.append(arguments)
        return SimpleNamespace(
            returncode=9
            if arguments[-1].startswith("bb-g4-bind-helper-")
            else 0
        )

    monkeypatch.setattr(attack.subprocess, "run", cleanup)
    with pytest.raises(attack.G4BindMountOrchestrationError) as raised:
        attack.orchestrate_target(
            docker="docker",
            subject_image="subject:image",
            helper_image="helper:image",
            payload_host=payload,
            authority_host=authority,
            different_device_host=different,
            timeout_seconds=5,
        )
    assert len(cleanup_calls) == 2
    assert raised.value.primary_error is primary
    assert raised.value.__cause__ is primary
    assert raised.value.primary_reason == "execution_exception"
    assert "SENTINEL" not in str(raised.value)
    assert "/raw/execution-command" not in str(raised.value)
    assert "/raw/execution-path" not in str(raised.value)
    assert len(raised.value.cleanup_failures) == 1
    assert isinstance(
        raised.value.cleanup_failures[0], attack.G4BindMountCleanupError
    )


@pytest.mark.parametrize(
    "interruption",
    [
        KeyboardInterrupt("SENTINEL /raw/keyboard-command"),
        _SentinelCancellation("SENTINEL /raw/cancellation-command"),
    ],
    ids=["keyboard-interrupt", "custom-cancellation"],
)
def test_cleanup_interruption_remains_primary_after_both_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption: BaseException,
) -> None:
    payload = tmp_path / "payload"
    authority = tmp_path / "authority"
    different = tmp_path / "different"
    for path in (payload, authority, different):
        path.mkdir()
    monkeypatch.setattr(
        attack,
        "_docker_run",
        lambda arguments, *, timeout: SimpleNamespace(
            stdout="0\n" if arguments[1] == "wait" else "container-id\n"
        ),
    )
    cleanup_calls: list[list[str]] = []

    def cleanup(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        cleanup_calls.append(arguments)
        if arguments[-1].startswith("bb-g4-bind-helper-"):
            raise interruption
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(attack.subprocess, "run", cleanup)
    with pytest.raises(attack.G4BindMountOrchestrationError) as raised:
        attack.orchestrate_target(
            docker="docker",
            subject_image="subject:image",
            helper_image="helper:image",
            payload_host=payload,
            authority_host=authority,
            different_device_host=different,
            timeout_seconds=5,
        )
    assert len(cleanup_calls) == 2
    assert raised.value.primary_error is interruption
    assert raised.value.__cause__ is interruption
    assert raised.value.primary_reason == "cleanup_interrupted"
    assert [
        failure.detail for failure in raised.value.cleanup_failures
    ] == ["interrupted", "docker rm returned 9"]
    aggregate_text = str(raised.value)
    assert "SENTINEL" not in aggregate_text
    assert "/raw/keyboard-command" not in aggregate_text
    assert "/raw/cancellation-command" not in aggregate_text


def test_nonzero_execution_remains_first_when_cleanup_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "payload"
    authority = tmp_path / "authority"
    different = tmp_path / "different"
    for path in (payload, authority, different):
        path.mkdir()

    def docker_run(
        arguments: list[str], *, timeout: float
    ) -> SimpleNamespace:
        if arguments[1] != "wait":
            return SimpleNamespace(stdout="container-id\n")
        return SimpleNamespace(
            stdout="6\n"
            if arguments[-1].startswith("bb-g4-bind-subject-")
            else "0\n"
        )

    monkeypatch.setattr(attack, "_docker_run", docker_run)
    cleanup_calls: list[list[str]] = []

    def cleanup(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        cleanup_calls.append(arguments)
        return SimpleNamespace(
            returncode=9
            if arguments[-1].startswith("bb-g4-bind-helper-")
            else 0
        )

    monkeypatch.setattr(attack.subprocess, "run", cleanup)
    with pytest.raises(attack.G4BindMountOrchestrationError) as raised:
        attack.orchestrate_target(
            docker="docker",
            subject_image="subject:image",
            helper_image="helper:image",
            payload_host=payload,
            authority_host=authority,
            different_device_host=different,
            timeout_seconds=5,
        )
    assert len(cleanup_calls) == 2
    execution = raised.value.primary_error
    assert isinstance(execution, attack.G4BindMountExecutionError)
    assert execution.exit_code == 6
    assert len(raised.value.cleanup_failures) == 1


def test_real_linux_setns_bind_mount_smoke_is_explicitly_gated() -> None:
    if attack.sys.platform != "linux":
        pytest.skip("Linux-only real setns/open_tree/move_mount smoke")
    configured = os.environ.get("BREADBOARD_G4_REAL_BIND_SMOKE_SOCKET")
    if configured is None:
        pytest.skip("set BREADBOARD_G4_REAL_BIND_SMOKE_SOCKET for privileged smoke")
    result = attack.request_preconfigured_bind_replace(configured)
    assert result.target_after == result.source_before
    assert result.source_before.device != result.target_before.device
