from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from breadboard.rl.phase5.models import (
    ACTIVE_POINTER_ERROR,
    ActiveStatusPointer,
    EvidenceCard,
    EvidenceClass,
    EvidenceNode,
    EvidenceState,
    SupportLevel,
)



EVIDENCE_CYCLE_ERROR = "evidence dependency graph must be acyclic"
TAMPERED_EVIDENCE_ERROR = "evidence artifact hash mismatch"
EVIDENCE_NOT_CURRENT_ERROR = "evidence is not current"
INELIGIBLE_SUPPORT_ERROR = "evidence support level is not point-eligible"
OBSERVED_VERIFICATION_ERROR = (
    "observed evidence requires independent current observed verification"
)
DERIVATION_PIN_ERROR = "derived evidence requires pinned derivation code and version"
DERIVATION_INPUT_ERROR = "derived evidence requires current observed inputs"
OBSERVED_REQUIRED_ERROR = "observed evidence is required"
FROZEN_PROOF_FLOORS = ("IBM target", "target training", "authority")


class FrozenEvidenceSubstitution(str, Enum):
    SOURCE_PRESENCE = "source_presence"
    FIXTURE_GOLD_JSONL = "fixture_gold_jsonl"
    MONKEYPATCHED_HTTP = "monkeypatched_http"
    LOOPBACK = "loopback"
    LOCAL_DOCKER = "local_docker"
    GENERIC_SLURM = "generic_slurm"
    COMPLETED_STATE = "completed_state"
    COPIED_MANIFEST = "copied_manifest"
    MATCHING_HASH = "matching_hash"


class EvidenceMutation(str, Enum):
    CHANGED_BYTES = "changed_bytes"
    MISSING_OBJECT = "missing_object"
    CROSS_CONTEXT_REUSE = "cross_context_reuse"
    OLD_HEAD_REVIEW = "old_head_review"
    THRESHOLD_DRIFT = "threshold_drift"
    FAILED_RERUN = "failed_rerun"
    SUPERSEDED_STATUS = "superseded_status"


class EvidenceSemanticKind(str, Enum):
    IBM_TARGET_EXECUTION = "ibm_target_execution"
    TARGET_TRAINING_EXECUTION = "target_training_execution"
    SCOPED_AUTHORITY_DECISION = "scoped_authority_decision"
    SOURCE_PRESENCE = "source_presence"
    FIXTURE_GOLD_JSONL = "fixture_gold_jsonl"
    MONKEYPATCHED_HTTP = "monkeypatched_http"
    LOOPBACK = "loopback"
    LOCAL_DOCKER = "local_docker"
    GENERIC_SLURM = "generic_slurm"
    COMPLETED_STATE = "completed_state"
    COPIED_MANIFEST = "copied_manifest"
    MATCHING_HASH = "matching_hash"


@dataclass(frozen=True)
class FrozenEvidenceIdentity:
    source_head: str
    config_digest: str
    task_digest: str
    run_id: str
    model_digest: str
    threshold_digest: str



@dataclass(frozen=True)
class EvidenceInvalidationResult:
    rejection_code: str
    root_node_id: str
    affected_node_ids: tuple[str, ...]
    effective_states: tuple[tuple[str, EvidenceState], ...]
    award_allowed: bool = False
    promotion_allowed: bool = False


_G2_REJECTION_CODES = {
    (substitution, proof_floor): (
        "g2_"
        + substitution.value
        + "_cannot_satisfy_"
        + proof_floor.lower().replace(" ", "_")
    )
    for substitution in FrozenEvidenceSubstitution
    for proof_floor in FROZEN_PROOF_FLOORS
}
_SEMANTIC_SUBSTITUTIONS = {
    EvidenceSemanticKind.SOURCE_PRESENCE: FrozenEvidenceSubstitution.SOURCE_PRESENCE,
    EvidenceSemanticKind.FIXTURE_GOLD_JSONL: FrozenEvidenceSubstitution.FIXTURE_GOLD_JSONL,
    EvidenceSemanticKind.MONKEYPATCHED_HTTP: FrozenEvidenceSubstitution.MONKEYPATCHED_HTTP,
    EvidenceSemanticKind.LOOPBACK: FrozenEvidenceSubstitution.LOOPBACK,
    EvidenceSemanticKind.LOCAL_DOCKER: FrozenEvidenceSubstitution.LOCAL_DOCKER,
    EvidenceSemanticKind.GENERIC_SLURM: FrozenEvidenceSubstitution.GENERIC_SLURM,
    EvidenceSemanticKind.COMPLETED_STATE: FrozenEvidenceSubstitution.COMPLETED_STATE,
    EvidenceSemanticKind.COPIED_MANIFEST: FrozenEvidenceSubstitution.COPIED_MANIFEST,
    EvidenceSemanticKind.MATCHING_HASH: FrozenEvidenceSubstitution.MATCHING_HASH,
}
_GENUINE_FLOOR_SEMANTICS = {
    "IBM target": EvidenceSemanticKind.IBM_TARGET_EXECUTION,
    "target training": EvidenceSemanticKind.TARGET_TRAINING_EXECUTION,
    "authority": EvidenceSemanticKind.SCOPED_AUTHORITY_DECISION,
}
_G3_REJECTION_CODES = {
    mutation: "g3_" + mutation.value for mutation in EvidenceMutation
}
_CONTEXT_IDENTITY_FIELDS = frozenset(
    {"config_digest", "task_digest", "run_id", "model_digest"}
)


def _g2_rejection_code(
    semantic_kind: EvidenceSemanticKind,
    *,
    proof_floor: str,
) -> str | None:
    if proof_floor not in FROZEN_PROOF_FLOORS:
        raise ValueError(f"unsupported frozen proof floor: {proof_floor}")
    if semantic_kind is _GENUINE_FLOOR_SEMANTICS[proof_floor]:
        return None
    substitution = _SEMANTIC_SUBSTITUTIONS.get(semantic_kind)
    if substitution is not None:
        return _G2_REJECTION_CODES[(substitution, proof_floor)]
    return (
        "g2_"
        + semantic_kind.value
        + "_cannot_satisfy_"
        + proof_floor.lower().replace(" ", "_")
    )


_INVALIDATING_STATES = frozenset(
    {
        EvidenceState.STALE,
        EvidenceState.SUPERSEDED,
        EvidenceState.INVALID,
        EvidenceState.REVOKED,
        EvidenceState.QUARANTINED,
    }
)
_NEVER_ELIGIBLE = frozenset(
    {
        SupportLevel.INFERRED,
        SupportLevel.WORKER_CLAIM,
        SupportLevel.UNVERIFIED,
        SupportLevel.CONTRADICTED,
    }
)




class EvidenceGraph:
    """A validated local DAG that is never authoritative for proof-floor awards."""

    def __init__(
        self,
        nodes: Sequence[EvidenceNode],
        active_pointers: Sequence[ActiveStatusPointer],
    ) -> None:
        if len(active_pointers) != 1:
            raise ValueError(ACTIVE_POINTER_ERROR)
        by_id = {node.node_id: node for node in nodes}
        if len(by_id) != len(nodes):
            raise ValueError("evidence node IDs must be unique")
        evidence_ids = [
            node.evidence_id for node in nodes if node.evidence_id is not None
        ]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError(
                "each evidence card must bind to exactly one evidence node"
            )
        for node in nodes:
            for dependency in node.dependencies:
                if dependency not in by_id:
                    raise ValueError(
                        f"evidence dependency references unknown node: {dependency}"
                    )
        pointer = active_pointers[0]
        if pointer.target_node_id not in by_id:
            raise ValueError(
                f"active-status pointer references unknown node: {pointer.target_node_id}"
            )
        if by_id[pointer.target_node_id].node_kind.value != "status":
            raise ValueError("active-status pointer target must be a status node")
        self._topological_order(by_id)
        self._nodes = by_id
        self._active_pointer = pointer
        self._state_overrides: dict[str, EvidenceState] = {}
        self._rejection_codes: dict[str, set[str]] = defaultdict(set)
        descendants: dict[str, set[str]] = defaultdict(set)
        for node in nodes:
            for dependency in node.dependencies:
                descendants[dependency].add(node.node_id)
        self._descendants = {
            key: frozenset(value) for key, value in descendants.items()
        }

    @staticmethod
    def _topological_order(nodes: Mapping[str, EvidenceNode]) -> tuple[str, ...]:
        indegree = {node_id: len(node.dependencies) for node_id, node in nodes.items()}
        dependents: dict[str, list[str]] = defaultdict(list)
        for node in nodes.values():
            for dependency in node.dependencies:
                dependents[dependency].append(node.node_id)
        queue = deque(
            sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        )
        ordered: list[str] = []
        while queue:
            node_id = queue.popleft()
            ordered.append(node_id)
            for dependent in sorted(dependents[node_id]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
        if len(ordered) != len(nodes):
            raise ValueError(EVIDENCE_CYCLE_ERROR)
        return tuple(ordered)

    @property
    def active_pointer(self) -> ActiveStatusPointer:
        return self._active_pointer

    @property
    def nodes(self) -> tuple[EvidenceNode, ...]:
        return tuple(self._nodes[node_id] for node_id in sorted(self._nodes))

    @property
    def canonical_root(self) -> None:
        return None

    def node_id_for_evidence(self, evidence_id: str) -> str | None:
        return next(
            (
                node.node_id
                for node in self._nodes.values()
                if node.evidence_id == evidence_id
            ),
            None,
        )

    def active_status_state(self) -> EvidenceState:
        return self.effective_states()[self._active_pointer.target_node_id]

    def effective_states(self) -> dict[str, EvidenceState]:
        return {
            node_id: self._state_overrides.get(node_id, node.state)
            for node_id, node in sorted(self._nodes.items())
        }

    def rejection_codes(self) -> dict[str, tuple[str, ...]]:
        return {
            node_id: tuple(sorted(codes))
            for node_id, codes in sorted(self._rejection_codes.items())
        }

    def _require_local_invalidation(self) -> None:
        return None

    def _invalidate_with_revoked_descendants(
        self,
        node_id: str,
        *,
        root_state: EvidenceState,
        rejection_code: str,
    ) -> EvidenceInvalidationResult:
        self._require_local_invalidation()
        affected = self._transitive_descendants(node_id)
        self._state_overrides[node_id] = root_state
        for dependent in affected - {node_id}:
            self._state_overrides[dependent] = EvidenceState.REVOKED
        for affected_id in affected:
            self._rejection_codes[affected_id].add(rejection_code)
        states = self.effective_states()
        return EvidenceInvalidationResult(
            rejection_code=rejection_code,
            root_node_id=node_id,
            affected_node_ids=tuple(sorted(affected)),
            effective_states=tuple(
                (affected_id, states[affected_id]) for affected_id in sorted(affected)
            ),
        )

    def invalidate_mutation(
        self,
        node_id: str,
        mutation: EvidenceMutation,
        *,
        card: EvidenceCard | None = None,
        artifact_bytes: bytes | None = None,
        object_present: bool | None = None,
        rerun_passed: bool | None = None,
        _identity_validated: bool = False,
    ) -> EvidenceInvalidationResult:
        self._require_local_invalidation()
        try:
            mutation = EvidenceMutation(mutation)
        except ValueError as error:
            raise ValueError(f"unknown evidence mutation: {mutation}") from error
        if node_id not in self._nodes:
            raise ValueError(f"unknown evidence node: {node_id}")
        if mutation is EvidenceMutation.CHANGED_BYTES:
            if card is None or artifact_bytes is None:
                raise ValueError(
                    "changed-bytes mutation requires its evidence card and observed bytes"
                )
            self._validate_card_binding(node_id, card)
            observed_hash = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
            if (
                observed_hash == card.artifact_sha256
                and len(artifact_bytes) == card.artifact_size
            ):
                raise ValueError(
                    "changed-bytes mutation did not change the frozen artifact"
                )
        elif mutation is EvidenceMutation.MISSING_OBJECT:
            if object_present is not False:
                raise ValueError(
                    "missing-object mutation requires an observed absent object"
                )
        elif mutation in {
            EvidenceMutation.CROSS_CONTEXT_REUSE,
            EvidenceMutation.OLD_HEAD_REVIEW,
            EvidenceMutation.THRESHOLD_DRIFT,
        }:
            if not _identity_validated:
                raise ValueError(
                    "identity mutations must be detected from frozen and observed identities"
                )
        elif mutation is EvidenceMutation.FAILED_RERUN:
            if rerun_passed is not False:
                raise ValueError(
                    "failed-rerun mutation requires an observed failed rerun"
                )
            if self._nodes[node_id].node_kind.value != "evidence":
                raise ValueError("failed-rerun invalidation requires an evidence node")
        elif mutation is EvidenceMutation.SUPERSEDED_STATUS:
            if (
                node_id != self._active_pointer.target_node_id
                or self._nodes[node_id].node_kind.value != "status"
            ):
                raise ValueError(
                    "superseded-status mutation requires the active status target"
                )
        root_state = {
            EvidenceMutation.OLD_HEAD_REVIEW: EvidenceState.STALE,
            EvidenceMutation.THRESHOLD_DRIFT: EvidenceState.STALE,
            EvidenceMutation.FAILED_RERUN: EvidenceState.SUPERSEDED,
            EvidenceMutation.SUPERSEDED_STATUS: EvidenceState.SUPERSEDED,
        }.get(mutation, EvidenceState.INVALID)
        return self._invalidate_with_revoked_descendants(
            node_id,
            root_state=root_state,
            rejection_code=_G3_REJECTION_CODES[mutation],
        )

    def invalidate_identity_drift(
        self,
        node_id: str,
        *,
        frozen: FrozenEvidenceIdentity,
        observed: FrozenEvidenceIdentity,
    ) -> EvidenceInvalidationResult:
        self._require_local_invalidation()
        if node_id not in self._nodes:
            raise ValueError(f"unknown evidence node: {node_id}")
        changed = {
            field
            for field in FrozenEvidenceIdentity.__dataclass_fields__
            if getattr(frozen, field) != getattr(observed, field)
        }
        if not changed:
            raise ValueError("evidence identity did not drift")
        if changed.issubset(_CONTEXT_IDENTITY_FIELDS):
            mutation = EvidenceMutation.CROSS_CONTEXT_REUSE
        elif changed == {"source_head"}:
            if self._nodes[node_id].node_kind.value != "review":
                raise ValueError("old-head invalidation requires a review node")
            mutation = EvidenceMutation.OLD_HEAD_REVIEW
        elif changed == {"threshold_digest"}:
            mutation = EvidenceMutation.THRESHOLD_DRIFT
        else:
            raise ValueError(
                "evidence identity drift spans more than one invalidation class"
            )
        return self.invalidate_mutation(
            node_id,
            mutation,
            _identity_validated=True,
        )

    def _validate_card_binding(self, node_id: str, card: EvidenceCard) -> None:
        if node_id not in self._nodes:
            raise ValueError(f"unknown evidence node: {node_id}")
        if self._nodes[node_id].evidence_id != card.evidence_id:
            raise ValueError(
                f"tamper card does not belong to evidence node: {card.evidence_id}"
            )

    def _transitive_descendants(self, node_id: str) -> frozenset[str]:
        if node_id not in self._nodes:
            raise ValueError(f"unknown evidence node: {node_id}")
        found = {node_id}
        queue = deque([node_id])
        while queue:
            current = queue.popleft()
            for dependent in self._descendants.get(current, ()):
                if dependent not in found:
                    found.add(dependent)
                    queue.append(dependent)
        return frozenset(found)

    def invalidate(
        self, node_id: str, state: EvidenceState = EvidenceState.STALE
    ) -> frozenset[str]:
        self._require_local_invalidation()
        if state not in _INVALIDATING_STATES:
            raise ValueError(
                "invalidation state must be stale, superseded, invalid, revoked, or quarantined"
            )
        affected = self._transitive_descendants(node_id)
        for affected_id in affected:
            self._state_overrides[affected_id] = state
        return affected

    def invalidate_tamper(
        self, node_id: str, card: EvidenceCard, artifact_bytes: bytes
    ) -> frozenset[str]:
        self._validate_card_binding(node_id, card)
        observed_hash = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
        if (
            observed_hash == card.artifact_sha256
            and len(artifact_bytes) == card.artifact_size
        ):
            return frozenset()
        self.invalidate_mutation(
            node_id,
            EvidenceMutation.CHANGED_BYTES,
            card=card,
            artifact_bytes=artifact_bytes,
        )
        raise ValueError(f"{TAMPERED_EVIDENCE_ERROR}: {card.evidence_id}")

    def invalidate_failed_rerun(self, node_id: str) -> frozenset[str]:
        result = self.invalidate_mutation(
            node_id,
            EvidenceMutation.FAILED_RERUN,
            rerun_passed=False,
        )
        return frozenset(result.affected_node_ids)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _identity_payload(identity: FrozenEvidenceIdentity) -> dict[str, str]:
    return {
        field: getattr(identity, field)
        for field in FrozenEvidenceIdentity.__dataclass_fields__
    }


def _identity_from_payload(value: object) -> FrozenEvidenceIdentity:
    if not isinstance(value, Mapping):
        raise ValueError("authoritative evidence requires frozen identity")
    expected = set(FrozenEvidenceIdentity.__dataclass_fields__)
    if set(value) != expected or any(
        not isinstance(value[field], str) or not value[field] for field in expected
    ):
        raise ValueError("authoritative evidence requires complete frozen identity")
    return FrozenEvidenceIdentity(**{field: value[field] for field in expected})





def build_g2_g3_contract_report() -> dict[str, object]:
    g2_cases = [
        {
            "award_allowed": False,
            "promotion_allowed": False,
            "proof_floor": proof_floor,
            "rejection_code": _G2_REJECTION_CODES[(substitution, proof_floor)],
            "substitution": substitution.value,
        }
        for substitution in FrozenEvidenceSubstitution
        for proof_floor in FROZEN_PROOF_FLOORS
    ]
    g3_cases = [
        {
            "award_allowed": False,
            "dependent_state": EvidenceState.REVOKED.value,
            "mutation": mutation.value,
            "promotion_allowed": False,
            "rejection_code": _G3_REJECTION_CODES[mutation],
            "root_state": (
                EvidenceState.STALE.value
                if mutation
                in {EvidenceMutation.OLD_HEAD_REVIEW, EvidenceMutation.THRESHOLD_DRIFT}
                else EvidenceState.SUPERSEDED.value
                if mutation
                in {
                    EvidenceMutation.FAILED_RERUN,
                    EvidenceMutation.SUPERSEDED_STATUS,
                }
                else EvidenceState.INVALID.value
            ),
        }
        for mutation in EvidenceMutation
    ]
    return {
        "g2": {"case_count": len(g2_cases), "cases": g2_cases},
        "g3": {
            "case_count": len(g3_cases),
            "cases": g3_cases,
            "cross_context_identity_fields": sorted(_CONTEXT_IDENTITY_FIELDS),
        },
        "schema_version": "bb.rl.phase5.g2-g3-contract-report.v1",
    }


def canonical_g2_g3_contract_report_bytes() -> bytes:
    return json.dumps(
        build_g2_g3_contract_report(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _cards_by_id(cards: Sequence[EvidenceCard]) -> dict[str, EvidenceCard]:
    by_id = {card.evidence_id: card for card in cards}
    if len(by_id) != len(cards):
        raise ValueError("evidence card IDs must be unique")
    return by_id


class EvidenceEligibilityError(ValueError):
    pass


def validate_support_eligibility(
    cards: Sequence[EvidenceCard], *, requires_observed: bool
) -> None:
    """Reject support laundering and resolve verification/input links within cards."""

    by_id = _cards_by_id(cards)
    verification_roots = {
        EvidenceClass.ARTIFACT_INTEGRITY,
        EvidenceClass.REVIEW_VERDICT,
    }
    for card in cards:
        if card.state is not EvidenceState.CURRENT:
            raise EvidenceEligibilityError(
                f"{EVIDENCE_NOT_CURRENT_ERROR}: {card.evidence_id}"
            )
        if card.support_level in _NEVER_ELIGIBLE:
            raise EvidenceEligibilityError(
                f"{INELIGIBLE_SUPPORT_ERROR}: {card.evidence_id}"
            )
        verification_ids = card.independent_verification_ids
        if (
            len(set(verification_ids)) != len(verification_ids)
            or card.evidence_id in verification_ids
        ):
            raise EvidenceEligibilityError(
                f"{OBSERVED_VERIFICATION_ERROR}: {card.evidence_id}"
            )
        linked = [by_id.get(link_id) for link_id in verification_ids]
        linked_are_current_observed = bool(linked) and all(
            linked_card is not None
            and linked_card.state is EvidenceState.CURRENT
            and linked_card.support_level is SupportLevel.OBSERVED
            for linked_card in linked
        )
        is_verification_root = card.evidence_class in verification_roots
        if card.support_level is SupportLevel.OBSERVED and not is_verification_root:
            linked_are_independent = linked_are_current_observed and all(
                linked_card is not None
                and linked_card.evidence_class in verification_roots
                for linked_card in linked
            )
            if not linked_are_independent:
                raise EvidenceEligibilityError(
                    f"{OBSERVED_VERIFICATION_ERROR}: {card.evidence_id}"
                )
        if card.support_level is SupportLevel.DERIVED_DETERMINISTICALLY:
            if requires_observed:
                raise EvidenceEligibilityError(
                    f"{OBSERVED_REQUIRED_ERROR}: {card.evidence_id}"
                )
            if card.derivation_code_hash is None or card.derivation_version is None:
                raise EvidenceEligibilityError(
                    f"{DERIVATION_PIN_ERROR}: {card.evidence_id}"
                )
            if not linked_are_current_observed:
                raise EvidenceEligibilityError(
                    f"{DERIVATION_INPUT_ERROR}: {card.evidence_id}"
                )


__all__ = [
    "DERIVATION_INPUT_ERROR",
    "DERIVATION_PIN_ERROR",
    "EVIDENCE_CYCLE_ERROR",
    "EVIDENCE_NOT_CURRENT_ERROR",
    "EvidenceEligibilityError",
    "EvidenceGraph",
    "EvidenceInvalidationResult",
    "EvidenceMutation",
    "EvidenceSemanticKind",
    "FROZEN_PROOF_FLOORS",
    "FrozenEvidenceIdentity",
    "FrozenEvidenceSubstitution",
    "INELIGIBLE_SUPPORT_ERROR",
    "OBSERVED_REQUIRED_ERROR",
    "OBSERVED_VERIFICATION_ERROR",
    "TAMPERED_EVIDENCE_ERROR",
    "build_g2_g3_contract_report",
    "canonical_g2_g3_contract_report_bytes",
    "validate_support_eligibility",
]
