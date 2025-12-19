from __future__ import annotations

import json

from agentic_coder_prototype.provider_adapters import provider_adapter_manager


def test_openrouter_tool_result_includes_call_id_alias() -> None:
    adapter = provider_adapter_manager.get_adapter("openrouter")
    msg = adapter.create_tool_result_message("call_123", "demo_tool", {"ok": True})

    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_123"
    assert msg["call_id"] == "call_123"
    assert msg["name"] == "demo_tool"
    assert json.loads(msg["content"]) == {"ok": True}

