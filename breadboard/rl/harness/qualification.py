from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import secrets
import ssl
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from breadboard.rl.harness import contracts as c


QUALIFICATION_RESOURCE_PACKAGE = "breadboard.rl.harness.resources.qualification"
FIXTURE_ROOT = files(QUALIFICATION_RESOURCE_PACKAGE)
TLS_ROOT = FIXTURE_ROOT.joinpath("tls")
CANONICAL_VECTORS = FIXTURE_ROOT.joinpath("canonical_artifact_vectors_v1.json")
CANONICAL_VECTORS_SHA256 = "6ce223373def584dccd2226c502cd9d41b445a95d4d66679f42f7f1372b34ade"
TLS_AUTHORITY_SHA256 = "96fa8fac28b11d65d100b35aa9add3665cbe170006efe6a0469f49035e508ba3"
TLS_CA_CERTIFICATE_SHA256 = "c3538c17bb431c42327b24fe9fc60b26d1f9bb4c62398b5bf3b4aa0a6915c887"
TLS_SERVER_CERTIFICATE_SHA256 = "329a1b6269bd66261718b852ea885e91fb814b03d9817325ba3f29637ee817dc"
TLS_SERVER_KEY_SHA256 = "93678974306d0ef9af6c9cd68921059b23800f44e1653933dcdf5baff5122dfc"
STORE_NAMES = (
    "cas",
    "locator",
    "materialization_cache",
    "workspace",
    "lease",
    "security_profile",
)


def _write_exclusive(path: Path, payload: bytes, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        mode,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short qualification fixture write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, mode, follow_symlinks=False)
    current = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != mode:
        raise RuntimeError("qualification file mode was not installed exactly")


def _read_resource(
    resource: Any,
    *,
    expected_sha256: str,
    max_bytes: int = 1024 * 1024,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            os.fspath(resource),
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_size > max_bytes
        ):
            raise RuntimeError("qualification resource identity is invalid")
        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(
                descriptor,
                min(64 * 1024, max_bytes + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
    except OSError as exc:
        raise RuntimeError("qualification resource is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > max_bytes:
        raise RuntimeError("qualification resource exceeds its byte limit")
    raw = bytes(payload)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError("qualification resource digest mismatch")
    return raw


def _load_json(resource: Any, *, expected_sha256: str) -> Mapping[str, Any]:
    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                raise RuntimeError("qualification resource has duplicate keys")
            result[name] = value
        return result

    value = json.loads(
        _read_resource(resource, expected_sha256=expected_sha256),
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(value, Mapping):
        raise RuntimeError("qualification resource must contain a JSON object")
    return value


def independent_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _d(character: str) -> str:
    if len(character) != 1 or character not in "0123456789abcdef":
        raise ValueError("digest seed must be one lowercase hexadecimal character")
    return "sha256:" + character * 64


def _policy_capabilities(**updates: Any) -> Any:
    from breadboard.rl.harness import contracts as c

    payload = {
        "responses_protocol": "responses-v1",
        "modalities": ["text", "vision"],
        "tool_calling": True,
        "parallel_tool_calls": True,
        "token_ids": True,
        "token_logprobs": True,
        "routing_metadata": True,
        "cancellation": True,
        "max_context_tokens": 32_768,
        "max_output_tokens": 4_096,
        "policy_slot_count": 1,
        "request_features": ["json_mode", "seed"],
    }
    payload.update(updates)
    return c.PolicyCapabilityVector.model_validate(payload)


def _capability_projection(
    *,
    protocol_abi: str,
    model_digest: str,
    tokenizer_digest: str,
    checkpoint_digest: str,
    capabilities: Any,
) -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.policy-selection-capabilities.v1",
        "protocol_abi": protocol_abi,
        "model_digest": model_digest,
        "tokenizer_digest": tokenizer_digest,
        "checkpoint_digest": checkpoint_digest,
        "capabilities": capabilities.model_dump(mode="json"),
    }


def _options(*, runtime_abi: str) -> Any:
    from breadboard_engine.compilation.contracts import (
        CompileOptions,
        CompileTarget,
        TaskContract,
        TaskEvidenceContract,
        TaskRetentionContract,
        TaskVerifierContract,
    )

    return CompileOptions(
        target=CompileTarget(
            runner_adapter_id="breadboard.conductor.v1",
            runtime_abi=runtime_abi,
        ),
        task_contract=TaskContract(
            contract_id="swe-task.v1",
            parameter_schema={
                "type": "object",
                "properties": {"instruction": {"type": "string"}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
            artifacts=(),
            verifier=TaskVerifierContract(
                binding_id=None,
                input_artifact_ids=(),
                result_schema={
                    "type": "object",
                    "properties": {"passed": {"type": "boolean"}},
                    "required": ["passed"],
                    "additionalProperties": False,
                },
                timeout_ms=30_000,
            ),
            evidence=TaskEvidenceContract(
                required_event_types=("turn.completed",),
                required_artifact_ids=(),
            ),
            retention=TaskRetentionContract(
                retention_class_id="test-evidence",
                minimum_retention_seconds=60,
            ),
        ),
        source_contract="v2",
        v1_loss_policy="reject_all",
    )


def _inputs(
    members: dict[str, bytes],
    *,
    edges: tuple[Any, ...],
    root: str,
) -> tuple[Any, Any, Any]:
    from breadboard_engine.compilation.bundle import (
        ManifestReader,
        build_dependency_closure,
        ingest_member_map,
    )
    from breadboard.artifacts import InMemoryCAS

    cas = InMemoryCAS()
    bundle = ingest_member_map(
        members,
        cas,
        entrypoints={"main": root},
        source_label="installed-qualification",
    )
    closure = build_dependency_closure(
        bundle,
        root_entrypoint="main",
        edges=edges,
    )
    return ManifestReader(cas=cas, bundle=bundle, closure=closure), closure, bundle


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    path: Path
    sha256: str
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class MaterializedProductionCompositionFixture:
    composition_ref_path: Path
    composition_manifest_path: Path
    object_cas_root: Path
    installed_roots: Mapping[str, Path]
    secret_paths: Mapping[str, Path]
    secret_files: Mapping[str, str]
    secret_seed_bytes: Mapping[str, bytes]
    tls_server_key_path: Path
    api_bearer: str
    tls_server_certificate_path: Path
    tls_ca_certificate_path: Path
    policy_callback_secret: str
    server_host: str
    server_port: int
    policy_server_host: str
    policy_server_port: int
    generated_candidate_name: str
    profile_name: str
    expected_executable_identity: ExecutableIdentity
    verifier_executable_identity: ExecutableIdentity
    selector_digest: str
    create_body: Mapping[str, object]
    policy_response_body: Mapping[str, object]
    policy_observation: Mapping[str, object]
    cleanup_paths: tuple[Path, ...]


@contextlib.contextmanager
def qualification_policy_server(
    fixture: MaterializedProductionCompositionFixture,
) -> Iterator[tuple[str, int, list[dict[str, Any]]]]:
    """Serve the bounded loopback policy exchange used by installed qualification."""

    requests: list[dict[str, Any]] = []
    expected_authorization = f"Bearer {fixture.policy_callback_secret}"
    completion_payload = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "loopback-complete"}],
            }
        ]
    }
    completion_bytes = json.dumps(
        completion_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    responses = (
        dict(fixture.policy_response_body),
        {
            "response_digest": "sha256:"
            + hashlib.sha256(completion_bytes).hexdigest(),
            "response_payload": completion_payload,
        },
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > 65_536:
                self.send_error(400)
                return
            body = self.rfile.read(length)
            try:
                parsed = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400)
                return
            if not isinstance(parsed, dict):
                self.send_error(400)
                return
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": parsed,
                }
            )
            if self.headers.get("Authorization") != expected_authorization:
                self.send_response(401)
                self.send_header("Connection", "close")
                self.send_header("Content-Length", "0")
                self.close_connection = True
                self.end_headers()
                return
            selected = responses[min(len(requests) - 1, len(responses) - 1)]
            response = json.dumps(
                selected,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            self.send_response(200)
            self.send_header("Connection", "close")
            self.close_connection = True
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            return

    class PolicyServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = False

    server = PolicyServer(
        (fixture.policy_server_host, fixture.policy_server_port),
        Handler,
    )
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(
        fixture.tls_server_certificate_path,
        fixture.tls_server_key_path,
    )
    server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield str(host), int(port), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("qualification policy server did not stop")


@dataclass(frozen=True, slots=True)
class InstalledFixturePaths:
    stores: Mapping[str, Path]
    secrets: Mapping[str, Path]
    tls_server_key: Path
    tls_server_certificate: Path
    tls_ca_certificate: Path
    launch_seeds: Mapping[str, bytes]


def install_runtime_paths(root: Path) -> InstalledFixturePaths:
    """Install mutable roots and secret material required by the immutable corpus.

    The checked-in TLS private key is source material only.  HTTPS servers must use
    the dedicated 0600 copy returned here; production bootstrap secrets are distinct
    0400 files and are never derived from one another.
    """

    root.mkdir(mode=0o700, parents=True)
    os.chmod(root, 0o700, follow_symlinks=False)
    stores: dict[str, Path] = {}
    for name in STORE_NAMES:
        path = root / name
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700, follow_symlinks=False)
        stores[name] = path.resolve(strict=True)

    secret_payloads = {
        "api-auth": f"fixture-api-{secrets.token_hex(24)}".encode(),
        "policy-callback": f"fixture-policy-{secrets.token_hex(24)}".encode(),
        "receipt-signing": f"fixture-receipt-{secrets.token_hex(32)}".encode(),
    }
    secret_paths: dict[str, Path] = {}
    for handle_id, payload in secret_payloads.items():
        path = root / f"{handle_id}.secret"
        _write_exclusive(path, payload, 0o400)
        secret_paths[handle_id] = path.resolve(strict=True)

    tls_key = root / "policy-server.key.pem"
    _write_exclusive(
        tls_key,
        _read_resource(
            TLS_ROOT / "server.key.pem",
            expected_sha256=TLS_SERVER_KEY_SHA256,
        ),
        0o600,
    )
    tls_server_certificate = root / "policy-server.cert.pem"
    _write_exclusive(
        tls_server_certificate,
        _read_resource(
            TLS_ROOT / "server.cert.pem",
            expected_sha256=TLS_SERVER_CERTIFICATE_SHA256,
        ),
        0o400,
    )
    tls_ca_certificate = root / "policy-ca.cert.pem"
    _write_exclusive(
        tls_ca_certificate,
        _read_resource(
            TLS_ROOT / "ca.cert.pem",
            expected_sha256=TLS_CA_CERTIFICATE_SHA256,
        ),
        0o400,
    )

    return InstalledFixturePaths(
        stores=MappingProxyType(stores),
        secrets=MappingProxyType(secret_paths),
        tls_server_key=tls_key.resolve(strict=True),
        tls_server_certificate=tls_server_certificate.resolve(strict=True),
        tls_ca_certificate=tls_ca_certificate.resolve(strict=True),
        launch_seeds=MappingProxyType(secret_payloads),
    )


def _setup_authority_projection(
    grant: dict[str, Any], task: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.setup-plan.v1",
        "setup_id": grant["setup_id"],
        "implementation_digest": grant["implementation_digest"],
        "argv": ["breadboard-setup", "--prepare-workspace"],
        "input_digests": list(task["input_artifact_digests"]),
        "writable_output_subtrees": ["workspace/output"],
        "writable_output_slots": ["patch"],
        "route_ids": ["policy-route"],
        "secret_handle_ids": ["policy-credential"],
        "timeout_ms": 60_000,
        "expected_outputs": [{"role": "patch", "artifact_id": "patch"}],
    }


def _route_authority_projection(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.route-authority.v1",
        "route_id": grant["route_id"],
        "protocol_abi": grant["protocol_abi"],
        "credential_handle_id": grant["credential_handle_id"],
        "scheme": "https",
        "authority": "policy.example.test",
        "paths": ["/v1/responses"],
        "methods": ["POST"],
        "ip_policy_digest": _d("2"),
        "dns_policy_digest": _d("3"),
        "request_schema_digest": _d("4"),
        "response_schema_digest": _d("5"),
        "max_request_bytes": 65_536,
        "max_response_bytes": 32_768,
        "max_requests_per_minute": 60,
        "data_classification": "confidential",
        "owner": {"owner_id": "operator", "authority_scope_digest": _d("1")},
    }


def _rebind_registry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rebound = copy.deepcopy(payload)
    component_digest_fields = {
        "runners": "runner_registry_digest",
        "tools": "tool_registry_digest",
        "setups": "setup_registry_digest",
        "routes": "route_registry_digest",
        "secret_handles": "secret_handle_registry_digest",
        "sandbox_runtimes": "sandbox_runtime_registry_digest",
        "images": "image_registry_digest",
        "repository_bindings": "repository_binding_registry_digest",
        "task_datasets": "task_dataset_registry_digest",
        "models": "model_registry_digest",
        "verifiers": "verifier_registry_digest",
        "evidence_policies": "evidence_policy_registry_digest",
        "retention_policies": "retention_policy_registry_digest",
        "policy_capability_attestations": "policy_capability_registry_digest",
    }
    component_digests = {
        digest_field: independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component": component,
                "records": rebound[component],
            }
        )
        for component, digest_field in component_digest_fields.items()
    }
    rebound["digests"] = {
        **component_digests,
        "snapshot_digest": independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component_digests": component_digests,
            }
        ),
    }
    return rebound


def _base_capability_payload() -> dict[str, Any]:
    payload = copy.deepcopy(
        next(
            item["payload"]
            for item in _load_json(
                CANONICAL_VECTORS,
                expected_sha256=CANONICAL_VECTORS_SHA256,
            )["vectors"]
            if item["artifact_kind"] == "capability_vector"
        )
    )
    payload["sandbox"]["mounts"].append(
        {
            "source_artifact_digest": _d("b"),
            "target_logical_path": "workspace/output",
            "access": "rw",
            "max_bytes": 1_048_576,
        }
    )
    setup = payload["setup_plans"][0]
    setup["plan_digest"] = independent_digest(
        _setup_authority_projection(setup, payload["task"])
    )
    route = payload["routes"][0]
    route["route_revision_digest"] = independent_digest(
        _route_authority_projection(route)
    )
    return payload


@dataclass(frozen=True, slots=True)
class _AdmissionSeed:
    request: object
    policy: object
    registries: object


def _admission_seed(
    *,
    capability_payload: dict[str, Any] | None = None,
    ceiling_capability_payload: dict[str, Any] | None = None,
) -> _AdmissionSeed:
    capability = c.CapabilityVector.model_validate(
        capability_payload or _base_capability_payload()
    )
    ceiling_vector = c.CapabilityVector.model_validate(
        ceiling_capability_payload or _base_capability_payload()
    )
    repository_binding_digest = _d("7")
    retention_grant = c.RetentionPolicyGrant(
        policy=capability.retention,
        minimum_seconds=86_400,
        maximum_seconds=2_592_000,
    )
    setup_projection = _setup_authority_projection(
        capability.setup_plans[0].to_canonical_obj(),
        capability.task.to_canonical_obj(),
    )
    setup_record = c.SetupRegistryRecord(
        grant=capability.setup_plans[0],
        argv=tuple(setup_projection["argv"]),
        input_digests=tuple(setup_projection["input_digests"]),
        writable_output_subtrees=tuple(setup_projection["writable_output_subtrees"]),
        writable_output_slots=tuple(setup_projection["writable_output_slots"]),
        route_ids=tuple(setup_projection["route_ids"]),
        secret_handle_ids=tuple(setup_projection["secret_handle_ids"]),
        timeout_ms=setup_projection["timeout_ms"],
        expected_outputs=tuple(
            c.SetupOutput.model_validate(output)
            for output in setup_projection["expected_outputs"]
        ),
    )
    route_projection = _route_authority_projection(
        capability.routes[0].to_canonical_obj()
    )
    route_record = c.RouteRegistryRecord(
        grant=capability.routes[0],
        scheme=route_projection["scheme"],
        authority=route_projection["authority"],
        paths=tuple(route_projection["paths"]),
        methods=tuple(route_projection["methods"]),
        ip_policy_digest=route_projection["ip_policy_digest"],
        dns_policy_digest=route_projection["dns_policy_digest"],
        request_schema_digest=route_projection["request_schema_digest"],
        response_schema_digest=route_projection["response_schema_digest"],
        max_request_bytes=route_projection["max_request_bytes"],
        max_response_bytes=route_projection["max_response_bytes"],
        max_requests_per_minute=route_projection["max_requests_per_minute"],
        data_classification=route_projection["data_classification"],
        owner=c.RouteOwnerAuthority.model_validate(route_projection["owner"]),
    )
    verifier_runtime = c.SandboxBinding(
        runtime_id="verifier-runtime",
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest=_d("7"),
        runtime_binary_digest=_d("8"),
        security_policy_digest=_d("9"),
        image_digest=capability.verifier.image_digest,
        network_policy_digest=capability.verifier.network_policy_digest,
    )
    attestation_projection = {
        "schema_version": "bb.rl.policy-capability-attestation.v1",
        "route_id": capability.routes[0].route_id,
        "route_revision_digest": capability.routes[0].route_revision_digest,
        "model_digest": capability.policy_slots[0].model_digest,
        "tokenizer_digest": capability.policy_slots[0].tokenizer_digest,
        "checkpoint_digest": capability.policy_slots[0].checkpoint_digest,
        "capability_digest": capability.policy_slots[
            0
        ].required_policy_capabilities_digest,
        "authorized_signer_key_ids": [
            "operator-signing-key",
            "startup-key",
            "startup-probe-key",
        ],
        "signature_verification_policy_digest": _d("d"),
        "validity": {
            "issued_at": "2026-07-10T11:00:00Z",
            "not_before": "2026-07-10T11:00:00Z",
            "expires_at": "2026-07-10T14:00:00Z",
        },
        "revocation": {
            "scope_digest": _d("1"),
            "epoch": 7,
            "state_digest": _d("3"),
        },
    }
    attestation_record = c.PolicyCapabilityAttestationRecord(
        **{
            key: value
            for key, value in attestation_projection.items()
            if key != "schema_version"
        },
        attestation_digest=independent_digest(attestation_projection),
    )
    registry_records: dict[str, tuple[Any, ...]] = {
        "runners": (c.RunnerRegistryRecord(grant=capability.runner),),
        "tools": (
            c.ToolRegistryRecord(
                grant=capability.tools[0],
                argument_schema_digest=_d("1"),
                result_schema_digest=_d("2"),
                reserved=False,
            ),
        ),
        "setups": (setup_record,),
        "routes": (route_record,),
        "secret_handles": (
            c.SecretHandleRegistryRecord(
                grant=capability.secret_handles[0],
                route_ids=(capability.routes[0].route_id,),
            ),
        ),
        "sandbox_runtimes": (
            c.SandboxRuntimeRegistryRecord(
                binding=c.SandboxBinding(
                    runtime_id=capability.sandbox.runtime_id,
                    runtime_class=capability.sandbox.runtime_class,
                    driver_implementation_digest=capability.sandbox.driver_implementation_digest,
                    runtime_binary_digest=capability.sandbox.runtime_binary_digest,
                    security_policy_digest=capability.sandbox.security_policy_digest,
                    image_digest=capability.sandbox.image_digest,
                    network_policy_digest=capability.sandbox.network_policy_digest,
                )
            ),
            c.SandboxRuntimeRegistryRecord(binding=verifier_runtime),
        ),
        "images": (
            c.ImageRegistryRecord(
                image_digest=capability.verifier.image_digest,
                runtime_id=verifier_runtime.runtime_id,
                repository_binding_digests=(),
            ),
            c.ImageRegistryRecord(
                image_digest=capability.sandbox.image_digest,
                runtime_id=capability.sandbox.runtime_id,
                repository_binding_digests=(repository_binding_digest,),
            ),
        ),
        "repository_bindings": (
            c.RepositoryBindingRegistryRecord(
                binding_digest=repository_binding_digest,
                repository_snapshot_digest=capability.task.repository_snapshot_digest,
                image_digest=capability.sandbox.image_digest,
            ),
        ),
        "task_datasets": (
            c.TaskDatasetRegistryRecord(
                task_contract_digest=capability.task.task_contract_digest,
                task_binding_digest=capability.task.task_binding_digest,
                repository_snapshot_digest=capability.task.repository_snapshot_digest,
                dataset_digests=capability.task.dataset_digests,
                input_artifact_digests=capability.task.input_artifact_digests,
            ),
        ),
        "models": (
            c.ModelRegistryRecord(
                identity=c.ModelIdentity(
                    model_id="model-a",
                    model_digest=capability.policy_slots[0].model_digest,
                    tokenizer_digest=capability.policy_slots[0].tokenizer_digest,
                    checkpoint_digest=capability.policy_slots[0].checkpoint_digest,
                )
            ),
        ),
        "verifiers": (
            c.VerifierRegistryRecord(
                grant=capability.verifier,
                runtime_id=verifier_runtime.runtime_id,
                runtime_class=verifier_runtime.runtime_class,
                security_policy_digest=verifier_runtime.security_policy_digest,
            ),
        ),
        "evidence_policies": (
            c.EvidencePolicyRegistryRecord(
                policy=capability.evidence,
                required_roles=("patch", "transcript"),
            ),
        ),
        "retention_policies": (c.RetentionPolicyRegistryRecord(grant=retention_grant),),
        "policy_capability_attestations": (attestation_record,),
    }
    component_digest_fields = {
        "runners": "runner_registry_digest",
        "tools": "tool_registry_digest",
        "setups": "setup_registry_digest",
        "routes": "route_registry_digest",
        "secret_handles": "secret_handle_registry_digest",
        "sandbox_runtimes": "sandbox_runtime_registry_digest",
        "images": "image_registry_digest",
        "repository_bindings": "repository_binding_registry_digest",
        "task_datasets": "task_dataset_registry_digest",
        "models": "model_registry_digest",
        "verifiers": "verifier_registry_digest",
        "evidence_policies": "evidence_policy_registry_digest",
        "retention_policies": "retention_policy_registry_digest",
        "policy_capability_attestations": "policy_capability_registry_digest",
    }
    component_digests = {
        component_digest_fields[component]: independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component": component,
                "records": [record.to_canonical_obj() for record in records],
            }
        )
        for component, records in registry_records.items()
    }
    digests = c.RegistryDigestSet(
        **component_digests,
        snapshot_digest=independent_digest(
            {
                "schema_version": c.REGISTRY_SNAPSHOT_SCHEMA_VERSION,
                "component_digests": component_digests,
            }
        ),
    )
    registries = c.RegistrySnapshotSet(digests=digests, **registry_records)
    compiler_payload = next(
        item["payload"]["compiled"]["compiler"]
        for item in _load_json(
            CANONICAL_VECTORS,
            expected_sha256=CANONICAL_VECTORS_SHA256,
        )["vectors"]
        if item["artifact_kind"] == "admission_request"
    )
    compiler_identity = c.CompilerIdentity.model_validate(compiler_payload)
    ceiling_retention = c.RetentionPolicyGrant(
        policy=ceiling_vector.retention,
        minimum_seconds=86_400,
        maximum_seconds=2_592_000,
    )
    ceiling = c.OperatorCeiling(
        runner_bindings=(ceiling_vector.runner,),
        tool_grants=ceiling_vector.tools,
        setup_grants=ceiling_vector.setup_plans,
        route_grants=ceiling_vector.routes,
        secret_handle_grants=ceiling_vector.secret_handles,
        sandbox_bindings=(
            c.SandboxBinding(
                runtime_id=ceiling_vector.sandbox.runtime_id,
                runtime_class=ceiling_vector.sandbox.runtime_class,
                driver_implementation_digest=ceiling_vector.sandbox.driver_implementation_digest,
                runtime_binary_digest=ceiling_vector.sandbox.runtime_binary_digest,
                security_policy_digest=ceiling_vector.sandbox.security_policy_digest,
                image_digest=ceiling_vector.sandbox.image_digest,
                network_policy_digest=ceiling_vector.sandbox.network_policy_digest,
            ),
        ),
        repository_snapshot_digests=(ceiling_vector.task.repository_snapshot_digest,),
        task_contract_digests=(ceiling_vector.task.task_contract_digest,),
        task_binding_digests=(ceiling_vector.task.task_binding_digest,),
        dataset_digests=ceiling_vector.task.dataset_digests,
        model_bindings=(
            c.ModelIdentity(
                model_id="model-a",
                model_digest=ceiling_vector.policy_slots[0].model_digest,
                tokenizer_digest=ceiling_vector.policy_slots[0].tokenizer_digest,
                checkpoint_digest=ceiling_vector.policy_slots[0].checkpoint_digest,
            ),
        ),
        verifier_grants=(ceiling_vector.verifier,),
        policy_slot_grants=ceiling_vector.policy_slots,
        evidence_policies=(ceiling_vector.evidence,),
        retention_policies=(ceiling_retention,),
        mutable_pointer_rules=ceiling_vector.mutable_pointers,
        resource_maxima=ceiling_vector.resources,
        execution_maxima=ceiling_vector.limits,
        allowed_egress_route_ids=ceiling_vector.sandbox.egress_route_ids,
        mount_grants=ceiling_vector.sandbox.mounts,
        artifact_policy_maximum=ceiling_vector.artifacts,
    )
    policy = c.AdmissionPolicySnapshot(
        policy_id="operator-default",
        revision="2026-07-10.1",
        subject_scope_digest=_d("1"),
        compiler_constraints=c.CompilerConstraints(
            allowed_compilers=(compiler_identity,)
        ),
        registry_digests=digests,
        ceiling=ceiling,
        required_security=c.RequiredSecurityPolicy(
            minimum_isolation_class=c.RuntimeClass.HARDENED_DOCKER,
            required_verifier_isolation_class=c.RuntimeClass.HARDENED_DOCKER,
            required_evidence_roles=("patch", "transcript"),
            prohibited_runtime_classes=(c.RuntimeClass.TRUSTED_PROCESS,),
            minimum_retention_seconds=86_400,
        ),
        receipt_ttl_seconds=3_600,
        validity=c.ValidityWindow(
            issued_at="2026-07-10T11:00:00Z",
            not_before="2026-07-10T11:00:00Z",
            expires_at="2026-07-10T14:00:00Z",
        ),
        revocation=c.RevocationBinding(
            scope_digest=_d("1"), epoch=7, state_digest=_d("3")
        ),
    )
    provenance = {"entries": []}
    diagnostics = {"defaults": [], "notices": []}
    compiled_payload = next(
        item["payload"]["compiled"]
        for item in _load_json(
            CANONICAL_VECTORS,
            expected_sha256=CANONICAL_VECTORS_SHA256,
        )["vectors"]
        if item["artifact_kind"] == "admission_request"
    )
    compiled_payload = copy.deepcopy(compiled_payload)
    compiled_payload["provenance_digest"] = independent_digest(provenance)
    compiled_payload["diagnostics_digest"] = independent_digest(diagnostics)
    compiled = c.CompiledArtifactIdentity.model_validate(compiled_payload)
    request = c.AdmissionRequest(
        subject=c.AuthenticatedSubject(
            tenant_id="tenant-a",
            principal_id="principal-a",
            authority_scope_digest=_d("1"),
        ),
        behavior_source=c.CompiledBehaviorSource(
            manifest_digest=compiled.manifest_digest,
            semantic_digest=compiled.semantic_digest,
        ),
        compiled=compiled,
        requested_capabilities=capability,
        requested_capability_digest=capability.canonical_digest(),
        task_binding_digest=capability.task.task_binding_digest,
        policy_binding_ref=c.PolicyBindingRef(
            route_id=capability.routes[0].route_id,
            registry_revision_digest=digests.route_registry_digest,
            attestation_digest=attestation_record.attestation_digest,
        ),
        admission_policy_digest=policy.canonical_digest(),
        registry_snapshot_digest=digests.snapshot_digest,
        validity=c.ValidityWindow(
            issued_at="2026-07-10T12:00:00Z",
            not_before="2026-07-10T12:00:00Z",
            expires_at="2026-07-10T13:00:00Z",
        ),
        parent_receipt_digest=None,
        overlay_chain_digest=None,
    )
    return _AdmissionSeed(request=request, policy=policy, registries=registries)


def materialize_production_composition_fixture(
    tmp_path: Path,
    *,
    policy_server_port: int | None = None,
    server_port: int | None = None,
    long_running: bool = False,
    policy_provider_id: str = "openai_responses",
    policy_model_id: str = "test-model",
    policy_context_window: int = 32_768,
    policy_max_output_tokens: int = 4_096,
) -> MaterializedProductionCompositionFixture:
    """Build the complete production loader corpus from canonical model authorities."""

    import copy
    import shutil
    import hashlib
    import json
    import socket
    from datetime import UTC, datetime, timedelta

    from breadboard_engine.compilation.contracts import (
        DependencyEdge,
        canonical_json_bytes,
    )
    from breadboard_engine.compilation.server_compiler import compile_config
    from breadboard.rl.harness import contracts as c
    from breadboard.rl.harness.composition import (
        ArtifactFileRefV1,
        AuthorityBundleV1,
        CompilerIdentityV1,
        CompositionRefV1,
        ControlPlaneV1,
        DNSPolicyDocumentV1,
        HarnessCompositionManifestV1,
        IPPolicyDocumentV1,
        InstalledV1,
        PolicyHttpAuthorityGraphV1,
        PolicyHttpSchemaAuthorityV1,
        PolicySecretRouteBindingV1,
        PolicyTlsTrustAuthorityV1,
        ReceiptAuthenticatorV1,
        SecretHandleSpecV1,
        SecretHandlesV1,
        SelectorCatalogV1,
        ServerV1,
        StoresV1,
        CASConfigRuntimeStore,
        HmacSha256ReceiptAuthenticator,
        PinnedRevocationStore,
        PinnedServerCompilerAdapter,
    )
    from breadboard.rl.harness.config_runtime import ConfigRuntime
    from breadboard.rl.harness.evidence import (
        EvidenceRoleBindingV2,
        EvidenceRoleSourceV2,
    )
    from breadboard.rl.harness.policy_http import (
        POLICY_HTTP_PROTOCOL_ABI,
        POLICY_HTTP_REQUEST_SCHEMA,
        POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
        POLICY_HTTP_RESPONSE_SCHEMA,
        POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
    )
    from breadboard.rl.harness.runners.base import RunnerAdapterDescriptor
    from breadboard.rl.harness.sandbox import (
        InstalledImage,
        InstalledRuntime,
        InstalledVerifier,
        SandboxNetworkPolicy,
        SandboxSecurityPolicy,
    )
    from breadboard.rl.harness.runners.conductor import (
        CONDUCTOR_ADAPTER_ID,
        CONDUCTOR_IMPLEMENTATION_DIGEST,
        CONDUCTOR_RUNTIME_ABI,
    )
    from breadboard.artifacts.cas import FilesystemCAS

    root = (tmp_path / "production-composition").resolve()
    installed_paths = install_runtime_paths(root / "installed")
    if policy_server_port is None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            policy_server_port = int(probe.getsockname()[1])
        finally:
            probe.close()
    if server_port is None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            server_port = int(probe.getsockname()[1])
        finally:
            probe.close()
    if server_port == policy_server_port:
        raise ValueError("API server and policy callback ports must be distinct")

    def digest_payload(value: str | bytes) -> str:
        payload = value if isinstance(value, bytes) else value.encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    runtime_install = root / "runtime-install"
    runtime_install.mkdir(mode=0o700, parents=True)
    shell_source = Path(os.path.realpath("/bin/sh"))
    shell_path = runtime_install / "shell"
    shutil.copyfile(shell_source, shell_path)
    shell_path.chmod(0o500)
    shell_path = shell_path.resolve(strict=True)
    shell_digest = digest_payload(shell_path.read_bytes())
    verifier_script = b"""#!/bin/sh
set -eu
manifest=input/verifier-request.json
test -f "$manifest"
payload=
IFS= read -r payload < "$manifest" || test -n "$payload"
case "$payload" in
  *'"schema_version":"bb.rl.verifier-request.v1"'*) ;;
  *) exit 64 ;;
esac
effective=${payload#*'"effective_plan_digest":"'}
effective=${effective%%'"'*}
episode=${payload#*'"episode_id":"'}
episode=${episode%%'"'*}
snapshot=${payload#*'"snapshot_digest":"'}
snapshot=${snapshot%%'"'*}
task=${payload#*'"task_digest":"'}
task=${task%%'"'*}
verifier=${payload#*'"verifier_digest":"'}
verifier=${verifier%%'"'*}
case "$effective:$snapshot:$task:$verifier" in
  sha256:*:sha256:*:sha256:*:sha256:*) ;;
  *) exit 65 ;;
esac
task_output=
IFS= read -r task_output < snapshot/task-output.json || test -n "$task_output"
test "$task_output" = '{"answer":"breadboard-production-fixture"}' || exit 67
test -n "$episode"
expected=$(printf '{"effective_plan_digest":"%s","episode_id":"%s","schema_version":"bb.rl.verifier-request.v1","snapshot_digest":"%s","task_digest":"%s","verifier_digest":"%s"}' "$effective" "$episode" "$snapshot" "$task" "$verifier")
test "$payload" = "$expected" || exit 66
printf '{"effective_plan_digest":"%s","episode_id":"%s","score":1.0,"snapshot_digest":"%s","task_digest":"%s","verifier_digest":"%s"}' "$effective" "$episode" "$snapshot" "$task" "$verifier" > result/result.json
"""
    verifier_path = runtime_install / "verifier"
    _write_exclusive(verifier_path, verifier_script, 0o500)
    verifier_path = verifier_path.resolve(strict=True)
    verifier_binary_digest = digest_payload(verifier_script)
    seccomp_bytes = b"{}"
    network_projection = {
        "mode": "none",
        "docker_network": "none",
        "egress_route_ids": [],
        "default_deny": True,
    }
    network_digest = SandboxNetworkPolicy.derive_digest(network_projection)
    primary_security_projection = {
        "uid": 65_534,
        "gid": 65_534,
        "read_only_root": True,
        "drop_all_capabilities": True,
        "no_new_privileges": True,
        "seccomp_digest": digest_payload(seccomp_bytes),
        "apparmor_profile": "breadboard-production-fixture",
        "selinux_label": None,
        "namespace_flags": [],
        "privileged": False,
        "devices": [],
        "docker_socket_forbidden": True,
        "tmpfs_mounts": [["/tmp", "rw,noexec,nosuid,size=1048576"]],
        "snapshot_max_depth": 8,
        "snapshot_max_files": 64,
        "snapshot_max_inodes": 128,
    }
    verifier_security_projection = {
        **primary_security_projection,
        "uid": 65_533,
        "gid": 65_533,
    }
    primary_security_digest = SandboxSecurityPolicy.derive_digest(
        primary_security_projection
    )
    verifier_security_digest = SandboxSecurityPolicy.derive_digest(
        verifier_security_projection
    )
    runner_grant = c.RunnerGrant(
        adapter_id=CONDUCTOR_ADAPTER_ID,
        runtime_abi=CONDUCTOR_RUNTIME_ABI,
        implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST,
    )
    sandbox_grant = c.SandboxGrant(
        runtime_id="trusted-process",
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        driver_implementation_digest=digest_payload("trusted-process-driver"),
        runtime_binary_digest=shell_digest,
        security_policy_digest=primary_security_digest,
        image_digest=digest_payload("trusted-process-image"),
        network_policy_digest=network_digest,
        egress_route_ids=(),
        mounts=(),
    )

    capability_payload = _base_capability_payload()
    verifier_payload = copy.deepcopy(capability_payload["verifier"])
    verifier_payload["network_policy_digest"] = network_digest
    verifier_payload["implementation_digest"] = independent_digest(
        {"implementation": "production-verifier-script", "code": verifier_binary_digest}
    )
    verifier_payload["executable_digest"] = verifier_binary_digest
    verifier_payload["code_digest"] = verifier_binary_digest
    verifier_grant = c.VerifierGrant.model_validate(verifier_payload)
    task_input = c.TaskEligibilityInput(
        task_type="training",
        labels=(),
        artifacts=(),
        parameters_digest="sha256:" + "1" * 64,
    )
    capability_payload["task"]["task_contract_digest"] = task_input.canonical_digest()
    setup_payload = capability_payload["setup_plans"][0]
    setup_payload["plan_digest"] = independent_digest(
        _setup_authority_projection(setup_payload, capability_payload["task"])
    )
    capability_payload["runner"] = runner_grant.model_dump(mode="json")
    capability_payload["sandbox"] = sandbox_grant.model_dump(mode="json")
    capability_payload["verifier"] = verifier_grant.model_dump(mode="json")
    selection_capabilities = _policy_capabilities(
        parallel_tool_calls=False,
        token_logprobs=False,
        max_context_tokens=policy_context_window,
        max_output_tokens=policy_max_output_tokens,
    )
    slot = capability_payload["policy_slots"][0]
    slot["slot_id"] = f"model:{policy_model_id}"
    capability_payload["routes"][0]["route_id"] = "policy-route"
    capability_digest = independent_digest(
        _capability_projection(
            protocol_abi=slot["protocol_abi"],
            model_digest=slot["model_digest"],
            tokenizer_digest=slot["tokenizer_digest"],
            checkpoint_digest=slot["checkpoint_digest"],
            capabilities=selection_capabilities,
        )
    )
    seed = _admission_seed(
        capability_payload=copy.deepcopy(capability_payload),
        ceiling_capability_payload=copy.deepcopy(capability_payload),
    )
    capability_payload["setup_plans"] = []
    capability_payload["task"]["repository_snapshot_digest"] = None
    capability_payload["task"]["dataset_digests"] = []
    capability_payload["task"]["input_artifact_digests"] = []
    slot["protocol_abi"] = POLICY_HTTP_PROTOCOL_ABI
    slot["secret_handle_id"] = "policy-callback"
    capability_digest = independent_digest(
        _capability_projection(
            protocol_abi=slot["protocol_abi"],
            model_digest=slot["model_digest"],
            tokenizer_digest=slot["tokenizer_digest"],
            checkpoint_digest=slot["checkpoint_digest"],
            capabilities=selection_capabilities,
        )
    )
    slot["required_policy_capabilities_digest"] = capability_digest
    route_grant = capability_payload["routes"][0]
    route_grant["protocol_abi"] = POLICY_HTTP_PROTOCOL_ABI
    route_grant["credential_handle_id"] = "policy-callback"
    secret_grant = capability_payload["secret_handles"][0]
    secret_grant["handle_id"] = "policy-callback"
    secret_grant["scope_digest"] = "sha256:" + "1" * 64

    route_values = {
        "scheme": c.RouteScheme.HTTPS,
        "authority": f"127.0.0.1:{policy_server_port}",
        "paths": ("/v1/responses",),
        "methods": (c.RouteMethod.POST,),
        "ip_policy_digest": "",
        "dns_policy_digest": "",
        "request_schema_digest": POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
        "response_schema_digest": POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
        "max_request_bytes": 65_536,
        "max_response_bytes": 32_768,
        "max_requests_per_minute": 60,
        "data_classification": c.DataClassification.CONFIDENTIAL,
        "owner": c.RouteOwnerAuthority(
            owner_id="operator", authority_scope_digest="sha256:" + "1" * 64
        ),
    }
    dns_projection = {
        "schema_version": "bb.rl.policy-dns-authority.v1",
        "hostname": "127.0.0.1",
        "allowed_addresses": ["127.0.0.1"],
        "resolution_mode": "pinned",
        "require_all_answers_admitted": True,
        "verify_connected_peer": True,
    }
    ip_projection = {
        "schema_version": "bb.rl.policy-ip-authority.v1",
        "allowed_addresses": ["127.0.0.1"],
        "allow_loopback": True,
        "allow_private": False,
        "allow_link_local": False,
        "allow_multicast": False,
        "allow_unspecified": False,
    }
    route_values["dns_policy_digest"] = independent_digest(dns_projection)
    route_values["ip_policy_digest"] = independent_digest(ip_projection)
    route_projection = {
        "schema_version": "bb.rl.route-authority.v1",
        "route_id": route_grant["route_id"],
        "protocol_abi": route_grant["protocol_abi"],
        "credential_handle_id": route_grant["credential_handle_id"],
        "scheme": route_values["scheme"].value,
        "authority": route_values["authority"],
        "paths": list(route_values["paths"]),
        "methods": [item.value for item in route_values["methods"]],
        "ip_policy_digest": route_values["ip_policy_digest"],
        "dns_policy_digest": route_values["dns_policy_digest"],
        "request_schema_digest": route_values["request_schema_digest"],
        "response_schema_digest": route_values["response_schema_digest"],
        "max_request_bytes": route_values["max_request_bytes"],
        "max_response_bytes": route_values["max_response_bytes"],
        "max_requests_per_minute": route_values["max_requests_per_minute"],
        "data_classification": route_values["data_classification"].value,
        "owner": route_values["owner"].model_dump(mode="json"),
    }
    route_grant["route_revision_digest"] = independent_digest(route_projection)
    route_record = c.RouteRegistryRecord(
        grant=c.RouteGrant.model_validate(route_grant), **route_values
    )

    capability = c.CapabilityVector.model_validate(capability_payload)
    verifier_runtime_id = "verifier-runtime"
    verifier_binding = c.SandboxBinding(
        runtime_id=verifier_runtime_id,
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        driver_implementation_digest=digest_payload("verifier-process-driver"),
        runtime_binary_digest=shell_digest,
        security_policy_digest=verifier_security_digest,
        image_digest=capability.verifier.image_digest,
        network_policy_digest=network_digest,
    )
    primary_binding = c.SandboxBinding(
        runtime_id=capability.sandbox.runtime_id,
        runtime_class=capability.sandbox.runtime_class,
        driver_implementation_digest=capability.sandbox.driver_implementation_digest,
        runtime_binary_digest=capability.sandbox.runtime_binary_digest,
        security_policy_digest=capability.sandbox.security_policy_digest,
        image_digest=capability.sandbox.image_digest,
        network_policy_digest=capability.sandbox.network_policy_digest,
    )
    runtime_records = tuple(
        sorted(
            (
                c.SandboxRuntimeRegistryRecord(binding=primary_binding),
                c.SandboxRuntimeRegistryRecord(binding=verifier_binding),
            ),
            key=lambda item: item.binding.runtime_id,
        )
    )
    image_records = tuple(
        sorted(
            (
                c.ImageRegistryRecord(
                    image_digest=capability.sandbox.image_digest,
                    runtime_id=capability.sandbox.runtime_id,
                    repository_binding_digests=(),
                ),
                c.ImageRegistryRecord(
                    image_digest=capability.verifier.image_digest,
                    runtime_id=verifier_runtime_id,
                    repository_binding_digests=(),
                ),
            ),
            key=lambda item: item.image_digest,
        )
    )
    verifier_record = c.VerifierRegistryRecord(
        grant=capability.verifier,
        runtime_id=verifier_runtime_id,
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        security_policy_digest=verifier_security_digest,
    )

    def security_policy(
        policy_digest: str, *, uid: int, gid: int
    ) -> SandboxSecurityPolicy:
        return SandboxSecurityPolicy(
            policy_digest=policy_digest,
            uid=uid,
            gid=gid,
            read_only_root=True,
            drop_all_capabilities=True,
            no_new_privileges=True,
            seccomp_bytes=seccomp_bytes,
            seccomp_digest=digest_payload(seccomp_bytes),
            apparmor_profile="breadboard-production-fixture",
            selinux_label=None,
            namespace_flags=(),
            privileged=False,
            devices=(),
            docker_socket_forbidden=True,
            tmpfs_mounts=(("/tmp", "rw,noexec,nosuid,size=1048576"),),
            snapshot_max_depth=8,
            snapshot_max_files=64,
            snapshot_max_inodes=128,
        )

    platform_identity = f"{os.uname().sysname.lower()}-{os.uname().machine.lower()}"
    installed_runtimes = tuple(
        sorted(
            (
                InstalledRuntime(
                    runtime_id=capability.sandbox.runtime_id,
                    runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
                    driver_implementation_digest=capability.sandbox.driver_implementation_digest,
                    executable_path=str(shell_path),
                    measured_binary_digest=shell_digest,
                    oci_runtime_name="process",
                    supported_platform_versions=(platform_identity,),
                    fixed_environment=(("PATH", "/usr/bin:/bin"),),
                ),
                InstalledRuntime(
                    runtime_id=verifier_runtime_id,
                    runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
                    driver_implementation_digest=verifier_binding.driver_implementation_digest,
                    executable_path=str(shell_path),
                    measured_binary_digest=shell_digest,
                    oci_runtime_name="process",
                    supported_platform_versions=(platform_identity,),
                    fixed_environment=(("PATH", "/usr/bin:/bin"),),
                ),
            ),
            key=lambda item: item.runtime_id,
        )
    )
    installed_images = tuple(
        sorted(
            (
                InstalledImage(
                    capability.sandbox.image_digest,
                    capability.sandbox.runtime_id,
                    "breadboard/trusted-process@" + capability.sandbox.image_digest,
                ),
                InstalledImage(
                    capability.verifier.image_digest,
                    verifier_runtime_id,
                    "breadboard/verifier@" + capability.verifier.image_digest,
                ),
            ),
            key=lambda item: item.image_digest,
        )
    )
    installed_security_policies = tuple(
        sorted(
            (
                security_policy(primary_security_digest, uid=65_534, gid=65_534),
                security_policy(verifier_security_digest, uid=65_533, gid=65_533),
            ),
            key=lambda item: item.policy_digest,
        )
    )
    installed_network_policies = (
        SandboxNetworkPolicy(
            policy_digest=network_digest,
            mode="none",
            docker_network="none",
            egress_route_ids=(),
            default_deny=True,
        ),
    )
    installed_verifiers = (
        InstalledVerifier(
            grant=capability.verifier,
            runtime_id=verifier_runtime_id,
            runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
            security_policy_digest=verifier_security_digest,
            argv=(str(verifier_path),),
            result_relative_path="result.json",
            executable_digest=capability.verifier.executable_digest,
            code_digest=capability.verifier.code_digest,
            input_schema_digest=capability.verifier.input_schema_digest,
            result_schema_digest=capability.verifier.result_schema_digest,
        ),
    )

    compiler_config = {
        "version": 2,
        "extends": ["base-config.json"],
        "profile": {
            "name": "production-fixture-profile",
            "metadata": {
                "breadboard_rl_authority": {
                    "requested_capabilities": capability.model_dump(mode="json"),
                    "task_binding_digest": capability.task.task_binding_digest,
                }
            },
        },
        "workspace": {"root": "workspace"},
        "providers": {
            "default_model": policy_model_id,
            "models": [
                {
                    "id": policy_model_id,
                    "adapter": "openai_responses",
                    "route_handle_id": "policy-route",
                    "credential_handle_id": "policy-callback",
                    "params": {"temperature": 0},
                }
            ],
        },
        "prompts": {
            "injection": {
                "system_order": [],
                "per_turn_order": [],
            }
        },
        "tools": {
            "registry": {
                "paths": ["tools"],
                "include": [capability.tools[0].tool_id],
                "exclude": [],
            }
        },
        "modes": [
            {
                "id": "build",
                "prompt": "Production fixture prompt.",
                "tools_enabled": [capability.tools[0].tool_id],
            }
        ],
        "loop": {"sequence": ["build"]},
    }
    base_config = {
        "provider_tools": {
            "api_variant": "responses",
            "use_native": True,
        }
    }
    config_bytes = canonical_json_bytes(compiler_config)
    base_config_bytes = canonical_json_bytes(base_config)
    terminal_tool_bytes = canonical_json_bytes(
        {
            "id": capability.tools[0].tool_id,
            "name": "shell",
            "description": "Run a shell command in the admitted workspace.",
            "parameters": [
                {
                    "name": "command",
                    "description": "Shell command to execute.",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }
            ],
            "execution": {"blocking": True, "max_per_turn": 1},
        }
    )
    config_members = {
        "production-config.json": config_bytes,
        "base-config.json": base_config_bytes,
        "tools/terminal.yaml": terminal_tool_bytes,
    }
    reader, closure, bundle = _inputs(
        config_members,
        root="production-config.json",
        edges=(
            DependencyEdge(
                "production-config.json",
                "extends",
                "base-config.json",
                "base-config.json",
                0,
            ),
            DependencyEdge(
                "production-config.json",
                "tool_registry",
                "tools",
                "tools/terminal.yaml",
                0,
            ),
        ),
    )
    compiled_manifest = compile_config(
        reader,
        closure,
        _options(runtime_abi=CONDUCTOR_RUNTIME_ABI),
    )
    compiled_bytes = compiled_manifest.canonical_bytes()
    compiled_digest = "sha256:" + hashlib.sha256(compiled_bytes).hexdigest()
    compiler = compiled_manifest.compiler
    compiler_identity = c.CompilerIdentity(
        compiler_id=compiler.compiler_id,
        semantic_version=compiler.compiler_version,
        code_digest=compiler.compiler_code_digest,
        source_schema_id=compiler.config_schema_id,
        source_schema_digest=compiler.config_schema_digest,
        manifest_schema_digest=compiler.manifest_schema_digest,
        canonicalizer_id=compiler.canonicalizer_id,
        runtime_abi=compiler.runtime_abi,
    )

    now = datetime.now(UTC).replace(microsecond=0)

    def utc_second(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    registry_payload = seed.registries.model_dump(mode="json")
    registry_payload["setups"] = []
    registry_payload["runners"] = [
        c.RunnerRegistryRecord(grant=capability.runner).model_dump(mode="json")
    ]
    registry_payload["routes"] = [route_record.model_dump(mode="json")]
    registry_payload["models"] = [
        c.ModelRegistryRecord(
            identity=c.ModelIdentity(
                model_id=policy_model_id,
                model_digest=slot["model_digest"],
                tokenizer_digest=slot["tokenizer_digest"],
                checkpoint_digest=slot["checkpoint_digest"],
            )
        ).model_dump(mode="json")
    ]
    registry_payload["secret_handles"] = [
        c.SecretHandleRegistryRecord(
            grant=capability.secret_handles[0],
            route_ids=(capability.routes[0].route_id,),
        ).model_dump(mode="json")
    ]
    registry_payload["sandbox_runtimes"] = [
        item.model_dump(mode="json") for item in runtime_records
    ]
    registry_payload["repository_bindings"] = []
    registry_payload["task_datasets"] = [
        c.TaskDatasetRegistryRecord(
            task_contract_digest=capability.task.task_contract_digest,
            task_binding_digest=capability.task.task_binding_digest,
            repository_snapshot_digest=None,
            dataset_digests=(),
            input_artifact_digests=(),
        ).model_dump(mode="json")
    ]
    registry_payload["images"] = [
        image.model_dump(mode="json") for image in image_records
    ]
    registry_payload["verifiers"] = [verifier_record.model_dump(mode="json")]
    attestation = copy.deepcopy(registry_payload["policy_capability_attestations"][0])
    attestation["route_id"] = route_grant["route_id"]
    attestation["route_revision_digest"] = route_grant["route_revision_digest"]
    attestation["capability_digest"] = capability_digest
    attestation["model_digest"] = slot["model_digest"]
    attestation["tokenizer_digest"] = slot["tokenizer_digest"]
    attestation["checkpoint_digest"] = slot["checkpoint_digest"]
    attestation["validity"] = {
        "issued_at": utc_second(now - timedelta(minutes=5)),
        "not_before": utc_second(now - timedelta(minutes=5)),
        "expires_at": utc_second(now + timedelta(hours=3)),
    }
    attestation_projection = {
        "schema_version": "bb.rl.policy-capability-attestation.v1",
        **{
            key: value
            for key, value in attestation.items()
            if key != "attestation_digest"
        },
    }
    attestation["attestation_digest"] = independent_digest(attestation_projection)
    registry_payload["policy_capability_attestations"] = [attestation]
    registry_payload = _rebind_registry_payload(registry_payload)
    registries = c.RegistrySnapshotSet.model_validate(registry_payload)

    validity = c.ValidityWindow(
        issued_at=utc_second(now - timedelta(minutes=5)),
        not_before=utc_second(now - timedelta(minutes=5)),
        expires_at=utc_second(now + timedelta(minutes=30)),
    )
    policy_payload = seed.policy.model_dump(mode="json")
    policy_payload["compiler_constraints"]["allowed_compilers"] = [
        compiler_identity.model_dump(mode="json")
    ]
    policy_payload["registry_digests"] = registries.digests.model_dump(mode="json")
    policy_payload["ceiling"]["runner_bindings"] = [
        capability.runner.model_dump(mode="json")
    ]
    policy_payload["ceiling"]["route_grants"] = [
        item.model_dump(mode="json") for item in capability.routes
    ]
    policy_payload["ceiling"]["secret_handle_grants"] = [
        item.model_dump(mode="json") for item in capability.secret_handles
    ]
    policy_payload["ceiling"]["policy_slot_grants"] = [
        item.model_dump(mode="json") for item in capability.policy_slots
    ]
    policy_payload["ceiling"]["model_bindings"] = [
        c.ModelIdentity(
            model_id=policy_model_id,
            model_digest=slot["model_digest"],
            tokenizer_digest=slot["tokenizer_digest"],
            checkpoint_digest=slot["checkpoint_digest"],
        ).model_dump(mode="json")
    ]
    policy_payload["ceiling"]["setup_grants"] = []
    policy_payload["ceiling"]["repository_snapshot_digests"] = []
    policy_payload["ceiling"]["dataset_digests"] = []
    policy_payload["ceiling"]["sandbox_bindings"] = [
        c.SandboxBinding(
            runtime_id=capability.sandbox.runtime_id,
            runtime_class=capability.sandbox.runtime_class,
            driver_implementation_digest=capability.sandbox.driver_implementation_digest,
            runtime_binary_digest=capability.sandbox.runtime_binary_digest,
            security_policy_digest=capability.sandbox.security_policy_digest,
            image_digest=capability.sandbox.image_digest,
            network_policy_digest=capability.sandbox.network_policy_digest,
        ).model_dump(mode="json")
    ]
    policy_payload["ceiling"]["verifier_grants"] = [
        capability.verifier.model_dump(mode="json")
    ]
    policy_payload["ceiling"]["resource_maxima"] = capability.resources.model_dump(
        mode="json"
    )
    policy_payload["ceiling"]["execution_maxima"] = capability.limits.model_dump(
        mode="json"
    )
    policy_payload["ceiling"]["allowed_egress_route_ids"] = list(
        capability.sandbox.egress_route_ids
    )
    policy_payload["ceiling"]["mount_grants"] = [
        item.model_dump(mode="json") for item in capability.sandbox.mounts
    ]
    policy_payload["required_security"] = {
        "minimum_isolation_class": "trusted_process",
        "required_verifier_isolation_class": "trusted_process",
        "required_evidence_roles": list(capability.artifacts.allowed_roles),
        "prohibited_runtime_classes": [],
        "minimum_retention_seconds": 86_400,
    }
    policy_payload["validity"] = {
        "issued_at": utc_second(now - timedelta(minutes=5)),
        "not_before": utc_second(now - timedelta(minutes=5)),
        "expires_at": utc_second(now + timedelta(hours=3)),
    }
    policy = c.AdmissionPolicySnapshot.model_validate(policy_payload)

    compiled_identity = c.CompiledArtifactIdentity(
        manifest_digest=compiled_digest,
        bundle_digest=compiled_manifest.inputs.bundle_digest,
        closure_digest=compiled_manifest.inputs.closure_digest,
        compiler_input_digest=compiled_manifest.inputs.compiler_input_digest,
        semantic_digest=compiled_manifest.semantic_digest,
        compiler=compiler_identity,
        provenance_digest=independent_digest(
            [item.to_canonical_obj() for item in compiled_manifest.provenance]
        ),
        diagnostics_digest=independent_digest(
            compiled_manifest.diagnostics.to_canonical_obj()
        ),
    )
    request_payload = seed.request.model_dump(mode="json")
    request_payload["compiled"] = compiled_identity.model_dump(mode="json")
    request_payload["behavior_source"] = {
        **request_payload["behavior_source"],
        "manifest_digest": compiled_digest,
        "semantic_digest": compiled_manifest.semantic_digest,
    }
    request_payload["requested_capabilities"] = capability.model_dump(mode="json")
    request_payload["requested_capability_digest"] = capability.canonical_digest()
    request_payload["task_binding_digest"] = capability.task.task_binding_digest
    request_payload["policy_binding_ref"] = {
        "route_id": capability.routes[0].route_id,
        "registry_revision_digest": registries.digests.route_registry_digest,
        "attestation_digest": attestation["attestation_digest"],
    }
    request_payload["admission_policy_digest"] = policy.canonical_digest()
    request_payload["registry_snapshot_digest"] = registries.digests.snapshot_digest
    request_payload["validity"] = validity.model_dump(mode="json")
    admission_request = c.AdmissionRequest.model_validate(request_payload)

    cas = FilesystemCAS(installed_paths.stores["cas"])
    try:
        entries_by_path = {entry.logical_path: entry for entry in bundle.entries}
        for logical_path, payload in config_members.items():
            entry = entries_by_path[logical_path]
            config_ref = cas.put_bytes(
                payload,
                artifact_id=entry.artifact_id,
                media_type=entry.media_type,
            )
            if (
                config_ref.sha256 != entry.blob_digest
                or config_ref.size_bytes != entry.size_bytes
            ):
                raise ValueError("config bundle member CAS identity mismatch")
        pinned_compiler = PinnedServerCompilerAdapter({compiled_digest: compiled_bytes})
        revocations = PinnedRevocationStore((policy.revocation,))
        receipt_store = CASConfigRuntimeStore(cas)
        authenticator = HmacSha256ReceiptAuthenticator(
            key_id="production-receipt-key",
            key=installed_paths.launch_seeds["receipt-signing"],
        )
        runtime = ConfigRuntime(
            compiler=pinned_compiler,
            policy=policy,
            registries=registries,
            revocations=revocations,
            store=receipt_store,
            clock=type("_Clock", (), {"current": lambda self: now})(),
            authenticator=authenticator,
        )
        receipt_ref = runtime.admit(admission_request)
        receipt_bytes = receipt_store.load(
            receipt_ref.digest,
            kind=c.ArtifactKind.ADMISSION_RECEIPT,
            max_bytes=4 * 1024 * 1024,
        )
        receipt = c.AdmissionReceipt.model_validate_json(receipt_bytes)
        admitted_set = c.AdmittedSetManifest(
            compiler_abi=receipt.compiled.compiler.semantic_version,
            admission_policy_digest=receipt.admission_policy_digest,
            operator_ceiling_digest=receipt.operator_ceiling_digest,
            registry_snapshot_digest=receipt.registry_snapshot_digest,
            revocation=receipt.revocation,
            receipt_digests=(receipt_ref.digest,),
            validity=receipt.validity,
        )
        admitted_ref = receipt_store.publish(
            kind=c.ArtifactKind.ADMITTED_SET,
            canonical_bytes=admitted_set.canonical_bytes(),
        )
        generated_name = f"qualification-candidate-{secrets.token_hex(12)}"
        selector = c.DirectSelector(
            admitted_set_root=admitted_ref.sha256,
            compiler_abi=admitted_set.compiler_abi,
            runtime_abi=receipt.compiled.compiler.runtime_abi,
            admission_policy_digest=receipt.admission_policy_digest,
            operator_ceiling_digest=receipt.operator_ceiling_digest,
            candidate=c.ConfigCandidate(
                candidate_id=generated_name,
                receipt_digest=receipt_ref.digest,
                predicates=(),
                overlays=(),
            ),
            validity=receipt.validity,
        )
        selector_runtime_ref = receipt_store.publish(
            kind=c.ArtifactKind.DIRECT_SELECTOR,
            canonical_bytes=selector.canonical_bytes(),
        )

        artifacts = root / "artifacts"
        artifacts.mkdir(mode=0o700)

        def file_ref(name: str, payload: bytes, media_type: str) -> ArtifactFileRefV1:
            path = artifacts / name
            path.write_bytes(payload)
            return ArtifactFileRefV1(
                path=str(path.resolve()),
                sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                media_type=media_type,
            )

        compiled_file_ref = file_ref(
            "compiled-manifest.json",
            compiled_bytes,
            "application/vnd.breadboard.compiled-manifest+json;version=1",
        )
        receipt_file_ref = file_ref(
            "admission-receipt.json",
            receipt_bytes,
            "application/vnd.breadboard.admission-receipt+json;version=1",
        )
        admitted_file_ref = file_ref(
            "admitted-set.json",
            admitted_set.canonical_bytes(),
            "application/vnd.breadboard.admitted-set+json;version=1",
        )
        selector_file_ref = file_ref(
            "direct-selector.json",
            selector.canonical_bytes(),
            "application/vnd.breadboard.direct-selector+json;version=1",
        )
        policy_ref = file_ref(
            "admission-policy.json", policy.canonical_bytes(), "application/json"
        )
        registry_ref = file_ref(
            "registry-snapshot.json", registries.canonical_bytes(), "application/json"
        )
        revocation_bytes = canonical_json_bytes(
            [policy.revocation.model_dump(mode="json")]
        )
        revocation_ref = file_ref(
            "revocations.json", revocation_bytes, "application/json"
        )

        policy_observation = c.PolicyCapabilityObservation(
            registry_revision_digest=registries.digests.route_registry_digest,
            route_id=capability.routes[0].route_id,
            route_revision_digest=capability.routes[0].route_revision_digest,
            provider_id=policy_provider_id,
            protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
            bridge_instance_id="bridge-production",
            bridge_build_digest="sha256:" + "a" * 64,
            model_id=policy_model_id,
            model_digest=slot["model_digest"],
            tokenizer_digest=slot["tokenizer_digest"],
            checkpoint_digest=slot["checkpoint_digest"],
            credential_handle_id=secret_grant["handle_id"],
            credential_handle_version_digest=secret_grant["handle_version_digest"],
            subject_scope_digest=admission_request.subject.authority_scope_digest,
            capabilities=selection_capabilities,
            capability_digest=capability_digest,
            provenance=c.AttestationProvenance(
                kind=c.AttestationKind.STARTUP_PROBE,
                issuer_id="operator-control-plane",
                signer_key_id="startup-key",
                environment_digest="sha256:" + "b" * 64,
                evidence_digest="sha256:" + "c" * 64,
                validity=c.ValidityWindow.model_validate(attestation["validity"]),
            ),
            revocation=policy.revocation,
        )
        capability_bytes = canonical_json_bytes(
            [policy_observation.model_dump(mode="json")]
        )
        capability_ref = file_ref(
            "policy-capabilities.json", capability_bytes, "application/json"
        )
        config_bundle_ref = file_ref(
            "config-bundle.json", bundle.canonical_bytes(), "application/json"
        )
        ca_bytes = _read_resource(
            TLS_ROOT / "ca.cert.pem",
            expected_sha256=TLS_CA_CERTIFICATE_SHA256,
        )
        ca_ref = file_ref("ca.cert.pem", ca_bytes, "application/x-pem-file")
        tls_metadata = _load_json(
            TLS_ROOT / "authority.json",
            expected_sha256=TLS_AUTHORITY_SHA256,
        )
        dns = DNSPolicyDocumentV1(
            dns_policy_digest=route_values["dns_policy_digest"],
            **{**dns_projection, "allowed_addresses": ("127.0.0.1",)},
        )
        ip = IPPolicyDocumentV1(
            ip_policy_digest=route_values["ip_policy_digest"],
            **{**ip_projection, "allowed_addresses": ("127.0.0.1",)},
        )
        schema_authority = PolicyHttpSchemaAuthorityV1(
            schema_version="bb.rl.policy-http-schema-authority.v1",
            protocol_abi=POLICY_HTTP_PROTOCOL_ABI,
            request_schema=POLICY_HTTP_REQUEST_SCHEMA,
            request_schema_digest=POLICY_HTTP_REQUEST_SCHEMA_DIGEST,
            response_schema=POLICY_HTTP_RESPONSE_SCHEMA,
            response_schema_digest=POLICY_HTTP_RESPONSE_SCHEMA_DIGEST,
        )
        policy_binding = PolicySecretRouteBindingV1(
            schema_version="bb.rl.policy-secret-route-binding.v1",
            handle_id=secret_grant["handle_id"],
            handle_version_digest=secret_grant["handle_version_digest"],
            scope_digest=secret_grant["scope_digest"],
            route_ids=(capability.routes[0].route_id,),
        )
        policy_http = PolicyHttpAuthorityGraphV1(
            registry_revision_digest=registries.digests.route_registry_digest,
            routes=(route_record,),
            observations=(policy_observation,),
            dns_policies=(dns,),
            ip_policies=(ip,),
            schema_authority=schema_authority,
            secret_bindings=(policy_binding,),
        )
        tls = PolicyTlsTrustAuthorityV1(
            schema_version="bb.rl.policy-tls-trust-authority.v1",
            route_id=capability.routes[0].route_id,
            server_name="127.0.0.1",
            ca_bundle_ref=ca_ref,
            expected_leaf_certificate_sha256=tls_metadata["server_leaf_der_sha256"],
            minimum_tls_version="TLSv1.3",
            cipher_suite="TLS_AES_256_GCM_SHA384",
            dedicated_single_leaf_ca=True,
        )
        authority = AuthorityBundleV1(
            schema_version="bb.rl.harness-authority-bundle.v1",
            admission_policy=policy,
            registries=registries,
            revocations=(policy.revocation,),
            policy_capabilities=(policy_observation,),
            policy_http=policy_http,
            tls_trust=(tls,),
            compiled_manifest_refs=(compiled_file_ref,),
            admission_receipt_refs=(receipt_file_ref,),
        )
        authority_ref = file_ref(
            "authority-bundle.json", authority.canonical_bytes(), "application/json"
        )

        def directory_ref(name: str):
            path = installed_paths.stores[name]
            current = path.stat(follow_symlinks=False)
            from breadboard.rl.harness.composition import DirectoryAuthorityRefV1

            return DirectoryAuthorityRefV1(
                authority_id=f"production-{name}",
                path=str(path),
                device=current.st_dev,
                inode=current.st_ino,
                owner_uid=current.st_uid,
                mode="0700",
            )

        installed_authority = InstalledV1(
            runner_adapters=(
                RunnerAdapterDescriptor(
                    adapter_id=capability.runner.adapter_id,
                    runtime_abi=capability.runner.runtime_abi,
                    implementation_digest=capability.runner.implementation_digest,
                ),
            ),
            runtimes=installed_runtimes,
            images=installed_images,
            security_policies=installed_security_policies,
            network_policies=installed_network_policies,
            verifiers=installed_verifiers,
        )
        evidence_bindings = tuple(
            EvidenceRoleBindingV2(
                role=role,
                source=EvidenceRoleSourceV2.RUNNER_RESULT,
                producer_id=f"production-{role}",
                producer_implementation_digest=capability.runner.implementation_digest,
            )
            for role in capability.artifacts.allowed_roles
        )
        manifest = HarnessCompositionManifestV1(
            schema_version="bb.rl.harness-composition.v1",
            composition_id="production-fixture-composition",
            authority_bundle_ref=authority_ref,
            config_bundle_ref=config_bundle_ref,
            admitted_set_ref=admitted_file_ref,
            selector_catalog=SelectorCatalogV1(direct=(selector_file_ref,)),
            control_plane=ControlPlaneV1(
                admission_policy_ref=policy_ref,
                registry_snapshot_ref=registry_ref,
                revocation_snapshot_ref=revocation_ref,
                policy_capability_snapshot_ref=capability_ref,
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
                    key_id="production-receipt-key",
                    algorithm="hmac-sha256-v1",
                    secret_handle_id="receipt-signing",
                ),
            ),
            installed=installed_authority,
            stores=StoresV1(
                **{name: directory_ref(name) for name in STORE_NAMES},
                lease_ttl_seconds=300,
            ),
            server=ServerV1(
                host="127.0.0.1",
                port=server_port,
                allow_unauthenticated_loopback=False,
                proxy_headers=False,
                request_timeout_seconds=10.0,
            ),
            secret_handles=SecretHandlesV1(
                records=(
                    SecretHandleSpecV1(handle_id="api-auth", purpose="api_bearer"),
                    SecretHandleSpecV1(
                        handle_id="policy-callback",
                        purpose="policy_callback",
                        route_ids=(capability.routes[0].route_id,),
                    ),
                    SecretHandleSpecV1(
                        handle_id="receipt-signing", purpose="receipt_signer"
                    ),
                )
            ),
            evidence_bindings=evidence_bindings,
        )
        manifest_path = artifacts / "composition-manifest.json"
        manifest_bytes = manifest.canonical_bytes()
        manifest_path.write_bytes(manifest_bytes)
        manifest_ref = CompositionRefV1(
            schema_version="bb.rl.harness-composition-ref.v1",
            manifest_path=str(manifest_path.resolve()),
            manifest_sha256="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            manifest_size_bytes=len(manifest_bytes),
            manifest_media_type="application/vnd.breadboard.harness-composition+json;version=1",
        )
        composition_ref_path = artifacts / "composition-ref.json"
        composition_ref_path.write_bytes(manifest_ref.canonical_bytes())
    finally:
        cas.close()

    runtime_path = Path(installed_runtimes[0].executable_path)
    runtime_stat = runtime_path.stat(follow_symlinks=False)
    verifier_stat = verifier_path.stat(follow_symlinks=False)
    response_payload = (
        {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "hold-open",
                    "name": "shell",
                    "arguments": '{"command":"sleep 30"}',
                }
            ]
        }
        if long_running
        else {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "write-task-output",
                    "name": "shell",
                    "arguments": json.dumps(
                        {
                            "command": (
                                'printf \'{"answer":"breadboard-production-fixture"}\' '
                                "> task-output.json"
                            )
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ]
        }
    )
    policy_response = {
        "response_digest": independent_digest(response_payload),
        "response_payload": response_payload,
    }
    resolution_request = c.ResolveEpisodeRequest(
        episode_id="production-fixture-episode",
        subject=admission_request.subject,
        selector=c.DirectSelectorRef(
            digest=selector_runtime_ref.sha256,
            ref=selector_runtime_ref,
        ),
        selection_nonce=None,
        task=task_input,
        policy_binding=admission_request.policy_binding_ref,
        episode_overlays=(),
    )
    resolution_body = resolution_request.model_dump(mode="json")
    create_body = {
        "schema_version": "bb.rl.episode.v2",
        "resolution": resolution_body,
    }
    secret_paths = MappingProxyType(dict(installed_paths.secrets))
    return MaterializedProductionCompositionFixture(
        composition_ref_path=composition_ref_path,
        composition_manifest_path=manifest_path,
        object_cas_root=installed_paths.stores["cas"],
        installed_roots=installed_paths.stores,
        secret_paths=secret_paths,
        secret_files=MappingProxyType(
            {key: str(value) for key, value in secret_paths.items()}
        ),
        secret_seed_bytes=installed_paths.launch_seeds,
        tls_server_key_path=installed_paths.tls_server_key,
        api_bearer=installed_paths.launch_seeds["api-auth"].decode(),
        tls_server_certificate_path=installed_paths.tls_server_certificate,
        tls_ca_certificate_path=installed_paths.tls_ca_certificate,
        policy_callback_secret=installed_paths.launch_seeds["policy-callback"].decode(),
        server_host="127.0.0.1",
        server_port=server_port,
        policy_server_host="127.0.0.1",
        policy_server_port=policy_server_port,
        generated_candidate_name=generated_name,
        profile_name="production-fixture-profile",
        expected_executable_identity=ExecutableIdentity(
            path=runtime_path,
            sha256=installed_runtimes[0].measured_binary_digest,
            device=runtime_stat.st_dev,
            inode=runtime_stat.st_ino,
        ),
        verifier_executable_identity=ExecutableIdentity(
            path=verifier_path,
            sha256=verifier_binary_digest,
            device=verifier_stat.st_dev,
            inode=verifier_stat.st_ino,
        ),
        selector_digest=selector_runtime_ref.sha256,
        create_body=MappingProxyType(create_body),
        policy_response_body=MappingProxyType(policy_response),
        policy_observation=MappingProxyType(policy_observation.model_dump(mode="json")),
        cleanup_paths=(),
    )
