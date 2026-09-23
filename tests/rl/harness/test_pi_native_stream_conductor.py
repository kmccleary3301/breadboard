from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import struct
import subprocess
import threading
from typing import Any, Mapping

import pytest

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.policy_provider import (
    E4TargetPolicyProjection,
    EpisodeOpenAICompletionsPolicyClient,
)
from breadboard.rl.harness.runners.base import (
    RunnerCancellationProbe,
    RunnerEventSink,
    RunnerOpenRequest,
    RunnerTermination,
    RunnerToolBinding,
)
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_IMPLEMENTATION_DIGEST,
    CONDUCTOR_RUNTIME_ABI,
    ConductorAdapter,
    ConductorRunRequest,
    PolicyRuntimeBinding,
)
from breadboard_engine.compilation.provider_response import PI_RESPONSE_CONSUMER_ID, profile_identity_digest
from breadboard_engine.e4_targets import load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from tests.compilation.test_server_compiler import _options
from tests.rl.harness.test_runner_conductor import _digest, _tool_grant
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan, _policy_capabilities

_NODE_MODULES = Path(os.environ.get("PI_CODING_AGENT_NODE_MODULES", "/tmp/pi-node-0731/node_modules"))
pytestmark = pytest.mark.skipif(
    not (_NODE_MODULES / "@mariozechner" / "pi-coding-agent" / "dist" / "index.js").is_file(),
    reason="pinned Pi 0.73.1 node_modules root is unavailable",
)


def _compile_target(tmp_path: Path, *, profile_digest: str) -> tuple[E4TargetPolicyProjection, Mapping[str, Any], c.CompiledConfigManifest]:
    cas = FilesystemCAS(tmp_path / "target-cas")
    try:
        compiled = compile_e4_harness(
            load_e4_target("pi@0.73.1"),
            {},
            {
                "version": 2,
                "profile": {"name": "pi-native-stream-test"},
                "workspace": {"root": "workspace"},
                "provider_tools": {"use_native": True, "api_variant": "responses"},
                "providers": {
                    "default_model": "model-a",
                    "models": [{
                        "id": "model-a",
                        "adapter": "openai",
                        "context_length": 32_768,
                        "route_handle_id": "route-a",
                        "credential_handle_id": "credential-a",
                        "params": {},
                        "response_policy": {
                            "schema_version": "bb.provider_native_response_policy.v1",
                            "consumer_id": PI_RESPONSE_CONSUMER_ID,
                            "provider_profile_digest": profile_digest,
                            "max_response_bytes": 1_048_576,
                            "max_stream_fragments": 10_000,
                        },
                    }],
            },
            },
            cas=cas,
            options=_options(),
            request_schema_version="bb.rl.headless-run-request.v2",
        )
        projection = E4TargetPolicyProjection.from_compiled(compiled.manifest)
        semantics = json.loads(json.dumps(compiled.manifest.semantic.to_canonical_obj()))
        return projection, semantics, compiled.manifest
    finally:
        cas.close()


def _sse_tool_response(index: int, calls: list[tuple[str, str, Mapping[str, Any]]]) -> bytes:
    chunks: list[dict[str, Any]] = []
    if calls:
        chunks.append(
            {
                "id": f"response-{index}",
                "object": "chat.completion.chunk",
                "created": index,
                "model": "model-a",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": ordinal,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments, separators=(",", ":"))},
                            }
                            for ordinal, (call_id, name, arguments) in enumerate(calls)
                        ],
                    },
                    "finish_reason": None,
                }],
            }
        )
        finish = "tool_calls"
    else:
        chunks.append(
            {
                "id": f"response-{index}",
                "object": "chat.completion.chunk",
                "created": index,
                "model": "model-a",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}],
            }
        )
        finish = "stop"
    chunks.append(
        {
            "id": f"response-{index}",
            "object": "chat.completion.chunk",
            "created": index,
            "model": "model-a",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        }
    )
    return b"".join(b"data: " + json.dumps(chunk, separators=(",", ":")).encode() + b"\n\n" for chunk in chunks) + b"data: [DONE]\n\n"


@contextmanager
def _scripted_server(responses: list[list[tuple[str, str, Mapping[str, Any]]]]):
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            requests.append(body)
            ordinal = len(requests) - 1
            payload = _sse_tool_response(ordinal + 1, responses[min(ordinal, len(responses) - 1)])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


class _NativeWorkerPort:
    def __init__(self, workspace: Path, grants: tuple[RunnerToolBinding, ...]) -> None:
        self.workspace = workspace
        self.scratch = workspace / ".scratch"
        self.scratch.mkdir()
        self._bindings = grants
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self.system_prompt = ""

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]:
        return self._bindings

    async def _ensure(self) -> None:
        if self._process is None:
            env = dict(os.environ)
            env["PI_NATIVE_WORKER_FRAMED"] = "1"
            env["PI_CODING_AGENT_NODE_MODULES"] = str(_NODE_MODULES)
            self._process = await asyncio.create_subprocess_exec(
                "node",
                str(Path(__file__).parents[3] / "breadboard/rl/harness/pi_tools_0_73_1.mjs"),
                cwd=self.workspace,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    async def invoke_native_phase(self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int) -> Mapping[str, Any]:
        await self._ensure()
        assert self._process is not None and self._process.stdin is not None and self._process.stdout is not None
        self._request_id += 1
        phase_payload = dict(payload)
        if operation == "initialize":
            phase_payload.update({
                "workspace": str(self.workspace),
                "scratch": str(self.scratch),
                "package_dir": str(_NODE_MODULES / "@mariozechner" / "pi-coding-agent"),
                "advertisement": json.loads(
                    (Path(__file__).parents[3] / "config/e4_targets/pi/0.73.1/native-config.json").read_text()
                )["advertisement"],
            })
        command = {
            "schema_version": "bb.native-worker.rpc.v1",
            "request_id": self._request_id,
            "operation": operation,
            "payload": phase_payload,
        }
        body = json.dumps(command, separators=(",", ":")).encode()
        self._process.stdin.write(struct.pack(">I", len(body)) + body)
        await self._process.stdin.drain()
        header = await asyncio.wait_for(self._process.stdout.readexactly(4), timeout_ms / 1000)
        size = struct.unpack(">I", header)[0]
        envelope = json.loads((await asyncio.wait_for(self._process.stdout.readexactly(size), timeout_ms / 1000)).decode())
        if "error" in envelope:
            raise RuntimeError(envelope["error"])
        result = envelope["result"]
        if operation == "initialize":
            self.system_prompt = result["system_prompt"]
        return result

    async def invoke_tool(self, tool_id: str, arguments: Mapping[str, Any], *, timeout_ms: int) -> Mapping[str, Any]:
        raise AssertionError("Pi native stream must use invoke_native_phase")
    async def close(self) -> None:
        if self._process is not None:
            if self._process.stdin is not None:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None


class _Events:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class _Cancellation:
    def raise_if_cancelled(self, checkpoint: str, *, turn: int | None = None, call_id: str | None = None) -> None:
        return None


async def _run_episode(tmp_path: Path, responses: list[list[tuple[str, str, Mapping[str, Any]]]]):
    with _scripted_server(responses) as (base_url, requests):
        profile = OpenAICompletionsProviderProfile(
            model="model-a",
            scoped_credential="episode-secret",
            base_url=base_url,
            context_window=32_768,
            max_output_tokens=2_048,
            caller_headers={},
            request_policy={
                "mode": "streaming",
                "include_usage": True,
                "strict_tools": None,
                "enable_thinking": None,
            },
            capabilities={"supports_store": True},
        )
        projection, semantics, manifest = _compile_target(
            tmp_path,
            profile_digest=profile_identity_digest(profile),
        )
        observation = _observation(
            provider_id="openai",
            capabilities=_policy_capabilities(
                request_features=["max_tokens", "n", "stream_options", "streaming"],
            ),
        )
        tools = tuple(_tool_grant(name) for name in ("bash", "edit", "read", "write"))
        plan = _plan(
            observation=observation,
            semantics=semantics,
            tools=tools,
            policy_slot_ids=("model:model-a",),
            limit_updates={"max_turns": 8, "action_timeout_ms": 35_000},
            implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST,
        )
        base_payload = plan.base_compiled.model_dump(mode="python")
        base_payload.update(
            manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
            compiler_input_digest=manifest.inputs.compiler_input_digest,
        )
        plan_payload = plan.model_dump(mode="python")
        plan_payload["base_compiled"] = c.CompiledArtifactIdentity.model_validate(base_payload)
        plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
        worker = _NativeWorkerPort(tmp_path, tuple(
            RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools
        ))
        client = EpisodeOpenAICompletionsPolicyClient(
            episode_id="episode-pi",
            effective_plan_digest=plan.canonical_digest(),
            observation=observation,
            profile=profile,
            target_projection=projection,
            timeout_seconds=45,
        )
        binding = PolicyRuntimeBinding(
            RunnerOpenRequest(episode_id="episode-pi", effective_plan=plan), client
        )
        sink = _Events()
        session = await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(
            RunnerOpenRequest(episode_id="episode-pi", effective_plan=plan),
            policy=binding,
            workspace=worker,
            cancellation=_Cancellation(),
            events=sink,
        )
        try:
            result = await session.run(ConductorRunRequest(task_input={"prompt": "work"}, context={}))
        finally:
            await session.close()
            await worker.close()
        system_prompt = worker.system_prompt
        await client.close()
    return result, requests, sink.events, system_prompt


@pytest.mark.asyncio
async def test_pi_native_stream_cap_batch_and_request_shape(tmp_path: Path) -> None:
    responses = [[
        ("a", "write", {"path": "a.txt", "content": "A\n"}),
        ("bad", "edit", {"path": "missing.txt", "edits": []}),
        ("c", "write", {"path": "c.txt", "content": "C\n"}),
    ]] + [[("loop", "bash", {"command": "printf loop"})]] * 7
    result, requests, _, system_prompt = await _run_episode(tmp_path, responses)
    assert requests[0]["messages"][0] == {"role": "system", "content": system_prompt}
    assert result.termination is RunnerTermination.LIMITS_EXCEEDED
    assert (tmp_path / "a.txt").read_text() == "A\n"
    assert (tmp_path / "c.txt").read_text() == "C\n"
    for request in requests:
        assert "n" not in request
        assert request.get("store") is False
        assert request["stream_options"] == {"include_usage": True}
        assert request["max_tokens"] == 2_048
        assert "strict" not in json.dumps(request)


@pytest.mark.asyncio
async def test_pi_native_stream_no_call_is_assistant_complete(tmp_path: Path) -> None:
    result, requests, _, system_prompt = await _run_episode(tmp_path, [[]])
    assert len(requests) == 1
    assert requests[0]["messages"][0] == {"role": "system", "content": system_prompt}
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE


@pytest.mark.asyncio
async def test_pi_native_worker_preserves_source_order_and_completion_order(tmp_path: Path) -> None:
    port = _NativeWorkerPort(tmp_path, ())
    try:
        initialized = await port.invoke_native_phase(
            "initialize",
            {
                "task": "phase contract",
                "model_config": {
                    "id": "model-a",
                    "provider": "openai",
                    "base_url": "http://127.0.0.1",
                    "input": ["text"],
                },
            },
            timeout_ms=5_000,
        )
        assert initialized["kind"] == "initialized"
        assert initialized["bootstrap"]["cwd"] == str(tmp_path)
        assert Path(initialized["bootstrap"]["home"]).parent == port.scratch
        await port.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {"id": "slow", "name": "bash", "arguments": {"command": "sleep 0.05; printf slow"}},
                    {"id": "fast", "name": "bash", "arguments": {"command": "printf fast"}},
                ],
            },
            timeout_ms=5_000,
        )
        executed = await port.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        assert [result["id"] for result in executed["results"]] == ["slow", "fast"]
        assert [result["completion_index"] for result in executed["results"]] == [1, 0]
        closed = await port.invoke_native_phase("close", {}, timeout_ms=5_000)
        assert closed["cleanup"]["all_dead"] is True
    finally:
        await port.close()
