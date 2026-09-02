from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from breadboard_engine.compilation.system_prompt_compiler import SystemPromptCompiler
from breadboard_engine.core.core import ToolDefinition, ToolParameter
from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult
from breadboard_engine.provider.contract_runtime import (
    ProviderRuntime,
    ProviderRuntimeContext,
)
from breadboard_engine.provider.contract_wire import canonical_json
from breadboard_engine.provider.invoker import ProviderInvoker
from breadboard_engine.provider.profiles import OpenAICompletionsProviderProfile
from breadboard_engine.provider.routing import ProviderDescriptor
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
from breadboard_engine.state.session_state import SessionState


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "provider_request_reconstruction_ft03.json"


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _tool_definitions(raw_tools: list[dict[str, object]]) -> list[ToolDefinition]:
    definitions: list[ToolDefinition] = []
    for raw in raw_tools:
        function = raw["function"]
        parameters = function["parameters"]
        definitions.append(
            ToolDefinition(
                name=function["name"],
                description=function["description"],
                parameters=[
                    ToolParameter(
                        name=name,
                        type=spec.get("type"),
                        description=spec.get("description"),
                    )
                    for name, spec in parameters.get("properties", {}).items()
                ],
            )
        )
    return definitions


def _profile(raw: dict[str, object]) -> OpenAICompletionsProviderProfile:
    return OpenAICompletionsProviderProfile(
        model=raw["model"],
        scoped_credential="fixture-placeholder-not-a-credential",
        base_url=raw["base_url"],
        context_window=raw["context_window"],
        max_output_tokens=raw["max_output_tokens"],
        sampling=raw.get("sampling") or {},
        provider_id=raw["provider_id"],
        runtime_id=raw["runtime_id"],
        caller_headers=raw.get("caller_headers") or {},
    )


def _descriptor(profile: OpenAICompletionsProviderProfile) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id="openai",
        runtime_id="openai_chat",
        default_api_variant="chat",
        supports_native_tools=True,
        supports_streaming=True,
        supports_reasoning_traces=False,
        supports_cache_control=False,
        tool_schema_format="openai",
        base_url=profile.base_url,
        api_key_env=None,
        default_headers={},
    )


class _HeldOutRuntime(ProviderRuntime):
    """Fake adapter that writes its post-dispatch bytes only after receiving input."""

    def __init__(self, oracle_path: Path, profile: OpenAICompletionsProviderProfile) -> None:
        super().__init__(_descriptor(profile))
        self.oracle_path = oracle_path
        self.profile = profile

    def invoke(
        self,
        *,
        client: object,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None,
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        del client, model, stream, context
        effective_tools: list[dict[str, object]] = []
        for tool in tools or []:
            function = dict(tool["function"])
            function["strict"] = False
            effective_tool = dict(tool)
            effective_tool["function"] = function
            effective_tools.append(effective_tool)
        wire = {
            "model": self.profile.model,
            "messages": [dict(message) for message in messages],
            "tools": effective_tools,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self.profile.max_output_tokens,
            "n": self.profile.sampling.n,
            "enable_thinking": False,
        }
        self.oracle_path.write_bytes(canonical_json(wire).encode("utf-8"))
        return ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content="FT-03 fake adapter completed",
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            ],
            raw_response=None,
            metadata={},
        )


def _fixture_messages(
    fixture: dict[str, object], compiler: SystemPromptCompiler
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    prompt = fixture["prompt"]
    raw_tools = fixture["tools"]
    compiled = compiler.compile_v2_prompts(
        prompt["config"],
        mode_name=prompt["mode_name"],
        tools=_tool_definitions(raw_tools),
        dialects=prompt["dialects"],
    )
    session = fixture["session"]
    return (
        [
            {"role": "system", "content": compiled["system"]},
            {"role": "developer", "content": compiled["per_turn"]},
            *session["history"],
        ],
        raw_tools,
    )


def _invoker() -> ProviderInvoker:
    return ProviderInvoker(
        provider_metrics=Mock(),
        route_health=Mock(is_circuit_open=Mock(return_value=False)),
        logger_v2=SimpleNamespace(run_dir=None),
        md_writer=SimpleNamespace(system=lambda message: message),
        retry_with_fallback=Mock(return_value=None),
        update_health_metadata=Mock(),
        set_last_latency=Mock(),
        set_html_detected=Mock(),
    )


def test_ft03_reconstructs_exact_request_bytes_at_profile_interface(tmp_path: Path) -> None:
    fixture = _load_fixture()
    compiler = SystemPromptCompiler(cache_dir=str(tmp_path / "compiler-cache"))
    messages, raw_tools = _fixture_messages(fixture, compiler)
    profile = _profile(fixture["lock"]["provider_profile"])
    oracle_path = tmp_path / "held-out-provider-request.json"
    records: list[dict[str, object]] = []

    def record(event_type: str, payload: dict[str, object], turn: int | None = None) -> None:
        records.append({"type": event_type, "payload": payload, "turn": turn})

    state = SessionState(
        workspace=".", image="ft03", config={}, event_emitter=record
    )
    state.set_provider_metadata("session_id", fixture["session"]["session_id"])
    state.set_provider_metadata("input_id", "ft03-input-01")
    state.set_provider_metadata("turn_id", "ft03-turn-01")
    for message in fixture["session"]["history"]:
        state.add_message(message, to_provider=False)

    runtime_context = ProviderRuntimeContext(
        session_state=state,
        agent_config={},
        stream=True,
        provider_profile=profile,
    )
    _invoker().invoke(
        runtime=_HeldOutRuntime(oracle_path, profile),
        client=object(),
        model=profile.model,
        send_messages=messages,
        tools_schema=raw_tools,
        stream_responses=True,
        runtime_context=runtime_context,
        session_state=state,
        markdown_logger=Mock(),
        turn_index=1,
        route_id="openai/ft03",
    )

    logical_messages = [
        {"role": "system", "content": messages[0]["content"]},
        {"role": "developer", "content": messages[1]["content"]},
    ]
    logical_messages.extend(
        event["payload"]["message"]
        for event in records
        if event["type"] in {"user_message", "assistant_message", "tool_result"}
    )
    runtime = OpenAIChatRuntime(_descriptor(profile))
    reconstructed = runtime.profile_chat_request(
        profile,
        logical_messages,
        raw_tools,
        context=ProviderRuntimeContext(
            session_state=SimpleNamespace(),
            agent_config={},
            stream=True,
            provider_profile=profile,
        ),
    )

    assert oracle_path.read_bytes() == canonical_json(reconstructed).encode("utf-8")
