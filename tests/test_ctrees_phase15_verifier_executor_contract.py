from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.live_benchmark_adapter import build_phase15_verifier_executor_protocol_payload
from agentic_coder_prototype.ctrees.verifier_executor_contract import build_verifier_executor_contract


def test_phase15_verifier_executor_contract_builds_execution_first_variant() -> None:
    config = load_agent_config(
        "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_verifier_owned_executor_v2_native.yaml"
    )
    payload = build_verifier_executor_contract(config)
    assert payload["enabled"] is True
    assert payload["family"] == "execution_first_verifier_owned_executor_v2_mini_native"
    assert payload["support_strategy"] == "minimal_support"
    assert payload["tool_allowlists"]["close"] == ["mark_task_complete"]
    assert payload["finish_rules"]["allow_shell_only_grounded_close"] is False


def test_phase15_verifier_protocol_payload_injects_task_closure_rule() -> None:
    config = load_agent_config(
        "agent_configs/misc/codex_cli_gpt54mini_e4_live_deterministic_verifier_owned_executor_v2_native.yaml"
    )
    payload = build_phase15_verifier_executor_protocol_payload(
        config=config,
        task={
            "id": "phase15_probe_edit_receipt_required_v1",
            "base_task_id": "downstream_interrupt_repair_v1",
            "prompt": "Apply one patch and verify it.",
            "closure_mode": "edit_required",
            "required_receipts": ["edit", "verification", "finish"],
            "requires_edit_commitment": True,
        },
    )
    assert payload["applied"] is True
    assert payload["family"] == "deterministic_verifier_owned_executor_v2_mini_native"
    assert payload["support_strategy"] == "deterministic_reranker"
    assert payload["task_context"]["closure_mode"] == "edit_required"
    assert "[Task closure rule]" in payload["prompt"]
    assert "required_receipts: edit, verification, finish" in payload["prompt"]
