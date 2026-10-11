"""Live, unmodified-stock execution conformance for the dated profiles.

Run with OPENCODE_TEST_BINARY and OPENCODE_PLUGIN_ROOT pointing at the pinned
installations. Plain tests compare with a separate stock CLI against the same
workspace. Plugin tests compare the proxy output to the stock server's own
completed tool parts, including stock errors when dependencies are unavailable.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from breadboard_engine.execution.opencode_tools import (
    DEFAULT_BINARY, OpenCodeToolSession, StockExecutionError,
    responses_payload, stock_system_messages, stock_tool_schema, upstream_headers,
)
from scripts.compaction_lanes.opencode_lane import ResponsesProvider


MODEL = "openai/gpt-5.1-codex-mini"


def response(session, calls=(), text=None):
    result = session.text_response(text or "")
    if text is None:
        result["output"] = []
    result["output"].extend({"type": "function_call", "id": "fc_" + call_id,
                             "call_id": call_id, "name": name, "arguments": json.dumps(args),
                             "status": "completed"} for call_id, name, args in calls)
    return result


def isolated_environment(root):
    return {"HOME": str(root / "home"), "OPENCODE_TEST_HOME": str(root / "home"),
            **{"XDG_" + name.upper() + "_HOME": str(root / name) for name in ("config", "data", "cache", "state")},
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}


@pytest.fixture
def binary():
    path = Path(os.environ.get("OPENCODE_TEST_BINARY", DEFAULT_BINARY))
    if not path.is_file():
        pytest.skip("Pinned stock OpenCode binary is not installed")
    return path


@pytest.fixture
def plain(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                                  stock_config={"permission": "allow", "agent": {"title": {"disable": True}}},
                                  environment=isolated_environment(tmp_path / "stock"))
    try:
        yield session
    finally:
        session.close()


def direct_stock(binary, workspace, scratch, calls, *, captured_requests=None, raw_arguments=None):
    provider = ResponsesProvider()
    stop = {"id": "resp_finish", "object": "response", "model": MODEL.split("/", 1)[1],
            "created_at": 1, "status": "completed", "output": [],
            "usage": {"input_tokens": 0, "output_tokens": 0}}
    scripted = copy.deepcopy(stop)
    scripted["output"] = [{"type": "function_call", "id": "fc_" + cid, "call_id": cid,
                           "name": name, "arguments": (raw_arguments or {}).get(cid, json.dumps(args)), "status": "completed"}
                          for cid, name, args in calls]
    provider.engine.set_script([{"type": "raw", "headers": {"content-type": "text/event-stream"},
                             "chunks": [responses_payload(scripted, True).decode()]},
                            {"type": "raw", "headers": {"content-type": "text/event-stream"},
                             "chunks": [responses_payload(stop, True).decode()]}])
    url = provider.start() + "/v1"
    config = {"model": MODEL, "permission": "allow", "compaction": {"auto": False, "prune": False},
              "provider": {"openai": {"options": {"baseURL": url, "apiKey": "test"}}}}
    env = {**os.environ, **isolated_environment(scratch), "OPENCODE_CONFIG_CONTENT": json.dumps(config),
           "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"}
    try:
        run = subprocess.run([str(binary), "run", "--format", "json", "--agent", "build", "--title", "Direct oracle"],
                             cwd=workspace, env=env, input="Execute the approved operation.",
                             text=True, capture_output=True, timeout=60)
        assert run.returncode == 0, run.stderr
        requests = provider.engine.recorded_requests
        if captured_requests is not None:
            captured_requests.extend(requests)
        return {item["call_id"]: item["output"] for row in requests for item in row["json"].get("input", [])
                if item.get("type") == "function_call_output"}
    finally:
        provider.stop()


@pytest.mark.parametrize("name,args", [
    ("bash", {"command": "printf stock-result", "description": "Print a deterministic result"}),
    ("read", {"filePath": "sample.txt"}),
    ("apply_patch", {"patchText": "*** Begin Patch\n*** Add File: created.txt\n+stock patch\n*** End Patch"}),
])
def test_plain_result_matches_separate_direct_stock_run(plain, binary, tmp_path, name, args):
    workspace = Path(plain.workspace)
    (workspace / "sample.txt").write_text("alpha\nbeta\n")
    call = ("call_compare", name, args)
    plain.user([{"type": "text", "text": "Execute the approved operation."}], agent="build")
    plain.submit(response(plain, [call]))
    actual = plain.result(call[0])["__mvi_text_output"]
    plain.submit(response(plain))
    if (workspace / "created.txt").exists():
        (workspace / "created.txt").unlink()
    oracle = direct_stock(binary, workspace, tmp_path / "oracle", [call])
    assert actual == oracle[call[0]]


def test_stock_prune_compaction_and_persistent_read_guard(plain):
    workspace = Path(plain.workspace)
    (workspace / "sample.txt").write_text("alpha\nbeta\n")
    first = plain.user([{"type": "text", "text": "Read and remember the file."}], agent="build")
    assert stock_system_messages(first)
    assert {t["name"] for t in stock_tool_schema(first)} >= {"bash", "read", "apply_patch", "task"}
    plain.submit(response(plain, [("call_read", "read", {"filePath": str(workspace / "sample.txt")})]))
    plain.prune(["call_read"])
    messages = plain.api("GET", "/session/" + plain.session_id + "/message")
    assert any(p.get("state", {}).get("time", {}).get("compacted") for m in messages for p in m["parts"])
    plain.compact("The file contains alpha and beta.")
    assert sum(r["kind"] == "compaction" for r in plain.records) == 1
    plain.submit(response(plain, [("call_patch", "apply_patch", {"patchText": "*** Begin Patch\n*** Update File: sample.txt\n@@\n-alpha\n+gamma\n*** End Patch"})]))
    assert (workspace / "sample.txt").read_text() == "gamma\nbeta\n"
    plain.submit(response(plain))


def test_proxy_strips_labels_and_fails_closed_on_unrelated_request(plain):
    headers = {"x-bb-stock-session": plain.session_id, "x-bb-stock-agent": "title",
               "x-bb-stock-provider": "openai", "Content-Type": "application/json", "User-Agent": "opencode/1.2.17"}
    assert upstream_headers(headers) == {"Content-Type": "application/json", "User-Agent": "opencode/1.2.17"}
    assert plain.classify({k.lower(): v for k, v in headers.items()}) == "title"
    with pytest.raises(StockExecutionError, match="Unlabelled"):
        plain.classify({})
    plain.process.terminate()
    plain.process.wait(timeout=10)
    with pytest.raises(StockExecutionError):
        plain.user([{"type": "text", "text": "No fallback is allowed."}])


def test_native_auto_compaction_exposes_prompt_and_results_before_bb_summary(plain):
    plain.user([{"type": "text", "text": "Inspect then compact."}], agent="build")
    payload = response(plain, [("call_auto", "bash", {"command": "printf before-summary",
                                                   "description": "Inspect before compaction"})])
    payload["usage"] = {"input_tokens": 390000, "output_tokens": 1}
    plain.submit(payload)
    assert plain.compaction_pending is not None
    assert plain.result("call_auto")["__mvi_text_output"] == "before-summary"
    prompt = plain.compaction_prompt()
    assert prompt is plain.compaction_pending["body"]
    assert plain.compaction_summary is None
    assert any(item.get("role") == "user" for item in prompt["input"])
    plain.compact("BB actual committed summary.")
    messages = plain.api("GET", "/session/" + plain.session_id + "/message")
    assert any(message["info"].get("summary") and
               any(part.get("text") == "BB actual committed summary." for part in message["parts"])
               for message in messages)
    assert len([row for row in plain.records if row["kind"] == "compaction"]) == 1
    plain.submit(response(plain))


def test_original_provider_overflow_creates_native_marker_and_replays_user(plain):
    original = "Retry this original user after provider overflow."
    plain.user([{"type": "text", "text": "Older context to compact."}], agent="build")
    plain.submit(response(plain, text="Older stable context."))
    plain.user([{"type": "text", "text": original}], agent="build")
    error = json.dumps({"error": {"message": "maximum context length exceeded",
                                  "type": "invalid_request_error", "code": "context_length_exceeded"}}).encode()
    plain.submit_overflow(400, {"content-type": "application/json"}, error)
    assert plain.compaction_pending is not None
    messages = plain.api("GET", "/session/" + plain.session_id + "/message")
    assert any(part.get("type") == "compaction" and part.get("overflow")
               for message in messages for part in message["parts"])
    plain.compact("BB summary after the original overflow.")
    last_user = [item for item in plain.wait_pending()["input"] if item.get("role") == "user"][-1]
    content = last_user["content"]
    text = content if isinstance(content, str) else "".join(part.get("text", "") for part in content)
    # Stock prompt.ts:638-643 decorates the genuine replay at serialization.
    assert text == "\n".join([
        "<system-reminder>", "The user sent the following message:", original, "",
        "Please address this message and continue with your tasks.", "</system-reminder>",
    ])
    plain.submit(response(plain))


def direct_stock_content(binary, workspace, scratch, payload):
    provider = ResponsesProvider()
    provider.engine.set_script([{"type": "raw", "headers": {"content-type": "text/event-stream"},
                                "chunks": [responses_payload(payload, True).decode()]}])
    url = provider.start() + "/v1"
    config = {"model": MODEL, "provider": {"openai": {"options": {"baseURL": url, "apiKey": "test"}}}}
    env = {**os.environ, **isolated_environment(scratch), "OPENCODE_CONFIG_CONTENT": json.dumps(config),
           "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"}
    try:
        run = subprocess.run([str(binary), "run", "--format", "json", "--agent", "build", "--title", "Direct oracle"],
                             cwd=workspace, env=env, input="Preserve the model's native content.",
                             text=True, capture_output=True, timeout=60, check=True)
        return [item["part"]["text"] for line in run.stdout.splitlines()
                if line.startswith("{") and (item := json.loads(line)).get("type") == "text"]
    finally:
        provider.stop()


@pytest.mark.parametrize("kind", ["reasoning", "refusal"])
def test_mirrored_native_reasoning_and_refusal_parts_are_preserved(plain, binary, tmp_path, kind):
    plain.user([{"type": "text", "text": "Preserve the model's native content."}], agent="build")
    payload = response(plain, text="Final answer.")
    if kind == "reasoning":
        payload["output"].insert(0, {"type": "reasoning", "id": "rs_native",
                                     "summary": [{"type": "summary_text", "text": "Native reasoning."}]})
        expected = "Native reasoning."
    else:
        payload["output"][0]["content"] = [{"type": "refusal", "refusal": "Native refusal."}]
        expected = "Native refusal."
    plain.submit(payload)
    messages = plain.api("GET", "/session/" + plain.session_id + "/message")
    if kind == "reasoning":
        assert any(part.get("text") == expected for message in messages for part in message["parts"])
    else:
        # Refusal events must retain stock's SDK behaviour, not invented text.
        native_text = [part["text"] for message in messages if message["info"]["role"] == "assistant"
                       for part in message["parts"] if part["type"] == "text"]
        assert native_text == direct_stock_content(binary, plain.workspace, tmp_path / "refusal-oracle", payload)
