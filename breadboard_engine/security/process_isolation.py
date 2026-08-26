"""Fail-closed credential isolation for model-controlled host processes."""

from __future__ import annotations

import argparse
import ctypes
import errno
import os
import platform
import shlex
import stat
import struct
import sys
import threading
from pathlib import Path
from typing import Mapping, Sequence
if __package__:
    from .child_environment import initial_provider_credential_keys



class ProcessIsolationUnavailable(RuntimeError):
    """Raised when the host cannot enforce the required process boundary."""


_SQLITE_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")
_REGISTERED_PATHS_LOCK = threading.RLock()
_REGISTERED_PROTECTED_PATHS: dict[str, Path] = {}
_HARDLINK_BOUNDARY_LOCK = threading.RLock()
_VALIDATED_HARDLINK_BOUNDARIES: dict[
    tuple[tuple[int, int], tuple[str, ...]],
    None,
] = {}
_MAX_VALIDATED_HARDLINK_BOUNDARIES = 256


def _normalized_path(path: str | os.PathLike[str]) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProcessIsolationUnavailable(
            "protected credential path is invalid"
        ) from exc


def register_protected_credential_path(
    path: str | os.PathLike[str],
    *,
    sqlite_sidecars: bool = False,
) -> tuple[Path, ...]:
    """Register a programmatic credential path for all later child launches."""
    base = _normalized_path(path)
    candidates = tuple(
        _normalized_path(f"{base}{suffix}")
        for suffix in (_SQLITE_SIDECAR_SUFFIXES if sqlite_sidecars else ("",))
    )
    with _REGISTERED_PATHS_LOCK:
        for candidate in candidates:
            _REGISTERED_PROTECTED_PATHS[str(candidate)] = candidate
    return candidates


def _sqlite_paths(path: str | os.PathLike[str]) -> tuple[Path, ...]:
    base = _normalized_path(path)
    return tuple(
        _normalized_path(f"{base}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES
    )


def protected_credential_paths(
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Return credential locations without opening or reading credential data."""
    source = os.environ if environment is None else environment
    raw_home = source.get("HOME")
    home = _normalized_path(raw_home) if raw_home else Path.home().resolve(strict=False)
    paths: list[Path] = [home / ".breadboard", home / ".codex"]
    for key in ("BREADBOARD_CREDENTIAL_STORE_PATH", "BREADBOARD_CREDENTIAL_DB"):
        explicit = source.get(key)
        if explicit:
            paths.extend(_sqlite_paths(explicit))
    state_dir = source.get("BREADBOARD_STATE_DIR")
    if state_dir:
        paths.append(_normalized_path(state_dir))
    with _REGISTERED_PATHS_LOCK:
        paths.extend(_REGISTERED_PROTECTED_PATHS.values())
    return tuple(dict.fromkeys(_normalized_path(path) for path in paths))


def _resolved_existing(path: str | os.PathLike[str]) -> Path | None:
    try:
        return Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _paths_overlap(left: Path, right: Path) -> bool:
    for candidate, root in ((left, right), (right, left)):
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _same_object(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _linked_regular_identities(
    root: Path,
) -> set[tuple[int, int]]:
    try:
        root_metadata = os.stat(root, follow_symlinks=False)
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise ProcessIsolationUnavailable(
            "credential hardlink boundary is unavailable"
        ) from exc
    if stat.S_ISREG(root_metadata.st_mode):
        if root_metadata.st_nlink > 1:
            return {(root_metadata.st_dev, root_metadata.st_ino)}
        return set()
    if not stat.S_ISDIR(root_metadata.st_mode):
        return set()

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise ProcessIsolationUnavailable(
            "credential hardlink boundary is unavailable"
        ) from exc
    linked: set[tuple[int, int]] = set()

    def visit(descriptor: int) -> None:
        try:
            names = os.listdir(descriptor)
        except OSError as exc:
            raise ProcessIsolationUnavailable(
                "credential hardlink boundary is unavailable"
            ) from exc
        for name in names:
            try:
                before = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ProcessIsolationUnavailable(
                    "credential hardlink boundary is unavailable"
                ) from exc
            if stat.S_ISDIR(before.st_mode):
                child = -1
                try:
                    child = os.open(
                        name,
                        flags,
                        dir_fd=descriptor,
                    )
                    after = os.fstat(child)
                    if not _same_object(before, after):
                        raise ProcessIsolationUnavailable(
                            "credential hardlink boundary changed"
                        )
                    visit(child)
                except FileNotFoundError:
                    continue
                except ProcessIsolationUnavailable:
                    raise
                except OSError as exc:
                    raise ProcessIsolationUnavailable(
                        "credential hardlink boundary is unavailable"
                    ) from exc
                finally:
                    if child >= 0:
                        os.close(child)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink > 1:
                linked.add((before.st_dev, before.st_ino))

    try:
        if not _same_object(root_metadata, os.fstat(root_fd)):
            raise ProcessIsolationUnavailable("credential hardlink boundary changed")
        visit(root_fd)
    finally:
        os.close(root_fd)
    return linked


def _validate_hardlink_boundary(
    workspace: Path,
    protected_paths: Sequence[Path],
) -> None:
    root_metadata = os.stat(workspace, follow_symlinks=False)
    key = (
        (root_metadata.st_dev, root_metadata.st_ino),
        tuple(sorted(str(path) for path in protected_paths)),
    )
    with _HARDLINK_BOUNDARY_LOCK:
        if key in _VALIDATED_HARDLINK_BOUNDARIES:
            return
    workspace_links = _linked_regular_identities(workspace)
    if workspace_links:
        protected_links: set[tuple[int, int]] = set()
        for path in protected_paths:
            protected_links.update(_linked_regular_identities(path))
        if workspace_links.intersection(protected_links):
            raise ProcessIsolationUnavailable(
                "process workspace contains a protected credential hardlink"
            )
    with _HARDLINK_BOUNDARY_LOCK:
        if len(_VALIDATED_HARDLINK_BOUNDARIES) >= (_MAX_VALIDATED_HARDLINK_BOUNDARIES):
            _VALIDATED_HARDLINK_BOUNDARIES.pop(
                next(iter(_VALIDATED_HARDLINK_BOUNDARIES))
            )
        _VALIDATED_HARDLINK_BOUNDARIES[key] = None


def _validate_workspace(
    workspace: str | os.PathLike[str],
    protected_paths: Sequence[Path],
) -> Path:
    resolved = _resolved_existing(workspace)
    if resolved is None or not resolved.is_dir():
        raise ProcessIsolationUnavailable("process workspace is unavailable")
    for protected in protected_paths:
        if _paths_overlap(resolved, protected.resolve(strict=False)):
            raise ProcessIsolationUnavailable(
                "process workspace overlaps a protected credential location"
            )
    return resolved


def _validate_working_directory(
    working_directory: str | os.PathLike[str] | None,
    workspace: Path,
) -> Path:
    resolved = _resolved_existing(working_directory or workspace)
    if resolved is None or not resolved.is_dir():
        raise ProcessIsolationUnavailable("process working directory is unavailable")
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ProcessIsolationUnavailable(
            "process working directory escapes the workspace"
        ) from exc
    return resolved


def validate_workspace_credential_boundary(
    workspace: str | os.PathLike[str],
    *,
    protected_paths: Sequence[str | os.PathLike[str]] = (),
) -> Path:
    """Reject workspace overlap or shared inodes before an external bind."""
    protected = tuple(
        dict.fromkeys(
            (
                *protected_credential_paths(),
                *(_normalized_path(path) for path in protected_paths),
            )
        )
    )
    root = _validate_workspace(workspace, protected)
    _validate_hardlink_boundary(root, protected)
    return root


def _command_argv(
    command: str | Sequence[str],
    *,
    shell: bool,
) -> tuple[str, ...]:
    if shell:
        if not isinstance(command, str):
            raise ProcessIsolationUnavailable("shell command must be text")
        if not command.strip() or "\x00" in command:
            raise ProcessIsolationUnavailable("process command is empty or invalid")
        return ("/bin/bash", "-lc", command)
    if isinstance(command, str):
        argv = tuple(shlex.split(command))
    else:
        argv = tuple(str(value) for value in command)
    if not argv or not argv[0] or any("\x00" in value for value in argv):
        raise ProcessIsolationUnavailable("process command is empty or invalid")
    return argv


_VIRTUAL_READ_MOUNTS = tuple(Path(path) for path in ("/dev", "/proc", "/run", "/sys"))


def _under_virtual_read_mount(path: Path) -> bool:
    return any(path == root or root in path.parents for root in _VIRTUAL_READ_MOUNTS)


def _toolchain_roots(
    environment: Mapping[str, str],
    protected_paths: Sequence[Path],
) -> tuple[Path, ...]:
    candidates = list(os.get_exec_path(dict(environment)))
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "NODE_PATH",
        "JAVA_HOME",
        "GOPATH",
        "GOMODCACHE",
        "CARGO_HOME",
        "RUSTUP_HOME",
    ):
        value = environment.get(key)
        if value:
            candidates.extend(value.split(os.pathsep))
    raw_home = environment.get("HOME")
    home = (
        _resolved_existing(raw_home) if raw_home else Path.home().resolve(strict=False)
    )
    roots: dict[str, Path] = {}
    for raw in candidates:
        if not raw:
            continue
        resolved = _resolved_existing(raw)
        if resolved is None or resolved == home or _under_virtual_read_mount(resolved):
            continue
        if any(_paths_overlap(resolved, protected) for protected in protected_paths):
            continue
        roots[str(resolved)] = resolved
    return tuple(roots.values())


def _command_runtime_roots(
    command: Sequence[str],
    environment: Mapping[str, str],
    protected_paths: Sequence[Path],
) -> tuple[Path, ...]:
    executable = Path(command[0]).expanduser()
    if not executable.is_absolute():
        executable = next(
            (
                Path(directory) / executable
                for directory in os.get_exec_path(dict(environment))
                if _resolved_existing(Path(directory) / executable) is not None
            ),
            executable,
        )
    lexical = Path(os.path.abspath(executable))
    resolved = _resolved_existing(lexical)
    if resolved is None or not resolved.is_file():
        return ()
    if any(_paths_overlap(resolved, protected) for protected in protected_paths):
        raise ProcessIsolationUnavailable(
            "process executable overlaps a protected credential location"
        )
    link_runtime_roots: tuple[Path, ...] = ()
    try:
        link_target = Path(os.readlink(lexical))
    except OSError:
        pass
    else:
        if not link_target.is_absolute():
            link_target = lexical.parent / link_target
        link_target = Path(os.path.abspath(link_target))
        link_runtime_roots = (
            link_target.parent,
            link_target.parent.parent,
        )
    home = Path.home().resolve(strict=False)
    roots: dict[str, Path] = {}
    for candidate in (
        lexical.parent,
        lexical.parent.parent,
        *link_runtime_roots,
        resolved.parent,
        resolved.parent.parent,
    ):
        resolved_candidate = candidate.resolve(strict=False)
        if (
            candidate == Path("/")
            or resolved_candidate == home
            or _under_virtual_read_mount(resolved_candidate)
            or any(
                _paths_overlap(resolved_candidate, protected)
                for protected in protected_paths
            )
        ):
            continue
        roots[str(candidate)] = candidate
    return tuple(roots.values())


def _linux_read_roots(
    workspace: Path,
    environment: Mapping[str, str],
    protected_paths: Sequence[Path],
    command: Sequence[str],
) -> tuple[Path, ...]:
    roots: dict[str, Path] = {}
    for raw in ("/bin", "/etc", "/lib", "/lib64", "/opt", "/sbin", "/usr"):
        resolved = _resolved_existing(raw)
        if resolved is not None:
            roots[str(resolved)] = resolved
    for root in _toolchain_roots(environment, protected_paths):
        roots[str(root)] = root
    for runtime_command in (command, (sys.executable,)):
        for root in _command_runtime_roots(
            runtime_command,
            environment,
            protected_paths,
        ):
            roots[str(root)] = root
    roots = {
        key: root for key, root in roots.items() if not _paths_overlap(root, workspace)
    }
    return tuple(sorted(roots.values(), key=str))


def _darwin_read_roots(
    workspace: Path,
    environment: Mapping[str, str],
    protected_paths: Sequence[Path],
    command: Sequence[str],
) -> tuple[Path, ...]:
    roots: dict[str, Path] = {}
    for raw in (
        "/Applications",
        "/Library",
        "/System",
        "/bin",
        "/etc",
        "/nix/store",
        "/opt/homebrew",
        "/private/etc",
        "/private/var/db",
        "/var/select",
        "/sbin",
        "/usr",
    ):
        lexical = Path(raw)
        resolved = _resolved_existing(lexical)
        if resolved is not None:
            roots[str(resolved)] = resolved
            if lexical != resolved:
                roots[str(lexical)] = lexical
    for root in _toolchain_roots(environment, protected_paths):
        roots[str(root)] = root
    for root in _command_runtime_roots(
        command,
        environment,
        protected_paths,
    ):
        roots[str(root)] = root
    return tuple(
        sorted(
            (root for root in roots.values() if not _paths_overlap(root, workspace)),
            key=str,
        )
    )


def _profile_string(path: Path) -> str:
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _darwin_profile(
    workspace: Path,
    protected_paths: Sequence[Path],
    read_roots: Sequence[Path],
) -> str:
    def selector(path: Path, operation: str) -> str:
        return f'({operation} "{_profile_string(path)}")'

    read_rules = " ".join(
        (
            '(literal "/")',
            selector(workspace, "subpath"),
            *(
                selector(path, "subpath" if path.is_dir() else "literal")
                for path in read_roots
            ),
            '(literal "/dev/null")',
            '(literal "/dev/zero")',
            '(literal "/dev/random")',
            '(literal "/dev/urandom")',
            '(literal "/dev/tty")',
        )
    )
    metadata_roots: dict[str, Path] = {}
    for readable in (workspace, *read_roots):
        for ancestor in readable.parents:
            if ancestor == Path("/"):
                continue
            if any(
                ancestor == protected or protected in ancestor.parents
                for protected in protected_paths
            ):
                continue
            metadata_roots[str(ancestor)] = ancestor
    metadata_rules = " ".join(
        selector(path, "literal") for path in sorted(metadata_roots.values(), key=str)
    )
    write_rules = " ".join(
        (
            selector(workspace, "subpath"),
            '(literal "/dev/null")',
            '(literal "/dev/tty")',
        )
    )
    protected_rules: list[str] = []
    for path in protected_paths:
        selectors = " ".join((selector(path, "literal"), selector(path, "subpath")))
        protected_rules.extend(
            (
                f"(deny file-read* {selectors})",
                f"(deny file-write* {selectors})",
            )
        )
    return "\n".join(
        (
            "(version 1)",
            "(deny default)",
            "(allow process-exec)",
            "(allow process-fork)",
            "(allow signal (target self))",
            '(allow sysctl-read (sysctl-name-regex #"^hw\\."))',
            '(allow sysctl-read (sysctl-name "kern.hostname"))',
            '(allow sysctl-read (sysctl-name "kern.osrelease"))',
            '(allow sysctl-read (sysctl-name "kern.ostype"))',
            '(allow sysctl-read (sysctl-name "kern.version"))',
            f"(allow file-read* {read_rules})",
            *(
                (f"(allow file-read-metadata {metadata_rules})",)
                if metadata_rules
                else ()
            ),
            f"(allow file-write* {write_rules})",
            *protected_rules,
        )
    )


def build_restricted_process_command(
    command: str | Sequence[str],
    *,
    workspace: str | os.PathLike[str],
    shell: bool,
    environment: Mapping[str, str],
    protected_paths: Sequence[str | os.PathLike[str]] = (),
    working_directory: str | os.PathLike[str] | None = None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Return isolated argv/environment, or fail before process creation."""
    protected = tuple(
        dict.fromkeys(
            (
                *protected_credential_paths(),
                *protected_credential_paths(environment),
                *(_normalized_path(path) for path in protected_paths),
            )
        )
    )
    root = _validate_workspace(workspace, protected)
    _validate_hardlink_boundary(root, protected)
    cwd = _validate_working_directory(working_directory, root)
    temp_root = root / ".breadboard" / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        temp_root.chmod(0o700)
    except OSError:
        pass

    child_environment = dict(environment)
    child_environment.update(
        {
            "HOME": str(root),
            "TMPDIR": str(temp_root),
            "TMP": str(temp_root),
            "TEMP": str(temp_root),
        }
    )
    target = _command_argv(command, shell=shell)
    system = platform.system()
    if system == "Darwin":
        if initial_provider_credential_keys():
            raise ProcessIsolationUnavailable(
                "macOS model process isolation requires provider credentials outside the startup environment"
            )
        sandbox_exec = Path("/usr/bin/sandbox-exec")
        if not sandbox_exec.is_file() or not os.access(sandbox_exec, os.X_OK):
            raise ProcessIsolationUnavailable("macOS process isolation is unavailable")
        read_roots = _darwin_read_roots(
            root,
            environment,
            protected,
            target,
        )
        if any(
            _paths_overlap(protected_path, read_root)
            for protected_path in protected
            for read_root in read_roots
        ):
            raise ProcessIsolationUnavailable(
                "protected credential location overlaps a macOS read root"
            )
        return (
            (
                str(sandbox_exec),
                "-p",
                _darwin_profile(root, protected, read_roots),
                "--",
                *target,
            ),
            child_environment,
        )
    if system == "Linux":
        read_roots = _linux_read_roots(
            root,
            environment,
            protected,
            target,
        )
        if any(
            _paths_overlap(protected_path, read_root)
            for protected_path in protected
            for read_root in read_roots
        ):
            raise ProcessIsolationUnavailable(
                "protected credential location overlaps a Linux read root"
            )
        helper = Path(__file__).resolve(strict=True)
        interpreter = _resolved_existing(sys.executable)
        if (
            not helper.is_file()
            or interpreter is None
            or not interpreter.is_file()
            or _paths_overlap(helper, root)
            or _paths_overlap(interpreter, root)
            or any(_paths_overlap(helper, path) for path in protected)
            or any(_paths_overlap(interpreter, path) for path in protected)
        ):
            raise ProcessIsolationUnavailable(
                "trusted Linux isolation helper is unavailable"
            )
        wrapper: list[str] = [
            sys.executable,
            "-I",
            str(helper),
            "--workspace",
            str(root),
            "--working-directory",
            str(cwd),
        ]
        for read_root in read_roots:
            wrapper.extend(("--read-root", str(read_root)))
        wrapper.extend(("--", *target))
        return tuple(wrapper), child_environment
    raise ProcessIsolationUnavailable(
        f"process isolation is unsupported on {system or 'this platform'}"
    )


_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2

_ACCESS_FS_EXECUTE = 1 << 0
_ACCESS_FS_WRITE_FILE = 1 << 1
_ACCESS_FS_READ_FILE = 1 << 2
_ACCESS_FS_READ_DIR = 1 << 3
_ACCESS_FS_REMOVE_DIR = 1 << 4
_ACCESS_FS_REMOVE_FILE = 1 << 5
_ACCESS_FS_MAKE_CHAR = 1 << 6
_ACCESS_FS_MAKE_DIR = 1 << 7
_ACCESS_FS_MAKE_REG = 1 << 8
_ACCESS_FS_MAKE_SOCK = 1 << 9
_ACCESS_FS_MAKE_FIFO = 1 << 10
_ACCESS_FS_MAKE_BLOCK = 1 << 11
_ACCESS_FS_MAKE_SYM = 1 << 12
_ACCESS_FS_REFER = 1 << 13
_ACCESS_FS_TRUNCATE = 1 << 14

_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446

_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_RET_K = 0x06
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_AUDIT_ARCH_X86_64 = 0xC000003E
_AUDIT_ARCH_AARCH64 = 0xC00000B7

_DENIED_SYSCALLS: dict[str, tuple[int, tuple[int, ...]]] = {
    "x86_64": (
        _AUDIT_ARCH_X86_64,
        (41, 42, 101, 298, 310, 311, 312, 321, 425, 438),
    ),
    "aarch64": (
        _AUDIT_ARCH_AARCH64,
        (117, 198, 203, 241, 270, 271, 272, 280, 425, 438),
    ),
    "arm64": (
        _AUDIT_ARCH_AARCH64,
        (117, 198, 203, 241, 270, 271, 272, 280, 425, 438),
    ),
}


class _SockFilter(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    )


class _SockFprog(ctypes.Structure):
    _fields_ = (
        ("length", ctypes.c_ushort),
        ("filters", ctypes.POINTER(_SockFilter)),
    )


def _syscall(libc: ctypes.CDLL, number: int, *args: object) -> int:
    result = int(libc.syscall(ctypes.c_long(number), *args))
    if result >= 0:
        return result
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number))


def _set_no_new_privileges(libc: ctypes.CDLL) -> None:
    if (
        libc.prctl(
            ctypes.c_int(_PR_SET_NO_NEW_PRIVS),
            ctypes.c_ulong(1),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
        != 0
    ):
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _install_linux_seccomp(libc: ctypes.CDLL) -> None:
    architecture = _DENIED_SYSCALLS.get(platform.machine().lower())
    if architecture is None:
        raise ProcessIsolationUnavailable(
            "Linux process-memory isolation is unsupported on this architecture"
        )
    audit_arch, denied = architecture
    instructions: list[_SockFilter] = [
        _SockFilter(_BPF_LD_W_ABS, 0, 0, 4),
        _SockFilter(_BPF_JMP_JEQ_K, 1, 0, audit_arch),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
        _SockFilter(_BPF_LD_W_ABS, 0, 0, 0),
    ]
    for syscall_number in denied:
        instructions.extend(
            (
                _SockFilter(_BPF_JMP_JEQ_K, 0, 1, syscall_number),
                _SockFilter(
                    _BPF_RET_K,
                    0,
                    0,
                    _SECCOMP_RET_ERRNO | errno.EPERM,
                ),
            )
        )
    instructions.append(_SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW))
    array_type = _SockFilter * len(instructions)
    filters = array_type(*instructions)
    program = _SockFprog(len(instructions), filters)
    if (
        libc.prctl(
            ctypes.c_int(_PR_SET_SECCOMP),
            ctypes.c_ulong(_SECCOMP_MODE_FILTER),
            ctypes.byref(program),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
        != 0
    ):
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _landlock_access_mask(abi: int) -> int:
    mask = (
        _ACCESS_FS_EXECUTE
        | _ACCESS_FS_WRITE_FILE
        | _ACCESS_FS_READ_FILE
        | _ACCESS_FS_READ_DIR
        | _ACCESS_FS_REMOVE_DIR
        | _ACCESS_FS_REMOVE_FILE
        | _ACCESS_FS_MAKE_CHAR
        | _ACCESS_FS_MAKE_DIR
        | _ACCESS_FS_MAKE_REG
        | _ACCESS_FS_MAKE_SOCK
        | _ACCESS_FS_MAKE_FIFO
        | _ACCESS_FS_MAKE_BLOCK
        | _ACCESS_FS_MAKE_SYM
    )
    if abi >= 2:
        mask |= _ACCESS_FS_REFER
    if abi >= 3:
        mask |= _ACCESS_FS_TRUNCATE
    return mask


def _landlock_add_path_rule(
    libc: ctypes.CDLL,
    ruleset_fd: int,
    path: Path,
    access: int,
) -> None:
    flags = getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0)
    path_fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(path_fd)
        if stat.S_ISDIR(metadata.st_mode):
            admitted_access = access
        elif stat.S_ISREG(metadata.st_mode):
            admitted_access = access & ~_ACCESS_FS_READ_DIR
        elif stat.S_ISCHR(metadata.st_mode):
            admitted_access = access & ~(_ACCESS_FS_READ_DIR | _ACCESS_FS_EXECUTE)
        else:
            raise OSError("Landlock root is an unsupported inode")
        attribute = struct.pack("=Qi", admitted_access, path_fd)
        buffer = ctypes.create_string_buffer(attribute, len(attribute))
        _syscall(
            libc,
            _LANDLOCK_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(buffer),
            ctypes.c_uint(0),
        )
    finally:
        os.close(path_fd)


def _apply_linux_landlock(workspace: Path, read_roots: Sequence[Path]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        abi = _syscall(
            libc,
            _LANDLOCK_CREATE_RULESET,
            ctypes.c_void_p(),
            ctypes.c_size_t(0),
            ctypes.c_uint(_LANDLOCK_CREATE_RULESET_VERSION),
        )
    except OSError as exc:
        raise ProcessIsolationUnavailable(
            "Linux Landlock filesystem isolation is unavailable"
        ) from exc
    if abi < 3:
        raise ProcessIsolationUnavailable(
            "Linux Landlock ABI v3 or newer is required for truncate isolation"
        )

    handled = _landlock_access_mask(abi)
    ruleset_attribute = ctypes.c_uint64(handled)
    try:
        ruleset_fd = _syscall(
            libc,
            _LANDLOCK_CREATE_RULESET,
            ctypes.byref(ruleset_attribute),
            ctypes.sizeof(ruleset_attribute),
            ctypes.c_uint(0),
        )
    except OSError as exc:
        raise ProcessIsolationUnavailable(
            "Linux Landlock ruleset creation failed"
        ) from exc

    read_access = handled & (
        _ACCESS_FS_EXECUTE | _ACCESS_FS_READ_FILE | _ACCESS_FS_READ_DIR
    )
    device_access = read_access | _ACCESS_FS_WRITE_FILE
    try:
        for path in read_roots:
            _landlock_add_path_rule(libc, ruleset_fd, path, read_access)
        for raw_device in (
            "/dev/null",
            "/dev/zero",
            "/dev/random",
            "/dev/urandom",
            "/dev/tty",
        ):
            device = _resolved_existing(raw_device)
            if device is not None:
                _landlock_add_path_rule(libc, ruleset_fd, device, device_access)
        _landlock_add_path_rule(libc, ruleset_fd, workspace, handled)
        _syscall(
            libc,
            _LANDLOCK_RESTRICT_SELF,
            ctypes.c_int(ruleset_fd),
            ctypes.c_uint(0),
        )
    except OSError as exc:
        raise ProcessIsolationUnavailable(
            "Linux Landlock policy installation failed"
        ) from exc
    finally:
        os.close(ruleset_fd)


def _parse_linux_launch(
    argv: Sequence[str],
) -> tuple[Path, Path, tuple[Path, ...], tuple[str, ...]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--working-directory")
    parser.add_argument("--read-root", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    namespace = parser.parse_args(list(argv))
    command = tuple(namespace.command)
    if command[:1] == ("--",):
        command = command[1:]
    if not command:
        raise ProcessIsolationUnavailable("process command is empty")
    workspace = _validate_workspace(namespace.workspace, ())
    working_directory = _validate_working_directory(
        namespace.working_directory,
        workspace,
    )
    read_roots: list[Path] = []
    for raw in namespace.read_root:
        root = _resolved_existing(raw)
        if root is None or not (root.is_dir() or root.is_file()):
            raise ProcessIsolationUnavailable("Linux read root is unavailable")
        if _paths_overlap(root, workspace):
            raise ProcessIsolationUnavailable("Linux read root overlaps workspace")
        if _under_virtual_read_mount(root):
            raise ProcessIsolationUnavailable(
                "Linux read root exposes a virtual system mount"
            )
        read_roots.append(root)
    return workspace, working_directory, tuple(dict.fromkeys(read_roots)), command


def _parse_args(argv: Sequence[str]) -> tuple[Path, tuple[str, ...]]:
    """Compatibility parser used by focused policy tests."""
    workspace, _working_directory, _read_roots, command = _parse_linux_launch(argv)
    return workspace, command


def main(argv: Sequence[str] | None = None) -> int:
    try:
        workspace, working_directory, read_roots, command = _parse_linux_launch(
            sys.argv[1:] if argv is None else argv
        )
        libc = ctypes.CDLL(None, use_errno=True)
        _set_no_new_privileges(libc)
        _install_linux_seccomp(libc)
        _apply_linux_landlock(workspace, read_roots)
        os.chdir(working_directory)
        os.execvpe(command[0], command, os.environ)
    except ProcessIsolationUnavailable as exc:
        print(f"process isolation unavailable: {exc}", file=sys.stderr)
        return 126
    except OSError as exc:
        print(
            f"isolated process launch failed ({exc.__class__.__name__})",
            file=sys.stderr,
        )
        return 126
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
