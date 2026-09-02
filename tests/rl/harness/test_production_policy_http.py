from __future__ import annotations

import asyncio
import hashlib
import ssl
import os
import stat
from breadboard_engine.compilation.contracts import canonical_json_bytes, canonical_sha256
from pathlib import Path

import httpx
import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import _secure_read
from breadboard.rl.harness.policy_http import (
    POLICY_HTTP_PROTOCOL_ABI,
    POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
    POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
    PolicyHttpError,
    PolicySecretAuthority,
    PolicyTlsTrustAuthority,
    PolicyTlsRouteObservation,
    RouteBoundPolicyHttpResolver,
    RouteNetworkAuthority,
)
from breadboard.rl.harness.runners.base import PolicyRuntimeInvokeRequest

D = "sha256:" + "a" * 64


def canonical(value: object) -> bytes:
    return canonical_json_bytes(value)

def _raw_http_response(body: bytes) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )


def digest(value: object) -> str:
    return canonical_sha256(value)


def _attestation(observation):
    values = {
        "route_id": observation.route_id,
        "route_revision_digest": observation.route_revision_digest,
        "model_digest": observation.model_digest,
        "tokenizer_digest": observation.tokenizer_digest,
        "checkpoint_digest": observation.checkpoint_digest,
        "capability_digest": observation.capability_digest,
        "validity": observation.provenance.validity,
        "revocation": observation.revocation,
        "authorized_signer_key_ids": (observation.provenance.signer_key_id,),
        "signature_verification_policy_digest": D,
    }
    provisional = c.PolicyCapabilityAttestationRecord.model_construct(
        **values, attestation_digest=D
    )
    return c.PolicyCapabilityAttestationRecord(
        **values, attestation_digest=provisional.derived_attestation_digest()
    )


def authorities(*, authority: str = "127.0.0.1", max_request: int = 100_000, max_response: int = 100_000, max_requests: int = 60):
    route_values = {
        "scheme": c.RouteScheme.HTTPS,
        "authority": authority,
        "paths": ("/v1/responses",),
        "methods": (c.RouteMethod.POST,),
        "ip_policy_digest": D,
        "dns_policy_digest": D,
        "request_schema_digest": POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
        "response_schema_digest": POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
        "max_request_bytes": max_request,
        "max_response_bytes": max_response,
        "max_requests_per_minute": max_requests,
        "data_classification": c.DataClassification.CONFIDENTIAL,
        "owner": c.RouteOwnerAuthority(owner_id="operator", authority_scope_digest=D),
    }
    provisional = c.RouteRegistryRecord.model_construct(
        grant=c.RouteGrant(
            route_id="policy-route", route_revision_digest=D,
            protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
            credential_handle_id="policy-secret",
        ),
        **route_values,
    )
    route = c.RouteRegistryRecord(
        grant=c.RouteGrant(
            route_id="policy-route",
            route_revision_digest=provisional.derived_route_revision_digest(),
            protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
            credential_handle_id="policy-secret",
        ),
        **route_values,
    )
    capabilities = c.PolicyCapabilityVector(
        responses_protocol=POLICY_HTTP_PROTOCOL_ABI,
        modalities=("text",),
        tool_calling=True,
        parallel_tool_calls=False,
        token_ids=False,
        token_logprobs=False,
        routing_metadata=False,
        cancellation=True,
        max_context_tokens=4096,
        max_output_tokens=1024,
        policy_slot_count=1,
        request_features=(),
    )
    capability_digest = canonical_sha256(
        {
            "schema_version": "bb.rl.policy-selection-capabilities.v1",
            "protocol_abi": POLICY_HTTP_PROTOCOL_ABI,
            "model_digest": D,
            "tokenizer_digest": D,
            "checkpoint_digest": D,
            "capabilities": capabilities.to_canonical_obj(),
        }
    )
    observation = c.PolicyCapabilityObservation(
        registry_revision_digest=D,
        route_id="policy-route",
        route_revision_digest=route.grant.route_revision_digest,
        provider_id="provider",
        protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
        bridge_instance_id="bridge",
        bridge_build_digest=D,
        model_id="model",
        model_digest=D,
        tokenizer_digest=D,
        checkpoint_digest=D,
        credential_handle_id="policy-secret",
        credential_handle_version_digest=D,
        subject_scope_digest=D,
        capabilities=capabilities,
        capability_digest=capability_digest,
        provenance=c.AttestationProvenance(
            kind=c.AttestationKind.STARTUP_PROBE,
            issuer_id="operator",
            signer_key_id="key",
            environment_digest=D,
            evidence_digest=D,
            validity=c.ValidityWindow(
                issued_at="2026-07-10T00:00:00Z",
                not_before="2026-07-10T00:00:00Z",
                expires_at="2026-07-11T00:00:00Z",
            ),
        ),
        revocation=c.RevocationBinding(scope_digest=D, epoch=1, state_digest=D),
    )
    attestation = _attestation(observation)
    binding = c.PolicyBindingRef(
        route_id="policy-route",
        registry_revision_digest=D,
        attestation_digest=attestation.attestation_digest,
    )
    return route, observation, binding


def _resolver(**kwargs):
    routes = kwargs["routes"]
    joined = [
        (_attestation(observation), observation)
        for observation in kwargs["observations"].values()
    ]
    kwargs["observations"] = {
        attestation.attestation_digest: observation
        for attestation, observation in joined
    }
    kwargs.setdefault(
        "attestations",
        {attestation.attestation_digest: attestation for attestation, _ in joined},
    )
    kwargs.setdefault(
        "network_authorities",
        {
            route_id: RouteNetworkAuthority(
                route_id, D, D, "127.0.0.1", ("127.0.0.1",),
                True, False, False, False, False,
            )
            for route_id in routes
        },
    )
    kwargs.setdefault(
        "secret_authorities",
        {"policy-secret": PolicySecretAuthority("policy-secret", D, D, ("policy-route",))},
    )
    ca_pem = Path("tests/fixtures/rl/harness/production_composition/tls/ca.cert.pem").read_bytes()
    kwargs.setdefault(
        "tls_authorities",
        {
            "policy-route": PolicyTlsTrustAuthority(
                "policy-route",
                "127.0.0.1",
                "sha256:" + hashlib.sha256(ca_pem).hexdigest(),
                ca_pem,
                "sha256:00ea6df3ae9eba4a396edd257a7fc16c7abc9d3b89d8a3dff462819828db9337",
                "TLSv1.3",
                "TLS_AES_256_GCM_SHA384",
                True,
            )
        },
    )
    transport = kwargs.pop("transport", None)
    resolver = RouteBoundPolicyHttpResolver(**kwargs)
    if transport is not None:
        original_resolve = resolver.resolve

        async def resolve_with_sealed_test_transport(*args, **resolve_kwargs):
            client = await original_resolve(*args, **resolve_kwargs)

            async def perform(body):
                async with httpx.AsyncClient(
                    transport=transport, trust_env=False
                ) as test_client:
                    async with test_client.stream(
                        "POST",
                        "https://127.0.0.1/v1/responses",
                        content=body,
                        headers={"authorization": f"Bearer {client._credential}"},
                    ) as response:
                        chunks = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > client._route.max_response_bytes:
                                raise PolicyHttpError(
                                    "policy response exceeds route limit",
                                    code="response_too_large",
                                )
                            chunks.append(chunk)
                        return response.status_code, b"".join(chunks)

            client._perform_pinned_https = perform
            return client

        resolver.resolve = resolve_with_sealed_test_transport
    return resolver


def request(payload=None, *, episode_id="episode-1"):
    payload = payload or {"input": "hello"}
    return PolicyRuntimeInvokeRequest(episode_id, D, D, "slot-1", digest(payload), payload, 1, 1)


def _materialize_test_server_key(source: Path, destination: Path) -> Path:
    # Checked-in key bytes are public test material, never a production secret.
    with pytest.raises(ValueError, match="unsafe secret"):
        _secure_read(str(source.resolve()), secret=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    try:
        os.write(fd, source.read_bytes())
        os.fsync(fd)
    finally:
        os.close(fd)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    return destination


@pytest.mark.asyncio
async def test_real_loopback_callback_lifecycle_and_credential(tmp_path):
    seen: list[bytes] = []
    tls_observations: list[PolicyTlsRouteObservation] = []
    async def record_tls(observation: PolicyTlsRouteObservation) -> None:
        tls_observations.append(observation)
    tls_root = Path("tests/fixtures/rl/harness/production_composition/tls")
    runtime_key = _materialize_test_server_key(
        tls_root / "server.key.pem", tmp_path / "server.key.pem"
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_3
    server_context.load_cert_chain(tls_root / "server.cert.pem", runtime_key)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            writer.close()
            await writer.wait_closed()
            return
        length = next(
            int(line.split(b":", 1)[1])
            for line in headers.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        seen.append(headers + await reader.readexactly(length))
        response_payload = {"output": [{"type": "message", "text": "loopback"}]}
        body = canonical({"response_digest": digest(response_payload), "response_payload": response_payload})
        writer.write(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
            + str(len(body)).encode()
            + b"\r\nconnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    route, observation, binding = authorities(authority=f"127.0.0.1:{port}")
    ca_pem = (tls_root / "ca.cert.pem").read_bytes()
    rogue_resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        network_authorities={
            "policy-route": RouteNetworkAuthority(
                "policy-route", D, D, "localhost", ("127.0.0.1",),
                True, False, False, False, False,
            )
        },
        tls_authorities={
            "policy-route": PolicyTlsTrustAuthority(
                "policy-route", "localhost",
                "sha256:" + hashlib.sha256(ca_pem).hexdigest(), ca_pem, D,
                "TLSv1.3", "TLS_AES_256_GCM_SHA384", True,
            )
        },
        credentials={"policy-secret": "must-not-reach-rogue-peer"},
        timeout_seconds=1,
    )
    rogue_client = await rogue_resolver.resolve(
        binding, episode_id="episode-rogue", effective_plan_digest=D
    )
    with pytest.raises(PolicyHttpError) as caught:
        await rogue_client.invoke(request(episode_id="episode-rogue"))
    assert caught.value.code == "tls_peer_mismatch"
    assert not seen
    await rogue_resolver.close()
    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        network_authorities={
            "policy-route": RouteNetworkAuthority("policy-route", D, D, "localhost", ("127.0.0.1",), True, False, False, False, False)
        },
        tls_authorities={
            "policy-route": PolicyTlsTrustAuthority(
                "policy-route",
                "localhost",
                "sha256:" + hashlib.sha256(ca_pem).hexdigest(),
                ca_pem,
                "sha256:00ea6df3ae9eba4a396edd257a7fc16c7abc9d3b89d8a3dff462819828db9337",
                "TLSv1.3",
                "TLS_AES_256_GCM_SHA384",
                True,
            )
        },
        credentials={"policy-secret": "loopback-policy-token"},
        timeout_seconds=1,
        tls_observation_sink=record_tls,
    )
    try:
        client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
        result = await client.invoke(request())
        assert result.response_payload["output"][0]["text"] == "loopback"
        assert b"authorization: bearer loopback-policy-token\r\n" in seen[0].lower()
        assert len(tls_observations) == 1
        tls_observation = tls_observations[0]
        assert tls_observation.tls_version == "TLSv1.3"
        assert tls_observation.cipher == "TLS_AES_256_GCM_SHA384"
        assert tls_observation.connected_peer_ip == "127.0.0.1"
        assert tls_observation.leaf_der_digest == "sha256:00ea6df3ae9eba4a396edd257a7fc16c7abc9d3b89d8a3dff462819828db9337"
        assert tls_observation.ca_authority_digest == "sha256:" + hashlib.sha256(ca_pem).hexdigest()
        assert tls_observation.route_digest == route.grant.route_revision_digest
        assert tls_observation.network_grant_ref == D
        assert tls_observation.request_digest == request().request_digest
        assert tls_observation.response_digest == result.response_digest
        await client.close()
    finally:
        await resolver.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_authenticated_canonical_callback_and_idempotent_close():
    route, observation, binding = authorities()
    seen = []

    async def callback(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        response_payload = {"output": [{"type": "message", "text": "ok"}]}
        return httpx.Response(200, content=canonical({"response_digest": digest(response_payload), "response_payload": response_payload}))

    resolver = _resolver(registry_revision_digest=D,
    routes={"policy-route": route}, observations={D: observation}, credentials={"policy-secret": "separate-policy-token"},
    timeout_seconds=1, transport=httpx.MockTransport(callback),)
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    result = await client.invoke(request())
    assert result.response_digest == digest({"output": [{"type": "message", "text": "ok"}]})
    assert seen[0].headers["authorization"] == "Bearer separate-policy-token"
    assert seen[0].url == "https://127.0.0.1/v1/responses"
    await client.close()
    await client.close()
    await resolver.close()
    await resolver.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,code", [
    ({"registry_revision_digest": "sha256:" + "b" * 64}, "registry_revision_mismatch"),
    ({"route_id": "other"}, "binding_not_admitted"),
    ({"attestation_digest": "sha256:" + "b" * 64}, "binding_not_admitted"),
])
async def test_authority_mismatches_fail_before_network(overrides, code):
    route, observation, binding = authorities()
    calls = 0
    async def callback(req):
        nonlocal calls
        calls += 1
        return httpx.Response(500)
    resolver = _resolver(registry_revision_digest=D, routes={"policy-route": route}, observations={D: observation}, credentials={"policy-secret": "token"}, timeout_seconds=1, transport=httpx.MockTransport(callback))
    with pytest.raises(PolicyHttpError) as caught:
        await resolver.resolve(c.PolicyBindingRef(**(binding.model_dump() | overrides)), episode_id="episode-1", effective_plan_digest=D)
    assert caught.value.code == code
    assert calls == 0
    await resolver.close()


@pytest.mark.asyncio
async def test_response_digest_and_canonicality_are_enforced():
    route, observation, binding = authorities()
    responses = iter([
        b'{"response_payload":{}, "response_digest":"' + D.encode() + b'"}',
        canonical({"response_digest": D, "response_payload": {"ok": True}}),
    ])
    async def callback(req):
        return httpx.Response(200, content=next(responses))
    resolver = _resolver(registry_revision_digest=D, routes={"policy-route": route}, observations={D: observation}, credentials={"policy-secret": "token"}, timeout_seconds=1, transport=httpx.MockTransport(callback))
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "response_invalid"
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "response_invalid"
    await resolver.close()


@pytest.mark.asyncio
async def test_cancel_terminates_active_async_request():
    route, observation, binding = authorities()
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def callback(req):
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    resolver = _resolver(registry_revision_digest=D, routes={"policy-route": route}, observations={D: observation}, credentials={"policy-secret": "token"}, timeout_seconds=30, transport=httpx.MockTransport(callback))
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    invocation = asyncio.create_task(client.invoke(request()))
    await entered.wait()
    await client.cancel("episode close")
    assert cancelled.is_set()
    with pytest.raises(asyncio.CancelledError):
        await invocation
    await resolver.close()


@pytest.mark.asyncio
async def test_request_and_response_bounds():
    route, observation, binding = authorities(max_request=1)
    resolver = _resolver(registry_revision_digest=D, routes={"policy-route": route}, observations={D: observation}, credentials={"policy-secret": "token"}, timeout_seconds=1, transport=httpx.MockTransport(lambda req: httpx.Response(500)))
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "request_too_large"
    await resolver.close()
    route, observation, binding = authorities(max_response=1)
    resolver = _resolver(registry_revision_digest=D,
    routes={"policy-route": route},
    observations={D: observation},
    credentials={"policy-secret": "token"},
    timeout_seconds=1,
    transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"{}")),)
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "response_too_large"
    await resolver.close()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(307, headers={"location": "https://elsewhere.invalid/"}), "callback_status_invalid"),
        (httpx.Response(200, content=b"not-json"), "response_invalid"),
        (httpx.Response(200, content=b'{"response_payload\":{},\"response_payload\":{}}'), "response_invalid"),
    ],
)
async def test_redirect_and_malformed_responses_fail_closed(response, code):
    route, observation, binding = authorities()
    resolver = _resolver(registry_revision_digest=D,
    routes={"policy-route": route},
    observations={D: observation},
    credentials={"policy-secret": "token"},
    timeout_seconds=1,
    transport=httpx.MockTransport(lambda req: response),)
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == code
    await resolver.close()


@pytest.mark.asyncio
async def test_transport_timeout_is_redacted_and_cleanup_still_closes():
    route, observation, binding = authorities()

    async def timeout(req):
        await asyncio.sleep(1)
        return httpx.Response(500)

    resolver = _resolver(registry_revision_digest=D,
    routes={"policy-route": route},
    observations={D: observation},
    credentials={"policy-secret": "token"},
    timeout_seconds=0.01,
    transport=httpx.MockTransport(timeout),)
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "callback_timeout"
    assert "seeded secret" not in str(caught.value)
    await resolver.close()
    await resolver.close()

@pytest.mark.asyncio
async def test_rate_authority_is_enforced_before_second_network_call():
    route, observation, binding = authorities(max_requests=1)
    calls = 0

    async def callback(req):
        nonlocal calls
        calls += 1
        payload = {"ok": True}
        return httpx.Response(
            200,
            content=canonical(
                {"response_digest": digest(payload), "response_payload": payload}
            ),
        )

    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
        transport=httpx.MockTransport(callback),
    )
    first = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    second = await resolver.resolve(binding, episode_id="episode-2", effective_plan_digest=D)
    await first.invoke(request())
    with pytest.raises(PolicyHttpError) as caught:
        await second.invoke(request(episode_id="episode-2"))
    assert caught.value.code == "rate_limit_exceeded"
    assert calls == 1
    await resolver.close()


@pytest.mark.asyncio
async def test_concurrent_close_waiters_share_cleanup_and_resolver_deregisters():
    route, observation, binding = authorities()
    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda req: httpx.Response(500)),
    )
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    await asyncio.gather(client.close(), client.close())
    assert not resolver._clients
    await asyncio.gather(resolver.close(), resolver.close())
    with pytest.raises(PolicyHttpError) as caught:
        await resolver.resolve(binding, episode_id="episode-2", effective_plan_digest=D)
    assert caught.value.code == "resolver_closed"


@pytest.mark.asyncio
async def test_http_client_ignores_proxy_and_ca_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setenv("SSL_CERT_FILE", "/attacker/ca.pem")
    route, observation, binding = authorities()
    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda req: httpx.Response(500)),
    )
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    assert not hasattr(client, "_client")
    assert client._ssl_context.cert_store_stats()["x509_ca"] == 1
    await resolver.close()

class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self._chunks = chunks
        self._delay = delay

    async def __aiter__(self):
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk


@pytest.mark.asyncio
async def test_chunked_response_is_bounded_before_complete_buffering():
    route, observation, binding = authorities(max_response=4)
    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, stream=_ChunkStream([b"123", b"456"]))
        ),
    )
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "response_too_large"
    await resolver.close()


@pytest.mark.asyncio
async def test_total_wall_deadline_stops_slow_drip_stream():
    route, observation, binding = authorities()
    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=0.03,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, stream=_ChunkStream([b"{", b"}", b" "], delay=0.02)
            )
        ),
    )
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "callback_timeout"
    await resolver.close()


@pytest.mark.parametrize("payload", [{"n": -0.0}, {"n": 1e30}, {"\U0001f600": 1, "\uffff": 2}])
def test_policy_digest_matches_service_jcs(payload):
    assert digest(payload) == canonical_sha256(payload)

@pytest.mark.asyncio
async def test_hostname_route_fails_before_mutable_dns_resolution():
    route, observation, binding = authorities(authority="localhost")
    calls = 0

    async def callback(req):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
        transport=httpx.MockTransport(callback),
    )
    with pytest.raises(PolicyHttpError) as caught:
        await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    assert caught.value.code == "unpinned_dns_route"
    assert calls == 0
    await resolver.close()


@pytest.mark.asyncio
async def test_missing_tls_network_or_secret_authority_fails_closed():
    route, observation, binding = authorities()
    for missing in ("tls_authorities", "network_authorities", "secret_authorities"):
        kwargs = {
            "registry_revision_digest": D,
            "routes": {"policy-route": route},
            "observations": {D: observation},
            "credentials": {"policy-secret": "token"},
            "timeout_seconds": 1,
            "transport": httpx.MockTransport(lambda req: httpx.Response(500)),
        }
        resolver = _resolver(**kwargs)
        getattr(resolver, f"_{missing}").clear()
        with pytest.raises(PolicyHttpError) as caught:
            await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
        assert caught.value.code == "authority_not_installed"
        await resolver.close()

def test_attestation_and_observation_maps_require_same_attestation_digest():
    route, observation, _ = authorities()
    network = RouteNetworkAuthority(
        "policy-route", D, D, "127.0.0.1", ("127.0.0.1",),
        True, False, False, False, False,
    )
    ca_pem = Path("tests/fixtures/rl/harness/production_composition/tls/ca.cert.pem").read_bytes()
    tls = PolicyTlsTrustAuthority(
        "policy-route", "127.0.0.1",
        "sha256:" + hashlib.sha256(ca_pem).hexdigest(), ca_pem, D,
        "TLSv1.3", "TLS_AES_256_GCM_SHA384", True,
    )
    attestation = _attestation(observation)
    with pytest.raises(PolicyHttpError) as caught:
        RouteBoundPolicyHttpResolver(
            registry_revision_digest=D,
            routes={"policy-route": route},
            observations={D: observation},
            attestations={attestation.attestation_digest: attestation},
            network_authorities={"policy-route": network},
            secret_authorities={
                "policy-secret": PolicySecretAuthority(
                    "policy-secret", D, D, ("policy-route",)
                )
            },
            tls_authorities={"policy-route": tls},
            credentials={"policy-secret": "token"},
            timeout_seconds=1,
        )
    assert caught.value.code == "attestation_authority_mismatch"
    with pytest.raises(TypeError, match="transport"):
        RouteBoundPolicyHttpResolver(
            registry_revision_digest=D,
            routes={"policy-route": route},
            observations={attestation.attestation_digest: observation},
            attestations={attestation.attestation_digest: attestation},
            network_authorities={"policy-route": network},
            secret_authorities={
                "policy-secret": PolicySecretAuthority(
                    "policy-secret", D, D, ("policy-route",)
                )
            },
            tls_authorities={"policy-route": tls},
            credentials={"policy-secret": "token"},
            timeout_seconds=1,
            transport=object(),
        )


def test_disallowed_ip_class_is_rejected_by_authority():
    with pytest.raises(ValueError, match="class"):
        RouteNetworkAuthority(
            "policy-route", D, D, "127.0.0.1", ("127.0.0.1",),
            False, False, False, False, False,
        )

@pytest.mark.asyncio
async def test_network_failure_traceback_retains_no_seeded_secret_or_path():
    route, observation, binding = authorities()
    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
    )
    client = await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    seed = "seeded-secret-/absolute/private/token"

    async def fail_before_send(body):
        raise OSError(seed)

    client._perform_pinned_https = fail_before_send
    with pytest.raises(PolicyHttpError) as caught:
        await client.invoke(request())
    assert caught.value.code == "callback_transport_failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    rendered = "".join(
        __import__("traceback").format_exception(caught.value)
    )
    assert seed not in rendered
    await resolver.close()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_factory",
    [
        lambda seed: b"HTTP/1.1 XYZ " + seed + b"\r\nContent-Length: 0\r\n\r\n",
        lambda seed: b"HTTP/1.1 200 OK\r\nContent-Length: -1" + seed + b"\r\n\r\n",
        lambda seed: b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nContent-Length: 0" + seed + b"\r\n\r\n",
        lambda seed: b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked" + seed + b"\r\nContent-Length: 0\r\n\r\n",
        lambda seed: b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n " + seed + b"\r\n\r\n",
        lambda seed: b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + seed,
        lambda seed: b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx" + seed,
        lambda seed: _raw_http_response(
            b'{"response_digest":"sha256:'
            + b"a" * 64
            + b'","response_payload":{"'
            + seed
            + b'":1,"'
            + seed
            + b'":2}}'
        ),
        lambda seed: _raw_http_response(
            b'{"response_digest":"' + seed + b'","response_payload":1}'
        ),
    ],
)
async def test_invalid_callback_framing_never_reflects_bearer_in_failure(
    response_factory, tmp_path, capsys
):
    seed = b"Bearer-reflection-seeded-secret"
    tls_root = Path("tests/fixtures/rl/harness/production_composition/tls")
    runtime_key = _materialize_test_server_key(
        tls_root / "server.key.pem", tmp_path / "server.key.pem"
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(tls_root / "server.cert.pem", runtime_key)

    async def invalid_callback(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            writer.write(response_factory(seed))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(
        invalid_callback, "127.0.0.1", 0, ssl=context
    )
    port = server.sockets[0].getsockname()[1]
    route, observation, binding = authorities(authority=f"127.0.0.1:{port}")
    ca_pem = (tls_root / "ca.cert.pem").read_bytes()
    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        network_authorities={
            "policy-route": RouteNetworkAuthority(
                "policy-route", D, D, "localhost", ("127.0.0.1",),
                True, False, False, False, False,
            )
        },
        tls_authorities={
            "policy-route": PolicyTlsTrustAuthority(
                "policy-route", "localhost",
                "sha256:" + hashlib.sha256(ca_pem).hexdigest(), ca_pem,
                "sha256:00ea6df3ae9eba4a396edd257a7fc16c7abc9d3b89d8a3dff462819828db9337",
                "TLSv1.3", "TLS_AES_256_GCM_SHA384", True,
            )
        },
        credentials={"policy-secret": seed.decode()},
        timeout_seconds=1,
    )
    try:
        client = await resolver.resolve(
            binding, episode_id="episode-1", effective_plan_digest=D
        )
        with pytest.raises(PolicyHttpError) as caught:
            await client.invoke(request())
        chain = []
        current = caught.value
        while current is not None:
            chain.append(repr(current))
            current = current.__cause__ or current.__context__
        rendered = "".join(__import__("traceback").format_exception(caught.value))
        captured = capsys.readouterr()
        combined = rendered + "".join(chain) + captured.out + captured.err
        assert seed.decode() not in combined
    finally:
        await resolver.close()
        server.close()
        await server.wait_closed()

@pytest.mark.asyncio
async def test_snapshot_digest_cannot_spoof_route_registry_digest():
    route, observation, binding = authorities()
    snapshot_digest = "sha256:" + "b" * 64
    assert snapshot_digest != observation.registry_revision_digest
    resolver = _resolver(
        registry_revision_digest=snapshot_digest,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
    )
    with pytest.raises(PolicyHttpError) as caught:
        await resolver.resolve(binding, episode_id="episode-1", effective_plan_digest=D)
    assert caught.value.code == "registry_revision_mismatch"
    await resolver.close()


def test_attestation_spoof_and_multiple_join_are_rejected():
    route, observation, _ = authorities()
    good = _attestation(observation)
    values = good.model_dump(mode="python")
    values["validity"] = good.validity
    values["revocation"] = good.revocation
    values["authorized_signer_key_ids"] = ("other-key",)
    values["attestation_digest"] = D
    provisional = c.PolicyCapabilityAttestationRecord.model_construct(**values)
    values["attestation_digest"] = provisional.derived_attestation_digest()
    spoof = c.PolicyCapabilityAttestationRecord(**values)
    with pytest.raises(PolicyHttpError) as caught:
        _resolver(
            registry_revision_digest=D,
            routes={"policy-route": route},
            observations={D: observation},
            attestations={spoof.attestation_digest: spoof},
            credentials={"policy-secret": "token"},
            timeout_seconds=1,
        )
    assert caught.value.code == "attestation_authority_mismatch"

    resolver = _resolver(
        registry_revision_digest=D,
        routes={"policy-route": route},
        observations={D: observation},
        credentials={"policy-secret": "token"},
        timeout_seconds=1,
    )
    values = good.model_dump(mode="python")
    values["validity"] = good.validity
    values["revocation"] = good.revocation
    values["signature_verification_policy_digest"] = "sha256:" + "b" * 64
    values["attestation_digest"] = D
    provisional = c.PolicyCapabilityAttestationRecord.model_construct(**values)
    values["attestation_digest"] = provisional.derived_attestation_digest()
    second = c.PolicyCapabilityAttestationRecord(**values)
    with pytest.raises(PolicyHttpError) as caught:
        RouteBoundPolicyHttpResolver(
            registry_revision_digest=D,
            routes=resolver._routes,
            observations={
                good.attestation_digest: observation,
                second.attestation_digest: observation,
            },
            attestations={
                good.attestation_digest: good,
                second.attestation_digest: second,
            },
            network_authorities=resolver._network_authorities,
            secret_authorities=resolver._secret_authorities,
            tls_authorities=resolver._tls_authorities,
            credentials={"policy-secret": "token"},
            timeout_seconds=1,
        )
    assert caught.value.code == "attestation_authority_mismatch"
