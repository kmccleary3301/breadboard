from __future__ import annotations

import os

from agentic_coder_prototype.ctrees.downstream_task_eval import run_phase11_downstream_pilot_proxy


def test_phase11_downstream_proxy_exposes_practical_baseline_contract() -> None:
    payload = run_phase11_downstream_pilot_proxy()

    assert payload["schema_version"] == "phase11_downstream_pilot_proxy_v1"
    assert payload["task_count"] == 4
    contract = payload["practical_baseline_contract"]
    assert contract["schema_version"] == "phase11_practical_baseline_contract_v1"
    expected_status = "contract_ready" if os.environ.get("OPENAI_API_KEY") else "provider_blocked_missing_env"
    assert payload["summary"]["practical_baseline_status"] == expected_status
    for task in payload["tasks"]:
        practical = next(item for item in task["systems"] if item["system_id"] == "practical_baseline")
        assert practical["execution_status"] == "pending_external_baseline"
        assert practical["outcome"]["task_success"] is None


def test_phase11_downstream_proxy_shows_candidate_a_ahead_of_deterministic_locally() -> None:
    payload = run_phase11_downstream_pilot_proxy()
    summary = payload["summary"]

    assert summary["candidate_a_vs_deterministic_wins"] == 4
    assert summary["candidate_a_vs_deterministic_losses"] == 0
    assert summary["candidate_a_vs_deterministic_ties"] == 0
