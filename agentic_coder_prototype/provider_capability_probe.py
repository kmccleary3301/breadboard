from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from .provider_runtime import (
    ProviderRuntimeError,
    ProviderResult,
    ProviderMessage,
    ProviderRuntimeContext,
)


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
    ) -> None:
        self.provider_router = provider_router
        self.provider_registry = provider_registry
        self.logger_v2 = logger_v2
        self.markdown_logger = markdown_logger

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
            result = self._probe_model(model_id)
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

    def _probe_model(self, model_id: str) -> CapabilityProbeResult:
        start = time.time()
        descriptor, runtime_model = self.provider_router.get_runtime_descriptor(model_id)
        client_cfg = self.provider_router.create_client_config(model_id)
        api_key = client_cfg.get("api_key")
        if not api_key:
            return CapabilityProbeResult(
                model_id=model_id,
                attempted=False,
                skipped_reason="missing_api_key",
                elapsed=0.0,
            )

        try:
            runtime = self.provider_registry.create_runtime(descriptor)
        except Exception as exc:
            return CapabilityProbeResult(
                model_id=model_id,
                attempted=False,
                skipped_reason="runtime_init_failed",
                error=str(exc),
                elapsed=time.time() - start,
            )

        try:
            client = runtime.create_client(
                api_key,
                base_url=client_cfg.get("base_url"),
                default_headers=client_cfg.get("default_headers"),
            )
        except Exception as exc:
            return CapabilityProbeResult(
                model_id=model_id,
                attempted=False,
                skipped_reason="client_init_failed",
                error=str(exc),
                elapsed=time.time() - start,
            )

        messages = [
            {"role": "system", "content": "You are a diagnostic probe. Reply concisely."},
            {"role": "user", "content": "Respond with the single word PING."},
        ]

        tools_schema = None
        context = None
        stream_success: Optional[bool] = None
        tool_stream_success: Optional[bool] = None
        json_mode_success: Optional[bool] = None
        latest_error: Optional[str] = None

        class _StubSessionState:
            def get_provider_metadata(self, key: str, default=None):
                return default

        context = ProviderRuntimeContext(
            session_state=_StubSessionState(),
            agent_config={},
            stream=True,
            extra={},
        )

        # Streaming probe
        try:
            runtime.invoke(
                client=client,
                model=runtime_model,
                messages=messages,
                tools=tools_schema,
                stream=True,
                context=context,
            )
            stream_success = True
        except ProviderRuntimeError as exc:
            stream_success = False
            latest_error = str(exc)
        except Exception as exc:
            stream_success = False
            latest_error = str(exc)

        # Non-streaming tool probe (if tools_schema supported)
        try:
            result = runtime.invoke(
                client=client,
                model=runtime_model,
                messages=messages,
                tools=tools_schema,
                stream=False,
                context=context,
            )
            tool_stream_success = self._detect_tool_message(result)
        except ProviderRuntimeError as exc:
            tool_stream_success = False
            latest_error = str(exc)
        except Exception as exc:
            tool_stream_success = False
            latest_error = str(exc)

        # JSON mode probe (if supported and streaming succeeded)
        try:
            json_messages = [
                {"role": "system", "content": "Respond with a JSON object with field pong=\"yes\"."},
                {"role": "user", "content": "Return the JSON now."},
            ]
            json_result = runtime.invoke(
                client=client,
                model=runtime_model,
                messages=json_messages,
                tools=None,
                stream=False,
                context=context,
            )
            json_mode_success = self._looks_like_json(json_result)
        except ProviderRuntimeError as exc:
            json_mode_success = False
            latest_error = str(exc)
        except Exception as exc:
            json_mode_success = False
            latest_error = str(exc)

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
