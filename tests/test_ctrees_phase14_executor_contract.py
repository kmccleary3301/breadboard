from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.executor_contract import build_executor_contract
from agentic_coder_prototype.ctrees.live_benchmark_adapter import build_phase14_executor_protocol_payload


def test_phase14_executor_contract_builds_candidate_a_variant() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt54_e4_live_candidate_a_executor_v1.yaml")
    payload = build_executor_contract(config)
    assert payload["enabled"] is True
    assert payload["family"] == "candidate_a_executor_v1"
    assert payload["support_strategy"] == "candidate_a"
    assert payload["tool_allowlists"]["localize"] == ["shell_command"]
    assert payload["tool_allowlists"]["close"] == ["mark_task_complete"]


def test_phase14_executor_protocol_payload_injects_support_and_executor_note() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt54_e4_live_deterministic_executor_v1.yaml")
    payload = build_phase14_executor_protocol_payload(
        config=config,
        task={
            "id": "downstream_dependency_shift_v1__r1",
            "base_task_id": "downstream_dependency_shift_v1",
            "prompt": "Continue with the dependency shift.",
        },
    )
    assert payload["applied"] is True
    assert payload["family"] == "deterministic_executor_v1"
    assert payload["support_strategy"] == "deterministic_reranker"
    assert payload["executor_contract"]["enabled"] is True
    assert "[Executor-selected support context]" in payload["prompt"]
    assert "[Runner-owned executor note]" in payload["prompt"]
