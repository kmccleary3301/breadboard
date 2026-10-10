from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
_LOCAL_NODE_MODULES = Path(__file__).resolve().parents[3] / "node_modules"
if "PI_CODING_AGENT_NODE_MODULES" not in os.environ and (_LOCAL_NODE_MODULES / "@mariozechner" / "pi-coding-agent").is_dir():
    os.environ["PI_CODING_AGENT_NODE_MODULES"] = str(_LOCAL_NODE_MODULES)


def _find_dist_compaction_js() -> Path | None:
    for env_var in ["PI_CODING_AGENT_NODE_MODULES", "PI057_CODING_AGENT_NODE_MODULES"]:
        base = os.environ.get(env_var)
        if base:
            candidate = Path(base) / "@mariozechner" / "pi-coding-agent" / "dist" / "core" / "compaction" / "compaction.js"
            if candidate.is_file():
                return candidate
    if _LOCAL_NODE_MODULES.is_dir():
        candidate = _LOCAL_NODE_MODULES / "@mariozechner" / "pi-coding-agent" / "dist" / "core" / "compaction" / "compaction.js"
        if candidate.is_file():
            return candidate
    return None


def _extract_const(source: str, const_name: str) -> str:
    m = re.search(rf"const {const_name} = `(.*?)`;", source, re.DOTALL)
    assert m, f"Could not find const {const_name}"
    return m.group(1)

import pytest

from breadboard.rl.harness.native_stream_profiles import PI_SUMMARIZATION_SYSTEM_PROMPT


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
    final_usage: dict[str, Any] | None = None,
    final_finish_reason: str = "stop",
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
                if final_finish_reason != "stop":
                    payload = payload.replace(b'"stop"', json.dumps(final_finish_reason).encode())
                if final_usage is not None:
                    usage_chunk = json.dumps({"choices": [], "usage": final_usage}).encode()
                    payload = payload.replace(b"data: [DONE]", b"data: " + usage_chunk + b"\n\ndata: [DONE]")

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
    context_window: int = 32_768,
    max_output_tokens: int = 2_048,
):
    model_id = "model-a"
    profile = OpenAICompletionsProviderProfile(
        model=model_id,
        scoped_credential="episode-secret",
        base_url=server_base_url,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
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
        context_length=context_window,
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
    """When compaction is enabled (pi-r3@0.73.1), context_length_exceeded triggers compaction and retries."""
    # Create a large task prompt to ensure keepRecentTokens cut point has content to summarize
    large_prompt = "Hello from user. " + ("Here is important context info. " * 3000)
    with _overflow_server(overflow_on_request_indices={0}) as (base_url, requests):
        result, events = await _run_test_episode(
            tmp_path,
            base_url,
            target_id="pi-r3@0.73.1",
            task_prompt=large_prompt,
        )
        assert result.termination == RunnerTermination.ASSISTANT_COMPLETE
        # 0: initial agent request (overflowed)
        # 1: summary request (generated by worker)
        # 2: retried agent request (with compacted history)
        assert len(requests) == 3
        
        # Check summary request structure
        summary_req = requests[1]
        assert summary_req["messages"][0]["role"] == "system"
        assert summary_req["messages"][0]["content"] == PI_SUMMARIZATION_SYSTEM_PROMPT
        assert summary_req["messages"][1]["role"] == "user"
        user_content = summary_req["messages"][1]["content"]
        assert isinstance(user_content, list), f"Expected stock block form [{{'type': 'text', 'text': ...}}], got {type(user_content)}"
        assert len(user_content) == 1
        assert user_content[0]["type"] == "text"
        user_text = user_content[0]["text"]
        assert user_text.startswith("<conversation>\n")
        assert "\n</conversation>\n\n" in user_text
        # Assert user text ends with the stock SUMMARIZATION_PROMPT
        dist_compaction = _find_dist_compaction_js()
        if dist_compaction:
            stock_summary_prompt = _extract_const(dist_compaction.read_text("utf-8"), "SUMMARIZATION_PROMPT")
            assert user_text.endswith(stock_summary_prompt)
        # Deviation compaction_summary_episode_tools: the summary request
        # carries the episode's tools, so the policy endpoint's tool schema
        # check sees one schema for the whole episode.
        assert summary_req["tools"] == requests[0]["tools"]
        assert summary_req["tools"]
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


@pytest.mark.parametrize("prompt_tokens,summary_count", [(1000, 1), (120000, 2)])
@pytest.mark.asyncio
async def test_overflow_retry_agent_end_uses_fresh_stock_usage(
    tmp_path: Path, prompt_tokens: int, summary_count: int,
):
    large_prompt = "Initial task. " + "Important context. " * 3000
    with _overflow_server(
        overflow_on_request_indices={0},
        final_usage={"prompt_tokens": prompt_tokens, "completion_tokens": 1000,
                     "total_tokens": prompt_tokens + 1000},
    ) as (base_url, requests):
        result, _ = await _run_test_episode(
            tmp_path, base_url, target_id="pi-r3@0.73.1", task_prompt=large_prompt,
            context_window=131072,
        )
        assert result.termination == RunnerTermination.ASSISTANT_COMPLETE
        trace = thaw_json(result.response["replay_trace"])
        summaries = [request for request in trace["requests"] if request.get("_compaction_summary")]
        assert len(summaries) == summary_count
        assert len(requests) == 2 + summary_count
        assert trace["request_count"] == 1

@pytest.mark.parametrize("finish_reason,completion_tokens,termination", [
    ("stop", 1000, RunnerTermination.ASSISTANT_COMPLETE),
    ("length", 0, RunnerTermination.POLICY_INCOMPLETE),
])
@pytest.mark.asyncio
async def test_agent_end_overflow_respects_stock_continue(
    tmp_path: Path, finish_reason: str, completion_tokens: int, termination: RunnerTermination,
):
    """Stock retains a successful assistant, so empty-queue continue refuses it."""
    with _overflow_server(
        overflow_on_request_indices=set(),
        final_usage={"prompt_tokens": 140000, "completion_tokens": completion_tokens,
                     "total_tokens": 140000 + completion_tokens},
        final_finish_reason=finish_reason,
    ) as (base_url, requests):
        result, _ = await _run_test_episode(
            tmp_path, base_url, target_id="pi-r3@0.73.1",
            task_prompt="Task " + "Background. " * 3000, context_window=131072,
        )
        trace = thaw_json(result.response["replay_trace"])
        summaries = [request for request in trace["requests"] if request.get("_compaction_summary")]
        assert result.termination == termination
        assert len(summaries) == 1
        assert len(requests) == 2
        assert trace["request_count"] == 1
        assert all("usage" in message for message in trace["messages"] if message["role"] == "assistant")



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
                target_id="pi-r3@0.73.1",
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
            target_id="pi-r3@0.73.1",
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
        target_id="pi-r3@0.73.1",
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


def test_summary_request_admission_requires_episode_tools_and_source_prompt(tmp_path: Path):
    from breadboard.rl.harness.native_stream_profiles import PI_SUMMARIZATION_SYSTEM_PROMPT
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
        target_id="pi-r3@0.73.1",
    )
    episode_tools = [thaw_json(tool) for tool in projection.chat_tools]

    def summary(system: str, tools: list) -> dict:
        return {
            "model": "model-a",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "<conversation>\n[User]: hi\n</conversation>\n\nSummarize."},
            ],
            "tools": tools,
        }

    def admit(request: dict) -> Any:
        return _responses_request_to_chat(
            request, expected_model_id="model-a", target_projection=projection,
            native_system_prompt="Correct native system prompt", compaction_summary=True,
        )

    _, tools = admit(summary(PI_SUMMARIZATION_SYSTEM_PROMPT, episode_tools))
    assert tools == episode_tools
    for request in (
        summary(PI_SUMMARIZATION_SYSTEM_PROMPT, []),
        summary("Correct native system prompt", episode_tools),
    ):
        with pytest.raises(ProviderContractError, match="compaction summary request does not match"):
            admit(request)


