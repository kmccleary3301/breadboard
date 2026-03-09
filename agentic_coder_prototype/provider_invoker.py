from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from .messaging.markdown_logger import MarkdownLogger
from .provider_runtime import ProviderResult, ProviderRuntimeContext, ProviderRuntimeError
from .state.session_state import SessionState


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
    ) -> None:
        self.provider_metrics = provider_metrics
        self.route_health = route_health
        self.logger_v2 = logger_v2
        self.md_writer = md_writer
        self.retry_with_fallback = retry_with_fallback
        self.update_health_metadata = update_health_metadata
        self.set_last_latency = set_last_latency
        self.set_html_detected = set_html_detected

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
        exchange_request = self._build_exchange_request_record(
            runtime=runtime,
            model=model,
            send_messages=send_messages,
            tools_schema=tools_schema,
            stream=stream_responses,
            route_id=route_id,
            turn_index=turn_index,
        )
        try:
            session_state.set_provider_metadata("last_provider_exchange_request", exchange_request)
        except Exception:
            pass

        if self.route_health.is_circuit_open(model):
            notice = f"[circuit-open] Skipping direct call for route {model}; attempting fallback."
            self.provider_metrics.add_circuit_skip(model)
            self._log_system_message(notice, markdown_logger)
            self._append_md_transcript(notice)
            try:
                session_state.add_transcript_entry({"circuit_open": {"model": model, "notice": notice}})
            except Exception:
                pass
            circuit_error = ProviderRuntimeError("route_circuit_open")
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
            )
            self.update_health_metadata(session_state)
            if fallback_result is not None:
                return fallback_result, False
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
            try:
                call_result = runtime.invoke(
                    client=client,
                    model=target_model,
                    messages=send_messages,
                    tools=tools_schema,
                    stream=use_stream,
                    context=runtime_context,
                )
                elapsed = time.time() - start_time
                self.provider_metrics.add_call(
                    target_model,
                    stream=use_stream,
                    elapsed=elapsed,
                    outcome="success",
                )
                self.set_last_latency(elapsed)
                self.set_html_detected(False)
                return call_result
            except ProviderRuntimeError as exc:
                elapsed = time.time() - start_time
                details = getattr(exc, "details", None)
                html_detected = isinstance(details, dict) and bool(details.get("html_detected"))
                self.provider_metrics.add_call(
                    target_model,
                    stream=use_stream,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason=str(exc),
                    html_detected=html_detected,
                    details=details if isinstance(details, dict) else None,
                )
                self.set_last_latency(elapsed)
                self.set_html_detected(html_detected)
                raise

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
                session_state.add_transcript_entry({"streaming_disabled": warning_payload})
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

        if stream_responses:
            try:
                result = _call_runtime(model, True)
                attempted_models.append((model, True, None))
                self.route_health.record_success(model)
                success_recorded = True
                self.update_health_metadata(session_state)
            except ProviderRuntimeError as exc:
                fallback_stream_reason = str(exc) or exc.__class__.__name__
                last_error = exc
                attempted_models.append((model, True, fallback_stream_reason))
                self.route_health.record_failure(model, fallback_stream_reason)
                self.update_health_metadata(session_state)

        used_streaming = stream_responses and result is not None

        if result is None:
            if fallback_stream_reason and stream_responses:
                warning_payload = {
                    "provider": getattr(runtime.descriptor, "provider_id", "unknown"),
                    "runtime": getattr(runtime.descriptor, "runtime_id", "unknown"),
                    "reason": fallback_stream_reason,
                }
                self.provider_metrics.add_stream_override(route=route_id, reason=fallback_stream_reason)
                session_state.add_transcript_entry({"streaming_disabled": warning_payload})
                warning_text = (
                    "[streaming-disabled] "
                    f"Provider {warning_payload['provider']} ({warning_payload['runtime']}) "
                    f"rejected streaming: {fallback_stream_reason}. Falling back to non-streaming."
                )
                self._log_system_message(warning_text, markdown_logger)
                self._append_md_transcript(warning_text)
                try:
                    session_state.set_provider_metadata("streaming_disabled", True)
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
                attempted_models.append((model, False, str(exc) or exc.__class__.__name__))
                result = None
                self.route_health.record_failure(model, str(exc) or exc.__class__.__name__)
                self.update_health_metadata(session_state)

        if result is None and _is_tool_turn():
            history = session_state.get_provider_metadata("streaming_disabled")
            if not history:
                reason_text = str(last_error) if last_error else "tool_turn_retry"
                _maybe_disable_stream(reason_text)

        if result is None:
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
            )
            if fallback_result is not None:
                result = fallback_result
                used_streaming = False
                success_recorded = True
            elif last_error:
                raise last_error

        try:
            if result is not None:
                session_state.set_provider_metadata("raw_finish_meta", result.metadata)
                session_state.set_provider_metadata(
                    "last_provider_exchange_response",
                    self._build_exchange_response_record(exchange_request=exchange_request, result=result),
                )
        except Exception:
            pass

        if result is not None and not success_recorded:
            self.route_health.record_success(model)
            self.update_health_metadata(session_state)
        else:
            self.update_health_metadata(session_state)

        if result is None:
            raise RuntimeError("Provider invocation failed without raising last_error")
        return result, used_streaming

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

    def _build_exchange_request_record(
        self,
        *,
        runtime: Any,
        model: str,
        send_messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        stream: bool,
        route_id: Optional[str],
        turn_index: int,
    ) -> Dict[str, Any]:
        """
        Build the normalized provider-exchange request record.

        This is the kernel-facing request boundary: enough structure to compare
        engines semantically without baking transport objects into the contract.
        """

        descriptor = getattr(runtime, "descriptor", None)
        metadata = ProviderInvoker._normalize_exchange_request_metadata(
            send_messages=send_messages,
            tools_schema=tools_schema,
            route_id=route_id,
            stream=stream,
        )
        return {
            "exchange_id": f"px_{uuid.uuid4().hex}",
            "provider_family": str(getattr(descriptor, "provider_id", "unknown")),
            "runtime_id": str(getattr(descriptor, "runtime_id", "unknown")),
            "route_id": route_id,
            "model": str(model),
            "stream": bool(stream),
            "turn_index": int(turn_index),
            "message_count": len(send_messages or []),
            "tool_count": len(tools_schema or []),
            "metadata": metadata,
        }

    def _build_exchange_response_record(
        self,
        *,
        exchange_request: Dict[str, Any],
        result: ProviderResult,
    ) -> Dict[str, Any]:
        """Build the normalized provider-exchange response record."""

        finish_reasons: List[Optional[str]] = []
        for message in result.messages or []:
            finish_reasons.append(getattr(message, "finish_reason", None))
        response_metadata = ProviderInvoker._normalize_exchange_response_metadata(
            request_record=exchange_request,
            result=result,
            finish_reasons=finish_reasons,
        )
        return {
            "exchange_id": exchange_request.get("exchange_id"),
            "request": dict(exchange_request),
            "response": {
                "message_count": len(result.messages or []),
                "finish_reasons": finish_reasons,
                "usage": result.usage,
                "metadata": response_metadata,
                "evidence_refs": [],
            },
        }

    @staticmethod
    def _normalize_exchange_request_metadata(
        *,
        send_messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        route_id: Optional[str],
        stream: bool,
    ) -> Dict[str, Any]:
        message_roles: List[str] = []
        for message in send_messages or []:
            role = message.get("role")
            if role is not None:
                message_roles.append(str(role))
        tool_names: List[str] = []
        for tool in tools_schema or []:
            name = tool.get("name") or tool.get("function", {}).get("name")
            if name:
                tool_names.append(str(name))
        return {
            "message_roles": message_roles,
            "tool_names": tool_names,
            "route_selected": route_id is not None,
            "stream_requested": bool(stream),
            "transport": "provider_runtime.invoke",
        }

    @staticmethod
    def _normalize_exchange_response_metadata(
        *,
        request_record: Dict[str, Any],
        result: ProviderResult,
        finish_reasons: List[Optional[str]],
    ) -> Dict[str, Any]:
        metadata = dict(result.metadata or {})
        metadata.setdefault("finish_reason_count", len([reason for reason in finish_reasons if reason is not None]))
        metadata.setdefault("stream_requested", bool(request_record.get("stream")))
        metadata.setdefault("route_id", request_record.get("route_id"))
        metadata.setdefault("provider_family", request_record.get("provider_family"))
        metadata.setdefault("runtime_id", request_record.get("runtime_id"))
        metadata.setdefault("message_count", len(result.messages or []))
        return metadata
