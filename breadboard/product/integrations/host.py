"""Host/sandbox adapter for the frozen integration catalog port."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from threading import Event
from typing import Any, Protocol, runtime_checkable

from .catalog import IntegrationDescriptor, ProbeReport, probe_for


@runtime_checkable
class HostPort(Protocol):
    def get_workspace(self) -> str: ...
    def execute(self, command: str | Sequence[str], **kwargs: Any) -> Any: ...


class SandboxHostAdapter:
    """Adapt an existing sandbox actor/proxy without editing its factory registry."""

    def __init__(
        self,
        host_id: str,
        sandbox: HostPort,
        *,
        implementation_id: str | None = None,
        capabilities: Iterable[str] = ("workspace", "execute"),
        effects: Iterable[str] = ("filesystem", "process"),
        permissions: Iterable[str] = ("host.execute",),
    ) -> None:
        if (
            not host_id
            or not callable(getattr(sandbox, "get_workspace", None))
            or not callable(getattr(sandbox, "execute", None))
        ):
            raise TypeError(
                "host adapter requires a sandbox with get_workspace() and execute()"
            )
        self.host_id = host_id
        self.sandbox = sandbox
        self.descriptor = IntegrationDescriptor(
            "bb.integration_descriptor.v1",
            "host:" + host_id,
            "host_driver",
            "host-port.v1",
            implementation_id or type(sandbox).__name__,
            tuple(sorted(set(capabilities))),
            effects=tuple(sorted(set(effects))),
            permissions=tuple(sorted(set(permissions))),
        )

    def workspace(self) -> str:
        workspace = self.sandbox.get_workspace()
        if not isinstance(workspace, str) or not workspace:
            raise RuntimeError("sandbox returned no workspace identity")
        return workspace

    def execute(self, command: str | Sequence[str], **kwargs: Any) -> Any:
        method = getattr(self.sandbox, "execute", None)
        if not callable(method):
            raise TypeError("sandbox does not expose the frozen execute port")
        return method(command, **kwargs)

    def execute_isolated(
        self,
        argv: Sequence[str],
        *,
        stdin_data: bytes,
        timeout_seconds: float,
        environment: Mapping[str, str],
        cancelled: Event | None = None,
        cancellation_grace_seconds: float = 1.0,
    ) -> Mapping[str, Any]:
        command = tuple(argv)
        if not command or any(
            not isinstance(value, str) or not value for value in command
        ):
            raise ValueError("isolated sandbox command requires explicit argv")
        if timeout_seconds <= 0 or cancellation_grace_seconds <= 0:
            raise ValueError("isolated sandbox deadlines must be positive")
        result = self.execute(
            command,
            stdin_data=stdin_data,
            timeout=timeout_seconds,
            env=dict(environment),
            inherit_env=False,
            close_fds=True,
            shell=False,
            cwd=self.workspace(),
            network="none",
            start_new_session=True,
            cancelled=cancelled,
            cancellation_grace_seconds=cancellation_grace_seconds,
        )
        if not isinstance(result, Mapping):
            raise TypeError("isolated sandbox returned no result envelope")
        if result.get("orphaned") is not False:
            raise RuntimeError("isolated sandbox did not prove child cleanup")
        return result

    def probe(self) -> ProbeReport:
        try:
            self.workspace()
        except Exception as exc:  # noqa: BLE001 - probes normalize adapter failures by type.
            return probe_for(self.descriptor, error=type(exc).__name__)
        return probe_for(self.descriptor)
