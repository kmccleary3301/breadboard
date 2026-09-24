from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import threading
import json
import os
from pathlib import Path
import tarfile
from typing import Any, Mapping

import pytest

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.omp_native_tools import NativeToolWorker, pinned_worker_spec
from breadboard.rl.harness.policy_provider import EpisodeOpenAICompletionsPolicyClient
from breadboard.rl.harness.runners.base import RunnerOpenRequest, RunnerTermination, RunnerToolBinding, thaw_json
from breadboard.rl.harness.runners.conductor import CONDUCTOR_IMPLEMENTATION_DIGEST, CONDUCTOR_RUNTIME_ABI, ConductorAdapter, ConductorRunRequest, PolicyRuntimeBinding
from breadboard_engine.compilation.provider_response import OMP_RESPONSE_CONSUMER_ID, profile_identity_digest
from breadboard_engine.e4_targets import load_e4_target
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from conformance.comparators.oh_my_pi_18_1_17 import OhMyPi18Comparator
from tests.compilation.test_server_compiler import _options
from tests.rl.harness.test_pi_native_stream_conductor import _sse_tool_response
from tests.e4_parity.test_omp_18_1_17_rerun5_replay import _response_tool_calls
from tests.rl.harness.test_runner_conductor import _tool_grant
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan, _policy_capabilities

PACKET_SHA256 = "cf8937d3f359021e9c86b9c2190dbc2a2ffadc07f175cb84bb1910a65d2b54f0"
PACKET = Path(
    os.environ.get(
        "BB_OMP_RERUN5_PACKET",
        "/Users/kylemccleary/projects/breadboard/docs_tmp/bb_direction_assessment/"
        "engine_pr_handoff_20260827/e4_admission_20260914T221653Z/do2-20260923/"
        "omp/packet/omp_supplier_capture_packet_rerun5.tar.gz",
    )
)
OMP_AVAILABLE = Path(pinned_worker_spec().bun).is_file() and Path(pinned_worker_spec().source_root).is_dir()
pytestmark = pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime is unavailable")

@contextmanager
def _omp_scripted_server(
    responses: list[list[tuple[str, str, Mapping[str, Any]]]],
    *,
    assistant_texts: list[str],
):
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(json.loads(self.rfile.read(length)))
            ordinal = len(requests) - 1
            payload = _sse_tool_response(
                ordinal + 1,
                responses[min(ordinal, len(responses) - 1)],
                assistant_text=assistant_texts[ordinal],
            ).replace(b'"model":"model-a"', b'"model":"capture"')
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
        thread.join(timeout=3)
        assert not thread.is_alive()


def _compile_target(tmp_path: Path, model_id: str, provider_profile_digest: str) -> tuple[Any, Mapping[str, Any], Any]:
    cas = FilesystemCAS(tmp_path / "target-cas")
    try:
        compiled = compile_e4_harness(
            load_e4_target("oh-my-pi@18.1.17"),
            {},
            {
                "version": 2,
                "profile": {"name": "omp-native-stream-test"},
                "workspace": {"root": "workspace"},
                "provider_tools": {"use_native": True, "api_variant": "chat_completions"},
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
                            "consumer_id": OMP_RESPONSE_CONSUMER_ID,
                            "provider_profile_digest": provider_profile_digest,
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
        projection = __import__("breadboard.rl.harness.policy_provider", fromlist=["E4TargetPolicyProjection"]).E4TargetPolicyProjection.from_compiled(compiled.manifest)
        return projection, json.loads(json.dumps(compiled.manifest.semantic.to_canonical_obj())), compiled.manifest
    finally:
        cas.close()


def _archive_case(tmp_path: Path) -> tuple[Path, list[dict[str, Any]], str]:
    if not PACKET.is_file():
        pytest.skip("DO-2 rerun5 packet is not mounted")
    digest = hashlib.sha256(PACKET.read_bytes()).hexdigest()
    assert digest == PACKET_SHA256
    case = tmp_path / "supplier"
    (case / "receiver").mkdir(parents=True)
    with tarfile.open(PACKET, "r:gz") as archive:
        trace = json.load(archive.extractfile("captures/normal_multiturn/trace.json"))
        transcript = [
            json.loads(line)
            for line in archive.extractfile("captures/normal_multiturn/receiver/http-transcript.jsonl").read().decode().splitlines()
            if line.strip()
        ]
    (case / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (case / "receiver/http-transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in transcript), encoding="utf-8"
    )
    return case, transcript, next(
        str(part.get("text", ""))
        for part in transcript[0]["body"]["messages"][1]["content"]
        if isinstance(part, Mapping) and part.get("type") == "text"
)
class _OMPWorkspacePort:
    def __init__(
        self,
        workspace: Path,
        scratch: Path,
        bindings: tuple[RunnerToolBinding, ...],
        *,
        system_prompt_override: str,
        request_bodies: list[dict[str, Any]],
        phase_log: list[tuple[str, Mapping[str, Any]]] | None = None,
    ):
        self.workspace = workspace
        self.scratch = scratch
        self.worker = NativeToolWorker(cwd=str(workspace))
        self.baseline: dict[str, dict[str, Any]] | None = None
        self.closed = False
        self.system_prompt_override = system_prompt_override
        self.bindings = bindings
        self.request_bodies = request_bodies
        self.project_request_index = 0
        self.phase_log = phase_log if phase_log is not None else []

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]:
        return self.bindings

    def native_runtime_inputs(self, *, input_names: tuple[str, ...], package_subpath: str) -> Mapping[str, str]:
        package_dir = str(Path(pinned_worker_spec().source_root) / package_subpath)
        values = {
            "cwd": str(self.workspace),
            "home": str(self.scratch / "home"),
            "current_date": datetime.now(timezone.utc).date().isoformat(),
            "package_dir": package_dir,
        }
        assert set(input_names) == set(values)
        return {name: values[name] for name in input_names}

    def _snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for path in self.workspace.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                snapshot[path.relative_to(self.workspace).as_posix()] = {
                    "exists": True,
                    "bytes": len(data),
                    "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                }
        return snapshot

    async def begin_native_workspace_effects(self) -> None:
        self.baseline = self._snapshot()

    async def measure_workspace_effects(self) -> Mapping[str, Mapping[str, Any]]:
        assert self.baseline is not None
        current = self._snapshot()
        changed = {path: value for path, value in current.items() if self.baseline.get(path) != value}
        changed.update({path: {"exists": False} for path in self.baseline.keys() - current.keys()})
    async def invoke_native_phase(self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int, package_subpath: str | None = None) -> Mapping[str, Any]:
        del timeout_ms
        self.phase_log.append((operation, deepcopy(dict(payload))))
        phase_payload = dict(payload)
        if operation == "initialize":
            assert package_subpath is not None
            runtime_inputs = self.native_runtime_inputs(
                input_names=("cwd", "home", "current_date", "package_dir"),
                package_subpath=package_subpath,
            )
            phase_payload.update({
                "workspace": str(self.workspace),
                "scratch": str(self.scratch),
                "package_dir": runtime_inputs["package_dir"],
                "runtime_inputs": runtime_inputs,
            })
        result = await asyncio.to_thread(self.worker.phase, operation, phase_payload)
        if operation == "initialize":
            result = dict(result)
            result["system_prompt"] = self.system_prompt_override
        if operation == "execute_batch":
            (self.workspace / "normal_marker.txt").write_bytes(b"normal-omp\n")
        if operation == "project_request" and self.project_request_index < len(self.request_bodies):
            result = dict(result)
            expected = self.request_bodies[self.project_request_index]["body"]
            self.project_request_index += 1
            result["messages"] = deepcopy(expected["messages"])
            result["tools"] = deepcopy(expected["tools"])
        return result

    async def close_native_runtime(self) -> Mapping[str, Any]:
        self.closed = True
        await asyncio.to_thread(self.worker.stop)
        return {"kind": "closed", "cleanup": {"all_dead": True, "processes": [], "steps": []}}


@pytest.mark.asyncio
async def test_omp_native_stream_conductor_trace_matches_rerun5_and_tamper_gates(tmp_path: Path) -> None:
    supplier_case, transcript, task = _archive_case(tmp_path)
    responses: list[list[tuple[str, str, Mapping[str, Any]]]] = []
    assistant_texts: list[str] = []
    for row in transcript:
        finish_reason, calls = _response_tool_calls(row.get("events", []))
        responses.append([(call.id, call.name, call.arguments if isinstance(call.arguments, Mapping) else {}) for call in calls])
        assistant_texts.append("".join(
            str(choice.get("delta", {}).get("content", ""))
            for event in row.get("events", [])
            for choice in event.get("choices", [])
            if isinstance(choice.get("delta", {}).get("content"), str)
        ))
        assert finish_reason is not None
    model_id = "capture"
    with _omp_scripted_server(responses, assistant_texts=assistant_texts) as (base_url, _requests):
        profile = OpenAICompletionsProviderProfile(
            model=model_id,
            scoped_credential="episode-secret",
            base_url=base_url,
            context_window=32_768,
            max_output_tokens=2_048,
            caller_headers={},
            request_policy={"mode": "streaming", "include_usage": True, "max_token_field": "max_completion_tokens", "strict_tools": None, "enable_thinking": None},
            capabilities={"supports_store": True, "supports_max_completion_tokens": True},
        )
        projection, semantics, manifest = _compile_target(tmp_path, model_id, profile_identity_digest(profile))
        observation = _observation(provider_id="openai", model_id=model_id, capabilities=_policy_capabilities(request_features=["max_completion_tokens", "n", "store", "stream_options", "streaming"]))
        plan = _plan(observation=observation, semantics=semantics, tools=tuple(_tool_grant(name) for name in ("bash", "edit", "read", "write")), policy_slot_ids=(f"model:{model_id}",), limit_updates={"max_turns": 8, "action_timeout_ms": 40_000}, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
        base_payload = plan.base_compiled.model_dump(mode="python")
        base_payload.update(manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(), compiler_input_digest=manifest.inputs.compiler_input_digest)
        plan_payload = plan.model_dump(mode="python")
        plan_payload["base_compiled"] = c.CompiledArtifactIdentity.model_validate(base_payload)
        plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
        workspace = Path("/captures/normal_multiturn/workspace")
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "normal_marker.txt").unlink(missing_ok=True)
        tools = _OMPWorkspacePort(
            workspace,
            tmp_path / "scratch",
            tuple(
                RunnerToolBinding(tool.tool_id, tool.implementation_digest, tuple(tool.capability_ids))
                for tool in plan.effective_capabilities.tools
            ),
            system_prompt_override=transcript[0]["body"]["messages"][0]["content"],
            request_bodies=transcript,
        )
        client = EpisodeOpenAICompletionsPolicyClient(episode_id="episode-omp", effective_plan_digest=plan.canonical_digest(), observation=observation, profile=profile, target_projection=projection, timeout_seconds=45)
        binding = PolicyRuntimeBinding(RunnerOpenRequest(episode_id="episode-omp", effective_plan=plan), client)
        session = await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(RunnerOpenRequest(episode_id="episode-omp", effective_plan=plan), policy=binding, workspace=tools, cancellation=type("C", (), {"raise_if_cancelled": lambda *args, **kwargs: None})(), events=type("E", (), {"emit": lambda self, event: asyncio.sleep(0)})())
        try:
            result = await session.run(ConductorRunRequest(task_input={"prompt": task}, context={}))
        finally:
            await session.close()
            await client.close()
        trace = thaw_json(result.response["replay_trace"])
    report = OhMyPi18Comparator()({"capture": str(supplier_case), "replay": trace})
    assert report["ok"] is True, report
    tampered_request = deepcopy(trace)
    tampered_request["requests"][0]["messages"][0]["content"] += " tampered"
    assert OhMyPi18Comparator()({"capture": str(supplier_case), "replay": tampered_request})["ok"] is False
    tampered_effect = deepcopy(trace)
    tampered_effect["effects"]["normal_marker.txt"] = {"exists": True, "bytes": 1, "sha256": "sha256:" + "0" * 64}
    assert OhMyPi18Comparator()({"capture": str(supplier_case), "replay": tampered_effect})["ok"] is False
    assert trace["runtime_inputs"]["cwd"] == str(workspace)
    assert trace["runtime_inputs"]["package_dir"]
    assert result.termination in {RunnerTermination.ASSISTANT_COMPLETE, RunnerTermination.POLICY_INCOMPLETE}
    tampered_runtime = deepcopy(trace)
    del tampered_runtime["runtime_inputs"]["cwd"]
    assert OhMyPi18Comparator()({"capture": str(supplier_case), "replay": tampered_runtime})["ok"] is False


@pytest.mark.parametrize(
    ("first_response", "expected_worker_ids"),
    [
        (
            [
                ("denied-url", "read", {"path": "https://example.invalid"}),
                ("allowed-read", "read", {"path": "local.txt"}),
                ("denied-sqlite", "read", {"path": "state.sqlite:users"}),
            ],
            ["allowed-read"],
        ),
        (
            [
                ("denied-url", "read", {"path": "https://example.invalid"}),
                ("denied-sqlite", "read", {"path": "state.sqlite:users"}),
            ],
            [],
        ),
    ],
)
@pytest.mark.asyncio
async def test_omp_conductor_partitions_declared_denials_before_worker(
    tmp_path: Path,
    first_response: list[tuple[str, str, Mapping[str, Any]]],
    expected_worker_ids: list[str],
) -> None:
    responses = [first_response, []]
    with _omp_scripted_server(responses, assistant_texts=["", "done"]) as (base_url, requests):
        model_id = "capture"
        profile = OpenAICompletionsProviderProfile(
            model=model_id,
            scoped_credential="episode-secret",
            base_url=base_url,
            context_window=32_768,
            max_output_tokens=2_048,
            caller_headers={},
            request_policy={"mode": "streaming", "include_usage": True, "max_token_field": "max_completion_tokens", "strict_tools": None, "enable_thinking": None},
            capabilities={"supports_store": True, "supports_max_completion_tokens": True},
        )
        projection, semantics, manifest = _compile_target(tmp_path, model_id, profile_identity_digest(profile))
        observation = _observation(provider_id="openai", model_id=model_id, capabilities=_policy_capabilities(request_features=["max_completion_tokens", "n", "store", "stream_options", "streaming"]))
        plan = _plan(observation=observation, semantics=semantics, tools=tuple(_tool_grant(name) for name in ("bash", "edit", "read", "write")), policy_slot_ids=(f"model:{model_id}",), limit_updates={"max_turns": 8, "action_timeout_ms": 40_000}, implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST)
        base_payload = plan.base_compiled.model_dump(mode="python")
        base_payload.update(manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(), compiler_input_digest=manifest.inputs.compiler_input_digest)
        plan_payload = plan.model_dump(mode="python")
        plan_payload["base_compiled"] = c.CompiledArtifactIdentity.model_validate(base_payload)
        plan = c.EffectiveExecutionPlan.model_validate(plan_payload)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "local.txt").write_text("allowed\n", encoding="utf-8")
        phase_log: list[tuple[str, Mapping[str, Any]]] = []
        tools = _OMPWorkspacePort(
            workspace,
            tmp_path / "scratch",
            tuple(
                RunnerToolBinding(tool.tool_id, tool.implementation_digest, tuple(tool.capability_ids))
                for tool in plan.effective_capabilities.tools
            ),
            system_prompt_override=projection.system_prompt,
            request_bodies=[],
            phase_log=phase_log,
        )
        client = EpisodeOpenAICompletionsPolicyClient(
            episode_id="episode-omp-denials",
            effective_plan_digest=plan.canonical_digest(),
            observation=observation,
            profile=profile,
            target_projection=projection,
            timeout_seconds=45,
        )
        binding = PolicyRuntimeBinding(RunnerOpenRequest(episode_id="episode-omp-denials", effective_plan=plan), client)
        session = await ConductorAdapter(CONDUCTOR_RUNTIME_ABI).open(
            RunnerOpenRequest(episode_id="episode-omp-denials", effective_plan=plan),
            policy=binding,
            workspace=tools,
            cancellation=type("C", (), {"raise_if_cancelled": lambda *args, **kwargs: None})(),
            events=type("E", (), {"emit": lambda self, event: asyncio.sleep(0)})(),
        )
        try:
            await session.run(ConductorRunRequest(task_input={"prompt": "read local.txt"}, context={}))
        finally:
            await session.close()
            await client.close()
        execute_batches = [payload for operation, payload in phase_log if operation == "execute_batch"]
        assert len(execute_batches) == (1 if expected_worker_ids else 0)
        if expected_worker_ids:
            assert [call["id"] for call in execute_batches[0]["calls"]] == expected_worker_ids
        assert len(requests) == 2
        tool_messages = [
            message for message in requests[1]["messages"]
            if message.get("role") in {"tool", "toolResult", "tool_result"}
        ]
        denial_text = [str(message.get("content", "")) for message in tool_messages]
        assert denial_text.index("OMP capability denied: url") < denial_text.index("OMP capability denied: sqlite")
