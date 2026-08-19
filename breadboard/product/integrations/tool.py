"""Tool executor adapter for the frozen integration catalog port."""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from .catalog import IntegrationDescriptor, ProbeReport, probe_for


@runtime_checkable
class ToolPort(Protocol):
    def execute(self, arguments: Mapping[str, Any]) -> Any: ...


class ToolIntegrationAdapter:
    """Adapt a callable or object with execute() into a catalog integration."""

    def __init__(
        self,
        tool_id: str,
        executor: ToolPort | Callable[[Mapping[str, Any]], Any],
        *,
        implementation_id: str | None = None,
        capabilities: Iterable[str] = ("execute",),
        effects: Iterable[str] = ("tool.execute",),
        permissions: Iterable[str] = ("tool.invoke",),
    ) -> None:
        if not tool_id or not (callable(executor) or callable(getattr(executor, "execute", None))):
            raise TypeError("tool adapter requires an executable tool")
        self.tool_id = tool_id
        self.executor = executor
        self.descriptor = IntegrationDescriptor(
            "bb.integration_descriptor.v1",
            "tool:" + tool_id,
            "tool_executor",
            "tool-port.v1",
            implementation_id or type(executor).__name__,
            tuple(sorted(set(capabilities))),
            effects=tuple(sorted(set(effects))),
            permissions=tuple(sorted(set(permissions))),
        )

    def execute(self, arguments: Mapping[str, Any]) -> Any:
        executor = self.executor
        method = getattr(executor, "execute", None)
        return method(arguments) if callable(method) else executor(arguments)  # type: ignore[operator]

    def probe(self) -> ProbeReport:
        return probe_for(self.descriptor)
