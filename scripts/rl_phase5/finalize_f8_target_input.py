from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import tarfile
import sys
from pathlib import Path
from typing import Literal

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation.contracts import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from scripts.rl_phase3.target_verl_smoke_train import F8TargetTrainingInput
from scripts.rl_phase5.run_f8_grpo_evidence_gate import (
    F8IdentityArtifactRefs,
    F8ImmutableJSONRef,
    F8ImageArtifact,
    F8PreflightArtifact,
    F8TargetIdentity,
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}")
_ALLOWED_ENV = frozenset(
    {
        "SLURM_JOB_ID",
        "SLURM_JOB_PARTITION",
        "SLURM_NNODES",
        "SLURM_NTASKS",
        "CUDA_VISIBLE_DEVICES",
        "HIP_VISIBLE_DEVICES",
        "ROCR_VISIBLE_DEVICES",
    }
)
_NATIVE_MAGICS = (
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
)


class F8TargetFinalizationError(RuntimeError):
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


class F8FinalizerTemplate(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-finalizer-template.v1"]
    template_id: str
    report_id: str
    requested_target_run_id: str
    command_id: str
    runner_authority_key_id: str
    runner_authority_key_digest: str
    identity_artifacts: F8IdentityArtifactRefs
    expected_ssh_alias: Literal["ZYPHRA_IBM_AMD_1"]
    expected_partition: Literal["gpu"]
    expected_gres: Literal["gpu:8"]
    expected_nodes: Literal[1]
    expected_tasks: Literal[1]
    scontrol_executable: str
    scontrol_digest: str
    rocm_root: str
    runtime_version_output_digest: str
    wrapper_source_digest: str
    target_source_digest: str
    gate_source_digest: str
    finalizer_source_digest: str
    _ids = field_validator(
        "template_id",
        "report_id",
        "command_id",
        "runner_authority_key_id",
    )(_identifier)
    _paths = field_validator("scontrol_executable", "rocm_root")(_absolute)
    _digests = field_validator(
        "runner_authority_key_digest",
        "scontrol_digest",
        "runtime_version_output_digest",
        "wrapper_source_digest",
        "target_source_digest",
        "gate_source_digest",
        "finalizer_source_digest",
    )(_digest)

    @model_validator(mode="after")
    def pending_identity(self) -> "F8FinalizerTemplate":
        if not self.requested_target_run_id.endswith("-pending"):
            raise ValueError("requested target identity must end in -pending")
        return self


class F8FinalizerObservation(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-finalizer-observation.v1"]
    template_ref: F8ImmutableJSONRef
    finalized_input_ref: F8ImmutableJSONRef
    finalized_preflight_ref: F8ImmutableJSONRef
    target: F8TargetIdentity
    scontrol_command: tuple[str, ...]
    scontrol_raw_ref: F8ImmutableJSONRef
    runtime_probe_ref: F8ImmutableJSONRef
    observed_environment: dict[str, str]
    partition: Literal["gpu"]
    gres: Literal["gpu:8"]
    nodes: Literal[1]
    tasks: Literal[1]
    input_sealed_before_wrapper: Literal[True]
    container_or_training_action_before_seal: Literal[False]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]


class F8TransportMember(_ExactModel):
    relative_path: str
    size: int
    digest: str
    _sha_value = field_validator("digest")(_digest)

    @model_validator(mode="after")
    def bounded_member(self) -> "F8TransportMember":
        path = Path(self.relative_path)
        if (
            self.size < 0
            or path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or self.relative_path != path.as_posix()
        ):
            raise ValueError("transport member is not a safe relative file")
        return self


class F8TargetTransportManifest(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-target-transport-manifest.v1"]
    template_ref: F8ImmutableJSONRef
    target: F8TargetIdentity
    report_id: str
    authority_key_id: str
    authority_key_digest: str
    members: tuple[F8TransportMember, ...]
    _ids = field_validator("report_id", "authority_key_id")(_identifier)
    _key_digest = field_validator("authority_key_digest")(_digest)

    @model_validator(mode="after")
    def exact_members(self) -> "F8TargetTransportManifest":
        names = tuple(member.relative_path for member in self.members)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("transport members must be unique and sorted")
        return self


class F8FinalizationResult(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-finalization-result.v1"]
    template_ref: F8ImmutableJSONRef
    finalized_input_ref: F8ImmutableJSONRef
    observation_ref: F8ImmutableJSONRef
    target: F8TargetIdentity
    wrapper_returncode: int
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]


def _canonical(path: Path, model: type[BaseModel]) -> tuple[BaseModel, bytes]:
    source = path.resolve(strict=True)
    raw = source.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F8TargetFinalizationError(f"{source} is not JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise F8TargetFinalizationError(f"{source} is not exact canonical JSON")
    try:
        return model.model_validate_json(raw, strict=True), raw
    except Exception as exc:
        raise F8TargetFinalizationError(f"{source} has the wrong schema") from exc


def _read_ref(ref: F8ImmutableJSONRef, model: type[BaseModel]) -> BaseModel:
    path = Path(ref.path).resolve(strict=True)
    raw = path.read_bytes()
    if _sha(raw) != ref.digest:
        raise F8TargetFinalizationError(f"immutable ref digest mismatch: {ref.path}")
    value, checked = _canonical(path, model)
    if checked != raw:
        raise AssertionError("authority changed during finalization")
    return value


def _write(path: Path, value: BaseModel) -> F8ImmutableJSONRef:
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        if os.write(fd, raw) != len(raw):
            raise F8TargetFinalizationError(f"short write: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)
    return F8ImmutableJSONRef(path=str(path.resolve()), digest=_sha(raw))


def _write_raw(path: Path, raw: bytes) -> F8ImmutableJSONRef:
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        if os.write(fd, raw) != len(raw):
            raise F8TargetFinalizationError(f"short write: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)
    return F8ImmutableJSONRef(path=str(path.resolve()), digest=_sha(raw))


def _package_target_export(
    work_root: Path,
    *,
    template_ref: F8ImmutableJSONRef,
    target: F8TargetIdentity,
    report_id: str,
    authority_key_id: str,
    authority_key_digest: str,
) -> F8ImmutableJSONRef:
    manifest_path = work_root / "transport-manifest.json"
    archive_path = work_root / "f8-export.tar.gz"
    files: list[Path] = []
    for path in work_root.rglob("*"):
        if path in {manifest_path, archive_path}:
            continue
        if path.is_symlink():
            raise F8TargetFinalizationError("target export contains a symbolic link")
        if path.is_file():
            files.append(path)
    members = tuple(
        F8TransportMember(
            relative_path=path.relative_to(work_root).as_posix(),
            size=path.stat().st_size,
            digest=_sha(path.read_bytes()),
        )
        for path in sorted(
            files, key=lambda value: value.relative_to(work_root).as_posix()
        )
    )
    manifest = F8TargetTransportManifest(
        schema_version="bb.rl.phase5-f8-target-transport-manifest.v1",
        template_ref=template_ref,
        target=target,
        report_id=report_id,
        authority_key_id=authority_key_id,
        authority_key_digest=authority_key_digest,
        members=members,
    )
    _write(manifest_path, manifest)
    with tarfile.open(archive_path, "x:gz") as archive:
        for path in files:
            archive.add(
                path, arcname=path.relative_to(work_root).as_posix(), recursive=False
            )
        archive.add(manifest_path, arcname=manifest_path.name, recursive=False)
    return F8ImmutableJSONRef(
        path=str(archive_path.resolve()), digest=_sha(archive_path.read_bytes())
    )


def _parse_scontrol(raw: str, template: F8FinalizerTemplate, job_id: str) -> None:
    lines = tuple(line for line in raw.splitlines() if line.strip())
    if len(lines) != 1:
        raise F8TargetFinalizationError("scontrol did not return one job record")
    fields: dict[str, str] = {}
    for token in shlex.split(lines[0]):
        if "=" not in token:
            continue
        name, value = token.split("=", 1)
        if name in fields:
            raise F8TargetFinalizationError("scontrol repeated a job field")
        fields[name] = value
    gpu = fields.get("TresPerNode", "")
    if gpu.startswith("gres/"):
        gpu = gpu.removeprefix("gres/")
    if (
        fields.get("JobId") != job_id
        or fields.get("Partition") != template.expected_partition
        or fields.get("NumNodes") != str(template.expected_nodes)
        or fields.get("NumTasks") != str(template.expected_tasks)
        or gpu != template.expected_gres
    ):
        raise F8TargetFinalizationError("scontrol job authority mismatch")


def _run_probe(
    command: tuple[str, ...], timeout: int
) -> subprocess.CompletedProcess[bytes]:
    try:
        observed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise F8TargetFinalizationError(
            f"probe failed: {Path(command[0]).name}"
        ) from exc
    if observed.returncode != 0:
        raise F8TargetFinalizationError(f"probe failed: {Path(command[0]).name}")
    return observed


def finalize_f8_target_input(
    *,
    template_path: str,
    expected_template_digest: str,
    output_root: str,
    environment: dict[str, str] | None = None,
    probe_timeout_seconds: int = 30,
    invoke_wrapper: bool = True,
) -> F8FinalizationResult:
    template_source = Path(_absolute(template_path)).resolve(strict=True)
    template_model, template_raw = _canonical(template_source, F8FinalizerTemplate)
    assert isinstance(template_model, F8FinalizerTemplate)
    template = template_model
    if _sha(template_raw) != _digest(expected_template_digest):
        raise F8TargetFinalizationError("finalizer template digest mismatch")
    finalizer_raw = Path(__file__).resolve().read_bytes()
    if _sha(finalizer_raw) != template.finalizer_source_digest:
        raise F8TargetFinalizationError("finalizer source digest mismatch")
    repo_root = Path(__file__).resolve().parents[2]
    source_paths = {
        "wrapper_source_digest": repo_root
        / "scripts/rl_phase3/run_verl_trainer_update.py",
        "target_source_digest": repo_root
        / "scripts/rl_phase3/target_verl_smoke_train.py",
        "gate_source_digest": repo_root
        / "scripts/rl_phase5/run_f8_grpo_evidence_gate.py",
    }
    for field, path in source_paths.items():
        if _sha(path.read_bytes()) != getattr(template, field):
            raise F8TargetFinalizationError(f"approved source mismatch: {path.name}")
    values = dict(os.environ if environment is None else environment)
    observed_environment = {
        name: values[name] for name in sorted(_ALLOWED_ENV) if name in values
    }
    job_id = observed_environment.get("SLURM_JOB_ID", "")
    if _ID_RE.fullmatch(job_id) is None:
        raise F8TargetFinalizationError("SLURM_JOB_ID is absent or malformed")
    if observed_environment.get("SLURM_JOB_PARTITION") not in (
        None,
        template.expected_partition,
    ):
        raise F8TargetFinalizationError("Slurm partition environment mismatch")
    for name, expected in (
        ("SLURM_NNODES", template.expected_nodes),
        ("SLURM_NTASKS", template.expected_tasks),
    ):
        if name in observed_environment and observed_environment[name] != str(expected):
            raise F8TargetFinalizationError(f"{name} mismatch")
    destination = Path(_absolute(output_root))
    destination.mkdir(parents=True, exist_ok=False)
    template_ref = F8ImmutableJSONRef(
        path=str(template_source), digest=_sha(template_raw)
    )
    scontrol_path = Path(template.scontrol_executable)
    scontrol = scontrol_path.resolve(strict=True)
    if (
        scontrol_path.name != "scontrol"
        or not scontrol.is_file()
        or not os.access(scontrol, os.X_OK)
        or _sha(scontrol.read_bytes()) != template.scontrol_digest
    ):
        raise F8TargetFinalizationError("pinned scontrol identity mismatch")
    scontrol_command = (str(scontrol_path), "show", "job", "-o", job_id)
    control = _run_probe(scontrol_command, probe_timeout_seconds)
    control_raw = control.stdout + control.stderr
    _parse_scontrol(control_raw.decode("utf-8", "strict"), template, job_id)
    scontrol_ref = _write_raw(destination / "scontrol.raw", control_raw)
    image = _read_ref(template.identity_artifacts.image, F8ImageArtifact)
    assert isinstance(image, F8ImageArtifact)
    runtime_path = Path(image.container_runtime_executable)
    runtime = runtime_path.resolve(strict=True)
    runtime_raw = runtime.read_bytes()
    image_path = Path(image.image_reference).resolve(strict=True)
    rocminfo = Path(template.rocm_root).resolve(strict=True) / "bin" / "rocminfo"
    if (
        runtime_path.name != "apptainer"
        or not runtime_raw.startswith(_NATIVE_MAGICS)
        or _sha(runtime_raw) != image.container_runtime_digest
        or image_path.suffix.lower() != ".sif"
        or _sha(image_path.read_bytes()) != image.immutable_image_digest
        or not rocminfo.is_file()
        or not os.access(rocminfo, os.X_OK)
    ):
        raise F8TargetFinalizationError("native Apptainer/SIF/ROCm authority mismatch")
    version_command = (str(runtime_path), "--version")
    version_result = _run_probe(version_command, probe_timeout_seconds)
    version_output = version_result.stdout + version_result.stderr
    if (
        _sha(version_output) != template.runtime_version_output_digest
        or b"apptainer" not in version_output.lower()
    ):
        raise F8TargetFinalizationError(
            "approved Apptainer version observation mismatch"
        )
    probe_raw = b""
    for command, result in (
        (version_command, version_result),
        (
            (str(runtime_path), "inspect", str(image_path)),
            _run_probe(
                (str(runtime_path), "inspect", str(image_path)), probe_timeout_seconds
            ),
        ),
        (
            (str(rocminfo), "--version"),
            _run_probe((str(rocminfo), "--version"), probe_timeout_seconds),
        ),
    ):
        probe_raw += (
            canonical_json_bytes(
                {
                    "command": list(command),
                    "returncode": result.returncode,
                    "stdout_sha256": _sha(result.stdout),
                    "stderr_sha256": _sha(result.stderr),
                    "combined_output_sha256": _sha(result.stdout + result.stderr),
                }
            )
            + b"\n"
        )
    probe_ref = _write_raw(destination / "runtime-probes.raw", probe_raw)
    preflight_template = _read_ref(
        template.identity_artifacts.preflight, F8PreflightArtifact
    )
    preflight = preflight_template.model_copy(
        update={
            "observed_environment": {
                name: value
                for name, value in observed_environment.items()
                if name
                in {
                    "SLURM_JOB_ID",
                    "CUDA_VISIBLE_DEVICES",
                    "HIP_VISIBLE_DEVICES",
                    "ROCR_VISIBLE_DEVICES",
                }
            }
        }
    )
    preflight_ref = _write(
        destination / "preflight.json",
        F8PreflightArtifact.model_validate(preflight.model_dump(), strict=True),
    )
    identities = template.identity_artifacts.model_copy(
        update={"preflight": preflight_ref}
    )
    target = F8TargetIdentity(
        target_run_id=template.requested_target_run_id.removesuffix("pending") + job_id,
        command_id=template.command_id,
        job_id=job_id,
    )
    canonical_input = F8TargetTrainingInput(
        schema_version="bb.rl.phase5-f8-verl-grpo-target-input.v4",
        execution_scope="ibm_slurm_apptainer",
        report_id=template.report_id,
        target=target,
        slurm_job_id_source="pinned",
        identity_artifacts=identities,
        runner_authority_key_id=template.runner_authority_key_id,
        runner_authority_key_digest=template.runner_authority_key_digest,
        run_root=str((destination.parent / "run-root").resolve()),
    )
    input_ref = _write(destination / "f8-input.json", canonical_input)
    observation = F8FinalizerObservation(
        schema_version="bb.rl.phase5-f8-finalizer-observation.v1",
        template_ref=template_ref,
        finalized_input_ref=input_ref,
        finalized_preflight_ref=preflight_ref,
        target=target,
        scontrol_command=scontrol_command,
        scontrol_raw_ref=scontrol_ref,
        runtime_probe_ref=probe_ref,
        observed_environment=observed_environment,
        partition="gpu",
        gres="gpu:8",
        nodes=1,
        tasks=1,
        input_sealed_before_wrapper=True,
        container_or_training_action_before_seal=False,
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_update_allowed=False,
    )
    observation_ref = _write(destination / "finalizer-observation.json", observation)
    os.write(
        1,
        b"F8_FINALIZED_INPUT_REF_JSON="
        + canonical_json_bytes(input_ref.model_dump(mode="json"))
        + b"\n",
    )
    os.write(
        1,
        b"F8_FINALIZER_OBSERVATION_REF_JSON="
        + canonical_json_bytes(observation_ref.model_dump(mode="json"))
        + b"\n",
    )
    wrapper_returncode = 0
    if invoke_wrapper:
        from scripts.rl_phase3.run_verl_trainer_update import main as wrapper_main

        wrapper_returncode = wrapper_main(["--f8-input", input_ref.path])
    result = F8FinalizationResult(
        schema_version="bb.rl.phase5-f8-finalization-result.v1",
        template_ref=template_ref,
        finalized_input_ref=input_ref,
        observation_ref=observation_ref,
        target=target,
        wrapper_returncode=wrapper_returncode,
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_update_allowed=False,
    )
    _write(destination / "finalization-result.json", result)
    export_ref = _package_target_export(
        destination.parent,
        template_ref=template_ref,
        target=target,
        report_id=template.report_id,
        authority_key_id=template.runner_authority_key_id,
        authority_key_digest=template.runner_authority_key_digest,
    )
    os.write(
        1,
        b"F8_TARGET_EXPORT_REF_JSON="
        + canonical_json_bytes(export_ref.model_dump(mode="json"))
        + b"\n",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Finalize the F8 input from same-job Slurm and native runtime authority"
    )
    parser.add_argument("--template", required=True)
    parser.add_argument("--expected-template-sha256", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--probe-timeout-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    result = finalize_f8_target_input(
        template_path=args.template,
        expected_template_digest=args.expected_template_sha256,
        output_root=args.output_root,
        probe_timeout_seconds=args.probe_timeout_seconds,
    )
    return result.wrapper_returncode


if __name__ == "__main__":
    raise SystemExit(main())
