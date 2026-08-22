from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from breadboard.rl.phase5.evidence import (
    ACTIVE_POINTER_ERROR,
    EVIDENCE_CYCLE_ERROR,
    EVIDENCE_NOT_CURRENT_ERROR,
    INELIGIBLE_SUPPORT_ERROR,
    OBSERVED_REQUIRED_ERROR,
    TAMPERED_EVIDENCE_ERROR,
    EvidenceEligibilityError,
    EvidenceGraph,
    validate_support_eligibility,
)
from breadboard.rl.phase5.models import (
    AUTHORITY_SYNTHESIS_ERROR,
    ActiveStatusPointer,
    AuthorityKind,
    AuthorityRecord,
    CampaignDisposition,
    ClaimState,
    EvidenceCard,
    EvidenceClass,
    EvidenceNode,
    EvidenceNodeKind,
    EvidenceState,
    ScoreItemState,
    SupportLevel,
    require_explicit_authority,
)
from breadboard.rl.phase5.score import ScoreDecision


NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
SHA256_A = "sha256:" + "a" * 64
SHA256_B = "sha256:" + "b" * 64


def _card(
    evidence_id: str,
    *,
    evidence_class: EvidenceClass = EvidenceClass.LOCAL_CONTRACT_TEST,
    support_level: SupportLevel = SupportLevel.OBSERVED,
    state: EvidenceState = EvidenceState.CURRENT,
    independent_verification_ids: tuple[str, ...] = ("integrity-root",),
    derivation_code_hash: str | None = None,
    derivation_version: str | None = None,
) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=evidence_id,
        evidence_class=evidence_class,
        support_level=support_level,
        state=state,
        proof_floor="local contract",
        artifact_uri=f"artifacts/{evidence_id}.json",
        artifact_sha256=SHA256_A,
        artifact_size=1,
        observed_at=NOW,
        claims=(f"claim:{evidence_id}",),
        non_claims=("external acceptance",),
        independent_verification_ids=independent_verification_ids,
        derivation_code_hash=derivation_code_hash,
        derivation_version=derivation_version,
    )


def _integrity_root() -> EvidenceCard:
    return _card(
        "integrity-root",
        evidence_class=EvidenceClass.ARTIFACT_INTEGRITY,
        independent_verification_ids=(),
    )


def _node(
    node_id: str,
    node_kind: EvidenceNodeKind,
    *dependencies: str,
) -> EvidenceNode:
    evidence_id = (
        node_id
        if node_kind in {EvidenceNodeKind.EVIDENCE, EvidenceNodeKind.REVIEW}
        else None
    )
    return EvidenceNode(
        node_id=node_id,
        evidence_id=evidence_id,
        node_kind=node_kind,
        dependencies=dependencies,
    )


def _graph(nodes: list[EvidenceNode]) -> EvidenceGraph:
    return EvidenceGraph(
        nodes=nodes,
        active_pointers=[
            ActiveStatusPointer(
                pointer_id="active",
                target_node_id="status",
                activated_at=NOW,
            )
        ],
    )


def _authority(**overrides: object) -> AuthorityRecord:
    fields: dict[str, object] = {
        "record_id": "authority-1",
        "kind": AuthorityKind.AUTHORITY_DECISION,
        "actor_identity": "person@example.test",
        "actor_role": "external-acceptance-authority",
        "scope": ("campaign:phase5", "checkpoint:sha256:abc"),
        "artifact_hashes": (SHA256_A, SHA256_B),
        "authority_artifact_uri": "authority/authority-1.json",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(days=1),
    }
    fields.update(overrides)
    return AuthorityRecord(**fields)


def test_taxonomy_enums_are_closed_and_match_the_frozen_playbook() -> None:
    assert {member.value for member in EvidenceClass} == {
        "source_reference",
        "fixture_oracle",
        "compile_artifact",
        "admission_decision",
        "local_contract_test",
        "local_process_integration",
        "local_container_attestation",
        "target_slurm_command",
        "target_episode_lifecycle",
        "target_training_run",
        "digitalocean_provider_run",
        "artifact_integrity",
        "replay_reproduction",
        "negative_control",
        "cleanup_rollback",
        "blocker",
        "review_verdict",
        "authority_decision",
    }
    assert {member.value for member in SupportLevel} == {
        "observed",
        "derived_deterministically",
        "inferred",
        "worker_claim",
        "unverified",
        "contradicted",
    }
    assert {member.value for member in EvidenceState} == {
        "current",
        "historical",
        "stale",
        "superseded",
        "invalid",
        "revoked",
        "quarantined",
    }
    assert {member.value for member in CampaignDisposition} == {
        "PASSED",
        "FAILED",
        "NOT_TRIGGERED",
        "INFEASIBLE_WITH_REQUIRED_NONCLAIM",
        "DISABLED_WITH_REQUIRED_NONCLAIM",
        "WAITING_EXTERNAL",
    }
    assert {member.value for member in ClaimState} == {
        "unclaimed",
        "pending",
        "supported",
        "rejected",
        "revoked",
    }
    assert {member.value for member in ScoreItemState} == {
        "pending",
        "awarded",
        "blocked",
        "failed",
        "deferred",
        "stale",
        "superseded",
        "revoked",
        "quarantined",
    }

    invalid = _card("unknown-class").model_dump()
    invalid["evidence_class"] = "worker_prose"
    with pytest.raises(ValidationError):
        EvidenceCard(**invalid)


def test_support_eligibility_accepts_verified_observed_and_pinned_derivation() -> None:
    root = _integrity_root()
    observed = _card("observed")
    derived = _card(
        "derived",
        support_level=SupportLevel.DERIVED_DETERMINISTICALLY,
        independent_verification_ids=("observed",),
        derivation_code_hash=SHA256_B,
        derivation_version="phase5-deriver-v1",
    )

    validate_support_eligibility([root, observed, derived], requires_observed=False)
    validate_support_eligibility([root, observed], requires_observed=True)

    with pytest.raises(EvidenceEligibilityError, match="^observed evidence is required: derived$"):
        validate_support_eligibility([root, observed, derived], requires_observed=True)


@pytest.mark.parametrize(
    "support_level",
    [
        SupportLevel.INFERRED,
        SupportLevel.WORKER_CLAIM,
        SupportLevel.UNVERIFIED,
        SupportLevel.CONTRADICTED,
    ],
)
def test_weak_support_can_never_become_point_eligible(
    support_level: SupportLevel,
) -> None:
    root = _integrity_root()
    card = _card("weak", support_level=support_level)

    with pytest.raises(
        EvidenceEligibilityError,
        match="^evidence support level is not point-eligible: weak$",
    ):
        validate_support_eligibility([root, card], requires_observed=False)


def test_support_eligibility_rejects_stale_and_unverified_inputs() -> None:
    root = _integrity_root()
    stale = _card("stale", state=EvidenceState.STALE)
    with pytest.raises(EvidenceEligibilityError, match="^evidence is not current: stale$"):
        validate_support_eligibility([root, stale], requires_observed=False)

    observed = _card("observed", independent_verification_ids=("missing",))
    with pytest.raises(
        EvidenceEligibilityError,
        match="^observed evidence requires independent current observed verification: observed$",
    ):
        validate_support_eligibility([root, observed], requires_observed=False)

    derived = _card(
        "derived",
        support_level=SupportLevel.DERIVED_DETERMINISTICALLY,
        independent_verification_ids=("missing",),
        derivation_code_hash=SHA256_B,
        derivation_version="phase5-deriver-v1",
    )
    with pytest.raises(
        EvidenceEligibilityError,
        match="^derived evidence requires current observed inputs: derived$",
    ):
        validate_support_eligibility([root, derived], requires_observed=False)


def test_graph_rejects_unknown_dependencies_and_cycles() -> None:
    status = _node("status", EvidenceNodeKind.STATUS)

    with pytest.raises(
        ValueError,
        match="^evidence dependency references unknown node: missing$",
    ):
        _graph([status, _node("claim", EvidenceNodeKind.CLAIM, "missing")])

    with pytest.raises(ValueError, match=f"^{EVIDENCE_CYCLE_ERROR}$"):
        _graph(
            [
                status,
                _node("first", EvidenceNodeKind.EVIDENCE, "second"),
                _node("second", EvidenceNodeKind.REVIEW, "first"),
            ]
        )


def test_graph_requires_exactly_one_active_status_pointer() -> None:
    status = _node("status", EvidenceNodeKind.STATUS)

    with pytest.raises(ValueError, match=f"^{ACTIVE_POINTER_ERROR}$"):
        EvidenceGraph(nodes=[status], active_pointers=[])
    with pytest.raises(ValueError, match=f"^{ACTIVE_POINTER_ERROR}$"):
        EvidenceGraph(
            nodes=[status],
            active_pointers=[
                ActiveStatusPointer(
                    pointer_id="first", target_node_id="status", activated_at=NOW
                ),
                ActiveStatusPointer(
                    pointer_id="second", target_node_id="status", activated_at=NOW
                ),
            ],
        )
    with pytest.raises(
        ValueError,
        match="^active-status pointer target must be a status node$",
    ):
        EvidenceGraph(
            nodes=[status, _node("review", EvidenceNodeKind.REVIEW)],
            active_pointers=[
                ActiveStatusPointer(
                    pointer_id="wrong", target_node_id="review", activated_at=NOW
                )
            ],
        )


@pytest.mark.parametrize(
    "invalid_state",
    [EvidenceState.STALE, EvidenceState.INVALID, EvidenceState.REVOKED],
)
def test_invalidation_propagates_through_review_point_and_promotion(
    invalid_state: EvidenceState,
) -> None:
    graph = _graph(
        [
            _node("status", EvidenceNodeKind.STATUS),
            _node("source", EvidenceNodeKind.EVIDENCE),
            _node("claim", EvidenceNodeKind.CLAIM, "source"),
            _node("review", EvidenceNodeKind.REVIEW, "claim"),
            _node("point", EvidenceNodeKind.POINT, "review"),
            _node("promotion", EvidenceNodeKind.PROMOTION, "point"),
            _node("unrelated", EvidenceNodeKind.EVIDENCE),
        ]
    )

    affected = graph.invalidate("source", invalid_state)
    states = graph.effective_states()

    assert affected == frozenset({"source", "claim", "review", "point", "promotion"})
    assert {states[node_id] for node_id in affected} == {invalid_state}
    assert states["unrelated"] is EvidenceState.CURRENT
    assert states["status"] is EvidenceState.CURRENT


def test_tamper_detection_invalidates_all_transitive_dependents() -> None:
    card = _card("source")
    graph = _graph(
        [
            _node("status", EvidenceNodeKind.STATUS),
            _node("source", EvidenceNodeKind.EVIDENCE),
            _node("review", EvidenceNodeKind.REVIEW, "source"),
            _node("point", EvidenceNodeKind.POINT, "review"),
            _node("promotion", EvidenceNodeKind.PROMOTION, "point"),
        ]
    )

    with pytest.raises(
        ValueError,
        match=f"^{TAMPERED_EVIDENCE_ERROR}: source$",
    ):
        graph.invalidate_tamper("source", card, b"changed artifact bytes")

    states = graph.effective_states()
    assert states["source"] is EvidenceState.INVALID
    assert {states[node_id] for node_id in ("review", "point", "promotion")} == {
        EvidenceState.REVOKED
    }


def test_failed_rerun_supersedes_prior_success_and_invalidates_dependents() -> None:
    graph = _graph(
        [
            _node("status", EvidenceNodeKind.STATUS),
            _node("successful-run", EvidenceNodeKind.EVIDENCE),
            _node("review", EvidenceNodeKind.REVIEW, "successful-run"),
            _node("point", EvidenceNodeKind.POINT, "review"),
            _node("promotion", EvidenceNodeKind.PROMOTION, "point"),
        ]
    )

    affected = graph.invalidate_failed_rerun("successful-run")
    states = graph.effective_states()

    assert affected == frozenset({"successful-run", "review", "point", "promotion"})
    assert states["successful-run"] is EvidenceState.SUPERSEDED
    assert states["review"] is EvidenceState.REVOKED
    assert states["point"] is EvidenceState.REVOKED
    assert states["promotion"] is EvidenceState.REVOKED


def test_authority_record_requires_typed_scope_hashes_and_future_expiry() -> None:
    authority = _authority()

    assert authority.scope == ("campaign:phase5", "checkpoint:sha256:abc")
    assert authority.artifact_hashes == (SHA256_A, SHA256_B)
    assert authority.expires_at > authority.issued_at
    assert authority.revocable is True

    with pytest.raises(ValidationError):
        _authority(scope=())
    with pytest.raises(ValidationError):
        _authority(scope=("",))
    with pytest.raises(ValidationError):
        _authority(artifact_hashes=("sha256:" + "A" * 64,))
    with pytest.raises(
        ValidationError,
        match="expires_at must be later than issued_at",
    ):
        _authority(expires_at=NOW)


def test_campaign_signals_cannot_synthesize_authority() -> None:
    signals = (
        ScoreDecision(
            item_id="H3",
            state=ScoreItemState.AWARDED,
            evidence_ids=("evidence:H3",),
            review_ids=("review:H3",),
            supervisor_decision_id="supervisor:H3",
        ),
        {"review": "approved"},
        {"job": {"state": "COMPLETED"}},
        {"issue": {"state": "closed"}},
        {"evidence": {"state": "current"}},
    )
    for signal in signals:
        with pytest.raises(ValueError, match=f"^{AUTHORITY_SYNTHESIS_ERROR}$"):
            require_explicit_authority(signal)
        with pytest.raises(ValueError, match=f"^{AUTHORITY_SYNTHESIS_ERROR}$"):
            AuthorityRecord.from_campaign_signal(signal)

    authority = _authority()
    assert require_explicit_authority(authority, at=NOW) is authority

    with pytest.raises(ValidationError):
        _authority(score=1000)
