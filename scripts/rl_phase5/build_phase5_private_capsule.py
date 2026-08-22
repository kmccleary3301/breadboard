from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_NAME = "breadboard_phase5_authority_capsule.zip"
_MANIFEST_COMPONENTS = ("scripts", "rl_phase5", "phase5_private_capsule_manifest.json")
_EXPECTED_MANIFEST = b'{"artifact":"dist/breadboard_phase5_authority_capsule.zip","files":[{"source":"breadboard/rl/phase5/authority_contract.py","target":"breadboard/rl/phase5/authority_contract.py"},{"source":"breadboard/rl/phase5/authority_ipc.py","target":"breadboard/rl/phase5/authority_ipc.py"},{"source":"breadboard/rl/phase5/evidence.py","target":"breadboard/rl/phase5/evidence.py"},{"source":"breadboard/rl/phase5/external_proof.py","target":"breadboard/rl/phase5/external_proof.py"},{"source":"breadboard/rl/phase5/models.py","target":"breadboard/rl/phase5/models.py"},{"source":"breadboard/rl/phase5/score.py","target":"breadboard/rl/phase5/score.py"},{"source":"breadboard/rl/phase5/server_authority.py","target":"breadboard/rl/phase5/server_authority.py"},{"source":"scripts/rl_phase5/phase5_authority_evidence.py","target":"phase5_authority_evidence.py"},{"source":"scripts/rl_phase5/phase5_authority_score.py","target":"phase5_authority_score.py"},{"source":"scripts/rl_phase5/phase5_authority_service.py","target":"phase5_authority_service.py"},{"source":"scripts/rl_phase5/phase5_authority_store.py","target":"phase5_authority_store.py"}],"schema":"bb.rl.phase5.private-capsule-manifest.v1"}\n'
_FILES = (
    ("breadboard/rl/phase5/authority_contract.py", "breadboard/rl/phase5/authority_contract.py"),
    ("breadboard/rl/phase5/authority_ipc.py", "breadboard/rl/phase5/authority_ipc.py"),
    ("breadboard/rl/phase5/evidence.py", "breadboard/rl/phase5/evidence.py"),
    ("breadboard/rl/phase5/external_proof.py", "breadboard/rl/phase5/external_proof.py"),
    ("breadboard/rl/phase5/models.py", "breadboard/rl/phase5/models.py"),
    ("breadboard/rl/phase5/score.py", "breadboard/rl/phase5/score.py"),
    ("breadboard/rl/phase5/server_authority.py", "breadboard/rl/phase5/server_authority.py"),
    ("scripts/rl_phase5/phase5_authority_evidence.py", "phase5_authority_evidence.py"),
    ("scripts/rl_phase5/phase5_authority_score.py", "phase5_authority_score.py"),
    ("scripts/rl_phase5/phase5_authority_service.py", "phase5_authority_service.py"),
    ("scripts/rl_phase5/phase5_authority_store.py", "phase5_authority_store.py"),
)
_GENERATED_FILES = {
    "breadboard/__init__.py": b"",
    "breadboard/rl/__init__.py": b"",
    "breadboard/rl/phase5/__init__.py": b"",
}
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_DIR_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)
_MAX_MANIFEST_BYTES = 16_384
_MAX_SOURCE_BYTES = 8_388_608


def _components(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if value.startswith("/") or "\\" in value or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("private capsule path is not a strict relative path")
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
                raise ValueError("private capsule source ancestor is not a directory")
        file_fd = os.open(components[-1], _READ_FLAGS, dir_fd=current_fd)
    finally:
        os.close(current_fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("private capsule source must be a single-link regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("private capsule source exceeds its size limit")
        after = os.fstat(file_fd)
        if _identity(before) != _identity(after) or total != before.st_size:
            raise ValueError("private capsule source changed while being read")
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def _capsule_bytes(files: dict[str, bytes]) -> bytes:
    inventory = {
        "files": [
            {
                "path": name,
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for name, content in sorted(files.items())
        ],
        "schema": "bb.rl.phase5.private-capsule-inventory.v1",
    }
    files["phase5-capsule-inventory.json"] = json.dumps(
        inventory,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as capsule:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            capsule.writestr(info, content)
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
            raise ValueError("private capsule artifact must be a single-link regular file")
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
                raise ValueError("private capsule temporary artifact is invalid")
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
            raise ValueError("private capsule manifest does not match the fixed allowlist")
        files = dict(_GENERATED_FILES)
        for source, target in _FILES:
            source_parts = _components(source)
            _components(target)
            files[target] = _read_regular_at(root_fd, source_parts, limit=_MAX_SOURCE_BYTES)
        content = _capsule_bytes(files)
        _publish(root_fd, content)
    finally:
        os.close(root_fd)
    print("sha256:" + hashlib.sha256(content).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
