from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
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
    worker = NativeToolWorker(cwd=str(tmp_path), spec=worker_spec)
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
