from __future__ import annotations

import os
import shutil
import ssl
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    CASConfigRuntimeStore,
    HmacSha256ReceiptAuthenticator,
    HarnessCompositionManifestV1,
    InstalledV1,
    OfflineImageAuthorityV1,
    PinnedFileAuthorityV1,
    PrivateDockerDaemonAuthorityV1,
    SecretHandleSpecV1,
    SecretHandlesV1,
    ServerV1,
    PinnedRevocationStore,
    PinnedServerCompilerAdapter,
    _PinnedPolicyCapabilityRegistry,
)
from breadboard.rl.harness.evidence import EvidenceRoleBindingV2, EvidenceRoleSourceV2
from breadboard.rl.harness.config_runtime import ConfigRuntime
from breadboard.rl.harness.runners.base import RunnerAdapterDescriptor
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_IMPLEMENTATION_DIGEST,
    TERMINAL_RUNTIME_ABI,
)
from breadboard.rl.harness.sandbox import (
    InstalledImage,
    InstalledRuntime,
    InstalledVerifier,
    SandboxNetworkPolicy,
    SandboxSecurityPolicy,
)
import breadboard.rl.phase5.f3_composition as f3_composition
from breadboard.rl.phase5.f3_authority_authoring import F3AuthorityInput, build_f3_authority
from breadboard.rl.phase5.f3_composition import (
    F3CompositionError,
    F3ProductionCompositionInput,
    PolicyTlsAuthorityInput,
    ReceiptAuthority,
    SecretAuthorities,
    SecretFileAuthority,
    SourceArtifact,
    StorePaths,
    build_f3_production_composition,
    sha256_bytes,
)
from breadboard.rl.state.cas import FilesystemCAS
from tests.rl.phase5.test_f3_authority_authoring import _spec as authority_spec


def _file(path: Path, payload: bytes, mode: int) -> tuple[str, str]:
    path.write_bytes(payload)
    path.chmod(mode)
    return os.fspath(path.resolve()), sha256_bytes(payload)


def _pinned(path: str, digest: str, mode: int) -> PinnedFileAuthorityV1:
    return PinnedFileAuthorityV1(
        path=path,
        digest=digest,
        owner_uid=os.stat(path, follow_symlinks=False).st_uid,
        mode=mode,
        executable=bool(mode & 0o111),
    )


def _composition_spec(tmp_path: Path) -> tuple[F3ProductionCompositionInput, Path]:
    binary_path, binary_digest = _file(tmp_path / "docker", b"f3-docker-binary\n", 0o500)
    runc_path, runc_digest = _file(tmp_path / "runc", b"f3-runc-binary\n", 0o500)
    dockerd_path, dockerd_digest = _file(tmp_path / "dockerd", b"f3-dockerd-binary\n", 0o500)
    containerd_path, containerd_digest = _file(tmp_path / "containerd", b"f3-containerd-binary\n", 0o500)
    archive_path, archive_digest = _file(tmp_path / "images.tar", b"f3-primary-and-verifier-images\n", 0o400)

    raw = authority_spec(tmp_path).model_dump(mode="json")
    runtime_source = {"immutable_reference": f"file://docker@{binary_digest}", "digest": binary_digest}
    oci_source = {"immutable_reference": f"file://runc@{runc_digest}", "digest": runc_digest}
    raw["primary_runtime"].update(
        driver_implementation_digest="sha256:" + "d" * 64,
        runtime_binary=runtime_source,
        oci_runtime_binary=oci_source,
    )
    raw["verifier_runtime"].update(
        driver_implementation_digest="sha256:" + "d" * 64,
        runtime_binary=runtime_source,
        oci_runtime_binary=oci_source,
    )
    spec = F3AuthorityInput.model_validate_json(canonical_json_bytes(raw), strict=True)
    authority_root = tmp_path / "authority"
    authority_manifest_path = Path(build_f3_authority(spec, os.fspath(authority_root.resolve())))
    authority_manifest_raw = authority_manifest_path.read_bytes()

    primary_image = spec.primary_image
    verifier_image = spec.verifier_image
    network = SandboxNetworkPolicy(
        policy_digest=spec.network.policy_digest,
        mode="none",
        docker_network="none",
        egress_route_ids=(),
        default_deny=True,
    )
    seccomp = b'{"defaultAction":"SCMP_ACT_ERRNO"}'
    seccomp_digest = sha256_bytes(seccomp)

    def security(authority: object) -> SandboxSecurityPolicy:
        return SandboxSecurityPolicy(
            policy_digest=authority.policy_digest,
            uid=authority.uid,
            gid=authority.gid,
            read_only_root=True,
            drop_all_capabilities=True,
            no_new_privileges=True,
            seccomp_bytes=seccomp,
            seccomp_digest=seccomp_digest,
            apparmor_profile=authority.lsm_profile,
            selinux_label=None,
            namespace_flags=("ipc", "mount", "network", "pid", "uts"),
            privileged=False,
            devices=(),
            docker_socket_forbidden=True,
            tmpfs_mounts=(("/tmp", "rw,nosuid,nodev,noexec,size=64m"),),
            snapshot_max_depth=32,
            snapshot_max_files=100000,
            snapshot_max_inodes=100000,
        )

    runtime_common = dict(
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        driver_implementation_digest=spec.primary_runtime.driver_implementation_digest,
        executable_path=binary_path,
        measured_binary_digest=binary_digest,
        oci_runtime_name="breadboard-runc",
        supported_platform_versions=("linux/amd64",),
        fixed_environment=(),
        idle_argv=("sh", "-lc", "trap : TERM INT; sleep infinity & wait"),
        runsc_binary_path=None,
        runsc_binary_digest=None,
        oci_runtime_binary_path=runc_path,
        oci_runtime_binary_digest=runc_digest,
    )
    runtimes = tuple(
        sorted(
            (
                InstalledRuntime(runtime_id=spec.primary_runtime.runtime_id, **runtime_common),
                InstalledRuntime(runtime_id=spec.verifier_runtime.runtime_id, **runtime_common),
            ),
            key=lambda item: item.runtime_id,
        )
    )
    images = tuple(
        sorted(
            (
                InstalledImage(primary_image.image_digest, primary_image.runtime_id, primary_image.immutable_reference),
                InstalledImage(verifier_image.image_digest, verifier_image.runtime_id, verifier_image.immutable_reference),
            ),
            key=lambda item: item.image_digest,
        )
    )
    securities = tuple(sorted((security(spec.primary_security), security(spec.verifier_security)), key=lambda item: item.policy_digest))
    verifier = InstalledVerifier(
        grant=spec.verifier.grant,
        runtime_id=spec.verifier.runtime_id,
        runtime_class=c.RuntimeClass.HARDENED_DOCKER,
        security_policy_digest=spec.verifier.security_policy_digest,
        argv=("/opt/r-swe/verify", "--snapshot", "/workspace/snapshot", "--result", "/workspace/result/result.json"),
        result_relative_path="result.json",
        executable_digest=spec.verifier.grant.executable_digest,
        code_digest=spec.verifier.grant.code_digest,
        input_schema_digest=spec.verifier.grant.input_schema_digest,
        result_schema_digest=spec.verifier.grant.result_schema_digest,
    )
    daemon = PrivateDockerDaemonAuthorityV1(
        daemon_instance_id="f3-test-daemon",
        dockerd=_pinned(dockerd_path, dockerd_digest, 0o500),
        docker=_pinned(binary_path, binary_digest, 0o500),
        runc=_pinned(runc_path, runc_digest, 0o500),
        containerd=_pinned(containerd_path, containerd_digest, 0o500),
        config_path=os.fspath((tmp_path / "daemon.json").resolve()),
        socket_path=os.fspath((tmp_path / "docker.sock").resolve()),
        pid_file=os.fspath((tmp_path / "docker.pid").resolve()),
        data_root=os.fspath((tmp_path / "docker-data").resolve()),
        exec_root=os.fspath((tmp_path / "docker-exec").resolve()),
        mount_stage_root=os.fspath((tmp_path / "mount-stage").resolve()),
        containerd_socket_path=os.fspath((tmp_path / "containerd.sock").resolve()),
        containerd_root=os.fspath((tmp_path / "containerd-root").resolve()),
        containerd_state=os.fspath((tmp_path / "containerd-state").resolve()),
        log_root=os.fspath((tmp_path / "docker-log").resolve()),
        log_limit_bytes=65536,
        storage_driver="vfs",
        runtime_name="breadboard-runc",
        images=tuple(
            sorted(
                (
                    OfflineImageAuthorityV1(
                        archive=_pinned(archive_path, archive_digest, 0o400),
                        image_id="sha256:" + "8" * 64,
                        source_image_digest=primary_image.image_digest,
                    ),
                    OfflineImageAuthorityV1(
                        archive=_pinned(archive_path, archive_digest, 0o400),
                        image_id="sha256:" + "9" * 64,
                        source_image_digest=verifier_image.image_digest,
                    ),
                ),
                key=lambda item: item.image_id,
            )
        ),
    )
    installed = InstalledV1(
        runner_adapters=(RunnerAdapterDescriptor(TERMINAL_ADAPTER_ID, TERMINAL_RUNTIME_ABI, TERMINAL_IMPLEMENTATION_DIGEST),),
        runtimes=runtimes,
        images=images,
        security_policies=securities,
        network_policies=(network,),
        verifiers=(verifier,),
        private_docker_daemon=daemon,
    )
    stores: dict[str, str] = {}
    for name in ("locator", "materialization_cache", "workspace", "lease", "security_profile", "service_output_root"):
        path = tmp_path / name
        path.mkdir(mode=0o700)
        stores[name] = os.fspath(path.resolve())
    stores["cas"] = os.fspath((authority_root / "cas").resolve())
    Path(stores["cas"]).chmod(0o700)

    fixture_tls = Path("tests/fixtures/rl/harness/production_composition/tls")
    ca_path = tmp_path / "ca.pem"
    leaf_path = tmp_path / "leaf.pem"
    key_path = tmp_path / "leaf.key"
    shutil.copyfile(fixture_tls / "ca.cert.pem", ca_path)
    shutil.copyfile(fixture_tls / "server.cert.pem", leaf_path)
    shutil.copyfile(fixture_tls / "server.key.pem", key_path)
    ca_path.chmod(0o444)
    leaf_path.chmod(0o444)
    key_path.chmod(0o400)
    leaf_der = ssl.PEM_cert_to_DER_cert(leaf_path.read_text(encoding="ascii"))

    api_path, _ = _file(tmp_path / "api.secret", b"api-secret-value-with-entropy-123456\n", 0o400)
    policy_path, _ = _file(tmp_path / "policy.secret", b"policy-secret-value-with-entropy-123\n", 0o400)
    receipt_path = spec.receipt_signer.secret_path
    handles = SecretHandlesV1(
        records=(
            SecretHandleSpecV1(handle_id="a-api", purpose="api_bearer", route_ids=()),
            SecretHandleSpecV1(handle_id=spec.policy.secret_handle_id, purpose="policy_callback", route_ids=(spec.policy.route_id,)),
            SecretHandleSpecV1(handle_id="z-receipt", purpose="receipt_signer", route_ids=()),
        )
    )
    return F3ProductionCompositionInput(
        schema_version="bb.rl.phase5-f3-production-input.v1",
        composition_id=spec.composition_id,
        authority_manifest=SourceArtifact(
            path=os.fspath(authority_manifest_path.resolve()),
            sha256=sha256_bytes(authority_manifest_raw),
            media_type="application/vnd.breadboard.rl.phase5-f3-authority-bundle+json;version=1",
        ),
        installed=installed,
        stores=StorePaths(**stores, lease_ttl_seconds=300),
        server=ServerV1(mode="loopback", host="127.0.0.1", port=18443, allow_unauthenticated_loopback=False, proxy_headers=False, request_timeout_seconds=30.0),
        secrets=SecretAuthorities(
            handles=handles,
            files={"a-api": api_path, spec.policy.secret_handle_id: policy_path, "z-receipt": receipt_path},
        ),
        receipt=ReceiptAuthority(key_id=spec.receipt_signer.key_id, secret_handle_id="z-receipt"),
        policy_tls=PolicyTlsAuthorityInput(
            route_id=spec.policy.route_id,
            ca_certificate=SourceArtifact(path=os.fspath(ca_path.resolve()), sha256=sha256_bytes(ca_path.read_bytes()), media_type="application/x-pem-file"),
            leaf_certificate=SourceArtifact(path=os.fspath(leaf_path.resolve()), sha256=sha256_bytes(leaf_path.read_bytes()), media_type="application/x-pem-file"),
            leaf_private_key=SecretFileAuthority(path=os.fspath(key_path.resolve()), sha256=sha256_bytes(key_path.read_bytes()), mode=0o400),
            expected_leaf_der_sha256=sha256_bytes(leaf_der),
            minimum_tls_version="TLSv1.3",
            cipher_suite="TLS_AES_256_GCM_SHA384",
            dedicated_single_leaf_ca=True,
        ),
        evidence_bindings=(
            EvidenceRoleBindingV2(
                role="terminal-result",
                source=EvidenceRoleSourceV2.RUNNER_RESULT,
                producer_id="f3-terminal-responses",
                producer_implementation_digest=TERMINAL_IMPLEMENTATION_DIGEST,
            ),
        ),
        resolution_task=spec.task.eligibility,
    ), authority_root


def test_builds_closed_generic_composition_with_exact_f3_authorities(tmp_path: Path) -> None:
    spec, _authority_root = _composition_spec(tmp_path)
    result = build_f3_production_composition(spec, os.fspath((tmp_path / "composition").resolve()))
    raw = Path(result.composition_manifest_path).read_bytes()
    manifest = HarnessCompositionManifestV1.model_validate_json(raw, strict=True)

    assert manifest.composition_id == spec.composition_id
    assert manifest.selector_catalog.direct[0].sha256 == sha256_bytes(
        Path(manifest.selector_catalog.direct[0].path).read_bytes()
    )
    assert manifest.installed.runner_adapters[0].adapter_id == TERMINAL_ADAPTER_ID
    assert len(manifest.installed.runtimes) == 2
    assert len(manifest.installed.private_docker_daemon.images) == 2
    assert {item.archive.path for item in manifest.installed.private_docker_daemon.images} == {spec.installed.private_docker_daemon.images[0].archive.path}
    assert manifest.stores.cas.path == spec.stores.cas
    assert manifest.evidence_bindings[0].role == "terminal-result"
    assert manifest.secret_handles == spec.secrets.handles
    assert manifest.control_plane.receipt_authenticator.secret_handle_id == spec.receipt.secret_handle_id
    assert Path(result.inventory_path).is_file()


def test_composed_direct_selector_resolves_exact_runtime_plan(tmp_path: Path) -> None:
    spec, _authority_root = _composition_spec(tmp_path)
    result = build_f3_production_composition(
        spec, os.fspath((tmp_path / "composition").resolve())
    )
    artifacts = Path(result.composition_manifest_path).parent
    policy = c.AdmissionPolicySnapshot.model_validate_json(
        (artifacts / "admission-policy.json").read_bytes(), strict=True
    )
    registries = c.RegistrySnapshotSet.model_validate_json(
        (artifacts / "registry-snapshot.json").read_bytes(), strict=True
    )
    receipt_raw = (artifacts / "admission-receipt.json").read_bytes()
    receipt = c.AdmissionReceipt.model_validate_json(receipt_raw, strict=True)
    compiled_raw = (artifacts / "compiled-manifest.json").read_bytes()
    observations = tuple(
        c.PolicyCapabilityObservation.model_validate_json(
            canonical_json_bytes(item), strict=True
        )
        for item in __import__("json").loads(
            (artifacts / "policy-capabilities.json").read_bytes()
        )
    )
    selector_raw = (artifacts / "direct-selector.json").read_bytes()
    selector_digest = sha256_bytes(selector_raw)
    cas = FilesystemCAS(spec.stores.cas)
    try:
        revocations = PinnedRevocationStore((policy.revocation,))
        runtime = ConfigRuntime(
            compiler=PinnedServerCompilerAdapter(
                {sha256_bytes(compiled_raw): compiled_raw}
            ),
            policy=policy,
            registries=registries,
            revocations=revocations,
            store=CASConfigRuntimeStore(cas),
            clock=type(
                "_Clock",
                (),
                {"current": lambda self: datetime(2026, 7, 13, tzinfo=UTC)},
            )(),
            authenticator=HmacSha256ReceiptAuthenticator(
                key_id=spec.receipt.key_id,
                key=Path(
                    spec.secrets.files[spec.receipt.secret_handle_id]
                ).read_text(encoding="utf-8").strip().encode("utf-8"),
            ),
            policy_capabilities=_PinnedPolicyCapabilityRegistry(
                observations, registries.policy_capability_attestations, revocations
            ),
        )
        resolved = runtime.resolve_episode(
            c.ResolveEpisodeRequest(
                episode_id="r-swe-001-composed-resolution",
                subject=receipt.subject,
                selector=c.DirectSelectorRef(
                    digest=selector_digest,
                    ref=c.ArtifactRef(
                        artifact_id=selector_digest,
                        sha256=selector_digest,
                        size_bytes=len(selector_raw),
                        media_type=(
                            "application/vnd.breadboard.direct-selector+json;version=1"
                        ),
                    ),
                ),
                selection_nonce=None,
                task=spec.resolution_task,
                policy_binding=receipt.policy_binding_ref,
                episode_overlays=(),
            )
        )
    finally:
        cas.close()

    plan = resolved.effective_plan
    assert plan.runner.adapter_id == TERMINAL_ADAPTER_ID
    assert plan.runner.runtime_abi == TERMINAL_RUNTIME_ABI
    assert tuple(tool.tool_id for tool in plan.effective_capabilities.tools) == (
        "list_files",
        "read_file",
        "shell",
        "submit",
        "write_file",
    )
    assert plan.task.task_contract_digest == spec.resolution_task.canonical_digest()
    assert plan.task.repository_snapshot_digest == spec.resolution_task.artifacts[0].digest
    assert plan.sandbox.runtime_class is c.RuntimeClass.HARDENED_DOCKER
    assert plan.verifier == spec.installed.verifiers[0].grant
    assert len(plan.policy_slots) == 1


@pytest.mark.parametrize("missing", ["secret", "store", "image", "archive"])
def test_rejects_missing_secret_store_image_or_archive(tmp_path: Path, missing: str) -> None:
    spec, _authority_root = _composition_spec(tmp_path)
    if missing == "secret":
        Path(next(iter(spec.secrets.files.values()))).unlink()
    elif missing == "store":
        Path(spec.stores.locator).rmdir()
    elif missing == "archive":
        Path(spec.installed.private_docker_daemon.images[0].archive.path).unlink()
    else:
        payload = spec.model_dump(mode="json")
        payload["installed"]["images"] = payload["installed"]["images"][:1]
        spec = F3ProductionCompositionInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )
    with pytest.raises((F3CompositionError, FileNotFoundError)):
        build_f3_production_composition(spec, os.fspath((tmp_path / "composition").resolve()))


def test_rejects_authority_digest_drift_and_gold_control_roles(tmp_path: Path) -> None:
    spec, _authority_root = _composition_spec(tmp_path)
    payload = spec.model_dump(mode="json")
    payload["authority_manifest"]["sha256"] = "sha256:" + "f" * 64
    drifted = F3ProductionCompositionInput.model_validate_json(canonical_json_bytes(payload), strict=True)
    with pytest.raises(F3CompositionError, match="digest mismatch"):
        build_f3_production_composition(drifted, os.fspath((tmp_path / "drifted").resolve()))

    payload = spec.model_dump(mode="json")
    payload["resolution_task"]["artifacts"][0]["role"] = "gold-patch"
    with pytest.raises(ValidationError, match="repository workspace"):
        F3ProductionCompositionInput.model_validate_json(canonical_json_bytes(payload), strict=True)


def test_large_installed_file_authority_uses_streaming_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large-installed-file"
    raw = b"ab"
    path.write_bytes(raw)
    path.chmod(0o500)
    digest = sha256_bytes(raw)
    source = SourceArtifact(
        path=os.fspath(path.resolve()),
        sha256=digest,
        media_type="application/octet-stream",
    )
    monkeypatch.setattr(f3_composition, "MAX_PINNED_ARTIFACT_BYTES", 1)
    monkeypatch.setattr(f3_composition, "MAX_PINNED_FILE_AUTHORITY_BYTES", 2)

    with pytest.raises(F3CompositionError, match="bounded regular file"):
        f3_composition._read_pinned(source, canonical_json=False)
    f3_composition._verify_file_authority(
        source.path,
        digest,
        os.stat(source.path, follow_symlinks=False).st_uid,
        0o500,
    )
