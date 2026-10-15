"""Stock permission updatedInput changes execution, not the staged assistant turn."""
import json

import pytest

from breadboard_engine.execution.claude_tools import ClaudeToolSession


@pytest.mark.parametrize("name", ["Agent", "Skill"])
def test_auxiliary_classification_uses_operator_updated_input(tmp_path, name):
    settings = tmp_path / ".claude"
    settings.mkdir()
    (settings / "settings.json").write_text(json.dumps({"permissions": {"ask": [name]}}))
    if name == "Skill":
        commands = settings / "commands"
        commands.mkdir()
        for command in ("original", "rewritten"):
            (commands / f"{command}.md").write_text(
                f"---\ndescription: {command}\ncontext: fork\nallowed-tools: Bash\n---\n{command}-prompt\n")
        arguments = {"skill": "original"}
        updated = {"skill": "rewritten"}
    else:
        arguments = {"description": "permission rewrite", "subagent_type": "general-purpose", "prompt": "original-prompt"}
        updated = {**arguments, "prompt": "rewritten-prompt"}
    permissions = []
    auxiliaries = []

    def permission(request):
        permissions.append(request)
        return {"behavior": "allow", "updatedInput": updated}

    def auxiliary(body, headers, path):
        auxiliaries.append(body)
        return 200, {"Content-Type": "text/event-stream"}, ClaudeToolSession.encode_response(
            [{"type": "text", "text": "rewritten-final"}], body)

    stock = ClaudeToolSession(str(tmp_path), "claude-haiku-4-5-20251001", timeout=30,
                             permission=permission, auxiliary=auxiliary)
    try:
        result = stock.execute(name, arguments, call_id="toolu_permission_rewrite")
        assert "rewritten-final" in result["__mvi_text_output"]
        assert permissions and permissions[0]["tool_use_id"] == "toolu_permission_rewrite"
        assert auxiliaries and "rewritten-prompt" in json.dumps(auxiliaries[0]["messages"])
        assistant = next(message for message in reversed(stock.operation["request"]["messages"])
                         if message["role"] == "assistant")
        native = next(block for block in assistant["content"] if block["type"] == "tool_use")
        assert native["input"] == arguments
    finally:
        stock.close()
