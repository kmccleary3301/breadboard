from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import wraps
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import signal
import socket
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from typing import Callable, Mapping, ParamSpec, Sequence, TypeVar

from breadboard.rl.phase5.authority_ipc import parse_canonical_object
from breadboard.rl.phase5.authority_contract import (
    ExternalArtifactClaim,
    IBMTargetExecutionResult,
    ProductionTransitionResult,
    ScopedAuthorityDecisionResult,
    SupportEvidenceResult,
    TargetTrainingExecutionResult,
)
from phase5_authority_evidence import (
    CanonicalEvidenceArtifact,
    EvidenceGraph,
    EvidenceInvalidationResult,
    FrozenEvidenceIdentity,
    _canonical_json_bytes,
    _derive_semantic_kind,
    _identity_payload,
    _take_server_evidence_composition_access,
)
from breadboard.rl.phase5.external_proof import (
    _PinnedExternalProofVerifier,
    _PinnedExternalVerifier,
)
from breadboard.rl.phase5.models import (
    ActiveStatusPointer,
    EvidenceCard,
    EvidenceClass,
    EvidenceNode,
)
from phase5_authority_store import FileTrustStore, _open_deployment_store



_MAX_MESSAGE_BYTES = 1_048_576
_MAX_SESSION_HANDLES = 6
_MAX_ACTIVE_SESSIONS = 8
_HANDLER_SHUTDOWN_SECONDS = 2.0
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"production transition requires {field}")




def _read_supervisor_fd(fd: int, *, limit: int) -> bytes:
    if fd < 0 or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
        raise RuntimeError("Phase 5 supervisor descriptor is not read-only")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(4_096, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise RuntimeError("Phase 5 supervisor descriptor is oversized")
    return b"".join(chunks)


@dataclass(frozen=True)
class _DeploymentConfig:
    deployment_id: str
    state_root: Path
    signing_key: bytes
    public_key_digest: str
    external_signers: tuple[_PinnedExternalVerifier, ...]

    @classmethod
    def from_supervisor_fds(
        cls,
        *,
        config_fd: int,
        signing_key_fd: int,
    ) -> tuple[_DeploymentConfig, str]:
        try:
            value = parse_canonical_object(
                _read_supervisor_fd(config_fd, limit=16_384)
            )
        except ValueError as error:
            raise RuntimeError("Phase 5 supervisor configuration is invalid") from error
        required = {
            "deployment_id",
            "endpoint",
            "external_signers",
            "public_key",
            "public_key_digest",
            "schema",
            "state_root",
        }
        if set(value) != required or value["schema"] != (
            "bb.rl.phase5.authority-service.v1"
        ):
            raise RuntimeError("Phase 5 supervisor configuration schema is invalid")
        deployment_id = value["deployment_id"]
        endpoint = value["endpoint"]
        state_root_text = value["state_root"]
        public_key_text = value["public_key"]
        public_key_digest = value["public_key_digest"]
        if not all(
            isinstance(item, str) and item
            for item in (
                deployment_id,
                endpoint,
                state_root_text,
                public_key_text,
                public_key_digest,
            )
        ):
            raise RuntimeError("Phase 5 supervisor configuration binding is invalid")
        state_root = Path(state_root_text)
        if (
            not Path(endpoint).is_absolute()
            or not state_root.is_absolute()
            or ".." in state_root.parts
        ):
            raise RuntimeError("Phase 5 supervisor paths must be absolute and fixed")
        signing_key = _read_supervisor_fd(signing_key_fd, limit=32)
        if len(signing_key) != 32:
            raise RuntimeError("Phase 5 server signing key must contain exactly 32 bytes")
        derived_public_key = FileTrustStore.public_key_for(signing_key)
        if (
            public_key_text != derived_public_key.hex()
            or public_key_digest
            != "sha256:" + hashlib.sha256(derived_public_key).hexdigest()
        ):
            raise RuntimeError(
                "Phase 5 server public-key pin does not match its private key"
            )
        signer_values = value["external_signers"]
        if not isinstance(signer_values, list):
            raise RuntimeError("Phase 5 external proof signer configuration is invalid")
        external_signers: list[_PinnedExternalVerifier] = []
        try:
            for signer in signer_values:
                if not isinstance(signer, dict) or set(signer) != {
                    "key_id",
                    "public_key",
                    "role",
                }:
                    raise ValueError
                public_key = bytes.fromhex(signer["public_key"])
                if (
                    signer["role"]
                    not in {
                        "ibm-target-execution",
                        "target-training-execution",
                        "scoped-authority-decision",
                    }
                    or not isinstance(signer["key_id"], str)
                    or not signer["key_id"]
                    or len(public_key) != 32
                ):
                    raise ValueError
                external_signers.append(
                    _PinnedExternalVerifier(
                        role=signer["role"],
                        key_id=signer["key_id"],
                        public_key=public_key,
                    )
                )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(
                "Phase 5 external proof signer configuration is invalid"
            ) from error
        if external_signers and {
            signer.role for signer in external_signers
        } != {
            "ibm-target-execution",
            "target-training-execution",
            "scoped-authority-decision",
        }:
            raise RuntimeError(
                "Phase 5 external proof signer roles are incomplete"
            )
        keys = [derived_public_key, *(signer.public_key for signer in external_signers)]
        key_ids = [signer.key_id for signer in external_signers]
        if len(set(keys)) != len(keys) or len(set(key_ids)) != len(key_ids):
            raise RuntimeError(
                "Phase 5 server and external proof signer identities must be distinct"
            )
        return (
            cls(
                deployment_id,
                state_root,
                signing_key,
                public_key_digest,
                tuple(external_signers),
            ),
            endpoint,
        )

    @property
    def immutable_identity(self) -> tuple[str, ...]:
        signer_identities = tuple(
            value
            for signer in self.external_signers
            for value in (
                signer.role,
                signer.key_id,
                hashlib.sha256(signer.public_key).hexdigest(),
            )
        )
        return (
            self.deployment_id,
            str(self.state_root),
            self.public_key_digest,
            *signer_identities,
        )


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _serialized_worker_call(
    method: Callable[_P, _R],
) -> Callable[_P, _R]:
    @wraps(method)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        instance = args[0]
        with instance._operation_lock:
            return method(*args, **kwargs)

    return wrapped


class _SerializedScoreEngine:
    __slots__ = ("_engine", "_operation_lock")

    def __init__(self, engine: object, operation_lock: threading.RLock) -> None:
        self._engine = engine
        self._operation_lock = operation_lock

    @property
    def catalog(self) -> tuple[object, ...]:
        return self._engine.catalog

    def evaluate(self, *args: object, **kwargs: object) -> object:
        with self._operation_lock:
            return self._engine.evaluate(*args, **kwargs)


class Phase5ProductionServer:
    """Server-owned Phase 5 evidence authority and score composition root."""

    __slots__ = (
        "__authority",
        "__bindings",
        "__config",
        "__consumed_receipt_ids",
        "__proof_verifier",
        "__repository",
        "__evidence_composition",
        "__score_capability",
        "_operation_lock",
    )

    def __init__(
        self,
        *,
        _token: object | None = None,
        config: _DeploymentConfig | None = None,
    ) -> None:
        if config is None:
            raise ValueError("Phase 5 worker authority requires deployment configuration")
        self._operation_lock = threading.RLock()
        evidence_composition = _take_server_evidence_composition_access()
        store = _open_deployment_store(
            root=config.state_root,
            deployment_key=config.signing_key,
            expected_public_key_digest=config.public_key_digest,
        )
        authority = evidence_composition.open_authority(store)
        proof_verifier = (
            _PinnedExternalProofVerifier(
                {signer.role: signer for signer in config.external_signers}
            )
            if config.external_signers
            else None
        )
        bindings: dict[str, str] = {}
        consumed_receipt_ids: set[str] = set()
        for object_id, artifact in authority._artifacts.items():
            try:
                payload = json.loads(artifact.artifact_bytes)
                evidence_id = payload["evidence_id"]
                evidence_class = EvidenceClass(payload["evidence_class"])
                identity = FrozenEvidenceIdentity(**payload["frozen_identity"])
                external_artifact = payload["external_artifact"]
                external_proof = payload["external_proof"]
                receipt_id = payload["proof_receipt_id"]
                transition = payload["transition"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    "persisted production transition is malformed"
                ) from error
            if not isinstance(evidence_id, str) or evidence_id in bindings:
                raise ValueError("persisted production evidence IDs must be unique")
            if external_artifact is None or external_proof is None:
                if not (
                    external_artifact is None
                    and external_proof is None
                    and receipt_id is None
                    and transition == {"record": "support"}
                ):
                    raise ValueError("persisted production proof binding is malformed")
            else:
                if proof_verifier is None:
                    raise ValueError(
                        "persisted external proofs require startup-pinned trust roots"
                    )
                verified = proof_verifier.verify(
                    evidence_id=evidence_id,
                    evidence_class=evidence_class,
                    identity=identity,
                    artifact_bytes=_canonical_json_bytes(external_artifact),
                    proof_bytes=_canonical_json_bytes(external_proof),
                    consumed_receipt_ids=consumed_receipt_ids,
                )
                if (
                    receipt_id != verified.receipt_id
                    or transition
                    != self._normalized_verified_transition(verified.transition)
                ):
                    raise ValueError("persisted production proof binding is malformed")
                consumed_receipt_ids.add(verified.receipt_id)
            bindings[evidence_id] = object_id
        repository = evidence_composition.open_repository(authority)
        self.__config = config
        self.__authority = authority
        self.__repository = repository
        self.__evidence_composition = evidence_composition
        self.__bindings = bindings
        self.__consumed_receipt_ids = consumed_receipt_ids
        self.__proof_verifier = proof_verifier
        self.__score_capability = evidence_composition.issue_score_capability(
            repository
        )

    @property
    def deployment_id(self) -> str:
        return self.__config.deployment_id

    @property
    def public_key_digest(self) -> str:
        return self.__config.public_key_digest

    @_serialized_worker_call
    def record_transition(
        self,
        result: ProductionTransitionResult,
        *,
        artifact_bytes: bytes | None = None,
        proof_bytes: bytes | None = None,
    ) -> CanonicalEvidenceArtifact:
        if not isinstance(
            result,
            (
                ExternalArtifactClaim,
                IBMTargetExecutionResult,
                TargetTrainingExecutionResult,
                ScopedAuthorityDecisionResult,
                SupportEvidenceResult,
            ),
        ):
            raise ValueError("production evidence result type is not recognized")
        external_artifact: Mapping[str, object] | None = None
        external_proof: Mapping[str, object] | None = None
        receipt_id: str | None = None
        if isinstance(result, SupportEvidenceResult):
            if artifact_bytes is not None or proof_bytes is not None:
                raise ValueError("support evidence does not accept an external proof")
            transition = {"record": "support"}
        else:
            if artifact_bytes is None or proof_bytes is None:
                raise ValueError(
                    "positive floor semantics require an authenticated external proof"
                )
            if self.__proof_verifier is None:
                raise ValueError(
                    "external proof trust roots are not configured at startup"
                )
            verified = self.__proof_verifier.verify(
                evidence_id=result.evidence_id,
                evidence_class=result.evidence_class,
                identity=result.identity,
                artifact_bytes=artifact_bytes,
                proof_bytes=proof_bytes,
                consumed_receipt_ids=self.__consumed_receipt_ids,
            )
            transition = self._normalized_verified_transition(verified.transition)
            claimed_transition = self._claimed_positive_transition(result)
            if claimed_transition is not None and transition != claimed_transition:
                raise ValueError(
                    "external transition proof does not bind the claimed positive fields"
                )
            external_artifact = dict(verified.artifact)
            external_proof = dict(verified.proof)
            receipt_id = verified.receipt_id
        evidence_id = result.evidence_id
        object_id = (
            "object:"
            + self.__config.deployment_id
            + ":"
            + hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()
        )
        artifact_uri = (
            "phase5://"
            + self.__config.deployment_id
            + "/evidence/"
            + hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()
        )
        payload = {
            "canonical_object_id": object_id,
            "evidence_class": result.evidence_class.value,
            "evidence_id": evidence_id,
            "external_artifact": external_artifact,
            "external_proof": external_proof,
            "frozen_identity": _identity_payload(result.identity),
            "proof_receipt_id": receipt_id,
            "schema_version": "bb.rl.phase5.production-transition.v3",
            "store_id": self.__authority._authority_id,
            "transition": transition,
        }
        artifact = self.__evidence_composition.record_payload(
            self.__authority,
            object_id=object_id,
            artifact_uri=artifact_uri,
            artifact_payload=payload,
        )
        existing = self.__bindings.get(evidence_id)
        if existing is not None and existing != object_id:
            raise ValueError("production evidence IDs are immutable")
        self.__bindings[evidence_id] = object_id
        if receipt_id is not None:
            self.__consumed_receipt_ids.add(receipt_id)
        return artifact

    @staticmethod
    def _claimed_positive_transition(
        result: ProductionTransitionResult,
    ) -> dict[str, object] | None:
        if isinstance(result, IBMTargetExecutionResult):
            return {
                "execution_plane": result.execution_plane,
                "exit_code": result.exit_code,
                "operation": result.operation,
                "provider": result.provider,
                "scheduler": result.scheduler,
                "target_run_id": result.target_run_id,
            }
        if isinstance(result, TargetTrainingExecutionResult):
            return {
                "checkpoint_digest": result.checkpoint_digest,
                "execution_plane": result.execution_plane,
                "exit_code": result.exit_code,
                "operation": result.operation,
                "provider": result.provider,
                "scheduler": result.scheduler,
                "training_run_id": result.training_run_id,
            }
        if isinstance(result, ScopedAuthorityDecisionResult):
            return {
                "actor_role": result.actor_role,
                "authority_record_id": result.authority_record_id,
                "decision": result.decision,
                "scope": list(result.scope),
            }
        return None

    @staticmethod
    def _normalized_verified_transition(
        transition: Mapping[str, object],
    ) -> dict[str, object]:
        semantic_kind = _derive_semantic_kind(transition)
        if semantic_kind is None:
            raise ValueError(
                "external proof does not establish a frozen-floor semantic kind"
            )
        return dict(transition)

    @_serialized_worker_call
    def open_graph(
        self,
        graph_alias: str,
        *,
        nodes: Sequence[EvidenceNode],
        active_pointers: Sequence[ActiveStatusPointer],
        cards: Sequence[EvidenceCard],
    ) -> EvidenceGraph:
        evidence_ids = {card.evidence_id for card in cards}
        try:
            bindings = {
                evidence_id: self.__bindings[evidence_id]
                for evidence_id in evidence_ids
            }
        except KeyError as error:
            raise ValueError(
                f"production graph references unrecorded evidence: {error.args[0]}"
            ) from error
        return self.__repository.open_graph(
            graph_alias,
            nodes=nodes,
            active_pointers=active_pointers,
            cards=cards,
            object_bindings=bindings,
        )

    @_serialized_worker_call
    def resolve_graph(self, graph_alias: str) -> EvidenceGraph:
        return self.__repository.resolve_graph(graph_alias)

    @_serialized_worker_call
    def score_engine(self, catalog: Sequence[object]):
        from phase5_authority_score import ScoreEngine

        return _SerializedScoreEngine(
            ScoreEngine(
                catalog,
                _authority_capability=self.__score_capability,
            ),
            self._operation_lock,
        )

    @_serialized_worker_call
    def reject_frozen_floor(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        rejection_code: str,
    ) -> EvidenceInvalidationResult:
        return self.__repository.reject_frozen_floor(
            graph,
            evidence_id,
            rejection_code=rejection_code,
        )

    @_serialized_worker_call
    def observe_artifact(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        observed_bytes: bytes | None,
    ) -> EvidenceInvalidationResult:
        return self.__repository.observe_artifact(
            graph,
            evidence_id,
            observed_bytes=observed_bytes,
        )

    @_serialized_worker_call
    def observe_rerun(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        exit_code: int,
    ) -> EvidenceInvalidationResult:
        return self.__repository.observe_rerun(
            graph,
            evidence_id,
            exit_code=exit_code,
        )

    @_serialized_worker_call
    def observe_identity(
        self,
        graph: EvidenceGraph,
        evidence_id: str,
        *,
        observed: FrozenEvidenceIdentity,
    ) -> EvidenceInvalidationResult:
        return self.__repository.observe_identity(
            graph,
            evidence_id,
            observed=observed,
        )

    @_serialized_worker_call
    def observe_active_status(
        self,
        graph: EvidenceGraph,
        *,
        observed_target_node_id: str,
    ) -> EvidenceInvalidationResult:
        return self.__repository.observe_active_status(
            graph,
            observed_target_node_id=observed_target_node_id,
        )

    @_serialized_worker_call
    def event_count(self) -> int:
        return len(self.__repository.event_log)

    @_serialized_worker_call
    def has_existing_artifact(self) -> bool:
        return bool(self.__authority._artifacts)

    @_serialized_worker_call
    def has_existing_graph(self) -> bool:
        return bool(self.__repository._graphs_by_root)




def _wire_registry() -> dict[str, type[object]]:
    from breadboard.rl.phase5.models import (
        AuthorityKind,
        AuthorityRecord,
        AuthorityRevocation,
        EvidenceClass,
        EvidenceNodeKind,
        EvidenceState,
        ScoreItemState,
        SupportLevel,
    )
    from breadboard.rl.phase5.score import ScoreDecision, ScoreEvaluation, ScoreItem

    classes: tuple[type[object], ...] = (
        ActiveStatusPointer,
        AuthorityKind,
        AuthorityRecord,
        AuthorityRevocation,
        CanonicalEvidenceArtifact,
        EvidenceCard,
        EvidenceClass,
        EvidenceInvalidationResult,
        EvidenceNode,
        EvidenceNodeKind,
        EvidenceState,
        ExternalArtifactClaim,
        FrozenEvidenceIdentity,
        IBMTargetExecutionResult,
        ScoreDecision,
        ScoreEvaluation,
        ScoreItem,
        ScoreItemState,
        ScopedAuthorityDecisionResult,
        SupportEvidenceResult,
        SupportLevel,
        TargetTrainingExecutionResult,
    )
    return {value.__name__: value for value in classes}


def authority_worker_entry(
    connection: object,
    *,
    server: Phase5ProductionServer,
    config: _DeploymentConfig,
) -> None:
    from multiprocessing.connection import Connection

    from breadboard.rl.phase5.authority_ipc import (
        IPC_SCHEMA,
        canonical_bytes,
        decode_value,
        digest,
        encode_value,
        parse_canonical_object,
    )

    if not isinstance(connection, Connection):
        raise RuntimeError("authority worker requires its inherited private channel")
    registry = _wire_registry()
    deployment_id = config.deployment_id
    public_key_digest = config.public_key_digest
    session_nonce = secrets.token_hex(32)
    signing_key = Ed25519PrivateKey.from_private_bytes(config.signing_key)
    public_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    def signed_response(value: dict[str, object]) -> bytes:
        unsigned = {
            **value,
            "server_public_key": public_key.hex(),
        }
        return canonical_bytes(
            {
                **unsigned,
                "signature": "ed25519:"
                + signing_key.sign(canonical_bytes(unsigned)).hex(),
            }
        )

    connection.send_bytes(
        signed_response(
            {
                "deployment_id": deployment_id,
                "error": None,
                "op": "handshake",
                "public_key_digest": public_key_digest,
                "request_sha256": None,
                "result": None,
                "result_sha256": digest(canonical_bytes(None)),
                "schema": IPC_SCHEMA,
                "sequence": 0,
                "session_nonce": session_nonce,
            }
        )
    )
    graphs: dict[str, EvidenceGraph] = {}
    engines: dict[str, object] = {}
    sequence = 1

    def handle(prefix: str) -> str:
        return (
            prefix
            + ":"
            + deployment_id
            + ":"
            + session_nonce
            + ":"
            + secrets.token_hex(24)
        )

    while True:
        try:
            if not connection.poll(30.0):
                continue
        except OSError:
            return
        try:
            raw = connection.recv_bytes(1_048_576)
        except (EOFError, OSError):
            return
        request_digest = digest(raw)
        op: object = None
        try:
            request = parse_canonical_object(raw)
            if set(request) != {
                "args",
                "deployment_id",
                "op",
                "public_key_digest",
                "schema",
                "sequence",
                "session_nonce",
            } or request != {
                **request,
                "schema": IPC_SCHEMA,
                "deployment_id": deployment_id,
                "public_key_digest": public_key_digest,
                "session_nonce": session_nonce,
                "sequence": sequence,
            }:
                raise ValueError("authority IPC request binding or sequence mismatch")
            op = request["op"]
            args = decode_value(request["args"], registry)
            if not isinstance(op, str) or not isinstance(args, dict):
                raise ValueError("authority IPC request operation is invalid")
            if op == "shutdown":
                result: object = None
            elif op == "record_transition":
                if set(args) != {"artifact_bytes", "proof_bytes", "result"}:
                    raise ValueError("record_transition request schema is invalid")
                result = server.record_transition(
                    args["result"],
                    artifact_bytes=args["artifact_bytes"],
                    proof_bytes=args["proof_bytes"],
                )
            elif op == "open_graph":
                if set(args) != {"active_pointers", "cards", "graph_alias", "nodes"}:
                    raise ValueError("open_graph request schema is invalid")
                if len(graphs) + len(engines) >= _MAX_SESSION_HANDLES:
                    raise ValueError("authority IPC session handle limit exceeded")
                graph = server.open_graph(
                    args["graph_alias"],
                    nodes=args["nodes"],
                    active_pointers=args["active_pointers"],
                    cards=args["cards"],
                )
                graph_handle = handle("graph")
                graphs[graph_handle] = graph
                result = graph_handle
            elif op == "resolve_graph":
                if set(args) != {"graph_alias"}:
                    raise ValueError("resolve_graph request schema is invalid")
                if len(graphs) + len(engines) >= _MAX_SESSION_HANDLES:
                    raise ValueError("authority IPC session handle limit exceeded")
                graph = server.resolve_graph(args["graph_alias"])
                graph_handle = handle("graph")
                graphs[graph_handle] = graph
                result = graph_handle
            elif op == "release_graph":
                if set(args) != {"graph_handle"}:
                    raise ValueError("release_graph request schema is invalid")
                if graphs.pop(args["graph_handle"], None) is None:
                    raise ValueError("unknown or cross-session graph handle")
                result = None
            elif op == "graph_snapshot":
                if set(args) != {"graph_handle"}:
                    raise ValueError("graph_snapshot request schema is invalid")
                graph = graphs.get(args["graph_handle"])
                if graph is None:
                    raise ValueError("unknown or cross-session graph handle")
                result = {
                    "active_pointer": graph.active_pointer,
                    "active_status_state": graph.active_status_state(),
                    "canonical_cards": graph._canonical_cards,
                    "canonical_root": graph.canonical_root,
                    "effective_states": graph.effective_states(),
                    "nodes": graph.nodes,
                    "rejection_codes": graph.rejection_codes(),
                }
            elif op == "score_engine":
                if set(args) != {"catalog"}:
                    raise ValueError("score_engine request schema is invalid")
                if len(graphs) + len(engines) >= _MAX_SESSION_HANDLES:
                    raise ValueError("authority IPC session handle limit exceeded")
                engine = server.score_engine(args["catalog"])
                engine_handle = handle("engine")
                engines[engine_handle] = engine
                result = {"catalog": engine.catalog, "engine_handle": engine_handle}
            elif op == "release_engine":
                if set(args) != {"engine_handle"}:
                    raise ValueError("release_engine request schema is invalid")
                if engines.pop(args["engine_handle"], None) is None:
                    raise ValueError("unknown or cross-session score handle")
                result = None
            elif op == "score_evaluate":
                required = {
                    "authority_revocations",
                    "decisions",
                    "engine_handle",
                    "evaluated_at",
                    "evidence_cards",
                    "graph_handle",
                    "supervisor_authorities",
                }
                if set(args) != required:
                    raise ValueError("score_evaluate request schema is invalid")
                engine = engines.get(args["engine_handle"])
                graph = graphs.get(args["graph_handle"])
                if engine is None or graph is None:
                    raise ValueError("unknown or cross-session score handle")
                result = engine.evaluate(
                    args["decisions"],
                    evidence_cards=args["evidence_cards"],
                    evidence_graph=graph,
                    supervisor_authorities=args["supervisor_authorities"],
                    authority_revocations=args["authority_revocations"],
                    evaluated_at=args["evaluated_at"],
                )
            elif op in {
                "reject_frozen_floor",
                "observe_artifact",
                "observe_rerun",
                "observe_identity",
                "observe_active_status",
            }:
                mutation_schemas = {
                    "reject_frozen_floor": {
                        "evidence_id",
                        "graph_handle",
                        "rejection_code",
                    },
                    "observe_artifact": {
                        "evidence_id",
                        "graph_handle",
                        "observed_bytes",
                    },
                    "observe_rerun": {
                        "evidence_id",
                        "exit_code",
                        "graph_handle",
                    },
                    "observe_identity": {
                        "evidence_id",
                        "graph_handle",
                        "observed",
                    },
                    "observe_active_status": {
                        "graph_handle",
                        "observed_target_node_id",
                    },
                }
                if set(args) != mutation_schemas[op]:
                    raise ValueError(f"{op} request schema is invalid")
                graph = graphs.get(args.pop("graph_handle"))
                if graph is None:
                    raise ValueError("unknown or cross-session graph handle")
                result = getattr(server, op)(graph, **args)
            elif op == "event_count":
                if args:
                    raise ValueError("event_count request schema is invalid")
                result = server.event_count()
            elif op == "has_existing_artifact":
                if args:
                    raise ValueError("has_existing_artifact request schema is invalid")
                result = server.has_existing_artifact()
            elif op == "has_existing_graph":
                if args:
                    raise ValueError("has_existing_graph request schema is invalid")
                result = server.has_existing_graph()
            else:
                raise ValueError("authority IPC operation is not allowlisted")
            response = {
                "deployment_id": deployment_id,
                "error": None,
                "public_key_digest": public_key_digest,
                "request_sha256": request_digest,
                "result": encode_value(result),
                "schema": IPC_SCHEMA,
                "sequence": sequence,
                "session_nonce": session_nonce,
                "op": op,
                "result_sha256": digest(canonical_bytes(encode_value(result))),
            }
        except Exception as error:
            response = {
                "deployment_id": deployment_id,
                "error": {"message": str(error), "type": type(error).__name__},
                "public_key_digest": public_key_digest,
                "request_sha256": request_digest,
                "result": None,
                "schema": IPC_SCHEMA,
                "sequence": sequence,
                "session_nonce": session_nonce,
                "op": op,
                "result_sha256": digest(canonical_bytes(None)),
            }
        response_bytes = signed_response(response)
        if len(response_bytes) > _MAX_MESSAGE_BYTES:
            response = {
                "deployment_id": deployment_id,
                "error": {
                    "message": "authority IPC response exceeds the operation limit",
                    "type": "ValueError",
                },
                "public_key_digest": public_key_digest,
                "request_sha256": request_digest,
                "result": None,
                "schema": IPC_SCHEMA,
                "sequence": sequence,
                "session_nonce": session_nonce,
                "op": op,
                "result_sha256": digest(canonical_bytes(None)),
            }
            response_bytes = signed_response(response)
        try:
            connection.send_bytes(response_bytes)
        except (BrokenPipeError, OSError):
            return
        sequence += 1
        if op == "shutdown":
            return


def _standalone_main() -> int:
    from multiprocessing.connection import Connection

    parser = argparse.ArgumentParser()
    parser.add_argument("--config-fd", required=True, type=int)
    parser.add_argument("--signing-key-fd", required=True, type=int)
    args = parser.parse_args()
    config, endpoint = _DeploymentConfig.from_supervisor_fds(
        config_fd=args.config_fd,
        signing_key_fd=args.signing_key_fd,
    )
    server = Phase5ProductionServer(config=config)
    socket_path = Path(endpoint)
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stop = threading.Event()
    session_slots = threading.BoundedSemaphore(_MAX_ACTIVE_SESSIONS)
    handlers_lock = threading.Lock()
    handlers: dict[threading.Thread, Connection] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()
        listener.close()

    def serve_one(connection: Connection) -> None:
        try:
            authority_worker_entry(
                connection,
                server=server,
                config=config,
            )
        finally:
            connection.close()
            with handlers_lock:
                handlers.pop(threading.current_thread(), None)
            session_slots.release()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        socket_path.unlink(missing_ok=True)
        listener.bind(endpoint)
        os.chmod(endpoint, 0o600)
        listener.listen(_MAX_ACTIVE_SESSIONS)
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                accepted, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if stop.is_set():
                    break
                raise
            if not session_slots.acquire(blocking=False):
                accepted.close()
                continue
            connection = Connection(accepted.detach())
            handler = threading.Thread(
                target=serve_one,
                args=(connection,),
                daemon=True,
                name="phase5-authority-session",
            )
            with handlers_lock:
                handlers[handler] = connection
            handler.start()
    finally:
        stop.set()
        listener.close()
        with handlers_lock:
            active = tuple(handlers.items())
        for _, connection in active:
            connection.close()
        deadline = time.monotonic() + _HANDLER_SHUTDOWN_SECONDS
        for handler, _ in active:
            handler.join(max(0.0, deadline - time.monotonic()))
        socket_path.unlink(missing_ok=True)
    return 0


__all__ = [
    "ExternalArtifactClaim",
    "IBMTargetExecutionResult",
    "Phase5ProductionServer",
    "ScopedAuthorityDecisionResult",
    "SupportEvidenceResult",
    "TargetTrainingExecutionResult",
]


if __name__ == "__main__":
    raise SystemExit(_standalone_main())
