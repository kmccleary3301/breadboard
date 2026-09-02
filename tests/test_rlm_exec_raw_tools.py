from __future__ import annotations

import json
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.provider.routing import ProviderDescriptor
from breadboard_engine.provider.runtime import ProviderMessage, ProviderResult, ProviderRuntimeError
from breadboard_engine.model_roles import compile_model_roles
from breadboard_engine.provider.health import RouteHealthManager
from breadboard_engine.provider.invoker import ProviderInvoker
from breadboard_engine.provider.metrics import ProviderMetricsCollector
from breadboard_engine.state.session_state import SessionState


def _make_conductor(config: dict, workspace: Path) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst.workspace = str(workspace)
    inst._current_route_id = None
    inst._last_runtime_latency = None
    inst._last_html_detected = False
    inst.provider_metrics = ProviderMetricsCollector()
    inst.route_health = RouteHealthManager()
    inst.logger_v2 = types.SimpleNamespace(run_dir=None)
    inst.md_writer = types.SimpleNamespace(system=lambda message: message)
    session_state = SessionState(str(workspace), "test", config)
    session_state.set_provider_metadata("session_id", "e5-parent-session")
    session_state.set_turn_context(
        input_id="input-parent",
        turn_id="turn-parent",
        turn_index=None,
    )
    inst._active_session_state = session_state

    def no_provider_retry(*_args: Any, last_error=None, **_kwargs: Any):
        if last_error is not None:
            raise last_error
        return None

    inst.provider_invoker = ProviderInvoker(
        provider_metrics=inst.provider_metrics,
        route_health=inst.route_health,
        logger_v2=inst.logger_v2,
        md_writer=inst.md_writer,
        retry_with_fallback=no_provider_retry,
        update_health_metadata=lambda _state: None,
        set_last_latency=lambda value: setattr(inst, "_last_runtime_latency", value),
        set_html_detected=lambda value: setattr(inst, "_last_html_detected", value),
        client_lease=inst._provider_client_lease,
    )
    return inst  # type: ignore[return-value]


class _StubRouter:
    def __init__(self) -> None:
        self.execution_calls: list[tuple[str, dict[str, Any]]] = []
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


    def get_credential_origin(
        self, model: str, **kwargs: Any
    ) -> Dict[str, str]:
        _ = (model, kwargs)
        return {"kind": "synthetic", "source": "test"}

    @contextmanager
    def execution_client_config(self, model: str, **kwargs: Any):
        self.execution_calls.append((model, dict(kwargs)))
        config = self.create_client_config(model)
        try:
            yield config
        finally:
            config.clear()


class _StubRegistry:
    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.invocation_count = 0
        self.router: _StubRouter | None = None

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
                _ = (client, model, tools, stream)
                outer.invocation_count += 1
                context.record_provider_event("response_start")
                try:
                    result = outer._handler(messages, context)
                except TypeError:
                    result = outer._handler(messages)
                for index, message in enumerate(result.messages):
                    if not isinstance(message, ProviderMessage):
                        continue
                    message_id = message.message_id or f"message-{index}"
                    context.record_provider_event(
                        "text_start",
                        {
                            "content_index": index,
                            "message_id": message_id,
                        },
                    )
                    if isinstance(message.content, str) and message.content:
                        context.record_provider_event(
                            "text_delta",
                            {
                                "content_index": index,
                                "message_id": message_id,
                                "delta": message.content,
                            },
                        )
                    context.record_provider_event(
                        "text_end",
                        {
                            "content_index": index,
                            "message_id": message_id,
                        },
                    )
                return result

        return _Runtime(descriptor)


def _install_stub_provider(monkeypatch: Any, handler: Any) -> _StubRegistry:
    router = _StubRouter()
    registry = _StubRegistry(handler)
    registry.router = router
    monkeypatch.setattr("breadboard_engine.agent_llm_openai.provider_router", router)
    monkeypatch.setattr("breadboard_engine.agent_llm_openai.provider_registry", registry)
    return registry


def test_rlm_provider_subcalls_preserve_parent_session_affinity(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        _ = messages
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
        )

    registry = _install_stub_provider(monkeypatch, _handler)
    conductor = _make_conductor({}, tmp_path)
    execution = conductor._execute_rlm_provider_subcall(
        model_route="stub/model",
        messages=[{"role": "user", "content": "hello"}],
        runtime_extra={},
    )

    assert registry.router is not None
    assert registry.router.execution_calls == [
        (
            "stub/model",
            {
                "session_id": "e5-parent-session",
                "endpoint_id": "stub/model",
                "account_selector": None,
            },
        )
    ]
    assert execution.provider_exchange["correlation"] == {
        "session_id": "e5-parent-session",
        "input_id": "input-parent",
        "turn_id": "turn-parent",
    }
    history = conductor._active_session_state.get_provider_metadata(
        "provider_exchange_history"
    )
    assert history == [execution.provider_exchange]


def test_blob_tools_require_rlm_feature(tmp_path: Path) -> None:
    conductor = _make_conductor({"features": {}}, tmp_path)
    out = conductor._exec_raw({"function": "blob.put", "arguments": {"content": "hello"}})
    assert out.get("reason") == "rlm_disabled"


def test_blob_put_and_get_roundtrip(tmp_path: Path) -> None:
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "blob_store": {"root": ".breadboard/rlm_blobs", "max_total_bytes": 100000, "max_blob_bytes": 10000},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)

    put = conductor._exec_raw({"function": "blob.put", "arguments": {"content": "alpha beta gamma"}})
    blob_id = str(put.get("blob_id") or "")
    assert blob_id.startswith("sha256:")

    get = conductor._exec_raw({"function": "blob.get", "arguments": {"blob_id": blob_id, "preview_bytes": 5}})
    assert get.get("blob_id") == blob_id
    assert get.get("truncated") is True
    assert "alpha" in str(get.get("preview") or "")


def test_llm_query_replay_short_circuit() -> None:
    cfg = {"features": {"rlm": {"enabled": True}}}
    conductor = _make_conductor(cfg, Path("."))
    out = conductor._exec_raw(
        {
            "function": "llm.query",
            "arguments": {"prompt": "ignored"},
            "expected_output": {"text": "replayed", "usage": {"total_tokens": 9}},
            "expected_status": "completed",
        }
    )
    assert out.get("text") == "replayed"
    assert out.get("usage", {}).get("total_tokens") == 9


def test_llm_query_budget_blocked_before_provider_call(tmp_path: Path) -> None:
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {"max_subcalls": 1},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    conductor._rlm_budget_state_cache = {"started_at": 1.0, "subcalls": 1, "total_tokens": 0, "total_cost_usd": 0.0}
    conductor._active_session_state.set_provider_metadata(
        "rlm_budget_state",
        {
            "started_at": 1.0,
            "subcalls": 1,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        },
    )
    out = conductor._exec_raw({"function": "llm.query", "arguments": {"prompt": "hello"}})
    assert out.get("reason") == "subcall_limit_exceeded"
    ledger_path = tmp_path / ".breadboard" / "meta" / "rlm_branches.json"
    assert ledger_path.exists()


def test_llm_batch_query_replay_short_circuit_list() -> None:
    cfg = {"features": {"rlm": {"enabled": True}}}
    conductor = _make_conductor(cfg, Path("."))
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {"queries": [{"prompt": "ignored 1"}, {"prompt": "ignored 2"}]},
            "expected_output": [
                {"status": "completed", "text": "a"},
                {"status": "completed", "text": "b"},
            ],
            "expected_status": "completed",
        }
    )
    assert out.get("item_count") == 2
    rows = out.get("results") or []
    assert isinstance(rows, list) and len(rows) == 2
    assert rows[0].get("request_index") == 0
    assert rows[1].get("request_index") == 1


def test_llm_batch_query_requires_queries_array(tmp_path: Path) -> None:
    cfg = {"features": {"rlm": {"enabled": True, "scheduling": {"mode": "batch", "batch": {"enabled": True}}}}}
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw({"function": "llm.batch_query", "arguments": {"queries": []}})
    assert out.get("reason") == "invalid_arguments"


def test_llm_batch_query_preserves_request_order_under_parallel_completion(monkeypatch: Any, tmp_path: Path) -> None:
    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        prompt = str(messages[-1].get("content") or "")
        if "slow" in prompt:
            time.sleep(0.03)
            text = "slow"
        else:
            time.sleep(0.001)
            text = "fast"
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content=text)],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.001},
        )

    _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 2}},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {
                "model": "stub/model",
                "queries": [{"prompt": "slow prompt"}, {"prompt": "fast prompt"}],
            },
        }
    )
    rows = out.get("results") or []
    assert [int(row.get("request_index", -1)) for row in rows] == [0, 1]
    assert [str(row.get("text") or "") for row in rows] == ["slow", "fast"]
    history = conductor._active_session_state.get_provider_metadata(
        "provider_exchange_history"
    )
    assert len(history) == 2
    assert len({exchange["exchange_id"] for exchange in history}) == 2
    assert all(
        exchange["correlation"]
        == {
            "session_id": "e5-parent-session",
            "input_id": "input-parent",
            "turn_id": "turn-parent",
        }
        for exchange in history
    )


def test_llm_batch_query_retries_then_completes(monkeypatch: Any, tmp_path: Path) -> None:
    attempts: Dict[str, int] = {}

    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        prompt = str(messages[-1].get("content") or "")
        attempts[prompt] = int(attempts.get(prompt) or 0) + 1
        if attempts[prompt] == 1:
            raise ProviderRuntimeError("transient")
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 2, "output_tokens": 2, "cost_usd": 0.002},
        )

    registry = _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 1, "retries": 1}},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {"model": "stub/model", "queries": [{"prompt": "flaky request"}]},
        }
    )
    rows = out.get("results") or []
    assert len(rows) == 1
    row = rows[0]
    assert row.get("status") == "completed"
    assert int(row.get("attempt_count") or 0) == 2
    attempts_list = row.get("attempts") or []
    assert len(attempts_list) == 2
    assert str(attempts_list[0].get("status")) == "provider_error"
    assert attempts_list[0].get("error") == "provider_error"
    assert str(attempts_list[1].get("status")) == "completed"
    assert registry.invocation_count == 2


def test_llm_batch_query_fail_fast_short_circuit(monkeypatch: Any, tmp_path: Path) -> None:
    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        prompt = str(messages[-1].get("content") or "")
        if "first" in prompt:
            raise ProviderRuntimeError("boom")
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.001},
        )

    registry = _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "fail_fast": True, "max_concurrency": 4}},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {
                "model": "stub/model",
                "queries": [{"prompt": "first fails"}, {"prompt": "second should be skipped"}],
            },
        }
    )
    rows = out.get("results") or []
    assert [str(row.get("status") or "") for row in rows] == ["failed", "blocked"]
    assert rows[0].get("error") == "provider_error"
    assert str(rows[1].get("reason") or "") == "fail_fast_short_circuit"
    assert registry.invocation_count == 1
    summary = out.get("summary") or {}
    assert summary.get("fail_fast") is True


def test_llm_batch_query_budget_reservation_blocks_later_items(monkeypatch: Any, tmp_path: Path) -> None:
    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        _ = messages
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.001},
        )

    registry = _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {"max_subcalls": 1},
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 2}},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {
                "model": "stub/model",
                "queries": [{"prompt": "first"}, {"prompt": "second"}],
            },
        }
    )
    rows = out.get("results") or []
    assert [str(row.get("status") or "") for row in rows] == ["completed", "blocked"]
    assert "subcall_limit_exceeded" in str(rows[1].get("reason") or "")
    assert registry.invocation_count == 1

    artifact_path = tmp_path / ".breadboard" / "meta" / "rlm_batch_subcalls.jsonl"
    assert artifact_path.exists()
    artifact_rows = [json.loads(line) for line in artifact_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(artifact_rows) == 2
    assert str(artifact_rows[1].get("status") or "") == "blocked"
    assert isinstance(artifact_rows[1].get("consumed_blobs"), list)


def test_rlm_budget_state_shared_across_batch_and_single_query(monkeypatch: Any, tmp_path: Path) -> None:
    def _handler(messages: list[Dict[str, Any]]) -> ProviderResult:
        _ = messages
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.001},
        )

    _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {"max_subcalls": 2},
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 2}},
            }
        },
        "providers": {"default_model": "stub/model"},
    }
    conductor = _make_conductor(cfg, tmp_path)

    batch_out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {"queries": [{"prompt": "batch item"}]},
        }
    )
    assert str((batch_out.get("results") or [{}])[0].get("status") or "") == "completed"

    query_ok = conductor._exec_raw({"function": "llm.query", "arguments": {"prompt": "single item"}})
    assert str(query_ok.get("reason") or "") != "subcall_limit_exceeded"
    assert str(query_ok.get("text") or "") == "ok"

    query_blocked = conductor._exec_raw({"function": "llm.query", "arguments": {"prompt": "third call blocked"}})
    assert str(query_blocked.get("reason") or "") == "subcall_limit_exceeded"


def test_llm_batch_query_honors_per_branch_concurrency_cap(monkeypatch: Any, tmp_path: Path) -> None:
    active_by_branch: Dict[str, int] = {}
    peak_by_branch: Dict[str, int] = {}
    lock = threading.Lock()

    def _handler(messages: list[Dict[str, Any]], context: Any) -> ProviderResult:
        _ = messages
        branch = str((getattr(context, "extra", {}) or {}).get("branch_id") or "unknown")
        with lock:
            active_by_branch[branch] = int(active_by_branch.get(branch) or 0) + 1
            peak_by_branch[branch] = max(int(peak_by_branch.get(branch) or 0), active_by_branch[branch])
        time.sleep(0.02)
        with lock:
            active_by_branch[branch] = max(0, int(active_by_branch.get(branch) or 0) - 1)
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.001},
        )

    _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {
                    "mode": "batch",
                    "batch": {
                        "enabled": True,
                        "max_concurrency": 3,
                        "max_concurrency_per_branch": 1,
                    },
                },
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {
                "model": "stub/model",
                "queries": [
                    {"prompt": "same branch first", "branch_id": "shared"},
                    {"prompt": "same branch second", "branch_id": "shared"},
                    {"prompt": "other branch", "branch_id": "other"},
                ],
            },
        }
    )
    rows = out.get("results") or []
    assert [str(row.get("status") or "") for row in rows] == ["completed", "completed", "completed"]
    assert peak_by_branch.get("shared") == 1
    summary = out.get("summary") or {}
    assert int(summary.get("max_concurrency_per_branch") or 0) == 1


def test_llm_query_model_argument_cannot_escape_active_role_lock(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    def _handler(
        messages: list[Dict[str, Any]],
        context: Any,
    ) -> ProviderResult:
        _ = (messages, context)
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="locked")],
            raw_response={},
            usage={},
        )

    registry = _install_stub_provider(monkeypatch, _handler)
    assert registry.router is not None
    config = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {"max_subcalls": 8},
                "scheduling": {
                    "mode": "batch",
                    "batch": {
                        "enabled": True,
                        "max_concurrency": 2,
                    },
                },
            }
        },
        "providers": {"default_model": "mock/slow"},
    }
    conductor = _make_conductor(config, tmp_path)
    conductor._model_role_lock = compile_model_roles(
        {
            "schema_version": "bb.model_roles.v1",
            "defaults": {
                "role": "default",
                "known_but_unbound_role": "error",
                "unknown_role": "error",
            },
            "roles": {
                "default": {
                    "primary": {
                        "provider_id": "mock",
                        "model_id": "primary",
                    },
                    "fallbacks": [],
                    "fallback_on": [],
                },
                "slow": {
                    "primary": {
                        "provider_id": "mock",
                        "model_id": "slow",
                    },
                    "fallbacks": [],
                    "fallback_on": [],
                },
            },
            "dispatch": {
                "subagents": {},
                "lanes": {
                    "main": "default",
                    "balanced": "slow",
                },
            },
            "policy": {
                "allow_environment_overrides": False,
                "cross_provider_fallback": "forbidden",
                "account_failover": "forbidden",
            },
        }
    ).as_dict()
    conductor._active_model_role = "default"
    leased_routes: list[str] = []

    @contextmanager
    def client_lease(model_route: str, _runtime: Any):
        leased_routes.append(model_route)
        yield object()

    conductor.provider_invoker.client_lease = client_lease

    result = conductor._exec_raw(
        {
            "function": "llm.query",
            "arguments": {
                "prompt": "use the active role",
                "model": "mock/slow",
            },
        }
    )
    mapped_result = conductor._exec_raw(
        {
            "function": "llm.query",
            "arguments": {
                "prompt": "use the mapped lane",
                "model": "mock/primary",
                "lane": "balanced",
            },
        }
    )
    batch_result = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {
                "queries": [
                    {
                        "prompt": "batch active role",
                        "model": "mock/slow",
                    },
                    {
                        "prompt": "batch mapped lane",
                        "model": "mock/primary",
                        "lane": "balanced",
                    },
                ]
            },
        }
    )



    assert result.get("text") == "locked", result
    assert result["route_id"] == "mock/primary"
    assert mapped_result["route_id"] == "mock/slow"
    assert [row["route_id"] for row in batch_result["results"]] == [
        "mock/primary",
        "mock/slow",
    ]
    assert leased_routes[:2] == ["mock/primary", "mock/slow"]
    assert sorted(leased_routes[2:]) == ["mock/primary", "mock/slow"]


def test_llm_query_artifact_write_handles_non_serializable_usage(monkeypatch: Any, tmp_path: Path) -> None:
    class _OpaqueUsage:
        def __repr__(self) -> str:  # pragma: no cover - defensive
            return "<opaque-usage>"

    def _handler(messages: list[Dict[str, Any]], context: Any) -> ProviderResult:
        _ = (messages, context)
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1, "opaque": _OpaqueUsage()},
        )

    _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {"mode": "sync"},
            }
        },
        "providers": {"default_model": "stub/model"},
    }
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw({"function": "llm.query", "arguments": {"prompt": "serialize usage", "model": "stub/model"}})
    assert str(out.get("text") or "") == "ok"

    subcalls_path = tmp_path / ".breadboard" / "meta" / "rlm_subcalls.jsonl"
    assert subcalls_path.exists()
    rows = [json.loads(line) for line in subcalls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 1
    usage = rows[-1].get("usage") or {}
    assert isinstance(usage.get("opaque"), str)


def test_rlm_provider_execution_preserves_single_and_batch_results_and_context(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    contexts: list[Dict[str, Any]] = []

    def _handler(messages: list[Dict[str, Any]], context: Any) -> ProviderResult:
        _ = messages
        contexts.append(dict(context.extra))
        return ProviderResult(
            messages=[
                ProviderMessage(role="assistant", content=" first "),
                ProviderMessage(role="assistant", content="second"),
            ],
            raw_response={},
            usage={"input_tokens": 2, "output_tokens": 3, "cost_usd": 0.25},
        )

    _install_stub_provider(monkeypatch, _handler)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {"max_subcalls": 4},
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 1}},
            }
        },
        "providers": {"default_model": "stub/model"},
    }
    conductor = _make_conductor(cfg, tmp_path)

    single = conductor._exec_raw(
        {
            "function": "llm.query",
            "arguments": {
                "prompt": "single",
                "model": "stub/model",
                "branch_id": "single-branch",
                "max_completion_tokens": 123,
                "temperature": 0.25,
            },
        }
    )
    batch = conductor._exec_raw(
        {
            "id": "batch-context-id",
            "function": "llm.batch_query",
            "arguments": {
                "model": "stub/model",
                "queries": [
                    {
                        "prompt": "batch",
                        "branch_id": "batch-branch",
                        "max_completion_tokens": 456,
                        "temperature": 0.5,
                    }
                ],
            },
        }
    )
    batch_row = (batch.get("results") or [{}])[0]

    assert single["text"] == "first\n\nsecond"
    assert single["usage"] == {"input_tokens": 2, "output_tokens": 3, "cost_usd": 0.25}
    assert single["usage_tokens"] == 5
    assert single["estimated_cost_usd"] == 0.25
    assert single["model"] == "stub-model"
    assert single["route_id"] == "stub/model"
    assert single["provider_id"] == "stub"
    assert batch_row["text"] == "first\n\nsecond"
    assert batch_row["usage"] == single["usage"]
    assert batch_row["usage_tokens"] == 5
    assert batch_row["estimated_cost_usd"] == 0.25
    assert batch_row["resolved_model"] == "stub-model"
    assert batch_row["route_id"] == "stub/model"
    assert batch_row["provider_id"] == "stub"

    assert contexts == [
        {
            "rlm_subcall": True,
            "branch_id": "single-branch",
            "max_completion_tokens": 123,
            "temperature": 0.25,
            "session_id": "e5-parent-session",
            "input_id": "input-parent",
            "turn_id": "turn-parent",
        },
        {
            "rlm_subcall": True,
            "rlm_batch_subcall": True,
            "branch_id": "batch-branch",
            "batch_id": "batch-context-id",
            "request_index": 0,
            "max_completion_tokens": 456,
            "temperature": 0.5,
            "session_id": "e5-parent-session",
            "input_id": "input-parent",
            "turn_id": "turn-parent",
        },
    ]


def test_rlm_provider_execution_redacts_missing_key_errors_for_single_and_batch(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    router = _StubRouter()
    registry = _StubRegistry(lambda _messages: None)
    monkeypatch.setattr(
        router,
        "get_credential_origin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("breadboard_engine.agent_llm_openai.provider_router", router)
    monkeypatch.setattr("breadboard_engine.agent_llm_openai.provider_registry", registry)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {"max_subcalls": 4},
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 1}},
            }
        },
        "providers": {"default_model": "stub/model"},
    }
    conductor = _make_conductor(cfg, tmp_path)

    single = conductor._exec_raw({"function": "llm.query", "arguments": {"prompt": "single"}})
    batch = conductor._exec_raw(
        {
            "id": "missing-key-batch",
            "function": "llm.batch_query",
            "arguments": {"queries": [{"prompt": "batch"}]},
        }
    )
    batch_row = (batch.get("results") or [{}])[0]

    assert single["reason"] == "api_key_missing"
    assert single["error"] == "RLM api_key_missing: api_key_missing"
    assert batch_row == {
        "request_index": 0,
        "status": "failed",
        "reason": "api_key_missing",
        "error": "api_key_missing",
        "branch_id": "root",
        "depth": 0,
        "lane": "tool_heavy",
        "attempt_count": 1,
        "parent_call_id": "",
        "blob_refs": [],
        "call_id": "missing-key-batch:0",
    }
    assert registry.invocation_count == 0


def test_llm_batch_timeout_retry_uses_post_invoke_elapsed_for_completed_attempt(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    clock = {"now": 0.0}
    invocation_count = 0

    def _handler(_messages: list[Dict[str, Any]]) -> ProviderResult:
        nonlocal invocation_count
        invocation_count += 1
        clock["now"] += 2.0 if invocation_count == 1 else 0.5
        return ProviderResult(
            messages=[ProviderMessage(role="assistant", content="ok")],
            raw_response={},
            usage={"input_tokens": 1, "output_tokens": 1},
        )

    def _extract_usage_metrics(_usage: Dict[str, Any]) -> tuple[int, float]:
        clock["now"] = 50.0
        return 2, 0.0

    _install_stub_provider(monkeypatch, _handler)
    monkeypatch.setattr("breadboard_engine.agent_llm_openai.time.time", lambda: clock["now"])
    monkeypatch.setattr("breadboard_engine.agent_llm_openai.extract_usage_metrics", _extract_usage_metrics)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 1}},
            }
        },
        "providers": {"default_model": "stub/model"},
    }
    conductor = _make_conductor(cfg, tmp_path)
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {
                "queries": [
                    {
                        "prompt": "timeout then complete",
                        "timeout_seconds": 1.0,
                        "retries": 1,
                    }
                ]
            },
        }
    )
    row = (out.get("results") or [{}])[0]

    assert row["status"] == "completed"
    assert row["attempt_count"] == 2
    assert row["attempts"] == [
        {"attempt": 1, "status": "timeout", "duration_seconds": 2.0},
        {"attempt": 2, "status": "completed", "duration_seconds": 0.5},
    ]
