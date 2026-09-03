from __future__ import annotations

import math
import re
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Tuple

if TYPE_CHECKING:
    from ..state.session_state import SessionState

from ..messaging.markdown_logger import MarkdownLogger
from .contracts import (
    ProviderContractError,
    ProviderCancelled,
    ProviderCorrelation,
    ProviderDone,
    ProviderErrorTerminal,
    ProviderExchangeRecorder,
    ProviderIdentity,
    ProviderRequest,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    sanitize_provider_result,
    strip_provider_exchange_completion_sentinels,
)
from .normalizer import (
    normalize_provider_result,
    normalized_result_messages,
    normalized_result_replay,
)


class ProviderInvoker:
    """Handles provider invocation, streaming fallbacks, and retry orchestration."""

    def __init__(
        self,
        *,
        provider_metrics: Any,
        route_health: Any,
        logger_v2: Any,
        md_writer: Any,
        retry_with_fallback: Callable[..., Optional[ProviderResult]],
        update_health_metadata: Callable[[SessionState], None],
        set_last_latency: Callable[[Optional[float]], None],
        set_html_detected: Callable[[bool], None],
        client_lease: Optional[Callable[[str, Any], Any]] = None,
    ) -> None:
        self.provider_metrics = provider_metrics
        self.route_health = route_health
        self.logger_v2 = logger_v2
        self.md_writer = md_writer
        self.retry_with_fallback = retry_with_fallback
        self.update_health_metadata = update_health_metadata
        self.set_last_latency = set_last_latency
        self.set_html_detected = set_html_detected
        self.client_lease = client_lease

    def invoke(
        self,
        *,
        runtime: Any,
        client: Any,
        model: str,
        send_messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        stream_responses: bool,
        runtime_context: ProviderRuntimeContext,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        turn_index: int,
        route_id: Optional[str],
    ) -> Tuple[ProviderResult, bool]:
        fallback_stream_reason: Optional[str] = None
        result: Optional[ProviderResult] = None
        last_error: Optional[ProviderRuntimeError] = None
        success_recorded = False
        self.set_last_latency(None)
        self.set_html_detected(False)
        descriptor = getattr(runtime, "descriptor", None)
        correlation = self._resolve_correlation(
            runtime_context, session_state, turn_index
        )
        provider = ProviderIdentity(
            provider_id=str(getattr(descriptor, "provider_id", "provider")),
            runtime_id=str(getattr(descriptor, "runtime_id", "runtime")),
            route_id=route_id,
            model=str(model),
        )
        request = ProviderRequest(
            stream=bool(stream_responses),
            messages=send_messages,
            tools=list(tools_schema or []),
        )
        recorder = ProviderExchangeRecorder(
            correlation=correlation, provider=provider, request=request
        )
        runtime_context.exchange_recorder = recorder
        runtime_context.session_id = correlation.session_id
        runtime_context.input_id = correlation.input_id
        runtime_context.turn_id = correlation.turn_id
        cache_observation_recorded = False
        pending_success_metric: Optional[Tuple[str, bool, float]] = None
        runtime_context.extra.pop("cache_observation", None)
        try:
            session_state.provider_metadata.pop("last_cache_observation", None)
        except Exception:
            pass

        def _observe_success(result_value: ProviderResult) -> Dict[str, Any]:
            nonlocal cache_observation_recorded
            if cache_observation_recorded:
                existing = runtime_context.extra.get("cache_observation")
                return existing if isinstance(existing, dict) else {}
            observation = self._cache_observation(
                result_value,
                route_id=recorder.provider.route_id or route_id or model,
                model=recorder.provider.model,
                context=runtime_context,
            )
            runtime_context.extra["cache_observation"] = observation
            try:
                session_state.set_provider_metadata(
                    "last_cache_observation", observation
                )
            except Exception:
                pass
            cache_observation_recorded = True
            return observation

        def _terminalize_error(exc: ProviderRuntimeError) -> None:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("cancelled"):
                owner = details.get("cancel_owner")
                if owner not in {"caller", "provider", "transport", "engine"}:
                    owner = "engine"
                terminal = ProviderCancelled(
                    output_emitted=exc.output_emitted or recorder.output_emitted,
                    owner=owner,
                    reason_code=str(details.get("reason_code") or "cancelled"),
                )
            else:
                category = exc.kind
                if category not in {
                    "adapter",
                    "provider",
                    "transport",
                    "protocol",
                    "configuration",
                }:
                    category = "protocol"
                http_status = details.get("status_code")
                if (
                    not isinstance(http_status, int)
                    or isinstance(http_status, bool)
                    or not 100 <= http_status <= 599
                ):
                    http_status = None
                terminal = ProviderErrorTerminal(
                    output_emitted=exc.output_emitted or recorder.output_emitted,
                    code=exc.safe_code,
                    category=category,
                    retryable=exc.replay_safe and not recorder.output_emitted,
                    http_status=http_status,
                )
            self._persist_exchange(session_state, recorder, terminal)

        def _adopt_result_provider(result_value: ProviderResult) -> None:
            identity = (
                result_value.metadata.get("provider_exchange_identity")
                if isinstance(result_value.metadata, dict)
                else None
            )
            if identity is None:
                return
            if not isinstance(identity, dict) or set(identity) != {
                "provider_id",
                "runtime_id",
                "route_id",
                "model",
            }:
                raise ProviderContractError(
                    "fallback result has invalid provider identity"
                )
            recorder.rebind_provider(
                ProviderIdentity(
                    provider_id=identity["provider_id"],
                    runtime_id=identity["runtime_id"],
                    route_id=identity["route_id"],
                    model=identity["model"],
                )
            )

        def _contract_failure() -> ProviderRuntimeError:
            return ProviderRuntimeError(
                "provider contract violation",
                details={"code": "provider_contract_error"},
                kind="protocol",
                output_emitted=recorder.output_emitted,
            )
        def _persist_success(result_value: ProviderResult) -> None:
            nonlocal pending_success_metric
            result_metadata = (
                result_value.metadata
                if isinstance(result_value.metadata, dict)
                else {}
            )
            fallback_metric = result_metadata.pop("_provider_success_metric", None)
            success_metric = pending_success_metric
            if success_metric is None and isinstance(fallback_metric, Mapping):
                metric_model = fallback_metric.get("route")
                metric_stream = fallback_metric.get("stream")
                metric_elapsed = fallback_metric.get("elapsed")
                if (
                    isinstance(metric_model, str)
                    and isinstance(metric_stream, bool)
                    and isinstance(metric_elapsed, (int, float))
                ):
                    success_metric = (
                        metric_model,
                        metric_stream,
                        float(metric_elapsed),
                    )
            try:
                sanitize_provider_result(result_value)
                _adopt_result_provider(result_value)
                normalized_events = normalize_provider_result(result_value)
                assistant_messages = normalized_result_messages(result_value)
                provider_replay = normalized_result_replay(
                    result_value,
                    provider_id=recorder.provider.provider_id,
                )
                result_value.metadata["normalized_events"] = normalized_events
                terminal = ProviderDone(
                    output_emitted=bool(assistant_messages)
                    or recorder.output_emitted,
                    finish_reason=self._finish_reason(result_value),
                    raw_provider_finish=self._raw_finish(result_value),
                    usage=result_value.usage,
                    assistant_messages=assistant_messages,
                    provider_replay=provider_replay,
                )
                exchange = self._persist_exchange(
                    session_state, recorder, terminal, result=result_value
                )
                result_value.metadata["provider_exchange"] = exchange
                cache_observation = _observe_success(result_value)
                if success_metric is not None:
                    metric_model, metric_stream, metric_elapsed = success_metric
                    self.provider_metrics.add_call(
                        metric_model,
                        stream=metric_stream,
                        elapsed=metric_elapsed,
                        outcome="success",
                        details={"cache_observation": cache_observation},
                    )
                    pending_success_metric = None
            except Exception:
                if success_metric is not None:
                    metric_model, metric_stream, metric_elapsed = success_metric
                    self.provider_metrics.add_call(
                        metric_model,
                        stream=metric_stream,
                        elapsed=metric_elapsed,
                        outcome="error",
                        error_reason="provider_contract_error",
                        html_detected=False,
                        details=None,
                    )
                pending_success_metric = None
                failure = _contract_failure()
                _terminalize_error(failure)
                raise failure from None
        profile_bound = runtime_context.provider_profile is not None
        if profile_bound and not stream_responses:
            profile_error = ProviderRuntimeError(
                "profile-bound provider invocation requires streaming",
                details={"code": "profile_requires_streaming"},
                kind="configuration",
            )
            _terminalize_error(profile_error)
            raise profile_error


        if self.route_health.is_circuit_open(model):
            notice = f"[circuit-open] Skipping direct call for route {model}; attempting fallback."
            self.provider_metrics.add_circuit_skip(model)
            self._log_system_message(notice, markdown_logger)
            self._append_md_transcript(notice)
            try:
                session_state.add_transcript_entry(
                    {"circuit_open": {"model": model, "notice": notice}}
                )
            except Exception:
                pass
            circuit_error = ProviderRuntimeError(
                "provider route unavailable",
                details={"code": "route_circuit_open"},
                kind="transport",
            )
            if profile_bound:
                _terminalize_error(circuit_error)
                raise circuit_error
            recorder.rebind_request_stream(False)
            try:
                fallback_result = self.retry_with_fallback(
                    runtime,
                    client,
                    model,
                    send_messages,
                    tools_schema,
                    runtime_context,
                    stream_responses=False,
                    session_state=session_state,
                    markdown_logger=markdown_logger,
                    attempted=[],
                    last_error=circuit_error,
                    client_lease=self.client_lease,
                    route_id=route_id,
                )
            except ProviderRuntimeError as exc:
                _terminalize_error(exc)
                raise
            except ProviderContractError:
                failure = _contract_failure()
                _terminalize_error(failure)
                raise failure from None
            except Exception:
                failure = ProviderRuntimeError(
                    "provider fallback failed",
                    details={"code": "provider_fallback_error"},
                    kind="adapter",
                    output_emitted=recorder.output_emitted,
                )
                _terminalize_error(failure)
                raise failure from None
            self.update_health_metadata(session_state)
            if fallback_result is not None:
                result = fallback_result
                _persist_success(result)
                return result, False
            _terminalize_error(circuit_error)
            raise circuit_error

        def _is_tool_turn() -> bool:
            if not tools_schema:
                return False
            for msg in reversed(session_state.messages):
                role = msg.get("role")
                if role == "assistant":
                    tool_calls = msg.get("tool_calls") or []
                    return bool(tool_calls)
                if role == "user":
                    break
            return False

        attempted_models: List[Tuple[str, bool, Optional[str]]] = []

        def _call_runtime(target_model: str, use_stream: bool) -> ProviderResult:
            start_time = time.time()
            nonlocal pending_success_metric
            try:
                if use_stream != stream_responses:
                    observer = runtime_context.extra.get("stream_retry_observer")
                    if callable(observer):
                        observer(target_model, use_stream)
                if self.client_lease is None or profile_bound:
                    call_result = sanitize_provider_result(
                        runtime.invoke(
                            client=client,
                            model=target_model,
                            messages=send_messages,
                            tools=tools_schema,
                            stream=use_stream,
                            context=runtime_context,
                        )
                    )
                else:
                    with self.client_lease(
                        route_id or target_model,
                        runtime,
                    ) as leased_client:
                        call_result = sanitize_provider_result(
                            runtime.invoke(
                                client=leased_client,
                                model=target_model,
                                messages=send_messages,
                                tools=tools_schema,
                                stream=use_stream,
                                context=runtime_context,
                            )
                        )
                elapsed = time.time() - start_time
                pending_success_metric = (target_model, use_stream, elapsed)
                self.set_last_latency(elapsed)
                self.set_html_detected(False)
                return call_result
            except ProviderContractError:
                elapsed = time.time() - start_time
                self.provider_metrics.add_call(
                    target_model,
                    stream=use_stream,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason="provider_contract_error",
                    html_detected=False,
                    details=None,
                )
                self.set_last_latency(elapsed)
                raise ProviderRuntimeError(
                    "provider contract violation",
                    details={"code": "provider_contract_error"},
                    kind="protocol",
                ) from None
            except ProviderRuntimeError as exc:
                elapsed = time.time() - start_time
                details = getattr(exc, "details", None)
                html_detected = isinstance(details, dict) and bool(
                    details.get("html_detected")
                )
                self.provider_metrics.add_call(
                    target_model,
                    stream=use_stream,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason=exc.safe_code,
                    html_detected=html_detected,
                    details=None,
                )
                self.set_last_latency(elapsed)
                self.set_html_detected(html_detected)
                raise
            except Exception:
                elapsed = time.time() - start_time
                self.provider_metrics.add_call(
                    target_model,
                    stream=use_stream,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason="provider_runtime_error",
                    html_detected=False,
                    details=None,
                )
                self.set_last_latency(elapsed)
                self.set_html_detected(False)
                raise ProviderRuntimeError(
                    "provider runtime failed",
                    details={"code": "provider_runtime_error"},
                    kind="adapter",
                ) from None

        def _maybe_disable_stream(reason: str) -> None:
            try:
                session_state.set_provider_metadata("streaming_disabled", True)
            except Exception:
                pass
            warning_payload = {
                "provider": getattr(runtime.descriptor, "provider_id", "unknown"),
                "runtime": getattr(runtime.descriptor, "runtime_id", "unknown"),
                "reason": reason,
            }
            try:
                session_state.add_transcript_entry(
                    {"streaming_disabled": warning_payload}
                )
            except Exception:
                pass
            warning_text = (
                "[streaming-disabled] "
                f"Provider {warning_payload['provider']} ({warning_payload['runtime']}) "
                f"rejected streaming: {reason}. Falling back to non-streaming."
            )
            self._log_system_message(warning_text, markdown_logger)
            self._append_md_transcript(warning_text)
            try:
                print(warning_text)
            except Exception:
                pass

        def _is_auth_or_policy(exc: ProviderRuntimeError) -> bool:
            details = exc.details if isinstance(exc.details, Mapping) else {}
            code = str(details.get("code") or "").lower()
            classification = str(
                details.get("classification") or ""
            ).lower()
            status = details.get("status_code", details.get("status"))
            return (
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
                or status in {401, 403, "401", "403"}
            )

        def _replay_safe(exc: ProviderRuntimeError) -> bool:
            details = exc.details if isinstance(exc.details, Mapping) else {}
            return (
                details.get("cancelled") is not True
                and exc.kind not in {"adapter", "configuration"}
                and exc.replay_safe
                and not recorder.output_emitted
                and not _is_auth_or_policy(exc)
            )

        def _can_retry_without_stream(exc: ProviderRuntimeError) -> bool:
            if profile_bound:
                return False
            if not _replay_safe(exc):
                return False
            details = exc.details if isinstance(exc.details, Mapping) else {}
            code = str(details.get("code") or "").lower()
            return exc.kind == "protocol" or code in {
                "streaming_unsupported",
                "unsupported_streaming",
                "stream_protocol_error",
                "stream_unavailable",
            }

        def _can_model_fallback(exc: ProviderRuntimeError) -> bool:
            if profile_bound:
                return False
            if not _replay_safe(exc) or exc.kind == "protocol":
                return False
            config = getattr(runtime_context, "agent_config", None)
            lock = (
                config.get("model_role_lock")
                if isinstance(config, Mapping)
                else None
            )
            if not isinstance(lock, Mapping):
                return True
            reason = exc.model_fallback_reason
            if not isinstance(reason, str) or not reason:
                return False
            active_role = str(
                (
                    config.get("active_model_role")
                    if isinstance(config, Mapping)
                    else None
                )
                or (
                    (lock.get("defaults") or {}).get("role")
                    if isinstance(lock.get("defaults"), Mapping)
                    else ""
                )
                or ""
            )
            roles = lock.get("roles")
            binding = (
                roles.get(active_role) if isinstance(roles, Mapping) else None
            )
            return isinstance(binding, Mapping) and reason in (
                binding.get("fallback_on") or ()
            )

        def _record_route_failure(exc: ProviderRuntimeError, reason: str) -> None:
            details = exc.details if isinstance(exc.details, Mapping) else {}
            if (
                exc.kind in {"adapter", "configuration"}
                or details.get("cancelled") is True
            ):
                return
            self.route_health.record_failure(model, reason)
            self.update_health_metadata(session_state)

        if stream_responses:
            try:
                result = _call_runtime(model, True)
                attempted_models.append((model, True, None))
                self.route_health.record_success(model)
                success_recorded = True
                self.update_health_metadata(session_state)
            except ProviderRuntimeError as exc:
                last_error = exc
                reason = exc.safe_code
                attempted_models.append((model, True, reason))
                _record_route_failure(exc, reason)
                if _can_retry_without_stream(exc):
                    fallback_stream_reason = reason
                elif not _can_model_fallback(exc):
                    _terminalize_error(exc)
                    raise
        used_streaming = stream_responses and result is not None

        if result is None and (
            not stream_responses or fallback_stream_reason is not None
        ):
            if fallback_stream_reason:
                recorder.rebind_request_stream(False)
                warning_payload = {
                    "provider": getattr(
                        runtime.descriptor, "provider_id", "unknown"
                    ),
                    "runtime": getattr(
                        runtime.descriptor, "runtime_id", "unknown"
                    ),
                    "reason": fallback_stream_reason,
                }
                self.provider_metrics.add_stream_override(
                    route=route_id, reason=fallback_stream_reason
                )
                session_state.add_transcript_entry(
                    {"streaming_disabled": warning_payload}
                )
                warning_text = (
                    "[streaming-disabled] "
                    f"Provider {warning_payload['provider']} ({warning_payload['runtime']}) "
                    f"rejected streaming: {fallback_stream_reason}. Falling back to non-streaming."
                )
                self._log_system_message(warning_text, markdown_logger)
                self._append_md_transcript(warning_text)
                try:
                    session_state.set_provider_metadata(
                        "streaming_disabled", True
                    )
                except Exception:
                    pass
                try:
                    print(warning_text)
                except Exception:
                    pass
            try:
                result = _call_runtime(model, False)
                attempted_models.append((model, False, None))
                used_streaming = False
                self.route_health.record_success(model)
                success_recorded = True
                self.update_health_metadata(session_state)
            except ProviderRuntimeError as exc:
                last_error = exc
                reason = exc.safe_code
                attempted_models.append((model, False, reason))
                result = None
                _record_route_failure(exc, reason)
                if not _can_model_fallback(exc):
                    _terminalize_error(exc)
                    raise
                recorder.reset_unemitted_attempt()

        if result is None and _is_tool_turn():
            history = session_state.get_provider_metadata("streaming_disabled")
            if not history:
                reason_text = last_error.safe_code if last_error else "tool_turn_retry"
                _maybe_disable_stream(reason_text)

        if result is None:
            recorder.rebind_request_stream(False)
            try:
                fallback_result = self.retry_with_fallback(
                    runtime,
                    client,
                    model,
                    send_messages,
                    tools_schema,
                    runtime_context,
                    stream_responses=False,
                    session_state=session_state,
                    markdown_logger=markdown_logger,
                    attempted=attempted_models,
                    last_error=last_error,
                    client_lease=self.client_lease,
                    route_id=route_id,
                )
            except ProviderRuntimeError as exc:
                _terminalize_error(exc)
                raise
            except ProviderContractError:
                failure = _contract_failure()
                _terminalize_error(failure)
                raise failure from None
            except Exception:
                failure = ProviderRuntimeError(
                    "provider fallback failed",
                    details={"code": "provider_fallback_error"},
                    kind="adapter",
                    output_emitted=recorder.output_emitted,
                )
                _terminalize_error(failure)
                raise failure from None
            if fallback_result is not None:
                result = fallback_result
                used_streaming = False
                success_recorded = True
            elif last_error:
                _terminalize_error(last_error)
                raise last_error
            else:
                no_result_error = ProviderRuntimeError(
                    "provider invocation produced no result",
                    details={"code": "provider_no_result"},
                    kind="protocol",
                )
                _terminalize_error(no_result_error)
                raise no_result_error

        if result is not None:
            _persist_success(result)

        if result is not None and not success_recorded:
            self.route_health.record_success(model)
            self.update_health_metadata(session_state)
        else:
            self.update_health_metadata(session_state)

        if result is None:
            no_result_error = ProviderRuntimeError(
                "provider invocation produced no result",
                details={"code": "provider_no_result"},
                kind="protocol",
            )
            _terminalize_error(no_result_error)
            raise no_result_error
        return result, used_streaming

    @staticmethod
    def _resolve_correlation(
        context: ProviderRuntimeContext, session_state: SessionState, turn_index: int
    ) -> ProviderCorrelation:
        metadata = getattr(session_state, "provider_metadata", {}) or {}
        extra = getattr(context, "extra", {}) or {}
        if not isinstance(metadata, Mapping) or not isinstance(extra, Mapping):
            raise ProviderContractError(
                "provider correlation metadata must be an object"
            )

        def exact_value(field_name: str) -> str:
            values = [
                getattr(context, field_name, None),
                extra.get(field_name),
                metadata.get(field_name),
            ]
            supplied: List[str] = []
            for value in values:
                if value is None:
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ProviderContractError(
                        f"provider {field_name} must be a nonempty string"
                    )
                supplied.append(value)
            if not supplied:
                raise ProviderContractError(
                    "provider invocation requires exact session/input/turn "
                    "correlation"
                )
            if any(value != supplied[0] for value in supplied[1:]):
                raise ProviderContractError(
                    f"provider {field_name} correlation sources disagree"
                )
            return supplied[0]

        del turn_index
        return ProviderCorrelation(
            session_id=exact_value("session_id"),
            input_id=exact_value("input_id"),
            turn_id=exact_value("turn_id"),
        )


    @staticmethod
    def _finish_reason(result: ProviderResult) -> str:
        aliases = {
            "stop": "stop",
            "end_turn": "stop",
            "completed": "stop",
            "complete": "stop",
            "stop_sequence": "stop",
            "pause_turn": "stop",
            "length": "length",
            "max_tokens": "length",
            "max_output_tokens": "length",
            "truncated": "length",
            "model_context_window_exceeded": "length",
            "toolUse": "toolUse",
            "tool_call": "toolUse",
            "tool_calls": "toolUse",
            "tool_use": "toolUse",
            "function_call": "toolUse",
            "error": "error",
            "content_filter": "error",
            "refusal": "error",
            "safety": "error",
            "blocked": "error",
            "aborted": "aborted",
        }
        raw_reasons = [
            message.finish_reason
            for message in result.messages
            if message.finish_reason is not None
        ]
        if not raw_reasons:
            return "stop"
        canonical_reasons: List[str] = []
        for reason in raw_reasons:
            canonical = aliases.get(reason)
            if canonical is None:
                raise ProviderContractError(
                    f"unknown provider finish reason: {reason!r}"
                )
            canonical_reasons.append(canonical)
        if any(reason != canonical_reasons[0] for reason in canonical_reasons[1:]):
            raise ProviderContractError(
                "provider result contains conflicting finish reasons"
            )
        return canonical_reasons[0]

    @staticmethod
    def _cache_observation(
        result: ProviderResult,
        *,
        route_id: str,
        model: str,
        context: ProviderRuntimeContext,
    ) -> Dict[str, Any]:
        usage = result.usage if isinstance(result.usage, Mapping) else {}

        def _token(*names: str) -> int | None:
            for name in names:
                value = usage.get(name)
                if type(value) is int and value >= 0:
                    return value
            return None

        def _nested_token(*names: str) -> int | None:
            for name in names:
                details = usage.get(name)
                if isinstance(details, Mapping):
                    value = details.get("cached_tokens")
                    if type(value) is int and value >= 0:
                        return value
            return None

        prefix_digest = (context.extra or {}).get("cache_prefix_digest")
        divergence_reason = (context.extra or {}).get("cache_divergence_reason")
        if not isinstance(prefix_digest, str):
            prefix_digest = None
        if not isinstance(divergence_reason, str):
            divergence_reason = None
        cache_read_tokens = _token(
            "cache_read_tokens", "cache_read", "cache_read_input_tokens"
        )
        if cache_read_tokens is None:
            cache_read_tokens = _nested_token(
                "prompt_tokens_details", "input_tokens_details"
            )
        cache_write_tokens = _token(
            "cache_write_tokens", "cache_write", "cache_creation_input_tokens"
        )

        return {
            "observed": cache_read_tokens is not None or cache_write_tokens is not None,
            "prefix_digest": prefix_digest,
            "provider_tokens": {
                "cache_read": cache_read_tokens,
                "cache_write": cache_write_tokens,
            },
            "route_id": route_id,
            "model": model,
            "divergence_reason": divergence_reason,
        }

    @staticmethod
    def _raw_finish(result: ProviderResult) -> Optional[str]:
        if isinstance(result.metadata, dict):
            raw_reason = result.metadata.get("raw_finish_reason")
            if isinstance(raw_reason, str) and raw_reason:
                return raw_reason
        return next(
            (
                str(message.finish_reason)
                for message in reversed(result.messages)
                if message.finish_reason
            ),
            None,
        )

    @staticmethod
    def _public_finish_metadata(metadata: Any) -> Dict[str, Any]:
        """Select bounded non-content diagnostics for the public run result."""

        if not isinstance(metadata, Mapping):
            return {}
        result: Dict[str, Any] = {}
        completed = metadata.get("provider_turn_completed")
        if isinstance(completed, bool):
            result["provider_turn_completed"] = completed
        for key in (
            "provider_turn_completion_method",
            "provider_turn_completion_reason",
            "raw_finish_reason",
        ):
            value = metadata.get(key)
            if (
                isinstance(value, str)
                and re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value
                )
            ):
                result[key] = value
        status_code = metadata.get("status_code")
        if (
            isinstance(status_code, int)
            and not isinstance(status_code, bool)
            and 100 <= status_code <= 599
        ):
            result["status_code"] = status_code
        timing = metadata.get("provider_runtime_timing")
        if isinstance(timing, Mapping):
            safe_timing: Dict[str, Any] = {}
            for index, (key, value) in enumerate(timing.items()):
                if index >= 64:
                    break
                if (
                    not isinstance(key, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                ):
                    continue
                if value is None or isinstance(value, bool):
                    safe_timing[key] = value
                elif (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                ):
                    safe_timing[key] = value
            if safe_timing:
                result["provider_runtime_timing"] = safe_timing
        return result

    @staticmethod
    def _persist_exchange(
        session_state: SessionState,
        recorder: ProviderExchangeRecorder,
        terminal: Any,
        *,
        result: Optional[ProviderResult] = None,
    ) -> Dict[str, Any]:
        exchange = strip_provider_exchange_completion_sentinels(
            recorder.build(terminal)
        )
        record_exchange = getattr(session_state, "record_provider_exchange", None)
        if callable(record_exchange):
            record_exchange(exchange)
        else:
            history = session_state.get_provider_metadata(
                "provider_exchange_history", []
            )
            if not isinstance(history, list):
                raise ProviderContractError(
                    "provider_exchange_history must be an array"
                )
            session_state.set_provider_metadata(
                "provider_exchange_history", [*history, exchange]
            )
            session_state.set_provider_metadata("last_provider_exchange", exchange)
        finish_metadata = (
            ProviderInvoker._public_finish_metadata(result.metadata)
            if result is not None
            else {}
        )
        session_state.set_provider_metadata("raw_finish_meta", finish_metadata)
        return exchange

    def _log_system_message(self, message: str, markdown_logger: MarkdownLogger) -> None:
        try:
            markdown_logger.log_system_message(message)
        except Exception:
            pass

    def _append_md_transcript(self, message: str) -> None:
        if not getattr(self.logger_v2, "run_dir", None):
            return
        try:
            self.logger_v2.append_text("conversation/conversation.md", self.md_writer.system(message))
        except Exception:
            pass
