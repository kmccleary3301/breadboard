from __future__ import annotations

import io
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import types
import zipfile

import pytest
from scripts.rl_phase5 import build_runtime_capability_payload as capability_builder
from scripts.rl_phase5 import build_transport_smoke_payload as publication_builder

from breadboard.rl.phase5.runtime_capability_payload import (
    COMPONENT,
    FIXED_NONCE_SHA256,
    MANIFEST_MEMBER,
    MANIFEST_SCHEMA,
    REPORT_ID,
    REPORT_SCHEMA,
    canonical_json_bytes,
    construct_runtime_capability_payload,
    sha256_bytes,
)
from breadboard.rl.phase5.transport_admission import (
    TransportAdmissionError,
    open_admitted_transport_packet,
)

_COMMAND_ID = "runtime-capability-r1"
_REQUESTED_TARGET_RUN_ID = "20260717T120000Z-slurm-pending"
_RUNNER_SOURCE_SHA256 = "sha256:" + "1" * 64
_RUNNER_TEST_SHA256 = "sha256:" + "2" * 64
_RUNTIME_SOURCE_SHA256 = "sha256:" + "3" * 64
_RUNTIME_TEST_SHA256 = "sha256:" + "4" * 64
_JOB_ID = "12345"
_FINAL_TARGET_RUN_ID = "20260717T120000Z-slurm-12345"
_PAYLOAD_NAME = "transport-smoke-payload.zip"
_RECEIPT_NAME = "transport-smoke-payload-build.json"
_RECEIPT_SCHEMA = "bb.rl.phase5.runtime-preflight-capability-payload-build.v1"
_CLAIM_BOUNDARY = (
    "local_deterministic_capability_build_and_cooperative_atomic_visibility_only"
)


def _component_input(**overrides: str) -> dict[str, str]:
    value = {
        "command_id": _COMMAND_ID,
        "fixed_nonce_sha256": FIXED_NONCE_SHA256,
        "requested_target_run_id": _REQUESTED_TARGET_RUN_ID,
        "runner_source_sha256": _RUNNER_SOURCE_SHA256,
        "runner_test_sha256": _RUNNER_TEST_SHA256,
        "runtime_source_sha256": _RUNTIME_SOURCE_SHA256,
        "runtime_test_sha256": _RUNTIME_TEST_SHA256,
    }
    value.update(overrides)
    return value


def _receipt(component_input: dict[str, str], payload: bytes, manifest: bytes) -> dict:
    return {
        "admission_binding": "authority_admission_sha256_equals_canonical_receipt_sha256",
        "admission_revalidation_required": True,
        "campaign_admission": False,
        "claim_boundary": _CLAIM_BOUNDARY,
        "command_id": component_input["command_id"],
        "component_identity": {
            "component": COMPONENT,
            "report_id": REPORT_ID,
            "schema_version": REPORT_SCHEMA,
        },
        "component_input": component_input,
        "component_input_sha256": sha256_bytes(canonical_json_bytes(component_input)),
        "deterministic_double_build": True,
        "fixed_nonce_sha256": FIXED_NONCE_SHA256,
        "incomplete_without_receipt": True,
        "passed": True,
        "payload_manifest_member": MANIFEST_MEMBER,
        "payload_manifest_sha256": sha256_bytes(manifest),
        "payload_manifest_size_bytes": len(manifest),
        "payload_path": _PAYLOAD_NAME,
        "payload_sha256": sha256_bytes(payload),
        "payload_size_bytes": len(payload),
        "publication_guarantee": "atomic_visibility_only",
        "publication_state": "complete",
        "requested_target_run_id": component_input["requested_target_run_id"],
        "runner_source_sha256": component_input["runner_source_sha256"],
        "runner_test_sha256": component_input["runner_test_sha256"],
        "same_uid_mutation_exclusion": False,
        "schema_version": _RECEIPT_SCHEMA,
        "target_execution": False,
        "transport_authority": False,
    }


def _build_packet(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    component_input = _component_input()
    payload, manifest = construct_runtime_capability_payload(component_input)
    receipt_raw = canonical_json_bytes(_receipt(component_input, payload, manifest))
    packet = tmp_path / "packet"
    packet.mkdir(mode=0o700)
    (packet / _PAYLOAD_NAME).write_bytes(payload)
    (packet / _PAYLOAD_NAME).chmod(0o444)
    (packet / _RECEIPT_NAME).write_bytes(receipt_raw)
    (packet / _RECEIPT_NAME).chmod(0o444)
    return packet, payload, receipt_raw


def _open(packet: Path, receipt_raw: bytes):
    return open_admitted_transport_packet(
        packet,
        expected_admission_sha256=sha256_bytes(receipt_raw),
        expected_command_id=_COMMAND_ID,
        expected_requested_target_run_id=_REQUESTED_TARGET_RUN_ID,
        expected_runner_source_sha256=_RUNNER_SOURCE_SHA256,
    )


def _replace(path: Path, raw: bytes) -> None:
    path.unlink()
    path.write_bytes(raw)
    path.chmod(0o444)


def test_constructor_is_byte_deterministic_and_manifest_closes_every_member() -> None:
    first, first_manifest = construct_runtime_capability_payload(_component_input())
    second, second_manifest = construct_runtime_capability_payload(_component_input())
    assert first == second
    assert first_manifest == second_manifest

    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == [
            "run.sh",
            "runtime_capability_probe.py",
            MANIFEST_MEMBER,
        ]
        manifest = json.loads(archive.read(MANIFEST_MEMBER))
        assert first_manifest == canonical_json_bytes(manifest)
        assert manifest["schema_version"] == MANIFEST_SCHEMA
        assert manifest["component"] == COMPONENT
        assert manifest["report_id"] == REPORT_ID
        assert manifest["report_schema_version"] == REPORT_SCHEMA
        for member in manifest["members"]:
            info = archive.getinfo(member["path"])
            raw = archive.read(info)
            assert member["sha256"] == sha256_bytes(raw)
            assert member["size_bytes"] == len(raw)
            assert member["mode"] == f"{stat.S_IMODE(info.external_attr >> 16):04o}"


def test_each_runtime_and_runner_digest_changes_payload_identity() -> None:
    fields = (
        "runner_source_sha256",
        "runner_test_sha256",
        "runtime_source_sha256",
        "runtime_test_sha256",
    )
    observations = []
    for index, field in enumerate(fields, start=5):
        payload, manifest = construct_runtime_capability_payload(
            _component_input(**{field: "sha256:" + str(index) * 64})
        )
        observations.append((sha256_bytes(payload), sha256_bytes(manifest)))
    assert len(set(observations)) == len(fields)


def test_exact_capability_tuple_is_admitted_with_retained_payload_descriptor(
    tmp_path: Path,
) -> None:
    packet, payload, receipt_raw = _build_packet(tmp_path)
    with _open(packet, receipt_raw) as admitted:
        assert admitted.payload_sha256 == sha256_bytes(payload)
        assert admitted.payload_size == len(payload)
        assert admitted.receipt["schema_version"] == _RECEIPT_SCHEMA
        assert os.pread(admitted.payload_fd, len(payload), 0) == payload


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "bb.rl.phase5.transport-smoke-payload-build.v1"),
        ("component_identity", {"component": "transport_smoke", "report_id": REPORT_ID, "schema_version": REPORT_SCHEMA}),
        ("claim_boundary", "broader"),
    ],
)
def test_capability_receipt_schema_identity_and_claim_drift_is_rejected(
    tmp_path: Path, field: str, replacement: object
) -> None:
    packet, _payload, receipt_raw = _build_packet(tmp_path)
    receipt = json.loads(receipt_raw)
    receipt[field] = replacement
    mutated = canonical_json_bytes(receipt)
    _replace(packet / _RECEIPT_NAME, mutated)
    with pytest.raises(TransportAdmissionError):
        _open(packet, mutated)


def test_self_consistent_runtime_source_swap_cannot_reuse_deterministic_payload(
    tmp_path: Path,
) -> None:
    packet, _payload, receipt_raw = _build_packet(tmp_path)
    receipt = json.loads(receipt_raw)
    receipt["component_input"]["runtime_source_sha256"] = "sha256:" + "9" * 64
    receipt["component_input_sha256"] = sha256_bytes(
        canonical_json_bytes(receipt["component_input"])
    )
    mutated = canonical_json_bytes(receipt)
    _replace(packet / _RECEIPT_NAME, mutated)
    with pytest.raises(
        TransportAdmissionError,
        match="does not close to the deterministic payload",
    ):
        _open(packet, mutated)


def test_payload_byte_swap_is_rejected_even_with_recomputed_receipt_digest(
    tmp_path: Path,
) -> None:
    packet, payload, receipt_raw = _build_packet(tmp_path)
    mutated_payload = payload[:-1] + bytes([payload[-1] ^ 1])
    _replace(packet / _PAYLOAD_NAME, mutated_payload)
    receipt = json.loads(receipt_raw)
    receipt["payload_sha256"] = sha256_bytes(mutated_payload)
    mutated_receipt = canonical_json_bytes(receipt)
    _replace(packet / _RECEIPT_NAME, mutated_receipt)
    with pytest.raises(
        TransportAdmissionError,
        match="does not close to the deterministic payload",
    ):
        _open(packet, mutated_receipt)


def test_embedded_probe_emits_typed_fail_closed_report_without_required_tools(
    tmp_path: Path,
) -> None:
    payload, _manifest = construct_runtime_capability_payload(_component_input())
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(extracted)
    (extracted / "run.sh").chmod(0o500)
    environment = {
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PHASE3_COMMAND_ID": _COMMAND_ID,
        "PHASE3_PAYLOAD_ZIP_SHA256": sha256_bytes(payload),
        "PHASE3_SLURM_JOB_ID": _JOB_ID,
        "PHASE3_TARGET_RUN_ID": _FINAL_TARGET_RUN_ID,
        "PYTHONHASHSEED": "0",
        "SLURM_JOB_ID": _JOB_ID,
        "SLURM_NNODES": "1",
        "SLURM_NTASKS": "1",
    }
    completed = subprocess.run(
        [str(extracted / "run.sh")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith("PHASE3_COMPONENT_REPORT_JSON=")
    ]
    assert len(lines) == 1
    report = json.loads(lines[0].split("=", 1)[1])
    assert report["component"] == COMPONENT
    assert report["report_id"] == REPORT_ID
    assert report["schema_version"] == REPORT_SCHEMA
    assert report["component_input_digest"] == sha256_bytes(
        canonical_json_bytes(_component_input())
    )
    assert report["authoritative"] is False
    assert report["passed"] is report["capability_ready"]
    assert report["nonclaims"]


def test_atomic_publisher_emits_exact_admissible_tuple_and_refuses_replacement(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "packet"
    component_input = _component_input()
    receipt = capability_builder.build(
        destination=destination,
        command_id=component_input["command_id"],
        requested_target_run_id=component_input["requested_target_run_id"],
        runner_source_sha256=component_input["runner_source_sha256"],
        runner_test_sha256=component_input["runner_test_sha256"],
        runtime_source_sha256=component_input["runtime_source_sha256"],
        runtime_test_sha256=component_input["runtime_test_sha256"],
    )
    payload_raw = (destination / _PAYLOAD_NAME).read_bytes()
    receipt_raw = (destination / _RECEIPT_NAME).read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload_raw)) as archive:
        manifest_raw = archive.read(MANIFEST_MEMBER)
    assert receipt == _receipt(component_input, payload_raw, manifest_raw)
    assert receipt_raw == canonical_json_bytes(receipt)
    assert sorted(path.name for path in destination.iterdir()) == sorted(
        [_PAYLOAD_NAME, _RECEIPT_NAME]
    )
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE((destination / _PAYLOAD_NAME).stat().st_mode) == 0o444
    assert stat.S_IMODE((destination / _RECEIPT_NAME).stat().st_mode) == 0o444
    with _open(destination, receipt_raw) as admitted:
        assert admitted.payload_sha256 == receipt["payload_sha256"]

    with pytest.raises(FileExistsError):
        capability_builder.build(
            destination=destination,
            command_id=component_input["command_id"],
            requested_target_run_id=component_input["requested_target_run_id"],
            runner_source_sha256=component_input["runner_source_sha256"],
            runner_test_sha256=component_input["runner_test_sha256"],
            runtime_source_sha256=component_input["runtime_source_sha256"],
            runtime_test_sha256=component_input["runtime_test_sha256"],
        )
    assert (destination / _PAYLOAD_NAME).read_bytes() == payload_raw
    assert (destination / _RECEIPT_NAME).read_bytes() == receipt_raw


def test_capability_receipt_recovers_uncertain_committed_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "packet"
    component_input = _component_input()
    real_rename = publication_builder._rename_directory_noreplace_at
    response_loss = RuntimeError("rename response lost")

    def rename_then_raise(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        real_rename(parent_fd, staging_leaf, destination_leaf)
        raise response_loss

    monkeypatch.setattr(
        publication_builder,
        "_rename_directory_noreplace_at",
        rename_then_raise,
    )
    with pytest.raises(publication_builder.PublicationRecoveryRequired) as captured:
        capability_builder.build(
            destination=destination,
            command_id=component_input["command_id"],
            requested_target_run_id=component_input["requested_target_run_id"],
            runner_source_sha256=component_input["runner_source_sha256"],
            runner_test_sha256=component_input["runner_test_sha256"],
            runtime_source_sha256=component_input["runtime_source_sha256"],
            runtime_test_sha256=component_input["runtime_test_sha256"],
        )
    recovery = captured.value
    assert recovery.__cause__ is response_loss
    assert recovery.committed is None
    assert recovery.receipt_presence is True
    monkeypatch.setattr(
        publication_builder,
        "_rename_directory_noreplace_at",
        real_rename,
    )

    recovered = publication_builder.recover_publication(recovery)
    receipt_raw = (destination / _RECEIPT_NAME).read_bytes()
    assert recovered == json.loads(receipt_raw)
    assert recovered["schema_version"] == _RECEIPT_SCHEMA
    with _open(destination, receipt_raw) as admitted:
        assert admitted.receipt == recovered


@pytest.mark.parametrize(
    ("version_stdout", "expected_passed", "expected_blocked_reasons"),
    [
        (
            {"bd": "bd version 1.0.5\n", "dolt": "dolt version 2.1.8\n"},
            True,
            [],
        ),
        (
            {"bd": "zero-exit impostor\n", "dolt": "zero-exit impostor\n"},
            False,
            ["bd_version_identity_failed", "dolt_version_identity_failed"],
        ),
    ],
)
def test_readiness_requires_recognizable_descriptor_bound_tool_versions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version_stdout: dict[str, str],
    expected_passed: bool,
    expected_blocked_reasons: list[str],
) -> None:
    payload, _manifest = construct_runtime_capability_payload(_component_input())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        source = archive.read("runtime_capability_probe.py")
    probe = types.ModuleType("runtime_capability_probe_under_test")
    exec(compile(source, "runtime_capability_probe.py", "exec"), probe.__dict__)

    class ProbePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def is_dir(self) -> bool:
            return self.value == "/proc/self/fd"

    def observe(name: str, _argv: list[str], _cwd: object, _env: object) -> dict:
        return {
            "name": name,
            "present": True,
            "execution_path": "retained_proc_self_fd",
            "version_exit_code": 0,
            "version_stdout_utf8": version_stdout[name],
        }

    for key, value in {
        "PHASE3_COMMAND_ID": _COMMAND_ID,
        "PHASE3_PAYLOAD_ZIP_SHA256": sha256_bytes(payload),
        "PHASE3_SLURM_JOB_ID": _JOB_ID,
        "PHASE3_TARGET_RUN_ID": _FINAL_TARGET_RUN_ID,
        "SLURM_JOB_ID": _JOB_ID,
        "SLURM_NNODES": "1",
        "SLURM_NTASKS": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(probe.os.sys, "argv", ["runtime_capability_probe.py"])
    monkeypatch.setattr(probe, "Path", ProbePath)
    monkeypatch.setattr(probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        probe,
        "descriptor_exec_observation",
        lambda _cwd, _env: {"passed": True},
    )
    monkeypatch.setattr(probe, "binary_observation", observe)

    assert probe.main() == 0
    line = capsys.readouterr().out.strip()
    report = json.loads(line.split("=", 1)[1])
    assert report["passed"] is expected_passed
    assert report["capability_ready"] is expected_passed
    assert report["blocked_reasons"] == expected_blocked_reasons


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="requires Linux /proc/self/fd executable descriptors",
)
def test_descriptor_bound_version_probe_limits_flooding_tool_output(
    tmp_path: Path,
) -> None:
    payload, _manifest = construct_runtime_capability_payload(_component_input())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        source = archive.read("runtime_capability_probe.py")
    probe = types.ModuleType("runtime_capability_flood_test")
    exec(compile(source, "runtime_capability_probe.py", "exec"), probe.__dict__)
    tool = tmp_path / "bd"
    tool.write_text("#!/bin/sh\nwhile :; do printf 'flooding-version-output\\n'; done\n")
    tool.chmod(0o500)
    environment = {
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "PATH": str(tmp_path),
        "PYTHONHASHSEED": "0",
    }

    observed = probe.binary_observation("bd", ["--version"], tmp_path, environment)

    assert observed["present"] is True
    assert observed["execution_path"] == "retained_proc_self_fd"
    assert observed["version_exit_code"] != 0
    assert len(observed["version_stdout_utf8"].encode()) <= (
        probe.MAX_VERSION_OUTPUT_BYTES
    )
