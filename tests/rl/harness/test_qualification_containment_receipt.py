from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import HmacSha256ReceiptAuthenticator
from breadboard.rl.harness.lease_envelope import (
    ContainmentReceipt,
    ContainmentReceiptError,
    ReceiptAuthenticator,
)
from breadboard.rl.harness.qualification import (
    verify_qualification_containment,
)
from breadboard.rl.harness.headless import (
    HeadlessWorkspaceInput,
    HeadlessRunRequest,
    ObsoleteOuterIsolationError,
)


def _make_receipt(
    authenticator: HmacSha256ReceiptAuthenticator,
    *,
    lease_id: str = "lease-123",
    runtime_id: str = "trusted-process",
    tamper: bool = False,
) -> ContainmentReceipt:
    unsigned = ContainmentReceipt(
        schema_version="bb.containment-receipt.v1",
        lease_id=lease_id,
        runtime_id=runtime_id,
        mode="userns",
        pid_namespace_inode=12345,
        mount_namespace_inode=67890,
        user_namespace_inode=11223,
        mountinfo_sha256="sha256:" + "a" * 64,
        writable_roots=("/workspace",),
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        key_id=authenticator.key_id,
        algorithm=authenticator.algorithm,
        signature=b"\0" * 32,
        outcome=None,
    )
    sig = authenticator.sign(unsigned.canonical_bytes())
    if tamper:
        sig = b"tampered_signature_32_bytes_pad!"
    return ContainmentReceipt(
        schema_version=unsigned.schema_version,
        lease_id=unsigned.lease_id,
        runtime_id=unsigned.runtime_id,
        mode=unsigned.mode,
        pid_namespace_inode=unsigned.pid_namespace_inode,
        mount_namespace_inode=unsigned.mount_namespace_inode,
        user_namespace_inode=unsigned.user_namespace_inode,
        mountinfo_sha256=unsigned.mountinfo_sha256,
        writable_roots=unsigned.writable_roots,
        created_at=unsigned.created_at,
        key_id=unsigned.key_id,
        algorithm=unsigned.algorithm,
        signature=sig,
        outcome=unsigned.outcome,
    )


def test_verify_qualification_containment_accepts_valid_signed_receipt() -> None:
    authenticator = HmacSha256ReceiptAuthenticator(key_id="test-key", key=b"secret" * 8)
    receipt = _make_receipt(authenticator, lease_id="lease-123", runtime_id="trusted-process")
    verified = verify_qualification_containment(
        receipt,
        lease_id="lease-123",
        runtime_id="trusted-process",
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        authenticator=authenticator,
    )
    assert verified is not None
    assert verified.lease_id == "lease-123"
    assert verified.runtime_id == "trusted-process"


def test_verify_qualification_containment_rejects_missing_receipt() -> None:
    authenticator = HmacSha256ReceiptAuthenticator(key_id="test-key", key=b"secret" * 8)
    with pytest.raises(ContainmentReceiptError, match="containment receipt is missing"):
        verify_qualification_containment(
            None,
            lease_id="lease-123",
            runtime_id="trusted-process",
            runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
            authenticator=authenticator,
        )


def test_verify_qualification_containment_rejects_tampered_signature() -> None:
    authenticator = HmacSha256ReceiptAuthenticator(key_id="test-key", key=b"secret" * 8)
    tampered = _make_receipt(authenticator, lease_id="lease-123", runtime_id="trusted-process", tamper=True)
    with pytest.raises(ContainmentReceiptError, match="containment receipt signature mismatch"):
        verify_qualification_containment(
            tampered,
            lease_id="lease-123",
            runtime_id="trusted-process",
            runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
            authenticator=authenticator,
        )


def test_verify_qualification_containment_rejects_lease_id_mismatch() -> None:
    authenticator = HmacSha256ReceiptAuthenticator(key_id="test-key", key=b"secret" * 8)
    receipt = _make_receipt(authenticator, lease_id="lease-123", runtime_id="trusted-process")
    with pytest.raises(ContainmentReceiptError, match="containment receipt lease mismatch"):
        verify_qualification_containment(
            receipt,
            lease_id="lease-wrong",
            runtime_id="trusted-process",
            runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
            authenticator=authenticator,
        )


def test_verify_qualification_containment_skips_non_trusted_process() -> None:
    authenticator = HmacSha256ReceiptAuthenticator(key_id="test-key", key=b"secret" * 8)
    result = verify_qualification_containment(
        None,
        lease_id="lease-123",
        runtime_id="hardened-docker",
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        authenticator=authenticator,
    )
    assert result is None


def test_headless_workspace_input_rejects_obsolete_outer_isolation() -> None:
    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessWorkspaceInput(
            base_commit="a" * 40,
            task_image_digest="sha256:" + "0" * 64,
            outer_isolation="apptainer",
        )

    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessWorkspaceInput.model_validate(
            {
                "base_commit": "a" * 40,
                "task_image_digest": "sha256:" + "0" * 64,
                "outer_isolation": "apptainer",
            }
        )


def test_headless_run_request_rejects_obsolete_outer_isolation() -> None:
    raw_request: dict[str, Any] = {
        "schema_version": "bb.rl.headless-run-request.v1",
        "episode_id": "ep-1",
        "result_path": "/tmp/result.json",
        "event_log_path": "/tmp/event.log",
        "prompt": "test prompt",
        "context": {},
        "tool_allowlist": ["bash"],
        "resolve_request": {
            "schema_version": "bb.rl.resolve-episode-request.v1",
            "episode_id": "ep-1",
            "subject": {"authority_id": "test", "authority_scope_digest": "sha256:" + "0" * 64},
            "selector": {"digest": "sha256:" + "0" * 64, "ref": "cas://selector"},
            "selection_nonce": None,
            "task": {
                "task_id": "task-1",
                "task_binding_digest": "sha256:" + "0" * 64,
                "repository_snapshot_digest": None,
                "dataset_digests": (),
                "input_artifact_digests": (),
            },
            "policy_binding": {
                "route_id": "route-1",
                "registry_revision_digest": "sha256:" + "0" * 64,
                "attestation_digest": "sha256:" + "0" * 64,
            },
            "episode_overlays": (),
        },
        "workspace": {
            "base_commit": "a" * 40,
            "task_image_digest": "sha256:" + "0" * 64,
            "outer_isolation": "apptainer",
        },
        "expected_resources": {
            "cpu_cores": 1,
            "memory_bytes": 1024,
            "disk_bytes": 1024,
            "wall_time_ms": 1000,
        },
        "expected_limits": {
            "action_timeout_ms": 1000,
            "output_bytes": 1024,
            "observation_bytes": 1024,
        },
        "expected_sandbox": {
            "image_digest": "sha256:" + "0" * 64,
            "network_policy_digest": "sha256:" + "0" * 64,
            "security_policy_digest": "sha256:" + "0" * 64,
        },
        "provider": {
            "model": "model-1",
            "authority_model_id": "auth-model-1",
            "credential_handle": "cred-1",
            "context_window": 4096,
            "max_output_tokens": 1024,
            "timeout_seconds": 1.0,
        },
    }
    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessRunRequest.model_validate(raw_request)


def test_fixture_verify_containment(tmp_path: Path) -> None:
    from breadboard.rl.harness.qualification import materialize_production_composition_fixture
    fixture = materialize_production_composition_fixture(tmp_path)
    assert fixture.containment_authenticator is not None
    receipt = _make_receipt(fixture.containment_authenticator, lease_id="fixture-lease-1")
    verified = fixture.verify_containment(receipt, lease_id="fixture-lease-1")
    assert verified is not None
    assert verified.lease_id == "fixture-lease-1"

    with pytest.raises(ContainmentReceiptError, match="containment receipt is missing"):
        fixture.verify_containment(None, lease_id="fixture-lease-1")
