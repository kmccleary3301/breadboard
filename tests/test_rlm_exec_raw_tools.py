from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from agentic_coder_prototype.provider_routing import ProviderDescriptor
from agentic_coder_prototype.provider_runtime import ProviderMessage, ProviderResult, ProviderRuntimeError

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


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
                try:
                    return outer._handler(messages, context)
                except TypeError:
                    return outer._handler(messages)

        return _Runtime(descriptor)


def _install_stub_provider(monkeypatch: Any, handler: Any) -> _StubRegistry:
    router = _StubRouter()
    registry = _StubRegistry(handler)
    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.provider_router", router)
    monkeypatch.setattr("agentic_coder_prototype.agent_llm_openai.provider_registry", registry)
    return registry


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
