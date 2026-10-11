from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from breadboard_engine.execution.opencode_tools import DEFAULT_BINARY, OpenCodeToolSession, StockExecutionError, responses_payload
from scripts.compaction_lanes.opencode_lane import ResponsesProvider, native_session_evidence


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
