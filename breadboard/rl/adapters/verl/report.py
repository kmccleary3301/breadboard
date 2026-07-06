from __future__ import annotations

from breadboard.rl.adapters.probe import AdapterProbeReport


def build_verl_jsonl_probe_report() -> AdapterProbeReport:
    return AdapterProbeReport(
        adapter_id="verl.jsonl_probe.v1",
        adapter_kind="verl_jsonl_probe_export",
        support_level="jsonl_probe",
        workload_family="swe",
        preserved_fields=[
            "input_ids",
            "attention_mask",
            "loss_mask",
            "completion_logprobs",
            "reward_scalar",
            "policy_id",
            "env_package_hash",
        ],
        field_mapping={
            "input_ids": "VeRLProbeRow.input_ids",
            "attention_mask": "VeRLProbeRow.attention_mask",
            "loss_mask": "VeRLProbeRow.loss_mask",
            "completion_logprobs": "VeRLProbeRow.completion_logprobs",
            "reward_scalar": "VeRLProbeRow.reward_scalar",
            "policy_id": "VeRLProbeRow.policy_id",
            "env_package_hash": "VeRLProbeRow.env_package_hash",
        },
        lost_fields=["verl_DataProto_object", "trainer_execution_state"],
        unsupported_fields=["ppo_grpo_trainer_execution", "distributed_actor_logprob_refresh"],
        source_artifacts=["docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.jsonl"],
        fidelity_notes=[
            "This report covers BreadBoard's token-native JSONL/Parquet projection, not VeRL's in-process DataProto object.",
            "Completion logprobs are explicit nullable fields with status metadata; they are not proof of actor-side logprob refresh.",
        ],
        promotion_requirements=[
            "Load the exported rows into a real VeRL DataProto or documented ingestion shim and validate tensor dtypes/shapes.",
            "Run a no-op or tiny trainer smoke that consumes BreadBoard rows without treating quarantined rows as trainable.",
        ],
        metadata={
            "fixture_scope": "jsonl_parquet_export_projection",
            "minimum_real_promotion_level": "live_probe",
        },
    )
