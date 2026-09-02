from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from breadboard_engine.compilation.system_prompt_compiler import SystemPromptCompiler
from breadboard_engine.core.core import ToolDefinition, ToolParameter
from breadboard_engine.provider.contract_messages import (
    ProviderMessage,
    ProviderRequest,
    ProviderResult,
)
from breadboard_engine.provider.contract_runtime import (
    ProviderDescriptor,
    ProviderRuntime,
    ProviderRuntimeContext,
)
from breadboard_engine.provider.contract_wire import canonical_json
from breadboard_engine.provider.invoker import ProviderInvoker
from breadboard_engine.provider.profiles import OpenAICompletionsProviderProfile
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
from breadboard_engine.state.session_state import SessionState


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ROOT / "docs_tmp/bb_direction_assessment/dsh_donor_impl/raw/FT-03/fixture.json"


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
    # This is a non-secret placeholder accepted only to satisfy the profile value
    # object; it is never written, passed to a client, or used by reconstruction.
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


def _canonical_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8")


class _CaptureRuntime(ProviderRuntime):
    """Fake adapter: held-out bytes are written only inside dispatch."""

    def __init__(self, capture_path: Path, profile: OpenAICompletionsProviderProfile) -> None:
        descriptor = ProviderDescriptor(
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
        super().__init__(descriptor)
        self.capture_path = capture_path
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
        adapter = OpenAIChatRuntime(self.descriptor)
        wire = self.profile.chat_request(
            adapter._convert_messages_to_chat(messages, context=context),
            adapter._convert_tools_to_openai(tools),
        )
        # This is the independent oracle's sole write, after dispatch entered the adapter.
        self.capture_path.parent.mkdir(parents=True, exist_ok=True)
        self.capture_path.write_bytes(_canonical_bytes(wire))
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


def _dispatch_fixture(
    fixture: dict[str, object],
    compiler: SystemPromptCompiler,
    capture_path: Path,
) -> tuple[list[dict[str, object]], dict[str, object], OpenAICompletionsProviderProfile]:
    session = fixture["session"]
    prompt = fixture["prompt"]
    raw_tools = fixture["tools"]
    definitions = _tool_definitions(raw_tools)
    compiled = compiler.compile_v2_prompts(
        prompt["config"],
        mode_name=prompt["mode_name"],
        tools=definitions,
        dialects=prompt["dialects"],
    )
    messages = [
        {"role": "system", "content": compiled["system"]},
        {"role": "developer", "content": compiled["per_turn"]},
        *session["history"],
    ]
    profile = _profile(fixture["lock"]["provider_profile"])
    records: list[dict[str, object]] = []
    state = SessionState(
        workspace=".",
        image="ft03",
        config={},
        event_emitter=lambda event_type, payload, turn: records.append(
            {"type": event_type, "payload": payload, "turn": turn}
        ),
    )
    state.set_provider_metadata("session_id", session["session_id"])
    state.set_provider_metadata("input_id", "ft03-input-01")
    state.set_provider_metadata("turn_id", "ft03-turn-01")
    for message in session["history"]:
        state.add_message(message, to_provider=False)
    runtime_context = ProviderRuntimeContext(
        session_state=state,
        agent_config={},
        stream=True,
        provider_profile=profile,
    )
    runtime = _CaptureRuntime(capture_path, profile)
    invoker = ProviderInvoker(
        provider_metrics=Mock(),
        route_health=Mock(is_circuit_open=Mock(return_value=False)),
        logger_v2=SimpleNamespace(run_dir=None),
        md_writer=SimpleNamespace(system=lambda message: message),
        retry_with_fallback=Mock(return_value=None),
        update_health_metadata=Mock(),
        set_last_latency=Mock(),
        set_html_detected=Mock(),
    )
    invoker.invoke(
        runtime=runtime,
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
    allowed = {
        "session_events": records,
        "lock": fixture["lock"],
        "prompt": prompt,
        "tools": raw_tools,
        "provider_identity": {
            "provider_id": profile.provider_id,
            "runtime_id": profile.runtime_id,
            "route_id": "openai/ft03",
            "model": profile.model,
        },
    }
    return records, allowed, profile


def _reconstruct_from_records(
    facts: dict[str, object], compiler: SystemPromptCompiler
) -> bytes:
    """Rebuild the wire value without exchange/request/capture inputs."""
    prompt = facts["prompt"]
    raw_tools = facts["tools"]
    definitions = _tool_definitions(raw_tools)
    compiled = compiler.compile_v2_prompts(
        prompt["config"],
        mode_name=prompt["mode_name"],
        tools=definitions,
        dialects=prompt["dialects"],
    )
    messages: list[dict[str, object]] = [
        {"role": "system", "content": compiled["system"]},
        {"role": "developer", "content": compiled["per_turn"]},
    ]
    for event in facts["session_events"]:
        if event["type"] in {"user_message", "assistant_message", "tool_result"}:
            messages.append(dict(event["payload"]["message"]))
    profile = facts["lock"]["provider_profile"]
    tools: list[dict[str, object]] = []
    for tool in raw_tools:
        function = dict(tool["function"])
        # Existing profile adapter rule: strict is effective false on the wire.
        function["strict"] = False
        tools.append({"type": "function", "function": function})
    # Existing profile defaults: omitted sampling.n materializes to 1.
    wire = {
        "model": profile["model"],
        "messages": [
            {"role": message["role"], "content": message.get("content", "")}
            for message in messages
        ],
        "tools": tools,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": profile["max_output_tokens"],
        "n": (profile.get("sampling") or {}).get("n", 1),
        "enable_thinking": False,
    }
    return _canonical_bytes(wire)


def run_characterization(evidence_dir: Path) -> None:
    fixture = _load_fixture()
    capture_path = evidence_dir / "capture/request.json"
    reconstruction_path = evidence_dir / "reconstruction/request.json"
    compiler = SystemPromptCompiler(cache_dir=str(evidence_dir / "compiler-cache"))
    _records, facts, _profile_value = _dispatch_fixture(fixture, compiler, capture_path)
    reconstructed = _reconstruct_from_records(facts, compiler)
    reconstruction_path.parent.mkdir(parents=True, exist_ok=True)
    reconstruction_path.write_bytes(reconstructed)
    captured = capture_path.read_bytes()
    assert hashlib.sha256(captured).hexdigest() == hashlib.sha256(reconstructed).hexdigest()
    assert captured == reconstructed


if __name__ == "__main__":
    run_characterization(Path(os.environ["FT03_EVIDENCE_DIR"]))
