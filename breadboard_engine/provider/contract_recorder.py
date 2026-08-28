"""Provider stream recorder and lifecycle input boundary."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Tuple

from .contract_wire import (
    ProviderContractError,
    ProviderProtocolError,
    _EVENT_INPUT_FIELDS,
    _EVENT_KINDS,
    _OUTPUT_EVENT_KINDS,
    _coalesce_text_fields,
    canonical_json,
)
from .contract_messages import (
    ProviderCorrelation,
    ProviderIdentity,
    ProviderRequest,
)
from .contract_events import (
    ProviderCancelled,
    ProviderDone,
    ProviderErrorTerminal,
    ProviderEvent,
    _validate_event_lifecycle,
)
from .contract_exchange import ProviderExchangeV2


@dataclass
class ProviderExchangeRecorder:
    """Capture required provider stream events before SessionState projection."""

    correlation: ProviderCorrelation
    provider: ProviderIdentity
    request: ProviderRequest
    exchange_id: str = field(default_factory=lambda: f"px_{uuid.uuid4().hex}")
    events: List[ProviderEvent] = field(default_factory=list)

    @property
    def output_emitted(self) -> bool:
        return any(event.kind in _OUTPUT_EVENT_KINDS for event in self.events)

    def reset_unemitted_attempt(self) -> None:
        """Discard lifecycle-only events before a replay-safe retry."""
        if self.output_emitted:
            raise ProviderProtocolError(
                "cannot reset a provider attempt after normative output"
            )
        self.events.clear()

    def rebind_request_stream(self, stream: bool) -> None:
        """Record the effective stream posture of a replay-safe retry."""
        if not isinstance(stream, bool):
            raise ProviderContractError("request.stream must be boolean")
        if self.request.stream == stream:
            return
        self.reset_unemitted_attempt()
        self.request = ProviderRequest(
            stream=stream,
            messages=self.request.messages,
            tools=self.request.tools,
        )

    def rebind_provider(self, provider: ProviderIdentity) -> None:
        """Begin a replay-safe fallback attempt with its actual provider identity."""
        if not isinstance(provider, ProviderIdentity):
            raise ProviderContractError(
                "fallback provider identity must be a ProviderIdentity"
            )
        if provider == self.provider:
            return
        provider.as_dict()
        self.reset_unemitted_attempt()
        self.provider = provider

    def record(self, kind: str, payload: Optional[Mapping[str, Any]] = None) -> None:
        if kind not in _EVENT_KINDS:
            raise ProviderProtocolError(f"unknown provider event kind: {kind!r}")
        if payload is not None and not isinstance(payload, Mapping):
            raise ProviderProtocolError("provider event payload must be an object")
        payload_dict = dict(payload or {})
        unknown = set(payload_dict) - _EVENT_INPUT_FIELDS[kind]
        if unknown:
            raise ProviderProtocolError(
                f"{kind} contains unknown input fields: {sorted(unknown)!r}"
            )
        if kind != "response_start" and not self.events:
            self.record("response_start")
        if kind == "response_start" and self.events:
            raise ProviderProtocolError(
                "response_start must be the first provider event"
            )

        is_response_start = kind == "response_start"
        is_tool = kind.startswith("tool_call_")
        is_delta = kind.endswith("_delta")
        try:
            indexed_values = [
                payload_dict[key]
                for key in ("content_index", "index")
                if key in payload_dict
            ]
            if len(indexed_values) > 1 and any(
                value != indexed_values[0] for value in indexed_values[1:]
            ):
                raise ProviderContractError(
                    "provider event content-index aliases disagree"
                )
            content_index = indexed_values[0] if indexed_values else 0
            message_id = _coalesce_text_fields(
                payload_dict,
                ("message_id", "item_id"),
                field_name="provider event message_id",
                required=not is_response_start,
            )
            call_id = (
                _coalesce_text_fields(
                    payload_dict,
                    ("call_id",),
                    field_name="provider event call_id",
                    required=True,
                )
                if is_tool
                else None
            )
            tool_name = (
                _coalesce_text_fields(
                    payload_dict,
                    ("name", "tool"),
                    field_name="provider event tool name",
                    required=kind == "tool_call_start",
                )
                if is_tool
                else None
            )
            if "provider_field" in payload_dict and payload_dict[
                "provider_field"
            ] not in {"reasoning", "reasoning_content"}:
                raise ProviderContractError(
                    "thinking delta contains an unknown provider field"
                )
            if kind == "text_end" and "text" in payload_dict and not isinstance(
                payload_dict["text"], str
            ):
                raise ProviderContractError("text_end text must be a string")

            arguments_json: Optional[str] = None
            parsed_arguments: Any = None
            if kind == "tool_call_end":
                argument_values = [
                    payload_dict[key]
                    for key in (
                        "arguments_json",
                        "arguments",
                        "parsed_arguments",
                        "arguments_parsed",
                    )
                    if key in payload_dict
                ]
                if not argument_values:
                    raise ProviderContractError(
                        "tool_call_end requires explicit arguments"
                    )
                normalized_arguments: List[Tuple[str, Any]] = []
                for raw_arguments in argument_values:
                    parsed = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else raw_arguments
                    )
                    normalized_arguments.append((canonical_json(parsed), parsed))
                if any(
                    encoded != normalized_arguments[0][0]
                    for encoded, _ in normalized_arguments[1:]
                ):
                    raise ProviderContractError(
                        "completed tool argument aliases disagree"
                    )
                arguments_json, parsed_arguments = normalized_arguments[0]

            delta = (
                _coalesce_text_fields(
                    payload_dict,
                    ("delta", "text", "arguments_delta"),
                    field_name="provider event delta",
                    max_length=65536,
                    required=True,
                )
                if is_delta
                else None
            )
            event = ProviderEvent(
                sequence=len(self.events),
                kind=kind,
                content_index=None if is_response_start else content_index,
                message_id=None if is_response_start else message_id,
                call_id=call_id,
                name=tool_name if kind == "tool_call_start" else None,
                delta=delta,
                arguments_json=arguments_json,
                arguments=parsed_arguments,
            )
            _validate_event_lifecycle(
                [*self.events, event], require_closed=False
            )
            event.validate()
        except (TypeError, ValueError, ProviderContractError):
            raise ProviderProtocolError(
                f"malformed {kind} provider event"
            ) from None
        self.events.append(event)

    def build(
        self, terminal: ProviderDone | ProviderErrorTerminal | ProviderCancelled
    ) -> ProviderExchangeV2:
        if isinstance(terminal, ProviderDone) and not self.events:
            self.record("response_start")
        return ProviderExchangeV2(
            schema_version="bb.provider_exchange.v2",
            exchange_id=self.exchange_id,
            correlation=self.correlation,
            provider=self.provider,
            request=self.request,
            events=list(self.events),
            terminal=terminal,
        ).validate()
