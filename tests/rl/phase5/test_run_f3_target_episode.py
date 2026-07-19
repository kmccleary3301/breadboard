from __future__ import annotations
from builtins import BaseExceptionGroup

from types import SimpleNamespace
import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.mount_namespace_broker import MountNamespaceBrokerError
from breadboard.rl.harness.runners.base import RunnerTermination, ToolCallEvent
from breadboard.rl.harness.runners.terminal import TERMINAL_TOOL_DEFINITIONS
from breadboard.rl.phase5.f3_authority_authoring import ImmutableAuthorityRef
from breadboard.rl.phase5.f3_composition import SourceArtifact, sha256_bytes
from scripts.rl_phase5 import run_f3_target_episode as target_module
from scripts.rl_phase5.run_f3_target_episode import (
    F3AuthorityRefs,
    F3CleanupFileAuthority,
    F3EpisodeCleanupAuthority,
    F3EvidenceExportAuthority,
    F3FixedPolicyAuthority,
    F3PolicyGenerationEvidence,
    F3TargetEpisodeError,
    F3TargetEpisodeInput,
    F3TargetEpisodeReport,
    _export_durable_evidence,
    _EpisodeCleanupOwner,
    _read_generation_evidence,
    _recover_after_cleanup,
    _resume_pre_cleanup_export,
    _validate_resolved_policy,
)
from tests.rl.harness.test_runner_terminal import (
    RecordingWorkspace,
    ScriptedPolicy,
    _open,
    _request,
    _effective_plan,
)
from tests.rl.phase5.test_f3_composition import _composition_spec


def _private_daemon_at(composition: object, daemon_root: Path) -> object:
    daemon_root.mkdir(mode=0o700)
    installed = composition.installed
    daemon = installed.private_docker_daemon
    assert daemon is not None
    relocated_paths = {
        field: os.fspath((daemon_root / name).resolve())
        for field, name in (
            ("config_path", "daemon.json"),
            ("socket_path", "docker.sock"),
            ("pid_file", "docker.pid"),
            ("data_root", "docker-data"),
            ("exec_root", "docker-exec"),
            ("mount_stage_root", "mount-stage"),
            ("containerd_socket_path", "containerd.sock"),
            ("containerd_root", "containerd-root"),
            ("containerd_state", "containerd-state"),
            ("log_root", "docker-log"),
        )
    }
    relocated = daemon.model_copy(update=relocated_paths)
    return composition.model_copy(
        update={
            "installed": installed.model_copy(
                update={"private_docker_daemon": relocated}
            )
        }
    )


def _cleanup_file(path: str) -> F3CleanupFileAuthority:
    metadata = os.stat(path, follow_symlinks=False)
    return F3CleanupFileAuthority(
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        sha256=sha256_bytes(Path(path).read_bytes()),
    )


def _cleanup_authority(
    run_root: Path, daemon_root: Path, secret_paths: set[str]
) -> F3EpisodeCleanupAuthority:
    run_metadata = run_root.stat()
    daemon_metadata = daemon_root.stat()
    run_parent = run_root.parent.stat()
    daemon_parent = daemon_root.parent.stat()
    return F3EpisodeCleanupAuthority(
        run_root=os.fspath(run_root.resolve()),
        run_root_device=run_metadata.st_dev,
        run_root_inode=run_metadata.st_ino,
        run_parent_device=run_parent.st_dev,
        run_parent_inode=run_parent.st_ino,
        run_parent_owner_uid=run_parent.st_uid,
        run_parent_mode=stat.S_IMODE(run_parent.st_mode),
        daemon_root=os.fspath(daemon_root.resolve()),
        daemon_root_device=daemon_metadata.st_dev,
        daemon_root_inode=daemon_metadata.st_ino,
        daemon_parent_device=daemon_parent.st_dev,
        daemon_parent_inode=daemon_parent.st_ino,
        secret_files=tuple(_cleanup_file(path) for path in sorted(secret_paths)),
    )


def _target_spec(tmp_path: Path) -> F3TargetEpisodeInput:
    run_root = tmp_path / "run"
    run_root.mkdir(mode=0o700)
    composition, _authority_root = _composition_spec(run_root)
    daemon_root = tmp_path / "private-daemon"
    composition = _private_daemon_at(composition, daemon_root)
    authority_manifest = json.loads(
        Path(composition.authority_manifest.path).read_bytes()
    )
    artifacts = authority_manifest["artifacts"]
    observations = json.loads(
        Path(artifacts["policy-capabilities.json"]["path"]).read_bytes()
    )
    observation = c.PolicyCapabilityObservation.model_validate_json(
        canonical_json_bytes(observations[0]), strict=True
    )
    graph_raw = Path(artifacts["policy-http.json"]["path"]).read_bytes()
    from breadboard.rl.harness.composition import PolicyHttpAuthorityGraphV1

    graph = PolicyHttpAuthorityGraphV1.model_validate_json(graph_raw, strict=True)
    model = c.ModelIdentity(
        model_id=observation.model_id,
        model_digest=observation.model_digest,
        tokenizer_digest=observation.tokenizer_digest,
        checkpoint_digest=observation.checkpoint_digest,
    )
    command = "git apply --whitespace=error /workspace/agent-candidate.patch"
    command_digest = sha256_bytes(command.encode("utf-8"))
    evidence = F3PolicyGenerationEvidence(
        schema_version="bb.rl.phase5-f3-policy-generation-evidence.v1",
        generator_role="agent-candidate",
        independent=True,
        task_id="R-SWE-001",
        repository_snapshot_digest=composition.resolution_task.artifacts[0].digest,
        command_sha256=command_digest,
        model=model,
    )
    evidence_raw = canonical_json_bytes(evidence.model_dump(mode="json"))
    evidence_path = run_root / "policy-generation.json"
    evidence_path.write_bytes(evidence_raw)
    evidence_path.chmod(0o400)
    task_digest = composition.resolution_task.canonical_digest()
    repository_digest = composition.resolution_task.artifacts[0].digest
    generation_digest = sha256_bytes(evidence_raw)
    export_parent = tmp_path / "evidence-exports"
    export_parent.mkdir(mode=0o700)
    lease_path = export_parent / "episode.execution.lease"
    lease_path.touch(mode=0o600)
    lease_metadata = lease_path.stat()
    export_parent_metadata = export_parent.stat()
    return F3TargetEpisodeInput(
        schema_version="bb.rl.phase5-f3-target-episode-input.v1",
        composition=composition,
        composition_output_dir=os.fspath((run_root / "composition").resolve()),
        workspace_quota_bytes=8 * 1024**3,
        episode_id="r-swe-001-target-episode",
        task_id="R-SWE-001",
        policy_visible_prompt="Repair the admitted repository using the independently generated candidate patch.",
        policy=F3FixedPolicyAuthority(
            slot_id="responses-policy",
            route_id=graph.routes[0].grant.route_id,
            route_revision_digest=graph.routes[0].grant.route_revision_digest,
            policy_capability_observation_digest=observation.canonical_digest(),
            model=model,
            patch_application_command=command,
            patch_application_command_sha256=command_digest,
            generation_evidence=SourceArtifact(
                path=os.fspath(evidence_path.resolve()),
                sha256=generation_digest,
                media_type="application/vnd.breadboard.rl.phase5-f3-policy-generation-evidence+json;version=1",
            ),
        ),
        refs=F3AuthorityRefs(
            task=ImmutableAuthorityRef(
                immutable_reference=f"cas://phase5/task@{task_digest}",
                digest=task_digest,
            ),
            repository=ImmutableAuthorityRef(
                immutable_reference=f"cas://phase5/repository@{repository_digest}",
                digest=repository_digest,
            ),
            generation=ImmutableAuthorityRef(
                immutable_reference=f"cas://phase5/generation@{generation_digest}",
                digest=generation_digest,
            ),
        ),
        cleanup_authority=_cleanup_authority(
            run_root,
            daemon_root,
            set(composition.secrets.files.values())
            | {composition.policy_tls.leaf_private_key.path},
        ),
        evidence_export=F3EvidenceExportAuthority(
            path=os.fspath((export_parent / "episode.evidence.json").resolve()),
            final_path=os.fspath(
                (export_parent / "episode.final-evidence.json").resolve()
            ),
            cleanup_failure_path=os.fspath(
                (export_parent / "episode.cleanup-failure.json").resolve()
            ),
            lease_path=os.fspath(lease_path.resolve()),
            lease_device=lease_metadata.st_dev,
            lease_inode=lease_metadata.st_ino,
            parent_device=export_parent_metadata.st_dev,
            parent_inode=export_parent_metadata.st_ino,
        ),
    )

@pytest.fixture
def external_authority_factory():
    authorities: list[target_module._EpisodeExternalAuthority] = []

    def create(
        spec: F3TargetEpisodeInput,
    ) -> target_module._EpisodeExternalAuthority:
        authority = target_module._EpisodeExternalAuthority(spec)
        authorities.append(authority)
        return authority

    yield create

    for authority in reversed(authorities):
        authority.close()


def test_inode_bound_cleanup_removes_exact_attempt_roots_and_is_idempotent(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    unrelated = tmp_path / "unrelated"
    for path in (run_root, daemon_root, unrelated):
        path.mkdir(mode=0o700)
    secret = run_root / "api.secret"
    secret.write_bytes(b"exact-secret\n")
    secret.chmod(0o400)
    for path in (
        run_root / "locator" / "stuck-allocating.json",
        run_root / "materialization_cache" / "quarantined.json",
        run_root / "security_profile" / "episode.json",
        run_root / "service_output_root" / "attempt.json",
        daemon_root / "containerd.sock.ttrpc",
        daemon_root / "docker-data" / "residue",
        daemon_root / "logs" / "dockerd.log",
    ):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_bytes(b"residue\n")
    (unrelated / "keep").write_bytes(b"unrelated\n")
    authority = _cleanup_authority(
        run_root, daemon_root, {os.fspath(secret.resolve())}
    )
    owner = _EpisodeCleanupOwner(authority)

    first = owner.close()
    second = owner.close()

    assert first == second
    assert first["exact_absence"] is True
    assert all(item["absent"] is True for item in first["roots"])
    assert all(item["absent"] is True for item in first["secret_files"])
    assert not run_root.exists()
    assert not daemon_root.exists()
    assert (unrelated / "keep").read_bytes() == b"unrelated\n"


@pytest.mark.parametrize("parent_mode", (0o700, 0o755))
def test_final_shared_parent_capture_accepts_stable_mode_and_sibling_churn(
    tmp_path: Path,
    parent_mode: int,
) -> None:
    shared_parent = tmp_path / "shared-parent"
    shared_parent.mkdir(mode=parent_mode)
    shared_parent.chmod(parent_mode)
    run_root = shared_parent / "attempt"
    run_root.mkdir(mode=0o700)
    sibling = shared_parent / "unrelated-sibling"
    sibling.mkdir(mode=0o700)
    sibling_marker = sibling / "keep"
    sibling_marker.write_bytes(b"preserved\n")
    daemon_parent = tmp_path / "daemon-parent"
    daemon_parent.mkdir(mode=0o700)
    daemon_root = daemon_parent / "private-daemon"
    daemon_root.mkdir(mode=0o700)

    authority = _cleanup_authority(run_root, daemon_root, set())
    owner = _EpisodeCleanupOwner(authority)
    churn = shared_parent / "ordinary-churn"
    churn.mkdir(mode=0o700)
    churn.rmdir()
    owner.revalidate()
    receipt = owner.close()

    parent_receipt = receipt["shared_parent_authority"]
    assert parent_receipt["expected"]["mode_int"] == parent_mode
    assert {
        row["mode_int"] for row in parent_receipt["observed"].values()
    } == {parent_mode}
    assert parent_receipt["authorized_child_inventory"] == [
        {
            "basename": "attempt",
            "path": os.fspath(run_root.resolve()),
            "device": authority.run_root_device,
            "inode": authority.run_root_inode,
            "owner_uid": os.getuid(),
            "mode": "0700",
            "absent": True,
        }
    ]
    assert parent_receipt["siblings_inspected"] is False
    assert parent_receipt["siblings_deleted"] is False
    assert sibling_marker.read_bytes() == b"preserved\n"
    assert not run_root.exists()


def test_stale_0700_capture_rejects_final_0755_parent(tmp_path: Path) -> None:
    shared_parent = tmp_path / "shared-parent"
    shared_parent.mkdir(mode=0o755)
    shared_parent.chmod(0o755)
    run_root = shared_parent / "attempt"
    run_root.mkdir(mode=0o700)
    daemon_parent = tmp_path / "daemon-parent"
    daemon_parent.mkdir(mode=0o700)
    daemon_root = daemon_parent / "private-daemon"
    daemon_root.mkdir(mode=0o700)
    stale = _cleanup_authority(run_root, daemon_root, set()).model_copy(
        update={"run_parent_mode": 0o700}
    )

    with pytest.raises(
        F3TargetEpisodeError,
        match="shared cleanup parent authority mismatch",
    ):
        _EpisodeCleanupOwner(stale)


@pytest.mark.parametrize("parent_mode", (0o775, 0o777))
def test_writable_shared_parent_modes_are_rejected(
    tmp_path: Path,
    parent_mode: int,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    run_root.mkdir(mode=0o700)
    daemon_root.mkdir(mode=0o700)
    payload = _cleanup_authority(run_root, daemon_root, set()).model_dump(
        mode="python"
    )
    payload["run_parent_mode"] = parent_mode

    with pytest.raises(
        ValidationError,
        match="owner writable/searchable and group/other non-writable",
    ):
        F3EpisodeCleanupAuthority.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "field",
    ("run_parent_device", "run_parent_inode", "run_parent_owner_uid"),
)
def test_shared_parent_wrong_identity_tuple_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    run_root.mkdir(mode=0o700)
    daemon_root.mkdir(mode=0o700)
    authority = _cleanup_authority(run_root, daemon_root, set())
    wrong = authority.model_copy(
        update={field: getattr(authority, field) + 1}
    )

    with pytest.raises(
        F3TargetEpisodeError,
        match="shared cleanup parent authority mismatch",
    ):
        _EpisodeCleanupOwner(wrong)


@pytest.mark.parametrize("changed_mode", (0o700, 0o775))
def test_shared_parent_mode_change_after_capture_is_rejected(
    tmp_path: Path,
    changed_mode: int,
) -> None:
    shared_parent = tmp_path / "shared-parent"
    shared_parent.mkdir(mode=0o755)
    shared_parent.chmod(0o755)
    run_root = shared_parent / "attempt"
    run_root.mkdir(mode=0o700)
    daemon_parent = tmp_path / "daemon-parent"
    daemon_parent.mkdir(mode=0o700)
    daemon_root = daemon_parent / "private-daemon"
    daemon_root.mkdir(mode=0o700)
    owner = _EpisodeCleanupOwner(
        _cleanup_authority(run_root, daemon_root, set())
    )
    shared_parent.chmod(changed_mode)

    with pytest.raises(
        BaseExceptionGroup,
        match="cleanup authority revalidation failed",
    ):
        owner.revalidate()
    owner.release()


@pytest.mark.parametrize("mutation", ("symlink", "substitution"))
def test_shared_parent_path_substitution_is_rejected_without_sibling_access(
    tmp_path: Path,
    mutation: str,
) -> None:
    shared_parent = tmp_path / "shared-parent"
    shared_parent.mkdir(mode=0o755)
    shared_parent.chmod(0o755)
    run_root = shared_parent / "attempt"
    run_root.mkdir(mode=0o700)
    marker = run_root / "keep"
    marker.write_bytes(b"preserved\n")
    daemon_parent = tmp_path / "daemon-parent"
    daemon_parent.mkdir(mode=0o700)
    daemon_root = daemon_parent / "private-daemon"
    daemon_root.mkdir(mode=0o700)
    authority = _cleanup_authority(run_root, daemon_root, set())
    relocated = tmp_path / "relocated-parent"
    shared_parent.rename(relocated)
    if mutation == "symlink":
        shared_parent.symlink_to(relocated, target_is_directory=True)
    else:
        shared_parent.mkdir(mode=0o755)
        (shared_parent / "attempt").mkdir(mode=0o700)

    with pytest.raises(F3TargetEpisodeError):
        _EpisodeCleanupOwner(authority)
    assert (relocated / "attempt" / "keep").read_bytes() == b"preserved\n"


def test_wrong_shared_parent_child_is_rejected_and_siblings_are_preserved(
    tmp_path: Path,
) -> None:
    shared_parent = tmp_path / "shared-parent"
    shared_parent.mkdir(mode=0o755)
    shared_parent.chmod(0o755)
    run_root = shared_parent / "attempt"
    run_root.mkdir(mode=0o700)
    original_marker = run_root / "original"
    original_marker.write_bytes(b"original\n")
    sibling = shared_parent / "sibling"
    sibling.mkdir(mode=0o700)
    sibling_marker = sibling / "keep"
    sibling_marker.write_bytes(b"sibling\n")
    daemon_parent = tmp_path / "daemon-parent"
    daemon_parent.mkdir(mode=0o700)
    daemon_root = daemon_parent / "private-daemon"
    daemon_root.mkdir(mode=0o700)
    owner = _EpisodeCleanupOwner(
        _cleanup_authority(run_root, daemon_root, set())
    )
    renamed = shared_parent / "renamed-attempt"
    run_root.rename(renamed)
    run_root.mkdir(mode=0o700)
    replacement_marker = run_root / "replacement"
    replacement_marker.write_bytes(b"replacement\n")

    with pytest.raises(BaseExceptionGroup, match="F3 cleanup failed"):
        owner.close()
    assert (renamed / "original").read_bytes() == b"original\n"
    assert replacement_marker.read_bytes() == b"replacement\n"
    assert sibling_marker.read_bytes() == b"sibling\n"


def test_partial_allocation_residue_is_exported_before_exact_cleanup(
    tmp_path: Path,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    cleanup_owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    locator_root = Path(spec.composition.stores.locator)
    locator_payloads = {
        "accepted.json": {
            "current_state": "accepted",
            "events": ["accepted"],
            "closed_tombstone_ref": None,
        },
        "stuck-allocating.json": {
            "current_state": "allocating",
            "events": ["accepted", "allocating"],
            "closed_tombstone_ref": None,
        },
        "quarantined.json": {
            "current_state": "quarantined",
            "events": ["accepted", "allocating", "failed", "quarantined"],
            "closed_tombstone_ref": None,
        },
    }
    for name, payload in locator_payloads.items():
        (locator_root / name).write_bytes(canonical_json_bytes(payload))
    assert len(tuple(locator_root.iterdir())) == 3
    assert not any(
        "closed" in path.name or "tombstone" in path.name
        for path in locator_root.iterdir()
    )
    assert all(
        stat.S_IMODE(os.stat(item.path, follow_symlinks=False).st_mode) == 0o400
        for item in spec.cleanup_authority.secret_files
    )

    daemon = spec.composition.installed.private_docker_daemon
    assert daemon is not None
    for path in (
        Path(daemon.containerd_ttrpc_socket_path),
        Path(daemon.data_root) / "partial-allocation",
        Path(daemon.log_root) / "dockerd.log",
    ):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_bytes(b"partial-allocation-residue\n")
    assert not os.path.lexists(daemon.pid_file)

    input_digest = "sha256:" + "6" * 64
    allocation_failure = F3TargetEpisodeError(
        "allocation failed before process publication"
    )
    primary = _export_durable_evidence(
        spec,
        cleanup_owner,
        external_authority,
        report=None,
        failure=allocation_failure,
    )
    primary_payload = json.loads(Path(primary["path"]).read_bytes())
    locator_entries = [
        item for item in primary_payload["entries"] if item["role"] == "locator"
    ]
    assert [item["path"] for item in locator_entries] == sorted(locator_payloads)

    cleanup_receipt = cleanup_owner.close()
    terminal = target_module._publish_terminal_cleanup_failure(
        spec,
        cleanup_owner,
        external_authority,
        input_digest,
        primary,
        allocation_failure,
        cleanup_receipt,
    )
    terminal_payload = json.loads(Path(terminal["path"]).read_bytes())
    assert terminal_payload["exact_absence_status"] == "verified_absent"
    assert terminal_payload["exact_absence_receipt"] == cleanup_receipt
    assert terminal_payload["cleanup_failure"]["type"] == "F3TargetEpisodeError"
    assert terminal_payload["cleanup_failure"]["message"].startswith("sha256:")
    assert not os.path.lexists(spec.cleanup_authority.run_root)
    assert not os.path.lexists(spec.cleanup_authority.daemon_root)
    with pytest.raises(
        F3TargetEpisodeError,
        match="durably failed during terminal cleanup",
    ):
        _recover_after_cleanup(
            spec,
            cleanup_owner,
            external_authority,
            input_digest,
        )


def test_cleanup_rejects_secret_digest_drift_after_sanitizing_and_is_stable(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    run_root.mkdir(mode=0o700)
    daemon_root.mkdir(mode=0o700)
    secret = run_root / "policy.secret"
    secret.write_bytes(b"original-secret\n")
    secret.chmod(0o400)
    authority = _cleanup_authority(
        run_root, daemon_root, {os.fspath(secret.resolve())}
    )
    owner = _EpisodeCleanupOwner(authority)
    secret.chmod(0o600)
    secret.write_bytes(b"changed-secret!\n")
    secret.chmod(0o400)

    with pytest.raises(BaseExceptionGroup, match="F3 cleanup failed") as first:
        owner.close()
    assert any("digest mismatch" in str(item) for item in first.value.exceptions)
    assert run_root.is_dir()
    assert daemon_root.is_dir()
    assert secret.read_bytes() == b"changed-secret!\n"
    assert owner._receipt is None
    assert not owner._fds
    assert not owner._secret_fds
    assert not owner._parent_fds
    assert not owner._secret_parent_fds
    with pytest.raises(BaseExceptionGroup) as second:
        owner.close()
    assert second.value is first.value



def test_durable_evidence_export_precedes_cleanup_and_is_restart_idempotent(
    tmp_path: Path,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    report_payload = _report_payload()
    input_digest = "sha256:" + "d" * 64
    report_payload["inputs"]["target_episode_input_sha256"] = input_digest
    report = F3TargetEpisodeReport.model_validate(report_payload, strict=True)

    first = _export_durable_evidence(
        spec, owner, external_authority, report=report, failure=None
    )
    exported = Path(spec.evidence_export.path)
    stale_pre = exported.with_name(f".{exported.name}.crash.tmp")
    os.link(exported, stale_pre)
    second = _export_durable_evidence(
        spec, owner, external_authority, report=report, failure=None
    )

    assert first == second
    assert not stale_pre.exists()
    assert exported.is_file()
    payload = json.loads(exported.read_bytes())
    assert {item["role"] for item in payload["entries"]} <= {
        "cas",
        "locator",
        "service_output",
    }
    forged = dict(payload)
    forged["secret"] = "PRIVATE-FORGED-RECORD"
    with pytest.raises(F3TargetEpisodeError, match="schema is inexact"):
        target_module._validate_pre_cleanup_record(
            spec, forged, input_digest
        )
    exported_raw = exported.read_bytes()
    for secret in spec.cleanup_authority.secret_files:
        assert Path(secret.path).read_bytes() not in exported_raw
    cleanup_receipt = owner.close()
    assert exported.is_file()
    assert not Path(spec.cleanup_authority.run_root).exists()
    assert not Path(spec.cleanup_authority.daemon_root).exists()
    final_first = target_module._publish_final_evidence(
        spec, owner, external_authority, report, cleanup_receipt, first
    )
    final_path = Path(spec.evidence_export.final_path)
    stale_final = final_path.with_name(f".{final_path.name}.crash.tmp")
    os.link(final_path, stale_final)
    final_second = target_module._publish_final_evidence(
        spec, owner, external_authority, report, cleanup_receipt, first
    )
    assert final_first == final_second
    assert not stale_final.exists()
    recovered = _recover_after_cleanup(
        spec, owner, external_authority, input_digest
    )
    assert recovered is not None
    assert recovered.cleanup["exact_absence_receipt"]["exact_absence"] is True
    assert recovered.evidence_export["final"]["verified"] is True
    assert Path(spec.evidence_export.final_path).is_file()


def test_absent_recovery_is_inode_bound_and_rejects_post_final_replacement(
    tmp_path: Path,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    live_owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    input_digest = "sha256:" + "a" * 64
    report_payload = _report_payload()
    report_payload["inputs"]["target_episode_input_sha256"] = input_digest
    report = F3TargetEpisodeReport.model_validate(report_payload, strict=True)
    primary = _export_durable_evidence(
        spec,
        live_owner,
        external_authority,
        report=report,
        failure=None,
    )
    cleanup_receipt = live_owner.close()
    target_module._publish_final_evidence(
        spec,
        live_owner,
        external_authority,
        report,
        cleanup_receipt,
        primary,
    )

    absent_owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    assert absent_owner.expected_absent is True
    assert not absent_owner._fds
    assert not absent_owner._secret_fds
    recovered = _recover_after_cleanup(
        spec,
        absent_owner,
        external_authority,
        input_digest,
    )
    assert recovered is not None
    absent_owner.release()
    assert not absent_owner._parent_fds
    assert not absent_owner._secret_parent_fds

    replacement = Path(spec.cleanup_authority.run_root)
    replacement.mkdir(mode=0o700)
    marker = replacement / "unrelated"
    marker.write_bytes(b"survives\n")
    with pytest.raises(
        F3TargetEpisodeError,
        match="closed cleanup authority path was replaced",
    ):
        _recover_after_cleanup(
            spec,
            absent_owner,
            external_authority,
            input_digest,
        )
    assert marker.read_bytes() == b"survives\n"


@pytest.mark.asyncio
async def test_restart_finishes_exported_pre_cleanup_episode_without_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    input_digest = "sha256:" + "e" * 64
    report_payload = _report_payload()
    report_payload["inputs"]["target_episode_input_sha256"] = input_digest
    report = F3TargetEpisodeReport.model_validate(report_payload, strict=True)
    _export_durable_evidence(
        spec, owner, external_authority, report=report, failure=None
    )
    events: list[str] = []

    class RecoveredComposition:
        async def close(self) -> None:
            events.append("composition-close")

    class RecoveredQuota:
        def close(self) -> None:
            events.append("quota-close")

    monkeypatch.setattr(
        target_module,
        "load_f3_production_composition",
        lambda *_args: RecoveredComposition(),
    )
    monkeypatch.setattr(
        target_module,
        "_WorkspaceQuotaRoot",
        lambda *_args: RecoveredQuota(),
    )

    recovered = await _resume_pre_cleanup_export(
        spec, owner, external_authority, input_digest
    )

    assert recovered is not None
    assert events == ["composition-close", "quota-close"]
    assert recovered.cleanup["exact_absence_receipt"]["exact_absence"] is True
    assert Path(spec.evidence_export.final_path).is_file()
    assert not Path(spec.cleanup_authority.run_root).exists()
    assert not Path(spec.cleanup_authority.daemon_root).exists()


def test_durable_evidence_rejects_embedded_secret_content(
    tmp_path: Path,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    secret = Path(spec.cleanup_authority.secret_files[0].path).read_bytes()
    leaked = Path(spec.composition.stores.service_output_root) / "leaked.txt"
    leaked.write_bytes(b"prefix-" + secret + b"-suffix")

    with pytest.raises(F3TargetEpisodeError, match="secret content"):
        _export_durable_evidence(
            spec,
            owner,
            external_authority,
            report=F3TargetEpisodeReport.model_validate(
                _report_payload(), strict=True
            ),
            failure=None,
        )

    assert not Path(spec.evidence_export.path).exists()
    assert Path(spec.cleanup_authority.run_root).is_dir()
    leaked.unlink()
    secret_text = "PRIVATE-FAILURE-TOKEN"
    untrusted = Path(
        spec.composition.stores.service_output_root
    ) / "untrusted.log"
    untrusted.write_text("PRIVATE-EXECUTION-OUTPUT")
    receipt = _export_durable_evidence(
        spec,
        owner,
        external_authority,
        report=None,
        failure=RuntimeError(secret_text),
    )
    raw = Path(spec.evidence_export.path).read_bytes()
    assert secret_text.encode() not in raw
    failure = json.loads(raw)["failure"]
    assert set(failure) == {"code", "type", "message", "operation", "details"}
    assert failure["code"] == "unknown_failure"
    assert failure["details"]["leaf_count"] == 1
    assert failure["details"]["leaves"][0]["code"] == "unknown_failure"
    assert receipt["verified"] is True
    digest_raw = Path(spec.evidence_export.path).read_bytes()
    assert b"PRIVATE-EXECUTION-OUTPUT" not in digest_raw
    assert all(
        set(item) == {"path", "role", "sha256", "size_bytes"}
        for item in json.loads(digest_raw)["entries"]
    )
    assert receipt["verified"] is True


def test_safe_failure_retains_bounded_broker_cleanup_leaves_and_final_absence() -> None:
    remote_leaves = [
        {
            "code": f"daemon_cleanup_{index}",
            "type": "OSError",
            "message": f"daemon cleanup leaf {index}",
            "operation": "shutdown",
            "details": {"group_path": [index], "errno": index + 1},
        }
        for index in range(4)
    ]
    broker_failure = MountNamespaceBrokerError(
        "workspace_authority_mismatch",
        "broker rejected the operation",
        details={
            "error": "ExceptionGroup",
            "message": "private Docker daemon cleanup failed",
            "details": {
                "operation": "shutdown",
                "exception_leaves": remote_leaves,
                "secret_value": "PRIVATE-DAEMON-TOKEN",
            },
        },
    )
    absence_failure = MountNamespaceBrokerError(
        "runtime_unsupported",
        "broker final absence proof failed",
    )

    projected = target_module._safe_export_failure(
        BaseExceptionGroup(
            "broker cleanup failed",
            [broker_failure, absence_failure],
        )
    )

    assert projected is not None
    assert set(projected) == {"code", "type", "message", "operation", "details"}
    assert projected["code"] == "exception_group"
    local_leaves = projected["details"]["leaves"]
    assert len(local_leaves) == 2
    assert local_leaves[0]["operation"] == "shutdown"
    retained = local_leaves[0]["details"]["details"]["exception_leaves"]
    assert [leaf["code"] for leaf in retained] == [
        f"daemon_cleanup_{index}" for index in range(4)
    ]
    assert all(leaf["operation"] == "shutdown" for leaf in retained)
    assert local_leaves[1]["code"] == "runtime_unsupported"
    assert local_leaves[1]["message"] != local_leaves[0]["message"]
    assert b"PRIVATE-DAEMON-TOKEN" not in canonical_json_bytes(projected)

def test_terminal_cleanup_failure_is_linked_verified_and_recovery_fails_closed(
    tmp_path: Path,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    input_digest = "sha256:" + "d" * 64
    owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    primary = _export_durable_evidence(
        spec,
        owner,
        external_authority,
        report=None,
        failure=RuntimeError("primary execution failure"),
    )
    primary_path = Path(spec.evidence_export.path)
    primary_raw = primary_path.read_bytes()
    cleanup_receipt = owner.close()
    remote_leaves = [
        {
            "code": f"daemon_cleanup_{index}",
            "type": "OSError",
            "message": f"daemon cleanup leaf {index}",
            "operation": "shutdown",
            "details": {"group_path": [index], "errno": index + 1},
        }
        for index in range(4)
    ]
    broker_failure = MountNamespaceBrokerError(
        "workspace_authority_mismatch",
        "broker rejected the operation",
        details={
            "error": "ExceptionGroup",
            "message": "private Docker daemon cleanup failed",
            "details": {
                "operation": "shutdown",
                "exception_leaves": remote_leaves,
                "secret_value": "PRIVATE-DAEMON-TOKEN",
            },
        },
    )
    final_absence = MountNamespaceBrokerError(
        "runtime_unsupported",
        "broker final absence proof failed",
    )
    cleanup_failure = BaseExceptionGroup(
        "broker cleanup failed",
        [broker_failure, final_absence],
    )

    terminal = target_module._publish_terminal_cleanup_failure(
        spec,
        owner,
        external_authority,
        input_digest,
        primary,
        cleanup_failure,
        cleanup_receipt,
    )

    assert primary_path.read_bytes() == primary_raw
    terminal_path = Path(spec.evidence_export.cleanup_failure_path)
    terminal_raw = terminal_path.read_bytes()
    assert terminal["sha256"] == sha256_bytes(terminal_raw)
    assert terminal["size_bytes"] == len(terminal_raw)
    assert terminal["verified"] is True
    payload = json.loads(terminal_raw)
    assert payload["target_episode_input_sha256"] == input_digest
    assert payload["primary_export"]["path"] == os.fspath(primary_path)
    assert payload["primary_export_sha256"] == primary["sha256"]
    assert payload["exact_absence_status"] == "verified_absent"
    assert payload["exact_absence_receipt"] == cleanup_receipt
    local_leaves = payload["cleanup_failure"]["details"]["leaves"]
    retained = local_leaves[0]["details"]["details"]["exception_leaves"]
    assert [leaf["code"] for leaf in retained] == [
        f"daemon_cleanup_{index}" for index in range(4)
    ]
    assert local_leaves[1]["code"] == "runtime_unsupported"
    assert b"PRIVATE-DAEMON-TOKEN" not in terminal_raw
    assert not os.path.lexists(spec.cleanup_authority.run_root)
    assert not os.path.lexists(spec.cleanup_authority.daemon_root)
    assert all(
        not os.path.lexists(item.path)
        for item in spec.cleanup_authority.secret_files
    )
    assert all(
        os.path.commonpath((root, os.fspath(terminal_path))) != root
        for root in (
            spec.cleanup_authority.run_root,
            spec.cleanup_authority.daemon_root,
        )
    )
    with pytest.raises(
        F3TargetEpisodeError,
        match="durably failed during terminal cleanup",
    ):
        target_module._recover_after_cleanup(
            spec, owner, external_authority, input_digest
        )

def _with_lexical_export_parent(
    spec: F3TargetEpisodeInput,
    parent: Path,
) -> F3TargetEpisodeInput:
    authority = spec.evidence_export
    relocated = authority.model_copy(
        update={
            field: os.path.join(
                os.fspath(parent),
                os.path.basename(getattr(authority, field)),
            )
            for field in (
                "path",
                "final_path",
                "cleanup_failure_path",
                "lease_path",
            )
        }
    )
    return spec.model_copy(update={"evidence_export": relocated})


def test_execution_lease_rejects_symlinked_export_parent_ancestor(
    tmp_path: Path,
) -> None:
    spec = _target_spec(tmp_path)
    alias = tmp_path / "ancestor-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    aliased = _with_lexical_export_parent(
        spec,
        alias / Path(spec.evidence_export.path).parent.name,
    )
    digest = sha256_bytes(
        canonical_json_bytes(aliased.model_dump(mode="json"))
    )

    cleanup_owner = _EpisodeCleanupOwner(aliased.cleanup_authority)
    with pytest.raises(F3TargetEpisodeError, match="symlink"):
        authority = target_module._EpisodeExternalAuthority(aliased)
        try:
            target_module._EpisodeExecutionLease(
                aliased, digest, authority, cleanup_owner
            )
        finally:
            authority.close()
        cleanup_owner.release()


def test_execution_lease_rejects_retargeted_export_parent_ancestor(
    tmp_path: Path,
) -> None:
    spec = _target_spec(tmp_path)
    original_parent = Path(spec.evidence_export.path).parent
    gateway = tmp_path / "gateway"
    gateway.mkdir(mode=0o700)
    nested_parent = gateway / original_parent.name
    original_parent.rename(nested_parent)
    nested = _with_lexical_export_parent(spec, nested_parent)
    moved_gateway = tmp_path / "moved-gateway"
    gateway.rename(moved_gateway)
    gateway.symlink_to(moved_gateway, target_is_directory=True)
    digest = sha256_bytes(
        canonical_json_bytes(nested.model_dump(mode="json"))
    )

    cleanup_owner = _EpisodeCleanupOwner(nested.cleanup_authority)
    with pytest.raises(F3TargetEpisodeError, match="symlink"):
        authority = target_module._EpisodeExternalAuthority(nested)
        try:
            target_module._EpisodeExecutionLease(
                nested, digest, authority, cleanup_owner
            )
        finally:
            authority.close()
        cleanup_owner.release()


def test_execution_lease_rejects_concurrent_owner(tmp_path: Path) -> None:
    spec = _target_spec(tmp_path)
    digest = sha256_bytes(
        canonical_json_bytes(spec.model_dump(mode="json"))
    )
    authority = target_module._EpisodeExternalAuthority(spec)
    cleanup_owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    first = target_module._EpisodeExecutionLease(
        spec, digest, authority, cleanup_owner
    )
    try:
        with pytest.raises(F3TargetEpisodeError, match="already leased"):
            target_module._EpisodeExecutionLease(
                spec, digest, authority, cleanup_owner
            )
    finally:
        first.close()

    retry = target_module._EpisodeExecutionLease(
        spec, digest, authority, cleanup_owner
    )
    retry.close()
    authority.close()
    cleanup_owner.release()

@pytest.mark.parametrize("mutation", ["symlink", "replacement"])
@pytest.mark.parametrize(
    "operation", ["publisher", "reader", "recovery", "cleanup"]
)
def test_external_authority_rejects_export_parent_retarget_across_lifecycle(
    tmp_path: Path,
    external_authority_factory,
    mutation: str,
    operation: str,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    cleanup_owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    input_digest = "sha256:" + "8" * 64
    if operation != "publisher":
        _export_durable_evidence(
            spec,
            cleanup_owner,
            external_authority,
            report=None,
            failure=RuntimeError("pre-cleanup failure"),
        )

    parent = Path(spec.evidence_export.path).parent
    pinned_parent = tmp_path / f"pinned-{mutation}-{operation}"
    parent.rename(pinned_parent)
    if mutation == "symlink":
        replacement = tmp_path / f"attacker-{operation}"
        replacement.mkdir(mode=0o700)
        parent.symlink_to(replacement, target_is_directory=True)
    else:
        replacement = parent
        replacement.mkdir(mode=0o700)
    marker = replacement / "unrelated"
    marker.write_bytes(b"survives\n")

    expected_error = F3TargetEpisodeError
    expected_message = "symlink-free component path|parent path identity changed"
    with pytest.raises(expected_error, match=expected_message):
        if operation == "publisher":
            _export_durable_evidence(
                spec,
                cleanup_owner,
                external_authority,
                report=None,
                failure=RuntimeError("publisher failure"),
            )
        elif operation == "reader":
            target_module._read_external_record(
                spec,
                cleanup_owner,
                external_authority,
                spec.evidence_export.path,
                "bb.rl.phase5-f3-durable-evidence-export.v1",
            )
        elif operation == "recovery":
            _recover_after_cleanup(
                spec, cleanup_owner, external_authority, input_digest
            )
        else:
            try:
                external_authority.revalidate(cleanup_owner)
            finally:
                cleanup_owner.close()

    assert marker.read_bytes() == b"survives\n"
    if operation == "cleanup":
        assert not os.path.lexists(spec.cleanup_authority.run_root)
        assert not os.path.lexists(spec.cleanup_authority.daemon_root)
    else:
        assert Path(spec.cleanup_authority.run_root).is_dir()
        assert Path(spec.cleanup_authority.daemon_root).is_dir()
    if parent.is_symlink():
        parent.unlink()
    else:
        marker.unlink()
        parent.rmdir()
    pinned_parent.rename(parent)

    if operation == "publisher":
        retry = _export_durable_evidence(
            spec,
            cleanup_owner,
            external_authority,
            report=None,
            failure=RuntimeError("publisher failure"),
        )
        assert retry["verified"] is True
    elif operation == "reader":
        retry = target_module._read_external_record(
            spec,
            cleanup_owner,
            external_authority,
            spec.evidence_export.path,
            "bb.rl.phase5-f3-durable-evidence-export.v1",
        )
        assert retry is not None
    elif operation == "recovery":
        assert (
            _recover_after_cleanup(
                spec, cleanup_owner, external_authority, input_digest
            )
            is None
        )
    receipt = cleanup_owner.close()
    assert receipt["exact_absence"] is True
    if mutation == "symlink":
        assert marker.read_bytes() == b"survives\n"


def test_cleanup_rejects_daemon_root_renamed_outside_pinned_parent(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    run_root.mkdir(mode=0o700)
    daemon_root.mkdir(mode=0o700)
    secret = run_root / "api.secret"
    secret.write_bytes(b"cross-parent-bound-secret\n")
    secret.chmod(0o400)
    owner = _EpisodeCleanupOwner(
        _cleanup_authority(
            run_root, daemon_root, {os.fspath(secret.resolve())}
        )
    )
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(mode=0o700)
    escaped = outside / "escaped-daemon"
    daemon_root.rename(escaped)

    with pytest.raises(BaseExceptionGroup, match="F3 cleanup failed") as first:
        owner.close()

    assert not run_root.exists()
    assert escaped.is_dir()
    assert list(escaped.iterdir()) == []
    assert owner._receipt is None
    with pytest.raises(BaseExceptionGroup) as second:
        owner.close()
    assert second.value is first.value


def test_durable_evidence_partial_write_preserves_cleanup_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    owner = _EpisodeCleanupOwner(spec.cleanup_authority)
    monkeypatch.setattr(target_module.os, "write", lambda *_args: 0)

    with pytest.raises(F3TargetEpisodeError, match="no progress"):
        _export_durable_evidence(
            spec,
            owner,
            external_authority,
            report=F3TargetEpisodeReport.model_validate(
                _report_payload(), strict=True
            ),
            failure=None,
        )

    assert not Path(spec.evidence_export.path).exists()
    assert Path(spec.cleanup_authority.run_root).is_dir()
    assert Path(spec.cleanup_authority.daemon_root).is_dir()




def test_cleanup_sanitizes_nested_root_rename_without_issuing_receipt(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    run_root.mkdir(mode=0o700)
    daemon_root.mkdir(mode=0o700)
    secret = run_root / "api.secret"
    secret.write_bytes(b"rename-bound-secret\n")
    secret.chmod(0o400)
    authority = _cleanup_authority(
        run_root, daemon_root, {os.fspath(secret.resolve())}
    )
    owner = _EpisodeCleanupOwner(authority)
    nested = tmp_path / "nested"
    nested.mkdir()
    renamed = nested / "renamed-attempt"
    run_root.rename(renamed)

    with pytest.raises(BaseExceptionGroup, match="F3 cleanup failed") as first:
        owner.close()

    assert (renamed / "api.secret").read_bytes() == b"rename-bound-secret\n"
    assert daemon_root.is_dir()
    assert owner._receipt is None
    with pytest.raises(BaseExceptionGroup) as second:
        owner.close()
    assert second.value is first.value


def test_cleanup_rejects_secret_rename_escape_after_sanitizing_roots(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    run_root.mkdir(mode=0o700)
    daemon_root.mkdir(mode=0o700)
    secret = run_root / "api.secret"
    secret.write_bytes(b"nested-secret-escape\n")
    secret.chmod(0o400)
    authority = _cleanup_authority(
        run_root, daemon_root, {os.fspath(secret.resolve())}
    )
    owner = _EpisodeCleanupOwner(authority)
    nested = tmp_path / "nested"
    nested.mkdir()
    escaped = nested / secret.name
    secret.rename(escaped)
    marker = nested / "unrelated"
    marker.write_bytes(b"survives\n")

    with pytest.raises(BaseExceptionGroup, match="F3 cleanup failed") as first:
        owner.close()

    assert run_root.is_dir()
    assert daemon_root.is_dir()
    assert escaped.read_bytes() == b"nested-secret-escape\n"
    assert marker.read_bytes() == b"survives\n"
    assert owner._receipt is None
    assert not owner._fds
    assert not owner._secret_fds
    assert not owner._parent_fds
    assert not owner._secret_parent_fds
    with pytest.raises(BaseExceptionGroup) as second:
        owner.close()
    assert second.value is first.value


def test_cleanup_rejects_root_replacement_and_preserves_unrelated_marker(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "attempt"
    daemon_root = tmp_path / "private-daemon"
    run_root.mkdir(mode=0o700)
    daemon_root.mkdir(mode=0o700)
    secret = run_root / "api.secret"
    secret.write_bytes(b"replacement-bound-secret\n")
    secret.chmod(0o400)
    authority = _cleanup_authority(
        run_root, daemon_root, {os.fspath(secret.resolve())}
    )
    owner = _EpisodeCleanupOwner(authority)
    renamed = tmp_path / "renamed-attempt"
    run_root.rename(renamed)
    run_root.mkdir(mode=0o700)
    replacement_marker = run_root / "unrelated"
    replacement_marker.write_bytes(b"survives\n")

    with pytest.raises(BaseExceptionGroup, match="F3 cleanup failed") as first:
        owner.close()

    assert (renamed / "api.secret").read_bytes() == b"replacement-bound-secret\n"
    assert replacement_marker.read_bytes() == b"survives\n"
    assert daemon_root.is_dir()
    assert owner._receipt is None
    with pytest.raises(BaseExceptionGroup) as second:
        owner.close()
    assert second.value is first.value


def test_fixed_policy_identity_is_exact_and_generation_evidence_is_joined(
    tmp_path: Path,
) -> None:
    spec = _target_spec(tmp_path)
    evidence = _read_generation_evidence(spec)

    assert evidence.independent is True
    assert evidence.generator_role == "agent-candidate"
    assert evidence.command_sha256 == spec.policy.patch_application_command_sha256
    assert evidence.repository_snapshot_digest == spec.refs.repository.digest
    assert evidence.model == spec.policy.model

    changed = evidence.model_dump(mode="json")
    changed["model"]["checkpoint_digest"] = "sha256:" + "f" * 64
    Path(spec.policy.generation_evidence.path).chmod(0o600)
    Path(spec.policy.generation_evidence.path).write_bytes(
        canonical_json_bytes(changed)
    )
    with pytest.raises(F3TargetEpisodeError, match="digest mismatch"):
        _read_generation_evidence(spec)


@pytest.mark.asyncio
async def test_setup_failure_closes_composition_and_quota_with_all_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    external_authority_factory,
) -> None:
    spec = _target_spec(tmp_path)
    external_authority = external_authority_factory(spec)
    input_digest = "sha256:" + "a" * 64
    events: list[str] = []

    class Quota:
        def mount(self) -> None:
            pass

        def close(self) -> None:
            events.append("quota")
            raise RuntimeError("quota-close")

    class Composition:
        app = SimpleNamespace(state=SimpleNamespace(episode_service=object()))

        async def close(self) -> None:
            events.append("composition")
            raise RuntimeError("composition-close")

    def reject_install(*_args: object) -> None:
        raise RuntimeError("install-failed")

    monkeypatch.setenv("SLURM_JOB_ID", "275232")
    monkeypatch.setenv("SLURM_JOB_NODELIST", "cnode-88")
    monkeypatch.setattr(
        target_module, "_read_generation_evidence", lambda _spec: object()
    )
    monkeypatch.setattr(target_module, "_WorkspaceQuotaRoot", lambda *_args: Quota())
    monkeypatch.setattr(
        target_module,
        "build_f3_production_composition",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        target_module,
        "load_f3_production_composition",
        lambda *_args: Composition(),
    )
    monkeypatch.setattr(target_module, "_install_exact_quota_backend", reject_install)

    with pytest.raises(BaseExceptionGroup) as raised:
        await target_module._run_f3_target_episode(
            spec,
            input_digest,
        )

    assert events == ["composition", "quota"]
    assert [str(item) for item in raised.value.exceptions] == [
        "install-failed",
        "composition-close",
        "quota-close",
    ]
    assert not os.path.lexists(spec.cleanup_authority.run_root)
    assert not os.path.lexists(spec.cleanup_authority.daemon_root)
    assert all(
        not os.path.lexists(item.path)
        for item in spec.cleanup_authority.secret_files
    )
    primary_path = Path(spec.evidence_export.path)
    terminal_path = Path(spec.evidence_export.cleanup_failure_path)
    assert primary_path.is_file()
    assert terminal_path.is_file()
    assert not Path(spec.evidence_export.final_path).exists()
    primary_raw = primary_path.read_bytes()
    terminal_raw = terminal_path.read_bytes()
    primary = json.loads(primary_raw)
    terminal = json.loads(terminal_raw)
    assert primary["report"] is None
    assert primary["failure"]["details"]["leaf_count"] == 1
    assert terminal["primary_export_sha256"] == sha256_bytes(primary_raw)
    assert terminal["primary_export"]["path"] == os.fspath(primary_path)
    assert terminal["exact_absence_status"] == "verified_absent"
    assert terminal["exact_absence_receipt"]["exact_absence"] is True
    assert [
        leaf["type"]
        for leaf in terminal["cleanup_failure"]["details"]["leaves"]
    ] == ["RuntimeError", "RuntimeError", "RuntimeError"]
    with pytest.raises(
        F3TargetEpisodeError,
        match="durably failed during terminal cleanup",
    ):
        target_module._recover_after_cleanup(
            spec,
            target_module._EpisodeCleanupOwner(spec.cleanup_authority),
            external_authority,
            input_digest,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["symlink", "replacement"])
async def test_post_publication_parent_retarget_still_cleans_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    spec = _target_spec(tmp_path)
    input_digest = "sha256:" + "9" * 64
    events: list[str] = []
    attack: dict[str, Path] = {}

    class Quota:
        def mount(self) -> None:
            pass

        def close(self) -> None:
            events.append("quota")

    class Composition:
        app = SimpleNamespace(state=SimpleNamespace(episode_service=object()))

        async def close(self) -> None:
            events.append("composition")

    def reject_install(*_args: object) -> None:
        raise F3TargetEpisodeError("primary installation failure")

    original_export = target_module._export_durable_evidence

    def relocate_after_publish(
        *args: object, **kwargs: object
    ) -> dict[str, object]:
        receipt = original_export(*args, **kwargs)
        parent = Path(spec.evidence_export.path).parent
        pinned_parent = tmp_path / f"published-{mutation}"
        parent.rename(pinned_parent)
        if mutation == "symlink":
            replacement = tmp_path / "attacker"
            replacement.mkdir(mode=0o700)
            parent.symlink_to(replacement, target_is_directory=True)
        else:
            replacement = parent
            replacement.mkdir(mode=0o700)
        marker = replacement / "unrelated"
        marker.write_bytes(b"survives\n")
        attack.update(
            {
                "pinned_parent": pinned_parent,
                "replacement": replacement,
                "marker": marker,
            }
        )
        return receipt

    monkeypatch.setenv("SLURM_JOB_ID", "275233")
    monkeypatch.setenv("SLURM_JOB_NODELIST", "cnode-89")
    monkeypatch.setattr(
        target_module, "_read_generation_evidence", lambda _spec: object()
    )
    monkeypatch.setattr(target_module, "_WorkspaceQuotaRoot", lambda *_args: Quota())
    monkeypatch.setattr(
        target_module,
        "build_f3_production_composition",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        target_module,
        "load_f3_production_composition",
        lambda *_args: Composition(),
    )
    monkeypatch.setattr(target_module, "_install_exact_quota_backend", reject_install)
    monkeypatch.setattr(
        target_module, "_export_durable_evidence", relocate_after_publish
    )

    with pytest.raises(BaseExceptionGroup) as raised:
        await target_module._run_f3_target_episode(spec, input_digest)

    assert events == ["composition", "quota"]
    assert isinstance(raised.value.exceptions[0], F3TargetEpisodeError)
    assert str(raised.value.exceptions[0]) == "primary installation failure"
    assert all(
        isinstance(item, F3TargetEpisodeError)
        for item in raised.value.exceptions[1:]
    )
    assert any(
        "symlink-free component path" in str(item)
        or "parent path identity changed" in str(item)
        for item in raised.value.exceptions[1:]
    )
    assert len(raised.value.exceptions) == 3
    assert not os.path.lexists(spec.cleanup_authority.run_root)
    assert not os.path.lexists(spec.cleanup_authority.daemon_root)
    assert all(
        not os.path.lexists(item.path)
        for item in spec.cleanup_authority.secret_files
    )
    assert attack["marker"].read_bytes() == b"survives\n"
    primary_path = attack["pinned_parent"] / Path(
        spec.evidence_export.path
    ).name
    assert primary_path.is_file()
    assert json.loads(primary_path.read_bytes())["report"] is None
    for path in (
        spec.evidence_export.cleanup_failure_path,
        spec.evidence_export.final_path,
    ):
        assert not (
            attack["pinned_parent"] / Path(path).name
        ).exists()
        assert not Path(path).exists()


@pytest.mark.asyncio
async def test_real_terminal_adapter_dispatches_candidate_patch_and_submit() -> None:
    command = "git apply --whitespace=error /workspace/agent-candidate.patch"
    response = {
        "output": [
            {
                "type": "function_call",
                "call_id": "apply-agent-candidate-patch",
                "name": "shell",
                "arguments": canonical_json_bytes({"command": command}).decode("utf-8"),
            },
            {
                "type": "function_call",
                "call_id": "submit-agent-candidate-patch",
                "name": "submit",
                "arguments": '{"result":"agent-candidate patch applied"}',
            },
        ]
    }
    policy = ScriptedPolicy([response])
    workspace = RecordingWorkspace()
    session, workspace, _cancellation, _sink = await _open(
        policy=policy, workspace=workspace, episode_id="f3-terminal-dispatch"
    )
    try:
        result = await session.run(
            _request(
                params={
                    "model": "f3-policy-model",
                    "input": '{"prompt":"repair admitted repository","task_id":"R-SWE-001"}',
                },
                tools=TERMINAL_TOOL_DEFINITIONS,
            )
        )
    finally:
        await session.close()

    assert result.termination is RunnerTermination.SUBMITTED
    assert result.turn_count == 1
    assert policy.requests[0]["model"] == "f3-policy-model"
    assert workspace.actions == [("shell", command, 9)]
    assert [
        event.tool_name for event in result.events if isinstance(event, ToolCallEvent)
    ] == ["shell", "submit"]


def test_rejects_resolved_policy_slot_mismatch(tmp_path: Path) -> None:
    spec = _target_spec(tmp_path)
    plan = _effective_plan()

    class Store:
        def load(self, *_args: object, **_kwargs: object) -> bytes:
            return plan.canonical_bytes()

    composition = SimpleNamespace(authority_graph=SimpleNamespace(store=Store()))
    create = SimpleNamespace(
        response=SimpleNamespace(
            effective_plan_ref=c.ArtifactRef(
                artifact_id=plan.canonical_digest(),
                sha256=plan.canonical_digest(),
                size_bytes=len(plan.canonical_bytes()),
                media_type="application/vnd.breadboard.effective-plan+json;version=1",
            ),
            effective_plan_digest=plan.canonical_digest(),
            policy_capability_observation_digest=(
                spec.policy.policy_capability_observation_digest
            ),
        )
    )
    with pytest.raises(F3TargetEpisodeError, match="exact terminal policy plan"):
        _validate_resolved_policy(spec, composition, create)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("policy-route", "TLS routes differ"),
        ("generation-ref", "generation reference"),
        ("gold-role", "forbidden authority role"),
    ],
)
def test_rejects_policy_identity_ref_and_gold_control_mismatch(
    tmp_path: Path, mutation: str, message: str
) -> None:
    spec = _target_spec(tmp_path)
    payload = spec.model_dump(mode="json")
    if mutation == "policy-route":
        payload["policy"]["route_id"] = "different-route"
    elif mutation == "generation-ref":
        digest = "sha256:" + "f" * 64
        payload["refs"]["generation"] = {
            "immutable_reference": f"cas://phase5/generation@{digest}",
            "digest": digest,
        }
    else:
        payload["policy_visible_prompt"] = "Use the forbidden gold_patch artifact."
    with pytest.raises((ValidationError, TypeError), match=message):
        F3TargetEpisodeInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


@pytest.mark.parametrize(
    "role",
    ("path", "final_path", "cleanup_failure_path", "lease_path"),
)
@pytest.mark.parametrize("root_role", ("run_root", "daemon_root"))
def test_target_input_rejects_external_path_equal_to_cleanup_root(
    tmp_path: Path,
    role: str,
    root_role: str,
) -> None:
    spec = _target_spec(tmp_path)
    payload = spec.model_dump(mode="json")
    root = Path(payload["cleanup_authority"][root_role])
    parent = root.parent
    external = payload["evidence_export"]
    for field in ("path", "final_path", "cleanup_failure_path", "lease_path"):
        external[field] = os.fspath((parent / f"external-{field}").resolve())
    external[role] = os.fspath(root.resolve())

    with pytest.raises(ValidationError, match="must be outside cleanup path"):
        F3TargetEpisodeInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


@pytest.mark.parametrize("root_role", ("run_root", "daemon_root"))
def test_target_input_rejects_external_paths_descending_from_cleanup_root(
    tmp_path: Path,
    root_role: str,
) -> None:
    spec = _target_spec(tmp_path)
    payload = spec.model_dump(mode="json")
    root = Path(payload["cleanup_authority"][root_role])
    parent = root / "external"
    external = payload["evidence_export"]
    for field in ("path", "final_path", "cleanup_failure_path", "lease_path"):
        external[field] = os.fspath((parent / field).resolve())

    with pytest.raises(ValidationError, match="must be outside cleanup path"):
        F3TargetEpisodeInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_target_input_rejects_non_page_aligned_workspace_quota(
    tmp_path: Path,
) -> None:
    payload = _target_spec(tmp_path).model_dump(mode="json")
    payload["workspace_quota_bytes"] -= 1
    with pytest.raises(ValidationError, match="page aligned"):
        F3TargetEpisodeInput.model_validate(payload, strict=True)


def _report_payload() -> dict[str, object]:
    return {
        "schema_version": "bb.rl.phase5-f3-target-episode-report.v1",
        "scheduler": {"slurm_job_id": "265001", "slurm_node_list": "gpu001"},
        "inputs": {"target_episode_input_sha256": "sha256:" + "1" * 64},
        "authorities": {
            "task_ref": "cas://task@sha256:" + "2" * 64,
            "repository_ref": "cas://repository@sha256:" + "3" * 64,
            "generation_ref": "cas://generation@sha256:" + "4" * 64,
        },
        "images": (
            {"role": "primary", "loaded_image_id": "sha256:" + "5" * 64},
            {"role": "verifier", "loaded_image_id": "sha256:" + "6" * 64},
        ),
        "resolution": {"effective_plan_digest": "sha256:" + "7" * 64},
        "lifecycle": {"terminal_request_count": 1},
        "artifacts": {"evidence_root": "sha256:" + "8" * 64},
        "verifier": {
            "passed": True,
            "reward": 1,
            "verifier_result_digest": "sha256:" + "9" * 64,
        },
        "cleanup": {"released": True, "no_orphan": True, "lease_root_entries": []},
        "claim_boundary": (
            "One R-SWE-001 episode was executed under the joined F3 authority and its admitted "
            "verifier; the reported reward is only that verifier's result for this episode, and "
            "does not claim correctness for unseen tasks or broader model quality."
        ),
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("reward", 0, "successful reward"),
        ("released", False, "cleanup"),
        ("no_orphan", False, "cleanup"),
    ],
)
def test_canonical_report_rejects_false_reward_local_cleanup_and_orphan(
    field: str, value: object, message: str
) -> None:
    payload = _report_payload()
    target = payload["verifier"] if field == "reward" else payload["cleanup"]
    target[field] = value
    with pytest.raises(ValidationError, match=message):
        F3TargetEpisodeReport.model_validate(payload, strict=True)


def test_canonical_report_preserves_all_authority_and_evidence_joins() -> None:
    report = F3TargetEpisodeReport.model_validate(_report_payload(), strict=True)
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    decoded = json.loads(raw)

    assert decoded["scheduler"]["slurm_job_id"] == "265001"
    assert decoded["authorities"]["task_ref"].startswith("cas://task@sha256:")
    assert {item["role"] for item in decoded["images"]} == {"primary", "verifier"}
    assert decoded["resolution"]["effective_plan_digest"].startswith("sha256:")
    assert decoded["lifecycle"]["terminal_request_count"] == 1
    assert decoded["verifier"]["reward"] == 1
    assert decoded["cleanup"] == {
        "lease_root_entries": [],
        "no_orphan": True,
        "released": True,
    }
    assert "broader model quality" in decoded["claim_boundary"]
