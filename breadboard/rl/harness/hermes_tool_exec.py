"""Hermes-only Linux shell authority, installed beside the native worker.

The supplier still creates its LocalEnvironment Bash processes. Its PATH lookup
selects the measured tool-exec/bash entrypoint, which calls this file before
execing the image's Bash. Restrictions belong to the child, not reusable native
executor threads; source skill-usage and spill commits remain control-owned.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import sys

_AUTHORITY_ENV = "E4_HERMES_SHELL_AUTHORITY"
_AUTHORITY_HASH_ENV = "E4_HERMES_SHELL_AUTHORITY_SHA256"
_SCHEMA = "bb.hermes-shell-authority.v1"
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
_EXECUTE = 1
_WRITE_FILE = 1 << 1
_TRUNCATE = 1 << 14
_WRITE_TREE = _WRITE_FILE | sum(1 << bit for bit in range(4, 15))
_HANDLED_FS = _EXECUTE | _READ_FILE | _READ_DIR | _WRITE_TREE
_READ_TREE = _EXECUTE | _READ_FILE | _READ_DIR
_MS_RDONLY = 1
_MS_NOSUID = 2
_MS_NODEV = 4
_MS_NOEXEC = 8
_MS_REMOUNT = 32
_MS_BIND = 4096
_MS_REC = 16384
_MS_PRIVATE = 1 << 18
_CLONE_NEWNS = 0x00020000
_CLONE_NEWNET = 0x40000000
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_DUMPABLE = 4
_PR_GET_DUMPABLE = 3
_PR_CAPBSET_READ = 23
_PR_CAPBSET_DROP = 24
_FIXTURE_PATHS = (
    "memories/MEMORY.md",
    "memories/USER.md",
    "skills/fixture-code-style/SKILL.md",
    "skills/fixture-code-style/references/assertions.md",
)
_STARTUP_FILES = (".bash_profile", ".bash_login", ".profile", ".bashrc")
_RUNTIME_ENVIRONMENT = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "TZ",
    "PYTHONHASHSEED",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
_IMAGE_READ_PATHS = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/etc",
    "/opt/conda",
    "/opt/miniconda3",
    "/opt/venv",
    "/dev/null",
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/proc/self",
    "/proc/thread-self",
    "/proc/cpuinfo",
    "/proc/meminfo",
    "/proc/stat",
    "/proc/uptime",
    "/proc/version",
    "/proc/sys/kernel/osrelease",
    "/sys/devices/system/cpu",
    "/sys/fs/cgroup",
)


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def _libc():
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Hermes shell authority requires admitted Linux x86_64")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.unshare.argtypes = [ctypes.c_int]
    libc.mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    return libc


def _checked(result: int, operation: str) -> int:
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, f"{operation}: {os.strerror(error)}")
    return result


def _landlock_abi(libc) -> int:
    abi = _checked(libc.syscall(444, 0, 0, 1), "landlock ABI query")
    if abi < 3:
        raise RuntimeError(
            "Hermes requires Landlock ABI >=3 for truncate and rename authority"
        )
    return abi


def _readonly_bind(libc, path: Path) -> None:
    raw = os.fsencode(path)
    _checked(libc.mount(raw, raw, None, _MS_BIND, None), f"bind {path}")
    _checked(
        libc.mount(None, raw, None, _MS_BIND | _MS_REMOUNT | _MS_RDONLY, None),
        f"seal {path}",
    )
    if not os.statvfs(path).f_flag & os.ST_RDONLY:
        raise RuntimeError(f"read-only bind was not observed: {path}")


def _owned_directory(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError(f"noncanonical Hermes authority directory: {path}")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError(f"unowned Hermes authority directory: {path}")
    return path


def _require_disjoint_roots(workspace: Path, scratch: Path) -> None:
    # Canonical roots; is_relative_to is reflexive, so equal roots also fail.
    if workspace.is_relative_to(scratch) or scratch.is_relative_to(workspace):
        raise ValueError("Hermes workspace and scratch must be disjoint")


def configure_shell_boundary(workspace: Path, scratch: Path, hermes_home: Path) -> dict:
    """Seal admitted inputs before supplier imports or native shell bootstrap."""
    libc = _libc()
    if os.getpid() != 1:
        raise RuntimeError("Hermes requires the native worker's PID namespace init")
    runtime_environment = {
        name: os.environ[name] for name in _RUNTIME_ENVIRONMENT if name in os.environ
    }
    os.environ.clear()
    os.environ.update(runtime_environment)
    abi = _landlock_abi(libc)
    workspace = _owned_directory(workspace)
    scratch = _owned_directory(scratch)
    hermes_home = _owned_directory(hermes_home)
    _require_disjoint_roots(workspace, scratch)
    if hermes_home != scratch / "hermes-home":
        raise ValueError("Hermes home must be the admitted scratch/hermes-home")
    native_root = Path(__file__).resolve().parent
    for member in ("hermes-native-config.json", "native_worker.py", "tool-exec/bash"):
        if not (native_root / member).is_file():
            raise RuntimeError(f"incomplete installed Hermes closure: {member}")
    shell_home = scratch / "shell-home"
    terminal_temp = scratch / "terminal"
    sealed_startup = scratch / "sealed-startup"
    for directory in (shell_home, terminal_temp, sealed_startup):
        directory.mkdir(mode=0o700)
    for name in _STARTUP_FILES:
        (shell_home / name).write_bytes(b"")
    empty_system_startup = sealed_startup / "empty"
    empty_system_startup.write_bytes(b"")
    os.chmod(empty_system_startup, 0o444)

    # Mount propagation must be private before any per-episode bind is changed.
    namespaces_before = {
        name: os.readlink(f"/proc/self/ns/{name}") for name in ("mnt", "net")
    }
    _checked(libc.unshare(_CLONE_NEWNS | _CLONE_NEWNET), "Hermes mount/network unshare")
    _checked(
        libc.mount(None, b"/", None, _MS_REC | _MS_PRIVATE, None),
        "private mount propagation",
    )
    namespaces_after = {
        name: os.readlink(f"/proc/self/ns/{name}") for name in ("mnt", "net")
    }
    if any(
        namespaces_before[name] == namespaces_after[name] for name in namespaces_before
    ):
        raise RuntimeError("Hermes namespace isolation was not observed")
    # CLONE_NEWPID changes syscall PIDs, not an inherited procfs mount.
    # Native psutil and LocalEnvironment must observe the same PID domain.
    _checked(
        libc.mount(
            b"proc", b"/proc", b"proc", _MS_NOSUID | _MS_NODEV | _MS_NOEXEC, None
        ),
        "mount native PID-domain procfs",
    )
    if os.readlink("/proc/self") != str(os.getpid()):
        raise RuntimeError("Hermes procfs does not match the native PID namespace")
    _checked(libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0), "protect native actor process")
    if (
        _checked(libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0), "read actor dumpability")
        != 0
    ):
        raise RuntimeError("Hermes actor process remains dumpable")

    sealed_files = [hermes_home / name for name in (*_FIXTURE_PATHS, "config.yaml")]
    sealed_files.extend(shell_home / name for name in _STARTUP_FILES)
    sealed_facts = []
    for path in sealed_files:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or path.resolve(strict=True) != path
        ):
            raise ValueError(f"unowned or aliased Hermes operator file: {path}")
        content = path.read_bytes()
        os.chmod(path, 0o444)
        _readonly_bind(libc, path)
        sealed_facts.append(
            {
                "path": str(path),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mode": 0o444,
            }
        )
    for destination in (Path("/etc/profile"), Path("/etc/bash.bashrc")):
        if destination.exists():
            _checked(
                libc.mount(
                    os.fsencode(empty_system_startup),
                    os.fsencode(destination),
                    None,
                    _MS_BIND,
                    None,
                ),
                f"seal startup {destination}",
            )
            _checked(
                libc.mount(
                    None,
                    os.fsencode(destination),
                    None,
                    _MS_BIND | _MS_REMOUNT | _MS_RDONLY,
                    None,
                ),
                f"readonly startup {destination}",
            )
            if (
                destination.read_bytes()
                or not os.statvfs(destination).f_flag & os.ST_RDONLY
            ):
                raise RuntimeError(
                    f"empty read-only startup not observed: {destination}"
                )
            sealed_facts.append(
                {
                    "path": str(destination),
                    "bytes": 0,
                    "sha256": hashlib.sha256(b"").hexdigest(),
                    "mode": 0o444,
                }
            )
    _readonly_bind(libc, native_root)

    authority_path = scratch / "shell-authority.json"
    # Keep /proc/self literal: each executing child must bind its own proc view,
    # never the actor's PID resolved while constructing this authority.
    read_paths = [path for path in _IMAGE_READ_PATHS if Path(path).exists()]
    read_paths.extend(map(str, (native_root, hermes_home, authority_path)))
    policy = {
        "schema_version": _SCHEMA,
        "landlock_abi": abi,
        "read_paths": read_paths,
        "write_roots": list(map(str, (workspace, shell_home, terminal_temp))),
        "readonly_paths": [str(native_root), *(fact["path"] for fact in sealed_facts)],
        "network_namespace": namespaces_after["net"],
    }
    raw = (json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(
        authority_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    digest = hashlib.sha256(raw).hexdigest()
    os.environ.update(
        {
            _AUTHORITY_ENV: str(authority_path),
            _AUTHORITY_HASH_ENV: digest,
            "HERMES_HOME": str(hermes_home),
            "HOME": str(shell_home),
            "TERMINAL_TEMP_DIR": str(terminal_temp),
            "TMPDIR": str(terminal_temp),
            "PATH": str(native_root / "tool-exec")
            + os.pathsep
            + os.environ.get("PATH", os.defpath),
            "SHELL": "/bin/bash",
        }
    )
    return {
        "schema_version": _SCHEMA,
        "landlock_abi": abi,
        "authority_sha256": digest,
        "authority_path": str(authority_path),
        "namespaces_before": namespaces_before,
        "namespaces_after": namespaces_after,
        "pid_namespace": os.readlink("/proc/self/ns/pid"),
        "proc_self_pid": int(os.readlink("/proc/self")),
        "actor_dumpable": False,
        "read_paths": read_paths,
        "write_roots": policy["write_roots"],
        "sealed_files": sealed_facts,
        "native_root_readonly": True,
        "bash_entrypoint": str(native_root / "tool-exec/bash"),
        "child_enforcement": "landlock-filesystem/capabilities-empty/no-new-privileges",
    }


def _read_authority() -> dict:
    path = os.environ[_AUTHORITY_ENV]
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size > 16384
        ):
            raise RuntimeError("invalid Hermes shell authority custody")
        raw = stream.read(16385)
    if hashlib.sha256(raw).hexdigest() != os.environ[_AUTHORITY_HASH_ENV]:
        raise RuntimeError("Hermes shell authority digest mismatch")
    policy = json.loads(raw)
    if policy["schema_version"] != _SCHEMA or policy["landlock_abi"] < 3:
        raise RuntimeError("unsupported Hermes shell authority")
    if os.readlink("/proc/self/ns/net") != policy["network_namespace"]:
        raise RuntimeError("Hermes shell escaped its admitted network namespace")
    for path in policy["readonly_paths"]:
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise RuntimeError(f"Hermes sealed input became writable: {path}")
    return policy


def _restrict_filesystem(libc, policy: dict) -> None:
    ruleset = _Ruleset(_HANDLED_FS)
    rules_fd = _checked(
        libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0),
        "create Landlock ruleset",
    )
    try:
        grants = [(path, _READ_TREE) for path in policy["read_paths"]]
        grants.extend((path, _HANDLED_FS) for path in policy["write_roots"])
        grants.append(("/dev/null", _READ_FILE | _WRITE_FILE | _TRUNCATE))
        for path, access in grants:
            path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                if not stat.S_ISDIR(os.fstat(path_fd).st_mode):
                    access &= _EXECUTE | _READ_FILE | _WRITE_FILE | _TRUNCATE
                rule = _PathRule(access, path_fd)
                _checked(
                    libc.syscall(445, rules_fd, 1, ctypes.byref(rule), 0),
                    f"add Landlock path {path}",
                )
            finally:
                os.close(path_fd)
        _checked(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no new privileges")
        _checked(libc.syscall(446, rules_fd, 0), "enforce Landlock ruleset")
    finally:
        os.close(rules_fd)


def _drop_capabilities(libc) -> None:
    # Nested Bash invocations inherit an empty bounding set. Read before drop so
    # they need no CAP_SETPCAP and cannot manufacture a privilege-restoring path.
    for capability in range(64):
        present = libc.prctl(_PR_CAPBSET_READ, capability, 0, 0, 0)
        if present < 0:
            if ctypes.get_errno() == 22:  # Beyond this kernel's last capability.
                break
            _checked(present, "read capability bounding set")
        if present:
            _checked(
                libc.prctl(_PR_CAPBSET_DROP, capability, 0, 0, 0),
                f"drop capability {capability}",
            )
    header = _CapHeader(0x20080522, 0)
    data = (_CapData * 2)()
    _checked(
        libc.capset(ctypes.byref(header), ctypes.byref(data)),
        "clear process capabilities",
    )
    _checked(
        libc.capget(ctypes.byref(header), ctypes.byref(data)),
        "observe process capabilities",
    )
    if any(row.effective or row.permitted or row.inheritable for row in data):
        raise RuntimeError("Hermes shell retained process capabilities")


def _exec_bash(arguments: list[str]) -> None:
    libc = _libc()
    policy = _read_authority()
    _restrict_filesystem(libc, policy)
    _drop_capabilities(libc)
    os.execv("/bin/bash", ["/bin/bash", *arguments])


if __name__ == "__main__":
    try:
        _exec_bash(sys.argv[1:])
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"Hermes shell boundary: {error}", file=sys.stderr)
        raise SystemExit(126)
