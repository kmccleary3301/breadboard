from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
from pathlib import Path
import secrets
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[2]
DIST_INFO = "breadboard_phase5_public-0.1.0.dist-info"
_ARTIFACT_NAME = "breadboard_phase5_public-0.1.0-py3-none-any.whl"
_MANIFEST_COMPONENTS = ("scripts", "rl_phase5", "phase5_public_wheel_manifest.json")
_EXPECTED_MANIFEST = b'{"artifact":"dist/breadboard_phase5_public-0.1.0-py3-none-any.whl","files":[{"source":"breadboard/rl/phase5/authority_contract.py","target":"breadboard/rl/phase5/authority_contract.py"},{"source":"breadboard/rl/phase5/authority_ipc.py","target":"breadboard/rl/phase5/authority_ipc.py"},{"source":"breadboard/rl/phase5/evidence.py","target":"breadboard/rl/phase5/evidence.py"},{"source":"breadboard/rl/phase5/external_proof.py","target":"breadboard/rl/phase5/external_proof.py"},{"source":"breadboard/rl/phase5/models.py","target":"breadboard/rl/phase5/models.py"},{"source":"breadboard/rl/phase5/score.py","target":"breadboard/rl/phase5/score.py"},{"source":"breadboard/rl/phase5/server_authority.py","target":"breadboard/rl/phase5/server_authority.py"}],"schema":"bb.rl.phase5.public-wheel-manifest.v1"}\n'
_FILES = (
    ("breadboard/rl/phase5/authority_contract.py", "breadboard/rl/phase5/authority_contract.py"),
    ("breadboard/rl/phase5/authority_ipc.py", "breadboard/rl/phase5/authority_ipc.py"),
    ("breadboard/rl/phase5/evidence.py", "breadboard/rl/phase5/evidence.py"),
    ("breadboard/rl/phase5/external_proof.py", "breadboard/rl/phase5/external_proof.py"),
    ("breadboard/rl/phase5/models.py", "breadboard/rl/phase5/models.py"),
    ("breadboard/rl/phase5/score.py", "breadboard/rl/phase5/score.py"),
    ("breadboard/rl/phase5/server_authority.py", "breadboard/rl/phase5/server_authority.py"),
)
_GENERATED_FILES = {
    "breadboard/__init__.py": b"",
    "breadboard/rl/__init__.py": b"",
    "breadboard/rl/phase5/__init__.py": b"",
    f"{DIST_INFO}/METADATA": (
        b"Metadata-Version: 2.1\nName: breadboard-phase5-public\nVersion: 0.1.0\n"
        b"Requires-Python: >=3.11\nRequires-Dist: cryptography>=45\n"
        b"Requires-Dist: pydantic>=2.11\n\n"
    ),
    f"{DIST_INFO}/WHEEL": (
        b"Wheel-Version: 1.0\nGenerator: breadboard-phase5\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n"
    ),
}
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_DIR_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)
_MAX_MANIFEST_BYTES = 16_384
_MAX_SOURCE_BYTES = 8_388_608


def _components(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if value.startswith("/") or "\\" in value or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("public wheel path is not a strict relative path")
    return parts


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_regular_at(root_fd: int, components: tuple[str, ...], *, limit: int) -> bytes:
    current_fd = os.dup(root_fd)
    try:
        for component in components[:-1]:
            child_fd = os.open(component, _DIR_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = child_fd
            if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
                raise ValueError("public wheel source ancestor is not a directory")
        file_fd = os.open(components[-1], _READ_FLAGS, dir_fd=current_fd)
    finally:
        os.close(current_fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("public wheel source must be a single-link regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("public wheel source exceeds its size limit")
        after = os.fstat(file_fd)
        if _identity(before) != _identity(after) or total != before.st_size:
            raise ValueError("public wheel source changed while being read")
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def _digest(value: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _wheel_bytes(files: dict[str, bytes]) -> bytes:
    record_path = f"{DIST_INFO}/RECORD"
    rows = [[name, _digest(content), str(len(content))] for name, content in sorted(files.items())]
    rows.append([record_path, "", ""])
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    files[record_path] = record.getvalue().encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            wheel.writestr(info, content)
    return output.getvalue()


def _open_dist(root_fd: int) -> int:
    try:
        return os.open("dist", _DIR_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        os.mkdir("dist", 0o755, dir_fd=root_fd)
        os.fsync(root_fd)
        return os.open("dist", _DIR_FLAGS, dir_fd=root_fd)


def _publish(root_fd: int, content: bytes) -> None:
    dist_fd = _open_dist(root_fd)
    temporary = f".{_ARTIFACT_NAME}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        try:
            existing = os.stat(_ARTIFACT_NAME, dir_fd=dist_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
            raise ValueError("public wheel artifact must be a single-link regular file")
        temp_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=dist_fd,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(temp_fd, view)
                view = view[written:]
            os.fsync(temp_fd)
            created = os.fstat(temp_fd)
            if not stat.S_ISREG(created.st_mode) or created.st_nlink != 1 or created.st_size != len(content):
                raise ValueError("public wheel temporary artifact is invalid")
        finally:
            os.close(temp_fd)
        os.rename(temporary, _ARTIFACT_NAME, src_dir_fd=dist_fd, dst_dir_fd=dist_fd)
        os.fsync(dist_fd)
    except Exception:
        try:
            os.unlink(temporary, dir_fd=dist_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(dist_fd)


def main() -> int:
    root_fd = os.open(ROOT, _DIR_FLAGS)
    try:
        manifest = _read_regular_at(root_fd, _MANIFEST_COMPONENTS, limit=_MAX_MANIFEST_BYTES)
        if manifest != _EXPECTED_MANIFEST:
            raise ValueError("public wheel manifest does not match the fixed allowlist")
        files = dict(_GENERATED_FILES)
        for source, target in _FILES:
            source_parts = _components(source)
            _components(target)
            files[target] = _read_regular_at(root_fd, source_parts, limit=_MAX_SOURCE_BYTES)
        content = _wheel_bytes(files)
        _publish(root_fd, content)
    finally:
        os.close(root_fd)
    print("sha256:" + hashlib.sha256(content).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
