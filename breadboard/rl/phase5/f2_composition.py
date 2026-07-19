from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import ssl
import socket
import stat
import subprocess
from types import MappingProxyType
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

from agentic_coder_prototype.compilation.contracts import CompiledConfigManifest, ConfigBundleManifest
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
    EvidenceReceiptSigningAuthorityV1,
    HarnessCompositionManifestV1,
    InstalledV1,
    HostRuntimeAuthorityV1,
    OpenSslAuthorityV1,
    OuterBridgePlanV1,
    PreboundServiceSocketPlanV1,
    PinnedFileAuthorityV1,
    PolicyHttpAuthorityGraphV1,
    PolicyTlsTrustAuthorityV1,
    ProductionComposition,
    ReceiptAuthenticatorV1,
    SecretHandlesV1,
    SelectorCatalogV1,
    TlsCallbackRuntimeInputV1,
    ServerV1,
    StoresV1,
    load_production_composition,
)
from breadboard.rl.harness.evidence import EvidenceRoleBindingV2
from breadboard.rl.state.cas import FilesystemCAS

_DIGEST_PREFIX = "sha256:"
_STORE_NAMES = ("cas", "locator", "materialization_cache", "workspace", "lease", "security_profile")
_COMPILED_MEDIA = "application/vnd.breadboard.compiled-manifest+json;version=1"
_RECEIPT_MEDIA = "application/vnd.breadboard.admission-receipt+json;version=1"
_ADMITTED_MEDIA = "application/vnd.breadboard.admitted-set+json;version=1"
_SELECTOR_MEDIA = "application/vnd.breadboard.direct-selector+json;version=1"
_F1_PREREQUISITE_ID = "20260711T203833Z-slurm-263537"
_F1_PREREQUISITE_ROOT = (
    "docs_tmp/ZYPHRA/RL_PHASE_5/evidence/target/F1/" + _F1_PREREQUISITE_ID
)
_F1_PREREQUISITE_REPORT_DIGEST = (
    "sha256:eaa3a09e8c396946fe82036f3bbf0d778503a647e190627b2ad7f944a2f16f59"
)
_HOST_RUNTIME_TARGET_RUN_ID = "20260712T023000Z-slurm-264250"
_HOST_RUNTIME_ROOT = (
    "/shared/breadboard-f2/host-runtime/"
    "07730b5d200c38171ae905345f1d21f9615ecc67fd565065bd88a69c42f14d91/runtime"
)
_HOST_RUNTIME_REPORT_DIGEST = (
    "sha256:e6428360047ed4d3c94cb4910e6ea4cfa6ebbf0e4fcd18eb2e2e679162b56431"
)
_FORBIDDEN_ROW_AUTHORITY_KEYS = frozenset({
    "admission_policy", "authority", "authority_bundle", "compiled", "config", "config_digest",
    "config_set", "image", "network", "overlays", "policy", "runtime", "secret", "selector",
    "server", "task_binding", "tls", "tool", "verifier",
})


class F2CompositionError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()

def _f1_prerequisite_bytes(report_ref: str, canonical_root: str) -> bytes:
    if (
        report_ref != _F1_PREREQUISITE_REPORT_DIGEST
        or canonical_root != _F1_PREREQUISITE_ROOT
    ):
        raise F2CompositionError("F1 prerequisite is not the approved canonical input")
    return canonical_json_bytes({
        "schema_version": "bb.rl.f2.f1-prerequisite.v1",
        "canonical_id": _F1_PREREQUISITE_ID,
        "report_schema": "bb.rl.f1.ibm-exact-container-preflight-report.v3",
        "report_ref": report_ref,
        "canonical_root": canonical_root,
    })


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest_value(value: str) -> str:
    if (
        len(value) != 71
        or not value.startswith(_DIGEST_PREFIX)
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise ValueError("artifact digest must be lowercase sha256")
    return value


def _positive_decimal(value: str) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 20
    ):
        raise ValueError("nanosecond identity must be a canonical positive decimal string")
    return value


class SourceArtifact(_ExactModel):
    path: str
    sha256: str
    media_type: str = "application/json"

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        if not value.startswith("/") or os.path.normpath(value) != value:
            raise ValueError("artifact path must be absolute and normalized")
        return value

    @field_validator("sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _digest_value(value)




class ConfigMemberSource(_ExactModel):
    logical_path: str
    source: SourceArtifact


class TlsAuthorityInput(_ExactModel):
    route_id: str
    target_ip: str
    ca_certificate: SourceArtifact
    leaf_certificate: SourceArtifact
    expected_leaf_der_sha256: str
    minimum_tls_version: Literal["TLSv1.3"]
    cipher_suite: Literal["TLS_AES_256_GCM_SHA384"]
    dedicated_single_leaf_ca: Literal[True]

    @model_validator(mode="after")
    def exact_target(self) -> "TlsAuthorityInput":
        address = ipaddress.ip_address(self.target_ip)
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError("policy callback target must be a non-loopback literal IP")
        if self.ca_certificate.media_type != "application/x-pem-file" or self.leaf_certificate.media_type != "application/x-pem-file":
            raise ValueError("TLS observations must be PEM files")
        return self


class OpenSslAuthorityInput(_ExactModel):
    path: Literal["/usr/bin/openssl"]
    sha256: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    ctime_ns: str
    size_bytes: int = Field(gt=0)
    mode: int = Field(ge=0, le=0o7777)
    owner_uid: int = Field(ge=0)
    version_stdout_sha256: str
    version: str = Field(min_length=1, max_length=256)
    discovery_report: SourceArtifact

    _digests = field_validator("sha256", "version_stdout_sha256")(_digest_value)
    _ctime_ns = field_validator("ctime_ns")(_positive_decimal)

    @model_validator(mode="after")
    def executable_mode(self) -> "OpenSslAuthorityInput":
        if not self.mode & 0o111:
            raise ValueError("OpenSSL authority must be executable")
        return self


class ExecutableObservationInput(_ExactModel):
    path: str
    sha256: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    ctime_ns: str
    size_bytes: int = Field(gt=0)
    mode: int = Field(ge=0, le=0o7777)
    owner_uid: int = Field(ge=0)

    _digest = field_validator("sha256")(_digest_value)
    _ctime_ns = field_validator("ctime_ns")(_positive_decimal)

    @model_validator(mode="after")
    def exact_executable(self) -> "ExecutableObservationInput":
        if (
            not self.path.startswith("/")
            or os.path.normpath(self.path) != self.path
            or not self.mode & 0o111
            or self.mode & 0o022
        ):
            raise ValueError("wrapper executable authority is not exact and sealed")
        return self


class WrapperHostExecutablesInput(_ExactModel):
    cleanup_python: ExecutableObservationInput
    sudo: ExecutableObservationInput
    env: ExecutableObservationInput
    docker: ExecutableObservationInput
    binary_discovery_report: SourceArtifact

    @model_validator(mode="after")
    def exact_paths(self) -> "WrapperHostExecutablesInput":
        expected = {
            "cleanup_python": (
                "/shared/breadboard-f2/host-runtime/"
                "07730b5d200c38171ae905345f1d21f9615ecc67fd565065bd88a69c42f14d91/"
                "runtime/bin/python"
            ),
            "sudo": "/usr/bin/sudo",
            "env": "/usr/bin/env",
            "docker": "/usr/bin/docker",
        }
        if any(getattr(self, name).path != path for name, path in expected.items()):
            raise ValueError("wrapper host executable path authority mismatch")
        if self.cleanup_python.sha256 != (
            "sha256:202c17d1671602a4ef1d43e9b2fdbef0769443f37bf5e51f6b603e0b2c27d9d8"
        ):
            raise ValueError("wrapper cleanup Python digest is not approved")
        return self


class WrapperImageOperatorAuthorization(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-image-authorization.v1"]
    authority_id: str
    build_report_ref: str
    image_tar_ref: str
    image_id: str
    source_image_digest: str
    authorized: Literal[True]

    _digests = field_validator(
        "build_report_ref", "image_tar_ref", "image_id", "source_image_digest"
    )(_digest_value)


class AuthoritySources(_ExactModel):
    f1_prerequisite_report: SourceArtifact
    f1_prerequisite_canonical_root: str
    ibm_target_record: SourceArtifact
    host_runtime_root: Literal[
        "/shared/breadboard-f2/host-runtime/07730b5d200c38171ae905345f1d21f9615ecc67fd565065bd88a69c42f14d91/runtime"
    ]
    host_runtime_build_report: SourceArtifact
    wrapper_host_executables: WrapperHostExecutablesInput
    wrapper_image_build_report: SourceArtifact
    wrapper_image_operator_authorization: SourceArtifact
    admission_policy: SourceArtifact
    registry_snapshot: SourceArtifact
    revocation_snapshot: SourceArtifact
    policy_capability_snapshot: SourceArtifact
    policy_http: SourceArtifact
    mount_broker_implementation: SourceArtifact
    openssl: OpenSslAuthorityInput
    config_bundle: SourceArtifact
    config_members: tuple[ConfigMemberSource, ...]
    compiled_manifests: tuple[SourceArtifact, ...]
    admission_receipts: tuple[SourceArtifact, ...]
    admitted_set: SourceArtifact
    direct_selector: SourceArtifact
    tls: TlsAuthorityInput

    @field_validator("f1_prerequisite_canonical_root")
    @classmethod
    def canonical_f1_root(cls, value: str) -> str:
        if value != _F1_PREREQUISITE_ROOT:
            raise ValueError("F1 prerequisite canonical root is not the approved F2 prerequisite")
        return value

    @model_validator(mode="after")
    def exact_members(self) -> "AuthoritySources":
        if self.f1_prerequisite_report.sha256 != _F1_PREREQUISITE_REPORT_DIGEST:
            raise ValueError("F1 prerequisite report digest is not approved for F2")
        if (
            self.host_runtime_root != _HOST_RUNTIME_ROOT
            or self.host_runtime_build_report.sha256 != _HOST_RUNTIME_REPORT_DIGEST
        ):
            raise ValueError("host runtime authority is not the approved F2 runtime")
        logical = tuple(item.logical_path for item in self.config_members)
        if logical != tuple(sorted(set(logical))):
            raise ValueError("config members must be logical-path sorted and unique")
        if len(self.compiled_manifests) != 1 or len(self.admission_receipts) != 1:
            raise ValueError("F2 requires exactly one compiled manifest and one admission receipt")
        if self.config_bundle.media_type != "application/json":
            raise ValueError("config bundle media type mismatch")
        if self.compiled_manifests[0].media_type != _COMPILED_MEDIA:
            raise ValueError("compiled manifest media type mismatch")
        if self.admission_receipts[0].media_type != _RECEIPT_MEDIA:
            raise ValueError("admission receipt media type mismatch")
        if self.admitted_set.media_type != _ADMITTED_MEDIA or self.direct_selector.media_type != _SELECTOR_MEDIA:
            raise ValueError("admitted set or direct selector media type mismatch")
        return self


class StorePaths(_ExactModel):
    cas: str
    locator: str
    materialization_cache: str
    workspace: str
    lease: str
    security_profile: str
    lease_ttl_seconds: int = Field(gt=0, le=86400)

    @field_validator(*_STORE_NAMES)
    @classmethod
    def normalized_absolute(cls, value: str) -> str:
        if not value.startswith("/") or os.path.normpath(value) != value:
            raise ValueError("store path must be absolute and normalized")
        return value


class SecretValidationInput(_ExactModel):
    handles: SecretHandlesV1
    files: dict[str, str]

    @model_validator(mode="after")
    def exact_handle_set(self) -> "SecretValidationInput":
        live_only_purposes = {
            "callback_tls_private_key",
            "evidence_receipt_signing_key",
            "callback_observation_signing_key",
        }
        expected = {
            item.handle_id
            for item in self.handles.records
            if item.purpose not in live_only_purposes
        }
        if set(self.files) != expected or len(set(self.files.values())) != len(self.files):
            raise ValueError("persisted secret files must exactly cover non-live handle IDs")
        return self


class ReceiptAuthorityInput(_ExactModel):
    key_id: str
    secret_handle_id: str


class RequestTemplateInput(_ExactModel):
    task_input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_row_authority(self) -> "RequestTemplateInput":
        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.lower() in _FORBIDDEN_ROW_AUTHORITY_KEYS or key.lower().endswith(("_authority", "_digest", "_ref")):
                        raise ValueError(f"request row may not carry authority field {key!r}")
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(self.task_input)
        visit(self.context)
        return self


class F2ProductionCompositionInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f2-production-input.v1"]
    composition_id: str = Field(min_length=1, max_length=256)
    authority: AuthoritySources
    installed: InstalledV1
    stores: StorePaths
    server: ServerV1
    outer_bridge_plan: OuterBridgePlanV1
    prebound_service_socket_plans: tuple[PreboundServiceSocketPlanV1, ...]
    secrets: SecretValidationInput
    receipt: ReceiptAuthorityInput
    evidence_bindings: tuple[EvidenceRoleBindingV2, ...]
    evidence_receipt_signing_authority: EvidenceReceiptSigningAuthorityV1
    request_template: RequestTemplateInput

    @model_validator(mode="after")
    def bind_prebound_socket_plans(self) -> "F2ProductionCompositionInput":
        roles = tuple(item.role for item in self.prebound_service_socket_plans)
        if roles != ("callback_tls", "fixed_policy", "harness"):
            raise ValueError("prebound service socket roles must be exact and sorted")
        if any(
            item.gateway != self.outer_bridge_plan.gateway
            for item in self.prebound_service_socket_plans
        ):
            raise ValueError("prebound service socket plan gateway mismatch")
        harness = tuple(
            item for item in self.prebound_service_socket_plans
            if item.role == "harness"
        )
        if len(harness) != 1 or harness[0].observed_port != self.server.port:
            raise ValueError("harness server does not bind its socket plan")
        if self.authority.tls.target_ip != self.outer_bridge_plan.gateway:
            raise ValueError("TLS callback target must equal private bridge gateway")
        tls_key_handles = tuple(
            item.handle_id
            for item in self.secrets.handles.records
            if item.purpose == "callback_tls_private_key"
        )
        if len(tls_key_handles) != 1:
            raise ValueError("exactly one callback TLS private-key handle is required")
        evidence_key_handles = tuple(
            item.handle_id
            for item in self.secrets.handles.records
            if item.purpose == "evidence_receipt_signing_key"
        )
        if evidence_key_handles != (
            self.evidence_receipt_signing_authority.private_key_secret_handle_id,
        ):
            raise ValueError("evidence receipt signing handle is not exact")
        callback_observation_keys = tuple(
            item.handle_id
            for item in self.secrets.handles.records
            if item.purpose == "callback_observation_signing_key"
        )
        if len(callback_observation_keys) != 1:
            raise ValueError("exactly one callback observation signing handle is required")
        return self




def _prepare_callback_tls_runtime(
    spec: F2ProductionCompositionInput,
    runtime: TlsCallbackRuntimeInputV1,
    *,
    live_secret_files: Mapping[str, str],
    socket_fd: int,
    private_key_fd: int,
) -> tuple[int, int, Mapping[str, Any]]:
    if type(socket_fd) is not int or socket_fd < 0 or type(private_key_fd) is not int or private_key_fd < 0:
        raise F2CompositionError("callback TLS runtime descriptors are invalid")
    try:
        private_key_path = live_secret_files[runtime.private_key_secret_handle_id]
    except KeyError as exc:
        raise F2CompositionError("callback TLS private key handle is unavailable") from exc
    callback_plans = tuple(
        item
        for item in spec.prebound_service_socket_plans
        if item.role == runtime.socket_role
    )
    if len(callback_plans) != 1:
        raise F2CompositionError("callback TLS socket plan is not singular")
    callback_plan = callback_plans[0]
    callback_socket = socket.fromfd(
        socket_fd, socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP
    )
    try:
        address = callback_socket.getsockname()
        socket_metadata = os.fstat(socket_fd)
        if (
            callback_socket.family != socket.AF_INET
            or callback_socket.type & socket.SOCK_STREAM != socket.SOCK_STREAM
            or callback_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1
            or address != (runtime.host, runtime.observed_port)
            or runtime.host != callback_plan.gateway
            or runtime.observed_port != callback_plan.observed_port
            or runtime.socket_plan_id != callback_plan.socket_plan_id
            or (
                socket_metadata.st_dev,
                socket_metadata.st_ino,
                socket_metadata.st_mode,
                socket_metadata.st_uid,
            )
            != (
                callback_plan.socket_device,
                callback_plan.socket_inode,
                callback_plan.socket_mode,
                callback_plan.socket_owner_uid,
            )
        ):
            raise F2CompositionError("callback TLS socket plan observation mismatch")
    finally:
        callback_socket.close()
    graph_bytes = _read_pinned(spec.authority.policy_http, canonical_json=True)
    graph = PolicyHttpAuthorityGraphV1.model_validate_json(graph_bytes, strict=True)
    if len(graph.routes) != 1:
        raise F2CompositionError("callback TLS route authority is not singular")
    route_address = urlsplit(f"//{graph.routes[0].authority}")
    if (
        runtime.route_id != spec.authority.tls.route_id
        or runtime.host != spec.outer_bridge_plan.gateway
        or runtime.host != spec.authority.tls.target_ip
        or runtime.host != route_address.hostname
        or runtime.observed_port != route_address.port
        or runtime.ca_certificate_sha256 != spec.authority.tls.ca_certificate.sha256
        or runtime.leaf_certificate_sha256 != spec.authority.tls.leaf_certificate.sha256
        or runtime.tls_policy.minimum_tls_version != spec.authority.tls.minimum_tls_version
        or runtime.tls_policy.maximum_tls_version != spec.authority.tls.minimum_tls_version
        or not runtime.tls_policy.server_certificate_verification_required
        or not runtime.tls_policy.hostname_verification_required
        or not runtime.tls_policy.bearer_authentication_required
        or runtime.tls_policy.mutual_tls_required
    ):
        raise F2CompositionError("callback TLS runtime does not bind route authority")
    key_metadata = os.fstat(private_key_fd)
    key_path_metadata = os.stat(private_key_path, follow_symlinks=False)
    key_identity = (
        key_metadata.st_dev,
        key_metadata.st_ino,
        key_metadata.st_ctime_ns,
        key_metadata.st_size,
        stat.S_IMODE(key_metadata.st_mode),
        key_metadata.st_uid,
    )
    if (
        not stat.S_ISREG(key_metadata.st_mode)
        or stat.S_IMODE(key_metadata.st_mode) != 0o400
        or key_identity
        != (
            key_path_metadata.st_dev,
            key_path_metadata.st_ino,
            key_path_metadata.st_ctime_ns,
            key_path_metadata.st_size,
            stat.S_IMODE(key_path_metadata.st_mode),
            key_path_metadata.st_uid,
        )
    ):
        raise F2CompositionError("callback TLS private key identity is unsafe")
    openssl_path = spec.authority.openssl.path
    command_options = {
        "executable": openssl_path,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": True,
        "close_fds": True,
        "env": {"LC_ALL": "C", "PATH": "/nonexistent"},
    }
    for certificate in (runtime.ca_certificate_ref, runtime.leaf_certificate_ref):
        _verify_pinned_file(SourceArtifact(
            path=certificate.path,
            sha256=certificate.sha256,
            media_type=certificate.media_type,
        ))
    certificate_public = subprocess.run(
        [openssl_path, "x509", "-in", runtime.leaf_certificate_ref.path, "-pubkey", "-noout"],
        **command_options,
    )
    key_public = subprocess.run(
        [openssl_path, "pkey", "-in", f"/proc/{os.getpid()}/fd/{private_key_fd}", "-pubout"],
        pass_fds=(private_key_fd,),
        **command_options,
    )
    public_der = subprocess.run(
        [openssl_path, "pkey", "-pubin", "-outform", "DER"],
        input=certificate_public.stdout,
        executable=openssl_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        close_fds=True,
        env={"LC_ALL": "C", "PATH": "/nonexistent"},
    )
    if (
        certificate_public.stderr
        or key_public.stderr
        or public_der.stderr
        or certificate_public.stdout != key_public.stdout
        or sha256_bytes(public_der.stdout) != runtime.leaf_public_key_sha256
    ):
        raise F2CompositionError("callback TLS private key does not bind leaf certificate")
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_3
    tls_context.maximum_version = ssl.TLSVersion.TLSv1_3
    if tls_context.verify_mode != ssl.CERT_NONE:
        raise F2CompositionError("callback TLS server must not require mutual TLS")
    tls_context.load_cert_chain(
        certfile=runtime.leaf_certificate_ref.path,
        keyfile=f"/proc/{os.getpid()}/fd/{private_key_fd}",
    )
    socket_dup = os.dup(socket_fd)
    key_dup = os.dup(private_key_fd)
    os.set_inheritable(socket_dup, False)
    os.set_inheritable(key_dup, False)
    identity = MappingProxyType({
        "handle_id": runtime.private_key_secret_handle_id,
        "device": key_identity[0],
        "inode": key_identity[1],
        "ctime_ns": key_identity[2],
        "size_bytes": key_identity[3],
        "mode": key_identity[4],
        "owner_uid": key_identity[5],
        "leaf_public_key_sha256": runtime.leaf_public_key_sha256,
    })
    return socket_dup, key_dup, identity


class OpenF2Composition:
    __slots__ = (
        "build", "composition", "callback_tls_runtime",
        "callback_tls_socket_fd", "callback_tls_private_key_fd",
        "callback_tls_private_key_identity", "_callback_closed",
    )

    def __init__(
        self,
        build: BuildResult,
        composition: ProductionComposition,
        callback_tls_runtime: TlsCallbackRuntimeInputV1,
        callback_tls_socket_fd: int,
        callback_tls_private_key_fd: int,
        callback_tls_private_key_identity: Mapping[str, Any],
    ) -> None:
        self.build = build
        self.composition = composition
        self.callback_tls_runtime = callback_tls_runtime
        self.callback_tls_socket_fd = callback_tls_socket_fd
        self.callback_tls_private_key_fd = callback_tls_private_key_fd
        self.callback_tls_private_key_identity = callback_tls_private_key_identity
        self._callback_closed = False

    @property
    def outer_bridge_lease(self) -> Any:
        try:
            lease = self.composition.outer_bridge_lease
        except AttributeError as exc:
            raise F2CompositionError("production loader did not emit outer bridge lease") from exc
        if lease is None:
            raise F2CompositionError("production loader did not create outer bridge lease")
        return lease

    @property
    def prebound_service_sockets(self) -> Mapping[str, Any]:
        return self.composition.prebound_service_sockets

    @property
    def tls_callback_socket_lease(self) -> Any:
        try:
            lease = self.composition.tls_callback_socket_lease
        except AttributeError as exc:
            raise F2CompositionError(
                "production loader did not emit callback TLS socket lease"
            ) from exc
        if lease is None:
            raise F2CompositionError(
                "production loader did not create callback TLS socket lease"
            )
        return lease


    @property
    def outer_bridge_cleanup_receipt(self) -> Any:
        return self.composition.outer_bridge_cleanup_receipt

    async def close(self) -> None:
        try:
            await self.composition.close()
            if (
                self.outer_bridge_lease is not None
                and self.outer_bridge_cleanup_receipt is None
            ):
                raise F2CompositionError("production loader did not emit bridge cleanup receipt")
        finally:
            if not self._callback_closed:
                self._callback_closed = True
                os.close(self.callback_tls_socket_fd)
                os.close(self.callback_tls_private_key_fd)

    async def __aenter__(self) -> "OpenF2Composition":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        await self.close()

class BuildResult(_ExactModel):
    composition_ref_path: str
    composition_manifest_path: str
    inventory_path: str
    request_template_path: str
    required_target_inputs: tuple[str, ...]


def _read_pinned(source: SourceArtifact, *, canonical_json: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source.path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise F2CompositionError(f"artifact is not a regular file: {source.path}")
        if before.st_size <= 0 or before.st_size > 64 * 1024 * 1024:
            raise F2CompositionError(f"artifact size is outside the production bound: {source.path}")
        buffer = bytearray(before.st_size)
        view = memoryview(buffer)
        offset = 0
        while offset < before.st_size:
            count = os.readv(fd, (view[offset:],))
            if count <= 0:
                raise F2CompositionError(f"artifact was truncated while reading: {source.path}")
            offset += count
        if os.read(fd, 1):
            raise F2CompositionError(f"artifact grew while reading: {source.path}")
        payload = bytes(buffer)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise F2CompositionError(f"artifact changed while reading: {source.path}")
    if sha256_bytes(payload) != source.sha256:
        raise F2CompositionError(f"artifact digest mismatch: {source.path}")
    if canonical_json:
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise F2CompositionError(f"invalid JSON artifact: {source.path}") from exc
        if canonical_json_bytes(value) != payload:
            raise F2CompositionError(f"artifact is not canonical JSON: {source.path}")
    return payload

def _verify_pinned_file(source: SourceArtifact) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source.path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise F2CompositionError(f"authority is not a nonempty regular file: {source.path}")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            total += len(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or total != before.st_size:
        raise F2CompositionError(f"authority changed while hashing: {source.path}")
    if _DIGEST_PREFIX + digest.hexdigest() != source.sha256:
        raise F2CompositionError(f"artifact digest mismatch: {source.path}")

def _verify_executable_observation(value: ExecutableObservationInput) -> None:
    _verify_pinned_file(SourceArtifact(
        path=value.path,
        sha256=value.sha256,
        media_type="application/octet-stream",
    ))
    current = os.stat(value.path, follow_symlinks=False)
    if (
        current.st_dev,
        current.st_ino,
        current.st_ctime_ns,
        current.st_size,
        stat.S_IMODE(current.st_mode),
        current.st_uid,
    ) != (
        value.device,
        value.inode,
        int(value.ctime_ns),
        value.size_bytes,
        value.mode,
        value.owner_uid,
    ):
        raise F2CompositionError("wrapper executable runtime observation mismatch")


def _write_exclusive(path: Path, payload: bytes, mode: int = 0o444) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), mode)
    try:
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short bundle write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _artifact_ref(path: Path, payload: bytes, media_type: str) -> ArtifactFileRefV1:
    return ArtifactFileRefV1(path=str(path.resolve()), sha256=sha256_bytes(payload), size_bytes=len(payload), media_type=media_type)


def _copy_artifact(directory: Path, name: str, source: SourceArtifact, *, canonical_json: bool = True) -> tuple[ArtifactFileRefV1, bytes]:
    payload = _read_pinned(source, canonical_json=canonical_json)
    path = directory / name
    _write_exclusive(path, payload)
    return _artifact_ref(path, payload, source.media_type), payload


def _measure_store(name: str, path: str) -> DirectoryAuthorityRefV1:
    current = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o700:
        raise F2CompositionError(f"store {name!r} must be a 0700 directory")
    return DirectoryAuthorityRefV1(
        authority_id=f"f2-{name}", path=path, device=current.st_dev, inode=current.st_ino,
        owner_uid=current.st_uid, mode="0700",
    )


def _validate_secret_files(secrets: SecretValidationInput) -> None:
    for handle_id, value in secrets.files.items():
        path = Path(value)
        if not path.is_absolute() or os.path.normpath(value) != value:
            raise F2CompositionError(f"secret handle {handle_id!r} path is not absolute and normalized")
        current = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o400:
            raise F2CompositionError(f"secret handle {handle_id!r} must be a 0400 regular file")


def _validate_installed(installed: InstalledV1, receipt: c.AdmissionReceipt) -> None:
    daemon = installed.private_docker_daemon
    if daemon is None:
        raise F2CompositionError("F2 requires explicit private Docker daemon authority")
    pinned = (daemon.dockerd, daemon.docker, daemon.runc, daemon.containerd, *(item.archive for item in daemon.images))
    for item in pinned:
        source = SourceArtifact(path=item.path, sha256=item.digest, media_type="application/octet-stream")
        _verify_pinned_file(source)
        current = os.stat(item.path, follow_symlinks=False)
        if current.st_uid != item.owner_uid or stat.S_IMODE(current.st_mode) != item.mode:
            raise F2CompositionError(f"private daemon file authority mismatch: {item.path}")
    for runtime in installed.runtimes:
        _verify_pinned_file(SourceArtifact(
            path=runtime.executable_path,
            sha256=runtime.measured_binary_digest,
            media_type="application/octet-stream",
        ))
        if runtime.oci_runtime_binary_path is not None:
            _verify_pinned_file(SourceArtifact(
                path=runtime.oci_runtime_binary_path,
                sha256=runtime.oci_runtime_binary_digest,
                media_type="application/octet-stream",
            ))
    for policy in installed.security_policies:
        if sha256_bytes(policy.seccomp_bytes) != policy.seccomp_digest:
            raise F2CompositionError("security policy seccomp authority mismatch")
    installed_image_ids = {item.image_digest for item in installed.images}
    offline_image_ids = {item.source_image_digest for item in daemon.images}
    if installed_image_ids != offline_image_ids or len(daemon.images) != len(installed.images):
        raise F2CompositionError("installed and offline image authorities differ")
    primary = receipt.effective_capabilities.sandbox
    if primary.runtime_class != c.RuntimeClass.HARDENED_DOCKER:
        raise F2CompositionError("primary sandbox must use hardened Docker")
    verifiers = installed.verifiers
    if len(verifiers) != 1 or verifiers[0].runtime_class != c.RuntimeClass.HARDENED_DOCKER:
        raise F2CompositionError("exactly one independent hardened Docker verifier is required")
    verifier = verifiers[0]
    if verifier.security_policy_digest == primary.security_policy_digest:
        raise F2CompositionError("verifier must have a distinct security policy and container authority")
    verifier_runtime = next((item for item in installed.runtimes if item.runtime_id == verifier.runtime_id), None)
    if verifier_runtime is None or verifier_runtime.runtime_class != c.RuntimeClass.HARDENED_DOCKER:
        raise F2CompositionError("verifier runtime authority is missing")
    network = next((item for item in installed.network_policies if item.policy_digest == primary.network_policy_digest), None)
    if network is None or network.mode != "none" or not network.default_deny or network.egress_route_ids:
        raise F2CompositionError("terminal sandbox network must be credential-free and default-deny")


def _validate_tls(tls: TlsAuthorityInput, graph: PolicyHttpAuthorityGraphV1) -> tuple[bytes, bytes]:
    ca = _read_pinned(tls.ca_certificate)
    leaf = _read_pinned(tls.leaf_certificate)
    if ca.count(b"-----BEGIN CERTIFICATE-----") != 1 or leaf.count(b"-----BEGIN CERTIFICATE-----") != 1:
        raise F2CompositionError("dedicated CA and leaf observations must each contain one PEM certificate")
    try:
        leaf_der = ssl.PEM_cert_to_DER_cert(leaf.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise F2CompositionError("invalid leaf PEM observation") from exc
    if sha256_bytes(leaf_der) != tls.expected_leaf_der_sha256:
        raise F2CompositionError("TLS leaf certificate observation digest mismatch")
    if len(graph.routes) != 1 or graph.routes[0].grant.route_id != tls.route_id:
        raise F2CompositionError("TLS route authority mismatch")
    route = graph.routes[0]
    parsed = urlsplit(f"//{route.authority}")
    if parsed.hostname != tls.target_ip or route.scheme.value != "https":
        raise F2CompositionError("policy callback must use the exact literal-IP HTTPS target")
    if tuple(graph.dns_policies[0].allowed_addresses) != (tls.target_ip,) or tuple(graph.ip_policies[0].allowed_addresses) != (tls.target_ip,):
        raise F2CompositionError("network authority must pin exactly the target IP")
    if graph.ip_policies[0].allow_loopback:
        raise F2CompositionError("network authority may not grant loopback")
    return ca, leaf


def required_target_discovery_inputs() -> tuple[str, ...]:
    return (
        "canonical F1 v3 preflight report path, exact digest, and canonical F1 root (prerequisite only)",
        "approved IBM target-record digest binding host key, ClusterName/controller, partition, account/QOS/reservation, and owner",
        "approved t0230 sealed host runtime root, build report, and cleanup Python identity",
        "absolute paths and sha256 digests for dockerd, docker CLI, containerd, and runc",
        "exact /usr/bin/sudo, /usr/bin/env, and /usr/bin/docker full file observations plus binary discovery report",
        "exact /usr/bin/openssl full file and semantic version observations plus discovery report",
        "mount namespace broker implementation path and sha256 digest",
        "private daemon config/socket/pid/data/exec/mount-stage/containerd root and state paths plus storage driver",
        "internal Docker bridge name/subnet/gateway/internal labels and cleanup authority; live network ID is measured after creation",
        "offline primary and verifier OCI image archive paths, archive digests, source digests, and loaded image IDs",
        "scratch wrapper image build report plus independent operator authorization binding report, tar, loaded ID, and source digest",
        "callback_tls requested-port-zero AF_INET/TCP IP_FREEBIND prebound plan and FD on the private bridge gateway, with exact observed port/socket plan/lease identity",
        "dedicated externally generated CA and leaf public certificate paths/digests, leaf public-key DER digest, and TLSv1.3/TLS_AES_256_GCM_SHA384 authority",
        "distinct 0700 CAS/locator/materialization/workspace/lease/security-profile directories",
        "0400 API bearer, HMAC receipt signer, callback bearer, callback-observation signer, TLS PKCS#8 key, and Ed25519 evidence-receipt signing key handles; all private-key handles require live open FDs",
        "Ed25519 evidence-receipt public-key path/digest/SPKI digest and exact attempt/composition/evidence-policy/OpenSSL authority joins",
    )


def _materialize_f2_production_composition(
    spec: F2ProductionCompositionInput | Mapping[str, Any],
    output_dir: str,
    *,
    tls_callback_runtime_input: TlsCallbackRuntimeInputV1,
) -> BuildResult:
    parsed = spec if isinstance(spec, F2ProductionCompositionInput) else F2ProductionCompositionInput.model_validate(spec, strict=True)
    root = Path(output_dir)
    if not root.is_absolute() or os.path.normpath(output_dir) != output_dir:
        raise F2CompositionError("output directory must be absolute and normalized")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    artifacts = root / "artifacts"
    artifacts.mkdir(mode=0o700)
    try:
        operator_input_bytes = canonical_json_bytes(parsed.model_dump(mode="json"))
        _write_exclusive(artifacts / "operator-input.json", operator_input_bytes)
        _validate_secret_files(parsed.secrets)
        sources = parsed.authority
        openssl = sources.openssl
        wrapper_tools = sources.wrapper_host_executables
        for executable in (
            wrapper_tools.cleanup_python,
            wrapper_tools.sudo,
            wrapper_tools.env,
            wrapper_tools.docker,
        ):
            _verify_executable_observation(executable)
        binary_discovery_ref, _binary_discovery_bytes = _copy_artifact(
            artifacts,
            "binary-discovery-report.json",
            wrapper_tools.binary_discovery_report,
        )
        _verify_pinned_file(SourceArtifact(
            path=openssl.path,
            sha256=openssl.sha256,
            media_type="application/octet-stream",
        ))
        openssl_before = os.stat(openssl.path, follow_symlinks=False)
        expected_openssl = (
            openssl.device, openssl.inode, int(openssl.ctime_ns), openssl.size_bytes,
            openssl.mode, openssl.owner_uid,
        )
        actual_openssl = (
            openssl_before.st_dev, openssl_before.st_ino, openssl_before.st_ctime_ns,
            openssl_before.st_size, stat.S_IMODE(openssl_before.st_mode), openssl_before.st_uid,
        )
        if actual_openssl != expected_openssl:
            raise F2CompositionError("OpenSSL runtime observation mismatch")
        observed_version = subprocess.run(
            [openssl.path, "version"],
            executable=openssl.path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            close_fds=True,
            env={"LC_ALL": "C", "PATH": "/nonexistent"},
        )
        openssl_after = os.stat(openssl.path, follow_symlinks=False)
        if (
            actual_openssl != (
                openssl_after.st_dev, openssl_after.st_ino, openssl_after.st_ctime_ns,
                openssl_after.st_size, stat.S_IMODE(openssl_after.st_mode), openssl_after.st_uid,
            )
            or sha256_bytes(observed_version.stdout) != openssl.version_stdout_sha256
            or observed_version.stdout.decode("ascii").rstrip("\n") != openssl.version
            or observed_version.stderr
        ):
            raise F2CompositionError("OpenSSL semantic observation mismatch")
        openssl_report_ref, _openssl_report_bytes = _copy_artifact(
            artifacts, "openssl-discovery-report.json", openssl.discovery_report
        )
        _copy_artifact(
            artifacts,
            "mount-broker-implementation.py",
            sources.mount_broker_implementation,
            canonical_json=False,
        )
        _copy_artifact(artifacts, "ibm-target-record.json", sources.ibm_target_record)
        host_runtime_ref, _host_runtime_bytes = _copy_artifact(
            artifacts, "host-runtime-build-report.json", sources.host_runtime_build_report
        )
        image_report_ref, image_report_bytes = _copy_artifact(
            artifacts, "wrapper-image-build-report.json", sources.wrapper_image_build_report
        )
        if json.loads(image_report_bytes).get("schema_version") != "bb.rl.f2.scratch-wrapper-image-build.v1":
            raise F2CompositionError("wrapper image build report schema mismatch")
        _authorization_ref, authorization_bytes = _copy_artifact(
            artifacts,
            "wrapper-image-operator-authorization.json",
            sources.wrapper_image_operator_authorization,
        )
        authorization = WrapperImageOperatorAuthorization.model_validate_json(
            authorization_bytes, strict=True
        )
        if authorization.build_report_ref != image_report_ref.sha256:
            raise F2CompositionError("wrapper image operator authorization does not bind the build report")
        daemon = parsed.installed.private_docker_daemon
        if (
            daemon is None
            or daemon.docker.path != wrapper_tools.docker.path
            or daemon.docker.digest != wrapper_tools.docker.sha256
        ):
            raise F2CompositionError("wrapper Docker authority does not bind private daemon")
        if daemon is None or not any(
            image.image_id == authorization.image_id
            and image.source_image_digest == authorization.source_image_digest
            and image.archive.digest == authorization.image_tar_ref
            for image in daemon.images
        ):
            raise F2CompositionError("authorized wrapper image does not bind private daemon authority")
        f1_report_ref, f1_report_bytes = _copy_artifact(
            artifacts, "f1-prerequisite-report.json", sources.f1_prerequisite_report
        )
        f1_report_value = json.loads(f1_report_bytes)
        if f1_report_value.get("schema_version") != "bb.rl.f1.ibm-exact-container-preflight-report.v3":
            raise F2CompositionError("current canonical F1 prerequisite report is required")
        prerequisite_bytes = _f1_prerequisite_bytes(
            f1_report_ref.sha256, sources.f1_prerequisite_canonical_root
        )
        _write_exclusive(artifacts / "prerequisite.json", prerequisite_bytes)
        policy_ref, policy_bytes = _copy_artifact(artifacts, "admission-policy.json", sources.admission_policy)
        registry_ref, registry_bytes = _copy_artifact(artifacts, "registry-snapshot.json", sources.registry_snapshot)
        revocation_ref, revocation_bytes = _copy_artifact(artifacts, "revocations.json", sources.revocation_snapshot)
        capability_ref, capability_bytes = _copy_artifact(artifacts, "policy-capabilities.json", sources.policy_capability_snapshot)
        policy_http_bytes = _read_pinned(sources.policy_http, canonical_json=True)
        policy = c.AdmissionPolicySnapshot.model_validate_json(policy_bytes, strict=True)
        registries = c.RegistrySnapshotSet.model_validate_json(registry_bytes, strict=True)
        revocations = tuple(c.RevocationBinding.model_validate(item, strict=True) for item in json.loads(revocation_bytes))
        capabilities = tuple(c.PolicyCapabilityObservation.model_validate(item, strict=True) for item in json.loads(capability_bytes))
        policy_http = PolicyHttpAuthorityGraphV1.model_validate_json(policy_http_bytes, strict=True)
        ca_bytes, leaf_bytes = _validate_tls(sources.tls, policy_http)
        ca_path = artifacts / "policy-ca.pem"
        _write_exclusive(ca_path, ca_bytes)
        ca_ref = _artifact_ref(ca_path, ca_bytes, "application/x-pem-file")
        leaf_path = artifacts / "policy-leaf.pem"
        _write_exclusive(leaf_path, leaf_bytes)
        leaf_ref = _artifact_ref(leaf_path, leaf_bytes, "application/x-pem-file")
        bundled_tls_callback_runtime = TlsCallbackRuntimeInputV1.model_validate(
            {
                **tls_callback_runtime_input.model_dump(mode="json"),
                "ca_certificate_ref": ca_ref.model_dump(mode="json"),
                "leaf_certificate_ref": leaf_ref.model_dump(mode="json"),
            },
            strict=True,
        )

        config_ref, config_bytes = _copy_artifact(artifacts, "config-bundle.json", sources.config_bundle)
        config_bundle = ConfigBundleManifest.from_json(config_bytes)
        if config_bundle.canonical_bytes() != config_bytes:
            raise F2CompositionError("config bundle is not canonical")
        declared = {entry.logical_path: entry for entry in config_bundle.entries}
        supplied = {item.logical_path: item for item in sources.config_members}
        if set(declared) != set(supplied):
            raise F2CompositionError("config member set mismatch")
        cas = FilesystemCAS(parsed.stores.cas)
        try:
            for logical_path in sorted(declared):
                entry = declared[logical_path]
                member = _read_pinned(supplied[logical_path].source)
                if sha256_bytes(member) != entry.blob_digest or len(member) != entry.size_bytes or supplied[logical_path].source.media_type != entry.media_type:
                    raise F2CompositionError(f"config member authority mismatch: {logical_path}")
                ref = cas.put_bytes(member, artifact_id=entry.artifact_id, media_type=entry.media_type)
                if ref.sha256 != entry.blob_digest or ref.size_bytes != entry.size_bytes:
                    raise F2CompositionError(f"config member CAS publication mismatch: {logical_path}")
        finally:
            cas.close()

        compiled_refs: list[ArtifactFileRefV1] = []
        compiled_models: list[CompiledConfigManifest] = []
        for index, source in enumerate(sources.compiled_manifests):
            ref, payload = _copy_artifact(artifacts, f"compiled-manifest-{index}.json", source)
            compiled_refs.append(ref)
            compiled_models.append(CompiledConfigManifest.from_json(payload))
        receipt_refs: list[ArtifactFileRefV1] = []
        receipts: list[c.AdmissionReceipt] = []
        for index, source in enumerate(sources.admission_receipts):
            ref, payload = _copy_artifact(artifacts, f"admission-receipt-{index}.json", source)
            receipt_refs.append(ref)
            receipts.append(c.AdmissionReceipt.model_validate_json(payload, strict=True))
        admitted_ref, admitted_bytes = _copy_artifact(artifacts, "admitted-set.json", sources.admitted_set)
        admitted = c.AdmittedSetManifest.model_validate_json(admitted_bytes, strict=True)
        selector_ref, selector_bytes = _copy_artifact(artifacts, "direct-selector.json", sources.direct_selector)
        selector = c.DirectSelector.model_validate_json(selector_bytes, strict=True)
        if selector.candidate.overlays or selector.candidate.predicates:
            raise F2CompositionError("C4 direct selector must have no overlays or fallback predicates")
        if selector.admitted_set_root != admitted.canonical_digest():
            raise F2CompositionError("direct selector admitted-set authority mismatch")
        _validate_installed(parsed.installed, receipts[0])

        compiler = compiled_models[0].compiler
        compiler_identity = CompilerIdentityV1(
            compiler_id=compiler.compiler_id, semantic_version=compiler.compiler_version,
            code_digest=compiler.compiler_code_digest, source_schema_digest=compiler.config_schema_digest,
            manifest_schema_digest=compiler.manifest_schema_digest, canonicalizer_id=compiler.canonicalizer_id,
            runtime_abi=compiler.runtime_abi,
        )
        tls = PolicyTlsTrustAuthorityV1(
            schema_version="bb.rl.policy-tls-trust-authority.v1", route_id=sources.tls.route_id,
            server_name=sources.tls.target_ip, ca_bundle_ref=ca_ref,
            expected_leaf_certificate_sha256=sources.tls.expected_leaf_der_sha256,
            minimum_tls_version="TLSv1.3", cipher_suite="TLS_AES_256_GCM_SHA384", dedicated_single_leaf_ca=True,
        )
        authority = AuthorityBundleV1(
            schema_version="bb.rl.harness-authority-bundle.v1", admission_policy=policy,
            registries=registries, revocations=revocations, policy_capabilities=capabilities,
            policy_http=policy_http, tls_trust=(tls,),
            compiled_manifest_refs=tuple(sorted(compiled_refs, key=lambda item: item.sha256)),
            admission_receipt_refs=tuple(sorted(receipt_refs, key=lambda item: item.sha256)),
        )
        authority_path = artifacts / "authority-bundle.json"
        authority_bytes = authority.canonical_bytes()
        _write_exclusive(authority_path, authority_bytes)
        authority_ref = _artifact_ref(authority_path, authority_bytes, "application/json")

        store_values = parsed.stores.model_dump(exclude={"lease_ttl_seconds"})
        stores = StoresV1(**{name: _measure_store(name, store_values[name]) for name in _STORE_NAMES}, lease_ttl_seconds=parsed.stores.lease_ttl_seconds)
        openssl_authority = OpenSslAuthorityV1(
            schema_version="bb.rl.harness-openssl-authority.v1",
            path=openssl.path,
            sha256=openssl.sha256,
            device=openssl.device,
            inode=openssl.inode,
            ctime_ns=openssl.ctime_ns,
            size_bytes=openssl.size_bytes,
            mode=openssl.mode,
            owner_uid=openssl.owner_uid,
            version_stdout_sha256=openssl.version_stdout_sha256,
            version=openssl.version,
            discovery_report_ref=openssl_report_ref,
        )
        manifest = HarnessCompositionManifestV1(
            schema_version="bb.rl.harness-composition.v1", composition_id=parsed.composition_id,
            authority_bundle_ref=authority_ref, config_bundle_ref=config_ref, admitted_set_ref=admitted_ref,
            selector_catalog=SelectorCatalogV1(direct=(selector_ref,), weighted=()),
            control_plane=ControlPlaneV1(
                admission_policy_ref=policy_ref, registry_snapshot_ref=registry_ref,
                revocation_snapshot_ref=revocation_ref, policy_capability_snapshot_ref=capability_ref,
                compiler=compiler_identity,
                receipt_authenticator=ReceiptAuthenticatorV1(key_id=parsed.receipt.key_id, algorithm="hmac-sha256-v1", secret_handle_id=parsed.receipt.secret_handle_id),
            ),
            installed=parsed.installed, stores=stores, server=parsed.server,
            outer_bridge_plan=parsed.outer_bridge_plan,
            prebound_service_socket_plans=parsed.prebound_service_socket_plans,
            openssl_authority=openssl_authority,
            host_runtime_authority=HostRuntimeAuthorityV1(
                schema_version="bb.rl.harness-host-runtime-authority.v1",
                target_run_id=_HOST_RUNTIME_TARGET_RUN_ID,
                root=sources.host_runtime_root,
                build_report_ref=host_runtime_ref,
                python_executable=PinnedFileAuthorityV1(
                    path=wrapper_tools.cleanup_python.path,
                    digest=wrapper_tools.cleanup_python.sha256,
                    owner_uid=wrapper_tools.cleanup_python.owner_uid,
                    mode=wrapper_tools.cleanup_python.mode,
                    executable=True,
                ),
            ),
            tls_callback_runtime_input=bundled_tls_callback_runtime,
            evidence_receipt_signing_authority=parsed.evidence_receipt_signing_authority,
            secret_handles=parsed.secrets.handles, evidence_bindings=parsed.evidence_bindings,
        )
        manifest_path = artifacts / "composition-manifest.json"
        manifest_bytes = manifest.canonical_bytes()
        _write_exclusive(manifest_path, manifest_bytes)
        composition_ref = CompositionRefV1(
            schema_version="bb.rl.harness-composition-ref.v1", manifest_path=str(manifest_path.resolve()),
            manifest_sha256=sha256_bytes(manifest_bytes), manifest_size_bytes=len(manifest_bytes),
            manifest_media_type=COMPOSITION_MEDIA_TYPE,
        )
        composition_ref_path = artifacts / "composition-ref.json"
        _write_exclusive(composition_ref_path, composition_ref.canonical_bytes())
        request_path = root / "request-template.jsonl"
        request_row = {"breadboard_v2": {"task_input": parsed.request_template.task_input, "context": parsed.request_template.context}}
        _write_exclusive(request_path, canonical_json_bytes(request_row) + b"\n")
        inventory_path = root / "inventory.json"
        inventory = {
            "schema_version": "bb.rl.phase5-f2-production-inventory.v1",
            "composition_id": parsed.composition_id,
            "artifacts": [
                {"path": path.name, "sha256": sha256_bytes(path.read_bytes()), "size_bytes": path.stat().st_size}
                for path in sorted(artifacts.iterdir(), key=lambda item: item.name)
            ],
            "request_template_sha256": sha256_bytes(request_path.read_bytes()),
            "tls_callback_runtime_input_digest": bundled_tls_callback_runtime.canonical_digest(),
            "secret_handle_ids": [
                item.handle_id for item in parsed.secrets.handles.records
            ],
        }
        _write_exclusive(inventory_path, canonical_json_bytes(inventory))
        for directory in (artifacts, root):
            dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        return BuildResult(
            composition_ref_path=str(composition_ref_path), composition_manifest_path=str(manifest_path),
            inventory_path=str(inventory_path), request_template_path=str(request_path),
            required_target_inputs=required_target_discovery_inputs(),
        )
    except BaseException:
        # Deliberately retain a failed, exclusive build for forensic inspection; callers must choose a new path.
        raise

def open_f2_production_composition(
    spec: F2ProductionCompositionInput | Mapping[str, Any],
    output_dir: str,
    *,
    prebound_service_socket_fds: Mapping[str, int],
    callback_tls_runtime: TlsCallbackRuntimeInputV1,
    callback_tls_private_key_fd: int,
    live_secret_files: Mapping[str, str],
) -> OpenF2Composition:
    parsed = (
        spec
        if isinstance(spec, F2ProductionCompositionInput)
        else F2ProductionCompositionInput.model_validate(spec, strict=True)
    )
    live_handle_ids = {
        item.handle_id
        for item in parsed.secrets.handles.records
        if item.purpose in {
            "callback_tls_private_key",
            "evidence_receipt_signing_key",
            "callback_observation_signing_key",
        }
    }
    if (
        set(live_secret_files) != live_handle_ids
        or len(set(live_secret_files.values())) != len(live_secret_files)
    ):
        raise F2CompositionError("live secret files do not exactly cover live-only handles")
    for handle_id, path in live_secret_files.items():
        metadata = os.stat(path, follow_symlinks=False)
        if (
            not path.startswith("/")
            or os.path.normpath(path) != path
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
        ):
            raise F2CompositionError(f"live secret {handle_id!r} is unsafe")
    try:
        callback_tls_socket_fd = prebound_service_socket_fds["callback_tls"]
    except KeyError as exc:
        raise F2CompositionError("callback_tls prebound service descriptor is required") from exc
    callback_socket_dup, callback_key_dup, callback_key_identity = (
        _prepare_callback_tls_runtime(
            parsed,
            callback_tls_runtime,
            live_secret_files=live_secret_files,
            socket_fd=callback_tls_socket_fd,
            private_key_fd=callback_tls_private_key_fd,
        )
    )
    try:
        build = _materialize_f2_production_composition(
            parsed,
            output_dir,
            tls_callback_runtime_input=callback_tls_runtime,
        )
        composition = load_production_composition(
            build.composition_ref_path,
            {**parsed.secrets.files, **live_secret_files},
            prebound_service_socket_fds=prebound_service_socket_fds,
        )
    except BaseException:
        os.close(callback_socket_dup)
        os.close(callback_key_dup)
        raise
    session = OpenF2Composition(
        build,
        composition,
        callback_tls_runtime,
        callback_socket_dup,
        callback_key_dup,
        callback_key_identity,
    )
    try:
        session.outer_bridge_lease
    except BaseException:
        asyncio.run(session.close())
        raise
    return session


def build_f2_production_composition(
    spec: F2ProductionCompositionInput | Mapping[str, Any],
    output_dir: str,
    *,
    prebound_service_socket_fds: Mapping[str, int],
    callback_tls_runtime: TlsCallbackRuntimeInputV1,
    callback_tls_private_key_fd: int,
    live_secret_files: Mapping[str, str],
) -> BuildResult:
    session = open_f2_production_composition(
        spec,
        output_dir,
        prebound_service_socket_fds=prebound_service_socket_fds,
        callback_tls_runtime=callback_tls_runtime,
        callback_tls_private_key_fd=callback_tls_private_key_fd,
        live_secret_files=live_secret_files,
    )
    asyncio.run(session.close())
    return session.build
