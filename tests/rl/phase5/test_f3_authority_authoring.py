from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    CASConfigRuntimeStore,
    HmacSha256ReceiptAuthenticator,
    PinnedRevocationStore,
    PinnedServerCompilerAdapter,
    _PinnedPolicyCapabilityRegistry,
)
from breadboard.rl.harness.config_runtime import ConfigRuntime
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_IMPLEMENTATION_DIGEST,
    TERMINAL_RUNTIME_ABI,
    TERMINAL_TOOL_DEFINITIONS,
)
from breadboard.rl.phase5.f3_authority_authoring import (
    F3AuthorityBundleManifest,
    F3AuthorityInput,
    F3ImageAuthority,
    F3NetworkAuthority,
    F3PolicyAuthority,
    F3ReceiptSignerInput,
    F3RepositoryAuthority,
    F3RuntimeAuthority,
    F3SecurityAuthority,
    F3TaskAuthority,
    F3VerifierAuthority,
    ImmutableAuthorityRef,
    author_f3_authority,
)
from breadboard.rl.state.cas import FilesystemCAS


def _d(character: str) -> str:
    return "sha256:" + character * 64


def _immutable(name: str, digest: str) -> ImmutableAuthorityRef:
    return ImmutableAuthorityRef(
        immutable_reference=f"cas://phase5/{name}@{digest}", digest=digest
    )


def _spec(tmp_path: Path) -> F3AuthorityInput:
    secret = tmp_path / "receipt.key"
    if secret.exists():
        secret.chmod(0o600)
    secret.write_bytes(b"f3-authority-test-signing-key-32-bytes-minimum\n")
    secret.chmod(0o400)
    repository_digest = _d("1")
    primary_image_digest = _d("2")
    verifier_image_digest = _d("3")
    network_digest = _d("4")
    eligibility = c.TaskEligibilityInput(
        task_type="r-swe",
        labels=(),
        artifacts=(
            c.TaskArtifact(
                role="repository-workspace",
                digest=repository_digest,
                media_type="application/vnd.breadboard.repository-workspace+tar",
                size_bytes=4096,
            ),
        ),
        parameters_digest=_d("5"),
    )
    repository_binding = c._canonical_digest(
        {
            "repository_snapshot_digest": repository_digest,
            "image_digest": primary_image_digest,
        }
    )
    verifier_grant = c.VerifierGrant(
        verifier_id="r-swe-verifier",
        implementation_digest=_d("6"),
        image_digest=verifier_image_digest,
        executable_digest=_d("7"),
        code_digest=_d("8"),
        input_schema_digest=_d("9"),
        result_schema_digest=_d("a"),
        network_policy_digest=network_digest,
        secret_handle_ids=(),
    )
    validity = c.ValidityWindow(
        issued_at="2026-07-13T00:00:00Z",
        not_before="2026-07-13T00:00:00Z",
        expires_at="2026-07-13T01:00:00Z",
    )
    scope_digest = _d("b")
    return F3AuthorityInput(
        schema_version="bb.rl.phase5-f3-authority-input.v1",
        composition_id="f3-r-swe-001",
        attempt_id="attempt-001",
        task=F3TaskAuthority(
            task_id="R-SWE-001",
            eligibility=eligibility,
            task_contract_digest=eligibility.canonical_digest(),
            task_binding_digest=_d("c"),
            prompt="Repair the admitted repository and submit the completed result.",
        ),
        repository=F3RepositoryAuthority(
            repository_snapshot_digest=repository_digest,
            binding_digest=repository_binding,
            source=_immutable("repository", repository_digest),
        ),
        primary_runtime=F3RuntimeAuthority(
            runtime_id="f3-primary",
            runtime_class=c.RuntimeClass.HARDENED_DOCKER,
            driver_implementation_digest=_d("d"),
            runtime_binary=_immutable("docker", _d("e")),
            oci_runtime_binary=_immutable("runc", _d("f")),
        ),
        primary_image=F3ImageAuthority(
            runtime_id="f3-primary",
            image_digest=primary_image_digest,
            immutable_reference=f"registry.invalid/r-swe-primary@{primary_image_digest}",
        ),
        primary_security=F3SecurityAuthority(
            policy_digest=_d("0"),
            seccomp_digest=_d("1"),
            uid=65532,
            gid=65532,
            read_only_root=True,
            drop_all_capabilities=True,
            no_new_privileges=True,
            privileged=False,
            docker_socket_forbidden=True,
            lsm_profile="breadboard-f3-primary",
        ),
        verifier_runtime=F3RuntimeAuthority(
            runtime_id="f3-verifier",
            runtime_class=c.RuntimeClass.HARDENED_DOCKER,
            driver_implementation_digest=_d("2"),
            runtime_binary=_immutable("docker-verifier", _d("3")),
            oci_runtime_binary=_immutable("runc-verifier", _d("4")),
        ),
        verifier_image=F3ImageAuthority(
            runtime_id="f3-verifier",
            image_digest=verifier_image_digest,
            immutable_reference=f"registry.invalid/r-swe-verifier@{verifier_image_digest}",
        ),
        verifier_security=F3SecurityAuthority(
            policy_digest=_d("5"),
            seccomp_digest=_d("6"),
            uid=65533,
            gid=65533,
            read_only_root=True,
            drop_all_capabilities=True,
            no_new_privileges=True,
            privileged=False,
            docker_socket_forbidden=True,
            lsm_profile="breadboard-f3-verifier",
        ),
        network=F3NetworkAuthority(
            policy_digest=network_digest,
            mode="none",
            docker_network="none",
            egress_route_ids=(),
            default_deny=True,
        ),
        verifier=F3VerifierAuthority(
            grant=verifier_grant,
            runtime_id="f3-verifier",
            security_policy_digest=_d("5"),
        ),
        policy=F3PolicyAuthority(
            subject=c.AuthenticatedSubject(
                tenant_id="phase5",
                principal_id="f3-author",
                authority_scope_digest=scope_digest,
            ),
            validity=validity,
            revocation=c.RevocationBinding(
                scope_digest=scope_digest, epoch=1, state_digest=_d("7")
            ),
            receipt_ttl_seconds=3600,
            route_id="f3-responses",
            target_ip="127.0.0.1",
            port=8443,
            owner_id="f3-policy-owner",
            secret_handle_id="f3-policy-secret",
            secret_handle_version_digest=_d("8"),
            model=c.ModelIdentity(
                model_id="f3-policy-model",
                model_digest=_d("9"),
                tokenizer_digest=_d("a"),
                checkpoint_digest=_d("b"),
            ),
            provider_id="f3-responses-provider",
            bridge_instance_id="f3-policy-bridge",
            bridge_build_digest=_d("c"),
            evidence_policy_revision_digest=_d("d"),
            retention_policy_revision_digest=_d("e"),
            retention_minimum_seconds=60,
            retention_maximum_seconds=3600,
        ),
        resources=c.ResourceLimits(
            cpu_millis=4000,
            memory_bytes=2 * 1024**3,
            pids=512,
            storage_bytes=4 * 1024**3,
            open_files=4096,
            wall_time_ms=300_000,
        ),
        limits=c.ExecutionLimits(
            max_turns=16,
            action_timeout_ms=30_000,
            observation_bytes=64 * 1024,
            response_bytes=64 * 1024,
            artifact_bytes_each=1024 * 1024,
            artifact_bytes_total=4 * 1024 * 1024,
            transcript_bytes=16 * 1024 * 1024,
            setup_timeout_ms=30_000,
            verifier_timeout_ms=60_000,
        ),
        receipt_signer=F3ReceiptSignerInput(
            key_id="f3-receipt-key", secret_path=os.fspath(secret.resolve())
        ),
    )


@pytest.mark.parametrize("field", ["runner", "tools", "terminal_implementation_digest"])
def test_input_rejects_runner_tool_and_digest_drift(tmp_path: Path, field: str) -> None:
    payload = _spec(tmp_path).model_dump(mode="json")
    payload[field] = _d("f")
    with pytest.raises(ValidationError) as raised:
        F3AuthorityInput.model_validate_json(canonical_json_bytes(payload), strict=True)
    assert any(error["loc"] == (field,) and error["type"] == "extra_forbidden" for error in raised.value.errors())


@pytest.mark.parametrize("role", ["gold-patch", "control-artifact"])
def test_input_rejects_gold_and_control_role_leakage(tmp_path: Path, role: str) -> None:
    payload = _spec(tmp_path).model_dump(mode="json")
    leaked = c.TaskEligibilityInput(
        task_type="r-swe",
        labels=(),
        artifacts=(
            c.TaskArtifact(
                role=role,
                digest=_d("1"),
                media_type="application/octet-stream",
                size_bytes=1,
            ),
        ),
        parameters_digest=_d("5"),
    )
    payload["task"]["eligibility"] = leaked.model_dump(mode="json")
    payload["task"]["task_contract_digest"] = leaked.canonical_digest()
    with pytest.raises(ValidationError, match="only the admitted repository workspace"):
        F3AuthorityInput.model_validate_json(canonical_json_bytes(payload), strict=True)


def test_input_rejects_mutable_and_mismatched_external_refs(tmp_path: Path) -> None:
    payload = _spec(tmp_path).model_dump(mode="json")
    payload["primary_image"]["immutable_reference"] = "registry.invalid/r-swe-primary:latest"
    with pytest.raises(ValidationError, match="immutable digest"):
        F3AuthorityInput.model_validate_json(canonical_json_bytes(payload), strict=True)

    payload = _spec(tmp_path).model_dump(mode="json")
    payload["repository"]["source"]["digest"] = _d("f")
    payload["repository"]["source"]["immutable_reference"] = f"cas://phase5/repository@{_d('f')}"
    with pytest.raises(ValidationError, match="does not bind the repository snapshot"):
        F3AuthorityInput.model_validate_json(canonical_json_bytes(payload), strict=True)


def test_authored_bundle_resolves_exact_terminal_plan(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    source = tmp_path / "f3-input.json"
    source.write_bytes(canonical_json_bytes(spec.model_dump(mode="json")))
    output = tmp_path / "authority"

    manifest_path = author_f3_authority(os.fspath(source.resolve()), os.fspath(output.resolve()))
    manifest_raw = Path(manifest_path).read_bytes()
    manifest = F3AuthorityBundleManifest.model_validate_json(manifest_raw, strict=True)
    assert canonical_json_bytes(manifest.model_dump(mode="json")) == manifest_raw
    assert Path(manifest.cas_root).stat().st_mode & 0o777 == 0o700
    assert manifest.runner.adapter_id == TERMINAL_ADAPTER_ID
    assert manifest.runner.runtime_abi == TERMINAL_RUNTIME_ABI
    assert manifest.runner.implementation_digest == TERMINAL_IMPLEMENTATION_DIGEST
    expected_tool_ids = tuple(sorted(item.tool_id for item in TERMINAL_TOOL_DEFINITIONS))
    assert manifest.terminal_tool_ids == expected_tool_ids

    def artifact(name: str) -> bytes:
        ref = manifest.artifacts[name]
        payload = Path(ref.path).read_bytes()
        assert _d_for_bytes(payload) == ref.sha256
        return payload

    compiled_bytes = artifact("compiled-manifest.json")
    policy = c.AdmissionPolicySnapshot.model_validate_json(artifact("admission-policy.json"), strict=True)
    registries = c.RegistrySnapshotSet.model_validate_json(artifact("registry-snapshot.json"), strict=True)
    receipt = c.AdmissionReceipt.model_validate_json(artifact("admission-receipt.json"), strict=True)
    observations = tuple(
        c.PolicyCapabilityObservation.model_validate_json(
            canonical_json_bytes(item), strict=True
        )
        for item in __import__("json").loads(artifact("policy-capabilities.json"))
    )
    cas = FilesystemCAS(Path(manifest.cas_root))
    try:
        store = CASConfigRuntimeStore(cas)
        revocations = PinnedRevocationStore((policy.revocation,))
        runtime = ConfigRuntime(
            compiler=PinnedServerCompilerAdapter(
                {manifest.artifacts["compiled-manifest.json"].sha256: compiled_bytes}
            ),
            policy=policy,
            registries=registries,
            revocations=revocations,
            store=store,
            clock=type(
                "_Clock",
                (),
                {"current": lambda self: datetime(2026, 7, 13, tzinfo=UTC)},
            )(),
            authenticator=HmacSha256ReceiptAuthenticator(
                key_id=spec.receipt_signer.key_id,
                key=Path(spec.receipt_signer.secret_path).read_bytes().removesuffix(b"\n"),
            ),
            policy_capabilities=_PinnedPolicyCapabilityRegistry(
                observations, registries.policy_capability_attestations, revocations
            ),
        )
        selector_artifact = manifest.artifacts["direct-selector.json"]
        selector_ref = c.DirectSelectorRef(
            digest=selector_artifact.sha256,
            ref=c.ArtifactRef(
                artifact_id=selector_artifact.sha256,
                sha256=selector_artifact.sha256,
                size_bytes=selector_artifact.size_bytes,
                media_type=selector_artifact.media_type,
            ),
        )
        resolved = runtime.resolve_episode(
            c.ResolveEpisodeRequest(
                episode_id="r-swe-001-episode",
                subject=spec.policy.subject,
                selector=selector_ref,
                selection_nonce=None,
                task=spec.task.eligibility,
                policy_binding=receipt.policy_binding_ref,
                episode_overlays=(),
            )
        )
    finally:
        cas.close()

    plan = resolved.effective_plan
    assert plan.effective_capabilities.runner.adapter_id == TERMINAL_ADAPTER_ID
    assert plan.effective_capabilities.runner.runtime_abi == TERMINAL_RUNTIME_ABI
    assert plan.effective_capabilities.runner.implementation_digest == TERMINAL_IMPLEMENTATION_DIGEST
    assert tuple(tool.tool_id for tool in plan.effective_capabilities.tools) == expected_tool_ids
    assert len(plan.effective_capabilities.policy_slots) == 1
    assert plan.effective_capabilities.sandbox.runtime_class is c.RuntimeClass.HARDENED_DOCKER
    assert plan.effective_capabilities.verifier == spec.verifier.grant
    assert plan.effective_capabilities.task.task_contract_digest == spec.task.task_contract_digest
    assert plan.effective_capabilities.task.task_binding_digest == spec.task.task_binding_digest
    assert plan.effective_capabilities.task.repository_snapshot_digest == spec.repository.repository_snapshot_digest
    assert plan.effective_capabilities.artifacts.allowed_roles == ("terminal-result",)
    assert set(manifest.artifacts) >= {
        "config-bundle.json",
        "config-closure.json",
        "admission-receipt.json",
        "admitted-set.json",
        "direct-selector.json",
    }


def _d_for_bytes(payload: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_authoring_deduplicates_equal_verifier_implementation_and_code_pins(
    tmp_path: Path,
) -> None:
    payload = _spec(tmp_path).model_dump(mode="json")
    payload["verifier"]["grant"]["implementation_digest"] = payload["verifier"]["grant"][
        "code_digest"
    ]
    spec = F3AuthorityInput.model_validate_json(
        canonical_json_bytes(payload),
        strict=True,
    )
    source = tmp_path / "equal-verifier-digests.json"
    source.write_bytes(canonical_json_bytes(spec.model_dump(mode="json")))

    manifest_path = author_f3_authority(
        os.fspath(source.resolve()),
        os.fspath((tmp_path / "equal-verifier-digests-authority").resolve()),
    )

    manifest = F3AuthorityBundleManifest.model_validate_json(
        Path(manifest_path).read_bytes(),
        strict=True,
    )
    receipt_ref = manifest.artifacts["admission-receipt.json"]
    receipt = c.AdmissionReceipt.model_validate_json(
        Path(receipt_ref.path).read_bytes(),
        strict=True,
    )
    assert len(receipt.pins) == len(set(receipt.pins))
