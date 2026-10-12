from dataclasses import fields, replace
import asyncio
from contextlib import contextmanager
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import os
from pathlib import Path
import shutil
import struct
import subprocess
import threading
from typing import Any, Callable, Mapping
from datetime import datetime, timezone
from breadboard.rl.harness import sandbox as sandbox_module
import pytest
from tests.rl.harness.native_dependencies import require_openclaw, resolve_openclaw
from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.lease_envelope import RuntimeContainment
from breadboard.rl.harness.policy_provider import E4TargetPolicyProjection, EpisodeOpenAICompletionsPolicyClient
from breadboard.rl.harness.runners.base import RunnerCancellationProbe, RunnerDependencyError, RunnerEventSink, RunnerOpenRequest, RunnerProtocolError, RunnerTermination, RunnerToolBinding, thaw_json
from breadboard.rl.harness.runners.conductor import CONDUCTOR_IMPLEMENTATION_DIGEST, CONDUCTOR_RUNTIME_ABI, ConductorAdapter, ConductorRunRequest, PolicyRuntimeBinding
from breadboard.rl.harness.runners.openclaw_semantics import OpenClawSemanticsState
from breadboard_engine.compilation.provider_response import OPENCLAW_RESPONSE_CONSUMER_ID, profile_identity_digest
from breadboard_engine.e4_targets import load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from tests.compilation.test_server_compiler import _options
from tests.rl.harness.test_runner_conductor import CONDUCTOR_TEST_AUTHENTICATOR, CONDUCTOR_TEST_LEDGER, _digest, _tool_grant
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan, _policy_capabilities
from tests.rl.harness.v2_service_fixtures import signed_containment_receipt

@pytest.fixture
def openclaw_dist(monkeypatch: pytest.MonkeyPatch) -> Path:
    dist = require_openclaw()
    monkeypatch.setenv("OPENCLAW_DIST", str(dist))
    return dist


_NODE_DIST = resolve_openclaw()


_WORKER = Path(__file__).parents[3] / "breadboard/rl/harness/openclaw_tool_worker.mjs"


def _compile_target(
    tmp_path: Path, *, profile_digest: str,
    target_id: str = "openclaw@2026.9.4", context_window: int = 32_768,
) -> tuple[E4TargetPolicyProjection, Mapping[str, Any], Any]:
    cas = FilesystemCAS(tmp_path / "target-cas")
    try:
        compiled = compile_e4_harness(
            load_e4_target(target_id),
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
                            "context_length": context_window,
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
    index: int, calls: list[tuple[str, str, Mapping[str, Any]]],
    *, content: str | None = None, usage: Mapping[str, Any] | None = None,
    finish_reason: str | None = None,
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
                            **({"content": content} if content is not None else {}),
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
                        "delta": {"role": "assistant", "content": "done" if content is None else content},
                        "finish_reason": None,
                    }
                ],
            }
        )
        finish = "stop"
    if finish_reason is not None:
        finish = finish_reason
    chunks.append(
        {
            "id": f"response-{index}",
            "object": "chat.completion.chunk",
            "created": index,
            "model": "model-a",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        }
    )
    if usage is not None:
        chunks.append({
            "id": f"response-{index}", "object": "chat.completion.chunk",
            "created": index, "model": "model-a", "choices": [], "usage": dict(usage),
        })
    return b"".join(
        b"data: " + json.dumps(chunk, separators=(",", ":")).encode() + b"\n\n"
        for chunk in chunks
    ) + b"data: [DONE]\n\n"


@contextmanager
def _scripted_server(responses: list[list[tuple[str, str, Mapping[str, Any]]]] | Callable[[int, list[dict[str, Any]]], list[tuple[str, str, Mapping[str, Any]]]]):
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
            calls = responses(ordinal, requests) if callable(responses) else responses[min(ordinal, len(responses) - 1)]
            if isinstance(calls, Mapping) and "http_error" in calls:
                # A scripted provider refusal: {"http_error": status, "body": {...}}.
                payload = json.dumps(calls["body"]).encode()
                self.send_response(calls["http_error"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = (
                _sse_tool_response(
                    ordinal + 1, calls["calls"] if "calls" in calls else [],
                    content=calls.get("content"), usage=calls.get("usage"),
                    finish_reason=calls.get("finish_reason"),
                )
                if isinstance(calls, Mapping) else _sse_tool_response(ordinal + 1, calls)
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
        self.containment = RuntimeContainment.ATTESTED
        self.containment_lease_id = "lease-conductor-test"
        self.containment_receipt = signed_containment_receipt(
            self.containment_lease_id, "sandbox", CONDUCTOR_TEST_AUTHENTICATOR
        )
        self.workspace = workspace
        self.scratch = workspace.parent / f"{workspace.name}-scratch"
        self.scratch.mkdir()
        (self.scratch / "home").mkdir(exist_ok=True)
        self._bindings = grants
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self.system_prompt = ""
        self.operations: list[str] = []
        self._effect_baseline: dict[str, dict[str, Any]] | None = None

    @property
    def declared_workspace(self) -> str:
        return str(self.workspace)

    def _snapshot_effects(self) -> dict[str, dict[str, Any]]:
        snapshot, _ = sandbox_module._workspace_effect_snapshot(
            self.workspace,
            exclude_root_git=False,
            max_total_bytes=1 << 30,
            max_inodes=1 << 16,
            max_depth=64,
        )
        return snapshot

    async def begin_native_workspace_effects(self) -> None:
        self.operations.append("begin_effects")
        self._effect_baseline = self._snapshot_effects()

    async def measure_workspace_effects(self) -> Mapping[str, Mapping[str, Any]]:
        self.operations.append("measure_effects")
        current = self._snapshot_effects()
        changed: dict[str, Mapping[str, Any]] = {}
        for path, value in current.items():
            baseline = self._effect_baseline.get(path) if self._effect_baseline else None
            if baseline is None or (
                baseline["bytes"] != value["bytes"]
                or baseline["sha256"] != value["sha256"]
            ):
                changed[path] = value
        if self._effect_baseline:
            for path in self._effect_baseline.keys() - current.keys():
                changed[path] = {"exists": False}
        return changed

    async def close_native_runtime(self) -> Mapping[str, Any]:
        self.operations.append("retire_runtime")
        process, self._process = self._process, None
        if process is not None:
            if process.returncode is None:
                process.kill()
            await process.wait()
        return {"kind": "closed", "cleanup": {"all_dead": True, "steps": []}}

    def native_runtime_inputs(
        self,
        *,
        input_names: tuple[str, ...],
        package_subpath: str,
    ) -> Mapping[str, str]:
        values = {
            "cwd": str(self.workspace),
            "home": str(self.scratch / "home"),
            "current_date": datetime.now(timezone.utc).date().isoformat(),
            "message_timestamp_ms": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            "package_dir": str(_NODE_DIST),
            "session_id": "bbe4-" + hashlib.sha256(str(self.workspace).encode()).hexdigest()[:32],
        }
        if set(input_names) != set(values):
            raise RuntimeError("unexpected runtime input declaration")
        return {name: values[name] for name in input_names}
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
            env = {
                "OPENCLAW_DIST": str(_NODE_DIST),
                "OPENCLAW_STATE_DIR": str(self.scratch / "state"),
                "HOME": str(self.scratch / "home"),
                "PATH": os.environ["PATH"],
                "TZ": "UTC",
            }
            self._process = await asyncio.create_subprocess_exec(
                "node",
                "--import",
                str(_WORKER.with_name("openclaw_classifier_loader.mjs")),
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
        package_subpath: str | None = None,
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
            phase_payload.setdefault(
                "runtime_inputs",
                self.native_runtime_inputs(
                    input_names=("cwd", "home", "current_date", "message_timestamp_ms", "package_dir", "session_id"),
                    package_subpath=".",
                ),
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
    async def invoke_native_finalization_phase(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Mapping[str, Any]:
        assert self._process is None
        assert operation == "finalize_command_result"
        self.operations.append(operation)
        return await _invoke_finalize_only(payload)

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


_SUPPLIER_WIRE_POLICY: Mapping[str, Any] = {
    "request_policy": {"max_token_field": "max_completion_tokens", "tool_choice": "auto"},
    "capabilities": {"supports_max_completion_tokens": True},
    "request_features": ("max_completion_tokens", "tool_choice"),
}


async def _run_episode(
    tmp_path: Path,
    responses: list[list[tuple[str, str, Mapping[str, Any]]]] | Callable[[int, list[dict[str, Any]]], list[tuple[str, str, Mapping[str, Any]]]],
    *,
    worker_factory: Callable[[Path, tuple[RunnerToolBinding, ...]], _NativeWorkerPort] | None = None,
    wire_policy: Mapping[str, Any] = _SUPPLIER_WIRE_POLICY,
    target_id: str = "openclaw@2026.9.4",
    context_window: int = 32_768,
    evidence_limits: Mapping[str, int] | None = None,
):
    with _scripted_server(responses) as (base_url, requests):
        profile = OpenAICompletionsProviderProfile(
            model="model-a",
            scoped_credential="episode-secret",
            base_url=base_url,
            context_window=context_window,
            max_output_tokens=2_048,
            caller_headers={},
            request_policy={
                "mode": "streaming",
                "include_usage": True,
                "strict_tools": None,
                "enable_thinking": None,
                **wire_policy["request_policy"],
            },
            capabilities=dict(wire_policy["capabilities"]),
        )
        projection, semantics, manifest = _compile_target(
            tmp_path, profile_digest=profile_identity_digest(profile),
            target_id=target_id, context_window=context_window,
        )
        # The runtime plan contract stores JSON arrays as lists; preserve the
        # compiler's exact tool bytes while adapting the immutable projection.
        original_projection = projection
        projection = replace(projection, chat_tools=thaw_json(projection.chat_tools))
        assert all(
            getattr(projection, field.name) == getattr(original_projection, field.name)
            for field in fields(projection) if field.name != "chat_tools"
        )
        assert projection.chat_tools == thaw_json(original_projection.chat_tools)
        observation = _observation(
            provider_id="openai",
            model_id="model-a",
            route_id="route-a",
            credential_handle_id="credential-a",
            protocol_abi="responses-v1",
            capabilities=_policy_capabilities(
                request_features=sorted([
                    "json_mode",
                    "seed",
                    "stream_options",
                    "streaming",
                    *wire_policy["request_features"],
                ])
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
            limit_updates={"max_turns": 8, "action_timeout_ms": 35_000, **(evidence_limits or {})},
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
        bindings = tuple(
            RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids)
            for t in tools
        )
        worker = (
            worker_factory(tmp_path, bindings)
            if worker_factory is not None
            else _NativeWorkerPort(tmp_path, bindings)
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
        session = await ConductorAdapter(
            CONDUCTOR_RUNTIME_ABI,
            containment_authenticator=CONDUCTOR_TEST_AUTHENTICATOR,
            admitted_lease_ledger=CONDUCTOR_TEST_LEDGER,
        ).open(
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


def _supplier_sanitize_tool_call_id(raw_id: str, dist_path: Path = _NODE_DIST) -> str:
    """Return the wire id the pinned supplier sanitizer assigns to a paired replay call."""
    history = [
        {"role": "assistant", "content": [{"type": "toolCall", "id": raw_id, "name": "exec", "arguments": {}}]},
        {"role": "toolResult", "toolCallId": raw_id, "content": []},
    ]
    script = (
        'import { o } from "./tool-call-id-CnwowhSs.mjs";\n'
        f"const res = o({json.dumps(history)}, \"strict\");\n"
        "if (res[0].content[0].id !== res[1].toolCallId) throw new Error('unpaired');\n"
        "process.stdout.write(res[0].content[0].id);\n"
    )
    proc = subprocess.run(
        [shutil.which("node") or "node", "--input-type=module", "-e", script],
        cwd=str(dist_path),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


async def _invoke_finalize_only(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    process = await asyncio.create_subprocess_exec(
        shutil.which("node") or "node",
        "--import",
        str(_WORKER.with_name("openclaw_classifier_loader.mjs")),
        str(_WORKER),
        "--finalize-only",
        cwd=_NODE_DIST.parent,
        env={},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    command = {
        "schema_version": "bb.native-worker.rpc.v1",
        "request_id": 1,
        "operation": "finalize_command_result",
        "payload": dict(payload),
    }
    body = json.dumps(command, separators=(",", ":")).encode()
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(struct.pack(">I", len(body)) + body)
    await process.stdin.drain()
    process.stdin.close()
    try:
        header = await asyncio.wait_for(process.stdout.readexactly(4), 10)
        size = struct.unpack(">I", header)[0]
        result = json.loads((await asyncio.wait_for(process.stdout.readexactly(size), 10)).decode())
        await asyncio.wait_for(process.wait(), 10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if "error" in result:
        raise RuntimeError(result["error"])
    assert process.returncode == 0
    return result["result"]

