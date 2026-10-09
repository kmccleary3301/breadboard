from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
_LOCAL_NODE_MODULES = Path(__file__).resolve().parents[3] / "node_modules"
if "PI_CODING_AGENT_NODE_MODULES" not in os.environ and (_LOCAL_NODE_MODULES / "@mariozechner" / "pi-coding-agent").is_dir():
    os.environ["PI_CODING_AGENT_NODE_MODULES"] = str(_LOCAL_NODE_MODULES)
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
    RunnerDependencyError,
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
from breadboard_engine.compilation.provider_response import PI_RESPONSE_CONSUMER_ID, profile_identity_digest
from breadboard_engine.provider.contracts import OpenAICompletionsProviderProfile
from tests.compilation.test_server_compiler import _options
from tests.rl.harness.test_runner_conductor import (
    CONDUCTOR_TEST_AUTHENTICATOR,
    CONDUCTOR_TEST_LEDGER,
    _tool_grant,
)
from tests.rl.harness.test_runner_policy_runtime import _observation, _plan, _policy_capabilities
from tests.rl.harness.test_pi_native_stream_conductor import (
    _Cancellation,
    _Events,
    _NativeWorkerPort,
    _compile_target,
    _sse_tool_response,
    _NODE_MODULES,
    _PINNED_PI_ENTRYPOINT,
)

pytestmark = pytest.mark.skipif(
    not _PINNED_PI_ENTRYPOINT.is_file(),
    reason="pinned Pi 0.73.1 node_modules root is unavailable",
)


@contextmanager
def _overflow_server(
    *,
    overflow_on_request_indices: set[int],
    overflow_error_code: str = "context_length_exceeded",
    summary_text: str = "## Goal\nComplete task\n\n## Constraints & Preferences\n- (none)\n\n## Progress\n### Done\n- First turn complete\n\n### In Progress\n- Final step\n\n## Key Decisions\n- (none)\n\n## Next\n- Finish",
    final_assistant_text: str = "Task completed successfully.",
):
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            req_index = len(requests)
            requests.append(body)

            # Check if this request should overflow
            if req_index in overflow_on_request_indices:
                err_payload = json.dumps({
                    "error": {
                        "message": f"This model maximum context length is 32768 tokens. However, your messages resulted in 40000 tokens.",
                        "type": "invalid_request_error",
                        "code": overflow_error_code,
                    }
                }).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(err_payload)
                return

            # Check if this is a compaction summary request
            msgs = body.get("messages", [])
            is_summary = (
                len(msgs) >= 2
                and msgs[0].get("role") == "system"
                and "context summarization assistant" in str(msgs[0].get("content", ""))
            )

            if is_summary:
                payload = _sse_tool_response(
                    req_index + 1,
                    [],
                    assistant_text=summary_text,
                )
            else:
                payload = _sse_tool_response(
                    req_index + 1,
                    [],
                    assistant_text=final_assistant_text,
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


async def _run_test_episode(
    tmp_path: Path,
    server_base_url: str,
    *,
    target_id: str,
    task_prompt: str = "Please summarize and finish",
):
    model_id = "model-a"
    profile = OpenAICompletionsProviderProfile(
        model=model_id,
        scoped_credential="episode-secret",
        base_url=server_base_url,
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
        target_id=target_id,
    )
    observation = _observation(
        provider_id="openai",
        model_id=model_id,
        capabilities=_policy_capabilities(
            request_features=["max_tokens", "n", "store", "stream_options", "streaming"],
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
    worker = _NativeWorkerPort(
        tmp_path,
        tuple(
            RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools
        ),
    )
    client = EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-pi-compaction",
        effective_plan_digest=plan.canonical_digest(),
        observation=observation,
        profile=profile,
        target_projection=projection,
        timeout_seconds=45,
    )
    binding = PolicyRuntimeBinding(
        RunnerOpenRequest(episode_id="episode-pi-compaction", effective_plan=plan), client
    )
    sink = _Events()
    session = await ConductorAdapter(
        CONDUCTOR_RUNTIME_ABI,
        containment_authenticator=CONDUCTOR_TEST_AUTHENTICATOR,
        admitted_lease_ledger=CONDUCTOR_TEST_LEDGER,
    ).open(
        RunnerOpenRequest(episode_id="episode-pi-compaction", effective_plan=plan),
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
    await client.close()
    return result, sink.events


@pytest.mark.asyncio
async def test_overflow_compaction_disabled_terminates_policy_incomplete(tmp_path: Path):
    """When policy.provider.compaction is false (pi@0.73.1), context_length_exceeded raises RunnerDependencyError (unchanged from main)."""
    with _overflow_server(overflow_on_request_indices={0}) as (base_url, requests):
        with pytest.raises(RunnerDependencyError):
            await _run_test_episode(
                tmp_path,
                base_url,
                target_id="pi@0.73.1",
            )
        # Only 1 request was sent (the initial request that overflowed)
        assert len(requests) == 1

@pytest.mark.asyncio
async def test_overflow_compaction_enabled_compacts_and_continues(tmp_path: Path):
    """When compaction is enabled (pi-r2@0.73.1), context_length_exceeded triggers compaction and retries."""
    # Create a large task prompt to ensure keepRecentTokens cut point has content to summarize
    large_prompt = "Hello from user. " + ("Here is important context info. " * 3000)
    with _overflow_server(overflow_on_request_indices={0}) as (base_url, requests):
        result, events = await _run_test_episode(
            tmp_path,
            base_url,
            target_id="pi-r2@0.73.1",
            task_prompt=large_prompt,
        )
        assert result.termination == RunnerTermination.ASSISTANT_COMPLETE
        # 0: initial agent request (overflowed)
        # 1: summary request (generated by worker)
        # 2: retried agent request (with compacted history)
        assert len(requests) == 3
        
        # Check summary request structure
        summary_req = requests[1]
        assert "context summarization assistant" in summary_req["messages"][0]["content"]
        
        # Check retried request has compactionSummary user message
        retried_req = requests[2]
        retried_messages = retried_req["messages"]
        # In convertToLlm rendering, compactionSummary is transformed into:
        # "The conversation history before this point was compacted into the following summary:\n\n<summary>..."
        has_summary_marker = any(
            "compacted into the following summary" in str(msg.get("content"))
            for msg in retried_messages
        )
        assert has_summary_marker

        trace = thaw_json(result.response["replay_trace"])
        assert trace["termination"]["kind"] == "Submitted"
        # Check that summary request is recorded in trace distinguishably
        trace_reqs = trace["requests"]
        assert len(trace_reqs) == 3
        assert trace_reqs[1].get("_compaction_summary") is True


@pytest.mark.asyncio
async def test_overflow_repeated_overflow_bounded_and_terminal(tmp_path: Path):
    """Repeated overflow after compaction falls through to the original path and raises RunnerDependencyError for Pi."""
    large_prompt = "Initial prompt. " + ("Context padding data. " * 3000)
    # Request 0 (initial) and Request 2 (retried after compaction) both overflow
    with _overflow_server(overflow_on_request_indices={0, 2}) as (base_url, requests):
        with pytest.raises(RunnerDependencyError):
            await _run_test_episode(
                tmp_path,
                base_url,
                target_id="pi-r2@0.73.1",
                task_prompt=large_prompt,
            )
        # Exactly 3 requests: initial (failed), summary (succeeded), retried (failed)
        assert len(requests) == 3

@pytest.mark.asyncio
async def test_request_cap_not_incremented_by_compaction(tmp_path: Path):
    """Compaction summary does not increment the agent turn request_cap counter."""
    large_prompt = "Prompt context. " + ("Background knowledge. " * 3000)
    with _overflow_server(overflow_on_request_indices={0}) as (base_url, requests):
        result, events = await _run_test_episode(
            tmp_path,
            base_url,
            target_id="pi-r2@0.73.1",
            task_prompt=large_prompt,
        )
        trace = thaw_json(result.response["replay_trace"])
        # Only 1 agent request was committed
        assert trace["request_count"] == 1
        assert trace["stream_fn_issued"] == 1


def test_normal_request_with_summary_text_in_last_message_still_rejected_when_differing(tmp_path: Path):
    """A normal request whose last message contains summary prompt text cannot bypass system prompt or tools checks."""
    from breadboard.rl.harness.policy_provider import _responses_request_to_chat, ProviderContractError
    profile = OpenAICompletionsProviderProfile(
        model="model-a",
        scoped_credential="secret",
        base_url="http://127.0.0.1:8000/v1",
        context_window=32_768,
        max_output_tokens=2_048,
        caller_headers={},
        request_policy={"mode": "streaming", "include_usage": True},
        capabilities={"supports_store": True},
    )
    projection, _, _ = _compile_target(
        tmp_path,
        profile_digest=profile_identity_digest(profile),
        model_id="model-a",
        target_id="pi-r2@0.73.1",
    )
    # Normal request (no compaction_summary=True) with wrong system prompt,
    # but last message attempts to mimic compaction summary text
    request_with_bad_system_prompt = {
        "model": "model-a",
        "messages": [
            {"role": "system", "content": "Attacker system prompt"},
            {"role": "user", "content": "Some normal input. structured context checkpoint summary in tool output"},
        ],
        "tools": [thaw_json(tool) for tool in projection.chat_tools],
    }
    with pytest.raises(ProviderContractError, match="source-native request does not match its compiled source surface"):
        _responses_request_to_chat(
            request_with_bad_system_prompt,
            expected_model_id="model-a",
            target_projection=projection,
            native_system_prompt="Correct native system prompt",
            compaction_summary=False,
        )

    # Normal request with wrong tools
    request_with_bad_tools = {
        "model": "model-a",
        "messages": [
            {"role": "system", "content": "Correct native system prompt"},
            {"role": "user", "content": "Some normal input. structured context checkpoint summary in tool output"},
        ],
        "tools": [],  # Empty tools not allowed for normal request
    }
    with pytest.raises(ProviderContractError, match="source-native request does not match its compiled source surface"):
        _responses_request_to_chat(
            request_with_bad_tools,
            expected_model_id="model-a",
            target_projection=projection,
            native_system_prompt="Correct native system prompt",
            compaction_summary=False,
        )
