from contextlib import nullcontext
import os
from types import SimpleNamespace
import ray
import pytest

from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.conductor.modes import add_enhanced_message_fields


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

def test_compiled_tool_prompt_preserves_first_class_input_media() -> None:
    media = {
        "type": "media",
        "kind": "image",
        "uri": "attachment://sha256:" + "a" * 64,
        "mime": "image/png",
    }
    state = SimpleNamespace(
        messages=[
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    dict(media),
                ],
            },
        ],
        provider_messages=[
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    dict(media),
                ],
            },
        ],
        context_mutation=nullcontext,
    )

    add_enhanced_message_fields(
        SimpleNamespace(),
        "system_compiled_and_persistent_per_turn",
        [],
        [],
        state,
        False,
        "",
        "inspect",
    )

    assert state.messages[1]["content"][1] == media
    assert state.provider_messages[1]["content"][1] == media
    assert state.provider_messages[1]["content"][0]["text"].startswith(
        "inspect"
    )



