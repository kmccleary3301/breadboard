from __future__ import annotations

from agentic_coder_prototype.agent import AgenticCoder
from agentic_coder_prototype.compilation.v2_loader import load_agent_config


CONFIG_PATH = "agent_configs/misc/breadboard_tui_smoke_chat.yaml"


def test_breadboard_tui_smoke_chat_config_is_chat_shaped() -> None:
    cfg = load_agent_config(CONFIG_PATH)

    assert cfg["providers"]["default_model"] == "smoke/dev"
    assert cfg["prompts"]["tool_prompt_mode"] == "none"
    assert cfg["provider_tools"]["use_native"] is False
    assert cfg["tools"]["mark_task_complete"] is False
    assert cfg["loop"]["sequence"] == [{"mode": "chat"}]
    assert cfg["loop"]["guardrails"]["zero_tool_watchdog"]["warn_after_turns"] == 0
    assert cfg["loop"]["guardrails"]["zero_tool_watchdog"]["abort_after_turns"] == 0
    assert cfg["completion"]["allow_zero_tool_completion"] is True


def test_breadboard_tui_smoke_chat_high_level_agent_emits_assistant_content(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "BREADBOARD_SMOKE_CHAT_CONTENT",
        "TRANSCRIPTOKDONE\n\nTASK COMPLETE\n\n>>>>>> END RESPONSE",
    )
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    monkeypatch.setenv("PRESERVE_SEEDED_WORKSPACE", "1")

    agent = AgenticCoder(
        CONFIG_PATH,
        workspace_dir=str(tmp_path / "workspace"),
        force_local_mode=True,
    )
    result = agent.run_task(
        "Reply with the single word formed by joining TRANSCRIPT, OK, and DONE with no separators.",
        max_iterations=3,
        stream=False,
    )

    assert result["completed"] is True
    assert result["completion_reason"] != "implementation_missing_write_receipt_loop"

    messages = result.get("messages") or []
    user_messages = [str(entry.get("content") or "") for entry in messages if entry.get("role") == "user"]
    assistant_messages = [str(entry.get("content") or "") for entry in messages if entry.get("role") == "assistant"]

    assert any("TRANSCRIPTOKDONE" in content for content in assistant_messages)
    assert all("general-purpose agentic software engineer" not in content for content in user_messages)
    assert all("VALIDATION_ERROR" not in content for content in user_messages)
