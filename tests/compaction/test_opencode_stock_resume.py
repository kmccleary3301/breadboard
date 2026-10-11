from breadboard_engine.execution.opencode_tools import OpenCodeToolSession
from tests.compaction.test_opencode_stock_tools import MODEL, binary, response, isolated_environment


def test_reopened_sidecar_retains_native_session_and_tool_history(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = workspace / ".breadboard/sessions/bb-resume/stock-opencode"
    options = dict(binary=binary, timeout=60, data_root=data,
                   environment=isolated_environment(tmp_path / "environment"),
                   stock_config={"agent": {"title": {"disable": True}}})
    with_first = OpenCodeToolSession(str(workspace), MODEL, **options)
    try:
        with_first.user([{"type": "text", "text": "Remember native history."}])
        with_first.submit(response(with_first, [("call_remember", "bash", {
            "command": "printf retained-result", "description": "Retain a tool result",
        })]))
        assert with_first.result("call_remember")["__mvi_text_output"] == "retained-result"
        with_first.submit(response(with_first))
        identity = with_first.session_id
    finally:
        with_first.close()
    reopened = OpenCodeToolSession(str(workspace), MODEL, **options)
    try:
        assert reopened.session_id == identity
        history = reopened.api("GET", "/session/" + identity + "/message")
        assert any(part.get("state", {}).get("output") == "retained-result"
                   for message in history for part in message["parts"])
        held = reopened.user([{"type": "text", "text": "Continue the same session."}])
        assert any(item.get("call_id") == "call_remember" and item.get("output") == "retained-result"
                   for item in held["input"])
        reopened.submit(response(reopened))
    finally:
        reopened.close()
