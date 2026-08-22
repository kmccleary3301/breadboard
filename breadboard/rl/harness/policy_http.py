from __future__ import annotations

import asyncio
import collections
import ipaddress
import hashlib
import time
import re
import ssl
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from dataclasses import dataclass
from urllib.parse import urlsplit


from agentic_coder_prototype.compilation.contracts import (
    CanonicalJSONError,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
)
from breadboard.rl.harness.contracts import (
    PolicyBindingRef,
    PolicyCapabilityAttestationRecord,
    PolicyCapabilityObservation,
    RouteRegistryRecord,
)
from breadboard.rl.harness.runners.base import (
    PolicyRuntimeClientPort,
    PolicyRuntimeInvokeRequest,
    PolicyRuntimeInvokeResult,
    freeze_json_object,
)
_SHA256_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")




POLICY_HTTP_PROTOCOL_ABI = "breadboard-policy-http-v1"
POLICY_HTTP_REQUEST_SCHEMA = {
    "schema_version": "bb.rl.policy-http-request-schema.v1",
    "required": (
        "schema_version",
        "episode_id",
        "effective_plan_digest",
        "binding_digest",
        "policy_slot_id",
        "request_digest",
        "request_payload",
        "turn",
        "attempt",
    ),
}
POLICY_HTTP_RESPONSE_SCHEMA = {
    "schema_version": "bb.rl.policy-http-response-schema.v1",
    "required": ("response_digest", "response_payload"),
}
POLICY_HTTP_REQUEST_SCHEMA_DIGEST = canonical_sha256(POLICY_HTTP_REQUEST_SCHEMA)
POLICY_HTTP_RESPONSE_SCHEMA_DIGEST = canonical_sha256(POLICY_HTTP_RESPONSE_SCHEMA)


@dataclass(frozen=True, slots=True)
class RouteNetworkAuthority:
    route_id: str
    dns_policy_digest: str
    ip_policy_digest: str
    hostname: str
    allowed_ip_addresses: tuple[str, ...]
    allow_loopback: bool
    allow_private: bool
    allow_link_local: bool
    allow_multicast: bool
    allow_unspecified: bool

    def __post_init__(self) -> None:
        normalized = tuple(str(ipaddress.ip_address(value)) for value in self.allowed_ip_addresses)
        if not normalized or normalized != tuple(sorted(set(normalized))):
            raise ValueError("allowed policy IP addresses must be canonical, sorted, and unique")
        for address_text in normalized:
            address = ipaddress.ip_address(address_text)
            class_flags = (
                (address.is_loopback, self.allow_loopback),
                (address.is_private and not address.is_loopback, self.allow_private),
                (address.is_link_local, self.allow_link_local),
                (address.is_multicast, self.allow_multicast),
                (address.is_unspecified, self.allow_unspecified),
            )
            if any(actual and not allowed for actual, allowed in class_flags):
                raise ValueError("policy IP address class is not admitted")


@dataclass(frozen=True, slots=True)
class PolicySecretAuthority:
    handle_id: str
    handle_version_digest: str
    scope_digest: str
    route_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.route_ids or self.route_ids != tuple(sorted(set(self.route_ids))):
            raise ValueError("policy secret routes must be sorted, unique, and nonempty")


@dataclass(frozen=True, slots=True)
class PolicyTlsTrustAuthority:
    route_id: str
    server_name: str
    ca_bundle_sha256: str
    ca_pem: bytes
    expected_leaf_certificate_sha256: str
    minimum_tls_version: str
    cipher_suite: str
    dedicated_single_leaf_ca: bool

    def __post_init__(self) -> None:
        if (
            self.minimum_tls_version != "TLSv1.3"
            or self.ca_pem.count(b"-----BEGIN CERTIFICATE-----") != 1
            or self.ca_pem.count(b"-----END CERTIFICATE-----") != 1
            or self.cipher_suite != "TLS_AES_256_GCM_SHA384"
            or self.dedicated_single_leaf_ca is not True
            or "sha256:" + hashlib.sha256(self.ca_pem).hexdigest() != self.ca_bundle_sha256
        ):
            raise ValueError("TLS trust authority is not closed and digest-bound")


@dataclass(frozen=True, slots=True)
class PolicyTlsRouteObservation:
    schema_version: str
    connected_peer_ip: str
    tls_version: str
    cipher: str
    leaf_der_digest: str
    ca_authority_digest: str
    server_name: str
    network_grant_ref: str
    route_digest: str
    request_digest: str
    response_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "bb.rl.policy-tls-route-observation.v1":
            raise ValueError("policy TLS observation schema mismatch")
        for field in ("leaf_der_digest", "ca_authority_digest", "network_grant_ref", "route_digest", "request_digest", "response_digest"):
            value = getattr(self, field)
            if type(value) is not str or _SHA256_REF.fullmatch(value) is None:
                raise ValueError("policy TLS observation digest invalid")


def _parse_http11_response_head(head: bytes, *, max_response_bytes: int) -> tuple[int, int]:
    if len(head) > 65536 or not head.endswith(b"\r\n\r\n"):
        raise PolicyHttpError("policy response headers are invalid", code="response_invalid")
    lines = head[:-4].split(b"\r\n")
    if not lines or any(not line for line in lines):
        raise PolicyHttpError("policy response headers are invalid", code="response_invalid")
    status_parts = lines[0].split(b" ", 2)
    if (
        len(status_parts) < 2
        or status_parts[0] != b"HTTP/1.1"
        or len(status_parts[1]) != 3
        or not status_parts[1].isdigit()
        or not 200 <= int(status_parts[1]) <= 599
        or any(byte < 32 or byte > 126 for byte in lines[0])
    ):
        raise PolicyHttpError("policy response status is invalid", code="response_invalid")
    token = frozenset(b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    headers: dict[bytes, bytes] = {}
    for line in lines[1:]:
        if line[:1] in {b" ", b"\t"} or b":" not in line:
            raise PolicyHttpError("policy response headers are invalid", code="response_invalid")
        name, value = line.split(b":", 1)
        if (
            not name
            or any(byte not in token for byte in name)
            or any(byte < 32 or byte == 127 or byte > 126 for byte in value)
        ):
            raise PolicyHttpError("policy response headers are invalid", code="response_invalid")
        lowered = name.lower()
        if lowered in headers:
            raise PolicyHttpError("policy response headers are duplicated", code="response_invalid")
        headers[lowered] = value.strip(b" ")
    if b"transfer-encoding" in headers:
        raise PolicyHttpError("policy response framing is unsupported", code="response_invalid")
    length = headers.get(b"content-length")
    if length is None or not length.isdigit() or (len(length) > 1 and length.startswith(b"0")):
        raise PolicyHttpError("policy response length is invalid", code="response_invalid")
    declared = int(length)
    if declared > max_response_bytes:
        raise PolicyHttpError("policy response exceeds route limit", code="response_too_large")
    return int(status_parts[1]), declared


class PolicyHttpError(RuntimeError):
    """A closed, redacted failure at the policy HTTP authority boundary."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

def _validated_policy_response(raw: bytes) -> PolicyRuntimeInvokeResult:
    failure: PolicyHttpError | None = None
    result: PolicyRuntimeInvokeResult | None = None
    try:
        value = canonical_json_loads(raw)
        if canonical_json_bytes(value) != raw:
            raise CanonicalJSONError("noncanonical response")
        if type(value) is not dict or set(value) != {"response_payload", "response_digest"}:
            raise ValueError("response shape is not closed")
        response_payload = value["response_payload"]
        if type(response_payload) is not dict:
            raise TypeError("response payload must be an object")
        if value["response_digest"] != canonical_sha256(response_payload):
            raise ValueError("response digest mismatch")
        result = PolicyRuntimeInvokeResult(
            freeze_json_object(response_payload, field_name="policy response"),
            value["response_digest"],
        )
    except Exception:
        failure = PolicyHttpError("policy response is invalid", code="response_invalid")
    if failure is not None:
        raise failure
    if result is None:
        raise PolicyHttpError("policy response is invalid", code="response_invalid")
    return result

class _RouteRateLimiter:
    def __init__(self, maximum: int) -> None:
        self._maximum = maximum
        self._requests: collections.deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._requests and self._requests[0] <= now - 60:
                self._requests.popleft()
            if len(self._requests) >= self._maximum:
                raise PolicyHttpError("policy route rate exceeded", code="rate_limit_exceeded")
            self._requests.append(now)

    def clear(self) -> None:
        self._requests.clear()


class RouteBoundPolicyHttpClient(PolicyRuntimeClientPort):
    def __init__(
        self,
        *,
        route: RouteRegistryRecord,
        observation: PolicyCapabilityObservation,
        network_authority: RouteNetworkAuthority,
        secret_authority: PolicySecretAuthority,
        credential: str,
        episode_id: str,
        rate_limiter: _RouteRateLimiter,
        effective_plan_digest: str,
        tls_authority: PolicyTlsTrustAuthority,
        timeout_seconds: float,
        on_close: Callable[["RouteBoundPolicyHttpClient"], Awaitable[None]] | None = None,
        tls_observation_sink: Callable[[PolicyTlsRouteObservation], Awaitable[None]] | None = None,
    ) -> None:
        if route.grant.route_id != observation.route_id or route.grant.route_revision_digest != observation.route_revision_digest:
            raise PolicyHttpError("policy route authority mismatch", code="route_authority_mismatch")
        if route.grant.credential_handle_id != observation.credential_handle_id:
            raise PolicyHttpError("policy credential authority mismatch", code="credential_authority_mismatch")
        if route.grant.protocol_abi != POLICY_HTTP_PROTOCOL_ABI or observation.protocol_abi != POLICY_HTTP_PROTOCOL_ABI:
            raise PolicyHttpError("policy protocol ABI mismatch", code="protocol_abi_mismatch")
        if route.request_schema_digest != POLICY_HTTP_REQUEST_SCHEMA_DIGEST or route.response_schema_digest != POLICY_HTTP_RESPONSE_SCHEMA_DIGEST:
            raise PolicyHttpError("policy schema authority mismatch", code="schema_authority_mismatch")
        if (
            network_authority.route_id != route.grant.route_id
            or network_authority.dns_policy_digest != route.dns_policy_digest
            or network_authority.ip_policy_digest != route.ip_policy_digest
        ):
            raise PolicyHttpError("policy network authority mismatch", code="network_authority_mismatch")
        if (
            secret_authority.handle_id != route.grant.credential_handle_id
            or route.grant.route_id not in secret_authority.route_ids
            or observation.credential_handle_version_digest != secret_authority.handle_version_digest
            or observation.subject_scope_digest != secret_authority.scope_digest
        ):
            raise PolicyHttpError("policy secret authority mismatch", code="secret_authority_mismatch")
        methods = tuple(item.value for item in route.methods)
        if methods != ("POST",) or len(route.paths) != 1:
            raise PolicyHttpError("policy route must grant one POST path", code="route_shape_unsupported")
        scheme = route.scheme.value
        if scheme != "https":
            raise PolicyHttpError("policy route must use HTTPS", code="route_scheme_unsupported")
        parsed_authority = urlsplit(f"//{route.authority}")
        try:
            literal_address = ipaddress.ip_address(parsed_authority.hostname or "")
        except ValueError as exc:
            raise PolicyHttpError("hostname routes require a pinned transport", code="unpinned_dns_route") from exc
        normalized_address = str(literal_address)
        if normalized_address not in network_authority.allowed_ip_addresses:
            raise PolicyHttpError("policy endpoint IP is not admitted", code="ip_not_admitted")
        if literal_address.is_loopback and not network_authority.allow_loopback:
            raise PolicyHttpError("policy loopback endpoint is forbidden", code="ip_class_forbidden")
        if tls_authority.route_id != route.grant.route_id or tls_authority.server_name != network_authority.hostname:
            raise PolicyHttpError("policy TLS authority mismatch", code="tls_authority_mismatch")
        self._ssl_context: ssl.SSLContext
        self._tls_authority = tls_authority
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        try:
            context.load_verify_locations(cadata=tls_authority.ca_pem.decode("ascii"))
        except (UnicodeDecodeError, ssl.SSLError):
            raise PolicyHttpError("policy TLS trust is invalid", code="tls_authority_mismatch") from None
        self._ssl_context = context
        self._route = route
        self._network_authority = network_authority
        self._observation = observation
        self._episode_id = episode_id
        self._effective_plan_digest = effective_plan_digest
        self._url = f"{scheme}://{route.authority}{route.paths[0]}"
        self._credential = credential
        self._timeout_seconds = timeout_seconds
        self._active: asyncio.Task[tuple[int, bytes]] | None = None
        self._lock = asyncio.Lock()
        self._active_done: asyncio.Event | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._rate_limiter = rate_limiter
        self._on_close = on_close
        self._tls_observation_sink = tls_observation_sink

    def observe(self) -> PolicyCapabilityObservation:
        return self._observation

    async def invoke(self, request: PolicyRuntimeInvokeRequest) -> PolicyRuntimeInvokeResult:
        if request.episode_id != self._episode_id or request.effective_plan_digest != self._effective_plan_digest:
            raise PolicyHttpError("policy request identity mismatch", code="request_identity_mismatch")
        payload = {
            "schema_version": "bb.rl.policy-http-request.v1",
            "episode_id": request.episode_id,
            "effective_plan_digest": request.effective_plan_digest,
            "binding_digest": request.binding_digest,
            "policy_slot_id": request.policy_slot_id,
            "request_digest": request.request_digest,
            "request_payload": dict(request.request_payload),
            "turn": request.turn,
            "attempt": request.attempt,
        }
        try:
            body = canonical_json_bytes(payload)
        except CanonicalJSONError as exc:
            raise PolicyHttpError("policy request is not canonical JSON", code="request_invalid") from exc
        if len(body) > self._route.max_request_bytes:
            raise PolicyHttpError("policy request exceeds route limit", code="request_too_large")
        async with self._lock:
            if self._close_task is not None:
                raise PolicyHttpError("policy client is closed", code="client_closed")
            if self._active is not None:
                raise PolicyHttpError("policy request already active", code="request_in_flight")
            await self._rate_limiter.acquire()
            task = asyncio.create_task(self._perform_request(body))
            self._active = task
            done = asyncio.Event()
            self._active_done = done
        failure: PolicyHttpError | None = None
        result: tuple[int, bytes] | None = None
        try:
            result = await task
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            failure = PolicyHttpError("policy callback timed out", code="callback_timeout")
        except (OSError, ssl.SSLError):
            failure = PolicyHttpError("policy callback transport failed", code="callback_transport_failed")
        finally:
            async with self._lock:
                if self._active is task:
                    self._active = None
                done.set()
                self._active_done = None
        if failure is not None:
            raise failure
        if result is None:
            raise PolicyHttpError("policy callback failed", code="callback_transport_failed")
        status_code, raw = result
        if status_code != 200:
            raise PolicyHttpError("policy callback rejected request", code="callback_status_invalid")
        return _validated_policy_response(raw)

    async def _perform_request(self, body: bytes) -> tuple[int, bytes]:
        async with asyncio.timeout(self._timeout_seconds):
            return await self._perform_pinned_https(body)
    async def _perform_pinned_https(self, body: bytes) -> tuple[int, bytes]:
        if self._ssl_context is None:
            raise PolicyHttpError("policy TLS authority is unavailable", code="tls_authority_mismatch")
        authority = urlsplit(f"//{self._route.authority}")
        host = authority.hostname
        port = authority.port or 443
        if host is None:
            raise PolicyHttpError("policy endpoint authority is invalid", code="network_authority_mismatch")
        reader, writer = await asyncio.open_connection(
            host,
            port,
            ssl=self._ssl_context,
            server_hostname=self._tls_authority.server_name,
            limit=65536,
        )
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            peer = writer.get_extra_info("peername")
            if ssl_object is None or not peer:
                raise PolicyHttpError("policy TLS peer is unavailable", code="tls_peer_mismatch")
            peer_address = str(ipaddress.ip_address(peer[0]))
            if peer_address not in self._network_authority.allowed_ip_addresses:
                raise PolicyHttpError("connected policy peer is not admitted", code="ip_not_admitted")
            peer_certificate = ssl_object.getpeercert()
            subject_alt_names = peer_certificate.get("subjectAltName", ()) if type(peer_certificate) is dict else ()
            if ("IP Address", host) not in subject_alt_names:
                raise PolicyHttpError("policy TLS literal IP SAN mismatch", code="tls_peer_mismatch")
            certificate = ssl_object.getpeercert(binary_form=True)
            tls_version = ssl_object.version()
            cipher = ssl_object.cipher()
            if (
                not certificate
                or "sha256:" + hashlib.sha256(certificate).hexdigest()
                != self._tls_authority.expected_leaf_certificate_sha256
                or not cipher
                or cipher[0] != self._tls_authority.cipher_suite
                or tls_version != self._tls_authority.minimum_tls_version
            ):
                raise PolicyHttpError("policy TLS peer mismatch", code="tls_peer_mismatch")
            request_head = (
                f"POST {self._route.paths[0]} HTTP/1.1\r\n"
                f"Host: {self._route.authority}\r\n"
                f"Authorization: Bearer {self._credential}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(request_head + body)
            await writer.drain()
            header_failure = False
            try:
                head = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                header_failure = True
                head = b""
            if header_failure:
                raise PolicyHttpError("policy response headers are invalid", code="response_invalid")
            status_code, declared = _parse_http11_response_head(
                head, max_response_bytes=self._route.max_response_bytes
            )
            body_failure = False
            try:
                payload = await reader.readexactly(declared)
            except asyncio.IncompleteReadError:
                body_failure = True
                payload = b""
            if body_failure:
                raise PolicyHttpError("policy response body is truncated", code="response_invalid")
            if await reader.read(1):
                raise PolicyHttpError("policy response has surplus bytes", code="response_invalid")
            validated_response = _validated_policy_response(payload)
            request_carrier = canonical_json_loads(body)
            if type(request_carrier) is not dict:
                raise PolicyHttpError("policy request carrier is invalid", code="request_invalid")
            if self._tls_observation_sink is not None:
                await self._tls_observation_sink(
                    PolicyTlsRouteObservation(
                        schema_version="bb.rl.policy-tls-route-observation.v1",
                        connected_peer_ip=peer_address,
                        tls_version=tls_version,
                        cipher=cipher[0],
                        leaf_der_digest="sha256:" + hashlib.sha256(certificate).hexdigest(),
                        ca_authority_digest=self._tls_authority.ca_bundle_sha256,
                        server_name=self._tls_authority.server_name,
                        network_grant_ref=self._network_authority.ip_policy_digest,
                        route_digest=self._route.grant.route_revision_digest,
                        request_digest=request_carrier["request_digest"],
                        response_digest=validated_response.response_digest,
                    )
                )
            return status_code, payload
        finally:
            writer.close()
            await writer.wait_closed()


    async def cancel(self, reason: str) -> None:
        del reason
        async with self._lock:
            task = self._active
            done = self._active_done
            if task is not None:
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if done is not None:
            await done.wait()

    async def close(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._close_impl())
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _close_impl(self) -> None:
        async with self._lock:
            task = self._active
            done = self._active_done
            if task is not None:
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if done is not None:
            await done.wait()
        self._credential = ""
        if self._on_close is not None:
            await self._on_close(self)




class RouteBoundPolicyHttpResolver:
    def __init__(
        self,
        *,
        registry_revision_digest: str,
        routes: Mapping[str, RouteRegistryRecord],
        observations: Mapping[str, PolicyCapabilityObservation],
        attestations: Mapping[str, PolicyCapabilityAttestationRecord],
        tls_authorities: Mapping[str, PolicyTlsTrustAuthority],
        network_authorities: Mapping[str, RouteNetworkAuthority],
        secret_authorities: Mapping[str, PolicySecretAuthority],
        credentials: Mapping[str, str],
        timeout_seconds: float,
        tls_observation_sink: Callable[[PolicyTlsRouteObservation], Awaitable[None]] | None = None,
    ) -> None:
        if any(type(route) is not RouteRegistryRecord for route in routes.values()):
            raise PolicyHttpError("policy route record is not exact", code="route_authority_mismatch")
        if any(type(observation) is not PolicyCapabilityObservation for observation in observations.values()):
            raise PolicyHttpError("policy observation is not exact", code="attestation_authority_mismatch")
        if any(type(attestation) is not PolicyCapabilityAttestationRecord for attestation in attestations.values()):
            raise PolicyHttpError("policy attestation record is not exact", code="attestation_authority_mismatch")
        try:
            validated_routes = {
                route_id: RouteRegistryRecord.model_validate(route.model_dump(mode="json"))
                for route_id, route in routes.items()
            }
            validated_observations = {
                digest: PolicyCapabilityObservation.model_validate(observation.model_dump(mode="json"))
                for digest, observation in observations.items()
            }
            validated_attestations = {
                digest: PolicyCapabilityAttestationRecord.model_validate(
                    attestation.model_dump(mode="json")
                )
                for digest, attestation in attestations.items()
            }
        except (TypeError, ValueError) as exc:
            raise PolicyHttpError("policy authority record is invalid", code="authority_invalid") from exc
        if any(route_id != route.grant.route_id for route_id, route in validated_routes.items()):
            raise PolicyHttpError("policy route map identity mismatch", code="route_authority_mismatch")
        if set(validated_attestations) != set(validated_observations):
            raise PolicyHttpError("policy attestation and observation sets differ", code="attestation_authority_mismatch")
        if len({observation.canonical_digest() for observation in validated_observations.values()}) != len(validated_observations):
            raise PolicyHttpError("policy observation joins are not unique", code="attestation_authority_mismatch")
        for digest, attestation in validated_attestations.items():
            observation = validated_observations[digest]
            if (
                digest != attestation.attestation_digest
                or attestation.route_id != observation.route_id
                or attestation.route_revision_digest != observation.route_revision_digest
                or attestation.model_digest != observation.model_digest
                or attestation.tokenizer_digest != observation.tokenizer_digest
                or attestation.checkpoint_digest != observation.checkpoint_digest
                or attestation.capability_digest != observation.capability_digest
                or attestation.revocation != observation.revocation
                or attestation.validity != observation.provenance.validity
                or observation.provenance.signer_key_id not in attestation.authorized_signer_key_ids
            ):
                raise PolicyHttpError("policy attestation observation join mismatch", code="attestation_authority_mismatch")
        self._registry_revision_digest = registry_revision_digest
        self._routes = validated_routes
        self._observations = validated_observations
        self._attestations = validated_attestations
        self._network_authorities = dict(network_authorities)
        self._tls_authorities = dict(tls_authorities)
        self._secret_authorities = dict(secret_authorities)
        self._credentials = dict(credentials)
        self._timeout_seconds = timeout_seconds
        self._rate_limiters = {
            route_id: _RouteRateLimiter(route.max_requests_per_minute)
            for route_id, route in validated_routes.items()
        }
        self._clients: set[RouteBoundPolicyHttpClient] = set()
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._tls_observation_sink = tls_observation_sink
        self._bootstrap_aborted = False

    def abort_bootstrap(self) -> None:
        if self._clients or self._close_task is not None:
            raise RuntimeError("cannot abort policy resolver after runtime admission")
        self._bootstrap_aborted = True
        self._credentials.clear()
        self._rate_limiters.clear()

    async def resolve(self, policy_binding: PolicyBindingRef, *, episode_id: str, effective_plan_digest: str) -> RouteBoundPolicyHttpClient:
        async with self._lock:
            if self._close_task is not None or self._bootstrap_aborted:
                raise PolicyHttpError("policy resolver is closed", code="resolver_closed")
            if policy_binding.registry_revision_digest != self._registry_revision_digest:
                raise PolicyHttpError("policy registry revision mismatch", code="registry_revision_mismatch")
            route = self._routes.get(policy_binding.route_id)
            observation = self._observations.get(policy_binding.attestation_digest)
            if route is None or observation is None or observation.route_id != policy_binding.route_id:
                raise PolicyHttpError("policy binding is not admitted", code="binding_not_admitted")
            if observation.registry_revision_digest != self._registry_revision_digest:
                raise PolicyHttpError("policy observation registry mismatch", code="registry_revision_mismatch")
            credential = self._credentials.get(route.grant.credential_handle_id)
            network = self._network_authorities.get(route.grant.route_id)
            secret = self._secret_authorities.get(route.grant.credential_handle_id)
            tls = self._tls_authorities.get(route.grant.route_id)
            if credential is None:
                raise PolicyHttpError("policy credential is not installed", code="credential_not_installed")
            if network is None or secret is None or tls is None:
                raise PolicyHttpError("policy route authority is not installed", code="authority_not_installed")
            client = RouteBoundPolicyHttpClient(
                route=route,
                tls_authority=tls,
                observation=observation,
                rate_limiter=self._rate_limiters[route.grant.route_id],
                network_authority=network,
                secret_authority=secret,
                credential=credential,
                episode_id=episode_id,
                effective_plan_digest=effective_plan_digest,
                timeout_seconds=self._timeout_seconds,
                on_close=self._deregister,
                tls_observation_sink=self._tls_observation_sink,
            )
            self._clients.add(client)
            return client

    async def _deregister(self, client: RouteBoundPolicyHttpClient) -> None:
        async with self._lock:
            self._clients.discard(client)

    async def close(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._close_impl())
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _close_impl(self) -> None:
        async with self._lock:
            clients = tuple(self._clients)
        results = await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        failures = [item for item in results if isinstance(item, Exception)]
        self._credentials.clear()
        for limiter in self._rate_limiters.values():
            limiter.clear()
        if failures:
            raise ExceptionGroup("policy client cleanup failed", failures)
