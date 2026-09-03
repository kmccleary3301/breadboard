from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation.contracts import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scripts.rl_phase5.finalize_f8_target_input import F8FinalizerTemplate
from scripts.rl_phase5.run_f8_grpo_evidence_gate import (
    F8ConfigArtifact,
    F8IdentityArtifactRefs,
    F8ImageArtifact,
    F8ImmutableJSONRef,
    F8PayloadArtifact,
    F8PreflightArtifact,
    F8TaskArtifact,
    F8TreeArtifact,
    F8VerifierArtifact,
)

_APPROVED_SOURCES: dict[str, tuple[str, str]] = {
    "scripts/rl_phase3/run_verl_trainer_update.py": (
        "sha256:5e3ba4e629d9d5a1e2763cc667346fa8fd417f35496af529c7bd24b10416284b",
        "f8_entrypoint",
    ),
    "scripts/rl_phase3/target_verl_smoke_train.py": (
        "sha256:f7c7d89ba6e74dfc0f0a342001cd9bb158b241f0ddc9f716e22ed9aa64f4de77",
        "f8_target",
    ),
    "scripts/rl_phase3/run_verl_trainer_update_legacy.py": (
        "sha256:2927e522bf882836b82dd6b91e9d82d64833e857d7591968c122b4b932728822",
        "non_f8_legacy",
    ),
    "scripts/rl_phase5/run_f8_grpo_evidence_gate.py": (
        "sha256:8e39b74bf5e670d6e6606187438032e7fab7eec9be2558037dcefa288cb1e8f2",
        "f8_gate_contract",
    ),
}
_SUPPORT_SOURCES = (
    "breadboard_engine/compilation/contracts.py",
    "breadboard_engine/__init__.py",
    "breadboard_engine/compilation/__init__.py",
)
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}")
_NATIVE_MAGICS = (
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
)


class F8LaunchPacketError(RuntimeError):
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


def _relative(value: str) -> str:
    path = Path(value)
    if (
        type(value) is not str
        or not value
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or "\x00" in value
    ):
        raise ValueError("expected a normalized relative path")
    return value


def _identifier(value: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError("expected a bounded identifier")
    return value


class F8LaunchSourceEntry(_ExactModel):
    relative_path: str
    size: int = Field(ge=0)
    digest: str
    role: Literal[
        "f8_entrypoint",
        "f8_target",
        "f8_gate_contract",
        "f8_finalizer",
        "non_f8_legacy",
        "python_support",
    ]
    _path = field_validator("relative_path")(_relative)
    _sha_value = field_validator("digest")(_digest)




class F8TargetLaunchPacket(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f8-target-launch-packet.v3"]
    packet_id: str
    packet_root: str
    payload_zip_ref: F8ImmutableJSONRef
    finalizer_template_digest: str
    finalizer_source_digest: str
    source_entries: tuple[F8LaunchSourceEntry, ...] = Field(min_length=8)
    execution_admission: Literal["blocked_missing_generic_f8_admission"]
    authority_key_id: str
    authority_key_digest: str
    controller_python_executable: str
    controller_python_digest: str
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    _ids = field_validator("packet_id", "authority_key_id")(_identifier)
    _paths = field_validator("packet_root", "controller_python_executable")(_absolute)
    _digests = field_validator(
        "finalizer_template_digest",
        "finalizer_source_digest",
        "authority_key_digest",
        "controller_python_digest",
    )(_digest)

    @model_validator(mode="after")
    def exact_sources(self) -> "F8TargetLaunchPacket":
        paths = tuple(entry.relative_path for entry in self.source_entries)
        expected = {
            *_APPROVED_SOURCES,
            "scripts/rl_phase5/finalize_f8_target_input.py",
            *_SUPPORT_SOURCES,
        }
        if set(paths) != expected or len(paths) != len(expected):
            raise ValueError("launch source entries are not the exact approved closure")
        return self


def _canonical(path: Path, model: type[BaseModel]) -> tuple[BaseModel, bytes]:
    source = path.resolve(strict=True)
    raw = source.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F8LaunchPacketError(f"{source} is not JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise F8LaunchPacketError(f"{source} is not exact canonical JSON")
    try:
        return model.model_validate_json(raw, strict=True), raw
    except Exception as exc:
        raise F8LaunchPacketError(f"{source} has the wrong F8 schema") from exc


def _write(path: Path, value: BaseModel) -> F8ImmutableJSONRef:
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        if os.write(fd, raw) != len(raw):
            raise F8LaunchPacketError(f"short write: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)
    return F8ImmutableJSONRef(path=str(path.resolve()), digest=_sha(raw))


def _identity_authority(
    paths: dict[str, str],
) -> tuple[F8IdentityArtifactRefs, dict[str, BaseModel]]:
    models: dict[str, type[BaseModel]] = {
        "config": F8ConfigArtifact,
        "task": F8TaskArtifact,
        "model": F8TreeArtifact,
        "tokenizer": F8TreeArtifact,
        "input_checkpoint": F8TreeArtifact,
        "verifier": F8VerifierArtifact,
        "image": F8ImageArtifact,
        "preflight": F8PreflightArtifact,
    }
    refs: dict[str, F8ImmutableJSONRef] = {}
    values: dict[str, BaseModel] = {}
    for name, model in models.items():
        path = Path(_absolute(paths[name])).resolve(strict=True)
        value, raw = _canonical(path, model)
        refs[name] = F8ImmutableJSONRef(path=str(path), digest=_sha(raw))
        values[name] = value
    identities = F8IdentityArtifactRefs.model_validate(refs, strict=True)
    config = values["config"]
    image = values["image"]
    preflight = values["preflight"]
    assert isinstance(config, F8ConfigArtifact)
    assert isinstance(image, F8ImageArtifact)
    assert isinstance(preflight, F8PreflightArtifact)
    payload, payload_raw = _canonical(
        Path(config.payload_manifest.path), F8PayloadArtifact
    )
    assert isinstance(payload, F8PayloadArtifact)
    if _sha(payload_raw) != config.payload_manifest.digest:
        raise F8LaunchPacketError("VeRL payload manifest digest mismatch")
    if (
        preflight.container_runtime_executable != image.container_runtime_executable
        or preflight.container_runtime_digest != image.container_runtime_digest
        or preflight.container_python_executable != image.container_python_executable
        or preflight.image_reference != image.image_reference
        or preflight.image_digest != image.immutable_image_digest
        or preflight.payload_digest != config.payload_manifest.digest
    ):
        raise F8LaunchPacketError(
            "preflight template authority does not join input authority"
        )
    return identities, values


def _native_file(path: Path, digest: str, name: str) -> Path:
    source = path.resolve(strict=True)
    raw = source.read_bytes()
    if (
        path.name != name
        or not source.is_file()
        or not os.access(source, os.X_OK)
        or not raw.startswith(_NATIVE_MAGICS)
        or _sha(raw) != digest
    ):
        raise F8LaunchPacketError(f"pinned native {name} identity mismatch")
    return path


def _validate_runtime(
    values: dict[str, BaseModel], rocm_root: Path, timeout: int
) -> str:
    image = values["image"]
    assert isinstance(image, F8ImageArtifact)
    runtime = _native_file(
        Path(image.container_runtime_executable),
        image.container_runtime_digest,
        "apptainer",
    )
    sif = Path(image.image_reference).resolve(strict=True)
    if (
        sif.suffix.lower() != ".sif"
        or _sha(sif.read_bytes()) != image.immutable_image_digest
    ):
        raise F8LaunchPacketError("pinned SIF identity mismatch")
    rocminfo = rocm_root.resolve(strict=True) / "bin" / "rocminfo"
    if not rocminfo.is_file() or not os.access(rocminfo, os.X_OK):
        raise F8LaunchPacketError("pinned ROCm root lacks bin/rocminfo")
    observations: list[subprocess.CompletedProcess[bytes]] = []
    for command in ((str(runtime), "--version"), (str(runtime), "inspect", str(sif))):
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=timeout,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise F8LaunchPacketError("native Apptainer probe failed") from exc
        if result.returncode != 0:
            raise F8LaunchPacketError("native Apptainer probe failed")
        observations.append(result)
    version_output = observations[0].stdout + observations[0].stderr
    if b"apptainer" not in version_output.lower():
        raise F8LaunchPacketError("runtime did not identify as native Apptainer")
    return _sha(version_output)


def _private_key(path: Path, protected_roots: tuple[Path, ...]) -> bytes:
    source = path.resolve(strict=True)
    metadata = source.stat()
    raw = source.read_bytes()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or len(raw) < 32:
        raise F8LaunchPacketError("runner authority key must be private and >=32 bytes")
    if any(source == root or root in source.parents for root in protected_roots):
        raise F8LaunchPacketError("runner authority key is target-visible")
    return raw


def _deterministic_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = str(path.relative_to(source))
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            mode = 0o550 if relative == "run.sh" else 0o440
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, path.read_bytes())


def build_f8_target_launch_packet(
    *,
    packet_root: str,
    packet_id: str,
    report_id: str,
    requested_target_run_id: str,
    command_id: str,
    identity_paths: dict[str, str],
    authority_key_file: str,
    authority_key_id: str,
    scontrol_executable: str,
    expected_scontrol_digest: str,
    rocm_root: str,
    controller_python_executable: str,
    expected_controller_python_digest: str,
    probe_timeout: int = 30,
) -> F8TargetLaunchPacket:
    destination = Path(_absolute(packet_root))
    if destination.exists():
        raise F8LaunchPacketError("launch packet destination already exists")
    identities, values = _identity_authority(identity_paths)
    config = values["config"]
    model = values["model"]
    tokenizer = values["tokenizer"]
    checkpoint = values["input_checkpoint"]
    image = values["image"]
    assert isinstance(config, F8ConfigArtifact)
    assert isinstance(model, F8TreeArtifact)
    assert isinstance(tokenizer, F8TreeArtifact)
    assert isinstance(checkpoint, F8TreeArtifact)
    assert isinstance(image, F8ImageArtifact)
    rocm = Path(_absolute(rocm_root)).resolve(strict=True)
    runtime_version_output_digest = _validate_runtime(values, rocm, probe_timeout)
    scontrol = _native_file(
        Path(_absolute(scontrol_executable)),
        _digest(expected_scontrol_digest),
        "scontrol",
    )
    controller_python = _native_file(
        Path(_absolute(controller_python_executable)),
        _digest(expected_controller_python_digest),
        Path(controller_python_executable).name,
    )
    protected_roots = tuple(
        Path(value).resolve()
        for value in (
            destination,
            config.working_directory,
            config.hf_home,
            model.root,
            tokenizer.root,
            checkpoint.root,
            image.image_reference,
        )
    )
    key = _private_key(Path(_absolute(authority_key_file)), protected_roots)
    repo_root = Path(__file__).resolve().parents[2]
    finalizer_path = repo_root / "scripts/rl_phase5/finalize_f8_target_input.py"
    finalizer_digest = _sha(finalizer_path.read_bytes())
    for relative, (expected, _) in _APPROVED_SOURCES.items():
        if _sha((repo_root / relative).read_bytes()) != expected:
            raise F8LaunchPacketError(f"approved F8 source mismatch: {relative}")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        payload = temporary / "payload"
        source_entries: list[F8LaunchSourceEntry] = []
        sources = (
            *_APPROVED_SOURCES,
            "scripts/rl_phase5/finalize_f8_target_input.py",
            *_SUPPORT_SOURCES,
        )
        for relative in sources:
            source = repo_root / relative
            if not source.is_file():
                raise F8LaunchPacketError(f"source closure file is missing: {relative}")
            raw = source.read_bytes()
            target = payload / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            if relative in _APPROVED_SOURCES:
                role = _APPROVED_SOURCES[relative][1]
            elif relative.endswith("finalize_f8_target_input.py"):
                role = "f8_finalizer"
            else:
                role = "python_support"
            source_entries.append(
                F8LaunchSourceEntry(
                    relative_path=relative,
                    size=len(raw),
                    digest=_sha(raw),
                    role=role,
                )
            )
        template = F8FinalizerTemplate(
            schema_version="bb.rl.phase5-f8-finalizer-template.v1",
            template_id=f"{_identifier(packet_id)}-template",
            report_id=_identifier(report_id),
            requested_target_run_id=requested_target_run_id,
            command_id=_identifier(command_id),
            runner_authority_key_id=_identifier(authority_key_id),
            runner_authority_key_digest=_sha(key),
            identity_artifacts=identities,
            expected_ssh_alias="ZYPHRA_IBM_AMD_1",
            expected_partition="gpu",
            expected_gres="gpu:8",
            expected_nodes=1,
            expected_tasks=1,
            scontrol_executable=str(scontrol),
            scontrol_digest=expected_scontrol_digest,
            rocm_root=str(rocm),
            runtime_version_output_digest=runtime_version_output_digest,
            wrapper_source_digest=_APPROVED_SOURCES[
                "scripts/rl_phase3/run_verl_trainer_update.py"
            ][0],
            target_source_digest=_APPROVED_SOURCES[
                "scripts/rl_phase3/target_verl_smoke_train.py"
            ][0],
            gate_source_digest=_APPROVED_SOURCES[
                "scripts/rl_phase5/run_f8_grpo_evidence_gate.py"
            ][0],
            finalizer_source_digest=finalizer_digest,
        )
        template_path = payload / "f8-finalizer-template.json"
        template_ref = _write(template_path, template)
        run_script = (
            "#!/bin/sh\n"
            "set -u\n"
            "WORK=$(pwd -P)\n"
            "echo F8_REMOTE_WORK_ROOT=$WORK\n"
            f"test -x {shlex.quote(str(controller_python))} || exit 70\n"
            f"test \"$(sha256sum {shlex.quote(str(controller_python))} | cut -d ' ' -f1)\" "
            f'= "{_digest(expected_controller_python_digest).removeprefix("sha256:")}" || exit 71\n'
            f"{shlex.quote(str(controller_python))} scripts/rl_phase5/finalize_f8_target_input.py "
            f'--template "$WORK/f8-finalizer-template.json" '
            f"--expected-template-sha256 {template_ref.digest} "
            '--output-root "$WORK/finalized"\n'
            "rc=$?\n"
            "exit $rc\n"
        )
        run_path = payload / "run.sh"
        run_path.write_text(run_script, encoding="utf-8")
        run_path.chmod(0o550)
        payload_zip = temporary / "f8-payload.zip"
        _deterministic_zip(payload, payload_zip)
        final_root = destination
        final_zip = final_root / "f8-payload.zip"
        zip_ref = F8ImmutableJSONRef(
            path=str(final_zip), digest=_sha(payload_zip.read_bytes())
        )
        packet = F8TargetLaunchPacket(
            schema_version="bb.rl.phase5-f8-target-launch-packet.v3",
            packet_id=_identifier(packet_id),
            packet_root=str(final_root),
            payload_zip_ref=zip_ref,
            finalizer_template_digest=template_ref.digest,
            finalizer_source_digest=finalizer_digest,
            source_entries=tuple(
                sorted(source_entries, key=lambda row: row.relative_path)
            ),
            execution_admission="blocked_missing_generic_f8_admission",
            authority_key_id=_identifier(authority_key_id),
            authority_key_digest=_sha(key),
            controller_python_executable=str(controller_python),
            controller_python_digest=expected_controller_python_digest,
            permanent_non_authority=True,
            promotion_authority=False,
            scorecard_update_allowed=False,
        )
        _write(temporary / "f8-launch-packet.json", packet)
        shutil.rmtree(payload)
        os.rename(temporary, destination)
        return packet
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a source-closed F8 payload blocked pending generic admission"
    )
    parser.add_argument("--packet-root", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--command-id", required=True)
    for name in (
        "config",
        "task",
        "model",
        "tokenizer",
        "input-checkpoint",
        "verifier",
        "image",
        "preflight",
    ):
        parser.add_argument(f"--{name}-ref", required=True)
    parser.add_argument("--runner-authority-key-file", required=True)
    parser.add_argument("--runner-authority-key-id", required=True)
    parser.add_argument("--scontrol-executable", required=True)
    parser.add_argument("--expected-scontrol-sha256", required=True)
    parser.add_argument("--rocm-root", required=True)
    parser.add_argument("--controller-python-executable", required=True)
    parser.add_argument("--expected-controller-python-sha256", required=True)
    parser.add_argument("--probe-timeout", type=int, default=30)
    args = parser.parse_args(argv)
    packet = build_f8_target_launch_packet(
        packet_root=args.packet_root,
        packet_id=args.packet_id,
        report_id=args.report_id,
        requested_target_run_id=args.target_run_id,
        command_id=args.command_id,
        identity_paths={
            "config": args.config_ref,
            "task": args.task_ref,
            "model": args.model_ref,
            "tokenizer": args.tokenizer_ref,
            "input_checkpoint": args.input_checkpoint_ref,
            "verifier": args.verifier_ref,
            "image": args.image_ref,
            "preflight": args.preflight_ref,
        },
        authority_key_file=args.runner_authority_key_file,
        authority_key_id=args.runner_authority_key_id,
        scontrol_executable=args.scontrol_executable,
        expected_scontrol_digest=args.expected_scontrol_sha256,
        rocm_root=args.rocm_root,
        controller_python_executable=args.controller_python_executable,
        expected_controller_python_digest=args.expected_controller_python_sha256,
        probe_timeout=args.probe_timeout,
    )
    os.write(
        1,
        b"F8_LAUNCH_PACKET_JSON="
        + canonical_json_bytes(packet.model_dump(mode="json"))
        + b"\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
