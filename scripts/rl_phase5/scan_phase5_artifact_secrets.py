from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

_SCHEMA = "bb.rl.phase5-artifact-secret-scan-receipt.v1"
_RULES = (
    ("pem-private-key", re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]{64,}\r?\n-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(rb"(?:A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}")),
    ("github-token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,255}")),
    ("openai-key", re.compile(rb"sk-[A-Za-z0-9_-]{32,}")),
    ("slack-token", re.compile(rb"xox(?:a|b|p|r|s)-[A-Za-z0-9-]{20,}")),
    ("google-api-key", re.compile(rb"AIza[0-9A-Za-z_-]{35}")),
    ("breadboard-secret-canary", re.compile(rb"BB_SECRET_CANARY_[A-Za-z0-9_-]{8,}")),
)
_ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _safe_archive_name(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or pure.is_absolute()
        or ".." in pure.parts
        or "\x00" in name
        or name != pure.as_posix()
    ):
        raise ValueError("artifact contains an unsafe member name")


def _tar_members(raw: bytes, parent: str) -> Iterable[tuple[str, bytes]]:
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            for member in archive.getmembers():
                _safe_archive_name(member.name)
                if member.isfile():
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("nested archive member cannot be read")
                    member_raw = stream.read()
                elif member.isdir():
                    member_raw = b""
                elif member.issym() or member.islnk():
                    _safe_archive_name(member.linkname)
                    member_raw = member.linkname.encode("utf-8")
                else:
                    raise ValueError("nested archive contains an unsafe member type")
                yield f"{parent}!/{member.name}", member_raw
    except tarfile.TarError as exc:
        raise ValueError("nested artifact archive is invalid") from exc


def scan_artifact(payload: Path) -> dict[str, Any]:
    raw_zip = payload.read_bytes()
    inventory: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []

    def scan(scope_kind: str, name: str, raw: bytes) -> None:
        name_raw = name.encode("utf-8")
        inventory.append(
            {
                "content_sha256": _sha256(raw),
                "name_sha256": _sha256(name_raw),
                "scope_kind": scope_kind,
                "size_bytes": len(raw),
            }
        )
        for target_kind, target in (("filename", name_raw), ("content", raw)):
            for rule_id, pattern in _RULES:
                if pattern.search(target) is not None:
                    findings.append(
                        {
                            "rule_id": rule_id,
                            "scope_kind": scope_kind,
                            "scope_name_sha256": _sha256(name_raw),
                            "target_kind": target_kind,
                        }
                    )

    scan("zip-raw", "<artifact-raw-bytes>", raw_zip)
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("artifact zip contains duplicate member names")
            for name in names:
                _safe_archive_name(name)
            payload_info = next(
                (info for info in infos if info.filename == "payload_manifest.json"),
                None,
            )
            if payload_info is None:
                raise ValueError("artifact payload manifest is missing")
            manifest_raw = archive.read(payload_info)
            manifest = json.loads(manifest_raw)
            if not isinstance(manifest, dict) or _canonical(manifest) != manifest_raw:
                raise ValueError("artifact payload manifest is not canonical JSON")
            for info in infos:
                if info.is_dir():
                    raise ValueError("artifact zip contains a directory member")
                member_raw = archive.read(info)
                scan("zip-member", info.filename, member_raw)
                if info.filename.endswith(_ARCHIVE_SUFFIXES):
                    for nested_name, nested_raw in _tar_members(
                        member_raw, info.filename
                    ):
                        scan("nested-member", nested_name, nested_raw)
    except zipfile.BadZipFile as exc:
        raise ValueError("artifact is not a valid zip") from exc

    ordered_inventory = sorted(
        inventory,
        key=lambda row: (
            row["scope_kind"],
            row["name_sha256"],
            row["content_sha256"],
        ),
    )
    ordered_findings = sorted(
        findings,
        key=lambda row: (
            row["rule_id"],
            row["scope_kind"],
            row["scope_name_sha256"],
            row["target_kind"],
        ),
    )
    scope_counts = {
        kind: sum(row["scope_kind"] == kind for row in ordered_inventory)
        for kind in ("zip-raw", "zip-member", "nested-member")
    }
    manifest_sha256 = next(
        row["content_sha256"]
        for row in ordered_inventory
        if row["scope_kind"] == "zip-member"
        and row["name_sha256"] == _sha256(b"payload_manifest.json")
    )
    return {
        "schema_version": _SCHEMA,
        "scanner_sha256": _sha256(Path(__file__).read_bytes()),
        "artifact": {
            "sha256": _sha256(raw_zip),
            "size_bytes": len(raw_zip),
        },
        "payload_manifest_sha256": manifest_sha256,
        "rules_sha256": _sha256(
            _canonical(
                [
                    {"rule_id": rule_id, "pattern": pattern.pattern.decode("ascii")}
                    for rule_id, pattern in _RULES
                ]
            )
        ),
        "inventory": {
            "scope_counts": scope_counts,
            "total_scopes": len(ordered_inventory),
            "inventory_sha256": _sha256(_canonical(ordered_inventory)),
            "filenames_scanned": True,
            "contents_scanned": True,
            "zip_raw_bytes_scanned": True,
            "canonical_payload_manifest_verified": True,
        },
        "finding_count": len(ordered_findings),
        "findings": ordered_findings,
        "passed": not ordered_findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministically scan one frozen Phase 5 artifact for secrets"
    )
    parser.add_argument("--payload-zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    receipt = scan_artifact(args.payload_zip.resolve(strict=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(receipt))
    return 0 if receipt["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "scan_artifact"]
