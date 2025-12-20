import os
import ray
import pytest

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


@pytest.mark.skipif('OPENAI_API_KEY' not in os.environ, reason="requires provider key")
def test_concurrency_group_shapes(tmp_path):
    if not ray.is_initialized():
        ray.init()

    cfg = {
        "version": 2,
        "providers": {"default_model": "openrouter/openai/gpt-5-nano", "models": [{"id": "openrouter/openai/gpt-5-nano", "adapter": "openai"}]},
        "tools": {
            "defs_dir": "implementations/tools/defs",
            "enabled": {"read_file": True, "list_dir": True, "apply_unified_patch": True},
        },
        "modes": [{"name": "build", "tools_enabled": ["*"], "tools_disabled": []}],
        "loop": {"sequence": [{"mode": "build"}]},
        "concurrency": {
            "groups": [
                {"name": "fs_reads", "match_tools": ["read_file", "list_dir"], "max_parallel": 2},
                {"name": "edits", "match_tools": ["apply_unified_patch"], "max_parallel": 1, "barrier_after": "apply_unified_patch"},
            ],
            "nonblocking_tools": ["read_file", "list_dir"],
            "at_most_one_of": ["run_shell"],
        },
    }

    sb_dir = str(tmp_path / "ws2")
    actor = OpenAIConductor.options(name=f"test-actor-conc-{os.getpid()}").remote(workspace=sb_dir, config=cfg)
    out = ray.get(actor.run_agentic_loop.remote("", "inspect", cfg["providers"]["default_model"], max_steps=1))
    assert isinstance(out, dict)
    ray.kill(actor)



