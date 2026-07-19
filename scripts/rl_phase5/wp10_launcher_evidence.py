#!/usr/bin/env python3
"""Fail-closed WP10 local launcher evidence validation.

This module deliberately knows nothing about credential values.  A caller supplies the
seed only to :class:`SeededSecretScanner`; scan results and manifests never retain a
value or a value-derived digest.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

SCHEMA = "bb.rl.launcher-identity.v1"
SCAN_ALGORITHM = "bb.rl.seeded-secret-scan.v1"
MANIFEST_NAME = "LAUNCHER_IDENTITY_MANIFEST.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_ALLOWED_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")
_SUSPICIOUS_ARCHIVE_SUFFIXES = (".gz", ".bz2", ".xz", ".7z", ".rar")

class EvidenceError(ValueError):
    """Evidence is incomplete, unsafe, non-canonical, or unverifiable."""

@dataclass(frozen=True)
class ScanLimits:
    max_files: int = 10_000
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_archive_depth: int = 4
    max_archive_members: int = 10_000
    max_expanded_bytes: int = 256 * 1024 * 1024

@dataclass(frozen=True)
class ScanResult:
    algorithm: str
    files_scanned: int
    archive_members_scanned: int
    bytes_scanned: int
    inventory: tuple[dict[str, Any], ...]
    matches: tuple[dict[str, str], ...]

    @property
    def passed(self) -> bool:
        return not self.matches

    def projection(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "files_scanned": self.files_scanned,
            "archive_members_scanned": self.archive_members_scanned,
            "bytes_scanned": self.bytes_scanned,
            "inventory": list(self.inventory),
            "matches": list(self.matches),
            "passed": self.passed,
        }

def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()

def _representations(secret: bytes) -> tuple[tuple[str, bytes], ...]:
    if not secret:
        raise EvidenceError("seeded secret must not be empty")
    variants: list[tuple[str, bytes]] = [("raw", secret)]
    try:
        text = secret.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        variants.extend([
            ("json", json.dumps(text, ensure_ascii=True)[1:-1].encode()),
            ("url", urllib.parse.quote_from_bytes(secret, safe="").encode()),
            ("shell-single-quoted", ("'" + text.replace("'", "'\\''") + "'").encode()),
            ("utf16le", text.encode("utf-16le")),
            ("utf16be", text.encode("utf-16be")),
        ])
    variants.extend([
        ("base64", base64.b64encode(secret)),
        ("base64url", base64.urlsafe_b64encode(secret)),
        ("base64url-unpadded", base64.urlsafe_b64encode(secret).rstrip(b"=")),
        ("hex-lower", secret.hex().encode()),
        ("hex-upper", secret.hex().upper().encode()),
    ])
    unique: dict[bytes, str] = {}
    for label, value in variants:
        if value and value not in unique:
            unique[value] = label
    return tuple((label, value) for value, label in unique.items())

class SeededSecretScanner:
    def __init__(self, secret: bytes, limits: ScanLimits | None = None) -> None:
        self._needles = _representations(secret)
        self._limits = limits or ScanLimits()
        self._files = self._members = self._bytes = self._expanded = 0
        self._inventory: list[dict[str, Any]] = []
        self._matches: list[dict[str, str]] = []

    def scan(self, roots: Iterable[Path]) -> ScanResult:
        roots = [Path(root) for root in roots]
        if not roots:
            raise EvidenceError("at least one scan root is required")
        seen_roots: set[Path] = set()
        for root in roots:
            if not root.is_absolute():
                raise EvidenceError("scan roots must be absolute")
            if stat.S_ISLNK(root.lstat().st_mode):
                raise EvidenceError("scan root must not be a symlink")
            canonical = root.resolve(strict=True)
            if canonical in seen_roots:
                raise EvidenceError("duplicate scan root")
            seen_roots.add(canonical)
            self._walk(canonical, canonical.name)
        return ScanResult(SCAN_ALGORITHM, self._files, self._members, self._bytes,
                          tuple(self._inventory), tuple(self._matches))

    def _walk(self, path: Path, label: str) -> None:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise EvidenceError(f"symlink omitted from evidence inventory: {label}")
        if stat.S_ISREG(info.st_mode):
            self._scan_file(path, label, info.st_size)
            return
        if not stat.S_ISDIR(info.st_mode):
            raise EvidenceError(f"special file omitted from evidence inventory: {label}")
        try:
            entries = sorted(os.scandir(path), key=lambda entry: entry.name)
        except OSError as exc:
            raise EvidenceError(f"unreadable evidence directory: {label}") from exc
        for entry in entries:
            self._walk(Path(entry.path), f"{label}/{entry.name}")

    def _consume(self, amount: int, *, expanded: bool = False) -> None:
        if amount < 0 or amount > self._limits.max_file_bytes:
            raise EvidenceError("file/member byte budget exceeded")
        self._bytes += amount
        if self._bytes > self._limits.max_total_bytes:
            raise EvidenceError("total scan byte budget exceeded")
        if expanded:
            self._expanded += amount
            if self._expanded > self._limits.max_expanded_bytes:
                raise EvidenceError("archive expanded-byte budget exceeded")

    def _scan_file(self, path: Path, label: str, size: int) -> None:
        self._files += 1
        if self._files > self._limits.max_files:
            raise EvidenceError("file inventory budget exceeded")
        self._consume(size)
        try:
            flags = os.O_RDONLY | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                current = path.lstat()
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    raise EvidenceError(f"evidence file changed during scan: {label}")
                data = stream.read(self._limits.max_file_bytes + 1)
        except OSError as exc:
            raise EvidenceError(f"unreadable evidence file: {label}") from exc
        if len(data) != size:
            raise EvidenceError(f"evidence file changed during scan: {label}")
        self._record(label, data, "file")
        suffix = label.lower()
        if suffix.endswith(".zip"):
            self._scan_zip(data, label, 1)
        elif suffix.endswith((".tar", ".tar.gz", ".tgz")):
            self._scan_tar(data, label, 1)
        elif suffix.endswith(_SUSPICIOUS_ARCHIVE_SUFFIXES):
            raise EvidenceError(f"unsupported archive format: {label}")

    def _record(self, label: str, data: bytes, kind: str) -> None:
        self._inventory.append({"path": label, "kind": kind, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        for encoding, needle in self._needles:
            if needle in data:
                self._matches.append({"path": label, "encoding": encoding})

    def _member_name(self, parent: str, raw: str) -> str:
        name = PurePosixPath(raw)
        if name.is_absolute() or ".." in name.parts or not name.parts:
            raise EvidenceError(f"unsafe archive member in {parent}")
        return f"{parent}!/{name.as_posix()}"

    def _archive_member(self, data: bytes, label: str, depth: int) -> None:
        if depth > self._limits.max_archive_depth:
            raise EvidenceError("archive nesting budget exceeded")
        self._members += 1
        if self._members > self._limits.max_archive_members:
            raise EvidenceError("archive member budget exceeded")
        self._consume(len(data), expanded=True)
        self._record(label, data, "archive_member")
        lower = label.lower()
        if lower.endswith(".zip"):
            self._scan_zip(data, label, depth + 1)
        elif lower.endswith((".tar", ".tar.gz", ".tgz")):
            self._scan_tar(data, label, depth + 1)
        elif lower.endswith(_SUSPICIOUS_ARCHIVE_SUFFIXES):
            raise EvidenceError(f"unsupported nested archive format: {label}")

    def _scan_zip(self, data: bytes, label: str, depth: int) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for member in archive.infolist():
                    member_label = self._member_name(label, member.filename)
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise EvidenceError(f"archive symlink omitted: {member_label}")
                    if member.is_dir():
                        continue
                    if member.file_size > self._limits.max_file_bytes:
                        raise EvidenceError("archive member byte budget exceeded")
                    payload = archive.read(member)
                    if len(payload) != member.file_size:
                        raise EvidenceError(f"incomplete archive member: {member_label}")
                    self._archive_member(payload, member_label, depth)
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise EvidenceError(f"invalid or encrypted zip archive: {label}") from exc

    def _scan_tar(self, data: bytes, label: str, depth: int) -> None:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                for member in archive.getmembers():
                    member_label = self._member_name(label, member.name)
                    if member.issym() or member.islnk():
                        raise EvidenceError(f"archive link omitted: {member_label}")
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise EvidenceError(f"archive special member omitted: {member_label}")
                    if member.size > self._limits.max_file_bytes:
                        raise EvidenceError("archive member byte budget exceeded")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise EvidenceError(f"unreadable archive member: {member_label}")
                    payload = stream.read(self._limits.max_file_bytes + 1)
                    if len(payload) != member.size:
                        raise EvidenceError(f"incomplete archive member: {member_label}")
                    self._archive_member(payload, member_label, depth)
        except (tarfile.TarError, OSError) as exc:
            raise EvidenceError(f"invalid tar archive: {label}") from exc

def _exact_keys(value: dict[str, Any], required: set[str], where: str) -> None:
    if set(value) != required:
        raise EvidenceError(f"{where} fields differ: missing={sorted(required-set(value))}, extra={sorted(set(value)-required)}")

def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("manifest must be an object")
    required = {"schema_version","claim_class","run_id","breadboard_head","wrapper_head","files","image","launcher","service","boundaries","credential","v2","callback","cleanup","artifacts","scan","ibm_target_proven"}
    _exact_keys(value, required, "manifest")
    if value["schema_version"] != SCHEMA or value["claim_class"] != "local_contract" or value["ibm_target_proven"] is not False:
        raise EvidenceError("manifest claim class is not the WP10 local contract")
    if not isinstance(value["run_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", value["run_id"]):
        raise EvidenceError("invalid run_id")
    for key in ("breadboard_head", "wrapper_head"):
        if not isinstance(value[key], str) or not _GIT_HEAD.fullmatch(value[key]): raise EvidenceError(f"invalid immutable {key}")
    files = value["files"]
    if not isinstance(files, list) or not files: raise EvidenceError("files must be non-empty")
    required_roles = {"generate_launcher","eval_launcher","secret_helper","recipe_consumer","recipe_config","callback_script"}
    roles = set()
    for entry in files:
        _exact_keys(entry, {"role","repository","path","sha256"}, "file identity")
        if entry["repository"] not in ("breadboard", "wrapper") or Path(entry["path"]).is_absolute() or ".." in Path(entry["path"]).parts or not _SHA256.fullmatch(entry["sha256"]): raise EvidenceError("invalid file identity")
        roles.add(entry["role"])
    if roles != required_roles: raise EvidenceError("file identity roles incomplete")
    image=value["image"]; _exact_keys(image,{"reference","observed_id"},"image")
    if not _DIGEST_IMAGE.fullmatch(image["reference"]) or not re.fullmatch(r"sha256:[0-9a-f]{64}",image["observed_id"]): raise EvidenceError("mutable or unresolved image identity")
    launcher=value["launcher"]; _exact_keys(launcher,{"kind","command_template_sha256","started"},"launcher")
    if launcher["kind"] != "generate_nemo" or launcher["started"] is not True or not _SHA256.fullmatch(launcher["command_template_sha256"]): raise EvidenceError("launcher observation incomplete")
    service=value["service"]; _exact_keys(service,{"url_origin_path","timeout_seconds","real_v2_service"},"service")
    url=urllib.parse.urlsplit(service["url_origin_path"])
    if url.scheme not in ("http","https") or not url.netloc or url.username or url.password or url.query or url.fragment or service["real_v2_service"] is not True: raise EvidenceError("invalid or non-real service observation")
    if not isinstance(service["timeout_seconds"],(int,float)) or isinstance(service["timeout_seconds"],bool) or not (0 < service["timeout_seconds"] < float("inf")): raise EvidenceError("invalid timeout")
    boundaries=value["boundaries"]; _exact_keys(boundaries,{"docker","ray","process"},"boundaries")
    for name in boundaries:
        _exact_keys(boundaries[name],{"observed","capture_artifact"},f"{name} boundary")
        if boundaries[name]["observed"] is not True or not isinstance(boundaries[name]["capture_artifact"],str): raise EvidenceError(f"missing {name} observation")
    credential=value["credential"]; _exact_keys(credential,{"present","source","container_path","source_mode","staged_mode","read_only","deleted_after_use","allowed_class"},"credential")
    if credential != {"present":True,"source":"token_file","container_path":"/run/secrets/breadboard_harness_token","source_mode":"0400","staged_mode":"0400","read_only":True,"deleted_after_use":True,"allowed_class":True}: raise EvidenceError("credential controls incomplete")
    v2=value["v2"]; _exact_keys(v2,{"request_id","episode_id","create_succeeded","run_succeeded","completed_observed","closed_observed"},"v2")
    if not all(v2[k] is True for k in ("create_succeeded","run_succeeded","completed_observed","closed_observed")) or not all(isinstance(v2[k],str) and v2[k] for k in ("request_id","episode_id")): raise EvidenceError("V2 lifecycle incomplete")
    callback=value["callback"]; _exact_keys(callback,{"invocations","requests","responses"},"callback")
    if not all(isinstance(callback[k],int) and not isinstance(callback[k],bool) and callback[k] > 0 for k in callback): raise EvidenceError("callback leg absent")
    cleanup=value["cleanup"]; _exact_keys(cleanup,{"processes_terminated","containers_absent","staged_secret_absent","callback_server_terminated"},"cleanup")
    if not all(item is True for item in cleanup.values()): raise EvidenceError("cleanup incomplete")
    artifacts=value["artifacts"]
    if not isinstance(artifacts,list) or not artifacts: raise EvidenceError("artifact inventory absent")
    paths=set()
    for item in artifacts:
        _exact_keys(item,{"path","sha256","size_bytes"},"artifact")
        if Path(item["path"]).is_absolute() or ".." in Path(item["path"]).parts or item["path"] in paths or not _SHA256.fullmatch(item["sha256"]) or not isinstance(item["size_bytes"],int) or item["size_bytes"] < 0: raise EvidenceError("invalid artifact identity")
        paths.add(item["path"])
    scan=value["scan"]; _exact_keys(scan,{"algorithm","passed","files_scanned","archive_members_scanned","bytes_scanned","inventory_complete"},"scan")
    if scan["algorithm"] != SCAN_ALGORITHM or scan["passed"] is not True or scan["inventory_complete"] is not True or any(not isinstance(scan[k],int) or scan[k] < 0 for k in ("files_scanned","archive_members_scanned","bytes_scanned")): raise EvidenceError("scan incomplete")
    forbidden = ("authorization", "token_hash", "token_length", "environment", "env_dump", "secret")
    def check_keys(node: Any) -> None:
        if isinstance(node,dict):
            for key, child in node.items():
                lower=key.lower()
                if any(term in lower for term in forbidden) and key not in {"staged_secret_absent"}: raise EvidenceError(f"forbidden secret-derived/evironment field: {key}")
                check_keys(child)
        elif isinstance(node,list):
            for child in node: check_keys(child)
    check_keys(value)
    return value

def write_manifest(path: Path, value: Any) -> None:
    validate_manifest(value)
    payload=canonical_json_bytes(value)
    path=Path(path)
    if path.exists() or path.is_symlink(): raise EvidenceError("manifest destination must be new")
    fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    except BaseException:
        try: path.unlink()
        except FileNotFoundError: pass
        raise

def load_manifest(path: Path) -> dict[str, Any]:
    raw=Path(path).read_bytes()
    try: value=json.loads(raw)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise EvidenceError("manifest is not valid UTF-8 JSON") from exc
    validate_manifest(value)
    if raw != canonical_json_bytes(value): raise EvidenceError("manifest is not canonical JSON")
    return value

def verify_evidence(directory: Path, expected_wrapper_head: str, expected_breadboard_head: str) -> dict[str,Any]:
    directory=Path(directory).resolve(strict=True)
    if directory.is_symlink() or not directory.is_dir(): raise EvidenceError("evidence directory must be a real directory")
    manifest=load_manifest(directory/MANIFEST_NAME)
    if manifest["wrapper_head"] != expected_wrapper_head or manifest["breadboard_head"] != expected_breadboard_head: raise EvidenceError("repository head mismatch")
    for artifact in manifest["artifacts"]:
        path=directory/artifact["path"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != artifact["size_bytes"] or sha256_file(path) != artifact["sha256"]: raise EvidenceError(f"artifact identity mismatch: {artifact['path']}")
    return manifest

def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest="command",required=True)
    verify=commands.add_parser("verify")
    verify.add_argument("--evidence-dir",type=Path,required=True)
    verify.add_argument("--expected-wrapper-head",required=True)
    verify.add_argument("--expected-breadboard-head",required=True)
    args=parser.parse_args(argv)
    verify_evidence(args.evidence_dir,args.expected_wrapper_head,args.expected_breadboard_head)
    return 0
if __name__ == "__main__": raise SystemExit(main())
