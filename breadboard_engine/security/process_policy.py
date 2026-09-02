"""Typed ownership for sanitized child process launch plans."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from .child_environment import build_child_environment
from .credential_boundary import _validate_workspace, _validate_working_directory
from .isolation_errors import ProcessIsolationUnavailable
from .launch_policy import _command_argv, build_restricted_process_command


EnvironmentValue = str | os.PathLike[str]


def _freeze_environment(environment: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({str(key): str(value) for key, value in environment.items()})


def _freeze_paths(
    values: Sequence[EnvironmentValue] | EnvironmentValue | None,
) -> tuple[EnvironmentValue, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, os.PathLike)):
        return (values,)
    return tuple(values)


@dataclass(frozen=True)
class ChildEnvironmentPlan:
    """Immutable, sanitized environment prepared for a child process."""

    environment: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", _freeze_environment(self.environment))

    def as_dict(self) -> dict[str, str]:
        """Return a subprocess-compatible mutable copy."""
        return dict(self.environment)


@dataclass(frozen=True)
class ChildProcessLaunchPlan:
    """Immutable argv/environment pair plus validated process directories."""

    command: tuple[str, ...]
    environment: Mapping[str, str]
    workspace: Path
    working_directory: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", tuple(str(value) for value in self.command))
        object.__setattr__(self, "environment", _freeze_environment(self.environment))
        object.__setattr__(self, "workspace", Path(self.workspace))
        object.__setattr__(self, "working_directory", Path(self.working_directory))

    @property
    def argv(self) -> tuple[str, ...]:
        """Expose the prepared command under the conventional subprocess name."""
        return self.command

    def environment_dict(self) -> dict[str, str]:
        """Return a subprocess-compatible mutable environment copy."""
        return dict(self.environment)


@dataclass(frozen=True)
class ChildProcessPolicy:
    """Immutable policy owning child environment and launch boundary decisions.

    ``environment_only`` is for runtimes that accept an environment projection
    (for example Ray). ``command_and_environment`` validates workspace
    containment and, by default, composes the platform isolation wrapper.
    """

    source_environment: Mapping[str, object] | None = None
    overrides: Mapping[str, object] | None = None
    allowed_override_keys: tuple[str, ...] = ()
    workspace: EnvironmentValue | None = None
    working_directory: EnvironmentValue | None = None
    shell: bool = False
    protected_paths: tuple[EnvironmentValue, ...] = ()
    trusted_launchers: tuple[EnvironmentValue, ...] = ()
    allow_network: bool = False
    trusted_credential_values: Mapping[str, object] | None = None
    provider_credential_read_roots: tuple[EnvironmentValue, ...] = ()
    provider_credential_write_roots: tuple[EnvironmentValue, ...] = ()
    isolate: bool = True

    def __post_init__(self) -> None:
        source = self.source_environment
        if source is not None:
            object.__setattr__(self, "source_environment", MappingProxyType(dict(source)))
        overrides = self.overrides
        if overrides is not None:
            object.__setattr__(self, "overrides", MappingProxyType(dict(overrides)))
        credentials = self.trusted_credential_values
        if credentials is not None:
            object.__setattr__(
                self,
                "trusted_credential_values",
                MappingProxyType(dict(credentials)),
            )
        object.__setattr__(
            self,
            "allowed_override_keys",
            tuple(str(key) for key in self.allowed_override_keys),
        )
        object.__setattr__(self, "protected_paths", _freeze_paths(self.protected_paths))
        object.__setattr__(self, "trusted_launchers", _freeze_paths(self.trusted_launchers))
        object.__setattr__(
            self,
            "provider_credential_read_roots",
            _freeze_paths(self.provider_credential_read_roots),
        )
        object.__setattr__(
            self,
            "provider_credential_write_roots",
            _freeze_paths(self.provider_credential_write_roots),
        )

    def environment_only(self) -> ChildEnvironmentPlan:
        """Build the allowlisted child environment without composing a command."""
        return ChildEnvironmentPlan(
            build_child_environment(
                source=self.source_environment,
                overrides=self.overrides,
                allowed_override_keys=self.allowed_override_keys,
            )
        )

    def command_and_environment(
        self,
        command: str | Sequence[str],
        *,
        environment: ChildEnvironmentPlan | None = None,
    ) -> ChildProcessLaunchPlan:
        """Build a validated command/environment launch plan.

        The optional environment plan lets callers preserve a separately
        validated environment-only result while keeping command construction
        owned by this policy.
        """
        if self.workspace is None:
            raise ProcessIsolationUnavailable(
                "process workspace is required for command launch"
            )
        root = _validate_workspace(self.workspace, ())
        cwd = _validate_working_directory(self.working_directory, root)
        child_environment = (
            environment if environment is not None else self.environment_only()
        )
        if self.isolate:
            argv, isolated_environment = build_restricted_process_command(
                command,
                workspace=root,
                working_directory=cwd,
                shell=self.shell,
                environment=child_environment.environment,
                protected_paths=self.protected_paths,
                trusted_launchers=self.trusted_launchers,
                allow_network=self.allow_network,
                trusted_credential_values=self.trusted_credential_values,
                provider_credential_read_roots=self.provider_credential_read_roots,
                provider_credential_write_roots=self.provider_credential_write_roots,
            )
        else:
            argv = _command_argv(command, shell=self.shell)
            isolated_environment = child_environment.environment
        return ChildProcessLaunchPlan(
            command=argv,
            environment=isolated_environment,
            workspace=root,
            working_directory=cwd,
        )


__all__ = [
    "ChildEnvironmentPlan",
    "ChildProcessLaunchPlan",
    "ChildProcessPolicy",
]
