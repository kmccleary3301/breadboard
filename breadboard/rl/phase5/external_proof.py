from __future__ import annotations

from dataclasses import dataclass
import json
import re
from types import MappingProxyType
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from breadboard.rl.phase5.evidence import (
    FrozenEvidenceIdentity,
    _canonical_json_bytes,
    _identity_payload,
    _sha256,
)
from breadboard.rl.phase5.models import EvidenceClass


_EXTERNAL_ARTIFACT_SCHEMA = "bb.rl.phase5.external-transition-artifact.v1"
_EXTERNAL_PROOF_SCHEMA = "bb.rl.phase5.external-transition-proof.v1"
_RECEIPT_ID = re.compile(r"receipt:[a-z0-9][a-z0-9._:-]{15,255}\Z")
_SIGNATURE = re.compile(r"ed25519:[0-9a-f]{128}\Z")
_ROLE_BY_CLASS = MappingProxyType(
    {
        EvidenceClass.TARGET_SLURM_COMMAND: "ibm-target-execution",
        EvidenceClass.TARGET_TRAINING_RUN: "target-training-execution",
        EvidenceClass.AUTHORITY_DECISION: "scoped-authority-decision",
    }
)


@dataclass(frozen=True)
class _PinnedExternalVerifier:
    role: str
    key_id: str
    public_key: bytes


@dataclass(frozen=True)
class _VerifiedExternalProof:
    artifact: Mapping[str, object]
    proof: Mapping[str, object]
    receipt_id: str
    signer_key_id: str
    signer_role: str
    transition: Mapping[str, object]


class _PinnedExternalProofVerifier:
    """Startup-pinned verifier for exact external transition artifacts and receipts."""

    def __init__(self, signers: Mapping[str, _PinnedExternalVerifier]) -> None:
        if set(signers) != set(_ROLE_BY_CLASS.values()):
            raise ValueError("external transition signer roles are incomplete")
        self._signers = MappingProxyType(dict(signers))

    def verify(
        self,
        *,
        evidence_id: str,
        evidence_class: EvidenceClass,
        identity: FrozenEvidenceIdentity,
        artifact_bytes: bytes,
        proof_bytes: bytes,
        consumed_receipt_ids: set[str],
    ) -> _VerifiedExternalProof:
        if evidence_class not in _ROLE_BY_CLASS:
            raise ValueError("external proof is not valid for this evidence class")
        artifact = _load_exact_canonical_object(
            artifact_bytes, "external transition artifact"
        )
        required_artifact_fields = {
            "evidence_class",
            "evidence_id",
            "frozen_identity",
            "schema",
            "transition",
        }
        if (
            set(artifact) != required_artifact_fields
            or artifact.get("schema") != _EXTERNAL_ARTIFACT_SCHEMA
            or artifact.get("evidence_id") != evidence_id
            or artifact.get("evidence_class") != evidence_class.value
            or artifact.get("frozen_identity") != _identity_payload(identity)
            or not isinstance(artifact.get("transition"), dict)
        ):
            raise ValueError("external transition artifact binding mismatch")

        proof = _load_exact_canonical_object(proof_bytes, "external transition proof")
        required_proof_fields = {
            "artifact_sha256",
            "artifact_size",
            "evidence_class",
            "evidence_id",
            "frozen_identity",
            "receipt_id",
            "schema",
            "scope",
            "signature",
            "signer_key_id",
            "signer_role",
            "transition_sha256",
        }
        if set(proof) != required_proof_fields or proof.get("schema") != (
            _EXTERNAL_PROOF_SCHEMA
        ):
            raise ValueError("external transition proof schema is invalid")
        receipt_id = proof.get("receipt_id")
        if not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id):
            raise ValueError("external transition proof receipt identity is invalid")
        if receipt_id in consumed_receipt_ids:
            raise ValueError("external transition proof receipt was already consumed")

        expected_role = _ROLE_BY_CLASS[evidence_class]
        signer_role = proof.get("signer_role")
        signer = self._signers.get(signer_role) if isinstance(signer_role, str) else None
        if signer is None or signer_role != expected_role:
            raise ValueError("external transition proof signer role mismatch")
        signer_key_id = proof.get("signer_key_id")
        if signer_key_id != signer.key_id:
            raise ValueError("external transition proof signer key is unknown")

        transition = artifact["transition"]
        assert isinstance(transition, dict)
        expected_scope = _proof_scope(
            evidence_id=evidence_id,
            evidence_class=evidence_class,
            identity=identity,
            transition=transition,
        )
        if (
            proof.get("artifact_sha256") != _sha256(artifact_bytes)
            or proof.get("artifact_size") != len(artifact_bytes)
            or proof.get("evidence_id") != evidence_id
            or proof.get("evidence_class") != evidence_class.value
            or proof.get("frozen_identity") != _identity_payload(identity)
            or proof.get("transition_sha256")
            != _sha256(_canonical_json_bytes(transition))
            or proof.get("scope") != expected_scope
        ):
            raise ValueError("external transition proof binding mismatch")

        signature = proof.get("signature")
        if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
            raise ValueError("external transition proof signature is malformed")
        unsigned = {key: value for key, value in proof.items() if key != "signature"}
        try:
            Ed25519PublicKey.from_public_bytes(signer.public_key).verify(
                bytes.fromhex(signature.removeprefix("ed25519:")),
                _canonical_json_bytes(unsigned),
            )
        except (InvalidSignature, ValueError) as error:
            raise ValueError(
                "external transition proof signature mismatch"
            ) from error
        return _VerifiedExternalProof(
            artifact=MappingProxyType(dict(artifact)),
            proof=MappingProxyType(dict(proof)),
            receipt_id=receipt_id,
            signer_key_id=signer.key_id,
            signer_role=signer.role,
            transition=MappingProxyType(dict(transition)),
        )


def _load_exact_canonical_object(value: bytes, label: str) -> dict[str, object]:
    if not isinstance(value, bytes):
        raise ValueError(f"{label} must be exact bytes")
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not canonical JSON") from error
    if not isinstance(parsed, dict) or _canonical_json_bytes(parsed) != value:
        raise ValueError(f"{label} is not canonical JSON")
    return parsed


def _proof_scope(
    *,
    evidence_id: str,
    evidence_class: EvidenceClass,
    identity: FrozenEvidenceIdentity,
    transition: Mapping[str, object],
) -> list[str]:
    identity_digest = _sha256(_canonical_json_bytes(_identity_payload(identity)))
    transition_digest = _sha256(_canonical_json_bytes(transition))
    return [
        f"evidence:{evidence_id}",
        f"class:{evidence_class.value}",
        f"identity:{identity_digest}",
        f"transition:{transition_digest}",
    ]


__all__: list[str] = []
