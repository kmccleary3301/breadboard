from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import pytest

from breadboard.rl.harness.omp_16_2_13_native_tools import (
    ALLOWED_TOOLS,
    CONSUMER_ID,
    LOCAL_ADAPTER_ID,
    PINNED_MODULE_SHA256,
    PinnedNativeWorkerSpec,
    NativeToolWorker,
    NativeWorkerPhaseError,
    verify_pinned_root,
)

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[2]
    / "e4_parity"
    / "fixtures"
    / "omp_16_2_13_supplier_cases"
)

_ROOT_ENV = os.environ.get("OMP16213_CODING_AGENT_NODE_MODULES")
if os.environ.get("BB_REQUIRE_PINNED_OMP16213_NODE") == "1" and not _ROOT_ENV:
    pytest.fail("BB_REQUIRE_PINNED_OMP16213_NODE=1 requires OMP16213_CODING_AGENT_NODE_MODULES")

_NODE_MODULES = Path(_ROOT_ENV) if _ROOT_ENV else None
if (
    os.environ.get("BB_REQUIRE_PINNED_OMP16213_NODE") == "1"
    and _NODE_MODULES is not None
    and not (_NODE_MODULES / "@oh-my-pi/pi-coding-agent/src/sdk.ts").is_file()
):
    pytest.fail(f"required pinned OMP 16.2.13 node_modules root is unavailable: {_NODE_MODULES}")

pytestmark = pytest.mark.skipif(_NODE_MODULES is None, reason="OMP16213_CODING_AGENT_NODE_MODULES is unset")


@pytest.fixture
def worker_spec() -> PinnedNativeWorkerSpec:
    assert _NODE_MODULES is not None
    return PinnedNativeWorkerSpec.discover(node_modules=_NODE_MODULES)


@pytest.fixture
def initialized_worker(worker_spec: PinnedNativeWorkerSpec, tmp_path: Path):
    worker = NativeToolWorker(cwd=str(tmp_path), spec=worker_spec, timeout_seconds=15)
    runtime_inputs = {
        "cwd": str(tmp_path),
        "home": str(tmp_path / "home"),
        "current_date": "2026-09-26",
        "package_dir": str(_NODE_MODULES / "@oh-my-pi/pi-coding-agent"),
    }
    model_config = {
        "id": "Qwen/Qwen3.5-35B-A3B",
        "name": "Qwen/Qwen3.5-35B-A3B",
        "provider": "vllm-local",
        "api": "openai-completions",
        "baseUrl": "http://127.0.0.1:18080/v1",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 200000,
        "maxTokens": 2048,
        "cost": {"input": 0.14, "output": 1.0, "cacheRead": 0, "cacheWrite": 0},
    }
    init_res = worker.initialize(
        runtime_inputs=runtime_inputs,
        model_config=model_config,
        workspace=str(tmp_path),
        scratch=str(tmp_path / ".scratch"),
    )
    yield worker, init_res
    worker.close()


def _normalize_system_prompt_date_cwd(prompt: str) -> str:
    """Normalize dynamic date/cwd reminder line and host workstation block for exact equivalence comparison."""
    # Pattern: Today is YYYY-MM-DD, and the current working directory is '<cwd>'.
    norm = re.sub(
        r"Today is \d{4}-\d{2}-\d{2}, and the current working directory is '[^']+'\.",
        "Today is <DATE>, and the current working directory is '<CWD>'.",
        prompt,
    )
    # Workstation block carries host OS/Arch/CPU differences between capture (Linux) and replay (Darwin/Mac)
    norm = re.sub(
        r"<workstation>.*?</workstation>",
        "<workstation>\n<NORMALIZED_WORKSTATION>\n</workstation>",
        norm,
        flags=re.DOTALL,
    )
    return norm


def test_fail_closed_on_wrong_root_or_sha(tmp_path: Path):
    """Test that spec discovery and verification fail closed on wrong root or bad sha."""
    wrong_root = tmp_path / "non_existent_node_modules"
    with pytest.raises((FileNotFoundError, ValueError)):
        verify_pinned_root(wrong_root)

    with pytest.raises((FileNotFoundError, ValueError)):
        PinnedNativeWorkerSpec.discover(node_modules=wrong_root)

    # Corrupt a dummy root with an incorrect hash
    fake_root = tmp_path / "corrupt_node_modules"
    fake_file = fake_root / "@oh-my-pi/pi-coding-agent/src/sdk.ts"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("corrupted content", encoding="utf-8")
    with pytest.raises(ValueError, match="sha mismatch"):
        verify_pinned_root(fake_root)


def test_o0_request_0_body_equals_capture_modulo_date_cwd(initialized_worker, tmp_path: Path):
    """Test o0 request 0 body equals the capture modulo date/cwd lines."""
    worker, init_res = initialized_worker
    assert init_res["kind"] == "initialized"
    bootstrap = init_res["bootstrap"]
    assert bootstrap["consumer_id"] == CONSUMER_ID
    assert "length_aborted_message" in bootstrap
    assert len(bootstrap["length_aborted_message"]) > 20

    # Load o0 capture ground truth
    o0_dir = FIXTURES_ROOT / "declared__o0_text_stop" / "capture"
    http_file = o0_dir / "http-transcript.jsonl"
    first_req = json.loads(http_file.read_text(encoding="utf-8").splitlines()[0])
    raw_bytes = base64.b64decode(first_req["raw_body_base64"]).decode("utf-8")
    captured_body = json.loads(raw_bytes)

    # Captured user message
    captured_messages = captured_body["messages"]
    user_message = [m for m in captured_messages if m["role"] == "user"]

    projected = worker.project_request(user_message)
    assert projected["kind"] == "request"
    body = projected["request_body"]

    # Verify key order
    expected_key_order = [
        "model",
        "messages",
        "stream",
        "stream_options",
        "tools",
        "max_completion_tokens",
        "preserve_thinking",
        "chat_template_kwargs",
    ]
    assert projected["request_members"] == expected_key_order
    assert list(body.keys()) == expected_key_order

    # Verify parameters
    assert body["model"] == captured_body["model"]
    assert body["stream"] == captured_body["stream"]
    assert body["stream_options"] == captured_body["stream_options"]
    assert body["max_completion_tokens"] == captured_body["max_completion_tokens"]
    assert body["preserve_thinking"] == captured_body["preserve_thinking"]
    assert body["chat_template_kwargs"] == captured_body["chat_template_kwargs"]

    # Verify tools
    assert len(body["tools"]) == len(captured_body["tools"]) == 5
    projected_tool_names = [t["function"]["name"] for t in body["tools"]]
    captured_tool_names = [t["function"]["name"] for t in captured_body["tools"]]
    assert projected_tool_names == captured_tool_names == list(ALLOWED_TOOLS)

    # Compare system prompt modulo date/cwd line
    projected_sys = body["messages"][0]["content"]
    captured_sys = captured_body["messages"][0]["content"]
    norm_projected = _normalize_system_prompt_date_cwd(projected_sys)
    norm_captured = _normalize_system_prompt_date_cwd(captured_sys)
    assert norm_projected == norm_captured


def test_o1_tool_results_execution(initialized_worker, tmp_path: Path):
    """Test o1 tool results for read, bash, write, and edit."""
    worker, _ = initialized_worker

    # Write initial file
    readme_path = tmp_path / "README.txt"
    readme_path.write_text("Initial text line 1\nInitial text line 2\n", encoding="utf-8")

    # 1. read
    read_calls = [{"id": "c1", "name": "read", "arguments": {"path": "README.txt", "i": "reading readme"}}]
    results1 = worker.execute_batch(read_calls)
    assert len(results1) == 1
    assert results1[0]["isError"] is False
    content1 = results1[0]["content"][0]["text"]
    assert "README.txt#" in content1
    assert "Initial text line 1" in content1
    tag_match = re.search(r"\[README\.txt#([0-9A-Fa-f]{4})\]", content1)
    assert tag_match is not None
    tag = tag_match.group(1)

    # 2. bash
    bash_calls = [{"id": "c2", "name": "bash", "arguments": {"command": "echo bash-omp", "i": "echoing test"}}]
    results2 = worker.execute_batch(bash_calls)
    assert len(results2) == 1
    assert results2[0]["isError"] is False
    assert "bash-omp" in results2[0]["content"][0]["text"]

    # 3. write
    write_calls = [
        {"id": "c3", "name": "write", "arguments": {"path": "written.txt", "content": "Sample written content", "i": "writing file"}}
    ]
    results3 = worker.execute_batch(write_calls)
    assert len(results3) == 1
    assert results3[0]["isError"] is False
    assert (tmp_path / "written.txt").read_text(encoding="utf-8") == "Sample written content"

    # 4. edit with valid tag
    edit_input = f"""[README.txt#{tag}]
SWAP 2.=2:
+Edited line 2 via hashline
"""
    edit_calls = [{"id": "c4", "name": "edit", "arguments": {"input": edit_input, "i": "editing line 2"}}]
    results4 = worker.execute_batch(edit_calls)
    assert len(results4) == 1
    assert results4[0]["isError"] is False
    assert "Edited line 2 via hashline" in (tmp_path / "README.txt").read_text(encoding="utf-8")


def test_o7_hashline_stale_and_unseen_anchor(initialized_worker, tmp_path: Path):
    """Test o7 hashline stale tag rejection (#0000) and unseen anchor rejection."""
    worker, _ = initialized_worker

    # Write a test file
    readme_path = tmp_path / "README.txt"
    readme_path.write_text("Initial text line 1\nInitial text line 2\n", encoding="utf-8")

    # Read to establish session tag
    worker.execute_batch([{"id": "c0", "name": "read", "arguments": {"path": "README.txt", "i": "reading readme"}}])

    # 1. Stale tag #0000 rejection
    stale_edit = """[README.txt#0000]
SWAP 1.=1:
+Attempting stale edit
"""
    results_stale = worker.execute_batch([{"id": "c1", "name": "edit", "arguments": {"input": stale_edit, "i": "stale edit"}}])
    assert len(results_stale) == 1
    assert results_stale[0]["isError"] is True
    stale_text = results_stale[0]["content"][0]["text"] if isinstance(results_stale[0]["content"], list) else results_stale[0]["content"]
    assert "hash #0000 is not from this session" in stale_text

    # 2. Unseen anchor rejection on a large file
    long_file = tmp_path / "long.txt"
    lines = [f"line {i}" for i in range(1, 400)]
    long_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Read default lines (which caps at early lines and mints tag)
    read_res = worker.execute_batch([{"id": "c2", "name": "read", "arguments": {"path": "long.txt", "i": "reading long"}}])
    content = read_res[0]["content"][0]["text"]
    tag_match = re.search(r"\[long\.txt#([0-9A-Fa-f]{4})\]", content)
    assert tag_match is not None
    tag = tag_match.group(1)

    # Edit anchor at line 350 which was unseen
    unseen_edit = f"""[long.txt#{tag}]
SWAP 350.=350:
+new content
"""
    results_unseen = worker.execute_batch([{"id": "c3", "name": "edit", "arguments": {"input": unseen_edit, "i": "unseen edit"}}])
    assert len(results_unseen) == 1
    assert results_unseen[0]["isError"] is True
    unseen_text = results_unseen[0]["content"][0]["text"] if isinstance(results_unseen[0]["content"], list) else results_unseen[0]["content"]
    assert "never displayed" in unseen_text or "unseen" in unseen_text.lower()


def test_bash_timeout_and_nonzero_exit(initialized_worker):
    """Test bash execution enforces timeout (1s) and preserves nonzero exit codes (42, 127)."""
    worker, _ = initialized_worker

    # 1. Nonzero exit 42
    res_42 = worker.execute_batch([{"id": "c1", "name": "bash", "arguments": {"command": "exit 42", "i": "exiting 42"}}])
    assert len(res_42) == 1
    assert res_42[0]["isError"] is True
    text_42 = res_42[0]["content"][0]["text"] if isinstance(res_42[0]["content"], list) else res_42[0]["content"]
    assert "Command exited with code 42" in text_42

    # 2. Nonzero exit 127 (command not found)
    res_127 = worker.execute_batch([
        {"id": "c2", "name": "bash", "arguments": {"command": "definitely-not-a-command-omp16213", "i": "running missing command"}}
    ])
    assert len(res_127) == 1
    assert res_127[0]["isError"] is True
    text_127 = res_127[0]["content"][0]["text"] if isinstance(res_127[0]["content"], list) else res_127[0]["content"]
    assert "Command exited with code 127" in text_127

    # 3. Timeout after 1 second
    res_to = worker.execute_batch([
        {"id": "c3", "name": "bash", "arguments": {"command": "sleep 5", "timeout": 1, "i": "sleeping with timeout"}}
    ])
    assert len(res_to) == 1
    assert res_to[0]["isError"] is True
    text_to = res_to[0]["content"][0]["text"] if isinstance(res_to[0]["content"], list) else res_to[0]["content"]
    assert "Command timed out after 1 seconds" in text_to


def test_o5_provider_failure_message(initialized_worker):
    """Test o5 provider failure message projection for HTTP 500."""
    worker, _ = initialized_worker
    error_body = json.dumps({"error": {"message": "Scripted HTTP 500", "type": "server_error"}})
    res = worker.project_provider_failure(
        http_status=500,
        response_body_text=error_body,
        messages=[{"role": "user", "content": [{"type": "text", "text": "Reply with the single word READY."}]}],
    )
    assert res["kind"] == "provider_failure"
    msg = res["message"]
    assert msg["role"] == "assistant"
    assert msg["stopReason"] == "error"
    assert msg["errorStatus"] == 500
    assert msg["errorMessage"] == "500 Scripted HTTP 500\nScripted HTTP 500 (type=server_error)"


def test_unknown_and_malformed_tool_calls(initialized_worker):
    """Test unknown tool and malformed JSON tool call error formatting."""
    worker, _ = initialized_worker

    # Unknown tool call
    res_unknown = worker.execute_batch([
        {"id": "c1", "name": "does_not_exist_tool", "arguments": {"i": "calling unknown tool"}}
    ])
    assert len(res_unknown) == 1
    assert res_unknown[0]["isError"] is True
    text_unknown = res_unknown[0]["content"]
    assert "Tool does_not_exist_tool not found" in str(text_unknown)

    # Malformed argument call (missing required command in bash)
    res_malformed = worker.execute_batch([
        {"id": "c2", "name": "bash", "arguments": "{malformed_not_json"}
    ])
    assert len(res_malformed) == 1
    assert res_malformed[0]["isError"] is True
    text_malformed = res_malformed[0]["content"]
    assert 'Validation failed for tool "bash"' in str(text_malformed)
    assert "command must be a string" in str(text_malformed)


def test_parse_streaming_json_batch(initialized_worker):
    """Test streaming argument parsing phase."""
    worker, _ = initialized_worker
    inputs = [
        '{"command": "echo 1"}',
        None,
        '{"command": ',
        '{"path": "file.txt", "i": "reading"}',
    ]
    parsed = worker.parse_streaming_json_batch(inputs)
    assert len(parsed) == 4
    assert parsed[0] == {"command": "echo 1"}
    assert parsed[1] == {}
    assert parsed[2] == {}
    assert parsed[3] == {"path": "file.txt", "i": "reading"}

def _state_history(*, tool_path="log.txt", tool_text=None, assistant_text="done", billed=120, old_size=3000, tool_preamble="Reading the log"):
    """Build persisted history through production response and dispatch methods."""
    from itertools import count
    from breadboard.rl.harness.runners.omp_16_2_13_semantics import Omp16213SemanticsState
    from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall

    clock = count(1000).__next__
    state = Omp16213SemanticsState(
        task="old task " + "x" * old_size, system_prompt="",
        model_id="Qwen/Qwen3.5-35B-A3B", provider="vllm-local", api="openai-completions",
        cost={"input": 0.14, "output": 1, "cacheRead": 0, "cacheWrite": 0},
        current_date_time="2026-09-26", length_aborted_message="aborted",
        runtime_inputs={"cwd": "/tmp", "home": "/tmp", "current_date": "2026-09-26", "package_dir": "/tmp"},
        clock=clock,
    )

    def respond(text, calls=(), finish="stop", tokens=120):
        state.begin_query()
        return state.prepare_response(NativeProviderResponse(
            binding_digest="binding", request_digest="request", response_id="response",
            model=state.model_id, content=text, finish_reason=finish, tool_calls=calls,
            usage={"prompt_tokens": tokens, "completion_tokens": 40, "total_tokens": tokens + 40},
        ), [json.loads(call.arguments) for call in calls])

    respond("old answer " + "x" * old_size)
    state.resume_after_compaction([{
        "role": "user", "content": [{"type": "text", "text": "current task " + "y" * 400}],
        "attribution": "user", "timestamp": clock(),
    }, {
        "role": "developer", "content": [{"type": "text", "text": "Keep the plan."}],
        "attribution": "agent", "timestamp": clock(),
    }])
    if tool_text is not None:
        call = NativeToolCall("c1", "read", json.dumps({"path": tool_path, "i": "Reading log"}))
        turn = respond(tool_preamble, (call,), "tool_calls", billed)
        state.commit_tool_results(turn.calls, [{
            "id": "c1", "completion_index": 0, "isError": False,
            "content": [{"type": "text", "text": tool_text}, {"type": "text", "text": "retained second block"}],
        }])
    else:
        respond(assistant_text, tokens=billed)
    return state


def _run_stock_session(messages, settings, summaries, system_prompt, tmp_path, *, second_messages=None, second_summaries=None):
    """Run stock AgentSession.compact and stock agent-loop request conversion."""
    from tests.rl.harness.test_omp_16_2_13_native_stream_conductor import _response_chunks

    responses = [
        b"".join(_response_chunks("stock", i, {
            "assistant_content": text, "finish_reason": "stop",
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        })).decode()
        for i, text in enumerate([*summaries, "next answer", *(([*second_summaries, "next answer"]) if second_summaries is not None else [])])
    ]
    script = f"""
import {{ pathToFileURL }} from "node:url";
const root = {json.dumps(str(_NODE_MODULES))};
const load = (path) => import(pathToFileURL(`${{root}}/@oh-my-pi/${{path}}`).href);
const {{ createAgentSession }} = await load("pi-coding-agent/src/sdk.ts");
const {{ Settings }} = await load("pi-coding-agent/src/config/settings.ts");
const {{ ModelRegistry }} = await load("pi-coding-agent/src/config/model-registry.ts");
const {{ SessionManager }} = await load("pi-coding-agent/src/session/session-manager.ts");
const {{ AuthStorage }} = await load("pi-ai/src/auth-storage.ts");
const cwd = {json.dumps(str(tmp_path))};
const settings = await Settings.init({{
  cwd, agentDir: cwd + "/oracle", inMemory: true, configFiles: [],
  overrides: {{ "retry.enabled": false, "compaction.enabled": true, "compaction.strategy": "context-full",
    ...Object.fromEntries(Object.entries({json.dumps(settings)}).map(([k,v]) => ["compaction." + k, v])) }},
}});
const authStorage = await AuthStorage.create(":memory:");
const modelRegistry = new ModelRegistry(authStorage, cwd + "/oracle/models.json", {{ settings }});
modelRegistry.registerProvider("vllm-local", {{
  baseUrl: "http://127.0.0.1:18080/v1", api: "openai-completions", apiKey: "omp-tool-worker-key",
  models: [{{ id: "Qwen/Qwen3.5-35B-A3B", name: "Qwen/Qwen3.5-35B-A3B",
    reasoning: false, input: ["text"], contextWindow: 200000, maxTokens: 2048 }}],
}});
const model = modelRegistry.find("vllm-local", "Qwen/Qwen3.5-35B-A3B");
const sessionManager = SessionManager.inMemory(cwd);
const {{ session }} = await createAgentSession({{
  cwd, agentDir: cwd + "/oracle", settings, authStorage, modelRegistry, model, sessionManager,
  thinkingLevel: "off", toolNames: ["read", "bash", "edit", "write"],
  autoApprove: false, skills: [], disableExtensionDiscovery: true, additionalExtensionPaths: [], enableMCP: false,
}});
session.agent.setSystemPrompt({json.dumps(system_prompt)});
const messages = {json.dumps(messages)};
for (const message of messages) sessionManager.appendMessage(message);
session.agent.replaceMessages(session.buildDisplaySessionContext().messages);
const entries = sessionManager.getBranch();
const activeBefore = session.agent.state.messages;
const requests = [];
const responses = {json.dumps(responses)};
globalThis.fetch = async (_url, init) => {{
  requests.push(JSON.parse(init.body));
  if (!responses.length) throw new Error("stock script exhausted");
  return new Response(responses.shift(), {{ headers: {{ "Content-Type": "text/event-stream" }} }});
}};
const result = await session.compact();
const firstKeptIndex = activeBefore.indexOf(entries.find(entry => entry.id === result.firstKeptEntryId).message);
const compacted = session.agent.state.messages;
await session.agent.continue();
const rounds = [{{ result, firstKeptIndex, requests: [...requests] }}];
if ({json.dumps(second_messages)} !== null) {{
  for (const message of {json.dumps(second_messages)}) sessionManager.appendMessage(message);
  session.agent.replaceMessages(session.buildDisplaySessionContext().messages);
  const activeBeforeSecond = session.agent.state.messages;
  const entriesBeforeSecond = sessionManager.getBranch();
  const requestStart = requests.length;
  const secondResult = await session.compact();
  const secondFirstKeptIndex = activeBeforeSecond.indexOf(
    entriesBeforeSecond.find(entry => entry.id === secondResult.firstKeptEntryId).message,
  );
  await session.agent.continue();
  rounds.push({{ result: secondResult, firstKeptIndex: secondFirstKeptIndex, requests: requests.slice(requestStart) }});
}}
await new Promise((resolve, reject) => process.stdout.write(
  JSON.stringify({{ result, firstKeptIndex, requests, compacted, rounds }}) + "\\n",
  error => error ? reject(error) : resolve(),
));
await session.dispose();
process.exit(0);
"""
    res = subprocess.run(["bun", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


def _prepare(worker, state, *, reason="threshold", checkpoint="agent_end", window=200000, settings=None):
    return worker.phase("prepare_compaction", {
        "messages": state.messages, "reason": reason, "checkpoint": checkpoint,
        "usage": {"prompt_tokens": 190000, "completion_tokens": 40, "total_tokens": 190040},
        "context_window": window, "settings": settings or {"keepRecentTokens": 150},
    })


def _finalize(worker, prepared, summary="## Goal\nPrior work.", short="Prior work finished."):
    payload = {"preparation": prepared["preparation"], "summary": summary}
    if prepared["turn_prefix_request"] is not None:
        payload["turn_prefix_summary"] = "## Original Request\nContinue the current task."
    first = worker.phase("finalize_compaction", payload)
    assert first["kind"] == "compaction_followup_request"
    finalized = worker.phase("finalize_compaction", {**payload, "followup_summaries": [short]})
    assert finalized["kind"] == "compaction_finalized"
    return finalized, first


def test_compaction_threshold_not_triggered(initialized_worker):
    worker, _ = initialized_worker
    res = _prepare(worker, _state_history())
    assert res["kind"] == "compaction_unavailable"
    assert res["reason"] == "not_triggered"


@pytest.mark.parametrize("reason,checkpoint", [
    ("threshold", "before_request"), ("threshold", "agent_end"), ("overflow", "overflow"),
])
def test_compaction_real_history_and_next_wire_equal_stock(initialized_worker, tmp_path, reason, checkpoint):
    worker, init = initialized_worker
    state = _state_history(tool_text="file contents " + "z" * 800, billed=190000)
    settings = {"keepRecentTokens": 30000, "strategy": "context-full"}
    summaries = ["## Goal\nOlder work.", "I read the log."]
    stock = _run_stock_session(state.messages, settings, summaries, init["system_prompt"], tmp_path)
    prepared = _prepare(worker, state, reason=reason, checkpoint=checkpoint, settings=settings)
    assert prepared["kind"] == "compaction_prepared"
    assert prepared["first_kept_index"] == stock["firstKeptIndex"]
    assert prepared["summary_request"]["messages"] == stock["requests"][0]["messages"]
    assert [entry["message"] for entry in prepared["preparation"]["entries"]] == state.messages
    finalized, followup = _finalize(worker, prepared, *summaries)
    assert followup["request"]["messages"] == stock["requests"][1]["messages"]
    assert finalized["summary"] == stock["result"]["summary"]
    compacted = finalized.get("messages", [
        finalized["compaction_message"], *state.messages[finalized["first_kept_index"]:],
    ])
    projected = worker.project_request(compacted)["request_body"]
    assert projected == stock["requests"][-1]
    assert any(message["role"] == "user" and "Older work." in str(message["content"]) for message in projected["messages"])
    assert any(message["role"] == "tool" for message in projected["messages"])


def test_compaction_snapcompact_text_only_fallback(initialized_worker):
    worker, _ = initialized_worker
    prepared = _prepare(worker, _state_history(billed=190000), settings={"strategy": "snapcompact", "keepRecentTokens": 150})
    assert prepared["kind"] == "compaction_prepared"
    assert prepared["preparation"]["settings"]["strategy"] == "context-full"


@pytest.mark.parametrize("checkpoint", ["agent_end", "before_request"])
def test_compaction_headroom_blocks_continuation(initialized_worker, checkpoint):
    worker, _ = initialized_worker
    state = _state_history(assistant_text="unshakable " + "x" * 12000, billed=120)
    prepared = _prepare(worker, state, checkpoint=checkpoint, window=10000, settings={"keepRecentTokens": 3100})
    finalized, _ = _finalize(worker, prepared)
    assert "continuation" not in finalized


@pytest.mark.parametrize("checkpoint,auto_continue,expected", [
    ("agent_end", True, True), ("agent_end", False, False), ("before_request", True, False),
])
def test_compaction_continuation_requires_stock_headroom(initialized_worker, checkpoint, auto_continue, expected):
    worker, _ = initialized_worker
    prepared = _prepare(worker, _state_history(billed=190000), checkpoint=checkpoint,
                        settings={"keepRecentTokens": 150, "autoContinue": auto_continue})
    finalized, _ = _finalize(worker, prepared)
    assert ("continuation" in finalized) is expected
    if expected:
        prompt = (_NODE_MODULES / "@oh-my-pi/pi-coding-agent/src/prompts/system/auto-continue.md").read_text()
        assert finalized["continuation"][-1]["content"] == [{"type": "text", "text": prompt}]
        assert finalized["continuation"][-1]["role"] == "developer"


@pytest.mark.parametrize("reason,checkpoint", [("overflow", "overflow"), ("threshold", "agent_end")])
@pytest.mark.parametrize("tool_path,protected", [("log.txt", False), ("local://PLAN.md:1-50", True)])
def test_compaction_shake_preserves_blocks_and_plan(initialized_worker, reason, checkpoint, tool_path, protected):
    worker, _ = initialized_worker
    state = _state_history(tool_path=tool_path, tool_text="huge output\n" + "x" * 12000, billed=120)
    prepared = _prepare(worker, state, reason=reason, checkpoint=checkpoint, window=10000,
                        settings={"keepRecentTokens": 3100})
    finalized, _ = _finalize(worker, prepared)
    if protected:
        assert finalized["messages"][-1] == state.messages[-1]
        assert "continuation" not in finalized
        if reason == "overflow":
            assert finalized["retry"] is False
    else:
        result = finalized["messages"][-1]
        assert result["role"] == "toolResult"
        assert isinstance(result["content"], list)
        assert "[shaken ~" in result["content"][0]["text"]
        assert len(result["content"]) == 1  # stock shake.ts:404-408 replaces whole toolResult
        assert result["prunedAt"] == prepared["preparation"]["timestamp"]
        assert finalized["messages"][-2]["content"] == state.messages[-2]["content"]


def test_compaction_stored_floor_uses_active_native_history(initialized_worker):
    worker, _ = initialized_worker
    state = _state_history(assistant_text="large native content " + "x" * 12000)
    assert _prepare(worker, state, window=10000)["kind"] == "compaction_prepared"


def test_compaction_ignores_stale_billed_usage(initialized_worker):
    worker, _ = initialized_worker
    state = _state_history(billed=190000)
    prepared = _prepare(worker, state)
    prepared["preparation"]["timestamp"] += 1
    finalized, _ = _finalize(worker, prepared)
    state.messages = finalized["messages"]
    assert _prepare(worker, state)["kind"] == "compaction_unavailable"


def test_compaction_midturn_disabled(initialized_worker):
    worker, _ = initialized_worker
    prepared = _prepare(worker, _state_history(billed=190000), checkpoint="before_request",
                        settings={"midTurnEnabled": False})
    assert prepared["kind"] == "compaction_unavailable"


def test_conductor_tool_turn_threshold_compaction_next_main_equals_stock(tmp_path, monkeypatch):
    import asyncio
    from copy import deepcopy
    from tests.rl.harness import test_omp_16_2_13_native_stream_conductor as harness

    original_load = harness.load_e4_target
    monkeypatch.setattr(harness, "load_e4_target", lambda _target: original_load("oh-my-pi-r3@16.2.13"))
    responses = []

    def response(text, calls=(), tokens=120):
        step = {
            "assistant_content": text,
            "tool_calls": list(calls),
            "finish_reason": "tool_calls" if calls else "stop",
            "usage": {"prompt_tokens": tokens, "completion_tokens": 40, "total_tokens": tokens + 40},
        }
        return (200, "text/event-stream", b"".join(harness._response_chunks("compaction-e2e", len(responses), step)))

    call = lambda ident: {"id": ident, "name": "read", "arguments": '{"path":"log.txt","i":"Reading log"}'}
    responses.extend([
        response("Older work " + "x" * 90000, [call("old")]),
        response("Read the next log", [call("new")], tokens=190000),
    ])
    observations = {}
    original_port = harness._Omp16213WorkerPort

    class RecordingPort(original_port):
        async def invoke_native_phase(self, operation, payload, **kwargs):
            result = await super().invoke_native_phase(operation, payload, **kwargs)
            if operation == "initialize":
                observations["system_prompt"] = result["system_prompt"]
            if operation == "prepare_compaction" and result["kind"] == "compaction_prepared":
                assert "prepared" not in observations, "unexpected repeated threshold compaction"
                observations["prepared"] = result
                observations["history"] = deepcopy(payload["messages"])
                observations["checkpoint"] = payload["checkpoint"]
                summaries = []
                if result["summary_request"] is not None:
                    summaries.append("## Goal\nOlder tool work summarized.")
                if result["turn_prefix_request"] is not None:
                    summaries.append("## Original Request\nRead the logs.")
                summaries.append("I read the logs.")
                observations["summaries"] = summaries
                responses.extend(response(text) for text in [*summaries, "next answer"])
            return result

    monkeypatch.setattr(harness, "_Omp16213WorkerPort", RecordingPort)

    def seed(workspace):
        workspace.mkdir()
        (workspace / "log.txt").write_text("first line\nsecond line\n")

    episode = asyncio.run(harness._run_episode(tmp_path, responses, task="Read the logs.", seed=seed))
    assert episode.error is None
    assert observations["checkpoint"] == "before_request"
    history = observations["history"]
    assert any(message["role"] == "toolResult" for message in history)
    assert all(isinstance(message["content"], list) and message["usage"]["input"] > 0
               for message in history if message["role"] == "assistant")
    assert "execute_batch" in episode.port.operations
    stock = _run_stock_session(history, {
        "strategy": "context-full", "keepRecentTokens": 20000,
    }, observations["summaries"], observations["system_prompt"], tmp_path / "workspace")
    wire = episode.requests[-1]["body"]
    assert wire == stock["requests"][-1]
    assert stock["compacted"][0]["role"] == "compactionSummary"
    assert wire["messages"][1]["role"] == "user"
    assert stock["compacted"][0]["summary"] in wire["messages"][1]["content"][0]["text"]


def test_shake_splices_assistant_span_preserving_tool_calls(initialized_worker):
    worker, _ = initialized_worker
    state = _state_history(tool_path="local://PLAN.md", tool_text="small result",
                           tool_preamble="before\n```text\n" + "x" * 12000 + "\n```\nafter")
    original_call = state.messages[-2]["content"][1]
    prepared = _prepare(worker, state, reason="overflow", checkpoint="overflow",
                        window=10000, settings={"keepRecentTokens": 3100})
    finalized, _ = _finalize(worker, prepared)
    assistant = finalized["messages"][-2]
    assert assistant["content"][0]["text"].startswith("before\n")
    assert "[shaken ~" in assistant["content"][0]["text"]
    assert assistant["content"][0]["text"].endswith("\nafter")
    assert assistant["content"][1] == original_call
    assert finalized["messages"][-1] == state.messages[-1]


def test_stock_usage_phase_preserves_cache_accounting(initialized_worker):
    worker, _ = initialized_worker
    parsed = worker.phase("parse_provider_usage", {"usage": {
        "prompt_tokens": 1000, "completion_tokens": 100,
        "prompt_cache_hit_tokens": 200, "prompt_cache_miss_tokens": 800,
        "completion_tokens_details": {"reasoning_tokens": 25},
    }})
    assert parsed["kind"] == "assistant_usage"
    assert parsed["usage"]["cacheRead"] == 200
    assert parsed["usage"]["cacheWrite"] == 0  # DeepSeek misses are ordinary input, not cache writes.
    assert parsed["usage"]["input"] == 800
    assert parsed["usage"]["output"] == 100  # Stock does not add billed reasoning tokens twice.
    empty = worker.phase("parse_provider_usage", {"usage": None})
    assert empty["usage"]["totalTokens"] == 0


@pytest.mark.parametrize("checkpoint", ["before_request", "agent_end"])
@pytest.mark.parametrize("timestamp_delta", [0, 1])
def test_agent_end_continuation_ignores_retained_billing(initialized_worker, checkpoint, timestamp_delta):
    worker, _ = initialized_worker
    state = _state_history(billed=190000)
    prepared = _prepare(worker, state, checkpoint="agent_end")
    prepared["preparation"]["timestamp"] += timestamp_delta
    finalized, _ = _finalize(worker, prepared)
    retained = state.messages[finalized["first_kept_index"]:]
    assert retained[-1]["role"] == "assistant"
    assert retained[-1]["usage"]["input"] == 190000
    summary = finalized["compaction_message"]
    state.messages = [summary, *retained]
    state.resume_after_compaction(finalized["continuation"])
    res = _prepare(worker, state, checkpoint=checkpoint)
    assert res["kind"] == "compaction_unavailable"
    assert res["reason"] == "not_triggered"


def test_two_compactions_preserve_stock_chronological_journal(initialized_worker, tmp_path):
    from copy import deepcopy
    from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall

    worker, init = initialized_worker
    state = _state_history(billed=190000, tool_text="first result " + "a" * 800)
    initial_history = deepcopy(state.messages)
    settings = {"keepRecentTokens": 30000, "autoContinue": False, "strategy": "context-full"}
    first_prepared = _prepare(worker, state, settings=settings)
    first_finalized, first_followup = _finalize(worker, first_prepared, "## Goal\nFirst pass.", "First pass finished.")
    state.messages = first_finalized["messages"]
    assert _prepare(worker, state, checkpoint="before_request", settings=settings)["kind"] == "compaction_unavailable"
    first_wire = worker.project_request(state.messages)["request_body"]

    def respond(text, tokens, calls=()):
        state.begin_query()
        raw_usage = {"prompt_tokens": tokens, "completion_tokens": 40, "total_tokens": tokens + 40}
        return state.prepare_response(NativeProviderResponse(
            binding_digest="binding", request_digest="request", response_id="response",
            model=state.model_id, content=text, finish_reason="tool_calls" if calls else "stop",
            tool_calls=calls, usage=raw_usage,
        ), [json.loads(call.arguments) for call in calls],
            usage=worker.phase("parse_provider_usage", {"usage": raw_usage})["usage"])

    state.resume_after_compaction([])
    respond("next answer", 120)
    user = {
        "role": "user", "content": [{"type": "text", "text": "New task " + "y" * 4000}],
        "attribution": "user", "timestamp": state.messages[-1]["timestamp"] + 1,
    }
    state.resume_after_compaction([user])
    call = NativeToolCall("c2", "read", json.dumps({"path": "log.txt", "i": "Reading log"}))
    turn = respond("New work " + "z" * 400, 190000, (call,))
    state.commit_tool_results(turn.calls, [{
        "id": "c2", "completion_index": 0, "isError": False,
        "content": [{"type": "text", "text": "second result " + "b" * 800}],
    }])
    second_messages = deepcopy(state.messages[-3:])
    second_prepared = _prepare(worker, state, settings=settings)
    assert second_prepared["kind"] == "compaction_prepared"
    # The previous marker remains after its retained native messages.
    entries = second_prepared["preparation"]["entries"]
    marker_index = next(i for i, entry in enumerate(entries) if entry["type"] == "compaction")
    assert marker_index > 0
    assert entries[marker_index - 1]["type"] == "message"
    assert entries[marker_index - 1]["message"] == initial_history[-1]
    second_finalized, second_followup = _finalize(worker, second_prepared, "## Goal\nSecond pass.", "Second pass finished.")
    second_wire = worker.project_request(second_finalized["messages"])["request_body"]

    prefix = "## Original Request\nContinue the current task."
    first_summaries = ["## Goal\nFirst pass."]
    second_summaries = ["## Goal\nSecond pass."]
    if first_prepared["turn_prefix_request"] is not None:
        first_summaries.append(prefix)
    if second_prepared["turn_prefix_request"] is not None:
        second_summaries.append(prefix)
    first_summaries.append("First pass finished.")
    second_summaries.append("Second pass finished.")
    stock = _run_stock_session(
        initial_history, settings, first_summaries, init["system_prompt"], tmp_path,
        second_messages=second_messages, second_summaries=second_summaries,
    )
    for prepared, followup, wire, oracle in (
        (first_prepared, first_followup, first_wire, stock["rounds"][0]),
        (second_prepared, second_followup, second_wire, stock["rounds"][1]),
    ):
        assert prepared["first_kept_index"] == oracle["firstKeptIndex"]
        assert prepared["summary_request"]["messages"] == oracle["requests"][0]["messages"]
        if prepared["turn_prefix_request"] is not None:
            assert prepared["turn_prefix_request"]["messages"] == oracle["requests"][1]["messages"]
        assert followup["request"]["messages"] == oracle["requests"][-2]["messages"]
        assert wire == oracle["requests"][-1]
