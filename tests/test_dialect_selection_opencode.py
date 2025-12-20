from agentic_coder_prototype.execution.dialect_manager import DialectManager


def test_opencode_preferred_for_gpt5_nano():
    config = {
        "dialect_profiles": {
            "openrouter/openai/gpt-5-nano": {
                "preferred_dialects": {},
                "exclusions": [],
                "mode": "mixed"
            }
        }
    }
    dm = DialectManager(config)
    requested = ["opencode_patch", "unified_diff", "aider_diff"]
    filtered = dm.get_dialects_for_model("openrouter/openai/gpt-5-nano", requested)
    # In mixed mode with no exclusions, it should pass through unchanged
    assert filtered == requested



