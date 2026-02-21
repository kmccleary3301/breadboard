from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.compilation.v2_loader import load_agent_config


def _load(rel_path: str) -> dict:
    repo_root = Path(__file__).resolve().parents[1]
    return load_agent_config(str(repo_root / rel_path))


def test_longrun_profile_configs_load_and_enable_longrun(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    conservative = _load("agent_configs/longrun_conservative_v1.yaml")
    balanced = _load("agent_configs/longrun_balanced_v1.yaml")
    aggressive = _load("agent_configs/longrun_aggressive_v1.yaml")

    assert conservative["long_running"]["enabled"] is True
    assert balanced["long_running"]["enabled"] is True
    assert aggressive["long_running"]["enabled"] is True

    c_budget = conservative["long_running"]["budgets"]
    b_budget = balanced["long_running"]["budgets"]
    a_budget = aggressive["long_running"]["budgets"]

    assert int(c_budget["max_episodes"]) < int(b_budget["max_episodes"]) < int(a_budget["max_episodes"])
    assert float(c_budget["total_cost_usd"]) < float(b_budget["total_cost_usd"]) < float(a_budget["total_cost_usd"])
    assert str(aggressive["long_running"]["queue"]["backend"]) == "feature_file"
    assert str(conservative["long_running"]["policy_profile"]) == "conservative"
    assert str(balanced["long_running"]["policy_profile"]) == "balanced"
    assert str(aggressive["long_running"]["policy_profile"]) == "aggressive"
    assert (
        str(conservative["prompts"]["packs"]["base"]["builder"])
        == "implementations/system_prompts/longrun/policy_conservative.md"
    )
    assert (
        str(balanced["prompts"]["packs"]["base"]["builder"])
        == "implementations/system_prompts/longrun/policy_balanced.md"
    )
    assert (
        str(aggressive["prompts"]["packs"]["base"]["builder"])
        == "implementations/system_prompts/longrun/policy_aggressive.md"
    )
