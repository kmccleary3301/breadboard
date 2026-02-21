from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.provider_routing import ProviderDescriptor
from agentic_coder_prototype.provider_runtime import ProviderMessage, ProviderResult, ProviderRuntimeError


def _make_conductor(config: dict, workspace: Path) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst.workspace = str(workspace)
    inst._active_session_state = None
    return inst  # type: ignore[return-value]


class _StubRouter:
    def __init__(self) -> None:
        self._descriptor = ProviderDescriptor(
            provider_id="stub",
            runtime_id="stub_runtime",
            default_api_variant="chat",
            supports_native_tools=False,
            supports_streaming=False,
            supports_reasoning_traces=False,
            supports_cache_control=False,
            tool_schema_format="openai",
            base_url=None,
            api_key_env="STUB_API_KEY",
            default_headers={},
        )

    def get_runtime_descriptor(self, model: str) -> tuple[ProviderDescriptor, str]:
        _ = model
        return self._descriptor, "stub-model"

    def create_client_config(self, model: str) -> Dict[str, Any]:
        _ = model
        return {"api_key": "stub-key", "base_url": None, "default_headers": {}}


class _StubRegistry:
    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.invocation_count = 0

    def create_runtime(self, descriptor: ProviderDescriptor) -> Any:
        outer = self

        class _Runtime:
            def __init__(self, _descriptor: ProviderDescriptor) -> None:
                self.descriptor = _descriptor

            def create_client(self, api_key: str, *, base_url: str | None = None, default_headers: Dict[str, str] | None = None) -> object:
                _ = (api_key, base_url, default_headers)
                return object()

            def invoke(
                self,
                *,
                client: Any,
                model: str,
                messages: list[Dict[str, Any]],
                tools: Any,
                stream: bool,
                context: Any,
            ) -> ProviderResult:
                _ = (client, model, tools, stream, context)
                outer.invocation_count += 1
                return outer._handler(messages)

        return _Runtime(descriptor)


def _install_stub_provider(monkeypatch: Any, handler: Any) -> _StubRegistry:
    router = _StubRouter()
    registry = _StubRegistry(handler)
    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.provider_router", router)
    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.provider_registry", registry)
    return registry


def test_rlm_batch_query_branch_explosion_smoke(monkeypatch: Any, tmp_path: Path) -> None:
    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        _ = messages
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0005},
        )

    registry = _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {
                    "mode": "batch",
                    "batch": {"enabled": True, "max_concurrency": 8, "max_concurrency_per_branch": 2},
                },
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    queries = [{"prompt": f"task {i}", "branch_id": f"branch.{i}"} for i in range(32)]
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {"model": "stub/model", "queries": queries},
        }
    )
    rows = out.get("results") or []
    assert len(rows) == 32
    assert [int(row.get("request_index", -1)) for row in rows] == list(range(32))
    assert all(str(row.get("status") or "") == "completed" for row in rows)
    assert registry.invocation_count == 32


def test_rlm_batch_query_fail_fast_cancels_remaining_items(monkeypatch: Any, tmp_path: Path) -> None:
    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        prompt = str(messages[-1].get("content") or "")
        if "task 0" in prompt:
            raise ProviderRuntimeError("forced-failure")
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0005},
        )

    registry = _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "fail_fast": True, "max_concurrency": 8}},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    queries = [{"prompt": f"task {i}", "branch_id": f"branch.{i}"} for i in range(20)]
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {"model": "stub/model", "queries": queries},
        }
    )
    rows = out.get("results") or []
    assert len(rows) == 20
    assert str(rows[0].get("status") or "") == "failed"
    assert all(str(row.get("status") or "") == "blocked" for row in rows[1:])
    assert all(str(row.get("reason") or "") == "fail_fast_short_circuit" for row in rows[1:])
    assert registry.invocation_count == 1
