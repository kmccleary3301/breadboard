from __future__ import annotations

from builtins import BaseExceptionGroup

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import stat
import signal
import tarfile
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
import sys

from breadboard.rl.harness.mount_namespace_broker import (
    MountNamespaceBroker,
    MountNamespaceBrokerError,
    _ProgressJournal,
    _execute_bounded,
)
from breadboard.rl.harness.private_docker_daemon import (
    OfflineImageAuthority,
    PinnedFileAuthority,
    PrivateDockerDaemonAuthority,
)


def executor_self_probes() -> dict[str, Any]:
    executable_fd = os.open(sys.executable, os.O_RDONLY | os.O_CLOEXEC)
    try:
        limited = _execute_bounded(
            (
                sys.executable,
                "-c",
                "import os; os.write(1,b'o'*65536); os.write(2,b'e'*65536)",
            ),
            executable_fd=executable_fd,
            timeout_ms=5_000,
            output_limit=4096,
        )
        timed = _execute_bounded(
            (sys.executable, "-c", "import time; time.sleep(60)"),
            executable_fd=executable_fd,
            timeout_ms=100,
            output_limit=4096,
        )
        held_pipe = _execute_bounded(
            (
                sys.executable,
                "-c",
                "import os,time; p=os.fork(); "
                "os.write(1,(str(p)+'\\n').encode()) if p else time.sleep(60); "
                "os._exit(0)",
            ),
            executable_fd=executable_fd,
            timeout_ms=500,
            output_limit=4096,
        )
    finally:
        os.close(executable_fd)
    descendant_pid = int(held_pipe[1].strip())
    observation = {
        "combined_limit": {
            "returncode": limited[0],
            "captured_bytes": len(limited[1]) + len(limited[2]),
            "timed_out": limited[3],
            "output_limited": limited[4],
        },
        "timeout": {
            "returncode": timed[0],
            "timed_out": timed[3],
            "output_limited": timed[4],
        },
        "held_pipe": {
            "returncode": held_pipe[0],
            "descendant_pid": descendant_pid,
            "descendant_absent": not Path(f"/proc/{descendant_pid}").exists(),
        },
    }
    if (
        observation["combined_limit"]["captured_bytes"] != 4096
        or observation["combined_limit"]["output_limited"] is not True
        or observation["timeout"]["timed_out"] is not True
        or observation["held_pipe"]["descendant_absent"] is not True
    ):
        raise RuntimeError("bounded executor self-probe failed")
    return observation


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def authority(path: Path, *, executable: bool) -> PinnedFileAuthority:
    path = path.resolve(strict=True)
    metadata = path.stat(follow_symlinks=False)
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISREG(metadata.st_mode) or bool(mode & 0o111) != executable:
        raise RuntimeError(f"operator authority mode mismatch: {path}")
    return PinnedFileAuthority(
        str(path), digest(path), metadata.st_uid, mode, executable
    )


def fixed_binary(name: str, supplied: str | None) -> Path:
    candidates = (
        [Path(supplied)]
        if supplied
        else [
            Path("/usr/bin") / name,
            Path("/usr/local/bin") / name,
            Path("/usr/sbin") / name,
            Path("/usr/local/sbin") / name,
        ]
    )
    existing = [
        item
        for item in candidates
        if item.is_absolute() and item.is_file() and not item.is_symlink()
    ]
    if len(existing) != 1 and supplied is not None:
        raise RuntimeError(f"exact {name} operator path is unavailable")
    if not existing:
        raise RuntimeError(f"no fixed absolute {name} candidate")
    return existing[0]


def image_id_from_archive(path: Path) -> str:
    with tarfile.open(path, "r:*") as archive:
        manifest_member = archive.getmember("manifest.json")
        value = json.load(archive.extractfile(manifest_member))
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        raise RuntimeError("offline archive must contain exactly one Docker image")
    config = value[0].get("Config")
    if type(config) is not str:
        raise RuntimeError("offline archive config identity is invalid")
    if config.endswith(".json") and "/" not in config:
        stem = config[:-5]
    elif config.startswith("blobs/sha256/") and config.count("/") == 2:
        stem = config.removeprefix("blobs/sha256/")
    else:
        raise RuntimeError("offline archive config identity is invalid")
    if len(stem) != 64 or any(
        character not in "0123456789abcdef" for character in stem
    ):
        raise RuntimeError("offline archive image id is invalid")
    return "sha256:" + stem


def assert_single_private_host(argv: tuple[str, ...], socket_path: str) -> None:
    expected = "unix://" + socket_path
    if (
        argv.count("--host") != 1
        or "--host" not in argv
        or argv.index("--host") + 1 >= len(argv)
        or argv[argv.index("--host") + 1] != expected
    ):
        raise RuntimeError("Docker command host authority is not exact")


def remove_scratch_exact(scratch: Path) -> None:
    shutil.rmtree(scratch)
    if scratch.exists():
        raise RuntimeError("probe scratch root remained after cleanup")


class DockerCommandError(RuntimeError):
    def __init__(self, observation: dict[str, Any]) -> None:
        super().__init__(
            "private Docker command failed: " + json.dumps(observation, sort_keys=True)
        )
        self.observation = observation


async def docker(
    broker: MountNamespaceBroker, *tail: str, timeout_ms: int = 120_000
) -> dict[str, Any]:
    broker.record_progress("docker_command", "begin", {"tail": list(tail)})
    result = await broker.docker_cli_executor.execute(
        broker.docker_invocation,
        ("--host", "unix://" + broker.daemon_binding.socket_path, *tail),
        timeout_ms=timeout_ms,
        output_limit=1024 * 1024,
        environment=(),
    )
    assert_single_private_host(result.argv, broker.daemon_binding.socket_path)
    observation = {
        "argv": list(result.argv),
        "returncode": result.returncode,
        "stdout_sha256": "sha256:" + hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": "sha256:" + hashlib.sha256(result.stderr).hexdigest(),
        "timed_out": result.timed_out,
        "output_limited": result.output_limited,
        "stdout_base64": base64.b64encode(result.stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(result.stderr).decode("ascii"),
    }
    broker.record_progress("docker_command", "end", observation)
    if result.returncode or result.timed_out or result.output_limited:
        raise DockerCommandError(observation)
    observation["stdout"] = result.stdout.decode("utf-8", "strict")
    return observation


async def lifecycle(args: argparse.Namespace) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RuntimeError("direct private broker probe requires effective uid 0")
    progress_fd = -1
    progress_closed = False
    scratch: Path | None = None
    broker: MountNamespaceBroker | None = None
    workspace_fd = profile_fd = -1
    report: dict[str, Any] = {
        "schema_version": "bb.rl.f2.private-broker-lifecycle.v1",
        "attempt_id": args.attempt_id,
    }
    probe_error: BaseException | None = None
    try:
        paths = {
            name: fixed_binary(name, getattr(args, name))
            for name in ("dockerd", "docker", "containerd", "runc")
        }
        archive = Path(args.offline_image_tar).resolve(strict=True)
        image_id = (
            None
            if args.stage_diagnostic
            else args.image_id or image_id_from_archive(archive)
        )
        source_digest = (
            None if args.stage_diagnostic else args.source_image_digest or image_id
        )
        progress_path = Path(args.progress_path)
        if not progress_path.is_absolute() or progress_path != Path(
            os.path.normpath(progress_path)
        ):
            raise RuntimeError("progress path must be canonical and absolute")
        progress_parent = progress_path.parent
        progress_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        progress_parent_metadata = progress_parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(progress_parent_metadata.st_mode)
            or progress_parent_metadata.st_uid != 0
            or stat.S_IMODE(progress_parent_metadata.st_mode) & 0o077
        ):
            raise RuntimeError("progress parent is not root-owned and sealed")
        progress_fd = os.open(
            progress_path,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | os.O_NOFOLLOW,
            0o600,
        )
        progress = _ProgressJournal(progress_fd, writer="probe-parent")
        progress({"event": "progress_open", "phase": "end"})
        scratch = Path(
            tempfile.mkdtemp(prefix="f2-private-broker-", dir=args.scratch_parent)
        ).resolve()
        workspace = scratch / "descriptor-workspace"
        workspace.mkdir(mode=0o755)
        (workspace / "sentinel").write_text("descriptor-stage\n", encoding="ascii")
        profile = scratch / "descriptor-profile.json"
        profile.write_bytes(b'{"defaultAction":"SCMP_ACT_ALLOW"}')
        roots = {
            name: str(scratch / name)
            for name in (
                "config.json",
                "docker.sock",
                "docker.pid",
                "docker-data",
                "docker-exec",
                "descriptor-mounts",
                "containerd.sock",
                "containerd-root",
                "containerd-state",
                "daemon-logs",
            )
        }
        daemon = PrivateDockerDaemonAuthority(
            daemon_instance_id=args.attempt_id,
            dockerd=authority(paths["dockerd"], executable=True),
            docker=authority(paths["docker"], executable=True),
            containerd=authority(paths["containerd"], executable=True),
            runc=authority(paths["runc"], executable=True),
            config_path=roots["config.json"],
            socket_path=roots["docker.sock"],
            pid_file=roots["docker.pid"],
            data_root=roots["docker-data"],
            exec_root=roots["docker-exec"],
            mount_stage_root=roots["descriptor-mounts"],
            images=(
                ()
                if args.stage_diagnostic
                else (
                    OfflineImageAuthority(
                        authority(archive, executable=False), image_id, source_digest
                    ),
                )
            ),
            containerd_socket_path=roots["containerd.sock"],
            containerd_root=roots["containerd-root"],
            containerd_state=roots["containerd-state"],
            log_root=roots["daemon-logs"],
            log_limit_bytes=1024 * 1024,
            storage_driver=args.storage_driver,
            runtime_name="breadboard-runc",
        )
        broker: MountNamespaceBroker | None = None
        workspace_fd = profile_fd = -1
        progress_closed = False
        report: dict[str, Any] = {
            "schema_version": "bb.rl.f2.private-broker-lifecycle.v1",
            "attempt_id": args.attempt_id,
        }
        report["executor_self_probes"] = executor_self_probes()
        probe_error: BaseException | None = None
        progress({"event": "broker_ctor", "phase": "begin"})
        broker = MountNamespaceBroker(
            daemon.mount_stage_root,
            daemon_authority=daemon,
            progress_fd=progress_fd,
        )
        progress({"event": "broker_ctor", "phase": "end"})
        report["broker"] = asdict(broker.observation)
        report["daemon"] = asdict(broker.daemon_binding)
        report["containerd"] = dict(broker.containerd_observation)
        binding = broker.daemon_binding
        config_raw = os.pread(binding.config_fd, binding.config_size, 0)
        report["runtime_registration"] = {
            "binding_runtime_proc_path": binding.runtime_proc_path,
            "config_base64": base64.b64encode(config_raw).decode("ascii"),
            "config_sha256": "sha256:" + hashlib.sha256(config_raw).hexdigest(),
            "docker_info_runtimes": await docker(
                broker, "info", "--format", "{{json .Runtimes}}"
            ),
        }
        if args.stage_diagnostic:
            workspace_fd = os.open(
                workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            workspace_stat = os.fstat(workspace_fd)
            broker.record_progress("stage_workspace", "begin")
            workspace_stage = await broker.stage(
                workspace_fd,
                expected_device=workspace_stat.st_dev,
                expected_inode=workspace_stat.st_ino,
                directory=True,
                lease_id=args.attempt_id,
                destination="/workspace",
            )
            broker.record_progress("stage_workspace", "end")
            report["stages"] = [asdict(workspace_stage)]
            broker.record_progress("release_workspace", "begin")
            await broker.release(workspace_stage)
            broker.record_progress("release_workspace", "end")
            broker.record_progress("probe_broker_close", "begin")
            broker.close()
            progress({"event": "probe_broker_close", "phase": "end"})
            report["cleanup"] = {
                "broker_process_absent": not Path(f"/proc/{broker.pid}").exists(),
                "stage_root_absent": not Path(daemon.mount_stage_root).exists(),
                "daemon_paths_absent": all(
                    not Path(path).exists() for path in roots.values()
                ),
            }
            if not all(report["cleanup"].values()):
                raise RuntimeError("private broker diagnostic cleanup proof failed")
            progress({"event": "report", "phase": "ready"})
            os.fsync(progress_fd)
            os.close(progress_fd)
            progress_closed = True
            progress_raw = progress_path.read_bytes()
            report["progress_journal"] = {
                "path": str(progress_path),
                "sha256": "sha256:" + hashlib.sha256(progress_raw).hexdigest(),
                "size_bytes": len(progress_raw),
            }
            report["diagnostic_only"] = True
            return report
        report["broker"] = asdict(broker.observation)
        report["daemon"] = asdict(broker.daemon_binding)
        report["containerd"] = dict(broker.containerd_observation)
        workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        profile_fd = os.open(profile, os.O_RDONLY | os.O_NOFOLLOW)
        workspace_stat, profile_stat = os.fstat(workspace_fd), os.fstat(profile_fd)
        broker.record_progress("stage_workspace", "begin")
        workspace_stage = await broker.stage(
            workspace_fd,
            expected_device=workspace_stat.st_dev,
            expected_inode=workspace_stat.st_ino,
            directory=True,
            lease_id=args.attempt_id,
            destination="/workspace",
        )
        broker.record_progress("stage_workspace", "end")
        broker.record_progress("stage_profile", "begin")
        profile_stage = await broker.stage(
            profile_fd,
            expected_device=profile_stat.st_dev,
            expected_inode=profile_stat.st_ino,
            directory=False,
            lease_id=args.attempt_id,
            destination="/probe-profile.json",
        )
        broker.record_progress("stage_profile", "end")
        report["stages"] = [asdict(workspace_stage), asdict(profile_stage)]
        broker.record_progress("path_replacement", "begin")
        held_workspace_path = scratch / "descriptor-workspace-held"
        held_profile_path = scratch / "descriptor-profile-held.json"
        workspace.rename(held_workspace_path)
        profile.rename(held_profile_path)
        workspace.mkdir(mode=0o700)
        (workspace / "replacement").write_text("replacement\n", encoding="ascii")
        profile.write_bytes(b'{"replacement":true}')
        replacement_workspace = workspace.stat(follow_symlinks=False)
        replacement_profile = profile.stat(follow_symlinks=False)
        report["path_replacement"] = {
            "workspace_descriptor": [workspace_stat.st_dev, workspace_stat.st_ino],
            "workspace_replacement": [
                replacement_workspace.st_dev,
                replacement_workspace.st_ino,
            ],
            "profile_descriptor": [profile_stat.st_dev, profile_stat.st_ino],
            "profile_replacement": [
                replacement_profile.st_dev,
                replacement_profile.st_ino,
            ],
        }
        broker.record_progress("path_replacement", "end")
        if (
            report["path_replacement"]["workspace_descriptor"]
            == report["path_replacement"]["workspace_replacement"]
            or report["path_replacement"]["profile_descriptor"]
            == report["path_replacement"]["profile_replacement"]
        ):
            raise RuntimeError("descriptor path replacement did not change identity")
        commands: list[dict[str, Any]] = []
        active_names: list[str] = []
        try:
            for role in ("primary", "verifier"):
                name = "bb-f2-" + role + "-" + args.attempt_id
                active_names.append(name)
                await broker.validate(workspace_stage, workspace_fd)
                await broker.validate(profile_stage, profile_fd)
                commands.append(
                    await docker(
                        broker,
                        "create",
                        "--name",
                        name,
                        "--label",
                        "bb.rl.f2.role=" + role,
                        "--mount",
                        f"type=bind,src={workspace_stage.source_path},dst=/workspace",
                        "--mount",
                        f"type=bind,src={profile_stage.source_path},dst=/probe-profile.json,readonly",
                        "--network",
                        "none",
                        "--pull=never",
                        "--entrypoint",
                        "/bin/sh",
                        image_id,
                        "-c",
                        "set -x; ls -lan /workspace /workspace/sentinel /probe-profile.json; "
                        "sha256sum /workspace/sentinel /probe-profile.json; "
                        "cat /workspace/sentinel /probe-profile.json; "
                        "grep -qx descriptor-stage /workspace/sentinel && "
                        "grep -q defaultAction /probe-profile.json",
                    )
                )
                commands.append(await docker(broker, "start", "--attach", name))
                inspected = await docker(broker, "inspect", name)
                document = json.loads(inspected["stdout"])
                if type(document) is not list or len(document) != 1:
                    raise RuntimeError("Docker inspect result is not exact")
                commands.append(inspected)
                await broker.validate(workspace_stage, workspace_fd)
                await broker.validate(profile_stage, profile_fd)
                commands.append(await docker(broker, "rm", name))
                absent_result = await broker.docker_cli_executor.execute(
                    broker.docker_invocation,
                    (
                        "--host",
                        "unix://" + broker.daemon_binding.socket_path,
                        "container",
                        "inspect",
                        name,
                    ),
                    timeout_ms=30_000,
                    output_limit=1024 * 1024,
                    environment=(),
                )
                assert_single_private_host(
                    absent_result.argv, broker.daemon_binding.socket_path
                )
                if (
                    absent_result.returncode == 0
                    or absent_result.timed_out
                    or absent_result.output_limited
                    or b"No such" not in absent_result.stderr
                ):
                    raise RuntimeError("container final absence was not proven")
                commands.append(
                    {
                        "name": name,
                        "absent_after_rm": True,
                        "inspect_returncode": absent_result.returncode,
                        "stderr_sha256": "sha256:"
                        + hashlib.sha256(absent_result.stderr).hexdigest(),
                    }
                )
                active_names.remove(name)
        except BaseException as root_error:
            cleanup_observations: list[dict[str, Any]] = []
            cleanup_errors: list[str] = []
            if isinstance(root_error, DockerCommandError):
                commands.append({"failed_command": root_error.observation})
            for name in active_names:
                for action, tail in (
                    ("inspect", ("inspect", name)),
                    ("logs", ("logs", name)),
                    ("rm_force", ("rm", "-f", name)),
                    ("post_inspect", ("container", "inspect", name)),
                ):
                    try:
                        result = await broker.docker_cli_executor.execute(
                            broker.docker_invocation,
                            (
                                "--host",
                                "unix://" + broker.daemon_binding.socket_path,
                                *tail,
                            ),
                            timeout_ms=15_000,
                            output_limit=1024 * 1024,
                            environment=(),
                        )
                        assert_single_private_host(
                            result.argv, broker.daemon_binding.socket_path
                        )
                        cleanup_observations.append(
                            {
                                "name": name,
                                "action": action,
                                "returncode": result.returncode,
                                "timed_out": result.timed_out,
                                "output_limited": result.output_limited,
                                "stdout_base64": base64.b64encode(result.stdout).decode(
                                    "ascii"
                                ),
                                "stderr_base64": base64.b64encode(result.stderr).decode(
                                    "ascii"
                                ),
                            }
                        )
                    except BaseException as action_error:
                        cleanup_observations.append(
                            {
                                "name": name,
                                "action": action,
                                "error": repr(action_error),
                            }
                        )
            for label, stage in (
                ("profile", profile_stage),
                ("workspace", workspace_stage),
            ):
                try:
                    await broker.release(stage)
                except BaseException as cleanup_error:
                    cleanup_errors.append(label + ":" + repr(cleanup_error))
            try:
                broker.close()
            except BaseException as cleanup_error:
                cleanup_errors.append("broker:" + repr(cleanup_error))
            broker = None
            raise RuntimeError(
                json.dumps(
                    {
                        "root_error": repr(root_error),
                        "commands": commands,
                        "container_cleanup": cleanup_observations,
                        "cleanup_errors": cleanup_errors,
                    },
                    sort_keys=True,
                )
            ) from root_error
        report["commands"] = commands
        broker.record_progress("release_profile", "begin")
        await broker.release(profile_stage)
        broker.record_progress("release_profile", "end")
        broker.record_progress("release_workspace", "begin")
        await broker.release(workspace_stage)
        broker.record_progress("release_workspace", "end")
        broker.record_progress("probe_broker_close", "begin")
        broker.close()
        progress({"event": "probe_broker_close", "phase": "end"})
        report["cleanup"] = {
            "broker_process_absent": not Path(f"/proc/{broker.pid}").exists(),
            "daemon_process_absent": not Path(
                f"/proc/{report['daemon']['daemon_pid']}"
            ).exists(),
            "containerd_process_absent": not Path(
                f"/proc/{report['containerd']['pid']}"
            ).exists(),
            "stage_root_absent": not Path(daemon.mount_stage_root).exists(),
            "daemon_paths_absent": all(
                not Path(path).exists() for path in roots.values()
            ),
        }
        if not all(report["cleanup"].values()):
            raise RuntimeError("private broker cleanup proof failed")
        progress({"event": "report", "phase": "ready"})
        os.fsync(progress_fd)
        os.close(progress_fd)
        progress_closed = True
        progress_raw = progress_path.read_bytes()
        report["progress_journal"] = {
            "path": str(progress_path),
            "sha256": "sha256:" + hashlib.sha256(progress_raw).hexdigest(),
            "size_bytes": len(progress_raw),
        }
        return report
    except BaseException as exc:
        probe_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if progress_fd >= 0 and not progress_closed:
            try:
                os.fsync(progress_fd)
                os.close(progress_fd)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        for fd in (profile_fd, workspace_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
        if broker is not None and not broker._resources_closed:
            try:
                broker.close()
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if scratch is not None:
            try:
                remove_scratch_exact(scratch)
                report.setdefault("cleanup", {})["scratch_root_absent"] = True
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            errors = (
                cleanup_errors
                if probe_error is None
                else [probe_error, *cleanup_errors]
            )
            raise BaseExceptionGroup("probe cleanup failed", errors) from None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--offline-image-tar", required=True)
    parser.add_argument("--image-id")
    parser.add_argument("--source-image-digest")
    parser.add_argument("--storage-driver", choices=("vfs", "overlay2"), default="vfs")
    parser.add_argument("--scratch-parent", default="/tmp")
    parser.add_argument("--progress-path", required=True)
    parser.add_argument("--stage-diagnostic", action="store_true")
    for name in ("dockerd", "docker", "containerd", "runc"):
        parser.add_argument("--" + name)
    args = parser.parse_args()

    def watchdog(_signum: int, _frame: object) -> None:
        raise TimeoutError("private broker probe exceeded total watchdog")

    previous_handler = signal.signal(signal.SIGALRM, watchdog)
    signal.alarm(60 if args.stage_diagnostic else 120)
    try:
        try:
            report = asyncio.run(lifecycle(args))
        except BaseException as exc:
            failure = {
                "schema_version": "bb.rl.f2.private-broker-failure.v1",
                "attempt_id": args.attempt_id,
                "code": (
                    exc.code
                    if isinstance(exc, MountNamespaceBrokerError)
                    else "probe_failed"
                ),
                "message": str(exc)[:1024],
                "details": (
                    exc.details
                    if isinstance(exc, MountNamespaceBrokerError)
                    else {"exception": type(exc).__name__}
                ),
                "fail_closed": True,
            }
            sys.stderr.buffer.write(canonical(failure) + b"\n")
            return 1
        print(canonical(report).decode("ascii"))
        return 0
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
