from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from breadboard.rl.phase5.runtime_capability_payload import (
    COMPONENT as _CAPABILITY_COMPONENT,
    COMPONENT_INPUT_KEYS as _CAPABILITY_COMPONENT_INPUT_KEYS,
    FIXED_NONCE_SHA256 as _CAPABILITY_FIXED_NONCE_SHA256,
    MANIFEST_MEMBER as _CAPABILITY_MANIFEST_MEMBER,
    REPORT_ID as _CAPABILITY_REPORT_ID,
    REPORT_SCHEMA as _CAPABILITY_REPORT_SCHEMA,
    construct_runtime_capability_payload,
)
from breadboard.rl.phase5.transport_smoke_payload import (
    construct_transport_smoke_payload,
)

_PAYLOAD_NAME = "transport-smoke-payload.zip"
_RECEIPT_NAME = "transport-smoke-payload-build.json"
_RECEIPT_SCHEMA = "bb.rl.phase5.transport-smoke-payload-build.v1"
_MANIFEST_MEMBER = "payload_manifest.json"
_REPORT_SCHEMA = "bb.rl.phase3.transport_smoke.v1"
_REPORT_ID = "transport-smoke-fixed-v1"
_COMPONENT = "transport_smoke"
_RECEIPT_MAX_BYTES = 65_536
_CLAIM_BOUNDARY = "local_deterministic_build_and_cooperative_atomic_visibility_only"
_ADMISSION_BINDING = "authority_admission_sha256_equals_canonical_receipt_sha256"
_FIXED_NONCE_SHA256 = (
    "sha256:10c8891ef057c347dca254e9325220c26ba81069b6741ec814de098e81f3c873"
)
_CAPABILITY_RECEIPT_SCHEMA = (
    "bb.rl.phase5.runtime-preflight-capability-payload-build.v1"
)
_CAPABILITY_CLAIM_BOUNDARY = (
    "local_deterministic_capability_build_and_cooperative_atomic_visibility_only"
)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RECEIPT_KEYS = {
    "admission_binding",
    "admission_revalidation_required",
    "campaign_admission",
    "claim_boundary",
    "command_id",
    "component_identity",
    "component_input",
    "component_input_sha256",
    "deterministic_double_build",
    "fixed_nonce_sha256",
    "incomplete_without_receipt",
    "passed",
    "payload_manifest_member",
    "payload_manifest_sha256",
    "payload_manifest_size_bytes",
    "payload_path",
    "payload_sha256",
    "payload_size_bytes",
    "publication_guarantee",
    "publication_state",
    "requested_target_run_id",
    "runner_source_sha256",
    "runner_test_sha256",
    "same_uid_mutation_exclusion",
    "schema_version",
    "target_execution",
    "transport_authority",
}
_COMPONENT_IDENTITY_KEYS = {"component", "report_id", "schema_version"}
_COMPONENT_INPUT_KEYS = {
    "command_id",
    "fixed_nonce_sha256",
    "requested_target_run_id",
    "runner_source_sha256",
    "runner_test_sha256",
}


class TransportAdmissionError(ValueError):
    """The local packet failed consumer-side admission revalidation."""


@dataclass
class AdmittedTransportPacket:
    """A validated transport packet whose payload bytes remain pinned by descriptor."""

    payload_fd: int
    payload_sha256: str
    payload_size: int
    payload_stat: os.stat_result
    receipt_sha256: str
    receipt: dict[str, Any]
    receipt_raw: bytes
    packet_identity: tuple[int, int]
    receipt_identity: tuple[int, int]
    payload_identity: tuple[int, int]
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if not self._closed:
            os.close(self.payload_fd)
            self._closed = True

    def __enter__(self) -> AdmittedTransportPacket:
        if self._closed:
            raise TransportAdmissionError("transport packet payload descriptor is closed")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()



def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()



def _sha256_bytes(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"



def _require_sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TransportAdmissionError(f"transport receipt {field_name} is not a strict SHA-256")
    return value



def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino



def _require_regular_0444(
    metadata: os.stat_result, *, name: str, maximum_size: int | None = None
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise TransportAdmissionError(f"transport packet child is not regular: {name}")
    if stat.S_IMODE(metadata.st_mode) != 0o444:
        raise TransportAdmissionError(f"transport packet child mode is not 0444: {name}")
    if maximum_size is not None and metadata.st_size > maximum_size:
        raise TransportAdmissionError(f"transport packet child exceeds its bound: {name}")



def _read_exact(descriptor: int, size: int, *, name: str) -> bytes:
    raw = bytearray()
    while len(raw) < size:
        chunk = os.read(descriptor, min(65_536, size - len(raw)))
        if not chunk:
            raise TransportAdmissionError(f"transport packet child truncated while reading: {name}")
        raw.extend(chunk)
    if os.read(descriptor, 1):
        raise TransportAdmissionError(f"transport packet child grew while reading: {name}")
    return bytes(raw)


def _read_payload_duplicate(descriptor: int, size: int) -> bytes:
    duplicate = -1
    try:
        duplicate = os.dup(descriptor)
        raw = bytearray()
        while len(raw) < size:
            chunk = os.pread(
                duplicate,
                min(1_048_576, size - len(raw)),
                len(raw),
            )
            if not chunk:
                raise TransportAdmissionError(
                    "transport payload truncated while reconstructing"
                )
            raw.extend(chunk)
        if os.pread(duplicate, 1, size):
            raise TransportAdmissionError(
                "transport payload grew while reconstructing"
            )
        return bytes(raw)
    finally:
        if duplicate >= 0:
            os.close(duplicate)



def _validate_capability_receipt(
    receipt: dict[str, Any],
    *,
    expected_command_id: str,
    expected_requested_target_run_id: str,
    expected_runner_source_sha256: str,
) -> tuple[dict[str, Any], str, int, bytes]:
    component_identity = receipt.get("component_identity")
    if (
        not isinstance(component_identity, dict)
        or set(component_identity) != _COMPONENT_IDENTITY_KEYS
        or component_identity
        != {
            "component": _CAPABILITY_COMPONENT,
            "report_id": _CAPABILITY_REPORT_ID,
            "schema_version": _CAPABILITY_REPORT_SCHEMA,
        }
    ):
        raise TransportAdmissionError(
            "runtime capability receipt component identity mismatch"
        )
    component_input = receipt.get("component_input")
    if (
        not isinstance(component_input, dict)
        or set(component_input) != _CAPABILITY_COMPONENT_INPUT_KEYS
    ):
        raise TransportAdmissionError(
            "runtime capability receipt component input keys mismatch"
        )

    command_id = receipt.get("command_id")
    requested_target_run_id = receipt.get("requested_target_run_id")
    runner_source_sha256 = _require_sha256(
        receipt.get("runner_source_sha256"), field_name="runner_source_sha256"
    )
    runner_test_sha256 = _require_sha256(
        receipt.get("runner_test_sha256"), field_name="runner_test_sha256"
    )
    runtime_source_sha256 = _require_sha256(
        component_input.get("runtime_source_sha256"),
        field_name="runtime_source_sha256",
    )
    runtime_test_sha256 = _require_sha256(
        component_input.get("runtime_test_sha256"),
        field_name="runtime_test_sha256",
    )
    if (
        not isinstance(command_id, str)
        or _SAFE_IDENTIFIER.fullmatch(command_id) is None
        or command_id != expected_command_id
    ):
        raise TransportAdmissionError("runtime capability receipt command_id mismatch")
    if (
        not isinstance(requested_target_run_id, str)
        or _SAFE_IDENTIFIER.fullmatch(requested_target_run_id) is None
        or not requested_target_run_id.endswith("-slurm-pending")
        or requested_target_run_id != expected_requested_target_run_id
    ):
        raise TransportAdmissionError(
            "runtime capability receipt requested_target_run_id mismatch"
        )
    if runner_source_sha256 != _require_sha256(
        expected_runner_source_sha256, field_name="expected runner_source_sha256"
    ):
        raise TransportAdmissionError(
            "runtime capability receipt runner source digest mismatch"
        )
    if component_input != {
        "command_id": command_id,
        "fixed_nonce_sha256": _CAPABILITY_FIXED_NONCE_SHA256,
        "requested_target_run_id": requested_target_run_id,
        "runner_source_sha256": runner_source_sha256,
        "runner_test_sha256": runner_test_sha256,
        "runtime_source_sha256": runtime_source_sha256,
        "runtime_test_sha256": runtime_test_sha256,
    }:
        raise TransportAdmissionError(
            "runtime capability receipt component input mismatch"
        )
    if receipt.get("component_input_sha256") != _sha256_bytes(
        _canonical_json_bytes(component_input)
    ):
        raise TransportAdmissionError(
            "runtime capability receipt component input digest mismatch"
        )

    exact_claims = {
        "admission_binding": _ADMISSION_BINDING,
        "admission_revalidation_required": True,
        "campaign_admission": False,
        "claim_boundary": _CAPABILITY_CLAIM_BOUNDARY,
        "deterministic_double_build": True,
        "fixed_nonce_sha256": _CAPABILITY_FIXED_NONCE_SHA256,
        "incomplete_without_receipt": True,
        "passed": True,
        "payload_manifest_member": _CAPABILITY_MANIFEST_MEMBER,
        "payload_path": _PAYLOAD_NAME,
        "publication_guarantee": "atomic_visibility_only",
        "publication_state": "complete",
        "same_uid_mutation_exclusion": False,
        "schema_version": _CAPABILITY_RECEIPT_SCHEMA,
        "target_execution": False,
        "transport_authority": False,
    }
    if any(receipt.get(key) != value for key, value in exact_claims.items()):
        raise TransportAdmissionError(
            "runtime capability receipt claim boundary mismatch"
        )

    payload_sha256 = _require_sha256(
        receipt.get("payload_sha256"), field_name="payload_sha256"
    )
    manifest_sha256 = _require_sha256(
        receipt.get("payload_manifest_sha256"),
        field_name="payload_manifest_sha256",
    )
    payload_size = receipt.get("payload_size_bytes")
    manifest_size = receipt.get("payload_manifest_size_bytes")
    if type(payload_size) is not int or payload_size < 1:
        raise TransportAdmissionError(
            "runtime capability receipt payload_size_bytes is invalid"
        )
    if type(manifest_size) is not int or manifest_size < 1:
        raise TransportAdmissionError(
            "runtime capability receipt payload_manifest_size_bytes is invalid"
        )
    try:
        expected_payload, expected_manifest = construct_runtime_capability_payload(
            component_input
        )
    except ValueError as exc:
        raise TransportAdmissionError(
            "runtime capability component input cannot reconstruct the reviewed payload"
        ) from exc
    if (
        payload_size != len(expected_payload)
        or payload_sha256 != _sha256_bytes(expected_payload)
        or manifest_size != len(expected_manifest)
        or manifest_sha256 != _sha256_bytes(expected_manifest)
    ):
        raise TransportAdmissionError(
            "runtime capability receipt does not close to the deterministic payload"
        )
    return receipt, payload_sha256, payload_size, expected_payload


def _validate_receipt(
    raw: bytes,
    *,
    expected_admission_sha256: str,
    expected_command_id: str,
    expected_requested_target_run_id: str,
    expected_runner_source_sha256: str,
) -> tuple[dict[str, Any], str, int, bytes]:
    receipt_sha256 = _sha256_bytes(raw)
    if receipt_sha256 != _require_sha256(
        expected_admission_sha256, field_name="authority admission_sha256"
    ):
        raise TransportAdmissionError("transport receipt digest does not match authority admission")
    try:
        receipt = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportAdmissionError("transport receipt is not valid UTF-8 JSON") from exc
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
        raise TransportAdmissionError("transport receipt keys mismatch")
    if raw != _canonical_json_bytes(receipt):
        raise TransportAdmissionError("transport receipt is not canonical JSON")
    if receipt.get("schema_version") == _CAPABILITY_RECEIPT_SCHEMA:
        return _validate_capability_receipt(
            receipt,
            expected_command_id=expected_command_id,
            expected_requested_target_run_id=expected_requested_target_run_id,
            expected_runner_source_sha256=expected_runner_source_sha256,
        )

    component_identity = receipt.get("component_identity")
    if (
        not isinstance(component_identity, dict)
        or set(component_identity) != _COMPONENT_IDENTITY_KEYS
        or component_identity
        != {
            "component": _COMPONENT,
            "report_id": _REPORT_ID,
            "schema_version": _REPORT_SCHEMA,
        }
    ):
        raise TransportAdmissionError("transport receipt component identity mismatch")
    component_input = receipt.get("component_input")
    if not isinstance(component_input, dict) or set(component_input) != _COMPONENT_INPUT_KEYS:
        raise TransportAdmissionError("transport receipt component input keys mismatch")

    command_id = receipt.get("command_id")
    requested_target_run_id = receipt.get("requested_target_run_id")
    runner_source_sha256 = _require_sha256(
        receipt.get("runner_source_sha256"), field_name="runner_source_sha256"
    )
    runner_test_sha256 = _require_sha256(
        receipt.get("runner_test_sha256"), field_name="runner_test_sha256"
    )
    if (
        not isinstance(command_id, str)
        or _SAFE_IDENTIFIER.fullmatch(command_id) is None
        or command_id != expected_command_id
    ):
        raise TransportAdmissionError("transport receipt command_id mismatch")
    if (
        not isinstance(requested_target_run_id, str)
        or _SAFE_IDENTIFIER.fullmatch(requested_target_run_id) is None
        or not requested_target_run_id.endswith("-slurm-pending")
        or requested_target_run_id != expected_requested_target_run_id
    ):
        raise TransportAdmissionError("transport receipt requested_target_run_id mismatch")
    if runner_source_sha256 != _require_sha256(
        expected_runner_source_sha256, field_name="expected runner_source_sha256"
    ):
        raise TransportAdmissionError("transport receipt runner source digest mismatch")
    if component_input != {
        "command_id": command_id,
        "fixed_nonce_sha256": _FIXED_NONCE_SHA256,
        "requested_target_run_id": requested_target_run_id,
        "runner_source_sha256": runner_source_sha256,
        "runner_test_sha256": runner_test_sha256,
    }:
        raise TransportAdmissionError("transport receipt component input mismatch")
    if receipt.get("component_input_sha256") != _sha256_bytes(
        _canonical_json_bytes(component_input)
    ):
        raise TransportAdmissionError("transport receipt component input digest mismatch")

    exact_claims = {
        "admission_binding": _ADMISSION_BINDING,
        "admission_revalidation_required": True,
        "campaign_admission": False,
        "claim_boundary": _CLAIM_BOUNDARY,
        "deterministic_double_build": True,
        "fixed_nonce_sha256": _FIXED_NONCE_SHA256,
        "incomplete_without_receipt": True,
        "passed": True,
        "payload_manifest_member": _MANIFEST_MEMBER,
        "payload_path": _PAYLOAD_NAME,
        "publication_guarantee": "atomic_visibility_only",
        "publication_state": "complete",
        "same_uid_mutation_exclusion": False,
        "schema_version": _RECEIPT_SCHEMA,
        "target_execution": False,
        "transport_authority": False,
    }
    if any(receipt.get(key) != value for key, value in exact_claims.items()):
        raise TransportAdmissionError("transport receipt claim boundary mismatch")

    payload_sha256 = _require_sha256(
        receipt.get("payload_sha256"), field_name="payload_sha256"
    )
    manifest_sha256 = _require_sha256(
        receipt.get("payload_manifest_sha256"), field_name="payload_manifest_sha256"
    )
    payload_size = receipt.get("payload_size_bytes")
    manifest_size = receipt.get("payload_manifest_size_bytes")
    if type(payload_size) is not int or payload_size < 1:
        raise TransportAdmissionError("transport receipt payload_size_bytes is invalid")
    if type(manifest_size) is not int or manifest_size < 1:
        raise TransportAdmissionError("transport receipt payload_manifest_size_bytes is invalid")
    try:
        expected_payload, expected_manifest = construct_transport_smoke_payload(
            component_input
        )
    except ValueError as exc:
        raise TransportAdmissionError(
            "transport receipt component input cannot reconstruct the reviewed payload"
        ) from exc
    if (
        payload_size != len(expected_payload)
        or payload_sha256 != _sha256_bytes(expected_payload)
        or manifest_size != len(expected_manifest)
        or manifest_sha256 != _sha256_bytes(expected_manifest)
    ):
        raise TransportAdmissionError(
            "transport receipt does not close to the deterministic payload"
        )
    return receipt, payload_sha256, payload_size, expected_payload



def _same_child(
    observed: os.stat_result, expected: os.stat_result, *, expected_size: int
) -> bool:
    return (
        stat.S_ISREG(observed.st_mode)
        and stat.S_IMODE(observed.st_mode) == 0o444
        and observed.st_size == expected_size
        and _identity(observed) == _identity(expected)
    )



def open_admitted_transport_packet(
    packet_dir: Path,
    *,
    expected_admission_sha256: str,
    expected_command_id: str,
    expected_requested_target_run_id: str,
    expected_runner_source_sha256: str,
) -> AdmittedTransportPacket:
    """Revalidate an exact packet tuple and retain its already-hashed payload fd."""

    packet_dir = Path(packet_dir)
    if not packet_dir.is_absolute() or ".." in packet_dir.parts:
        raise TransportAdmissionError("transport packet directory must be absolute and canonical")
    try:
        resolved = packet_dir.resolve(strict=True)
    except OSError as exc:
        raise TransportAdmissionError("transport packet directory is unavailable") from exc
    if resolved != packet_dir:
        raise TransportAdmissionError("transport packet directory must be canonical")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    odirectory = getattr(os, "O_DIRECTORY", None)
    if type(nofollow) is not int or nofollow == 0 or type(odirectory) is not int or odirectory == 0:
        raise TransportAdmissionError("descriptor-relative no-follow admission is unsupported")

    directory_fd = -1
    receipt_fd = -1
    payload_fd = -1
    try:
        directory_fd = os.open(
            packet_dir,
            os.O_RDONLY | odirectory | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
        packet_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(packet_stat.st_mode) or stat.S_IMODE(packet_stat.st_mode) != 0o700:
            raise TransportAdmissionError("transport packet directory mode is not 0700")
        if set(os.listdir(directory_fd)) != {_PAYLOAD_NAME, _RECEIPT_NAME}:
            raise TransportAdmissionError("transport packet does not contain the exact tuple")

        child_flags = (
            os.O_RDONLY
            | nofollow
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        receipt_fd = os.open(_RECEIPT_NAME, child_flags, dir_fd=directory_fd)
        receipt_stat = os.fstat(receipt_fd)
        _require_regular_0444(
            receipt_stat, name=_RECEIPT_NAME, maximum_size=_RECEIPT_MAX_BYTES
        )
        receipt_raw = _read_exact(receipt_fd, receipt_stat.st_size, name=_RECEIPT_NAME)
        receipt_after = os.fstat(receipt_fd)
        if not _same_child(receipt_after, receipt_stat, expected_size=len(receipt_raw)):
            raise TransportAdmissionError("transport receipt identity changed while reading")
        receipt, payload_sha256, payload_size, expected_payload = _validate_receipt(
            receipt_raw,
            expected_admission_sha256=expected_admission_sha256,
            expected_command_id=expected_command_id,
            expected_requested_target_run_id=expected_requested_target_run_id,
            expected_runner_source_sha256=expected_runner_source_sha256,
        )
        os.close(receipt_fd)
        receipt_fd = -1

        payload_fd = os.open(_PAYLOAD_NAME, child_flags, dir_fd=directory_fd)
        payload_stat = os.fstat(payload_fd)
        _require_regular_0444(payload_stat, name=_PAYLOAD_NAME)
        if payload_stat.st_size != payload_size:
            raise TransportAdmissionError("transport payload size does not match receipt")
        payload_raw = _read_payload_duplicate(payload_fd, payload_size)
        if payload_raw != expected_payload:
            raise TransportAdmissionError(
                "transport payload bytes do not match deterministic reconstruction"
            )
        payload_after = os.fstat(payload_fd)
        if not _same_child(payload_after, payload_stat, expected_size=payload_size):
            raise TransportAdmissionError(
                "transport payload identity changed while reconstructing"
            )
        os.lseek(payload_fd, 0, os.SEEK_SET)

        if set(os.listdir(directory_fd)) != {_PAYLOAD_NAME, _RECEIPT_NAME}:
            raise TransportAdmissionError("transport packet tuple changed during admission")
        named_receipt = os.stat(_RECEIPT_NAME, dir_fd=directory_fd, follow_symlinks=False)
        named_payload = os.stat(_PAYLOAD_NAME, dir_fd=directory_fd, follow_symlinks=False)
        packet_after = os.fstat(directory_fd)
        named_packet = os.stat(packet_dir, follow_symlinks=False)
        if (
            not _same_child(named_receipt, receipt_stat, expected_size=len(receipt_raw))
            or not _same_child(named_payload, payload_stat, expected_size=payload_size)
            or not stat.S_ISDIR(packet_after.st_mode)
            or stat.S_IMODE(packet_after.st_mode) != 0o700
            or _identity(packet_after) != _identity(packet_stat)
            or _identity(named_packet) != _identity(packet_stat)
        ):
            raise TransportAdmissionError("transport packet identity changed during admission")

        admitted = AdmittedTransportPacket(
            payload_fd=payload_fd,
            payload_sha256=payload_sha256,
            payload_size=payload_size,
            payload_stat=payload_stat,
            receipt_sha256=_sha256_bytes(receipt_raw),
            receipt=receipt,
            receipt_raw=receipt_raw,
            packet_identity=_identity(packet_stat),
            receipt_identity=_identity(receipt_stat),
            payload_identity=_identity(payload_stat),
        )
        payload_fd = -1
        return admitted
    except TransportAdmissionError:
        raise
    except OSError as exc:
        raise TransportAdmissionError("transport packet admission I/O failed") from exc
    finally:
        if payload_fd >= 0:
            os.close(payload_fd)
        if receipt_fd >= 0:
            os.close(receipt_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
