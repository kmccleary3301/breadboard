from __future__ import annotations

from breadboard.rl.adapters.probe import AdapterProbeReport


def build_ors_fixture_probe_report() -> AdapterProbeReport:
    return AdapterProbeReport(
        adapter_id="ors.fixture.v1",
        adapter_kind="ors_openreward_fixture",
        support_level="fixture_probe",
        workload_family="swe",
        preserved_fields=[
            "task_id",
            "prompt_fields",
            "reward_scalar",
            "verifier_evidence_ref",
            "split_id",
        ],
        field_mapping={
            "task_id": "M6 run_summary.rows[].task_id",
            "prompt_fields": "M6 run_summary.rows[].prompt_preview",
            "reward_scalar": "M6 run_summary.rows[].reward_scalar",
            "verifier_evidence_ref": "M6 run_summary.rows[].verifier_evidence_ref",
            "split_id": "M6 run_summary.rows[].split_id",
        },
        lost_fields=["live_ors_server_route", "remote_reward_model_metadata"],
        unsupported_fields=["production_ors_execution"],
        source_artifacts=["docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/run_summary.json"],
        fidelity_notes=[
            "This probe treats ORS/OpenReward as a reward-record exchange shape over the M6 controlled SWE toy run.",
            "It preserves local reward and verifier references but does not contact an ORS service or refresh remote reward metadata.",
        ],
        promotion_requirements=[
            "Submit a BreadBoard-generated rollout row to a real ORS/OpenReward endpoint and record route/version metadata.",
            "Verify reward parity between the local verifier scalar and the returned ORS/OpenReward result on accepted and quarantined rows.",
        ],
        metadata={
            "fixture_scope": "local_reward_record_projection",
            "minimum_real_promotion_level": "live_probe",
        },
    )
