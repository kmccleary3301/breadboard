from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
from builtins import BaseExceptionGroup, ExceptionGroup
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_bytes
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, Sequence

from breadboard_engine.compilation.bundle import build_dependency_closure
from breadboard_engine.compilation.contracts import (
    ClosureMember,
    CompiledConfig,
    CompiledConfigManifest,
    ConfigBundleManifest,
    DependencyEdge,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.artifacts.cas import ArtifactConflictError, FilesystemCAS

from . import contracts as c
from .api import create_app
from .config_runtime import CompilerSemanticView, ConfigRuntime
from .evidence import (
    EpisodeEvidenceRepository,
    EvidenceRoleBindingV2,
    EvidenceRoleSourceV2,
    FilesystemEpisodeLocatorStore,
    V2EvidenceAuthority,
)
from .materialization import (
    DirectoryStorageBackend,
    FilesystemMaterializationStore,
    SealedSourceManifest,
    SourceManifestEntry,
)
from .mount_namespace_broker import (
    MountNamespaceBroker,
    recover_supervisor_journals,
)
from .policy_http import (
    PolicySecretAuthority,
    PolicyTlsTrustAuthority,
    RouteBoundPolicyHttpResolver,
    RouteNetworkAuthority,
)
from .private_docker_daemon import (
    OfflineImageAuthority,
    PinnedFileAuthority,
    PrivateDockerDaemonAuthority,
)
from .runners.base import RunnerAdapterDescriptor, RunnerAdapterRegistry
from .runners.conductor import CONDUCTOR_ADAPTER_ID, ConductorAdapter
from .runners.terminal import TERMINAL_ADAPTER_ID, TerminalResponsesAdapter
from .sandbox import (
    InstalledImage,
    InstalledRuntime,
    InstalledSandboxAuthoritySet,
    InstalledVerifier,
    SandboxNetworkPolicy,
    SandboxRuntimeManager,
    SandboxSecurityPolicy,
    TrustedProcessBackend,
)
from .sandbox_docker import (
    DockerRuntimeAdapter,
    DockerSandboxBackend,
    InspectDockerMeasurementProvider,
)
from .service import (
    BreadBoardV2EpisodeService,
    PolicyRuntimeClientResolver,
    V2FaultInjectionAuthority,
    V2LifecycleDependencies,
)

class ManagedPolicyRuntimeClientResolver(PolicyRuntimeClientResolver, Protocol):
    def abort_bootstrap(self) -> None: ...

    async def close(self) -> None: ...


PolicyClientResolverFactory = Callable[
    [ManagedPolicyRuntimeClientResolver],
    ManagedPolicyRuntimeClientResolver,
]


COMPOSITION_REF_MEDIA_TYPE = (
    "application/vnd.breadboard.harness-composition+json;version=1"
)
COMPOSITION_MEDIA_TYPE = COMPOSITION_REF_MEDIA_TYPE
COMPOSED_MEDIA_TYPE = "application/vnd.breadboard.harness-composed+json;version=1"
_MAX_AUTHORITY_BYTES = 64 * 1024 * 1024
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _load_json_exact(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON member")
            out[key] = value
        return out

    try:
        value = json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _v: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid canonical JSON") from exc
    if _canonical_bytes(value) != data:
        raise ValueError("authority document is not canonical JSON")
    return value


def _absolute(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith("/")
        or os.path.normpath(value) != value
    ):
        raise ValueError("path must be absolute and normalized")
    return value


def _digest(value: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("digest must be lowercase sha256")
    return value


def _positive_decimal(value: str) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 20
    ):
        raise ValueError(
            "nanosecond identity must be a canonical positive decimal string"
        )
    return value


def _signed_payload(value: BaseModel) -> tuple[dict[str, Any], dict[str, Any]]:
    unsigned = value.model_dump(mode="json")
    unsigned.pop("signature")
    authenticated = dict(unsigned)
    for key in ("signer_key_id", "signature_algorithm", "auth_digest"):
        authenticated.pop(key)
    return authenticated, unsigned


def _verify_model_auth_digest(value: BaseModel) -> bool:
    authenticated, _unsigned = _signed_payload(value)
    return "sha256:" + hashlib.sha256(
        _canonical_bytes(authenticated)
    ).hexdigest() == getattr(value, "auth_digest")


def _verify_model_signature(value: BaseModel, authenticator: Any) -> bool:
    _authenticated, unsigned = _signed_payload(value)
    return (
        authenticator.key_id == getattr(value, "signer_key_id")
        and authenticator.algorithm == getattr(value, "signature_algorithm")
        and authenticator.verify(
            _canonical_bytes(unsigned), bytes.fromhex(getattr(value, "signature"))
        )
    )


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.model_dump(mode="json"))


class ArtifactFileRefV1(_ExactModel):
    path: str
    sha256: str
    size_bytes: int = Field(gt=0, le=_MAX_AUTHORITY_BYTES)
    media_type: str

    _path = field_validator("path")(_absolute)
    _sha = field_validator("sha256")(_digest)


class CompositionRefV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-composition-ref.v1"]
    manifest_path: str
    manifest_sha256: str
    manifest_size_bytes: int = Field(gt=0, le=_MAX_AUTHORITY_BYTES)
    manifest_media_type: Literal[COMPOSITION_MEDIA_TYPE]

    _path = field_validator("manifest_path")(_absolute)
    _sha = field_validator("manifest_sha256")(_digest)


class CompositionRefV2(CompositionRefV1):
    schema_version: Literal["bb.rl.harness-composition-ref.v2"]


class DirectoryAuthorityRefV1(_ExactModel):
    authority_id: str = Field(min_length=1, max_length=256)
    path: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    owner_uid: int = Field(ge=0)
    mode: str = Field(pattern=r"0[0-7]{3}")

    _path = field_validator("path")(_absolute)


class SecretHandleSpecV1(_ExactModel):
    handle_id: str = Field(min_length=1, max_length=256)
    purpose: Literal[
        "api_bearer",
        "receipt_signer",
        "policy_callback",
        "callback_tls_private_key",
        "callback_observation_signing_key",
        "evidence_receipt_signing_key",
    ]
    route_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_routes(self) -> "SecretHandleSpecV1":
        if self.route_ids != tuple(sorted(set(self.route_ids))):
            raise ValueError("route_ids must be sorted and unique")
        if self.purpose == "policy_callback" and not self.route_ids:
            raise ValueError("policy callback handle requires routes")
        if self.purpose != "policy_callback" and self.route_ids:
            raise ValueError("only policy callback handles may bind routes")
        return self


class SecretHandlesV1(_ExactModel):
    records: tuple[SecretHandleSpecV1, ...]

    @model_validator(mode="after")
    def validate_records(self) -> "SecretHandlesV1":
        ids = tuple(item.handle_id for item in self.records)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("secret handles must be sorted and unique")
        purposes = [item.purpose for item in self.records]
        if purposes.count("api_bearer") != 1 or purposes.count("receipt_signer") != 1:
            raise ValueError("exactly one API and receipt handle are required")
        routes = [route for item in self.records for route in item.route_ids]
        if len(routes) != len(set(routes)):
            raise ValueError("a route may have only one credential handle")
        return self


class SelectorCatalogV1(_ExactModel):
    direct: tuple[ArtifactFileRefV1, ...] = ()
    weighted: tuple[ArtifactFileRefV1, ...] = ()

    @model_validator(mode="after")
    def sorted_refs(self) -> "SelectorCatalogV1":
        for refs in (self.direct, self.weighted):
            keys = tuple(ref.sha256 for ref in refs)
            if keys != tuple(sorted(set(keys))):
                raise ValueError("selector refs must be digest-sorted and unique")
        return self


class ReceiptAuthenticatorV1(_ExactModel):
    key_id: str = Field(min_length=1, max_length=256)
    algorithm: Literal["hmac-sha256-v1"]
    secret_handle_id: str


class CompilerIdentityV1(_ExactModel):
    compiler_id: str
    semantic_version: str
    code_digest: str
    source_schema_digest: str
    manifest_schema_digest: str
    canonicalizer_id: str
    runtime_abi: str

    _digests = field_validator(
        "code_digest", "source_schema_digest", "manifest_schema_digest"
    )(_digest)


class ControlPlaneV1(_ExactModel):
    admission_policy_ref: ArtifactFileRefV1
    registry_snapshot_ref: ArtifactFileRefV1
    revocation_snapshot_ref: ArtifactFileRefV1
    policy_capability_snapshot_ref: ArtifactFileRefV1
    compiler: CompilerIdentityV1
    receipt_authenticator: ReceiptAuthenticatorV1


class DNSPolicyDocumentV1(_ExactModel):
    schema_version: Literal["bb.rl.policy-dns-authority.v1"]
    dns_policy_digest: str
    hostname: str = Field(min_length=1, max_length=253)
    allowed_addresses: tuple[str, ...]
    resolution_mode: Literal["pinned"]
    require_all_answers_admitted: Literal[True]
    verify_connected_peer: Literal[True]

    @model_validator(mode="after")
    def bind_document(self) -> "DNSPolicyDocumentV1":
        addresses = tuple(
            str(ipaddress.ip_address(item)) for item in self.allowed_addresses
        )
        if not addresses or addresses != tuple(sorted(set(addresses))):
            raise ValueError(
                "DNS addresses must be canonical, sorted, unique, and nonempty"
            )
        if self.dns_policy_digest != _projection_digest(
            self.model_dump(mode="json", exclude={"dns_policy_digest"})
        ):
            raise ValueError("DNS policy digest mismatch")
        return self


class IPPolicyDocumentV1(_ExactModel):
    schema_version: Literal["bb.rl.policy-ip-authority.v1"]
    ip_policy_digest: str
    allowed_addresses: tuple[str, ...]
    allow_loopback: bool
    allow_private: bool
    allow_link_local: bool
    allow_multicast: bool
    allow_unspecified: bool

    @model_validator(mode="after")
    def bind_document(self) -> "IPPolicyDocumentV1":
        addresses = tuple(
            str(ipaddress.ip_address(item)) for item in self.allowed_addresses
        )
        if not addresses or addresses != tuple(sorted(set(addresses))):
            raise ValueError(
                "IP addresses must be canonical, sorted, unique, and nonempty"
            )
        if self.ip_policy_digest != _projection_digest(
            self.model_dump(mode="json", exclude={"ip_policy_digest"})
        ):
            raise ValueError("IP policy digest mismatch")
        return self


class PolicyHttpSchemaAuthorityV1(_ExactModel):
    schema_version: Literal["bb.rl.policy-http-schema-authority.v1"]
    protocol_abi: str
    request_schema: dict[str, Any]
    request_schema_digest: str
    response_schema: dict[str, Any]
    response_schema_digest: str

    @model_validator(mode="after")
    def bind_schemas(self) -> "PolicyHttpSchemaAuthorityV1":
        if self.request_schema_digest != _projection_digest(
            self.request_schema
        ) or self.response_schema_digest != _projection_digest(self.response_schema):
            raise ValueError("policy HTTP schema digest mismatch")
        return self


class PolicySecretRouteBindingV1(_ExactModel):
    schema_version: Literal["bb.rl.policy-secret-route-binding.v1"]
    handle_id: str = Field(min_length=1, max_length=256)
    handle_version_digest: str
    scope_digest: str
    route_ids: tuple[str, ...]
    _digests = field_validator("handle_version_digest", "scope_digest")(_digest)

    @model_validator(mode="after")
    def canonical_routes(self) -> "PolicySecretRouteBindingV1":
        if not self.route_ids or self.route_ids != tuple(sorted(set(self.route_ids))):
            raise ValueError("secret route IDs must be sorted, unique, and nonempty")
        return self


class PolicyHttpAuthorityGraphV1(_ExactModel):
    registry_revision_digest: str
    routes: tuple[c.RouteRegistryRecord, ...]
    observations: tuple[c.PolicyCapabilityObservation, ...]
    dns_policies: tuple[DNSPolicyDocumentV1, ...]
    ip_policies: tuple[IPPolicyDocumentV1, ...]
    schema_authority: PolicyHttpSchemaAuthorityV1
    secret_bindings: tuple[PolicySecretRouteBindingV1, ...]
    _revision = field_validator("registry_revision_digest")(_digest)

    @model_validator(mode="after")
    def close_graph(self) -> "PolicyHttpAuthorityGraphV1":
        dns = {item.dns_policy_digest: item for item in self.dns_policies}
        ips = {item.ip_policy_digest: item for item in self.ip_policies}
        if len(dns) != len(self.dns_policies) or len(ips) != len(self.ip_policies):
            raise ValueError("duplicate DNS or IP policy authority")
        bindings: dict[str, PolicySecretRouteBindingV1] = {}
        for binding in self.secret_bindings:
            for route_id in binding.route_ids:
                if route_id in bindings:
                    raise ValueError("route has multiple secret bindings")
                bindings[route_id] = binding
        route_ids = {route.grant.route_id for route in self.routes}
        if set(bindings) != route_ids:
            raise ValueError(
                "policy secret bindings must name exactly the installed routes"
            )
        for route in self.routes:
            if route.dns_policy_digest not in dns or route.ip_policy_digest not in ips:
                raise ValueError("route policy authority is missing")
            if (
                dns[route.dns_policy_digest].allowed_addresses
                != ips[route.ip_policy_digest].allowed_addresses
            ):
                raise ValueError("DNS and IP address authorities differ")
            if (
                route.request_schema_digest
                != self.schema_authority.request_schema_digest
                or route.response_schema_digest
                != self.schema_authority.response_schema_digest
                or route.grant.protocol_abi != self.schema_authority.protocol_abi
            ):
                raise ValueError("route policy HTTP ABI authority mismatch")
            binding = bindings.get(route.grant.route_id)
            if binding is None or binding.handle_id != route.grant.credential_handle_id:
                raise ValueError("route secret authority is missing or mismatched")
        return self


class PolicyTlsTrustAuthorityV1(_ExactModel):
    schema_version: Literal["bb.rl.policy-tls-trust-authority.v1"]
    route_id: str = Field(min_length=1, max_length=256)
    server_name: str = Field(min_length=1, max_length=253)
    ca_bundle_ref: ArtifactFileRefV1
    expected_leaf_certificate_sha256: str
    minimum_tls_version: Literal["TLSv1.3"]
    cipher_suite: Literal["TLS_AES_256_GCM_SHA384"]
    dedicated_single_leaf_ca: Literal[True]
    _leaf = field_validator("expected_leaf_certificate_sha256")(_digest)

    @model_validator(mode="after")
    def exact_tls_authority(self) -> "PolicyTlsTrustAuthorityV1":
        if self.ca_bundle_ref.media_type != "application/x-pem-file":
            raise ValueError("TLS CA authority must be a PEM artifact")
        try:
            ipaddress.ip_address(self.server_name)
        except ValueError:
            if (
                self.server_name.lower() != self.server_name
                or self.server_name.endswith(".")
            ):
                raise ValueError("TLS server name must be canonical")
        return self


class AuthorityBundleV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-authority-bundle.v1"]
    admission_policy: c.AdmissionPolicySnapshot
    registries: c.RegistrySnapshotSet
    revocations: tuple[c.RevocationBinding, ...]
    policy_capabilities: tuple[c.PolicyCapabilityObservation, ...]
    policy_http: PolicyHttpAuthorityGraphV1
    tls_trust: tuple[PolicyTlsTrustAuthorityV1, ...]
    compiled_manifest_refs: tuple[ArtifactFileRefV1, ...]
    admission_receipt_refs: tuple[ArtifactFileRefV1, ...]

    @model_validator(mode="after")
    def exact_members(self) -> "AuthorityBundleV1":
        if (
            self.policy_http.registry_revision_digest
            != self.registries.digests.route_registry_digest
        ):
            raise ValueError("authority bundle registry revision mismatch")
        route_ids = tuple(route.grant.route_id for route in self.policy_http.routes)
        tls_ids = tuple(item.route_id for item in self.tls_trust)
        route_by_id = {route.grant.route_id: route for route in self.policy_http.routes}
        from urllib.parse import urlsplit

        for trust in self.tls_trust:
            route_host = urlsplit(f"//{route_by_id[trust.route_id].authority}").hostname
            if route_host != trust.server_name:
                raise ValueError("TLS server name does not bind the route authority")
        if route_ids != tuple(sorted(set(route_ids))) or tls_ids != route_ids:
            raise ValueError(
                "every route requires exactly one sorted TLS trust authority"
            )
        if any(route.scheme.value != "https" for route in self.policy_http.routes):
            raise ValueError("policy routes must use HTTPS")
        for refs in (self.compiled_manifest_refs, self.admission_receipt_refs):
            digests = tuple(item.sha256 for item in refs)
            if digests != tuple(sorted(set(digests))):
                raise ValueError(
                    "authority member refs must be digest sorted and unique"
                )
        return self


class StoresV1(_ExactModel):
    cas: DirectoryAuthorityRefV1
    locator: DirectoryAuthorityRefV1
    materialization_cache: DirectoryAuthorityRefV1
    workspace: DirectoryAuthorityRefV1
    lease: DirectoryAuthorityRefV1
    security_profile: DirectoryAuthorityRefV1
    lease_ttl_seconds: int = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def distinct_roots(self) -> "StoresV1":
        roots = (
            self.cas,
            self.locator,
            self.materialization_cache,
            self.workspace,
            self.lease,
            self.security_profile,
        )
        identities = {(item.device, item.inode) for item in roots}
        if len(identities) != len(roots) or len({item.path for item in roots}) != len(
            roots
        ):
            raise ValueError("store directory authorities must be distinct")
        return self


class DockerNetworkLabelV1(_ExactModel):
    key: str = Field(
        min_length=1, max_length=128, pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]*"
    )
    value: str = Field(max_length=256)


class OuterBridgePlanV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-outer-bridge-plan.v1"]
    network_name: str = Field(
        min_length=1, max_length=128, pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]*"
    )
    driver: Literal["bridge"]
    subnet: str
    gateway: str
    internal: Literal[True]
    labels: tuple[DockerNetworkLabelV1, ...]
    cleanup_owner: Literal["f2_outer_orchestrator"]
    cleanup_ref: str = Field(min_length=1, max_length=256)

    @field_validator("labels", mode="before")
    @classmethod
    def parse_label_wire(cls, value: Any) -> Any:
        if type(value) is list:
            return tuple(
                DockerNetworkLabelV1.model_validate(item, strict=True) for item in value
            )
        return value

    @model_validator(mode="after")
    def exact_private_network(self) -> "OuterBridgePlanV1":
        try:
            network = ipaddress.ip_network(self.subnet, strict=True)
            gateway = ipaddress.ip_address(self.gateway)
        except ValueError as exc:
            raise ValueError("internal bridge network is not canonical") from exc
        rfc1918 = tuple(
            ipaddress.ip_network(value)
            for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        )
        ula = ipaddress.ip_network("fc00::/7")
        private_network = (
            network.version == 4
            and any(network.subnet_of(authority) for authority in rfc1918)
        ) or (network.version == 6 and network.subnet_of(ula))
        if (
            str(network) != self.subnet
            or str(gateway) != self.gateway
            or gateway.version != network.version
            or gateway not in network
            or gateway.is_loopback
            or gateway.is_link_local
            or gateway.is_unspecified
            or gateway.is_multicast
            or not private_network
            or gateway == network.network_address
            or (network.version == 4 and gateway == network.broadcast_address)
        ):
            raise ValueError("internal bridge must use an exact RFC1918 or ULA gateway")
        label_keys = tuple(item.key for item in self.labels)
        if label_keys != tuple(sorted(set(label_keys))):
            raise ValueError("internal bridge labels must be sorted and unique")
        if "\x00" in self.cleanup_ref:
            raise ValueError("internal bridge cleanup reference is invalid")
        return self

    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()


class OuterBridgeLeaseV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-outer-bridge-lease.v1"]
    composition_digest: str
    plan_digest: str
    broker_pid: int = Field(gt=0)
    broker_starttime: str = Field(min_length=1)
    broker_mount_namespace: str = Field(min_length=1)
    daemon_instance_id: str = Field(min_length=1)
    daemon_pid: int = Field(gt=0)
    daemon_starttime: str = Field(min_length=1)
    daemon_pid_namespace: str = Field(min_length=1)
    network_id: str = Field(pattern=r"[0-9a-f]{64}")
    network_name: str = Field(
        min_length=1, max_length=128, pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]*"
    )
    inspect_media_type: Literal[
        "application/vnd.breadboard.docker-network-inspect+json;version=1"
    ]
    inspect_bytes_base64: str
    inspect_sha256: str
    created_at: str
    lease_expires_at: str
    lease_id: str
    signer_key_id: str = Field(min_length=1)
    signature_algorithm: Literal["hmac-sha256-v1"]
    auth_digest: str
    signature: str = Field(pattern=r"[0-9a-f]{64}")

    _digests = field_validator(
        "composition_digest",
        "plan_digest",
        "inspect_sha256",
        "lease_id",
        "auth_digest",
    )(_digest)

    @model_validator(mode="after")
    def exact_lease(self) -> "OuterBridgeLeaseV1":
        try:
            inspect_bytes = base64.b64decode(self.inspect_bytes_base64, validate=True)
            created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
            expires = datetime.fromisoformat(
                self.lease_expires_at.replace("Z", "+00:00")
            )
        except (ValueError, UnicodeError) as exc:
            raise ValueError("outer bridge lease observation is malformed") from exc
        if (
            not inspect_bytes
            or _canonical_bytes(_load_json_exact(inspect_bytes)) != inspect_bytes
            or "sha256:" + hashlib.sha256(inspect_bytes).hexdigest()
            != self.inspect_sha256
            or created.tzinfo is None
            or expires.tzinfo is None
            or expires <= created
        ):
            raise ValueError("outer bridge lease observation is not exact")
        if not _verify_model_auth_digest(self):
            raise ValueError("outer bridge lease authentication digest is not exact")
        return self

    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def verify_authenticator(self, authenticator: Any) -> bool:
        return _verify_model_signature(self, authenticator)


class PreboundServiceSocketPlanV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-prebound-service-socket-plan.v1"]
    role: str = Field(min_length=1, max_length=128)
    gateway: str
    observed_port: int = Field(ge=1, le=65535)
    family: Literal["AF_INET"]
    socket_type: Literal["SOCK_STREAM"]
    protocol: Literal["IPPROTO_TCP"]
    socket_device: int = Field(ge=0)
    socket_inode: int = Field(gt=0)
    socket_mode: int = Field(gt=0)
    socket_owner_uid: int = Field(ge=0)
    getsockname_host: str
    getsockname_port: int = Field(ge=1, le=65535)
    ip_freebind: Literal[True]
    socket_plan_id: str

    _id = field_validator("socket_plan_id")(_digest)

    @model_validator(mode="after")
    def exact_socket(self) -> "PreboundServiceSocketPlanV1":
        if (
            self.gateway != self.getsockname_host
            or self.observed_port != self.getsockname_port
            or not stat.S_ISSOCK(self.socket_mode)
        ):
            raise ValueError("prebound service socket plan is not exact")
        unsigned = self.model_dump(mode="json")
        unsigned.pop("socket_plan_id")
        expected_id = "sha256:" + hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
        if self.socket_plan_id != expected_id:
            raise ValueError("prebound service socket plan identity is not exact")
        return self

    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()


class PreboundServiceSocketLeaseV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-prebound-service-socket-lease.v1"]
    role: str = Field(min_length=1, max_length=128)
    socket_plan_digest: str
    socket_plan_id: str
    bridge_lease_id: str
    bridge_lease_digest: str
    pre_create_observation_bytes_base64: str
    pre_create_observation_digest: str
    post_create_observation_bytes_base64: str
    post_create_observation_digest: str
    server_handoff_receipt: str

    _digests = field_validator(
        "socket_plan_digest",
        "socket_plan_id",
        "bridge_lease_id",
        "bridge_lease_digest",
        "pre_create_observation_digest",
        "post_create_observation_digest",
        "server_handoff_receipt",
    )(_digest)

    @model_validator(mode="after")
    def exact_observations(self) -> "PreboundServiceSocketLeaseV1":
        try:
            before = base64.b64decode(
                self.pre_create_observation_bytes_base64, validate=True
            )
            after = base64.b64decode(
                self.post_create_observation_bytes_base64, validate=True
            )
        except ValueError as exc:
            raise ValueError("prebound socket lease bytes are malformed") from exc
        if (
            not before
            or not after
            or _canonical_bytes(_load_json_exact(before)) != before
            or _canonical_bytes(_load_json_exact(after)) != after
            or "sha256:" + hashlib.sha256(before).hexdigest()
            != self.pre_create_observation_digest
            or "sha256:" + hashlib.sha256(after).hexdigest()
            != self.post_create_observation_digest
            or before != after
        ):
            raise ValueError("prebound socket lease observations are not exact")
        return self


class OuterBridgeCleanupReceiptV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-outer-bridge-cleanup-receipt.v1"]
    lease_id: str
    lease_digest: str
    network_id: str = Field(pattern=r"[0-9a-f]{64}")
    network_name: str
    delete_returncode: int
    delete_stdout_base64: str
    delete_stderr_base64: str
    delete_result_sha256: str
    post_list_bytes_base64: str
    post_list_sha256: str
    post_inspect_stdout_base64: str
    post_inspect_stderr_base64: str
    post_inspect_sha256: str
    id_absent: Literal[True]
    name_absent: Literal[True]
    broker_pid: int = Field(gt=0)
    broker_starttime: str = Field(min_length=1)
    signer_key_id: str = Field(min_length=1)
    signature_algorithm: Literal["hmac-sha256-v1"]
    auth_digest: str
    signature: str = Field(pattern=r"[0-9a-f]{64}")

    _digests = field_validator(
        "lease_id",
        "lease_digest",
        "delete_result_sha256",
        "post_list_sha256",
        "post_inspect_sha256",
        "auth_digest",
    )(_digest)

    @model_validator(mode="after")
    def exact_receipt(self) -> "OuterBridgeCleanupReceiptV1":
        try:
            delete_stdout = base64.b64decode(self.delete_stdout_base64, validate=True)
            delete_stderr = base64.b64decode(self.delete_stderr_base64, validate=True)
            post_list = base64.b64decode(self.post_list_bytes_base64, validate=True)
            post_inspect_stdout = base64.b64decode(
                self.post_inspect_stdout_base64, validate=True
            )
            post_inspect_stderr = base64.b64decode(
                self.post_inspect_stderr_base64, validate=True
            )
        except ValueError as exc:
            raise ValueError("outer bridge cleanup bytes are malformed") from exc
        delete_bytes = _canonical_bytes(
            {
                "returncode": self.delete_returncode,
                "stdout_base64": self.delete_stdout_base64,
                "stderr_base64": self.delete_stderr_base64,
            }
        )
        inspect_bytes = _canonical_bytes(
            {
                "stdout_base64": self.post_inspect_stdout_base64,
                "stderr_base64": self.post_inspect_stderr_base64,
            }
        )
        if (
            "sha256:" + hashlib.sha256(delete_bytes).hexdigest()
            != self.delete_result_sha256
            or "sha256:" + hashlib.sha256(post_list).hexdigest()
            != self.post_list_sha256
            or "sha256:" + hashlib.sha256(inspect_bytes).hexdigest()
            != self.post_inspect_sha256
            or not post_inspect_stdout
            or not post_inspect_stderr
            or not _verify_model_auth_digest(self)
        ):
            raise ValueError("outer bridge cleanup receipt is not exact")
        _ = delete_stdout, delete_stderr
        return self

    def verify_authenticator(self, authenticator: Any) -> bool:
        return _verify_model_signature(self, authenticator)


class ServerV1(_ExactModel):
    mode: Literal["loopback", "internal_bridge"] = "loopback"
    host: str
    port: int = Field(ge=1, le=65535)
    allow_unauthenticated_loopback: Literal[False]
    proxy_headers: Literal[False]
    request_timeout_seconds: float = Field(gt=0, le=600)

    @model_validator(mode="after")
    def exact_bind_authority(self) -> "ServerV1":
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise ValueError("server host must be one canonical IP address") from exc
        if str(address) != self.host:
            raise ValueError("server host must be one canonical IP address")
        if self.mode == "loopback":
            if self.host not in {"127.0.0.1", "::1"}:
                raise ValueError("loopback server authority is not exact")
        elif (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or not address.is_private
        ):
            raise ValueError(
                "internal bridge server host must be private and nonloopback"
            )
        return self


class PinnedFileAuthorityV1(_ExactModel):
    path: str
    digest: str
    owner_uid: int = Field(ge=0)
    mode: int = Field(ge=0, le=0o7777)
    executable: bool

    _path = field_validator("path")(_absolute)
    _digest = field_validator("digest")(_digest)

    @model_validator(mode="after")
    def mode_matches_executable(self) -> "PinnedFileAuthorityV1":
        if self.executable != bool(self.mode & 0o111):
            raise ValueError("file executable authority contradicts its mode")
        return self


class OfflineImageAuthorityV1(_ExactModel):
    archive: PinnedFileAuthorityV1
    image_id: str

    source_image_digest: str
    _digests = field_validator("image_id", "source_image_digest")(_digest)


class OpenSslAuthorityV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-openssl-authority.v1"]
    path: Literal["/usr/bin/openssl"]
    sha256: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    ctime_ns: str
    size_bytes: int = Field(gt=0)
    mode: int = Field(ge=0, le=0o7777)
    owner_uid: Literal[0]
    version_stdout_sha256: str
    version: str = Field(min_length=1, max_length=256, pattern=r"OpenSSL [^\r\n]+")
    discovery_report_ref: ArtifactFileRefV1

    _digests = field_validator("sha256", "version_stdout_sha256")(_digest)
    _ctime_ns = field_validator("ctime_ns")(_positive_decimal)

    @model_validator(mode="after")
    def exact_openssl_authority(self) -> "OpenSslAuthorityV1":
        if (
            self.mode & 0o111 == 0
            or self.mode & 0o022
            or self.discovery_report_ref.media_type
            != "application/vnd.breadboard.rl.openssl-discovery+json;version=1"
        ):
            raise ValueError("OpenSSL authority is not an exact sealed executable")
        return self


class TlsCallbackPolicyV1(_ExactModel):
    minimum_tls_version: Literal["TLSv1.3"]
    maximum_tls_version: Literal["TLSv1.3"]
    server_certificate_verification_required: Literal[True]
    hostname_verification_required: Literal[True]
    bearer_authentication_required: Literal[True]
    mutual_tls_required: Literal[False]


class TlsCallbackRuntimeInputV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-tls-callback-runtime-input.v1"]
    route_id: str = Field(min_length=1, max_length=256)
    host: str
    observed_port: int = Field(ge=1, le=65535)
    socket_role: Literal["callback_tls"]
    socket_plan_id: str
    ca_certificate_ref: ArtifactFileRefV1
    leaf_certificate_ref: ArtifactFileRefV1
    ca_certificate_sha256: str
    leaf_certificate_sha256: str
    leaf_public_key_sha256: str
    private_key_secret_handle_id: str = Field(min_length=1, max_length=256)
    tls_policy: TlsCallbackPolicyV1

    _digests = field_validator(
        "socket_plan_id",
        "ca_certificate_sha256",
        "leaf_certificate_sha256",
        "leaf_public_key_sha256",
    )(_digest)

    @model_validator(mode="after")
    def exact_runtime_input(self) -> "TlsCallbackRuntimeInputV1":
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise ValueError("TLS callback host is not one canonical IP") from exc
        if (
            address.version != 4
            or str(address) != self.host
            or not address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or self.ca_certificate_ref.sha256 != self.ca_certificate_sha256
            or self.leaf_certificate_ref.sha256 != self.leaf_certificate_sha256
            or self.ca_certificate_ref.media_type != "application/x-pem-file"
            or self.leaf_certificate_ref.media_type != "application/x-pem-file"
        ):
            raise ValueError("TLS callback runtime input is not exact")
        return self

    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()


class EvidenceReceiptSigningAuthorityV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-evidence-receipt-signing-authority.v1"]
    attempt_id: str = Field(min_length=1, max_length=256)
    composition_digest: str
    evidence_policy_digest: str
    algorithm: Literal["Ed25519"]
    public_key_ref: ArtifactFileRefV1
    public_key_sha256: str
    public_key_spki_sha256: str
    private_key_secret_handle_id: str = Field(min_length=1, max_length=256)
    openssl_authority_digest: str

    _digests = field_validator(
        "composition_digest",
        "evidence_policy_digest",
        "public_key_sha256",
        "public_key_spki_sha256",
        "openssl_authority_digest",
    )(_digest)

    @model_validator(mode="after")
    def exact_public_authority(self) -> "EvidenceReceiptSigningAuthorityV1":
        if (
            self.public_key_ref.sha256 != self.public_key_sha256
            or self.public_key_ref.media_type != "application/x-pem-file"
        ):
            raise ValueError("Ed25519 receipt public-key authority is not exact")
        return self

    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()


class CallbackJournalVerificationReceiptV1(_ExactModel):
    schema_version: Literal["bb.rl.callback-journal-verification-receipt.v1"]
    attempt_id: str = Field(min_length=1, max_length=256)
    composition_digest: str
    route_id: str = Field(min_length=1, max_length=256)
    journal_ref: ArtifactFileRefV1
    snapshot_ref: ArtifactFileRefV1
    head_mac: str = Field(pattern=r"[0-9a-f]{64}")
    event_count: int = Field(ge=0)
    chain_verified: Literal[True]
    snapshot_verified: Literal[True]
    evidence_policy_digest: str
    signer_public_key_spki_sha256: str
    signer_authority_digest: str

    _digests = field_validator(
        "composition_digest",
        "evidence_policy_digest",
        "signer_public_key_spki_sha256",
        "signer_authority_digest",
    )(_digest)

    def canonical_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()


class EvidenceReceiptSignatureV1(_ExactModel):
    schema_version: Literal["bb.rl.evidence-receipt-signature.v1"]
    algorithm: Literal["Ed25519"]
    signer_authority_digest: str
    receipt_digest: str
    signature_base64: str

    _digests = field_validator("signer_authority_digest", "receipt_digest")(_digest)

    @model_validator(mode="after")
    def exact_signature(self) -> "EvidenceReceiptSignatureV1":
        try:
            signature = base64.b64decode(self.signature_base64, validate=True)
        except ValueError as exc:
            raise ValueError("Ed25519 receipt signature is malformed") from exc
        if len(signature) != 64:
            raise ValueError("Ed25519 receipt signature is not exact")
        return self


@dataclass(frozen=True, slots=True)
class EvidenceReceiptSigningHandoff:
    authority: EvidenceReceiptSigningAuthorityV1
    private_key_fd: int

    def validate_live(self) -> None:
        if type(self.private_key_fd) is not int or self.private_key_fd < 0:
            raise ValueError("evidence receipt private-key descriptor is invalid")
        metadata = os.fstat(self.private_key_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or os.get_inheritable(self.private_key_fd)
        ):
            raise ValueError("evidence receipt private-key descriptor is not exact")


class HostRuntimeAuthorityV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-host-runtime-authority.v1"]
    target_run_id: str = Field(
        min_length=1, max_length=128, pattern=r"[0-9]{8}T[0-9]{6}Z-slurm-[0-9]+"
    )
    root: str
    build_report_ref: ArtifactFileRefV1
    python_executable: PinnedFileAuthorityV1

    _root = field_validator("root")(_absolute)

    @model_validator(mode="after")
    def exact_build_report(self) -> "HostRuntimeAuthorityV1":
        if (
            not self.python_executable.executable
            or not self.python_executable.path.startswith(self.root + "/")
        ):
            raise ValueError(
                "host runtime Python authority escapes sealed runtime root"
            )
        if (
            self.build_report_ref.media_type
            != "application/vnd.breadboard.rl.f2-host-runtime-build-report+json;version=1"
        ):
            raise ValueError("host runtime build report media type is not exact")
        return self


class PrivateDockerDaemonAuthorityV1(_ExactModel):
    daemon_instance_id: str = Field(min_length=1, max_length=256)
    dockerd: PinnedFileAuthorityV1
    docker: PinnedFileAuthorityV1
    runc: PinnedFileAuthorityV1
    containerd: PinnedFileAuthorityV1
    config_path: str
    socket_path: str
    pid_file: str
    data_root: str
    exec_root: str
    mount_stage_root: str
    containerd_socket_path: str
    containerd_root: str
    containerd_state: str
    log_root: str
    log_limit_bytes: int = Field(ge=4096, le=1024 * 1024)
    storage_driver: Literal["vfs", "overlay2"]
    runtime_name: Literal["breadboard-runc"]
    images: tuple[OfflineImageAuthorityV1, ...] = ()

    _paths = field_validator(
        "config_path",
        "socket_path",
        "pid_file",
        "data_root",
        "exec_root",
        "mount_stage_root",
        "containerd_socket_path",
        "containerd_root",
        "containerd_state",
        "log_root",
    )(_absolute)

    @model_validator(mode="after")
    def exact_private_authority(self) -> "PrivateDockerDaemonAuthorityV1":
        paths = (
            self.config_path,
            self.socket_path,
            self.pid_file,
            self.data_root,
            self.exec_root,
            self.mount_stage_root,
            self.containerd_socket_path,
            self.containerd_ttrpc_socket_path,
            self.containerd_root,
            self.containerd_state,
            self.log_root,
        )
        if len(set(paths)) != len(paths):
            raise ValueError("private Docker daemon paths must be distinct")
        if len({os.path.dirname(path) for path in paths}) != 1:
            raise ValueError(
                "private Docker daemon outputs must be exact children of one authority root"
            )
        if not all(
            item.executable
            for item in (self.dockerd, self.docker, self.runc, self.containerd)
        ):
            raise ValueError("private Docker executable authorities must be executable")
        if any(item.archive.executable for item in self.images):
            raise ValueError("offline image archives cannot be executable")
        image_ids = tuple(item.image_id for item in self.images)
        if image_ids != tuple(sorted(set(image_ids))):
            raise ValueError(
                "private Docker image authorities must be sorted and unique"
            )
        return self

    @property
    def containerd_ttrpc_socket_path(self) -> str:
        return self.containerd_socket_path + ".ttrpc"

    @property
    def daemon_root(self) -> str:
        return os.path.dirname(self.config_path)


class InstalledV1(_ExactModel):
    runner_adapters: tuple[RunnerAdapterDescriptor, ...]
    runtimes: tuple[InstalledRuntime, ...]
    images: tuple[InstalledImage, ...]
    security_policies: tuple[SandboxSecurityPolicy, ...]
    network_policies: tuple[SandboxNetworkPolicy, ...]
    verifiers: tuple[InstalledVerifier, ...]
    private_docker_daemon: PrivateDockerDaemonAuthorityV1 | None = None

    @model_validator(mode="after")
    def exact_authorities(self) -> "InstalledV1":
        InstalledSandboxAuthoritySet(
            runtimes=self.runtimes,
            images=self.images,
            security_policies=self.security_policies,
            network_policies=self.network_policies,
            verifiers=self.verifiers,
        )
        adapter_keys = tuple(
            (item.adapter_id, item.runtime_abi) for item in self.runner_adapters
        )
        if adapter_keys != tuple(sorted(adapter_keys)) or len(adapter_keys) != len(
            set(adapter_keys)
        ):
            raise ValueError("runner adapter authorities must be sorted and unique")
        hardened = tuple(
            runtime
            for runtime in self.runtimes
            if runtime.runtime_class
            in {
                c.RuntimeClass.HARDENED_DOCKER,
                c.RuntimeClass.HARDENED_GVISOR,
            }
        )
        if bool(hardened) != (self.private_docker_daemon is not None):
            raise ValueError(
                "hardened Docker requires exactly one explicit private daemon authority"
            )
        if hardened and self.private_docker_daemon is not None:
            daemon = self.private_docker_daemon
            expected_runtime_authority = (
                daemon.docker.path,
                daemon.docker.digest,
                daemon.runtime_name,
                daemon.runc.path,
                daemon.runc.digest,
            )
            runtime_authorities = {
                (
                    runtime.executable_path,
                    runtime.measured_binary_digest,
                    runtime.oci_runtime_name,
                    runtime.oci_runtime_binary_path,
                    runtime.oci_runtime_binary_digest,
                )
                for runtime in hardened
            }
            runtime_mechanics = {
                (
                    runtime.runtime_class,
                    runtime.driver_implementation_digest,
                    runtime.supported_platform_versions,
                    runtime.fixed_environment,
                    runtime.idle_argv,
                    runtime.runsc_binary_path,
                    runtime.runsc_binary_digest,
                )
                for runtime in hardened
            }
            if (
                runtime_authorities != {expected_runtime_authority}
                or len(runtime_mechanics) != 1
            ):
                raise ValueError(
                    "hardened runtimes must share the exact private daemon authority"
                )
        if any(
            runtime.runtime_class
            not in {
                c.RuntimeClass.TRUSTED_PROCESS,
                c.RuntimeClass.HARDENED_DOCKER,
                c.RuntimeClass.HARDENED_GVISOR,
            }
            for runtime in self.runtimes
        ):
            raise ValueError("unsupported_installed_runtime")
        return self


class HarnessCompositionManifestV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-composition.v1"]
    composition_id: str = Field(min_length=1, max_length=256)
    authority_bundle_ref: ArtifactFileRefV1
    config_bundle_ref: ArtifactFileRefV1
    admitted_set_ref: ArtifactFileRefV1
    selector_catalog: SelectorCatalogV1
    control_plane: ControlPlaneV1
    installed: InstalledV1
    stores: StoresV1
    server: ServerV1
    outer_bridge_plan: OuterBridgePlanV1 | None = None
    prebound_service_socket_plans: tuple[PreboundServiceSocketPlanV1, ...] = ()
    openssl_authority: OpenSslAuthorityV1 | None = None
    host_runtime_authority: HostRuntimeAuthorityV1 | None = None
    tls_callback_runtime_input: TlsCallbackRuntimeInputV1 | None = None
    evidence_receipt_signing_authority: EvidenceReceiptSigningAuthorityV1 | None = None
    secret_handles: SecretHandlesV1
    evidence_bindings: tuple[EvidenceRoleBindingV2, ...]

    @field_validator("evidence_bindings", mode="before")
    @classmethod
    def parse_evidence_binding_wire(cls, value: Any) -> Any:
        if type(value) is tuple and all(
            type(item) is EvidenceRoleBindingV2 for item in value
        ):
            return value
        if type(value) is not list:
            return value
        expected = {
            "schema_version",
            "role",
            "source",
            "producer_id",
            "producer_implementation_digest",
        }
        bindings: list[EvidenceRoleBindingV2] = []
        for item in value:
            if type(item) is not dict or set(item) != expected:
                raise ValueError("evidence role binding wire keys are not exact")
            if item["schema_version"] != "bb.rl.evidence-role-binding.v2":
                raise ValueError("evidence role binding schema is unsupported")
            if (
                type(item["role"]) is not str
                or not item["role"]
                or type(item["source"]) is not str
                or type(item["producer_id"]) is not str
                or not item["producer_id"]
                or type(item["producer_implementation_digest"]) is not str
            ):
                raise ValueError(
                    "evidence role binding wire values are not exact strings"
                )
            bindings.append(
                EvidenceRoleBindingV2(
                    role=item["role"],
                    source=EvidenceRoleSourceV2(item["source"]),
                    producer_id=item["producer_id"],
                    producer_implementation_digest=item[
                        "producer_implementation_digest"
                    ],
                )
            )
        return tuple(bindings)

    @model_validator(mode="after")
    def cross_bind(self) -> "HarnessCompositionManifestV1":
        ids = {item.handle_id for item in self.secret_handles.records}
        receipt = self.control_plane.receipt_authenticator.secret_handle_id
        matching = [
            item for item in self.secret_handles.records if item.handle_id == receipt
        ]
        if len(matching) != 1 or matching[0].purpose != "receipt_signer":
            raise ValueError("receipt authenticator must bind receipt secret")
        if receipt not in ids:
            raise ValueError("unknown receipt secret handle")
        callback_tls = self.tls_callback_runtime_input
        callback_keys = tuple(
            item
            for item in self.secret_handles.records
            if item.purpose == "callback_tls_private_key"
        )
        if self.server.mode == "loopback" and callback_tls is not None:
            raise ValueError(
                "loopback composition cannot carry TLS callback runtime input"
            )
        if callback_tls is None:
            if callback_keys:
                raise ValueError("unused TLS callback private-key handle")
        elif (
            len(callback_keys) != 1
            or callback_keys[0].handle_id != callback_tls.private_key_secret_handle_id
        ):
            raise ValueError("TLS callback private-key handle is not exact")
        if callback_tls is not None and self.openssl_authority is None:
            raise ValueError("TLS callback requires pinned OpenSSL authority")
        evidence_signer = self.evidence_receipt_signing_authority
        evidence_keys = tuple(
            item
            for item in self.secret_handles.records
            if item.purpose == "evidence_receipt_signing_key"
        )
        if evidence_signer is None:
            if evidence_keys:
                raise ValueError("unused evidence receipt signing-key handle")
        elif (
            len(evidence_keys) != 1
            or evidence_keys[0].handle_id
            != evidence_signer.private_key_secret_handle_id
            or self.openssl_authority is None
        ):
            raise ValueError("evidence receipt signing authority is not exact")
        if evidence_signer is not None:
            openssl_digest = (
                "sha256:"
                + hashlib.sha256(
                    _canonical_bytes(self.openssl_authority.model_dump(mode="json"))
                ).hexdigest()
            )
            if evidence_signer.openssl_authority_digest != openssl_digest:
                raise ValueError("evidence receipt OpenSSL authority is not exact")
        if self.server.mode == "loopback":
            if self.outer_bridge_plan is not None:
                raise ValueError("loopback server cannot carry outer bridge authority")
        elif (
            self.outer_bridge_plan is None
            or self.server.host != self.outer_bridge_plan.gateway
        ):
            raise ValueError(
                "internal bridge server host must equal outer bridge gateway"
            )
        socket_roles = tuple(item.role for item in self.prebound_service_socket_plans)
        if socket_roles != tuple(sorted(set(socket_roles))):
            raise ValueError("prebound service socket roles must be sorted and unique")
        if self.outer_bridge_plan is None:
            if self.prebound_service_socket_plans:
                raise ValueError(
                    "loopback composition cannot carry prebound socket plans"
                )
        else:
            for socket_plan in self.prebound_service_socket_plans:
                if socket_plan.gateway != self.outer_bridge_plan.gateway:
                    raise ValueError(
                        "prebound socket gateway does not match bridge plan"
                    )
            harness = tuple(
                item
                for item in self.prebound_service_socket_plans
                if item.role == "harness"
            )
            if len(harness) != 1 or harness[0].observed_port != self.server.port:
                raise ValueError("harness prebound socket does not bind server port")
            if callback_tls is not None:
                callback_plans = tuple(
                    item
                    for item in self.prebound_service_socket_plans
                    if item.role == callback_tls.socket_role
                )
                if (
                    len(callback_plans) != 1
                    or callback_tls.host != self.outer_bridge_plan.gateway
                    or callback_tls.observed_port != callback_plans[0].observed_port
                    or callback_tls.socket_plan_id != callback_plans[0].socket_plan_id
                ):
                    raise ValueError(
                        "TLS callback does not bind its gateway socket plan"
                    )
        if self.server.mode == "internal_bridge":
            if self.openssl_authority is None:
                raise ValueError(
                    "internal bridge composition requires pinned OpenSSL executable authority"
                )
        elif self.openssl_authority is not None:
            raise ValueError(
                "loopback composition cannot carry OpenSSL executable authority"
            )
        if (
            self.server.mode == "internal_bridge"
            and self.host_runtime_authority is None
        ):
            raise ValueError(
                "internal bridge composition requires host runtime build authority"
            )
        if self.host_runtime_authority is not None and any(
            not runtime.executable_path.startswith(
                self.host_runtime_authority.root + "/"
            )
            for runtime in self.installed.runtimes
            if runtime.runtime_class is c.RuntimeClass.TRUSTED_PROCESS
        ):
            raise ValueError("installed runtime path escapes host runtime authority")
        return self


class HarnessCompositionManifestV2(HarnessCompositionManifestV1):
    schema_version: Literal["bb.rl.harness-composition.v2"]
    config_bundle_ref: None = Field(default=None, exclude=True)
    config_bundle_refs: tuple[ArtifactFileRefV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_config_bundle_set(self) -> "HarnessCompositionManifestV2":
        digests = tuple(ref.sha256 for ref in self.config_bundle_refs)
        if digests != tuple(sorted(set(digests))):
            raise ValueError("V2 config bundle refs must be digest sorted and unique")
        return self


class ComposedHarnessManifestV1(_ExactModel):
    schema_version: Literal["bb.rl.harness-composed.v1"]
    composition_id: str
    input_manifest_digest: str
    authority_bundle_digest: str
    config_bundle_digest: str
    admitted_set_digest: str
    selector_digests: tuple[str, ...]
    admission_policy_digest: str
    registry_snapshot_digest: str
    revocation_state_digests: tuple[str, ...]
    compiler_identity: CompilerIdentityV1
    installed_authority_digest: str
    runner_registry_digest: str
    evidence_authority_digest: str
    store_authority_digests: tuple[str, ...]
    server_authority_digest: str
    outer_bridge_plan_digest: str
    openssl_authority_digest: str
    host_runtime_authority_digest: str
    tls_callback_runtime_input_digest: str
    evidence_receipt_signing_authority_digest: str
    secret_handle_ids: tuple[str, ...]
    receipt_key_id: str
    receipt_algorithm: Literal["hmac-sha256-v1"]

    _digest_fields = field_validator(
        "input_manifest_digest",
        "authority_bundle_digest",
        "config_bundle_digest",
        "admitted_set_digest",
        "admission_policy_digest",
        "registry_snapshot_digest",
        "installed_authority_digest",
        "runner_registry_digest",
        "evidence_authority_digest",
        "server_authority_digest",
        "outer_bridge_plan_digest",
        "openssl_authority_digest",
        "host_runtime_authority_digest",
        "tls_callback_runtime_input_digest",
        "evidence_receipt_signing_authority_digest",
    )(_digest)


_ARTIFACT_MEDIA_TYPES: Mapping[c.ArtifactKind, str] = MappingProxyType(
    {
        c.ArtifactKind.ADMISSION_RECEIPT: "application/vnd.breadboard.admission-receipt+json;version=1",
        c.ArtifactKind.COMPILED_MANIFEST: "application/vnd.breadboard.compiled-manifest+json;version=1",
        c.ArtifactKind.ADMITTED_SET: "application/vnd.breadboard.admitted-set+json;version=1",
        c.ArtifactKind.DIRECT_SELECTOR: "application/vnd.breadboard.direct-selector+json;version=1",
        c.ArtifactKind.CONFIG_SET: "application/vnd.breadboard.config-set+json;version=1",
        c.ArtifactKind.MUTATION_OVERLAY: "application/vnd.breadboard.mutation-overlay+json;version=1",
        c.ArtifactKind.SELECTION_RECORD: "application/vnd.breadboard.selection-record+json;version=1",
        c.ArtifactKind.EFFECTIVE_EXECUTION_PLAN: "application/vnd.breadboard.effective-execution-plan+json;version=1",
    }
)


class _SystemUTCClock:
    def current(self) -> datetime:
        return datetime.now(UTC)


class HmacSha256ReceiptAuthenticator:
    __slots__ = ("_key_id", "_key")

    def __init__(self, *, key_id: str, key: bytes) -> None:
        if not key_id or len(key) < 32:
            raise ValueError("invalid receipt authenticator authority")
        self._key_id = key_id
        self._key = bytes(key)

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def algorithm(self) -> str:
        return "hmac-sha256-v1"

    def sign(self, unsigned_canonical_bytes: bytes) -> bytes:
        return hmac.digest(self._key, unsigned_canonical_bytes, "sha256")

    def verify(self, unsigned_canonical_bytes: bytes, signature: bytes) -> bool:
        return hmac.compare_digest(self.sign(unsigned_canonical_bytes), signature)


class PinnedRevocationStore:
    def __init__(self, bindings: Sequence[c.RevocationBinding]) -> None:
        validated = tuple(
            c.RevocationBinding.model_validate(item.model_dump(mode="json"))
            for item in bindings
        )
        mapping = {item.scope_digest: item for item in validated}
        if len(mapping) != len(validated):
            raise ValueError("duplicate revocation scope")
        self._bindings = MappingProxyType(mapping)

    def load(self, scope_digest: str) -> c.RevocationBinding:
        try:
            return self._bindings[scope_digest]
        except KeyError as exc:
            raise ValueError("revocation scope is not pinned") from exc


class _PinnedPolicyCapabilityRegistry:
    def __init__(
        self,
        observations: Sequence[c.PolicyCapabilityObservation],
        attestations: Sequence[c.PolicyCapabilityAttestationRecord],
        revocations: PinnedRevocationStore,
    ) -> None:
        values = tuple(
            c.PolicyCapabilityObservation.model_validate(item.model_dump(mode="json"))
            for item in observations
        )
        mapping: dict[str, c.PolicyCapabilityObservation] = {}
        for attestation in attestations:
            matches = tuple(
                observation
                for observation in values
                if (
                    observation.route_id == attestation.route_id
                    and observation.route_revision_digest
                    == attestation.route_revision_digest
                    and observation.model_digest == attestation.model_digest
                    and observation.tokenizer_digest == attestation.tokenizer_digest
                    and observation.checkpoint_digest == attestation.checkpoint_digest
                    and observation.capability_digest == attestation.capability_digest
                    and observation.revocation == attestation.revocation
                    and observation.provenance.validity == attestation.validity
                    and observation.provenance.signer_key_id
                    in attestation.authorized_signer_key_ids
                )
            )
            if len(matches) != 1:
                raise ValueError(
                    "policy attestation does not uniquely bind an observation"
                )
            mapping[attestation.attestation_digest] = matches[0]
        if len(mapping) != len(tuple(attestations)):
            raise ValueError("duplicate policy capability attestation")
        if {item.canonical_digest() for item in mapping.values()} != {
            item.canonical_digest() for item in values
        }:
            raise ValueError("policy observation authority is not exactly attested")
        self._observations = MappingProxyType(mapping)
        self._revocations = revocations

    @property
    def attested_observations(self) -> Mapping[str, c.PolicyCapabilityObservation]:
        return self._observations

    def observe(
        self,
        *,
        binding: c.PolicyBindingRef,
        subject: c.AuthenticatedSubject,
        now: datetime,
    ) -> c.PolicyCapabilityObservation:
        try:
            observation = self._observations[binding.attestation_digest]
        except KeyError as exc:
            raise ValueError("policy capability attestation is not pinned") from exc
        if (
            observation.route_id != binding.route_id
            or observation.registry_revision_digest != binding.registry_revision_digest
            or observation.subject_scope_digest != subject.authority_scope_digest
        ):
            raise ValueError("policy capability attestation identity mismatch")
        if observation.revocation != self._revocations.load(
            subject.authority_scope_digest
        ):
            raise ValueError("policy capability revocation is stale")
        return observation


class CASConfigRuntimeStore:
    def __init__(self, cas: FilesystemCAS, *, clock: Any | None = None) -> None:
        self._cas = cas
        self._clock = clock or _SystemUTCClock()

    def publish(self, *, kind: c.ArtifactKind, canonical_bytes: bytes) -> c.ArtifactRef:
        if _canonical_bytes(_load_json_exact(canonical_bytes)) != canonical_bytes:
            raise ValueError("runtime artifact is not canonical JSON")
        ref = self._cas.put_bytes(
            canonical_bytes, media_type=_ARTIFACT_MEDIA_TYPES[kind]
        )
        if self._cas.get_bytes(ref, max_bytes=len(canonical_bytes)) != canonical_bytes:
            raise ValueError("runtime artifact readback mismatch")
        return c.ArtifactRef(
            artifact_id=ref.sha256,
            sha256=ref.sha256,
            size_bytes=ref.size_bytes,
            media_type=ref.media_type,
        )

    def load(self, digest: str, *, kind: c.ArtifactKind, max_bytes: int) -> bytes:
        ref = self._cas.get_ref(digest)
        if ref.sha256 != digest or ref.media_type != _ARTIFACT_MEDIA_TYPES[kind]:
            raise ValueError("runtime artifact kind or digest mismatch")
        payload = self._cas.get_bytes(ref, max_bytes=max_bytes)
        if _canonical_bytes(_load_json_exact(payload)) != payload:
            raise ValueError("runtime artifact is not canonical JSON")
        return payload

    @staticmethod
    def _binding_id(owner_key: str) -> str:
        return f"v2/selection-binding/{owner_key}"

    def get_selection_binding(self, owner_key: str) -> c.SelectionBinding | None:
        try:
            payload = self._cas.get_bytes(self._binding_id(owner_key), max_bytes=65536)
        except FileNotFoundError:
            return None
        return c.SelectionBinding.model_validate(_load_json_exact(payload))

    def bind_selection_once(
        self, *, owner_key: str, request_digest: str, selection_record_digest: str
    ) -> c.SelectionCommitToken:
        binding = c.SelectionBinding(
            owner_key=owner_key,
            request_digest=request_digest,
            selection_record_digest=selection_record_digest,
        )
        payload = binding.canonical_bytes()
        try:
            stored = self._cas.put_bytes(
                payload,
                artifact_id=self._binding_id(owner_key),
                media_type="application/vnd.breadboard.selection-binding+json;version=1",
            )
        except ArtifactConflictError as exc:
            raise ValueError("selection binding conflict") from exc
        if self.get_selection_binding(owner_key) != binding:
            raise ValueError("selection binding readback mismatch")
        return c.SelectionCommitToken(
            binding=binding,
            binding_ref=c.ArtifactRef(
                artifact_id=stored.sha256,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
            ),
            verified_at=self._clock.current()
            .astimezone(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        )


def _verify_server_manifest(
    payload: bytes, expected_input_digest: str
) -> CompiledConfigManifest:
    from breadboard_engine.compilation.server_compiler import (
        verify_cached_manifest,
    )

    return verify_cached_manifest(
        payload, expected_compiler_input_digest=expected_input_digest
    )


def _json_pointer_parts(pointer: str) -> tuple[str, ...]:
    if type(pointer) is not str or not pointer.startswith("/"):
        raise ValueError("absolute JSON pointer required")
    return tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    )


def _json_pointer_get(value: Any, pointer: str) -> Any:
    current = value
    for part in _json_pointer_parts(pointer):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _json_pointer_set(value: Any, pointer: str, replacement: Any) -> None:
    parts = _json_pointer_parts(pointer)
    current = value
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    final = parts[-1]
    if isinstance(current, list):
        current[int(final)] = replacement
    else:
        current[final] = replacement


def _semantic_difference_paths(
    left: Any, right: Any, path: str = ""
) -> tuple[str, ...]:
    if type(left) is dict and type(right) is dict:
        return tuple(
            difference
            for key in sorted(set(left) | set(right))
            for difference in _semantic_difference_paths(
                left.get(key),
                right.get(key),
                path + "/" + key.replace("~", "~0").replace("/", "~1"),
            )
        )
    if type(left) is list and type(right) is list:
        if len(left) != len(right):
            return (path + "/length",)
        return tuple(
            difference
            for index, (left_item, right_item) in enumerate(
                zip(left, right, strict=True)
            )
            for difference in _semantic_difference_paths(
                left_item, right_item, f"{path}/{index}"
            )
        )
    return () if left == right else (path or "/",)


def _compiled_identity_projection(
    manifest: CompiledConfigManifest,
) -> dict[str, Any]:
    compiler = manifest.compiler
    return {
        "bundle_digest": manifest.inputs.bundle_digest,
        "closure_digest": manifest.inputs.closure_digest,
        "compiler_input_digest": manifest.inputs.compiler_input_digest,
        "compiler": {
            "compiler_id": compiler.compiler_id,
            "semantic_version": compiler.compiler_version,
            "code_digest": compiler.compiler_code_digest,
            "source_schema_id": compiler.config_schema_id,
            "source_schema_digest": compiler.config_schema_digest,
            "manifest_schema_digest": compiler.manifest_schema_digest,
            "canonicalizer_id": compiler.canonicalizer_id,
            "runtime_abi": compiler.runtime_abi,
        },
        "provenance_digest": "sha256:"
        + hashlib.sha256(
            _canonical_bytes(
                [item.to_canonical_obj() for item in manifest.provenance]
            )
        ).hexdigest(),
        "diagnostics_digest": "sha256:"
        + hashlib.sha256(
            _canonical_bytes(manifest.diagnostics.to_canonical_obj())
        ).hexdigest(),
    }


class PinnedServerCompilerAdapter:
    def __init__(self, manifests: Mapping[str, bytes]) -> None:
        verified: dict[str, tuple[bytes, CompiledConfigManifest]] = {}
        for digest, payload in manifests.items():
            if "sha256:" + hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError("compiled manifest digest mismatch")
            parsed = CompiledConfigManifest.from_json(payload)
            cached = _verify_server_manifest(
                payload, parsed.inputs.compiler_input_digest
            )
            verified[digest] = (bytes(payload), cached)
        self._manifests = MappingProxyType(verified)

    def _manifest(self, request: c.AdmissionRequest) -> CompiledConfigManifest:
        try:
            _, manifest = self._manifests[request.compiled.manifest_digest]
        except KeyError as exc:
            raise ValueError("compiled manifest is not pinned") from exc
        expected = _compiled_identity_projection(manifest)
        observed = request.compiled.model_dump(mode="json")
        observed.pop("manifest_digest")
        observed_semantic_digest = observed.pop("semantic_digest")
        if observed != expected:
            raise ValueError("compiled manifest identity mismatch")
        source = request.behavior_source
        if isinstance(source, c.OverlayDerivedBehaviorSource):
            if (
                observed_semantic_digest == manifest.semantic_digest
                or source.base_manifest_digest
                != request.compiled.manifest_digest
                or source.derived_semantic_digest
                != observed_semantic_digest
                or request.parent_receipt_digest is None
                or source.parent_receipt_digest
                != request.parent_receipt_digest
                or request.overlay_chain_digest is None
                or source.overlay_chain_digest
                != request.overlay_chain_digest
            ):
                raise ValueError("overlay-derived compiler identity mismatch")
        elif observed_semantic_digest != manifest.semantic_digest:
            raise ValueError("compiled manifest semantic identity mismatch")
        return manifest

    def verify_bundle(self, request: c.AdmissionRequest) -> None:
        self._manifest(request)

    def enforce_compile_budget(self, request: c.AdmissionRequest) -> None:
        self._manifest(request)

    def compile(self, request: c.AdmissionRequest) -> CompilerSemanticView:
        manifest = self._manifest(request)
        semantic = manifest.semantic.to_canonical_obj()
        metadata = semantic.get("metadata")
        profile_metadata = (
            metadata.get("profile_metadata") if isinstance(metadata, Mapping) else None
        )
        authority = (
            profile_metadata.get("breadboard_rl_authority")
            if isinstance(profile_metadata, Mapping)
            else None
        )
        if not isinstance(authority, Mapping) or set(authority) != {
            "requested_capabilities",
            "task_binding_digest",
        }:
            raise ValueError("compiled manifest lacks BreadBoard capability authority")
        capabilities = c.CapabilityVector.model_validate(
            authority["requested_capabilities"]
        )
        task_binding_digest = authority["task_binding_digest"]
        if (
            capabilities != request.requested_capabilities
            or task_binding_digest != capabilities.task.task_binding_digest
            or task_binding_digest != request.task_binding_digest
        ):
            raise ValueError("request differs from compiled capability authority")
        mutable_pointers = tuple(
            item.model_dump(mode="json") for item in capabilities.mutable_pointers
        )
        return CompilerSemanticView(
            {
                "compiler_identity": request.compiled.compiler.model_dump(mode="json"),
                "compile_input_identity": {
                    "bundle_digest": manifest.inputs.bundle_digest,
                    "closure_digest": manifest.inputs.closure_digest,
                    "compiler_input_digest": manifest.inputs.compiler_input_digest,
                },
                "semantic_identity": {
                    "manifest_digest": request.compiled.manifest_digest,
                    "semantic_digest": request.compiled.semantic_digest,
                },
                "requested_capabilities": capabilities.model_dump(mode="json"),
                "task_contract": {
                    "task_binding_digest": task_binding_digest,
                    "task": capabilities.task.model_dump(mode="json"),
                },
                "mutable_pointer_declarations": list(mutable_pointers),
                "provenance": [item.to_canonical_obj() for item in manifest.provenance],
                "diagnostics": manifest.diagnostics.to_canonical_obj(),
                "loss_disposition": {"runner_visible_losses": []},
                "authority_disposition": {"forbidden_raw_authority": []},
            }
        )

    def extract_effective_semantics(
        self, *, canonical_manifest_bytes: bytes
    ) -> Mapping[str, Any]:
        parsed = CompiledConfigManifest.from_json(canonical_manifest_bytes)
        _verify_server_manifest(
            canonical_manifest_bytes, parsed.inputs.compiler_input_digest
        )
        return parsed.semantic.to_canonical_obj()

    def normalize_effective_semantics(
        self,
        *,
        canonical_manifest_bytes: bytes,
        effective_semantics: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        baseline = self.extract_effective_semantics(
            canonical_manifest_bytes=canonical_manifest_bytes
        )
        candidate = json.loads(_canonical_bytes(effective_semantics))
        root_id = baseline["root_config_node_id"]
        root_index = next(
            index
            for index, node in enumerate(baseline["config_nodes"])
            if node["node_id"] == root_id
        )
        allowed = tuple(baseline["optimizer_mutable_pointers"])
        mirror_by_pointer = {
            pointer: (
                f"/config_nodes/{root_index}/semantic_config" + pointer
            )
            for pointer in allowed
        }
        initial_differences = set(
            _semantic_difference_paths(baseline, candidate)
        )
        allowed_differences = set(allowed) | set(mirror_by_pointer.values())
        if not initial_differences <= allowed_differences:
            raise ValueError("effective semantics changed undeclared fields")
        for pointer, mirror in mirror_by_pointer.items():
            baseline_value = _json_pointer_get(baseline, pointer)
            candidate_value = _json_pointer_get(candidate, pointer)
            baseline_mirror = _json_pointer_get(baseline, mirror)
            candidate_mirror = _json_pointer_get(candidate, mirror)
            if candidate_value == baseline_value:
                if candidate_mirror != baseline_mirror:
                    raise ValueError(
                        "root semantic mirror changed without declared pointer"
                    )
                continue
            if candidate_mirror not in (baseline_mirror, candidate_value):
                raise ValueError(
                    "root semantic mirror differs from declared pointer"
                )
            _json_pointer_set(candidate, mirror, candidate_value)
        differences = set(_semantic_difference_paths(baseline, candidate))
        if not differences <= allowed_differences:
            raise ValueError("effective semantics normalization drifted")
        CompiledConfig.from_dict(candidate)
        return candidate


    def validate_effective_semantics(
        self, *, canonical_manifest_bytes: bytes, effective_semantics: Mapping[str, Any]
    ) -> str:
        normalized = self.normalize_effective_semantics(
            canonical_manifest_bytes=canonical_manifest_bytes,
            effective_semantics=effective_semantics,
        )
        return (
            "sha256:"
            + hashlib.sha256(
                _canonical_bytes(
                    {
                        "schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID,
                        "config": normalized,
                    }
                )
            ).hexdigest()
        )


class _CASMaterializationSourceReader:
    """Read sealed source manifests and members from the composition's shared CAS."""

    def __init__(self, cas: FilesystemCAS) -> None:
        self._cas = cas
        self._manifests: dict[str, SealedSourceManifest] = {}

    def load_manifest(self, digest: str, *, max_bytes: int) -> SealedSourceManifest:
        payload = self._cas.get_bytes(digest, max_bytes=max_bytes)
        raw = _load_json_exact(payload)
        if type(raw) is not dict or _canonical_bytes(raw) != payload:
            raise ValueError("sealed source manifest is not canonical")
        if set(raw) != {
            "schema_version",
            "media_type",
            "source_digest",
            "entries",
            "total_bytes",
            "total_files",
        }:
            raise ValueError("sealed source manifest keys are not exact")
        entries_raw = raw["entries"]
        if type(entries_raw) is not list:
            raise ValueError("sealed source entries must be a list")
        entries = tuple(
            SourceManifestEntry(
                logical_path=item["path"],
                kind=item["kind"],
                byte_count=item["bytes"],
                mode=item["mode"],
                content_digest=item["digest"],
            )
            for item in entries_raw
            if type(item) is dict
            and set(item) == {"path", "kind", "bytes", "mode", "digest"}
        )
        if len(entries) != len(entries_raw):
            raise ValueError("sealed source entry keys are not exact")
        manifest = SealedSourceManifest(
            source_digest=raw["source_digest"],
            schema_identity=raw["schema_version"],
            media_identity=raw["media_type"],
            entries=entries,
            total_bytes=raw["total_bytes"],
            total_files=raw["total_files"],
        )
        if manifest.source_digest != digest:
            raise ValueError("sealed source authority does not match requested digest")
        self._manifests[digest] = manifest
        return manifest

    def read_member(self, digest: str, logical_path: str, *, max_bytes: int) -> bytes:
        manifest = self._manifests.get(digest)
        if manifest is None:
            manifest = self.load_manifest(digest, max_bytes=_MAX_AUTHORITY_BYTES)
        member = next(
            (item for item in manifest.entries if item.logical_path == logical_path),
            None,
        )
        if member is None or member.kind != "file" or member.content_digest is None:
            raise ValueError("sealed source member is not admitted")
        stored_ref = self._cas.get_ref(member.content_digest)
        if stored_ref.sha256 != member.content_digest:
            raise ValueError("sealed source member digest authority mismatch")
        payload = self._cas.get_bytes(member.content_digest, max_bytes=max_bytes)
        if len(payload) != member.byte_count:
            raise ValueError("sealed source member size mismatch")
        return payload


@dataclass(frozen=True, slots=True)
class AuthorityGraph:
    config_runtime: ConfigRuntime
    cas: FilesystemCAS
    compiler: PinnedServerCompilerAdapter
    revocations: PinnedRevocationStore
    policy_capabilities: _PinnedPolicyCapabilityRegistry
    store: CASConfigRuntimeStore
    clock: _SystemUTCClock
    authenticator: HmacSha256ReceiptAuthenticator
    policy: c.AdmissionPolicySnapshot
    registries: c.RegistrySnapshotSet
    admitted_set: c.AdmittedSetManifest
    direct_selectors: tuple[c.DirectSelector, ...]
    weighted_selectors: tuple[c.ConfigSetManifest, ...]
    policy_http: PolicyHttpAuthorityGraphV1
    tls_trust: tuple[PolicyTlsTrustAuthorityV1, ...]
    tls_ca_pem_by_route: Mapping[str, bytes]


def _build_authority_graph(
    *,
    cas: FilesystemCAS,
    policy: c.AdmissionPolicySnapshot,
    registries: c.RegistrySnapshotSet,
    revocations: Sequence[c.RevocationBinding],
    policy_capabilities: Sequence[c.PolicyCapabilityObservation],
    admitted_set: c.AdmittedSetManifest,
    direct_selectors: Sequence[c.DirectSelector],
    weighted_selectors: Sequence[c.ConfigSetManifest],
    compiled_manifests: Mapping[str, bytes],
    admission_receipts: Mapping[str, bytes],
    policy_http: PolicyHttpAuthorityGraphV1,
    tls_trust: Sequence[PolicyTlsTrustAuthorityV1],
    tls_ca_pem_by_route: Mapping[str, bytes],
    receipt_key_id: str,
    receipt_key: bytes,
) -> AuthorityGraph:
    if policy_http.registry_revision_digest != registries.digests.route_registry_digest:
        raise ValueError("policy HTTP authority registry revision mismatch")
    if policy_http.routes != registries.routes:
        raise ValueError("policy HTTP route authority differs from registry")
    if policy_http.observations != tuple(policy_capabilities):
        raise ValueError("policy HTTP observations differ from capability snapshot")
    if tuple(item.route_id for item in tls_trust) != tuple(
        route.grant.route_id for route in policy_http.routes
    ):
        raise ValueError("TLS trust authority differs from policy routes")
    if set(tls_ca_pem_by_route) != {item.route_id for item in tls_trust}:
        raise ValueError("TLS CA authority is incomplete")
    clock = _SystemUTCClock()
    compiler = PinnedServerCompilerAdapter(compiled_manifests)
    revocation_store = PinnedRevocationStore(revocations)
    capabilities = _PinnedPolicyCapabilityRegistry(
        policy_capabilities,
        registries.policy_capability_attestations,
        revocation_store,
    )
    store = CASConfigRuntimeStore(cas, clock=clock)
    authenticator = HmacSha256ReceiptAuthenticator(
        key_id=receipt_key_id, key=receipt_key
    )
    runtime = ConfigRuntime(
        compiler=compiler,
        policy=policy,
        registries=registries,
        revocations=revocation_store,
        store=store,
        clock=clock,
        authenticator=authenticator,
        policy_capabilities=capabilities,
    )
    for kind, values in (
        (c.ArtifactKind.ADMITTED_SET, (admitted_set,)),
        (c.ArtifactKind.DIRECT_SELECTOR, tuple(direct_selectors)),
        (c.ArtifactKind.CONFIG_SET, tuple(weighted_selectors)),
    ):
        for value in values:
            store.publish(kind=kind, canonical_bytes=value.canonical_bytes())
    for kind, payloads in (
        (c.ArtifactKind.COMPILED_MANIFEST, compiled_manifests),
        (c.ArtifactKind.ADMISSION_RECEIPT, admission_receipts),
    ):
        for digest, payload in payloads.items():
            if store.publish(kind=kind, canonical_bytes=payload).sha256 != digest:
                raise ValueError(f"{kind.value} publication mismatch")
    return AuthorityGraph(
        config_runtime=runtime,
        cas=cas,
        compiler=compiler,
        revocations=revocation_store,
        policy_capabilities=capabilities,
        store=store,
        clock=clock,
        authenticator=authenticator,
        policy=policy,
        registries=registries,
        admitted_set=admitted_set,
        direct_selectors=tuple(direct_selectors),
        weighted_selectors=tuple(weighted_selectors),
        policy_http=policy_http,
        tls_trust=tuple(tls_trust),
        tls_ca_pem_by_route=MappingProxyType(dict(tls_ca_pem_by_route)),
    )


@dataclass(slots=True)
class _PinnedSecret:
    fd: int
    data: bytes

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _observed_socket(fd: int, plan: OuterBridgePlanV1) -> dict[str, Any]:
    metadata = os.fstat(fd)
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError("prebound service descriptor is not a socket")
    duplicate = socket.socket(fileno=os.dup(fd))
    try:
        address = duplicate.getsockname()
        family = duplicate.family
        socket_type = duplicate.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
        protocol = duplicate.getsockopt(
            socket.SOL_SOCKET, getattr(socket, "SO_PROTOCOL", 38)
        )
        freebind = duplicate.getsockopt(
            socket.SOL_IP, getattr(socket, "IP_FREEBIND", 15)
        )
    finally:
        duplicate.close()
    if (
        family != socket.AF_INET
        or socket_type != socket.SOCK_STREAM
        or protocol != socket.IPPROTO_TCP
        or freebind != 1
        or type(address) is not tuple
        or len(address) != 2
        or address[0] != plan.gateway
        or type(address[1]) is not int
        or not 1 <= address[1] <= 65535
    ):
        raise ValueError("prebound service socket authority is not exact")
    return {
        "gateway": address[0],
        "observed_port": address[1],
        "family": "AF_INET",
        "socket_type": "SOCK_STREAM",
        "protocol": "IPPROTO_TCP",
        "socket_device": metadata.st_dev,
        "socket_inode": metadata.st_ino,
        "socket_mode": metadata.st_mode,
        "socket_owner_uid": metadata.st_uid,
        "getsockname_host": address[0],
        "getsockname_port": address[1],
        "ip_freebind": True,
    }


class OuterBridgeLifecycle:
    def __init__(
        self,
        *,
        broker: MountNamespaceBroker,
        composition_digest: str,
        plan: OuterBridgePlanV1,
        authenticator: HmacSha256ReceiptAuthenticator,
        lease_ttl_seconds: int,
        prebound_service_socket_plans: Sequence[PreboundServiceSocketPlanV1],
        prebound_service_socket_fds: Mapping[str, int],
    ) -> None:
        self._broker = broker
        self._composition_digest = _digest(composition_digest)
        self._plan = plan
        self._authenticator = authenticator
        self._lease_ttl_seconds = lease_ttl_seconds
        self._socket_plans = MappingProxyType(
            {item.role: item for item in prebound_service_socket_plans}
        )
        if (
            not self._socket_plans
            or tuple(self._socket_plans) != tuple(sorted(self._socket_plans))
            or set(self._socket_plans) != set(prebound_service_socket_fds)
            or any(
                type(role) is not str or not role or type(fd) is not int or fd < 0
                for role, fd in prebound_service_socket_fds.items()
            )
        ):
            raise ValueError("prebound service socket descriptors are not exact")
        self._socket_fds = MappingProxyType(dict(prebound_service_socket_fds))
        self.lease: OuterBridgeLeaseV1 | None = None
        self.service_sockets: Mapping[str, PreboundServiceSocketLeaseV1] = (
            MappingProxyType({})
        )
        self.cleanup_receipt: OuterBridgeCleanupReceiptV1 | None = None

    @staticmethod
    def _sha256(data: bytes) -> str:
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def _docker(self, tail: Sequence[str], *, allow_failure: bool = False) -> Any:
        result = self._broker.execute_docker(
            tail, timeout_ms=30_000, output_limit=4 * 1024 * 1024
        )
        if (
            result.timed_out
            or result.output_limited
            or (result.returncode != 0 and not allow_failure)
        ):
            raise ValueError("private Docker network operation failed")
        return result

    def _signed(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        auth_digest = self._sha256(_canonical_bytes(payload))
        unsigned = {
            **payload,
            "signer_key_id": self._authenticator.key_id,
            "signature_algorithm": self._authenticator.algorithm,
            "auth_digest": auth_digest,
        }
        return {
            **unsigned,
            "signature": self._authenticator.sign(_canonical_bytes(unsigned)).hex(),
        }

    def start(self) -> OuterBridgeLeaseV1:
        if self.lease is not None:
            raise ValueError("outer bridge lifecycle cannot be restarted")
        before = {
            role: _observed_socket(fd, self._plan)
            for role, fd in self._socket_fds.items()
        }
        for role, observed in before.items():
            socket_plan = self._socket_plans[role]
            expected = socket_plan.model_dump(mode="json")
            for key in ("schema_version", "role", "socket_plan_id"):
                expected.pop(key)
            if observed != expected:
                raise ValueError(
                    "prebound descriptor does not match immutable socket plan"
                )
        labels = tuple(
            part
            for item in self._plan.labels
            for part in ("--label", f"{item.key}={item.value}")
        )
        created = self._docker(
            (
                "network",
                "create",
                "--driver",
                self._plan.driver,
                "--internal",
                "--subnet",
                self._plan.subnet,
                "--gateway",
                self._plan.gateway,
                *labels,
                self._plan.network_name,
            )
        )
        network_id = created.stdout.decode("ascii", "strict").strip()
        if re.fullmatch(r"[0-9a-f]{64}", network_id) is None:
            raise ValueError("Docker returned a malformed network identity")
        try:
            inspected = self._docker(("network", "inspect", network_id))
            document = _load_json_exact(inspected.stdout)
            if type(document) is not list or len(document) != 1:
                raise ValueError("Docker network inspect result is not singular")
            observation = document[0]
            if type(observation) is not dict:
                raise ValueError("Docker network inspect result is malformed")
            ipam = observation.get("IPAM")
            configs = None if type(ipam) is not dict else ipam.get("Config")
            labels_observed = observation.get("Labels")
            expected_labels = {item.key: item.value for item in self._plan.labels}
            if (
                observation.get("Id") != network_id
                or observation.get("Name") != self._plan.network_name
                or observation.get("Driver") != self._plan.driver
                or observation.get("Internal") is not True
                or type(configs) is not list
                or len(configs) != 1
                or configs[0].get("Subnet") != self._plan.subnet
                or configs[0].get("Gateway") != self._plan.gateway
                or labels_observed != expected_labels
                or observation.get("Containers") != {}
            ):
                raise ValueError("Docker network inspect does not match bridge plan")
            inspect_bytes = _canonical_bytes(observation)
            created_text = observation.get("Created")
            if type(created_text) is not str:
                raise ValueError("Docker network creation time is absent")
            created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                raise ValueError("Docker network creation time is unzoned")
            expires_at = created_at + timedelta(seconds=self._lease_ttl_seconds)
            binding = self._broker.daemon_binding
            if binding is None:
                raise ValueError("private daemon binding is absent")
            observation_broker = self._broker.observation
            base = {
                "schema_version": "bb.rl.harness-outer-bridge-lease.v1",
                "composition_digest": self._composition_digest,
                "plan_digest": self._plan.canonical_digest(),
                "broker_pid": observation_broker.pid,
                "broker_starttime": observation_broker.starttime,
                "broker_mount_namespace": observation_broker.mount_namespace,
                "daemon_instance_id": binding.daemon_instance_id,
                "daemon_pid": binding.daemon_pid,
                "daemon_starttime": binding.daemon_starttime,
                "daemon_pid_namespace": binding.daemon_pid_namespace,
                "network_id": network_id,
                "network_name": self._plan.network_name,
                "inspect_media_type": (
                    "application/vnd.breadboard.docker-network-inspect+json;version=1"
                ),
                "inspect_bytes_base64": base64.b64encode(inspect_bytes).decode("ascii"),
                "inspect_sha256": self._sha256(inspect_bytes),
                "created_at": created_at.isoformat(),
                "lease_expires_at": expires_at.isoformat(),
            }
            lease_id = self._sha256(_canonical_bytes(base))
            self.lease = OuterBridgeLeaseV1.model_validate(
                self._signed({**base, "lease_id": lease_id}), strict=True
            )
            after = {
                role: _observed_socket(fd, self._plan)
                for role, fd in self._socket_fds.items()
            }
            if before != after:
                raise ValueError(
                    "prebound service socket changed during bridge creation"
                )
            authorities = {}
            bridge_lease_digest = self.lease.canonical_digest()
            for role, observed in before.items():
                before_bytes = _canonical_bytes(observed)
                after_bytes = _canonical_bytes(after[role])
                socket_plan = self._socket_plans[role]
                handoff_receipt = self._sha256(
                    _canonical_bytes(
                        {
                            "role": role,
                            "socket_device": observed["socket_device"],
                            "socket_inode": observed["socket_inode"],
                            "bridge_lease_id": self.lease.lease_id,
                            "state": "retained_for_direct_server_handoff",
                        }
                    )
                )
                authorities[role] = PreboundServiceSocketLeaseV1(
                    schema_version=("bb.rl.harness-prebound-service-socket-lease.v1"),
                    role=role,
                    socket_plan_digest=socket_plan.canonical_digest(),
                    socket_plan_id=(socket_plan.socket_plan_id),
                    bridge_lease_id=self.lease.lease_id,
                    bridge_lease_digest=bridge_lease_digest,
                    pre_create_observation_bytes_base64=base64.b64encode(
                        before_bytes
                    ).decode("ascii"),
                    pre_create_observation_digest=self._sha256(before_bytes),
                    post_create_observation_bytes_base64=base64.b64encode(
                        after_bytes
                    ).decode("ascii"),
                    post_create_observation_digest=self._sha256(after_bytes),
                    server_handoff_receipt=handoff_receipt,
                )
            self.service_sockets = MappingProxyType(authorities)
            return self.lease
        except BaseException:
            self._docker(("network", "rm", network_id), allow_failure=True)
            raise

    def close(self) -> None:
        lease = self.lease
        if lease is None or self.cleanup_receipt is not None:
            return
        delete = self._docker(("network", "rm", lease.network_id), allow_failure=True)
        listed = self._docker(
            (
                "network",
                "ls",
                "--no-trunc",
                "--format",
                "{{json .}}",
            )
        )
        inspect_id = self._docker(
            ("network", "inspect", lease.network_id), allow_failure=True
        )
        inspect_name = self._docker(
            ("network", "inspect", lease.network_name), allow_failure=True
        )
        id_absent = inspect_id.returncode != 0 and (
            lease.network_id.encode() not in listed.stdout
        )
        name_absent = inspect_name.returncode != 0 and (
            lease.network_name.encode() not in listed.stdout
        )
        if delete.returncode != 0 or not id_absent or not name_absent:
            raise ValueError("outer bridge cleanup absence proof failed")
        delete_bytes = _canonical_bytes(
            {
                "returncode": delete.returncode,
                "stdout_base64": base64.b64encode(delete.stdout).decode("ascii"),
                "stderr_base64": base64.b64encode(delete.stderr).decode("ascii"),
            }
        )
        inspect_bytes = _canonical_bytes(
            {
                "id": {
                    "returncode": inspect_id.returncode,
                    "stdout_base64": base64.b64encode(inspect_id.stdout).decode(
                        "ascii"
                    ),
                    "stderr_base64": base64.b64encode(inspect_id.stderr).decode(
                        "ascii"
                    ),
                },
                "name": {
                    "returncode": inspect_name.returncode,
                    "stdout_base64": base64.b64encode(inspect_name.stdout).decode(
                        "ascii"
                    ),
                    "stderr_base64": base64.b64encode(inspect_name.stderr).decode(
                        "ascii"
                    ),
                },
            }
        )
        inspect_stdout_base64 = base64.b64encode(inspect_bytes).decode("ascii")
        inspect_stderr_base64 = base64.b64encode(
            inspect_id.stderr + inspect_name.stderr
        ).decode("ascii")
        inspect_receipt_bytes = _canonical_bytes(
            {
                "stdout_base64": inspect_stdout_base64,
                "stderr_base64": inspect_stderr_base64,
            }
        )
        payload = {
            "schema_version": ("bb.rl.harness-outer-bridge-cleanup-receipt.v1"),
            "lease_id": lease.lease_id,
            "lease_digest": lease.canonical_digest(),
            "network_id": lease.network_id,
            "network_name": lease.network_name,
            "delete_returncode": delete.returncode,
            "delete_stdout_base64": base64.b64encode(delete.stdout).decode("ascii"),
            "delete_stderr_base64": base64.b64encode(delete.stderr).decode("ascii"),
            "delete_result_sha256": self._sha256(delete_bytes),
            "post_list_bytes_base64": base64.b64encode(listed.stdout).decode("ascii"),
            "post_list_sha256": self._sha256(listed.stdout),
            "post_inspect_stdout_base64": inspect_stdout_base64,
            "post_inspect_stderr_base64": inspect_stderr_base64,
            "post_inspect_sha256": self._sha256(inspect_receipt_bytes),
            "id_absent": True,
            "name_absent": True,
            "broker_pid": self._broker.observation.pid,
            "broker_starttime": self._broker.observation.starttime,
        }
        self.cleanup_receipt = OuterBridgeCleanupReceiptV1.model_validate(
            self._signed(payload), strict=True
        )


@dataclass(frozen=True, slots=True)
class ProductionCleanupInventory:
    """Post-close observations from the composition's owned runtime authorities."""

    active_lease_ids: tuple[str, ...]
    orphan_resource_ids: tuple[str, ...]
    leaked_artifact_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]
    container_ids: tuple[str, ...]
    process_ids: tuple[int, ...]
    cgroup_paths: tuple[str, ...]
    mount_paths: tuple[str, ...]
    workspace_paths: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    secret_lease_ids: tuple[str, ...]
    broker_descriptor_count: int

    def canonical_projection(
        self, broker_close_receipt_ref: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        projection = asdict(self)
        projection["broker_close_receipt_ref"] = broker_close_receipt_ref
        return projection

    def canonical_digest(
        self, broker_close_receipt_ref: Mapping[str, Any] | None
    ) -> str:
        return "sha256:" + hashlib.sha256(
            _canonical_bytes(self.canonical_projection(broker_close_receipt_ref))
        ).hexdigest()


def _process_starttime(pid: int) -> str | None:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        suffix = payload[payload.rindex(")") + 2 :].split()
    except (OSError, ValueError):
        return None
    return suffix[19] if len(suffix) >= 20 else None


class _ProductionCleanupProbe:
    """Captures authority identities before launch and queries them after close."""

    def __init__(
        self,
        *,
        manifest: HarnessCompositionManifestV1,
        materialization: Any,
        sandbox_runtime: Any,
        broker: MountNamespaceBroker | None,
        pinned: Mapping[str, _PinnedSecret],
        directory_fds: Mapping[str, int],
    ) -> None:
        self._materialization = materialization
        self._sandbox_runtime = sandbox_runtime
        self._broker = broker
        self._lease_root = Path(manifest.stores.lease.path)
        self._workspace_root = Path(manifest.stores.workspace.path)
        self._secret_fds = {
            handle_id: self._fd_identity(secret.fd)
            for handle_id, secret in pinned.items()
        }
        descriptors: dict[str, tuple[int, int, int]] = {}

        def remember(label: str, descriptor: Any) -> None:
            if type(descriptor) is int and descriptor >= 0:
                identity = self._fd_identity(descriptor)
                if identity is not None:
                    descriptors[label] = identity

        for name, descriptor in directory_fds.items():
            remember(f"directory:{name}", descriptor)
        remember("materialization:cache", getattr(materialization, "_cache_root_fd", None))
        remember(
            "materialization:workspace",
            getattr(materialization, "_workspace_root_fd", None),
        )
        remember("sandbox:lease", getattr(sandbox_runtime, "_lease_root_fd", None))
        for handle_id, secret in pinned.items():
            remember(f"secret:{handle_id}", secret.fd)
        if broker is not None:
            remember("broker:socket", broker._socket.fileno())
            remember("broker:daemon-root", getattr(broker, "_daemon_root_fd", None))
            for name, descriptor in broker._authority_fds.items():
                remember(f"broker:{name}", descriptor)
        journal_identity = (
            None
            if broker is None
            else self._fd_identity(getattr(broker, "_journal_root_fd", -1))
        )
        self._supervisor_journal_identity = (
            None if journal_identity is None else journal_identity[1:]
        )
        self._descriptors = descriptors

        process_identities: dict[int, str] = {}
        if broker is not None:
            process_identities[broker.observation.pid] = broker.observation.starttime
            binding = broker.daemon_binding
            if binding is not None:
                process_identities[binding.daemon_pid] = binding.daemon_starttime
            containerd = broker.containerd_observation
            if containerd is not None:
                pid = containerd.get("pid")
                starttime = containerd.get("starttime")
                if type(pid) is int and type(starttime) is str:
                    process_identities[pid] = starttime
        self._process_identities = process_identities
        authority = None if broker is None else broker._daemon_authority
        self._container_root = (
            None if authority is None else Path(authority.data_root) / "containers"
        )
        self._broker_paths = (
            ()
            if broker is None
            else tuple(
                dict.fromkeys(
                    (
                        broker._stage_root,
                        *(
                            receipt["source_path"]
                            for receipt in getattr(broker, "_receipts", {}).values()
                            if type(receipt.get("source_path")) is str
                        ),
                    )
                )
            )
        )
        self._episode_lease_ids: set[str] = set()
        self._episode_workspace_paths: set[Path] = set()
        self._episode_artifacts: dict[str, Path] = {}
        self._episode_container_ids: set[str] = set()
        self._episode_cgroup_paths: set[Path] = set()
        self._episode_mount_roots: set[Path] = {
            Path(path) for path in self._broker_paths
        }

    def _capture_process(self, pid: int, start_identity: str | None = None) -> None:
        starttime = (
            _process_starttime(pid)
            if start_identity is None
            else start_identity.removeprefix("linux-proc-start:")
        )
        if starttime is None:
            return
        self._process_identities[pid] = starttime
        try:
            lines = Path(f"/proc/{pid}/cgroup").read_text(
                encoding="ascii"
            ).splitlines()
        except OSError:
            return
        for line in lines:
            relative = line.rsplit(":", 1)[-1].lstrip("/")
            self._episode_cgroup_paths.add(Path("/sys/fs/cgroup") / relative)

    def _record_lease(self, lease: Any) -> None:
        lease_id = getattr(lease, "lease_id", None)
        if type(lease_id) is str:
            self._episode_lease_ids.add(lease_id)
        workspace = getattr(lease, "workspace", None)
        if workspace is None:
            materialized = getattr(lease, "_materialized", None)
            workspace = getattr(materialized, "workspace_path", None)
        if workspace is not None:
            path = Path(workspace)
            self._episode_workspace_paths.add(path)
            self._episode_mount_roots.add(path)
        runtime = getattr(lease, "_runtime", None)
        container_id = getattr(runtime, "container_id", None)
        if type(container_id) is str:
            self._episode_container_ids.add(container_id)
        for staged in getattr(runtime, "_staged_mounts", ()):
            for attribute in ("source_path", "target_path", "path"):
                value = getattr(staged, attribute, None)
                if type(value) is str:
                    self._episode_mount_roots.add(Path(value))
    def _record_runtime(self, lease: Any) -> None:
        self._record_lease(lease)
        runtime = getattr(lease, "_runtime", None)
        for process_group in getattr(runtime, "_groups", ()):
            if type(process_group) is int and process_group > 0:
                self._capture_process(process_group)


    async def _release_snapshot(
        self, snapshot_id: str, close: Any
    ) -> Any:
        del snapshot_id
        return await close()


    @staticmethod
    def _mountinfo_paths(pid: int | None = None) -> tuple[Path, ...]:
        source = (
            Path("/proc/self/mountinfo")
            if pid is None
            else Path(f"/proc/{pid}/mountinfo")
        )
        try:
            lines = source.read_text(encoding="ascii").splitlines()
        except OSError:
            return ()
        paths: list[Path] = []
        for line in lines:
            fields = line.split()
            if len(fields) < 5:
                continue
            decoded = (
                fields[4]
                .replace("\\040", " ")
                .replace("\\011", "\t")
                .replace("\\012", "\n")
                .replace("\\134", "\\")
            )
            paths.append(Path(decoded))
        return tuple(paths)

    @staticmethod
    def _fd_identity(descriptor: int) -> tuple[int, int, int] | None:
        try:
            metadata = os.fstat(descriptor)
        except OSError:
            return None
        return descriptor, metadata.st_dev, metadata.st_ino

    @staticmethod
    def _same_fd(identity: tuple[int, int, int] | None) -> bool:
        if identity is None:
            return False
        descriptor, device, inode = identity
        try:
            metadata = os.fstat(descriptor)
        except OSError:
            return False
        return (metadata.st_dev, metadata.st_ino) == (device, inode)

    @staticmethod
    def _children(root: Path, errors: list[str], label: str) -> tuple[Path, ...]:
        try:
            return tuple(sorted(root.iterdir())) if root.exists() else ()
        except OSError as exc:
            errors.append(f"{label}:{type(exc).__name__}")
            return ()

    @staticmethod
    def _lease_inventory_paths(
        paths: tuple[Path, ...],
        journal_identity: tuple[int, int] | None,
        errors: list[str],
    ) -> tuple[Path, ...]:
        if journal_identity is None:
            return paths
        inventory_paths: list[Path] = []
        for path in paths:
            if path.name != "supervisor-journal":
                inventory_paths.append(path)
                continue
            try:
                metadata = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                errors.append(
                    f"supervisor_journal_inventory:{type(exc).__name__}"
                )
                inventory_paths.append(path)
                continue
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != journal_identity
            ):
                inventory_paths.append(path)
        return tuple(inventory_paths)

    def observe(self) -> ProductionCleanupInventory:
        errors: list[str] = []
        lease_paths = self._children(self._lease_root, errors, "lease_inventory")
        lease_paths = self._lease_inventory_paths(
            lease_paths,
            self._supervisor_journal_identity,
            errors,
        )
        root_workspace_paths = self._children(
            self._workspace_root, errors, "workspace_inventory"
        )
        workspace_paths = tuple(
            sorted(
                {
                    *root_workspace_paths,
                    *(
                        path
                        for path in self._episode_workspace_paths
                        if path.exists()
                    ),
                }
            )
        )
        active_leases = {
            path.stem for path in lease_paths
        } | set(getattr(self._sandbox_runtime, "_leases", {})) | set(
            getattr(self._materialization, "_active_workspaces", {})
        )
        snapshots = dict(getattr(self._sandbox_runtime, "_snapshots", {}))
        live_artifacts = {
            snapshot_id: Path(path)
            for snapshot_id, (_receipt, path) in snapshots.items()
            if Path(path).exists()
        }
        live_artifacts.update(
            {
                snapshot_id: path
                for snapshot_id, path in self._episode_artifacts.items()
                if path.exists()
            }
        )
        artifact_paths = tuple(
            sorted(os.fspath(path) for path in live_artifacts.values())
        )
        live_processes = tuple(
            sorted(
                pid
                for pid, starttime in self._process_identities.items()
                if _process_starttime(pid) == starttime
            )
        )
        cgroup_paths = {
            path for path in self._episode_cgroup_paths if path.exists()
        }
        cgroup_root = Path("/sys/fs/cgroup")
        if self._episode_container_ids and cgroup_root.exists():
            try:
                for root, directories, _files in os.walk(cgroup_root):
                    for name in directories:
                        if any(
                            container_id in name
                            for container_id in self._episode_container_ids
                        ):
                            cgroup_paths.add(Path(root) / name)
            except OSError as exc:
                errors.append(f"cgroup_inventory:{type(exc).__name__}")
        container_ids = tuple(
            sorted(
                {
                    path.name
                    for path in self._children(
                        self._container_root, errors, "container_inventory"
                    )
                }
            )
        ) if self._container_root is not None else ()
        observed_mounts = set(self._mountinfo_paths())
        for pid in live_processes:
            observed_mounts.update(self._mountinfo_paths(pid))
        mount_paths = tuple(
            sorted(
                os.fspath(path)
                for path in observed_mounts
                if any(
                    path == root or root in path.parents
                    for root in self._episode_mount_roots
                )
            )
        )
        secret_ids = tuple(
            sorted(
                handle_id
                for handle_id, identity in self._secret_fds.items()
                if self._same_fd(identity)
            )
        )
        descriptor_count = sum(
            self._same_fd(identity) for identity in self._descriptors.values()
        )
        cgroup_values = tuple(sorted(os.fspath(path) for path in cgroup_paths))
        workspace_values = tuple(os.fspath(path) for path in workspace_paths)
        orphan_ids = tuple(
            sorted(
                {
                    *active_leases,
                    *container_ids,
                    *(f"pid:{pid}" for pid in live_processes),
                    *cgroup_values,
                    *mount_paths,
                    *workspace_values,
                    *artifact_paths,
                    *secret_ids,
                }
            )
        )
        return ProductionCleanupInventory(
            active_lease_ids=tuple(sorted(active_leases)),
            orphan_resource_ids=orphan_ids,
            leaked_artifact_ids=tuple(sorted(live_artifacts)),
            cleanup_errors=tuple(errors),
            container_ids=container_ids,
            process_ids=live_processes,
            cgroup_paths=cgroup_values,
            mount_paths=mount_paths,
            workspace_paths=workspace_values,
            artifact_paths=artifact_paths,
            secret_lease_ids=secret_ids,
            broker_descriptor_count=descriptor_count,
        )


class _TrackedPrimaryLease:
    __slots__ = ("_lease", "_tracker")

    def __init__(self, lease: Any, tracker: _ProductionCleanupProbe) -> None:
        self._lease = lease
        self._tracker = tracker

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lease, name)

    async def close(self) -> Any:
        self._tracker._record_runtime(self._lease)
        return await self._lease.close()


class _TrackedVerifierLease:
    __slots__ = ("_lease", "_snapshot_id", "_tracker")

    def __init__(
        self,
        lease: Any,
        snapshot_id: str,
        tracker: _ProductionCleanupProbe,
    ) -> None:
        self._lease = lease
        self._snapshot_id = snapshot_id
        self._tracker = tracker

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lease, name)

    async def close(self) -> Any:
        self._tracker._record_runtime(self._lease)
        return await self._tracker._release_snapshot(
            self._snapshot_id, self._lease.close
        )


class _ProductionSandboxRuntime:
    """Owned production boundary recording every sandbox resource allocation."""

    __slots__ = ("_manager", "_tracker")

    def __init__(
        self, manager: Any, tracker: _ProductionCleanupProbe
    ) -> None:
        self._manager = manager
        self._tracker = tracker

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)

    async def open(self, request: Any) -> _TrackedPrimaryLease:
        lease = await self._manager.open(request)
        self._tracker._record_runtime(lease)
        return _TrackedPrimaryLease(lease, self._tracker)

    async def open_verifier(
        self, primary: Any, snapshot: Any
    ) -> _TrackedVerifierLease:
        primary_lease = (
            primary._lease
            if type(primary) is _TrackedPrimaryLease
            else primary
        )
        snapshot_value = self._manager._snapshots.get(snapshot.snapshot_id)
        if snapshot_value is not None:
            self._tracker._episode_artifacts[snapshot.snapshot_id] = Path(
                snapshot_value[1]
            )
        lease = await self._manager.open_verifier(primary_lease, snapshot)
        self._tracker._record_runtime(lease)
        return _TrackedVerifierLease(
            lease, snapshot.snapshot_id, self._tracker
        )

    async def reconcile_stale(self) -> Any:
        return await self._manager.reconcile_stale()

    async def close(self) -> Any:
        return await self._manager.close()

    def abort_bootstrap(self) -> None:
        self._manager.abort_bootstrap()


def _non_repeating_close_callback(
    callback: Callable[[], Any],
    *,
    retry_cleanup: Callable[[], Any] | None = None,
) -> Callable[[], Awaitable[None]]:
    owner: asyncio.Task[None] | None = None
    completed = False
    failed = False

    async def invoke(value: Callable[[], Any]) -> None:
        result = value()
        if hasattr(result, "__await__"):
            await result

    async def close_once() -> None:
        nonlocal completed, failed, owner
        if completed:
            if failed and retry_cleanup is not None:
                await invoke(retry_cleanup)
            return
        if owner is None:
            owner = asyncio.create_task(invoke(callback))
        try:
            await asyncio.shield(owner)
        except asyncio.CancelledError:
            raise
        except BaseException:
            completed = True
            failed = True
            raise
        completed = True

    return close_once


class ProductionComposition:
    def __init__(
        self,
        *,
        app: Any,
        service: BreadBoardV2EpisodeService,
        server: ServerV1,
        manifest: ComposedHarnessManifestV1,
        manifest_ref: str,
        authority_graph: AuthorityGraph,
        bridge_lifecycle: OuterBridgeLifecycle | None,
        cleanup_probe: _ProductionCleanupProbe,
        runtime_close_callbacks: Sequence[Any],
        authority_close_callbacks: Sequence[Any],
    ) -> None:
        self.app = app
        self.service = service
        self.server = server
        self.manifest = manifest
        self.manifest_ref = manifest_ref
        self.authority_graph = authority_graph
        self._bridge_lifecycle = bridge_lifecycle
        self._cleanup_probe = cleanup_probe
        self._runtime_callbacks = list(runtime_close_callbacks)
        self._authority_callbacks = list(authority_close_callbacks)
        self._lock = asyncio.Lock()
        self._runtime_close_lock = asyncio.Lock()
        self._authority_close_lock = asyncio.Lock()
        self._runtime_closed = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def outer_bridge_lease(self) -> OuterBridgeLeaseV1 | None:
        lifecycle = self._bridge_lifecycle
        return None if lifecycle is None else lifecycle.lease

    @property
    def prebound_service_sockets(
        self,
    ) -> Mapping[str, PreboundServiceSocketLeaseV1]:
        lifecycle = self._bridge_lifecycle
        return MappingProxyType({}) if lifecycle is None else lifecycle.service_sockets

    @property
    def tls_callback_socket_lease(
        self,
    ) -> PreboundServiceSocketLeaseV1 | None:
        return self.prebound_service_sockets.get("callback_tls")

    @property
    def outer_bridge_cleanup_receipt(
        self,
    ) -> OuterBridgeCleanupReceiptV1 | None:
        lifecycle = self._bridge_lifecycle
        return None if lifecycle is None else lifecycle.cleanup_receipt

    def observe_cleanup_inventory(self) -> ProductionCleanupInventory:
        if not self._runtime_closed:
            raise RuntimeError(
                "cleanup inventory is unavailable before runtime close"
            )
        return self._cleanup_probe.observe()


    async def close_runtime(self) -> None:
        async with self._runtime_close_lock:
            while True:
                async with self._lock:
                    if self._runtime_closed:
                        return
                    if not self._runtime_callbacks:
                        self._runtime_closed = True
                        return
                    callback = self._runtime_callbacks[-1]
                try:
                    result = callback()
                    if hasattr(result, "__await__"):
                        await result
                except BaseException as exc:
                    raise BaseExceptionGroup(
                        "production runtime close failed", [exc]
                    ) from exc
                async with self._lock:
                    if (
                        self._runtime_callbacks
                        and self._runtime_callbacks[-1] is callback
                    ):
                        self._runtime_callbacks.pop()


    async def close(self) -> None:
        async with self._lock:
            task = self._close_task
            if task is None:
                task = asyncio.create_task(self._close_owner())
                self._close_task = task
        try:
            await _await_composition_close(task)
        finally:
            if task.done() and not task.cancelled() and task.exception() is not None:
                async with self._lock:
                    if self._close_task is task:
                        self._close_task = None

    async def _close_owner(self) -> None:
        try:
            await self.close_runtime()
        except BaseException as exc:
            raise BaseExceptionGroup(
                "production composition close failed", [exc]
            ) from exc
        async with self._authority_close_lock:
            while True:
                async with self._lock:
                    if self._closed:
                        return
                    if not self._authority_callbacks:
                        self._closed = True
                        return
                    callback = self._authority_callbacks[-1]
                try:
                    result = callback()
                    if hasattr(result, "__await__"):
                        await result
                except BaseException as exc:
                    raise BaseExceptionGroup(
                        "production composition close failed", [exc]
                    ) from exc
                async with self._lock:
                    if (
                        self._authority_callbacks
                        and self._authority_callbacks[-1] is callback
                    ):
                        self._authority_callbacks.pop()


async def _await_composition_close(task: asyncio.Task[None]) -> None:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
    failure: BaseException | None = None
    try:
        task.result()
    except BaseException as exc:
        failure = exc
    if cancellation is not None:
        if failure is not None:
            raise BaseExceptionGroup(
                "production composition close cancelled and failed",
                [cancellation, failure],
            )
        raise cancellation
    if failure is not None:
        raise failure


def _secure_read(
    path: str,
    *,
    expected_size: int | None = None,
    expected_digest: str | None = None,
    secret: bool = False,
) -> tuple[bytes, int]:
    _absolute(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
            raise ValueError("unsafe file identity")
        if secret:
            if (
                before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o400
                or not 1 <= before.st_size <= 8192
            ):
                raise ValueError("unsafe secret file")
        elif before.st_size > _MAX_AUTHORITY_BYTES:
            raise ValueError("authority file exceeds bound")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("file changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ValueError("file grew during read")
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("file changed during read")
        data = b"".join(chunks)
        if expected_size is not None and len(data) != expected_size:
            raise ValueError("authority size mismatch")
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if expected_digest is not None and digest != expected_digest:
            raise ValueError("authority digest mismatch")
        return data, fd
    except BaseException:
        os.close(fd)
        raise


def _validate_secret(data: bytes, purpose: str) -> bytes:
    if purpose in {"callback_tls_private_key", "evidence_receipt_signing_key"}:
        if (
            len(data) > 8192
            or b"\x00" in data
            or b"\r" in data
            or not data.startswith(b"-----BEGIN PRIVATE KEY-----\n")
            or not data.endswith(b"\n-----END PRIVATE KEY-----\n")
            or data.count(b"-----BEGIN PRIVATE KEY-----") != 1
            or data.count(b"-----END PRIVATE KEY-----") != 1
        ):
            raise ValueError("invalid private signing key")
        return data
    if data.endswith(b"\n"):
        data = data[:-1]
    if not data or b"\x00" in data or b"\r" in data or b"\n" in data:
        raise ValueError("invalid secret content")
    if purpose in {"api_bearer", "policy_callback"}:
        try:
            data.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("HTTP secret must be UTF-8") from exc
    if (
        purpose in {"receipt_signer", "callback_observation_signing_key"}
        and len(data) < 32
    ):
        raise ValueError("signing key is too short")
    return data


def _read_ref(ref: ArtifactFileRefV1, *, canonical_json: bool = True) -> bytes:
    data, fd = _secure_read(
        ref.path, expected_size=ref.size_bytes, expected_digest=ref.sha256
    )
    os.close(fd)
    if canonical_json:
        _load_json_exact(data)
    return data


def _projection_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _measure_installed_runtime(runtime: InstalledRuntime) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(runtime.executable_path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("installed runtime executable is not regular")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        current = os.stat(runtime.executable_path, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) or "sha256:" + digest.hexdigest() != runtime.measured_binary_digest:
            raise ValueError("installed runtime executable authority mismatch")
    finally:
        os.close(fd)


def _validate_installed_registry_graph(
    installed: InstalledV1,
    registries: c.RegistrySnapshotSet,
    receipts: Sequence[c.AdmissionReceipt],
    evidence_bindings: Sequence[EvidenceRoleBindingV2],
) -> None:
    capabilities = tuple(item.effective_capabilities for item in receipts)
    reachable_runners = {
        (
            item.runner.adapter_id,
            item.runner.runtime_abi,
            item.runner.implementation_digest,
        )
        for item in capabilities
    }
    runtime_records = {
        item.binding.runtime_id: item.binding for item in registries.sandbox_runtimes
    }
    verifier_records = {item.grant.verifier_id: item for item in registries.verifiers}
    reachable_runtime_ids = {item.sandbox.runtime_id for item in capabilities}
    reachable_images = {
        (item.sandbox.image_digest, item.sandbox.runtime_id) for item in capabilities
    }
    reachable_security = {item.sandbox.security_policy_digest for item in capabilities}
    reachable_network = {item.sandbox.network_policy_digest for item in capabilities}
    reachable_verifiers = {item.verifier.verifier_id for item in capabilities}
    reachable_roles = {
        role for item in capabilities for role in item.artifacts.allowed_roles
    }
    for verifier_id in reachable_verifiers:
        verifier_record = verifier_records.get(verifier_id)
        if verifier_record is None:
            raise ValueError("reachable verifier registry authority is missing")
        verifier_runtime = runtime_records.get(verifier_record.runtime_id)
        if verifier_runtime is None:
            raise ValueError("reachable verifier runtime authority is missing")
        if (
            verifier_runtime.runtime_class != verifier_record.runtime_class
            or verifier_runtime.security_policy_digest
            != verifier_record.security_policy_digest
        ):
            raise ValueError("reachable verifier runtime binding is inconsistent")
        reachable_runtime_ids.add(verifier_record.runtime_id)
        reachable_images.add(
            (verifier_runtime.image_digest, verifier_record.runtime_id)
        )
        reachable_security.add(verifier_runtime.security_policy_digest)
        reachable_network.add(verifier_runtime.network_policy_digest)
    if {item.role for item in evidence_bindings} != reachable_roles:
        raise ValueError("installed evidence role authority is incomplete")
    evidence_records = {
        (item.policy.policy_id, item.policy.revision_digest)
        for item in registries.evidence_policies
    }
    retention_records = {
        (item.grant.policy.policy_id, item.grant.policy.revision_digest)
        for item in registries.retention_policies
    }
    if any(
        (item.evidence.policy_id, item.evidence.revision_digest) not in evidence_records
        or (item.retention.policy_id, item.retention.revision_digest)
        not in retention_records
        for item in capabilities
    ):
        raise ValueError("reachable evidence or retention authority is missing")

    runner_records = {
        (
            item.grant.adapter_id,
            item.grant.runtime_abi,
            item.grant.implementation_digest,
        )
        for item in registries.runners
    }
    runner_installed = {
        (item.adapter_id, item.runtime_abi, item.implementation_digest)
        for item in installed.runner_adapters
    }
    if runner_installed != reachable_runners or not runner_installed <= runner_records:
        raise ValueError("installed runner registry is not exactly reachable")
    images = {(item.image_digest, item.runtime_id) for item in installed.images}
    registry_images = {
        (item.image_digest, item.runtime_id) for item in registries.images
    }
    if {item.runtime_id for item in installed.runtimes} != reachable_runtime_ids:
        raise ValueError("installed runtime authority is not exactly reachable")
    if images != reachable_images or not images <= registry_images:
        raise ValueError("installed image authority is not exactly reachable")
    if {
        item.policy_digest for item in installed.security_policies
    } != reachable_security:
        raise ValueError("installed security policy authority is not exactly reachable")
    if {item.policy_digest for item in installed.network_policies} != reachable_network:
        raise ValueError("installed network policy authority is not exactly reachable")
    for runtime in installed.runtimes:
        binding = runtime_records.get(runtime.runtime_id)
        if binding is None or (
            binding.runtime_class,
            binding.driver_implementation_digest,
            binding.runtime_binary_digest,
        ) != (
            runtime.runtime_class,
            runtime.driver_implementation_digest,
            runtime.measured_binary_digest,
        ):
            raise ValueError("installed runtime registry authority mismatch")
        if (
            binding.security_policy_digest not in reachable_security
            or binding.network_policy_digest not in reachable_network
            or (binding.image_digest, runtime.runtime_id) not in images
        ):
            raise ValueError("installed sandbox binding is incomplete")
        _measure_installed_runtime(runtime)
    if {item.grant.verifier_id for item in installed.verifiers} != reachable_verifiers:
        raise ValueError("installed verifier authority is not exactly reachable")
    for verifier in installed.verifiers:
        record = verifier_records.get(verifier.grant.verifier_id)
        if record is None or (
            record.grant,
            record.runtime_id,
            record.runtime_class,
            record.security_policy_digest,
        ) != (
            verifier.grant,
            verifier.runtime_id,
            verifier.runtime_class,
            verifier.security_policy_digest,
        ):
            raise ValueError("installed verifier registry authority mismatch")


def _verify_config_bundle_cas(
    cas: FilesystemCAS,
    bundles: Mapping[str, ConfigBundleManifest],
    compiled_manifests: Mapping[str, bytes],
) -> None:
    parsed_manifests = tuple(
        CompiledConfigManifest.from_json(payload)
        for payload in compiled_manifests.values()
    )
    compiled_bundle_digests = {
        compiled.inputs.bundle_digest for compiled in parsed_manifests
    }
    if compiled_bundle_digests != set(bundles):
        raise ValueError("compiled manifest config bundle set mismatch")
    for compiled in parsed_manifests:
        bundle = bundles[compiled.inputs.bundle_digest]
        bundled_paths = {entry.logical_path for entry in bundle.entries}
        member_paths = tuple(
            item.logical_path
            for item in compiled.source_dependencies
            if item.logical_path in bundled_paths
        )
        edge_values: list[DependencyEdge] = []
        edge_ordinals: dict[tuple[str, str], int] = {}
        for item in compiled.source_dependencies:
            if item.from_logical_path is None or item.raw_reference is None:
                continue
            key = (item.from_logical_path, item.dependency_kind)
            ordinal = edge_ordinals.get(key, 0)
            edge_ordinals[key] = ordinal + 1
            edge_values.append(
                DependencyEdge(
                    from_path=item.from_logical_path,
                    kind=item.dependency_kind,
                    raw_ref=item.raw_reference,
                    logical_path=item.logical_path,
                    ordinal=ordinal,
                )
            )
        edges = tuple(edge_values)
        external = tuple(
            ClosureMember(
                logical_path=item.logical_path,
                artifact_id=item.blob_digest,
                blob_digest=item.blob_digest,
                size_bytes=item.size_bytes,
                media_type=item.media_type,
                source="external",
            )
            for item in compiled.source_dependencies
            if item.logical_path not in bundled_paths
        )
        for member in external:
            ref = cas.get_ref(member.artifact_id)
            if (
                ref.sha256 != member.blob_digest
                or ref.size_bytes != member.size_bytes
                or ref.media_type != member.media_type
            ):
                raise ValueError("external closure CAS member authority mismatch")
            cas.get_bytes(ref, max_bytes=member.size_bytes)
        entrypoint = next(
            (
                item.name
                for item in bundle.entrypoints
                if item.logical_path == compiled.inputs.entrypoint
            ),
            None,
        )
        if entrypoint is None:
            raise ValueError("compiled entrypoint is absent from config bundle")
        closure = build_dependency_closure(
            bundle,
            root_entrypoint=entrypoint,
            member_paths=member_paths,
            edges=edges,
            external_members=external,
        )
        if closure.closure_digest != compiled.inputs.closure_digest:
            raise ValueError("compiled dependency closure authority mismatch")
        for entry in bundle.entries:
            ref = cas.get_ref(entry.artifact_id)
            if (
                ref.sha256 != entry.blob_digest
                or ref.size_bytes != entry.size_bytes
                or ref.media_type != entry.media_type
            ):
                raise ValueError("config bundle CAS member authority mismatch")
            cas.get_bytes(ref, max_bytes=entry.size_bytes)


class _DirectoryIdentityGuard:
    def __init__(self, fd: int, path: str, label: str) -> None:
        self._fd = fd
        self._path = path
        self._label = label

    def check(self) -> None:
        expected = os.fstat(self._fd)
        current = os.stat(self._path, follow_symlinks=False)
        if (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError(f"{self._label} runtime directory authority changed")

    def check_empty(self) -> None:
        self.check()
        with os.scandir(self._path) as entries:
            if next(entries, None) is not None:
                raise ValueError(f"{self._label} contains unadmitted profile content")


class _PinnedDirectoryStorageBackend(DirectoryStorageBackend):
    def __init__(self, guard: _DirectoryIdentityGuard) -> None:
        super().__init__()
        self._guard = guard

    def allocate(self, **kwargs: Any) -> Any:
        self._guard.check()
        return super().allocate(**kwargs)

    def measure(self, backing: Any) -> Mapping[str, Any]:
        self._guard.check()
        return super().measure(backing)

    def release(self, backing: Any) -> None:
        self._guard.check()
        super().release(backing)

    def verify_absent(self, backing: Any) -> bool:
        self._guard.check()
        return super().verify_absent(backing)


class _PinnedMaterializationStore(FilesystemMaterializationStore):
    def __init__(
        self,
        *args: Any,
        authority_guard: _DirectoryIdentityGuard,
        workspace_guard: _DirectoryIdentityGuard,
        **kwargs: Any,
    ) -> None:
        self._authority_guard = authority_guard
        self._workspace_guard = workspace_guard
        super().__init__(*args, **kwargs)

    def materialize(self, plan: Any) -> Any:
        self._authority_guard.check()
        return super().materialize(plan)

    def release(self, workspace: Any) -> Any:
        self._authority_guard.check()
        return super().release(workspace)

    def _check_filesystems(self) -> None:
        self._authority_guard.check()
        self._workspace_guard.check()

    def recover_stale_cache_holder(self, record: Mapping[str, Any]) -> Any:
        self._check_filesystems()
        return super().recover_stale_cache_holder(record)

    def verify_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        self._check_filesystems()
        return super().verify_snapshot(*args, **kwargs)

    def copy_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        self._check_filesystems()
        return super().copy_snapshot(*args, **kwargs)

    def seal_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        self._check_filesystems()
        return super().seal_snapshot(*args, **kwargs)


class _PinnedTrustedProcessBackend(TrustedProcessBackend):
    def __init__(self, guard: _DirectoryIdentityGuard) -> None:
        self._guard = guard

    async def launch(self, *args: Any, **kwargs: Any) -> Any:
        self._guard.check_empty()
        return await super().launch(*args, **kwargs)

    async def reconcile(self, record: Mapping[str, Any]) -> Any:
        self._guard.check_empty()
        return await super().reconcile(record)


class _PinnedSandboxRuntimeManager(SandboxRuntimeManager):
    def __init__(
        self, *args: Any, authority_guard: _DirectoryIdentityGuard, **kwargs: Any
    ) -> None:
        self._authority_guard = authority_guard
        super().__init__(*args, **kwargs)

    async def open(self, request: Any) -> Any:
        self._authority_guard.check()
        return await super().open(request)

    async def open_verifier(self, primary: Any, snapshot: Any) -> Any:
        self._authority_guard.check()
        return await super().open_verifier(primary, snapshot)

    async def reconcile_stale(self) -> Any:
        self._authority_guard.check()
        return await super().reconcile_stale()

    async def close(self) -> Any:
        self._authority_guard.check()
        return await super().close()


class _BootstrapRollback:
    def __init__(self) -> None:
        self._callbacks: list[Any] = []

    def own(self, callback: Any) -> None:
        self._callbacks.append(callback)

    def attempt(self, action: Any) -> Any:
        try:
            return action()
        except BaseException as primary:
            failures: list[BaseException] = []
            for callback in reversed(self._callbacks):
                try:
                    callback()
                except BaseException as cleanup:
                    failures.append(cleanup)
            self._callbacks.clear()
            if failures:
                raise ExceptionGroup(
                    "production bootstrap and rollback failed", [primary, *failures]
                )
            raise

    def transfer(self) -> tuple[Any, ...]:
        callbacks = tuple(self._callbacks)
        self._callbacks.clear()
        return callbacks


def _private_daemon_authority(
    value: PrivateDockerDaemonAuthorityV1,
) -> PrivateDockerDaemonAuthority:
    def file(item: PinnedFileAuthorityV1) -> PinnedFileAuthority:
        return PinnedFileAuthority(
            path=item.path,
            digest=item.digest,
            owner_uid=item.owner_uid,
            mode=item.mode,
            executable=item.executable,
        )

    return PrivateDockerDaemonAuthority(
        daemon_instance_id=value.daemon_instance_id,
        dockerd=file(value.dockerd),
        docker=file(value.docker),
        runc=file(value.runc),
        containerd=file(value.containerd),
        config_path=value.config_path,
        socket_path=value.socket_path,
        pid_file=value.pid_file,
        data_root=value.data_root,
        exec_root=value.exec_root,
        mount_stage_root=value.mount_stage_root,
        containerd_socket_path=value.containerd_socket_path,
        containerd_root=value.containerd_root,
        containerd_state=value.containerd_state,
        log_root=value.log_root,
        log_limit_bytes=value.log_limit_bytes,
        storage_driver=value.storage_driver,
        runtime_name=value.runtime_name,
        images=tuple(
            OfflineImageAuthority(
                archive=file(item.archive),
                image_id=item.image_id,
                source_image_digest=item.source_image_digest,
            )
            for item in value.images
        ),
    )


def _build_runtime_graph(
    *,
    manifest: HarnessCompositionManifestV1,
    authority: AuthorityBundleV1,
    graph: AuthorityGraph,
    pinned: Mapping[str, _PinnedSecret],
    directory_fds: Mapping[str, int],
    admission_receipts: Sequence[c.AdmissionReceipt],
    composition_digest: str,
    prebound_service_socket_fds: Mapping[str, int],
    fault_injection_authority: V2FaultInjectionAuthority | None,
    policy_client_resolver_factory: PolicyClientResolverFactory | None,
) -> tuple[
    Any,
    BreadBoardV2EpisodeService,
    Sequence[Any],
    OuterBridgeLifecycle | None,
    _ProductionCleanupProbe,
]:
    _validate_installed_registry_graph(
        manifest.installed,
        authority.registries,
        admission_receipts,
        manifest.evidence_bindings,
    )

    def revalidate_directory(name: str, path: str) -> None:
        pinned_stat = os.fstat(directory_fds[name])
        current = os.stat(path, follow_symlinks=False)
        if (pinned_stat.st_dev, pinned_stat.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError(f"{name} runtime directory authority changed")

    for name in (
        "locator",
        "materialization_cache",
        "workspace",
        "lease",
        "security_profile",
    ):
        revalidate_directory(name, getattr(manifest.stores, name).path)
    installed = InstalledSandboxAuthoritySet(
        runtimes=manifest.installed.runtimes,
        images=manifest.installed.images,
        security_policies=manifest.installed.security_policies,
        network_policies=manifest.installed.network_policies,
        verifiers=manifest.installed.verifiers,
    )
    adapters = []
    for descriptor in manifest.installed.runner_adapters:
        if descriptor.adapter_id == CONDUCTOR_ADAPTER_ID:
            adapter = ConductorAdapter(descriptor.runtime_abi)
        elif descriptor.adapter_id == TERMINAL_ADAPTER_ID:
            adapter = TerminalResponsesAdapter(descriptor.runtime_abi)
        else:
            raise ValueError("runner adapter is not installed by the closed switch")
        if adapter.descriptor != descriptor:
            raise ValueError("runner adapter descriptor mismatch")
        adapters.append(adapter)
    runner_registry = RunnerAdapterRegistry(adapters)

    source_reader = _CASMaterializationSourceReader(graph.cas)
    cache_guard = _DirectoryIdentityGuard(
        directory_fds["materialization_cache"],
        manifest.stores.materialization_cache.path,
        "materialization_cache",
    )
    workspace_guard = _DirectoryIdentityGuard(
        directory_fds["workspace"], manifest.stores.workspace.path, "workspace"
    )
    rollback = _BootstrapRollback()
    materialization = rollback.attempt(
        lambda: _PinnedMaterializationStore(
            cache_root=manifest.stores.materialization_cache.path,
            workspace_root=manifest.stores.workspace.path,
            source_reader=source_reader,
            clock=graph.clock,
            lease_ttl=timedelta(seconds=manifest.stores.lease_ttl_seconds),
            storage_backend=_PinnedDirectoryStorageBackend(workspace_guard),
            random_bytes=token_bytes,
            authority_guard=cache_guard,
            workspace_guard=workspace_guard,
            cache_root_fd=directory_fds["materialization_cache"],
            workspace_root_fd=directory_fds["workspace"],
        )
    )
    rollback.own(materialization.close)
    rollback.attempt(
        lambda: revalidate_directory(
            "materialization_cache", str(materialization.cache_root)
        )
    )
    rollback.attempt(
        lambda: revalidate_directory("workspace", str(materialization.workspace_root))
    )
    lease_guard = _DirectoryIdentityGuard(
        directory_fds["lease"], manifest.stores.lease.path, "lease"
    )
    security_guard = _DirectoryIdentityGuard(
        directory_fds["security_profile"],
        manifest.stores.security_profile.path,
        "security_profile",
    )
    rollback.attempt(security_guard.check_empty)
    hardened = tuple(
        runtime
        for runtime in manifest.installed.runtimes
        if runtime.runtime_class
        in {
            c.RuntimeClass.HARDENED_DOCKER,
            c.RuntimeClass.HARDENED_GVISOR,
        }
    )
    private_daemon_owner: MountNamespaceBroker | None = None
    docker_adapter: DockerRuntimeAdapter | None = None
    docker_backend: DockerSandboxBackend | None = None
    bridge_lifecycle: OuterBridgeLifecycle | None = None
    if hardened:
        daemon_authority = manifest.installed.private_docker_daemon
        if daemon_authority is None:
            raise ValueError("private Docker daemon authority is missing")
        private_authority = _private_daemon_authority(daemon_authority)
        journal_name = "supervisor-journal"
        try:
            os.mkdir(
                journal_name,
                mode=0o700,
                dir_fd=directory_fds["lease"],
            )
            os.fsync(directory_fds["lease"])
        except FileExistsError:
            pass
        journal_fd = os.open(
            journal_name,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fds["lease"],
        )
        try:
            journal_metadata = os.fstat(journal_fd)
            if (
                not stat.S_ISDIR(journal_metadata.st_mode)
                or journal_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(journal_metadata.st_mode) != 0o700
            ):
                raise ValueError("supervisor journal authority is not sealed")
            rollback.attempt(
                lambda: recover_supervisor_journals(
                    journal_fd,
                    directory_fds["lease"],
                    authenticator=graph.authenticator,
                )
            )
            private_daemon_owner = rollback.attempt(
                lambda: MountNamespaceBroker(
                    private_authority.mount_stage_root,
                    daemon_authority=private_authority,
                    journal_root_fd=journal_fd,
                    journal_root_path=str(
                        Path(manifest.stores.lease.path) / journal_name
                    ),
                    journal_authenticator=graph.authenticator,
                )
            )
        finally:
            os.close(journal_fd)
        rollback.own(private_daemon_owner.close)
        daemon_binding = private_daemon_owner.daemon_binding
        if daemon_binding is None:
            raise ValueError("private Docker broker daemon binding is missing")
        if manifest.outer_bridge_plan is not None:
            bridge_lifecycle = rollback.attempt(
                lambda: OuterBridgeLifecycle(
                    broker=private_daemon_owner,
                    composition_digest=composition_digest,
                    plan=manifest.outer_bridge_plan,
                    authenticator=graph.authenticator,
                    lease_ttl_seconds=manifest.stores.lease_ttl_seconds,
                    prebound_service_socket_plans=(
                        manifest.prebound_service_socket_plans
                    ),
                    prebound_service_socket_fds=prebound_service_socket_fds,
                )
            )
            rollback.attempt(bridge_lifecycle.start)
            rollback.own(bridge_lifecycle.close)
        docker_adapter = rollback.attempt(
            lambda: DockerRuntimeAdapter(
                executor=private_daemon_owner.docker_cli_executor,
                cli_environment=(),
                mechanics_invocation=private_daemon_owner.docker_invocation,
                daemon_binding=daemon_binding,
            )
        )
        rollback.own(docker_adapter.close)
        docker_backend = rollback.attempt(
            lambda: DockerSandboxBackend(
                adapter=docker_adapter,
                measurement_provider=InspectDockerMeasurementProvider(),
                security_profile_root=manifest.stores.security_profile.path,
                mount_stager=private_daemon_owner,
            )
        )
        rollback.own(docker_backend.close)
    if manifest.outer_bridge_plan is not None and bridge_lifecycle is None:
        raise ValueError("outer bridge plan requires one private Docker daemon")
    if manifest.outer_bridge_plan is None and prebound_service_socket_fds:
        raise ValueError("loopback composition cannot consume prebound sockets")
    sandbox_manager = rollback.attempt(
        lambda: _PinnedSandboxRuntimeManager(
            registries=authority.registries,
            installed_authorities=installed,
            materialization_store=materialization,
            lease_root=manifest.stores.lease.path,
            process_backend=_PinnedTrustedProcessBackend(security_guard),
            docker_backend=docker_backend,
            random_bytes=token_bytes,
            authority_guard=lease_guard,
            lease_root_fd=directory_fds["lease"],
        )
    )
    rollback.own(sandbox_manager.abort_bootstrap)
    rollback.attempt(
        lambda: revalidate_directory("lease", str(sandbox_manager.lease_root))
    )
    cleanup_probe = _ProductionCleanupProbe(
        manifest=manifest,
        materialization=materialization,
        sandbox_runtime=sandbox_manager,
        broker=private_daemon_owner,
        pinned=pinned,
        directory_fds=directory_fds,
    )
    sandbox_runtime = _ProductionSandboxRuntime(
        sandbox_manager, cleanup_probe
    )

    evidence_authority = rollback.attempt(
        lambda: V2EvidenceAuthority(manifest.evidence_bindings)
    )

    routes = {item.grant.route_id: item for item in graph.policy_http.routes}
    observations = dict(graph.policy_capabilities.attested_observations)
    dns = {item.dns_policy_digest: item for item in graph.policy_http.dns_policies}
    ips = {item.ip_policy_digest: item for item in graph.policy_http.ip_policies}
    network_authorities = {
        route_id: RouteNetworkAuthority(
            route_id=route_id,
            dns_policy_digest=route.dns_policy_digest,
            ip_policy_digest=route.ip_policy_digest,
            hostname=dns[route.dns_policy_digest].hostname,
            allowed_ip_addresses=ips[route.ip_policy_digest].allowed_addresses,
            allow_loopback=ips[route.ip_policy_digest].allow_loopback,
            allow_private=ips[route.ip_policy_digest].allow_private,
            allow_link_local=ips[route.ip_policy_digest].allow_link_local,
            allow_multicast=ips[route.ip_policy_digest].allow_multicast,
            allow_unspecified=ips[route.ip_policy_digest].allow_unspecified,
        )
        for route_id, route in routes.items()
    }
    secret_authorities = {
        item.handle_id: PolicySecretAuthority(
            handle_id=item.handle_id,
            handle_version_digest=item.handle_version_digest,
            scope_digest=item.scope_digest,
            route_ids=item.route_ids,
        )
        for item in graph.policy_http.secret_bindings
    }
    tls_authorities = {
        item.route_id: PolicyTlsTrustAuthority(
            route_id=item.route_id,
            server_name=item.server_name,
            ca_bundle_sha256=item.ca_bundle_ref.sha256,
            ca_pem=graph.tls_ca_pem_by_route[item.route_id],
            expected_leaf_certificate_sha256=item.expected_leaf_certificate_sha256,
            minimum_tls_version=item.minimum_tls_version,
            cipher_suite=item.cipher_suite,
            dedicated_single_leaf_ca=item.dedicated_single_leaf_ca,
        )
        for item in graph.tls_trust
    }
    credentials = {
        spec.handle_id: pinned[spec.handle_id].data.decode("utf-8")
        for spec in manifest.secret_handles.records
        if spec.purpose == "policy_callback"
    }
    authority_policy_resolver = rollback.attempt(
        lambda: RouteBoundPolicyHttpResolver(
            registry_revision_digest=graph.policy_http.registry_revision_digest,
            routes=routes,
            observations=observations,
            attestations={
                item.attestation_digest: item
                for item in graph.registries.policy_capability_attestations
            },
            tls_authorities=tls_authorities,
            network_authorities=network_authorities,
            secret_authorities=secret_authorities,
            credentials=credentials,
            timeout_seconds=manifest.server.request_timeout_seconds,
        )
    )
    if policy_client_resolver_factory is None:
        policy_resolver: ManagedPolicyRuntimeClientResolver = (
            authority_policy_resolver
        )
    else:
        try:
            policy_resolver = policy_client_resolver_factory(
                authority_policy_resolver
            )
            if not all(
                callable(getattr(policy_resolver, name, None))
                for name in ("resolve", "close", "abort_bootstrap")
            ):
                raise TypeError(
                    "policy client resolver factory returned an unmanaged resolver"
                )
        except BaseException:
            authority_policy_resolver.abort_bootstrap()
            raise
    rollback.own(policy_resolver.abort_bootstrap)
    locator = rollback.attempt(
        lambda: FilesystemEpisodeLocatorStore(
            manifest.stores.locator.path, root_fd=directory_fds["locator"]
        )
    )
    rollback.own(locator.close)
    rollback.attempt(lambda: revalidate_directory("locator", str(locator.root)))
    try:
        evidence_repository = EpisodeEvidenceRepository(
            graph.cas, locator, clock=graph.clock.current
        )
        dependencies = V2LifecycleDependencies(
            config_runtime=graph.config_runtime,
            runner_registry=runner_registry,
            sandbox_runtime=sandbox_runtime,
            policy_client_resolver=policy_resolver,
            evidence_repository=evidence_repository,
            evidence_authority=evidence_authority,
            clock=graph.clock.current,
            fault_injection_authority=fault_injection_authority,
        )
        service = BreadBoardV2EpisodeService(dependencies)
        api_specs = [
            item
            for item in manifest.secret_handles.records
            if item.purpose == "api_bearer"
        ]
        if len(api_specs) != 1:
            raise ValueError("exactly one API bearer authority is required")
        api_token = pinned[api_specs[0].handle_id].data.decode("utf-8")
        app = create_app(
            service,
            auth_token=api_token,
            allow_unauthenticated_loopback=False,
        )
    except BaseException as exc:
        rollback.attempt(lambda error=exc: (_ for _ in ()).throw(error))
        raise AssertionError("unreachable")
    async def retry_service_cleanup() -> None:
        receipts = await sandbox_manager.close()
        if any(
            receipt.state.value not in {"released", "already_released"}
            for receipt in receipts
        ):
            raise RuntimeError("sandbox runtime cleanup pending")


    rollback.transfer()
    return (
        app,
        service,
        (
            materialization.close,
            locator.close,
            policy_resolver.close,
            *(() if private_daemon_owner is None else (private_daemon_owner.close,)),
            *(() if bridge_lifecycle is None else (bridge_lifecycle.close,)),
            *(() if docker_adapter is None else (docker_adapter.close,)),
            *(
                ()
                if docker_backend is None
                else (docker_backend.close_runtime,)
            ),
            _non_repeating_close_callback(
                service.close,
                retry_cleanup=retry_service_cleanup,
            ),
        ),
        bridge_lifecycle,
        cleanup_probe,
    )


def load_production_composition(
    composition_ref_path: str,
    secret_files: Mapping[str, str],
    *,
    composition_ref_data: bytes | None = None,
    prebound_service_socket_fds: Mapping[str, int] | None = None,
    fault_injection_authority: V2FaultInjectionAuthority | None = None,
    policy_client_resolver_factory: PolicyClientResolverFactory | None = None,
) -> ProductionComposition:
    composition_ref_path = _absolute(composition_ref_path)
    if composition_ref_data is None:
        ref_data, ref_fd = _secure_read(composition_ref_path)
        os.close(ref_fd)
    else:
        if type(composition_ref_data) is not bytes:
            raise TypeError("composition_ref_data must be exact bytes")
        if len(composition_ref_data) > _MAX_AUTHORITY_BYTES:
            raise ValueError("composition ref exceeds bound")
        ref_data = composition_ref_data
    _load_json_exact(ref_data)
    ref_schema = json.loads(ref_data).get("schema_version")
    if ref_schema == "bb.rl.harness-composition-ref.v1":
        ref: CompositionRefV1 | CompositionRefV2 = CompositionRefV1.model_validate_json(
            ref_data, strict=True
        )
    elif ref_schema == "bb.rl.harness-composition-ref.v2":
        ref = CompositionRefV2.model_validate_json(ref_data, strict=True)
    else:
        raise ValueError("unsupported composition ref schema")
    manifest_data, manifest_fd = _secure_read(
        ref.manifest_path,
        expected_size=ref.manifest_size_bytes,
        expected_digest=ref.manifest_sha256,
    )
    os.close(manifest_fd)
    _load_json_exact(manifest_data)
    manifest_schema = json.loads(manifest_data).get("schema_version")
    if (
        type(ref) is CompositionRefV1
        and manifest_schema == "bb.rl.harness-composition.v1"
    ):
        manifest: HarnessCompositionManifestV1 | HarnessCompositionManifestV2 = (
            HarnessCompositionManifestV1.model_validate_json(manifest_data, strict=True)
        )
    elif (
        type(ref) is CompositionRefV2
        and manifest_schema == "bb.rl.harness-composition.v2"
    ):
        manifest = HarnessCompositionManifestV2.model_validate_json(
            manifest_data, strict=True
        )
    else:
        raise ValueError("composition ref and manifest schema versions differ")
    socket_fds = MappingProxyType(dict(prebound_service_socket_fds or {}))
    supplied = dict(secret_files)
    required = {item.handle_id for item in manifest.secret_handles.records}
    if set(supplied) != required:
        raise ValueError("secret handle set mismatch")
    if len(set(supplied.values())) != len(supplied):
        raise ValueError("secret file reuse is forbidden")
    pinned: dict[str, _PinnedSecret] = {}
    cas: FilesystemCAS | None = None
    directory_fds: dict[str, int] = {}
    manifest_authority_fds: dict[str, int] = {}
    try:
        host_runtime = manifest.host_runtime_authority
        if host_runtime is not None:
            _read_ref(host_runtime.build_report_ref)
            python = host_runtime.python_executable
            _data, python_fd = _secure_read(python.path, expected_digest=python.digest)
            manifest_authority_fds["host_runtime_python"] = python_fd
            python_metadata = os.fstat(python_fd)
            python_current = os.stat(python.path, follow_symlinks=False)
            if (
                not stat.S_ISREG(python_metadata.st_mode)
                or python_metadata.st_uid != python.owner_uid
                or stat.S_IMODE(python_metadata.st_mode) != python.mode
                or not python.executable
                or (python_metadata.st_dev, python_metadata.st_ino)
                != (python_current.st_dev, python_current.st_ino)
            ):
                raise ValueError("host runtime Python executable authority mismatch")
        openssl = manifest.openssl_authority
        if openssl is not None:
            _data, openssl_fd = _secure_read(
                openssl.path,
                expected_size=openssl.size_bytes,
                expected_digest=openssl.sha256,
            )
            manifest_authority_fds["openssl"] = openssl_fd
            metadata = os.fstat(openssl_fd)
            current = os.stat(openssl.path, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_ctime_ns,
                    metadata.st_size,
                    metadata.st_uid,
                    stat.S_IMODE(metadata.st_mode),
                )
                != (
                    openssl.device,
                    openssl.inode,
                    int(openssl.ctime_ns),
                    openssl.size_bytes,
                    openssl.owner_uid,
                    openssl.mode,
                )
                or (metadata.st_dev, metadata.st_ino)
                != (current.st_dev, current.st_ino)
            ):
                raise ValueError("OpenSSL executable authority mismatch")
            version = subprocess.run(
                (openssl.path, "version"),
                executable=f"/proc/{os.getpid()}/fd/{openssl_fd}",
                pass_fds=(openssl_fd,),
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
            if (
                version.returncode != 0
                or "sha256:" + hashlib.sha256(version.stdout).hexdigest()
                != openssl.version_stdout_sha256
                or version.stdout.decode("utf-8", "strict").strip() != openssl.version
            ):
                raise ValueError("OpenSSL version authority mismatch")
            _read_ref(openssl.discovery_report_ref)
        tls_callback = manifest.tls_callback_runtime_input
        if tls_callback is not None:
            ca_pem = _read_ref(tls_callback.ca_certificate_ref, canonical_json=False)
            leaf_pem = _read_ref(
                tls_callback.leaf_certificate_ref, canonical_json=False
            )
            for label, pem in (("CA", ca_pem), ("leaf", leaf_pem)):
                if (
                    not pem.startswith(b"-----BEGIN CERTIFICATE-----\n")
                    or not pem.endswith(b"-----END CERTIFICATE-----\n")
                    or pem.count(b"-----BEGIN CERTIFICATE-----") != 1
                    or pem.count(b"-----END CERTIFICATE-----") != 1
                ):
                    raise ValueError(
                        f"TLS callback {label} certificate is not canonical PEM"
                    )
        evidence_signer = manifest.evidence_receipt_signing_authority
        if evidence_signer is not None:
            public_key_pem = _read_ref(
                evidence_signer.public_key_ref, canonical_json=False
            )
            if (
                not public_key_pem.startswith(b"-----BEGIN PUBLIC KEY-----\n")
                or not public_key_pem.endswith(b"-----END PUBLIC KEY-----\n")
                or public_key_pem.count(b"-----BEGIN PUBLIC KEY-----") != 1
                or public_key_pem.count(b"-----END PUBLIC KEY-----") != 1
            ):
                raise ValueError("evidence receipt public key is not canonical PEM")
        for spec in manifest.secret_handles.records:
            try:
                data, fd = _secure_read(
                    _absolute(supplied[spec.handle_id]), secret=True
                )
            except (OSError, ValueError):
                raise ValueError(
                    f"secret handle {spec.handle_id!r} is unavailable or unsafe"
                ) from None
            try:
                validated = _validate_secret(data, spec.purpose)
            except BaseException:
                os.close(fd)
                raise
            pinned[spec.handle_id] = _PinnedSecret(fd, validated)
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for name, directory in manifest.stores.model_dump().items():
            if not isinstance(directory, dict):
                continue
            fd = os.open(directory["path"], directory_flags)
            directory_fds[name] = fd
            current = os.fstat(fd)
            actual = (
                current.st_dev,
                current.st_ino,
                current.st_uid,
                f"0{stat.S_IMODE(current.st_mode):03o}",
            )
            expected = (
                directory["device"],
                directory["inode"],
                directory["owner_uid"],
                directory["mode"],
            )
            if not stat.S_ISDIR(current.st_mode) or actual != expected:
                raise ValueError("directory authority mismatch")
        cas = FilesystemCAS(manifest.stores.cas.path)
        cas_root = os.fstat(cas._root_fd)
        expected_cas = manifest.stores.cas
        if (cas_root.st_dev, cas_root.st_ino) != (
            expected_cas.device,
            expected_cas.inode,
        ):
            raise ValueError("CAS reopened a different directory authority")

        authority_bytes = _read_ref(manifest.authority_bundle_ref)
        authority = AuthorityBundleV1.model_validate_json(authority_bytes, strict=True)
        manifest_policy_routes = {
            item.handle_id: item.route_ids
            for item in manifest.secret_handles.records
            if item.purpose == "policy_callback"
        }
        graph_policy_routes = {
            item.handle_id: item.route_ids
            for item in authority.policy_http.secret_bindings
        }
        registry_policy_routes = {
            item.grant.handle_id: item.route_ids
            for item in authority.registries.secret_handles
            if item.grant.handle_id in manifest_policy_routes
        }
        if not (
            manifest_policy_routes == graph_policy_routes == registry_policy_routes
        ):
            raise ValueError("policy secret route authority cross-reference mismatch")
        registry_secrets = {
            item.grant.handle_id: item for item in authority.registries.secret_handles
        }
        for binding in authority.policy_http.secret_bindings:
            record = registry_secrets.get(binding.handle_id)
            if (
                record is None
                or record.grant.handle_version_digest != binding.handle_version_digest
                or record.grant.scope_digest != binding.scope_digest
            ):
                raise ValueError("policy secret identity authority mismatch")
        bundle_refs = (
            (manifest.config_bundle_ref,)
            if type(manifest) is HarnessCompositionManifestV1
            else manifest.config_bundle_refs
        )
        config_bundles: dict[str, ConfigBundleManifest] = {}
        for bundle_ref in bundle_refs:
            config_bytes = _read_ref(bundle_ref)
            config_bundle = ConfigBundleManifest.from_json(config_bytes)
            if config_bundle.canonical_bytes() != config_bytes:
                raise ValueError("config bundle is not canonical")
            if config_bundle.bundle_digest in config_bundles:
                raise ValueError("config bundle semantic digest is duplicated")
            config_bundles[config_bundle.bundle_digest] = config_bundle
        admitted_bytes = _read_ref(manifest.admitted_set_ref)
        admitted_set = c.AdmittedSetManifest.model_validate_json(
            admitted_bytes, strict=True
        )
        control_documents = (
            (
                manifest.control_plane.admission_policy_ref,
                authority.admission_policy.canonical_bytes(),
                "admission policy",
            ),
            (
                manifest.control_plane.registry_snapshot_ref,
                authority.registries.canonical_bytes(),
                "registry snapshot",
            ),
            (
                manifest.control_plane.revocation_snapshot_ref,
                _canonical_bytes(
                    [item.model_dump(mode="json") for item in authority.revocations]
                ),
                "revocation snapshot",
            ),
            (
                manifest.control_plane.policy_capability_snapshot_ref,
                _canonical_bytes(
                    [
                        item.model_dump(mode="json")
                        for item in authority.policy_capabilities
                    ]
                ),
                "policy capability snapshot",
            ),
        )
        for document_ref, expected_bytes, label in control_documents:
            if _read_ref(document_ref) != expected_bytes:
                raise ValueError(f"{label} authority cross-reference mismatch")
        if (
            admitted_set.admission_policy_digest
            != authority.admission_policy.canonical_digest()
        ):
            raise ValueError("admitted set policy authority mismatch")
        if (
            admitted_set.registry_snapshot_digest
            != authority.registries.digests.snapshot_digest
        ):
            raise ValueError("admitted set registry authority mismatch")
        revocations = {item.scope_digest: item for item in authority.revocations}
        if (
            revocations.get(admitted_set.revocation.scope_digest)
            != admitted_set.revocation
        ):
            raise ValueError("admitted set revocation authority mismatch")

        direct_selectors = tuple(
            c.DirectSelector.model_validate_json(_read_ref(item), strict=True)
            for item in manifest.selector_catalog.direct
        )
        weighted_selectors = tuple(
            c.ConfigSetManifest.model_validate_json(_read_ref(item), strict=True)
            for item in manifest.selector_catalog.weighted
        )
        admitted_root = admitted_set.canonical_digest()
        if any(
            item.admitted_set_root != admitted_root
            for item in (*direct_selectors, *weighted_selectors)
        ):
            raise ValueError("selector admitted-set cross-reference mismatch")
        compiled_manifests: dict[str, bytes] = {}
        for member_ref in authority.compiled_manifest_refs:
            if (
                member_ref.media_type
                != _ARTIFACT_MEDIA_TYPES[c.ArtifactKind.COMPILED_MANIFEST]
            ):
                raise ValueError("compiled manifest media type mismatch")
            compiled_manifests[member_ref.sha256] = _read_ref(member_ref)
        expected_compiler = manifest.control_plane.compiler
        for payload in compiled_manifests.values():
            compiler = CompiledConfigManifest.from_json(payload).compiler
            actual_identity = (
                compiler.compiler_id,
                compiler.compiler_version,
                compiler.compiler_code_digest,
                compiler.config_schema_digest,
                compiler.manifest_schema_digest,
                compiler.canonicalizer_id,
                compiler.runtime_abi,
            )
            declared_identity = (
                expected_compiler.compiler_id,
                expected_compiler.semantic_version,
                expected_compiler.code_digest,
                expected_compiler.source_schema_digest,
                expected_compiler.manifest_schema_digest,
                expected_compiler.canonicalizer_id,
                expected_compiler.runtime_abi,
            )
            if actual_identity != declared_identity:
                raise ValueError("compiler authority identity mismatch")
        _verify_config_bundle_cas(cas, config_bundles, compiled_manifests)
        admission_receipts: dict[str, bytes] = {}
        for member_ref in authority.admission_receipt_refs:
            if (
                member_ref.media_type
                != _ARTIFACT_MEDIA_TYPES[c.ArtifactKind.ADMISSION_RECEIPT]
            ):
                raise ValueError("admission receipt media type mismatch")
            payload = _read_ref(member_ref)
            receipt = c.AdmissionReceipt.model_validate_json(payload, strict=True)
            if receipt.canonical_bytes() != payload:
                raise ValueError("admission receipt is not canonical")
            admission_receipts[member_ref.sha256] = payload
        if set(admission_receipts) != set(admitted_set.receipt_digests):
            raise ValueError("admitted set receipt membership mismatch")
        referenced_compiled = {
            c.AdmissionReceipt.model_validate_json(
                payload, strict=True
            ).compiled.manifest_digest
            for payload in admission_receipts.values()
        }
        if referenced_compiled != set(compiled_manifests):
            raise ValueError("compiled manifest membership mismatch")

        tls_ca_pem: dict[str, bytes] = {}
        for tls in authority.tls_trust:
            pem = _read_ref(tls.ca_bundle_ref, canonical_json=False)
            if (
                not pem.startswith(b"-----BEGIN CERTIFICATE-----\n")
                or not pem.endswith(b"-----END CERTIFICATE-----\n")
                or pem.count(b"-----BEGIN CERTIFICATE-----") != 1
            ):
                raise ValueError("TLS CA must be one canonical PEM certificate")
            tls_ca_pem[tls.route_id] = pem
        receipt_handle = manifest.control_plane.receipt_authenticator.secret_handle_id
        graph = _build_authority_graph(
            cas=cas,
            policy=authority.admission_policy,
            registries=authority.registries,
            revocations=authority.revocations,
            policy_capabilities=authority.policy_capabilities,
            admitted_set=admitted_set,
            direct_selectors=direct_selectors,
            weighted_selectors=weighted_selectors,
            compiled_manifests=compiled_manifests,
            admission_receipts=admission_receipts,
            policy_http=authority.policy_http,
            tls_trust=authority.tls_trust,
            tls_ca_pem_by_route=tls_ca_pem,
            receipt_key_id=manifest.control_plane.receipt_authenticator.key_id,
            receipt_key=pinned[receipt_handle].data,
        )
        selector_refs = (
            *manifest.selector_catalog.direct,
            *manifest.selector_catalog.weighted,
        )
        composed = ComposedHarnessManifestV1(
            schema_version="bb.rl.harness-composed.v1",
            composition_id=manifest.composition_id,
            input_manifest_digest=ref.manifest_sha256,
            authority_bundle_digest=manifest.authority_bundle_ref.sha256,
            config_bundle_digest=(
                manifest.config_bundle_ref.sha256
                if type(manifest) is HarnessCompositionManifestV1
                else _projection_digest(
                    [ref.sha256 for ref in manifest.config_bundle_refs]
                )
            ),
            admitted_set_digest=manifest.admitted_set_ref.sha256,
            selector_digests=tuple(sorted(item.sha256 for item in selector_refs)),
            admission_policy_digest=authority.admission_policy.canonical_digest(),
            registry_snapshot_digest=authority.registries.digests.snapshot_digest,
            revocation_state_digests=tuple(
                sorted(item.state_digest for item in authority.revocations)
            ),
            compiler_identity=manifest.control_plane.compiler,
            installed_authority_digest=_projection_digest(
                manifest.installed.model_dump(mode="json")
            ),
            runner_registry_digest=_projection_digest(
                [asdict(item) for item in manifest.installed.runner_adapters]
            ),
            evidence_authority_digest=_projection_digest(
                [asdict(item) for item in manifest.evidence_bindings]
            ),
            store_authority_digests=tuple(
                _projection_digest(item)
                for key, item in sorted(manifest.stores.model_dump(mode="json").items())
                if key != "lease_ttl_seconds"
            ),
            server_authority_digest=_projection_digest(
                manifest.server.model_dump(mode="json")
            ),
            outer_bridge_plan_digest=_projection_digest(
                None
                if manifest.outer_bridge_plan is None
                else manifest.outer_bridge_plan.model_dump(mode="json")
            ),
            openssl_authority_digest=_projection_digest(
                None
                if manifest.openssl_authority is None
                else manifest.openssl_authority.model_dump(mode="json")
            ),
            host_runtime_authority_digest=_projection_digest(
                None
                if manifest.host_runtime_authority is None
                else manifest.host_runtime_authority.model_dump(mode="json")
            ),
            tls_callback_runtime_input_digest=_projection_digest(
                None
                if manifest.tls_callback_runtime_input is None
                else manifest.tls_callback_runtime_input.model_dump(mode="json")
            ),
            evidence_receipt_signing_authority_digest=_projection_digest(
                None
                if manifest.evidence_receipt_signing_authority is None
                else manifest.evidence_receipt_signing_authority.model_dump(mode="json")
            ),
            secret_handle_ids=tuple(sorted(required)),
            receipt_key_id=manifest.control_plane.receipt_authenticator.key_id,
            receipt_algorithm="hmac-sha256-v1",
        )
        composed_bytes = composed.canonical_bytes()
        published = cas.put_bytes(composed_bytes, media_type=COMPOSED_MEDIA_TYPE)
        if cas.get_bytes(published, max_bytes=len(composed_bytes)) != composed_bytes:
            raise ValueError("composed manifest CAS readback mismatch")
        (
            app,
            service,
            runtime_callbacks,
            bridge_lifecycle,
            cleanup_probe,
        ) = _build_runtime_graph(
            manifest=manifest,
            authority=authority,
            graph=graph,
            pinned=pinned,
            directory_fds=directory_fds,
            admission_receipts=tuple(
                c.AdmissionReceipt.model_validate_json(payload, strict=True)
                for payload in admission_receipts.values()
            ),
            composition_digest=ref.manifest_sha256,
            prebound_service_socket_fds=socket_fds,
            fault_injection_authority=fault_injection_authority,
            policy_client_resolver_factory=policy_client_resolver_factory,
        )
        return ProductionComposition(
            app=app,
            service=service,
            server=manifest.server,
            manifest=composed,
            manifest_ref=published.sha256,
            authority_graph=graph,
            bridge_lifecycle=bridge_lifecycle,
            cleanup_probe=cleanup_probe,
            runtime_close_callbacks=runtime_callbacks,
            authority_close_callbacks=[
                *(lambda fd=fd: os.close(fd) for fd in directory_fds.values()),
                *(lambda fd=fd: os.close(fd) for fd in manifest_authority_fds.values()),
                cas.close,
                *(item.close for item in pinned.values()),
            ],
        )
    except BaseException:
        if cas is not None:
            cas.close()
        for item in pinned.values():
            item.close()
        for fd in manifest_authority_fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        for fd in directory_fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        raise


__all__ = [
    "ArtifactFileRefV1",
    "CASConfigRuntimeStore",
    "AuthorityGraph",
    "AuthorityBundleV1",
    "ComposedHarnessManifestV1",
    "DNSPolicyDocumentV1",
    "DockerNetworkLabelV1",
    "CallbackJournalVerificationReceiptV1",
    "EvidenceReceiptSignatureV1",
    "EvidenceReceiptSigningAuthorityV1",
    "EvidenceReceiptSigningHandoff",
    "HmacSha256ReceiptAuthenticator",
    "HostRuntimeAuthorityV1",
    "OuterBridgeCleanupReceiptV1",
    "OuterBridgeLeaseV1",
    "OuterBridgePlanV1",
    "OuterBridgeLifecycle",
    "PreboundServiceSocketLeaseV1",
    "PreboundServiceSocketPlanV1",
    "OfflineImageAuthorityV1",
    "OpenSslAuthorityV1",
    "TlsCallbackPolicyV1",
    "TlsCallbackRuntimeInputV1",
    "PinnedFileAuthorityV1",
    "PinnedRevocationStore",
    "PinnedServerCompilerAdapter",
    "PrivateDockerDaemonAuthorityV1",
    "DirectoryAuthorityRefV1",
    "HarnessCompositionManifestV1",
    "IPPolicyDocumentV1",
    "PolicyHttpAuthorityGraphV1",
    "PolicyHttpSchemaAuthorityV1",
    "PolicySecretRouteBindingV1",
    "PolicyTlsTrustAuthorityV1",
    "ManagedPolicyRuntimeClientResolver",
    "PolicyClientResolverFactory",
    "ProductionComposition",
    "SecretHandleSpecV1",
    "ServerV1",
    "load_production_composition",
]
