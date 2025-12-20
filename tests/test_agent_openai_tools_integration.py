import json
import os
import sys
import types

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
TOOLS_ROOT = os.path.join(PROJECT_ROOT, "tool_calling")
if TOOLS_ROOT not in sys.path:
    sys.path.insert(0, TOOLS_ROOT)

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.provider_capability_probe import ProviderCapabilityProbeRunner
from agentic_coder_prototype.provider_health import RouteHealthManager
from agentic_coder_prototype.provider_runtime import (
    ProviderRuntimeContext,
    ProviderRuntimeError,
    ProviderResult,
    ProviderMessage,
)
from agentic_coder_prototype.provider_routing import ProviderDescriptor
from agentic_coder_prototype.state.session_state import SessionState
from agentic_coder_prototype.provider_metrics import ProviderMetricsCollector


def test_provider_schema_names_roundtrip():
    """Ensure YAML -> OpenAI tools schema preserves tool names we expect to execute."""
    import importlib.util

    yaml_loader_spec = importlib.util.spec_from_file_location(
        "tool_calling.tool_yaml_loader",
        os.path.join(PROJECT_ROOT, "tool_calling", "tool_yaml_loader.py"),
    )
    yaml_loader_module = importlib.util.module_from_spec(yaml_loader_spec)
    yaml_loader_spec.loader.exec_module(yaml_loader_module)

    provider_schema_spec = importlib.util.spec_from_file_location(
        "tool_calling.provider_schema",
        os.path.join(PROJECT_ROOT, "tool_calling", "provider_schema.py"),
    )
    provider_schema_module = importlib.util.module_from_spec(provider_schema_spec)
    provider_schema_spec.loader.exec_module(provider_schema_module)

    load_yaml_tools = yaml_loader_module.load_yaml_tools
    build_openai_tools_schema_from_yaml = (
        provider_schema_module.build_openai_tools_schema_from_yaml
    )

    loaded = load_yaml_tools("implementations/tools/defs")
    tools_schema = build_openai_tools_schema_from_yaml(loaded.tools)
    names = [t["function"]["name"] for t in tools_schema]
    # sanity
    assert "run_shell" in names
    assert "read_file" in names


class _DummyMarkdownLogger:
    def __init__(self):
        self.messages = []

    def log_system_message(self, message: str) -> None:
        self.messages.append(message)


def _make_conductor_for_retry(config=None):
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config or {}
    inst.logger_v2 = types.SimpleNamespace(
        run_dir=None,
        append_text=lambda *args, **kwargs: None,
        write_json=lambda *args, **kwargs: None,
        include_structured_requests=True,
    )
    inst.md_writer = types.SimpleNamespace(system=lambda text: text)
    inst._apply_streaming_policy_for_turn = lambda *args, **kwargs: (False, None)
    inst.api_recorder = types.SimpleNamespace(save_request=lambda *args, **kwargs: None, save_response=lambda *args, **kwargs: None)
    inst.structured_request_recorder = types.SimpleNamespace(record_request=lambda *args, **kwargs: None)
    inst.prompt_logger = types.SimpleNamespace(save_per_turn=lambda *args, **kwargs: None)
    inst.provider_logger = types.SimpleNamespace(save_tools_provided=lambda *args, **kwargs: None)
    inst.enhanced_executor = None
    inst._initialize_dialect_manager = lambda: None
    inst._initialize_yaml_tools = lambda: None
    inst._initialize_config_validator = lambda: None
    inst._initialize_enhanced_executor = lambda: None
    inst.workspace = "."
    inst.image = "test"
    inst.route_health = RouteHealthManager()
    inst._update_health_metadata = lambda *args, **kwargs: None
    inst.provider_metrics = ProviderMetricsCollector()
    inst.capability_probe_runner = ProviderCapabilityProbeRunner(
        provider_router=None,
        provider_registry=None,
        logger_v2=types.SimpleNamespace(run_dir=None, append_text=lambda *args, **kwargs: None),
        markdown_logger=None,
    )
    inst._capability_probes_ran = True
    return inst


def _make_conductor_for_policies(config=None):
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config or {}
    inst.logger_v2 = types.SimpleNamespace(
        run_dir=None,
        append_text=lambda *args, **kwargs: None,
        write_json=lambda *args, **kwargs: None,
        include_structured_requests=True,
    )
    inst.md_writer = types.SimpleNamespace(system=lambda text: text)
    inst.api_recorder = types.SimpleNamespace(save_request=lambda *args, **kwargs: None, save_response=lambda *args, **kwargs: None)
    inst.structured_request_recorder = types.SimpleNamespace(record_request=lambda *args, **kwargs: None)
    inst.prompt_logger = types.SimpleNamespace(save_per_turn=lambda *args, **kwargs: None)
    inst.provider_logger = types.SimpleNamespace(save_tools_provided=lambda *args, **kwargs: None)
    inst.enhanced_executor = None
    inst._initialize_dialect_manager = lambda: None
    inst._initialize_yaml_tools = lambda: None
    inst._initialize_config_validator = lambda: None
    inst._initialize_enhanced_executor = lambda: None
    inst.workspace = "."
    inst.image = "test"
    inst.route_health = RouteHealthManager()
    inst._update_health_metadata = lambda *args, **kwargs: None
    inst.provider_metrics = ProviderMetricsCollector()
    inst.capability_probe_runner = ProviderCapabilityProbeRunner(
        provider_router=None,
        provider_registry=None,
        logger_v2=types.SimpleNamespace(run_dir=None, append_text=lambda *args, **kwargs: None),
        markdown_logger=None,
    )
    inst._capability_probes_ran = True
    inst.current_native_tools = []
    inst.current_text_based_tools = []
    return inst


def test_retry_with_fallback_marks_degraded(monkeypatch):
    conductor = _make_conductor_for_retry()
    markdown_logger = _DummyMarkdownLogger()
    session_state = SessionState(workspace=".", image="img")
    session_state.set_provider_metadata("current_turn_index", 2)
    session_state.messages.append({
        "role": "assistant",
        "tool_calls": [{"function": {"name": "run_shell"}}],
    })

    class PrimaryRuntime:
        def __init__(self):
            self.descriptor = types.SimpleNamespace(provider_id="openrouter", runtime_id="openrouter_chat")
            self.calls = 0

        def invoke(self, *, client, model, messages, tools, stream, context):
            self.calls += 1
            raise ProviderRuntimeError("primary failure")

    class FallbackRuntime:
        def __init__(self):
            self.descriptor = ProviderDescriptor(
                provider_id="openai",
                runtime_id="openai_chat",
                default_api_variant="chat",
                supports_native_tools=True,
                supports_streaming=True,
                supports_reasoning_traces=True,
                supports_cache_control=False,
                tool_schema_format="openai",
                base_url=None,
                api_key_env="OPENAI_API_KEY",
                default_headers={},
            )

        def create_client(self, api_key, *, base_url=None, default_headers=None):
            return object()

        def invoke(self, *, client, model, messages, tools, stream, context):
            return ProviderResult(
                messages=[ProviderMessage(role="assistant", content="fallback", tool_calls=[])],
                raw_response={"id": "fallback"},
                usage={},
                metadata={"model": model},
            )

    primary_runtime = PrimaryRuntime()
    fallback_runtime = FallbackRuntime()

    class StubRouter:
        def __init__(self):
            self.primary_descriptor = ProviderDescriptor(
                provider_id="openrouter",
                runtime_id="openrouter_chat",
                default_api_variant="chat",
                supports_native_tools=True,
                supports_streaming=True,
                supports_reasoning_traces=True,
                supports_cache_control=False,
                tool_schema_format="openai",
                base_url=None,
                api_key_env="OPENROUTER_API_KEY",
                default_headers={},
            )
            self.fallback_descriptor = fallback_runtime.descriptor

        def get_provider_config(self, model):
            if model.startswith("openrouter/"):
                config = types.SimpleNamespace(
                    provider_id="openrouter",
                    base_url=None,
                    api_key_env="OPENROUTER_API_KEY",
                    default_headers={},
                    runtime_id="openrouter_chat",
                    default_api_variant="chat",
                    supports_native_tools=True,
                    supports_streaming=True,
                    supports_reasoning_traces=True,
                    supports_cache_control=False,
                )
                return config, "openai/gpt-4o-mini", True
            config = types.SimpleNamespace(
                provider_id="openai",
                base_url=None,
                api_key_env="OPENAI_API_KEY",
                default_headers={},
                runtime_id="openai_chat",
                default_api_variant="chat",
                supports_native_tools=True,
                supports_streaming=True,
                supports_reasoning_traces=True,
                supports_cache_control=False,
            )
            return config, model.split("/")[-1], True

        def get_runtime_descriptor(self, model):
            if model.startswith("openrouter/"):
                return self.primary_descriptor, "openai/gpt-4o-mini"
            return self.fallback_descriptor, model.split("/")[-1]

        def create_client_config(self, model):
            return {"api_key": "dummy", "base_url": None, "default_headers": {}}

    class StubRegistry:
        def create_runtime(self, descriptor):
            if descriptor.provider_id == "openai":
                return fallback_runtime
            return primary_runtime

    stub_router = StubRouter()
    stub_registry = StubRegistry()

    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.provider_router", stub_router)
    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.provider_registry", stub_registry)
    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.time.sleep", lambda _: None)
    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.random.uniform", lambda a, b: 0.0)

    runtime_context = ProviderRuntimeContext(
        session_state=session_state,
        agent_config={},
        stream=False,
    )

    result = conductor._retry_with_fallback(
        runtime=primary_runtime,
        client=object(),
        model="openrouter/openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        tools_schema=[{"type": "function", "function": {"name": "run_shell"}}],
        runtime_context=runtime_context,
        stream_responses=False,
        session_state=session_state,
        markdown_logger=markdown_logger,
        attempted=[("openrouter/openai/gpt-4o-mini", False, "primary failure")],
        last_error=ProviderRuntimeError("primary failure"),
    )

    assert result.messages[0].content == "fallback"
    degraded = session_state.get_provider_metadata("degraded_routes")
    assert "openrouter/openai/gpt-4o-mini" in degraded
    assert degraded["openrouter/openai/gpt-4o-mini"]["reason"]
    fallback_meta = session_state.get_provider_metadata("fallback_route")
    assert fallback_meta["from"] == "openrouter/openai/gpt-4o-mini"
    assert fallback_meta["to"] == "openai/gpt-4o-mini"
    assert markdown_logger.messages


def test_streaming_policy_disables_after_capability_probe(monkeypatch):
    config = {
        "providers": {
            "models": [
                {
                    "id": "openrouter/x-ai/grok-4-fast",
                    "routing": {
                        "disable_stream_on_probe_failure": True,
                        "disable_native_tools_on_probe_failure": True,
                    },
                }
            ]
        }
    }
    conductor = _make_conductor_for_policies(config)
    conductor._current_route_id = "openrouter/x-ai/grok-4-fast"
    runtime = types.SimpleNamespace(
        descriptor=types.SimpleNamespace(provider_id="openrouter", runtime_id="openrouter_chat")
    )
    session_state = SessionState(workspace=".", image="img")
    session_state.set_provider_metadata(
        "capability_probes",
        [
            {
                "model_id": "openrouter/x-ai/grok-4-fast",
                "attempted": True,
                "stream_success": False,
                "tool_stream_success": True,
                "json_mode_success": True,
            }
        ],
    )
    markdown_logger = _DummyMarkdownLogger()
    effective_stream, policy = conductor._apply_streaming_policy_for_turn(
        runtime,
        model="openai/grok-4-fast",
        tools_schema=None,
        stream_requested=True,
        session_state=session_state,
        markdown_logger=markdown_logger,
        turn_index=1,
    )
    assert effective_stream is False
    assert policy is not None
    history = session_state.get_provider_metadata("stream_policy_history")
    assert isinstance(history, list) and history[-1]["stream_effective"] is False


def test_streaming_policy_respects_disable_override(monkeypatch):
    config = {
        "providers": {
            "models": [
                {
                    "id": "openrouter/x-ai/grok-4-fast",
                    "routing": {
                        "disable_stream_on_probe_failure": False,
                    },
                }
            ]
        }
    }
    conductor = _make_conductor_for_policies(config)
    conductor._current_route_id = "openrouter/x-ai/grok-4-fast"
    runtime = types.SimpleNamespace(
        descriptor=types.SimpleNamespace(provider_id="openrouter", runtime_id="openrouter_chat")
    )
    session_state = SessionState(workspace=".", image="img")
    session_state.set_provider_metadata(
        "capability_probes",
        [
            {
                "model_id": "openrouter/x-ai/grok-4-fast",
                "attempted": True,
                "stream_success": False,
                "tool_stream_success": True,
                "json_mode_success": True,
            }
        ],
    )
    markdown_logger = _DummyMarkdownLogger()
    effective_stream, policy = conductor._apply_streaming_policy_for_turn(
        runtime,
        model="openai/grok-4-fast",
        tools_schema=None,
        stream_requested=True,
        session_state=session_state,
        markdown_logger=markdown_logger,
        turn_index=2,
    )
    assert effective_stream is True
    assert policy is None
    history = session_state.get_provider_metadata("stream_policy_history")
    assert history in (None, [])


def test_capability_tool_policy_disables_native_tools(monkeypatch):
    config = {
        "providers": {
            "models": [
                {
                    "id": "openrouter/x-ai/grok-4-fast",
                    "routing": {
                        "disable_native_tools_on_probe_failure": True,
                    },
                }
            ]
        }
    }
    conductor = _make_conductor_for_policies(config)
    conductor._current_route_id = "openrouter/x-ai/grok-4-fast"
    session_state = SessionState(workspace=".", image="img")
    session_state.set_provider_metadata(
        "capability_probes",
        [
            {
                "model_id": "openrouter/x-ai/grok-4-fast",
                "attempted": True,
                "stream_success": True,
                "tool_stream_success": False,
                "json_mode_success": True,
            }
        ],
    )
    markdown_logger = _DummyMarkdownLogger()
    cfg = {"use_native": None}
    updated = conductor._apply_capability_tool_overrides(cfg, session_state, markdown_logger)
    assert updated["use_native"] is False
    history = session_state.get_provider_metadata("tool_policy_history")
    assert isinstance(history, list) and history[-1]["reason"] == "capability_probe_tool_failure"


def test_select_fallback_route_skips_circuit_open(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    conductor = _make_conductor_for_policies({})
    for _ in range(RouteHealthManager.FAILURE_THRESHOLD):
        conductor.route_health.record_failure("openrouter/openai/gpt-4o-mini", "error")
    selected, diagnostics = conductor._select_fallback_route(
        "openrouter/x-ai/grok-4-fast",
        "openrouter",
        "openai/grok-4-fast",
        ["openrouter/openai/gpt-4o-mini", "gpt-4o-mini"],
    )
    assert selected == "gpt-4o-mini"
    assert any(item["route"] == "openrouter/openai/gpt-4o-mini" and item["reason"] == "circuit_open" for item in diagnostics["skipped"])
