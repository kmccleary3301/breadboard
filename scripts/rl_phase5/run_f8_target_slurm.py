from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, NoReturn

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation.contracts import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, field_validator

from scripts.rl_phase3.target_verl_smoke_train import F8TargetTrainingInput
from scripts.rl_phase5.build_f8_target_launch_packet import F8TargetLaunchPacket
from scripts.rl_phase5.finalize_f8_target_input import (
    F8FinalizerObservation,
    F8FinalizerTemplate,
    F8TargetTransportManifest,
    _parse_scontrol,
)
from scripts.rl_phase5.run_f8_grpo_evidence_gate import (
    F8CheckpointReloadEvidence,
    F8ExpectedEpisodeJoin,
    F8GRPOEvidenceGateInput,
    F8ImageArtifact,
    F8ImmutableJSONRef,
    F8ObservedRuntimeManifest,
    F8OptimizerStepsManifest,
    F8RolloutEvidenceManifest,
    F8RolloutSampleRecord,
    F8TargetRunnerReceipt,
    F8TargetSourceReport,
    F8TerminalLifecycleRecord,
    F8TrainerMetricsManifest,
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}")
_REMOTE_RE = re.compile(r"/[A-Za-z0-9_./-]{1,4096}")


class F8TargetSlurmError(RuntimeError):
    pass


class F8GenericAdmissionUnavailableError(F8TargetSlurmError, ValueError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("expected a lowercase sha256 digest")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not Path(value).is_absolute() or "\x00" in value:
        raise ValueError("expected an absolute path")
    return value


def _identifier(value: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError("expected a bounded identifier")
    return value


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class F8ExternalRunnerResult(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-external-runner-result.v1"]
    packet_ref: F8ImmutableJSONRef
    phase3_manifest_ref: F8ImmutableJSONRef
    phase3_raw_log_ref: F8ImmutableJSONRef
    retrieved_export_ref: F8ImmutableJSONRef
    finalized_input_ref: F8ImmutableJSONRef
    finalizer_observation_ref: F8ImmutableJSONRef
    target_source_report_ref: F8ImmutableJSONRef
    target_runner_receipt_ref: F8ImmutableJSONRef
    gate_input_ref: F8ImmutableJSONRef
    job_id: str
    completed_at: str
    passed: Literal[True]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    _job = field_validator("job_id")(_identifier)


class F8ExternalRunnerFailure(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-external-runner-failure.v1"]
    packet_ref: F8ImmutableJSONRef
    stage: str
    error_class: str
    failed_quarantined: Literal[True]
    completed_at: str
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    _ids = field_validator("stage", "error_class")(_identifier)


def _canonical(path: Path, model: type[BaseModel]) -> tuple[BaseModel, bytes]:
    source = path.resolve(strict=True)
    raw = source.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F8TargetSlurmError(f"{source} is not JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise F8TargetSlurmError(f"{source} is not exact canonical JSON")
    try:
        return model.model_validate_json(raw, strict=True), raw
    except Exception as exc:
        raise F8TargetSlurmError(f"{source} has the wrong F8 schema") from exc


def _write(path: Path, value: BaseModel) -> F8ImmutableJSONRef:
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        if os.write(fd, raw) != len(raw):
            raise F8TargetSlurmError(f"short write: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)
    return F8ImmutableJSONRef(path=str(path.resolve()), digest=_sha(raw))


def _file_ref(path: Path) -> F8ImmutableJSONRef:
    source = path.resolve(strict=True)
    return F8ImmutableJSONRef(path=str(source), digest=_sha(source.read_bytes()))


def _private_key(
    path: Path, packet: F8TargetLaunchPacket, expected_id: str, expected_digest: str
) -> bytes:
    source = path.resolve(strict=True)
    metadata = source.stat()
    raw = source.read_bytes()
    if (
        packet.authority_key_id != _identifier(expected_id)
        or packet.authority_key_digest != _digest(expected_digest)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or len(raw) < 32
        or _sha(raw) != expected_digest
        or source == Path(packet.packet_root).resolve()
        or Path(packet.packet_root).resolve() in source.parents
    ):
        raise F8TargetSlurmError("external HMAC authority is not independently pinned")
    return raw


def _validate_payload(packet: F8TargetLaunchPacket) -> F8FinalizerTemplate:
    archive_path = Path(packet.payload_zip_ref.path).resolve(strict=True)
    raw = archive_path.read_bytes()
    if _sha(raw) != packet.payload_zip_ref.digest:
        raise F8TargetSlurmError("launch payload zip digest mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        names = tuple(info.filename for info in archive.infolist())
        if len(names) != len(set(names)) or any(
            name.startswith("/") or ".." in Path(name).parts for name in names
        ):
            raise F8TargetSlurmError("launch payload zip paths are unsafe")
        required = {
            "run.sh",
            "f8-finalizer-template.json",
            *(entry.relative_path for entry in packet.source_entries),
        }
        if set(names) != required:
            raise F8TargetSlurmError(
                "launch payload zip is not the exact source closure"
            )
        template_raw = archive.read("f8-finalizer-template.json")
        try:
            template = F8FinalizerTemplate.model_validate_json(
                template_raw, strict=True
            )
        except Exception as exc:
            raise F8TargetSlurmError("finalizer template schema mismatch") from exc
        if (
            canonical_json_bytes(json.loads(template_raw)) != template_raw
            or _sha(template_raw) != packet.finalizer_template_digest
            or template.finalizer_source_digest != packet.finalizer_source_digest
            or template.runner_authority_key_id != packet.authority_key_id
            or template.runner_authority_key_digest != packet.authority_key_digest
        ):
            raise F8TargetSlurmError("finalizer template is not bound to launch packet")
        entries = {entry.relative_path: entry for entry in packet.source_entries}
        for relative, entry in entries.items():
            source_raw = archive.read(relative)
            if len(source_raw) != entry.size or _sha(source_raw) != entry.digest:
                raise F8TargetSlurmError(f"payload source changed: {relative}")
    return template


def _exact_executable(path: str, digest: str, name: str) -> Path:
    executable = Path(_absolute(path))
    source = executable.resolve(strict=True)
    if (
        executable.name != name
        or not source.is_file()
        or not os.access(source, os.X_OK)
        or _sha(source.read_bytes()) != _digest(digest)
    ):
        raise F8TargetSlurmError(f"pinned {name} executable mismatch")
    return executable




def _validate_raw_lifecycle_order(raw: str) -> None:
    prefixes = (
        "F8_REMOTE_WORK_ROOT=",
        "F8_FINALIZED_INPUT_REF_JSON=",
        "F8_FINALIZER_OBSERVATION_REF_JSON=",
        "PHASE3_COMPONENT_REPORT_JSON=",
        "F8_TARGET_EXPORT_REF_JSON=",
    )
    lines = raw.splitlines()
    positions: list[int] = []
    for prefix in prefixes:
        matches = tuple(
            index for index, line in enumerate(lines) if line.startswith(prefix)
        )
        if len(matches) != 1:
            raise F8TargetSlurmError(
                f"F8 execution log lacks one {prefix.rstrip('=')}"
            )
        positions.append(matches[0])
    if positions != sorted(positions) or len(positions) != len(set(positions)):
        raise F8TargetSlurmError("target finalization/training/export order is invalid")




def _run_scp(command: tuple[str, ...], timeout: int) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise F8TargetSlurmError("retrieval scp failed") from exc
    if result.returncode != 0:
        raise F8TargetSlurmError("retrieval scp failed")


def _safe_extract(archive_path: Path, destination: Path) -> tuple[str, ...]:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        names = tuple(member.name for member in members)
        if len(names) != len(set(names)):
            raise F8TargetSlurmError("retrieved export contains duplicate paths")
        for member in members:
            path = Path(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or not path.parts
                or ".." in path.parts
                or member.name != path.as_posix()
            ):
                raise F8TargetSlurmError("retrieved export contains unsafe paths")
        archive.extractall(destination, filter="data")
    return tuple(sorted(names))


def _validate_transport(
    *,
    extracted: Path,
    archive_names: tuple[str, ...],
    packet: F8TargetLaunchPacket,
    template: F8FinalizerTemplate,
    job_id: str,
) -> F8TargetTransportManifest:
    manifest_path = extracted / "transport-manifest.json"
    manifest_model, _ = _canonical(manifest_path, F8TargetTransportManifest)
    assert isinstance(manifest_model, F8TargetTransportManifest)
    manifest = manifest_model
    expected_names = tuple(
        sorted(
            (
                "transport-manifest.json",
                *(member.relative_path for member in manifest.members),
            )
        )
    )
    if archive_names != expected_names:
        raise F8TargetSlurmError(
            "target transport manifest does not enumerate the exact archive"
        )
    if (
        manifest.template_ref.digest != packet.finalizer_template_digest
        or manifest.target.job_id != job_id
        or manifest.target.command_id != template.command_id
        or manifest.report_id != template.report_id
        or manifest.authority_key_id != packet.authority_key_id
        or manifest.authority_key_digest != packet.authority_key_digest
    ):
        raise F8TargetSlurmError("target transport authority/job join mismatch")
    for member in manifest.members:
        path = (extracted / member.relative_path).resolve(strict=True)
        if (
            not path.is_relative_to(extracted.resolve())
            or not path.is_file()
            or path.stat().st_size != member.size
            or _sha(path.read_bytes()) != member.digest
        ):
            raise F8TargetSlurmError(
                f"target transport member digest mismatch: {member.relative_path}"
            )
    return manifest


def _mapped_path(remote_path: str, remote_root: str, local_root: Path) -> Path:
    remote = Path(remote_path)
    root = Path(remote_root)
    try:
        relative = remote.relative_to(root)
    except ValueError as exc:
        raise F8TargetSlurmError(
            "target artifact escaped the exported run root"
        ) from exc
    return (local_root / relative).resolve(strict=True)


def _mapped_ref(
    ref: F8ImmutableJSONRef,
    model: type[BaseModel],
    remote_root: str,
    local_root: Path,
) -> BaseModel:
    path = _mapped_path(ref.path, remote_root, local_root)
    raw = path.read_bytes()
    if _sha(raw) != ref.digest:
        raise F8TargetSlurmError(f"retrieved immutable ref digest mismatch: {ref.path}")
    try:
        value = model.model_validate_json(raw, strict=True)
    except Exception as exc:
        raise F8TargetSlurmError(f"retrieved ref schema mismatch: {ref.path}") from exc
    if canonical_json_bytes(json.loads(raw)) != raw:
        raise F8TargetSlurmError(f"retrieved ref is not canonical: {ref.path}")
    return value


def _mapped_raw_ref(
    ref: F8ImmutableJSONRef,
    remote_root: str,
    local_root: Path,
) -> bytes:
    raw = _mapped_path(ref.path, remote_root, local_root).read_bytes()
    if _sha(raw) != ref.digest:
        raise F8TargetSlurmError(f"retrieved immutable ref digest mismatch: {ref.path}")
    return raw


def _validate_finalizer_observation(
    *,
    observation: F8FinalizerObservation,
    finalized: F8TargetTrainingInput,
    template: F8FinalizerTemplate,
    remote_root: str,
    local_root: Path,
) -> None:
    if observation.template_ref.digest != _sha(
        _mapped_path(
            observation.template_ref.path, remote_root, local_root
        ).read_bytes()
    ) or observation.template_ref.digest != _sha(
        canonical_json_bytes(template.model_dump(mode="json"))
    ):
        raise F8TargetSlurmError("finalizer template observation digest mismatch")
    if observation.finalized_preflight_ref != finalized.identity_artifacts.preflight:
        raise F8TargetSlurmError("finalizer preflight/input join mismatch")
    scontrol_raw = _mapped_raw_ref(
        observation.scontrol_raw_ref, remote_root, local_root
    )
    if observation.scontrol_command != (
        template.scontrol_executable,
        "show",
        "job",
        "-o",
        finalized.target.job_id,
    ):
        raise F8TargetSlurmError("finalizer scontrol command identity mismatch")
    _parse_scontrol(
        scontrol_raw.decode("utf-8", "strict"),
        template,
        finalized.target.job_id,
    )
    image_path = Path(template.identity_artifacts.image.path).resolve(strict=True)
    image_raw = image_path.read_bytes()
    if _sha(image_raw) != template.identity_artifacts.image.digest:
        raise F8TargetSlurmError("finalizer image authority ref changed")
    image = F8ImageArtifact.model_validate_json(image_raw, strict=True)
    probe_raw = _mapped_raw_ref(observation.runtime_probe_ref, remote_root, local_root)
    raw_lines = tuple(line for line in probe_raw.splitlines() if line)
    if len(raw_lines) != 3:
        raise F8TargetSlurmError("finalizer runtime probe set is incomplete")
    probes: list[dict[str, Any]] = []
    for raw in raw_lines:
        value = json.loads(raw)
        if canonical_json_bytes(value) != raw or not isinstance(value, dict):
            raise F8TargetSlurmError("finalizer runtime probe is not canonical")
        probes.append(value)
    expected_commands = (
        [image.container_runtime_executable, "--version"],
        [image.container_runtime_executable, "inspect", image.image_reference],
        [str(Path(template.rocm_root) / "bin" / "rocminfo"), "--version"],
    )
    if any(
        probe.get("command") != expected
        or probe.get("returncode") != 0
        or set(probe)
        != {
            "command",
            "returncode",
            "stdout_sha256",
            "stderr_sha256",
            "combined_output_sha256",
        }
        for probe, expected in zip(probes, expected_commands, strict=True)
    ):
        raise F8TargetSlurmError("finalizer runtime probe authority mismatch")
    if probes[0]["combined_output_sha256"] != template.runtime_version_output_digest:
        raise F8TargetSlurmError("finalizer Apptainer version observation mismatch")


def _derive_signed_receipt(
    *,
    packet: F8TargetLaunchPacket,
    source_ref: F8ImmutableJSONRef,
    source: F8TargetSourceReport,
    remote_root: str,
    local_root: Path,
    key: bytes,
    output_root: Path,
) -> tuple[F8ImmutableJSONRef, F8ImmutableJSONRef]:
    runtime = _mapped_ref(
        source.artifacts.observed_runtime,
        F8ObservedRuntimeManifest,
        remote_root,
        local_root,
    )
    rollout = _mapped_ref(
        source.artifacts.rollout_manifest,
        F8RolloutEvidenceManifest,
        remote_root,
        local_root,
    )
    metrics = _mapped_ref(
        source.artifacts.trainer_metrics_manifest,
        F8TrainerMetricsManifest,
        remote_root,
        local_root,
    )
    optimizer = _mapped_ref(
        source.artifacts.optimizer_steps_manifest,
        F8OptimizerStepsManifest,
        remote_root,
        local_root,
    )
    reload = _mapped_ref(
        source.artifacts.checkpoint_reload,
        F8CheckpointReloadEvidence,
        remote_root,
        local_root,
    )
    terminal = _mapped_ref(
        source.artifacts.terminal_lifecycle,
        F8TerminalLifecycleRecord,
        remote_root,
        local_root,
    )
    assert isinstance(runtime, F8ObservedRuntimeManifest)
    assert isinstance(rollout, F8RolloutEvidenceManifest)
    assert isinstance(metrics, F8TrainerMetricsManifest)
    assert isinstance(optimizer, F8OptimizerStepsManifest)
    assert isinstance(reload, F8CheckpointReloadEvidence)
    assert isinstance(terminal, F8TerminalLifecycleRecord)
    if (
        not source.passed
        or source.blocked_reason
        or terminal.terminal_state != "closed"
        or terminal.trainer_returncode != 0
        or not terminal.process_group_reaped
        or terminal.remaining_process_ids
        or terminal.remaining_container_ids
        or terminal.active_lease_ids
        or terminal.cleanup_errors
        or any(
            item.target != source.target
            for item in (runtime, rollout, metrics, optimizer, reload, terminal)
        )
    ):
        raise F8TargetSlurmError(
            "target runtime/training/reload/lifecycle did not close"
        )
    expected: list[F8ExpectedEpisodeJoin] = []
    for ref in rollout.records:
        record = _mapped_ref(ref, F8RolloutSampleRecord, remote_root, local_root)
        assert isinstance(record, F8RolloutSampleRecord)
        if record.target != source.target or record.input_hashes != source.input_hashes:
            raise F8TargetSlurmError("callback record target/input join mismatch")
        expected.append(
            F8ExpectedEpisodeJoin(
                episode_id=record.rollout_carrier.episode_id,
                attempt_id=record.rollout_carrier.attempt_id,
                rollout_carrier_digest=record.rollout_carrier.carrier_digest,
            )
        )
    if len({(row.episode_id, row.attempt_id) for row in expected}) != len(expected):
        raise F8TargetSlurmError("callback episode/attempt joins are duplicated")
    roles = {entry.role: entry.digest for entry in packet.source_entries}
    fields: dict[str, Any] = {
        "schema_version": "bb.rl.phase5-f8-target-runner-receipt.v1",
        "component": "f8_canonical_target_runner",
        "execution_scope": "ibm_slurm_apptainer",
        "target": source.target.model_dump(mode="json"),
        "input_ref": source.input_ref.model_dump(mode="json"),
        "input_hashes": source.input_hashes.model_dump(mode="json"),
        "source_report_ref": source_ref.model_dump(mode="json"),
        "authority_key_id": packet.authority_key_id,
        "authority_key_digest": packet.authority_key_digest,
        "authority_signature": "sha256:" + "0" * 64,
        "slurm_job_id": source.target.job_id,
        "runtime_ref": source.artifacts.observed_runtime.model_dump(mode="json"),
        "wrapper_source_digest": roles["f8_entrypoint"],
        "target_source_digest": roles["f8_target"],
        "gate_source_digest": roles["f8_gate_contract"],
        "reload_harness_manifest_digest": source.artifacts.reload_harness.digest,
        "container_observation_ref": source.artifacts.container_observation.model_dump(
            mode="json"
        ),
        "callback_record_refs": tuple(
            ref.model_dump(mode="json") for ref in rollout.records
        ),
        "callback_disposition_refs": tuple(
            ref.model_dump(mode="json") for ref in rollout.dispositions
        ),
        "trainer_step_refs": tuple(
            ref.model_dump(mode="json") for ref in metrics.records
        ),
        "optimizer_step_refs": tuple(
            ref.model_dump(mode="json") for ref in optimizer.records
        ),
        "checkpoint_reload_ref": source.artifacts.checkpoint_reload.model_dump(
            mode="json"
        ),
        "terminal_lifecycle_ref": source.artifacts.terminal_lifecycle.model_dump(
            mode="json"
        ),
        "trainer_pid": runtime.trainer_pid,
        "trainer_pgid": runtime.trainer_pgid,
        "reload_pid": reload.reload_pid,
        "trainer_returncode": terminal.trainer_returncode,
        "command": tuple(runtime.command),
        "completed_at": source.completed_at,
    }
    unsigned = dict(fields)
    del unsigned["authority_signature"]
    fields["authority_signature"] = (
        "sha256:"
        + hmac.new(key, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
    )
    receipt = F8TargetRunnerReceipt.model_validate(fields, strict=True)
    receipt_ref = _write(output_root / "f8-target-runner-receipt.json", receipt)
    gate = F8GRPOEvidenceGateInput(
        schema_version="bb.rl.phase5-f8-grpo-evidence-gate-input.v3",
        gate_id=f"{packet.packet_id}-gate",
        target=source.target,
        expected_episode_joins=tuple(expected),
        target_source_report=source_ref,
        target_runner_receipt=receipt_ref,
    )
    gate_ref = _write(output_root / "f8-gate-input.json", gate)
    return receipt_ref, gate_ref


def run_f8_external_target(
    *,
    launch_packet_path: str,
    runner_authority_key_file: str,
    expected_runner_authority_key_id: str,
    expected_runner_authority_key_digest: str,
    output_root: str,
    scp_executable: str,
    expected_scp_digest: str,
    scp_timeout_seconds: int,
    target_run_timeout_seconds: int,
) -> NoReturn:
    packet_path = Path(_absolute(launch_packet_path)).resolve(strict=True)
    packet_model, _ = _canonical(packet_path, F8TargetLaunchPacket)
    assert isinstance(packet_model, F8TargetLaunchPacket)
    _validate_payload(packet_model)
    _absolute(runner_authority_key_file)
    _identifier(expected_runner_authority_key_id)
    _digest(expected_runner_authority_key_digest)
    _absolute(output_root)
    _absolute(scp_executable)
    _digest(expected_scp_digest)
    if (
        type(scp_timeout_seconds) is not int
        or scp_timeout_seconds <= 0
        or type(target_run_timeout_seconds) is not int
        or target_run_timeout_seconds <= 0
    ):
        raise ValueError("F8 runner timeouts must be positive integers")
    raise F8GenericAdmissionUnavailableError(
        "F8 target execution is blocked: no separately admitted generic F8 "
        "executable with exact campaign authority exists; transport-smoke "
        "admission accepts only transport-smoke-payload.zip"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an F8 launch packet and fail closed pending generic admission"
    )
    parser.add_argument("--launch-packet", required=True)
    parser.add_argument("--runner-authority-key-file", required=True)
    parser.add_argument("--expected-runner-authority-key-id", required=True)
    parser.add_argument("--expected-runner-authority-key-sha256", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--scp-executable", required=True)
    parser.add_argument("--expected-scp-sha256", required=True)
    parser.add_argument("--scp-timeout-seconds", type=int, default=600)
    parser.add_argument("--target-run-timeout-seconds", type=int, default=86_400)
    args = parser.parse_args(argv)
    run_f8_external_target(
        launch_packet_path=args.launch_packet,
        runner_authority_key_file=args.runner_authority_key_file,
        expected_runner_authority_key_id=args.expected_runner_authority_key_id,
        expected_runner_authority_key_digest=args.expected_runner_authority_key_sha256,
        output_root=args.output_root,
        scp_executable=args.scp_executable,
        expected_scp_digest=args.expected_scp_sha256,
        scp_timeout_seconds=args.scp_timeout_seconds,
        target_run_timeout_seconds=args.target_run_timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
