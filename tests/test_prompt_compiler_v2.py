from agentic_coder_prototype.compilation.system_prompt_compiler import get_compiler
from agentic_coder_prototype.core.core import ToolDefinition, ToolParameter


def _mk_tools():
    return [
        ToolDefinition(
            type_id="python",
            name="read_file",
            description="Read file",
            parameters=[ToolParameter(name="path", type="string", description="", default=None)],
        ),
        ToolDefinition(
            type_id="diff",
            name="apply_unified_patch",
            description="Apply patch",
            parameters=[ToolParameter(name="patch", type="string", description="", default=None)],
        ),
    ]


def test_compile_v2_prompts_minimal():
    comp = get_compiler()
    cfg = {
        "version": 2,
        "providers": {"default_model": "openrouter/openai/gpt-5-nano", "models": [{"id": "openrouter/openai/gpt-5-nano", "adapter": "openai"}]},
        "prompts": {
            "packs": {
                "base": {
                    # leave empty to exercise fallback
                }
            },
            "injection": {"system_order": ["@pack(base).system"], "per_turn_order": ["mode_specific"]},
        },
        "modes": [{"name": "build"}],
        "loop": {"sequence": [{"mode": "build"}]},
    }
    res = comp.compile_v2_prompts(cfg, mode_name="build", tools=_mk_tools(), dialects=["unified_diff"]) 
    assert isinstance(res["system"], str)
    assert isinstance(res["per_turn"], str)
    assert isinstance(res["cache_key"], str)



