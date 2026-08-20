from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from breadboard_engine.provider.runtime import ProviderMessage, ProviderResult, ProviderToolCall
from breadboard.product.evidence.replay import ReplayManifest, ReplayManifestEntry, ReplayPlan
from breadboard.product.integrations.host import SandboxHostAdapter
from breadboard.product.integrations.replay import HostReplayWorker, ProviderReplayWorker, ToolReplayWorker
from breadboard.product.integrations.tool import ToolIntegrationAdapter
from breadboard.product.runtime.artifacts import ArtifactStore


def _plan(tmp_path: Path, worker_id: str, output_path: str, request: dict[str, Any]) -> tuple[ReplayPlan, bytes]:
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    source = ArtifactStore(tmp_path / "artifacts").put(encoded, media_type="application/json")
    manifest = ReplayManifest(
        (
            ReplayManifestEntry(output_path, "application/json"),
            ReplayManifestEntry("transcript.json", "application/json"),
        )
    )
    return ReplayPlan("source-session", source, worker_id, manifest), encoded


class _Provider:
    provider_id = "fixture"
    runtime_id = "fixture-v1"

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def invoke(self, **kwargs: Any) -> ProviderResult:
        self.invocations.append(kwargs)
        return ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content="done",
                    tool_calls=[ProviderToolCall("call-1", "finish", '{"ok":true}')],
                    finish_reason="stop",
                    index=0,
                )
            ],
            raw_response={"secret": "must-not-be-published"},
            usage={"input_tokens": 3, "output_tokens": 1},
            model="fixture-model",
            metadata={"request_id": "request-1"},
        )


def test_provider_replay_uses_product_port_and_excludes_raw_response(tmp_path: Path) -> None:
    provider = _Provider()
    worker = ProviderReplayWorker(provider, client=object(), context=object())
    request = {
        "schema_version": "bb.provider_replay_input.v1",
        "model": "fixture-model",
        "messages": [{"role": "user", "content": "finish"}],
        "tools": [{"type": "function", "function": {"name": "finish"}}],
        "stream": False,
    }
    plan, encoded = _plan(tmp_path, worker.worker_id, "provider_result.json", request)

    result = worker.execute(plan, encoded)

    output = json.loads(result.outputs["provider_result.json"])
    assert output["messages"][0]["tool_calls"][0]["name"] == "finish"
    assert output["usage"] == {"input_tokens": 3, "output_tokens": 1}
    assert "must-not-be-published" not in result.outputs["provider_result.json"].decode()
    assert provider.invocations[0]["messages"] == request["messages"]
    assert [row["span_id"] for row in result.transcript] == ["provider.invoke.request", "provider.invoke.response"]
    assert result.transcript[1]["parent_span_id"] == "provider.invoke.request"


def test_tool_and_host_replay_preserve_exact_requests_and_json_results(tmp_path: Path) -> None:
    tool_calls: list[dict[str, Any]] = []
    tool = ToolIntegrationAdapter("fixture", lambda arguments: tool_calls.append(dict(arguments)) or {"sum": arguments["left"] + arguments["right"]})
    tool_worker = ToolReplayWorker(tool)
    tool_request = {"schema_version": "bb.tool_replay_input.v1", "arguments": {"left": 2, "right": 3}}
    tool_plan, tool_bytes = _plan(tmp_path / "tool", tool_worker.worker_id, "tool_result.json", tool_request)
    tool_result = tool_worker.execute(tool_plan, tool_bytes)
    assert tool_calls == [{"left": 2, "right": 3}]
    assert json.loads(tool_result.outputs["tool_result.json"])["result"] == {"sum": 5}

    class Host:
        def get_workspace(self) -> str:
            return "workspace-fixture"

        def execute(self, command: str, **kwargs: Any) -> dict[str, Any]:
            return {"command": command, "timeout": kwargs["timeout"], "stdout": b"ok"}

    host_worker = HostReplayWorker(SandboxHostAdapter("fixture", Host()))
    host_request = {"schema_version": "bb.host_replay_input.v1", "command": "check", "options": {"timeout": 5}}
    host_plan, host_bytes = _plan(tmp_path / "host", host_worker.worker_id, "host_result.json", host_request)
    host_result = host_worker.execute(host_plan, host_bytes)
    assert json.loads(host_result.outputs["host_result.json"])["result"] == {
        "command": "check",
        "stdout": {"data": "b2s=", "encoding": "base64"},
        "timeout": 5,
    }
    assert host_result.transcript[1]["parent_span_id"] == "host.execute.request"


@pytest.mark.parametrize("port", ["provider", "tool", "host"])
def test_replay_adapter_does_not_swallow_port_errors(tmp_path: Path, port: str) -> None:
    class PortFailure(RuntimeError):
        pass

    if port == "provider":
        class Provider(_Provider):
            def invoke(self, **kwargs: Any) -> ProviderResult:
                raise PortFailure("provider failed")
        worker = ProviderReplayWorker(Provider(), client=object(), context=object())
        request = {"schema_version": "bb.provider_replay_input.v1", "model": "m", "messages": [], "tools": None, "stream": False}
        output = "provider_result.json"
    elif port == "tool":
        def fail_tool(_arguments: Any) -> Any:
            raise PortFailure("tool failed")
        worker = ToolReplayWorker(ToolIntegrationAdapter("fixture", fail_tool))
        request = {"schema_version": "bb.tool_replay_input.v1", "arguments": {}}
        output = "tool_result.json"
    else:
        class Host:
            def get_workspace(self) -> str:
                return "workspace-fixture"
            def execute(self, command: str, **kwargs: Any) -> Any:
                raise PortFailure("host failed")
        worker = HostReplayWorker(SandboxHostAdapter("fixture", Host()))
        request = {"schema_version": "bb.host_replay_input.v1", "command": "fail", "options": {}}
        output = "host_result.json"
    plan, encoded = _plan(tmp_path / port, worker.worker_id, output, request)

    with pytest.raises(PortFailure):
        worker.execute(plan, encoded)
