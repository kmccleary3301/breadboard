"""Replay the r6 declared Pi 0.57.1 capture cases through the real Conductor.

Each case runs the real ``ConductorAdapter`` session, the real framed
``pi_tools_0_57_1.mjs`` worker on the pinned 0.57.1 node root
(``PI057_CODING_AGENT_NODE_MODULES``) and ``Pi0571SemanticsState``.  A local
HTTP server serves the case's captured responses: BB sends one request per
logical turn, so request ``k`` receives the response upstream received for its
``k``-th ``X-Stainless-Retry-Count: 0`` request.  The resulting replay trace is
judged by ``PiCodingAgent0571Comparator`` against the packet case.
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
import subprocess
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
from breadboard.rl.harness.runners.base import RunnerOpenRequest
from breadboard_engine.compilation.provider_response import (
    NativeResponseBindingError,
    PI_0_57_1_RESPONSE_CONSUMER_ID,
    profile_identity_digest,
)
from breadboard_engine.e4_targets import load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from conformance.comparators.pi_coding_agent_0_57_1 import PiCodingAgent0571Comparator
from tests.compilation.test_server_compiler import _options
from tests.rl.harness.test_runner_conductor import (
    CONDUCTOR_TEST_AUTHENTICATOR,
    CONDUCTOR_TEST_LEDGER,
    _tool_grant,
)
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan, _policy_capabilities
from tests.rl.harness.v2_service_fixtures import signed_containment_receipt

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "e4_parity" / "fixtures" / "pi_0_57_1_supplier_cases"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
CASES = tuple(MANIFEST["cases"])
WORKER = REPO_ROOT / "breadboard" / "rl" / "harness" / "pi_tools_0_57_1.mjs"
NATIVE_CONFIG = json.loads((REPO_ROOT / "config/e4_targets/pi/0.57.1-r3/native-config.json").read_text(encoding="utf-8"))
TOOL_ORDER = tuple(NATIVE_CONFIG["tools"]["ordered"])
MODEL_ID = "Qwen/Qwen3.5-35B-A3B"
# BB policy slot id (identifiers exclude path syntax); the wire model is MODEL_ID.
SLOT_MODEL_ID = "qwen3-5-35b-a3b"
UPSTREAM_CWD = "/testbed"
UPSTREAM_HOME = "/capture/home"
UPSTREAM_PACKAGE_DIR = "/opt/bb-pi-runtime/pi/node_modules/@mariozechner/pi-coding-agent"

_ROOT_ENV = os.environ.get("PI057_CODING_AGENT_NODE_MODULES")
if os.environ.get("BB_REQUIRE_PINNED_PI057_NODE") == "1" and not _ROOT_ENV:
    pytest.fail("BB_REQUIRE_PINNED_PI057_NODE=1 requires PI057_CODING_AGENT_NODE_MODULES")
_NODE_MODULES = Path(_ROOT_ENV) if _ROOT_ENV else None
if (
    os.environ.get("BB_REQUIRE_PINNED_PI057_NODE") == "1"
    and not (_NODE_MODULES / "@mariozechner/pi-coding-agent/dist/index.js").is_file()
):
    pytest.fail(f"required pinned Pi 0.57.1 node_modules root is unavailable: {_NODE_MODULES}")
pytestmark = pytest.mark.skipif(_NODE_MODULES is None, reason="PI057_CODING_AGENT_NODE_MODULES is unset")
# The declared R3 runtime provisions neither fd nor rg (packet identity
# fd_rg_provisioned=false); the worker fails closed if either resolves, so its
# PATH holds only node and the system tool directories.
_NODE_BINARY = shutil.which("node")
assert _NODE_BINARY is not None
_WORKER_PATH = os.pathsep.join((str(Path(_NODE_BINARY).parent), "/usr/bin", "/bin"))
assert shutil.which("fd", path=_WORKER_PATH) is None and shutil.which("rg", path=_WORKER_PATH) is None


# --- packet case inputs -----------------------------------------------------

def _capture(case: str, name: str) -> Path:
    return FIXTURES / case / "capture" / name


def _rows(case: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _capture(case, "http-transcript.jsonl").read_text(encoding="utf-8").splitlines()]


def _logical_rows(case: str) -> list[dict[str, Any]]:
    """Upstream rows that began a logical turn (the SDK stamps retries 1..n)."""
    return [row for row in _rows(case) if row["headers"]["X-Stainless-Retry-Count"] == "0"]


def _upstream_body(row: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(base64.b64decode(row["raw_body_base64"]))


def _scenario(case: str) -> dict[str, Any]:
    return json.loads(_capture(case, "scenario.json").read_text(encoding="utf-8"))


def _task_text(case: str) -> str:
    """The prompt upstream received: request 0's user text (the R3 run_task.py wrapper)."""
    [part] = _upstream_body(_logical_rows(case)[0])["messages"][1]["content"]
    assert part["type"] == "text"
    return part["text"]


# The chunk builder below reproduces the capture receiver that served the
# packet (hist-parity/kit-pi057-r6/pi057_capture_receiver.py, sha256
# 87a0caea37d9c409a03afaf90b1fabec8885ba9cdae804e2881c49f59af0a30c,
# sse_chunk/tool_chunks/response_chunks).  The transcript records only the
# request side plus chunk_count/finish_reason; both are asserted per row.

def _sse(case_id: str, step: int, delta: Mapping[str, Any], finish: str | None = None,
         usage: Mapping[str, Any] | None = None) -> bytes:
    value: dict[str, Any] = {
        "id": f"pi057-{case_id}-{step:02d}",
        "object": "chat.completion.chunk",
        "created": 1724745600,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": dict(delta), "finish_reason": finish}],
    }
    if usage is not None:
        value["usage"] = dict(usage)
    return ("data: " + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")


def _response_chunks(case_id: str, index: int, step: Mapping[str, Any]) -> list[bytes]:
    out: list[bytes] = []
    if "text_fragments" in step:
        out.append(_sse(case_id, index, {"role": "assistant"}))
        out.extend(_sse(case_id, index, {"content": fragment}) for fragment in step["text_fragments"])
    elif step.get("assistant_content"):
        out.append(_sse(case_id, index, {"role": "assistant", "content": step["assistant_content"]}))
    for seq, call in enumerate(step.get("tool_calls", [])):
        out.append(_sse(case_id, index, {"role": "assistant", "tool_calls": [{
            "index": seq, "id": call["id"], "type": "function",
            "function": {"name": call["name"], "arguments": ""},
        }]}))
        for fragment in call["split"] if "split" in call else [call["arguments"]]:
            out.append(_sse(case_id, index, {"tool_calls": [{"index": seq, "function": {"arguments": fragment}}]}))
    finish = step["finish_reason"] if "finish_reason" in step else ("tool_calls" if step.get("tool_calls") else "stop")
    out.append(_sse(case_id, index, {}, finish, step["usage"] if "usage" in step else None))
    out.append(b"data: [DONE]\n\n")
    return out


def _served_responses(case: str) -> list[tuple[int, str, bytes]]:
    """Per logical turn: (status, content type, body bytes) exactly as upstream received it."""
    scenario = _scenario(case)
    served: list[tuple[int, str, bytes]] = []
    for row in _logical_rows(case):
        if row["served"] == "completion":
            chunks = _response_chunks(scenario["case_id"], row["index"], scenario["steps"][row["index"]])
            assert len(chunks) == row["chunk_count"]
            assert json.loads(chunks[-2][len(b"data: "):])["choices"][0]["finish_reason"] == row["finish_reason"]
            served.append((200, "text/event-stream", b"".join(chunks)))
        else:
            assert row["served"] == "http_error"
            body = json.dumps(row["response"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            served.append((row["status"], "application/json", body))
    return served


def _seed_workspace(case: str, workspace: Path) -> None:
    """Seed what production can seed: the regular files of workspace-manifest-pre.json.

    Production containment admits only regular files (sandbox.py:3717-3721)
    and its workspace seed is files-only, so the golden symlink, empty
    directories and the manifest's extra ``.git`` directory are absent; the
    comparator names that as ``workspace_regular_files_only``.
    """
    workspace.mkdir()
    pre = json.loads(_capture(case, "workspace-manifest-pre.json").read_text(encoding="utf-8"))
    for path in sorted(pre):
        entry = pre[path]
        if entry["type"] != "file":
            continue
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (FIXTURES / "blobs" / entry["sha256"].removeprefix("sha256:")).read_bytes()
        assert len(data) == entry["bytes"]
        target.write_bytes(data)
        os.chmod(target, int(entry["mode"], 8))


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


# --- sandbox port running the real framed worker ----------------------------

class _Pi057WorkerPort:
    def __init__(self, workspace: Path, scratch: Path, grants: tuple[RunnerToolBinding, ...]) -> None:
        assert _NODE_MODULES is not None
        self.containment = RuntimeContainment.ATTESTED
        self.containment_lease_id = CONDUCTOR_TEST_LEDGER.record.lease_id
        self.containment_receipt = signed_containment_receipt(
            self.containment_lease_id, "sandbox", CONDUCTOR_TEST_AUTHENTICATOR
        )
        self.workspace = workspace
        self.scratch = scratch
        self.scratch.mkdir()
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
            "package_dir": self._package_dir(package_subpath),
        }
        assert set(input_names) == set(values)
        return {name: values[name] for name in input_names}

    async def _ensure(self) -> None:
        if self._process is None:
            env = dict(os.environ)
            env.update(
                PATH=_WORKER_PATH,
                PI_NATIVE_WORKER_FRAMED="1",
                PI_CODING_AGENT_NODE_MODULES=str(_NODE_MODULES),
                PI_OFFLINE="1",
            )
            self._process = await asyncio.create_subprocess_exec(
                "node", str(WORKER), cwd=self.workspace, env=env,
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
        return envelope["result"]

    async def invoke_tool(self, tool_id: str, arguments: Mapping[str, Any], *, timeout_ms: int) -> Mapping[str, Any]:
        raise AssertionError("Pi 0.57.1 native stream must use invoke_native_phase")

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
            load_e4_target("pi-r3@0.57.1"),
            {},
            {
                "version": 2,
                "profile": {"name": "pi-0-57-1-native-stream-test"},
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
                            "consumer_id": PI_0_57_1_RESPONSE_CONSUMER_ID,
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


def _profile(base_url: str, *, strict_tools: bool | None = False) -> OpenAICompletionsProviderProfile:
    # R3 custody models.json: contextWindow 200000, maxTokens 32000; pinned
    # buildBaseOptions sends min(model.maxTokens, 32000) (simple-options.js:4).
    return OpenAICompletionsProviderProfile(
        model=MODEL_ID,
        scoped_credential="episode-secret",
        base_url=base_url,
        context_window=200_000,
        max_output_tokens=32_000,
        caller_headers={},
        request_policy={"mode": "streaming", "include_usage": True, "strict_tools": strict_tools, "enable_thinking": None},
        capabilities={"supports_store": False, "supports_strict_tools": True},
    )


class _Episode:
    def __init__(self, result: Any, error: BaseException | None, requests: list[dict[str, Any]],
                 port: _Pi057WorkerPort) -> None:
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
    strict_tools: bool | None = False,
    cancel_on_tool_call: str | None = None,
) -> _Episode:
    workspace = tmp_path / "workspace"
    seed(workspace)
    with _provider(responses) as (base_url, requests):
        profile = _profile(base_url, strict_tools=strict_tools)
        projection, semantics, manifest = _compile_target(tmp_path, profile)
        features = ["max_tokens", "n", "stream_options", "streaming"] + (["strict_tools"] if strict_tools is not None else [])
        observation = _observation(
            provider_id="openai", model_id=SLOT_MODEL_ID,
            capabilities=_policy_capabilities(request_features=sorted(features)),
        )
        tools = tuple(_tool_grant(name) for name in sorted(TOOL_ORDER))
        plan = _plan(
            observation=observation, semantics=semantics, tools=tools,
            policy_slot_ids=(f"model:{SLOT_MODEL_ID}",),
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
        port = _Pi057WorkerPort(
            workspace, tmp_path / "scratch",
            tuple(RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools),
        )
        client = EpisodeOpenAICompletionsPolicyClient(
            episode_id="episode-pi-0571", effective_plan_digest=plan.canonical_digest(),
            observation=observation, profile=profile, target_projection=projection, timeout_seconds=45,
        )
        open_request = RunnerOpenRequest(episode_id="episode-pi-0571", effective_plan=plan)
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
                # External cancel while the anchor tool executes (D10 P4 anchor).
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


def _run_case(tmp_path: Path, case: str, **kwargs: Any) -> _Episode:
    return asyncio.run(_run_episode(
        tmp_path, _served_responses(case), task=_task_text(case),
        seed=lambda workspace: _seed_workspace(case, workspace), **kwargs,
    ))


def _compare(case: str, trace: Mapping[str, Any], process: Mapping[str, Any] | None = None) -> dict[str, Any]:
    replay: dict[str, Any] = {"trace": deepcopy(dict(trace))}
    if process is not None:
        replay["process"] = process
    return PiCodingAgent0571Comparator()({"capture": {"case_dir": str(FIXTURES / case)}, "replay": replay})


def _to_upstream(value: Any, runtime: Mapping[str, str], upstream_date_time: str) -> Any:
    """Map BB's own runtime roots onto upstream's; literal task text is untouched."""
    if isinstance(value, str):
        value = value.replace(
            f"Current date and time: {runtime['current_date_time']}", f"Current date and time: {upstream_date_time}",
        )
        value = value.replace(runtime["package_dir"], UPSTREAM_PACKAGE_DIR)
        value = value.replace(runtime["home"], UPSTREAM_HOME)
        return value.replace(runtime["cwd"], UPSTREAM_CWD)
    if isinstance(value, list):
        return [_to_upstream(item, runtime, upstream_date_time) for item in value]
    if isinstance(value, dict):
        return {key: _to_upstream(item, runtime, upstream_date_time) for key, item in value.items()}
    return value


def _upstream_date_time(case: str) -> str:
    system = _upstream_body(_logical_rows(case)[0])["messages"][0]["content"]
    return next(line for line in system.splitlines() if line.startswith("Current date and time: ")).split(": ", 1)[1]


# --- per-case replay verdicts -------------------------------------------------

BASE_DIVERGENCES = ["sdk_transport_headers", "json_member_order"]
# D4/D7 named divergences; everything else in each case compares exactly.
EXPECTED_DIVERGENCES = {
    "declared__p0_text_stop": BASE_DIVERGENCES,
    "declared__p1_all_tools_sequence": BASE_DIVERGENCES + ["tool_download_attempt", "workspace_regular_files_only"],
    # Pinned parseStreamingJson/validateToolArguments run in the worker, so the
    # unknown-tool, malformed and split calls match upstream with no repair divergence.
    "declared__p2_fragmented_malformed_repair": BASE_DIVERGENCES,
    "declared__p3_context_compaction": BASE_DIVERGENCES + ["compaction_start_then_exit"],
    "declared__p5_retry_default_http500_exhausted": BASE_DIVERGENCES + ["sdk_hidden_transport_retry"],
    "declared__p6_disabled_resources_and_agents_md": BASE_DIVERGENCES,
    "declared__p7_retry_session_recovers": BASE_DIVERGENCES + ["sdk_hidden_transport_retry"],
    "declared__p8_retry_sdk_recovers": BASE_DIVERGENCES + ["sdk_hidden_transport_retry"],
}


@pytest.mark.parametrize("case", sorted(EXPECTED_DIVERGENCES))
def test_declared_case_replays_through_the_conductor(tmp_path: Path, case: str) -> None:
    episode = _run_case(tmp_path, case)
    assert episode.error is None
    assert len(episode.requests) == len(_logical_rows(case))
    trace = episode.trace
    assert [request["body"] for request in episode.requests] == trace["requests"]
    report = _compare(case, trace)
    assert report["verdict"] == "named_divergence", report["findings"]
    assert [item["name"] for item in report["divergences"]] == EXPECTED_DIVERGENCES[case]
    if "sdk_hidden_transport_retry" in EXPECTED_DIVERGENCES[case]:
        assert episode.result.termination == RunnerTermination.POLICY_INCOMPLETE
        assert trace["termination"] == {"kind": "error", "native_stop_reason": "error"}


def test_p4_external_cancel_at_the_anchor(tmp_path: Path) -> None:
    """The conductor emits no replay trace when cancelled, so the comparator's
    external_cancel_signal rule is covered only by the DO-2 installed replay;
    here the observable prefix is checked: CancelledError at the anchor and the
    four sent bodies equal upstream's with BB's own roots mapped back."""
    case = "declared__p4_bash_timeout_nonzero_cancel"
    anchor = _scenario(case)["signal_cancel_on_tool_start"]
    episode = _run_case(tmp_path, case, cancel_on_tool_call=anchor)
    assert isinstance(episode.error, asyncio.CancelledError)
    assert episode.port.prepared_ids == [anchor]
    upstream = [_upstream_body(row) for row in _logical_rows(case)]
    assert len(episode.requests) == len(upstream) == 4
    runtime = {
        "cwd": str(episode.port.workspace), "home": str(episode.port.scratch / "home"),
        "package_dir": str(_NODE_MODULES / "@mariozechner/pi-coding-agent"),
    }
    system = episode.requests[0]["body"]["messages"][0]["content"]
    runtime["current_date_time"] = next(
        line for line in system.splitlines() if line.startswith("Current date and time: ")
    ).split(": ", 1)[1]
    observed = [_to_upstream(request["body"], runtime, _upstream_date_time(case)) for request in episode.requests]
    if subprocess.run(["/bin/bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, check=True).stdout.strip() == "3":
        # Host environment, not the clone: the capture ran bash 5 (Linux), whose
        # command-not-found line carries "line 1: "; macOS /bin/bash 3.2 omits it.
        upstream = json.loads(json.dumps(upstream).replace("/bin/bash: line 1: ", "/bin/bash: "))
    assert observed == upstream


def test_caller_profile_drift_is_refused_before_any_request(tmp_path: Path) -> None:
    # strict_tools None would drop the pinned `strict: false` member
    # (openai-completions.js:604-615); the compiled Pi 0.57.1 source-profile
    # admission refuses that profile before the Conductor opens, so no request
    # is sent.  The Conductor's own native_request_body_mismatch guard is
    # covered in test_pi_0_57_1_engine.test_request_body_mismatch_is_typed.
    with pytest.raises(NativeResponseBindingError, match="compiled source profile"):
        _run_case(tmp_path, "declared__p0_text_stop", strict_tools=None)


def test_non_json_http_502_body_reaches_the_pinned_error_message(tmp_path: Path) -> None:
    body = "<html><body>502 Bad Gateway</body></html>"
    case = "declared__p0_text_stop"
    episode = asyncio.run(_run_episode(
        tmp_path, [(502, "text/html", body.encode())], task=_task_text(case),
        seed=lambda workspace: _seed_workspace(case, workspace),
    ))
    assert episode.error is None and len(episode.requests) == 1
    assert episode.result.termination == RunnerTermination.POLICY_INCOMPLETE
    terminal = episode.trace["messages"][-1]
    # openai 6.26.0 client makeRequest passes a non-JSON body as the message;
    # core/error.js makeMessage renders `${status} ${message}`.
    assert (terminal["stopReason"], terminal["errorMessage"]) == ("error", f"502 {body}")
