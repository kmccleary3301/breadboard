from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from urllib.parse import urlsplit

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_ROOT = Path(__file__).resolve().parent
_PROVENANCE_FILENAME = "engine-build-provenance.v1.json"
_HEX40 = re.compile(r"[0-9a-f]{40}")


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _source_identity() -> tuple[str, str, str]:
    commit = os.environ.get("BREADBOARD_BUILD_SOURCE_COMMIT")
    tree = os.environ.get("BREADBOARD_BUILD_SOURCE_TREE")
    repository = os.environ.get("BREADBOARD_BUILD_SOURCE_REPOSITORY")
    if commit is None or tree is None or repository is None:
        status = _git(
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "breadboard_engine",
            "pyproject.toml",
            "setup.py",
            "requirements.txt",
            "requirements_web.txt",
        )
        if status:
            raise RuntimeError("wheel provenance requires clean engine build inputs")
        commit = _git("rev-parse", "HEAD")
        tree = _git("rev-parse", "HEAD^{tree}")
        repository = _git("remote", "get-url", "origin")
    if _HEX40.fullmatch(commit) is None or _HEX40.fullmatch(tree) is None:
        raise RuntimeError(
            "wheel provenance requires exact lowercase commit and tree IDs"
        )
    parsed_repository = urlsplit(repository)
    if (
        parsed_repository.scheme != "https"
        or not parsed_repository.hostname
        or parsed_repository.username is not None
        or parsed_repository.password is not None
        or parsed_repository.query
        or parsed_repository.fragment
        or parsed_repository.path in {"", "/"}
    ):
        raise RuntimeError("wheel provenance requires an HTTPS source repository URL")
    return repository, commit, tree


def _hash_files(root: Path, paths: tuple[str, ...], *, domain: bytes) -> str:
    digest = hashlib.sha256(domain + b"\0")
    for relative in paths:
        path = root / relative
        content = path.read_bytes()
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _engine_source_sha256(package_root: Path) -> str:
    source_paths = sorted(
        path
        for path in package_root.rglob("*.py")
        if path.is_file() and not path.is_symlink()
    )
    if not source_paths:
        raise RuntimeError("wheel provenance found no engine Python sources")
    digest = hashlib.sha256(b"breadboard-engine-python-source-v1\0")
    for path in source_paths:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _target() -> dict[str, str]:
    target_platform = (
        "darwin"
        if sys.platform == "darwin"
        else "linux"
        if sys.platform.startswith("linux")
        else None
    )
    target_architecture = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
    }.get(platform.machine().lower())
    if target_platform is None or target_architecture is None:
        raise RuntimeError("wheel provenance does not support this build target")
    return {"platform": target_platform, "architecture": target_architecture}


class BuildPyWithProvenance(_build_py):
    def _provenance_path(self) -> Path:
        return Path(self.build_lib) / "breadboard_engine" / _PROVENANCE_FILENAME

    def run(self) -> None:
        super().run()
        repository, commit, tree = _source_identity()
        package_root = Path(self.build_lib) / "breadboard_engine"
        payload = {
            "schemaVersion": "bb.engine_build_provenance.v1",
            "sourceRepository": repository,
            "sourceCommit": commit,
            "sourceTree": tree,
            "engineSourceSha256": _engine_source_sha256(package_root),
            "dependencyLockSha256": _hash_files(
                _ROOT,
                ("requirements.txt", "requirements_web.txt"),
                domain=b"breadboard-engine-dependency-lock-v1",
            ),
            "buildRecipeSha256": _hash_files(
                _ROOT,
                ("pyproject.toml", "setup.py"),
                domain=b"breadboard-engine-build-recipe-v1",
            ),
            "target": _target(),
        }
        provenance_path = self._provenance_path()
        provenance_path.parent.mkdir(parents=True, exist_ok=True)
        provenance_path.write_text(
            json.dumps(
                payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            )
            + "\n",
            encoding="utf-8",
        )
        provenance_path.chmod(0o644)

    def get_outputs(self, include_bytecode: int = 1) -> list[str]:
        outputs = list(super().get_outputs(include_bytecode))
        provenance_path = str(self._provenance_path())
        if provenance_path not in outputs:
            outputs.append(provenance_path)
        return outputs


setup(cmdclass={"build_py": BuildPyWithProvenance})
