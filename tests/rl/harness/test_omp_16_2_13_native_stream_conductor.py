"""Replay the declared OMP 16.2.13 capture cases through the real Conductor.

Modeled on test_pi_0_57_1_native_stream_conductor.py: real Conductor, real
sandbox worker (omp_16_2_13_native_tool_worker.ts), SSE rebuilt from
scenario.json using the kit receiver algorithm, all declared cases
(o0-o12, o6b, f2), and verdicts evaluated via OhMyPi16213Comparator.
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import struct
import threading
from typing import Any, Callable, Mapping

import pytest

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.lease_envelope import RuntimeContainment
from breadboard.rl.harness.policy_provider import (
    E4TargetPolicyProjection,
    EpisodeOpenAICompletionsPolicyClient,
)
from breadboard.rl.harness.runners.base import (
    RunnerOpenRequest,
    RunnerProtocolError,
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
    OMP_16_2_13_RESPONSE_CONSUMER_ID,
    profile_identity_digest,
)
from breadboard_engine.e4_targets import load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from conformance.comparators.oh_my_pi_16_2_13 import OhMyPi16213Comparator
from tests.compilation.test_server_compiler import _options
from tests.rl.harness.test_runner_conductor import (
    CONDUCTOR_TEST_AUTHENTICATOR,
    CONDUCTOR_TEST_LEDGER,
    _tool_grant,
)
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan, _policy_capabilities
from tests.rl.harness.v2_service_fixtures import signed_containment_receipt

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "e4_parity" / "fixtures" / "omp_16_2_13_supplier_cases"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8")) if (FIXTURES / "manifest.json").is_file() else {"cases": []}
CASES = tuple(MANIFEST["cases"])
WORKER = REPO_ROOT / "breadboard" / "rl" / "harness" / "runners" / "omp_16_2_13_native_tool_worker.ts"
TOOL_ORDER = ("read", "bash", "edit", "write", "generate_image")
MODEL_ID = "Qwen/Qwen3.5-35B-A3B"
SLOT_MODEL_ID = "qwen3-5-35b-a3b"
UPSTREAM_CWD = "/testbed"
UPSTREAM_HOME = "/capture/home"
UPSTREAM_PACKAGE_DIR = "/opt/omp/node_modules/@oh-my-pi/pi-coding-agent"

_ROOT_ENV = os.environ.get("OMP16213_CODING_AGENT_NODE_MODULES")
if os.environ.get("BB_REQUIRE_PINNED_OMP16213_NODE") == "1" and not _ROOT_ENV:
    pytest.fail("BB_REQUIRE_PINNED_OMP16213_NODE=1 requires OMP16213_CODING_AGENT_NODE_MODULES")

_NODE_MODULES = Path(_ROOT_ENV) if _ROOT_ENV else None
if (
    _NODE_MODULES is not None
    and os.environ.get("BB_REQUIRE_PINNED_OMP16213_NODE") == "1"
    and not _NODE_MODULES.is_dir()
):
    pytest.fail(f"OMP16213_CODING_AGENT_NODE_MODULES is not a directory: {_NODE_MODULES}")

_BUN_BIN = shutil.which("bun")
worker_available = WORKER.is_file() and _NODE_MODULES is not None and _BUN_BIN is not None
pytestmark = pytest.mark.skipif(_NODE_MODULES is None or not worker_available, reason="OMP 16.2.13 pinned node modules or worker unavailable")


# --- packet case inputs -----------------------------------------------------

def _capture(case: str, name: str) -> Path:
    return FIXTURES / case / "capture" / name


def _rows(case: str) -> list[dict[str, Any]]:
    p = _capture(case, "http-transcript.jsonl")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


def _header_val(headers: Any, name: str) -> str:
    if isinstance(headers, dict):
        for k, v in headers.items():
            if k.lower() == name.lower():
                return str(v)
    elif isinstance(headers, list):
        for item in headers:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                if str(item[0]).lower() == name.lower():
                    return str(item[1])
    return "0"


def _logical_rows(case: str) -> list[dict[str, Any]]:
    """Upstream rows that began a logical turn (the SDK stamps retries 1..n)."""
    return [
        row for row in _rows(case)
        if _header_val(row["headers"] if "headers" in row else {}, "x-stainless-retry-count") == "0"
    ]


def _scenario(case: str) -> dict[str, Any]:
    return json.loads(_capture(case, "scenario.json").read_text(encoding="utf-8"))

def _upstream_body(row: Mapping[str, Any]) -> dict[str, Any]:
    if "body" in row and isinstance(row["body"], dict):
        return row["body"]
    return json.loads(base64.b64decode(row["raw_body_base64"]))
def _task_text(case: str) -> str:
    """The prompt upstream received: request 0's user text."""
    body = _upstream_body(_logical_rows(case)[0])
    for msg in body["messages"]:
        if "role" in msg and msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list) and content and content[0]["type"] == "text":
                return content[0]["text"]
            if isinstance(content, str):
                return content
    raise AssertionError(f"case {case} body messages has no user text prompt")


# --- SSE chunk builder from kit receiver algorithm -------------------------

def _sse_chunk(case_id: str, step: int, delta: Mapping[str, Any] | None, finish: str | None = None,
               usage: Mapping[str, Any] | None = None, no_choices: bool = False) -> bytes:
    value: dict[str, Any] = {
        "id": f"omp16213-{case_id}-{step:02d}",
        "object": "chat.completion.chunk",
        "created": 1724745600,
        "model": MODEL_ID,
        "choices": [] if no_choices else [{"index": 0, "delta": dict(delta or {}), "finish_reason": finish}],
    }
    if usage is not None:
        value["usage"] = dict(usage)
    return ("data: " + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")


def _tool_chunks(case_id: str, step: int, call: Mapping[str, Any], seq: int) -> list[bytes]:
    fragments = call["split"] if ("split" in call and call["split"] is not None) else [call["arguments"]]
    out = [_sse_chunk(case_id, step, {"role": "assistant", "tool_calls": [
        {"index": seq, "id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": ""}}]}, None)]
    for fragment in fragments:
        out.append(_sse_chunk(case_id, step, {"tool_calls": [{"index": seq, "function": {"arguments": fragment}}]}, None))
    return out


def _response_chunks(case_id: str, index: int, step: Mapping[str, Any]) -> list[bytes]:
    out: list[bytes] = []
    if "text_fragments" in step and step["text_fragments"] is not None:
        out.append(_sse_chunk(case_id, index, {"role": "assistant"}, None))
        for frag in step["text_fragments"]:
            out.append(_sse_chunk(case_id, index, {"content": frag}, None))
    elif "assistant_content" in step and step["assistant_content"] is not None:
        out.append(_sse_chunk(case_id, index, {"role": "assistant", "content": step["assistant_content"]}, None))
    for seq, call in enumerate(step["tool_calls"] if "tool_calls" in step else []):
        out.extend(_tool_chunks(case_id, index, call, seq))
    if "kind" in step and step["kind"] == "broken_stream":
        keep = step["keep_chunks"]
        return out[:keep]
    finish = step["finish_reason"] if "finish_reason" in step else ("tool_calls" if ("tool_calls" in step and step["tool_calls"]) else "stop")
    out.append(_sse_chunk(case_id, index, {}, finish))
    if "usage" in step and step["usage"] is not None:
        out.append(_sse_chunk(case_id, index, None, None, usage=step["usage"], no_choices=True))
    out.append(b"data: [DONE]\n\n")
    return out


def _served_responses(case: str) -> list[tuple[int, str, bytes]]:
    """Per logical turn: (status, content type, body bytes) exactly as upstream received it."""
    scenario = _scenario(case)
    served: list[tuple[int, str, bytes]] = []
    for row in _logical_rows(case):
        if "served" in row and row["served"] == "http_error":
            body = json.dumps(row["response"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            served.append((row["status"], "application/json", body))
        else:
            step_data = scenario["steps"][row["index"]]
            chunks = _response_chunks(scenario["case_id"], row["index"], step_data)
            served.append((200, "text/event-stream", b"".join(chunks)))
    return served


def _seed_workspace(case: str, workspace: Path) -> None:
    # Production seeds regular files only (named divergence workspace_regular_files_only).
    workspace.mkdir(parents=True, exist_ok=True)
    pre = json.loads(_capture(case, "workspace-manifest-pre.json").read_text(encoding="utf-8"))
    for path, entry in sorted(pre.items()):
        if entry["type"] != "file":
            continue
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((FIXTURES / "blobs" / entry["sha256"].removeprefix("sha256:")).read_bytes())
        os.chmod(target, int(entry["mode"], 8))


def _cases_data() -> dict[str, Any]:
    cases_file = FIXTURES / "omp16213_capture_cases.json"
    if not cases_file.is_file():
        raise FileNotFoundError(f"missing cases fixture file: {cases_file}")
    return json.loads(cases_file.read_text(encoding="utf-8"))["cases"]


# --- fake provider ----------------------------------------------------------

@contextmanager
def _provider(responses: list[tuple[int, str, bytes]], on_request: Callable[[int], None] | None = None):
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            return None

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"body": body, "headers": dict(self.headers)})
            ordinal = len(requests) - 1
            if ordinal >= len(responses):
                status, content_type, payload = 429, "application/json", b'{"error":{"message":"script exhausted"}}'
            else:
                status, content_type, payload = responses[ordinal]
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            if on_request is not None:
                on_request(ordinal)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


# --- worker port running framed worker --------------------------------------

class _Omp16213WorkerPort:
    def __init__(
        self,
        workspace: Path,
        scratch: Path,
        grants: tuple[RunnerToolBinding, ...],
    ) -> None:
        self.containment = RuntimeContainment.ATTESTED
        self.containment_lease_id = CONDUCTOR_TEST_LEDGER.record.lease_id
        self.containment_receipt = signed_containment_receipt(
            self.containment_lease_id, "sandbox", CONDUCTOR_TEST_AUTHENTICATOR
        )
        self.workspace = workspace
        self.scratch = scratch
        self.scratch.mkdir(parents=True, exist_ok=True)
        self._bindings = grants
        self.operations: list[str] = []
        self.prepared_ids: list[str] = []
        self.execute_started: asyncio.Event | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._effect_baseline: dict[str, dict[str, Any]] | None = None
    @property
    def declared_workspace(self) -> str:
        return str(self.workspace)

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]:
        return self._bindings

    def _package_dir(self, package_subpath: str) -> str:
        assert _NODE_MODULES is not None
        return str(_NODE_MODULES / Path(package_subpath.removeprefix("node_modules/")))

    def _snapshot_effects(self) -> dict[str, dict[str, Any]]:
        snapshot, _ = sandbox_module._workspace_effect_snapshot(
            self.workspace, exclude_root_git=False, max_total_bytes=1 << 30, max_inodes=1 << 16, max_depth=64,
        )
        return snapshot

    async def begin_native_workspace_effects(self) -> None:
        assert self._effect_baseline is None
        self._effect_baseline = self._snapshot_effects()

    async def measure_workspace_effects(self) -> Mapping[str, Mapping[str, Any]]:
        assert self._effect_baseline is not None
        current = self._snapshot_effects()
        changed: dict[str, Mapping[str, Any]] = {
            path: value for path, value in current.items()
            if path not in self._effect_baseline
            or (self._effect_baseline[path]["bytes"], self._effect_baseline[path]["sha256"]) != (value["bytes"], value["sha256"])
        }
        for path in self._effect_baseline.keys() - current.keys():
            changed[path] = {"exists": False}
        return changed

    def native_runtime_inputs(self, *, input_names: tuple[str, ...], package_subpath: str) -> Mapping[str, str]:
        values = {
            "cwd": str(self.workspace),
            "home": str(self.scratch / "home"),
            "current_date": "2026-09-26",
            "package_dir": self._package_dir(package_subpath),
        }
        return {name: values[name] for name in input_names}

    async def _ensure(self) -> None:
        if self._process is None:
            assert _BUN_BIN is not None
            assert _NODE_MODULES is not None
            env = dict(os.environ)
            for k in ("TERM_PROGRAM", "TERM_PROGRAM_VERSION", "TERM", "COLORTERM", "TERMINAL_EMULATOR", "WT_SESSION"):
                env.pop(k, None)
            env.update(
                HOME=str(self.scratch / "home"),
                OMP_NATIVE_WORKER_FRAMED="1",
                OMP16213_CODING_AGENT_NODE_MODULES=str(_NODE_MODULES),
                OMP_OFFLINE="1",
            )
            (self.scratch / "home").mkdir(parents=True, exist_ok=True)
            self._process = await asyncio.create_subprocess_exec(
                _BUN_BIN, str(WORKER), cwd=self.workspace, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

    async def invoke_native_phase(
        self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int, package_subpath: str | None = None,
    ) -> Mapping[str, Any]:
        await self._ensure()
        assert self._process is not None and self._process.stdin is not None and self._process.stdout is not None
        self._request_id += 1
        self.operations.append(operation)
        phase_payload = dict(payload)
        if operation == "initialize":
            assert package_subpath is not None
            phase_payload.setdefault("workspace", str(self.workspace))
            phase_payload.setdefault("scratch", str(self.scratch))
            phase_payload.setdefault("package_dir", self._package_dir(package_subpath))
        if operation == "prepare_tools":
            self.prepared_ids = [call["id"] for call in payload["calls"]]
        body = json.dumps({
            "schema_version": "bb.native-worker.rpc.v1", "request_id": self._request_id,
            "operation": operation, "payload": phase_payload,
        }, separators=(",", ":")).encode()
        self._process.stdin.write(struct.pack(">I", len(body)) + body)
        await self._process.stdin.drain()
        if operation == "execute_batch" and self.execute_started is not None:
            self.execute_started.set()
        header = await asyncio.wait_for(self._process.stdout.readexactly(4), timeout_ms / 1000)
        size = struct.unpack(">I", header)[0]
        envelope = json.loads((await asyncio.wait_for(self._process.stdout.readexactly(size), timeout_ms / 1000)).decode())
        if "error" in envelope:
            raise RuntimeError(envelope["error"])
        result = dict(envelope["result"])
        return result

    async def invoke_tool(self, tool_id: str, arguments: Mapping[str, Any], *, timeout_ms: int) -> Mapping[str, Any]:
        raise AssertionError("OMP 16.2.13 native stream must use invoke_native_phase")

    async def close_native_runtime(self) -> Mapping[str, Any]:
        self.operations.append("retire_runtime")
        await self.close()
        return {"kind": "closed", "cleanup": {"all_dead": True, "steps": []}}

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            if process.returncode is None:
                process.kill()
            await process.wait()


class _Events:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class _Cancellation:
    def raise_if_cancelled(self, checkpoint: str, *, turn: int | None = None, call_id: str | None = None) -> None:
        return None


def _compile_target(tmp_path: Path, profile: OpenAICompletionsProviderProfile):
    cas = FilesystemCAS(tmp_path / "target-cas")
    try:
        compiled = compile_e4_harness(
            load_e4_target("oh-my-pi-r2@16.2.13"),
            {},
            {
                "version": 2,
                "profile": {"name": "omp-16-2-13-native-stream-test"},
                "workspace": {"root": "workspace"},
                "provider_tools": {"use_native": True, "api_variant": "chat_completions"},
                "providers": {
                    "default_model": SLOT_MODEL_ID,
                    "models": [{
                        "id": SLOT_MODEL_ID,
                        "adapter": "openai",
                        "context_length": profile.context_window,
                        "route_handle_id": "route-a",
                        "credential_handle_id": "credential-a",
                        "params": {},
                        "response_policy": {
                            "schema_version": "bb.provider_native_response_policy.v1",
                            "consumer_id": OMP_16_2_13_RESPONSE_CONSUMER_ID,
                            "provider_profile_digest": profile_identity_digest(profile),
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


def _profile(base_url: str) -> OpenAICompletionsProviderProfile:
    return OpenAICompletionsProviderProfile(
        model=MODEL_ID,
        scoped_credential="episode-secret",
        base_url=base_url,
        context_window=200_000,
        max_output_tokens=2048,
        caller_headers={},
        request_policy={
            "mode": "streaming",
            "include_usage": True,
            "max_token_field": "max_completion_tokens",
            "strict_tools": None,
            "enable_thinking": None,
            "preserve_thinking": True,
            "chat_template_kwargs": {"preserve_thinking": True},
        },
        capabilities={
            "supports_store": False,
            "supports_tools": True,
            "supports_strict_tools": False,
            "supports_thinking_control": True,
            "supports_max_completion_tokens": True,
            "supports_stream_options": True,
        },
        sampling={"n": 1},
    )


class _Episode:
    def __init__(self, result: Any, error: BaseException | None, requests: list[dict[str, Any]],
                 port: _Omp16213WorkerPort) -> None:
        self.result = result
        self.error = error
        self.requests = requests
        self.port = port

    @property
    def trace(self) -> dict[str, Any]:
        return thaw_json(self.result.response["replay_trace"])


async def _run_episode(
    tmp_path: Path,
    responses: list[tuple[int, str, bytes]],
    *,
    task: str,
    seed: Callable[[Path], None],
    cancel_on_tool_call: str | None = None,
) -> _Episode:
    workspace = tmp_path / "workspace"
    seed(workspace)
    with _provider(responses) as (base_url, requests):
        profile = _profile(base_url)
        projection, semantics, manifest = _compile_target(tmp_path, profile)
        features = ["chat_template_kwargs", "max_completion_tokens", "n", "preserve_thinking", "stream_options", "streaming"]
        observation = _observation(
            provider_id="openai", model_id=SLOT_MODEL_ID,
            capabilities=_policy_capabilities(tool_calling=True, request_features=sorted(features)),
        )
        tools = tuple(_tool_grant(name) for name in sorted(TOOL_ORDER))
        plan = _plan(
            observation=observation, semantics=semantics, tools=tools,
            policy_slot_ids=(f"model:{SLOT_MODEL_ID}",),
            limit_updates={"max_turns": 8, "action_timeout_ms": 40_000},
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
        port = _Omp16213WorkerPort(
            workspace, tmp_path / "scratch",
            tuple(RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools),
        )
        client = EpisodeOpenAICompletionsPolicyClient(
            episode_id="episode-omp-16213", effective_plan_digest=plan.canonical_digest(),
            observation=observation, profile=profile, target_projection=projection, timeout_seconds=45,
        )
        open_request = RunnerOpenRequest(episode_id="episode-omp-16213", effective_plan=plan)
        session = await ConductorAdapter(
            CONDUCTOR_RUNTIME_ABI,
            containment_authenticator=CONDUCTOR_TEST_AUTHENTICATOR,
            admitted_lease_ledger=CONDUCTOR_TEST_LEDGER,
        ).open(
            open_request, policy=PolicyRuntimeBinding(open_request, client), workspace=port,
            cancellation=_Cancellation(), events=_Events(),
        )
        result: Any = None
        error: BaseException | None = None
        try:
            run = asyncio.create_task(session.run(ConductorRunRequest(task_input={"prompt": task}, context={})))
            if cancel_on_tool_call is not None:
                port.execute_started = asyncio.Event()
                while True:
                    await port.execute_started.wait()
                    port.execute_started.clear()
                    if cancel_on_tool_call in port.prepared_ids:
                        break
                await asyncio.sleep(0.5)
                run.cancel()
            try:
                result = await run
            except (asyncio.CancelledError, RunnerProtocolError) as exc:
                error = exc
        finally:
            await session.close()
            await port.close()
            await client.close()
    return _Episode(result, error, requests, port)


def _compare(case: str, trace: Mapping[str, Any], process: Mapping[str, Any] | None = None) -> dict[str, Any]:
    replay: dict[str, Any] = {"trace": deepcopy(dict(trace))}
    if process is not None:
        replay["process"] = process
    return OhMyPi16213Comparator()({"capture": {"case_dir": str(FIXTURES / case)}, "replay": replay})


# Per-case replay tests running as far as the worker allows:

@pytest.mark.skipif(not worker_available, reason="omp_16_2_13_native_tool_worker.ts or node root unavailable")
@pytest.mark.parametrize("case", CASES)
def test_omp_16_2_13_declared_case_replays_through_conductor(tmp_path: Path, case: str) -> None:
    # Only the workspace is seeded: production gives the worker a lease-owned native
    # scratch (its PI_CODING_AGENT_DIR) that no episode input can populate.
    def _seed(ws: Path) -> None:
        _seed_workspace(case, ws)

    if case == "declared__o4_bash_timeout_nonzero_cancel":
        case_key = case.removeprefix("declared__")
        anchor = _cases_data()[case_key]["signal_cancel_on_tool_start"]
        episode = asyncio.run(_run_episode(
            tmp_path, _served_responses(case), task=_task_text(case),
            seed=_seed, cancel_on_tool_call=anchor,
        ))
        assert isinstance(episode.error, asyncio.CancelledError)
        expected_requests = _rows(case)
        assert len(episode.requests) == len(expected_requests)
        assert episode.result is None
        return

    episode = asyncio.run(_run_episode(
        tmp_path, _served_responses(case), task=_task_text(case),
        seed=_seed,
    ))
    assert episode.error is None
    trace = episode.trace
    report = _compare(case, trace)
    assert report["passed"] is True, f"{case} failed: {report['findings']}"
    assert report["verdict"] in {"exact", "normalized", "named_divergence"}
