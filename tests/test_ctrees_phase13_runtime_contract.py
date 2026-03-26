from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.live_benchmark_adapter import build_phase13_runtime_protocol_payload
from agentic_coder_prototype.ctrees.runtime_finish_guard import evaluate_runtime_finish_guard
from agentic_coder_prototype.ctrees.runtime_policy_contract import build_runtime_policy_contract
from agentic_coder_prototype.ctrees.runtime_state import build_runtime_state
from agentic_coder_prototype.ctrees.tool_policy import allowed_tool_classes_for_phase, should_force_edit_commit


def test_phase13_runtime_policy_contract_builds_candidate_a_runtime_v1() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt5_e4_live_candidate_a_runtime_v1.yaml")
    payload = build_runtime_policy_contract(config)
    assert payload["enabled"] is True
    assert payload["family"] == "candidate_a_runtime_v1"
    assert payload["variant"] == "candidate_a_runtime_v1"
    assert payload["support_strategy"] == "candidate_a"
    assert payload["phase_sequence"] == ["localize", "commit_edit", "edit", "verify", "close"]


def test_phase13_runtime_protocol_payload_separates_support_and_runtime() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt5_e4_live_deterministic_runtime_v1.yaml")
    payload = build_phase13_runtime_protocol_payload(
        config=config,
        task={
            "id": "downstream_dependency_shift_v1__r1",
            "base_task_id": "downstream_dependency_shift_v1",
            "prompt": "Continue with the dependency shift.",
        },
    )
    assert payload["applied"] is True
    assert payload["family"] == "deterministic_runtime_v1"
    assert payload["support_strategy"] == "deterministic_reranker"
    assert payload["support_payload"]["support_node_ids"]
    assert payload["runtime_contract"]["enabled"] is True
    assert "[Runtime execution contract]" in payload["prompt"]
    assert "Support strategy: deterministic_reranker" in payload["prompt"]


def test_phase13_tool_policy_and_finish_guard() -> None:
    state = build_runtime_state(family="candidate_a_runtime_v1", support_strategy="candidate_a", phase="localize")
    assert allowed_tool_classes_for_phase("edit") == ["apply_patch", "write_file", "read"]
    state["inspection_turns"] = 2
    assert should_force_edit_commit(state, max_localize_turns=2) is True
    state["phase"] = "close"
    guarded = evaluate_runtime_finish_guard(
        state,
        grounded_completion=True,
        verified_completion=True,
        require_verified_completion=True,
    )
    assert guarded["allowed"] is True


def test_phase13_runtime_policy_contract_builds_v2_rules() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt5_e4_live_candidate_a_runtime_v2.yaml")
    payload = build_runtime_policy_contract(config)
    assert payload["variant"] == "candidate_a_runtime_v2"
    assert payload["localize_budget"]["force_edit_hypothesis_by_turn"] == 1
    assert payload["localize_budget"]["mandatory_write_by_turn"] == 2
    assert payload["localize_budget"]["allow_verify_before_edit"] is False
    assert "By turn 1, commit to exactly one edit hypothesis" in payload["prompt_block"]


def test_phase13_runtime_protocol_payload_builds_minimal_support_challenger() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_runtime_v1.yaml")
    payload = build_phase13_runtime_protocol_payload(
        config=config,
        task={
            "id": "downstream_semantic_pivot_v1__r1",
            "base_task_id": "downstream_semantic_pivot_v1",
            "prompt": "Make the smallest grounded fix and verify it.",
        },
    )
    assert payload["applied"] is True
    assert payload["family"] == "execution_first_runtime_v1_mini"
    assert payload["support_strategy"] == "minimal_support"
    assert payload["support_payload"]["base_strategy"] == "deterministic_reranker"
    assert len(payload["support_payload"]["support_node_ids"]) <= 1
