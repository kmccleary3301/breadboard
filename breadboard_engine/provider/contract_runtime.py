"""Provider runtime sanitization, context, errors, and interface."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional

from ..security import redaction
from .contract_wire import ProviderContractError, canonical_json
from .contract_events import _safe_error_code
from .contract_messages import ProviderMessage, ProviderResult
from .contract_recorder import ProviderExchangeRecorder
from .profiles import OpenAICompletionsProviderProfile
from .routing import ProviderDescriptor


def _portable_provider_payload(value: Any, *, seen: set[int] | None = None) -> Any:
    """Convert a provider SDK value to bounded redacted data for diagnostics only."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redaction.scrub_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return redaction.scrub_text(bytes(value).decode("utf-8", errors="replace"))[
            :8192
        ]
    active = seen if seen is not None else set()
    marker = id(value)
    if marker in active:
        return "<recursive>"
    active.add(marker)
    try:
        if isinstance(value, Mapping):
            return {
                redaction.scrub_text(str(key)): _portable_provider_payload(
                    item, seen=active
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_portable_provider_payload(item, seen=active) for item in value]
        if is_dataclass(value) and not isinstance(value, type):
            return {
                item.name: _portable_provider_payload(
                    getattr(value, item.name), seen=active
                )
                for item in fields(value)
            }
        for method_name in ("model_dump", "to_dict", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    converted = method()
                except Exception:
                    continue
                if converted is not value:
                    return _portable_provider_payload(converted, seen=active)
        return f"<{type(value).__name__}>"
    finally:
        active.discard(marker)


def sanitize_provider_result(result: ProviderResult) -> ProviderResult:
    """Remove operation secrets and SDK transport objects before lease release."""
    if not isinstance(result, ProviderResult):
        raise ProviderContractError("runtime returned an invalid ProviderResult")
    for message in result.messages:
        if not isinstance(message, ProviderMessage):
            raise ProviderContractError("runtime result contains an invalid message")
        message.content = _portable_provider_payload(message.content)
        message.finish_reason = (
            redaction.scrub_text(message.finish_reason)
            if message.finish_reason is not None
            else None
        )
        message.message_id = (
            redaction.scrub_text(message.message_id)
            if message.message_id is not None
            else None
        )
        message.raw_message = _portable_provider_payload(message.raw_message)
        message.raw_choice = _portable_provider_payload(message.raw_choice)
        message.reasoning = _portable_provider_payload(message.reasoning)
        message.annotations = (
            _portable_provider_payload(message.annotations)
            if isinstance(message.annotations, dict)
            else {}
        )
        message.tool_results = _portable_provider_payload(message.tool_results)
        for call in message.tool_calls:
            call.id = redaction.scrub_text(call.id) if call.id is not None else None
            call.name = (
                redaction.scrub_text(call.name) if call.name is not None else None
            )
            call.parsed_arguments = _portable_provider_payload(call.parsed_arguments)
            canonical_arguments = canonical_json(call.parsed_arguments)
            call.arguments = canonical_arguments
            call.arguments_json = canonical_arguments
            call.raw = _portable_provider_payload(call.raw)
    result.raw_response = _portable_provider_payload(result.raw_response)
    result.usage = (
        _portable_provider_payload(result.usage)
        if isinstance(result.usage, dict)
        else None
    )
    result.encrypted_reasoning = (
        _portable_provider_payload(result.encrypted_reasoning)
        if isinstance(result.encrypted_reasoning, list)
        else None
    )
    result.reasoning_summaries = (
        [redaction.scrub_text(str(item))[:8192] for item in result.reasoning_summaries]
        if result.reasoning_summaries is not None
        else None
    )
    result.reasoning_blocks = (
        _portable_provider_payload(result.reasoning_blocks)
        if isinstance(result.reasoning_blocks, list)
        else None
    )
    result.provider_replay = (
        _portable_provider_payload(result.provider_replay)
        if isinstance(result.provider_replay, list)
        else None
    )
    result.model = (
        redaction.scrub_text(result.model) if result.model is not None else None
    )
    result.metadata = (
        _portable_provider_payload(result.metadata)
        if isinstance(result.metadata, dict)
        else {}
    )
    return result
@dataclass
class ProviderRuntimeContext:
    """Context object passed to provider runtimes."""

    session_state: Any
    agent_config: Dict[str, Any]
    stream: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    input_id: Optional[str] = None
    turn_id: Optional[str] = None
    exchange_recorder: Optional[ProviderExchangeRecorder] = None
    cancel_requested: Optional[Callable[[], bool]] = None
    provider_profile: Optional[OpenAICompletionsProviderProfile] = None

    def record_provider_event(
        self, kind: str, payload: Optional[Mapping[str, Any]] = None
    ) -> None:
        if self.exchange_recorder is not None:
            self.exchange_recorder.record(kind, payload)

    def raise_if_cancelled(self) -> None:
        """Raise the typed caller-cancellation boundary before more provider work."""

        if self.cancel_requested is None or not self.cancel_requested():
            return
        raise ProviderRuntimeError(
            "provider invocation cancelled",
            kind="transport",
            details={
                "code": "caller_cancelled",
                "classification": "cancelled",
                "cancelled": True,
                "cancel_owner": "caller",
                "reason_code": "caller_cancelled",
            },
            output_emitted=bool(
                self.exchange_recorder and self.exchange_recorder.output_emitted
            ),
        )


ProviderErrorKind = Literal[
    "adapter", "provider", "transport", "protocol", "configuration"
]
_MODEL_FALLBACK_REASONS = frozenset(
    {
        "provider_unavailable",
        "rate_limited",
        "model_unavailable",
        "capability_drift",
        "timeout_before_output",
    }
)


def _derive_model_fallback_reason(
    details: Mapping[str, Any],
    *,
    kind: ProviderErrorKind,
    output_emitted: bool,
) -> str | None:
    if output_emitted or kind in {"adapter", "configuration", "protocol"}:
        return None
    code = _safe_error_code(details.get("code"))
    classification = str(
        details.get("classification") or ""
    ).strip().lower()
    status = details.get("status_code", details.get("status"))
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    if (
        code
        in {
            "auth_failure",
            "authentication_failed",
            "unauthorized",
            "forbidden",
            "policy_rejection",
            "policy_denied",
        }
        or classification in {"auth", "authentication", "policy"}
        or status_code in {401, 403}
    ):
        return None
    if (
        code in {"rate_limited", "rate_limit", "too_many_requests"}
        or classification in {"rate_limit", "rate_limited"}
        or status_code == 429
    ):
        return "rate_limited"
    if (
        code
        in {
            "timeout",
            "request_timeout",
            "provider_timeout",
            "connection_timeout",
        }
        or classification in {"timeout", "timed_out"}
    ):
        return "timeout_before_output"
    if code in {"model_unavailable", "model_not_found", "invalid_model"}:
        return "model_unavailable"
    if code in {"capability_drift", "unsupported_capability"}:
        return "capability_drift"
    if status_code is not None and status_code >= 500:
        return "provider_unavailable"
    if kind in {"provider", "transport"} and code in {
        "provider_error",
        "transport_error",
        "service_unavailable",
        "connection_error",
        "route_circuit_open",
    }:
        return "provider_unavailable"
    return None


class ProviderRuntimeError(RuntimeError):
    """Raised when a provider runtime encounters a classified fatal error."""

    def __init__(
        self,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        kind: ProviderErrorKind = "provider",
        output_emitted: bool = False,
        model_fallback_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.details: Dict[str, Any] = dict(details or {})
        self.kind: ProviderErrorKind = kind
        self.output_emitted = output_emitted
        derived_reason = _derive_model_fallback_reason(
            self.details, kind=kind, output_emitted=output_emitted
        )
        explicit_reason = (
            model_fallback_reason
            if model_fallback_reason in _MODEL_FALLBACK_REASONS
            else None
        )
        self.model_fallback_reason = derived_reason or (
            explicit_reason
            if not output_emitted
            and kind in {"provider", "transport"}
            and _safe_error_code(self.details.get("code"))
            not in {
                "auth_failure",
                "authentication_failed",
                "unauthorized",
                "forbidden",
                "policy_rejection",
                "policy_denied",
            }
            and str(
                self.details.get("classification") or ""
            ).strip().lower()
            not in {"auth", "authentication", "policy"}
            and self.details.get("status_code", self.details.get("status"))
            not in {401, 403, "401", "403"}
            else None
        )

    @property
    def replay_safe(self) -> bool:
        return not self.output_emitted

    @property
    def safe_code(self) -> str:
        value = self.details.get("code") if isinstance(self.details, dict) else None
        return _safe_error_code(value or f"{self.kind}_error")


class ProviderRuntime:
    """Interface for provider runtimes."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        raise NotImplementedError

    def create_client_from_config(self, config: Mapping[str, Any]) -> Any:
        """Create a runtime client without collapsing provider-specific credentials."""

        return self.create_client(
            config.get("api_key"),
            base_url=config.get("base_url"),
            default_headers=config.get("default_headers"),
        )

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        raise NotImplementedError
