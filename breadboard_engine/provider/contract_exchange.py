"""Provider exchange aggregate, wire codec, and public projection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from .contract_wire import (
    ProviderContractError,
    _OUTPUT_EVENT_KINDS,
    _require_fields,
    _require_text,
    _strict_dict,
    _validate_content_wire,
    _validate_exchange_wire_semantics,
    _validate_provider_exchange_v2_wire,
    _validate_provider_replay_wire,
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


@dataclass
class ProviderExchangeV2:
    schema_version: str
    exchange_id: str
    correlation: ProviderCorrelation
    provider: ProviderIdentity
    request: ProviderRequest
    events: List[ProviderEvent]
    terminal: ProviderDone | ProviderErrorTerminal | ProviderCancelled

    def validate(self) -> "ProviderExchangeV2":
        if self.schema_version != "bb.provider_exchange.v2":
            raise ProviderContractError(
                "provider exchange must use bb.provider_exchange.v2"
            )
        _require_text(self.exchange_id, "exchange_id", max_length=256)
        if not isinstance(self.correlation, ProviderCorrelation):
            raise ProviderContractError("correlation must be ProviderCorrelation")
        if not isinstance(self.provider, ProviderIdentity):
            raise ProviderContractError("provider must be ProviderIdentity")
        if not isinstance(self.request, ProviderRequest):
            raise ProviderContractError("request must be ProviderRequest")
        for index, message in enumerate(self.request.messages):
            if isinstance(message, Mapping):
                _validate_content_wire(
                    message.get("content"),
                    field_name=f"request.messages[{index}].content",
                )
        if not isinstance(self.events, list):
            raise ProviderContractError("events must be an array")
        for index, event in enumerate(self.events):
            if not isinstance(event, ProviderEvent):
                raise ProviderContractError("events must contain ProviderEvent values")
            if event.sequence != index:
                raise ProviderContractError(
                    "provider event sequences must be contiguous zero-based integers"
                )
            event.validate()
        if not isinstance(
            self.terminal, (ProviderDone, ProviderErrorTerminal, ProviderCancelled)
        ):
            raise ProviderContractError("exchange must contain exactly one terminal")
        self.terminal.as_dict()
        if isinstance(self.terminal, ProviderDone):
            for index, message in enumerate(self.terminal.assistant_messages):
                if isinstance(message, Mapping):
                    _validate_content_wire(
                        message.get("content"),
                        field_name=f"terminal.assistant_messages[{index}].content",
                    )
            for index, replay in enumerate(self.terminal.provider_replay):
                _validate_provider_replay_wire(
                    replay, field_name=f"terminal.provider_replay[{index}]"
                )
        observed_output = any(
            event.kind in _OUTPUT_EVENT_KINDS for event in self.events
        )
        if isinstance(self.terminal, ProviderDone):
            observed_output = observed_output or bool(
                self.terminal.assistant_messages
            )
            if self.terminal.output_emitted is not observed_output:
                raise ProviderContractError(
                    "done terminal output_emitted disagrees with its output"
                )
        elif observed_output and not self.terminal.output_emitted:
            raise ProviderContractError(
                "terminal cannot deny recorded provider output"
            )
        _validate_event_lifecycle(
            self.events,
            require_closed=isinstance(self.terminal, ProviderDone),
        )
        return self

    def as_dict(self) -> Dict[str, Any]:
        self.validate()
        terminal = self.terminal.as_dict()
        result = {
            "schema_version": self.schema_version,
            "exchange_id": self.exchange_id,
            "correlation": self.correlation.as_dict(),
            "provider": self.provider.as_dict(),
            "request": self.request.as_dict(),
            "events": [event.as_dict() for event in self.events],
            "terminal": terminal,
        }
        _validate_provider_exchange_v2_wire(result)
        _validate_exchange_wire_semantics(result)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderExchangeV2":
        _validate_provider_exchange_v2_wire(value)
        top = _strict_dict(
            value,
            field_name="provider exchange",
            allowed={
                "schema_version",
                "exchange_id",
                "correlation",
                "provider",
                "request",
                "events",
                "terminal",
            },
        )
        _require_fields(
            top,
            {
                "schema_version",
                "exchange_id",
                "correlation",
                "provider",
                "request",
                "events",
                "terminal",
            },
            field_name="provider exchange",
        )
        _validate_exchange_wire_semantics(top)
        correlation = _strict_dict(
            top.get("correlation"),
            field_name="correlation",
            allowed={"session_id", "input_id", "turn_id"},
        )
        _require_fields(
            correlation,
            {"session_id", "input_id", "turn_id"},
            field_name="correlation",
        )
        provider = _strict_dict(
            top.get("provider"),
            field_name="provider",
            allowed={"provider_id", "runtime_id", "route_id", "model"},
        )
        _require_fields(
            provider,
            {"provider_id", "runtime_id", "route_id", "model"},
            field_name="provider",
        )
        request = _strict_dict(
            top.get("request"),
            field_name="request",
            allowed={"stream", "messages", "tools"},
        )
        _require_fields(
            request,
            {"stream", "messages", "tools"},
            field_name="request",
        )
        events_raw = top.get("events")
        if not isinstance(events_raw, list):
            raise ProviderContractError("events must be an array")
        events: List[ProviderEvent] = []
        for raw in events_raw:
            if not isinstance(raw, Mapping):
                raise ProviderContractError("event must be an object")
            allowed_event_fields = {
                "sequence",
                "kind",
                "content_index",
                "message_id",
                "call_id",
                "name",
                "delta",
                "arguments_json",
                "arguments",
            }
            raw = _strict_dict(raw, field_name="event", allowed=allowed_event_fields)
            _require_fields(
                raw, {"sequence", "kind"}, field_name="event"
            )
            event = ProviderEvent(
                sequence=raw.get("sequence"),
                kind=raw.get("kind"),
                content_index=raw.get("content_index"),
                message_id=raw.get("message_id"),
                call_id=raw.get("call_id"),
                name=raw.get("name"),
                delta=raw.get("delta"),
                arguments_json=raw.get("arguments_json"),
                arguments=raw.get("arguments"),
            )
            events.append(event)
        terminal_raw = top.get("terminal")
        if not isinstance(terminal_raw, Mapping):
            raise ProviderContractError("terminal must be an object")
        terminal_kind = terminal_raw.get("kind")
        if terminal_kind == "done":
            terminal_raw = _strict_dict(
                terminal_raw,
                field_name="done terminal",
                allowed={
                    "kind",
                    "output_emitted",
                    "finish_reason",
                    "raw_provider_finish",
                    "usage",
                    "assistant_messages",
                    "provider_replay",
                    "evidence_refs",
                },
            )
            _require_fields(
                terminal_raw,
                {
                    "kind",
                    "output_emitted",
                    "finish_reason",
                    "assistant_messages",
                    "evidence_refs",
                },
                field_name="done terminal",
            )
            terminal: ProviderDone | ProviderErrorTerminal | ProviderCancelled = (
                ProviderDone(
                    output_emitted=terminal_raw["output_emitted"],
                    finish_reason=terminal_raw["finish_reason"],
                    raw_provider_finish=terminal_raw.get("raw_provider_finish"),
                    usage=terminal_raw.get("usage"),
                    assistant_messages=terminal_raw["assistant_messages"],
                    provider_replay=terminal_raw.get("provider_replay", []),
                    evidence_refs=terminal_raw["evidence_refs"],
                    _wire_strict=True,
                    _wire_provider_replay_present="provider_replay" in terminal_raw,
                )
            )
        elif terminal_kind == "error":
            terminal_raw = _strict_dict(
                terminal_raw,
                field_name="error terminal",
                allowed={
                    "kind",
                    "output_emitted",
                    "code",
                    "category",
                    "retryable",
                    "http_status",
                    "evidence_refs",
                },
            )
            _require_fields(
                terminal_raw,
                {
                    "kind",
                    "output_emitted",
                    "code",
                    "category",
                    "retryable",
                    "evidence_refs",
                },
                field_name="error terminal",
            )
            terminal = ProviderErrorTerminal(
                output_emitted=terminal_raw["output_emitted"],
                code=terminal_raw["code"],
                category=terminal_raw["category"],
                retryable=terminal_raw["retryable"],
                http_status=terminal_raw.get("http_status"),
                evidence_refs=terminal_raw["evidence_refs"],
            )
        elif terminal_kind == "cancelled":
            terminal_raw = _strict_dict(
                terminal_raw,
                field_name="cancelled terminal",
                allowed={
                    "kind",
                    "output_emitted",
                    "owner",
                    "reason_code",
                    "evidence_refs",
                },
            )
            _require_fields(
                terminal_raw,
                {
                    "kind",
                    "output_emitted",
                    "owner",
                    "reason_code",
                    "evidence_refs",
                },
                field_name="cancelled terminal",
            )
            terminal = ProviderCancelled(
                output_emitted=terminal_raw["output_emitted"],
                owner=terminal_raw["owner"],
                reason_code=terminal_raw["reason_code"],
                evidence_refs=terminal_raw["evidence_refs"],
            )
        else:
            raise ProviderContractError(
                "terminal kind must be done, error, or cancelled"
            )
        return cls(
            schema_version=top.get("schema_version"),
            exchange_id=top.get("exchange_id"),
            correlation=ProviderCorrelation(**correlation),
            provider=ProviderIdentity(**provider),
            request=ProviderRequest(**request, _wire_strict=True),
            events=events,
            terminal=terminal,
        ).validate()


def encode_provider_exchange(
    exchange: ProviderExchangeV2 | Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the strict v2 JSON object used for persistence and transport."""
    if isinstance(exchange, ProviderExchangeV2):
        return exchange.as_dict()
    return ProviderExchangeV2.from_dict(exchange).as_dict()

_PUBLIC_COMPLETION_SENTINELS = frozenset(
    {
        ">>>>>> END RESPONSE",
        "TASK COMPLETE",
    }
)


def strip_public_completion_sentinel_lines(value: str) -> str:
    """Remove standalone completion-control lines from public text."""
    lines = value.splitlines(keepends=True)
    removed = any(line.strip() in _PUBLIC_COMPLETION_SENTINELS for line in lines)
    filtered = "".join(
        line for line in lines if line.strip() not in _PUBLIC_COMPLETION_SENTINELS
    )
    return filtered.rstrip("\r\n") if removed else filtered


def strip_public_completion_sentinel_tree(value: Any) -> Any:
    """Remove completion-control lines from every string in a public value."""
    if isinstance(value, str):
        return strip_public_completion_sentinel_lines(value)
    if isinstance(value, list):
        return [strip_public_completion_sentinel_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_public_completion_sentinel_tree(item) for item in value)
    if isinstance(value, dict):
        return {
            key: strip_public_completion_sentinel_tree(item)
            for key, item in value.items()
        }
    return value


def strip_provider_exchange_completion_sentinels(
    exchange: ProviderExchangeV2 | Mapping[str, Any],
) -> Dict[str, Any]:
    """Remove control-only completion lines without changing request/tool inputs."""

    document = encode_provider_exchange(exchange)
    delta_groups: Dict[tuple[str, Any, Any], List[Dict[str, Any]]] = {}
    for event in document["events"]:
        if event["kind"] not in {"text_delta", "thinking_delta"} or not isinstance(
            event.get("delta"), str
        ):
            continue
        key = (
            event["kind"],
            event.get("content_index"),
            event.get("message_id"),
        )
        delta_groups.setdefault(key, []).append(event)
    removed_event_ids: set[int] = set()
    for events in delta_groups.values():
        combined = "".join(event["delta"] for event in events)
        sanitized = strip_public_completion_sentinel_lines(combined)
        if sanitized == combined:
            continue
        if sanitized:
            events[0]["delta"] = sanitized
            removed_event_ids.update(id(event) for event in events[1:])
        else:
            removed_event_ids.update(id(event) for event in events)
    if removed_event_ids:
        document["events"] = [
            event
            for event in document["events"]
            if id(event) not in removed_event_ids
        ]
        for sequence, event in enumerate(document["events"]):
            event["sequence"] = sequence

    terminal = document["terminal"]
    if terminal["kind"] == "done":
        sanitized_messages: List[Dict[str, Any]] = []
        for message in terminal["assistant_messages"]:
            sanitized_blocks: List[Dict[str, Any]] = []
            for block in message["content"]:
                block_type = block.get("type")
                if block_type in {"text", "thinking"} and isinstance(
                    block.get("text"), str
                ):
                    text = strip_public_completion_sentinel_lines(block["text"])
                    if text:
                        sanitized_blocks.append({**block, "text": text})
                elif block_type == "provider_replay":
                    sanitized_blocks.append(
                        strip_public_completion_sentinel_tree(block)
                    )
                else:
                    sanitized_blocks.append(block)
            if sanitized_blocks:
                sanitized_messages.append(
                    {**message, "content": sanitized_blocks}
                )
        terminal["assistant_messages"] = sanitized_messages
        terminal["provider_replay"] = strip_public_completion_sentinel_tree(
            terminal.get("provider_replay", [])
        )
    observed_output = any(
        event["kind"] in _OUTPUT_EVENT_KINDS
        for event in document["events"]
    )
    if terminal["kind"] == "done":
        terminal["output_emitted"] = bool(
            observed_output or terminal["assistant_messages"]
        )
    else:
        terminal["output_emitted"] = bool(
            terminal.get("output_emitted") or observed_output
        )
    return encode_provider_exchange(document)
