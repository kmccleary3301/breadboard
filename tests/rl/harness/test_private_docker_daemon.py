from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import signal
import stat
from dataclasses import replace
import sys
import time
from pathlib import Path

import pytest

from breadboard.rl.harness import composition
from breadboard.rl.harness import private_docker_daemon
from breadboard.rl.harness.contracts import RuntimeClass
from breadboard.rl.harness.private_docker_daemon import (
    CommandResult,
    OfflineImageAuthority,
    PinnedFileAuthority,
    PrivateDockerDaemonAuthority,
    PrivateDockerDaemonError,
    PrivateDockerDaemonOwner,
)
from breadboard.rl.harness.sandbox import InstalledRuntime
from breadboard.rl.harness.sandbox_docker import PrivateDockerDaemonBinding


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _file(path: Path, payload: bytes, mode: int = 0o755) -> PinnedFileAuthority:
    path.write_bytes(payload)
    path.chmod(mode)
    metadata = path.stat(follow_symlinks=False)
    return PinnedFileAuthority(
        path=str(path),
        digest=_digest(path),
        owner_uid=metadata.st_uid,
        mode=stat.S_IMODE(metadata.st_mode),
        executable=bool(stat.S_IMODE(metadata.st_mode) & 0o111),
    )


def _authority(tmp_path: Path, *, image: bool = False) -> PrivateDockerDaemonAuthority:
    images = ()
    if image:
        archive = _file(tmp_path / "image.tar", b"offline-image", 0o600)
        images = (
            OfflineImageAuthority(
                archive,
                "sha256:" + "4" * 64,
                "sha256:" + "5" * 64,
            ),
        )
    daemon_tag = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    daemon_root = Path(f"/tmp/bbpd-{daemon_tag}")
    daemon_root.mkdir(mode=0o700)
    return PrivateDockerDaemonAuthority(
        daemon_instance_id="private-test",
        dockerd=_file(tmp_path / "dockerd", b"dockerd"),
        docker=_file(tmp_path / "docker", b"docker"),
        runc=_file(tmp_path / "runc", b"runc"),
        containerd=_file(tmp_path / "containerd", b"containerd"),
        config_path=str(daemon_root / "daemon.json"),
        socket_path=str(daemon_root / "d.sock"),
        pid_file=str(daemon_root / "docker.pid"),
        data_root=str(daemon_root / "data"),
        mount_stage_root=str(daemon_root / "mount-stages"),
        exec_root=str(daemon_root / "exec"),
        containerd_socket_path=str(daemon_root / "c.sock"),
        containerd_root=str(daemon_root / "containerd-root"),
        containerd_state=str(daemon_root / "containerd-state"),
        log_root=str(daemon_root / "daemon-logs"),
        log_limit_bytes=4096,
        storage_driver="vfs",
        runtime_name="breadboard-runc",
        images=images,
    )


def _remove_socket_parent(authority: PrivateDockerDaemonAuthority) -> None:
    try:
        Path(authority.daemon_root).rmdir()
    except FileNotFoundError:
        pass


def test_owner_keeps_effective_runtime_snapshot_identity_separate(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    snapshot = tmp_path / "effective-runc"
    snapshot.write_bytes(Path(authority.runc.path).read_bytes())
    snapshot.chmod(authority.runc.mode)
    snapshot_fd = os.open(snapshot, os.O_RDONLY | os.O_CLOEXEC)
    owner = PrivateDockerDaemonOwner(
        authority,
        prerequisite_check=lambda: None,
        runtime_registration_path=str(tmp_path / "registered-runc"),
        runtime_effective_fd=snapshot_fd,
    )
    try:
        effective = os.fstat(owner._fds["runtime-effective"])
        source = os.fstat(owner._fds["runc"])
        assert (effective.st_dev, effective.st_ino) == (
            snapshot.stat().st_dev,
            snapshot.stat().st_ino,
        )
        assert (effective.st_dev, effective.st_ino) != (
            source.st_dev,
            source.st_ino,
        )
        assert private_docker_daemon._digest_fd(
            owner._fds["runtime-effective"]
        ) == authority.runc.digest
    finally:
        owner.close()
        os.close(snapshot_fd)
        _remove_socket_parent(authority)


def test_owner_pins_exact_files_and_seals_deterministic_config(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    owner = PrivateDockerDaemonOwner(authority, prerequisite_check=lambda: None)
    try:
        config = Path(authority.config_path).read_bytes()
        assert config == owner._config_bytes()
        assert stat.S_IMODE(Path(authority.config_path).stat().st_mode) == 0o600
        parsed = json.loads(config)
        assert parsed["containerd"] == authority.containerd_socket_path
        assert parsed["runtimes"][authority.runtime_name]["path"] == (
            f"/proc/{os.getpid()}/fd/{owner._fds['runc']}"
        )
        assert parsed["iptables"] is False
        assert parsed["bridge"] == "none"
        assert owner.docker_invocation.executable_descriptor_path == (
            f"/proc/{os.getpid()}/fd/{owner._fds['docker']}"
        )
        with pytest.raises(PrivateDockerDaemonError, match="procfd bind mounts") as blocked:
            owner.descriptor_mount_source(owner._fds["config"])
        assert blocked.value.code == "runtime_unsupported"
    finally:
        owner.close()
    for path in (
        authority.config_path,
        authority.data_root,
        authority.exec_root,
        authority.containerd_root,
        authority.containerd_state,
    ):
        assert not os.path.lexists(path)
    _remove_socket_parent(authority)


def test_operator_authority_allows_node_local_inode_and_records_live_identity(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    original = Path(authority.docker.path)
    old_inode = original.stat().st_ino
    replacement = tmp_path / "replacement"
    replacement.write_bytes(original.read_bytes())
    replacement.chmod(0o755)
    original.unlink()
    replacement.rename(original)
    owner = PrivateDockerDaemonOwner(authority, prerequisite_check=lambda: None)
    try:
        observation = owner._file_observations["docker"]
        assert observation.inode == original.stat().st_ino
        assert observation.inode != old_inode
        assert observation.digest == authority.docker.digest
    finally:
        owner.close()
        _remove_socket_parent(authority)
def test_pinned_docker_descriptor_content_drift_quarantines(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    owner = PrivateDockerDaemonOwner(authority, prerequisite_check=lambda: None)
    Path(authority.docker.path).write_bytes(b"changed-in-place")
    try:
        with pytest.raises(PrivateDockerDaemonError, match="descriptor authority drifted"):
            owner._assert_docker_cli()
    finally:
        owner.close()
        _remove_socket_parent(authority)


def test_default_prerequisite_gate_is_rootful_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = _authority(tmp_path)
    monkeypatch.setattr(
        "breadboard.rl.harness.private_docker_daemon.os.geteuid", lambda: 1000
    )
    with pytest.raises(PrivateDockerDaemonError, match="effective uid 0") as rejected:
        PrivateDockerDaemonOwner(authority)
    assert rejected.value.code == "runtime_unsupported"
    _remove_socket_parent(authority)




class _FakeProcess:
    def __init__(self) -> None:
        self.pid = os.getpid()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def send_signal(self, sig: int) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def test_launch_uses_descriptor_executables_empty_env_fixed_host_and_offline_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = _authority(tmp_path, image=True)
    launches: list[tuple[tuple[str, ...], str, tuple[int, ...], dict[str, str]]] = []
    commands: list[tuple[tuple[str, ...], str, tuple[int, ...], dict[str, str]]] = []
    processes: list[_FakeProcess] = []
    sockets: list[socket.socket] = []
    progress: list[dict[str, object]] = []

    def launcher(argv, *, executable, pass_fds, env, log_fd, log_limit_bytes):
        process = _FakeProcess()
        processes.append(process)
        launches.append((tuple(argv), executable, tuple(pass_fds), dict(env)))
        os.write(log_fd, ("launch " + Path(argv[0]).name + "\n").encode())
        assert log_limit_bytes == authority.log_limit_bytes
        endpoint = authority.containerd_socket_path if "--address" in argv else authority.socket_path
        server = socket.socket(socket.AF_UNIX)
        server.bind(endpoint)
        sockets.append(server)
        if "--address" in argv:
            ttrpc = socket.socket(socket.AF_UNIX)
            ttrpc.bind(authority.containerd_ttrpc_socket_path)
            sockets.append(ttrpc)
        else:
            Path(authority.pid_file).write_text(str(process.pid))
        return process

    def runner(argv, *, executable, pass_fds, env, timeout):
        commands.append((tuple(argv), executable, tuple(pass_fds), dict(env)))
        tail = tuple(argv)[3:]
        if tail[:1] == ("info",):
            payload = {
                "DockerRootDir": authority.data_root,
                "Driver": authority.storage_driver,
                "DefaultRuntime": authority.runtime_name,
                "Containerd": {"Address": authority.containerd_socket_path},
                "Runtimes": {
                    authority.runtime_name: {
                        "path": str(tmp_path / "private-runtime-bin" / "runc"),
                        "status": {
                            "org.opencontainers.runtime-spec.features": (
                                "{\"ociVersionMin\":\"1.0.0\",\"ociVersionMax\":\"1.2.0\"}"
                            )
                        },
                    }
                },
            }
            return CommandResult(0, json.dumps(payload).encode(), b"")
        if tail[:3] == ("image", "inspect", "--format"):
            inspected = {
                "Id": authority.images[0].image_id,
                "GraphDriver": {"Name": authority.storage_driver},
            }
            return CommandResult(0, json.dumps(inspected).encode(), b"")
        return CommandResult(0, b"loaded\n", b"")

    monkeypatch.setattr(
        "breadboard.rl.harness.private_docker_daemon._process_starttime",
        lambda _pid: "123",
    )
    monkeypatch.setattr(
        "breadboard.rl.harness.private_docker_daemon.os.readlink",
        lambda _path: "pid:[42]",
    )
    monkeypatch.setattr(PrivateDockerDaemonOwner, "_assert_containerd_live", lambda self: None)

    def validate_socket(binding: PrivateDockerDaemonBinding):
        current = os.stat(binding.socket_path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (binding.socket_device, binding.socket_inode):
            raise RuntimeError("socket drift")
        return current

    monkeypatch.setattr(PrivateDockerDaemonBinding, "validate_live", validate_socket)
    owner = PrivateDockerDaemonOwner(
        authority,
        prerequisite_check=lambda: None,
        daemon_environment={"PATH": str(tmp_path / "private-runtime-bin")},
        launcher=launcher,
        runner=runner,
        runtime_registration_path=str(
            tmp_path / "private-runtime-bin" / "runc"
        ),
        progress_sink=lambda event: progress.append(dict(event)),
    )
    try:
        binding = owner.start(readiness_timeout=1)
        assert len(launches) == 2
        assert launches[0][0] == (
            authority.containerd.path,
            "--address",
            authority.containerd_socket_path,
            "--root",
            authority.containerd_root,
            "--state",
            authority.containerd_state,
        )
        assert launches[1][0] == (
            authority.dockerd.path,
            "--config-file",
            binding.config_proc_path,
        )
        assert all(environment == {"PATH": str(tmp_path / "private-runtime-bin")} for _, _, _, environment in launches)
        assert all(environment == {} for _, _, _, environment in commands)
        assert all(executable.startswith(f"/proc/{os.getpid()}/fd/") for _, executable, _, _ in launches + commands)
        host_prefix = (authority.docker.path, "--host", "unix://" + authority.socket_path)
        assert all(command[:3] == host_prefix for command, _, _, _ in commands)
        load = next(command for command, _, _, _ in commands if command[3:5] == ("image", "load"))
        assert load[5] == "--input" and load[6].startswith(f"/proc/{os.getpid()}/fd/")
        inspect = next(command for command, _, _, _ in commands if command[3:5] == ("image", "inspect"))
        assert inspect[-1] == authority.images[0].image_id
        assert not any("pull" in argument for command, _, _, _ in commands for argument in command)
        events = [event["event"] for event in progress]
        assert events[:2] == ["owner_init", "owner_init"]
        assert "containerd_spawned" in events
        assert "containerd_socket_ready" in events
        assert "dockerd_spawned" in events
        assert "docker_info_attempt" in events
        assert "dockerd_ready" in events
        assert events.count("image_load") == 2
        assert events.count("image_inspect") == 2
        assert all(
            set(event) == {"event", "phase", "monotonic_ns", "details"}
            for event in progress
        )
        runtime_event = next(
            event for event in progress
            if event["event"] == "runtime_registration"
        )
        assert runtime_event["details"]["advertised_path"] == str(
            tmp_path / "private-runtime-bin" / "runc"
        )
        assert runtime_event["details"]["expected_path"] == str(
            tmp_path / "private-runtime-bin" / "runc"
        )
        init_end = next(
            event for event in progress
            if event["event"] == "owner_init" and event["phase"] == "end"
        )
        assert init_end["details"]["config_digest"].startswith("sha256:")

        Path(authority.socket_path).unlink()
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(authority.socket_path)
        sockets.append(replacement)
        with pytest.raises(PrivateDockerDaemonError, match="authority drifted"):
            _ = owner.binding
    finally:
        with pytest.raises(ExceptionGroup, match="cleanup failed"):
            owner.close()
        for server in sockets:
            server.close()
        if os.path.lexists(authority.socket_path):
            os.unlink(authority.socket_path)
        _remove_socket_parent(authority)


def test_daemon_crash_quarantines_and_owner_never_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = replace(_authority(tmp_path), log_limit_bytes=1024 * 1024)
    log_payload = b"x" * (512 * 1024)
    processes: list[_FakeProcess] = []
    sockets: list[socket.socket] = []

    def launcher(argv, *, executable, pass_fds, env, log_fd, log_limit_bytes):
        process = _FakeProcess()
        processes.append(process)
        os.write(log_fd, log_payload)
        endpoint = (
            authority.containerd_socket_path
            if "--address" in argv
            else authority.socket_path
        )
        server = socket.socket(socket.AF_UNIX)
        server.bind(endpoint)
        sockets.append(server)
        if "--address" in argv:
            ttrpc = socket.socket(socket.AF_UNIX)
            ttrpc.bind(authority.containerd_ttrpc_socket_path)
            sockets.append(ttrpc)
        else:
            Path(authority.pid_file).write_text(str(process.pid))
        return process

    def runner(argv, **_kwargs):
        payload = {
            "DockerRootDir": authority.data_root,
            "Driver": authority.storage_driver,
            "DefaultRuntime": authority.runtime_name,
            "Containerd": {"Address": authority.containerd_socket_path},
            "Runtimes": {
                authority.runtime_name: {
                    "path": str(tmp_path / "private-runtime-bin" / "runc"),
                    "status": {
                        "org.opencontainers.runtime-spec.features": (
                            "{\"ociVersionMin\":\"1.0.0\","
                            "\"ociVersionMax\":\"1.2.0\"}"
                        )
                    },
                }
            },
        }
        return CommandResult(0, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(
        "breadboard.rl.harness.private_docker_daemon._process_starttime",
        lambda _pid: "1",
    )
    monkeypatch.setattr(
        "breadboard.rl.harness.private_docker_daemon.os.readlink",
        lambda _path: "pid:[1]",
    )
    monkeypatch.setattr(
        PrivateDockerDaemonOwner, "_assert_containerd_live", lambda self: None
    )
    monkeypatch.setattr(
        PrivateDockerDaemonBinding,
        "validate_live",
        lambda self: os.stat(self.socket_path),
    )
    owner = PrivateDockerDaemonOwner(
        authority,
        prerequisite_check=lambda: None,
        daemon_environment={"PATH": str(tmp_path / "private-runtime-bin")},
        launcher=launcher,
        runner=runner,
        runtime_registration_path=str(
            tmp_path / "private-runtime-bin" / "runc"
        ),
        export_log_fds=True,
    )
    try:
        owner.start(readiness_timeout=1)
        processes[1].returncode = 17
        with pytest.raises(PrivateDockerDaemonError, match="crashed") as crashed:
            _ = owner.binding
        logs = crashed.value.details["daemon_logs"]
        assert {item["role"] for item in logs} == {"containerd", "dockerd"}
        assert all(item["size_bytes"] == len(log_payload) for item in logs)
        assert all(
            base64.b64decode(item["bytes_base64"], validate=True)
            == log_payload
            for item in logs
        )
        with pytest.raises(PrivateDockerDaemonError, match="cannot be restarted"):
            owner.start(readiness_timeout=1)
    finally:
        owner.close()
        exported = owner.detach_log_fds()
        for role, fd in zip(("containerd", "dockerd"), exported, strict=True):
            try:
                receipt = owner.log_receipts[role]
                metadata = os.fstat(fd)
                assert stat.S_ISREG(metadata.st_mode)
                assert stat.S_IMODE(metadata.st_mode) == receipt.mode == 0o600
                observed = os.pread(fd, metadata.st_size, 0)
                assert observed == log_payload
                assert (
                    "sha256:" + hashlib.sha256(observed).hexdigest()
                    == receipt.sha256
                )
            finally:
                os.close(fd)
        with pytest.raises(
            PrivateDockerDaemonError, match="export is unavailable"
        ):
            owner.detach_log_fds()
        assert not os.path.exists(authority.log_root)
        for server in sockets:
            server.close()
        _remove_socket_parent(authority)


def _exception_leaf_types(error: BaseException) -> list[str]:
    children = getattr(error, "exceptions", ())
    if children:
        return [
            leaf
            for child in children
            for leaf in _exception_leaf_types(child)
        ]
    return [type(error).__name__]


def test_partial_close_retains_nested_errors_cleans_exact_roots_and_retries(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    owner = PrivateDockerDaemonOwner(authority, prerequisite_check=lambda: None)

    class NestedFailureProcess(_FakeProcess):
        def send_signal(self, sig: int) -> None:
            self.returncode = 0
            raise ExceptionGroup(
                "signal failure",
                [
                    ValueError("primary leaf"),
                    ExceptionGroup("nested", [OSError("cleanup leaf")]),
                ],
            )

    owner._process = NestedFailureProcess()
    with pytest.raises(BaseExceptionGroup) as raised:
        owner.close()

    assert _exception_leaf_types(raised.value) == ["ValueError", "OSError"]
    for path in (
        authority.config_path,
        authority.data_root,
        authority.exec_root,
        authority.containerd_root,
        authority.containerd_state,
        authority.log_root,
        authority.containerd_ttrpc_socket_path,
    ):
        assert not os.path.lexists(path)
    owner.close()
    owner.close()
    _remove_socket_parent(authority)


@pytest.mark.parametrize("substitution", ["root", "file"])
def test_close_refuses_substituted_authority_without_deleting_replacement(
    tmp_path: Path,
    substitution: str,
) -> None:
    authority = _authority(tmp_path)
    owner = PrivateDockerDaemonOwner(authority, prerequisite_check=lambda: None)
    path = Path(
        authority.data_root
        if substitution == "root"
        else authority.config_path
    )
    admitted = path.with_name(path.name + ".admitted")
    path.rename(admitted)
    if substitution == "root":
        path.mkdir(mode=0o700)
        marker = path / "unrelated"
        marker.write_text("preserve")
    else:
        path.write_text("unrelated")
        marker = path

    with pytest.raises(BaseExceptionGroup, match="cleanup failed"):
        owner.close()

    assert marker.read_text() == (
        "preserve" if substitution == "root" else "unrelated"
    )
    if substitution == "root":
        marker.unlink()
        path.rmdir()
    else:
        path.unlink()
    admitted.rename(path)
    owner.close()
    owner.close()
    _remove_socket_parent(authority)


def test_runtime_registration_accepts_target_status_and_rejects_mutations() -> None:
    expected = "/private/.runtime-bin/runc"
    target_info = {
        "DefaultRuntime": "breadboard-runc",
        "Runtimes": {
            "breadboard-runc": {
                "path": expected,
                "status": {
                    "org.opencontainers.runtime-spec.features": (
                        "{\"ociVersionMin\":\"1.0.0\","
                        "\"ociVersionMax\":\"1.2.0\"}"
                    )
                },
            }
        },
    }
    advertised, status_digest = (
        private_docker_daemon._runtime_registration_evidence(
            target_info, "breadboard-runc", expected
        )
    )
    assert advertised == expected
    assert status_digest.startswith("sha256:")
    target_info["Runtimes"]["breadboard-runc"]["path"] = "/proc/self/fd/6"
    with pytest.raises(ValueError, match="path is not exact"):
        private_docker_daemon._runtime_registration_evidence(
            target_info, "breadboard-runc", expected
        )
    target_info["Runtimes"]["breadboard-runc"] = {
        "path": expected,
        "runtimeArgs": ["--unsafe"],
        "status": {},
    }
    with pytest.raises(ValueError, match="arguments are not exact"):
        private_docker_daemon._runtime_registration_evidence(
            target_info, "breadboard-runc", expected
        )
    target_info["Runtimes"]["breadboard-runc"] = {
        "path": expected, "status": {}, "unknownExecKey": "unsafe"
    }
    with pytest.raises(ValueError, match="keys are not exact"):
        private_docker_daemon._runtime_registration_evidence(
            target_info, "breadboard-runc", expected
        )
    target_info["Runtimes"]["breadboard-runc"] = {
        "path": expected, "status": {}
    }
    target_info["DefaultRuntime"] = "runc"
    with pytest.raises(ValueError, match="default runtime is not exact"):
        private_docker_daemon._runtime_registration_evidence(
            target_info, "breadboard-runc", expected
        )

def test_default_runner_kills_process_group_when_child_ignores_term() -> None:
    started = time.monotonic()
    result = private_docker_daemon._default_runner(
        (
            sys.executable,
            "-c",
            (
                "import signal,time;"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "time.sleep(30)"
            ),
        ),
        executable=sys.executable,
        pass_fds=(),
        env={},
        timeout=0.1,
    )
    assert result.timed_out is True
    assert result.returncode == -signal.SIGKILL
    assert time.monotonic() - started < 3.0


def test_composition_file_authority_is_operator_authored_not_node_pinned() -> None:
    payload = {
        "path": "/usr/bin/docker",
        "digest": "sha256:" + "a" * 64,
        "owner_uid": 0,
        "mode": 0o755,
        "executable": True,
    }
    parsed = composition.PinnedFileAuthorityV1.model_validate(payload, strict=True)
    assert parsed.model_dump() == payload
    with pytest.raises(ValueError):
        composition.PinnedFileAuthorityV1.model_validate(
            {**payload, "device": 8, "inode": 42}, strict=True
        )




def test_composition_rejects_hardened_runtime_without_private_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(composition, "InstalledSandboxAuthoritySet", lambda **_kwargs: object())
    runtime = InstalledRuntime(
        runtime_id="hardened",
        runtime_class=RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest="sha256:" + "1" * 64,
        executable_path="/docker",
        measured_binary_digest="sha256:" + "2" * 64,
        oci_runtime_name="breadboard-runc",
        supported_platform_versions=("1",),
        oci_runtime_binary_path="/runc",
        oci_runtime_binary_digest="sha256:" + "3" * 64,
    )
    with pytest.raises(ValueError, match="explicit private daemon authority"):
        composition.InstalledV1(
            runner_adapters=(),
            runtimes=(runtime,),
            images=(),
            security_policies=(),
            network_policies=(),
            verifiers=(),
        )


def test_composition_allows_distinct_primary_and_verifier_hardened_runtimes() -> None:
    def pinned(path: str, character: str) -> composition.PinnedFileAuthorityV1:
        return composition.PinnedFileAuthorityV1(
            path=path,
            digest="sha256:" + character * 64,
            owner_uid=0,
            mode=0o755,
            executable=True,
        )

    runtimes = tuple(
        InstalledRuntime(
            runtime_id=runtime_id,
            runtime_class=RuntimeClass.HARDENED_DOCKER,
            driver_implementation_digest="sha256:" + driver_character * 64,
            executable_path="/usr/bin/docker",
            measured_binary_digest="sha256:" + "2" * 64,
            oci_runtime_name="breadboard-runc",
            supported_platform_versions=("1.47",),
            oci_runtime_binary_path="/usr/bin/runc",
            oci_runtime_binary_digest="sha256:" + "3" * 64,
        )
        for runtime_id, driver_character in (("primary", "4"), ("verifier", "4"))
    )
    daemon = composition.PrivateDockerDaemonAuthorityV1(
        daemon_instance_id="f2-private-daemon",
        dockerd=pinned("/usr/bin/dockerd", "1"),
        docker=pinned("/usr/bin/docker", "2"),
        runc=pinned("/usr/bin/runc", "3"),
        containerd=pinned("/usr/bin/containerd", "6"),
        config_path="/run/f2/daemon.json",
        socket_path="/run/f2/docker.sock",
        pid_file="/run/f2/docker.pid",
        data_root="/run/f2/data",
        exec_root="/run/f2/exec",
        mount_stage_root="/run/f2/mount-stage",
        containerd_socket_path="/run/f2/containerd.sock",
        containerd_root="/run/f2/containerd-root",
        containerd_state="/run/f2/containerd-state",
        log_root="/run/f2/log",
        log_limit_bytes=64 * 1024,
        storage_driver="vfs",
        runtime_name="breadboard-runc",
    )

    installed = composition.InstalledV1(
        runner_adapters=(),
        runtimes=runtimes,
        images=(),
        security_policies=(),
        network_policies=(),
        verifiers=(),
        private_docker_daemon=daemon,
    )

    assert tuple(runtime.runtime_id for runtime in installed.runtimes) == (
        "primary",
        "verifier",
    )

    mismatched_verifiers = (
        replace(
            runtimes[1],
            oci_runtime_binary_digest="sha256:" + "7" * 64,
        ),
        replace(
            runtimes[1],
            runtime_class=RuntimeClass.HARDENED_GVISOR,
            runsc_binary_path="/usr/bin/runc",
            runsc_binary_digest="sha256:" + "3" * 64,
        ),
    )
    for mismatched_verifier in mismatched_verifiers:
        with pytest.raises(ValueError, match="share the exact private daemon authority"):
            composition.InstalledV1(
                runner_adapters=(),
                runtimes=(runtimes[0], mismatched_verifier),
                images=(),
                security_policies=(),
                network_policies=(),
                verifiers=(),
                private_docker_daemon=daemon,
            )
