from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness import composition
from breadboard.rl.harness.composition import (
    ArtifactFileRefV1,
    CallbackJournalVerificationReceiptV1,
    DNSPolicyDocumentV1,
    DockerNetworkLabelV1,
    OuterBridgePlanV1,
    PreboundServiceSocketPlanV1,
    EvidenceReceiptSignatureV1,
    EvidenceReceiptSigningAuthorityV1,
    EvidenceReceiptSigningHandoff,
    IPPolicyDocumentV1,
    OpenSslAuthorityV1,
    PolicyTlsTrustAuthorityV1,
    CASConfigRuntimeStore,
    HmacSha256ReceiptAuthenticator,
    PinnedFileAuthorityV1,
    PinnedRevocationStore,
    ServerV1,
    HarnessCompositionManifestV1,
    TlsCallbackPolicyV1,
    TlsCallbackRuntimeInputV1,
)
from breadboard.artifacts.cas import FilesystemCAS


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_hmac_authenticator_binds_exact_unsigned_receipt_bytes() -> None:
    authenticator = HmacSha256ReceiptAuthenticator(key_id="production-receipt-key", key=b"k" * 32)
    unsigned = b'{"schema_version":"bb.rl.admission-receipt.v1"}'
    signature = authenticator.sign(unsigned)

    assert authenticator.algorithm == "hmac-sha256-v1"
    assert authenticator.verify(unsigned, signature)
    assert not authenticator.verify(unsigned + b" ", signature)


def test_revocation_store_has_no_missing_scope_default() -> None:
    binding = c.RevocationBinding(scope_digest="sha256:" + "1" * 64, epoch=7, state_digest="sha256:" + "2" * 64)
    store = PinnedRevocationStore((binding,))

    assert store.load(binding.scope_digest) == binding
    with pytest.raises(ValueError, match="not pinned"):
        store.load("sha256:" + "3" * 64)


def test_cas_runtime_store_is_canonical_kind_checked_and_bind_once(tmp_path) -> None:
    store = CASConfigRuntimeStore(FilesystemCAS(tmp_path / "cas"), clock=type("Clock", (), {"current": lambda self: datetime(2026, 7, 10, tzinfo=UTC)})())
    payload = b'{"schema_version":"bb.rl.direct-selector.v1"}'
    ref = store.publish(kind=c.ArtifactKind.DIRECT_SELECTOR, canonical_bytes=payload)

    assert store.load(ref.sha256, kind=c.ArtifactKind.DIRECT_SELECTOR, max_bytes=1024) == payload
    with pytest.raises(ValueError, match="kind or digest"):
        store.load(ref.sha256, kind=c.ArtifactKind.CONFIG_SET, max_bytes=1024)
    with pytest.raises(ValueError, match="canonical"):
        store.publish(kind=c.ArtifactKind.DIRECT_SELECTOR, canonical_bytes=b'{ "schema_version": "bb.rl.direct-selector.v1" }')

    owner = "sha256:" + "4" * 64
    request = "sha256:" + "5" * 64
    selection = "sha256:" + "6" * 64
    first = store.bind_selection_once(owner_key=owner, request_digest=request, selection_record_digest=selection)
    second = store.bind_selection_once(owner_key=owner, request_digest=request, selection_record_digest=selection)
    assert first.binding == second.binding == store.get_selection_binding(owner)
    with pytest.raises(ValueError, match="conflict"):
        store.bind_selection_once(owner_key=owner, request_digest="sha256:" + "7" * 64, selection_record_digest=selection)


def _server(**changes):
    values = {
        "host": "127.0.0.1",
        "port": 8711,
        "allow_unauthenticated_loopback": False,
        "proxy_headers": False,
        "request_timeout_seconds": 30.0,
    }
    values.update(changes)
    return ServerV1(**values)


def test_server_authority_preserves_exact_loopback_default() -> None:
    server = _server()
    assert server.mode == "loopback"
    with pytest.raises(ValueError, match="loopback server authority"):
        _server(host="0.0.0.0")


def _outer_bridge(**changes):
    values = {
        "schema_version": "bb.rl.harness-outer-bridge-plan.v1",
        "network_name": "bb-f2-outer",
        "driver": "bridge",
        "subnet": "172.30.44.0/24",
        "gateway": "172.30.44.1",
        "internal": True,
        "labels": (
            DockerNetworkLabelV1(key="bb.rl.owner", value="f2"),
            DockerNetworkLabelV1(key="bb.rl.run", value="run-1"),
        ),
        "cleanup_owner": "f2_outer_orchestrator",
        "cleanup_ref": "run-1:bb-f2-outer",
    }
    values.update(changes)
    return OuterBridgePlanV1(**values)


def test_server_authority_cross_binds_exact_internal_bridge_gateway() -> None:
    network = _outer_bridge()
    server = _server(mode="internal_bridge", host=network.gateway)
    assert server.host == network.gateway
    with pytest.raises(ValueError, match="private and nonloopback"):
        _server(mode="internal_bridge", host="8.8.8.8")


def _socket_plan(
    gateway: str, port: int, *, role: str = "harness", inode: int = 42
) -> PreboundServiceSocketPlanV1:
    values = {
        "schema_version": "bb.rl.harness-prebound-service-socket-plan.v1",
        "role": role,
        "gateway": gateway,
        "observed_port": port,
        "family": "AF_INET",
        "socket_type": "SOCK_STREAM",
        "protocol": "IPPROTO_TCP",
        "socket_device": 8,
        "socket_inode": inode,
        "socket_mode": stat.S_IFSOCK | 0o600,
        "socket_owner_uid": 0,
        "getsockname_host": gateway,
        "getsockname_port": port,
        "ip_freebind": True,
    }
    plan_id = "sha256:" + hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PreboundServiceSocketPlanV1(**values, socket_plan_id=plan_id)


def _tls_callback_input(
    gateway: str = "10.44.0.1",
) -> TlsCallbackRuntimeInputV1:
    socket_plan = _socket_plan(
        gateway, 44443, role="callback_tls", inode=99
    )
    return TlsCallbackRuntimeInputV1(
        schema_version="bb.rl.harness-tls-callback-runtime-input.v1",
        route_id="callback-route",
        host=gateway,
        observed_port=socket_plan.observed_port,
        socket_role="callback_tls",
        socket_plan_id=socket_plan.socket_plan_id,
        ca_certificate_ref=ArtifactFileRefV1(
            path="/authority/callback-ca.pem",
            sha256="sha256:" + "1" * 64,
            size_bytes=128,
            media_type="application/x-pem-file",
        ),
        leaf_certificate_ref=ArtifactFileRefV1(
            path="/authority/callback-leaf.pem",
            sha256="sha256:" + "2" * 64,
            size_bytes=128,
            media_type="application/x-pem-file",
        ),
        ca_certificate_sha256="sha256:" + "1" * 64,
        leaf_certificate_sha256="sha256:" + "2" * 64,
        leaf_public_key_sha256="sha256:" + "3" * 64,
        private_key_secret_handle_id="callback-key",
        tls_policy=TlsCallbackPolicyV1(
            minimum_tls_version="TLSv1.3",
            maximum_tls_version="TLSv1.3",
            server_certificate_verification_required=True,
            hostname_verification_required=True,
            bearer_authentication_required=True,
            mutual_tls_required=False,
        ),
    )


def test_tls_callback_runtime_input_and_private_key_are_exact() -> None:
    runtime_input = _tls_callback_input()
    assert TlsCallbackRuntimeInputV1.model_validate_json(
        runtime_input.canonical_bytes(), strict=True
    ) == runtime_input
    with pytest.raises(ValueError, match="not exact"):
        TlsCallbackRuntimeInputV1.model_validate(
            {**runtime_input.model_dump(mode="json"), "host": "127.0.0.1"},
            strict=True,
        )
    key = (
        b"-----BEGIN PRIVATE KEY-----\n"
        b"cHJpdmF0ZS1rZXktbWF0ZXJpYWw=\n"
        b"-----END PRIVATE KEY-----\n"
    )
    assert composition._validate_secret(
        key, "callback_tls_private_key"
    ) == key
    with pytest.raises(ValueError, match="private.*key"):
        composition._validate_secret(
            b"not-a-private-key", "callback_tls_private_key"
        )
    assert composition._validate_secret(
        b"o" * 32, "callback_observation_signing_key"
    ) == b"o" * 32
    with pytest.raises(ValueError, match="signing key is too short"):
        composition._validate_secret(
            b"o" * 31, "callback_observation_signing_key"
        )


def test_ed25519_evidence_receipt_models_and_live_handoff(tmp_path: Path) -> None:
    public_ref = ArtifactFileRefV1(
        path="/authority/evidence-receipt-ed25519.pub",
        sha256="sha256:" + "4" * 64,
        size_bytes=113,
        media_type="application/x-pem-file",
    )
    authority = EvidenceReceiptSigningAuthorityV1(
        schema_version=(
            "bb.rl.harness-evidence-receipt-signing-authority.v1"
        ),
        attempt_id="attempt-1",
        composition_digest="sha256:" + "5" * 64,
        evidence_policy_digest="sha256:" + "6" * 64,
        algorithm="Ed25519",
        public_key_ref=public_ref,
        public_key_sha256=public_ref.sha256,
        public_key_spki_sha256="sha256:" + "7" * 64,
        private_key_secret_handle_id="evidence-signing-key",
        openssl_authority_digest="sha256:" + "8" * 64,
    )
    receipt = CallbackJournalVerificationReceiptV1(
        schema_version="bb.rl.callback-journal-verification-receipt.v1",
        attempt_id=authority.attempt_id,
        composition_digest=authority.composition_digest,
        route_id="callback-route",
        journal_ref=ArtifactFileRefV1(
            path="/evidence/callback-journal.jsonl",
            sha256="sha256:" + "9" * 64,
            size_bytes=128,
            media_type="application/jsonl",
        ),
        snapshot_ref=ArtifactFileRefV1(
            path="/evidence/callback-snapshot.json",
            sha256="sha256:" + "a" * 64,
            size_bytes=128,
            media_type="application/json",
        ),
        head_mac="b" * 64,
        event_count=3,
        chain_verified=True,
        snapshot_verified=True,
        evidence_policy_digest=authority.evidence_policy_digest,
        signer_public_key_spki_sha256=authority.public_key_spki_sha256,
        signer_authority_digest=authority.canonical_digest(),
    )
    signature = EvidenceReceiptSignatureV1(
        schema_version="bb.rl.evidence-receipt-signature.v1",
        algorithm="Ed25519",
        signer_authority_digest=authority.canonical_digest(),
        receipt_digest=receipt.canonical_digest(),
        signature_base64=base64.b64encode(b"s" * 64).decode("ascii"),
    )
    assert len(base64.b64decode(signature.signature_base64)) == 64
    key_path = tmp_path / "evidence-signing-key.pem"
    key_path.write_bytes(
        b"-----BEGIN PRIVATE KEY-----\n"
        b"ZWQyNTUxOS1wcml2YXRlLWtleQ==\n"
        b"-----END PRIVATE KEY-----\n"
    )
    key_path.chmod(0o400)
    fd = os.open(key_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        handoff = EvidenceReceiptSigningHandoff(authority, fd)
        handoff.validate_live()
        assert composition._validate_secret(
            key_path.read_bytes(), "evidence_receipt_signing_key"
        ) == key_path.read_bytes()
    finally:
        os.close(fd)


def _manifest_cross_bind(
    server: ServerV1, outer_bridge, *, openssl=True, host_runtime=True,
    tls_callback=None,
):
    manifest = HarnessCompositionManifestV1.model_construct(
        server=server,
        openssl_authority=(
            OpenSslAuthorityV1(
                schema_version="bb.rl.harness-openssl-authority.v1",
                path="/usr/bin/openssl",
                sha256="sha256:" + "d" * 64,
                device=8,
                inode=42,
                ctime_ns="123",
                size_bytes=1024,
                mode=0o755,
                owner_uid=0,
                version_stdout_sha256="sha256:" + "e" * 64,
                version="OpenSSL 3.0.0",
                discovery_report_ref=ArtifactFileRefV1(
                    path="/authority/openssl-discovery.json",
                    sha256="sha256:" + "f" * 64,
                    size_bytes=128,
                    media_type="application/vnd.breadboard.rl.openssl-discovery+json;version=1",
                ),
            )
            if openssl
            else None
        ),
        outer_bridge_plan=outer_bridge,
        prebound_service_socket_plans=(
            (
                *((_socket_plan(
                    outer_bridge.gateway,
                    tls_callback.observed_port,
                    role="callback_tls",
                    inode=99,
                ),) if tls_callback is not None else ()),
                _socket_plan(outer_bridge.gateway, server.port),
            )
            if outer_bridge is not None
            else ()
        ),
        host_runtime_authority=(
            SimpleNamespace(root="/runtime")
            if host_runtime
            else None
        ),
        tls_callback_runtime_input=tls_callback,
        installed=SimpleNamespace(runtimes=()),
        secret_handles=SimpleNamespace(records=(
            SimpleNamespace(handle_id="receipt", purpose="receipt_signer"),
            *((SimpleNamespace(
                handle_id="callback-key",
                purpose="callback_tls_private_key",
            ),) if tls_callback is not None else ()),
        )),
        control_plane=SimpleNamespace(
            receipt_authenticator=SimpleNamespace(secret_handle_id="receipt")
        ),
    )
    return manifest.cross_bind()


def test_manifest_cross_binds_server_to_outer_bridge_and_absence() -> None:
    network = _outer_bridge()
    server = _server(mode="internal_bridge", host=network.gateway)
    assert _manifest_cross_bind(server, network) is not None
    with pytest.raises(ValueError, match="equal outer bridge gateway"):
        _manifest_cross_bind(
            _server(mode="internal_bridge", host="172.30.44.2"),
            network,
        )
    with pytest.raises(ValueError, match="cannot carry"):
        _manifest_cross_bind(_server(), network)
    with pytest.raises(ValueError, match="requires pinned OpenSSL"):
        _manifest_cross_bind(server, network, openssl=False)
    with pytest.raises(ValueError, match="requires host runtime"):
        _manifest_cross_bind(server, network, host_runtime=False)
    assert _manifest_cross_bind(
        _server(), None, openssl=False, host_runtime=False
    ) is not None

def test_manifest_cross_binds_tls_callback_to_exact_private_key_handle() -> None:
    network = _outer_bridge()
    server = _server(mode="internal_bridge", host=network.gateway)
    runtime_input = _tls_callback_input(network.gateway)
    assert _manifest_cross_bind(
        server, network, tls_callback=runtime_input
    ) is not None
    with pytest.raises(ValueError, match="loopback composition"):
        _manifest_cross_bind(
            _server(), None, openssl=True, host_runtime=False,
            tls_callback=runtime_input,
        )
    with pytest.raises(ValueError, match="requires pinned OpenSSL"):
        _manifest_cross_bind(
            server, network, openssl=False, tls_callback=runtime_input
        )


@pytest.mark.parametrize(
    ("subnet", "gateway"),
    (
        ("8.8.8.0/24", "8.8.8.1"),
        ("169.254.20.0/24", "169.254.20.1"),
        ("127.0.0.0/8", "127.0.0.2"),
        ("0.0.0.0/0", "0.0.0.0"),
        ("fe80::/64", "fe80::1"),
    ),
)
def test_internal_bridge_rejects_nonprivate_or_unsafe_addresses(
    subnet: str, gateway: str
) -> None:
    with pytest.raises(ValueError, match="RFC1918 or ULA"):
        _outer_bridge(
            subnet=subnet,
            gateway=gateway,
            labels=(),
            cleanup_ref="run-1",
        )


def test_internal_bridge_rejects_unsorted_or_duplicate_labels() -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        _outer_bridge(
            subnet="10.44.0.0/24",
            gateway="10.44.0.1",
            labels=(
                DockerNetworkLabelV1(key="z", value="1"),
                DockerNetworkLabelV1(key="a", value="2"),
            ),
            cleanup_ref="run-1",
        )


def test_network_authority_documents_bind_canonical_bytes() -> None:
    dns_projection = {
        "schema_version": "bb.rl.policy-dns-authority.v1",
        "hostname": "127.0.0.1",
        "allowed_addresses": ["127.0.0.1"],
        "resolution_mode": "pinned",
        "require_all_answers_admitted": True,
        "verify_connected_peer": True,
    }
    dns_input = {**dns_projection, "allowed_addresses": ("127.0.0.1",)}
    dns = DNSPolicyDocumentV1(dns_policy_digest=_digest(dns_projection), **dns_input)
    ip_projection = {
        "schema_version": "bb.rl.policy-ip-authority.v1",
        "allowed_addresses": ["127.0.0.1"],
        "allow_loopback": True,
        "allow_private": False,
        "allow_link_local": False,
        "allow_multicast": False,
        "allow_unspecified": False,
    }
    ip_input = {**ip_projection, "allowed_addresses": ("127.0.0.1",)}
    ip = IPPolicyDocumentV1(ip_policy_digest=_digest(ip_projection), **ip_input)

    assert dns.allowed_addresses == ip.allowed_addresses == (str(ipaddress.ip_address("127.0.0.1")),)
    with pytest.raises(ValueError, match="digest mismatch"):
        DNSPolicyDocumentV1(dns_policy_digest="sha256:" + "0" * 64, **dns_input)


def test_tls_authority_binds_checked_in_dedicated_ca_and_leaf() -> None:
    fixture = Path(__file__).parents[2] / "fixtures/rl/harness/production_composition/tls"
    metadata = json.loads((fixture / "authority.json").read_bytes())
    metadata_bytes = (fixture / "authority.json").read_bytes()
    assert b"server.key" not in metadata_bytes
    assert b"PRIVATE KEY" not in metadata_bytes
    assert hashlib.sha256((fixture / "server.key.pem").read_bytes()).hexdigest().encode() not in metadata_bytes
    ca_path = (fixture / "ca.cert.pem").resolve()
    ca_bytes = ca_path.read_bytes()
    authority = PolicyTlsTrustAuthorityV1(
        schema_version="bb.rl.policy-tls-trust-authority.v1",
        route_id="policy-loopback",
        server_name=metadata["server_name"],
        ca_bundle_ref=ArtifactFileRefV1(
            path=str(ca_path),
            sha256=metadata["ca_cert_pem_sha256"],
            size_bytes=len(ca_bytes),
            media_type="application/x-pem-file",
        ),
        expected_leaf_certificate_sha256=metadata["server_leaf_der_sha256"],
        minimum_tls_version=metadata["minimum_tls_version"],
        cipher_suite=metadata["cipher_suite"],
        dedicated_single_leaf_ca=metadata["dedicated_single_leaf_ca"],
    )

    assert authority.ca_bundle_ref.sha256 == "sha256:" + hashlib.sha256(ca_bytes).hexdigest()
    assert authority.dedicated_single_leaf_ca is True
    with pytest.raises(ValueError, match="PEM"):
        authority.model_copy(
            update={"ca_bundle_ref": authority.ca_bundle_ref.model_copy(update={"media_type": "application/json"})}
        ).__class__.model_validate(
            {**authority.model_dump(mode="json"), "ca_bundle_ref": {**authority.ca_bundle_ref.model_dump(mode="json"), "media_type": "application/json"}}
        )
