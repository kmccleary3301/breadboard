"""Provider stream-event and terminal lifecycle codecs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from .contract_wire import (
    ProviderContractError,
    ProviderProtocolError,
    _EVENT_KINDS,
    _FINISH_REASONS,
    _MAX_EXTENSION_KEYS,
    _SAFE_CODE,
    _USAGE_KEYS,
    _bounded_json_value,
    _canonical_value,
    _require_text,
    _validate_canonical_argument_pair,
)
from .contract_messages import (
    ProviderMessage,
    _sanitize_replay,
    normalize_request_messages,
)


@dataclass
class ProviderEvent:
    sequence: int
    kind: str
    content_index: Optional[int] = None
    message_id: Optional[str] = None
    call_id: Optional[str] = None
    name: Optional[str] = None
    delta: Optional[str] = None
    arguments_json: Optional[str] = None
    arguments: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise ProviderContractError("event.sequence must be a nonnegative integer")
        if self.kind not in _EVENT_KINDS:
            raise ProviderProtocolError(f"unknown provider event kind: {self.kind!r}")
        if self.metadata:
            raise ProviderContractError("event metadata is not part of the v2 contract")
        fields = {
            "content_index": self.content_index,
            "message_id": self.message_id,
            "call_id": self.call_id,
            "name": self.name,
            "delta": self.delta,
            "arguments_json": self.arguments_json,
            "arguments": self.arguments,
        }
        allowed = {
            "response_start": set(),
            "text_start": {"content_index", "message_id"},
            "text_delta": {"content_index", "message_id", "delta"},
            "text_end": {"content_index", "message_id"},
            "thinking_start": {"content_index", "message_id"},
            "thinking_delta": {"content_index", "message_id", "delta"},
            "thinking_end": {"content_index", "message_id"},
            "tool_call_start": {"content_index", "message_id", "call_id", "name"},
            "tool_call_delta": {"content_index", "message_id", "call_id", "delta"},
            "tool_call_end": {
                "content_index",
                "message_id",
                "call_id",
                "arguments_json",
                "arguments",
            },
        }[self.kind]
        unexpected = {
            key
            for key, value in fields.items()
            if value is not None and key not in allowed
        }
        if unexpected:
            raise ProviderContractError(
                f"{self.kind} contains invalid fields: {sorted(unexpected)!r}"
            )
        if self.kind == "response_start":
            return
        if (
            not isinstance(self.content_index, int)
            or isinstance(self.content_index, bool)
            or self.content_index < 0
        ):
            raise ProviderContractError("indexed provider event requires content_index")
        _require_text(self.message_id, "provider event message_id", max_length=256)
        if self.kind.startswith("tool_call_"):
            _require_text(self.call_id, "tool event call_id", max_length=256)
        if self.kind == "tool_call_start":
            _require_text(self.name, "tool event name", max_length=256)
        if self.kind.endswith("_delta"):
            _require_text(self.delta, "provider event delta", max_length=65536)
        if self.kind == "tool_call_end":
            if (
                not isinstance(self.arguments_json, str)
                or len(self.arguments_json) > 65536
            ):
                raise ProviderContractError(
                    "tool_call_end requires bounded arguments_json"
                )
            _validate_canonical_argument_pair(
                self.arguments_json,
                self.arguments,
                field_name="tool_call_end",
            )

    def as_dict(self) -> Dict[str, Any]:
        self.validate()
        result: Dict[str, Any] = {"sequence": self.sequence, "kind": self.kind}
        for key in (
            "content_index",
            "message_id",
            "call_id",
            "name",
            "delta",
            "arguments_json",
            "arguments",
        ):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass
class ProviderDone:
    output_emitted: bool
    finish_reason: str = "stop"
    raw_provider_finish: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    assistant_messages: List[Dict[str, Any]] = field(default_factory=list)
    provider_replay: List[Dict[str, Any]] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    _wire_strict: bool = field(default=False, repr=False, compare=False)
    _wire_provider_replay_present: bool = field(
        default=True, repr=False, compare=False
    )

    def as_dict(self) -> Dict[str, Any]:
        if self.finish_reason not in _FINISH_REASONS:
            raise ProviderContractError("invalid finish_reason")
        if not isinstance(self.output_emitted, bool):
            raise ProviderContractError("terminal.output_emitted must be boolean")
        if not isinstance(self.assistant_messages, list):
            raise ProviderContractError(
                "terminal.assistant_messages must be an array"
            )
        if not isinstance(self.provider_replay, list):
            raise ProviderContractError(
                "terminal.provider_replay must be an array"
            )
        if not isinstance(self.evidence_refs, list):
            raise ProviderContractError(
                "terminal.evidence_refs must be an array"
            )
        result: Dict[str, Any] = {
            "kind": "done",
            "output_emitted": self.output_emitted,
            "finish_reason": self.finish_reason,
        }
        if self.raw_provider_finish is not None:
            raw_provider_finish = _require_text(
                self.raw_provider_finish, "raw_provider_finish", max_length=128
            )
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", raw_provider_finish
            ):
                raise ProviderContractError(
                    "raw_provider_finish must be a protocol token"
                )
            result["raw_provider_finish"] = raw_provider_finish
        if self._wire_strict:
            if self.usage is not None:
                result["usage"] = _canonical_value(self.usage)
            result["assistant_messages"] = _canonical_value(
                self.assistant_messages
            )
            if self._wire_provider_replay_present or self.provider_replay:
                result["provider_replay"] = _canonical_value(
                    self.provider_replay
                )
            result["evidence_refs"] = _canonical_value(self.evidence_refs)
            return result
        if self.usage is not None:
            result["usage"] = normalize_usage(self.usage)
        result["assistant_messages"] = [
            normalize_terminal_message(message) for message in self.assistant_messages
        ]
        result["provider_replay"] = [
            _sanitize_replay(item) for item in self.provider_replay
        ]
        result["evidence_refs"] = _normalize_evidence_refs(self.evidence_refs)
        return result


@dataclass
class ProviderErrorTerminal:
    output_emitted: bool
    code: str
    category: Literal["adapter", "provider", "transport", "protocol", "configuration"]
    retryable: bool
    http_status: Optional[int] = None
    evidence_refs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        code = _safe_error_code(self.code)
        if self.category not in {
            "adapter",
            "provider",
            "transport",
            "protocol",
            "configuration",
        }:
            raise ProviderContractError("invalid error category")
        if not isinstance(self.evidence_refs, list):
            raise ProviderContractError(
                "terminal.evidence_refs must be an array"
            )
        if not isinstance(self.output_emitted, bool) or not isinstance(
            self.retryable, bool
        ):
            raise ProviderContractError("invalid error terminal booleans")
        result: Dict[str, Any] = {
            "kind": "error",
            "output_emitted": self.output_emitted,
            "code": code,
            "category": self.category,
            "retryable": self.retryable,
        }
        if self.http_status is not None:
            if (
                not isinstance(self.http_status, int)
                or isinstance(self.http_status, bool)
                or not 100 <= self.http_status <= 599
            ):
                raise ProviderContractError("http_status must be a valid HTTP status")
            result["http_status"] = self.http_status
        result["evidence_refs"] = _normalize_evidence_refs(self.evidence_refs)
        return result


@dataclass
class ProviderCancelled:
    output_emitted: bool
    owner: Literal["caller", "provider", "transport", "engine"]
    reason_code: str
    evidence_refs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        if self.owner not in {"caller", "provider", "transport", "engine"}:
            raise ProviderContractError("invalid cancellation owner")
        if not isinstance(self.output_emitted, bool):
            raise ProviderContractError("invalid cancellation output_emitted")
        if not isinstance(self.evidence_refs, list):
            raise ProviderContractError(
                "terminal.evidence_refs must be an array"
            )
        return {
            "kind": "cancelled",
            "output_emitted": self.output_emitted,
            "owner": self.owner,
            "reason_code": _safe_error_code(self.reason_code),
            "evidence_refs": _normalize_evidence_refs(self.evidence_refs),
        }


def _safe_error_code(value: Any) -> str:
    text = str(value or "provider_error").strip().lower().replace(" ", "_")
    if not _SAFE_CODE.fullmatch(text):
        text = "provider_error"
    return text


def _normalize_evidence_refs(values: Iterable[Any]) -> List[str]:
    if not isinstance(values, (list, tuple)):
        raise ProviderContractError("evidence_refs must be an array")
    refs = [_require_text(item, "evidence ref", max_length=1024) for item in values]
    if len(refs) != len(set(refs)):
        raise ProviderContractError("evidence refs must be unique")
    return refs


def normalize_usage(usage: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(usage, Mapping):
        raise ProviderContractError("usage must be an object")
    aliases = {
        "input_tokens": "inputTokens",
        "prompt_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "completion_tokens": "outputTokens",
        "cache_read_tokens": "cacheReadTokens",
        "cache_read_input_tokens": "cacheReadTokens",
        "cache_write_tokens": "cacheWriteTokens",
        "cache_creation_input_tokens": "cacheWriteTokens",
        "total_tokens": "totalTokens",
        "reasoning_tokens": "reasoningTokens",
    }
    result: Dict[str, Any] = {}
    extensions: Dict[str, Any] = {}
    explicit_extensions = usage.get("extensions")
    extensions_supplied = "extensions" in usage
    if extensions_supplied:
        if not isinstance(explicit_extensions, Mapping):
            raise ProviderContractError("usage.extensions must be an object")
        for key, value in explicit_extensions.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ProviderContractError("usage extension key is invalid")
            extensions[key] = _bounded_json_value(
                value, field_name=f"usage.extensions.{key}"
            )
    for key, value in usage.items():
        if key == "extensions":
            continue
        if not isinstance(key, str):
            raise ProviderContractError("usage keys must be strings")
        normalized_key = aliases.get(key, key)
        if normalized_key not in _USAGE_KEYS:
            if not key or len(key) > 128:
                raise ProviderContractError("usage extension key is invalid")
            canonical_value = _bounded_json_value(
                value, field_name=f"usage.extensions.{key}"
            )
            if key in extensions and extensions[key] != canonical_value:
                raise ProviderContractError(
                    f"conflicting usage extension value for {key}"
                )
            extensions[key] = canonical_value
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ProviderContractError(
                f"usage.{normalized_key} must be a nonnegative integer"
            )
        if normalized_key in result and result[normalized_key] != value:
            raise ProviderContractError(
                f"conflicting usage aliases for {normalized_key}"
            )
        result[normalized_key] = value
    if len(extensions) > _MAX_EXTENSION_KEYS:
        raise ProviderContractError("usage.extensions contains too many keys")
    if extensions or extensions_supplied:
        result["extensions"] = _bounded_json_value(
            extensions, field_name="usage.extensions"
        )
    return result


def normalize_terminal_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, ProviderMessage):
        if message.role != "assistant":
            raise ProviderContractError(
                "terminal assistant_messages must use assistant role"
            )
        return message.as_dict()
    if not isinstance(message, Mapping):
        raise ProviderContractError("terminal message must be an object")
    role = message.get("role")
    if role != "assistant":
        raise ProviderContractError(
            "terminal assistant_messages must use assistant role"
        )
    return normalize_request_messages([dict(message)])[0]


def _validate_event_lifecycle(
    events: Sequence[ProviderEvent], *, require_closed: bool
) -> None:
    if not events:
        if require_closed:
            raise ProviderContractError(
                "done provider exchange requires response_start"
            )
        return
    if events[0].kind != "response_start":
        raise ProviderContractError(
            "provider events must begin with response_start"
        )
    open_blocks: Dict[
        Tuple[int, str], Tuple[str, Optional[str]]
    ] = {}
    closed_blocks: set[Tuple[int, str]] = set()
    for event in events[1:]:
        if event.kind == "response_start":
            raise ProviderContractError(
                "provider events contain duplicate response_start"
            )
        if event.content_index is None or event.message_id is None:
            raise ProviderContractError(
                "provider content lifecycle is missing identity"
            )
        key = (event.content_index, event.message_id)
        family, phase = event.kind.rsplit("_", 1)
        identity = (family, event.call_id if family == "tool_call" else None)
        if phase == "start":
            if key in open_blocks or key in closed_blocks:
                raise ProviderContractError(
                    "provider content lifecycle contains a duplicate start"
                )
            open_blocks[key] = identity
            continue
        active = open_blocks.get(key)
        if active is None or active != identity:
            raise ProviderContractError(
                "provider content lifecycle is incomplete or mismatched"
            )
        if phase == "end":
            del open_blocks[key]
            closed_blocks.add(key)
    if require_closed and open_blocks:
        raise ProviderContractError(
            "done provider exchange contains unclosed content"
        )

