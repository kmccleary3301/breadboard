"""Typed provider-owner facade and lossless ``bb.provider_exchange.v2`` codec.

This module is intentionally separate from the standard-library-only contract
and transport helpers.  Importing it opts into the engine's canonical provider
codec; it never imports a provider client or performs an effect.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from breadboard_engine.provider.contract_events import (
    ProviderCancelled,
    ProviderDone,
    ProviderErrorTerminal,
    ProviderEvent,
)
from breadboard_engine.provider.contract_exchange import (
    ProviderExchangeV2,
    encode_provider_exchange,
)
from breadboard_engine.provider.contract_messages import (
    ProviderCorrelation,
    ProviderIdentity,
    ProviderRequest,
)

from .author import AttemptId, GenerationId, InstanceId, RequestId, WorkId, WorkerSessionId
from .transport import RequestKey, WireProtocolError


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    worker_session_id: WorkerSessionId
    request_id: RequestId
    generation_id: GenerationId
    instance_id: InstanceId
    work_id: WorkId
    attempt_id: AttemptId
    authority_epoch: int

    def as_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "authority_epoch": self.authority_epoch,
            "generation_id": self.generation_id,
            "instance_id": self.instance_id,
            "request_id": self.request_id,
            "worker_session_id": self.worker_session_id,
            "work_id": self.work_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProviderRoute":
        key = RequestKey.from_dict(value)
        return cls(
            WorkerSessionId(key.worker_session_id),
            RequestId(key.request_id),
            GenerationId(key.generation_id),
            InstanceId(key.instance_id),
            WorkId(key.work_id),
            AttemptId(key.attempt_id),
            key.authority_epoch,
        )


@dataclass(frozen=True, slots=True)
class ProviderExchangeRequest:
    schema_version: str
    exchange_id: str
    correlation: ProviderCorrelation
    provider: ProviderIdentity
    request: ProviderRequest
    route: ProviderRoute


    def __post_init__(self) -> None:
        if self.schema_version != "bb.provider_exchange.v2":
            raise ValueError("provider requests must use bb.provider_exchange.v2")
        if not self.exchange_id:
            raise ValueError("provider exchange_id must not be empty")


@dataclass(frozen=True, slots=True)
class ProviderCallRequest:
    """Canonical provider input before the owner assigns exchange identity.

    A caller supplies the complete provider identity and request body, while
    correlation and admitted worker routing remain owner-owned.  In
    particular, this type deliberately has no session, turn, exchange, or
    worker-route fields.
    """

    provider: ProviderIdentity
    request: ProviderRequest

    def __post_init__(self) -> None:
        if not isinstance(self.provider, ProviderIdentity):
            raise TypeError("provider must be ProviderIdentity")
        if not isinstance(self.request, ProviderRequest):
            raise TypeError("request must be ProviderRequest")
        # Validate the composed values without projecting any request fields.
        self.provider.as_dict()
        self.request.as_dict()

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider.as_dict(),
            "request": self.request.as_dict(),
        }

    to_dict = as_dict

    @classmethod
    def from_dict(cls, value: object) -> "ProviderCallRequest":
        if not isinstance(value, Mapping) or set(value) != {"provider", "request"}:
            raise WireProtocolError(
                "provider call request requires exactly provider and request"
            )
        provider = _mapping(value["provider"], "provider")
        request = _mapping(value["request"], "request")
        try:
            return cls(
                provider=ProviderIdentity(
                    provider_id=provider["provider_id"],
                    runtime_id=provider["runtime_id"],
                    route_id=provider["route_id"],
                    model=provider["model"],
                ),
                request=ProviderRequest(
                    stream=request["stream"],
                    messages=request["messages"],
                    tools=request["tools"],
                    _wire_strict=True,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WireProtocolError("provider call request is malformed") from exc


@dataclass(frozen=True, slots=True)
class ProviderExchangeHandle:
    exchange_id: str
    stream_id: str
    route: ProviderRoute


@dataclass(frozen=True, slots=True)
class ProviderUnknown:
    exchange_id: str
    correlation: ProviderCorrelation
    route: ProviderRoute
    reason: str
    output_emitted: bool
    last_sequence: int | None
    evidence_refs: tuple[str, ...]


ProviderStreamItem: TypeAlias = (
    ProviderEvent
    | ProviderDone
    | ProviderErrorTerminal
    | ProviderCancelled
    | ProviderUnknown
)
ProviderOutcome: TypeAlias = ProviderExchangeV2 | ProviderUnknown



class ProviderExchangeCodec(Protocol):
    def encode_request(self, request: ProviderExchangeRequest) -> bytes:
        ...

    def decode_request(self, body: bytes, route: ProviderRoute) -> ProviderExchangeRequest:
        ...

    def encode_exchange(self, exchange: ProviderExchangeV2) -> bytes:
        ...

    def decode_exchange(self, body: bytes) -> ProviderExchangeV2:
        ...

    def encode_call_request(self, request: ProviderCallRequest) -> bytes:
        ...

    def decode_call_request(self, body: bytes) -> ProviderCallRequest:
        ...


class CanonicalProviderExchangeCodec:
    """Use the existing strict engine codec without projecting away fields."""

    @staticmethod
    def encode_call_request(request: ProviderCallRequest) -> bytes:
        if not isinstance(request, ProviderCallRequest):
            raise TypeError("request must be ProviderCallRequest")
        return _canonical_json(request.as_dict())

    @staticmethod
    def decode_call_request(body: bytes) -> ProviderCallRequest:
        value = _decode_object(body, "provider call request")
        try:
            return ProviderCallRequest.from_dict(value)
        except (TypeError, ValueError, WireProtocolError) as exc:
            raise WireProtocolError("provider call request is malformed") from exc

    @staticmethod
    def encode_request(request: ProviderExchangeRequest) -> bytes:
        if not isinstance(request, ProviderExchangeRequest):
            raise TypeError("request must be ProviderExchangeRequest")
        value = {
            "correlation": request.correlation.as_dict(),
            "exchange_id": request.exchange_id,
            "provider": request.provider.as_dict(),
            "request": request.request.as_dict(),
            "schema_version": request.schema_version,
        }
        return _canonical_json(value)

    @staticmethod
    def decode_request(body: bytes, route: ProviderRoute) -> ProviderExchangeRequest:
        value = _decode_object(body, "provider exchange request")
        expected = {"schema_version", "exchange_id", "correlation", "provider", "request"}
        if set(value) != expected:
            raise WireProtocolError("provider exchange request fields are not exact")
        try:
            correlation = _mapping(value["correlation"], "correlation")
            provider = _mapping(value["provider"], "provider")
            request = _mapping(value["request"], "request")
            return ProviderExchangeRequest(
                schema_version=value["schema_version"],
                exchange_id=value["exchange_id"],
                correlation=ProviderCorrelation(
                    session_id=correlation["session_id"],
                    input_id=correlation["input_id"],
                    turn_id=correlation["turn_id"],
                ),
                provider=ProviderIdentity(
                    provider_id=provider["provider_id"],
                    runtime_id=provider["runtime_id"],
                    route_id=provider["route_id"],
                    model=provider["model"],
                ),
                request=ProviderRequest(
                    stream=request["stream"],
                    messages=request["messages"],
                    tools=request["tools"],
                    _wire_strict=True,
                ),
                route=route,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WireProtocolError("provider exchange request is malformed") from exc

    @staticmethod
    def encode_exchange(exchange: ProviderExchangeV2) -> bytes:
        if not isinstance(exchange, ProviderExchangeV2):
            raise TypeError("exchange must be canonical ProviderExchangeV2")
        return _canonical_json(encode_provider_exchange(exchange))

    @staticmethod
    def decode_exchange(body: bytes) -> ProviderExchangeV2:
        value = _decode_object(body, "provider exchange")
        try:
            return ProviderExchangeV2.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise WireProtocolError("provider exchange is malformed") from exc


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise WireProtocolError("provider value is not canonical JSON") from exc


def _decode_object(body: bytes, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WireProtocolError(f"{label} is not UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise WireProtocolError(f"{label} must be an object")
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise WireProtocolError(f"{label} must be an object")
    return value

__all__ = [
    "CanonicalProviderExchangeCodec", "ProviderCallRequest", "ProviderCancelled",
    "ProviderCorrelation", "ProviderDone", "ProviderErrorTerminal", "ProviderEvent",
    "ProviderExchangeCodec", "ProviderExchangeHandle", "ProviderExchangeRequest",
    "ProviderExchangeV2", "ProviderIdentity", "ProviderOutcome", "ProviderRequest",
    "ProviderRoute", "ProviderStreamItem", "ProviderUnknown",
]
