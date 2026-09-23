from dataclasses import replace

import asyncio
from contextlib import contextmanager
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import struct
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
    thaw_json,
)
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_IMPLEMENTATION_DIGEST,
    CONDUCTOR_RUNTIME_ABI,
    ConductorAdapter,
    ConductorRunRequest,
    PolicyRuntimeBinding,
)
from breadboard_engine.compilation.provider_response import (
    OPENCLAW_RESPONSE_CONSUMER_ID,
    profile_identity_digest,
)
from breadboard_engine.e4_targets import load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from tests.compilation.test_server_compiler import _options
from tests.rl.harness.test_runner_conductor import _digest, _tool_grant
from tests.rl.harness.test_runner_policy_runtime import (
    _observation,
    _plan,
    _policy_capabilities,
)



_NODE_DIST = Path(
    os.environ.get("OPENCLAW_DIST", "/tmp/openclaw-npm-20260923/node_modules/openclaw/dist")
)
_WORKER = Path(__file__).parents[3] / "breadboard/rl/harness/openclaw_tool_worker.mjs"
pytestmark = pytest.mark.skipif(
    not (_NODE_DIST / "core-coding-tools-DoP9tAh3.mjs").is_file(),
    reason="pinned OpenClaw 2026.9.4 dist is unavailable",
)


def _compile_target(
    tmp_path: Path, *, profile_digest: str
) -> tuple[E4TargetPolicyProjection, Mapping[str, Any], Any]:
    cas = FilesystemCAS(tmp_path / "target-cas")
    try:
        compiled = compile_e4_harness(
            load_e4_target("openclaw@2026.9.4"),
            {},
            {
                "version": 2,
                "profile": {"name": "openclaw-native-stream-test"},
                "workspace": {"root": "workspace"},
                "provider_tools": {"use_native": True, "api_variant": "responses"},
                "providers": {
                    "default_model": "model-a",
                    "models": [
                        {
                            "id": "model-a",
                            "adapter": "openai",
                            "context_length": 32_768,
                            "route_handle_id": "route-a",
                            "credential_handle_id": "credential-a",
                            "params": {},
                            "response_policy": {
                                "schema_version": "bb.provider_native_response_policy.v1",
                                "consumer_id": OPENCLAW_RESPONSE_CONSUMER_ID,
                                "provider_profile_digest": profile_digest,
                                "max_response_bytes": 1_048_576,
                                "max_stream_fragments": 10_000,
                            },
                        }
                    ],
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


def _sse_tool_response(
    index: int, calls: list[tuple[str, str, Mapping[str, Any]]]
) -> bytes:
    chunks: list[dict[str, Any]] = []
    if calls:
        chunks.append(
            {
                "id": f"response-{index}",
                "object": "chat.completion.chunk",
                "created": index,
                "model": "model-a",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": ordinal,
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments, separators=(",", ":")),
                                    },
                                }
                                for ordinal, (call_id, name, arguments) in enumerate(calls)
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
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
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "done"},
                        "finish_reason": None,
                    }
                ],
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
    return b"".join(
        b"data: " + json.dumps(chunk, separators=(",", ":")).encode() + b"\n\n"
        for chunk in chunks
    ) + b"data: [DONE]\n\n"


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
            payload = _sse_tool_response(
                ordinal + 1, responses[min(ordinal, len(responses) - 1)]
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
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
        self.operations: list[str] = []

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]:
        return self._bindings

    def _bootstrap_assets(self) -> list[dict[str, str]]:
        root = Path(__file__).parents[3] / "config/e4_targets/openclaw/2026.9.4"
        assets = []
        for name in ("AGENTS.md", "SOUL.md"):
            content = (root / "bootstrap" / name).read_text()
            assets.append(
                {
                    "name": name,
                    "content": content,
                    "sha256": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
                }
            )
        return assets

    async def _ensure(self) -> None:
        if self._process is None:
            env = dict(os.environ)
            env["OPENCLAW_DIST"] = str(_NODE_DIST)
            self._process = await asyncio.create_subprocess_exec(
                "node",
                str(_WORKER),
                cwd=self.workspace,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    async def invoke_native_phase(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Mapping[str, Any]:
        await self._ensure()
        assert (
            self._process is not None
            and self._process.stdin is not None
            and self._process.stdout is not None
        )
        self.operations.append(operation)
        self._request_id += 1
        phase_payload = dict(payload)
        if operation == "initialize":
            phase_payload.update(
                {
                    "workspace": str(self.workspace),
                    "scratch": str(self.scratch),
                    "scopeKey": "openclaw-conductor-test",
                    "bootstrap_assets": self._bootstrap_assets(),
                }
            )
        command = {
            "schema_version": "bb.native-worker.rpc.v1",
            "request_id": self._request_id,
            "operation": operation,
            "payload": phase_payload,
        }
        body = json.dumps(command, separators=(",", ":")).encode()
        self._process.stdin.write(struct.pack(">I", len(body)) + body)
        await self._process.stdin.drain()
        header = await asyncio.wait_for(
            self._process.stdout.readexactly(4), timeout_ms / 1000
        )
        size = struct.unpack(">I", header)[0]
        envelope = json.loads(
            (
                await asyncio.wait_for(
                    self._process.stdout.readexactly(size), timeout_ms / 1000
                )
            ).decode()
        )
        if "error" in envelope:
            raise RuntimeError(envelope["error"])
        result = envelope["result"]
        if operation == "initialize":
            self.system_prompt = result["system_prompt"]
        return result

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
    def raise_if_cancelled(
        self,
        checkpoint: str,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        return None


async def _run_episode(
    tmp_path: Path,
    responses: list[list[tuple[str, str, Mapping[str, Any]]]],
):
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
            tmp_path, profile_digest=profile_identity_digest(profile)
        )
        # The runtime plan contract stores JSON arrays as lists; preserve the
        # compiler's exact tool bytes while adapting the immutable projection.
        projection = replace(projection, chat_tools=thaw_json(projection.chat_tools))
        observation = _observation(
            provider_id="openai",
            model_id="model-a",
            route_id="route-a",
            credential_handle_id="credential-a",
            protocol_abi="responses-v1",
            capabilities=_policy_capabilities(
                request_features=[
                    "json_mode",
                    "max_tokens",
                    "seed",
                    "stream_options",
                    "streaming",
                ]
            ),
        )
        tools = tuple(
            _tool_grant(name)
            for name in ("edit", "exec", "ls", "process", "read", "write")
        )
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
        worker = _NativeWorkerPort(
            tmp_path,
            tuple(
                RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids)
                for t in tools
            ),
        )
        client = EpisodeOpenAICompletionsPolicyClient(
            episode_id="episode-openclaw",
            effective_plan_digest=plan.canonical_digest(),
            observation=observation,
            profile=profile,
            target_projection=projection,
            timeout_seconds=45,
        )
        binding = PolicyRuntimeBinding(
            RunnerOpenRequest(episode_id="episode-openclaw", effective_plan=plan), client
        )
        sink = _Events()
        session = await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(
            RunnerOpenRequest(episode_id="episode-openclaw", effective_plan=plan),
            policy=binding,
            workspace=worker,
            cancellation=_Cancellation(),
            events=sink,
        )
        try:
            result = await session.run(
                ConductorRunRequest(task_input={"prompt": "work"}, context={})
            )
        finally:
            await session.close()
            await worker.close()
        system_prompt = worker.system_prompt
        operations = tuple(worker.operations)
        await client.close()
    return result, requests, sink.events, system_prompt, operations


@pytest.mark.asyncio
async def test_openclaw_native_stream_cap_refuses_ninth_http_request(tmp_path: Path) -> None:
    responses = [
        [(f"call-{index}", "write", {"path": f"turn-{index}.txt", "content": "ok\n"})]
        for index in range(8)
    ]
    result, requests, _, system_prompt, operations = await _run_episode(tmp_path, responses)
    assert result.termination is RunnerTermination.LIMITS_EXCEEDED
    assert len(requests) == 8
    assert requests[0]["messages"][0] == {"role": "system", "content": system_prompt}
    assert all(request.get("stream_options") == {"include_usage": True} for request in requests)
    assert all(request.get("store") is False for request in requests)
    assert all("n" not in request and "strict" not in request for request in requests)
    assert operations[-1] == "close"
    assert all((tmp_path / f"turn-{index}.txt").read_text() == "ok\n" for index in range(8))


@pytest.mark.asyncio
async def test_openclaw_native_worker_ack_and_close_are_scope_verified(tmp_path: Path) -> None:
    worker = _NativeWorkerPort(tmp_path, ())
    try:
        initialized = await worker.invoke_native_phase(
            "initialize",
            {
                "task": "phase contract",
                "model_config": {
                    "id": "model-a",
                    "provider": "openai",
                    "baseUrl": "http://127.0.0.1",
                    "input": ["text"],
                },
            },
            timeout_ms=5_000,
        )
        assert initialized["kind"] == "initialized"
        await worker.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {
                        "id": "background",
                        "name": "exec",
                        "arguments": {"command": "printf done", "background": True},
                    }
                ]
            },
            timeout_ms=5_000,
        )
        launched = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        session_id = launched["results"][0]["details"]["sessionId"]
        await worker.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [
                    {
                        "id": "poll",
                        "name": "process",
                        "arguments": {"action": "poll", "sessionId": session_id, "timeout": 100},
                    }
                ]
            },
            timeout_ms=5_000,
        )
        polled = await worker.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        delivery_id = polled["results"][0]["delivery_id"]
        assert worker.operations[-1] == "execute_batch"
        acknowledged = await worker.invoke_native_phase(
            "ack",
            {"delivery_id": delivery_id, "history_digest": "sha256:" + "a" * 64},
            timeout_ms=5_000,
        )
        assert acknowledged["kind"] in {"acknowledged", "acked"}
        assert worker.operations[-1] == "ack"
        closed = await worker.invoke_native_phase("close", {}, timeout_ms=5_000)
        assert closed["cleanup"]["all_dead"] is True
    finally:
        await worker.close()
