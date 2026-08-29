from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import platform
import re
import subprocess
import sys
from urllib.parse import urlsplit

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist

_ROOT = Path(__file__).resolve().parent
_PROVENANCE_FILENAME = "engine-build-provenance.v1.json"
_SOURCE_IDENTITY_FILENAME = "engine-source-identity.v1.json"
_SOURCE_IDENTITY_SCHEMA = "bb.engine_source_identity.v1"
_MAX_SOURCE_IDENTITY_BYTES = 4096
_HEX40 = re.compile(r"[0-9a-f]{40}")
_WHEEL_INPUT_PATHS = (
    "adaptive_iter.py",
    "agent_configs",
    "agentic_coder_prototype",
    "breadboard",
    "breadboard_engine",
    "breadboard_sdk",
    "config",
    "conformance",
    "contracts",
    "docs",
    "implementations",
    "scripts",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "requirements_web.txt",
    ":(exclude)docs/conformance/evidence_snapshots/**",
    ":(exclude)docs/conformance/e4_target_support/**",
    ":(exclude)scripts/archive/**",
)


def _canonical_repository(value: str) -> str:
    raw = value.strip()
    scp_remote = re.fullmatch(r"git@([^:/]+):(.+)", raw)
    if scp_remote is not None:
        raw = f"https://{scp_remote.group(1)}/{scp_remote.group(2)}"
    parsed = urlsplit(raw)
    if parsed.scheme == "ssh":
        try:
            port = parsed.port
        except ValueError:
            port = None
        if (
            not parsed.hostname
            or parsed.username not in {None, "git"}
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path in {"", "/"}
        ):
            raise RuntimeError(
                "wheel provenance requires an HTTPS source repository URL"
            )
        netloc = parsed.hostname + (f":{port}" if port is not None else "")
        raw = f"https://{netloc}{parsed.path}"
        parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path in {"", "/"}
    ):
        raise RuntimeError("wheel provenance requires an HTTPS source repository URL")
    return raw


def _embedded_source_identity() -> tuple[str, str, str] | None:
    path = _ROOT / "breadboard_engine" / _SOURCE_IDENTITY_FILENAME
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_SOURCE_IDENTITY_BYTES
        ):
            return None
        payload = json.loads(path.read_text(encoding="ascii"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "schemaVersion",
        "sourceRepository",
        "sourceCommit",
        "sourceTree",
    }:
        return None
    repository = payload["sourceRepository"]
    commit = payload["sourceCommit"]
    tree = payload["sourceTree"]
    if (
        payload["schemaVersion"] != _SOURCE_IDENTITY_SCHEMA
        or not isinstance(repository, str)
        or not isinstance(commit, str)
        or not isinstance(tree, str)
        or _HEX40.fullmatch(commit) is None
        or _HEX40.fullmatch(tree) is None
    ):
        return None
    try:
        repository = _canonical_repository(repository)
    except RuntimeError:
        return None
    return repository, commit, tree


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
    raw_repository_override = os.environ.get("BREADBOARD_BUILD_SOURCE_REPOSITORY")
    overrides = (
        (
            _canonical_repository(raw_repository_override)
            if raw_repository_override is not None
            else None
        ),
        os.environ.get("BREADBOARD_BUILD_SOURCE_COMMIT"),
        os.environ.get("BREADBOARD_BUILD_SOURCE_TREE"),
    )
    if any(value is not None for value in overrides) and not all(
        value is not None for value in overrides
    ):
        raise RuntimeError("wheel provenance source overrides must be complete")

    try:
        in_checkout = _git("rev-parse", "--is-inside-work-tree") == "true"
    except (OSError, subprocess.CalledProcessError):
        in_checkout = False

    if in_checkout:
        status = _git(
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *_WHEEL_INPUT_PATHS,
        )
        if status:
            raise RuntimeError("wheel provenance requires clean wheel build inputs")
        actual = (
            _canonical_repository(_git("remote", "get-url", "origin")),
            _git("rev-parse", "HEAD"),
            _git("rev-parse", "HEAD^{tree}"),
        )
        if all(value is not None for value in overrides) and overrides != actual:
            raise RuntimeError(
                "wheel provenance source overrides do not match the Git checkout"
            )
        repository, commit, tree = actual
    elif all(value is not None for value in overrides):
        repository, commit, tree = overrides
    else:
        embedded = _embedded_source_identity()
        if embedded is None:
            raise RuntimeError(
                "wheel provenance requires a Git checkout, complete source overrides, "
                "or embedded sdist source identity"
            )
        repository, commit, tree = embedded

    assert repository is not None
    assert commit is not None
    assert tree is not None
    if _HEX40.fullmatch(commit) is None or _HEX40.fullmatch(tree) is None:
        raise RuntimeError(
            "wheel provenance requires exact lowercase commit and tree IDs"
        )
    repository = _canonical_repository(repository)
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


class SdistWithSourceIdentity(_sdist):
    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        repository, commit, tree = _source_identity()
        release_files = list(files)
        for dependency_lock in ("requirements.txt", "requirements_web.txt"):
            if dependency_lock not in release_files:
                release_files.append(dependency_lock)
        super().make_release_tree(base_dir, release_files)
        path = Path(base_dir) / "breadboard_engine" / _SOURCE_IDENTITY_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": _SOURCE_IDENTITY_SCHEMA,
                    "sourceRepository": repository,
                    "sourceCommit": commit,
                    "sourceTree": tree,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="ascii",
        )
        path.chmod(0o644)


class BuildPyWithProvenance(_build_py):
    def _provenance_path(self) -> Path:
        return Path(self.build_lib) / "breadboard_engine" / _PROVENANCE_FILENAME

    def run(self) -> None:
        repository, commit, tree = _source_identity()
        super().run()
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


setup(
    cmdclass={
        "build_py": BuildPyWithProvenance,
        "sdist": SdistWithSourceIdentity,
    }
)
