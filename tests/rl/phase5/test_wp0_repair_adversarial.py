from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from breadboard.rl.phase5.bootstrap import bootstrap_campaign
from breadboard.rl.phase5.evidence import EvidenceGraph
from breadboard.rl.phase5.models import (
    ActiveStatusPointer,
    AuthorityKind,
    AuthorityRecord,
    AuthorityRevocation,
    BlockerFailureClass,
    BlockerKind,
    BlockerRecord,
    BlockerState,
    ClaimRecord,
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
from breadboard.rl.phase5.score import (
    ScoreDecision,
    ScoreEngine,
    ScoreEvaluation,
    ScoreItem,
    parse_score_catalog,
    validate_fixed_catalog,
)


SPEC_ROOT = Path("/Users/kylemccleary/projects/breadboard/docs_tmp/ZYPHRA/RL_PHASE_5")
PLAYBOOK = SPEC_ROOT / "BB_Z_RL_PHASE_5_CONFIG_NATIVE_EXECUTION_AND_OPTIMIZATION_PLAYBOOK.md"
GOAL_PROMPT = SPEC_ROOT / "phase5_config_native_1000_goal_prompt.txt"
GENERATED_AT = "2026-07-09T12:00:00Z"
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
SHA256_A = "sha256:" + "a" * 64
SHA256_B = "sha256:" + "b" * 64
BREADBOARD_PAYLOAD_SHA256 = (
    "sha256:f9a6f160c0a523c5ccd3f345c5de75c195430e021f7c4db3c834f5d64eeb644c"
)
WRAPPER_PAYLOAD_SHA256 = (
    "sha256:479e5d98dd581e53dcd1a2542951fd0c753ea6f6b37794c8738cb7bd066d4d63"
)


def _catalog() -> tuple[ScoreItem, ...]:
    return parse_score_catalog(PLAYBOOK)


def _bootstrap(output_dir: Path, *, generated_at: str = GENERATED_AT):
    return bootstrap_campaign(
        playbook_path=PLAYBOOK,
        goal_prompt_path=GOAL_PROMPT,
        output_dir=output_dir,
        generated_at=generated_at,
    )


def _bytes_by_name(output_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(output_dir).as_posix(): path.read_bytes()
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }


def _score_row(text: str, item_id: str) -> str:
    prefix = f"- [ ] **{item_id} —"
    return next(line for line in text.splitlines() if line.startswith(prefix))


def _authority(
    record_id: str,
    *,
    issued_at: datetime = NOW - timedelta(minutes=1),
    expires_at: datetime = NOW + timedelta(minutes=1),
    revocable: bool = True,
    scope: tuple[str, ...] = ("score-item:A1",),
    artifact_hashes: tuple[str, ...] = (SHA256_A,),
) -> AuthorityRecord:
    return AuthorityRecord(
        record_id=record_id,
        kind=AuthorityKind.AUTHORITY_DECISION,
        actor_identity="supervisor@example.test",
        actor_role="phase5-supervisor",
        scope=scope,
        artifact_hashes=artifact_hashes,
        authority_artifact_uri=f"authority/{record_id}.json",
        issued_at=issued_at,
        expires_at=expires_at,
        revocable=revocable,
    )


def _evidence_card(
    evidence_id: str,
    *,
    evidence_class: EvidenceClass,
    proof_floor: str,
    state: EvidenceState = EvidenceState.CURRENT,
    verification_ids: tuple[str, ...] = (),
    artifact_sha256: str = SHA256_A,
    reviewed_artifact_hashes: tuple[str, ...] = (),
) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=evidence_id,
        evidence_class=evidence_class,
        support_level=SupportLevel.OBSERVED,
        state=state,
        proof_floor=proof_floor,
        artifact_uri=f"artifacts/{evidence_id}.json",
        artifact_sha256=artifact_sha256,
        artifact_size=1,
        observed_at=NOW,
        claims=(f"claim:{evidence_id}",),
        non_claims=("external acceptance",),
        independent_verification_ids=verification_ids,
        reviewed_artifact_hashes=reviewed_artifact_hashes,
    )


def _graph_for_cards(cards: tuple[EvidenceCard, ...]) -> EvidenceGraph:
    return EvidenceGraph(
        nodes=(
            EvidenceNode(node_id="status", node_kind=EvidenceNodeKind.STATUS),
            *(
                EvidenceNode(
                    node_id=f"node:{card.evidence_id}",
                    evidence_id=card.evidence_id,
                    node_kind=(
                        EvidenceNodeKind.REVIEW
                        if card.evidence_class is EvidenceClass.REVIEW_VERDICT
                        else EvidenceNodeKind.EVIDENCE
                    ),
                )
                for card in cards
            ),
        ),
        active_pointers=(
            ActiveStatusPointer(
                pointer_id="active",
                target_node_id="status",
                activated_at=NOW,
            ),
        ),
    )


@pytest.mark.parametrize("mutation", ["malformed", "extra", "substituted"])
def test_score_parser_rejects_every_noncanonical_checkbox_candidate(mutation: str) -> None:
    text = PLAYBOOK.read_text(encoding="utf-8")
    a1 = _score_row(text, "A1")
    substituted = a1.replace("**A1 —", "**A6 —", 1)
    if mutation == "malformed":
        malformed = a1.replace("**A1 — 15 —", "**A1 — fifteen —", 1)
        changed = text.replace(a1, f"{malformed}\n{substituted}", 1)
    elif mutation == "extra":
        extra = substituted.replace("— 15 —", "— fifteen —", 1)
        changed = text.replace(a1, f"{a1}\n{extra}", 1)
    else:
        changed = text.replace(a1, substituted, 1)

    with pytest.raises(ValueError):
        parse_score_catalog(changed)


def test_fixed_catalog_validator_pins_exact_ids_and_full_row_digest() -> None:
    catalog = list(_catalog())
    a1 = catalog[0]
    substituted_id = ScoreItem(
        item_id="A6",
        points=a1.points,
        workstream=a1.workstream,
        proof_floor=a1.proof_floor,
        description=a1.description,
        pass_predicate=a1.pass_predicate,
        owner_packet=a1.owner_packet,
    )
    changed_predicate = a1.model_copy(
        update={"pass_predicate": f"{a1.pass_predicate} silently weakened"}
    )

    for replacement in (substituted_id, changed_predicate):
        mutated = (replacement, *catalog[1:])
        with pytest.raises(ValueError):
            validate_fixed_catalog(mutated)


@pytest.mark.parametrize("case", ["duplicate", "incomplete"])
def test_direct_score_evaluation_requires_the_complete_unique_fixed_catalog(
    case: str,
) -> None:
    catalog = _catalog()
    decisions = [ScoreDecision(item_id=item.item_id) for item in catalog]
    if case == "duplicate":
        decisions[-1] = ScoreDecision(item_id="A1")
    else:
        decisions.pop()

    with pytest.raises(ValidationError):
        ScoreEvaluation(catalog=catalog, decisions=decisions)


def test_direct_score_evaluation_cannot_bypass_award_validation() -> None:
    catalog = _catalog()
    decisions = [ScoreDecision(item_id=item.item_id) for item in catalog]
    decisions[0] = ScoreDecision(
        item_id="A1",
        state=ScoreItemState.AWARDED,
        evidence_ids=("arbitrary-evidence",),
        review_ids=("arbitrary-review",),
        supervisor_decision_id="arbitrary-supervisor",
    )

    with pytest.raises(ValidationError):
        ScoreEvaluation(catalog=catalog, decisions=decisions)


def test_pending_evaluation_cannot_be_copied_or_constructed_as_awarded() -> None:
    pending = ScoreEngine(_catalog()).evaluate(())
    awarded_decisions = list(pending.decisions)
    awarded_decisions[0] = ScoreDecision(
        item_id="A1",
        state=ScoreItemState.AWARDED,
        evidence_ids=("arbitrary-evidence",),
        review_ids=("arbitrary-review",),
        supervisor_decision_id="arbitrary-supervisor",
    )
    update = {"decisions": tuple(awarded_decisions)}

    with pytest.raises((TypeError, ValueError)):
        pending.model_copy(update=update)
    with pytest.raises((TypeError, ValueError)):
        pending.copy(update=update)
    with pytest.raises((TypeError, ValueError)):
        pending.copy(include={"catalog", "decisions"}, update=update)
    with pytest.raises((TypeError, ValueError)):
        ScoreEvaluation.model_construct(catalog=pending.catalog, **update)
    with pytest.raises(ValidationError):
        ScoreEvaluation.model_validate(
            {"catalog": pending.catalog, "decisions": update["decisions"]}
        )


def test_bootstrap_records_canonical_payload_digests_separately_from_heads(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    scorecard = json.loads((output_dir / "SCORECARD.json").read_text(encoding="utf-8"))
    frozen = scorecard["frozen_hashes"]

    assert frozen["breadboard_baseline"]["head"] == (
        "550a387706d4ca4bc49760070f55a58100af168e"
    )
    assert frozen["breadboard_baseline"]["canonical_payload_sha256"] == BREADBOARD_PAYLOAD_SHA256
    assert frozen["wrapper_baseline"]["head"] == (
        "d5221607f59ea05ffeba1e2931eff12142d9504d"
    )
    assert frozen["wrapper_baseline"]["canonical_payload_sha256"] == WRAPPER_PAYLOAD_SHA256
    assert frozen["breadboard_baseline"]["canonical_payload_sha256"] != (
        "sha256:" + hashlib.sha256(frozen["breadboard_baseline"]["head"].encode()).hexdigest()
    )
    assert frozen["wrapper_baseline"]["canonical_payload_sha256"] != (
        "sha256:" + hashlib.sha256(frozen["wrapper_baseline"]["head"].encode()).hexdigest()
    )


def test_identical_bootstrap_rerun_is_a_filesystem_noop(tmp_path: Path) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    old_ns = 946_684_800_000_000_000
    for path in output_dir.rglob("*"):
        if path.is_file():
            os.utime(path, ns=(old_ns, old_ns))
    before = {
        path.relative_to(output_dir).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }

    _bootstrap(output_dir)

    after = {
        path.relative_to(output_dir).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_differing_bootstrap_rerun_cannot_replace_canonical_artifact_bytes(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    before = _bytes_by_name(output_dir)

    with pytest.raises(ValueError, match="immutable|differ|existing|canonical"):
        _bootstrap(output_dir, generated_at="2026-07-09T12:00:01Z")

    assert _bytes_by_name(output_dir) == before


@pytest.mark.parametrize("duplicate_kind", ["node_id", "evidence_id"])
def test_evidence_graph_rejects_duplicate_node_or_evidence_identity(
    duplicate_kind: str,
) -> None:
    duplicate = "node:a" if duplicate_kind == "node_id" else "node:b"
    second_evidence = "evidence:a" if duplicate_kind == "evidence_id" else "evidence:b"
    nodes = (
        EvidenceNode(node_id="status", node_kind=EvidenceNodeKind.STATUS),
        EvidenceNode(
            node_id="node:a",
            evidence_id="evidence:a",
            node_kind=EvidenceNodeKind.EVIDENCE,
        ),
        EvidenceNode(
            node_id=duplicate,
            evidence_id=second_evidence,
            node_kind=EvidenceNodeKind.EVIDENCE,
        ),
    )

    with pytest.raises(ValueError, match="unique|duplicate|exactly one"):
        EvidenceGraph(
            nodes=nodes,
            active_pointers=(
                ActiveStatusPointer(
                    pointer_id="active",
                    target_node_id="status",
                    activated_at=NOW,
                ),
            ),
        )


def test_tamper_invalidation_rejects_a_card_bound_to_another_node() -> None:
    source = _evidence_card(
        "evidence:source",
        evidence_class=EvidenceClass.LOCAL_CONTRACT_TEST,
        proof_floor="local contract",
    )
    other = source.model_copy(update={"evidence_id": "evidence:other"})
    graph = _graph_for_cards((source,))

    with pytest.raises(ValueError, match="bind|belong|mismatch"):
        graph.invalidate_tamper(
            "node:evidence:source",
            other,
            b"tampered bytes",
        )

    assert graph.effective_states()["node:evidence:source"] is EvidenceState.CURRENT


def test_invalidation_starts_at_the_unsupported_node_and_spares_unrelated_branches() -> None:
    graph = EvidenceGraph(
        nodes=(
            EvidenceNode(node_id="status", node_kind=EvidenceNodeKind.STATUS),
            EvidenceNode(
                node_id="evidence:a",
                evidence_id="card:a",
                node_kind=EvidenceNodeKind.EVIDENCE,
            ),
            EvidenceNode(
                node_id="review:a",
                evidence_id="card:review-a",
                node_kind=EvidenceNodeKind.REVIEW,
                dependencies=("evidence:a",),
            ),
            EvidenceNode(
                node_id="point:a",
                node_kind=EvidenceNodeKind.POINT,
                dependencies=("review:a",),
            ),
            EvidenceNode(
                node_id="evidence:b",
                evidence_id="card:b",
                node_kind=EvidenceNodeKind.EVIDENCE,
            ),
            EvidenceNode(
                node_id="review:b",
                evidence_id="card:review-b",
                node_kind=EvidenceNodeKind.REVIEW,
                dependencies=("evidence:b",),
            ),
        ),
        active_pointers=(
            ActiveStatusPointer(
                pointer_id="active",
                target_node_id="status",
                activated_at=NOW,
            ),
        ),
    )

    assert graph.invalidate("evidence:a") == frozenset(
        {"evidence:a", "review:a", "point:a"}
    )
    states = graph.effective_states()
    assert states["evidence:a"] is EvidenceState.STALE
    assert states["review:a"] is EvidenceState.STALE
    assert states["point:a"] is EvidenceState.STALE
    assert states["evidence:b"] is EvidenceState.CURRENT
    assert states["review:b"] is EvidenceState.CURRENT
    assert states["status"] is EvidenceState.CURRENT


def test_authority_requires_aware_revocable_expiring_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _authority(
            "naive-issued",
            issued_at=datetime(2026, 7, 9, 11, 59),
            expires_at=datetime(2026, 7, 9, 12, 1, tzinfo=timezone.utc),
        )
    with pytest.raises(ValidationError, match="timezone"):
        _authority(
            "naive-expiry",
            expires_at=datetime(2026, 7, 9, 12, 1),
        )
    with pytest.raises(ValidationError, match="revocable"):
        _authority("non-revocable", revocable=False)


@pytest.mark.parametrize("case", ["not_yet_issued", "expired", "revoked", "wrong_scope"])
def test_authority_consumption_rejects_every_noncurrent_decision(case: str) -> None:
    authority = _authority("supervisor:A1")
    at = NOW
    revocations: tuple[AuthorityRevocation, ...] = ()
    required_scope = ("score-item:A1",)
    if case == "not_yet_issued":
        at = authority.issued_at - timedelta(seconds=1)
    elif case == "expired":
        at = authority.expires_at
    elif case == "revoked":
        revocations = (
            AuthorityRevocation(
                revocation_id="revocation:supervisor:A1",
                target_record_id=authority.record_id,
                target_artifact_hash=SHA256_A,
                actor_identity="governance@example.test",
                actor_role="phase5-governance",
                reason="decision withdrawn",
                revocation_artifact_uri="authority/revocation-supervisor-A1.json",
                revocation_artifact_sha256=SHA256_A,
                revoked_at=NOW - timedelta(seconds=1),
            ),
        )
    else:
        required_scope = ("score-item:F8",)

    with pytest.raises(ValueError, match="not current|ineligible|non-revoked"):
        require_explicit_authority(
            authority,
            at=at,
            revocations=revocations,
            required_scope=required_scope,
            required_artifact_hashes=(SHA256_A,),
        )


def test_blocker_record_requires_typed_owner_wake_scope_and_state() -> None:
    valid: dict[str, object] = {
        "blocker_id": "blocker:ibm-token",
        "blocker_kind": BlockerKind.EXTERNAL,
        "failure_class": BlockerFailureClass.MISSING_TOKEN,
        "affected_packet_ids": ("WP13",),
        "affected_score_item_ids": ("D5", "F1"),
        "owner_identity": "ibm-operator@example.test",
        "wake_condition": "A current token-file lease is issued for the pinned target.",
        "next_action": "Verify the hash-bound token lease and rerun preflight.",
        "state": BlockerState.OPEN,
        "opened_at": NOW,
        "evidence_ids": ("evidence:missing-token",),
        "evidence_hashes": (SHA256_A,),
    }
    blocker = BlockerRecord(**valid)
    assert blocker.owner_identity == "ibm-operator@example.test"
    assert blocker.wake_condition
    assert blocker.affected_packet_ids == ("WP13",)
    assert blocker.state is BlockerState.OPEN

    invalid_records = []
    for field in ("owner_identity", "wake_condition", "state"):
        invalid = dict(valid)
        invalid.pop(field)
        invalid_records.append(invalid)
    no_scope = dict(valid, affected_packet_ids=(), affected_score_item_ids=())
    invalid_records.append(no_scope)
    woken_without_artifact = dict(valid, state=BlockerState.WOKEN)
    invalid_records.append(woken_without_artifact)

    for invalid in invalid_records:
        with pytest.raises(ValidationError):
            BlockerRecord(**invalid)


def test_claim_record_requires_an_explicit_claim_nonclaim_pair() -> None:
    claim = ClaimRecord(
        claim_id="claim:A1",
        subject="score-item:A1",
        claim_state=ClaimState.UNCLAIMED,
        claim="A1 governance is supported",
        non_claims=("external acceptance", "promotion authority"),
        proof_floor="governance",
    )
    assert claim.claim_state is ClaimState.UNCLAIMED
    assert claim.non_claims == ("external acceptance", "promotion authority")

    with pytest.raises(ValidationError):
        ClaimRecord(
            claim_id="claim:A1",
            subject="score-item:A1",
            claim_state=ClaimState.UNCLAIMED,
            claim="A1 governance is supported",
            non_claims=(),
            proof_floor="governance",
        )
    with pytest.raises(ValidationError, match="evidence|review"):
        ClaimRecord(
            claim_id="claim:A1",
            subject="score-item:A1",
            claim_state=ClaimState.SUPPORTED,
            claim="A1 governance is supported",
            non_claims=("external acceptance",),
            proof_floor="governance",
        )


def _f8_award_material() -> tuple[
    ScoreDecision,
    tuple[EvidenceCard, ...],
    EvidenceGraph,
    AuthorityRecord,
]:
    integrity = _evidence_card(
        "integrity:F8",
        evidence_class=EvidenceClass.ARTIFACT_INTEGRITY,
        proof_floor="governance",
        artifact_sha256=SHA256_A,
    )
    training = _evidence_card(
        "evidence:F8",
        evidence_class=EvidenceClass.TARGET_TRAINING_RUN,
        proof_floor="target training",
        verification_ids=(integrity.evidence_id,),
        artifact_sha256=SHA256_B,
    )
    review = _evidence_card(
        "review:F8",
        evidence_class=EvidenceClass.REVIEW_VERDICT,
        proof_floor="governance",
        reviewed_artifact_hashes=(SHA256_A, SHA256_B),
    )
    cards = (integrity, training, review)
    decision = ScoreDecision(
        item_id="F8",
        state=ScoreItemState.AWARDED,
        evidence_ids=(integrity.evidence_id, training.evidence_id),
        review_ids=(review.evidence_id,),
        supervisor_decision_id="supervisor:F8",
    )
    authority = _authority(
        "supervisor:F8",
        scope=("score-item:F8",),
        artifact_hashes=(SHA256_A, SHA256_B),
    )
    return decision, cards, _graph_for_cards(cards), authority


def test_score_engine_rejects_lower_floor_evidence_laundering() -> None:
    decision, cards, _graph, authority = _f8_award_material()
    integrity, _training, review = cards
    local_substitute = _evidence_card(
        "evidence:F8",
        evidence_class=EvidenceClass.LOCAL_CONTRACT_TEST,
        proof_floor="local contract",
        verification_ids=(integrity.evidence_id,),
        artifact_sha256=SHA256_B,
    )
    substituted = (integrity, local_substitute, review)

    with pytest.raises(ValueError, match="proof floor"):
        ScoreEngine(_catalog()).evaluate(
            (decision,),
            evidence_cards=substituted,
            evidence_graph=_graph_for_cards(substituted),
            supervisor_authorities=(authority,),
            evaluated_at=NOW,
        )


@pytest.mark.parametrize("review_case", ["fake_class", "stale", "wrong_hash"])
def test_score_engine_requires_a_current_hash_review_verdict(review_case: str) -> None:
    decision, cards, _graph, authority = _f8_award_material()
    integrity, training, review = cards
    if review_case == "fake_class":
        replacement = _evidence_card(
            review.evidence_id,
            evidence_class=EvidenceClass.ARTIFACT_INTEGRITY,
            proof_floor="governance",
        )
    elif review_case == "stale":
        replacement = review.model_copy(update={"state": EvidenceState.STALE})
    else:
        replacement = review.model_copy(
            update={"reviewed_artifact_hashes": (SHA256_A,)}
        )
    substituted = (integrity, training, replacement)

    with pytest.raises(ValueError, match="review"):
        ScoreEngine(_catalog()).evaluate(
            (decision,),
            evidence_cards=substituted,
            evidence_graph=_graph_for_cards(substituted),
            supervisor_authorities=(authority,),
            evaluated_at=NOW,
        )


@pytest.mark.parametrize("authority_case", ["absent", "stale", "expired", "revoked"])
def test_score_engine_requires_a_current_explicit_supervisor_authority(
    authority_case: str,
) -> None:
    decision, cards, graph, authority = _f8_award_material()
    authorities: tuple[AuthorityRecord, ...] = (authority,)
    revocations: tuple[AuthorityRevocation, ...] = ()
    if authority_case == "absent":
        authorities = ()
    elif authority_case == "stale":
        authorities = (
            _authority(
                authority.record_id,
                issued_at=NOW + timedelta(minutes=1),
                expires_at=NOW + timedelta(minutes=2),
                scope=("score-item:F8",),
                artifact_hashes=(SHA256_A, SHA256_B),
            ),
        )
    elif authority_case == "expired":
        authorities = (
            _authority(
                authority.record_id,
                issued_at=NOW - timedelta(minutes=2),
                expires_at=NOW - timedelta(minutes=1),
                scope=("score-item:F8",),
                artifact_hashes=(SHA256_A, SHA256_B),
            ),
        )
    else:
        revocations = (
            AuthorityRevocation(
                revocation_id="revocation:supervisor:F8",
                target_record_id=authority.record_id,
                target_artifact_hash=SHA256_A,
                actor_identity="governance@example.test",
                actor_role="phase5-governance",
                reason="score award withdrawn",
                revocation_artifact_uri="authority/revocation-supervisor-F8.json",
                revocation_artifact_sha256=SHA256_A,
                revoked_at=NOW - timedelta(seconds=1),
            ),
        )

    with pytest.raises(ValueError, match="supervisor|current|eligible|revoked"):
        ScoreEngine(_catalog()).evaluate(
            (decision,),
            evidence_cards=cards,
            evidence_graph=graph,
            supervisor_authorities=authorities,
            authority_revocations=revocations,
            evaluated_at=NOW,
        )


def test_bootstrap_manifest_hash_binds_every_selected_artifact(tmp_path: Path) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    active = json.loads((output_dir / "ACTIVE_STATUS.json").read_text(encoding="utf-8"))
    pointer = active["artifact_manifest_pointer"]
    assert pointer["filename"] == "ARTIFACT_MANIFEST.json"
    manifest_bytes = (output_dir / pointer["filename"]).read_bytes()
    assert pointer["sha256"] == "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()

    manifest = json.loads(manifest_bytes)
    entries = {entry["filename"]: entry for entry in manifest["artifacts"]}
    selected = {
        filename
        for filename in active["artifact_pointers"].values()
        if filename is not None
    }
    assert set(entries) == selected
    assert "ARTIFACT_MANIFEST.json" not in entries
    assert "ACTIVE_STATUS.json" not in entries
    for filename, entry in entries.items():
        artifact_bytes = (output_dir / filename).read_bytes()
        assert entry["media_type"]
        assert entry["size"] == len(artifact_bytes)
        assert entry["sha256"] == (
            "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
        )


def test_bootstrap_rejects_manifest_bound_child_tamper(tmp_path: Path) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    scorecard_path = output_dir / "SCORECARD.json"
    scorecard_path.write_bytes(scorecard_path.read_bytes() + b" ")
    tampered = _bytes_by_name(output_dir)

    with pytest.raises(ValueError, match="immutable|manifest|canonical|hash|differ"):
        _bootstrap(output_dir)

    assert _bytes_by_name(output_dir) == tampered


def test_bootstrap_materializes_every_bounded_packet_contract_before_admission(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    dag = json.loads((output_dir / "WORK_PACKET_DAG.yaml").read_text(encoding="utf-8"))
    required = {
        "files",
        "symbols",
        "non_goals",
        "evidence_contract",
        "rollback",
        "attempt_budget",
    }
    for packet in dag["packets"]:
        assert required <= packet.keys(), packet["packet_id"]
        assert packet["files"], packet["packet_id"]
        assert packet["symbols"], packet["packet_id"]
        assert packet["non_goals"], packet["packet_id"]
        assert packet["evidence_contract"], packet["packet_id"]
        assert packet["rollback"], packet["packet_id"]
        assert packet["attempt_budget"], packet["packet_id"]

    wp0 = next(packet for packet in dag["packets"] if packet["packet_id"] == "WP0")
    assert wp0["state"] == "ADMITTED"


def test_loop_spec_emits_every_guarded_primary_and_side_state_transition(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    loop = json.loads((output_dir / "LOOP_SPEC.yaml").read_text(encoding="utf-8"))
    transitions = loop["transitions"]
    by_pair = {
        (transition["from_state"], transition["to_state"]): transition
        for transition in transitions
    }
    expected_pairs = {
        ("PLANNED", "ADMISSION_REVIEW"),
        ("ADMISSION_REVIEW", "ADMITTED"),
        ("ADMITTED", "READY"),
        ("READY", "CLAIMED"),
        ("CLAIMED", "EXECUTING"),
        ("EXECUTING", "VERIFYING"),
        ("VERIFYING", "AWAITING_REVIEW"),
        ("AWAITING_REVIEW", "SATISFIED"),
        ("AWAITING_REVIEW", "CHANGES_REQUESTED"),
        ("CHANGES_REQUESTED", "READY"),
        ("CHANGES_REQUESTED", "ADMISSION_REVIEW"),
        ("BLOCKED_INTERNAL", "ADMISSION_REVIEW"),
        ("BLOCKED_EXTERNAL", "READY"),
        ("BUDGET_EXHAUSTED", "ESCALATED"),
        ("ESCALATED", "ADMISSION_REVIEW"),
        ("SATISFIED", "EVIDENCE_STALE"),
        ("EVIDENCE_STALE", "ADMISSION_REVIEW"),
        ("REVOKED", "ADMISSION_REVIEW"),
    }
    assert expected_pairs <= by_pair.keys()
    assert all(transition["event"] for transition in transitions)
    assert all(transition["guards"] for transition in transitions)
    side_states = set(loop["side_states"])
    assert not any(
        transition["from_state"] in side_states
        and transition["to_state"] == "SATISFIED"
        for transition in transitions
    )
    wake_guards = " ".join(by_pair[("BLOCKED_EXTERNAL", "READY")]["guards"]).lower()
    assert "wake" in wake_guards
    assert "artifact" in wake_guards
    assert "verif" in wake_guards


def test_loop_spec_closes_scale_training_retry_rollback_and_fallbacks(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    loop = json.loads((output_dir / "LOOP_SPEC.yaml").read_text(encoding="utf-8"))
    budgets = loop["budgets"]
    assert budgets["scale_training_attempts_per_topology_recipe"] == 1
    assert budgets["exact_transient_infrastructure_retries"] == 1

    rollback = loop["rollback"]
    actions = " ".join(rollback["ordered_actions"]).lower().replace("_", " ")
    for required in (
        "publish",
        "immutable config set manifest",
        "cancel",
        "episode",
        "revoke",
        "secret",
        "route",
        "reconcile",
        "resource",
        "quarantine",
        "reward",
        "checkpoint",
        "evidence",
        "active status",
    ):
        assert required in actions
    assert set(rollback["fallback_denials"]) == {
        "legacy_parser",
        "profile_execution",
        "mutable_paths",
        "unknown_provider_to_openai",
        "unknown_driver_to_process",
        "another_weighted_candidate",
        "stale_cache_content",
    }
    assert rollback["compatibility_replay_requires_reapproval"] is True


def test_loop_spec_enumerates_every_hard_invalidator_and_exact_propagation(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    loop = json.loads((output_dir / "LOOP_SPEC.yaml").read_text(encoding="utf-8"))
    invalidation = loop["invalidation"]
    assert set(invalidation["hard_invalidators"]) == {
        "source_or_head",
        "closure_byte",
        "compiler",
        "policy",
        "schema",
        "admitted_set",
        "selector",
        "weight",
        "seed",
        "overlay",
        "image",
        "runtime",
        "security",
        "task",
        "data",
        "model",
        "checkpoint",
        "verifier",
        "launcher",
        "dependency",
        "command",
        "threshold",
        "test",
        "target_job",
        "target_node",
        "raw_log_or_artifact",
        "cleanup",
        "review_hash",
        "authority_scope",
    }
    assert invalidation["propagation"] == (
        "dependency_descendants_from_earliest_unsupported_node"
    )
    assert invalidation["historical_artifacts_immutable"] is True


def test_taxonomy_publishes_the_closed_claim_state_ladder(tmp_path: Path) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)
    taxonomy = json.loads(
        (output_dir / "EVIDENCE_TAXONOMY.json").read_text(encoding="utf-8")
    )
    assert taxonomy["claim_states"] == [
        "unclaimed",
        "pending",
        "supported",
        "rejected",
        "revoked",
    ]
