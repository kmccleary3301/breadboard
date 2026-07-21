"""Provider runtime adapter for the frozen integration catalog port."""
from __future__ import annotations
import hashlib
import json

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


def _client_state_value(value: Any) -> Any:
    if value is None or type(value) in (bool, int, float, str):
        json.dumps(value, allow_nan=False)
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("provider client state mappings require plain string keys")
        return {key: {"type": f"{type(item).__module__}.{type(item).__qualname__}", "sha256": "sha256:" + hashlib.sha256(str(item).encode("utf-8")).hexdigest()} for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_client_state_value(item) for item in value]
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}", "value_sha256": "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()}


def _client_live_state(client: Any) -> str:
    state = {}
    for name in ("base_url", "timeout", "max_retries", "default_headers", "headers"):
        if isinstance(client, Mapping):
            if name not in client:
                continue
            value = client[name]
        else:
            value = getattr(client, name, None)
            if value is None:
                continue
        state[name] = _client_state_value(value)
    payload = json.dumps(state, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class ProviderRuntimeAdapter:
    """Expose an existing ProviderRuntime without changing provider registries."""

    def __init__(self, runtime: ProviderRuntime, descriptor: ProviderDescriptor | None = None) -> None:
        if not callable(getattr(runtime, "invoke", None)) or not callable(getattr(runtime, "create_client", None)):
            raise TypeError("runtime must implement the frozen provider port")
        self.runtime = runtime
        self._client_identities: list[tuple[Any, dict[str, Any]]] = []
        self._client_replay_specs: list[tuple[Any, dict[str, Any]]] = []
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
        headers = dict(default_headers or {})
        client = self.runtime.create_client(api_key, base_url=base_url, default_headers=headers)
        self._client_identities.append((client, {
            "credential_sha256": "sha256:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
            "base_url": base_url,
            "default_headers": {name: "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest() for name, value in sorted(headers.items())},
            "timeout": str(getattr(client, "timeout", None)),
            "max_retries": getattr(client, "max_retries", None),
            "live_state_sha256": _client_live_state(client),
        }))
        self._client_replay_specs.append((client, {
            "api_key": api_key,
            "base_url": base_url,
            "default_headers": headers,
        }))
        return client
    def replay_client_identity(self, client: Any, secret_bindings: Mapping[str, str]) -> dict[str, Any]:
        reference = self.port_descriptor.api_key_env
        expected = secret_bindings.get(reference)
        actual = client.get("api_key") if isinstance(client, Mapping) else getattr(client, "api_key", None)
        if callable(getattr(actual, "get_secret_value", None)):
            actual = actual.get_secret_value()
        creation = next((record for candidate, record in self._client_identities if candidate is client), None)
        expected_digest = "sha256:" + hashlib.sha256(expected.encode("utf-8")).hexdigest() if isinstance(expected, str) else None
        exposed_credential_matches = actual is None or (isinstance(actual, str) and actual == expected)
        return {
            "verified": creation is not None and creation["credential_sha256"] == expected_digest and creation["live_state_sha256"] == _client_live_state(client) and exposed_credential_matches,
            "client_type": f"{type(client).__module__}.{type(client).__qualname__}",
            "credential_reference": reference,
            "creation_options": creation,
        }

    def replay_worker_client_spec(self, client: Any, secret_bindings: Mapping[str, str]) -> dict[str, Any]:
        spec = next((record for candidate, record in self._client_replay_specs if candidate is client), None)
        expected = secret_bindings.get(self.port_descriptor.api_key_env)
        if spec is None or spec["api_key"] != expected:
            raise ValueError("provider client cannot be reconstructed from the frozen secret binding")
        return dict(spec)

    def replay_worker_client(self, spec: Mapping[str, Any]) -> Any:
        return self.create_client(
            spec["api_key"],
            base_url=spec.get("base_url"),
            default_headers=spec.get("default_headers"),
        )

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
