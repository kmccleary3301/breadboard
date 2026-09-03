from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from breadboard_engine.compilation.contracts import canonical_json_bytes

from scripts.rl_phase5.scan_phase5_artifact_secrets import main, scan_artifact


def _artifact(path: Path, *, member_name: str = "runtime.py", body: bytes = b"print('safe')\n") -> Path:
    source_buffer = BytesIO()
    with tarfile.open(fileobj=source_buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as source:
        info = tarfile.TarInfo("breadboard/rl/runtime.py")
        info.size = len(body)
        info.mtime = 0
        info.mode = 0o400
        source.addfile(info, BytesIO(body))
    source_raw = source_buffer.getvalue()
    rows = [
        {
            "mode": "0400",
            "path": member_name,
            "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
            "size_bytes": len(body),
        },
        {
            "mode": "0400",
            "path": "breadboard-source.tar.gz",
            "sha256": "sha256:" + hashlib.sha256(source_raw).hexdigest(),
            "size_bytes": len(source_raw),
        },
    ]
    manifest = canonical_json_bytes({"member_count": len(rows), "members": rows})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member_name, body)
        archive.writestr("breadboard-source.tar.gz", source_raw)
        archive.writestr("payload_manifest.json", manifest)
    return path


def test_scanner_is_deterministic_and_scans_raw_manifest_members_and_filenames(tmp_path: Path) -> None:
    payload = _artifact(tmp_path / "safe.zip")

    first = scan_artifact(payload)
    second = scan_artifact(payload)

    assert first == second
    assert first["passed"] is True
    assert first["finding_count"] == 0
    assert first["inventory"]["scope_counts"] == {
        "zip-raw": 1,
        "zip-member": 3,
        "nested-member": 1,
    }
    assert first["inventory"]["filenames_scanned"] is True
    assert first["inventory"]["zip_raw_bytes_scanned"] is True
    assert first["inventory"]["canonical_payload_manifest_verified"] is True


@pytest.mark.parametrize(
    ("member_name", "body", "target_kind"),
    (
        ("runtime.py", b"BB_SECRET_CANARY_0123456789abcdef\n", "content"),
        ("BB_SECRET_CANARY_0123456789abcdef.py", b"safe\n", "filename"),
        ("runtime.py", b"-----BEGIN PRIVATE KEY-----\n" + b"A" * 96 + b"\n-----END PRIVATE KEY-----\n", "content"),
    ),
)
def test_scanner_canaries_fail_closed_without_logging_secret(
    tmp_path: Path, member_name: str, body: bytes, target_kind: str
) -> None:
    payload = _artifact(tmp_path / "canary.zip", member_name=member_name, body=body)
    output = tmp_path / "receipt.json"

    assert main(["--payload-zip", str(payload), "--output", str(output)]) == 2
    receipt = json.loads(output.read_bytes())
    assert receipt["passed"] is False
    assert receipt["finding_count"] >= 1
    assert target_kind in {finding["target_kind"] for finding in receipt["findings"]}
    assert b"BB_SECRET_CANARY" not in output.read_bytes()
    assert b"BEGIN PRIVATE KEY" not in output.read_bytes()
