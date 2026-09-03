from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes
from scripts.rl_phase5.run_f8_grpo_evidence_gate import (
    F8CheckpointUpdate,
    F8CleanupEvidence,
    F8EpisodeJoin,
    F8EvidenceJoin,
    F8GRPOEvidenceGateReport,
    F8ImmutableJSONRef,
    F8InputHashes,
    F8LearningEvidence,
    F8RolloutCarrier,
    F8TargetIdentity,
    F8TrainingIdentities,
)
from scripts.rl_phase5.run_f9_estimator_truth_gate import (
    F9EstimatorTruthGateError,
    F9EstimatorTruthGateInput,
    F9EstimatorTruthGateReport,
    F9ImmutableJSONRef,
    run_f9_estimator_truth_gate,
)


NONCLAIM = "GRPO evidence only; no PPO estimator claim is allowed"


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: Any) -> F9ImmutableJSONRef:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_json_bytes(
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    )
    path.write_bytes(raw)
    return F9ImmutableJSONRef(path=str(path.resolve()), digest=_sha(raw))


def _identities() -> F8TrainingIdentities:
    return F8TrainingIdentities(
        config_digest=_digest("config"),
        task_digest=_digest("task"),
        model_digest=_digest("model"),
        tokenizer_digest=_digest("tokenizer"),
        input_checkpoint_digest=_digest("checkpoint-before"),
        output_checkpoint_digest=_digest("checkpoint-after"),
        verifier_digest=_digest("verifier"),
        image_digest=_digest("image"),
        preflight_digest=_digest("preflight"),
    )


def _f8_report(
    tmp_path: Path, *, completed_at: str = "2026-07-13T12:00:00Z"
) -> tuple[F8GRPOEvidenceGateReport, F9ImmutableJSONRef]:
    identities = _identities()
    target = F8TargetIdentity(
        target_run_id="ibm-target-run-f8",
        command_id="ibm-command-f8",
        job_id="ibm-job-f8",
    )
    input_hashes = F8InputHashes(
        config_input_sha256=identities.config_digest,
        task_input_sha256=identities.task_digest,
        model_input_sha256=identities.model_digest,
        tokenizer_input_sha256=identities.tokenizer_digest,
        checkpoint_input_sha256=identities.input_checkpoint_digest,
        verifier_input_sha256=identities.verifier_digest,
        image_input_sha256=identities.image_digest,
        preflight_input_sha256=identities.preflight_digest,
    )
    joins: list[F8EpisodeJoin] = []
    for sample_index in range(64):
        group = sample_index // 8
        rollout_index = sample_index % 8
        carrier = F8RolloutCarrier(
            target_run_id=target.target_run_id,
            episode_id=f"episode-f8-{sample_index:03d}",
            attempt_id=f"attempt-f8-{sample_index:03d}",
            optimizer_step=group % 3 + 1,
            task_row_id=f"task-row-{group:03d}",
            rollout_index=rollout_index,
            config_digest=identities.config_digest,
            task_digest=identities.task_digest,
            model_digest=identities.model_digest,
            tokenizer_digest=identities.tokenizer_digest,
            checkpoint_digest=identities.input_checkpoint_digest,
            verifier_digest=identities.verifier_digest,
            image_digest=identities.image_digest,
            preflight_digest=identities.preflight_digest,
            carrier_digest=_digest(f"rollout-carrier-{sample_index}"),
        )
        joins.append(
            F8EpisodeJoin(
                episode_id=carrier.episode_id,
                attempt_id=carrier.attempt_id,
                identities=identities,
                rollout_carrier=carrier,
                generated_sample_count=1,
                joined_sample_count=1,
                reward_min=0.0 if sample_index % 2 == 0 else 1.0,
                reward_max=0.0 if sample_index % 2 == 0 else 1.0,
                evidence_digest=_digest(f"episode-evidence-{sample_index}"),
            )
        )
    source_ref_value = _write_json(
        tmp_path / "f8-target-source.json",
        {"canonical_f8_source_fixture": True, "target": target.model_dump(mode="json")},
    )
    source_ref = F8ImmutableJSONRef(
        path=source_ref_value.path,
        digest=source_ref_value.digest,
    )
    report = F8GRPOEvidenceGateReport(
        schema_version="bb.rl.phase5-f8-grpo-evidence-gate-report.v3",
        component="f8_grpo_evidence_gate",
        report_id="f8-grpo-evidence-ibm-target-run-f8",
        passed=True,
        blocked_reason="",
        input_digest=_digest("f8-input"),
        gate_id="f8-gate",
        target=target,
        identities=identities,
        input_hashes=input_hashes,
        trainer_backend="verl_grpo",
        algorithm_adv_estimator="grpo",
        estimator_label="grpo",
        rollout_n=8,
        learning_evidence=F8LearningEvidence(
            run_kind="bounded",
            optimizer_step_count=3,
            generated_sample_count=64,
            reward_min=0.0,
            reward_max=1.0,
            advantage_abs_max=1.0,
            actor_gradient_norm=0.5,
            learning_rate=1e-6,
            optimizer_step_skipped=False,
            optimizer_update_finite=True,
            aborted_ratio=0.0,
            dropped_stale_samples=0,
            actor_ppo_kl=0.001,
            actor_k3_kl=0.00001,
            required_kl_metrics_present=True,
        ),
        episode_joins=tuple(joins),
        evidence_join=F8EvidenceJoin(
            generated_sample_count=64,
            joined_sample_count=64,
            unmatched_sample_count=0,
            duplicate_join_count=0,
            carrier_alignment_exact=True,
            episode_attempt_alignment_exact=True,
            evidence_manifest_digest=_digest("manifest"),
        ),
        checkpoint_update=F8CheckpointUpdate(
            checkpoint_before_digest=identities.input_checkpoint_digest,
            checkpoint_after_digest=identities.output_checkpoint_digest,
            changed_parameter_name="actor.layer.weight",
            parameter_before_digest=_digest("actor-before"),
            parameter_after_digest=_digest("actor-after"),
            optimizer_update_digest=_digest("optimizer-update"),
            retained_checkpoint_digest=identities.output_checkpoint_digest,
            fresh_process_reload=True,
            reload_model_digest=identities.model_digest,
            reload_config_digest=identities.config_digest,
            reload_tokenizer_digest=identities.tokenizer_digest,
            changed_parameter_reverified=True,
            bounded_inference_or_preflight_passed=True,
        ),
        cleanup=F8CleanupEvidence(
            terminal_state="closed",
            active_lease_ids=(),
            remaining_process_ids=(),
            remaining_container_ids=(),
            cleanup_errors=(),
            failed_outputs_quarantined=True,
            failed_checkpoints_quarantined=True,
            retained_checkpoint_present=True,
        ),
        target_source_report=source_ref,
        completed_at=completed_at,
        claim_scope="finite_step_optimizer_signal_not_convergence_or_benchmark_gain",
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_authority=False,
        scorecard_update_allowed=False,
    )
    return report, _write_json(tmp_path / "f8-report.json", report)


def _spec(
    tmp_path: Path,
    f8: F8GRPOEvidenceGateReport,
    f8_ref: F9ImmutableJSONRef,
    *,
    ppo_ref: F9ImmutableJSONRef | None = None,
    output_name: str = "f9-output",
) -> F9EstimatorTruthGateInput:
    return F9EstimatorTruthGateInput(
        schema_version="bb.rl.phase5-f9-estimator-truth-gate-input.v3",
        expected_f8_report_id=f8.report_id,
        expected_target=f8.target,
        expected_identities=f8.identities,
        f8_evidence_not_before="2026-07-13T11:00:00Z",
        f8_report=f8_ref,
        ppo_trainer_report=ppo_ref,
        output_dir=str((tmp_path / output_name).resolve()),
    )


def _run(
    spec: F9EstimatorTruthGateInput, *, input_digest: str
) -> tuple[F9EstimatorTruthGateReport, str]:
    return run_f9_estimator_truth_gate(spec, input_digest=input_digest)


def _rewrite_f8(
    tmp_path: Path,
    f8: F8GRPOEvidenceGateReport,
    payload: dict[str, Any],
    name: str,
) -> tuple[F8GRPOEvidenceGateReport, F9ImmutableJSONRef]:
    mutated = F8GRPOEvidenceGateReport.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )
    return mutated, _write_json(tmp_path / f"{name}.json", mutated)


def _assert_exact_nonclaim(report: F9EstimatorTruthGateReport) -> None:
    assert report.estimator_mode == "GRPO"
    assert report.estimator_label == "GRPO/conditional policy optimization"
    assert report.estimator_claim == NONCLAIM
    assert report.disposition == "DISABLED_WITH_REQUIRED_NONCLAIM"
    assert report.ppo_claim_allowed is False
    assert report.critic_value_claim_allowed is False
    assert report.gae_returns_claim_allowed is False
    assert report.ppo_external_trust_policy_available is False
    assert report.permanent_non_authority is True
    assert report.promotion_authority is False
    assert report.scorecard_authority is False
    assert report.scorecard_update_allowed is False


def test_exact_grpo_grouping_optimizer_and_checkpoint_evidence_emits_nonclaim(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    report, report_path = _run(
        _spec(tmp_path, f8, f8_ref), input_digest=_digest("grpo-only")
    )
    _assert_exact_nonclaim(report)
    assert report.ppo_evidence_status == "NOT_SUPPLIED"
    assert report.ppo_evidence_supplied is False
    assert report.ppo_trainer_report is None
    assert report.grpo_rollout_n == 8
    assert len(report.episode_joins) == 64
    assert report.learning_evidence.optimizer_step_count == 3
    assert Path(report_path).is_file()
    assert capfd.readouterr().out.startswith("PHASE3_COMPONENT_REPORT_JSON=")


def test_complete_locally_self_signed_ibm_fixture_cannot_unlock_ppo(
    tmp_path: Path,
) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    forged = {
        "schema_version": "locally-forged-ppo-authority.v1",
        "execution_scope": "ibm_slurm_apptainer",
        "runner_id": "ibm-runner-local-forgery",
        "trainer": "verl.trainer.main_ppo",
        "algorithm_adv_estimator": "gae",
        "critic_present": True,
        "gae_returns_present": True,
        "actor_optimizer_steps": 3,
        "critic_optimizer_steps": 3,
        "actor_checkpoint_changed": True,
        "critic_checkpoint_changed": True,
        "raw_stdout": "/fabricated/ppo_stdout.log",
        "raw_stderr": "/fabricated/ppo_stderr.log",
        "caller_chosen_key_id": "local-hmac-key",
        "caller_chosen_signature": _digest("locally-signed-receipt"),
    }
    ppo_ref = _write_json(tmp_path / "forged-ibm-ppo.json", forged)
    report, _ = _run(
        _spec(tmp_path, f8, f8_ref, ppo_ref=ppo_ref),
        input_digest=_digest("self-signed-ibm"),
    )
    _assert_exact_nonclaim(report)
    assert report.ppo_evidence_status == "DISABLED_NO_APPROVED_EXTERNAL_TRUST_POLICY"
    assert report.ppo_evidence_supplied is True
    assert report.ppo_trainer_report == ppo_ref


@pytest.mark.parametrize(
    "adversary",
    [
        "forced-grpo-labeled-ppo",
        "main-ppo-entrypoint-only",
        "copied-ppo-config",
        "missing-raw-logs",
        "contradictory-grouped-ppo",
        "fixture-execution",
        "local-execution",
        "generic-slurm-label",
        "stale-ppo-artifacts",
        "cross-run-ppo-artifacts",
        "opaque-actor-checkpoint",
        "opaque-critic-checkpoint",
        "nan-actor-checkpoint",
        "nan-critic-checkpoint",
        "unchanged-actor-checkpoint",
        "unchanged-critic-checkpoint",
        "switched-actor-parameter",
        "switched-critic-parameter",
        "nonterminal-multistep-gae",
    ],
)
def test_untrusted_ppo_diagnostics_and_labels_always_emit_exact_nonclaim(
    tmp_path: Path,
    adversary: str,
) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    ppo_ref = _write_json(
        tmp_path / f"{adversary}.json",
        {
            "adversary": adversary,
            "claims_ppo": True,
            "claims_ibm": True,
            "claims_external_authority": True,
        },
    )
    report, _ = _run(
        _spec(tmp_path, f8, f8_ref, ppo_ref=ppo_ref, output_name=adversary),
        input_digest=_digest(adversary),
    )
    _assert_exact_nonclaim(report)
    assert report.ppo_evidence_status == "DISABLED_NO_APPROVED_EXTERNAL_TRUST_POLICY"


@pytest.mark.parametrize(
    "mutation",
    ["sixteen-member-duplicate-group", "duplicate-carrier", "missing-optimizer-step"],
)
def test_grpo_multiplicity_carrier_and_optimizer_mutations_fail_before_output(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    mutation: str,
) -> None:
    f8, _ = _f8_report(tmp_path)
    payload = f8.model_dump(mode="json")
    joins = payload["episode_joins"]
    if mutation == "sixteen-member-duplicate-group":
        for index in range(56, 64):
            joins[index]["rollout_carrier"]["optimizer_step"] = 1
            joins[index]["rollout_carrier"]["task_row_id"] = "task-row-000"
    elif mutation == "duplicate-carrier":
        joins[-1]["rollout_carrier"]["carrier_digest"] = joins[0]["rollout_carrier"][
            "carrier_digest"
        ]
    elif mutation == "missing-optimizer-step":
        for join in joins:
            if join["rollout_carrier"]["optimizer_step"] == 3:
                join["rollout_carrier"]["optimizer_step"] = 1
    mutated, mutated_ref = _rewrite_f8(tmp_path, f8, payload, mutation)
    spec = _spec(tmp_path, mutated, mutated_ref, output_name=f"out-{mutation}")
    with pytest.raises(F9EstimatorTruthGateError, match="F8 GRPO"):
        _run(spec, input_digest=_digest(mutation))
    assert not Path(spec.output_dir).exists()
    assert capfd.readouterr().out == ""


def test_stale_f8_fails_before_persistence_or_stdout(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    f8, f8_ref = _f8_report(tmp_path, completed_at="2026-07-13T10:59:59Z")
    spec = _spec(tmp_path, f8, f8_ref)
    with pytest.raises(F9EstimatorTruthGateError, match="required F8 evidence is stale"):
        _run(spec, input_digest=_digest("stale-f8"))
    assert not Path(spec.output_dir).exists()
    assert capfd.readouterr().out == ""


def test_input_rejects_all_caller_controlled_authority_pins_and_key_seams(
    tmp_path: Path,
) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    payload = _spec(tmp_path, f8, f8_ref).model_dump(mode="json")
    for field, value in (
        ("ppo_external_authority_receipt", f8_ref.model_dump(mode="json")),
        ("expected_ppo_authority_key_id", "caller-key"),
        ("expected_ppo_authority_key_digest", _digest("caller-key")),
        ("ppo_runner_authority_key_file", "/tmp/caller.key"),
    ):
        overclaim = dict(payload)
        overclaim[field] = value
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            F9EstimatorTruthGateInput.model_validate(overclaim, strict=True)


def test_report_schema_cannot_represent_any_ppo_claim(
    tmp_path: Path,
) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    report, _ = _run(
        _spec(tmp_path, f8, f8_ref), input_digest=_digest("report-schema")
    )
    payload = report.model_dump(mode="json")
    for field, value in (
        ("estimator_mode", "PPO"),
        ("ppo_claim_allowed", True),
        ("critic_value_claim_allowed", True),
        ("gae_returns_claim_allowed", True),
        ("disposition", "VERIFIED_REAL_PPO"),
        ("ppo_external_trust_policy_available", True),
    ):
        overclaim = dict(payload)
        overclaim[field] = value
        with pytest.raises(ValidationError):
            F9EstimatorTruthGateReport.model_validate(overclaim, strict=True)


def test_models_forbid_extra_fields(tmp_path: Path) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    payload = _spec(tmp_path, f8, f8_ref).model_dump(mode="json")
    payload["unreviewed_mode"] = "ppo"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        F9EstimatorTruthGateInput.model_validate(payload, strict=True)


def test_f8_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    bad_ref = f8_ref.model_copy(update={"digest": _digest("wrong-f8")})
    spec = _spec(tmp_path, f8, bad_ref)
    with pytest.raises(F9EstimatorTruthGateError, match="digest mismatch"):
        _run(spec, input_digest=_digest("bad-f8-digest"))


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    f8, f8_ref = _f8_report(tmp_path)
    spec = _spec(tmp_path, f8, f8_ref)
    _run(spec, input_digest=_digest("first-write"))
    with pytest.raises(FileExistsError):
        _run(spec, input_digest=_digest("second-write"))
