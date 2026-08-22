from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import stat
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

from agentic_coder_prototype.compilation.contracts import (
    CompiledConfigManifest,
    ConfigBundleManifest,
    canonical_json_bytes,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    COMPOSITION_MEDIA_TYPE,
    ArtifactFileRefV1,
    AuthorityBundleV1,
    CompilerIdentityV1,
    CompositionRefV1,
    ControlPlaneV1,
    DirectoryAuthorityRefV1,
    HarnessCompositionManifestV1,
    InstalledV1,
    PolicyHttpAuthorityGraphV1,
    PolicyTlsTrustAuthorityV1,
    ProductionComposition,
    ReceiptAuthenticatorV1,
    SecretHandlesV1,
    SelectorCatalogV1,
    ServerV1,
    StoresV1,
    load_production_composition,
)
from breadboard.rl.harness.evidence import EvidenceRoleBindingV2, EvidenceRoleSourceV2
from breadboard.rl.harness.service import V2FaultInjectionAuthority
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_IMPLEMENTATION_DIGEST,
    TERMINAL_RUNTIME_ABI,
)
from breadboard.rl.phase5.f3_authority_authoring import F3AuthorityBundleManifest
from breadboard.rl.state.cas import FilesystemCAS

MAX_PINNED_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_PINNED_FILE_AUTHORITY_BYTES = 4 * 1024 * 1024 * 1024

_DIGEST_PREFIX = "sha256:"
_STORE_NAMES = (
    "cas",
    "locator",
    "materialization_cache",
    "workspace",
    "lease",
    "security_profile",
)
_EXPECTED_AUTHORITY_ARTIFACTS = frozenset(
    {
        "config-bundle.json",
        "config-closure.json",
        "compiled-manifest.json",
        "admission-policy.json",
        "registry-snapshot.json",
        "admission-receipt.json",
        "admitted-set.json",
        "direct-selector.json",
        "policy-capabilities.json",
        "policy-http.json",
        "revocations.json",
    }
)


class F3CompositionError(ValueError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def sha256_bytes(payload: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith(_DIGEST_PREFIX)
    ):
        raise ValueError("authority requires a lowercase sha256 digest")
    try:
        bytes.fromhex(value[7:])
    except ValueError as exc:
        raise ValueError("authority requires a lowercase sha256 digest") from exc
    if value != value.lower():
        raise ValueError("authority requires a lowercase sha256 digest")
    return value


def _absolute(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
    ):
        raise ValueError("path must be absolute and normalized")
    return value


class SourceArtifact(_ExactModel):
    path: str
    sha256: str
    media_type: str = Field(min_length=1, max_length=256)

    _path = field_validator("path")(_absolute)
    _sha256 = field_validator("sha256")(_digest)


class SecretFileAuthority(_ExactModel):
    path: str
    sha256: str
    mode: Literal[0o400]

    _path = field_validator("path")(_absolute)
    _sha256 = field_validator("sha256")(_digest)


class StorePaths(_ExactModel):
    cas: str
    locator: str
    materialization_cache: str
    workspace: str
    lease: str
    security_profile: str
    service_output_root: str
    lease_ttl_seconds: int = Field(gt=0, le=86400)

    _paths = field_validator(*_STORE_NAMES, "service_output_root")(_absolute)

    @model_validator(mode="after")
    def distinct_paths(self) -> "StorePaths":
        paths = tuple(
            getattr(self, name) for name in (*_STORE_NAMES, "service_output_root")
        )
        if len(paths) != len(set(paths)):
            raise ValueError("store and service output paths must be distinct")
        return self


class SecretAuthorities(_ExactModel):
    handles: SecretHandlesV1
    files: dict[str, str]

    @model_validator(mode="after")
    def exact_files(self) -> "SecretAuthorities":
        records = self.handles.records
        if tuple(item.purpose for item in records) != tuple(
            sorted(item.purpose for item in records)
        ):
            raise ValueError("secret handle records must be purpose sorted")
        purposes = [item.purpose for item in records]
        if sorted(purposes) != ["api_bearer", "policy_callback", "receipt_signer"]:
            raise ValueError(
                "F3 requires exactly API, policy callback, and receipt signer handles"
            )
        if set(self.files) != {item.handle_id for item in records}:
            raise ValueError("secret files must exactly cover the handle set")
        if len(set(self.files.values())) != len(self.files):
            raise ValueError("secret files may not be reused")
        for path in self.files.values():
            _absolute(path)
        return self


class ReceiptAuthority(_ExactModel):
    key_id: str = Field(min_length=1, max_length=128)
    secret_handle_id: str = Field(min_length=1, max_length=128)


class PolicyTlsAuthorityInput(_ExactModel):
    route_id: str = Field(min_length=1, max_length=128)
    ca_certificate: SourceArtifact
    leaf_certificate: SourceArtifact
    leaf_private_key: SecretFileAuthority
    expected_leaf_der_sha256: str
    minimum_tls_version: Literal["TLSv1.3"]
    cipher_suite: Literal["TLS_AES_256_GCM_SHA384"]
    dedicated_single_leaf_ca: Literal[True]

    _leaf = field_validator("expected_leaf_der_sha256")(_digest)

    @model_validator(mode="after")
    def pem_media(self) -> "PolicyTlsAuthorityInput":
        if (
            self.ca_certificate.media_type != "application/x-pem-file"
            or self.leaf_certificate.media_type != "application/x-pem-file"
        ):
            raise ValueError("policy TLS certificate authorities must be PEM artifacts")
        return self


class F3ProductionCompositionInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f3-production-input.v1"]
    composition_id: str = Field(min_length=1, max_length=256)
    authority_manifest: SourceArtifact
    installed: InstalledV1
    stores: StorePaths
    server: ServerV1
    secrets: SecretAuthorities
    receipt: ReceiptAuthority
    policy_tls: PolicyTlsAuthorityInput
    evidence_bindings: tuple[EvidenceRoleBindingV2, ...]
    resolution_task: c.TaskEligibilityInput

    @field_validator("evidence_bindings", mode="before")
    @classmethod
    def parse_bindings(cls, value: Any) -> Any:
        if type(value) is list:
            return tuple(
                EvidenceRoleBindingV2(
                    role=item["role"],
                    source=EvidenceRoleSourceV2(item["source"]),
                    producer_id=item["producer_id"],
                    producer_implementation_digest=item[
                        "producer_implementation_digest"
                    ],
                )
                for item in value
            )
        return value

    @model_validator(mode="after")
    def close_f3_input(self) -> "F3ProductionCompositionInput":
        if (
            self.authority_manifest.media_type
            != "application/vnd.breadboard.rl.phase5-f3-authority-bundle+json;version=1"
        ):
            raise ValueError("F3 authority manifest media type is not exact")
        if self.server.mode != "loopback":
            raise ValueError(
                "F3 one-episode service must use authenticated loopback authority"
            )
        purposes = {
            item.handle_id: item.purpose for item in self.secrets.handles.records
        }
        if purposes.get(self.receipt.secret_handle_id) != "receipt_signer":
            raise ValueError("receipt authority does not bind the receipt secret")
        policy_handles = [
            item
            for item in self.secrets.handles.records
            if item.purpose == "policy_callback"
        ]
        if len(policy_handles) != 1 or policy_handles[0].route_ids != (
            self.policy_tls.route_id,
        ):
            raise ValueError("policy callback secret does not bind the TLS route")
        if len(self.evidence_bindings) != 1:
            raise ValueError("F3 requires one terminal-result evidence binding")
        binding = self.evidence_bindings[0]
        if (
            binding.role != "terminal-result"
            or binding.source is not EvidenceRoleSourceV2.RUNNER_RESULT
            or binding.producer_implementation_digest != TERMINAL_IMPLEMENTATION_DIGEST
        ):
            raise ValueError("terminal-result evidence binding is not exact")
        artifacts = self.resolution_task.artifacts
        if len(artifacts) != 1 or artifacts[0].role != "repository-workspace":
            raise ValueError(
                "resolution task must expose only the repository workspace"
            )
        return self


class F3CompositionBuildResult(_ExactModel):
    composition_ref_path: str
    composition_manifest_path: str
    authority_bundle_path: str
    inventory_path: str
    service_output_root: str
    authority_manifest_sha256: str

    _paths = field_validator(
        "composition_ref_path",
        "composition_manifest_path",
        "authority_bundle_path",
        "inventory_path",
        "service_output_root",
    )(_absolute)
    _digest = field_validator("authority_manifest_sha256")(_digest)


def _read_pinned(source: SourceArtifact, *, canonical_json: bool) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source.path, flags)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_PINNED_ARTIFACT_BYTES
        ):
            raise F3CompositionError(
                f"artifact is not one bounded regular file: {source.path}"
            )
        payload = bytearray(before.st_size)
        view = memoryview(payload)
        offset = 0
        while offset < before.st_size:
            count = os.readv(fd, (view[offset:],))
            if count <= 0:
                raise F3CompositionError(
                    f"artifact truncated during read: {source.path}"
                )
            offset += count
        if os.read(fd, 1):
            raise F3CompositionError(f"artifact grew during read: {source.path}")
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise F3CompositionError(f"artifact changed during read: {source.path}")
    raw = bytes(payload)
    if sha256_bytes(raw) != source.sha256:
        raise F3CompositionError(f"artifact digest mismatch: {source.path}")
    if canonical_json:
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise F3CompositionError(f"artifact is not JSON: {source.path}") from exc
        if canonical_json_bytes(value) != raw:
            raise F3CompositionError(f"artifact is not canonical JSON: {source.path}")
    return raw


def _verify_pinned_file(source: SourceArtifact) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source.path, flags)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_PINNED_FILE_AUTHORITY_BYTES
        ):
            raise F3CompositionError(
                f"file authority is not one bounded regular file: {source.path}"
            )
        digest = hashlib.sha256()
        buffer = bytearray(min(before.st_size, 1024 * 1024))
        view = memoryview(buffer)
        offset = 0
        while offset < before.st_size:
            count = os.readv(fd, (view[: min(len(view), before.st_size - offset)],))
            if count <= 0:
                raise F3CompositionError(
                    f"file authority truncated during read: {source.path}"
                )
            digest.update(view[:count])
            offset += count
        if os.read(fd, 1):
            raise F3CompositionError(f"file authority grew during read: {source.path}")
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise F3CompositionError(f"file authority changed during read: {source.path}")
    if "sha256:" + digest.hexdigest() != source.sha256:
        raise F3CompositionError(f"file authority digest mismatch: {source.path}")


def _verify_secret(path: str, *, expected_digest: str | None = None) -> bytes:
    metadata = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o400
    ):
        raise F3CompositionError(f"secret is not one 0400 regular file: {path}")
    raw = Path(path).read_bytes()
    if not raw or len(raw) > 8192:
        raise F3CompositionError(f"secret size is outside the bound: {path}")
    if expected_digest is not None and sha256_bytes(raw) != expected_digest:
        raise F3CompositionError(f"secret digest mismatch: {path}")
    return raw


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o444) -> None:
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), mode
    )
    try:
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short composition write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _artifact_ref(path: Path, payload: bytes, media_type: str) -> ArtifactFileRefV1:
    return ArtifactFileRefV1(
        path=os.fspath(path.resolve()),
        sha256=sha256_bytes(payload),
        size_bytes=len(payload),
        media_type=media_type,
    )


def _measure_directory(name: str, path: str) -> DirectoryAuthorityRefV1:
    metadata = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise F3CompositionError(f"directory authority {name!r} must be 0700")
    return DirectoryAuthorityRefV1(
        authority_id=f"f3-{name}",
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner_uid=metadata.st_uid,
        mode="0700",
    )


def _verify_file_authority(path: str, digest: str, owner_uid: int, mode: int) -> None:
    source = SourceArtifact(
        path=path, sha256=digest, media_type="application/octet-stream"
    )
    _verify_pinned_file(source)
    metadata = os.stat(path, follow_symlinks=False)
    if metadata.st_uid != owner_uid or stat.S_IMODE(metadata.st_mode) != mode:
        raise F3CompositionError(f"installed file identity mismatch: {path}")


def _validate_installed(installed: InstalledV1, receipt: c.AdmissionReceipt) -> None:
    if len(installed.runner_adapters) != 1:
        raise F3CompositionError("F3 requires exactly one installed runner adapter")
    descriptor = installed.runner_adapters[0]
    if (
        descriptor.adapter_id,
        descriptor.runtime_abi,
        descriptor.implementation_digest,
    ) != (
        TERMINAL_ADAPTER_ID,
        TERMINAL_RUNTIME_ABI,
        TERMINAL_IMPLEMENTATION_DIGEST,
    ):
        raise F3CompositionError("installed terminal adapter identity mismatch")
    daemon = installed.private_docker_daemon
    if daemon is None:
        raise F3CompositionError(
            "F3 requires one explicit private Docker daemon authority"
        )
    for authority in (
        daemon.dockerd,
        daemon.docker,
        daemon.containerd,
        daemon.runc,
        *(item.archive for item in daemon.images),
    ):
        _verify_file_authority(
            authority.path, authority.digest, authority.owner_uid, authority.mode
        )
    for runtime in installed.runtimes:
        _verify_file_authority(
            runtime.executable_path,
            runtime.measured_binary_digest,
            daemon.docker.owner_uid,
            daemon.docker.mode,
        )
        if (
            runtime.oci_runtime_binary_path is None
            or runtime.oci_runtime_binary_digest is None
        ):
            raise F3CompositionError("hardened runtime OCI authority is missing")
        _verify_file_authority(
            runtime.oci_runtime_binary_path,
            runtime.oci_runtime_binary_digest,
            daemon.runc.owner_uid,
            daemon.runc.mode,
        )
    if len(installed.runtimes) != 2 or any(
        item.runtime_class is not c.RuntimeClass.HARDENED_DOCKER
        for item in installed.runtimes
    ):
        raise F3CompositionError(
            "F3 requires distinct hardened Docker primary and verifier runtimes"
        )
    if (
        len(installed.images) != 2
        or len(installed.security_policies) != 2
        or len(installed.network_policies) != 1
        or len(installed.verifiers) != 1
    ):
        raise F3CompositionError("F3 installed primary/verifier catalogs are not exact")
    offline = {(item.source_image_digest, item.image_id) for item in daemon.images}
    installed_images = {item.image_digest for item in installed.images}
    if {item[0] for item in offline} != installed_images or len(offline) != 2:
        raise F3CompositionError(
            "private archive image IDs do not bind both installed images"
        )
    primary = receipt.effective_capabilities.sandbox
    verifier_grant = receipt.effective_capabilities.verifier
    if primary.runtime_class is not c.RuntimeClass.HARDENED_DOCKER:
        raise F3CompositionError("primary sandbox is not hardened Docker")
    verifier = installed.verifiers[0]
    if (
        verifier.grant != verifier_grant
        or verifier.runtime_class is not c.RuntimeClass.HARDENED_DOCKER
    ):
        raise F3CompositionError(
            "installed verifier does not bind the admitted verifier"
        )
    if (
        not verifier.argv
        or not verifier.result_relative_path
        or any(not value for value in verifier.argv)
    ):
        raise F3CompositionError("verifier argv/result authority is incomplete")
    if (
        primary.image_digest == verifier.grant.image_digest
        or primary.security_policy_digest == verifier.security_policy_digest
    ):
        raise F3CompositionError(
            "primary and verifier image/security authorities must be independent"
        )
    network = installed.network_policies[0]
    if (
        network.policy_digest != primary.network_policy_digest
        or network.policy_digest != verifier.grant.network_policy_digest
    ):
        raise F3CompositionError("primary and verifier network authorities differ")
    if (
        network.mode != "none"
        or network.docker_network != "none"
        or network.egress_route_ids
        or not network.default_deny
    ):
        raise F3CompositionError(
            "F3 sandbox network must be credential-free and default-deny"
        )


def _validate_tls(
    value: PolicyTlsAuthorityInput, graph: PolicyHttpAuthorityGraphV1
) -> tuple[bytes, bytes]:
    ca = _read_pinned(value.ca_certificate, canonical_json=False)
    leaf = _read_pinned(value.leaf_certificate, canonical_json=False)
    _verify_secret(
        value.leaf_private_key.path, expected_digest=value.leaf_private_key.sha256
    )
    if (
        ca.count(b"-----BEGIN CERTIFICATE-----") != 1
        or leaf.count(b"-----BEGIN CERTIFICATE-----") != 1
    ):
        raise F3CompositionError(
            "policy CA and leaf authorities must each contain one certificate"
        )
    try:
        leaf_der = ssl.PEM_cert_to_DER_cert(leaf.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise F3CompositionError(
            "policy leaf authority is not a PEM certificate"
        ) from exc
    if sha256_bytes(leaf_der) != value.expected_leaf_der_sha256:
        raise F3CompositionError("policy leaf DER digest mismatch")
    if len(graph.routes) != 1 or graph.routes[0].grant.route_id != value.route_id:
        raise F3CompositionError("policy TLS route authority mismatch")
    route = graph.routes[0]
    parsed = urlsplit(f"//{route.authority}")
    if route.scheme.value != "https" or parsed.hostname is None:
        raise F3CompositionError("policy route must be exact HTTPS")
    dns = graph.dns_policies[0]
    ips = graph.ip_policies[0]
    if (
        dns.hostname != parsed.hostname
        or tuple(dns.allowed_addresses) != tuple(ips.allowed_addresses)
        or parsed.hostname not in ips.allowed_addresses
    ):
        raise F3CompositionError("policy DNS/IP/TLS authorities are not closed")
    return ca, leaf


def build_f3_production_composition(
    spec: F3ProductionCompositionInput | Mapping[str, Any], output_dir: str
) -> F3CompositionBuildResult:
    parsed = (
        spec
        if isinstance(spec, F3ProductionCompositionInput)
        else F3ProductionCompositionInput.model_validate(spec, strict=True)
    )
    root = Path(_absolute(output_dir))
    if os.path.lexists(root):
        raise F3CompositionError("composition output already exists")
    for handle_id, path in parsed.secrets.files.items():
        try:
            _verify_secret(path)
        except F3CompositionError as exc:
            raise F3CompositionError(
                f"secret handle {handle_id!r} is unavailable or unsafe"
            ) from exc
    authority_manifest_raw = _read_pinned(
        parsed.authority_manifest, canonical_json=True
    )
    authority_manifest = F3AuthorityBundleManifest.model_validate_json(
        authority_manifest_raw, strict=True
    )
    if authority_manifest.composition_id != parsed.composition_id:
        raise F3CompositionError("F3 authority and production composition IDs differ")
    if set(authority_manifest.artifacts) != _EXPECTED_AUTHORITY_ARTIFACTS:
        raise F3CompositionError("F3 authority artifact closure is not exact")
    if authority_manifest.cas_root != parsed.stores.cas:
        raise F3CompositionError(
            "F3 authority CAS is not the composition CAS authority"
        )
    if (
        authority_manifest.task_contract_digest
        != parsed.resolution_task.canonical_digest()
    ):
        raise F3CompositionError(
            "resolution task digest does not bind the authored task contract"
        )
    root.mkdir(mode=0o700, parents=False)
    artifacts = root / "artifacts"
    artifacts.mkdir(mode=0o700)
    try:
        copied: dict[str, tuple[ArtifactFileRefV1, bytes]] = {}
        for name, source_ref in sorted(authority_manifest.artifacts.items()):
            source = SourceArtifact(
                path=source_ref.path,
                sha256=source_ref.sha256,
                media_type=source_ref.media_type,
            )
            payload = _read_pinned(source, canonical_json=True)
            destination = artifacts / name
            _write_exclusive(destination, payload)
            copied[name] = (
                _artifact_ref(destination, payload, source.media_type),
                payload,
            )
        policy = c.AdmissionPolicySnapshot.model_validate_json(
            copied["admission-policy.json"][1], strict=True
        )
        registries = c.RegistrySnapshotSet.model_validate_json(
            copied["registry-snapshot.json"][1], strict=True
        )
        receipt = c.AdmissionReceipt.model_validate_json(
            copied["admission-receipt.json"][1], strict=True
        )
        admitted = c.AdmittedSetManifest.model_validate_json(
            copied["admitted-set.json"][1], strict=True
        )
        selector = c.DirectSelector.model_validate_json(
            copied["direct-selector.json"][1], strict=True
        )
        policy_http = PolicyHttpAuthorityGraphV1.model_validate_json(
            copied["policy-http.json"][1], strict=True
        )
        revocations = tuple(
            c.RevocationBinding.model_validate(item, strict=True)
            for item in json.loads(copied["revocations.json"][1])
        )
        observations = tuple(
            c.PolicyCapabilityObservation.model_validate_json(
                canonical_json_bytes(item), strict=True
            )
            for item in json.loads(copied["policy-capabilities.json"][1])
        )
        compiled = CompiledConfigManifest.from_json(copied["compiled-manifest.json"][1])
        config_bundle = ConfigBundleManifest.from_json(copied["config-bundle.json"][1])
        if config_bundle.canonical_bytes() != copied["config-bundle.json"][1]:
            raise F3CompositionError("config bundle is not canonical")
        if (
            selector.admitted_set_root != admitted.canonical_digest()
            or selector.candidate.receipt_digest
            != copied["admission-receipt.json"][0].sha256
        ):
            raise F3CompositionError(
                "direct selector does not bind the admitted receipt"
            )
        if receipt.subject.authority_scope_digest != policy.subject_scope_digest:
            raise F3CompositionError(
                "receipt subject does not bind the admission policy"
            )
        if (
            receipt.task_binding_digest != authority_manifest.task_binding_digest
            or receipt.effective_capabilities.task.repository_snapshot_digest
            != authority_manifest.repository_snapshot_digest
        ):
            raise F3CompositionError(
                "receipt task/repository authorities differ from F3 manifest"
            )
        _validate_installed(parsed.installed, receipt)
        ca, leaf = _validate_tls(parsed.policy_tls, policy_http)
        ca_path = artifacts / "policy-ca.pem"
        leaf_path = artifacts / "policy-leaf.pem"
        _write_exclusive(ca_path, ca)
        _write_exclusive(leaf_path, leaf)
        ca_ref = _artifact_ref(ca_path, ca, "application/x-pem-file")
        _artifact_ref(leaf_path, leaf, "application/x-pem-file")
        route = policy_http.routes[0]
        server_name = urlsplit(f"//{route.authority}").hostname
        if server_name is None:
            raise F3CompositionError("policy server name is absent")
        tls = PolicyTlsTrustAuthorityV1(
            schema_version="bb.rl.policy-tls-trust-authority.v1",
            route_id=route.grant.route_id,
            server_name=server_name,
            ca_bundle_ref=ca_ref,
            expected_leaf_certificate_sha256=parsed.policy_tls.expected_leaf_der_sha256,
            minimum_tls_version="TLSv1.3",
            cipher_suite="TLS_AES_256_GCM_SHA384",
            dedicated_single_leaf_ca=True,
        )
        authority = AuthorityBundleV1(
            schema_version="bb.rl.harness-authority-bundle.v1",
            admission_policy=policy,
            registries=registries,
            revocations=revocations,
            policy_capabilities=observations,
            policy_http=policy_http,
            tls_trust=(tls,),
            compiled_manifest_refs=(copied["compiled-manifest.json"][0],),
            admission_receipt_refs=(copied["admission-receipt.json"][0],),
        )
        authority_path = artifacts / "authority-bundle.json"
        authority_bytes = authority.canonical_bytes()
        _write_exclusive(authority_path, authority_bytes)
        authority_ref = _artifact_ref(
            authority_path, authority_bytes, "application/json"
        )
        store_paths = {name: getattr(parsed.stores, name) for name in _STORE_NAMES}
        stores = StoresV1(
            **{
                name: _measure_directory(name, path)
                for name, path in store_paths.items()
            },
            lease_ttl_seconds=parsed.stores.lease_ttl_seconds,
        )
        _measure_directory("service_output_root", parsed.stores.service_output_root)
        compiler = compiled.compiler
        manifest = HarnessCompositionManifestV1(
            schema_version="bb.rl.harness-composition.v1",
            composition_id=parsed.composition_id,
            authority_bundle_ref=authority_ref,
            config_bundle_ref=copied["config-bundle.json"][0],
            admitted_set_ref=copied["admitted-set.json"][0],
            selector_catalog=SelectorCatalogV1(
                direct=(copied["direct-selector.json"][0],), weighted=()
            ),
            control_plane=ControlPlaneV1(
                admission_policy_ref=copied["admission-policy.json"][0],
                registry_snapshot_ref=copied["registry-snapshot.json"][0],
                revocation_snapshot_ref=copied["revocations.json"][0],
                policy_capability_snapshot_ref=copied["policy-capabilities.json"][0],
                compiler=CompilerIdentityV1(
                    compiler_id=compiler.compiler_id,
                    semantic_version=compiler.compiler_version,
                    code_digest=compiler.compiler_code_digest,
                    source_schema_digest=compiler.config_schema_digest,
                    manifest_schema_digest=compiler.manifest_schema_digest,
                    canonicalizer_id=compiler.canonicalizer_id,
                    runtime_abi=compiler.runtime_abi,
                ),
                receipt_authenticator=ReceiptAuthenticatorV1(
                    key_id=parsed.receipt.key_id,
                    algorithm="hmac-sha256-v1",
                    secret_handle_id=parsed.receipt.secret_handle_id,
                ),
            ),
            installed=parsed.installed,
            stores=stores,
            server=parsed.server,
            outer_bridge_plan=None,
            prebound_service_socket_plans=(),
            openssl_authority=None,
            host_runtime_authority=None,
            tls_callback_runtime_input=None,
            evidence_receipt_signing_authority=None,
            secret_handles=parsed.secrets.handles,
            evidence_bindings=parsed.evidence_bindings,
        )
        manifest_path = artifacts / "composition-manifest.json"
        manifest_bytes = manifest.canonical_bytes()
        _write_exclusive(manifest_path, manifest_bytes)
        composition_ref = CompositionRefV1(
            schema_version="bb.rl.harness-composition-ref.v1",
            manifest_path=os.fspath(manifest_path.resolve()),
            manifest_sha256=sha256_bytes(manifest_bytes),
            manifest_size_bytes=len(manifest_bytes),
            manifest_media_type=COMPOSITION_MEDIA_TYPE,
        )
        ref_path = artifacts / "composition-ref.json"
        _write_exclusive(ref_path, composition_ref.canonical_bytes())
        inventory_path = root / "inventory.json"
        inventory = {
            "schema_version": "bb.rl.phase5-f3-production-inventory.v1",
            "composition_id": parsed.composition_id,
            "authority_manifest_sha256": parsed.authority_manifest.sha256,
            "composition_manifest_sha256": composition_ref.manifest_sha256,
            "service_output_root": parsed.stores.service_output_root,
            "artifacts": [
                {
                    "path": path.name,
                    "sha256": sha256_bytes(path.read_bytes()),
                    "size_bytes": path.stat().st_size,
                }
                for path in sorted(artifacts.iterdir(), key=lambda item: item.name)
            ],
        }
        _write_exclusive(inventory_path, canonical_json_bytes(inventory))
        return F3CompositionBuildResult(
            composition_ref_path=os.fspath(ref_path.resolve()),
            composition_manifest_path=os.fspath(manifest_path.resolve()),
            authority_bundle_path=os.fspath(authority_path.resolve()),
            inventory_path=os.fspath(inventory_path.resolve()),
            service_output_root=parsed.stores.service_output_root,
            authority_manifest_sha256=parsed.authority_manifest.sha256,
        )
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def load_f3_production_composition(
    build: F3CompositionBuildResult,
    secret_files: Mapping[str, str],
    *,
    fault_injection_authority: V2FaultInjectionAuthority | None = None,
) -> ProductionComposition:
    if type(build) is not F3CompositionBuildResult:
        raise TypeError("build must be an exact F3CompositionBuildResult")
    return load_production_composition(
        build.composition_ref_path,
        secret_files,
        fault_injection_authority=fault_injection_authority,
    )


__all__ = [
    "F3CompositionBuildResult",
    "F3CompositionError",
    "F3ProductionCompositionInput",
    "PolicyTlsAuthorityInput",
    "ReceiptAuthority",
    "SecretAuthorities",
    "SecretFileAuthority",
    "SourceArtifact",
    "StorePaths",
    "build_f3_production_composition",
    "load_f3_production_composition",
    "sha256_bytes",
]
