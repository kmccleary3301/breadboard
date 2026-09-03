from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath

BREADBOARD_MEMBERS = (
    "breadboard/rl",
    "breadboard_engine/compilation",
    "scripts/rl_phase5/f2_container_entry.py",
    "scripts/rl_phase5/run_f2_target_command.py",
    "scripts/rl_phase5/f2_private_broker_lifecycle_probe.py",
    "scripts/rl_phase5/author_f2_target_dynamic_packet.py",
)
WRAPPER_MEMBERS = (
    "launch/generate_nemo.sh",
    "launch/eval_nemo.sh",
    "launch/utils",
    "recipe/nemo_async",
    "recipe/nemo_common",
    "responses_api_agents/breadboard_agent",
    "third_party/nemo-gym/nemo_gym",
    "third_party/nemo-gym/pyproject.toml",
    "third_party/nemo-gym/uv.lock",
    "third_party/nemo-gym/LICENSE",
    "third_party/nemo-gym/README.md",
)
INVENTORY_NAME = "F2_SOURCE_INVENTORY.json"


def _git_head(root: Path) -> str:
    git = root / ".git"
    if git.is_file():
        marker = git.read_text("utf-8").strip()
        if not marker.startswith("gitdir: "):
            raise ValueError(f"invalid git file: {git}")
        git = (root / marker[8:]).resolve()
    common = git
    if (git / "commondir").is_file():
        common = (git / (git / "commondir").read_text("utf-8").strip()).resolve()
    head = (git / "HEAD").read_text("ascii").strip()
    if head.startswith("ref: "):
        ref = head[5:]
        loose = next((p for p in (git / ref, common / ref) if p.is_file()), None)
        if loose:
            head = loose.read_text("ascii").strip()
        else:
            packed = common / "packed-refs"
            values = [line.split(" ", 1)[0] for line in packed.read_text("ascii").splitlines() if line and not line.startswith(("#", "^")) and line.endswith(" " + ref)]
            if len(values) != 1:
                raise ValueError(f"cannot resolve git HEAD for {root}")
            head = values[0]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError(f"invalid git HEAD for {root}")
    return head


def _source_files(root: Path, members: tuple[str, ...]):
    seen: set[PurePosixPath] = set()
    for member in members:
        source = root / member
        if not source.exists():
            raise FileNotFoundError(source)
        candidates = [source] if source.is_file() else sorted(source.rglob("*"))
        for path in candidates:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                raise ValueError(f"links/special files forbidden: {path}")
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if relative in seen:
                continue
            seen.add(relative)
            raw = path.read_bytes()
            after = path.stat()
            if after.st_size != len(raw) or after.st_mtime_ns != metadata.st_mtime_ns:
                raise RuntimeError(f"source changed during read: {path}")
            mode = 0o755 if metadata.st_mode & 0o111 else 0o644
            yield relative.as_posix(), raw, mode


def build_bundle(breadboard_root: Path, wrapper_root: Path, output: Path) -> dict[str, object]:
    breadboard_root, wrapper_root, output = breadboard_root.resolve(), wrapper_root.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(output)
    entries: list[tuple[str, bytes, int]] = []
    entries.extend(_source_files(breadboard_root, BREADBOARD_MEMBERS))
    entries.extend(_source_files(wrapper_root, WRAPPER_MEMBERS))
    entries.sort(key=lambda item: item[0])
    names = [name for name, _, _ in entries]
    if len(names) != len(set(names)):
        raise ValueError("duplicate bundle member")
    members = [{"path": name, "mode": mode, "size_bytes": len(raw), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()} for name, raw, mode in entries]
    tree_input = b"".join(name.encode() + b"\0" + mode.to_bytes(4, "big") + len(raw).to_bytes(8, "big") + hashlib.sha256(raw).digest() for name, raw, mode in entries)
    inventory = {
        "schema_version": "bb.rl.f2.source-bundle-inventory.v1",
        "breadboard_head": _git_head(breadboard_root),
        "wrapper_head": _git_head(wrapper_root),
        "tree_sha256": "sha256:" + hashlib.sha256(tree_input).hexdigest(),
        "members": members,
    }
    entries.append((INVENTORY_NAME, json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode(), 0o644))
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as sink, gzip.GzipFile(filename="", mode="wb", fileobj=sink, compresslevel=9, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, raw, mode in entries:
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), mode, 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(raw))
    except Exception:
        output.unlink(missing_ok=True)
        raise
    with output.open("rb") as persisted:
        os.fsync(persisted.fileno())
    archive_raw = output.read_bytes()
    return {**inventory, "archive_sha256": "sha256:" + hashlib.sha256(archive_raw).hexdigest(), "archive_size_bytes": len(archive_raw)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--breadboard-root", type=Path, required=True)
    parser.add_argument("--wrapper-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()
    args.inventory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(args.inventory, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        result = build_bundle(args.breadboard_root, args.wrapper_root, args.output)
        inventory_raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
        os.write(descriptor, inventory_raw)
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        args.inventory.unlink(missing_ok=True)
        raise
    else:
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
