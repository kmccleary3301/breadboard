from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import contextmanager
from datetime import date
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
    RunnerDependencyError,
    RunnerEventSink,
    RunnerOpenRequest,
    RunnerPolicyBindingError,
    RunnerTermination,
    RunnerToolBinding,
    SourceEventCommitEvent,
    ToolObservationEvent,
    thaw_json,
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
from conformance.comparators.pi_coding_agent_0_73_1 import PiCodingAgent0731Comparator

SUPPLIER_CASE = Path(__file__).parents[2] / "e4_parity" / "fixtures" / "pi_0_73_1_supplier_case"

_NODE_MODULES = Path(os.environ.get("PI_CODING_AGENT_NODE_MODULES", "/tmp/pi-node-0731/node_modules"))
_PROMPT_TEMPLATE = Path(__file__).parents[3] / "config/e4_targets/pi/0.73.1/prompts/system-prompt.md"
pytestmark = pytest.mark.skipif(
    not (_NODE_MODULES / "@mariozechner" / "pi-coding-agent" / "dist" / "index.js").is_file(),
    reason="pinned Pi 0.73.1 node_modules root is unavailable",
)


def _compile_target(
    tmp_path: Path,
    *,
    profile_digest: str,
    model_id: str = "model-a",
) -> tuple[E4TargetPolicyProjection, Mapping[str, Any], c.CompiledConfigManifest]:
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
                    "default_model": model_id,
                    "models": [{
                        "id": model_id,
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


def _sse_tool_response(
    index: int,
    calls: list[tuple[str, str, Mapping[str, Any]]],
    *,
    assistant_text: str = "done",
) -> bytes:
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
                        "content": assistant_text,
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
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": assistant_text}, "finish_reason": None}],
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
def _scripted_server(
    responses: list[list[tuple[str, str, Mapping[str, Any]]]],
    *,
    assistant_texts: list[str] | None = None,
):
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
            assistant_text = (
                assistant_texts[ordinal]
                if assistant_texts is not None and ordinal < len(assistant_texts)
                else "done"
            )
            payload = _sse_tool_response(
                ordinal + 1,
                responses[min(ordinal, len(responses) - 1)],
                assistant_text=assistant_text,
            )
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
        self.operations: list[str] = []
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self.system_prompt = ""
        self.initialize_runtime_inputs: Mapping[str, str] = {}

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]:
        return self._bindings

    def native_runtime_inputs(
        self,
        *,
        input_names: tuple[str, ...],
        package_subpath: str,
    ) -> Mapping[str, str]:
        package_root = Path(package_subpath)
        if package_root.parts[:1] == ("node_modules",):
            package_root = Path(*package_root.parts[1:])
        values = {
            "cwd": str(self.workspace),
            "home": str(self.scratch / "home"),
            "current_date": date.today().isoformat(),
            "package_dir": str(_NODE_MODULES / package_root),
        }
        if set(input_names) != set(values):
            raise RuntimeError("unexpected runtime input declaration")
        return {name: values[name] for name in input_names}

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
        self.operations.append(operation)
        phase_payload = dict(payload)
        if operation == "initialize":
            phase_payload.setdefault("workspace", str(self.workspace))
            phase_payload.setdefault("scratch", str(self.scratch))
            phase_payload.setdefault("package_dir", str(_NODE_MODULES / "@mariozechner" / "pi-coding-agent"))
            phase_payload.setdefault(
                "runtime_inputs",
                {
                    "cwd": phase_payload["workspace"],
                    "home": str(self.scratch / "home"),
                    "current_date": date.today().isoformat(),
                    "package_dir": phase_payload["package_dir"],
                },
            )
            self.initialize_runtime_inputs = dict(phase_payload["runtime_inputs"])
            phase_payload.setdefault(
                "advertisement",
                json.loads(
                    (Path(__file__).parents[3] / "config/e4_targets/pi/0.73.1/native-config.json").read_text()
                )["advertisement"],
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
            self.operations.append("close")
            if self._process.stdin is not None:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None


def _render_sealed_prompt(bootstrap: Mapping[str, Any]) -> str:
    context = "".join(
        f"## {entry['path']}\n\n{entry['content']}\n\n"
        for entry in bootstrap["project_context"]
    )
    replacements = {
        "{{readme_path}}": f"{bootstrap['package_dir']}/README.md",
        "{{docs_path}}": f"{bootstrap['package_dir']}/docs",
        "{{examples_path}}": f"{bootstrap['package_dir']}/examples",
        "{{project_context}}": context,
        "{{current_date}}": bootstrap["current_date"],
        "{{cwd}}": bootstrap["cwd"],
    }
    prompt = _PROMPT_TEMPLATE.read_text(encoding="utf-8")
    for slot, value in replacements.items():
        assert prompt.count(slot) == 1
        prompt = prompt.replace(slot, value)
    assert "{{" not in prompt
    return prompt


class _Events:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class _Cancellation:
    def raise_if_cancelled(self, checkpoint: str, *, turn: int | None = None, call_id: str | None = None) -> None:
        return None


async def _run_episode(
    tmp_path: Path,
    responses: list[list[tuple[str, str, Mapping[str, Any]]]],
    *,
    request_features: list[str] | None = None,
    model_id: str = "model-a",
    task_prompt: str = "work",
    assistant_texts: list[str] | None = None,
):
    with _scripted_server(responses, assistant_texts=assistant_texts) as (base_url, requests):
        profile = OpenAICompletionsProviderProfile(
            model=model_id,
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
            model_id=model_id,
        )
        observation = _observation(
            provider_id="openai",
            model_id=model_id,
            capabilities=_policy_capabilities(
                request_features=(
                    request_features
                    if request_features is not None
                    else ["max_tokens", "n", "store", "stream_options", "streaming"]
                ),
            ),
        )
        tools = tuple(_tool_grant(name) for name in ("bash", "edit", "read", "write"))
        plan = _plan(
            observation=observation,
            semantics=semantics,
            tools=tools,
            policy_slot_ids=(f"model:{model_id}",),
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
            result = await session.run(
                ConductorRunRequest(task_input={"prompt": task_prompt}, context={})
            )
        finally:
            await session.close()
            await worker.close()
        system_prompt = worker.system_prompt
        operations = tuple(worker.operations)
        assert thaw_json(result.response["replay_trace"])["runtime_inputs"] == dict(
            worker.initialize_runtime_inputs
        )
        await client.close()
    return result, requests, sink.events, system_prompt, operations


@pytest.mark.asyncio
async def test_pi_native_stream_cap_batch_and_request_shape(tmp_path: Path) -> None:
    responses = [[
        ("a", "write", {"path": "a.txt", "content": "A\n"}),
        ("bad", "edit", {"path": "missing.txt", "edits": []}),
        ("c", "write", {"path": "c.txt", "content": "C\n"}),
    ]] + [[("loop", "bash", {"command": "printf loop"})]] * 7
    result, requests, events, system_prompt, operations = await _run_episode(tmp_path, responses)
    observations = [event for event in events if isinstance(event, ToolObservationEvent)]
    assert observations
    observation_order = [(event.turn, event.ordinal) for event in observations]
    assert sorted(event.call_id for event in observations[:3]) == ["a", "bad", "c"]
    assert sorted(event.ordinal for event in observations[:3]) == [0, 1, 2]
    assert observation_order[3:] == [(turn, 0) for turn in range(2, 9)]
    assert any(isinstance(event, SourceEventCommitEvent) for event in events)
    assert "ack" not in operations
    assert operations[-1] == "close"
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
    result, requests, _, system_prompt, _ = await _run_episode(tmp_path, [[]])
    assert len(requests) == 1
    assert requests[0]["messages"][0] == {"role": "system", "content": system_prompt}
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE

@pytest.mark.asyncio
async def test_pi_native_stream_replay_trace_matches_supplier_fixture(
    tmp_path: Path,
) -> None:
    responses = [
        [(
            "pi-tool-normal_workspace_episode-00-00",
            "write",
            {"path": "pi-marker.txt", "content": "pi-native-marker\n"},
        )],
        [(
            "pi-tool-normal_workspace_episode-01-00",
            "read",
            {"path": "pi-marker.txt"},
        )],
        [(
            "pi-tool-normal_workspace_episode-02-00",
            "bash",
            {"command": "printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\npi normal complete\n'"},
        )],
        [],
    ]
    (tmp_path / "AGENTS.md").write_text(
        "Pi capture fixture: use only the four admitted tools and leave requested effects in this workspace.\n",
        encoding="utf-8",
    )
    result, _, _, _, _ = await _run_episode(
        tmp_path,
        responses,
        model_id="gpt-4o-mini",
        task_prompt="Create pi-marker.txt, read it back, then print the completion marker.",
        assistant_texts=[
            "Writing marker.",
            "Reading marker.",
            "Complete.",
            "Final answer: pi normal complete.",
        ],
    )
    trace = thaw_json(result.response["replay_trace"])
    comparator = PiCodingAgent0731Comparator()
    report = comparator({
        "capture": {"case_dir": str(SUPPLIER_CASE)},
        "replay": {"trace": trace},
    })
    assert report["passed"] is True, report["assertions"][0]["detail"]
    tampered = deepcopy(trace)
    tampered["requests"][0]["messages"][0]["content"] += " tampered"
    tampered_report = comparator({
        "capture": {"case_dir": str(SUPPLIER_CASE)},
        "replay": {"trace": tampered},
    })
    assert tampered_report["passed"] is False

@pytest.mark.asyncio
async def test_pi_native_stream_requires_store_capability_before_sending(
    tmp_path: Path,
) -> None:
    with pytest.raises(RunnerDependencyError, match="policy runtime invocation failed"):
        await _run_episode(
            tmp_path,
            [[]],
            request_features=["max_tokens", "n", "stream_options", "streaming"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("empty", "advertisement"),
        ("missing_sha", "native_sha256"),
        ("wrong_sha", "hash mismatch"),
        ("unknown_tool", "exactly read"),
        ("extra_top", "advertisement keys"),
        ("extra_read", "read keys"),
        ("extra_prompt", "prompt keys"),
        ("removal_absent", "occur exactly once"),
        ("removal_twice", "occur exactly once"),
        ("removal_duplicate", "duplicate"),
        ("removal_overlap", "occur exactly once"),
    ],
)
async def test_pi_native_worker_rejects_invalid_advertisement(tmp_path: Path, case: str, message: str) -> None:
    workspace = tmp_path / case
    workspace.mkdir()
    advertisement = json.loads(
        (Path(__file__).parents[3] / "config/e4_targets/pi/0.73.1/native-config.json").read_text()
    )["advertisement"]
    if case == "empty":
        advertisement = {}
    elif case == "missing_sha":
        del advertisement["tools"]["read"]["native_sha256"]
    elif case == "wrong_sha":
        advertisement["tools"]["read"]["native_sha256"] = "sha256:" + ("0" * 64)
    elif case == "unknown_tool":
        advertisement["tools"]["bash"] = {"description": "unexpected", "native_sha256": "sha256:" + ("0" * 64)}
    elif case == "extra_top":
        advertisement["extra"] = True
    elif case == "extra_read":
        advertisement["tools"]["read"]["extra"] = True
    elif case == "extra_prompt":
        advertisement["prompt"]["extra"] = True
    elif case == "removal_absent":
        advertisement["prompt"]["remove_exact"] = ["not present in native prompt"]
    elif case == "removal_twice":
        advertisement["prompt"]["remove_exact"] = ["a"]
    elif case == "removal_duplicate":
        removal = advertisement["prompt"]["remove_exact"][0]
        advertisement["prompt"]["remove_exact"] = [removal, removal]
    elif case == "removal_overlap":
        removal = advertisement["prompt"]["remove_exact"][0]
        advertisement["prompt"]["remove_exact"] = [removal, removal[1:]]
    port = _NativeWorkerPort(workspace, ())
    try:
        with pytest.raises(RuntimeError, match=message):
            await port.invoke_native_phase(
                "initialize",
                {
                    "task": "invalid advertisement",
                    "model_config": {"id": "model-a", "provider": "openai", "input": ["text"]},
                    "advertisement": advertisement,
                },
                timeout_ms=5_000,
            )
    finally:
        await port.close()


@pytest.mark.asyncio
async def test_pi_native_worker_preserves_source_order_and_completion_order(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Pi native worker test context.\n", encoding="utf-8")
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
        assert initialized["system_prompt"] == _render_sealed_prompt(initialized["bootstrap"])
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
        await port.invoke_native_phase(
            "prepare_tools",
            {"calls": [{"id": "background", "name": "bash", "arguments": {"command": "sleep 30 & printf bg"}}]},
            timeout_ms=5_000,
        )
        await port.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        closed = await port.invoke_native_phase("close", {}, timeout_ms=5_000)
        assert closed["cleanup"]["all_dead"] is True
        assert closed["cleanup"]["processes"]
        for process in closed["cleanup"]["processes"]:
            with pytest.raises(ProcessLookupError):
                os.kill(process["pid"], 0)
    finally:
        await port.close()


@pytest.mark.asyncio
async def test_pi_native_worker_close_skips_already_dead_process_group(tmp_path: Path) -> None:
    port = _NativeWorkerPort(tmp_path, ())
    try:
        await port.invoke_native_phase(
            "initialize",
            {
                "task": "dead process group",
                "model_config": {"id": "model-a", "provider": "openai", "input": ["text"]},
            },
            timeout_ms=5_000,
        )
        await port.invoke_native_phase(
            "prepare_tools",
            {"calls": [{"id": "done", "name": "bash", "arguments": {"command": "printf done"}}]},
            timeout_ms=5_000,
        )
        await port.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        closed = await port.invoke_native_phase("close", {}, timeout_ms=5_000)
        assert closed["cleanup"] == {"processes": [], "all_dead": True}
    finally:
        await port.close()


@pytest.mark.asyncio
async def test_pi_native_worker_preserves_late_detached_stdout_until_wait_grace(tmp_path: Path) -> None:
    port = _NativeWorkerPort(tmp_path, ())
    try:
        await port.invoke_native_phase(
            "initialize",
            {
                "task": "late output",
                "model_config": {"id": "model-a", "provider": "openai", "input": ["text"]},
            },
            timeout_ms=5_000,
        )
        await port.invoke_native_phase(
            "prepare_tools",
            {"calls": [{"id": "late", "name": "bash", "arguments": {"command": "(sleep 0.05; printf late) &"}}]},
            timeout_ms=5_000,
        )
        executed = await port.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        assert executed["results"][0]["content"][0]["text"] == "late"
        await port.invoke_native_phase("close", {}, timeout_ms=5_000)
    finally:
        await port.close()


@pytest.mark.asyncio
async def test_pi_native_worker_leaves_detached_effect_until_close(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    port = _NativeWorkerPort(tmp_path, ())
    try:
        await port.invoke_native_phase(
            "initialize",
            {
                "task": "late effect",
                "model_config": {"id": "model-a", "provider": "openai", "input": ["text"]},
            },
            timeout_ms=5_000,
        )
        await port.invoke_native_phase(
            "prepare_tools",
            {
                "calls": [{
                    "id": "marker",
                    "name": "bash",
                    "arguments": {"command": f"(sleep 0.5; printf late > {marker}) & printf parent"},
                }],
            },
            timeout_ms=5_000,
        )
        await port.invoke_native_phase("execute_batch", {}, timeout_ms=5_000)
        await asyncio.sleep(0.7)
        assert marker.read_text(encoding="utf-8") == "late"
        closed = await port.invoke_native_phase("close", {}, timeout_ms=5_000)
        assert closed["cleanup"]["all_dead"] is True
    finally:
        await port.close()
