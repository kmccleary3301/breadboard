import json

from agentic_coder_prototype.provider_ir import (
    convert_legacy_messages,
    IRPart,
    IRToolCall,
    IRToolResult,
    IRConversation,
    IRDeltaEvent,
    IRFinish,
)
from agentic_coder_prototype.state.session_state import SessionState


def test_convert_legacy_messages_basic():
    legacy = [
        {
            "role": "assistant",
            "content": "Hello world",
            "tool_calls": [
                {"id": "call-1", "name": "list_dir", "arguments": {"path": "."}},
            ],
            "tool_results": [
                {"tool_call_id": "call-1", "ok": True, "result": {"path": ".", "items": []}},
            ],
        }
    ]

    converted = convert_legacy_messages(legacy)
    assert len(converted) == 1
    msg = converted[0]
    assert msg.role == "assistant"
    assert msg.parts == [IRPart.text_part("Hello world")]
    assert msg.tool_calls == [IRToolCall(id="call-1", name="list_dir", args={"path": "."})]
    assert msg.tool_results == [
        IRToolResult(tool_call_id="call-1", ok=True, result={"path": ".", "items": []}, error=None)
    ]


def test_session_state_conversation_ir(tmp_path):
    session = SessionState(workspace=str(tmp_path), image="python:latest")
    session.add_message({"role": "system", "content": "Setup"})
    session.add_message({"role": "assistant", "content": "<TOOL_CALL> list_dir(path='.', depth=1) </TOOL_CALL>"})
    session.add_ir_event(IRDeltaEvent(cursor="turn_1:text:0", type="text", payload={"content": "Setup"}))
    session.set_ir_finish(IRFinish(reason="stop", usage={"prompt_tokens": 1, "completion_tokens": 1}))

    ir = session.build_conversation_ir(conversation_id="test")
    assert isinstance(ir, IRConversation)
    assert ir.ir_version == "1"
    assert len(ir.messages) == 2
    assert ir.events[0].cursor == "turn_1:text:0"
    assert ir.finish.reason == "stop"


def test_session_snapshot_includes_conversation_ir(tmp_path):
    session = SessionState(workspace=str(tmp_path), image="python:latest")
    session.add_message({"role": "system", "content": "Setup"})
    session.add_ir_event(IRDeltaEvent(cursor="turn_1:text:0", type="text", payload={"content": "Setup"}))
    session.set_ir_finish(IRFinish(reason="stop", usage={"prompt_tokens": 1, "completion_tokens": 0}))

    snapshot_path = tmp_path / "snapshot.json"
    session.write_snapshot(str(snapshot_path), model="mock", diff={"ok": True, "data": {"diff": ""}})
    data = json.loads(snapshot_path.read_text())
    assert data["ir_version"] == "1"
    conv = data.get("conversation_ir")
    assert conv is not None
    assert conv["ir_version"] == "1"
    assert isinstance(conv.get("messages"), list)


def test_ir_finish_agent_summary_round_trip():
    session = SessionState(workspace="/tmp", image="python:latest")
    session.add_message({"role": "assistant", "content": "Done"})
    session.completion_summary = {"completed": True, "reason": "mock"}
    finish = IRFinish(reason="stop", usage={}, agent_summary=session.completion_summary)
    session.set_ir_finish(finish)

    conv = session.build_conversation_ir(conversation_id="test")
    assert conv.finish is not None
    assert conv.finish.agent_summary == session.completion_summary
