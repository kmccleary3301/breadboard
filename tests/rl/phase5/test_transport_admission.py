from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from pathlib import Path
import warnings
import zipfile

import pytest

from breadboard.rl.phase5.transport_admission import (
    TransportAdmissionError,
    open_admitted_transport_packet,
)
from scripts.rl_phase5 import build_transport_smoke_payload as payload_builder

_COMMAND_ID = "transport-smoke-r1"
_REQUESTED_TARGET_RUN_ID = "20260716T120000Z-slurm-pending"
_RUNNER_TEST_SHA256 = "sha256:" + "2" * 64
_PAYLOAD_NAME = "transport-smoke-payload.zip"
_RECEIPT_NAME = "transport-smoke-payload-build.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNNER_SOURCE = _REPO_ROOT / "scripts/rl_phase3/run_phase3_target_command_impl.py"


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _runner_source_sha256() -> str:
    return _sha256(_RUNNER_SOURCE.read_bytes())


def _build_packet(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    packet_dir = tmp_path / "packet"
    payload_builder.build(
        destination=packet_dir,
        command_id=_COMMAND_ID,
        requested_target_run_id=_REQUESTED_TARGET_RUN_ID,
        runner_source_sha256=_runner_source_sha256(),
        runner_test_sha256=_RUNNER_TEST_SHA256,
    )
    return (
        packet_dir,
        (packet_dir / _PAYLOAD_NAME).read_bytes(),
        (packet_dir / _RECEIPT_NAME).read_bytes(),
    )


def _open(packet_dir: Path, receipt_raw: bytes):
    return open_admitted_transport_packet(
        packet_dir,
        expected_admission_sha256=_sha256(receipt_raw),
        expected_command_id=_COMMAND_ID,
        expected_requested_target_run_id=_REQUESTED_TARGET_RUN_ID,
        expected_runner_source_sha256=_runner_source_sha256(),
    )


def _replace_file(path: Path, raw: bytes, mode: int = 0o444) -> None:
    path.unlink()
    path.write_bytes(raw)
    path.chmod(mode)


def _rewrite_receipt(packet_dir: Path, mutate) -> bytes:
    receipt_path = packet_dir / _RECEIPT_NAME
    receipt = json.loads(receipt_path.read_bytes())
    mutate(receipt)
    raw = _canonical(receipt)
    _replace_file(receipt_path, raw)
    return raw

def _zip_info(
    name: str,
    mode: int,
    *,
    file_type: int = stat.S_IFREG,
    compression: int = zipfile.ZIP_STORED,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.compress_type = compression
    info.external_attr = (file_type | mode) << 16
    info.extra = b""
    info.comment = b""
    return info


def _archive_bytes(
    entries: list[tuple[zipfile.ZipInfo, bytes]], *, trailing: bytes = b""
) -> bytes:
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.comment = b""
            for info, raw in entries:
                archive.writestr(info, raw)
    return buffer.getvalue() + trailing


def _payload_parts(payload_raw: bytes) -> tuple[dict, bytes, dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(payload_raw)) as archive:
        manifest_raw = archive.read("payload_manifest.json")
        manifest = json.loads(manifest_raw)
        members = {
            "run.sh": archive.read("run.sh"),
            "transport_smoke.py": archive.read("transport_smoke.py"),
        }
    return manifest, manifest_raw, members


def _exact_entries(
    members: dict[str, bytes], manifest_raw: bytes
) -> list[tuple[zipfile.ZipInfo, bytes]]:
    return [
        (_zip_info("run.sh", 0o500), members["run.sh"]),
        (_zip_info("transport_smoke.py", 0o400), members["transport_smoke.py"]),
        (_zip_info("payload_manifest.json", 0o400), manifest_raw),
    ]


def _install_recomputed_payload(
    packet_dir: Path, payload_raw: bytes, manifest_raw: bytes
) -> bytes:
    _replace_file(packet_dir / _PAYLOAD_NAME, payload_raw)

    def mutate(receipt: dict) -> None:
        receipt["payload_manifest_sha256"] = _sha256(manifest_raw)
        receipt["payload_manifest_size_bytes"] = len(manifest_raw)
        receipt["payload_sha256"] = _sha256(payload_raw)
        receipt["payload_size_bytes"] = len(payload_raw)

    return _rewrite_receipt(packet_dir, mutate)


def _track_admission_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> list[int]:
    descriptors: list[int] = []
    real_open = os.open
    real_dup = os.dup

    def tracked_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def tracked_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        descriptors.append(duplicate)
        return duplicate

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "dup", tracked_dup)
    return descriptors


def _assert_admission_rejects_and_closes_descriptors(
    packet_dir: Path,
    receipt_raw: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptors = _track_admission_descriptors(monkeypatch)
    with pytest.raises(TransportAdmissionError):
        with _open(packet_dir, receipt_raw):
            pass
    assert descriptors
    for descriptor in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_exact_built_tuple_is_admitted_and_retains_the_exact_payload_fd(
    tmp_path: Path,
) -> None:
    packet_dir, payload_raw, receipt_raw = _build_packet(tmp_path)
    packet_stat = packet_dir.stat()
    payload_stat = (packet_dir / _PAYLOAD_NAME).stat()
    receipt_stat = (packet_dir / _RECEIPT_NAME).stat()

    with _open(packet_dir, receipt_raw) as admitted:
        payload_fd = admitted.payload_fd
        assert admitted.payload_sha256 == _sha256(payload_raw)
        assert admitted.payload_size == len(payload_raw)
        assert admitted.receipt_sha256 == _sha256(receipt_raw)
        assert admitted.receipt == json.loads(receipt_raw)
        assert admitted.packet_identity == (packet_stat.st_dev, packet_stat.st_ino)
        assert admitted.payload_identity == (payload_stat.st_dev, payload_stat.st_ino)
        assert admitted.receipt_identity == (receipt_stat.st_dev, receipt_stat.st_ino)
        assert os.pread(payload_fd, len(payload_raw) + 1, 0) == payload_raw

    with pytest.raises(OSError):
        os.fstat(payload_fd)

def test_successful_payload_closure_closes_validation_and_retained_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    descriptors = _track_admission_descriptors(monkeypatch)

    with _open(packet_dir, receipt_raw) as admitted:
        assert os.fstat(admitted.payload_fd).st_size == admitted.payload_size

    assert descriptors
    for descriptor in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_packet_directory_must_be_a_real_nonsymlink_directory(
    tmp_path: Path, kind: str
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    original = tmp_path / "original"
    packet_dir.rename(original)
    if kind == "file":
        packet_dir.write_bytes(b"not a directory")
    else:
        packet_dir.symlink_to(original, target_is_directory=True)

    with pytest.raises(ValueError):
        with _open(packet_dir, receipt_raw):
            pass


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o777])
def test_packet_directory_mode_must_be_exactly_private(
    tmp_path: Path, mode: int
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    packet_dir.chmod(mode)

    with pytest.raises(ValueError):
        with _open(packet_dir, receipt_raw):
            pass


@pytest.mark.parametrize("name", [_PAYLOAD_NAME, _RECEIPT_NAME])
@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_tuple_children_must_be_real_regular_files(
    tmp_path: Path, name: str, kind: str
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    child = packet_dir / name
    child.unlink()
    if kind == "directory":
        child.mkdir()
    else:
        external = tmp_path / f"external-{name}"
        external.write_bytes(b"external")
        child.symlink_to(external)

    with pytest.raises(ValueError):
        with _open(packet_dir, receipt_raw):
            pass


@pytest.mark.parametrize("name", [_PAYLOAD_NAME, _RECEIPT_NAME])
@pytest.mark.parametrize("mode", [0o400, 0o600, 0o644, 0o555])
def test_tuple_child_modes_must_be_exactly_read_only(
    tmp_path: Path, name: str, mode: int
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    (packet_dir / name).chmod(mode)

    with pytest.raises(ValueError):
        with _open(packet_dir, receipt_raw):
            pass


@pytest.mark.parametrize("mutation", ["extra", "missing-payload", "missing-receipt"])
def test_packet_directory_must_contain_the_exact_two_entry_tuple(
    tmp_path: Path, mutation: str
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    if mutation == "extra":
        extra = packet_dir / "extra"
        extra.write_bytes(b"extra")
        extra.chmod(0o444)
    elif mutation == "missing-payload":
        (packet_dir / _PAYLOAD_NAME).unlink()
    else:
        (packet_dir / _RECEIPT_NAME).unlink()

    with pytest.raises(ValueError):
        with _open(packet_dir, receipt_raw):
            pass


@pytest.mark.parametrize(
    "receipt_raw",
    [
        b"{not-json}\n",
        b"[]\n",
        b'{"schema_version":"duplicate","schema_version":"duplicate"}\n',
    ],
)
def test_malformed_receipt_is_rejected(tmp_path: Path, receipt_raw: bytes) -> None:
    packet_dir, _payload_raw, _original_receipt_raw = _build_packet(tmp_path)
    _replace_file(packet_dir / _RECEIPT_NAME, receipt_raw)

    with pytest.raises(ValueError):
        with _open(packet_dir, receipt_raw):
            pass


def test_noncanonical_receipt_bytes_are_rejected_even_when_json_is_equivalent(
    tmp_path: Path,
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    receipt = json.loads(receipt_raw)
    noncanonical = json.dumps(receipt, indent=2, sort_keys=False).encode()
    _replace_file(packet_dir / _RECEIPT_NAME, noncanonical)

    with pytest.raises(ValueError):
        with _open(packet_dir, noncanonical):
            pass


def test_receipt_larger_than_the_admission_limit_is_rejected(tmp_path: Path) -> None:
    packet_dir, _payload_raw, _receipt_raw = _build_packet(tmp_path)
    oversized = b" " * 65_537
    _replace_file(packet_dir / _RECEIPT_NAME, oversized)

    with pytest.raises(ValueError):
        with _open(packet_dir, oversized):
            pass


def test_authority_must_bind_the_exact_canonical_receipt_bytes(tmp_path: Path) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)

    with pytest.raises(ValueError):
        with open_admitted_transport_packet(
            packet_dir,
            expected_admission_sha256="sha256:" + "0" * 64,
            expected_command_id=_COMMAND_ID,
            expected_requested_target_run_id=_REQUESTED_TARGET_RUN_ID,
            expected_runner_source_sha256=_runner_source_sha256(),
        ):
            pass

    assert _sha256(receipt_raw) != "sha256:" + "0" * 64


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "bb.rl.phase5.transport-smoke-payload-build.v0"),
        ("passed", False),
        ("publication_state", "building"),
        ("incomplete_without_receipt", False),
        ("transport_authority", True),
        ("target_execution", True),
        ("campaign_admission", True),
        ("deterministic_double_build", False),
        ("claim_boundary", "authoritative"),
        ("publication_guarantee", "mutation_exclusion"),
        ("same_uid_mutation_exclusion", True),
        ("admission_revalidation_required", False),
        ("admission_binding", "path_only"),
        ("payload_path", "other.zip"),
        ("command_id", "different-command"),
        ("requested_target_run_id", "20260716T130000Z-slurm-pending"),
        ("runner_source_sha256", "sha256:" + "9" * 64),
    ],
)
def test_receipt_schema_claim_identity_and_source_mismatches_are_rejected(
    tmp_path: Path, field: str, replacement: object
) -> None:
    packet_dir, _payload_raw, _receipt_raw = _build_packet(tmp_path)
    changed_receipt_raw = _rewrite_receipt(
        packet_dir, lambda receipt: receipt.__setitem__(field, replacement)
    )

    with pytest.raises(ValueError):
        with _open(packet_dir, changed_receipt_raw):
            pass


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("command_id", "different-command"),
        ("requested_target_run_id", "20260716T130000Z-slurm-pending"),
        ("runner_source_sha256", "sha256:" + "9" * 64),
    ],
)
def test_component_input_identity_and_source_mismatches_are_rejected(
    tmp_path: Path, field: str, replacement: str
) -> None:
    packet_dir, _payload_raw, _receipt_raw = _build_packet(tmp_path)

    def mutate(receipt: dict) -> None:
        receipt["component_input"][field] = replacement
        receipt["component_input_sha256"] = _sha256(
            _canonical(receipt["component_input"])
        )

    changed_receipt_raw = _rewrite_receipt(packet_dir, mutate)
    with pytest.raises(ValueError):
        with _open(packet_dir, changed_receipt_raw):
            pass


@pytest.mark.parametrize(
    "field",
    [
        "payload_sha256",
        "payload_size_bytes",
        "payload_manifest_sha256",
        "payload_manifest_size_bytes",
    ],
)
def test_payload_and_manifest_digest_and_size_claim_swaps_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    packet_dir, payload_raw, _receipt_raw = _build_packet(tmp_path)
    replacement: object = (
        "sha256:" + "8" * 64
        if field.endswith("_sha256")
        else len(payload_raw) + 1
    )
    changed_receipt_raw = _rewrite_receipt(
        packet_dir, lambda receipt: receipt.__setitem__(field, replacement)
    )

    _assert_admission_rejects_and_closes_descriptors(
        packet_dir, changed_receipt_raw, monkeypatch
    )


def test_payload_path_swap_is_rejected_even_when_receipt_is_unchanged(
    tmp_path: Path,
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)
    _replace_file(packet_dir / _PAYLOAD_NAME, b"same path, different payload")

    with pytest.raises(ValueError):
        with _open(packet_dir, receipt_raw):
            pass


def test_admitted_descriptor_is_independent_of_later_payload_path_replacement(
    tmp_path: Path,
) -> None:
    packet_dir, payload_raw, receipt_raw = _build_packet(tmp_path)
    payload_path = packet_dir / _PAYLOAD_NAME

    with _open(packet_dir, receipt_raw) as admitted:
        _replace_file(payload_path, b"replacement after admission")
        assert os.pread(admitted.payload_fd, len(payload_raw) + 1, 0) == payload_raw
        assert payload_path.read_bytes() != payload_raw


def test_admission_identity_values_describe_regular_exact_mode_objects(
    tmp_path: Path,
) -> None:
    packet_dir, _payload_raw, receipt_raw = _build_packet(tmp_path)

    with _open(packet_dir, receipt_raw) as admitted:
        assert stat.S_ISREG(os.fstat(admitted.payload_fd).st_mode)
        assert stat.S_IMODE(os.fstat(admitted.payload_fd).st_mode) == 0o444


def test_fully_recomputed_canonical_authority_rejects_arbitrary_executable_run_sh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_dir, payload_raw, _receipt_raw = _build_packet(tmp_path)
    manifest, _manifest_raw, members = _payload_parts(payload_raw)
    arbitrary_run_sh = (
        b"#!/bin/bash\nset -euo pipefail\nexec /usr/bin/true\n"
    )
    members["run.sh"] = arbitrary_run_sh
    run_claim = next(
        row for row in manifest["members"] if row["path"] == "run.sh"
    )
    run_claim["sha256"] = _sha256(arbitrary_run_sh)
    run_claim["size_bytes"] = len(arbitrary_run_sh)
    malicious_manifest_raw = _canonical(manifest)
    malicious_payload_raw = _archive_bytes(
        _exact_entries(members, malicious_manifest_raw)
    )
    changed_receipt_raw = _install_recomputed_payload(
        packet_dir, malicious_payload_raw, malicious_manifest_raw
    )

    assert changed_receipt_raw == _canonical(json.loads(changed_receipt_raw))
    assert run_claim["sha256"] == _sha256(arbitrary_run_sh)
    assert run_claim["size_bytes"] == len(arbitrary_run_sh)
    _assert_admission_rejects_and_closes_descriptors(
        packet_dir, changed_receipt_raw, monkeypatch
    )


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "traversal", "link", "extra", "missing", "non-stored"],
)
def test_archive_shape_and_storage_mutations_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    packet_dir, payload_raw, _receipt_raw = _build_packet(tmp_path)
    _manifest, manifest_raw, members = _payload_parts(payload_raw)
    entries = _exact_entries(members, manifest_raw)

    if mutation == "duplicate":
        entries.insert(1, (_zip_info("run.sh", 0o500), members["run.sh"]))
    elif mutation == "traversal":
        entries.insert(
            -1, (_zip_info("../escape", 0o400), b"must never be extracted")
        )
    elif mutation == "link":
        entries[0] = (
            _zip_info("run.sh", 0o500, file_type=stat.S_IFLNK),
            b"transport_smoke.py",
        )
    elif mutation == "extra":
        entries.insert(-1, (_zip_info("extra.txt", 0o400), b"extra"))
    elif mutation == "missing":
        entries.pop(1)
    elif mutation == "non-stored":
        entries[0] = (
            _zip_info("run.sh", 0o500, compression=zipfile.ZIP_DEFLATED),
            members["run.sh"],
        )
    else:
        raise AssertionError(f"unhandled archive mutation: {mutation}")

    changed_payload_raw = _archive_bytes(entries)
    changed_receipt_raw = _install_recomputed_payload(
        packet_dir, changed_payload_raw, manifest_raw
    )
    _assert_admission_rejects_and_closes_descriptors(
        packet_dir, changed_receipt_raw, monkeypatch
    )


@pytest.mark.parametrize(
    "mutation",
    ["archive-mode", "manifest-mode", "manifest-size", "manifest-hash", "content"],
)
def test_member_mode_size_hash_and_content_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    packet_dir, payload_raw, _receipt_raw = _build_packet(tmp_path)
    manifest, manifest_raw, members = _payload_parts(payload_raw)
    run_claim = next(
        row for row in manifest["members"] if row["path"] == "run.sh"
    )

    if mutation == "manifest-mode":
        run_claim["mode"] = "0400"
        manifest_raw = _canonical(manifest)
    elif mutation == "manifest-size":
        run_claim["size_bytes"] += 1
        manifest_raw = _canonical(manifest)
    elif mutation == "manifest-hash":
        run_claim["sha256"] = "sha256:" + "0" * 64
        manifest_raw = _canonical(manifest)
    elif mutation == "content":
        members["transport_smoke.py"] += b"\n# unauthorized content drift\n"

    entries = _exact_entries(members, manifest_raw)
    if mutation == "archive-mode":
        entries[0] = (_zip_info("run.sh", 0o700), members["run.sh"])
    changed_payload_raw = _archive_bytes(entries)
    changed_receipt_raw = _install_recomputed_payload(
        packet_dir, changed_payload_raw, manifest_raw
    )
    _assert_admission_rejects_and_closes_descriptors(
        packet_dir, changed_receipt_raw, monkeypatch
    )


def test_trailing_zip_ambiguity_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_dir, payload_raw, _receipt_raw = _build_packet(tmp_path)
    _manifest, manifest_raw, _members = _payload_parts(payload_raw)
    changed_payload_raw = payload_raw + b"ambiguous trailing bytes"
    changed_receipt_raw = _install_recomputed_payload(
        packet_dir, changed_payload_raw, manifest_raw
    )

    _assert_admission_rejects_and_closes_descriptors(
        packet_dir, changed_receipt_raw, monkeypatch
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "extra-key",
        "missing-key",
        "command-id",
        "component",
        "fixed-nonce",
        "report-id",
        "report-schema",
        "requested-run-id",
        "runner-source",
        "runner-test",
        "resource",
        "nonclaim",
        "execution",
    ],
)
def test_canonical_manifest_schema_keys_claims_and_contract_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    packet_dir, payload_raw, _receipt_raw = _build_packet(tmp_path)
    manifest, _manifest_raw, members = _payload_parts(payload_raw)

    if mutation == "schema":
        manifest["schema_version"] = "bb.rl.phase5.transport-smoke-payload.v0"
    elif mutation == "extra-key":
        manifest["unexpected"] = False
    elif mutation == "missing-key":
        manifest.pop("resources")
    elif mutation == "command-id":
        manifest["command_id"] = "transport-smoke-r2"
    elif mutation == "component":
        manifest["component"] = "other_component"
    elif mutation == "fixed-nonce":
        manifest["fixed_nonce_sha256"] = "sha256:" + "9" * 64
    elif mutation == "report-id":
        manifest["report_id"] = "transport-smoke-other"
    elif mutation == "report-schema":
        manifest["report_schema_version"] = "bb.rl.phase3.transport_smoke.v0"
    elif mutation == "requested-run-id":
        manifest["requested_target_run_id"] = (
            "20260716T130000Z-slurm-pending"
        )
    elif mutation == "runner-source":
        manifest["runner_source_sha256"] = "sha256:" + "8" * 64
    elif mutation == "runner-test":
        manifest["runner_test_sha256"] = "sha256:" + "7" * 64
    elif mutation == "resource":
        manifest["resources"]["gpus"] = 1
    elif mutation == "nonclaim":
        manifest["nonclaims"] = manifest["nonclaims"][1:]
    elif mutation == "execution":
        manifest["execution_contract"]["required_executables"] = ["/bin/sh"]
    else:
        raise AssertionError(f"unhandled manifest mutation: {mutation}")

    changed_manifest_raw = _canonical(manifest)
    changed_payload_raw = _archive_bytes(
        _exact_entries(members, changed_manifest_raw)
    )
    changed_receipt_raw = _install_recomputed_payload(
        packet_dir, changed_payload_raw, changed_manifest_raw
    )
    assert changed_manifest_raw == _canonical(json.loads(changed_manifest_raw))
    _assert_admission_rejects_and_closes_descriptors(
        packet_dir, changed_receipt_raw, monkeypatch
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("command_id", "transport-smoke-r2"),
        ("fixed_nonce_sha256", "sha256:" + "9" * 64),
        (
            "requested_target_run_id",
            "20260716T130000Z-slurm-pending",
        ),
        ("runner_source_sha256", "sha256:" + "8" * 64),
        ("runner_test_sha256", "sha256:" + "7" * 64),
    ],
)
def test_self_consistent_manifest_component_input_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    packet_dir, payload_raw, _receipt_raw = _build_packet(tmp_path)
    manifest, _manifest_raw, members = _payload_parts(payload_raw)
    manifest["component_input"][field] = replacement
    manifest["component_input_sha256"] = _sha256(
        _canonical(manifest["component_input"])
    )
    top_level_field = {
        "command_id": "command_id",
        "fixed_nonce_sha256": "fixed_nonce_sha256",
        "requested_target_run_id": "requested_target_run_id",
        "runner_source_sha256": "runner_source_sha256",
        "runner_test_sha256": "runner_test_sha256",
    }[field]
    manifest[top_level_field] = replacement
    changed_manifest_raw = _canonical(manifest)
    changed_payload_raw = _archive_bytes(
        _exact_entries(members, changed_manifest_raw)
    )
    changed_receipt_raw = _install_recomputed_payload(
        packet_dir, changed_payload_raw, changed_manifest_raw
    )

    assert manifest["component_input_sha256"] == _sha256(
        _canonical(manifest["component_input"])
    )
    _assert_admission_rejects_and_closes_descriptors(
        packet_dir, changed_receipt_raw, monkeypatch
    )
