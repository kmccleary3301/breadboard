from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


CLAIM_BOUNDARY = "p2_m3_closed_loop_prototype_not_production_rl_claim"


def _stable_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def stable_sha256(data: Mapping[str, Any]) -> str:
    return sha256(_stable_json(data).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PolicySnapshotIdentity:
    policy_name: str
    checkpoint_ref: str
    parameter_sha256: str
    trainer_name: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def snapshot_id(self) -> str:
        return "policy_snapshot:" + stable_sha256(
            {
                "checkpoint_ref": self.checkpoint_ref,
                "created_at": self.created_at,
                "parameter_sha256": self.parameter_sha256,
                "policy_name": self.policy_name,
                "trainer_name": self.trainer_name,
            }
        )[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_snapshot_id": self.snapshot_id,
            "policy_name": self.policy_name,
            "checkpoint_ref": self.checkpoint_ref,
            "parameter_sha256": self.parameter_sha256,
            "trainer_name": self.trainer_name,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RolloutGenerationEntry:
    rollout_id: str
    policy_snapshot_id: str
    env_package_id: str
    task_id: str
    trajectory_ref: str
    projection_ref: str
    generated_at: str
    target_run_id: str
    attempt_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollout_id": self.rollout_id,
            "policy_snapshot_id": self.policy_snapshot_id,
            "env_package_id": self.env_package_id,
            "task_id": self.task_id,
            "trajectory_ref": self.trajectory_ref,
            "projection_ref": self.projection_ref,
            "generated_at": self.generated_at,
            "target_run_id": self.target_run_id,
            "attempt_index": self.attempt_index,
        }


@dataclass(frozen=True)
class RewardVerifierEvidence:
    evidence_id: str
    rollout_id: str
    verifier_id: str
    verifier_version: str
    reward_scalar: float
    verifier_evidence_hash: str
    passed: bool
    failure_taxonomy: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "rollout_id": self.rollout_id,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "reward_scalar": self.reward_scalar,
            "verifier_evidence_hash": self.verifier_evidence_hash,
            "passed": self.passed,
            "failure_taxonomy": self.failure_taxonomy,
        }


@dataclass(frozen=True)
class AdmissionDecision:
    decision_id: str
    rollout_id: str
    accepted: bool
    reasons: list[str]
    decided_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "rollout_id": self.rollout_id,
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "decided_at": self.decided_at,
        }


@dataclass(frozen=True)
class TrainerHandoff:
    handoff_id: str
    admitted_rollout_ids: list[str]
    trainer_name: str
    trainer_projection_hash: str
    target_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "admitted_rollout_ids": list(self.admitted_rollout_ids),
            "trainer_name": self.trainer_name,
            "trainer_projection_hash": self.trainer_projection_hash,
            "target_run_id": self.target_run_id,
        }


@dataclass(frozen=True)
class ReplayClosure:
    closure_id: str
    rollout_id: str
    replay_status: str
    admission_accepted: bool
    replay_ref: str
    verifier_evidence_id: str
    trainer_handoff_id: str | None
    target_run_id: str
    claim_boundary: str = CLAIM_BOUNDARY
    scorecard_update_allowed: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "closure_id": self.closure_id,
            "rollout_id": self.rollout_id,
            "replay_status": self.replay_status,
            "admission_accepted": self.admission_accepted,
            "replay_ref": self.replay_ref,
            "verifier_evidence_id": self.verifier_evidence_id,
            "trainer_handoff_id": self.trainer_handoff_id,
            "target_run_id": self.target_run_id,
            "claim_boundary": self.claim_boundary,
            "scorecard_update_allowed": self.scorecard_update_allowed,
            "errors": list(self.errors),
        }


def make_rollout_id(policy_snapshot_id: str, env_package_id: str, task_id: str, attempt_index: int) -> str:
    digest = stable_sha256(
        {
            "attempt_index": attempt_index,
            "env_package_id": env_package_id,
            "policy_snapshot_id": policy_snapshot_id,
            "task_id": task_id,
        }
    )[:16]
    return "rollout:" + digest


def close_replay(
    rollout: RolloutGenerationEntry,
    evidence: RewardVerifierEvidence,
    admission: AdmissionDecision,
    *,
    replay_ref: str,
    trainer_handoff: TrainerHandoff | None = None,
) -> ReplayClosure:
    errors: list[str] = []
    if evidence.rollout_id != rollout.rollout_id:
        errors.append("evidence_rollout_mismatch")
    if admission.rollout_id != rollout.rollout_id:
        errors.append("admission_rollout_mismatch")
    if admission.accepted:
        if not evidence.passed:
            errors.append("accepted_rollout_failed_verifier")
        if trainer_handoff is None:
            errors.append("accepted_rollout_missing_trainer_handoff")
        elif rollout.rollout_id not in trainer_handoff.admitted_rollout_ids:
            errors.append("accepted_rollout_absent_from_trainer_handoff")
    elif trainer_handoff is not None and rollout.rollout_id in trainer_handoff.admitted_rollout_ids:
        errors.append("rejected_rollout_present_in_trainer_handoff")

    if errors:
        replay_status = "replay_closure_invalid"
    elif admission.accepted:
        replay_status = "accepted_replay_closed"
    else:
        replay_status = "rejected_replay_closed"

    trainer_handoff_id = trainer_handoff.handoff_id if trainer_handoff is not None else None
    closure_id = "replay_closure:" + stable_sha256(
        {
            "admission_accepted": admission.accepted,
            "replay_ref": replay_ref,
            "rollout_id": rollout.rollout_id,
            "trainer_handoff_id": trainer_handoff_id,
            "verifier_evidence_id": evidence.evidence_id,
        }
    )[:16]
    return ReplayClosure(
        closure_id=closure_id,
        rollout_id=rollout.rollout_id,
        replay_status=replay_status,
        admission_accepted=admission.accepted,
        replay_ref=replay_ref,
        verifier_evidence_id=evidence.evidence_id,
        trainer_handoff_id=trainer_handoff_id,
        target_run_id=rollout.target_run_id,
        errors=errors,
    )


def build_closed_loop_fixture_ledger(target_run_id: str = "fixture-phase2-closed-loop") -> dict[str, Any]:
    snapshot = PolicySnapshotIdentity(
        policy_name="breadboard-swe-tiny-policy",
        checkpoint_ref="fixtures/policies/swe_tiny/checkpoint-0001",
        parameter_sha256="0" * 64,
        trainer_name="verl.fixture.off_policy",
        created_at="2026-06-18T00:00:00Z",
        metadata={"fixture_scope": "deterministic_closed_loop"},
    )
    accepted_rollout = RolloutGenerationEntry(
        rollout_id=make_rollout_id(snapshot.snapshot_id, "swe_toy_patch", "accepted-task", 0),
        policy_snapshot_id=snapshot.snapshot_id,
        env_package_id="swe_toy_patch",
        task_id="accepted-task",
        trajectory_ref="cas://trajectory/accepted-task",
        projection_ref="cas://projection/accepted-task",
        generated_at="2026-06-18T00:01:00Z",
        target_run_id=target_run_id,
    )
    rejected_rollout = RolloutGenerationEntry(
        rollout_id=make_rollout_id(snapshot.snapshot_id, "swe_toy_patch", "rejected-task", 0),
        policy_snapshot_id=snapshot.snapshot_id,
        env_package_id="swe_toy_patch",
        task_id="rejected-task",
        trajectory_ref="cas://trajectory/rejected-task",
        projection_ref="cas://projection/rejected-task",
        generated_at="2026-06-18T00:02:00Z",
        target_run_id=target_run_id,
    )
    accepted_evidence = RewardVerifierEvidence(
        evidence_id="evidence:accepted-task",
        rollout_id=accepted_rollout.rollout_id,
        verifier_id="swe_toy_verifier",
        verifier_version="fixture-v1",
        reward_scalar=1.0,
        verifier_evidence_hash="1" * 64,
        passed=True,
    )
    rejected_evidence = RewardVerifierEvidence(
        evidence_id="evidence:rejected-task",
        rollout_id=rejected_rollout.rollout_id,
        verifier_id="swe_toy_verifier",
        verifier_version="fixture-v1",
        reward_scalar=0.0,
        verifier_evidence_hash="2" * 64,
        passed=False,
        failure_taxonomy="verifier_failure",
    )
    accepted_admission = AdmissionDecision(
        decision_id="admission:accepted-task",
        rollout_id=accepted_rollout.rollout_id,
        accepted=True,
        reasons=["verifier_passed", "projection_schema_valid", "replay_ref_present"],
        decided_at="2026-06-18T00:03:00Z",
    )
    rejected_admission = AdmissionDecision(
        decision_id="admission:rejected-task",
        rollout_id=rejected_rollout.rollout_id,
        accepted=False,
        reasons=["verifier_failed"],
        decided_at="2026-06-18T00:04:00Z",
    )
    handoff = TrainerHandoff(
        handoff_id="trainer_handoff:accepted-only",
        admitted_rollout_ids=[accepted_rollout.rollout_id],
        trainer_name="verl.fixture.off_policy",
        trainer_projection_hash="3" * 64,
        target_run_id=target_run_id,
    )
    closures = [
        close_replay(
            accepted_rollout,
            accepted_evidence,
            accepted_admission,
            replay_ref="cas://replay/accepted-task",
            trainer_handoff=handoff,
        ),
        close_replay(
            rejected_rollout,
            rejected_evidence,
            rejected_admission,
            replay_ref="cas://replay/rejected-task",
        ),
    ]
    return {
        "report_id": "bb_zyphra_rl_phase2_closed_loop_v1",
        "target_run_id": target_run_id,
        "claim_boundary": CLAIM_BOUNDARY,
        "scorecard_update_allowed": False,
        "passed": all(closure.replay_status != "replay_closure_invalid" for closure in closures),
        "policy_snapshot": snapshot.to_dict(),
        "rollouts": [accepted_rollout.to_dict(), rejected_rollout.to_dict()],
        "reward_verifier_evidence": [accepted_evidence.to_dict(), rejected_evidence.to_dict()],
        "admission_decisions": [accepted_admission.to_dict(), rejected_admission.to_dict()],
        "trainer_handoffs": [handoff.to_dict()],
        "replay_closures": [closure.to_dict() for closure in closures],
    }


def replay_closure_errors(closures: Sequence[ReplayClosure]) -> list[str]:
    errors: list[str] = []
    for closure in closures:
        errors.extend(closure.errors)
    return errors
