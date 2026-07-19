from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated

from pydantic import StringConstraints

import breadboard.rl.phase5.score as _public_score
from phase5_authority_evidence import (
    _authoritative_repository_for_graph,
    FROZEN_PROOF_FLOORS,
    EvidenceGraph,
    ServerEvidenceRepository,
    validate_support_eligibility,
)
from breadboard.rl.phase5.models import (
    AuthorityKind,
    AuthorityRecord,
    AuthorityRevocation,
    EvidenceCard,
    EvidenceClass,
    EvidenceState,
    ScoreItemState,
    SupportLevel,
    require_explicit_authority,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

DERIVED_TOTAL_INJECTION_ERROR = "derived totals are read-only and cannot be supplied"
UNKNOWN_SCORE_ITEM_ERROR = "score decision references unknown item"
DUPLICATE_SCORE_DECISION_ERROR = "duplicate score decision"
AWARD_EVIDENCE_ERROR = "awarded decision requires evidence_ids"
AWARD_REVIEW_ERROR = "awarded decision requires review_ids"
AWARD_SUPERVISOR_ERROR = "awarded decision requires supervisor_decision_id"
PROOF_FLOOR_ERROR = "award evidence does not satisfy the item's exact proof floor"
UNVALIDATED_AWARD_EVALUATION_ERROR = (
    "award-bearing evaluations can be constructed only by ScoreEngine"
)
ACTIVE_STATUS_ERROR = "awarded decision requires one current active status"
PROVENANCE_ERROR = "award evidence requires server-issued frozen semantic provenance"
PROVENANCE_BINDING_ERROR = "frozen semantic provenance does not bind the evidence card"
AUTHORITATIVE_GRAPH_ERROR = "awarded decision requires canonical server evidence graph"
_ENGINE_CONSTRUCTION_TOKEN = object()

FIXED_ITEM_COUNT = 49
FIXED_CATALOG_POINTS = 1000
FIXED_CATALOG_SHA256 = (
    "sha256:fcb412743ebb83c49a96784755d881af05c6933271b8729ce7015f751883eb96"
)
FIXED_SCORE_ROW_SHA256 = (
    "sha256:30b963f59998dd641d32d9fdcfed5e3794beeeebb55bf01e5e5cf5d9075238cc"
)
FIXED_WORKSTREAM_COUNTS = {
    "A": 5,
    "B": 7,
    "C": 6,
    "D": 7,
    "E": 6,
    "F": 10,
    "G": 4,
    "H": 4,
}
FIXED_WORKSTREAM_POINTS = {
    "A": 90,
    "B": 170,
    "C": 150,
    "D": 170,
    "E": 120,
    "F": 200,
    "G": 60,
    "H": 40,
}
FIXED_ITEM_IDS = tuple(
    f"{workstream}{number}"
    for workstream, count in FIXED_WORKSTREAM_COUNTS.items()
    for number in range(1, count + 1)
)
KNOWN_PROOF_FLOORS = frozenset(
    {
        "governance",
        "local contract",
        "local process",
        "local container",
        "IBM target",
        "target training",
        "DigitalOcean",
        "authority",
        "local container / conditional DigitalOcean",
    }
)
_SCORE_ROW = re.compile(
    r"^- \[ \] \*\*([A-H]\d+) — (\d+) — ([^*]+):\*\* "
    r"(.*?) \*\*Pass:\*\* (.+)$"
)
_DETAILED_START = "## Detailed workstreams"
_DETAILED_END = "## Implementation packets and dependency order"

_FLOOR_CLASSES: dict[str, frozenset[EvidenceClass]] = {
    "governance": frozenset(
        {
            EvidenceClass.SOURCE_REFERENCE,
            EvidenceClass.ARTIFACT_INTEGRITY,
            EvidenceClass.ADMISSION_DECISION,
            EvidenceClass.CLEANUP_ROLLBACK,
        }
    ),
    "local contract": frozenset({EvidenceClass.LOCAL_CONTRACT_TEST}),
    "local process": frozenset({EvidenceClass.LOCAL_PROCESS_INTEGRATION}),
    "local container": frozenset({EvidenceClass.LOCAL_CONTAINER_ATTESTATION}),
    "IBM target": frozenset(
        {EvidenceClass.TARGET_SLURM_COMMAND, EvidenceClass.TARGET_EPISODE_LIFECYCLE}
    ),
    "target training": frozenset({EvidenceClass.TARGET_TRAINING_RUN}),
    "DigitalOcean": frozenset({EvidenceClass.DIGITALOCEAN_PROVIDER_RUN}),
    "authority": frozenset({EvidenceClass.AUTHORITY_DECISION}),
    "local container / conditional DigitalOcean": frozenset(
        {
            EvidenceClass.LOCAL_CONTAINER_ATTESTATION,
            EvidenceClass.DIGITALOCEAN_PROVIDER_RUN,
        }
    ),
}
_OBSERVED_FLOORS = frozenset(
    {
        "local process",
        "local container",
        "IBM target",
        "target training",
        "DigitalOcean",
        "authority",
        "local container / conditional DigitalOcean",
    }
)




# The signed service accepts and returns the public immutable score DTOs.  Only
# the authority-bearing evaluation engine is private.
ScoreDecision = _public_score.ScoreDecision
ScoreEvaluation = _public_score.ScoreEvaluation
ScoreItem = _public_score.ScoreItem
_ENGINE_CONSTRUCTION_TOKEN = _public_score._ENGINE_CONSTRUCTION_TOKEN


class ScoreEngine:
    def __init__(
        self,
        catalog: Sequence[ScoreItem],
        *,
        _authority_capability: object | None = None,
        **derived_totals: object,
    ) -> None:
        if derived_totals:
            raise ValueError(DERIVED_TOTAL_INJECTION_ERROR)
        self._catalog = tuple(catalog)
        validate_fixed_catalog(self._catalog)
        self._items_by_id = {item.item_id: item for item in self._catalog}
        self.__authority_capability = _authority_capability

    @property
    def catalog(self) -> tuple[ScoreItem, ...]:
        return self._catalog

    def evaluate(
        self,
        decisions: Sequence[ScoreDecision],
        *,
        evidence_cards: Sequence[EvidenceCard] = (),
        evidence_graph: EvidenceGraph | None = None,
        supervisor_authorities: Sequence[AuthorityRecord] = (),
        authority_revocations: Sequence[AuthorityRevocation] = (),
        evaluated_at: datetime | None = None,
    ) -> ScoreEvaluation:
        decision_ids = [decision.item_id for decision in decisions]
        duplicates = sorted(
            item_id for item_id, count in Counter(decision_ids).items() if count > 1
        )
        if duplicates:
            raise ValueError(f"{DUPLICATE_SCORE_DECISION_ERROR}: {duplicates[0]}")
        unknown = sorted(set(decision_ids) - self._items_by_id.keys())
        if unknown:
            raise ValueError(f"{UNKNOWN_SCORE_ITEM_ERROR}: {unknown[0]}")
        supplied = {decision.item_id: decision for decision in decisions}
        complete = tuple(
            supplied.get(item.item_id, ScoreDecision(item_id=item.item_id))
            for item in self._catalog
        )
        awarded = [
            decision
            for decision in complete
            if decision.state is ScoreItemState.AWARDED
        ]
        if awarded:
            cards_by_id = _unique_cards(evidence_cards)
            authorities_by_id = {
                record.record_id: record for record in supervisor_authorities
            }
            if len(authorities_by_id) != len(supervisor_authorities):
                raise ValueError("supervisor authority record IDs must be unique")
            if evidence_graph is None:
                raise ValueError(AWARD_EVIDENCE_ERROR)
            if evidence_graph.active_status_state() is not EvidenceState.CURRENT:
                status_node = evidence_graph.active_pointer.target_node_id
                codes = evidence_graph.rejection_codes().get(status_node, ())
                suffix = f": {codes[0]}" if codes else ""
                raise ValueError(f"{ACTIVE_STATUS_ERROR}{suffix}")
            for decision in awarded:
                item = self._items_by_id[decision.item_id]
                try:
                    cards = [
                        cards_by_id[evidence_id]
                        for evidence_id in decision.evidence_ids
                    ]
                    reviews = [
                        cards_by_id[review_id] for review_id in decision.review_ids
                    ]
                except KeyError as error:
                    raise ValueError(
                        f"{AWARD_EVIDENCE_ERROR}: {decision.item_id}"
                    ) from error
                if item.proof_floor in FROZEN_PROOF_FLOORS:
                    repository = _authoritative_repository_for_graph(
                        self.__authority_capability,
                        evidence_graph,
                    )
                    if repository is None or not repository.owns_graph(evidence_graph):
                        raise ValueError(
                            f"{AUTHORITATIVE_GRAPH_ERROR}: {decision.item_id}"
                        )
                    try:
                        repository.validate_award_graph(
                            evidence_graph,
                            supplied_cards=evidence_cards,
                            evidence_ids=decision.evidence_ids,
                            review_ids=decision.review_ids,
                        )
                    except ValueError as error:
                        error_text = str(error)
                        prefix = (
                            PROVENANCE_BINDING_ERROR
                            if "card" in error_text
                            else AUTHORITATIVE_GRAPH_ERROR
                        )
                        raise ValueError(f"{prefix}: {decision.item_id}") from error
                floor_cards = _validate_item_floor(item, cards)
                _validate_frozen_provenance(
                    item,
                    floor_cards,
                    evidence_graph,
                    repository=repository
                    if item.proof_floor in FROZEN_PROOF_FLOORS
                    else None,
                )
                _validate_current_reviews(item, cards, reviews)
                validate_support_eligibility(
                    cards + reviews,
                    requires_observed=item.proof_floor in _OBSERVED_FLOORS,
                )
                effective = evidence_graph.effective_states()
                if item.proof_floor in FROZEN_PROOF_FLOORS:
                    invalidated_nodes = [
                        node_id
                        for node_id, state in effective.items()
                        if state is not EvidenceState.CURRENT
                    ]
                else:
                    evidence_nodes = {
                        node.evidence_id: node.node_id
                        for node in evidence_graph.nodes
                        if node.evidence_id is not None
                    }
                    invalidated_nodes = [
                        evidence_nodes.get(evidence_id, "")
                        for evidence_id in decision.evidence_ids + decision.review_ids
                        if evidence_id not in evidence_nodes
                        or effective[evidence_nodes[evidence_id]]
                        is not EvidenceState.CURRENT
                    ]
                if invalidated_nodes:
                    rejection_codes = evidence_graph.rejection_codes()
                    codes = next(
                        (
                            rejection_codes[node_id]
                            for node_id in invalidated_nodes
                            if rejection_codes.get(node_id)
                        ),
                        (),
                    )
                    suffix = f": {codes[0]}" if codes else ""
                    raise ValueError(
                        f"{AWARD_EVIDENCE_ERROR}: {decision.item_id}{suffix}"
                    )
                authority = authorities_by_id.get(decision.supervisor_decision_id or "")
                if (
                    authority is None
                    or authority.kind is not AuthorityKind.AUTHORITY_DECISION
                ):
                    raise ValueError(f"{AWARD_SUPERVISOR_ERROR}: {decision.item_id}")
                bound_hashes = tuple(card.artifact_sha256 for card in cards + reviews)
                require_explicit_authority(
                    authority,
                    at=evaluated_at,
                    revocations=tuple(authority_revocations),
                    required_scope=(f"score-item:{item.item_id}",),
                    required_artifact_hashes=bound_hashes,
                )
        return ScoreEvaluation.model_validate(
            {"catalog": self._catalog, "decisions": complete},
            context={"construction_token": _ENGINE_CONSTRUCTION_TOKEN},
        )


def _unique_cards(cards: Sequence[EvidenceCard]) -> dict[str, EvidenceCard]:
    by_id = {card.evidence_id: card for card in cards}
    if len(by_id) != len(cards):
        raise ValueError("evidence card IDs must be unique")
    return by_id


def _validate_item_floor(
    item: ScoreItem,
    cards: Sequence[EvidenceCard],
) -> tuple[EvidenceCard, ...]:
    allowed_classes = _FLOOR_CLASSES[item.proof_floor]
    floor_cards = tuple(
        card
        for card in cards
        if card.proof_floor == item.proof_floor
        and card.evidence_class in allowed_classes
    )
    if not floor_cards:
        raise ValueError(f"{PROOF_FLOOR_ERROR}: {item.item_id}")
    if item.proof_floor in _OBSERVED_FLOORS and any(
        card.support_level is not SupportLevel.OBSERVED for card in floor_cards
    ):
        raise ValueError(f"{PROOF_FLOOR_ERROR}: {item.item_id}")
    return floor_cards


def _validate_frozen_provenance(
    item: ScoreItem,
    floor_cards: Sequence[EvidenceCard],
    evidence_graph: EvidenceGraph,
    *,
    repository: ServerEvidenceRepository | None,
) -> None:
    if item.proof_floor not in FROZEN_PROOF_FLOORS:
        return
    if repository is None:
        raise ValueError(f"{PROVENANCE_ERROR}: {item.item_id}")
    for card in floor_cards:
        try:
            rejection_code = repository.resolve_frozen_floor(
                evidence_graph,
                card,
                proof_floor=item.proof_floor,
            )
        except ValueError as error:
            prefix = (
                PROVENANCE_BINDING_ERROR
                if "bind" in str(error) or "seal" in str(error)
                else PROVENANCE_ERROR
            )
            raise ValueError(f"{prefix}: {item.item_id}") from error
        if rejection_code is not None:
            repository.reject_frozen_floor(
                evidence_graph,
                card.evidence_id,
                rejection_code=rejection_code,
            )
            raise ValueError(
                f"{AWARD_EVIDENCE_ERROR}: {item.item_id}: {rejection_code}"
            )


def _validate_current_reviews(
    item: ScoreItem,
    cards: Sequence[EvidenceCard],
    reviews: Sequence[EvidenceCard],
) -> None:
    if not reviews or any(
        review.evidence_class is not EvidenceClass.REVIEW_VERDICT
        or review.support_level is not SupportLevel.OBSERVED
        or review.state is not EvidenceState.CURRENT
        for review in reviews
    ):
        raise ValueError(f"{AWARD_REVIEW_ERROR}: {item.item_id}")
    required_hashes = {card.artifact_sha256 for card in cards}
    reviewed_hashes = {
        artifact_hash
        for review in reviews
        for artifact_hash in review.reviewed_artifact_hashes
    }
    if not required_hashes.issubset(reviewed_hashes):
        raise ValueError(f"{AWARD_REVIEW_ERROR}: {item.item_id}")


def _catalog_sha256(catalog: Sequence[ScoreItem]) -> str:
    payload = [item.model_dump(mode="json") for item in catalog]
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _score_row_sha256(catalog: Sequence[ScoreItem]) -> str:
    rows = "\n".join(
        f"{item.item_id}|{item.points}|{item.proof_floor}|{item.pass_predicate}"
        for item in catalog
    )
    return "sha256:" + hashlib.sha256(rows.encode()).hexdigest()


def validate_fixed_catalog(catalog: Sequence[ScoreItem]) -> None:
    if len(catalog) != FIXED_ITEM_COUNT:
        raise ValueError("score catalog must contain exactly 49 items")
    ids = tuple(item.item_id for item in catalog)
    if ids != FIXED_ITEM_IDS:
        raise ValueError(
            "score item IDs and order must equal the frozen 49-item catalog"
        )
    if sum(item.points for item in catalog) != FIXED_CATALOG_POINTS:
        raise ValueError("score catalog must total exactly 1000 points")
    counts = Counter(item.workstream for item in catalog)
    points = Counter()
    for item in catalog:
        points[item.workstream] += item.points
    if dict(sorted(counts.items())) != FIXED_WORKSTREAM_COUNTS:
        raise ValueError("score workstream item counts must be A-H 5/7/6/7/6/10/4/4")
    if dict(sorted(points.items())) != FIXED_WORKSTREAM_POINTS:
        raise ValueError(
            "score workstream point totals must be A-H 90/170/150/170/120/200/60/40"
        )
    if _score_row_sha256(catalog) != FIXED_SCORE_ROW_SHA256:
        raise ValueError("score rows do not match the frozen ledger digest")
    if _catalog_sha256(catalog) != FIXED_CATALOG_SHA256:
        raise ValueError(
            "score catalog metadata does not match the frozen catalog digest"
        )


def parse_score_catalog(playbook: str | Path) -> tuple[ScoreItem, ...]:
    if isinstance(playbook, Path):
        text = playbook.read_text()
    elif "\n" not in playbook and Path(playbook).is_file():
        text = Path(playbook).read_text()
    else:
        text = playbook
    try:
        section = text.split(_DETAILED_START, 1)[1].split(_DETAILED_END, 1)[0]
    except IndexError as error:
        raise ValueError(
            "playbook must contain the delimited Detailed workstreams section"
        ) from error
    candidates = [line for line in section.splitlines() if line.startswith("- [ ]")]
    parsed: list[ScoreItem] = []
    for line in candidates:
        match = _SCORE_ROW.fullmatch(line)
        if match is None:
            raise ValueError(f"malformed score checkbox row: {line}")
        item_id, points, proof_floor, description, pass_predicate = match.groups()
        parsed.append(
            ScoreItem(
                item_id=item_id,
                points=int(points),
                workstream=item_id[0],
                proof_floor=proof_floor.strip(),
                description=description.strip(),
                pass_predicate=pass_predicate.strip(),
                owner_packet=_owner_packet(item_id),
            )
        )
    catalog = tuple(parsed)
    validate_fixed_catalog(catalog)
    return catalog


def _owner_packet(item_id: str) -> str:
    workstream = item_id[0]
    number = int(item_id[1:])
    if workstream == "A":
        return "WP0"
    if workstream == "B":
        return {1: "WP1", 2: "WP2", 3: "WP2", 4: "WP2", 5: "WP8", 6: "WP4", 7: "WP2"}[
            number
        ]
    if workstream == "C":
        return "WP7" if number == 5 else "WP3"
    if workstream == "D":
        return {1: "WP8", 2: "WP4", 3: "WP9", 4: "WP8", 5: "WP10", 6: "WP8", 7: "WP11"}[
            number
        ]
    if workstream == "E":
        return "WP15" if number == 6 else "WP8"
    if workstream == "F":
        if number <= 6:
            return "WP13"
        if number <= 9:
            return "WP14"
        return "WP14b"
    if workstream == "G":
        return {1: "WP4", 2: "WP15", 3: "WP15", 4: "WP14b"}[number]
    return "WP15"


__all__ = [
    "AWARD_EVIDENCE_ERROR",
    "AWARD_REVIEW_ERROR",
    "AWARD_SUPERVISOR_ERROR",
    "ACTIVE_STATUS_ERROR",
    "AUTHORITATIVE_GRAPH_ERROR",
    "DERIVED_TOTAL_INJECTION_ERROR",
    "DUPLICATE_SCORE_DECISION_ERROR",
    "FIXED_CATALOG_POINTS",
    "FIXED_CATALOG_SHA256",
    "FIXED_ITEM_COUNT",
    "FIXED_ITEM_IDS",
    "FIXED_SCORE_ROW_SHA256",
    "FIXED_WORKSTREAM_COUNTS",
    "FIXED_WORKSTREAM_POINTS",
    "PROOF_FLOOR_ERROR",
    "PROVENANCE_BINDING_ERROR",
    "PROVENANCE_ERROR",
    "ScoreDecision",
    "ScoreEngine",
    "ScoreEvaluation",
    "ScoreItem",
    "UNKNOWN_SCORE_ITEM_ERROR",
    "parse_score_catalog",
    "UNVALIDATED_AWARD_EVALUATION_ERROR",
    "validate_fixed_catalog",
]
