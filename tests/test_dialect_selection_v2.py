import pytest

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


def _make_conductor_instance(config):
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst._native_preference_hint = None
    inst._provider_tools_effective = None
    return inst


def _mk_config(by_model=None, by_tool_kind=None):
    return {
        "version": 2,
        "tools": {
            "dialects": {
                "selection": {
                    "by_model": by_model or {},
                    "by_tool_kind": by_tool_kind or {},
                }
            }
        }
    }


def _names_in_order(names):
    return names, {n: i for i, n in enumerate(names)}


def test_by_model_pattern_priority():
    cfg = _mk_config(by_model={
        "openrouter/openai/*": ["unified_diff", "opencode_patch"],
        "anthropic/*": ["aider_diff", "unified_diff"],
    })

    conductor = _make_conductor_instance(cfg)
    avail = ["aider_diff", "unified_diff", "opencode_patch", "bash_block"]
    ordered = conductor._apply_v2_dialect_selection(avail, "openrouter/openai/gpt-5-nano", [])
    idx = {n: i for i, n in enumerate(ordered)}
    assert idx["unified_diff"] < idx["opencode_patch"]


def test_by_tool_kind_diff_precedes_when_present():
    cfg = _mk_config(by_tool_kind={
        "diff": ["aider_diff", "unified_diff", "opencode_patch"],
        "bash": ["bash_block"],
    })
    conductor = _make_conductor_instance(cfg)
    avail = ["opencode_patch", "bash_block", "unified_diff", "aider_diff"]
    ordered = conductor._apply_v2_dialect_selection(avail, "openrouter/openai/gpt-5-nano", [])
    # Aider should be the first among diff formats
    assert ordered[0] in ("aider_diff", "unified_diff")
    assert "bash_block" in ordered

def test_preference_sets_native_hint_and_yaml_priority():
    cfg = {
        "version": 2,
        "tools": {
            "dialects": {
                "preference": {
                    "default": ["opencode_patch", "unified_diff"],
                    "by_model": {
                        "openrouter/openai/*": { "native": True, "order": ["provider_native", "yaml_command", "opencode_patch"] }
                    },
                    "by_tool_kind": {
                        "bash": ["provider_native", "bash_block"]
                    },
                }
            }
        }
    }
    conductor = _make_conductor_instance(cfg)
    avail = ["opencode_patch", "yaml_command", "unified_diff", "bash_block"]
    ordered = conductor._apply_v2_dialect_selection(avail, "openrouter/openai/gpt-5-nano", [])
    assert ordered[0] == "yaml_command"
    native_hint = conductor._get_native_preference_hint()
    assert native_hint is True


def test_preference_can_disable_native_hint():
    cfg = {
        "version": 2,
        "tools": {
            "dialects": {
                "preference": {
                    "default": ["unified_diff", "opencode_patch"],
                    "by_model": {
                        "openrouter/openai/*": { "native": False, "order": ["unified_diff", "opencode_patch"] }
                    },
                }
            }
        }
    }
    conductor = _make_conductor_instance(cfg)
    avail = ["opencode_patch", "unified_diff", "bash_block"]
    ordered = conductor._apply_v2_dialect_selection(avail, "openrouter/openai/gpt-5-nano", [])
    assert ordered[0] in ("unified_diff", "opencode_patch")
    native_hint = conductor._get_native_preference_hint()
    assert native_hint is False