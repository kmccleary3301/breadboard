from agentic_coder_prototype.agent_llm_openai import compute_tool_prompt_mode


def test_compute_tool_prompt_mode_native_enabled():
    cfg = {"provider_tools": {"suppress_prompts": False}}
    out = compute_tool_prompt_mode("system_once", True, cfg)
    assert out == "per_turn_append"


def test_compute_tool_prompt_mode_suppressed():
    cfg = {"provider_tools": {"suppress_prompts": True}}
    out = compute_tool_prompt_mode("system_once", True, cfg)
    assert out == "none"


def test_compute_tool_prompt_mode_no_native():
    cfg = {}
    out = compute_tool_prompt_mode("system_once", False, cfg)
    assert out == "system_once"


