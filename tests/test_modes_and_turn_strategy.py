import os
import ray
import pytest

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


@pytest.mark.skipif('OPENAI_API_KEY' not in os.environ, reason="requires provider key")
def test_mode_tool_gating_and_turn_strategy(tmp_path):
    if not ray.is_initialized():
        ray.init()

    cfg = {
        "version": 2,
        "providers": {"default_model": "openrouter/openai/gpt-5-nano", "models": [{"id": "openrouter/openai/gpt-5-nano", "adapter": "openai"}]},
        "tools": {
            "defs_dir": "implementations/tools/defs",
            "enabled": {"read_file": True, "run_shell": True, "apply_unified_patch": True},
        },
        "modes": [
            {"name": "plan", "tools_enabled": ["read_file"], "tools_disabled": ["run_shell", "apply_unified_patch"]},
            {"name": "build", "tools_enabled": ["*"], "tools_disabled": []},
        ],
        "loop": {
            "sequence": [
                {"if": "features.plan", "then": {"mode": "plan"}},
                {"mode": "build"}
            ],
            "turn_strategy": {"flow": "assistant_continuation", "relay": "tool_role"}
        },
        "features": {"plan": True},
    }

    sb_dir = str(tmp_path / "ws")
    actor = OpenAIConductor.options(name=f"test-actor-{os.getpid()}").remote(workspace=sb_dir, config=cfg)
    # Exercise a minimal run to ensure no exceptions and v2 wiring executes
    out = ray.get(actor.run_agentic_loop.remote("", "hello", cfg["providers"]["default_model"], max_steps=1))
    assert isinstance(out, dict)
    ray.kill(actor)



