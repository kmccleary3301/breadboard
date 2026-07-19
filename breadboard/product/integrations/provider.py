"""Provider runtime adapter for the frozen integration catalog port."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agentic_coder_prototype.provider.routing import ProviderDescriptor
from agentic_coder_prototype.provider.runtime import ProviderResult, ProviderRuntime, ProviderRuntimeContext

from .catalog import IntegrationDescriptor, ProbeReport, probe_for


@runtime_checkable
class ProviderPort(Protocol):
    provider_id: str
    runtime_id: str

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult: ...


class ProviderRuntimeAdapter:
    """Expose an existing ProviderRuntime without changing provider registries."""

    def __init__(self, runtime: ProviderRuntime, descriptor: ProviderDescriptor | None = None) -> None:
        if not callable(getattr(runtime, "invoke", None)) or not callable(getattr(runtime, "create_client", None)):
            raise TypeError("runtime must implement the frozen provider port")
        self.runtime = runtime
        self.port_descriptor = descriptor or getattr(runtime, "descriptor", None)
        if not isinstance(self.port_descriptor, ProviderDescriptor):
            raise TypeError("runtime must expose a ProviderDescriptor")
        self.descriptor = IntegrationDescriptor(
            "bb.integration_descriptor.v1",
            "provider:" + self.port_descriptor.provider_id,
            "provider_adapter",
            "provider-runtime.v1",
            self.port_descriptor.runtime_id,
            _capabilities(self.port_descriptor),
            secret_reference_names=(self.port_descriptor.api_key_env,),
            effects=("network",),
            permissions=("provider.invoke",),
        )

    @property
    def provider_id(self) -> str:
        return self.port_descriptor.provider_id

    @property
    def runtime_id(self) -> str:
        return self.port_descriptor.runtime_id

    def create_client(self, api_key: str, *, base_url: str | None = None, default_headers: Mapping[str, str] | None = None) -> Any:
        return self.runtime.create_client(api_key, base_url=base_url, default_headers=dict(default_headers or {}))

    def invoke(self, **kwargs: Any) -> ProviderResult:
        return self.runtime.invoke(**kwargs)

    def probe(self) -> ProbeReport:
        return probe_for(self.descriptor)


def _capabilities(descriptor: ProviderDescriptor) -> tuple[str, ...]:
    values: list[str] = []
    if descriptor.supports_native_tools:
        values.append("native_tools")
    if descriptor.supports_streaming:
        values.append("streaming")
    if descriptor.supports_reasoning_traces:
        values.append("reasoning_traces")
    if descriptor.supports_cache_control:
        values.append("cache_control")
    return tuple(values)


def provider_adapter_from_runtime(runtime: ProviderRuntime) -> ProviderRuntimeAdapter:
    """Adapt a runtime instance created by the existing provider registry."""
    return ProviderRuntimeAdapter(runtime)
