from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import pytest
from breadboard_engine.execution.opencode.lifecycle import DEFAULT_BINARY
from breadboard_engine.execution.opencode.session import OpenCodeToolSession
from breadboard_engine.execution.opencode.wire import StockExecutionError
from breadboard_engine.execution.opencode.wire import responses_payload
from breadboard_engine.execution.opencode.wire import stock_system_messages
from breadboard_engine.execution.opencode.wire import stock_tool_schema
from breadboard_engine.execution.opencode.wire import upstream_headers
from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
import httpx
from breadboard_engine.compaction_lanes.opencode_stock import native_session_evidence

"""Live, unmodified-stock execution conformance for the dated profiles.

Run with OPENCODE_TEST_BINARY and OPENCODE_PLUGIN_ROOT pointing at the pinned
installations. Plain tests compare with a separate stock CLI against the same
workspace. Plugin tests compare the proxy output to the stock server's own
completed tool parts, including stock errors when dependencies are unavailable.
"""





MODEL = "openai/gpt-5.1-codex-mini"
LIVE_MCP = os.environ.get("OPENCODE_TEST_LIVE_MCP") == "1"
live_mcp = pytest.mark.skipif(
    not LIVE_MCP, reason="Set OPENCODE_TEST_LIVE_MCP=1 for remote MCP conformance"
)


def hermetic_stock_config(config):
    config = copy.deepcopy(config)
    if not LIVE_MCP:
        mcp = config.setdefault("mcp", {})
        for name in ("websearch", "context7", "grep_app"):
            mcp[name] = {"enabled": False}
    return config



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







def _assert_child_clock_replay(messages, current):
    # message-v2.ts:740 orders only by creation time. A summary sorted before
    # its tied marker leaves that compaction part pending (prompt.ts:304-314).
    # Replay uses the latest synthetic user, not the original marker (532).
    markers = [message for message in messages if message["info"]["role"] == "user"
               and any(part["type"] == "compaction" and part["auto"] for part in message["parts"])]
    assert len(markers) == 1
    marker = markers[0]
    summaries = [message for message in messages if message["info"].get("summary") is True
                 and message["info"].get("finish") and not message["info"].get("error")
                 and message["info"]["parentID"] == marker["info"]["id"]]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["info"]["time"]["created"] == marker["info"]["time"]["created"]
    assert messages.index(summary) < messages.index(marker)
    parent = next(message for message in reversed(messages) if message["info"]["role"] == "user")
    assert current["parentID"] == parent["info"]["id"]
    assert current["sessionID"] == parent["info"]["sessionID"] == marker["info"]["sessionID"]
    assert current["time"]["created"] >= parent["info"]["time"]["created"]
    assert any(part["type"] == "text" and part.get("synthetic") is True and part["text"] ==
               "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed."
               for part in parent["parts"])


@pytest.mark.parametrize("child_compaction", [False, True])
def test_stock_task_executes_real_child_and_strips_transport_labels(tmp_path, child_compaction, monkeypatch, record_property):
    binary = Path(os.environ.get("OPENCODE_TEST_BINARY", DEFAULT_BINARY))
    if not binary.is_file():
        pytest.skip("Pinned stock OpenCode binary is not installed")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    oracle = ResponsesProvider()
    url = oracle.start()
    usage = {"input_tokens": 0, "output_tokens": 0}
    envelope = {"id": "resp_child", "object": "response", "model": "gpt-5.1-codex-mini",
                "created_at": 1, "status": "completed", "usage": usage}
    child_call = {"type": "function_call", "id": "fc_child", "call_id": "call_child",
                  "name": "bash", "arguments": json.dumps({"command": "printf child-result", "description": "Inspect child"}),
                  "status": "completed"}
    child_text = {"type": "message", "id": "msg_child", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": "child-result", "annotations": []}]}
    forwarded = []
    summary_requests = []
    main_requests = []

    def auxiliary(body, headers, path, provider, *, raw_body):
        assert provider == "openai"
        assert not any(key.lower().startswith("x-bb-stock-") for key in headers)
        forwarded.append(body)
        assert json.loads(raw_body) == body
        sid = body["prompt_cache_key"]
        assert session.api("GET", "/session/" + sid)["parentID"] == session.session_id
        messages = session.api("GET", "/session/" + sid + "/message")
        active = [message["info"] for message in messages
                  if message["info"]["role"] == "assistant"
                  and not message["info"].get("finish") and not message["info"].get("error")]
        assert len(active) == 1
        current = active[0]
        if current["agent"] == "compaction":
            assert child_compaction and current.get("summary") is True and len(main_requests) == 1
            if summary_requests:
                _assert_child_clock_replay(messages, current)
            summary_requests.append(current["id"])
            output, response_usage = [child_text], usage
        else:
            assert current["agent"] == "explore" and not current.get("summary")
            assert len(main_requests) < 2
            output = [child_call] if not main_requests else [child_text]
            response_usage = {"input_tokens": 390000, "output_tokens": 1} if child_compaction and not main_requests else usage
            main_requests.append(current["id"])
        oracle.engine.set_script([{"type": "raw", "headers": {"content-type": "text/event-stream"},
                                  "chunks": [responses_payload({**envelope, "usage": response_usage,
                                                               "output": output}, True).decode()]}])
        result = httpx.post(url + path, headers=headers, content=raw_body, timeout=60)
        return result.status_code, dict(result.headers), result.content

    environment = {"HOME": str(tmp_path / "home"), "OPENCODE_TEST_HOME": str(tmp_path / "home"),
                   **{"XDG_" + name.upper() + "_HOME": str(tmp_path / name) for name in ("config", "data", "cache", "state")},
                   "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}
    session = None
    native_events = []
    try:
        session = OpenCodeToolSession(str(workspace), "openai/gpt-5.1-codex-mini", binary=binary,
                                      auxiliary=auxiliary, timeout=60, environment=environment,
                                      stock_config={"permission": "allow", "agent": {"title": {"disable": True}}})
        read_line = session.event_connection.readline
        def observe_line(*args, **kwargs):
            line = read_line(*args, **kwargs)
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                if event["type"] in {"message.updated", "message.part.updated", "session.status",
                                     "session.idle", "session.compacted", "session.error"}:
                    native_events.append(event)
            return line
        monkeypatch.setattr(session.event_connection, "readline", observe_line)
        session.user([{"type": "text", "text": "Delegate the inspection."}], agent="build")
        task = {"type": "function_call", "id": "fc_parent", "call_id": "call_parent", "name": "task",
                "arguments": json.dumps({"description": "Inspect child", "prompt": "Inspect the workspace and report child-result.",
                                         "subagent_type": "explore"}), "status": "completed"}
        try:
            session.submit({**envelope, "output": [task]})
        except StockExecutionError as exc:
            if session.failure:
                raise
            evidence = {"native": native_session_evidence(session.api, session.session_id),
                        "events": native_events, "records": session.records,
                        "request_done": session.request_done.is_set(),
                        "native_prompt_done": session.native_prompt_done.is_set(),
                        "pending": session.pending, "compaction_pending": session.compaction_pending}
            raise AssertionError(f"{exc}\nNative task diagnostic:\n" + json.dumps(evidence)) from exc
        actual = session.result("call_parent")["__mvi_text_output"]
        assert "child-result" in actual
        children = session.api("GET", "/session/" + session.session_id + "/children")
        assert len(children) == 1
        child_messages = session.api("GET", "/session/" + children[0]["id"] + "/message")
        assert any(p.get("tool") == "bash" and p.get("state", {}).get("output") == "child-result"
                   for message in child_messages for p in message["parts"])
        summaries = [message["info"] for message in child_messages if message["info"].get("summary") is True]
        assert len(summaries) == len(summary_requests)
        assert bool(summaries) is child_compaction
        assert all(summary.get("finish") == "stop" and not summary.get("error") for summary in summaries)
        assert len(main_requests) == 2
        record_property("child_compactions", len(summary_requests))
        observed = oracle.engine.recorded_requests
        assert [row["json"] for row in observed] == forwarded
        assert not any(key.lower().startswith("x-bb-stock-") for row in observed for key in row["headers"])
        assert [r["kind"] for r in session.records if r["kind"] == "child"] == ["child"] * (2 + len(summary_requests))
        session.submit({**envelope, "output": []})
    finally:
        if session is not None:
            session.close()
        oracle.stop()

def raw_descriptor(payload):
    return {"type": "raw", "headers": {"content-type": "text/event-stream"},
            "chunks": [responses_payload(payload, True).decode()]}


def connect_auxiliary(session, provider):
    def auxiliary(body, headers, path, provider_id, *, raw_body):
        assert provider_id == "openai"
        assert json.loads(raw_body) == body
        assert not any(key.lower().startswith("x-bb-stock-") for key in headers)
        return session.forward_auxiliary_http(provider.base_url + path, headers, raw_body)
    session.auxiliary = auxiliary



def test_fallback_response_preserves_literal_text_and_usage():
    from types import SimpleNamespace
    from breadboard_engine.execution.opencode.wire import response_from_result
    result = SimpleNamespace(
        raw_response=None, usage={"input_tokens": 7, "output_tokens": 3},
        messages=[SimpleNamespace(content="Literal answer", tool_calls=[])],
    )
    payload = response_from_result(result, "gpt-fixture")
    assert payload["model"] == "gpt-fixture"
    assert isinstance(payload["created_at"], int)
    assert payload["output"][0]["content"][0]["text"] == "Literal answer"
    assert payload["usage"]["input_tokens"] == 7
    assert payload["usage"]["output_tokens"] == 3


def test_upstream_headers_remove_case_mixed_credentials_without_mutation():
    headers = {
        "X-BB-Stock-Session": "session", "X-BB-Stock-Agent": "agent",
        "X-BB-Stock-Provider": "openai", "Authorization": "secret",
        "X-API-Key": "secret", "Host": "local", "Content-Length": "1",
        "Connection": "close", "Accept-Encoding": "gzip",
        "Content-Type": "application/json", "X-Custom": "retained",
    }
    original = dict(headers)
    assert upstream_headers(headers) == {
        "Content-Type": "application/json", "X-Custom": "retained",
    }
    assert headers == original


def test_canonical_pinned_catalogs_retain_all_advertised_tools():
    from breadboard_engine.compilation.tool_registry import load_tool_registry
    from breadboard_engine.compilation.tool_yaml_loader import load_yaml_tools
    for catalog, expected in (("defs_opencode_1_2_17", 10), ("defs_omo_3_10_0", 31)):
        directory = Path(__file__).resolve().parents[2] / "implementations/tools" / catalog
        registry = load_tool_registry(directory)
        dynamic = load_yaml_tools(str(directory))
        assert len(registry.tools_by_name) == expected
        assert {tool.name for tool in dynamic.tools} == set(registry.tools_by_name)
        for name in ("apply_patch", "bash", "question", "read", "todowrite", "webfetch"):
            assert name in registry.tools_by_name


def test_claude_package_default_accepts_unmapped_uid(monkeypatch, tmp_path):
    from breadboard_engine.execution import claude_tools
    def unmapped(uid):
        raise KeyError(uid)
    monkeypatch.setattr(claude_tools.pwd, "getpwuid", unmapped)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert claude_tools._default_package().is_relative_to(tmp_path)


def test_owned_auxiliary_shutdown_cancels_forward_and_joins_listener():
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler
    import threading
    from breadboard_engine.execution.local_http_proxy import LocalHTTPProxy
    from breadboard_engine.execution.opencode_auxiliary import AuxiliaryCancelled, AuxiliaryTransport
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            entered.set()
            release.wait(10)
    proxy = LocalHTTPProxy(Handler)
    proxy.start()
    transport = AuxiliaryTransport()
    try:
        with ThreadPoolExecutor() as pool:
            request = pool.submit(
                transport.forward, "owned", f"http://127.0.0.1:{proxy.server_port}/", {}, b"{}",
            )
            assert entered.wait(10)
            transport.close()
            with pytest.raises(AuxiliaryCancelled):
                request.result(timeout=5)
            assert not transport.thread.is_alive()
    finally:
        release.set()
        transport.close()
        proxy.close()
    assert not proxy.listener.is_alive()


@pytest.fixture
def omo_dependencies():
    from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, LSP_ROOT
    root = Path(os.environ.get("OPENCODE_PLUGIN_ROOT", PLUGIN)).expanduser().resolve()
    required = (root / "package.json", root / "dist/index.js",
                root / "node_modules/@ast-grep/cli/sg",
                LSP_ROOT / "node_modules/.bin/typescript-language-server",
                LSP_ROOT / "node_modules/.bin/pyright-langserver")
    if not all(path.is_file() for path in required):
        pytest.skip("Pinned OMO/ast-grep/LSP conformance dependencies are not installed")


@pytest.fixture
def short_stock_workspace():
    import tempfile
    # Unix sockets must fit sockaddr_un even when pytest's root is deeply nested.
    # The entire short workspace is owned, admitted by the same sandbox policy,
    # and removed only after dependent stock sessions have shut down.
    with tempfile.TemporaryDirectory(prefix="bb-stock-", dir="/tmp") as directory:
        yield Path(directory).resolve()


def test_auxiliary_upstream_disconnect_is_observed_by_forward_owner():
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler
    import socket
    import threading
    import httpx
    from breadboard_engine.execution.local_http_proxy import LocalHTTPProxy
    from breadboard_engine.execution.opencode_auxiliary import AuxiliaryTransport
    entered, disconnect = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            entered.set()
            disconnect.wait(10)
            self.connection.shutdown(socket.SHUT_RDWR)
    proxy = LocalHTTPProxy(Handler)
    proxy.start()
    transport = AuxiliaryTransport()
    unobserved = []
    try:
        with ThreadPoolExecutor() as pool:
            request = pool.submit(
                transport.forward, "owned", f"http://127.0.0.1:{proxy.server_port}/", {}, b"{}",
            )
            assert entered.wait(10)
            transport.loop.call_soon_threadsafe(
                transport.loop.set_exception_handler,
                lambda loop, context: unobserved.append(context),
            )
            disconnect.set()
            with pytest.raises(httpx.HTTPError):
                request.result(timeout=5)
            transport.close()
            assert not unobserved
    finally:
        disconnect.set()
        transport.close()
        proxy.close()


def test_auxiliary_failed_close_is_retried_by_closed_lifecycle():
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler
    import threading
    from breadboard_engine.execution.local_http_proxy import LocalHTTPProxy
    from breadboard_engine.execution.opencode.lifecycle import OpenCodeLifecycle
    from breadboard_engine.execution.opencode_auxiliary import AuxiliaryTransport
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            entered.set()
            release.wait(10)
    proxy = LocalHTTPProxy(Handler)
    proxy.start()
    transport = AuxiliaryTransport()
    cancel, failures = transport.cancel, [RuntimeError("cancellation timed out")]
    def flaky_cancel(session_id):
        if failures:
            raise failures.pop()
        cancel(session_id)
    transport.cancel = flaky_cancel
    try:
        with ThreadPoolExecutor() as pool:
            pool.submit(transport.forward, "owned", f"http://127.0.0.1:{proxy.server_port}/", {}, b"{}")
            assert entered.wait(10)
            with pytest.raises(RuntimeError, match="cancellation timed out"):
                transport.close()
            release.set()
            lifecycle = object.__new__(OpenCodeLifecycle)
            lifecycle.closed, lifecycle.auxiliary_transport = True, transport
            lifecycle._close_locked()
            assert transport.closed and not transport.thread.is_alive()
    finally:
        release.set()
        transport.close()
        proxy.close()
