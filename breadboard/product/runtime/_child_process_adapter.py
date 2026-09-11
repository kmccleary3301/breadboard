"""Authenticated process-group execution and retained child handoff."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from breadboard.product.runtime._child_state import ChildActivation, ChildError, ChildSpec, ExecutionTarget
from breadboard.product.runtime.artifacts import AnchoredStorage
from breadboard.product.runtime.process_child import entry_command


class _DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


RESEARCH_WORLD_WORKER_COMMAND: tuple[str, ...] = ("@breadboard/research-world-worker/v1",)
_RESEARCH_WORKER_TOKEN = RESEARCH_WORLD_WORKER_COMMAND[0]
_RESEARCH_WORKER_BINDING_ENV = "BREADBOARD_RESEARCH_WORLD_WORKER"
_RESEARCH_WORKER_MODE_ENV = "BREADBOARD_RESEARCH_WORLD_MODE"
_RESEARCH_WORKER_CLOSURE_ENV = "BREADBOARD_VERIFIED_ENGINE_ROOT"
_RESEARCH_WORKER_MAX_TASK_BYTES = 1024 * 1024
_RESEARCH_WORKER_MAX_RESULT_BYTES = 4 * 1024 * 1024


class ProcessExecutionAdapter:
    family = "execution-world-process"
    released_absence_is_terminal = True

    def __init__(self, command: Sequence[str] = ("/bin/sh", "-c", "sleep 30")) -> None:
        if os.name == "nt":
            raise ChildError("process child adapter requires POSIX process-group support")
        self.command = tuple(command)
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._status_paths: dict[str, Path] = {}
        self._workspace: Path | None = None

    def retained_config(self) -> dict[str, Any]:
        return {"command": list(self.command)}

    def bind_workspace(self, workspace: Path) -> None:
        self._workspace = workspace

    @staticmethod
    def _remove_runtime_tree(path: Path) -> None:
        if path.is_symlink():
            raise ChildError("process child runtime cannot be a symlink")
        if not path.exists():
            return
        # Frozen closures stay sealed until their execution owner releases them.
        # Grant deletion access through directory descriptors, never symlink paths.
        for _, _, _, directory in os.fwalk(path, follow_symlinks=False):
            os.fchmod(directory, 0o700)
        shutil.rmtree(path)

    def _materialize_frozen_runtime(self, workspace: Path, target_ref: str) -> Path:
        destination = self._control_path(target_ref, "runtime", workspace)
        pending = self._control_path(target_ref, "runtime.pending", workspace)
        if destination.is_symlink() or pending.is_symlink():
            raise ChildError("process child runtime cannot be a symlink")
        if destination.is_dir():
            return destination
        source = Path(sys.executable).resolve().parent
        if os.environ.get(_RESEARCH_WORKER_CLOSURE_ENV) != str(source):
            raise ChildError("frozen child requires the verified engine closure")
        self._remove_runtime_tree(pending)
        pending.mkdir(mode=0o700)
        try:
            if sys.platform == "darwin":
                subprocess.run(
                    ("/bin/cp", "-cR", str(source) + "/.", str(pending)),
                    check=True, stdin=subprocess.DEVNULL, capture_output=True,
                )
            else:
                shutil.copytree(source, pending, dirs_exist_ok=True)
            os.replace(pending, destination)
            AnchoredStorage.sync_directory(destination.parent)
        except BaseException:
            self._remove_runtime_tree(pending)
            raise
        return destination

    @staticmethod
    def _worker_binding() -> Path:
        binding = os.environ.get(_RESEARCH_WORKER_BINDING_ENV)
        if not binding:
            raise ChildError(f"{_RESEARCH_WORKER_BINDING_ENV} is required for the research worker")
        mode = os.environ.get(_RESEARCH_WORKER_MODE_ENV)
        if mode not in {"source", "frozen"}:
            raise ChildError(f"{_RESEARCH_WORKER_MODE_ENV} must be explicitly set to source or frozen")
        path = Path(binding).expanduser().resolve()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ChildError(f"{_RESEARCH_WORKER_BINDING_ENV} is not an executable file")
        if mode == "frozen":
            closure = os.environ.get(_RESEARCH_WORKER_CLOSURE_ENV)
            if not closure:
                raise ChildError(f"{_RESEARCH_WORKER_CLOSURE_ENV} is required in frozen mode")
            root = Path(closure).expanduser().resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ChildError("research worker binding is outside the verified engine closure") from error
        return path

    @staticmethod
    def _is_worker_command(command: Sequence[str]) -> bool:
        return tuple(command) == (_RESEARCH_WORKER_TOKEN,)

    def _materialize_worker(self, source: Path, workspace: Path, target_ref: str) -> Path:
        destination = self._control_path(target_ref, "worker", workspace)
        root = destination.parent
        temporary = root / f".{destination.name}.{os.urandom(8).hex()}.tmp"
        shutil.copyfile(source, temporary)
        temporary.chmod(0o700)
        os.replace(temporary, destination)
        AnchoredStorage.sync_directory(root)
        return destination

    _TERM_TIMEOUT_SECONDS = 0.5
    _KILL_TIMEOUT_SECONDS = 0.5

    @staticmethod
    def _group_alive(group: int) -> bool | None:
        try:
            output = subprocess.check_output(["ps", "-axo", "pgid=,stat="], text=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        for line in output.splitlines():
            fields = line.strip().split()
            if len(fields) < 2:
                continue
            try:
                process_group = int(fields[0])
            except ValueError:
                continue
            if process_group == group and not fields[1].startswith("Z"):
                return True
        return False

    def _wait_for_exit(self, target: Mapping[str, Any], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.observe(target) not in {"absent", "completed", "failed"}:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def _workspace_path(self, activation: ChildActivation) -> Path:
        raw = activation.workspace if activation.workspace is not None else self._workspace
        if raw is None or not str(raw).strip():
            raise ChildError("process child adapter is not bound to a workspace")
        workspace = Path(raw).expanduser().resolve()
        if not workspace.is_dir():
            raise ChildError(f"process child workspace is unavailable: {workspace}")
        return workspace

    def _control_path(
        self,
        target_ref: str,
        suffix: str,
        workspace: Path | None = None,
    ) -> Path:
        root = workspace if workspace is not None else self._workspace
        if root is None:
            raise ChildError("process child adapter is not bound to a workspace")
        breadboard_root = root / ".breadboard"
        status_root = breadboard_root / "process-children"
        status_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if status_root.is_symlink() or not status_root.is_dir():
            raise ChildError("process child status root is not a directory")
        status_root.chmod(0o700)
        AnchoredStorage.sync_directory(root)
        AnchoredStorage.sync_directory(breadboard_root)
        identity = hashlib.sha256(target_ref.encode("utf-8")).hexdigest()
        return status_root / f"{identity}.{suffix}"

    def _status_path(
        self,
        target_ref: str,
        workspace: Path | None = None,
    ) -> Path:
        return self._control_path(target_ref, "status", workspace)

    def _known_control_path(self, target_ref: str, suffix: str) -> Path:
        status_path = self._status_paths.get(target_ref)
        if status_path is not None:
            return status_path.with_suffix(f".{suffix}")
        return self._control_path(target_ref, suffix)

    @staticmethod
    def _write_control(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            stream = os.fdopen(descriptor, "wb")
            descriptor = -1
            with stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def _release(self, target_ref: str) -> None:
        self._write_control(self._known_control_path(target_ref, "release"), b"1")

    def _clear_handoff(self, target_ref: str) -> None:
        for suffix in ("task", "release", "guard", "result", "worker"):
            try:
                self._known_control_path(target_ref, suffix).unlink(missing_ok=True)
            except ChildError:
                return
        for suffix in ("runtime", "runtime.pending"):
            self._remove_runtime_tree(self._known_control_path(target_ref, suffix))

    def _completed_status(self, target_ref: str) -> bool | None:
        path = self._status_paths.get(target_ref)
        if path is None:
            try:
                path = self._status_path(target_ref)
            except ChildError:
                return None
        try:
            value = path.read_text(encoding="ascii")
        except OSError:
            return None
        if value == "0":
            return True
        try:
            int(value)
        except ValueError:
            return None
        return False
    def _worker_result_bytes(self, target_ref: str) -> bytes | None:
        try:
            value = self._known_control_path(target_ref, "result").read_bytes()
        except (ChildError, OSError):
            return None
        if len(value) > _RESEARCH_WORKER_MAX_RESULT_BYTES:
            return None
        return value

    def _worker_result_status(self, target_ref: str) -> str:
        value = self._worker_result_bytes(target_ref)
        if value is None:
            return "failed"
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "failed"
        if not isinstance(parsed, Mapping):
            return "failed"
        status = parsed.get("status")
        if status == "completed":
            return "completed"
        if status in {"failed", "unsupported"}:
            return "failed"
        return "failed"

    @staticmethod
    def _process_start_token(pid: int) -> str | int | None:
        if sys.platform.startswith("linux"):
            try:
                data = Path(f"/proc/{pid}/stat").read_bytes()
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                    encoding="ascii"
                ).strip()
            except OSError:
                return None
            fields = data[data.rfind(b")") + 1 :].split()
            if len(fields) <= 19:
                return None
            try:
                start_time = int(fields[19])
            except ValueError:
                return None
            return f"{boot_id}:{start_time}"
        if sys.platform == "darwin":
            try:
                libproc = ctypes.CDLL("libproc.dylib", use_errno=True)
            except OSError:
                return None
            proc_pidinfo = libproc.proc_pidinfo
            proc_pidinfo.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            proc_pidinfo.restype = ctypes.c_int
            info = _DarwinProcBsdInfo()
            size = ctypes.sizeof(info)
            if proc_pidinfo(pid, 3, 0, ctypes.byref(info), size) != size:
                return None
            if info.pbi_start_tvsec == 0 or info.pbi_start_tvusec >= 1_000_000:
                return None
            return (
                int(info.pbi_start_tvsec) * 1_000_000
                + int(info.pbi_start_tvusec)
            )
        return None

    @classmethod
    def _identity(cls, pid: int) -> tuple[str, int]:
        start_token = cls._process_start_token(pid)
        if start_token is None:
            raise ProcessLookupError(pid)
        group = os.getpgid(pid)
        return f"kernel:{start_token}", group
    @staticmethod
    def _pending_pid(target_ref: str) -> int | None:
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        for line in output.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and target_ref in fields[1]:
                try:
                    return int(fields[0])
                except ValueError:
                    continue
        return None

    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        workspace = self._workspace_path(activation)
        target_ref = activation.execution_target_ref
        command = self.command
        if spec.adapter_config:
            adapter_config = spec.adapter_config
            command_value = adapter_config.get("command") if isinstance(adapter_config, Mapping) else None
            if (
                not isinstance(command_value, list)
                or not command_value
                or any(type(part) is not str or not part for part in command_value)
            ):
                raise ChildError("durable process child command is malformed")
            command = tuple(command_value)
        worker = self._is_worker_command(command)
        runtime = self._materialize_frozen_runtime(workspace, target_ref) if getattr(sys, "frozen", False) else None
        environment = None
        executable = sys.executable
        if runtime is not None:
            executable = str(runtime / Path(sys.executable).name)
            environment = dict(os.environ)
            environment[_RESEARCH_WORKER_CLOSURE_ENV] = str(runtime)
            environment[_RESEARCH_WORKER_BINDING_ENV] = str(runtime / "breadboard-research-world")
            environment["BREADBOARD_RESEARCH_WORLD_HELPER"] = executable
            environment.pop("RAY_TMPDIR", None)
        if worker:
            source = self._worker_binding()
            if runtime is not None:
                command = (str(runtime / source.relative_to(Path(sys.executable).resolve().parent)),)
            else:
                command = (str(self._materialize_worker(source, workspace, target_ref)),)
        status_path = self._status_path(target_ref, workspace)
        task_path = self._control_path(target_ref, "task", workspace)
        release_path = self._control_path(target_ref, "release", workspace)
        guard_path = self._control_path(target_ref, "guard", workspace)
        result_path = self._control_path(target_ref, "result", workspace)
        self._status_paths[target_ref] = status_path
        for path in (status_path, task_path, release_path, guard_path, result_path):
            path.unlink(missing_ok=True)
        task_bytes = spec.task.encode("utf-8")
        if worker and len(task_bytes) > _RESEARCH_WORKER_MAX_TASK_BYTES:
            raise ChildError("research worker task exceeds the bounded input limit")
        self._write_control(task_path, task_bytes)
        group_token = os.urandom(32).hex()
        result_limit = str(_RESEARCH_WORKER_MAX_RESULT_BYTES if worker else 0)
        process = subprocess.Popen(
            (
                *entry_command(executable), target_ref, group_token,
                str(release_path), str(task_path), str(status_path),
                str(guard_path), str(result_path) if worker else "", result_limit, *command,
            ),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(workspace),
            env=environment,
        )
        self._processes[target_ref] = process
        try:
            token, group = self._identity(process.pid)
        except BaseException:
            self._processes.pop(target_ref, None)
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        metadata: dict[str, Any] = {
            "launch_phase": "pending",
            "process_group_token": group_token,
            "research_world_worker": worker,
        }
        target = ExecutionTarget(target_ref, process.pid, token, group, process, metadata)
        accepted = False
        try:
            publisher = activation.publish_target
            if publisher is not None:
                publisher(target)
            metadata["launch_phase"] = "release_committed"
            if publisher is not None:
                publisher(target)
            accepted = True
            self._release(target_ref)
            metadata["launch_phase"] = "released"
            if publisher is not None:
                publisher(target)
        except BaseException:
            if accepted:
                raise
            self._processes.pop(target_ref, None)
            try:
                os.killpg(group, 15)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(group, 9)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
        return target

    def observe(self, target: Mapping[str, Any]) -> str:
        target_ref = str(target.get("ref", ""))
        process = self._processes.get(target_ref)
        pid, token, group = target.get("pid"), target.get("start_token"), target.get("process_group_id")
        if type(group) is not int:
            if process is not None:
                return "pending"
            return "pending" if self._pending_pid(target_ref) is not None else "absent"

        def group_state() -> bool | None:
            try:
                return self._group_alive(group)
            except (OSError, subprocess.CalledProcessError, RuntimeError):
                return None
        def terminal_state() -> str:
            completed = self._completed_status(target_ref)
            metadata = target.get("metadata")
            worker = isinstance(metadata, Mapping) and metadata.get("research_world_worker") is True
            if worker:
                if completed is True:
                    return self._worker_result_status(target_ref)
                return "failed" if self._worker_result_bytes(target_ref) is not None else "absent"
            return "completed" if completed is True else "absent"


        def leaderless_state() -> str:
            alive = group_state()
            if alive is False:
                return terminal_state()
            return "running" if alive is True else "pending"

        if process is not None and process.poll() is not None:
            if group_state() is not False:
                return "pending"
            self._processes.pop(target_ref, None)
            return terminal_state()
        if type(pid) is not int or type(token) is not str:
            return "pending"
        try:
            observed_token, observed_group = self._identity(pid)
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            return leaderless_state()
        if observed_token != token or observed_group != group:
            return terminal_state() if group_state() is False else "pending"
        try:
            state = subprocess.check_output(["ps", "-p", str(pid), "-o", "stat="], text=True).strip()
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            return leaderless_state()
        if not state or state.startswith("Z"):
            return leaderless_state()
        return "running"

    def _verified_pending_process(self, target: Mapping[str, Any]) -> bool:
        pid = target.get("pid")
        token = target.get("start_token")
        group = target.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            return False
        try:
            if self._identity(pid) != (token, group):
                return False
            command = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="],
                text=True,
            )
        except (OSError, ProcessLookupError, subprocess.CalledProcessError):
            return False
        return str(target.get("ref", "")) in command

    def release_committed(self, target: Mapping[str, Any]) -> bool:
        if not self._verified_pending_process(target):
            return False
        self._release(str(target["ref"]))
        return True

    def release_pending(self, target: Mapping[str, Any]) -> bool:
        metadata = target.get("metadata")
        if not isinstance(metadata, Mapping):
            return False
        phase = metadata.get("launch_phase")
        if phase == "pending":
            return self._verified_pending_process(target)
        if phase == "release_committed":
            return self.release_committed(target)
        return False

    def recover(self, target: Mapping[str, Any]) -> ExecutionTarget | None:
        pid, token, group = target.get("pid"), target.get("start_token"), target.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            process = self._processes.get(str(target.get("ref", "")))
            if process is not None and process.poll() is None:
                pid = process.pid
            else:
                pending_pid = self._pending_pid(str(target.get("ref", "")))
                if pending_pid is None:
                    return None
                try:
                    pending_token, pending_group = self._identity(pending_pid)
                except (OSError, ProcessLookupError, RuntimeError):
                    return None
                pending_target = {
                    "ref": str(target.get("ref", "")),
                    "pid": pending_pid,
                    "start_token": pending_token,
                    "process_group_id": pending_group,
                }
                if self.cancel(pending_target) is False:
                    return ExecutionTarget(
                        str(target["ref"]),
                        pending_pid,
                        pending_token,
                        pending_group,
                        metadata=dict(target.get("metadata") or {}),
                    )
                return None
            try:
                token, group = self._identity(pid)
            except (OSError, ProcessLookupError, RuntimeError):
                return None
        if self.observe(
            {
                "ref": str(target.get("ref", "")),
                "pid": pid,
                "start_token": token,
                "process_group_id": group,
            }
        ) != "running":
            return None
        recovered_metadata = dict(target.get("metadata") or {})
        recovered_metadata.setdefault("launch_phase", "pending")
        return ExecutionTarget(
            str(target["ref"]),
            pid,
            token,
            group,
            metadata=recovered_metadata,
        )
    @staticmethod
    def _group_member_pids(group: int) -> tuple[int, ...]:
        try:
            output = subprocess.check_output(
                ["ps", "-axo", "pid=,pgid=,stat="],
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return ()
        members: list[int] = []
        for row in output.splitlines():
            fields = row.strip().split()
            if len(fields) < 3 or fields[2].startswith("Z"):
                continue
            try:
                pid, process_group = int(fields[0]), int(fields[1])
            except ValueError:
                continue
            if process_group == group:
                members.append(pid)
        return tuple(members)

    @staticmethod
    def _process_has_group_token(pid: int, token: str) -> bool:
        try:
            command = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="],
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return False
        return token in command

    def _verified_group_owner(self, target: Mapping[str, Any]) -> bool:
        group = target.get("process_group_id")
        metadata = target.get("metadata")
        token = (
            metadata.get("process_group_token")
            if isinstance(metadata, Mapping)
            else None
        )
        if (
            type(group) is not int
            or type(token) is not str
            or len(token) != 64
            or any(character not in "0123456789abcdef" for character in token)
        ):
            return False
        return any(
            self._process_has_group_token(pid, token)
            for pid in self._group_member_pids(group)
        )

    def _signal_verified(self, target: Mapping[str, Any], signum: int) -> bool:
        pid = target.get("pid")
        token = target.get("start_token")
        group = target.get("process_group_id")
        if type(pid) is not int or type(token) is not str or type(group) is not int:
            return False
        leader_verified = False
        try:
            observed_token, observed_group = self._identity(pid)
        except (OSError, ProcessLookupError, RuntimeError):
            pass
        else:
            leader_verified = observed_token == token and observed_group == group
        if not leader_verified and not self._verified_group_owner(target):
            return False
        try:
            os.killpg(group, signum)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return True

    def cancel(self, target: Mapping[str, Any]) -> bool:
        observed = self.observe(target)
        target_ref = str(target.get("ref", ""))
        if observed == "absent":
            self._clear_handoff(target_ref)
            return True
        if observed != "running":
            return False
        if not self._signal_verified(target, 15):
            return False
        if self._wait_for_exit(target, self._TERM_TIMEOUT_SECONDS):
            self._clear_handoff(target_ref)
            return True
        if not self._signal_verified(target, 9):
            return False
        exited = self._wait_for_exit(target, self._KILL_TIMEOUT_SECONDS)
        if exited:
            self._clear_handoff(target_ref)
        return exited

    def prepare_result(self, target: Mapping[str, Any], spec: ChildSpec) -> bytes | None:
        metadata = target.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("research_world_worker") is not True:
            return None
        value = self._worker_result_bytes(str(target.get("ref", "")))
        if value is None:
            return None
        try:
            parsed = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ChildError("research worker result is not valid JSON") from error
        if not isinstance(parsed, Mapping) or parsed.get("status") not in {"completed", "failed", "unsupported"}:
            raise ChildError("research worker result has an invalid status")
        return value

    def cleanup_handoff(self, target: Mapping[str, Any]) -> None:
        self._clear_handoff(str(target.get("ref", "")))

    def release_terminal(self, target: Mapping[str, Any]) -> bool:
        self._clear_handoff(str(target.get("ref", "")))
        return True
