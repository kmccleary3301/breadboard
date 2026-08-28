"""Linux Landlock and seccomp child process implementation."""

from __future__ import annotations

import argparse
import ctypes
import errno
import os
import platform
import stat
import struct
import sys
from pathlib import Path
from typing import Sequence

from .credential_boundary import (
    _paths_overlap,
    _resolved_existing,
    _validate_workspace,
    _validate_working_directory,
)
from .isolation_errors import ProcessIsolationUnavailable
from .path_policy import under_virtual_read_mount

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


def _install_linux_seccomp(
    libc: ctypes.CDLL,
    *,
    allow_network: bool = False,
) -> None:
    machine = platform.machine().lower()
    architecture = _DENIED_SYSCALLS.get(machine)
    if architecture is None:
        raise ProcessIsolationUnavailable(
            "Linux process-memory isolation is unsupported on this architecture"
        )
    audit_arch, denied = architecture
    if allow_network:
        network = {"x86_64": {41, 42}, "aarch64": {198, 203}, "arm64": {198, 203}}[
            machine
        ]
        denied = tuple(number for number in denied if number not in network)
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
                _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
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


def _apply_linux_landlock(
    workspace: Path,
    read_roots: Sequence[Path],
    trusted_launchers: Sequence[Path] = (),
) -> None:
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
    workspace_access = handled & ~_ACCESS_FS_EXECUTE
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
        _landlock_add_path_rule(libc, ruleset_fd, workspace, workspace_access)
        for launcher in trusted_launchers:
            _landlock_add_path_rule(libc, ruleset_fd, launcher, _ACCESS_FS_EXECUTE)
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
) -> tuple[Path, Path, tuple[Path, ...], tuple[Path, ...], tuple[str, ...], bool]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--working-directory")
    parser.add_argument("--read-root", action="append", default=[])
    parser.add_argument("--trusted-launcher", action="append", default=[])
    parser.add_argument("--allow-network", action="store_true")
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
        if under_virtual_read_mount(root):
            raise ProcessIsolationUnavailable(
                "Linux read root exposes a virtual system mount"
            )
        read_roots.append(root)
    trusted_launchers: list[Path] = []
    for raw in namespace.trusted_launcher:
        launcher = _resolved_existing(raw)
        if (
            launcher is None
            or not launcher.is_file()
            or not os.access(launcher, os.X_OK)
        ):
            raise ProcessIsolationUnavailable("trusted process launcher is unavailable")
        trusted_launchers.append(launcher)
    return (
        workspace,
        working_directory,
        tuple(dict.fromkeys(read_roots)),
        tuple(dict.fromkeys(trusted_launchers)),
        command,
        bool(namespace.allow_network),
    )


def _parse_args(argv: Sequence[str]) -> tuple[Path, tuple[str, ...]]:
    """Compatibility parser used by focused policy tests."""
    workspace, _working_directory, _read_roots, _launchers, command, _network = (
        _parse_linux_launch(argv)
    )
    return workspace, command


def main(argv: Sequence[str] | None = None) -> int:
    try:
        (
            workspace,
            working_directory,
            read_roots,
            trusted_launchers,
            command,
            allow_network,
        ) = _parse_linux_launch(sys.argv[1:] if argv is None else argv)
        libc = ctypes.CDLL(None, use_errno=True)
        _set_no_new_privileges(libc)
        _install_linux_seccomp(libc, allow_network=allow_network)
        _apply_linux_landlock(workspace, read_roots, trusted_launchers)
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
