from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentic_coder_prototype.compilation.contracts import (
    canonical_json_bytes,
    canonical_json_loads,
)
from pydantic import ValidationError

from breadboard.rl.harness import contracts as c
import breadboard.rl.harness.composition as composition_module
import breadboard.rl.harness.service as service_module
from breadboard.rl.harness.composition import (
    AuthorityBundleV1,
    CompositionRefV1,
    HarnessCompositionManifestV1,
)
from breadboard.rl.harness.evidence import (
    EpisodeEvidenceRepository,
    InMemoryEpisodeLocatorStore,
    V2EvidenceAuthority,
)
from breadboard.rl.harness.runners.base import (
    RunnerResult,
    RunnerTermination,
    RunnerTerminationEvent,
    RunnerTurn,
)
from breadboard.rl.harness.service import (
    BreadBoardV2EpisodeService,
    V2LifecycleDependencies,
)
from breadboard.rl.phase5.f3_composition import (
    F3ProductionCompositionInput,
    build_f3_production_composition,
    sha256_bytes,
)
from breadboard.rl.state.cas import FilesystemCAS, InMemoryCAS
import breadboard.rl.phase5.f6_restart_replay_authoring as f6_authoring_module
from breadboard.rl.phase5.f6_restart_replay_authoring import (
    F6ImmutableFileSource,
    F6RestartReplayAuthoringError,
    F6RestartReplayAuthoringInput,
    build_f6_restart_replay_input,
    read_f6_restart_replay_authoring_input,
)
from scripts.rl_phase5.build_f6_restart_replay_input import main as builder_main
import scripts.rl_phase5.run_f6_restart_replay as f6_runner_module
from scripts.rl_phase5.run_f6_restart_replay import (
    BreadBoardF6Runtime,
    F6RestartReplayInput,
    F6TargetIdentity,
    run_f6_restart_replay,
)
from tests.rl.phase5.test_f3_composition import _composition_spec
from tests.rl.harness.v2_service_fixtures import (
    DeterministicLease,
    DeterministicSession,
    DeterministicVerifier,
    deterministic_clock,
    deterministic_sandbox_plan,
    service_case,
)


@pytest.fixture(autouse=True)
def _freeze_f3_authority_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        composition_module,
        "_SystemUTCClock",
        type(
            "_F6AuthoringClock",
            (),
            {"current": lambda self: datetime(2026, 7, 13, tzinfo=UTC)},
        ),
    )


def _source(path: Path) -> F6ImmutableFileSource:
    return F6ImmutableFileSource(
        path=os.fspath(path.resolve()),
        sha256=sha256_bytes(path.read_bytes()),
    )


def _request(
    production: F3ProductionCompositionInput,
    manifest: HarnessCompositionManifestV1,
    authority: AuthorityBundleV1,
    episode_id: str = "f6-original-episode",
) -> c.ResolveEpisodeRequest:
    selector_ref = manifest.selector_catalog.direct[0]
    receipt_ref = authority.admission_receipt_refs[0]
    receipt = c.AdmissionReceipt.model_validate_json(
        Path(receipt_ref.path).read_bytes(), strict=True
    )
    return c.ResolveEpisodeRequest(
        episode_id=episode_id,
        subject=receipt.subject,
        selector=c.DirectSelectorRef(
            digest=selector_ref.sha256,
            ref=c.ArtifactRef(
                artifact_id=selector_ref.sha256,
                sha256=selector_ref.sha256,
                size_bytes=selector_ref.size_bytes,
                media_type=selector_ref.media_type,
            ),
        ),
        selection_nonce=None,
        task=production.resolution_task,
        policy_binding=receipt.policy_binding_ref,
        episode_overlays=(),
    )


def _authoring_case(
    tmp_path: Path,
) -> tuple[
    F6RestartReplayAuthoringInput,
    Path,
    HarnessCompositionManifestV1,
    AuthorityBundleV1,
]:
    production, _ = _composition_spec(tmp_path)
    build = build_f3_production_composition(
        production, os.fspath((tmp_path / "composition").resolve())
    )
    descriptor_path = Path(build.composition_ref_path)
    descriptor = CompositionRefV1.model_validate_json(
        descriptor_path.read_bytes(), strict=True
    )
    manifest_path = Path(descriptor.manifest_path)
    manifest = HarnessCompositionManifestV1.model_validate_json(
        manifest_path.read_bytes(), strict=True
    )
    authority_path = Path(manifest.authority_bundle_ref.path)
    authority = AuthorityBundleV1.model_validate_json(
        authority_path.read_bytes(), strict=True
    )
    request_path = tmp_path / "original-request.json"
    request_path.write_bytes(
        canonical_json_bytes(
            _request(production, manifest, authority).model_dump(mode="json")
        )
    )
    spec = F6RestartReplayAuthoringInput(
        schema_version="bb.rl.phase5-f6-restart-replay-authoring-input.v1",
        composition_descriptor=_source(descriptor_path),
        composition_manifest=_source(manifest_path),
        authority_bundle=_source(authority_path),
        original_request=_source(request_path),
        target=F6TargetIdentity(
            target_run_id="phase3-target-run",
            slurm_job_id="12345",
            slurm_nodelist="worker-01",
            local_hostname="worker-01",
        ),
        fresh_episode_id="f6-fresh-live-episode",
        task_input={
            "responses_create_params": {
                "model": "model-a",
                "input": "repair the admitted workspace",
            }
        },
        run_context={"campaign": "f6-restart-replay"},
        secret_files={
            handle_id: _source(Path(path))
            for handle_id, path in production.secrets.files.items()
        },
        report_path=os.fspath((tmp_path / "f6-report.json").resolve()),
    )
    return spec, request_path, manifest, authority


def test_builds_closed_canonical_f6_input_with_exact_episode_id_delta(
    tmp_path: Path,
) -> None:
    spec, request_path, manifest, _ = _authoring_case(tmp_path)
    output = tmp_path / "f6-input.json"

    descriptor = build_f6_restart_replay_input(
        spec, os.fspath(output.resolve())
    )
    raw = output.read_bytes()
    target = F6RestartReplayInput.model_validate_json(raw, strict=True)

    assert raw == canonical_json_bytes(target.model_dump(mode="json"))
    assert descriptor.target_input_sha256 == sha256_bytes(raw)
    assert target.production.composition_ref_path == spec.composition_descriptor.path
    assert target.production.composition_descriptor_ref.digest == spec.composition_descriptor.sha256
    assert target.production.composition_manifest_ref.digest == spec.composition_manifest.sha256
    assert target.production.authority_bundle_ref.digest == spec.authority_bundle.sha256
    assert {
        handle_id: (source.path, source.sha256)
        for handle_id, source in target.production.secret_files.items()
    } == {
        handle_id: (source.path, source.sha256)
        for handle_id, source in spec.secret_files.items()
    }
    for source in target.production.secret_files.values():
        observed = os.stat(source.path, follow_symlinks=False)
        assert source.identity.device == observed.st_dev
        assert source.identity.inode == observed.st_ino
        assert source.identity.size_bytes == observed.st_size
        assert source.identity.mtime_ns == str(observed.st_mtime_ns)
        assert source.identity.ctime_ns == str(observed.st_ctime_ns)
        assert source.identity.owner_uid == observed.st_uid
        assert source.identity.mode == 0o400
        assert source.identity.nlink == 1
    assert set(target.production.secret_files) == {
        record.handle_id for record in manifest.secret_handles.records
    }
    original = target.original_request.model_dump(mode="json")
    fresh = target.fresh_live_request.model_dump(mode="json")
    assert original.pop("episode_id") == "f6-original-episode"
    assert fresh.pop("episode_id") == "f6-fresh-live-episode"
    assert original == fresh
    assert target.original_request.task.canonical_digest() == target.immutable_identity.task_contract_digest
    assert target.original_request.selector.digest == target.immutable_identity.selector_digest
    assert descriptor.original_request_sha256 == sha256_bytes(request_path.read_bytes())
    output_stat = output.stat(follow_symlinks=False)
    assert stat.S_IMODE(output_stat.st_mode) == 0o400
    assert descriptor.target_input_identity.inode == output_stat.st_ino
    assert descriptor.target_input_identity.ctime_ns == str(output_stat.st_ctime_ns)
    assert not Path(target.report_path).exists()


def test_builder_cli_reads_only_canonical_authoring_input_and_writes_exclusively(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec, _, _, _ = _authoring_case(tmp_path)
    authoring_path = tmp_path / "authoring.json"
    authoring_path.write_bytes(canonical_json_bytes(spec.model_dump(mode="json")))
    output = tmp_path / "target-input.json"

    assert builder_main(
        ["--input", os.fspath(authoring_path.resolve()), "--output", os.fspath(output.resolve())]
    ) == 0
    stdout = capsys.readouterr().out.encode("utf-8")
    descriptor = canonical_json_loads(stdout.rstrip(b"\n"))
    assert descriptor["target_input_path"] == os.fspath(output.resolve())
    assert F6RestartReplayInput.model_validate_json(output.read_bytes(), strict=True)

    with pytest.raises(F6RestartReplayAuthoringError, match="already exists"):
        build_f6_restart_replay_input(spec, os.fspath(output.resolve()))

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text("{\n}\n", encoding="utf-8")
    with pytest.raises((F6RestartReplayAuthoringError, ValidationError)):
        read_f6_restart_replay_authoring_input(os.fspath(noncanonical.resolve()))


@pytest.mark.parametrize("failure", ["descriptor_hash", "manifest_identity", "request_identity"])
def test_rejects_stale_hash_or_mismatched_f3_identity_before_output(
    tmp_path: Path, failure: str
) -> None:
    spec, request_path, _, _ = _authoring_case(tmp_path)
    value = spec.model_dump(mode="json")
    if failure == "descriptor_hash":
        value["composition_descriptor"]["sha256"] = "sha256:" + "f" * 64
    elif failure == "manifest_identity":
        other = tmp_path / "other-manifest.json"
        other.write_bytes(Path(spec.composition_manifest.path).read_bytes())
        value["composition_manifest"] = _source(other).model_dump(mode="json")
    else:
        request = c.ResolveEpisodeRequest.model_validate_json(
            request_path.read_bytes(), strict=True
        )
        changed = request.model_dump(mode="json")
        changed["selector"]["digest"] = "sha256:" + "e" * 64
        changed["selector"]["ref"]["artifact_id"] = "sha256:" + "e" * 64
        changed["selector"]["ref"]["sha256"] = "sha256:" + "e" * 64
        request_path.write_bytes(canonical_json_bytes(changed))
        value["original_request"] = _source(request_path).model_dump(mode="json")
    changed_spec = F6RestartReplayAuthoringInput.model_validate(value, strict=True)
    output = tmp_path / "rejected.json"

    with pytest.raises(F6RestartReplayAuthoringError):
        build_f6_restart_replay_input(changed_spec, os.fspath(output.resolve()))

    assert not output.exists()


@pytest.mark.parametrize("authority_kind", ["runtime", "daemon", "archive"])
def test_rejects_changed_pinned_installed_file_authority_before_output(
    tmp_path: Path, authority_kind: str
) -> None:
    spec, _, manifest, _ = _authoring_case(tmp_path)
    daemon = manifest.installed.private_docker_daemon
    assert daemon is not None
    if authority_kind == "runtime":
        path = Path(manifest.installed.runtimes[0].executable_path)
    elif authority_kind == "daemon":
        path = Path(daemon.dockerd.path)
    else:
        path = Path(daemon.images[0].archive.path)
    path.chmod(path.stat().st_mode | 0o200)
    path.write_bytes(b"changed-after-f3-authoring")
    output = tmp_path / f"changed-{authority_kind}.json"

    with pytest.raises(F6RestartReplayAuthoringError, match="authority"):
        build_f6_restart_replay_input(spec, os.fspath(output.resolve()))

    assert not output.exists()


def test_rejects_recreated_store_directory_authority_before_output(
    tmp_path: Path,
) -> None:
    spec, _, manifest, _ = _authoring_case(tmp_path)
    store = Path(manifest.stores.workspace.path)
    displaced = tmp_path / "displaced-workspace"
    store.rename(displaced)
    store.mkdir(mode=int(manifest.stores.workspace.mode, 8))
    output = tmp_path / "changed-store.json"

    with pytest.raises(
        F6RestartReplayAuthoringError, match="directory authority"
    ):
        build_f6_restart_replay_input(spec, os.fspath(output.resolve()))

    assert not output.exists()


def test_rejects_digest_correct_but_semantically_mismatched_control_authority(
    tmp_path: Path,
) -> None:
    spec, _, manifest, _ = _authoring_case(tmp_path)
    control_ref = manifest.control_plane.policy_capability_snapshot_ref
    changed_control = canonical_json_bytes([])
    Path(control_ref.path).chmod(
        Path(control_ref.path).stat().st_mode | 0o200
    )
    Path(control_ref.path).write_bytes(changed_control)
    manifest_value = manifest.model_dump(mode="json")
    changed_ref = manifest_value["control_plane"][
        "policy_capability_snapshot_ref"
    ]
    changed_ref["sha256"] = sha256_bytes(changed_control)
    changed_ref["size_bytes"] = len(changed_control)
    changed_manifest = HarnessCompositionManifestV1.model_validate_json(
        canonical_json_bytes(manifest_value), strict=True
    )
    manifest_raw = canonical_json_bytes(changed_manifest.model_dump(mode="json"))
    manifest_path = Path(spec.composition_manifest.path)
    manifest_path.chmod(manifest_path.stat().st_mode | 0o200)
    manifest_path.write_bytes(manifest_raw)

    descriptor_path = Path(spec.composition_descriptor.path)
    descriptor = CompositionRefV1.model_validate_json(
        descriptor_path.read_bytes(), strict=True
    )
    descriptor_value = descriptor.model_dump(mode="json")
    descriptor_value["manifest_sha256"] = sha256_bytes(manifest_raw)
    descriptor_value["manifest_size_bytes"] = len(manifest_raw)
    descriptor_raw = canonical_json_bytes(descriptor_value)
    descriptor_path.chmod(descriptor_path.stat().st_mode | 0o200)
    descriptor_path.write_bytes(descriptor_raw)

    spec_value = spec.model_dump(mode="json")
    spec_value["composition_descriptor"] = _source(descriptor_path).model_dump(
        mode="json"
    )
    spec_value["composition_manifest"] = _source(manifest_path).model_dump(
        mode="json"
    )
    changed_spec = F6RestartReplayAuthoringInput.model_validate(
        spec_value, strict=True
    )
    output = tmp_path / "mismatched-control.json"

    with pytest.raises(
        F6RestartReplayAuthoringError, match="cross-reference mismatch"
    ):
        build_f6_restart_replay_input(
            changed_spec, os.fspath(output.resolve())
        )

    assert not output.exists()


def test_runtime_rejects_post_authoring_secret_replacement_before_loader_or_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, manifest, _ = _authoring_case(tmp_path)
    target_path = tmp_path / "secret-pinned-input.json"
    build_f6_restart_replay_input(spec, os.fspath(target_path.resolve()))
    target = F6RestartReplayInput.model_validate_json(
        target_path.read_bytes(), strict=True
    )
    api_handle = next(
        record.handle_id
        for record in manifest.secret_handles.records
        if record.purpose == "api_bearer"
    )
    secret_path = Path(target.production.secret_files[api_handle].path)
    original_secret = secret_path.read_bytes()
    secret_path.unlink()
    secret_path.write_bytes(original_secret)
    secret_path.chmod(0o400)
    loader_calls: list[tuple[str, dict[str, str]]] = []

    def forbidden_loader(
        composition_ref_path: str, secret_files: dict[str, str]
    ) -> Any:
        loader_calls.append((composition_ref_path, secret_files))
        raise AssertionError("loader must not observe changed secret authority")

    monkeypatch.setattr(
        f6_runner_module, "load_production_composition", forbidden_loader
    )
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", target.target.target_run_id)
    monkeypatch.setenv("SLURM_JOB_ID", target.target.slurm_job_id)
    monkeypatch.setenv("SLURM_JOB_NODELIST", target.target.slurm_nodelist)
    monkeypatch.setenv("SLURMD_NODENAME", target.target.local_hostname)

    with pytest.raises(
        f6_runner_module.F6RestartReplayError,
        match="secret authority mismatch",
    ):
        BreadBoardF6Runtime(target)

    assert loader_calls == []
    assert not Path(target.report_path).exists()


@pytest.mark.parametrize("reference_kind", ["compiled", "receipt"])
def test_rejects_compiled_or_receipt_media_type_drift(
    tmp_path: Path, reference_kind: str
) -> None:
    spec, _, manifest, authority = _authoring_case(tmp_path)
    authority_value = authority.model_dump(mode="json")
    field = (
        "compiled_manifest_refs"
        if reference_kind == "compiled"
        else "admission_receipt_refs"
    )
    authority_value[field][0]["media_type"] = "application/json"
    changed_authority_raw = canonical_json_bytes(authority_value)
    authority_path = Path(spec.authority_bundle.path)
    authority_path.chmod(authority_path.stat().st_mode | 0o200)
    authority_path.write_bytes(changed_authority_raw)

    manifest_value = manifest.model_dump(mode="json")
    manifest_value["authority_bundle_ref"]["sha256"] = sha256_bytes(
        changed_authority_raw
    )
    manifest_value["authority_bundle_ref"]["size_bytes"] = len(
        changed_authority_raw
    )
    manifest_raw = canonical_json_bytes(manifest_value)
    manifest_path = Path(spec.composition_manifest.path)
    manifest_path.chmod(manifest_path.stat().st_mode | 0o200)
    manifest_path.write_bytes(manifest_raw)

    descriptor_path = Path(spec.composition_descriptor.path)
    descriptor = CompositionRefV1.model_validate_json(
        descriptor_path.read_bytes(), strict=True
    )
    descriptor_value = descriptor.model_dump(mode="json")
    descriptor_value["manifest_sha256"] = sha256_bytes(manifest_raw)
    descriptor_value["manifest_size_bytes"] = len(manifest_raw)
    descriptor_path.chmod(descriptor_path.stat().st_mode | 0o200)
    descriptor_path.write_bytes(canonical_json_bytes(descriptor_value))

    spec_value = spec.model_dump(mode="json")
    spec_value["composition_descriptor"] = _source(descriptor_path).model_dump(
        mode="json"
    )
    spec_value["composition_manifest"] = _source(manifest_path).model_dump(
        mode="json"
    )
    spec_value["authority_bundle"] = _source(authority_path).model_dump(
        mode="json"
    )
    changed_spec = F6RestartReplayAuthoringInput.model_validate(
        spec_value, strict=True
    )
    output = tmp_path / f"{reference_kind}-media-type.json"

    with pytest.raises(
        F6RestartReplayAuthoringError, match="media type mismatch"
    ):
        build_f6_restart_replay_input(
            changed_spec, os.fspath(output.resolve())
        )

    assert not output.exists()


@pytest.mark.parametrize("drift", ["metadata", "version"])
def test_rejects_openssl_metadata_or_version_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    executable = tmp_path / "openssl"
    executable.write_bytes(b"bounded-openssl")
    executable.chmod(0o500)
    metadata = executable.stat()
    stdout = b"OpenSSL bounded-test\n"
    item = SimpleNamespace(
        path=os.fspath(executable.resolve()),
        sha256=sha256_bytes(executable.read_bytes()),
        device=metadata.st_dev,
        inode=metadata.st_ino + (1 if drift == "metadata" else 0),
        ctime_ns=str(metadata.st_ctime_ns),
        size_bytes=metadata.st_size,
        owner_uid=metadata.st_uid,
        mode=0o500,
        version_stdout_sha256=sha256_bytes(stdout),
        version=(
            "OpenSSL mismatched"
            if drift == "version"
            else "OpenSSL bounded-test"
        ),
    )
    manifest = SimpleNamespace(
        installed=SimpleNamespace(
            runtimes=(), private_docker_daemon=None
        ),
        host_runtime_authority=None,
        openssl_authority=item,
        stores=SimpleNamespace(model_dump=lambda mode: {}),
    )
    monkeypatch.setattr(
        f6_authoring_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=stdout, stderr=b""
        ),
    )
    pinned_fds: list[int] = []
    try:
        with pytest.raises(
            F6RestartReplayAuthoringError,
            match="OpenSSL (executable|version) authority mismatch",
        ):
            f6_authoring_module._pin_manifest_authorities(
                manifest, pinned_fds
            )
    finally:
        for descriptor in pinned_fds:
            os.close(descriptor)


def test_exclusive_output_race_never_unlinks_foreign_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, _, _ = _authoring_case(tmp_path)
    output = (tmp_path / "race-winner.json").resolve()
    original_open = os.open

    def racing_open(
        path: str, flags: int, mode: int = 0o777, **kwargs: Any
    ) -> int:
        if path == output.name and flags & os.O_EXCL:
            foreign = original_open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
                **kwargs,
            )
            try:
                os.write(foreign, b"foreign-winner")
            finally:
                os.close(foreign)
            raise FileExistsError(path)
        return original_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(f6_authoring_module.os, "open", racing_open)

    with pytest.raises(FileExistsError):
        build_f6_restart_replay_input(spec, os.fspath(output))

    assert output.read_bytes() == b"foreign-winner"


def test_runtime_rejects_post_authoring_secret_mode_drift_before_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, manifest, _ = _authoring_case(tmp_path)
    target_path = tmp_path / "secret-mode-input.json"
    build_f6_restart_replay_input(spec, os.fspath(target_path.resolve()))
    target = F6RestartReplayInput.model_validate_json(
        target_path.read_bytes(), strict=True
    )
    api_handle = next(
        record.handle_id
        for record in manifest.secret_handles.records
        if record.purpose == "api_bearer"
    )
    Path(target.production.secret_files[api_handle].path).chmod(0o600)
    loader_calls: list[object] = []
    monkeypatch.setattr(
        f6_runner_module,
        "load_production_composition",
        lambda *args, **kwargs: loader_calls.append((args, kwargs)),
    )
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", target.target.target_run_id)
    monkeypatch.setenv("SLURM_JOB_ID", target.target.slurm_job_id)
    monkeypatch.setenv("SLURM_JOB_NODELIST", target.target.slurm_nodelist)
    monkeypatch.setenv("SLURMD_NODENAME", target.target.local_hostname)

    with pytest.raises(
        f6_runner_module.F6RestartReplayError,
        match="secret authority mismatch",
    ):
        BreadBoardF6Runtime(target)

    assert loader_calls == []
    assert not Path(target.report_path).exists()




def test_runtime_rejects_post_authoring_secret_timestamp_drift_before_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, _, manifest, _ = _authoring_case(tmp_path)
    target_path = tmp_path / "secret-timestamp-input.json"
    build_f6_restart_replay_input(
        spec,
        os.fspath(target_path.resolve()),
    )
    target = F6RestartReplayInput.model_validate_json(
        target_path.read_bytes(),
        strict=True,
    )
    api_handle = next(
        record.handle_id
        for record in manifest.secret_handles.records
        if record.purpose == "api_bearer"
    )
    secret_path = Path(target.production.secret_files[api_handle].path)
    observed = secret_path.stat(follow_symlinks=False)
    os.utime(
        secret_path,
        ns=(observed.st_atime_ns, observed.st_mtime_ns + 1),
        follow_symlinks=False,
    )
    loader_calls: list[object] = []
    monkeypatch.setattr(
        f6_runner_module,
        "load_production_composition",
        lambda *args, **kwargs: loader_calls.append((args, kwargs)),
    )
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", target.target.target_run_id)
    monkeypatch.setenv("SLURM_JOB_ID", target.target.slurm_job_id)
    monkeypatch.setenv(
        "SLURM_JOB_NODELIST",
        target.target.slurm_nodelist,
    )
    monkeypatch.setenv(
        "SLURMD_NODENAME",
        target.target.local_hostname,
    )

    with pytest.raises(
        f6_runner_module.F6RestartReplayError,
        match="secret authority mismatch",
    ):
        BreadBoardF6Runtime(target)

    assert loader_calls == []
    assert not Path(target.report_path).exists()


def test_rejects_missing_secret_closure_preexisting_report_and_source_output_alias(
    tmp_path: Path,
) -> None:
    spec, _, _, _ = _authoring_case(tmp_path)
    value = spec.model_dump(mode="json")
    value["secret_files"].pop(next(iter(value["secret_files"])))
    missing = F6RestartReplayAuthoringInput.model_validate(value, strict=True)
    with pytest.raises(F6RestartReplayAuthoringError, match="secret handle closure"):
        build_f6_restart_replay_input(
            missing, os.fspath((tmp_path / "missing.json").resolve())
        )

    Path(spec.report_path).write_bytes(b"preexisting")
    with pytest.raises(F6RestartReplayAuthoringError, match="report output already exists"):
        build_f6_restart_replay_input(
            spec, os.fspath((tmp_path / "preexisting-report.json").resolve())
        )
    Path(spec.report_path).unlink()

    with pytest.raises(F6RestartReplayAuthoringError, match="exclusive"):
        build_f6_restart_replay_input(spec, spec.composition_descriptor.path)


class _PerEpisodeDeterministicSandbox:
    def __init__(self, registries: c.RegistrySnapshotSet, calls: list[str]) -> None:
        self.registries = registries
        self.installed_authorities = SimpleNamespace()
        self._calls = calls

    async def reconcile_stale(self) -> tuple[()]:
        self._calls.append("sandbox.reconcile_stale")
        return ()

    async def open(self, request: Any) -> DeterministicLease:
        self._calls.append("sandbox.open")
        return DeterministicLease(self._calls, request.effective_plan_digest)

    async def open_verifier(
        self, lease: DeterministicLease, snapshot: Any
    ) -> DeterministicVerifier:
        self._calls.append("sandbox.open_verifier")
        return DeterministicVerifier(self._calls)

    async def close(self) -> tuple[()]:
        self._calls.append("sandbox.manager.close")
        return ()


def _authority_graph(
    spec: F6RestartReplayAuthoringInput,
    manifest: HarnessCompositionManifestV1,
    authority: AuthorityBundleV1,
) -> tuple[Any, FilesystemCAS]:
    admitted = c.AdmittedSetManifest.model_validate_json(
        Path(manifest.admitted_set_ref.path).read_bytes(), strict=True
    )
    selector = c.DirectSelector.model_validate_json(
        Path(manifest.selector_catalog.direct[0].path).read_bytes(), strict=True
    )
    cas = FilesystemCAS(manifest.stores.cas.path)
    graph = composition_module._build_authority_graph(
        cas=cas,
        policy=authority.admission_policy,
        registries=authority.registries,
        revocations=authority.revocations,
        policy_capabilities=authority.policy_capabilities,
        admitted_set=admitted,
        direct_selectors=(selector,),
        weighted_selectors=(),
        compiled_manifests={
            ref.sha256: Path(ref.path).read_bytes()
            for ref in authority.compiled_manifest_refs
        },
        admission_receipts={
            ref.sha256: Path(ref.path).read_bytes()
            for ref in authority.admission_receipt_refs
        },
        policy_http=authority.policy_http,
        tls_trust=authority.tls_trust,
        tls_ca_pem_by_route={
            trust.route_id: Path(trust.ca_bundle_ref.path).read_bytes()
            for trust in authority.tls_trust
        },
        receipt_key_id=manifest.control_plane.receipt_authenticator.key_id,
        receipt_key=composition_module._validate_secret(
            Path(
                spec.secret_files[
                    manifest.control_plane.receipt_authenticator.secret_handle_id
                ].path
            ).read_bytes(),
            "receipt_signer",
        ),
    )
    return graph, cas


def test_real_f6_runtime_service_seam_restarts_rehydrates_and_runs_fresh_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, manifest, authority = _authoring_case(tmp_path)
    target_path = tmp_path / "real-seam-input.json"
    build_descriptor = build_f6_restart_replay_input(
        spec,
        os.fspath(target_path.resolve()),
    )
    target = F6RestartReplayInput.model_validate_json(
        target_path.read_bytes(), strict=True
    )
    graph, graph_cas = _authority_graph(spec, manifest, authority)
    case = service_case()
    case.policy_client.observation = authority.policy_capabilities[0]
    case.runner.descriptor = manifest.installed.runner_adapters[0]
    case.runner.emit_result_events = True
    repository = EpisodeEvidenceRepository(
        InMemoryCAS(), InMemoryEpisodeLocatorStore()
    )
    evidence_authority = V2EvidenceAuthority(manifest.evidence_bindings)
    services: list[BreadBoardV2EpisodeService] = []

    class _LoadedComposition:
        def __init__(self) -> None:
            sandbox = _PerEpisodeDeterministicSandbox(
                authority.registries, case.calls
            )
            service = BreadBoardV2EpisodeService(
                V2LifecycleDependencies(
                    config_runtime=graph.config_runtime,
                    runner_registry=case.registry,
                    sandbox_runtime=sandbox,
                    policy_client_resolver=case.policy_resolver,
                    evidence_repository=repository,
                    evidence_authority=evidence_authority,
                    clock=deterministic_clock,
                )
            )
            services.append(service)
            self.app = SimpleNamespace(
                state=SimpleNamespace(episode_service=service)
            )
            self.authority_graph = graph
            self.manifest = SimpleNamespace(
                input_manifest_digest=target.production.composition_manifest_ref.digest,
                authority_bundle_digest=target.production.authority_bundle_ref.digest,
            )

        async def close(self) -> None:
            await self.app.state.episode_service.close()

    async def terminal_session_run(
        session: DeterministicSession, request: Any
    ) -> RunnerResult:
        session.calls.append("session.run")
        termination = RunnerTermination.SUBMITTED
        result = RunnerResult(
            episode_id=session.request.episode_id,
            effective_plan_digest=session.request.effective_plan_digest,
            original_request={
                "responses_create_params": dict(request.responses_create_params)
            },
            response={"answer": "deterministic"},
            termination=termination,
            turn_count=1,
            turns=(
                RunnerTurn(
                    1, ({"type": "submit", "value": "deterministic"},)
                ),
            ),
            events=(
                RunnerTerminationEvent(
                    0,
                    session.request.episode_id,
                    session.request.effective_plan_digest,
                    1,
                    termination,
                ),
            ),
        )
        if session.events is not None:
            for event in result.events:
                await session.events.emit(event)
        return result

    monkeypatch.setattr(DeterministicSession, "run", terminal_session_run)


    def exact_preflight(
        request: Any, registries: Any, installed_authorities: Any
    ) -> Any:
        plan = request.effective_plan
        return SimpleNamespace(
            runtime_class=plan.sandbox.runtime_class,
            runtime=SimpleNamespace(
                runtime_id=plan.sandbox.runtime_id,
                runtime_class=plan.sandbox.runtime_class,
                measured_binary_digest=plan.sandbox.runtime_binary_digest,
            ),
            image=SimpleNamespace(image_digest=plan.sandbox.image_digest),
            security_policy=SimpleNamespace(
                policy_digest=plan.sandbox.security_policy_digest
            ),
            network_policy=SimpleNamespace(
                policy_digest=plan.sandbox.network_policy_digest
            ),
            verifier=SimpleNamespace(
                grant=SimpleNamespace(
                    implementation_digest=plan.verifier.implementation_digest
                )
            ),
            materialization_plan=deterministic_sandbox_plan().materialization_plan,
        )

    monkeypatch.setattr(
        service_module,
        "build_sandbox_execution_plan",
        exact_preflight,
    )
    loaded_secret_digests: list[dict[str, str]] = []

    def exact_loader(
        composition_ref_path: str, secret_files: dict[str, str]
    ) -> _LoadedComposition:
        assert composition_ref_path == target.production.composition_ref_path
        loaded_secret_digests.append(
            {
                handle_id: sha256_bytes(Path(path).read_bytes())
                for handle_id, path in secret_files.items()
            }
        )
        return _LoadedComposition()

    monkeypatch.setattr(
        f6_runner_module,
        "load_production_composition",
        exact_loader,
    )
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", target.target.target_run_id)
    monkeypatch.setenv("SLURM_JOB_ID", target.target.slurm_job_id)
    monkeypatch.setenv("SLURM_JOB_NODELIST", target.target.slurm_nodelist)
    monkeypatch.setenv("SLURMD_NODENAME", target.target.local_hostname)

    try:
        report = run_f6_restart_replay(
            os.fspath(target_path.resolve()),
            expected_input_sha256=build_descriptor.target_input_sha256,
            expected_input_identity=build_descriptor.target_input_identity,
        )
    finally:
        graph_cas.close()

    expected_secret_digests = {
        handle_id: source.sha256
        for handle_id, source in target.production.secret_files.items()
    }
    assert loaded_secret_digests == [
        expected_secret_digests,
        expected_secret_digests,
    ]
    assert len(services) == 2
    assert services[0] is not services[1]
    assert report.restart.prior_service_closed is True
    assert report.restart.process_memory_retained is False
    assert report.restart.recovered_from_durable_state is True
    assert report.cached.create_disposition == "cached"
    assert report.cached.run_disposition == "cached"
    assert report.cached_runner_calls == 0
    assert report.fresh_live.create_disposition == "fresh"
    assert report.fresh_live.run_disposition == "fresh"
    assert report.fresh_live_runner_calls == 1
    assert case.calls.count("session.run") == 2
    assert report.original.deterministic == report.cached.deterministic
    assert report.original.deterministic == report.fresh_live.deterministic
    assert report.original.episode_binding == report.cached.episode_binding
    assert report.original.durable == report.cached.durable
    durable = report.original.durable
    assert (
        durable.locator_completed_tombstone_ref_digest
        == durable.completed_tombstone_digest
    )
    assert (
        durable.locator_closed_tombstone_ref_digest
        == durable.closed_tombstone_digest
    )
    assert (
        durable.closed_tombstone_completed_ref_digest
        == durable.completed_tombstone_digest
    )
    assert (
        durable.closed_envelope_completed_ref_digest
        == durable.completed_envelope_digest
    )
    assert report.original.cleanup.active_lease_ids == ()
    assert report.cached.cleanup.active_lease_ids == ()
    assert report.fresh_live.cleanup.active_lease_ids == ()
