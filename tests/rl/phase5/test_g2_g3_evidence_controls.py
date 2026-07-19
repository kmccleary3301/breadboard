from __future__ import annotations

from dataclasses import dataclass, replace
import atexit
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import socket
import threading
import sys
import tempfile
import time
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import breadboard.rl.phase5.evidence as evidence_module
import breadboard.rl.phase5.server_authority as server_authority_module
from tests.rl.phase5.phase5_test_client import connect_test_authority
from breadboard.rl.phase5.authority_ipc import (
    IPC_SCHEMA,
    canonical_bytes as _ipc_canonical_bytes,
    encode_value as _ipc_encode_value,
    parse_canonical_object as _parse_ipc_object,
)
from breadboard.rl.phase5.evidence import (
    EvidenceGraph,
    EvidenceMutation,
    FrozenEvidenceIdentity,
    FrozenEvidenceSubstitution,
    build_g2_g3_contract_report,
    canonical_g2_g3_contract_report_bytes,
)
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
    PROVENANCE_BINDING_ERROR,
    ScoreDecision,
    ScoreEngine,
    parse_score_catalog,
)
from breadboard.rl.phase5.server_authority import (
    ExternalArtifactClaim,
    IBMTargetExecutionResult,
    Phase5ProductionServer,
    ScopedAuthorityDecisionResult,
    SupportEvidenceResult,
    TargetTrainingExecutionResult,
)


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
PLAYBOOK = Path(
    "/Users/kylemccleary/projects/breadboard/docs_tmp/ZYPHRA/RL_PHASE_5/"
    "BB_Z_RL_PHASE_5_CONFIG_NATIVE_EXECUTION_AND_OPTIMIZATION_PLAYBOOK.md"
)
REPO_ROOT = Path(__file__).resolve().parents[3]
DIGESTS = tuple("sha256:" + character * 64 for character in "abcdef")
FROZEN_SUBSTITUTIONS = (
    "source_presence",
    "fixture_gold_jsonl",
    "monkeypatched_http",
    "loopback",
    "local_docker",
    "generic_slurm",
    "completed_state",
    "copied_manifest",
    "matching_hash",
)
FROZEN_FLOORS = ("IBM target", "target training", "authority")
FROZEN_MUTATIONS = (
    "changed_bytes",
    "missing_object",
    "cross_context_reuse",
    "old_head_review",
    "threshold_drift",
    "failed_rerun",
    "superseded_status",
)
CROSS_CONTEXT_FIELDS = ("config_digest", "task_digest", "run_id", "model_digest")
FLOOR_CASES = {
    "IBM target": ("D5", EvidenceClass.TARGET_SLURM_COMMAND),
    "target training": ("F8", EvidenceClass.TARGET_TRAINING_RUN),
    "authority": ("H3", EvidenceClass.AUTHORITY_DECISION),
}
IDENTITY = FrozenEvidenceIdentity(
    source_head=DIGESTS[0],
    config_digest=DIGESTS[1],
    task_digest=DIGESTS[2],
    run_id="run-a",
    model_digest=DIGESTS[3],
    threshold_digest=DIGESTS[4],
)
TEST_PRIVATE_KEYS = {
    EvidenceClass.TARGET_SLURM_COMMAND: Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"breadboard-g2-test-ibm-signer").digest()
    ),
    EvidenceClass.TARGET_TRAINING_RUN: Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"breadboard-g2-test-training-signer").digest()
    ),
    EvidenceClass.AUTHORITY_DECISION: Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(b"breadboard-g2-test-authority-signer").digest()
    ),
}


def _public_key_hex(evidence_class: EvidenceClass) -> str:
    return (
        TEST_PRIVATE_KEYS[evidence_class]
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
def _server_public_key(key: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(key)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def _server_public_key_digest(key: bytes) -> str:
    return "sha256:" + hashlib.sha256(_server_public_key(key)).hexdigest()






def _test_socket_path(root: Path) -> Path:
    suffix = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / f"breadboard-p5-{suffix}.sock"


def _install_test_deployment(
    root: Path, key: bytes, deployment_id: str
) -> dict[str, str]:
    endpoint = _test_socket_path(root)
    parent = root.parent.resolve()
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    public_key = _server_public_key(key)
    values = {
        "BREADBOARD_PHASE5_DEPLOYMENT_ID": deployment_id,
        "BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST": _server_public_key_digest(key),
        "BREADBOARD_PHASE5_SIGNING_KEY_HEX": key.hex(),
        "BREADBOARD_PHASE5_STATE_ROOT": str(root),
        "BREADBOARD_PHASE5_AUTHORITY_SOCKET": str(endpoint),
        "BREADBOARD_PHASE5_IBM_PROOF_KEY_ID": "test-ibm-proof-key",
        "BREADBOARD_PHASE5_IBM_PROOF_PUBLIC_KEY_HEX": _public_key_hex(
            EvidenceClass.TARGET_SLURM_COMMAND
        ),
        "BREADBOARD_PHASE5_TRAINING_PROOF_KEY_ID": "test-training-proof-key",
        "BREADBOARD_PHASE5_TRAINING_PROOF_PUBLIC_KEY_HEX": _public_key_hex(
            EvidenceClass.TARGET_TRAINING_RUN
        ),
        "BREADBOARD_PHASE5_AUTHORITY_PROOF_KEY_ID": "test-authority-proof-key",
        "BREADBOARD_PHASE5_AUTHORITY_PROOF_PUBLIC_KEY_HEX": _public_key_hex(
            EvidenceClass.AUTHORITY_DECISION
        ),
        "_PUBLIC_KEY_HEX": public_key.hex(),
        "_SERVICE_CONFIG": str(parent / "service-config.json"),
        "_SERVICE_KEY": str(parent / "service-signing-key"),
        "_TRUST_DESCRIPTOR": str(parent / "authority-trust.json"),
    }
    service_config = {
        "deployment_id": deployment_id,
        "endpoint": str(endpoint),
        "external_signers": [
            {
                "key_id": values["BREADBOARD_PHASE5_IBM_PROOF_KEY_ID"],
                "public_key": values[
                    "BREADBOARD_PHASE5_IBM_PROOF_PUBLIC_KEY_HEX"
                ],
                "role": "ibm-target-execution",
            },
            {
                "key_id": values["BREADBOARD_PHASE5_TRAINING_PROOF_KEY_ID"],
                "public_key": values[
                    "BREADBOARD_PHASE5_TRAINING_PROOF_PUBLIC_KEY_HEX"
                ],
                "role": "target-training-execution",
            },
            {
                "key_id": values["BREADBOARD_PHASE5_AUTHORITY_PROOF_KEY_ID"],
                "public_key": values[
                    "BREADBOARD_PHASE5_AUTHORITY_PROOF_PUBLIC_KEY_HEX"
                ],
                "role": "scoped-authority-decision",
            },
        ],
        "public_key": public_key.hex(),
        "public_key_digest": values["BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST"],
        "schema": "bb.rl.phase5.authority-service.v1",
        "state_root": str(root),
    }
    Path(values["_SERVICE_CONFIG"]).write_bytes(
        _ipc_canonical_bytes(service_config)
    )
    Path(values["_SERVICE_KEY"]).write_bytes(key)
    Path(values["_TRUST_DESCRIPTOR"]).write_bytes(
        _ipc_canonical_bytes(
            {
                "deployment_id": deployment_id,
                "protocol": IPC_SCHEMA,
                "public_key": public_key.hex(),
                "public_key_digest": values[
                    "BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST"
                ],
                "schema": "bb.rl.phase5.authority-trust.v1",
                "socket": str(endpoint),
                "version": 1,
            }
        )
    )
    return values


_TEST_PARENT = Path(tempfile.mkdtemp(prefix="phase5-server-authority-"))
if "G2_TEST_DEPLOYMENT_JSON" in os.environ:
    _TEST_DEPLOYMENT = json.loads(os.environ["G2_TEST_DEPLOYMENT_JSON"])
else:
    _TEST_DEPLOYMENT = _install_test_deployment(
        _TEST_PARENT / "authority",
        secrets.token_bytes(32),
        "focused-g2-g3",
    )
SIGNER_BY_CLASS = {
    EvidenceClass.TARGET_SLURM_COMMAND: (
        "ibm-target-execution",
        _TEST_DEPLOYMENT["BREADBOARD_PHASE5_IBM_PROOF_KEY_ID"],
        TEST_PRIVATE_KEYS[EvidenceClass.TARGET_SLURM_COMMAND],
    ),
    EvidenceClass.TARGET_TRAINING_RUN: (
        "target-training-execution",
        _TEST_DEPLOYMENT["BREADBOARD_PHASE5_TRAINING_PROOF_KEY_ID"],
        TEST_PRIVATE_KEYS[EvidenceClass.TARGET_TRAINING_RUN],
    ),
    EvidenceClass.AUTHORITY_DECISION: (
        "scoped-authority-decision",
        _TEST_DEPLOYMENT["BREADBOARD_PHASE5_AUTHORITY_PROOF_KEY_ID"],
        TEST_PRIVATE_KEYS[EvidenceClass.AUTHORITY_DECISION],
    ),
}


def _start_test_authority(values: dict[str, str]) -> subprocess.Popen[bytes]:
    endpoint = Path(values["BREADBOARD_PHASE5_AUTHORITY_SOCKET"])
    endpoint.unlink(missing_ok=True)
    config_file = Path(values["_SERVICE_CONFIG"]).open("rb")
    key_file = Path(values["_SERVICE_KEY"]).open("rb")
    process = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/rl_phase5/phase5_authority_service.py"),
            "--config-fd",
            str(config_file.fileno()),
            "--signing-key-fd",
            str(key_file.fileno()),
        ],
        cwd=REPO_ROOT,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO_ROOT)},
        pass_fds=(config_file.fileno(), key_file.fileno()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    config_file.close()
    key_file.close()
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                "test authority failed to start: "
                + (stdout + stderr).decode("utf-8", errors="replace")
            )
        if endpoint.exists():
            return process
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    raise TimeoutError("test authority socket did not become ready")


def _stop_test_authority(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)




_TEST_AUTHORITY_PROCESS = _start_test_authority(_TEST_DEPLOYMENT)
atexit.register(_stop_test_authority, _TEST_AUTHORITY_PROCESS)

_TEST_CLIENT: Phase5ProductionServer | None = None


def _start_test_client() -> Phase5ProductionServer:
    global _TEST_CLIENT
    if _TEST_CLIENT is None:
        _TEST_CLIENT = connect_test_authority(
            deployment_id=_TEST_DEPLOYMENT["BREADBOARD_PHASE5_DEPLOYMENT_ID"],
            endpoint=_TEST_DEPLOYMENT["BREADBOARD_PHASE5_AUTHORITY_SOCKET"],
            public_key_digest=_TEST_DEPLOYMENT[
                "BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST"
            ],
            public_key_hex=_TEST_DEPLOYMENT["_PUBLIC_KEY_HEX"],
        )
    return _TEST_CLIENT


start_phase5_server = _start_test_client
SUBSTITUTION_TRANSITIONS = {
    "source_presence": {
        "source_path": "breadboard/rl/phase5/server_authority.py",
        "source_present": True,
    },
    "fixture_gold_jsonl": {
        "fixture_format": "jsonl",
        "fixture_path": "fixtures/gold.jsonl",
        "fixture_role": "gold",
    },
    "monkeypatched_http": {
        "http_client": "monkeypatch",
        "http_status": 200,
        "transport": "in_process",
    },
    "loopback": {
        "endpoint": "http://127.0.0.1:8080",
        "network_scope": "loopback",
    },
    "local_docker": {
        "container_runtime": "docker",
        "execution_host": "localhost",
        "image_digest": DIGESTS[5],
    },
    "generic_slurm": {
        "provider": "generic",
        "scheduler": "slurm",
        "state": "COMPLETED",
    },
    "completed_state": {
        "scheduler": "slurm",
        "scheduler_state": "COMPLETED",
    },
    "copied_manifest": {
        "manifest_origin": "copied",
        "manifest_sha256": DIGESTS[5],
    },
    "matching_hash": {
        "actual_sha256": DIGESTS[5],
        "expected_sha256": DIGESTS[5],
    },
}


@dataclass(frozen=True)
class AwardPacket:
    namespace: str
    alias: str
    proof_floor: str
    server: Phase5ProductionServer
    engine: ScoreEngine
    decision: ScoreDecision
    cards: tuple[EvidenceCard, ...]
    graph: EvidenceGraph
    authority: AuthorityRecord
    artifacts: tuple[Any, ...]
    nodes: tuple[EvidenceNode, ...]
    pointer: ActiveStatusPointer


def _slug(value: str) -> str:
    return value.lower().replace(" ", "-").replace("_", "-")


def _g2_code(substitution: str, floor: str) -> str:
    return f"g2_{substitution}_cannot_satisfy_{floor.lower().replace(' ', '_')}"


def _production_result(
    evidence_id: str,
    evidence_class: EvidenceClass,
    proof_floor: str,
    *,
    identity: FrozenEvidenceIdentity = IDENTITY,
    substitution: str | None = None,
    suffix: str,
):
    if substitution is not None:
        return ExternalArtifactClaim(
            evidence_id=evidence_id,
            evidence_class=evidence_class,
            identity=identity,
        )
    if evidence_class in {
        EvidenceClass.ARTIFACT_INTEGRITY,
        EvidenceClass.REPLAY_REPRODUCTION,
        EvidenceClass.REVIEW_VERDICT,
    }:
        return SupportEvidenceResult(
            evidence_id=evidence_id,
            evidence_class=evidence_class,
            identity=identity,
        )
    if proof_floor == "IBM target":
        return IBMTargetExecutionResult(
            evidence_id=evidence_id,
            evidence_class=evidence_class,
            identity=identity,
            provider="IBM",
            execution_plane="target",
            scheduler="slurm",
            operation="episode",
            exit_code=0,
            target_run_id=f"ibm-target-{suffix}",
        )
    if proof_floor == "target training":
        return TargetTrainingExecutionResult(
            evidence_id=evidence_id,
            evidence_class=evidence_class,
            identity=identity,
            provider="IBM",
            execution_plane="target",
            scheduler="slurm",
            operation="training",
            exit_code=0,
            training_run_id=f"ibm-training-{suffix}",
            checkpoint_digest=DIGESTS[5],
        )
    if proof_floor == "authority":
        return ScopedAuthorityDecisionResult(
            evidence_id=evidence_id,
            evidence_class=evidence_class,
            identity=identity,
            actor_role="phase5-supervisor",
            authority_record_id=f"authority-{suffix}",
            decision="approved",
            scope=("score-item:*",),
        )
    return SupportEvidenceResult(
        evidence_id=evidence_id,
        evidence_class=evidence_class,
        identity=identity,
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _identity_payload(identity: FrozenEvidenceIdentity) -> dict[str, str]:
    return {
        field: getattr(identity, field)
        for field in FrozenEvidenceIdentity.__dataclass_fields__
    }


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _result_transition(result: object) -> dict[str, object]:
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
    raise TypeError("result has no external transition")


def _external_proof_material(
    result: ExternalArtifactClaim
    | IBMTargetExecutionResult
    | TargetTrainingExecutionResult
    | ScopedAuthorityDecisionResult,
    transition: dict[str, object],
    receipt_suffix: str,
    *,
    signer_role: str | None = None,
    signer_key_id: str | None = None,
    signer_private_key: Ed25519PrivateKey | None = None,
    artifact_update: dict[str, object] | None = None,
    proof_update: dict[str, object] | None = None,
) -> tuple[bytes, bytes]:
    artifact: dict[str, object] = {
        "evidence_class": result.evidence_class.value,
        "evidence_id": result.evidence_id,
        "frozen_identity": _identity_payload(result.identity),
        "schema": "bb.rl.phase5.external-transition-artifact.v1",
        "transition": transition,
    }
    if artifact_update:
        artifact.update(artifact_update)
    artifact_bytes = _canonical_bytes(artifact)
    expected_role, expected_key_id, expected_private_key = SIGNER_BY_CLASS[
        result.evidence_class
    ]
    role = signer_role or expected_role
    key_id = signer_key_id or expected_key_id
    private_key = signer_private_key or expected_private_key
    identity_digest = _sha256(_canonical_bytes(_identity_payload(result.identity)))
    transition_digest = _sha256(_canonical_bytes(transition))
    proof: dict[str, object] = {
        "artifact_sha256": _sha256(artifact_bytes),
        "artifact_size": len(artifact_bytes),
        "evidence_class": result.evidence_class.value,
        "evidence_id": result.evidence_id,
        "frozen_identity": _identity_payload(result.identity),
        "receipt_id": f"receipt:{receipt_suffix}",
        "schema": "bb.rl.phase5.external-transition-proof.v1",
        "scope": [
            f"evidence:{result.evidence_id}",
            f"class:{result.evidence_class.value}",
            f"identity:{identity_digest}",
            f"transition:{transition_digest}",
        ],
        "signer_key_id": key_id,
        "signer_role": role,
        "transition_sha256": transition_digest,
    }
    if proof_update:
        proof.update(proof_update)
    proof["signature"] = "ed25519:" + private_key.sign(
        _canonical_bytes(proof)
    ).hex()
    return artifact_bytes, _canonical_bytes(proof)


def _record_card(
    server: Phase5ProductionServer,
    namespace: str,
    role: str,
    evidence_class: EvidenceClass,
    proof_floor: str,
    *,
    verification_ids: tuple[str, ...] = (),
    reviewed_hashes: tuple[str, ...] = (),
    substitution: str | None = None,
) -> tuple[EvidenceCard, Any]:
    evidence_id = f"{namespace}:{role}"
    result = _production_result(
        evidence_id,
        evidence_class,
        proof_floor,
        substitution=substitution,
        suffix=f"{namespace}-{role}",
    )
    if isinstance(result, SupportEvidenceResult):
        artifact = server.record_transition(result)
    else:
        transition = (
            SUBSTITUTION_TRANSITIONS[substitution]
            if substitution is not None
            else _result_transition(result)
        )
        artifact_bytes, proof_bytes = _external_proof_material(
            result,
            transition,
            f"{namespace}-{role}",
        )
        artifact = server.record_transition(
            result,
            artifact_bytes=artifact_bytes,
            proof_bytes=proof_bytes,
        )
    card = EvidenceCard(
        evidence_id=evidence_id,
        evidence_class=evidence_class,
        support_level=SupportLevel.OBSERVED,
        state=EvidenceState.CURRENT,
        proof_floor=proof_floor,
        artifact_uri=artifact.artifact_uri,
        artifact_sha256="sha256:" + hashlib.sha256(artifact.artifact_bytes).hexdigest(),
        artifact_size=len(artifact.artifact_bytes),
        observed_at=NOW,
        independent_verification_ids=verification_ids,
        reviewed_artifact_hashes=reviewed_hashes,
    )
    return card, artifact


def _award_packet(
    proof_floor: str = "IBM target",
    *,
    substitution: str | None = None,
    namespace: str | None = None,
    alias: str | None = None,
    server: Phase5ProductionServer | None = None,
) -> AwardPacket:
    server = server or start_phase5_server()
    namespace = namespace or "packet-" + secrets.token_hex(8)
    alias = alias or "graph-" + namespace
    item_id, floor_class = FLOOR_CASES[proof_floor]
    integrity, integrity_artifact = _record_card(
        server,
        namespace,
        "integrity",
        EvidenceClass.ARTIFACT_INTEGRITY,
        "governance",
    )
    floor, floor_artifact = _record_card(
        server,
        namespace,
        "floor",
        floor_class,
        proof_floor,
        verification_ids=(integrity.evidence_id,),
        substitution=substitution,
    )
    dependent, dependent_artifact = _record_card(
        server,
        namespace,
        "dependent",
        EvidenceClass.REPLAY_REPRODUCTION,
        proof_floor,
        verification_ids=(integrity.evidence_id,),
    )
    review, review_artifact = _record_card(
        server,
        namespace,
        "review",
        EvidenceClass.REVIEW_VERDICT,
        "governance",
        reviewed_hashes=(integrity.artifact_sha256, floor.artifact_sha256),
    )
    scoped_authority, authority_artifact = _record_card(
        server,
        namespace,
        "scoped-authority",
        EvidenceClass.AUTHORITY_DECISION,
        "authority",
        verification_ids=(integrity.evidence_id,),
    )
    cards = (integrity, floor, dependent, review, scoped_authority)
    artifacts = (
        integrity_artifact,
        floor_artifact,
        dependent_artifact,
        review_artifact,
        authority_artifact,
    )
    nodes = (
        EvidenceNode(node_id="status", node_kind=EvidenceNodeKind.STATUS),
        EvidenceNode(
            node_id="integrity",
            evidence_id=integrity.evidence_id,
            node_kind=EvidenceNodeKind.EVIDENCE,
            dependencies=("status",),
        ),
        EvidenceNode(
            node_id="floor",
            evidence_id=floor.evidence_id,
            node_kind=EvidenceNodeKind.EVIDENCE,
            dependencies=("integrity",),
        ),
        EvidenceNode(
            node_id="dependent-evidence",
            evidence_id=dependent.evidence_id,
            node_kind=EvidenceNodeKind.EVIDENCE,
            dependencies=("floor",),
        ),
        EvidenceNode(
            node_id="claim",
            node_kind=EvidenceNodeKind.CLAIM,
            dependencies=("dependent-evidence",),
        ),
        EvidenceNode(
            node_id="review",
            evidence_id=review.evidence_id,
            node_kind=EvidenceNodeKind.REVIEW,
            dependencies=("claim",),
        ),
        EvidenceNode(
            node_id="point",
            node_kind=EvidenceNodeKind.POINT,
            dependencies=("review",),
        ),
        EvidenceNode(
            node_id="scoped-authority",
            evidence_id=scoped_authority.evidence_id,
            node_kind=EvidenceNodeKind.EVIDENCE,
            dependencies=("point",),
        ),
        EvidenceNode(
            node_id="promotion",
            node_kind=EvidenceNodeKind.PROMOTION,
            dependencies=("scoped-authority",),
        ),
    )
    pointer = ActiveStatusPointer(
        pointer_id=f"active:{namespace}",
        target_node_id="status",
        activated_at=NOW,
    )
    graph = server.open_graph(
        alias,
        nodes=nodes,
        active_pointers=(pointer,),
        cards=cards,
    )
    authority = AuthorityRecord(
        record_id=f"{namespace}:supervisor-authority",
        kind=AuthorityKind.AUTHORITY_DECISION,
        actor_identity="supervisor@example.test",
        actor_role="phase5-supervisor",
        scope=(f"score-item:{item_id}",),
        artifact_hashes=(
            integrity.artifact_sha256,
            floor.artifact_sha256,
            review.artifact_sha256,
        ),
        authority_artifact_uri=f"authority/{namespace}.json",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=1),
    )
    decision = ScoreDecision(
        item_id=item_id,
        state=ScoreItemState.AWARDED,
        evidence_ids=(integrity.evidence_id, floor.evidence_id),
        review_ids=(review.evidence_id,),
        supervisor_decision_id=authority.record_id,
    )
    return AwardPacket(
        namespace=namespace,
        alias=alias,
        proof_floor=proof_floor,
        server=server,
        engine=server.score_engine(parse_score_catalog(PLAYBOOK)),
        decision=decision,
        cards=cards,
        graph=graph,
        authority=authority,
        artifacts=artifacts,
        nodes=nodes,
        pointer=pointer,
    )


def _evaluate(
    packet: AwardPacket,
    *,
    graph: EvidenceGraph | None = None,
    cards: tuple[EvidenceCard, ...] | None = None,
    engine: ScoreEngine | None = None,
):
    return (engine or packet.engine).evaluate(
        (packet.decision,),
        evidence_cards=cards or packet.cards,
        evidence_graph=graph or packet.graph,
        supervisor_authorities=(packet.authority,),
        evaluated_at=NOW,
    )


def _mutate(packet: AwardPacket, mutation_name: str) -> Any:
    server = packet.server
    mutation = EvidenceMutation(mutation_name)
    if mutation is EvidenceMutation.CHANGED_BYTES:
        return server.observe_artifact(
            packet.graph,
            f"{packet.namespace}:floor",
            observed_bytes=b"changed authoritative artifact bytes",
        )
    if mutation is EvidenceMutation.MISSING_OBJECT:
        return server.observe_artifact(
            packet.graph,
            f"{packet.namespace}:floor",
            observed_bytes=None,
        )
    if mutation is EvidenceMutation.CROSS_CONTEXT_REUSE:
        return server.observe_identity(
            packet.graph,
            f"{packet.namespace}:floor",
            observed=replace(IDENTITY, run_id="run-other"),
        )
    if mutation is EvidenceMutation.OLD_HEAD_REVIEW:
        return server.observe_identity(
            packet.graph,
            f"{packet.namespace}:review",
            observed=replace(IDENTITY, source_head=DIGESTS[5]),
        )
    if mutation is EvidenceMutation.THRESHOLD_DRIFT:
        return server.observe_identity(
            packet.graph,
            f"{packet.namespace}:floor",
            observed=replace(IDENTITY, threshold_digest=DIGESTS[5]),
        )
    if mutation is EvidenceMutation.FAILED_RERUN:
        return server.observe_rerun(
            packet.graph,
            f"{packet.namespace}:floor",
            exit_code=1,
        )
    return server.observe_active_status(
        packet.graph,
        observed_target_node_id="status-generation-2",
    )


def _expected_mutation_states(mutation_name: str) -> dict[str, EvidenceState]:
    mutation = EvidenceMutation(mutation_name)
    root = (
        "review"
        if mutation is EvidenceMutation.OLD_HEAD_REVIEW
        else "status"
        if mutation is EvidenceMutation.SUPERSEDED_STATUS
        else "floor"
    )
    root_state = (
        EvidenceState.STALE
        if mutation
        in {EvidenceMutation.OLD_HEAD_REVIEW, EvidenceMutation.THRESHOLD_DRIFT}
        else EvidenceState.SUPERSEDED
        if mutation
        in {EvidenceMutation.FAILED_RERUN, EvidenceMutation.SUPERSEDED_STATUS}
        else EvidenceState.INVALID
    )
    expected = {
        "claim": EvidenceState.REVOKED,
        "point": EvidenceState.REVOKED,
        "promotion": EvidenceState.REVOKED,
        "review": EvidenceState.REVOKED,
        "scoped-authority": EvidenceState.REVOKED,
    }
    expected[root] = root_state
    if mutation is EvidenceMutation.OLD_HEAD_REVIEW:
        expected["floor"] = EvidenceState.CURRENT
        expected["dependent-evidence"] = EvidenceState.CURRENT
    elif mutation is EvidenceMutation.SUPERSEDED_STATUS:
        expected["floor"] = EvidenceState.REVOKED
        expected["dependent-evidence"] = EvidenceState.REVOKED
    else:
        expected["dependent-evidence"] = EvidenceState.REVOKED
    return expected


def test_frozen_case_inventory_and_exact_report_codes_are_closed() -> None:
    assert (
        tuple(item.value for item in FrozenEvidenceSubstitution) == FROZEN_SUBSTITUTIONS
    )
    assert tuple(item.value for item in EvidenceMutation) == FROZEN_MUTATIONS
    report = build_g2_g3_contract_report()
    assert report["g2"]["case_count"] == 27
    assert report["g3"]["case_count"] == 7
    assert {case["rejection_code"] for case in report["g2"]["cases"]} == {
        _g2_code(substitution, floor)
        for substitution in FROZEN_SUBSTITUTIONS
        for floor in FROZEN_FLOORS
    }
    assert {case["rejection_code"] for case in report["g3"]["cases"]} == {
        f"g3_{mutation}" for mutation in FROZEN_MUTATIONS
    }


@pytest.mark.parametrize("proof_floor", FROZEN_FLOORS)
def test_real_typed_production_transition_positive_for_each_floor(
    proof_floor: str,
) -> None:
    packet = _award_packet(proof_floor)
    result = _evaluate(packet)
    assert result.deployment_id == packet.server.deployment_id
    assert result.public_key_digest == packet.server.public_key_digest
    assert (
        result.decisions_by_item[packet.decision.item_id].state
        is ScoreItemState.AWARDED
    )
    assert packet.graph.effective_states()["promotion"] is EvidenceState.CURRENT


@pytest.mark.parametrize("substitution", FROZEN_SUBSTITUTIONS)
@pytest.mark.parametrize("proof_floor", FROZEN_FLOORS)
def test_all_27_frozen_substitutions_fail_with_exact_code(
    substitution: str,
    proof_floor: str,
) -> None:
    packet = _award_packet(proof_floor, substitution=substitution)
    expected = _g2_code(substitution, proof_floor)
    with pytest.raises(ValueError, match=rf"{expected}$"):
        _evaluate(packet)
    assert packet.graph.effective_states()["floor"] is EvidenceState.INVALID
    assert packet.graph.effective_states()["promotion"] is EvidenceState.REVOKED


def test_label_only_substitution_artifact_is_not_a_production_fact() -> None:
    server = start_phase5_server()
    evidence_id = f"label-only-{secrets.token_hex(8)}"
    claim = ExternalArtifactClaim(
        evidence_id=evidence_id,
        evidence_class=EvidenceClass.TARGET_SLURM_COMMAND,
        identity=IDENTITY,
    )
    artifact_bytes, proof_bytes = _external_proof_material(
        claim,
        {"observed_substitution": "loopback"},
        f"label-only-{secrets.token_hex(8)}",
    )
    with pytest.raises(
        ValueError,
        match="authoritative production transition is not recognized",
    ):
        server.record_transition(
            claim,
            artifact_bytes=artifact_bytes,
            proof_bytes=proof_bytes,
        )


@pytest.mark.parametrize("mutation_name", FROZEN_MUTATIONS)
def test_all_seven_mutations_revoke_signed_graph_state_and_remain_alias_safe(
    mutation_name: str,
) -> None:
    packet = _award_packet()
    assert _evaluate(packet).awarded_points > 0
    result = _mutate(packet, mutation_name)
    count = packet.server.event_count()
    assert _mutate(packet, mutation_name) == result
    assert packet.server.event_count() == count
    assert result.rejection_code == f"g3_{mutation_name}"
    states = packet.graph.effective_states()
    for node_id, expected in _expected_mutation_states(mutation_name).items():
        assert states[node_id] is expected
    alias = packet.server.open_graph(
        packet.alias + ":alias",
        nodes=packet.nodes,
        active_pointers=(packet.pointer,),
        cards=packet.cards,
    )
    assert alias.canonical_root == packet.graph.canonical_root
    assert alias.effective_states() == states
    with pytest.raises(ValueError, match=rf"g3_{mutation_name}$"):
        _evaluate(packet, graph=alias)


@pytest.mark.parametrize("field", CROSS_CONTEXT_FIELDS)
def test_cross_context_identity_uses_canonical_frozen_record(field: str) -> None:
    packet = _award_packet()
    result = packet.server.observe_identity(
        packet.graph,
        f"{packet.namespace}:floor",
        observed=replace(IDENTITY, **{field: "foreign-context"}),
    )
    assert result.rejection_code == "g3_cross_context_reuse"


def test_no_public_provision_path_key_repository_or_control_plane_surface() -> None:
    server = start_phase5_server()
    assert importlib.util.find_spec(
        "breadboard.rl.phase5.authority_worker"
    ) is None
    assert importlib.util.find_spec(
        "breadboard.rl.phase5.trust_store"
    ) is None
    assert "BREADBOARD_PHASE5_SIGNING_KEY_HEX" not in os.environ
    assert not hasattr(evidence_module, "_phase5_evidence_control_plane")
    assert not hasattr(server_authority_module, "FrozenSubstitutionResult")
    assert not hasattr(server_authority_module, "authority_worker_entry")
    assert not hasattr(server_authority_module, "_authority_worker")
    assert not hasattr(server_authority_module, "_DeploymentConfig")
    assert not hasattr(server_authority_module, "TEST_MODE")
    assert not hasattr(server, "repository")
    assert not hasattr(server, "record_result")
    assert not hasattr(server, "seal_opened_artifact")
    with pytest.raises(ValueError, match="fixed system trust bootstrap"):
        Phase5ProductionServer()
    with pytest.raises(TypeError):
        start_phase5_server(Path(tempfile.mkdtemp()))  # type: ignore[call-arg]
    packet = _award_packet()
    with pytest.raises(ValueError, match=rf"^{DERIVED_TOTAL_INJECTION_ERROR}$"):
        ScoreEngine(packet.engine.catalog, evidence_repository=object())
    with pytest.raises(ValueError, match=rf"^{DERIVED_TOTAL_INJECTION_ERROR}$"):
        ScoreEngine(packet.engine.catalog, _authority_capability=object())


def test_arbitrary_root_and_key_cannot_start_or_award() -> None:
    attacker_root = Path(tempfile.mkdtemp(prefix="phase5-forged-root-"))
    attacker_env = _client_only_env()
    attacker_env.update(
        {
            "BREADBOARD_PHASE5_STATE_ROOT": str(attacker_root),
            "BREADBOARD_PHASE5_SIGNING_KEY_HEX": secrets.token_hex(32),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from breadboard.rl.phase5.server_authority "
                "import start_phase5_server; start_phase5_server()"
            ),
        ],
        cwd=REPO_ROOT,
        env=attacker_env,
        check=False,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert b"forbidden authority configuration" in completed.stderr
    assert not any(attacker_root.iterdir())
    assert not hasattr(evidence_module, "ServerEvidenceAuthority")
    assert not hasattr(evidence_module, "ServerEvidenceRepository")
    assert not hasattr(evidence_module, "_ServerScoreCapability")


def test_startup_public_verification_material_cannot_mint_a_proof() -> None:
    public_key = Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(
            _TEST_DEPLOYMENT["BREADBOARD_PHASE5_IBM_PROOF_PUBLIC_KEY_HEX"]
        )
    )
    assert not hasattr(public_key, "sign")
    with pytest.raises(AttributeError):
        getattr(public_key, "sign")(_canonical_bytes({"forged": True}))
    assert not any(
        "PRIVATE" in name or ("SEED" in name and "SIGNING" not in name)
        for name in _TEST_DEPLOYMENT
    )


_TEST_CLIENT_BOOTSTRAP = (
    "from tests.rl.phase5.phase5_test_client import connect_test_authority;"
    "s=connect_test_authority("
    f"deployment_id={_TEST_DEPLOYMENT['BREADBOARD_PHASE5_DEPLOYMENT_ID']!r},"
    f"endpoint={_TEST_DEPLOYMENT['BREADBOARD_PHASE5_AUTHORITY_SOCKET']!r},"
    "public_key_digest="
    f"{_TEST_DEPLOYMENT['BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST']!r},"
    f"public_key_hex={_TEST_DEPLOYMENT['_PUBLIC_KEY_HEX']!r});"
)
_CLIENT_START_PROBE = (
    "from breadboard.rl.phase5.server_authority "
    "import start_phase5_server;start_phase5_server()"
)


def _client_only_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("BREADBOARD_PHASE5_SIGNING_KEY_HEX", None)
    env.pop("BREADBOARD_PHASE5_STATE_ROOT", None)
    return env


def _signed_handshake(
    private_key: Ed25519PrivateKey,
    *,
    deployment_id: str,
) -> dict[str, object]:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    unsigned: dict[str, object] = {
        "deployment_id": deployment_id,
        "error": None,
        "op": "handshake",
        "public_key_digest": "sha256:" + hashlib.sha256(public_key).hexdigest(),
        "request_sha256": None,
        "result": None,
        "result_sha256": "sha256:" + hashlib.sha256(b"null").hexdigest(),
        "schema": IPC_SCHEMA,
        "sequence": 0,
        "server_public_key": public_key.hex(),
        "session_nonce": secrets.token_hex(32),
    }
    return {
        **unsigned,
        "signature": "ed25519:"
        + private_key.sign(_ipc_canonical_bytes(unsigned)).hex(),
    }


def _probe_fake_endpoint(handshake: dict[str, object]) -> subprocess.CompletedProcess[bytes]:
    from multiprocessing.connection import Connection

    parent = Path(tempfile.mkdtemp(prefix="phase5-fake-endpoint-")).resolve()
    endpoint = parent / "authority.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(endpoint))
    listener.listen(1)

    def serve_once() -> None:
        accepted, _ = listener.accept()
        connection = Connection(accepted.detach())
        try:
            connection.send_bytes(_ipc_canonical_bytes(handshake))
        finally:
            connection.close()
            listener.close()

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    env = _client_only_env()
    probe = (
        "from tests.rl.phase5.phase5_test_client import connect_test_authority;"
        "connect_test_authority("
        f"deployment_id={_TEST_DEPLOYMENT['BREADBOARD_PHASE5_DEPLOYMENT_ID']!r},"
        f"endpoint={str(endpoint)!r},"
        "public_key_digest="
        f"{_TEST_DEPLOYMENT['BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST']!r},"
        f"public_key_hex={_TEST_DEPLOYMENT['_PUBLIC_KEY_HEX']!r})"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
    )
    thread.join(timeout=5)
    return completed


def test_application_refuses_worker_signing_and_state_configuration() -> None:
    env = _client_only_env()
    env["BREADBOARD_PHASE5_SIGNING_KEY_HEX"] = _TEST_DEPLOYMENT[
        "BREADBOARD_PHASE5_SIGNING_KEY_HEX"
    ]
    env["BREADBOARD_PHASE5_STATE_ROOT"] = _TEST_DEPLOYMENT[
        "BREADBOARD_PHASE5_STATE_ROOT"
    ]
    completed = subprocess.run(
        [sys.executable, "-c", _CLIENT_START_PROBE],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert b"forbidden authority configuration" in completed.stderr


@pytest.mark.parametrize(
    ("attack", "expected"),
    (
        ("wrong-key", b"public key mismatch"),
        ("cross-deployment", b"deployment binding mismatch"),
        ("tampered-result", b"signature mismatch"),
    ),
)
def test_fake_endpoint_cannot_impersonate_official_signed_authority(
    attack: str,
    expected: bytes,
) -> None:
    official_key = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(_TEST_DEPLOYMENT["BREADBOARD_PHASE5_SIGNING_KEY_HEX"])
    )
    signing_key = (
        Ed25519PrivateKey.generate() if attack == "wrong-key" else official_key
    )
    deployment_id = "foreign-deployment" if attack == "cross-deployment" else (
        _TEST_DEPLOYMENT["BREADBOARD_PHASE5_DEPLOYMENT_ID"]
    )
    handshake = _signed_handshake(signing_key, deployment_id=deployment_id)
    if attack == "wrong-key":
        handshake["public_key_digest"] = _TEST_DEPLOYMENT[
            "BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST"
        ]
    elif attack == "tampered-result":
        handshake["result"] = {"forged": True}
    completed = _probe_fake_endpoint(handshake)
    assert completed.returncode != 0
    assert expected in completed.stderr


def test_client_close_is_idempotent_and_poisoned_after_close() -> None:
    probe = (
        _TEST_CLIENT_BOOTSTRAP
        + "s.close();s.close();"
        "\ntry:s.event_count()"
        "\nexcept RuntimeError as e:"
        "\n assert 'closed' in str(e)"
        "\nelse:raise AssertionError('closed client accepted an operation')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=_client_only_env(),
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_ipc_codec_rejects_depth_and_collection_exhaustion() -> None:
    nested: object = None
    for _ in range(34):
        nested = (nested,)
    with pytest.raises(ValueError, match="nesting limit"):
        _ipc_encode_value(nested)
    with pytest.raises(ValueError, match="collection limit"):
        _ipc_encode_value(tuple(range(4_097)))


def test_session_exhaustion_rejects_excess_and_shutdown_cleans_handlers() -> None:
    from multiprocessing.connection import Connection

    parent = Path(tempfile.mkdtemp(prefix="phase5-session-exhaustion-"))
    values = _install_test_deployment(
        parent / "authority",
        secrets.token_bytes(32),
        "session-exhaustion",
    )
    service = _start_test_authority(values)
    connections: list[Connection] = []
    try:
        for _ in range(8):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(values["BREADBOARD_PHASE5_AUTHORITY_SOCKET"])
            connection = Connection(client.detach())
            assert connection.poll(5.0)
            _parse_ipc_object(connection.recv_bytes(1_048_576))
            connections.append(connection)

        excess_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        excess_client.connect(values["BREADBOARD_PHASE5_AUTHORITY_SOCKET"])
        excess = Connection(excess_client.detach())
        assert excess.poll(5.0)
        with pytest.raises(EOFError):
            excess.recv_bytes(1_048_576)
        excess.close()

        connections.pop().close()
        deadline = time.monotonic() + 5.0
        while True:
            replacement_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                replacement_client.connect(
                    values["BREADBOARD_PHASE5_AUTHORITY_SOCKET"]
                )
                replacement = Connection(replacement_client.detach())
                if replacement.poll(0.2):
                    _parse_ipc_object(replacement.recv_bytes(1_048_576))
                    connections.append(replacement)
                    break
                replacement.close()
            except (ConnectionRefusedError, EOFError):
                replacement_client.close()
            if time.monotonic() >= deadline:
                raise AssertionError("released session slot was not reusable")
            time.sleep(0.02)

        service.terminate()
        service.wait(timeout=6)
        assert service.returncode == 0
        assert not Path(values["BREADBOARD_PHASE5_AUTHORITY_SOCKET"]).exists()
    finally:
        if service.poll() is None:
            service.kill()
            service.wait(timeout=5)
        for connection in connections:
            connection.close()


def test_session_handle_cap_releases_capacity() -> None:
    packet = _award_packet("IBM target")
    engines = [packet.server.score_engine(packet.engine.catalog) for _ in range(4)]
    try:
        with pytest.raises(ValueError, match="session handle limit"):
            packet.server.score_engine(packet.engine.catalog)
        engines[0].close()
        replacement = packet.server.score_engine(packet.engine.catalog)
        replacement.close()
    finally:
        for engine in engines:
            engine.close()
        packet.engine.close()
        packet.graph.close()


def test_pre_start_assignment_cannot_take_over_fixed_authority_bootstrap() -> None:
    probe = (
        "import breadboard.rl.phase5.server_authority as m;"
        "m._load_fixed_trust_binding=lambda:(_ for _ in ()).throw("
        "AssertionError('fake loader called'));"
        "m._TRUST_DESCRIPTOR_PATH='/tmp/attacker-trust.json';"
        "m._AUTHORITY_ENDPOINT='/tmp/attacker.sock';"
        "m.start_phase5_server()"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=_client_only_env(),
        check=False,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert b"trust descriptor is unavailable" in completed.stderr
    assert b"fake loader called" not in completed.stderr
    assert not hasattr(server_authority_module, "_build_fixed_trust_loader")
    assert not hasattr(server_authority_module, "_make_start_phase5_server")


@pytest.mark.parametrize(
    "field,value",
    (
        ("provider", "local"),
        ("execution_plane", "loopback"),
        ("scheduler", "generic-slurm"),
        ("operation", "fixture"),
        ("exit_code", 1),
        ("target_run_id", ""),
    ),
)
def test_all_ibm_transition_fields_are_validated(field: str, value: object) -> None:
    values: dict[str, object] = {
        "evidence_id": "ignored-field-negative",
        "evidence_class": EvidenceClass.TARGET_SLURM_COMMAND,
        "identity": IDENTITY,
        "provider": "IBM",
        "execution_plane": "target",
        "scheduler": "slurm",
        "operation": "episode",
        "exit_code": 0,
        "target_run_id": "target-run",
    }
    values[field] = value
    with pytest.raises(ValueError):
        IBMTargetExecutionResult(**values)  # type: ignore[arg-type]


def test_arbitrary_bytes_kind_provenance_and_ignored_fields_are_not_accepted() -> None:
    server = start_phase5_server()
    with pytest.raises(ValueError, match="result type is not recognized"):
        server.record_transition(b"attacker supplied bytes")  # type: ignore[arg-type]
    base = {
        "evidence_id": "attacker",
        "evidence_class": EvidenceClass.TARGET_SLURM_COMMAND,
        "identity": IDENTITY,
        "provider": "IBM",
        "execution_plane": "target",
        "scheduler": "slurm",
        "operation": "episode",
        "exit_code": 0,
        "target_run_id": "target-run",
    }
    with pytest.raises(TypeError):
        IBMTargetExecutionResult(**base, kind="ibm_target_execution")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        IBMTargetExecutionResult(**base, semantic_kind="ibm_target_execution")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        IBMTargetExecutionResult(**base, display_name="IBM")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        IBMTargetExecutionResult(**base, artifact_bytes=b"forged")  # type: ignore[call-arg]


def test_field_valid_positive_claim_requires_exact_artifact_and_proof_bytes() -> None:
    server = start_phase5_server()
    claim = _production_result(
        "forged-positive-without-proof",
        EvidenceClass.TARGET_SLURM_COMMAND,
        "IBM target",
        suffix="forged-positive-without-proof",
    )
    with pytest.raises(
        ValueError,
        match="positive floor semantics require an authenticated external proof",
    ):
        server.record_transition(claim)
    artifact_bytes, _ = _external_proof_material(
        claim,
        _result_transition(claim),
        "forged-positive-missing-proof",
    )
    with pytest.raises(
        ValueError,
        match="positive floor semantics require an authenticated external proof",
    ):
        server.record_transition(claim, artifact_bytes=artifact_bytes)
    with pytest.raises(ValueError, match="proof is not canonical JSON"):
        server.record_transition(
            claim,
            artifact_bytes=artifact_bytes,
            proof_bytes=b"{malformed",
        )


@pytest.mark.parametrize(
    "attack,error",
    (
        ("altered-artifact", "proof binding mismatch"),
        ("altered-signature", "signature mismatch"),
        ("wrong-role", "signer role mismatch"),
        ("wrong-key", "signature mismatch"),
        ("unknown-key", "signer key is unknown"),
        ("wrong-scope", "proof binding mismatch"),
    ),
)
def test_tampered_or_wrong_signer_external_proofs_fail_closed(
    attack: str,
    error: str,
) -> None:
    server = start_phase5_server()
    evidence_id = f"proof-attack-{attack}-{secrets.token_hex(4)}"
    claim = _production_result(
        evidence_id,
        EvidenceClass.TARGET_SLURM_COMMAND,
        "IBM target",
        suffix=evidence_id,
    )
    transition = _result_transition(claim)
    material_kwargs: dict[str, object] = {}
    if attack == "wrong-role":
        role, key_id, private_key = SIGNER_BY_CLASS[
            EvidenceClass.TARGET_TRAINING_RUN
        ]
        material_kwargs = {
            "signer_role": role,
            "signer_key_id": key_id,
            "signer_private_key": private_key,
        }
    elif attack == "wrong-key":
        material_kwargs = {"signer_private_key": Ed25519PrivateKey.generate()}
    elif attack == "unknown-key":
        material_kwargs = {"signer_key_id": "unknown-external-key"}
    elif attack == "wrong-scope":
        material_kwargs = {"proof_update": {"scope": ["score-item:attacker"]}}
    artifact_bytes, proof_bytes = _external_proof_material(
        claim,
        transition,
        f"proof-attack-{attack}-{secrets.token_hex(8)}",
        **material_kwargs,
    )
    if attack == "altered-artifact":
        altered = json.loads(artifact_bytes)
        altered["transition"]["target_run_id"] += "-tampered"
        artifact_bytes = _canonical_bytes(altered)
    elif attack == "altered-signature":
        altered = json.loads(proof_bytes)
        signature = altered["signature"]
        altered["signature"] = signature[:-1] + (
            "0" if signature[-1] != "0" else "1"
        )
        proof_bytes = _canonical_bytes(altered)
    with pytest.raises(ValueError, match=error):
        server.record_transition(
            claim,
            artifact_bytes=artifact_bytes,
            proof_bytes=proof_bytes,
        )


@pytest.mark.parametrize("binding", ("evidence", "identity", "run", "scope"))
def test_copied_external_proof_cannot_cross_exact_bindings(binding: str) -> None:
    server = start_phase5_server()
    original_id = f"proof-copy-source-{binding}-{secrets.token_hex(4)}"
    original = _production_result(
        original_id,
        EvidenceClass.TARGET_SLURM_COMMAND,
        "IBM target",
        suffix=original_id,
    )
    if binding == "scope":
        original = _production_result(
            original_id,
            EvidenceClass.AUTHORITY_DECISION,
            "authority",
            suffix=original_id,
        )
    artifact_bytes, proof_bytes = _external_proof_material(
        original,
        _result_transition(original),
        f"proof-copy-{binding}-{secrets.token_hex(8)}",
    )
    copied = original
    if binding == "evidence":
        copied = replace(original, evidence_id=original_id + "-other")
    elif binding == "identity":
        copied = replace(
            original,
            identity=replace(IDENTITY, run_id="foreign-proof-context"),
        )
    elif binding == "run":
        copied = replace(original, target_run_id="another-target-run")
    else:
        copied = replace(original, scope=("score-item:foreign",))
    expected = (
        "external transition artifact binding mismatch"
        if binding in {"evidence", "identity"}
        else "does not bind the claimed positive fields"
    )
    with pytest.raises(ValueError, match=expected):
        server.record_transition(
            copied,
            artifact_bytes=artifact_bytes,
            proof_bytes=proof_bytes,
        )


def test_external_proof_receipt_is_single_use_in_live_server() -> None:
    server = start_phase5_server()
    evidence_id = f"proof-replay-{secrets.token_hex(8)}"
    claim = _production_result(
        evidence_id,
        EvidenceClass.TARGET_SLURM_COMMAND,
        "IBM target",
        suffix=evidence_id,
    )
    artifact_bytes, proof_bytes = _external_proof_material(
        claim,
        _result_transition(claim),
        f"proof-replay-{secrets.token_hex(8)}",
    )
    server.record_transition(
        claim,
        artifact_bytes=artifact_bytes,
        proof_bytes=proof_bytes,
    )
    with pytest.raises(ValueError, match="receipt was already consumed"):
        server.record_transition(
            claim,
            artifact_bytes=artifact_bytes,
            proof_bytes=proof_bytes,
        )


def test_omitted_edges_and_forged_cards_are_rejected() -> None:
    packet = _award_packet()
    omitted = tuple(
        node.model_copy(update={"dependencies": ("status",)})
        if node.node_id == "claim"
        else node
        for node in packet.nodes
    )
    with pytest.raises(ValueError, match="omits a required evidence-to-claim edge"):
        packet.server.open_graph(
            packet.alias + ":omitted",
            nodes=omitted,
            active_pointers=(packet.pointer,),
            cards=packet.cards,
        )
    forged_cards = tuple(
        card.model_copy(update={"artifact_uri": "attacker/forged.json"})
        if card.evidence_id == f"{packet.namespace}:floor"
        else card
        for card in packet.cards
    )
    with pytest.raises(
        ValueError,
        match=rf"^{PROVENANCE_BINDING_ERROR}: {packet.decision.item_id}$",
    ):
        _evaluate(packet, cards=forged_cards)


def _cold_seed(full: bool) -> dict[str, object]:
    server = start_phase5_server()
    positive_count = 0
    substitution_count = 0
    mutation_count = 0
    floors = FROZEN_FLOORS if full else ("IBM target",)
    for floor in floors:
        slug = _slug(floor)
        packet = _award_packet(
            floor,
            namespace=f"cold-positive-{slug}",
            alias=f"cold-positive-{slug}",
            server=server,
        )
        assert _evaluate(packet).awarded_points > 0
        positive_count += 1
    if full:
        for floor in FROZEN_FLOORS:
            for substitution in FROZEN_SUBSTITUTIONS:
                slug = f"{_slug(floor)}-{_slug(substitution)}"
                packet = _award_packet(
                    floor,
                    substitution=substitution,
                    namespace=f"cold-substitution-{slug}",
                    alias=f"cold-substitution-{slug}",
                    server=server,
                )
                expected = _g2_code(substitution, floor)
                try:
                    _evaluate(packet)
                except ValueError as error:
                    assert str(error).endswith(expected)
                else:
                    raise AssertionError("frozen substitution was awarded")
                substitution_count += 1
        for mutation in FROZEN_MUTATIONS:
            packet = _award_packet(
                namespace=f"cold-mutation-{mutation}",
                alias=f"cold-mutation-{mutation}",
                server=server,
            )
            assert _evaluate(packet).awarded_points > 0
            _mutate(packet, mutation)
            mutation_count += 1
    else:
        packet = _award_packet(
            namespace="tamper-one",
            alias="tamper-one",
            server=server,
        )
        assert _evaluate(packet).awarded_points > 0
        _mutate(packet, "changed_bytes")
        mutation_count = 1
    return {
        "mutations": mutation_count,
        "positives": positive_count,
        "substitutions": substitution_count,
    }


def _cold_packet(server: Phase5ProductionServer, alias: str, floor: str) -> AwardPacket:
    graph = server.resolve_graph(alias)
    cards = tuple(graph._canonical_cards.values())
    cards_by_id = {card.evidence_id: card for card in cards}
    namespace = alias
    item_id, _ = FLOOR_CASES[floor]
    integrity = cards_by_id[f"{namespace}:integrity"]
    floor_card = cards_by_id[f"{namespace}:floor"]
    review = cards_by_id[f"{namespace}:review"]
    authority = AuthorityRecord(
        record_id=f"{namespace}:supervisor-authority",
        kind=AuthorityKind.AUTHORITY_DECISION,
        actor_identity="supervisor@example.test",
        actor_role="phase5-supervisor",
        scope=(f"score-item:{item_id}",),
        artifact_hashes=(
            integrity.artifact_sha256,
            floor_card.artifact_sha256,
            review.artifact_sha256,
        ),
        authority_artifact_uri=f"authority/{namespace}.json",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=1),
    )
    decision = ScoreDecision(
        item_id=item_id,
        state=ScoreItemState.AWARDED,
        evidence_ids=(integrity.evidence_id, floor_card.evidence_id),
        review_ids=(review.evidence_id,),
        supervisor_decision_id=authority.record_id,
    )
    return AwardPacket(
        namespace=namespace,
        alias=alias,
        proof_floor=floor,
        server=server,
        engine=server.score_engine(parse_score_catalog(PLAYBOOK)),
        decision=decision,
        cards=cards,
        graph=graph,
        authority=authority,
        artifacts=(),
        nodes=graph.nodes,
        pointer=graph.active_pointer,
    )


def _cold_verify() -> dict[str, object]:
    server = start_phase5_server()
    positives: dict[str, int] = {}
    substitutions: dict[str, str] = {}
    mutations: dict[str, dict[str, str]] = {}
    for floor in FROZEN_FLOORS:
        alias = f"cold-positive-{_slug(floor)}"
        packet = _cold_packet(server, alias, floor)
        positives[floor] = _evaluate(packet).awarded_points
    for floor in FROZEN_FLOORS:
        for substitution in FROZEN_SUBSTITUTIONS:
            slug = f"{_slug(floor)}-{_slug(substitution)}"
            alias = f"cold-substitution-{slug}"
            packet = _cold_packet(server, alias, floor)
            expected = _g2_code(substitution, floor)
            try:
                _evaluate(packet)
            except ValueError as error:
                if not str(error).endswith(expected):
                    raise
            else:
                raise AssertionError("cold substitution was awarded")
            substitutions[alias] = expected
    for mutation in FROZEN_MUTATIONS:
        alias = f"cold-mutation-{mutation}"
        packet = _cold_packet(server, alias, "IBM target")
        try:
            _evaluate(packet)
        except ValueError as error:
            if not str(error).endswith(f"g3_{mutation}"):
                raise
        else:
            raise AssertionError("cold invalidated graph was awarded")
        states = packet.graph.effective_states()
        expected = _expected_mutation_states(mutation)
        for node_id, expected_state in expected.items():
            if states[node_id] is not expected_state:
                raise AssertionError(f"{mutation}:{node_id}:{states[node_id]}")
        mutations[mutation] = {
            node_id: states[node_id].value for node_id in sorted(expected)
        }
    return {
        "mutations": mutations,
        "positives": positives,
        "substitutions": substitutions,
    }


def _cold_replay() -> dict[str, str]:
    server = start_phase5_server()
    evidence_id = "cold-positive-ibm-target:floor"
    claim = _production_result(
        evidence_id,
        EvidenceClass.TARGET_SLURM_COMMAND,
        "IBM target",
        suffix="cold-positive-ibm-target-floor",
    )
    artifact_bytes, proof_bytes = _external_proof_material(
        claim,
        _result_transition(claim),
        "cold-positive-ibm-target-floor",
    )
    try:
        server.record_transition(
            claim,
            artifact_bytes=artifact_bytes,
            proof_bytes=proof_bytes,
        )
    except ValueError as error:
        return {"replay_error": str(error)}
    raise AssertionError("cold-restarted server accepted a consumed proof receipt")


def _ipc_attack_matrix() -> dict[str, bool]:
    from multiprocessing.connection import Connection

    server = start_phase5_server()
    results = {"separate_process": _TEST_AUTHORITY_PROCESS.pid != os.getpid()}
    try:
        server._rpc("not-allowlisted", {})
    except ValueError as error:
        results["unknown_op"] = "not allowlisted" in str(error)
    try:
        server._rpc(
            "graph_snapshot",
            {
                "graph_handle": (
                    f"graph:{server.deployment_id}:foreign-session:"
                    f"{secrets.token_hex(24)}"
                )
            },
        )
    except ValueError as error:
        results["forged_cross_session_handle"] = "cross-session" in str(error)

    client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client_socket.connect(
        _TEST_DEPLOYMENT["BREADBOARD_PHASE5_AUTHORITY_SOCKET"]
    )
    raw_connection = Connection(client_socket.detach())
    if not raw_connection.poll(5.0):
        raise AssertionError("authority worker did not send a handshake")
    handshake = _parse_ipc_object(raw_connection.recv_bytes(1_048_576))
    session_nonce = handshake["session_nonce"]

    def raw_request(
        *,
        raw: bytes | None = None,
        sequence: int,
        deployment_id: str | None = None,
        request_session_nonce: str | None = None,
    ) -> dict[str, object]:
        if raw is None:
            request = {
                "args": _ipc_encode_value({}),
                "deployment_id": deployment_id or server.deployment_id,
                "op": "event_count",
                "public_key_digest": server.public_key_digest,
                "schema": IPC_SCHEMA,
                "sequence": sequence,
                "session_nonce": request_session_nonce or session_nonce,
            }
            raw = _ipc_canonical_bytes(request)
        raw_connection.send_bytes(raw)
        if not raw_connection.poll(5.0):
            raise AssertionError("authority worker did not answer raw attack request")
        return _parse_ipc_object(raw_connection.recv_bytes(1_048_576))

    malformed = raw_request(raw=b"{}", sequence=1)
    results["malformed"] = malformed["error"] is not None
    replay = raw_request(sequence=1)
    results["replay"] = replay["error"] is not None
    out_of_order = raw_request(sequence=8)
    results["out_of_order"] = out_of_order["error"] is not None
    wrong_deployment = raw_request(
        sequence=4,
        deployment_id="foreign-deployment",
    )
    results["cross_deployment"] = wrong_deployment["error"] is not None
    wrong_session = raw_request(
        sequence=5,
        request_session_nonce=secrets.token_hex(32),
    )
    results["cross_session"] = wrong_session["error"] is not None
    raw_connection.close()

    tampered = dict(handshake)
    tampered["result"] = 1
    try:
        server_authority_module._verified_envelope(
            _ipc_canonical_bytes(tampered),
            expected_deployment_id=server.deployment_id,
            expected_public_key_digest=server.public_key_digest,
            expected_public_key_hex=_TEST_DEPLOYMENT["_PUBLIC_KEY_HEX"],
        )
    except RuntimeError as error:
        results["response_tamper"] = (
            "signature" in str(error) or "result binding" in str(error)
        )

    left, right = socket.socketpair()
    try:
        empty_connection = Connection(left.detach())
        results["timeout"] = not empty_connection.poll(0.01)
        empty_connection.close()
    finally:
        right.close()

    _TEST_AUTHORITY_PROCESS.terminate()
    _TEST_AUTHORITY_PROCESS.wait(timeout=5)
    try:
        server.event_count()
    except (EOFError, OSError, RuntimeError):
        results["worker_death"] = True
    return results


def _subprocess_env(root: Path, key: bytes, deployment_id: str) -> dict[str, str]:
    values = _install_test_deployment(root, key, deployment_id)
    env = _client_only_env()
    env["G2_TEST_DEPLOYMENT_JSON"] = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
    )
    return env
def _deployment_values(env: dict[str, str]) -> dict[str, str]:
    return json.loads(env["G2_TEST_DEPLOYMENT_JSON"])


def _replace_external_signers(
    env: dict[str, str],
    signers: list[dict[str, str]],
) -> None:
    values = _deployment_values(env)
    config_path = Path(values["_SERVICE_CONFIG"])
    config = _parse_ipc_object(config_path.read_bytes())
    config["external_signers"] = signers
    config_path.write_bytes(_ipc_canonical_bytes(config))




def _run_self(
    mode: str,
    env: dict[str, str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
    )
    if check and completed.returncode:
        raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))
    return completed


def test_worker_ipc_rejects_transport_and_handle_attacks() -> None:
    parent = Path(tempfile.mkdtemp(prefix="phase5-ipc-attacks-"))
    key = secrets.token_bytes(32)
    env = _subprocess_env(parent / "authority", key, "ipc-attacks")
    result = json.loads(_run_self("ipc-attacks", env).stdout)
    assert result == {
        "cross_deployment": True,
        "cross_session": True,
        "forged_cross_session_handle": True,
        "malformed": True,
        "out_of_order": True,
        "replay": True,
        "response_tamper": True,
        "separate_process": True,
        "timeout": True,
        "unknown_op": True,
        "worker_death": True,
    }


@pytest.mark.parametrize("configuration", ("missing-role", "shared-server-key"))
def test_startup_rejects_incomplete_or_shared_external_signer_roots(
    configuration: str,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix=f"phase5-signer-{configuration}-"))
    key = secrets.token_bytes(32)
    env = _subprocess_env(parent / "authority", key, f"signer-{configuration}")
    values = _deployment_values(env)
    config = _parse_ipc_object(Path(values["_SERVICE_CONFIG"]).read_bytes())
    signers = config["external_signers"]
    assert isinstance(signers, list)
    if configuration == "missing-role":
        _replace_external_signers(env, signers[1:])
        expected = b"external proof signer roles are incomplete"
    else:
        signers[0]["public_key"] = values["_PUBLIC_KEY_HEX"]
        _replace_external_signers(env, signers)
        expected = b"signer identities must be distinct"
    opened = _run_self("open-only", env, check=False)
    assert opened.returncode != 0
    assert expected in opened.stderr


def test_support_only_server_does_not_require_external_proof_trust_roots() -> None:
    parent = Path(tempfile.mkdtemp(prefix="phase5-support-only-"))
    env = _subprocess_env(
        parent / "authority",
        secrets.token_bytes(32),
        "support-only",
    )
    _replace_external_signers(env, [])
    result = json.loads(_run_self("support-only", env).stdout)
    assert result == {
        "artifact": True,
        "floor_error": "external proof trust roots are not configured at startup",
    }

def test_cold_process_reconstructs_full_graph_and_score_engine_parity() -> None:
    parent = Path(tempfile.mkdtemp(prefix="phase5-cold-parity-"))
    key = secrets.token_bytes(32)
    env = _subprocess_env(parent / "authority", key, "cold-parity")
    seeded = _run_self("seed-full", env)
    assert json.loads(seeded.stdout) == {
        "mutations": 7,
        "positives": 3,
        "substitutions": 27,
    }
    verified = _run_self("verify-full", env)
    result = json.loads(verified.stdout)
    assert set(result["positives"]) == set(FROZEN_FLOORS)
    assert all(points > 0 for points in result["positives"].values())
    assert len(result["substitutions"]) == 27
    assert set(result["mutations"]) == set(FROZEN_MUTATIONS)
    assert result["mutations"]["old_head_review"]["claim"] == "revoked"
    assert result["mutations"]["old_head_review"]["review"] == "stale"
    replayed = json.loads(_run_self("replay-one", env).stdout)
    assert replayed == {
        "replay_error": "external transition proof receipt was already consumed"
    }

    values = _deployment_values(env)
    report_worker = _start_test_authority(values)
    client_env = _client_only_env()
    report_bootstrap = (
        "import breadboard.rl.phase5.server_authority as m;"
        "from tests.rl.phase5.phase5_test_client import connect_test_authority;"
        "m.start_phase5_server=lambda:connect_test_authority("
        f"deployment_id={values['BREADBOARD_PHASE5_DEPLOYMENT_ID']!r},"
        f"endpoint={values['BREADBOARD_PHASE5_AUTHORITY_SOCKET']!r},"
        f"public_key_digest={values['BREADBOARD_PHASE5_PUBLIC_KEY_DIGEST']!r},"
        f"public_key_hex={values['_PUBLIC_KEY_HEX']!r});"
        "import runpy;"
        "runpy.run_path('scripts/rl_phase5/build_g2_g3_contract_report.py',"
        "run_name='__main__')"
    )
    report_one = subprocess.run(
        [sys.executable, "-c", report_bootstrap],
        cwd=REPO_ROOT,
        env=client_env,
        check=True,
        capture_output=True,
    )
    report_two = subprocess.run(
        [sys.executable, "-c", report_bootstrap],
        cwd=REPO_ROOT,
        env=client_env,
        check=True,
        capture_output=True,
    )
    expected = canonical_g2_g3_contract_report_bytes() + b"\n"
    assert report_one.stdout == expected
    assert report_two.stdout == expected
    assert report_one.stdout == report_two.stdout
    assert report_one.stderr == report_two.stderr == b""
    report_worker.terminate()
    report_worker.wait(timeout=5)


@pytest.mark.parametrize(
    "tamper",
    (
        "graph-truncation",
        "alias-tamper",
        "event-truncation",
        "artifact-tamper",
        "state-head-truncation",
        "foreign-event-replacement",
        "foreign-key-replacement",
    ),
)
def test_cold_constructor_rejects_truncation_tamper_and_foreign_replacement(
    tamper: str,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix=f"phase5-{tamper}-"))
    root = parent / "authority"
    key = secrets.token_bytes(32)
    env = _subprocess_env(root, key, f"tamper-{tamper}")
    _run_self("seed-one", env)
    state_files = sorted((root / "state").glob("*.json"))
    artifact_files = sorted((root / "artifacts").glob("*.json"))
    if tamper == "graph-truncation":
        state_files[-1].write_bytes(state_files[-1].read_bytes()[:-1])
    elif tamper == "alias-tamper":
        value = state_files[-1].read_bytes()
        state_files[-1].write_bytes(value.replace(b"tamper-one", b"tamper-Xne", 1))
    elif tamper == "event-truncation":
        events = root / "events.jsonl"
        events.write_bytes(events.read_bytes()[:-1])
    elif tamper == "artifact-tamper":
        artifact_files[0].write_bytes(artifact_files[0].read_bytes() + b"x")
    elif tamper == "state-head-truncation":
        head = root / "state.head.json"
        head.write_bytes(head.read_bytes()[:-1])
    elif tamper == "foreign-event-replacement":
        events = root / "events.jsonl"
        replacement = root / "events.foreign"
        replacement.write_bytes(events.read_bytes())
        os.replace(replacement, events)
    else:
        key_path = root / "authority.key"
        replacement = root / "key.foreign"
        replacement.write_bytes(key_path.read_bytes())
        os.replace(replacement, key_path)
    opened = _run_self("open-only", env, check=False)
    assert opened.returncode != 0
    assert opened.stderr


def test_report_cli_rejects_path_key_and_artifact_overrides() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase5/build_g2_g3_contract_report.py",
            "--trust-store",
            "/tmp/attacker",
            "--artifact-id",
            "attacker",
        ],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 2
    assert b"unrecognized arguments" in completed.stderr


BUILDER_CASES = (
    (
        "build_phase5_public_wheel.py",
        "phase5_public_wheel_manifest.json",
        "breadboard_phase5_public-0.1.0-py3-none-any.whl",
    ),
    (
        "build_phase5_private_capsule.py",
        "phase5_private_capsule_manifest.json",
        "breadboard_phase5_authority_capsule.zip",
    ),
)
BUILDER_ATTACKS = (
    "absolute-source",
    "parent-source",
    "source-leaf-symlink",
    "source-ancestor-symlink",
    "absolute-artifact",
    "parent-artifact",
    "artifact-parent-symlink",
    "artifact-final-symlink",
    "altered-allowlist",
    "extra-field",
)


def _builder_sandbox(
    builder_name: str,
    manifest_name: str,
) -> tuple[Path, Path, dict[str, object]]:
    sandbox = Path(tempfile.mkdtemp(prefix=f"phase5-builder-{builder_name}-"))
    scripts = sandbox / "scripts" / "rl_phase5"
    scripts.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "scripts" / "rl_phase5" / builder_name, scripts / builder_name)
    manifest_source = REPO_ROOT / "scripts" / "rl_phase5" / manifest_name
    manifest = json.loads(manifest_source.read_bytes())
    (scripts / manifest_name).write_bytes(manifest_source.read_bytes())
    for entry in manifest["files"]:
        source = str(entry["source"])
        destination = sandbox / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / source, destination)
    return sandbox, scripts / manifest_name, manifest


@pytest.mark.parametrize(
    ("builder_name", "manifest_name", "artifact_name"),
    BUILDER_CASES,
)
@pytest.mark.parametrize("attack", BUILDER_ATTACKS)
def test_artifact_builders_reject_path_and_symlink_substitution(
    builder_name: str,
    manifest_name: str,
    artifact_name: str,
    attack: str,
) -> None:
    sandbox, manifest_path, manifest = _builder_sandbox(builder_name, manifest_name)
    outside = Path(tempfile.mkdtemp(prefix="phase5-builder-outside-"))
    sentinel = outside / "sentinel"
    sentinel_bytes = b"builder-secret-must-not-leak-or-change"
    sentinel.write_bytes(sentinel_bytes)
    if attack == "absolute-source":
        manifest["files"][0]["source"] = "/etc/hosts"
    elif attack == "parent-source":
        manifest["files"][0]["source"] = "../sentinel"
    elif attack == "source-leaf-symlink":
        source = sandbox / str(manifest["files"][0]["source"])
        source.unlink()
        source.symlink_to(sentinel)
    elif attack == "source-ancestor-symlink":
        ancestor = sandbox / "breadboard" / "rl"
        shutil.rmtree(ancestor)
        ancestor.symlink_to(outside, target_is_directory=True)
    elif attack == "absolute-artifact":
        manifest["artifact"] = str(outside / artifact_name)
    elif attack == "parent-artifact":
        manifest["artifact"] = f"../{artifact_name}"
    elif attack == "artifact-parent-symlink":
        (sandbox / "dist").symlink_to(outside, target_is_directory=True)
    elif attack == "artifact-final-symlink":
        (sandbox / "dist").mkdir()
        (sandbox / "dist" / artifact_name).symlink_to(sentinel)
    elif attack == "altered-allowlist":
        manifest["files"] = list(reversed(manifest["files"]))
    elif attack == "extra-field":
        manifest["unexpected"] = True
    else:
        raise AssertionError(attack)
    if attack not in {
        "source-leaf-symlink",
        "source-ancestor-symlink",
        "artifact-parent-symlink",
        "artifact-final-symlink",
    }:
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    completed = subprocess.run(
        [sys.executable, str(sandbox / "scripts" / "rl_phase5" / builder_name)],
        cwd=sandbox,
        check=False,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert sentinel.read_bytes() == sentinel_bytes
    assert sentinel_bytes not in completed.stdout + completed.stderr
    assert not (outside / artifact_name).exists()
    artifact = sandbox / "dist" / artifact_name
    if attack != "artifact-final-symlink":
        assert not artifact.exists()
    if (sandbox / "dist").is_dir():
        assert not any(".tmp" in entry.name for entry in (sandbox / "dist").iterdir())


def _script_main() -> int:
    mode = sys.argv[1]
    if mode == "seed-full":
        value = _cold_seed(True)
    elif mode == "seed-one":
        value = _cold_seed(False)
    elif mode == "verify-full":
        value = _cold_verify()
    elif mode == "replay-one":
        value = _cold_replay()
    elif mode == "ipc-attacks":
        value = _ipc_attack_matrix()
    elif mode == "open-only":
        server = start_phase5_server()
        value = {
            "artifact": server.has_existing_artifact(),
            "graph": server.has_existing_graph(),
        }
    elif mode == "support-only":
        server = start_phase5_server()
        artifact = server.record_transition(
            SupportEvidenceResult(
                evidence_id="support-only",
                evidence_class=EvidenceClass.ARTIFACT_INTEGRITY,
                identity=IDENTITY,
            )
        )
        try:
            server.record_transition(
                ExternalArtifactClaim(
                    evidence_id="floor-without-roots",
                    evidence_class=EvidenceClass.TARGET_SLURM_COMMAND,
                    identity=IDENTITY,
                ),
                artifact_bytes=b"{}",
                proof_bytes=b"{}",
            )
        except ValueError as error:
            floor_error = str(error)
        else:
            raise AssertionError("signerless server accepted a floor proof")
        value = {
            "artifact": bool(artifact.artifact_bytes),
            "floor_error": floor_error,
        }
    else:
        raise ValueError(f"unknown test subprocess mode: {mode}")
    sys.stdout.buffer.write(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_script_main())
