"""OpenCode lane opencode test support behavioral regressions."""
from __future__ import annotations
from breadboard_engine.compaction_lanes import opencode_stock as _lane_opencode_stock
import json
from breadboard_engine.compaction_lanes.opencode_scenarios import TEST_WINDOW

def record(body, path="/v1/responses"):
    raw = json.dumps(body).encode()
    return {"json": body, "path": path, "raw_body": raw.decode(), "raw_body_bytes": raw.hex()}

def summary_record(messages):
    return record({"input": [{"role": "developer", "content": "You are a helpful AI assistant tasked with summarizing conversations."}, *messages]})

def _native_prune_event_fence(monkeypatch, tmp_path, events, *, already_compacted=False):
    import io
    from types import SimpleNamespace
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    part = {"id": "part", "messageID": "assistant", "sessionID": "root",
            "type": "tool", "callID": "call_seed", "tool": "bash",
            "state": {"status": "completed", "output": "Original tool output",
                      "time": {"start": 1, "end": 2}}}
    if already_compacted:
        part["state"]["time"]["compacted"] = 3
    def api(method, path):
        assert (method, path) == ("GET", "/session/root/message")
        return [{"info": {"id": "assistant"}, "parts": [part]}]
    response = io.BytesIO("".join("data: " + json.dumps(event) + "\n\n" for event in events).encode())
    response.status = 200
    def request(method, path, *, headers):
        assert (method, path) == ("GET", "/event")
        assert headers["Authorization"].startswith("Basic ")
        assert headers["x-opencode-directory"] == str(tmp_path.resolve())
    connection = SimpleNamespace(
        sock=None, request=request, getresponse=lambda: response, close=lambda: None,
    )
    monkeypatch.setattr(lane.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    return _lane_opencode_stock.NativePruneFence(
        api, "http://127.0.0.1:9123", tmp_path, "root", ["call_seed"], "fixture-password",
    )

def _pruned_part_event(**changes):
    part = {"id": "part", "messageID": "assistant", "sessionID": "root",
            "type": "tool", "callID": "call_seed", "tool": "bash",
            "state": {"status": "completed", "output": "Original tool output",
                      "time": {"start": 1, "end": 2, "compacted": 3}}}
    part.update(changes)
    return {"type": "message.part.updated", "properties": {"part": part}}

def _runtime_id_api_fixture(background, child):
    root = "ses_012345abcdef" + "A" * 14
    output = (
        f"Background task launched.\n\nBackground Task ID: {background}\n"
        "Description: Delegated inspection\nAgent: explore\nStatus: running\n\n"
        f'System notifies on completion. Use `background_output` with task_id="{background}" to check.'
        f"\n\n<task_metadata>\nsession_id: {child}\ntask_id: {child}\n"
        f"background_task_id: {background}\n</task_metadata>"
    )
    return {"session": {"id": root}, "children": [{"session": {"id": child, "parentID": root}, "messages": []}],
            "messages": [{"info": {"id": "native-launch", "role": "assistant", "sessionID": root},
                          "parts": [{"type": "tool", "tool": "task", "callID": "call_delegated",
                                     "state": {"input": {"run_in_background": True, "description": "Delegated inspection"},
                                               "metadata": {"sessionId": child}, "output": output}}]}]}

def _runtime_id_guard_fixture():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    stock = _runtime_id_api_fixture("bg_012345ab", "ses_" + "a" * 12 + "B" * 14)
    bb = _runtime_id_api_fixture("bg_abcdef01", "ses_" + "b" * 12 + "C" * 14)
    return RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]]), stock, bb

def _pending_recovery_setup():
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.state.session_state import SessionState
    from tests.compaction.test_opencode_integration import FakeRuntime
    state = SessionState("ws", "image", {})
    state.add_message({"role": "user", "content": "Finish the inspection."})
    state.set_provider_metadata("usage", {"total_tokens": 150})
    controller = CompactionController({"compaction": {
        "enabled": True, "preset": "oh-my-opencode@3.10.0", "contextWindow": TEST_WINDOW,
    }})
    return controller, state, FakeRuntime()

def _finish_pending_recovery(controller, state, runtime):
    return controller.finish_assistant_step(
        state, conductor=None, runtime=runtime, client=object(),
        model="gpt-test", turn_index=1,
    )

def _arm_native_overflow(controller, state, runtime):
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
    error = ProviderRuntimeError("provider operation failed", details={
        "code": "context_length_exceeded", "overflow_tokens": 74000, "overflow_limit": 64000,
    })
    error.__cause__ = RuntimeError("prompt is too long: 74000 tokens > 64000 maximum")
    assert controller.recover_from_overflow(
        error, state, turn_index=1, conductor=None, runtime=runtime,
        client=object(), model="gpt-test",
    )

def _time_tie_fixture():
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    def native(identifier, role, created, text, parent=None):
        return {"info": {"id": identifier, "role": role, "time": {"created": created},
                         "parentID": parent}, "parts": [{"type": "text", "text": text}]}
    stock = {"messages": [native("sa", "assistant", 10, "Done", "su"),
                           native("su", "user", 10, "Finish")]}
    bb = {"messages": [native("bu", "user", 20, "Finish"),
                       native("ba", "assistant", 21, "Done", "bu")]}
    stock["session"] = {"id": "root-session"}
    bb["session"] = {"id": "root-session"}
    header = {"role": "developer", "content": "Unchanged system"}
    stock_body = {"input": [header, *map(CreatedTimeTieOrder.wire, stock["messages"])],
                  "store": False}
    bb_body = {"input": [header, *map(CreatedTimeTieOrder.wire, bb["messages"])],
               "store": False}
    stock_body["prompt_cache_key"] = bb_body["prompt_cache_key"] = "root-session"
    return stock, bb, stock_body, bb_body

def _tool_time_tie_fixture(tool_count=1):
    import copy
    stock, bb, left, right = _time_tie_fixture()
    tools, calls, outputs = [], [], []
    for index in range(tool_count):
        call_id = f"call-{index}"
        arguments = {"command": f"printf {index}"}
        output = str(index)
        tools.append({"type": "tool", "callID": call_id, "tool": "bash",
                      "state": {"status": "completed", "input": arguments, "output": output}})
        calls.append({"type": "function_call", "call_id": call_id, "name": "bash",
                      "arguments": json.dumps(arguments, separators=(",", ":"))})
        outputs.append({"type": "function_call_output", "call_id": call_id, "output": output})
    stock["messages"][0]["parts"] = tools
    bb["messages"][1]["parts"] = copy.deepcopy(tools)
    user = left["input"][2]
    left["input"] = [left["input"][0], *calls, *outputs, user]
    right["input"] = copy.deepcopy([right["input"][0], user, *calls, *outputs])
    return stock, bb, left, right

def _add_completed_notification(evidence, background):
    # Pinned manager.ts:1255-1287 plus internal-initiator-marker.ts:1-10.
    root = evidence["session"]["id"]
    child = next(child for child in evidence["children"]
                 if any(background in part.get("state", {}).get("output", "")
                        and part.get("state", {}).get("metadata", {}).get("sessionId") == child["session"]["id"]
                        for message in evidence["messages"] for part in message["parts"]))
    child["messages"] = [{"info": {"id": "completed-" + background, "role": "assistant",
                                   "sessionID": child["session"]["id"], "finish": "stop",
                                   "time": {"created": 10, "completed": 11}}, "parts": []}]
    text = ("<system-reminder>\n[ALL BACKGROUND TASKS COMPLETE]\n\n**Completed:**\n"
            f"- `{background}`: Delegated inspection\n\n"
            'Use `background_output(task_id="<id>")` to retrieve each result.\n'
            "</system-reminder>\n<!-- OMO_INTERNAL_INITIATOR -->")
    evidence["messages"].append({
        "info": {"id": "notification-" + background, "role": "user", "sessionID": root},
        "parts": [{"type": "text", "text": text, "sessionID": root,
                   "messageID": "notification-" + background}]})
    return {"prompt_cache_key": root,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}

def _completed_notification_guard_fixture():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    stock = _runtime_id_api_fixture("bg_012345ab", "ses_" + "a" * 12 + "B" * 14)
    bb = _runtime_id_api_fixture("bg_abcdef01", "ses_" + "b" * 12 + "C" * 14)
    bodies = {"stock": _add_completed_notification(stock, "bg_012345ab"),
              "bb": _add_completed_notification(bb, "bg_abcdef01")}
    return RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]]), stock, bb, bodies
