"""Exercise actual pinned plugin tools; no BB native executors or MCP replicas."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import shlex
import shutil
import socket
import urllib.error
import urllib.request
import uuid

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, responses_payload
from breadboard_engine.security.stock_install import stock_install_digest
from scripts.compaction_lanes.opencode_lane import ResponsesProvider
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


PLUGIN = Path.home() / ".cache/bb-compaction-e4/pkgs/oh-my-opencode@3.10.0-5137df72"
PLUGIN_DIGEST = "ec9c7f47c21ff078eb81231e0c0af22e8aea16b626e707f5e6d2fc128ae8e2dc"
LSP_ROOT = Path.home() / ".cache/bb-compaction-e4/pkgs/opencode-lsp-1.2.17"
LSP_PIN = {"path": str(LSP_ROOT), "digest": "2b27ba95b4a5f4968141f3c353f0e4abd8e1e9be1572b8e0d99292604ffa7478"}


def lsp_environment():
    return {"PATH": str(LSP_ROOT / "node_modules/.bin") + os.pathsep + os.environ.get("PATH", ""),
            "NODE_PATH": str(LSP_ROOT / "node_modules"),
            "BUN_INSTALL_CACHE_DIR": str(LSP_ROOT / "install-cache"),
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1"}


@pytest.fixture
def omo(tmp_path, binary):
    root = Path(os.environ.get("OPENCODE_PLUGIN_ROOT", PLUGIN)).expanduser().resolve()
    assert root == PLUGIN.resolve(), "Conformance requires the staged pinned plugin, never /tmp"
    assert stock_install_digest(root) == PLUGIN_DIGEST
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.ts").write_text("export const answer = 42;\nconsole.log(answer);\n")
    session = OpenCodeToolSession(
        str(workspace), MODEL, binary=binary, plugin_root=root, timeout=120,
        stock_config={"agent": {"title": {"disable": True}}},
        lsp_root=LSP_PIN,
        pty=True,
        read_only_roots=[{"path": str(root), "digest": PLUGIN_DIGEST}],
        environment=isolated_environment(tmp_path / "stock"),
    )
    try:
        yield session
    finally:
        session.close()


def direct_omo(binary, workspace, scratch, calls, *, captured_requests=None, root_session=None, child_script=None, sequential=False):
    provider = ResponsesProvider(record_path=scratch / "requests.jsonl")
    base = {"id": "resp_omo_oracle", "object": "response", "model": MODEL.split("/", 1)[1],
            "created_at": 1, "status": "completed", "usage": {"input_tokens": 0, "output_tokens": 0}}
    outputs = [{"type": "function_call", "id": "fc_" + cid, "call_id": cid,
                "name": name, "arguments": json.dumps(args), "status": "completed"}
               for cid, name, args in calls]
    groups = [[output] for output in outputs] if sequential else [outputs]
    provider.engine.set_script([
        {"type": "raw", "headers": {"content-type": "text/event-stream"},
         "chunks": [responses_payload({**base, "output": group}, True).decode()]}
        for group in [*groups, []]
    ])
    url = provider.start() + "/v1"
    config = {"model": MODEL, "plugin": [(PLUGIN / "dist/index.js").as_uri()],
              "agent": {"title": {"disable": True}},
              "provider": {"openai": {"options": {"baseURL": url, "apiKey": "test"}}}}
    env = {**os.environ, **isolated_environment(scratch), "OPENCODE_CONFIG_CONTENT": json.dumps(config),
           "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"}
    env.update(lsp_environment())
    try:
        session_args = []
        if root_session is not None:
            provider.engine.set_child_script(root_session["id"], child_script)
            scratch.mkdir(parents=True, exist_ok=True)
            fixture = scratch / "genuine-empty-root.json"
            fixture.write_text(json.dumps({"info": root_session, "messages": []}))
            subprocess.run([str(binary), "import", str(fixture)], cwd=workspace, env=env,
                           text=True, capture_output=True, check=True, timeout=60)
            session_args = ["--session", root_session["id"]]
        subprocess.run([str(binary), "run", "--format", "json", "--title", "Direct plugin oracle", *session_args],
                       cwd=workspace, env=env, input="Execute this stock tool group.",
                       text=True, capture_output=True, check=True, timeout=180)
        if captured_requests is not None:
            captured_requests.extend(provider.engine.recorded_requests)
        return {item["call_id"]: item["output"] for row in provider.engine.recorded_requests
                for item in row["json"].get("input", ()) if item.get("type") == "function_call_output"}
    finally:
        provider.stop()


@pytest.mark.parametrize("name,args", [
    ("lsp_symbols", {"filePath": "sample.ts", "scope": "document"}),
    ("ast_grep_search", {"pattern": "console.log($A)", "lang": "typescript", "paths": ["sample.ts"]}),
    ("session_info", {"session_id": "ses_missing_conformance"}),
    ("session_read", {"session_id": "ses_missing_conformance"}),
    ("session_search", {"query": "absent", "session_id": "ses_missing_conformance"}),
    ("skill", {"name": "git-master"}),
    ("skill_mcp", {"mcp_name": "context7", "tool_name": "resolve-library-id"}),
    ("interactive_bash", {"tmux_command": "-V"}),
])
def test_plugin_group_result_matches_direct_stock(omo, binary, tmp_path, name, args):
    held = omo.user([{"type": "text", "text": "Execute this stock tool group."}])
    assert name in {item["name"] for item in held["tools"]}
    call = ("call_omo_group", name, args)
    omo.submit(response(omo, [call]))
    actual = omo.result(call[0])["__mvi_text_output"]
    omo.submit(response(omo))
    expected = direct_omo(binary, omo.workspace, tmp_path / "oracle", [call])
    assert actual == expected[call[0]]


def test_native_context7_call_uses_stock_remote_endpoint(omo):
    held = omo.user([{"type": "text", "text": "Resolve the React library through stock MCP."}])
    name = "context7_resolve-library-id"
    assert name in {item["name"] for item in held["tools"]}
    omo.submit(response(omo, [("call_context7", name, {"libraryName": "react", "query": "React hooks"})]))
    result = omo.result("call_context7")
    assert result["__mvi_text_output"]
    # The actual stock SDK output is authoritative even when the real endpoint errors.
    native = omo.pending["body"]["input"]
    source = next(item for item in native if item.get("call_id") == "call_context7" and
                  item.get("type") == "function_call_output")
    assert source == omo.results["call_context7"]
    omo.submit(response(omo))


def _native_idle_reentry(evidence, events):
    """Qualify the pinned race from source events and new native ancestry."""
    session_id = evidence["session"]["id"]
    attempts = {
        "Summarizing session (attempt 1/2)...": 1,
        "Summarizing session (attempt 2/2)...": 2,
    }
    abort_index = next((index for index, event in enumerate(events)
                        if event["type"] == "session.error"
                        and event["properties"].get("sessionID") == session_id
                        and event["properties"].get("error", {}).get("name") == "MessageAbortedError"), None)
    if abort_index is None:
        return None
    exhausted_index = next((index for index in range(abort_index + 1, len(events))
                            if events[index]["type"] == "tui.toast.show"
                            and events[index]["properties"].get("title") == "Auto Compact Failed"
                            and events[index]["properties"].get("message") == "All recovery attempts failed. Please start a new session."
                            and events[index]["properties"].get("variant") == "error"), None)
    if exhausted_index is None:
        return None
    reset_index = next((index for index in range(exhausted_index + 1, len(events))
                       if events[index]["type"] == "tui.toast.show"
                       and events[index]["properties"].get("title") == "Auto Compact"
                       and events[index]["properties"].get("variant") == "warning"
                       and events[index]["properties"].get("message") == "Summarizing session (attempt 1/2)..."), None)
    if reset_index is None:
        return None
    def observed_attempts(rows):
        return [attempts[event["properties"]["message"]] for event in rows
                if event["type"] == "tui.toast.show"
                and event["properties"].get("title") == "Auto Compact"
                and event["properties"].get("variant") == "warning"
                and event["properties"].get("message") in attempts]
    assert observed_attempts(events[:abort_index]) == [1, 2]
    reset_attempts = observed_attempts(events[reset_index:])
    assert reset_attempts in ([1], [1, 2]), "An unqualified further recovery epoch was observed"
    aborts = [message["info"] for message in evidence["messages"]
              if message["info"]["role"] == "assistant"
              and message["info"].get("summary") is not True
              and message["info"].get("error", {}).get("name") == "MessageAbortedError"]
    assert len(aborts) == 1
    completed = aborts[0]["time"]["completed"]
    original_markers = {message["info"]["parentID"] for message in evidence["messages"]
                        if message["info"].get("summary") is True
                        and message["info"].get("finish") and not message["info"].get("error")
                        and message["info"]["time"]["created"] <= aborts[0]["time"]["created"]}
    assert len(original_markers) >= 2
    markers = {message["info"]["id"] for message in evidence["messages"]
               if message["info"]["role"] == "user"
               and message["info"]["time"]["created"] >= completed
               and any(part["type"] == "compaction" for part in message["parts"])}
    if not markers:
        return None
    assert len(markers) <= len(reset_attempts)
    for marker_id in markers:
        assert any(message["info"].get("summary") is True
                   and message["info"].get("parentID") == marker_id
                   for message in evidence["messages"])
    return markers


def _native_clock_replay(evidence, current):
    """Match this unfinished root summary to the latest inverted checkpoint."""
    session_id = evidence["session"]["id"]
    messages = evidence["messages"]
    if (current["role"] != "assistant" or current.get("summary") is not True
            or current.get("finish") or current.get("error") or current["sessionID"] != session_id):
        return None
    completed = [message for message in messages
                 if message["info"]["role"] == "assistant"
                 and message["info"].get("summary") is True
                 and message["info"].get("finish") and not message["info"].get("error")
                 and message["info"]["sessionID"] == session_id]
    if not completed:
        return None
    summary = completed[-1]
    marker = next((message for message in messages
                   if message["info"]["id"] == summary["info"]["parentID"]
                   and message["info"]["role"] == "user"
                   and message["info"]["sessionID"] == session_id
                   and any(part["type"] == "compaction" and part.get("auto") is True
                           for part in message["parts"])), None)
    if (marker is None
            or summary["info"]["time"]["created"] != marker["info"]["time"]["created"]
            or messages.index(summary) >= messages.index(marker)):
        return None
    parent = next((message for message in reversed(messages)
                   if message["info"]["role"] == "user" and message["info"]["sessionID"] == session_id), None)
    if (parent is None or current["parentID"] != parent["info"]["id"]
            or parent["info"]["time"]["created"] < marker["info"]["time"]["created"]):
        return None
    texts = [part for part in parent["parts"] if part["type"] == "text" and not part.get("ignored")]
    if not texts or any(part["type"] == "compaction" for part in parent["parts"]):
        return None
    # compaction.ts:264-288 creates this synthetic continuation. The captured
    # interrupted replay instead belongs to the real subsequent human user.
    if any(part.get("synthetic") is True for part in texts):
        if len(texts) != 1 or texts[0]["text"] != (
                "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed."):
            return None
    return {"marker_id": marker["info"]["id"], "summary_id": summary["info"]["id"],
            "replay_id": current["id"], "parent_id": parent["info"]["id"],
            "time_created": marker["info"]["time"]["created"]}


@pytest.mark.parametrize("mutation", [None, "unrelated_older_tie", "without_inversion", "not_latest_parent"])
def test_native_clock_replay_requires_current_summary_ancestry(mutation):
    from scripts.compaction_lanes.opencode_lane import stock_control_flow_ties
    evidence = json.loads((Path(__file__).resolve().parents[1] /
                          "fixtures/opencode_1_2_17_root_clock_replay.json").read_text())["native"]
    messages = evidence["messages"]
    current = next(message["info"] for message in messages
                   if message["info"].get("summary") is True
                   and not message["info"].get("finish") and not message["info"].get("error"))
    summary = next(message for message in reversed(messages)
                   if message["info"].get("summary") is True
                   and message["info"].get("finish") and not message["info"].get("error"))
    marker = next(message for message in messages if message["info"]["id"] == summary["info"]["parentID"])
    if mutation == "unrelated_older_tie":
        later = next(message["info"] for message in messages
                     if message["info"].get("summary") is True and message["info"].get("error"))
        later.pop("error")
        later["finish"] = "stop"
    elif mutation == "without_inversion":
        messages.remove(marker)
        messages.insert(messages.index(summary), marker)
    elif mutation == "not_latest_parent":
        current["parentID"] = marker["info"]["id"]
    assert stock_control_flow_ties(evidence), "Every mutation retains the historical session-wide tie"
    if mutation:
        assert _native_clock_replay(evidence, current) is None
    else:
        assert _native_clock_replay(evidence, current)


@pytest.mark.parametrize("missing", [
    None, "abort_event", "exhaustion_event", "reset_event", "new_marker",
    "abort_record", "original_summaries", "new_summary", "second_reset",
])
def test_native_idle_reentry_requires_exact_source_provenance(missing):
    trace = json.loads((Path(__file__).resolve().parents[2] /
                        "artifacts/compaction_lanes/native-stock-defects/recovery_idle_reentry.json").read_text())
    evidence, events = trace["native"], trace["events"]
    if missing == "abort_event":
        events = [event for event in events if event["type"] != "session.error"]
    elif missing == "exhaustion_event":
        events = [event for event in events if event["properties"].get("title") != "Auto Compact Failed"]
    elif missing == "reset_event":
        reset_index = next(index for index, event in enumerate(events)
                           if event["properties"].get("title") == "Auto Compact Failed")
        events = events[:reset_index] + [event for event in events[reset_index:]
                 if event["properties"].get("message") != "Summarizing session (attempt 1/2)..."]
    elif missing == "abort_record":
        evidence["messages"] = [message for message in evidence["messages"]
                                if message["info"].get("error", {}).get("name") != "MessageAbortedError"]
    elif missing == "original_summaries":
        evidence["messages"] = [message for message in evidence["messages"]
                                if not (message["info"].get("summary") is True and message["info"].get("finish"))]
    elif missing == "new_summary":
        evidence["messages"] = [message for message in evidence["messages"]
                                if not (message["info"].get("summary") is True and not message["info"].get("finish"))]
    elif missing == "new_marker":
        evidence["messages"] = [message for message in evidence["messages"]
                                if message["info"]["id"] != "msg_129a36753001EkzN0KXJmDZCLx"]
    elif missing == "second_reset":
        events.append(next(event for event in reversed(events)
                           if event["properties"].get("message") == "Summarizing session (attempt 1/2)..."))
    if missing in {"abort_record", "original_summaries", "new_summary", "second_reset"}:
        with pytest.raises(AssertionError):
            _native_idle_reentry(evidence, events)
    elif missing:
        assert _native_idle_reentry(evidence, events) is None
    else:
        assert _native_idle_reentry(evidence, events) == {"msg_129a36753001EkzN0KXJmDZCLx"}


@pytest.mark.parametrize("interrupt", [False, True])
def test_plugin_error_summary_reuses_native_transition_and_interrupts_continuation(omo, interrupt, monkeypatch):
    from scripts.compaction_lanes.opencode_lane import native_session_evidence
    native_events = []
    read_line = omo.event_connection.readline
    def observe_line(*args, **kwargs):
        line = read_line(*args, **kwargs)
        if line.startswith(b"data: "):
            event = json.loads(line[6:])
            if event["type"] in {"tui.toast.show", "session.compacted", "session.error"}:
                with omo.condition:
                    native_events.append(event)
                    omo.condition.notify_all()
        return line
    monkeypatch.setattr(omo.event_connection, "readline", observe_line)

    served_extra = 0
    tied_requests = []
    aborted_tie = None
    def serve_native_summaries():
        nonlocal served_extra
        # Consume actual source requests, never retry a failed main response.
        while omo.compaction_pending is not None:
            queued = omo.compaction_pending
            evidence = native_session_evidence(omo.api, omo.session_id)
            reentry = _native_idle_reentry(evidence, tuple(native_events))
            unfinished = [message["info"] for message in evidence["messages"]
                          if message["info"].get("summary") is True
                          and not message["info"].get("finish") and not message["info"].get("error")]
            assert len(unfinished) == 1
            # The original marker can be replayed onto a synthetic continuation
            # or the next real human user (prompt.ts:532). Require current
            # ancestry, not a historical session-wide tie.
            ties = _native_clock_replay(evidence, unfinished[0])
            qualified_reentry = reentry is not None and unfinished[0]["parentID"] in reentry
            assert ties or qualified_reentry, "An unqualified extra source checkpoint:\n" + json.dumps(evidence)
            if ties:
                tied_requests.append(ties)
            summary = "Genuine extra summary for the qualified source checkpoint."
            omo.compact(summary)
            assert queued["summary"] == summary
            assert omo.compaction_pending is not queued
            served_extra += 1

    omo.user([{"type": "text", "text": "Keep this older context."}])
    omo.submit(response(omo, text="Older context recorded."))
    omo.user([{"type": "text", "text": "Recover through the stock plugin."}])
    error = json.dumps({"error": {"message": "too many tokens: 410000 tokens > 400000 maximum",
                                  "type": "invalid_request_error"}}).encode()
    omo.submit_overflow(400, {"content-type": "application/json"}, error)
    queued = omo.compaction_pending
    assert queued is not None
    assert omo.compaction_prompt() is queued["body"]
    omo.compact("Plugin recovery preserved the older context.")
    assert omo.request_done.wait(5)
    assert not omo._root_finished()
    serve_native_summaries()
    messages = omo.api("GET", "/session/" + omo.session_id + "/message")
    assert any(message["info"].get("summary") is True and any(
        part.get("text") == "Plugin recovery preserved the older context." for part in message["parts"])
        for message in messages)
    call = ("call_recovered_bash", "bash", {"command": "printf plugin-tool-done",
                                         "description": "Execute after plugin recovery"})
    omo.submit(response(omo, [call]))
    assert omo.result(call[0])["__mvi_text_output"] == "plugin-tool-done"
    omo.submit(response(omo, text="Recovered tool completed."))
    # Stock keeps the parser armed for its second bounded idle checkpoint.
    omo.wait_pending(allow_finished_request=False)
    assert omo.compaction_pending is not None
    omo.compact("Second source-owned recovery checkpoint.")
    if interrupt:
        assert omo.pending is not None or omo.compaction_pending is not None
        if omo.compaction_pending is not None:
            pending_evidence = native_session_evidence(omo.api, omo.session_id)
            pending_summaries = [message["info"] for message in pending_evidence["messages"]
                                 if message["info"].get("summary") is True
                                 and not message["info"].get("finish") and not message["info"].get("error")]
            assert len(pending_summaries) == 1
            aborted_tie = _native_clock_replay(pending_evidence, pending_summaries[0])
            assert aborted_tie is not None, json.dumps(pending_evidence)
    else:
        serve_native_summaries()
        omo.submit(response(omo, text="Both source recovery checkpoints completed."))
        assert omo._root_finished()
    before_abort = native_session_evidence(omo.api, omo.session_id)
    aborted_summary_ids = {message["info"]["id"] for message in before_abort["messages"]
                           if omo.compaction_pending is not None
                           and message["info"].get("summary") is True
                           and not message["info"].get("finish") and not message["info"].get("error")}
    omo.finish_run()
    assert omo.pending is None and omo.compaction_pending is None
    omo.user([{"type": "text", "text": "Follow up after the interrupted recovery."}])
    serve_native_summaries()
    omo.submit(response(omo, text="Same stock session remains usable."))
    # Abort-idle handlers can enqueue a source epoch after the human's main
    # response. Serve every actual unfinished summary, not a timing retry.
    while True:
        evidence = native_session_evidence(omo.api, omo.session_id)
        unfinished = [message for message in evidence["messages"]
                      if message["info"].get("summary") is True
                      and not message["info"].get("finish") and not message["info"].get("error")]
        if not unfinished and omo.pending is None and omo.compaction_pending is None:
            break
        omo.wait_pending(allow_finished_request=False)
        serve_native_summaries()
        if omo.pending is not None:
            omo.submit(response(omo, text="Source recovery continuation completed."))
    evidence = native_session_evidence(omo.api, omo.session_id)
    reentry = _native_idle_reentry(evidence, tuple(native_events))
    summaries = [message["info"] for message in evidence["messages"] if message["info"].get("summary") is True]
    completed_summaries = [info for info in summaries if info.get("finish") and not info.get("error")]
    aborted_summaries = [info for info in summaries if info.get("error", {}).get("name") == "MessageAbortedError"]
    assert {info["id"] for info in aborted_summaries} == aborted_summary_ids
    assert len(completed_summaries) == 2 + served_extra
    records = [record for record in omo.records if record["kind"] == "compaction"]
    assert len(records) == len(completed_summaries) + len(aborted_summaries)
    if not tied_requests and aborted_tie is None and reentry is None:
        assert not tied_requests and served_extra == 0 and len(records) == 2
    else:
        assert served_extra > 0 or aborted_summaries
        assert len(records) > 2
    assert omo._root_finished() and omo.pending is None and omo.compaction_pending is None


@pytest.mark.parametrize("name,args,endpoint", [
    ("websearch_web_search_exa", {
        "query": 'site:opencode.ai/docs/ "opencode run"',
        "numResults": 1, "type": "fast", "livecrawl": "fallback",
        "contextMaxCharacters": 1000,
    }, "https://mcp.exa.ai/mcp?tools=web_search_exa"),
    ("grep_app_searchGitHub", {
        "query": "BREADBOARD_STOCK_MCP_CONFORMANCE_NEVER_MATCH_20261009",
        "repo": "anomalyco/opencode", "matchCase": True,
    }, "https://mcp.grep.app"),
])
def test_native_network_mcp_call_matches_direct_stock(omo, binary, tmp_path, name, args, endpoint):
    # HTTP errors are online responses, not grounds to hide a failed differential.
    try:
        with urllib.request.urlopen(endpoint, timeout=20) as connection:
            connection.read(1)
    except urllib.error.HTTPError:
        pass
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, (socket.gaierror, TimeoutError, ConnectionError)) or (
            isinstance(reason, OSError) and reason.errno in {50, 51, 60, 61, 64, 65, 101, 110, 111, 113}
        ):
            pytest.skip(f"Offline stock MCP endpoint {endpoint}: {reason}")
        raise
    held = omo.user([{"type": "text", "text": "Call the actual stock remote MCP tool."}])
    assert name in {item["name"] for item in held["tools"]}, omo.api("GET", "/mcp")
    call = ("call_live_mcp", name, args)
    omo.submit(response(omo, [call]))
    actual = omo.result(call[0])["__mvi_text_output"]
    assert actual, "The real endpoint must yield a stock MCP result"
    omo.submit(response(omo))
    expected = direct_omo(binary, omo.workspace, tmp_path / "oracle", [call])
    assert call[0] in expected, "Direct stock must execute the same advertised MCP call"
    assert actual == expected[call[0]]


def test_native_interactive_bash_full_tmux_lifecycle_matches_direct_stock(omo, binary, tmp_path):
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is absent: real interactive_bash lifecycle requires tmux")
    token = uuid.uuid4().hex
    target = "bb-stock-" + token
    channel = "bb-ready-" + token
    socket_path = str(Path(omo.workspace) / "tmux.sock")
    prefix = "-S " + shlex.quote(socket_path)
    shell = "env PS1= bash --noprofile --norc"
    sent = "printf BB_TMUX_CAPTURE_DONE; tmux " + prefix + " wait-for -S " + channel
    calls = [
        ("call_tmux_create", "interactive_bash", {
            "tmux_command": f"{prefix} new-session -d -s {target} -x 80 -y 24 {shlex.quote(shell)}"}),
        ("call_tmux_send", "interactive_bash", {
            "tmux_command": f"{prefix} send-keys -t {target} {shlex.quote(sent)} Enter"}),
        # Stock explicitly blocks this subcommand and directs capture through
        # its bash tool (interactive-bash/tools.ts:66-90). Preserve that result.
        ("call_tmux_guidance", "interactive_bash", {"tmux_command": f"capture-pane -p -t {target}"}),
        ("call_tmux_capture", "bash", {
            "command": f"tmux {prefix} wait-for {channel}; tmux {prefix} capture-pane -p -t {target}",
            "description": "Capture the actual stock tmux pane after its output signal"}),
        ("call_tmux_kill", "interactive_bash", {"tmux_command": f"{prefix} kill-session -t {target}"}),
        ("call_tmux_absent", "bash", {
            "command": f'tmux {prefix} has-session -t {target} 2>/dev/null; printf "tmux_session_status=%s\\n" "$?"',
            "description": "Verify the test-owned tmux session was killed"}),
    ]
    try:
        omo.user([{"type": "text", "text": "Exercise the entire stock tmux lifecycle."}])
        actual = {}
        for call in calls:
            omo.submit(response(omo, [call]))
            actual[call[0]] = omo.result(call[0])["__mvi_text_output"]
        assert actual["call_tmux_create"] == actual["call_tmux_send"] == actual["call_tmux_kill"] == "(no output)"
        assert "blocked in interactive_bash" in actual["call_tmux_guidance"]
        assert "BB_TMUX_CAPTURE_DONE" in actual["call_tmux_capture"].splitlines()
        assert actual["call_tmux_absent"] == "tmux_session_status=1\n"
        omo.submit(response(omo))
        expected = direct_omo(binary, omo.workspace, tmp_path / "oracle", calls, sequential=True)
        assert actual == {call[0]: expected[call[0]] for call in calls}
    finally:
        # Only this test's explicit private socket is addressed, never the
        # user's default tmux server. Cleanup also covers assertion failures.
        subprocess.run([tmux, "-S", socket_path, "kill-server"], capture_output=True, timeout=10)
