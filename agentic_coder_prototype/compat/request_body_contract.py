from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.provider_runtime import (
    AnthropicMessagesRuntime,
    OpenAIChatRuntime,
    ProviderRuntime,
)


@dataclass(frozen=True)
class CapturedRequest:
    provider_id: str
    runtime_id: str
    payload: Dict[str, Any]


class RequestCaptureStore:
    def __init__(self) -> None:
        self._requests: List[CapturedRequest] = []

    def record(self, runtime: ProviderRuntime, payload: Dict[str, Any]) -> None:
        self._requests.append(
            CapturedRequest(
                provider_id=getattr(runtime.descriptor, "provider_id", "unknown"),
                runtime_id=getattr(runtime.descriptor, "runtime_id", "unknown"),
                payload=copy.deepcopy(payload),
            )
        )

    def last(self) -> CapturedRequest:
        if not self._requests:
            raise RuntimeError("No provider requests captured.")
        return self._requests[-1]


def serialize_request(payload: Dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return (data + "\n").encode("utf-8")


class _RawResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.http_response = None

    def parse(self) -> Any:
        return self._payload


class _WithRawResponse:
    def __init__(self, runtime: ProviderRuntime, store: RequestCaptureStore, variant: str) -> None:
        self._runtime = runtime
        self._store = store
        self._variant = variant

    def create(self, **kwargs: Any) -> _RawResponse:
        self._store.record(self._runtime, kwargs)
        response_payload = _build_response_payload(self._variant, kwargs)
        return _RawResponse(response_payload)


class _CaptureOpenAICompletions:
    def __init__(self, runtime: ProviderRuntime, store: RequestCaptureStore, variant: str) -> None:
        self.with_raw_response = _WithRawResponse(runtime, store, variant)

    def create(self, **kwargs: Any) -> Any:
        return _build_response_payload("chat", kwargs)


class _CaptureOpenAIChat:
    def __init__(self, runtime: ProviderRuntime, store: RequestCaptureStore) -> None:
        self.completions = _CaptureOpenAICompletions(runtime, store, _openai_variant(runtime))


class _CaptureOpenAIResponses:
    def __init__(self, runtime: ProviderRuntime, store: RequestCaptureStore) -> None:
        self.with_raw_response = _WithRawResponse(runtime, store, "responses")

    def create(self, **kwargs: Any) -> Any:
        return _build_response_payload("responses", kwargs)


class _CaptureOpenAIClient:
    def __init__(self, runtime: ProviderRuntime, store: RequestCaptureStore) -> None:
        self.chat = _CaptureOpenAIChat(runtime, store)
        self.responses = _CaptureOpenAIResponses(runtime, store)


class _CaptureAnthropicMessages:
    def __init__(self, runtime: ProviderRuntime, store: RequestCaptureStore) -> None:
        self.with_raw_response = _WithRawResponse(runtime, store, "anthropic")

    def create(self, **kwargs: Any) -> Any:
        return _build_response_payload("anthropic", kwargs)


class _CaptureAnthropicClient:
    def __init__(self, runtime: ProviderRuntime, store: RequestCaptureStore) -> None:
        self.messages = _CaptureAnthropicMessages(runtime, store)


def _openai_variant(runtime: ProviderRuntime) -> str:
    runtime_id = str(getattr(runtime.descriptor, "runtime_id", "") or "")
    default_variant = str(getattr(runtime.descriptor, "default_api_variant", "") or "")
    if "responses" in runtime_id or default_variant == "responses":
        return "responses"
    return "chat"


def _build_response_payload(variant: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    model = kwargs.get("model") or "mock"
    if variant == "responses":
        return {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "TASK COMPLETE"}],
                }
            ],
            "model": model,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    if variant == "anthropic":
        return {
            "content": [{"type": "text", "text": "TASK COMPLETE"}],
            "stop_reason": "stop",
            "model": model,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "TASK COMPLETE"},
                "finish_reason": "stop",
            }
        ],
        "model": model,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


@contextmanager
def _capture_provider_clients(store: RequestCaptureStore) -> Iterator[None]:
    patches: List[tuple[type, Any]] = []

    def _patch(runtime_cls: type, factory) -> None:
        original = runtime_cls.create_client

        def _create_client(self, api_key: str, *, base_url: Optional[str] = None, default_headers: Optional[Dict[str, str]] = None) -> Any:
            return factory(self, store)

        runtime_cls.create_client = _create_client  # type: ignore[assignment]
        patches.append((runtime_cls, original))

    _patch(OpenAIChatRuntime, lambda runtime, store: _CaptureOpenAIClient(runtime, store))
    _patch(AnthropicMessagesRuntime, lambda runtime, store: _CaptureAnthropicClient(runtime, store))

    try:
        yield
    finally:
        for runtime_cls, original in patches:
            runtime_cls.create_client = original  # type: ignore[assignment]


def _apply_compat_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = copy.deepcopy(config)
    cfg.setdefault("logging", {})["enabled"] = False
    cfg.setdefault("provider_probes", {})["enabled"] = False
    cfg.setdefault("features", {})["plan"] = False
    provider_tools = cfg.setdefault("provider_tools", {})
    anthropic_cfg = provider_tools.setdefault("anthropic", {})
    if isinstance(anthropic_cfg, dict):
        anthropic_cfg["stream"] = False
    return cfg


def capture_request_body(
    *,
    config_path: str,
    workspace_root: Path,
    user_prompt: str = "Hello from compat harness.",
    system_prompt: str = "",
    max_steps: int = 1,
) -> CapturedRequest:
    config = load_agent_config(config_path)
    config = _apply_compat_overrides(config)
    workspace_root.mkdir(parents=True, exist_ok=True)
    cfg_workspace = config.setdefault("workspace", {})
    cfg_workspace["root"] = str(workspace_root)

    cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = cls(
        workspace=str(workspace_root),
        config=config,
        local_mode=True,
    )

    tool_prompt_mode = ""
    prompts_cfg = config.get("prompts") if isinstance(config, dict) else None
    if isinstance(prompts_cfg, dict):
        tool_prompt_mode = str(prompts_cfg.get("tool_prompt_mode") or "")
    if not tool_prompt_mode:
        tool_prompt_mode = "system_compiled_and_persistent_per_turn"

    store = RequestCaptureStore()
    with _capture_provider_clients(store):
        conductor.run_agentic_loop(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=config.get("providers", {}).get("default_model") or "",
            max_steps=max_steps,
            stream_responses=False,
            tool_prompt_mode=tool_prompt_mode,
        )
    return store.last()


def default_request_body_cases() -> Iterable[Dict[str, Any]]:
    return [
        {
            "id": "claude_code",
            "config_path": "agent_configs/claude_code_haiku45_c_fs_v2.yaml",
        },
        {
            "id": "claude_code_system",
            "config_path": "agent_configs/claude_code_haiku45_c_fs_v2.yaml",
            "system_prompt": "System compat harness.",
        },
        {
            "id": "codex_cli",
            "config_path": "agent_configs/codex_cli_gpt51mini_e4_live.yaml",
        },
        {
            "id": "codex_cli_multiline",
            "config_path": "agent_configs/codex_cli_gpt51mini_e4_live.yaml",
            "user_prompt": "Hello from compat harness.\n\nPlease keep responses brief.",
        },
        {
            "id": "opencode",
            "config_path": "agent_configs/opencode_openai_gpt5mini_c_fs_cli_shared.yaml",
        },
        {
            "id": "opencode_multiline",
            "config_path": "agent_configs/opencode_openai_gpt5mini_c_fs_cli_shared.yaml",
            "user_prompt": "Hello from compat harness.\nSecond line.\nThird line.",
        },
    ]
