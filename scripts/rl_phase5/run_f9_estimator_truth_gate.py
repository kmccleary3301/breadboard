from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes, canonical_json_loads
from scripts.rl_phase5.run_f8_grpo_evidence_gate import (
    F8CheckpointUpdate,
    F8EpisodeJoin,
    F8EvidenceJoin,
    F8GRPOEvidenceGateReport,
    F8InputHashes,
    F8LearningEvidence,
    F8TargetIdentity,
    F8TrainingIdentities,
)

_REPORT_NAME = "f9-estimator-truth-gate.report.json"
_COMPONENT = "f9_estimator_truth_gate"
_GRPO_LABEL = "GRPO/conditional policy optimization"
_GRPO_CLAIM = "GRPO evidence only; no PPO estimator claim is allowed"
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class F9EstimatorTruthGateError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("expected lowercase sha256 digest")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not Path(value).is_absolute():
        raise ValueError("path must be absolute")
    return value


def _utc(value: str) -> str:
    if type(value) is not str or _UTC_RE.fullmatch(value) is None:
        raise ValueError("expected canonical UTC timestamp")
    datetime.fromisoformat(value[:-1] + "+00:00")
    return value


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class F9ImmutableJSONRef(_ExactModel):
    path: str
    digest: str
    media_type: Literal["application/json"] = "application/json"

    _path = field_validator("path")(_absolute)
    _sha = field_validator("digest")(_digest)


class F9EstimatorTruthGateInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f9-estimator-truth-gate-input.v3"]
    expected_f8_report_id: str = Field(min_length=1, max_length=256)
    expected_target: F8TargetIdentity
    expected_identities: F8TrainingIdentities
    f8_evidence_not_before: str
    f8_report: F9ImmutableJSONRef
    ppo_trainer_report: F9ImmutableJSONRef | None
    output_dir: str

    _freshness = field_validator("f8_evidence_not_before")(_utc)
    _output = field_validator("output_dir")(_absolute)


PPOEvidenceStatus = Literal[
    "NOT_SUPPLIED",
    "DISABLED_NO_APPROVED_EXTERNAL_TRUST_POLICY",
]


class F9EstimatorTruthGateReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f9-estimator-truth-gate-report.v2"]
    component: Literal["f9_estimator_truth_gate"]
    report_id: str = Field(min_length=1, max_length=512)
    passed: Literal[True]
    input_digest: str
    f8_report: F9ImmutableJSONRef
    f8_report_id: str
    f8_completed_at: str
    f8_target_source_report: F9ImmutableJSONRef
    target: F8TargetIdentity
    identities: F8TrainingIdentities
    input_hashes: F8InputHashes
    episode_joins: tuple[F8EpisodeJoin, ...] = Field(min_length=1)
    grpo_algorithm_adv_estimator: Literal["grpo"]
    grpo_rollout_n: Literal[8]
    grouped_multi_sample_evidence: Literal[True]
    learning_evidence: F8LearningEvidence
    evidence_join: F8EvidenceJoin
    checkpoint_update: F8CheckpointUpdate
    estimator_mode: Literal["GRPO"]
    estimator_label: Literal["GRPO/conditional policy optimization"]
    ppo_claim_allowed: Literal[False]
    critic_value_claim_allowed: Literal[False]
    gae_returns_claim_allowed: Literal[False]
    estimator_claim: Literal["GRPO evidence only; no PPO estimator claim is allowed"]
    disposition: Literal["DISABLED_WITH_REQUIRED_NONCLAIM"]
    ppo_evidence_status: PPOEvidenceStatus
    ppo_evidence_supplied: bool
    ppo_trainer_report: F9ImmutableJSONRef | None
    ppo_external_trust_policy_available: Literal[False]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]
    scorecard_update_allowed: Literal[False]

    _input = field_validator("input_digest")(_digest)
    _completed = field_validator("f8_completed_at")(_utc)

    @model_validator(mode="after")
    def exact_nonclaim(self) -> "F9EstimatorTruthGateReport":
        if self.ppo_evidence_supplied != (self.ppo_trainer_report is not None):
            raise ValueError("PPO supplied flag does not match the immutable reference")
        expected_status: PPOEvidenceStatus = (
            "DISABLED_NO_APPROVED_EXTERNAL_TRUST_POLICY"
            if self.ppo_evidence_supplied
            else "NOT_SUPPLIED"
        )
        if self.ppo_evidence_status != expected_status:
            raise ValueError("PPO status does not match disabled evidence scope")
        return self


def _read_required_f8(ref: F9ImmutableJSONRef) -> F8GRPOEvidenceGateReport:
    try:
        raw = Path(ref.path).resolve(strict=True).read_bytes()
    except OSError as exc:
        raise F9EstimatorTruthGateError("required F8 report is missing or unreadable") from exc
    if _sha256(raw) != ref.digest:
        raise F9EstimatorTruthGateError("required F8 report digest mismatch")
    try:
        value = canonical_json_loads(raw)
    except Exception as exc:
        raise F9EstimatorTruthGateError("required F8 report is invalid JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise F9EstimatorTruthGateError("required F8 report is not canonical JSON")
    try:
        return F8GRPOEvidenceGateReport.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        raise F9EstimatorTruthGateError(
            "required F8 report does not satisfy the exact schema"
        ) from exc


def _validate_grpo_evidence(f8: F8GRPOEvidenceGateReport) -> None:
    joins = f8.episode_joins
    if (
        f8.trainer_backend != "verl_grpo"
        or f8.algorithm_adv_estimator != "grpo"
        or f8.estimator_label != "grpo"
        or f8.rollout_n != 8
        or len(joins) != 64
        or len(joins) != f8.learning_evidence.generated_sample_count
        or len(joins) != f8.evidence_join.generated_sample_count
        or len(joins) != f8.evidence_join.joined_sample_count
        or f8.evidence_join.unmatched_sample_count != 0
        or f8.evidence_join.duplicate_join_count != 0
        or not f8.evidence_join.carrier_alignment_exact
        or not f8.evidence_join.episode_attempt_alignment_exact
    ):
        raise F9EstimatorTruthGateError(
            "F8 GRPO sample/evidence cardinality is contradictory"
        )

    groups: dict[tuple[int, str], set[int]] = defaultdict(set)
    group_counts: dict[tuple[int, str], int] = defaultdict(int)
    seen_episode_attempts: set[tuple[str, str]] = set()
    seen_carrier_digests: set[str] = set()
    seen_carrier_slots: set[tuple[int, str, int]] = set()
    observed_optimizer_steps: set[int] = set()
    expected_indices = set(range(8))

    for join in joins:
        carrier = join.rollout_carrier
        episode_attempt = (join.episode_id, join.attempt_id)
        slot = (carrier.optimizer_step, carrier.task_row_id, carrier.rollout_index)
        if (
            join.generated_sample_count != 1
            or join.joined_sample_count != 1
            or join.identities != f8.identities
            or carrier.target_run_id != f8.target.target_run_id
            or carrier.episode_id != join.episode_id
            or carrier.attempt_id != join.attempt_id
            or carrier.rollout_index >= 8
            or episode_attempt in seen_episode_attempts
            or carrier.carrier_digest in seen_carrier_digests
            or slot in seen_carrier_slots
        ):
            raise F9EstimatorTruthGateError(
                "F8 GRPO episode/carrier identity join is contradictory"
            )
        seen_episode_attempts.add(episode_attempt)
        seen_carrier_digests.add(carrier.carrier_digest)
        seen_carrier_slots.add(slot)
        observed_optimizer_steps.add(carrier.optimizer_step)
        group = (carrier.optimizer_step, carrier.task_row_id)
        groups[group].add(carrier.rollout_index)
        group_counts[group] += 1

    expected_steps = set(range(1, f8.learning_evidence.optimizer_step_count + 1))
    if (
        len(groups) != 8
        or any(indices != expected_indices for indices in groups.values())
        or any(count != 8 for count in group_counts.values())
        or observed_optimizer_steps != expected_steps
        or len(observed_optimizer_steps) != f8.learning_evidence.optimizer_step_count
    ):
        raise F9EstimatorTruthGateError(
            "F8 GRPO evidence lacks exact grouped rollout or optimizer-step coverage"
        )

    checkpoint = f8.checkpoint_update
    if (
        checkpoint.checkpoint_before_digest != f8.identities.input_checkpoint_digest
        or checkpoint.checkpoint_after_digest != f8.identities.output_checkpoint_digest
        or checkpoint.parameter_before_digest == checkpoint.parameter_after_digest
        or checkpoint.retained_checkpoint_digest != checkpoint.checkpoint_after_digest
        or not checkpoint.fresh_process_reload
        or not checkpoint.changed_parameter_reverified
        or not checkpoint.bounded_inference_or_preflight_passed
        or f8.learning_evidence.optimizer_step_skipped
        or not f8.learning_evidence.optimizer_update_finite
        or f8.learning_evidence.learning_rate <= 0
    ):
        raise F9EstimatorTruthGateError(
            "F8 GRPO optimizer/checkpoint identity join is contradictory"
        )


def _write_report(report: F9EstimatorTruthGateReport, output_dir: str) -> str:
    root = Path(output_dir)
    root.mkdir(mode=0o750, parents=False, exist_ok=True)
    output = root / _REPORT_NAME
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    fd = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return os.fspath(output.resolve())


def _component_line(report: F9EstimatorTruthGateReport, path: str) -> bytes:
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    if Path(path).read_bytes() != raw:
        raise F9EstimatorTruthGateError("persisted F9 report mismatch")
    envelope = {
        "schema_version": "bb.rl.phase5-f9-estimator-truth-component-report.v2",
        "component": _COMPONENT,
        "report_id": report.report_id,
        "passed": True,
        "estimator_mode": "GRPO",
        "estimator_label": _GRPO_LABEL,
        "estimator_claim": _GRPO_CLAIM,
        "disposition": "DISABLED_WITH_REQUIRED_NONCLAIM",
        "ppo_claim_allowed": False,
        "ppo_evidence_status": report.ppo_evidence_status,
        "ppo_external_trust_policy_available": False,
        "permanent_non_authority": True,
        "promotion_authority": False,
        "scorecard_authority": False,
        "scorecard_update_allowed": False,
        "report_path": path,
        "report_sha256": _sha256(raw),
    }
    return b"PHASE3_COMPONENT_REPORT_JSON=" + canonical_json_bytes(envelope) + b"\n"


def run_f9_estimator_truth_gate(
    spec: F9EstimatorTruthGateInput,
    *,
    input_digest: str,
) -> tuple[F9EstimatorTruthGateReport, str]:
    if type(spec) is not F9EstimatorTruthGateInput:
        raise TypeError("spec must be an exact F9EstimatorTruthGateInput")
    _digest(input_digest)
    f8 = _read_required_f8(spec.f8_report)
    if (
        f8.report_id != spec.expected_f8_report_id
        or f8.target != spec.expected_target
        or f8.identities != spec.expected_identities
    ):
        raise F9EstimatorTruthGateError(
            "required F8 report does not join the pinned F9 lineage"
        )
    if _timestamp(f8.completed_at) < _timestamp(spec.f8_evidence_not_before):
        raise F9EstimatorTruthGateError("required F8 evidence is stale")
    _validate_grpo_evidence(f8)

    ppo_supplied = spec.ppo_trainer_report is not None
    report = F9EstimatorTruthGateReport(
        schema_version="bb.rl.phase5-f9-estimator-truth-gate-report.v2",
        component=_COMPONENT,
        report_id=f"f9-estimator-truth-{f8.target.target_run_id}",
        passed=True,
        input_digest=input_digest,
        f8_report=spec.f8_report,
        f8_report_id=f8.report_id,
        f8_completed_at=f8.completed_at,
        f8_target_source_report=F9ImmutableJSONRef(
            path=f8.target_source_report.path,
            digest=f8.target_source_report.digest,
        ),
        target=f8.target,
        identities=f8.identities,
        input_hashes=f8.input_hashes,
        episode_joins=f8.episode_joins,
        grpo_algorithm_adv_estimator="grpo",
        grpo_rollout_n=8,
        grouped_multi_sample_evidence=True,
        learning_evidence=f8.learning_evidence,
        evidence_join=f8.evidence_join,
        checkpoint_update=f8.checkpoint_update,
        estimator_mode="GRPO",
        estimator_label=_GRPO_LABEL,
        ppo_claim_allowed=False,
        critic_value_claim_allowed=False,
        gae_returns_claim_allowed=False,
        estimator_claim=_GRPO_CLAIM,
        disposition="DISABLED_WITH_REQUIRED_NONCLAIM",
        ppo_evidence_status=(
            "DISABLED_NO_APPROVED_EXTERNAL_TRUST_POLICY"
            if ppo_supplied
            else "NOT_SUPPLIED"
        ),
        ppo_evidence_supplied=ppo_supplied,
        ppo_trainer_report=spec.ppo_trainer_report,
        ppo_external_trust_policy_available=False,
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_authority=False,
        scorecard_update_allowed=False,
    )
    path = _write_report(report, spec.output_dir)
    os.write(1, _component_line(report, path))
    return report, path


def _read_input(path: str) -> tuple[F9EstimatorTruthGateInput, str]:
    raw = Path(path).resolve(strict=True).read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F9EstimatorTruthGateError("F9 input is not canonical JSON")
    return F9EstimatorTruthGateInput.model_validate_json(raw, strict=True), _sha256(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the strict F9 estimator-truth evidence gate"
    )
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    spec, input_digest = _read_input(args.input)
    run_f9_estimator_truth_gate(spec, input_digest=input_digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
