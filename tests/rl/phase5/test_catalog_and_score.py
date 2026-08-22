from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from breadboard.rl.phase5.evidence import EvidenceGraph
from breadboard.rl.phase5.models import (
    ActiveStatusPointer,
    AuthorityKind,
    AuthorityRecord,
    EvidenceCard,
    EvidenceClass,
    EvidenceNode,
    EvidenceNodeKind,
    EvidenceState,
    ScoreItemState,
    SupportLevel,
)
from breadboard.rl.phase5.score import (
    DERIVED_TOTAL_INJECTION_ERROR,
    ScoreDecision,
    ScoreEngine,
    parse_score_catalog,
)


PLAYBOOK = Path(
    "/Users/kylemccleary/projects/breadboard/docs_tmp/ZYPHRA/RL_PHASE_5/"
    "BB_Z_RL_PHASE_5_CONFIG_NATIVE_EXECUTION_AND_OPTIMIZATION_PLAYBOOK.md"
)

EXPECTED_ITEM_METADATA = (
    ("A1", 15, "governance"),
    ("A2", 15, "governance"),
    ("A3", 20, "local contract"),
    ("A4", 20, "local contract"),
    ("A5", 20, "governance"),
    ("B1", 25, "local contract"),
    ("B2", 30, "local contract"),
    ("B3", 25, "local contract"),
    ("B4", 30, "local contract"),
    ("B5", 30, "local contract"),
    ("B6", 15, "local contract"),
    ("B7", 15, "local contract"),
    ("C1", 30, "local contract"),
    ("C2", 25, "local contract"),
    ("C3", 25, "local contract"),
    ("C4", 25, "local contract"),
    ("C5", 25, "local container"),
    ("C6", 20, "local contract"),
    ("D1", 25, "local contract"),
    ("D2", 25, "local contract"),
    ("D3", 25, "local contract"),
    ("D4", 20, "local process"),
    ("D5", 25, "IBM target"),
    ("D6", 25, "local container"),
    ("D7", 25, "local contract"),
    ("E1", 20, "local contract"),
    ("E2", 20, "local contract"),
    ("E3", 20, "local contract"),
    ("E4", 20, "local container"),
    ("E5", 20, "local container"),
    ("E6", 20, "governance"),
    ("F1", 20, "IBM target"),
    ("F2", 20, "IBM target"),
    ("F3", 20, "IBM target"),
    ("F4", 25, "IBM target"),
    ("F5", 20, "IBM target"),
    ("F6", 20, "IBM target"),
    ("F7", 20, "IBM target"),
    ("F8", 30, "target training"),
    ("F9", 10, "target training"),
    ("F10", 15, "local container / conditional DigitalOcean"),
    ("G1", 15, "local contract"),
    ("G2", 15, "local contract"),
    ("G3", 15, "local contract"),
    ("G4", 15, "local container"),
    ("H1", 10, "local contract"),
    ("H2", 10, "governance"),
    ("H3", 10, "authority"),
    ("H4", 10, "governance"),
)
EXPECTED_CATALOG_DIGEST = "30b963f59998dd641d32d9fdcfed5e3794beeeebb55bf01e5e5cf5d9075238cc"


def _catalog():
    return parse_score_catalog(PLAYBOOK)


def _awarded(item_id: str) -> ScoreDecision:
    return ScoreDecision(
        item_id=item_id,
        state=ScoreItemState.AWARDED,
        evidence_ids=("integrity", f"evidence:{item_id}"),
        review_ids=(f"review:{item_id}",),
        supervisor_decision_id=f"supervisor:{item_id}",
    )


def _award_evidence(
    item_id: str,
) -> tuple[list[EvidenceCard], EvidenceGraph, AuthorityRecord]:
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    digest = "sha256:" + "a" * 64
    cards = [
        EvidenceCard(
            evidence_id="integrity",
            evidence_class=EvidenceClass.ARTIFACT_INTEGRITY,
            support_level=SupportLevel.OBSERVED,
            state=EvidenceState.CURRENT,
            proof_floor="governance",
            artifact_uri="artifacts/integrity.json",
            artifact_sha256=digest,
            artifact_size=1,
            observed_at=now,
        ),
        EvidenceCard(
            evidence_id=f"evidence:{item_id}",
            evidence_class=EvidenceClass.SOURCE_REFERENCE,
            support_level=SupportLevel.OBSERVED,
            state=EvidenceState.CURRENT,
            proof_floor="governance",
            artifact_uri=f"artifacts/{item_id}.json",
            artifact_sha256=digest,
            artifact_size=1,
            observed_at=now,
            independent_verification_ids=("integrity",),
        ),
        EvidenceCard(
            evidence_id=f"review:{item_id}",
            evidence_class=EvidenceClass.REVIEW_VERDICT,
            support_level=SupportLevel.OBSERVED,
            state=EvidenceState.CURRENT,
            proof_floor="governance",
            artifact_uri=f"reviews/{item_id}.json",
            artifact_sha256=digest,
            artifact_size=1,
            observed_at=now,
            reviewed_artifact_hashes=(digest,),
        ),
    ]
    nodes = [
        EvidenceNode(
            node_id="status",
            node_kind=EvidenceNodeKind.STATUS,
        ),
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
    ]
    graph = EvidenceGraph(
        nodes=nodes,
        active_pointers=[
            ActiveStatusPointer(
                pointer_id="active", target_node_id="status", activated_at=now
            )
        ],
    )
    authority = AuthorityRecord(
        record_id=f"supervisor:{item_id}",
        kind=AuthorityKind.AUTHORITY_DECISION,
        actor_identity="supervisor@example.test",
        actor_role="phase5-supervisor",
        scope=(f"score-item:{item_id}",),
        artifact_hashes=(digest,),
        authority_artifact_uri=f"authority/{item_id}.json",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
    )
    return cards, graph, authority


def test_frozen_playbook_catalog_preserves_all_exact_score_fields() -> None:
    catalog = _catalog()

    assert tuple((item.item_id, item.points, item.proof_floor) for item in catalog) == EXPECTED_ITEM_METADATA
    assert Counter(item.workstream for item in catalog) == {
        "A": 5,
        "B": 7,
        "C": 6,
        "D": 7,
        "E": 6,
        "F": 10,
        "G": 4,
        "H": 4,
    }
    assert sum(item.points for item in catalog) == 1000
    assert all(item.description and item.pass_predicate and item.owner_packet for item in catalog)

    canonical_rows = "\n".join(
        f"{item.item_id}|{item.points}|{item.proof_floor}|{item.pass_predicate}"
        for item in catalog
    )
    assert hashlib.sha256(canonical_rows.encode("utf-8")).hexdigest() == EXPECTED_CATALOG_DIGEST


def test_score_totals_are_derived_and_each_item_is_all_or_none() -> None:
    engine = ScoreEngine(_catalog())
    evidence_cards, evidence_graph, authority = _award_evidence("A1")
    evaluation = engine.evaluate(
        [
            _awarded("A1"),
            ScoreDecision(
                item_id="F8",
                state=ScoreItemState.FAILED,
                evidence_ids=tuple(f"evidence:{index}" for index in range(100)),
                review_ids=("review:F8",),
                supervisor_decision_id="supervisor:F8",
            ),
        ],
        evidence_cards=evidence_cards,
        evidence_graph=evidence_graph,
        supervisor_authorities=(authority,),
        evaluated_at=datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
    )

    assert evaluation.catalog_points == 1000
    assert evaluation.awarded_points == 15
    assert evaluation.pending_points == 955
    assert evaluation.unawarded_points == 985
    assert evaluation.awarded_points_by_item["A1"] == 15
    assert evaluation.awarded_points_by_item["F8"] == 0

    with pytest.raises(ValueError, match=f"^{DERIVED_TOTAL_INJECTION_ERROR}$"):
        ScoreEngine(_catalog(), awarded_points=1000)

    with pytest.raises(ValidationError):
        evaluation.awarded_points = 1000


def test_award_requires_evidence_review_and_supervisor_decision() -> None:
    invalid_inputs = (
        (
            {
                "item_id": "A1",
                "state": ScoreItemState.AWARDED,
                "review_ids": ("review:A1",),
                "supervisor_decision_id": "supervisor:A1",
            },
            "awarded decision requires evidence_ids: A1",
        ),
        (
            {
                "item_id": "A1",
                "state": ScoreItemState.AWARDED,
                "evidence_ids": ("evidence:A1",),
                "supervisor_decision_id": "supervisor:A1",
            },
            "awarded decision requires review_ids: A1",
        ),
        (
            {
                "item_id": "A1",
                "state": ScoreItemState.AWARDED,
                "evidence_ids": ("evidence:A1",),
                "review_ids": ("review:A1",),
            },
            "awarded decision requires supervisor_decision_id: A1",
        ),
    )
    for fields, message in invalid_inputs:
        with pytest.raises(ValidationError, match=message):
            ScoreDecision(**fields)


def test_score_engine_rejects_unknown_and_duplicate_item_decisions() -> None:
    engine = ScoreEngine(_catalog())

    with pytest.raises(ValueError, match="^score decision references unknown item: A6$"):
        engine.evaluate([ScoreDecision(item_id="A6")])
    with pytest.raises(ValueError, match="^duplicate score decision: A1$"):
        engine.evaluate([ScoreDecision(item_id="A1"), ScoreDecision(item_id="A1")])
