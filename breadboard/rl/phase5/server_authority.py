from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import stat
import struct
import socket
import threading
from typing import Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from multiprocessing.connection import Connection

from breadboard.rl.phase5.authority_contract import (
    CanonicalEvidenceArtifactDTO,
    EvidenceInvalidationResultDTO,
    ExternalArtifactClaim,
    IBMTargetExecutionResult,
    ProductionTransitionResult,
    ScopedAuthorityDecisionResult,
    ScoreDecisionDTO,
    ScoreItemDTO,
    SupportEvidenceResult,
    TargetTrainingExecutionResult,
)
from breadboard.rl.phase5.authority_ipc import (
    IPC_SCHEMA,
    canonical_bytes,
    decode_value,
    digest,
    encode_value,
    parse_canonical_object,
)
from breadboard.rl.phase5.models import (
    ActiveStatusPointer,
    AuthorityKind,
    AuthorityRecord,
    AuthorityRevocation,
    EvidenceCard,
    EvidenceClass,
    EvidenceNode,
    EvidenceNodeKind,
    EvidenceState,
    ScoreItemState,
    SupportLevel,
)


_MAX_MESSAGE_BYTES = 1_048_576
_MAX_TRUST_DESCRIPTOR_BYTES = 16_384


def _client_registry() -> dict[str, type[object]]:
    return {
        value.__name__: value
        for value in (
            ActiveStatusPointer,
            AuthorityKind,
            AuthorityRecord,
            AuthorityRevocation,
            EvidenceCard,
            EvidenceClass,
            EvidenceNode,
            EvidenceNodeKind,
            EvidenceState,
            ExternalArtifactClaim,
            IBMTargetExecutionResult,
            ScoreItemState,
            ScopedAuthorityDecisionResult,
            SupportEvidenceResult,
            SupportLevel,
            TargetTrainingExecutionResult,
        )
    } | {
        "CanonicalEvidenceArtifact": CanonicalEvidenceArtifactDTO,
        "EvidenceInvalidationResult": EvidenceInvalidationResultDTO,
        "ScoreDecision": ScoreDecisionDTO,
        "ScoreItem": ScoreItemDTO,
    }


def _build_fixed_trust_loader(
    *,
    _close=os.close,
    _environ=os.environ,
    _fstat=os.fstat,
    _open=os.open,
    _read=os.read,
    _parse=parse_canonical_object,
    _is_dir=stat.S_ISDIR,
    _is_file=stat.S_ISREG,
):
    endpoint = "/run/breadboard/phase5-authority.sock"
    forbidden_app_env = frozenset(
        {
            "BREADBOARD_PHASE5_STATE_ROOT",
            "BREADBOARD_PHASE5_SIGNING_KEY_HEX",
            "BREADBOARD_PHASE5_AUTHORITY_SOCKET",
            "BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST",
            "BREADBOARD_PHASE5_DEPLOYMENT_ID",
            "BREADBOARD_PHASE5_IBM_PROOF_KEY_ID",
            "BREADBOARD_PHASE5_IBM_PROOF_PUBLIC_KEY_HEX",
            "BREADBOARD_PHASE5_TRAINING_PROOF_KEY_ID",
            "BREADBOARD_PHASE5_TRAINING_PROOF_PUBLIC_KEY_HEX",
            "BREADBOARD_PHASE5_AUTHORITY_PROOF_KEY_ID",
            "BREADBOARD_PHASE5_AUTHORITY_PROOF_PUBLIC_KEY_HEX",
        }
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    descriptor_components = ("etc", "breadboard", "phase5")
    descriptor_name = "authority-trust.json"

    class TrustBinding:
        __slots__ = (
            "deployment_id",
            "endpoint",
            "public_key_digest",
            "public_key_hex",
        )

        def __init__(
            self,
            *,
            deployment_id: str,
            public_key_digest: str,
            public_key_hex: str,
        ) -> None:
            self.deployment_id = deployment_id
            self.endpoint = endpoint
            self.public_key_digest = public_key_digest
            self.public_key_hex = public_key_hex

    def require_root_owned_mode(value: os.stat_result, *, directory: bool) -> None:
        expected_kind = _is_dir if directory else _is_file
        if (
            not expected_kind(value.st_mode)
            or value.st_uid != 0
            or value.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or (not directory and value.st_nlink != 1)
        ):
            raise RuntimeError("Phase 5 authority trust path is not root-secure")

    def read_descriptor() -> bytes:
        descriptor = _open("/", directory_flags)
        try:
            require_root_owned_mode(_fstat(descriptor), directory=True)
            for component in descriptor_components:
                child = _open(component, directory_flags, dir_fd=descriptor)
                _close(descriptor)
                descriptor = child
                require_root_owned_mode(_fstat(descriptor), directory=True)
            trust_fd = _open(descriptor_name, flags, dir_fd=descriptor)
            try:
                require_root_owned_mode(_fstat(trust_fd), directory=False)
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = _read(
                        trust_fd,
                        min(4_096, _MAX_TRUST_DESCRIPTOR_BYTES + 1 - total),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > _MAX_TRUST_DESCRIPTOR_BYTES:
                        raise RuntimeError(
                            "Phase 5 authority trust descriptor is oversized"
                        )
                return b"".join(chunks)
            finally:
                _close(trust_fd)
        except OSError as error:
            raise RuntimeError(
                "Phase 5 authority trust descriptor is unavailable"
            ) from error
        finally:
            _close(descriptor)


    def load() -> TrustBinding:
        if forbidden_app_env.intersection(_environ):
            raise RuntimeError(
                "Phase 5 application environment contains forbidden authority configuration"
            )
        try:
            value = _parse(read_descriptor())
        except ValueError as error:
            raise RuntimeError("Phase 5 authority trust descriptor is invalid") from error
        if set(value) != {
            "deployment_id",
            "protocol",
            "public_key",
            "public_key_digest",
            "schema",
            "socket",
            "version",
        }:
            raise RuntimeError("Phase 5 authority trust descriptor schema is invalid")
        deployment_id = value["deployment_id"]
        public_key_hex = value["public_key"]
        public_key_digest = value["public_key_digest"]
        if (
            value["schema"] != "bb.rl.phase5.authority-trust.v1"
            or value["protocol"] != IPC_SCHEMA
            or value["version"] != 1
            or value["socket"] != endpoint
            or not isinstance(deployment_id, str)
            or not deployment_id
            or not isinstance(public_key_hex, str)
            or len(public_key_hex) != 64
            or not isinstance(public_key_digest, str)
        ):
            raise RuntimeError("Phase 5 authority trust descriptor binding is invalid")
        try:
            public_key = bytes.fromhex(public_key_hex)
        except ValueError as error:
            raise RuntimeError("Phase 5 authority trust descriptor key is invalid") from error
        if public_key_digest != "sha256:" + hashlib.sha256(public_key).hexdigest():
            raise RuntimeError("Phase 5 authority trust descriptor key digest mismatch")
        return TrustBinding(
            deployment_id=deployment_id,
            public_key_digest=public_key_digest,
            public_key_hex=public_key_hex,
        )

    return load


_load_fixed_trust_binding = _build_fixed_trust_loader()
del _build_fixed_trust_loader




def _verified_envelope(
    raw: bytes,
    *,
    expected_deployment_id: str,
    expected_public_key_digest: str,
    expected_public_key_hex: str,
) -> dict[str, object]:
    envelope = parse_canonical_object(raw)
    required = {
        "deployment_id",
        "error",
        "op",
        "public_key_digest",
        "request_sha256",
        "result",
        "result_sha256",
        "schema",
        "sequence",
        "server_public_key",
        "session_nonce",
        "signature",
    }
    if set(envelope) != required:
        raise RuntimeError("Phase 5 authority response schema is invalid")
    public_key_text = envelope.get("server_public_key")
    signature_text = envelope.get("signature")
    if (
        not isinstance(public_key_text, str)
        or len(public_key_text) != 64
        or not isinstance(signature_text, str)
        or not signature_text.startswith("ed25519:")
        or len(signature_text) != 136
    ):
        raise RuntimeError("Phase 5 authority response signature is malformed")
    try:
        public_key = bytes.fromhex(public_key_text)
        signature = bytes.fromhex(signature_text.removeprefix("ed25519:"))
    except ValueError as error:
        raise RuntimeError("Phase 5 authority response signature is malformed") from error
    actual_digest = "sha256:" + hashlib.sha256(public_key).hexdigest()
    if public_key_text != expected_public_key_hex:
        raise RuntimeError("Phase 5 authority response public key mismatch")
    if (
        actual_digest != expected_public_key_digest
        or envelope.get("public_key_digest") != expected_public_key_digest
        or envelope.get("deployment_id") != expected_deployment_id
    ):
        raise RuntimeError("Phase 5 authority response deployment binding mismatch")
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            canonical_bytes(unsigned),
        )
    except (InvalidSignature, ValueError) as error:
        raise RuntimeError("Phase 5 authority response signature mismatch") from error
    if envelope.get("result_sha256") != digest(
        canonical_bytes(envelope.get("result"))
    ):
        raise RuntimeError("Phase 5 authority response result binding mismatch")
    return envelope


class RemoteEvidenceGraph:
    __slots__ = ("__handle", "__server")

    def __init__(self, server: Phase5ProductionServer, handle: str) -> None:
        self.__server = server
        self.__handle = handle

    @property
    def _remote_handle(self) -> str:
        return self.__handle

    def _snapshot(self) -> dict[str, object]:
        value = self.__server._rpc(
            "graph_snapshot", {"graph_handle": self.__handle}
        )
        if not isinstance(value, dict):
            raise RuntimeError("authority worker returned an invalid graph snapshot")
        return value

    @property
    def canonical_root(self) -> str:
        value = self._snapshot()["canonical_root"]
        if not isinstance(value, str):
            raise RuntimeError("authority worker returned an invalid canonical root")
        return value

    @property
    def nodes(self) -> tuple[EvidenceNode, ...]:
        value = self._snapshot()["nodes"]
        if not isinstance(value, tuple) or not all(
            isinstance(item, EvidenceNode) for item in value
        ):
            raise RuntimeError("authority worker returned invalid graph nodes")
        return value

    @property
    def active_pointer(self) -> ActiveStatusPointer:
        value = self._snapshot()["active_pointer"]
        if not isinstance(value, ActiveStatusPointer):
            raise RuntimeError("authority worker returned an invalid active pointer")
        return value

    @property
    def _canonical_cards(self) -> dict[str, EvidenceCard]:
        value = self._snapshot()["canonical_cards"]
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(card, EvidenceCard)
            for key, card in value.items()
        ):
            raise RuntimeError("authority worker returned invalid canonical cards")
        return value

    def effective_states(self) -> dict[str, EvidenceState]:
        value = self._snapshot()["effective_states"]
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(state, EvidenceState)
            for key, state in value.items()
        ):
            raise RuntimeError("authority worker returned invalid effective states")
        return value

    def rejection_codes(self) -> dict[str, tuple[str, ...]]:
        value = self._snapshot()["rejection_codes"]
        if not isinstance(value, dict):
            raise RuntimeError("authority worker returned invalid rejection codes")
        return value

    def active_status_state(self) -> EvidenceState:
        value = self._snapshot()["active_status_state"]
        if not isinstance(value, EvidenceState):
            raise RuntimeError("authority worker returned invalid active status state")
        return value

    def close(self) -> None:
        if self.__handle:
            self.__server._rpc("release_graph", {"graph_handle": self.__handle})
            self.__handle = ""
    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass




@dataclass(frozen=True)
class ScoreEvaluationDTO:
    catalog: tuple[object, ...]
    decisions: tuple[object, ...]
    deployment_id: str
    public_key_digest: str
    response_signature: str

    @property
    def catalog_points(self) -> int:
        return sum(item.points for item in self.catalog)

    @property
    def awarded_points(self) -> int:
        points = {item.item_id: item.points for item in self.catalog}
        return sum(
            points[decision.item_id]
            for decision in self.decisions
            if decision.state is ScoreItemState.AWARDED
        )

    @property
    def pending_points(self) -> int:
        points = {item.item_id: item.points for item in self.catalog}
        return sum(
            points[decision.item_id]
            for decision in self.decisions
            if decision.state is ScoreItemState.PENDING
        )

    @property
    def unawarded_points(self) -> int:
        return self.catalog_points - self.awarded_points

    @property
    def awarded_points_by_item(self) -> dict[str, int]:
        points = {item.item_id: item.points for item in self.catalog}
        return {
            decision.item_id: (
                points[decision.item_id]
                if decision.state is ScoreItemState.AWARDED
                else 0
            )
            for decision in self.decisions
        }

    @property
    def decisions_by_item(self) -> dict[str, object]:
        return {decision.item_id: decision for decision in self.decisions}


class RemoteScoreEngine:
    __slots__ = ("__catalog", "__handle", "__server")

    def __init__(
        self,
        server: Phase5ProductionServer,
        handle: str,
        catalog: tuple[object, ...],
    ) -> None:
        self.__server = server
        self.__handle = handle
        self.__catalog = catalog

    @property
    def catalog(self) -> tuple[object, ...]:
        return self.__catalog

    def evaluate(
        self,
        decisions: Sequence[object],
        *,
        evidence_cards: Sequence[EvidenceCard] = (),
        evidence_graph: RemoteEvidenceGraph | None = None,
        supervisor_authorities: Sequence[AuthorityRecord] = (),
        authority_revocations: Sequence[AuthorityRevocation] = (),
        evaluated_at: object | None = None,
    ) -> ScoreEvaluationDTO:
        if not isinstance(evidence_graph, RemoteEvidenceGraph):
            raise ValueError("award-bearing evaluation requires a remote evidence graph")
        value, signature = self.__server._rpc_with_signature(
            "score_evaluate",
            {
                "authority_revocations": tuple(authority_revocations),
                "decisions": tuple(decisions),
                "engine_handle": self.__handle,
                "evaluated_at": evaluated_at,
                "evidence_cards": tuple(evidence_cards),
                "graph_handle": evidence_graph._remote_handle,
                "supervisor_authorities": tuple(supervisor_authorities),
            },
        )
        if not isinstance(value, dict) or set(value) != {"catalog", "decisions"}:
            raise RuntimeError("authority worker returned an invalid score DTO")
        catalog = value["catalog"]
        returned_decisions = value["decisions"]
        if not isinstance(catalog, tuple) or not isinstance(returned_decisions, tuple):
            raise RuntimeError("authority worker returned an invalid score DTO")
        return ScoreEvaluationDTO(
            catalog=catalog,
            decisions=returned_decisions,
            deployment_id=self.__server.deployment_id,
            public_key_digest=self.__server.public_key_digest,
            response_signature=signature,
        )

    def close(self) -> None:
        if self.__handle:
            self.__server._rpc("release_engine", {"engine_handle": self.__handle})
            self.__handle = ""
    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass




def _require_root_peer(client_socket: socket.socket) -> None:
    if hasattr(socket, "SO_PEERCRED"):
        raw = client_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("3i", raw)
    elif hasattr(socket, "LOCAL_PEERCRED"):
        raw = client_socket.getsockopt(
            socket.SOL_LOCAL,
            socket.LOCAL_PEERCRED,
            12,
        )
        uid = int.from_bytes(raw[4:8], byteorder="little")
    else:
        return
    if uid != 0:
        raise RuntimeError("Phase 5 authority peer is not root-owned")

def _initialize_fixed_client(
    client: object,
    identity: object,
    token: object,
    expected_token: object,
    *,
    _connection_type=Connection,
    _peer_check=_require_root_peer,
    _registry=_client_registry,
    _socket_factory=socket.socket,
    _verified=_verified_envelope,
) -> None:
    if token is not expected_token:
        raise ValueError(
            "Phase 5 production server requires fixed system trust bootstrap"
        )
    deployment_id = identity.deployment_id
    endpoint = identity.endpoint
    public_key_digest = identity.public_key_digest
    public_key_hex = identity.public_key_hex
    client_socket = _socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
    client_socket.settimeout(15.0)
    try:
        client_socket.connect(endpoint)
        _peer_check(client_socket)
        client_socket.settimeout(None)
    except Exception:
        client_socket.close()
        raise
    connection = _connection_type(client_socket.detach())
    if not connection.poll(15.0):
        connection.close()
        raise TimeoutError("Phase 5 authority handshake timed out")
    try:
        raw = connection.recv_bytes(_MAX_MESSAGE_BYTES)
        handshake = _verified(
            raw,
            expected_deployment_id=deployment_id,
            expected_public_key_digest=public_key_digest,
            expected_public_key_hex=public_key_hex,
        )
    except Exception:
        connection.close()
        raise
    if (
        handshake.get("schema") != IPC_SCHEMA
        or handshake.get("op") != "handshake"
        or handshake.get("sequence") != 0
        or handshake.get("request_sha256") is not None
        or not isinstance(handshake.get("session_nonce"), str)
    ):
        connection.close()
        raise RuntimeError("Phase 5 authority handshake binding mismatch")
    client._Phase5ProductionServer__closed = False
    client._Phase5ProductionServer__connection = connection
    client._Phase5ProductionServer__deployment_id = deployment_id
    client._Phase5ProductionServer__endpoint = endpoint
    client._Phase5ProductionServer__lock = threading.Lock()
    client._Phase5ProductionServer__public_key_digest = public_key_digest
    client._Phase5ProductionServer__public_key_hex = public_key_hex
    client._Phase5ProductionServer__registry = _registry()
    client._Phase5ProductionServer__sequence = 1
    client._Phase5ProductionServer__session_nonce = handshake["session_nonce"]
    client._Phase5ProductionServer__timeout = 15.0


class Phase5ProductionServer:
    """Signed client for a supervisor-owned standalone Phase 5 authority."""

    __slots__ = (
        "__closed",
        "__connection",
        "__deployment_id",
        "__endpoint",
        "__lock",
        "__public_key_digest",
        "__public_key_hex",
        "__registry",
        "__sequence",
        "__session_nonce",
        "__timeout",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise ValueError(
            "Phase 5 production server requires fixed system trust bootstrap"
        )

    @property
    def deployment_id(self) -> str:
        return self.__deployment_id

    @property
    def public_key_digest(self) -> str:
        return self.__public_key_digest

    @property
    def immutable_identity(self) -> tuple[str, str, str, str]:
        return (
            self.__deployment_id,
            self.__endpoint,
            self.__public_key_digest,
            self.__public_key_hex,
        )

    def _poison(self) -> None:
        self.__closed = True
        try:
            self.__connection.close()
        except OSError:
            pass

    def _rpc_with_signature(
        self, op: str, args: dict[str, object]
    ) -> tuple[object, str]:
        with self.__lock:
            if self.__closed:
                raise RuntimeError("Phase 5 authority client is closed")
            sequence = self.__sequence
            request = {
                "args": encode_value(args),
                "deployment_id": self.__deployment_id,
                "op": op,
                "public_key_digest": self.__public_key_digest,
                "schema": IPC_SCHEMA,
                "sequence": sequence,
                "session_nonce": self.__session_nonce,
            }
            raw = canonical_bytes(request)
            if len(raw) > _MAX_MESSAGE_BYTES:
                raise ValueError("Phase 5 authority request exceeds the operation limit")
            request_digest = digest(raw)
            try:
                self.__connection.send_bytes(raw)
                if not self.__connection.poll(self.__timeout):
                    raise TimeoutError("Phase 5 authority response timed out")
                response_raw = self.__connection.recv_bytes(_MAX_MESSAGE_BYTES)
                response = _verified_envelope(
                    response_raw,
                    expected_deployment_id=self.__deployment_id,
                    expected_public_key_digest=self.__public_key_digest,
                    expected_public_key_hex=self.__public_key_hex,
                )
            except Exception:
                self._poison()
                raise
            if (
                response.get("schema") != IPC_SCHEMA
                or response.get("session_nonce") != self.__session_nonce
                or response.get("sequence") != sequence
                or response.get("request_sha256") != request_digest
                or response.get("op") != op
            ):
                self._poison()
                raise RuntimeError("Phase 5 authority response binding mismatch")
            self.__sequence += 1
            error = response.get("error")
            if error is not None:
                if not isinstance(error, dict) or set(error) != {"message", "type"}:
                    self._poison()
                    raise RuntimeError("Phase 5 authority error schema is invalid")
                error_type = error["type"]
                message = error["message"]
                if error_type in {"TypeError", "ValueError"}:
                    raise ValueError(message)
                raise RuntimeError(message)
            return (
                decode_value(response.get("result"), self.__registry),
                str(response["signature"]),
            )

    def _rpc(self, op: str, args: dict[str, object]) -> object:
        return self._rpc_with_signature(op, args)[0]

    def record_transition(
        self,
        result: ProductionTransitionResult,
        *,
        artifact_bytes: bytes | None = None,
        proof_bytes: bytes | None = None,
    ) -> CanonicalEvidenceArtifactDTO:
        value = self._rpc(
            "record_transition",
            {
                "artifact_bytes": artifact_bytes,
                "proof_bytes": proof_bytes,
                "result": result,
            },
        )
        if not isinstance(value, CanonicalEvidenceArtifactDTO):
            raise RuntimeError("authority worker returned an invalid artifact")
        return value

    def open_graph(
        self,
        graph_alias: str,
        *,
        nodes: Sequence[EvidenceNode],
        active_pointers: Sequence[ActiveStatusPointer],
        cards: Sequence[EvidenceCard],
    ) -> RemoteEvidenceGraph:
        handle = self._rpc(
            "open_graph",
            {
                "active_pointers": tuple(active_pointers),
                "cards": tuple(cards),
                "graph_alias": graph_alias,
                "nodes": tuple(nodes),
            },
        )
        if not isinstance(handle, str):
            raise RuntimeError("authority worker returned an invalid graph handle")
        return RemoteEvidenceGraph(self, handle)

    def resolve_graph(self, graph_alias: str) -> RemoteEvidenceGraph:
        handle = self._rpc("resolve_graph", {"graph_alias": graph_alias})
        if not isinstance(handle, str):
            raise RuntimeError("authority worker returned an invalid graph handle")
        return RemoteEvidenceGraph(self, handle)

    def score_engine(self, catalog: Sequence[object]) -> RemoteScoreEngine:
        original_catalog = tuple(catalog)
        value = self._rpc("score_engine", {"catalog": original_catalog})
        if not isinstance(value, dict) or set(value) != {"catalog", "engine_handle"}:
            raise RuntimeError("authority worker returned invalid score-engine metadata")
        handle = value["engine_handle"]
        if not isinstance(handle, str):
            raise RuntimeError("authority worker returned invalid score-engine metadata")
        return RemoteScoreEngine(self, handle, original_catalog)

    def _graph_call(
        self,
        op: str,
        graph: RemoteEvidenceGraph,
        args: dict[str, object],
    ) -> EvidenceInvalidationResultDTO:
        if not isinstance(graph, RemoteEvidenceGraph):
            raise ValueError("authority mutation requires a remote evidence graph")
        value = self._rpc(op, {"graph_handle": graph._remote_handle, **args})
        if not isinstance(value, EvidenceInvalidationResultDTO):
            raise RuntimeError("authority worker returned an invalid mutation result")
        return value

    def reject_frozen_floor(self, graph: RemoteEvidenceGraph, evidence_id: str, *, rejection_code: str) -> EvidenceInvalidationResultDTO:
        return self._graph_call("reject_frozen_floor", graph, {"evidence_id": evidence_id, "rejection_code": rejection_code})

    def observe_artifact(self, graph: RemoteEvidenceGraph, evidence_id: str, *, observed_bytes: bytes | None) -> EvidenceInvalidationResultDTO:
        return self._graph_call("observe_artifact", graph, {"evidence_id": evidence_id, "observed_bytes": observed_bytes})

    def observe_rerun(self, graph: RemoteEvidenceGraph, evidence_id: str, *, exit_code: int) -> EvidenceInvalidationResultDTO:
        return self._graph_call("observe_rerun", graph, {"evidence_id": evidence_id, "exit_code": exit_code})

    def observe_identity(self, graph: RemoteEvidenceGraph, evidence_id: str, *, observed: object) -> EvidenceInvalidationResultDTO:
        return self._graph_call("observe_identity", graph, {"evidence_id": evidence_id, "observed": observed})

    def observe_active_status(self, graph: RemoteEvidenceGraph, *, observed_target_node_id: str) -> EvidenceInvalidationResultDTO:
        return self._graph_call("observe_active_status", graph, {"observed_target_node_id": observed_target_node_id})

    def event_count(self) -> int:
        value = self._rpc("event_count", {})
        if type(value) is not int:
            raise RuntimeError("authority worker returned an invalid event count")
        return value

    def has_existing_artifact(self) -> bool:
        return self._rpc("has_existing_artifact", {}) is True

    def has_existing_graph(self) -> bool:
        return self._rpc("has_existing_graph", {}) is True

    def close(self) -> None:
        with self.__lock:
            if self.__closed:
                return
            try:
                self.__connection.close()
            finally:
                self.__closed = True

    def __enter__(self) -> Phase5ProductionServer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _make_start_phase5_server(
    *,
    _initialize=_initialize_fixed_client,
    _load_trust=_load_fixed_trust_binding,
    _new=object.__new__,
    _server_type=Phase5ProductionServer,
):
    token = object()
    active_server: Phase5ProductionServer | None = None
    active_identity: tuple[str, str, str, str] | None = None

    def start_phase5_server() -> Phase5ProductionServer:
        nonlocal active_identity, active_server
        identity = _load_trust()
        identity_key = (
            identity.deployment_id,
            identity.endpoint,
            identity.public_key_digest,
            identity.public_key_hex,
        )
        if active_server is not None:
            if identity_key != active_identity:
                raise RuntimeError("Phase 5 deployment authority is immutable after startup")
            return active_server
        server = _new(_server_type)
        _initialize(server, identity, token, token)
        active_identity = identity_key
        active_server = server
        return server

    return start_phase5_server


start_phase5_server = _make_start_phase5_server()
del _initialize_fixed_client
del _load_fixed_trust_binding
del _make_start_phase5_server
del _require_root_peer


__all__ = [
    "ExternalArtifactClaim",
    "IBMTargetExecutionResult",
    "Phase5ProductionServer",
    "RemoteEvidenceGraph",
    "RemoteScoreEngine",
    "ScopedAuthorityDecisionResult",
    "ScoreEvaluationDTO",
    "SupportEvidenceResult",
    "TargetTrainingExecutionResult",
    "start_phase5_server",
]
