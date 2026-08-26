from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


from .contracts import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)

class _MissingCapabilityProbeCredential(Exception):
    pass


@dataclass
class CapabilityProbeResult:
    model_id: str
    attempted: bool
    stream_success: Optional[bool] = None
    tool_stream_success: Optional[bool] = None
    json_mode_success: Optional[bool] = None
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    elapsed: float = 0.0


class ProviderCapabilityProbeRunner:
    """Runs lightweight capability probes for provider routes."""

    def __init__(
        self,
        provider_router,
        provider_registry,
        logger_v2,
        markdown_logger,
        provider_invoker=None,
    ) -> None:
        self.provider_router = provider_router
        self.provider_registry = provider_registry
        self.logger_v2 = logger_v2
        self.markdown_logger = markdown_logger
        self.provider_invoker = provider_invoker

    def _log(self, message: str) -> None:
        try:
            if self.markdown_logger:
                self.markdown_logger.log_system_message(message)
        except Exception:
            pass
        try:
            if getattr(self.logger_v2, "run_dir", None):
                self.logger_v2.append_text(
                    "conversation/conversation.md",
                    message + "\n",
                )
        except Exception:
            pass

    def _should_run(self, config: Dict[str, Any]) -> bool:
        probe_cfg = (config.get("provider_probes") or {})
        if not probe_cfg:
            return False
        if not probe_cfg.get("enabled", False):
            return False
        env_override = os.getenv("KC_DISABLE_PROVIDER_PROBES")
        if env_override and env_override.lower() in {"1", "true", "yes"}:
            return False
        return True

    def run(
        self,
        config: Dict[str, Any],
        session_state,
    ) -> List[CapabilityProbeResult]:
        if not self._should_run(config):
            return []

        providers_cfg = config.get("providers") or {}
        raw_models = providers_cfg.get("models") or []
        model_ids: List[str] = []
        for item in raw_models:
            model_id = item.get("id")
            if model_id:
                model_ids.append(model_id)
        default_model = providers_cfg.get("default_model")
        if default_model:
            model_ids.append(default_model)
        unique_models = []
        seen = set()
        for mid in model_ids:
            if mid not in seen:
                unique_models.append(mid)
                seen.add(mid)

        results: List[CapabilityProbeResult] = []
        for model_id in unique_models:
            result = self._probe_model(model_id, session_state)
            results.append(result)

        if getattr(self.logger_v2, "run_dir", None):
            try:
                payload = [asdict(r) for r in results]
                self.logger_v2.write_json("meta/capability_probes.json", payload)
            except Exception:
                pass

        try:
            session_state.set_provider_metadata("capability_probes", [asdict(r) for r in results])
        except Exception:
            pass
        return results

    def _probe_model(self, model_id: str, session_state) -> CapabilityProbeResult:
        start = time.time()
        descriptor, runtime_model = self.provider_router.get_runtime_descriptor(model_id)
        try:
            runtime = self.provider_registry.create_runtime(descriptor)
        except Exception:
            return CapabilityProbeResult(
                model_id=model_id,
                attempted=False,
                skipped_reason="runtime_init_failed",
                error="capability_probe_init_error",
                elapsed=time.time() - start,
            )

        def _invoke(*, probe_kind: str, **kwargs):
            if self.provider_invoker is None:
                raise RuntimeError("capability probe invoker is unavailable")
            session_id = session_state.get_provider_metadata("session_id")
            input_id = session_state.get_provider_metadata("input_id")
            turn_id = session_state.get_provider_metadata("turn_id")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (session_id, input_id, turn_id)
            ):
                raise RuntimeError("capability probe correlation is unavailable")
            origin = self.provider_router.get_credential_origin(
                model_id, session_id=session_id
            )
            if origin is None:
                raise _MissingCapabilityProbeCredential
            context = ProviderRuntimeContext(
                session_state=session_state,
                agent_config={},
                stream=bool(kwargs["stream"]),
                extra={
                    "diagnostic_probe": True,
                    "probe_kind": probe_kind,
                    "session_id": session_id,
                    "input_id": input_id,
                    "turn_id": turn_id,
                },
                session_id=session_id,
                input_id=input_id,
                turn_id=turn_id,
            )
            turn_index = session_state.get_provider_metadata(
                "current_turn_index", 0
            )
            if not isinstance(turn_index, int) or isinstance(turn_index, bool):
                turn_index = 0
            result, _used_streaming = self.provider_invoker.invoke(
                runtime=runtime,
                client=None,
                model=kwargs["model"],
                send_messages=kwargs["messages"],
                tools_schema=kwargs["tools"],
                stream_responses=bool(kwargs["stream"]),
                runtime_context=context,
                session_state=session_state,
                markdown_logger=self.markdown_logger,
                turn_index=turn_index,
                route_id=model_id,
            )
            return result

        messages = [
            {"role": "system", "content": "You are a diagnostic probe. Reply concisely."},
            {"role": "user", "content": "Respond with the single word PING."},
        ]

        tools_schema = None
        stream_success: Optional[bool] = None
        tool_stream_success: Optional[bool] = None
        json_mode_success: Optional[bool] = None
        latest_error: Optional[str] = None

        # Streaming probe
        try:
            _invoke(
                probe_kind="stream",
                model=runtime_model,
                messages=messages,
                tools=tools_schema,
                stream=True,
            )
            stream_success = True
        except _MissingCapabilityProbeCredential:
            return CapabilityProbeResult(
                model_id=model_id,
                attempted=False,
                skipped_reason="missing_api_key",
                elapsed=time.time() - start,
            )
        except ProviderRuntimeError as exc:
            stream_success = False
            latest_error = exc.safe_code
        except Exception:
            stream_success = False
            latest_error = "capability_probe_error"

        # Non-streaming tool probe (if tools_schema supported)
        try:
            result = _invoke(
                probe_kind="tool",
                model=runtime_model,
                messages=messages,
                tools=tools_schema,
                stream=False,
            )
            tool_stream_success = self._detect_tool_message(result)
        except ProviderRuntimeError as exc:
            tool_stream_success = False
            latest_error = exc.safe_code
        except Exception:
            tool_stream_success = False
            latest_error = "capability_probe_error"

        # JSON mode probe (if supported and streaming succeeded)
        try:
            json_messages = [
                {"role": "system", "content": "Respond with a JSON object with field pong=\"yes\"."},
                {"role": "user", "content": "Return the JSON now."},
            ]
            json_result = _invoke(
                probe_kind="json",
                model=runtime_model,
                messages=json_messages,
                tools=None,
                stream=False,
            )
            json_mode_success = self._looks_like_json(json_result)
        except ProviderRuntimeError as exc:
            json_mode_success = False
            latest_error = exc.safe_code
        except Exception:
            json_mode_success = False
            latest_error = "capability_probe_error"

        elapsed = time.time() - start
        result = CapabilityProbeResult(
            model_id=model_id,
            attempted=True,
            stream_success=stream_success,
            tool_stream_success=tool_stream_success,
            json_mode_success=json_mode_success,
            error=latest_error,
            elapsed=elapsed,
        )
        status_parts = [
            f"model={model_id}",
            f"stream={stream_success}",
            f"tool_stream={tool_stream_success}",
            f"json={json_mode_success}",
        ]
        if latest_error:
            status_parts.append(f"error={latest_error}")
        self._log("[capability-probe] " + " ".join(status_parts))
        return result

    def _detect_tool_message(self, result: ProviderResult) -> Optional[bool]:
        if not result or not getattr(result, "messages", None):
            return None
        for msg in result.messages:
            if isinstance(msg, ProviderMessage) and msg.tool_calls:
                return True
        return False

    def _looks_like_json(self, result: ProviderResult) -> Optional[bool]:
        if not result or not getattr(result, "messages", None):
            return None
        try:
            for msg in result.messages:
                if not isinstance(msg, ProviderMessage):
                    continue
                content = msg.content or ""
                if isinstance(content, str) and content.strip().startswith("{"):
                    return True
        except Exception:
            return None
        return False
