from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    HarnessCompositionManifestV1,
    TlsCallbackRuntimeInputV1,
)
from breadboard.rl.state.cas import FilesystemCAS
from breadboard.rl.harness.config_runtime import ConfigRuntime
from breadboard.rl.phase5.f2_authority_authoring import (
    F2AuthorityAuthoringError,
    F2C4DynamicAuthorityInput,
    F2C4StaticAuthorityInput,
    F2C4SemanticInput,
    TlsPrivateKeyRuntimeHandoffV1,
    F2C4TargetDynamicObservations,
    TlsCallbackLiveHandoffV1,
    TlsCallbackSocketRuntimeHandoffV1,
    author_f2_target_dynamic_authority,
    author_f2_operator_input,
    _read_canonical_input,
    _read_secret_0400,
    _validate_operator_source,
    _write_exclusive,
    C4CallbackAuthority,
    C4ModelIdentity,
    C4PolicyAuthority,
    C4TaskIdentity,
    _derive_c4,
    _compile_c4_config,
    _receipt_validity,
    ExternalArtifact,
    materialize_f2_c4_semantic_input,
)


def _incomplete_semantic(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "bb.rl.phase5-f2-c4-semantic-input.v1",
        "composition_id": "c4",
        "attempt_id": "attempt-1",
        "prompt": "Complete the fixed terminal task.",
        "shell_command": "printf 'breadboard-f2-terminal-ok\\n' > /workspace/work/result.txt",
        "completion": "F2 terminal episode complete",
    }
    value.update(updates)
    return value


@pytest.mark.parametrize(
    "injected",
    [
        {"registry_records": {}},
        {"admission_policy_template": {}},
        {"compile_options": {}},
        {"config_members": []},
        {"dependency_edges": []},
        {"policy_capabilities": []},
        {"policy_http": {}},
        {"selector": {}},
        {"overlays": []},
        {"row_route": "https://attacker.invalid"},
    ],
)
def test_closed_c4_input_rejects_authority_injection(injected: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as raised:
        F2C4SemanticInput.model_validate(_incomplete_semantic(**injected))
    assert any(
        error["type"] == "extra_forbidden" and error["loc"] == (next(iter(injected)),)
        for error in raised.value.errors()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shell_command", "printf pwned"),
        ("completion", "looks done"),
        ("schema_version", "bb.rl.phase5-f2-c4-semantic-input.v2"),
    ],
)
def test_closed_c4_input_rejects_alternate_fixed_semantics(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as raised:
        F2C4SemanticInput.model_validate(_incomplete_semantic(**{field: value}))
    assert any(error["loc"] == (field,) and error["type"] == "literal_error" for error in raised.value.errors())


def test_canonical_input_rejects_one_byte_mutation(tmp_path: Path) -> None:
    path = tmp_path / "semantic.json"
    canonical = canonical_json_bytes(_incomplete_semantic())
    path.write_bytes(canonical)
    assert _read_canonical_input(os.fspath(path)) == canonical
    path.write_bytes(canonical + b" ")
    with pytest.raises(F2AuthorityAuthoringError, match="canonical JSON"):
        _read_canonical_input(os.fspath(path))


def test_signer_requires_regular_0400_descriptor(tmp_path: Path) -> None:
    secret = tmp_path / "receipt.key"
    secret.write_bytes(b"x" * 32)
    secret.chmod(0o400)
    assert _read_secret_0400(os.fspath(secret)) == b"x" * 32
    secret.chmod(0o600)
    with pytest.raises(F2AuthorityAuthoringError, match="0400"):
        _read_secret_0400(os.fspath(secret))


def test_descriptor_writer_is_exclusive_and_complete(tmp_path: Path) -> None:
    directory_fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        _write_exclusive(directory_fd, "object.json", b'{"a":1}')
        with pytest.raises(FileExistsError):
            _write_exclusive(directory_fd, "object.json", b'{"a":2}')
    finally:
        os.close(directory_fd)
    assert (tmp_path / "object.json").read_bytes() == b'{"a":1}'


def test_operator_source_validation_uses_strict_canonical_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard.rl.phase5.f2_composition import F2ProductionCompositionInput

    operator = {
        "schema_version": "bb.rl.phase5-f2-production-input.v1",
        "evidence_bindings": [
            {
                "schema_version": "bb.rl.evidence-role-binding.v2",
                "role": "terminal-result",
                "source": "verifier_result",
                "producer_id": "exact-output",
                "producer_implementation_digest": "sha256:" + "1" * 64,
            }
        ],
        "prebound_service_socket_plans": [],
    }
    observed: list[tuple[bytes, bool]] = []

    def validate_json(raw: bytes, *, strict: bool) -> SimpleNamespace:
        observed.append((raw, strict))
        return SimpleNamespace(model_dump=lambda **_kwargs: operator)

    monkeypatch.setattr(F2ProductionCompositionInput, "model_validate_json", validate_json)
    assert _validate_operator_source(operator) == canonical_json_bytes(operator)
    assert observed == [(canonical_json_bytes(operator), True)]

def test_author_refuses_existing_destination_before_reading_input(tmp_path: Path) -> None:
    destination = tmp_path / "published"
    destination.mkdir()
    sentinel = destination / "owned"
    sentinel.write_bytes(b"operator-owned")
    with pytest.raises(F2AuthorityAuthoringError, match="already exists"):
        author_f2_operator_input(
            semantic_input_path=os.fspath(tmp_path / "missing.json"),
            output_dir=os.fspath(destination),
        )
    assert sentinel.read_bytes() == b"operator-owned"


@pytest.mark.parametrize(
    ("model", "payload", "forbidden"),
    [
        (
            F2C4StaticAuthorityInput,
            {"schema_version": "bb.rl.phase5-f2-c4-static-authority-input.v1", "stores": {}},
            "stores",
        ),
        (
            F2C4DynamicAuthorityInput,
            {"schema_version": "bb.rl.phase5-f2-c4-dynamic-authority-input.v1", "wrapper_image_build_report": {}},
            "wrapper_image_build_report",
        ),
    ],
)
def test_static_dynamic_models_reject_cross_phase_authority(
    model: type, payload: dict[str, object], forbidden: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        model.model_validate(payload)
    assert any(error["loc"] == (forbidden,) and error["type"] == "extra_forbidden" for error in raised.value.errors())


def test_semantic_materializer_requires_typed_dynamic_fragment(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="F2C4DynamicAuthorityInput"):
        materialize_f2_c4_semantic_input(
            os.fspath(tmp_path / "missing-static.json"),
            {},  # type: ignore[arg-type]
            os.fspath(tmp_path / "semantic.json"),
        )


def test_target_dynamic_author_requires_typed_same_process_observations() -> None:
    with pytest.raises(TypeError, match="exact typed plan and observations"):
        author_f2_target_dynamic_authority({}, {})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("model", "schema_version"),
    [
        (F2C4SemanticInput, "bb.rl.phase5-f2-c4-semantic-input.v1"),
        (F2C4DynamicAuthorityInput, "bb.rl.phase5-f2-c4-dynamic-authority-input.v1"),
        (
            F2C4TargetDynamicObservations,
            "bb.rl.phase5-f2-c4-target-dynamic-observations.v1",
        ),
        (
            HarnessCompositionManifestV1,
            "bb.rl.harness-composition.v1",
        ),
    ],
)
def test_strict_json_authorities_reject_non_string_evidence_identity(
    model: type, schema_version: str
) -> None:
    payload = canonical_json_bytes(
        {
            "schema_version": schema_version,
            "evidence_bindings": [
                {
                    "schema_version": "bb.rl.evidence-role-binding.v2",
                    "role": "terminal-result",
                    "source": "verifier_result",
                    "producer_id": 1,
                    "producer_implementation_digest": "sha256:" + "1" * 64,
                }
            ],
        }
    )
    with pytest.raises(ValidationError) as raised:
        model.model_validate_json(payload, strict=True)
    assert any(
        error["loc"] == ("evidence_bindings",)
        and "wire values are not exact strings" in error["msg"]
        for error in raised.value.errors()
    )


def test_tls_key_handoff_rejects_closed_descriptor(tmp_path: Path) -> None:
    key = tmp_path / "leaf.key"
    payload = b"x" * 32
    key.write_bytes(payload)
    key.chmod(0o400)
    descriptor = os.open(key, os.O_RDONLY)
    info = os.fstat(descriptor)
    handoff = TlsPrivateKeyRuntimeHandoffV1(
        path=os.fspath(key), descriptor_fd=descriptor,
        device=info.st_dev, inode=info.st_ino, ctime_ns=info.st_ctime_ns,
        size_bytes=info.st_size, mode=0o400, owner_uid=info.st_uid,
        private_key_sha256="sha256:" + __import__("hashlib").sha256(payload).hexdigest(),
        leaf_certificate_sha256="sha256:" + "1" * 64,
        leaf_public_key_sha256="sha256:" + "2" * 64,
    )
    os.close(descriptor)
    with pytest.raises(F2AuthorityAuthoringError, match="no longer live"):
        handoff.validate_live()


def test_live_callback_rejects_fd_key_cert_and_secret_mutations(tmp_path: Path) -> None:
    key_path = tmp_path / "leaf.pk8"
    key_payload = b"k" * 64
    key_path.write_bytes(key_payload)
    key_path.chmod(0o400)
    key_fd = os.open(key_path, os.O_RDONLY)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    try:
        key_info = os.fstat(key_fd)
        socket_info = os.fstat(listener.fileno())
        host, port = listener.getsockname()
        leaf_digest = "sha256:" + "1" * 64
        public_key_digest = "sha256:" + "2" * 64
        key_handoff = TlsPrivateKeyRuntimeHandoffV1(
            path=os.fspath(key_path), descriptor_fd=key_fd,
            device=key_info.st_dev, inode=key_info.st_ino, ctime_ns=key_info.st_ctime_ns,
            size_bytes=key_info.st_size, mode=0o400, owner_uid=key_info.st_uid,
            private_key_sha256="sha256:" + hashlib.sha256(key_payload).hexdigest(),
            leaf_certificate_sha256=leaf_digest,
            leaf_public_key_sha256=public_key_digest,
        )
        socket_handoff = TlsCallbackSocketRuntimeHandoffV1(
            descriptor_fd=listener.fileno(), gateway=host, observed_port=port,
            socket_device=socket_info.st_dev, socket_inode=socket_info.st_ino,
            socket_mode=socket_info.st_mode, socket_owner_uid=socket_info.st_uid,
        )
        socket_plan_id = "sha256:" + "4" * 64
        runtime = TlsCallbackRuntimeInputV1.model_construct(
            route_id="f2-fixed-policy-callback", host=host, observed_port=port,
            socket_role="callback_tls", socket_plan_id=socket_plan_id,
            ca_certificate_sha256="sha256:" + "3" * 64,
            leaf_certificate_sha256=leaf_digest,
            leaf_public_key_sha256=public_key_digest,
            private_key_secret_handle_id="tls-key",
        )
        callback_plan = SimpleNamespace(
            role="callback_tls", gateway=host, observed_port=port,
            socket_plan_id=socket_plan_id, socket_device=socket_info.st_dev,
            socket_inode=socket_info.st_ino, socket_mode=socket_info.st_mode,
            socket_owner_uid=socket_info.st_uid,
        )
        dynamic = SimpleNamespace(
            tls=SimpleNamespace(
                route_id="f2-fixed-policy-callback",
                ca_certificate=SimpleNamespace(sha256="sha256:" + "3" * 64),
                leaf_certificate=SimpleNamespace(sha256=leaf_digest),
            ),
            outer_bridge_plan=SimpleNamespace(gateway=host),
            prebound_service_socket_plans=(callback_plan,),
            callback=SimpleNamespace(target_ip=host, port=port),
            tls_leaf_public_key_sha256=public_key_digest,
            secret_handles=SimpleNamespace(records=(
                SimpleNamespace(handle_id="tls-key", purpose="callback_tls_private_key"),
            )),
        )
        live = TlsCallbackLiveHandoffV1(runtime, key_handoff, socket_handoff)
        live.validate_against(dynamic)
        with pytest.raises(F2AuthorityAuthoringError, match="private key digest"):
            dataclasses.replace(live, tls_private_key=dataclasses.replace(key_handoff, private_key_sha256="sha256:" + "0" * 64)).validate_against(dynamic)
        with pytest.raises(F2AuthorityAuthoringError, match="does not bind"):
            live.validate_against(SimpleNamespace(**{**dynamic.__dict__, "tls": SimpleNamespace(**{**dynamic.tls.__dict__, "leaf_certificate": SimpleNamespace(sha256="sha256:" + "9" * 64)})}))
        with pytest.raises(F2AuthorityAuthoringError, match="does not bind"):
            live.validate_against(SimpleNamespace(**{**dynamic.__dict__, "secret_handles": SimpleNamespace(records=(SimpleNamespace(handle_id="tls-key", purpose="policy_callback"),))}))
        with pytest.raises(F2AuthorityAuthoringError, match="does not bind"):
            dataclasses.replace(
                live,
                runtime_input=runtime.model_copy(update={"socket_plan_id": "sha256:" + "5" * 64}),
            ).validate_against(dynamic)
        listener.close()
        with pytest.raises(F2AuthorityAuthoringError, match="no longer live"):
            live.validate_against(dynamic)
    finally:
        os.close(key_fd)
        listener.close()


@pytest.mark.parametrize("field", [
    "callback_socket_fd",
    "tls_private_key_path", "tls_private_key_bytes",
    "callback_observation_signing_key_path",
    "callback_observation_signing_key_bytes",
    "callback_observation_signing_key_fd",
    "evidence_receipt_private_key_path",
    "evidence_receipt_private_key_bytes",
    "evidence_receipt_private_key_fd",
])
def test_dynamic_observations_reject_persisted_fd_or_key_fields(field: str) -> None:
    with pytest.raises(ValidationError) as raised:
        F2C4TargetDynamicObservations.model_validate({
            "schema_version": "bb.rl.phase5-f2-c4-target-dynamic-observations.v1",
            field: 7 if field.endswith("_fd") else "forbidden",
        })
    assert any(error["loc"] == (field,) and error["type"] == "extra_forbidden" for error in raised.value.errors())


def test_author_failure_removes_staging_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    semantic = tmp_path / "semantic.json"
    semantic.write_bytes(b"{}")
    external_path = tmp_path / "external.json"
    payload = b"{}"
    external_path.write_bytes(payload)
    external = ExternalArtifact(
        path=os.fspath(external_path),
        sha256="sha256:" + __import__("hashlib").sha256(payload).hexdigest(),
    )
    external_path.chmod(0o700)
    info = external_path.stat()
    version_stdout = b"OpenSSL test\n"
    openssl = SimpleNamespace(
        path=os.fspath(external_path),
        sha256=external.sha256,
        device=info.st_dev,
        inode=info.st_ino,
        ctime_ns=info.st_ctime_ns,
        size_bytes=info.st_size,
        mode=0o700,
        owner_uid=info.st_uid,
        version_stdout_sha256="sha256:" + __import__("hashlib").sha256(version_stdout).hexdigest(),
        version="OpenSSL test",
        discovery_report=external,
    )
    wrapper_host_executables = SimpleNamespace(
        cleanup_python=openssl,
        sudo=openssl,
        env=openssl,
        docker=openssl,
        binary_discovery_report=external,
    )
    spec = SimpleNamespace(
        f1_prerequisite_report=external,
        ibm_target_record=external,
        host_runtime_build_report=external,
        wrapper_image_build_report=external,
        wrapper_image_operator_authorization=external,
        mount_broker_implementation=external,
        wrapper_host_executables=wrapper_host_executables,
        openssl=openssl,
    )
    monkeypatch.setattr(
        "breadboard.rl.phase5.f2_authority_authoring.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=version_stdout, stderr=b""),
    )
    monkeypatch.setattr(F2C4SemanticInput, "model_validate_json", lambda *_args, **_kwargs: spec)
    monkeypatch.setattr(
        "breadboard.rl.phase5.f2_authority_authoring._derive_c4",
        lambda _spec: (_ for _ in ()).throw(RuntimeError("injected authoring failure")),
    )
    destination = tmp_path / "output"
    with pytest.raises(RuntimeError, match="injected authoring failure"):
        author_f2_operator_input(os.fspath(semantic), os.fspath(destination))
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".output.authoring-*"))


def test_fixed_c4_derivation_closes_all_production_authority_joins_and_compiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = lambda character: "sha256:" + character * 64
    runner = SimpleNamespace(adapter_id="terminal", runtime_abi="terminal.v1", implementation_digest=digest("1"))
    primary = SimpleNamespace(
        runtime_id="primary",
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest=digest("2"),
        measured_binary_digest=digest("3"),
    )
    verifier_runtime = SimpleNamespace(
        runtime_id="verifier-runtime",
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest=digest("2"),
        measured_binary_digest=digest("3"),
    )
    images = (
        SimpleNamespace(runtime_id="primary", image_digest=digest("5")),
        SimpleNamespace(runtime_id="verifier-runtime", image_digest=digest("a")),
    )
    security_policies = (
        SimpleNamespace(policy_digest=digest("4")),
        SimpleNamespace(policy_digest=digest("9")),
    )
    network_policies = (
        SimpleNamespace(
            policy_digest=digest("b"),
            mode="none",
            default_deny=True,
            egress_route_ids=(),
        ),
    )
    verifier_grant = c.VerifierGrant(
        verifier_id="exact-output", implementation_digest=digest("c"), image_digest=digest("a"),
        executable_digest=digest("d"), code_digest=digest("e"), input_schema_digest=digest("f"),
        result_schema_digest=digest("0"), network_policy_digest=digest("b"), secret_handle_ids=(),
    )
    verifier = SimpleNamespace(
        grant=verifier_grant,
        runtime_id="verifier-runtime",
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        security_policy_digest=digest("9"),
    )
    validity = c.ValidityWindow(
        issued_at="2026-07-12T00:00:00Z", not_before="2026-07-12T00:00:01Z",
        expires_at="2026-07-12T02:00:01Z",
    )
    revocation = c.RevocationBinding(scope_digest=digest("1"), epoch=1, state_digest=digest("2"))
    policy = C4PolicyAuthority(
        subject=c.AuthenticatedSubject(tenant_id="tenant", principal_id="operator", authority_scope_digest=digest("1")),
        validity=validity, revocation=revocation, receipt_ttl_seconds=300,
        evidence_policy_revision_digest=digest("3"), retention_policy_revision_digest=digest("4"),
        retention_minimum_seconds=60, retention_maximum_seconds=3600,
    )
    receipt_validity = _receipt_validity(policy)
    assert receipt_validity == c.ValidityWindow(
        issued_at="2026-07-12T00:00:00Z",
        not_before="2026-07-12T00:00:01Z",
        expires_at="2026-07-12T00:05:01Z",
    )
    assert policy.validity.expires_at == "2026-07-12T02:00:01Z"
    spec = SimpleNamespace(
        prompt="Complete the fixed terminal task.",
        installed=SimpleNamespace(
            runner_adapters=(runner,),
            runtimes=(primary, verifier_runtime),
            images=images,
            security_policies=security_policies,
            network_policies=network_policies,
            verifiers=(verifier,),
        ),
        primary_runtime_id="primary", verifier_id="exact-output", tool_implementation_digest=digest("5"),
        callback=C4CallbackAuthority(target_ip="10.20.0.2", port=8443, protocol_abi="breadboard-policy-http-v1", owner_id="operator", secret_handle_id="callback", secret_handle_version_digest=digest("6")),
        policy=policy, model=C4ModelIdentity(model_digest=digest("7"), tokenizer_digest=digest("8"), checkpoint_digest=digest("9")),
        task=C4TaskIdentity(task_contract_digest=digest("a"), task_binding_digest=digest("b")),
        resources=c.ResourceLimits(cpu_millis=1000, memory_bytes=1_000_000, pids=64, storage_bytes=1_000_000, open_files=64, wall_time_ms=30_000),
        limits=c.ExecutionLimits(max_turns=1, action_timeout_ms=10_000, observation_bytes=4096, response_bytes=4096, artifact_bytes_each=4096, artifact_bytes_total=4096, transcript_bytes=8192, setup_timeout_ms=1000, verifier_timeout_ms=10_000),
        artifact_policy=c.ArtifactPolicyGrant(allowed_roles=("terminal-result",), max_each_bytes=4096, max_total_bytes=4096),
        receipt_signer=SimpleNamespace(key_id="receipt-key"), attempt_id="attempt-1",
        ibm_target_record=SimpleNamespace(sha256=digest("d")),
    )
    capability, registries, observations, policy_http, ceiling = _derive_c4(spec)
    assert capability.sandbox.runtime_binary_digest == digest("3")
    assert capability.sandbox.security_policy_digest == digest("4")
    assert capability.tools[0].tool_id == "shell"
    assert registries.digests.snapshot_digest == c.RegistrySnapshotSet.derive_snapshot_digest(
        registries.digests.model_dump(mode="json", exclude={"snapshot_digest"})
    )
    assert observations == policy_http.observations
    assert ceiling.verifier_grants == (verifier_grant,)
    assert registries.policy_capability_attestations[0].validity == policy.validity
    cas = FilesystemCAS(tmp_path / "cas")
    try:
        manifest, _bundle, member_bytes = _compile_c4_config(spec, capability, cas)
    finally:
        cas.close()
    assert tuple(sorted(member_bytes)) == (
        "base-config.json",
        "c4-terminal-direct.json",
        "tools/shell.yaml",
    )
    compiled = json.loads(manifest.canonical_bytes())
    assert compiled["semantic"]["tools"]["selected_tool_ids"] == ["shell"]

    executable_path = tmp_path / "observed-executable"
    executable_payload = b"observed executable bytes"
    executable_path.write_bytes(executable_payload)
    executable_path.chmod(0o700)
    executable_info = executable_path.stat()
    executable_ref = "sha256:" + hashlib.sha256(executable_payload).hexdigest()
    external = ExternalArtifact(path=os.fspath(executable_path), sha256=executable_ref)
    version_stdout = b"OpenSSL test\n"
    observed_executable = SimpleNamespace(
        path=os.fspath(executable_path),
        sha256=executable_ref,
        device=executable_info.st_dev,
        inode=executable_info.st_ino,
        ctime_ns=executable_info.st_ctime_ns,
        size_bytes=executable_info.st_size,
        mode=0o700,
        owner_uid=executable_info.st_uid,
        version_stdout_sha256="sha256:" + hashlib.sha256(version_stdout).hexdigest(),
        version="OpenSSL test",
        discovery_report=external,
    )
    spec.f1_prerequisite_report = external
    spec.ibm_target_record = external
    spec.host_runtime_build_report = external
    spec.wrapper_image_build_report = external
    spec.wrapper_image_operator_authorization = external
    spec.mount_broker_implementation = external
    spec.wrapper_host_executables = SimpleNamespace(
        cleanup_python=observed_executable,
        sudo=observed_executable,
        env=observed_executable,
        docker=observed_executable,
        binary_discovery_report=external,
    )
    spec.openssl = observed_executable
    receipt_secret = tmp_path / "receipt-secret"
    receipt_secret.write_bytes(b"r" * 32)
    receipt_secret.chmod(0o400)
    spec.receipt_signer = SimpleNamespace(
        key_id="receipt-key",
        secret_path=os.fspath(receipt_secret),
    )
    semantic = tmp_path / "semantic.json"
    semantic.write_bytes(b"{}")
    monkeypatch.setattr(
        F2C4SemanticInput,
        "model_validate_json",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(
        "breadboard.rl.phase5.f2_authority_authoring.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=version_stdout, stderr=b""),
    )
    actual_admit = ConfigRuntime.admit
    admitted_requests: list[c.AdmissionRequest] = []
    admitted_policies: list[c.AdmissionPolicySnapshot] = []
    admitted_receipts: list[c.AdmissionReceipt] = []

    def capture_real_admission(
        runtime: ConfigRuntime,
        request: c.AdmissionRequest,
    ) -> c.ArtifactRef:
        admitted_requests.append(request)
        admitted_policies.append(runtime._policy)
        receipt_ref = actual_admit(runtime, request)
        receipt_bytes = runtime._store.load(
            receipt_ref.digest,
            kind=c.ArtifactKind.ADMISSION_RECEIPT,
            max_bytes=4 * 1024 * 1024,
        )
        admitted_receipts.append(
            c.AdmissionReceipt.model_validate_json(receipt_bytes, strict=True)
        )
        raise RuntimeError("stop after observed real admission")

    monkeypatch.setattr(ConfigRuntime, "admit", capture_real_admission)
    with pytest.raises(RuntimeError, match="stop after observed real admission"):
        author_f2_operator_input(
            os.fspath(semantic),
            os.fspath(tmp_path / "authored"),
        )
    assert len(admitted_requests) == len(admitted_policies) == len(admitted_receipts) == 1
    admitted_request = admitted_requests[0]
    assert admitted_policies[0].validity == policy.validity
    assert admitted_request.validity == receipt_validity
    assert admitted_receipts[0].validity == receipt_validity
    assert admitted_request.requested_capability_digest == capability.canonical_digest()
    assert admitted_request.registry_snapshot_digest == registries.digests.snapshot_digest
    assert (
        admitted_request.policy_binding_ref.attestation_digest
        == registries.policy_capability_attestations[0].attestation_digest
    )


def test_authoring_module_has_no_fixture_or_test_schema_dependency() -> None:
    module = Path(__file__).parents[3] / "breadboard" / "rl" / "phase5" / "f2_authority_authoring.py"
    source = module.read_text(encoding="utf-8")
    assert "production_composition_fixture" not in source
    assert "tests.rl" not in source
    assert "tests.compilation" not in source
    assert "registry_records" not in source
    assert "admission_policy_template" not in source
