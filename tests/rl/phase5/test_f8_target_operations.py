from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from breadboard_engine.compilation.contracts import canonical_json_bytes

from scripts.rl_phase3.target_verl_smoke_train import F8TargetTrainingInput
from scripts.rl_phase5 import finalize_f8_target_input as finalizer
from scripts.rl_phase5.build_f8_target_launch_packet import (
    F8LaunchPacketError,
    F8TargetLaunchPacket,
    build_f8_target_launch_packet,
)
from scripts.rl_phase5.run_f8_grpo_evidence_gate import F8ImmutableJSONRef
from scripts.rl_phase5.run_f8_target_slurm import (
    F8GenericAdmissionUnavailableError,
    F8TargetSlurmError,
    _validate_finalizer_observation,
    _validate_raw_lifecycle_order,
    _safe_extract,
    _validate_transport,
    run_f8_external_target,
)


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: Any) -> F8ImmutableJSONRef:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_json_bytes(value)
    path.write_bytes(raw)
    return F8ImmutableJSONRef(path=str(path.resolve()), digest=_sha(raw))


def _copy_native(source: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(Path(source).resolve(strict=True))
    return destination.absolute()


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o750)
    return path.resolve()


def _entries(root: Path, names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": name,
            "size": (root / name).stat().st_size,
            "digest": _sha((root / name).read_bytes()),
        }
        for name in names
    ]


def _native_apptainer(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    source = path.with_suffix(".c")
    source.write_text(
        "#include <stdio.h>\n#include <string.h>\n"
        "int main(int argc, char **argv) { "
        'if (argc > 1 && strcmp(argv[1], "--version") == 0) '
        '{ puts("apptainer version 1.3.0"); return 0; } '
        'if (argc > 1 && strcmp(argv[1], "inspect") == 0) '
        '{ puts("{}"); return 0; } return 2; }\n',
        encoding="utf-8",
    )
    subprocess.run(
        ("cc", str(source), "-o", str(path)), check=True, capture_output=True
    )
    source.unlink()
    return path.absolute()


def _authority(root: Path) -> tuple[dict[str, str], dict[str, Path]]:
    authority = root / "authority"
    payload = authority / "payload"
    trainer = payload / "verl" / "trainer" / "main_ppo.py"
    reward = payload / "verl" / "trainer" / "ppo" / "reward.py"
    trainer.parent.mkdir(parents=True)
    reward.parent.mkdir(parents=True)
    trainer.write_text("# pinned trainer\n", encoding="utf-8")
    reward.write_text("def load(value): return value\n", encoding="utf-8")
    payload_ref = _write(
        authority / "payload.json",
        {
            "schema_version": "bb.rl.phase5-f8-verl-payload-tree.v1",
            "root": str(payload.resolve()),
            "distribution": "verl",
            "distribution_version": "focused-two-phase-contract",
            "provenance": "fresh operational authority",
            "entries": _entries(
                payload,
                ("verl/trainer/main_ppo.py", "verl/trainer/ppo/reward.py"),
            ),
            "exact_tree": True,
            "entrypoint_relative_path": "verl/trainer/main_ppo.py",
            "reward_loader_relative_path": "verl/trainer/ppo/reward.py",
        },
    )
    checkpoint = authority / "checkpoint"
    checkpoint.mkdir()
    for name, raw in (
        ("weights.bin", b"weights"),
        ("model.bin", b"model"),
        ("tokenizer.json", b"{}"),
    ):
        (checkpoint / name).write_bytes(raw)
    verifier_source = authority / "verifier.py"
    verifier_source.write_text(
        "def compute_score(*args): return 1.0\n", encoding="utf-8"
    )
    verifier_source_ref = F8ImmutableJSONRef(
        path=str(verifier_source.resolve()), digest=_sha(verifier_source.read_bytes())
    )
    apptainer = _native_apptainer(authority / "bin" / "apptainer")
    scontrol = _copy_native("/bin/echo", authority / "bin" / "scontrol")
    controller = _copy_native(sys.executable, authority / "bin" / "python3")
    image = authority / "native-rocm.sif"
    image.write_bytes(b"native-sif")
    rocm = authority / "rocm"
    _script(rocm / "bin" / "rocminfo", "echo rocm\n")
    config = {
        "schema_version": "bb.rl.phase5-f8-config.v2",
        "trainer_entrypoint": "verl.trainer.main_ppo",
        "rollout_name": "vllm",
        "rollout_mode": "sync",
        "rollout_n": 32,
        "train_batch_size": 2,
        "val_batch_size": 1,
        "total_training_steps": 3,
        "n_gpus_per_node": 8,
        "actor_learning_rate": 1e-6,
        "timeout_seconds": 60,
        "terminate_grace_seconds": 2,
        "kill_grace_seconds": 2,
        "reward_num_workers": 4,
        "max_prompt_length": 64,
        "max_response_length": 32,
        "hf_home": str((authority / "hf-home").resolve()),
        "working_directory": str(payload.resolve()),
        "payload_manifest": payload_ref.model_dump(mode="json"),
        "trainer_module_relative_path": "verl/trainer/main_ppo.py",
        "trainer_module_digest": _sha(trainer.read_bytes()),
        "reward_loader_relative_path": "verl/trainer/ppo/reward.py",
        "reward_loader_digest": _sha(reward.read_bytes()),
        "output_parameter_relative_path": "weights.bin",
        "changed_parameter_name": "weight",
    }
    task = {
        "schema_version": "bb.rl.phase5-f8-task.v1",
        "train_rows": [
            {
                "row_id": f"train-{index}",
                "data_source": "math",
                "prompt": [{"role": "user", "content": f"Return {index % 2}"}],
                "ground_truth": str(index % 2),
            }
            for index in range(6)
        ],
        "val_rows": [
            {
                "row_id": "val-1",
                "data_source": "math",
                "prompt": [{"role": "user", "content": "Return 1"}],
                "ground_truth": "1",
            }
        ],
    }
    trees = {
        "model": {
            "schema_version": "bb.rl.phase5-f8-model-tree.v1",
            "root": str(checkpoint.resolve()),
            "entries": _entries(checkpoint, ("model.bin",)),
            "exact_tree": False,
            "format": "transformers",
            "parameter_files": {},
        },
        "tokenizer": {
            "schema_version": "bb.rl.phase5-f8-tokenizer-tree.v1",
            "root": str(checkpoint.resolve()),
            "entries": _entries(checkpoint, ("tokenizer.json",)),
            "exact_tree": False,
            "format": "transformers",
            "parameter_files": {},
        },
        "input_checkpoint": {
            "schema_version": "bb.rl.phase5-f8-checkpoint-tree.v1",
            "root": str(checkpoint.resolve()),
            "entries": _entries(
                checkpoint, ("weights.bin", "model.bin", "tokenizer.json")
            ),
            "exact_tree": True,
            "format": "transformers",
            "parameter_files": {"weight": "weights.bin"},
        },
    }
    refs = {
        "config": _write(authority / "config.json", config),
        "task": _write(authority / "task.json", task),
        **{
            name: _write(authority / f"{name}.json", value)
            for name, value in trees.items()
        },
        "verifier": _write(
            authority / "verifier.json",
            {
                "schema_version": "bb.rl.phase5-f8-verifier.v1",
                "source": verifier_source_ref.model_dump(mode="json"),
                "function_name": "compute_score",
            },
        ),
    }
    refs["image"] = _write(
        authority / "image.json",
        {
            "schema_version": "bb.rl.phase5-f8-image.v2",
            "immutable_image_digest": _sha(image.read_bytes()),
            "image_reference": str(image.resolve()),
            "container_runtime_executable": str(apptainer),
            "container_runtime_digest": _sha(apptainer.read_bytes()),
            "container_python_executable": "/usr/bin/python3",
        },
    )
    refs["preflight"] = _write(
        authority / "preflight-template.json",
        {
            "schema_version": "bb.rl.phase5-f8-preflight.v2",
            "passed": True,
            "container_runtime_executable": str(apptainer),
            "container_runtime_digest": _sha(apptainer.read_bytes()),
            "container_python_executable": "/usr/bin/python3",
            "trainer_module": "verl.trainer.main_ppo",
            "accelerator_mode": "rocm",
            "payload_digest": payload_ref.digest,
            "image_reference": str(image.resolve()),
            "image_digest": _sha(image.read_bytes()),
            "observed_environment": {},
        },
    )
    return (
        {name: ref.path for name, ref in refs.items()},
        {
            "apptainer": apptainer,
            "scontrol": scontrol,
            "controller": controller,
            "image": image,
            "rocm": rocm,
        },
    )


def _packet(root: Path) -> tuple[F8TargetLaunchPacket, Path, Path, dict[str, Path]]:
    refs, binaries = _authority(root)
    key = root / "external-key" / "f8.key"
    key.parent.mkdir()
    key.write_bytes(b"K" * 48)
    key.chmod(0o600)
    packet_root = root / "packet"
    packet = build_f8_target_launch_packet(
        packet_root=str(packet_root.resolve()),
        packet_id="f8-operation-1",
        report_id="f8-report-1",
        requested_target_run_id="20260714T120000Z-f8-slurm-pending",
        command_id="f8-command-1",
        identity_paths=refs,
        authority_key_file=str(key.resolve()),
        authority_key_id="f8-key-1",
        scontrol_executable=str(binaries["scontrol"]),
        expected_scontrol_digest=_sha(binaries["scontrol"].read_bytes()),
        rocm_root=str(binaries["rocm"]),
        controller_python_executable=str(binaries["controller"]),
        expected_controller_python_digest=_sha(binaries["controller"].read_bytes()),
    )
    return packet, packet_root, key, binaries


def _template_from_zip(packet: F8TargetLaunchPacket) -> tuple[dict[str, Any], bytes]:
    with zipfile.ZipFile(packet.payload_zip_ref.path) as archive:
        raw = archive.read("f8-finalizer-template.json")
    return json.loads(raw), raw


def _probe_for(job_id: str, *, mismatch: str = "") -> Any:
    def run(
        command: tuple[str, ...], timeout: int
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout
        if Path(command[0]).name == "scontrol":
            partition = "cpu" if mismatch == "partition" else "gpu"
            raw = (
                f"JobId={job_id} Partition={partition} NumNodes=1 NumTasks=1 "
                "TresPerNode=gres/gpu:8\n"
            ).encode()
            return subprocess.CompletedProcess(command, 0, raw, b"")
        if Path(command[0]).name == "apptainer" and command[1] == "--version":
            return subprocess.CompletedProcess(
                command, 0, b"apptainer version 1.3.0\n", b""
            )
        return subprocess.CompletedProcess(command, 0, b"probe-ok\n", b"")

    return run




def test_builder_emits_blocked_source_closed_f8_payload(
    tmp_path: Path,
) -> None:
    packet, _, key, _ = _packet(tmp_path)
    assert packet.schema_version == "bb.rl.phase5-f8-target-launch-packet.v3"
    assert packet.execution_admission == "blocked_missing_generic_f8_admission"
    assert "phase3_invocation" not in packet.model_dump(mode="json")
    with zipfile.ZipFile(packet.payload_zip_ref.path) as archive:
        names = set(archive.namelist())
        run_sh = archive.read("run.sh").decode()
        assert "f8-input.json" not in names
        assert "f8-finalizer-template.json" in names
        assert "finalize_f8_target_input.py" in "\n".join(names)
        assert "run_phase3_target_command.py" not in run_sh
        assert "finalize_f8_target_input.py" in run_sh
        assert run_sh.index("sha256sum") < run_sh.index("finalize_f8_target_input.py")
        assert packet.controller_python_digest.removeprefix("sha256:") in run_sh
        assert "tar " not in run_sh
        assert not any(archive.read(name) == key.read_bytes() for name in names)
    assert packet.permanent_non_authority is True
    assert packet.promotion_authority is False
    assert packet.scorecard_update_allowed is False


def test_external_validation_rejects_training_report_before_input_seal() -> None:
    with pytest.raises(F8TargetSlurmError, match="order is invalid"):
        _validate_raw_lifecycle_order(
            "F8_REMOTE_WORK_ROOT=/remote/work\n"
            "PHASE3_COMPONENT_REPORT_JSON={}\n"
            "F8_FINALIZED_INPUT_REF_JSON={}\n"
            "F8_FINALIZER_OBSERVATION_REF_JSON={}\n"
            "F8_TARGET_EXPORT_REF_JSON={}\n"
        )


def test_finalizer_seals_actual_job_input_before_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    packet, _, _, _ = _packet(tmp_path)
    template_value, template_raw = _template_from_zip(packet)
    target = tmp_path / "target-work"
    target.mkdir()
    template_path = target / "template.json"
    template_path.write_bytes(template_raw)
    wrapper_calls: list[str] = []

    def wrapper(argv: list[str] | None = None) -> int:
        assert argv is not None
        input_path = Path(argv[1])
        assert input_path.exists()
        spec = F8TargetTrainingInput.model_validate_json(
            input_path.read_bytes(), strict=True
        )
        assert spec.target.job_id == "70001"
        assert spec.slurm_job_id_source == "pinned"
        wrapper_calls.append(input_path.read_text())
        return 0

    import scripts.rl_phase3.run_verl_trainer_update as wrapper_module

    monkeypatch.setattr(finalizer, "_run_probe", _probe_for("70001"))
    monkeypatch.setattr(wrapper_module, "main", wrapper)
    result = finalizer.finalize_f8_target_input(
        template_path=str(template_path.resolve()),
        expected_template_digest=_sha(template_raw),
        output_root=str((target / "finalized").resolve()),
        environment={
            "SLURM_JOB_ID": "70001",
            "SLURM_JOB_PARTITION": "gpu",
            "SLURM_NNODES": "1",
            "SLURM_NTASKS": "1",
            "HOSTILE_TRAINER_OVERRIDE": "ignored",
        },
    )
    assert wrapper_calls
    observation = json.loads(Path(result.observation_ref.path).read_bytes())
    assert observation["input_sealed_before_wrapper"] is True
    assert observation["container_or_training_action_before_seal"] is False
    assert "HOSTILE_TRAINER_OVERRIDE" not in observation["observed_environment"]
    preflight = json.loads(
        Path(
            json.loads(Path(result.finalized_input_ref.path).read_bytes())[
                "identity_artifacts"
            ]["preflight"]["path"]
        ).read_bytes()
    )
    assert preflight["observed_environment"]["SLURM_JOB_ID"] == "70001"
    assert template_value["runner_authority_key_digest"] == packet.authority_key_digest
    export_line = next(
        line
        for line in capfd.readouterr().out.splitlines()
        if line.startswith("F8_TARGET_EXPORT_REF_JSON=")
    )
    export_ref = F8ImmutableJSONRef.model_validate_json(
        export_line.split("=", 1)[1], strict=True
    )
    assert _sha(Path(export_ref.path).read_bytes()) == export_ref.digest
    extracted = tmp_path / "transport-check"
    extracted.mkdir()
    names = _safe_extract(Path(export_ref.path), extracted)
    template = finalizer.F8FinalizerTemplate.model_validate_json(
        template_raw, strict=True
    )
    manifest = _validate_transport(
        extracted=extracted,
        archive_names=names,
        packet=packet,
        template=template,
        job_id="70001",
    )
    assert manifest.target == result.target
    assert "finalized/finalization-result.json" in {
        member.relative_path for member in manifest.members
    }
    finalized = F8TargetTrainingInput.model_validate_json(
        Path(result.finalized_input_ref.path).read_bytes(), strict=True
    )
    finalized_observation = finalizer.F8FinalizerObservation.model_validate_json(
        Path(result.observation_ref.path).read_bytes(), strict=True
    )
    _validate_finalizer_observation(
        observation=finalized_observation,
        finalized=finalized,
        template=template,
        remote_root=str(target.resolve()),
        local_root=extracted,
    )


@pytest.mark.parametrize(
    ("environment", "probe_mismatch", "match"),
    [
        ({"SLURM_JOB_ID": "bad job"}, "", "absent or malformed"),
        (
            {"SLURM_JOB_ID": "70001", "SLURM_JOB_PARTITION": "cpu"},
            "",
            "partition environment",
        ),
        ({"SLURM_JOB_ID": "70001"}, "partition", "scontrol job authority mismatch"),
    ],
)
def test_finalizer_rejects_hostile_env_or_scontrol_before_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    environment: dict[str, str],
    probe_mismatch: str,
    match: str,
) -> None:
    packet, _, _, _ = _packet(tmp_path)
    _, template_raw = _template_from_zip(packet)
    work = tmp_path / "target"
    work.mkdir()
    template_path = work / "template.json"
    template_path.write_bytes(template_raw)
    monkeypatch.setattr(
        finalizer, "_run_probe", _probe_for("70001", mismatch=probe_mismatch)
    )
    with pytest.raises(finalizer.F8TargetFinalizationError, match=match):
        finalizer.finalize_f8_target_input(
            template_path=str(template_path.resolve()),
            expected_template_digest=_sha(template_raw),
            output_root=str((work / "finalized").resolve()),
            environment=environment,
            invoke_wrapper=False,
        )
    assert not (work / "finalized" / "f8-input.json").exists()


@pytest.mark.parametrize("tamper", ("runtime", "sif"))
def test_finalizer_rejects_wrong_runtime_or_sif_inside_allocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tamper: str,
) -> None:
    packet, _, _, binaries = _packet(tmp_path)
    _, template_raw = _template_from_zip(packet)
    template_path = tmp_path / "template.json"
    template_path.write_bytes(template_raw)
    binaries["apptainer" if tamper == "runtime" else "image"].write_bytes(b"tampered")
    monkeypatch.setattr(finalizer, "_run_probe", _probe_for("70001"))
    with pytest.raises(
        finalizer.F8TargetFinalizationError,
        match="native Apptainer/SIF/ROCm authority mismatch",
    ):
        finalizer.finalize_f8_target_input(
            template_path=str(template_path.resolve()),
            expected_template_digest=_sha(template_raw),
            output_root=str((tmp_path / "finalized").resolve()),
            environment={"SLURM_JOB_ID": "70001"},
            invoke_wrapper=False,
        )


def test_finalizer_rejects_runtime_version_drift_and_same_output_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, _, _, _ = _packet(tmp_path)
    _, template_raw = _template_from_zip(packet)
    template_path = tmp_path / "template.json"
    template_path.write_bytes(template_raw)

    def drifted_probe(
        command: tuple[str, ...], timeout: int
    ) -> subprocess.CompletedProcess[bytes]:
        result = _probe_for("70001")(command, timeout)
        if Path(command[0]).name == "apptainer" and command[1] == "--version":
            return subprocess.CompletedProcess(
                command, 0, b"apptainer version 9.9.9\n", b""
            )
        return result

    monkeypatch.setattr(finalizer, "_run_probe", drifted_probe)
    with pytest.raises(
        finalizer.F8TargetFinalizationError,
        match="approved Apptainer version observation mismatch",
    ):
        finalizer.finalize_f8_target_input(
            template_path=str(template_path.resolve()),
            expected_template_digest=_sha(template_raw),
            output_root=str((tmp_path / "version-drift").resolve()),
            environment={"SLURM_JOB_ID": "70001"},
            invoke_wrapper=False,
        )
    monkeypatch.setattr(finalizer, "_run_probe", _probe_for("70001"))
    output_root = tmp_path / "one-shot-work" / "finalized"
    finalizer.finalize_f8_target_input(
        template_path=str(template_path.resolve()),
        expected_template_digest=_sha(template_raw),
        output_root=str(output_root.resolve()),
        environment={"SLURM_JOB_ID": "70001"},
        invoke_wrapper=False,
    )
    with pytest.raises(FileExistsError):
        finalizer.finalize_f8_target_input(
            template_path=str(template_path.resolve()),
            expected_template_digest=_sha(template_raw),
            output_root=str(output_root.resolve()),
            environment={"SLURM_JOB_ID": "70002"},
            invoke_wrapper=False,
        )


def test_transport_rejects_substituted_member_or_incomplete_tar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    packet, _, _, _ = _packet(tmp_path)
    _, template_raw = _template_from_zip(packet)
    template_path = tmp_path / "template.json"
    template_path.write_bytes(template_raw)
    monkeypatch.setattr(finalizer, "_run_probe", _probe_for("70001"))
    finalizer.finalize_f8_target_input(
        template_path=str(template_path.resolve()),
        expected_template_digest=_sha(template_raw),
        output_root=str((tmp_path / "target" / "finalized").resolve()),
        environment={"SLURM_JOB_ID": "70001"},
        invoke_wrapper=False,
    )
    export_line = next(
        line
        for line in capfd.readouterr().out.splitlines()
        if line.startswith("F8_TARGET_EXPORT_REF_JSON=")
    )
    export_ref = F8ImmutableJSONRef.model_validate_json(
        export_line.split("=", 1)[1], strict=True
    )
    extracted = tmp_path / "retrieved"
    extracted.mkdir()
    names = _safe_extract(Path(export_ref.path), extracted)
    template = finalizer.F8FinalizerTemplate.model_validate_json(
        template_raw, strict=True
    )
    victim = extracted / "finalized" / "f8-input.json"
    victim.write_bytes(victim.read_bytes() + b"tamper")
    with pytest.raises(F8TargetSlurmError, match="member digest mismatch"):
        _validate_transport(
            extracted=extracted,
            archive_names=names,
            packet=packet,
            template=template,
            job_id="70001",
        )
    incomplete = tmp_path / "incomplete.tar.gz"
    with tarfile.open(incomplete, "x:gz") as archive:
        archive.add(
            extracted / "transport-manifest.json",
            arcname="transport-manifest.json",
            recursive=False,
        )
    incomplete_root = tmp_path / "incomplete"
    incomplete_root.mkdir()
    incomplete_names = _safe_extract(incomplete, incomplete_root)
    with pytest.raises(F8TargetSlurmError, match="exact archive"):
        _validate_transport(
            extracted=incomplete_root,
            archive_names=incomplete_names,
            packet=packet,
            template=template,
            job_id="70001",
        )


def test_finalizer_rejects_template_or_finalizer_digest_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, _, _, _ = _packet(tmp_path)
    template, raw = _template_from_zip(packet)
    path = tmp_path / "template.json"
    template["report_id"] = "tampered-report"
    path.write_bytes(canonical_json_bytes(template))
    with pytest.raises(
        finalizer.F8TargetFinalizationError, match="template digest mismatch"
    ):
        finalizer.finalize_f8_target_input(
            template_path=str(path.resolve()),
            expected_template_digest=_sha(raw),
            output_root=str((tmp_path / "finalized").resolve()),
            environment={"SLURM_JOB_ID": "70001"},
            invoke_wrapper=False,
        )
    valid_path = tmp_path / "valid-template.json"
    valid_path.write_bytes(raw)
    forged_finalizer = tmp_path / "finalize_f8_target_input.py"
    forged_finalizer.write_text("# substituted finalizer\n", encoding="utf-8")
    monkeypatch.setattr(finalizer, "__file__", str(forged_finalizer))
    with pytest.raises(
        finalizer.F8TargetFinalizationError, match="finalizer source digest mismatch"
    ):
        finalizer.finalize_f8_target_input(
            template_path=str(valid_path.resolve()),
            expected_template_digest=_sha(raw),
            output_root=str((tmp_path / "forged-finalizer").resolve()),
            environment={"SLURM_JOB_ID": "70001"},
            invoke_wrapper=False,
        )


def test_builder_rejects_target_visible_key_or_tampered_sif(tmp_path: Path) -> None:
    refs, binaries = _authority(tmp_path)
    key = Path(json.loads(Path(refs["model"]).read_bytes())["root"]) / "key"
    key.write_bytes(b"A" * 40)
    key.chmod(0o600)
    common = dict(
        packet_root=str((tmp_path / "packet").resolve()),
        packet_id="f8-operation",
        report_id="f8-report",
        requested_target_run_id="20260714T120000Z-f8-pending",
        command_id="f8-command",
        identity_paths=refs,
        authority_key_file=str(key),
        authority_key_id="f8-key",
        scontrol_executable=str(binaries["scontrol"]),
        expected_scontrol_digest=_sha(binaries["scontrol"].read_bytes()),
        rocm_root=str(binaries["rocm"]),
        controller_python_executable=str(binaries["controller"]),
        expected_controller_python_digest=_sha(binaries["controller"].read_bytes()),
    )
    with pytest.raises(F8LaunchPacketError, match="target-visible"):
        build_f8_target_launch_packet(**common)
    external = tmp_path / "external.key"
    external.write_bytes(b"A" * 40)
    external.chmod(0o600)
    common["authority_key_file"] = str(external)
    binaries["image"].write_bytes(b"tampered")
    with pytest.raises(F8LaunchPacketError, match="SIF identity mismatch"):
        build_f8_target_launch_packet(**common)


def test_external_runner_default_fails_closed_before_target_or_output_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, root, key, _ = _packet(tmp_path)
    output = (tmp_path / "runner-output").resolve()

    def reject_subprocess(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected target action: {args!r} {kwargs!r}")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    with pytest.raises(
        F8GenericAdmissionUnavailableError,
        match="no separately admitted generic F8 executable with exact campaign authority",
    ) as raised:
        run_f8_external_target(
            launch_packet_path=str(root / "f8-launch-packet.json"),
            runner_authority_key_file=str(key),
            expected_runner_authority_key_id=packet.authority_key_id,
            expected_runner_authority_key_digest=packet.authority_key_digest,
            output_root=str(output),
            scp_executable=str((tmp_path / "missing-scp").resolve()),
            expected_scp_digest="sha256:" + "0" * 64,
            scp_timeout_seconds=2,
            target_run_timeout_seconds=30,
        )
    assert isinstance(raised.value, ValueError)
    assert "transport-smoke-payload.zip" in str(raised.value)
    assert not output.exists()


def test_external_runner_validates_packet_before_admission_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packet, root, key, _ = _packet(tmp_path)
    payload = Path(packet.payload_zip_ref.path)
    payload.write_bytes(payload.read_bytes() + b"tamper")
    output = (tmp_path / "runner-output").resolve()

    def reject_subprocess(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected target action: {args!r} {kwargs!r}")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    with pytest.raises(F8TargetSlurmError, match="payload zip digest mismatch"):
        run_f8_external_target(
            launch_packet_path=str(root / "f8-launch-packet.json"),
            runner_authority_key_file=str(key),
            expected_runner_authority_key_id=packet.authority_key_id,
            expected_runner_authority_key_digest=packet.authority_key_digest,
            output_root=str(output),
            scp_executable=str((tmp_path / "missing-scp").resolve()),
            expected_scp_digest="sha256:" + "0" * 64,
            scp_timeout_seconds=2,
            target_run_timeout_seconds=30,
        )
    assert not output.exists()
