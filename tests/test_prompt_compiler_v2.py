from breadboard_engine.compilation.system_prompt_compiler import get_compiler
import hashlib

from breadboard_engine.conductor.modes import _finalize_model_surface
from breadboard_engine.core.core import ToolDefinition, ToolParameter


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



def test_compile_v2_prompts_records_ordered_model_surface_contributions():
    comp = get_compiler()
    cfg = {
        "prompts": {
            "packs": {"base": {"first": "first", "second": "second"}},
            "injection": {
                "system_order": ["@pack(base).first", "@pack(base).second"],
                "per_turn_order": ["mode_specific"],
            },
        },
        "modes": [{"name": "build", "prompt": "mode"}],
    }

    result = comp.compile_v2_prompts(
        cfg, mode_name="build", tools=_mk_tools(), dialects=["unified_diff"]
    )

    assert [
        (item["order"], item["source_ref"])
        for item in result["model_surface"]["prompt_sections"]["system"]
    ] == [(0, "@pack(base).first"), (1, "@pack(base).second")]
    assert result["model_surface"]["prompt_sections"]["per_turn"][0][
        "source_ref"
    ] == "mode_specific"
    assert [
        (item["order"], item["source_ref"])
        for item in result["model_surface"]["tools"]
    ] == [(0, "tool_registry[0]"), (1, "tool_registry[1]")]


def test_final_model_surface_hashes_transformed_provider_request() -> None:
    final_system = "compiled system\n\nruntime environment"
    messages = [
        {"role": "system", "content": final_system},
        {"role": "user", "content": "request"},
    ]
    request_body = {
        "model": "provider/model-a",
        "messages": messages,
        "tools": [],
        "stream": False,
    }
    surface = _finalize_model_surface(
        {
            "prompt_sections": {"system": [], "per_turn": []},
            "tools": [],
        },
        messages,
        [],
        "",
        request_body,
    )

    assert surface is not None
    assert surface["prompt_sections"]["per_turn"] == []
    assert surface["prompt_sections"]["system"][0]["content_sha256"] == (
        hashlib.sha256(final_system.encode("utf-8")).hexdigest()
    )
    assert surface["provider_request"]["messages_sha256"]
    assert surface["provider_request"]["request_sha256"]
    changed_model_surface = _finalize_model_surface(
        {"prompt_sections": {"system": [], "per_turn": []}, "tools": []},
        messages,
        [],
        "",
        {**request_body, "model": "provider/model-b"},
    )
    assert changed_model_surface is not None
    assert (
        surface["provider_request"]["request_sha256"]
        != changed_model_surface["provider_request"]["request_sha256"]
    )

