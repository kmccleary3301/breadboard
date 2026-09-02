from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
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

    def __init__(
        self, oracle_path: Path | None, profile: OpenAICompletionsProviderProfile
    ) -> None:
        super().__init__(_descriptor(profile))
        self.oracle_path = oracle_path
        self.profile = profile
        self.held_out: bytes | None = None

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
        for field_name in (
            "temperature",
            "top_p",
            "seed",
            "frequency_penalty",
            "presence_penalty",
        ):
            value = getattr(self.profile.sampling, field_name)
            if value is not None:
                wire[field_name] = value
        self.held_out = canonical_json(wire).encode("utf-8")
        if self.oracle_path is not None:
            self.oracle_path.write_bytes(self.held_out)
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


W24_RUN_SET: tuple[dict[str, object], ...] = (
    {
        "case": "W1.FT-01.case-a-authority-replacement-before-submit",
        "session_id": "w24-session-01-yolo",
        "approval_mode": "yolo",
        "mode_name": "ft01_authority_replacement",
        "provider_before": "cli_mock/reference",
        "provider_after": "cli_mock/reference",
    },
    {
        "case": "W1.FT-01.case-b-permission-after-dispatch",
        "session_id": "w24-session-02-write",
        "approval_mode": "write",
        "mode_name": "ft01_permission_settlement",
        "provider_before": "cli_mock/reference",
        "provider_after": "cli_mock/reference",
    },
    {
        "case": "W3.FT-02.invalid-edit-in-flight",
        "session_id": "w24-session-03-yolo",
        "approval_mode": "yolo",
        "mode_name": "ft02_invalid_inflight_edit",
        "provider_before": "cli_mock/reference",
        "provider_after": "cli_mock/reference",
    },
    {
        "case": "W3.FT-02.reconfigure-mid-turn",
        "session_id": "w24-session-04-write",
        "approval_mode": "write",
        "mode_name": "ft02_mid_turn_reconfigure",
        "provider_before": "cli_mock/reference",
        "provider_after": "cli_mock/reference",
    },
    {
        "case": "W3.FT-02.reconfigure-across-restart",
        "session_id": "w24-session-05-yolo",
        "approval_mode": "yolo",
        "mode_name": "ft02_restart_durability",
        "provider_before": "cli_mock/reference",
        "provider_after": "cli_mock/reference",
    },
    {
        "case": "W3.FT-02.provider-swap-between-turns",
        "session_id": "w24-session-06-write",
        "approval_mode": "write",
        "mode_name": "ft02_provider_swap",
        "provider_before": "mock/provider-a",
        "provider_after": "mock/provider-b",
    },
)


def _ablation_records(
    fixture: dict[str, object], run_case: dict[str, object]
) -> dict[str, object]:
    records = copy.deepcopy(fixture)
    session = records["session"]
    session["session_id"] = run_case["session_id"]
    session["approval_mode"] = run_case["approval_mode"]
    prompt = records["prompt"]
    prompt["mode_name"] = run_case["mode_name"]
    prompt["config"]["modes"] = [
        {
            "name": run_case["mode_name"],
            "prompt": f"[ablation:{run_case['case']}]\nMode-specific section.",
        }
    ]
    records["ablation"] = dict(run_case)
    return records


def test_w24_run_set_has_six_sessions_two_modes_and_one_swap() -> None:
    assert len(W24_RUN_SET) == 6
    assert len({case["session_id"] for case in W24_RUN_SET}) == 6
    assert {case["approval_mode"] for case in W24_RUN_SET} == {"yolo", "write"}
    assert sum(
        case["provider_before"] != case["provider_after"] for case in W24_RUN_SET
    ) == 1
    assert len(W24_RUN_SET) + sum(
        case["provider_before"] != case["provider_after"] for case in W24_RUN_SET
    ) == 7


@pytest.mark.parametrize(
    "run_case",
    W24_RUN_SET,
    ids=[str(case["case"]) for case in W24_RUN_SET],
)
def test_records_only_ablation_reconstructs_each_request(
    tmp_path: Path, run_case: dict[str, object]
) -> None:
    fixture = _ablation_records(_load_fixture(), run_case)
    provider_swap = run_case["provider_before"] != run_case["provider_after"]
    routes = [run_case["provider_after"]]
    if provider_swap:
        routes = [run_case["provider_before"], run_case["provider_after"]]

    reconstructed_count = 0
    held_out_by_route: dict[str, bytes] = {}
    profile_identity_by_route: dict[str, str] = {}
    for route_index, route in enumerate(routes):
        route_fixture = copy.deepcopy(fixture)
        profile_raw = copy.deepcopy(route_fixture["lock"]["provider_profile"])
        if provider_swap:
            if route == run_case["provider_before"]:
                profile_raw["base_url"] = "http://127.0.0.1:8111/v1"
                profile_raw["sampling"] = {"temperature": 0.1}
            else:
                profile_raw["base_url"] = "http://127.0.0.1:8222/v1"
                profile_raw["sampling"] = {"temperature": 0.2}
        route_fixture["lock"]["provider_profile"] = profile_raw
        route_fixture["ablation"]["provider_route"] = route

        dispatch_compiler = SystemPromptCompiler(
            cache_dir=str(tmp_path / f"dispatch-compiler-cache-{route_index}")
        )
        dispatch_messages, dispatch_tools = _fixture_messages(
            route_fixture, dispatch_compiler
        )
        profile = _profile(profile_raw)
        runtime = _HeldOutRuntime(None, profile)
        state = SessionState(workspace=".", image="w24", config={})
        state.set_provider_metadata(
            "session_id", f"{run_case['session_id']}-{route_index}"
        )
        state.set_provider_metadata(
            "input_id", f"{run_case['session_id']}-{route_index}-input"
        )
        state.set_provider_metadata(
            "turn_id", f"{run_case['session_id']}-{route_index}-turn"
        )
        for message in route_fixture["session"]["history"]:
            state.add_message(message, to_provider=False)

        _invoker().invoke(
            runtime=runtime,
            client=object(),
            model=profile.model,
            send_messages=dispatch_messages,
            tools_schema=dispatch_tools,
            stream_responses=True,
            runtime_context=ProviderRuntimeContext(
                session_state=state,
                agent_config={},
                stream=True,
                provider_profile=profile,
            ),
            session_state=state,
            markdown_logger=Mock(),
            turn_index=1,
            route_id=route,
        )
        held_out_bytes = runtime.held_out
        assert held_out_bytes is not None
        held_out_by_route[route] = held_out_bytes
        profile_identity_by_route[route] = profile.identity_json()

        reconstruct_compiler = SystemPromptCompiler(
            cache_dir=str(tmp_path / f"reconstruct-compiler-cache-{route_index}")
        )
        reconstruct_messages, reconstruct_tools = _fixture_messages(
            route_fixture, reconstruct_compiler
        )
        reconstructed = OpenAIChatRuntime(
            _descriptor(profile)
        ).profile_chat_request(
            profile,
            reconstruct_messages,
            reconstruct_tools,
            context=ProviderRuntimeContext(
                session_state=SimpleNamespace(),
                agent_config={},
                stream=True,
                provider_profile=profile,
            ),
        )
        assert held_out_bytes == canonical_json(reconstructed).encode("utf-8")
        runtime.held_out = None
        reconstructed_count += 1

    assert reconstructed_count == (2 if provider_swap else 1)
    if provider_swap:
        before_route = run_case["provider_before"]
        after_route = run_case["provider_after"]
        assert profile_identity_by_route[before_route] != profile_identity_by_route[after_route]
        assert held_out_by_route[before_route] != held_out_by_route[after_route]


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
