from __future__ import annotations

import hashlib
import hmac
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
    EvidenceNodeKind,
    EvidenceState,
    SupportLevel,
)
from phase5_authority_store import FileTrustStore, StoredArtifact



def _is_deployment_store(candidate: object) -> bool:
    return getattr(candidate, "_phase5_private_store_marker", False) is True


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
class CanonicalEvidenceArtifact:
    """An immutable object sealed by the server's canonical artifact authority."""

    object_id: str
    artifact_uri: str
    artifact_bytes: bytes
    authority_id: str
    authority_hmac: str

    def __post_init__(self) -> None:
        if not self.object_id or not self.artifact_uri or not self.authority_id:
            raise ValueError(
                "canonical evidence objects require identity, URI, and authority"
            )
        if not isinstance(self.artifact_bytes, bytes):
            raise ValueError("canonical evidence object bytes must be immutable bytes")
        if not self.authority_hmac.startswith("hmac-sha256:"):
            raise ValueError(
                "canonical evidence objects require a server authority HMAC"
            )


@dataclass(frozen=True)
class EvidenceResolution:
    """Opaque repository capability; semantic provenance is intentionally absent."""

    repository_id: str
    record_id: str
    seal: str


@dataclass(frozen=True)
class EvidenceInvalidationEvent:
    sequence: int
    canonical_root: str
    event_key: str
    mutation: str
    rejection_code: str
    root_node_id: str
    state_overrides: tuple[tuple[str, EvidenceState], ...]
    affected_node_ids: tuple[str, ...]
    event_hmac: str


@dataclass(frozen=True)
class _EvidenceRecord:
    record_id: str
    canonical_object_id: str
    card: EvidenceCard
    semantic_kind: EvidenceSemanticKind | None
    identity: FrozenEvidenceIdentity
    artifact_bytes: bytes
    record_hmac: str


@dataclass(frozen=True)
class _CanonicalGraphRecord:
    canonical_root: str
    nodes: tuple[EvidenceNode, ...]
    active_pointer: ActiveStatusPointer
    cards: tuple[EvidenceCard, ...]
    resolutions: tuple[tuple[str, EvidenceResolution], ...]
    graph_hmac: str


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


_AUTHORITATIVE_GRAPH_TOKEN = object()


class EvidenceGraph:
    """A validated DAG; award-capable instances are sealed by a server repository."""

    def __init__(
        self,
        nodes: Sequence[EvidenceNode],
        active_pointers: Sequence[ActiveStatusPointer],
        *,
        _authority_token: object | None = None,
        _repository: ServerEvidenceRepository | None = None,
        _canonical_root: str | None = None,
        _graph_hmac: str | None = None,
        _canonical_cards: Sequence[EvidenceCard] = (),
        _resolutions: Mapping[str, EvidenceResolution] | None = None,
    ) -> None:
        authoritative_values = (_repository, _canonical_root, _graph_hmac)
        if _authority_token is not _AUTHORITATIVE_GRAPH_TOKEN and any(
            value is not None for value in authoritative_values
        ):
            raise ValueError(
                "authoritative evidence graphs can be issued only by the server repository"
            )
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
        self._repository = _repository
        self._canonical_root = _canonical_root
        self._graph_hmac = _graph_hmac
        self._canonical_cards = {card.evidence_id: card for card in _canonical_cards}
        self._resolutions = dict(_resolutions or {})
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
    def canonical_root(self) -> str | None:
        return self._canonical_root

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
        if self._repository is not None and self._canonical_root is not None:
            return self._repository._effective_states(self._canonical_root)
        return {
            node_id: self._state_overrides.get(node_id, node.state)
            for node_id, node in sorted(self._nodes.items())
        }

    def rejection_codes(self) -> dict[str, tuple[str, ...]]:
        if self._repository is not None and self._canonical_root is not None:
            return self._repository._rejection_codes(self._canonical_root)
        return {
            node_id: tuple(sorted(codes))
            for node_id, codes in sorted(self._rejection_codes.items())
        }

    def _require_local_invalidation(self) -> None:
        if self._repository is not None:
            raise ValueError(
                "authoritative evidence observations must be derived by the server repository"
            )

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


def _derive_semantic_kind(attestation: object) -> EvidenceSemanticKind | None:
    if not isinstance(attestation, Mapping):
        raise ValueError(
            "authoritative evidence requires a typed production transition"
        )
    fields = dict(attestation)
    if (
        set(fields)
        == {
            "execution_plane",
            "exit_code",
            "operation",
            "provider",
            "scheduler",
            "target_run_id",
        }
        and fields["provider"] == "IBM"
        and fields["execution_plane"] == "target"
        and fields["scheduler"] == "slurm"
        and fields["operation"] == "episode"
        and fields["exit_code"] == 0
        and isinstance(fields["target_run_id"], str)
        and bool(fields["target_run_id"])
    ):
        return EvidenceSemanticKind.IBM_TARGET_EXECUTION
    if (
        set(fields)
        == {
            "checkpoint_digest",
            "execution_plane",
            "exit_code",
            "operation",
            "provider",
            "scheduler",
            "training_run_id",
        }
        and fields["provider"] == "IBM"
        and fields["execution_plane"] == "target"
        and fields["scheduler"] == "slurm"
        and fields["operation"] == "training"
        and fields["exit_code"] == 0
        and isinstance(fields["training_run_id"], str)
        and bool(fields["training_run_id"])
        and isinstance(fields["checkpoint_digest"], str)
        and fields["checkpoint_digest"].startswith("sha256:")
    ):
        return EvidenceSemanticKind.TARGET_TRAINING_EXECUTION
    if (
        set(fields)
        == {
            "actor_role",
            "authority_record_id",
            "decision",
            "scope",
        }
        and fields["decision"] == "approved"
        and fields["actor_role"] == "phase5-supervisor"
        and isinstance(fields["authority_record_id"], str)
        and bool(fields["authority_record_id"])
        and isinstance(fields["scope"], list)
        and bool(fields["scope"])
        and all(isinstance(item, str) and item for item in fields["scope"])
    ):
        return EvidenceSemanticKind.SCOPED_AUTHORITY_DECISION
    if fields == {
        "source_path": "breadboard/rl/phase5/server_authority.py",
        "source_present": True,
    }:
        return EvidenceSemanticKind.SOURCE_PRESENCE
    if fields == {
        "fixture_format": "jsonl",
        "fixture_path": "fixtures/gold.jsonl",
        "fixture_role": "gold",
    }:
        return EvidenceSemanticKind.FIXTURE_GOLD_JSONL
    if fields == {
        "http_client": "monkeypatch",
        "http_status": 200,
        "transport": "in_process",
    }:
        return EvidenceSemanticKind.MONKEYPATCHED_HTTP
    if fields == {
        "endpoint": "http://127.0.0.1:8080",
        "network_scope": "loopback",
    }:
        return EvidenceSemanticKind.LOOPBACK
    if (
        set(fields) == {"container_runtime", "execution_host", "image_digest"}
        and fields["container_runtime"] == "docker"
        and fields["execution_host"] == "localhost"
        and isinstance(fields["image_digest"], str)
        and fields["image_digest"].startswith("sha256:")
    ):
        return EvidenceSemanticKind.LOCAL_DOCKER
    if fields == {
        "provider": "generic",
        "scheduler": "slurm",
        "state": "COMPLETED",
    }:
        return EvidenceSemanticKind.GENERIC_SLURM
    if fields == {
        "scheduler": "slurm",
        "scheduler_state": "COMPLETED",
    }:
        return EvidenceSemanticKind.COMPLETED_STATE
    if (
        set(fields) == {"manifest_origin", "manifest_sha256"}
        and fields["manifest_origin"] == "copied"
        and isinstance(fields["manifest_sha256"], str)
        and fields["manifest_sha256"].startswith("sha256:")
    ):
        return EvidenceSemanticKind.COPIED_MANIFEST
    if (
        set(fields) == {"actual_sha256", "expected_sha256"}
        and isinstance(fields["actual_sha256"], str)
        and fields["actual_sha256"] == fields["expected_sha256"]
        and fields["actual_sha256"].startswith("sha256:")
    ):
        return EvidenceSemanticKind.MATCHING_HASH
    if fields == {"record": "support"}:
        return None
    raise ValueError("authoritative production transition is not recognized")


def _artifact_authority_payload(
    *,
    authority_id: str,
    object_id: str,
    artifact_uri: str,
    artifact_bytes: bytes,
) -> bytes:
    return _canonical_json_bytes(
        {
            "artifact_sha256": _sha256(artifact_bytes),
            "artifact_size": len(artifact_bytes),
            "artifact_uri": artifact_uri,
            "authority_id": authority_id,
            "object_id": object_id,
        }
    )


def _make_repository_gate():
    token = object()
    access_issued = False

    def is_repository_token(candidate: object) -> bool:
        return candidate is token

    class ServerCompositionAccess:
        __slots__ = ()

        @staticmethod
        def open_authority(
            trust_store: FileTrustStore,
        ) -> ServerEvidenceAuthority:
            if not _is_deployment_store(trust_store):
                raise ValueError(
                    "server evidence authority requires a deployment-issued trust store"
                )
            return ServerEvidenceAuthority(
                _server_token=token,
                _trust_store=trust_store,
            )

        @staticmethod
        def record_payload(
            authority: ServerEvidenceAuthority,
            *,
            object_id: str,
            artifact_uri: str,
            artifact_payload: Mapping[str, object],
        ) -> CanonicalEvidenceArtifact:
            return authority._record_payload(
                object_id=object_id,
                artifact_uri=artifact_uri,
                artifact_payload=artifact_payload,
                _server_token=token,
            )

        @staticmethod
        def open_repository(
            authority: ServerEvidenceAuthority,
        ) -> ServerEvidenceRepository:
            return authority._open_repository(_server_token=token)

        @staticmethod
        def issue_score_capability(
            repository: ServerEvidenceRepository,
        ) -> _ServerScoreCapability:
            return _issue_score_capability(repository, _server_token=token)

    def take_server_composition_access() -> ServerCompositionAccess:
        nonlocal access_issued
        if access_issued:
            raise ValueError("server evidence composition access was already issued")
        access_issued = True
        return ServerCompositionAccess()

    return is_repository_token, take_server_composition_access


(
    _is_repository_token,
    _take_server_evidence_composition_access,
) = _make_repository_gate()
del _make_repository_gate


class ServerEvidenceAuthority:
    """Internal adapter over the deployment-selected filesystem trust store."""

    def __init__(
        self,
        *,
        _server_token: object | None = None,
        _trust_store: FileTrustStore | None = None,
    ) -> None:
        if not _is_repository_token(_server_token) or _trust_store is None:
            raise ValueError("server evidence authority is owned by server composition")
        self._trust_store = _trust_store
        self._authority_id = _trust_store.store_id
        self._secret = _trust_store._key
        self._artifacts = {
            object_id: CanonicalEvidenceArtifact(
                object_id=stored.object_id,
                artifact_uri=stored.artifact_uri,
                artifact_bytes=stored.artifact_bytes,
                authority_id=stored.store_id,
                authority_hmac=stored.authority_hmac,
            )
            for object_id, stored in _trust_store.load_artifacts().items()
        }
        self._repository: ServerEvidenceRepository | None = None

    def _record_payload(
        self,
        *,
        object_id: str,
        artifact_uri: str,
        artifact_payload: Mapping[str, object],
        _server_token: object,
    ) -> CanonicalEvidenceArtifact:
        if not _is_repository_token(_server_token):
            raise ValueError("production evidence recording requires server capability")
        stored = self._trust_store.record_artifact(
            object_id=object_id,
            artifact_uri=artifact_uri,
            artifact_payload=artifact_payload,
        )
        artifact = CanonicalEvidenceArtifact(
            object_id=stored.object_id,
            artifact_uri=stored.artifact_uri,
            artifact_bytes=stored.artifact_bytes,
            authority_id=stored.store_id,
            authority_hmac=stored.authority_hmac,
        )
        existing = self._artifacts.get(object_id)
        if existing is not None and existing != artifact:
            raise ValueError("server canonical evidence objects are append-only")
        self._artifacts[object_id] = artifact
        if self._repository is not None:
            self._repository._persist_state()
        return artifact

    def _open_repository(self, *, _server_token: object) -> ServerEvidenceRepository:
        if not _is_repository_token(_server_token):
            raise ValueError("server evidence repositories require server capability")
        if self._repository is None:
            self._repository = ServerEvidenceRepository(
                _authority_token=_server_token,
                _authority=self,
            )
        return self._repository

    def _verify_artifact(self, artifact: CanonicalEvidenceArtifact) -> bool:
        return self._trust_store.verify_artifact(
            StoredArtifact(
                object_id=artifact.object_id,
                artifact_uri=artifact.artifact_uri,
                artifact_bytes=artifact.artifact_bytes,
                store_id=artifact.authority_id,
                authority_hmac=artifact.authority_hmac,
            )
        )


class ServerEvidenceRepository:
    """Server-owned canonical provenance, graph, and append-only revocation store."""

    def __init__(
        self,
        *,
        _authority_token: object | None = None,
        _authority: ServerEvidenceAuthority | None = None,
    ) -> None:
        if not _is_repository_token(_authority_token) or _authority is None:
            raise ValueError(
                "server evidence repositories can be opened only by server-held authority"
            )
        artifacts = tuple(_authority._artifacts.values())
        if any(not _authority._verify_artifact(artifact) for artifact in artifacts):
            raise ValueError("canonical evidence object authority HMAC mismatch")
        by_id = {artifact.object_id: artifact for artifact in artifacts}
        if len(by_id) != len(artifacts):
            raise ValueError("canonical evidence object IDs must be unique")
        self._repository_id = "repo:" + _authority._authority_id
        self._secret = _authority._secret
        self._authority = _authority
        self._artifacts = _authority._artifacts
        self._records_by_id: dict[str, _EvidenceRecord] = {}
        self._record_id_by_object: dict[str, str] = {}
        self._graphs_by_root: dict[str, _CanonicalGraphRecord] = {}
        self._aliases: dict[str, str] = {}
        self._evidence_roots: dict[str, str] = {}
        self._events: list[EvidenceInvalidationEvent] = []
        self._event_keys: dict[str, EvidenceInvalidationEvent] = {}
        self._loading = True
        self._load_persisted_state()
        self._loading = False

    @property
    def repository_id(self) -> str:
        return self._repository_id

    @property
    def event_log(self) -> tuple[EvidenceInvalidationEvent, ...]:
        return tuple(self._events)

    @property
    def authoritative_record_count(self) -> int:
        return len(self._records_by_id)

    def _mac(self, payload: bytes) -> str:
        return (
            "hmac-sha256:" + hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        )

    def _verify_mac(self, payload: bytes, claimed: str) -> bool:
        return hmac.compare_digest(self._mac(payload), claimed)

    def _open_record(
        self,
        *,
        object_id: str,
        card: EvidenceCard,
    ) -> tuple[_EvidenceRecord, EvidenceResolution]:
        artifact = self._artifacts.get(object_id)
        if artifact is None:
            raise ValueError(f"unknown canonical evidence object: {object_id}")
        if not self._authority._verify_artifact(artifact):
            raise ValueError("canonical evidence object authority HMAC mismatch")
        try:
            envelope = json.loads(artifact.artifact_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("canonical evidence object must contain JSON") from error
        if not isinstance(envelope, Mapping):
            raise ValueError("canonical evidence object must contain a record")
        required = {
            "canonical_object_id",
            "evidence_class",
            "evidence_id",
            "external_artifact",
            "external_proof",
            "frozen_identity",
            "proof_receipt_id",
            "schema_version",
            "store_id",
            "transition",
        }
        if set(envelope) != required or envelope.get("schema_version") != (
            "bb.rl.phase5.production-transition.v3"
        ):
            raise ValueError("canonical evidence object has an unsupported schema")
        if envelope.get("store_id") != artifact.authority_id:
            raise ValueError("canonical evidence object trust-store identity mismatch")
        if envelope.get("canonical_object_id") != artifact.object_id:
            raise ValueError("canonical evidence object identity mismatch")
        if envelope.get("evidence_id") != card.evidence_id:
            raise ValueError("canonical evidence object does not bind the evidence ID")
        if envelope.get("evidence_class") != card.evidence_class.value:
            raise ValueError(
                "canonical evidence object does not bind the evidence class"
            )
        if card.artifact_uri != artifact.artifact_uri:
            raise ValueError("canonical evidence object does not bind the artifact URI")
        if card.artifact_sha256 != _sha256(artifact.artifact_bytes) or (
            card.artifact_size != len(artifact.artifact_bytes)
        ):
            raise ValueError(
                "canonical evidence object does not bind the artifact bytes"
            )
        identity = _identity_from_payload(envelope["frozen_identity"])
        semantic_kind = _derive_semantic_kind(envelope["transition"])
        if semantic_kind is None:
            if not (
                envelope["external_artifact"] is None
                and envelope["external_proof"] is None
                and envelope["proof_receipt_id"] is None
            ):
                raise ValueError(
                    "support evidence cannot carry frozen semantic provenance"
                )
        elif (
            not isinstance(envelope["external_artifact"], Mapping)
            or not isinstance(envelope["external_proof"], Mapping)
            or not isinstance(envelope["proof_receipt_id"], str)
            or not envelope["proof_receipt_id"]
        ):
            raise ValueError(
                "frozen semantic provenance requires a verified external receipt"
            )
        record_payload = _canonical_json_bytes(
            {
                "artifact_sha256": card.artifact_sha256,
                "canonical_object_id": artifact.object_id,
                "evidence_class": card.evidence_class.value,
                "evidence_id": card.evidence_id,
                "identity": _identity_payload(identity),
                "issuer": "phase5-server-evidence-repository",
                "semantic_kind": (
                    semantic_kind.value if semantic_kind is not None else None
                ),
            }
        )
        record_id = "record:" + hashlib.sha256(record_payload).hexdigest()
        record_hmac = self._mac(record_payload)
        existing_id = self._record_id_by_object.get(object_id)
        if existing_id is not None:
            existing = self._records_by_id[existing_id]
            if existing.record_id != record_id or existing.card != card:
                raise ValueError("canonical evidence objects are append-only")
            record = existing
        else:
            record = _EvidenceRecord(
                record_id=record_id,
                canonical_object_id=object_id,
                card=card,
                semantic_kind=semantic_kind,
                identity=identity,
                artifact_bytes=artifact.artifact_bytes,
                record_hmac=record_hmac,
            )
            self._records_by_id[record_id] = record
            self._record_id_by_object[object_id] = record_id
        resolution_payload = _canonical_json_bytes(
            {
                "record_id": record.record_id,
                "repository_id": self._repository_id,
            }
        )
        return record, EvidenceResolution(
            repository_id=self._repository_id,
            record_id=record.record_id,
            seal=self._mac(resolution_payload),
        )

    def _state_payload(self) -> dict[str, object]:
        graphs: dict[str, object] = {}
        for root, record in sorted(self._graphs_by_root.items()):
            object_bindings = {
                evidence_id: self._records_by_id[
                    resolution.record_id
                ].canonical_object_id
                for evidence_id, resolution in record.resolutions
            }
            graphs[root] = {
                "active_pointer": record.active_pointer.model_dump(mode="json"),
                "cards": [
                    card.model_dump(mode="json")
                    for card in sorted(
                        record.cards, key=lambda value: value.evidence_id
                    )
                ],
                "graph_hmac": record.graph_hmac,
                "nodes": [
                    node.model_dump(mode="json")
                    for node in sorted(record.nodes, key=lambda value: value.node_id)
                ],
                "object_bindings": dict(sorted(object_bindings.items())),
            }
        events = [
            {
                **json.loads(self._event_payload(event)),
                "event_hmac": event.event_hmac,
            }
            for event in self._events
        ]
        return {
            "aliases": dict(sorted(self._aliases.items())),
            "artifact_ids": sorted(self._artifacts),
            "effective_states": {
                root: {
                    node_id: state.value
                    for node_id, state in self._effective_states(root).items()
                }
                for root in sorted(self._graphs_by_root)
            },
            "events": events,
            "evidence_roots": dict(sorted(self._evidence_roots.items())),
            "graphs": graphs,
            "rejection_codes": {
                root: {
                    node_id: list(codes)
                    for node_id, codes in self._rejection_codes(root).items()
                }
                for root in sorted(self._graphs_by_root)
            },
            "repository_id": self._repository_id,
            "schema": "bb.rl.phase5.repository-state.v2",
        }

    def _persist_state(self) -> None:
        if not self._loading:
            self._authority._trust_store.commit_state(self._state_payload())

    def _load_persisted_state(self) -> None:
        loaded = self._authority._trust_store.load_state()
        if loaded is None:
            if self._artifacts or self._authority._trust_store.events():
                raise ValueError("Phase 5 authority state is incomplete")
            return
        payload = loaded[2]
        required = {
            "aliases",
            "artifact_ids",
            "effective_states",
            "events",
            "evidence_roots",
            "graphs",
            "rejection_codes",
            "repository_id",
            "schema",
        }
        if (
            set(payload) != required
            or payload["schema"] != "bb.rl.phase5.repository-state.v2"
            or payload["repository_id"] != self._repository_id
            or payload["artifact_ids"] != sorted(self._artifacts)
            or not isinstance(payload["graphs"], Mapping)
            or not isinstance(payload["aliases"], Mapping)
            or not isinstance(payload["evidence_roots"], Mapping)
            or not isinstance(payload["events"], list)
        ):
            raise ValueError("persisted Phase 5 repository state is invalid")
        for canonical_root, value in sorted(payload["graphs"].items()):
            if not isinstance(canonical_root, str) or not isinstance(value, Mapping):
                raise ValueError("persisted canonical graph is malformed")
            try:
                nodes = tuple(
                    EvidenceNode.model_validate(item) for item in value["nodes"]
                )
                pointer = ActiveStatusPointer.model_validate(value["active_pointer"])
                cards = tuple(
                    EvidenceCard.model_validate(item) for item in value["cards"]
                )
                bindings = value["object_bindings"]
                graph_hmac = value["graph_hmac"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("persisted canonical graph is malformed") from error
            if (
                set(value)
                != {
                    "active_pointer",
                    "cards",
                    "graph_hmac",
                    "nodes",
                    "object_bindings",
                }
                or not isinstance(bindings, Mapping)
                or not isinstance(graph_hmac, str)
            ):
                raise ValueError("persisted canonical graph is malformed")
            graph = EvidenceGraph(nodes=nodes, active_pointers=(pointer,))
            cards_by_id = _cards_by_id(cards)
            if set(bindings) != set(cards_by_id):
                raise ValueError("persisted canonical graph bindings are incomplete")
            resolutions: dict[str, EvidenceResolution] = {}
            records: dict[str, _EvidenceRecord] = {}
            for evidence_id in sorted(cards_by_id):
                object_id = bindings[evidence_id]
                if not isinstance(object_id, str):
                    raise ValueError("persisted canonical graph binding is malformed")
                record, resolution = self._open_record(
                    object_id=object_id,
                    card=cards_by_id[evidence_id],
                )
                records[evidence_id] = record
                resolutions[evidence_id] = resolution
            self._validate_typed_inventory(graph, records)
            root_payload = self._graph_root_payload(
                nodes=nodes,
                active_pointer=pointer,
                cards=cards,
                resolutions=resolutions,
            )
            if canonical_root != _sha256(root_payload):
                raise ValueError("persisted canonical graph root hash mismatch")
            expected_graph_hmac = self._mac(
                _canonical_json_bytes(
                    {
                        "canonical_root": canonical_root,
                        "repository_id": self._repository_id,
                    }
                )
            )
            if not hmac.compare_digest(graph_hmac, expected_graph_hmac):
                raise ValueError("persisted canonical graph signature mismatch")
            self._graphs_by_root[canonical_root] = _CanonicalGraphRecord(
                canonical_root=canonical_root,
                nodes=nodes,
                active_pointer=pointer,
                cards=cards,
                resolutions=tuple(sorted(resolutions.items())),
                graph_hmac=graph_hmac,
            )
        aliases = dict(payload["aliases"])
        if any(
            not isinstance(alias, str)
            or not alias
            or not isinstance(root, str)
            or root not in self._graphs_by_root
            for alias, root in aliases.items()
        ) or set(aliases.values()) != set(self._graphs_by_root):
            raise ValueError("persisted canonical graph alias closure is invalid")
        self._aliases = aliases
        derived_evidence_roots: dict[str, str] = {}
        for root, record in self._graphs_by_root.items():
            for card in record.cards:
                previous = derived_evidence_roots.setdefault(card.evidence_id, root)
                if previous != root:
                    raise ValueError(
                        "persisted evidence is bound to multiple graph roots"
                    )
        if dict(payload["evidence_roots"]) != derived_evidence_roots:
            raise ValueError("persisted evidence-to-root closure is invalid")
        self._evidence_roots = derived_evidence_roots
        persisted_event_values: list[dict[str, object]] = []
        for expected_sequence, value in enumerate(payload["events"], 1):
            if not isinstance(value, Mapping):
                raise ValueError("persisted invalidation event is malformed")
            event_value = dict(value)
            event_hmac = event_value.pop("event_hmac", None)
            required_event = {
                "affected_node_ids",
                "canonical_root",
                "event_key",
                "mutation",
                "rejection_code",
                "root_node_id",
                "sequence",
                "state_overrides",
            }
            if (
                set(event_value) != required_event
                or event_value["sequence"] != expected_sequence
                or event_value["canonical_root"] not in self._graphs_by_root
                or not isinstance(event_hmac, str)
            ):
                raise ValueError("persisted invalidation event is malformed")
            nodes = {
                node.node_id
                for node in self._graphs_by_root[event_value["canonical_root"]].nodes
            }
            try:
                affected = tuple(event_value["affected_node_ids"])
                overrides = tuple(
                    (node_id, EvidenceState(state))
                    for node_id, state in event_value["state_overrides"]
                )
            except (TypeError, ValueError) as error:
                raise ValueError("persisted invalidation event is malformed") from error
            if (
                not affected
                or set(affected) - nodes
                or event_value["root_node_id"] not in nodes
                or {node_id for node_id, _ in overrides} != set(affected)
            ):
                raise ValueError(
                    "persisted invalidation event graph closure is invalid"
                )
            event = EvidenceInvalidationEvent(
                sequence=expected_sequence,
                canonical_root=event_value["canonical_root"],
                event_key=event_value["event_key"],
                mutation=event_value["mutation"],
                rejection_code=event_value["rejection_code"],
                root_node_id=event_value["root_node_id"],
                state_overrides=overrides,
                affected_node_ids=affected,
                event_hmac=event_hmac,
            )
            if not self._verify_mac(self._event_payload(event), event.event_hmac):
                raise ValueError("persisted invalidation event signature mismatch")
            if event.event_key in self._event_keys:
                raise ValueError("persisted invalidation event key is duplicated")
            self._events.append(event)
            self._event_keys[event.event_key] = event
            persisted_event_values.append(event_value)
        if list(self._authority._trust_store.events()) != persisted_event_values:
            raise ValueError("Phase 5 event log and signed state disagree")
        effective = payload["effective_states"]
        rejections = payload["rejection_codes"]
        if not isinstance(effective, Mapping) or not isinstance(rejections, Mapping):
            raise ValueError("persisted effective state is malformed")
        expected_effective = {
            root: {
                node_id: state.value
                for node_id, state in self._effective_states(root).items()
            }
            for root in sorted(self._graphs_by_root)
        }
        expected_rejections = {
            root: {
                node_id: list(codes)
                for node_id, codes in self._rejection_codes(root).items()
            }
            for root in sorted(self._graphs_by_root)
        }
        if effective != expected_effective or rejections != expected_rejections:
            raise ValueError("persisted effective state does not match event replay")

    @staticmethod
    def _graph_root_payload(
        *,
        nodes: Sequence[EvidenceNode],
        active_pointer: ActiveStatusPointer,
        cards: Sequence[EvidenceCard],
        resolutions: Mapping[str, EvidenceResolution],
    ) -> bytes:
        return _canonical_json_bytes(
            {
                "active_status_target": active_pointer.target_node_id,
                "cards": [
                    card.model_dump(mode="json")
                    for card in sorted(cards, key=lambda item: item.evidence_id)
                ],
                "nodes": [
                    node.model_dump(mode="json")
                    for node in sorted(nodes, key=lambda item: item.node_id)
                ],
                "records": {
                    evidence_id: resolution.record_id
                    for evidence_id, resolution in sorted(resolutions.items())
                },
            }
        )

    def open_graph(
        self,
        graph_alias: str,
        *,
        nodes: Sequence[EvidenceNode],
        active_pointers: Sequence[ActiveStatusPointer],
        cards: Sequence[EvidenceCard],
        object_bindings: Mapping[str, str],
    ) -> EvidenceGraph:
        if not graph_alias:
            raise ValueError("canonical evidence graphs require an alias")
        graph = EvidenceGraph(nodes=nodes, active_pointers=active_pointers)
        cards_by_id = _cards_by_id(cards)
        node_evidence_ids = {
            node.evidence_id for node in graph.nodes if node.evidence_id is not None
        }
        if set(cards_by_id) != node_evidence_ids:
            raise ValueError(
                "canonical evidence graph cards must exactly cover evidence and review nodes"
            )
        if set(object_bindings) != set(cards_by_id):
            raise ValueError(
                "canonical evidence graph object bindings must exactly cover its cards"
            )
        resolutions: dict[str, EvidenceResolution] = {}
        records: dict[str, _EvidenceRecord] = {}
        for evidence_id in sorted(cards_by_id):
            record, resolution = self._open_record(
                object_id=object_bindings[evidence_id],
                card=cards_by_id[evidence_id],
            )
            records[evidence_id] = record
            resolutions[evidence_id] = resolution
        self._validate_typed_inventory(graph, records)
        root_payload = self._graph_root_payload(
            nodes=nodes,
            active_pointer=graph.active_pointer,
            cards=cards,
            resolutions=resolutions,
        )
        canonical_root = _sha256(root_payload)
        existing_alias_root = self._aliases.get(graph_alias)
        if existing_alias_root is not None and existing_alias_root != canonical_root:
            raise ValueError("canonical evidence graph aliases are immutable")
        conflicting = sorted(
            evidence_id
            for evidence_id in records
            if evidence_id in self._evidence_roots
            and self._evidence_roots[evidence_id] != canonical_root
        )
        if conflicting:
            raise ValueError(
                "canonical evidence is already bound to another graph root: "
                + conflicting[0]
            )
        graph_hmac = self._mac(
            _canonical_json_bytes(
                {
                    "canonical_root": canonical_root,
                    "repository_id": self._repository_id,
                }
            )
        )
        record = self._graphs_by_root.get(canonical_root)
        if record is None:
            record = _CanonicalGraphRecord(
                canonical_root=canonical_root,
                nodes=tuple(nodes),
                active_pointer=graph.active_pointer,
                cards=tuple(cards),
                resolutions=tuple(sorted(resolutions.items())),
                graph_hmac=graph_hmac,
            )
            self._graphs_by_root[canonical_root] = record
        self._aliases[graph_alias] = canonical_root
        for evidence_id in records:
            self._evidence_roots[evidence_id] = canonical_root
        self._persist_state()
        return self._graph_from_record(record)

    def resolve_graph(self, graph_alias: str) -> EvidenceGraph:
        try:
            root = self._aliases[graph_alias]
        except KeyError as error:
            raise ValueError(
                f"unknown canonical evidence graph: {graph_alias}"
            ) from error
        return self._graph_from_record(self._graphs_by_root[root])

    def _graph_from_record(self, record: _CanonicalGraphRecord) -> EvidenceGraph:
        return EvidenceGraph(
            nodes=record.nodes,
            active_pointers=(record.active_pointer,),
            _authority_token=_AUTHORITATIVE_GRAPH_TOKEN,
            _repository=self,
            _canonical_root=record.canonical_root,
            _graph_hmac=record.graph_hmac,
            _canonical_cards=record.cards,
            _resolutions=dict(record.resolutions),
        )

    def _validate_typed_inventory(
        self,
        graph: EvidenceGraph,
        records: Mapping[str, _EvidenceRecord],
    ) -> None:
        by_kind: dict[EvidenceNodeKind, list[EvidenceNode]] = defaultdict(list)
        by_id = {node.node_id: node for node in graph.nodes}
        for node in graph.nodes:
            by_kind[node.node_kind].append(node)
        for kind in (
            EvidenceNodeKind.STATUS,
            EvidenceNodeKind.CLAIM,
            EvidenceNodeKind.POINT,
            EvidenceNodeKind.PROMOTION,
        ):
            if len(by_kind[kind]) != 1:
                raise ValueError(
                    "canonical award graph requires exactly one " + kind.value + " node"
                )
        if not by_kind[EvidenceNodeKind.REVIEW]:
            raise ValueError("canonical award graph requires a review node")
        status = by_kind[EvidenceNodeKind.STATUS][0]
        claim = by_kind[EvidenceNodeKind.CLAIM][0]
        point = by_kind[EvidenceNodeKind.POINT][0]
        promotion = by_kind[EvidenceNodeKind.PROMOTION][0]
        if graph.active_pointer.target_node_id != status.node_id:
            raise ValueError("canonical award graph status pointer is not canonical")
        for review in by_kind[EvidenceNodeKind.REVIEW]:
            if (
                review.evidence_id is None
                or records[review.evidence_id].card.evidence_class
                is not EvidenceClass.REVIEW_VERDICT
                or not self._is_ancestor(by_id, claim.node_id, review.node_id)
                or not self._is_ancestor(by_id, review.node_id, point.node_id)
            ):
                raise ValueError(
                    "canonical award graph requires claim-to-review-to-point edges"
                )
        downstream_authorities = [
            node
            for node in by_kind[EvidenceNodeKind.EVIDENCE]
            if node.evidence_id is not None
            and records[node.evidence_id].semantic_kind
            is EvidenceSemanticKind.SCOPED_AUTHORITY_DECISION
            and self._is_ancestor(by_id, point.node_id, node.node_id)
            and self._is_ancestor(by_id, node.node_id, promotion.node_id)
        ]
        if len(downstream_authorities) != 1:
            raise ValueError(
                "canonical award graph requires one point-to-authority-to-promotion path"
            )
        authority_id = downstream_authorities[0].node_id
        for node in by_kind[EvidenceNodeKind.EVIDENCE]:
            if node.node_id == authority_id:
                continue
            if not self._is_ancestor(by_id, status.node_id, node.node_id) or not (
                self._is_ancestor(by_id, node.node_id, claim.node_id)
            ):
                raise ValueError(
                    "canonical award graph omits a required evidence-to-claim edge"
                )

    @staticmethod
    def _is_ancestor(
        nodes: Mapping[str, EvidenceNode],
        ancestor: str,
        descendant: str,
    ) -> bool:
        pending = list(nodes[descendant].dependencies)
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == ancestor:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(nodes[current].dependencies)
        return False

    def owns_graph(self, graph: EvidenceGraph) -> bool:
        root = graph._canonical_root
        if (
            root is None
            or graph._graph_hmac is None
            or root not in self._graphs_by_root
            or graph._repository is None
            or graph._repository.repository_id != self._repository_id
        ):
            return False
        payload = _canonical_json_bytes(
            {"canonical_root": root, "repository_id": self._repository_id}
        )
        record = self._graphs_by_root[root]
        return (
            self._verify_mac(payload, graph._graph_hmac)
            and graph._graph_hmac == record.graph_hmac
            and graph.nodes
            == tuple(sorted(record.nodes, key=lambda node: node.node_id))
        )

    def validate_award_graph(
        self,
        graph: EvidenceGraph,
        *,
        supplied_cards: Sequence[EvidenceCard],
        evidence_ids: Sequence[str],
        review_ids: Sequence[str],
    ) -> None:
        if not self.owns_graph(graph):
            raise ValueError(
                "award graph is not owned by the server evidence repository"
            )
        cards_by_id = _cards_by_id(supplied_cards)
        if set(cards_by_id) != set(graph._canonical_cards):
            raise ValueError("award cards do not match the canonical graph inventory")
        if any(
            cards_by_id[evidence_id] != canonical
            for evidence_id, canonical in graph._canonical_cards.items()
        ):
            raise ValueError("award card does not match its canonical server record")
        by_id = {node.node_id: node for node in graph.nodes}
        claim = next(
            node for node in graph.nodes if node.node_kind is EvidenceNodeKind.CLAIM
        )
        for evidence_id in evidence_ids:
            node_id = graph.node_id_for_evidence(evidence_id)
            if (
                node_id is None
                or by_id[node_id].node_kind is not EvidenceNodeKind.EVIDENCE
                or not self._is_ancestor(by_id, node_id, claim.node_id)
            ):
                raise ValueError(
                    "award evidence is absent from the canonical typed graph"
                )
        canonical_reviews = {
            node.evidence_id
            for node in graph.nodes
            if node.node_kind is EvidenceNodeKind.REVIEW
        }
        if set(review_ids) != canonical_reviews:
            raise ValueError("award reviews do not match the canonical typed graph")

    def _record_for_evidence(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
    ) -> _EvidenceRecord:
        if not self.owns_graph(graph):
            raise ValueError(
                "award graph is not owned by the server evidence repository"
            )
        resolution = graph._resolutions.get(evidence_id)
        if resolution is None or resolution.repository_id != self._repository_id:
            raise ValueError("authoritative provenance resolution is unavailable")
        payload = _canonical_json_bytes(
            {
                "record_id": resolution.record_id,
                "repository_id": resolution.repository_id,
            }
        )
        if not self._verify_mac(payload, resolution.seal):
            raise ValueError("authoritative provenance resolution seal mismatch")
        try:
            return self._records_by_id[resolution.record_id]
        except KeyError as error:
            raise ValueError(
                "authoritative provenance record is unavailable"
            ) from error

    def resolve_frozen_floor(
        self,
        graph: EvidenceGraph,
        card: EvidenceCard,
        *,
        proof_floor: str,
    ) -> str | None:
        record = self._record_for_evidence(graph, card.evidence_id)
        if record.card != card:
            raise ValueError(
                "frozen semantic provenance does not bind the evidence card"
            )
        if record.semantic_kind is None:
            raise ValueError("frozen semantic provenance is unavailable")
        return _g2_rejection_code(record.semantic_kind, proof_floor=proof_floor)

    def reject_frozen_floor(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        rejection_code: str,
    ) -> EvidenceInvalidationResult:
        record = self._record_for_evidence(graph, evidence_id)
        node_id = graph.node_id_for_evidence(evidence_id)
        if node_id is None:
            raise ValueError(f"unknown canonical evidence binding: {evidence_id}")
        return self._append_invalidation(
            graph,
            mutation="g2_provenance_rejection",
            rejection_code=rejection_code,
            root_node_id=node_id,
            root_state=EvidenceState.INVALID,
            observation_digest=record.record_id,
        )

    def observe_artifact(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        observed_bytes: bytes | None,
    ) -> EvidenceInvalidationResult:
        record = self._record_for_evidence(graph, evidence_id)
        node_id = graph.node_id_for_evidence(evidence_id)
        if node_id is None:
            raise ValueError(f"unknown canonical evidence binding: {evidence_id}")
        if observed_bytes is None:
            mutation = EvidenceMutation.MISSING_OBJECT
            observation_digest = "absent"
        elif not isinstance(observed_bytes, bytes):
            raise ValueError(
                "authoritative artifact observation requires bytes or absence"
            )
        elif observed_bytes == record.artifact_bytes:
            raise ValueError("authoritative artifact observation did not change")
        else:
            mutation = EvidenceMutation.CHANGED_BYTES
            observation_digest = _sha256(observed_bytes)
        return self._append_invalidation(
            graph,
            mutation=mutation.value,
            rejection_code=_G3_REJECTION_CODES[mutation],
            root_node_id=node_id,
            root_state=EvidenceState.INVALID,
            observation_digest=observation_digest,
        )

    def observe_rerun(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        exit_code: int,
    ) -> EvidenceInvalidationResult:
        self._record_for_evidence(graph, evidence_id)
        node_id = graph.node_id_for_evidence(evidence_id)
        if (
            node_id is None
            or graph._nodes[node_id].node_kind is not EvidenceNodeKind.EVIDENCE
        ):
            raise ValueError("failed-rerun observation requires canonical evidence")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError(
                "authoritative rerun observation requires an integer exit code"
            )
        if exit_code == 0:
            raise ValueError("authoritative rerun observation did not fail")
        mutation = EvidenceMutation.FAILED_RERUN
        return self._append_invalidation(
            graph,
            mutation=mutation.value,
            rejection_code=_G3_REJECTION_CODES[mutation],
            root_node_id=node_id,
            root_state=EvidenceState.SUPERSEDED,
            observation_digest=f"exit:{exit_code}",
        )

    def observe_identity(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        observed: FrozenEvidenceIdentity,
    ) -> EvidenceInvalidationResult:
        record = self._record_for_evidence(graph, evidence_id)
        if not isinstance(observed, FrozenEvidenceIdentity):
            raise ValueError("authoritative identity observation is malformed")
        changed = {
            field
            for field in FrozenEvidenceIdentity.__dataclass_fields__
            if getattr(record.identity, field) != getattr(observed, field)
        }
        if not changed:
            raise ValueError("authoritative evidence identity did not drift")
        node_id = graph.node_id_for_evidence(evidence_id)
        if node_id is None:
            raise ValueError(f"unknown canonical evidence binding: {evidence_id}")
        if changed.issubset(_CONTEXT_IDENTITY_FIELDS):
            mutation = EvidenceMutation.CROSS_CONTEXT_REUSE
            root_state = EvidenceState.INVALID
        elif changed == {"source_head"}:
            if graph._nodes[node_id].node_kind is not EvidenceNodeKind.REVIEW:
                raise ValueError("old-head invalidation requires a canonical review")
            mutation = EvidenceMutation.OLD_HEAD_REVIEW
            root_state = EvidenceState.STALE
        elif changed == {"threshold_digest"}:
            mutation = EvidenceMutation.THRESHOLD_DRIFT
            root_state = EvidenceState.STALE
        else:
            raise ValueError(
                "authoritative evidence identity drift spans more than one invalidation class"
            )
        return self._append_invalidation(
            graph,
            mutation=mutation.value,
            rejection_code=_G3_REJECTION_CODES[mutation],
            root_node_id=node_id,
            root_state=root_state,
            observation_digest=_sha256(
                _canonical_json_bytes(_identity_payload(observed))
            ),
        )

    def observe_active_status(
        self,
        graph: EvidenceGraph,
        *,
        observed_target_node_id: str,
    ) -> EvidenceInvalidationResult:
        if not self.owns_graph(graph):
            raise ValueError(
                "award graph is not owned by the server evidence repository"
            )
        root_node_id = graph.active_pointer.target_node_id
        if observed_target_node_id == root_node_id:
            raise ValueError("authoritative status observation is still current")
        mutation = EvidenceMutation.SUPERSEDED_STATUS
        return self._append_invalidation(
            graph,
            mutation=mutation.value,
            rejection_code=_G3_REJECTION_CODES[mutation],
            root_node_id=root_node_id,
            root_state=EvidenceState.SUPERSEDED,
            observation_digest=observed_target_node_id,
        )

    def _append_invalidation(
        self,
        graph: EvidenceGraph,
        *,
        mutation: str,
        rejection_code: str,
        root_node_id: str,
        root_state: EvidenceState,
        observation_digest: str,
    ) -> EvidenceInvalidationResult:
        if not self.owns_graph(graph) or graph._canonical_root is None:
            raise ValueError(
                "award graph is not owned by the server evidence repository"
            )
        event_key = _sha256(
            _canonical_json_bytes(
                {
                    "canonical_root": graph._canonical_root,
                    "mutation": mutation,
                    "observation": observation_digest,
                    "root_node_id": root_node_id,
                }
            )
        )
        existing = self._event_keys.get(event_key)
        if existing is not None:
            return self._event_result(existing)
        if mutation == EvidenceMutation.OLD_HEAD_REVIEW.value:
            reviewed_claims = [
                dependency
                for dependency in graph._nodes[root_node_id].dependencies
                if graph._nodes[dependency].node_kind is EvidenceNodeKind.CLAIM
            ]
            if len(reviewed_claims) != 1:
                raise ValueError("canonical old-head review has no reviewed claim")
            affected = set(graph._transitive_descendants(reviewed_claims[0]))
            overrides = {node_id: EvidenceState.REVOKED for node_id in affected}
            overrides[root_node_id] = EvidenceState.STALE
        else:
            affected = set(graph._transitive_descendants(root_node_id))
            overrides = {
                node_id: EvidenceState.REVOKED
                for node_id in affected
                if node_id != root_node_id
            }
            overrides[root_node_id] = root_state
        event_value = {
            "affected_node_ids": sorted(affected),
            "canonical_root": graph._canonical_root,
            "event_key": event_key,
            "mutation": mutation,
            "rejection_code": rejection_code,
            "root_node_id": root_node_id,
            "sequence": len(self._events) + 1,
            "state_overrides": [
                (node_id, state.value) for node_id, state in sorted(overrides.items())
            ],
        }
        event = EvidenceInvalidationEvent(
            sequence=event_value["sequence"],
            canonical_root=graph._canonical_root,
            event_key=event_key,
            mutation=mutation,
            rejection_code=rejection_code,
            root_node_id=root_node_id,
            state_overrides=tuple(sorted(overrides.items())),
            affected_node_ids=tuple(sorted(affected)),
            event_hmac=self._mac(_canonical_json_bytes(event_value)),
        )
        self._events.append(event)
        self._event_keys[event_key] = event
        self._authority._trust_store.append_event(event_value)
        self._persist_state()
        return self._event_result(event)

    def _event_payload(self, event: EvidenceInvalidationEvent) -> bytes:
        return _canonical_json_bytes(
            {
                "affected_node_ids": list(event.affected_node_ids),
                "canonical_root": event.canonical_root,
                "event_key": event.event_key,
                "mutation": event.mutation,
                "rejection_code": event.rejection_code,
                "root_node_id": event.root_node_id,
                "sequence": event.sequence,
                "state_overrides": [
                    (node_id, state.value) for node_id, state in event.state_overrides
                ],
            }
        )

    def _events_for_root(
        self, canonical_root: str
    ) -> tuple[EvidenceInvalidationEvent, ...]:
        events = tuple(
            event for event in self._events if event.canonical_root == canonical_root
        )
        for event in events:
            if not self._verify_mac(self._event_payload(event), event.event_hmac):
                raise ValueError("authoritative invalidation event HMAC mismatch")
        return events

    def _effective_states(self, canonical_root: str) -> dict[str, EvidenceState]:
        try:
            record = self._graphs_by_root[canonical_root]
        except KeyError as error:
            raise ValueError("unknown canonical evidence graph root") from error
        states = {node.node_id: node.state for node in record.nodes}
        for event in self._events_for_root(canonical_root):
            states.update(dict(event.state_overrides))
        return {node_id: states[node_id] for node_id in sorted(states)}

    def _rejection_codes(self, canonical_root: str) -> dict[str, tuple[str, ...]]:
        codes: dict[str, set[str]] = defaultdict(set)
        for event in self._events_for_root(canonical_root):
            for node_id in event.affected_node_ids:
                codes[node_id].add(event.rejection_code)
        return {
            node_id: tuple(sorted(node_codes))
            for node_id, node_codes in sorted(codes.items())
        }

    def _event_result(
        self, event: EvidenceInvalidationEvent
    ) -> EvidenceInvalidationResult:
        states = self._effective_states(event.canonical_root)
        return EvidenceInvalidationResult(
            rejection_code=event.rejection_code,
            root_node_id=event.root_node_id,
            affected_node_ids=event.affected_node_ids,
            effective_states=tuple(
                (node_id, states[node_id]) for node_id in event.affected_node_ids
            ),
        )


def _make_score_capability_gate():
    token = object()

    def is_score_capability_token(candidate: object) -> bool:
        return candidate is token

    def issue(
        repository: ServerEvidenceRepository,
    ) -> _ServerScoreCapability:
        return _ServerScoreCapability(repository, _token=token)

    return is_score_capability_token, issue


(
    _is_score_capability_token,
    _issue_score_capability_internal,
) = _make_score_capability_gate()
del _make_score_capability_gate


class _ServerScoreCapability:
    __slots__ = ("__repository", "__seal")

    def __init__(
        self,
        repository: ServerEvidenceRepository,
        *,
        _token: object,
    ) -> None:
        if not _is_score_capability_token(_token):
            raise ValueError(
                "score authority capabilities are issued only by server composition"
            )
        self.__repository = repository
        self.__seal = repository._mac(
            _canonical_json_bytes(
                {
                    "capability": "score-authority",
                    "repository_id": repository.repository_id,
                }
            )
        )

    def _repository_for_graph(
        self, graph: EvidenceGraph
    ) -> ServerEvidenceRepository | None:
        repository = self.__repository
        expected = repository._mac(
            _canonical_json_bytes(
                {
                    "capability": "score-authority",
                    "repository_id": repository.repository_id,
                }
            )
        )
        if not hmac.compare_digest(expected, self.__seal):
            raise ValueError("score authority capability seal mismatch")
        return repository if repository.owns_graph(graph) else None


def _issue_score_capability(
    repository: ServerEvidenceRepository,
    *,
    _server_token: object,
) -> _ServerScoreCapability:
    if not _is_repository_token(_server_token):
        raise ValueError("score authority capabilities are server-owned")
    return _issue_score_capability_internal(repository)


def _authoritative_repository_for_graph(
    capability: object,
    graph: EvidenceGraph,
) -> ServerEvidenceRepository | None:
    if not isinstance(capability, _ServerScoreCapability):
        return None
    return capability._repository_for_graph(graph)


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
    "CanonicalEvidenceArtifact",
    "DERIVATION_INPUT_ERROR",
    "DERIVATION_PIN_ERROR",
    "EVIDENCE_CYCLE_ERROR",
    "EVIDENCE_NOT_CURRENT_ERROR",
    "EvidenceEligibilityError",
    "EvidenceGraph",
    "EvidenceInvalidationEvent",
    "EvidenceInvalidationResult",
    "EvidenceMutation",
    "EvidenceResolution",
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
