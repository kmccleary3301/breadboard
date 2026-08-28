"""Platform process launch policy and command construction."""

from __future__ import annotations

import os
import platform
import shlex
import sys
from pathlib import Path
from typing import Mapping, Sequence

from .child_environment import (
    initial_provider_credential_keys,
    is_loader_environment_key,
    purge_provider_credentials,
)
from .credential_boundary import (
    _normalized_path,
    _paths_overlap,
    _resolved_existing,
    _validate_hardlink_boundary,
    _validate_workspace,
    _validate_working_directory,
    protected_credential_paths,
)
from .isolation_errors import ProcessIsolationUnavailable
from .path_policy import (
    prepare_workspace_temp_directory,
    under_virtual_read_mount,
)

_TRUSTED_CREDENTIAL_ENV_KEYS = frozenset({"OPENAI_API_KEY", "CODEX_AUTH_TOKEN"})


def _validate_trusted_credential_values(
    values: Mapping[str, object] | None,
) -> dict[str, str]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ProcessIsolationUnavailable("trusted credential values must be a mapping")
    validated: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key)
        if key not in _TRUSTED_CREDENTIAL_ENV_KEYS:
            raise ProcessIsolationUnavailable(
                "trusted credential environment key is not permitted"
            )
        if (
            not isinstance(raw_value, str)
            or not raw_value
            or "\x00" in raw_value
            or "\n" in raw_value
            or "\r" in raw_value
        ):
            raise ProcessIsolationUnavailable("trusted credential value is invalid")
        validated[key] = raw_value
    return validated


def _validate_provider_credential_read_roots(
    roots: Sequence[str | os.PathLike[str]] | str | os.PathLike[str] | None,
    *,
    workspace: Path,
) -> tuple[Path, ...]:
    if roots is None:
        return ()
    candidates = (roots,) if isinstance(roots, (str, os.PathLike)) else tuple(roots)
    validated: dict[str, Path] = {}
    for raw in candidates:
        try:
            resolved = _resolved_existing(raw)
        except (TypeError, ValueError):
            resolved = None
        if resolved is None or not resolved.is_dir() or resolved == Path("/"):
            raise ProcessIsolationUnavailable(
                "provider credential read root is unavailable"
            )
        if _paths_overlap(resolved, workspace):
            raise ProcessIsolationUnavailable(
                "provider credential read root overlaps the process workspace"
            )
        validated[str(resolved)] = resolved
    return tuple(validated.values())


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
        if resolved is None or resolved == home or under_virtual_read_mount(resolved):
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
            or under_virtual_read_mount(resolved_candidate)
            or any(
                _paths_overlap(resolved_candidate, protected)
                for protected in protected_paths
            )
        ):
            continue
        roots[str(candidate)] = candidate
    return tuple(roots.values())


def _resolve_executable_path(
    value: str | os.PathLike[str],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
) -> tuple[Path, Path | None]:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        lexical = Path(
            os.path.abspath(
                candidate if candidate.is_absolute() else working_directory / candidate
            )
        )
    else:
        lexical = next(
            (
                Path(directory) / candidate
                for directory in os.get_exec_path(dict(environment))
                if _resolved_existing(Path(directory) / candidate) is not None
            ),
            working_directory / candidate,
        )
        lexical = Path(os.path.abspath(lexical))
    return lexical, _resolved_existing(lexical)


def _validated_trusted_launchers(
    launchers: Sequence[str | os.PathLike[str]],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
    protected_paths: Sequence[Path],
) -> tuple[Path, ...]:
    trusted: dict[str, Path] = {}
    for raw in launchers:
        lexical, resolved = _resolve_executable_path(
            raw,
            working_directory=working_directory,
            environment=environment,
        )
        if (
            resolved is None
            or not resolved.is_file()
            or not os.access(resolved, os.X_OK)
        ):
            raise ProcessIsolationUnavailable("trusted process launcher is unavailable")
        if any(
            _paths_overlap(candidate, protected)
            for candidate in (lexical, resolved)
            for protected in protected_paths
        ):
            raise ProcessIsolationUnavailable(
                "trusted process launcher overlaps a protected credential location"
            )
        trusted[str(lexical)] = resolved
    return tuple(trusted.values())


def _reject_untrusted_workspace_launcher(
    command: Sequence[str],
    *,
    workspace: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    trusted_launchers: Sequence[str | os.PathLike[str]],
) -> None:
    lexical, _resolved = _resolve_executable_path(
        command[0],
        working_directory=working_directory,
        environment=environment,
    )
    if not _paths_overlap(lexical, workspace):
        return
    trusted_lexical = {
        _resolve_executable_path(
            launcher,
            working_directory=working_directory,
            environment=environment,
        )[0]
        for launcher in trusted_launchers
    }
    if lexical not in trusted_lexical:
        raise ProcessIsolationUnavailable(
            "workspace executable is not an explicitly trusted launcher"
        )


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
    *,
    writable_roots: Sequence[Path] = (),
    trusted_launchers: Sequence[Path] = (),
    allow_network: bool = False,
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
    executable_paths = tuple(dict.fromkeys((*read_roots, *trusted_launchers)))
    exec_rules = " ".join(
        selector(path, "subpath" if path.is_dir() else "literal")
        for path in executable_paths
    )
    write_rules = " ".join(
        (
            selector(workspace, "subpath"),
            *(
                selector(path, "subpath" if path.is_dir() else "literal")
                for path in writable_roots
            ),
            '(literal "/dev/null")',
            '(literal "/dev/tty")',
        )
    )
    protected_rules: list[str] = []
    for path in protected_paths:
        selectors = " ".join((selector(path, "literal"), selector(path, "subpath")))
        protected_rules.extend(
            (f"(deny file-read* {selectors})", f"(deny file-write* {selectors})")
        )
    return "\n".join(
        (
            "(version 1)",
            "(deny default)",
            f"(allow process-exec {exec_rules})"
            if exec_rules
            else "(deny process-exec)",
            "(allow process-fork)",
            "(allow signal (target self))",
            '(allow sysctl-read (sysctl-name-regex #"^hw\\."))',
            '(allow sysctl-read (sysctl-name "kern.hostname"))',
            *(("(allow network*)",) if allow_network else ()),
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
    trusted_launchers: Sequence[str | os.PathLike[str]] = (),
    allow_network: bool = False,
    trusted_credential_values: Mapping[str, object] | None = None,
    provider_credential_read_roots: Sequence[str | os.PathLike[str]]
    | str
    | os.PathLike[str]
    | None = None,
    provider_credential_write_roots: Sequence[str | os.PathLike[str]]
    | str
    | os.PathLike[str]
    | None = None,
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
    provider_read_roots = _validate_provider_credential_read_roots(
        provider_credential_read_roots,
        workspace=root,
    )
    provider_write_roots = _validate_provider_credential_read_roots(
        provider_credential_write_roots,
        workspace=root,
    )
    authorized_provider_roots = tuple(
        dict.fromkeys((*provider_read_roots, *provider_write_roots))
    )
    if any(
        not any(
            provider_root == protected_path
            or provider_root.is_relative_to(protected_path)
            for protected_path in protected
        )
        for provider_root in authorized_provider_roots
    ):
        raise ProcessIsolationUnavailable(
            "provider credential root is not a protected credential location"
        )
    effective_protected = tuple(
        path
        for path in protected
        if not any(
            _paths_overlap(path, provider_root)
            for provider_root in authorized_provider_roots
        )
    )
    cwd = _validate_working_directory(working_directory, root)
    temp_root = prepare_workspace_temp_directory(root)

    child_environment = {
        str(key): str(value)
        for key, value in environment.items()
        if not is_loader_environment_key(key) and str(key) != "CODEX_HOME"
    }
    purge_provider_credentials(child_environment)
    child_environment.update(
        _validate_trusted_credential_values(trusted_credential_values)
    )
    codex_home = environment.get("CODEX_HOME")
    if codex_home and authorized_provider_roots:
        resolved_codex_home = _resolved_existing(codex_home)
        if resolved_codex_home in authorized_provider_roots:
            child_environment["CODEX_HOME"] = str(resolved_codex_home)
    child_environment.update(
        {
            "HOME": str(root),
            "TMPDIR": str(temp_root),
            "TMP": str(temp_root),
            "TEMP": str(temp_root),
        }
    )
    target = _command_argv(command, shell=shell)
    trusted_launcher_paths = _validated_trusted_launchers(
        trusted_launchers,
        working_directory=cwd,
        environment=environment,
        protected_paths=effective_protected,
    )
    _reject_untrusted_workspace_launcher(
        target,
        workspace=root,
        working_directory=cwd,
        environment=environment,
        trusted_launchers=trusted_launchers,
    )
    system = platform.system()
    if system == "Darwin":
        if initial_provider_credential_keys():
            raise ProcessIsolationUnavailable(
                "macOS model process isolation requires provider credentials outside the startup environment"
            )
        sandbox_exec = Path("/usr/bin/sandbox-exec")
        if not sandbox_exec.is_file() or not os.access(sandbox_exec, os.X_OK):
            raise ProcessIsolationUnavailable("macOS process isolation is unavailable")
        read_roots = (
            *_darwin_read_roots(
                root,
                environment,
                effective_protected,
                target,
            ),
            *authorized_provider_roots,
        )
        if any(
            _paths_overlap(protected_path, read_root)
            for protected_path in effective_protected
            for read_root in read_roots
        ):
            raise ProcessIsolationUnavailable(
                "protected credential location overlaps a macOS read root"
            )
        return (
            (
                str(sandbox_exec),
                "-p",
                _darwin_profile(
                    root,
                    effective_protected,
                    read_roots,
                    writable_roots=provider_write_roots,
                    trusted_launchers=trusted_launcher_paths,
                    allow_network=allow_network,
                ),
                "--",
                *target,
            ),
            child_environment,
        )
    if system == "Linux":
        read_roots = (
            *_linux_read_roots(
                root,
                environment,
                effective_protected,
                target,
            ),
            *authorized_provider_roots,
        )
        if any(
            _paths_overlap(protected_path, read_root)
            for protected_path in effective_protected
            for read_root in read_roots
        ):
            raise ProcessIsolationUnavailable(
                "protected credential location overlaps a Linux read root"
            )
        helper = Path(__file__).with_name("process_isolation.py").resolve(strict=True)
        interpreter = _resolved_existing(sys.executable)
        if (
            not helper.is_file()
            or interpreter is None
            or not interpreter.is_file()
            or _paths_overlap(helper, root)
            or _paths_overlap(interpreter, root)
            or any(_paths_overlap(helper, path) for path in effective_protected)
            or any(_paths_overlap(interpreter, path) for path in effective_protected)
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
        for write_root in provider_write_roots:
            wrapper.extend(("--write-root", str(write_root)))
        for launcher in trusted_launcher_paths:
            wrapper.extend(("--trusted-launcher", str(launcher)))
        if allow_network:
            wrapper.append("--allow-network")
        wrapper.extend(("--", *target))
        return tuple(wrapper), child_environment
    raise ProcessIsolationUnavailable(
        f"process isolation is unsupported on {system or 'this platform'}"
    )
