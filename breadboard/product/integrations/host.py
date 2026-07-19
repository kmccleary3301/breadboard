"""Host/sandbox adapter for the frozen integration catalog port."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from .catalog import IntegrationDescriptor, ProbeReport, probe_for


@runtime_checkable
class HostPort(Protocol):
    def get_workspace(self) -> str: ...
    def execute(self, command: str, **kwargs: Any) -> Any: ...


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
        if not host_id or not callable(getattr(sandbox, "get_workspace", None)) or not callable(getattr(sandbox, "execute", None)):
            raise TypeError("host adapter requires a sandbox with get_workspace() and execute()")
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

    def execute(self, command: str, **kwargs: Any) -> Any:
        method = getattr(self.sandbox, "execute", None)
        if not callable(method):
            raise RuntimeError("sandbox does not expose the frozen execute port")
        return method(command, **kwargs)

    def probe(self) -> ProbeReport:
        try:
            self.workspace()
        except Exception as exc:
            return probe_for(self.descriptor, error=type(exc).__name__)
        return probe_for(self.descriptor)
