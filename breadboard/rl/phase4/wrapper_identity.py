from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

WRAPPER_IDENTITY_SCHEMA = "bb.rl.phase4.wrapper_identity.v1"
REQUIRED_SUBMODULES = {
    "verl": "third_party/verl",
    "nemo_gym": "third_party/nemo-gym",
}
PACKAGE_SENTINELS = {
    "verl": ("pyproject.toml", "setup.py", "verl/__init__.py"),
    "nemo_gym": ("pyproject.toml", "setup.py", "nemo_gym/__init__.py"),
}
GitRunner = Callable[[list[str], Path], str]


def sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def sha256_json(payload: Any) -> str:
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _run_git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _directory_digest(root: Path) -> str:
    h = hashlib.sha256()
    any_file = False
    if not root.exists() or not root.is_dir():
        return ""
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc" or ".git" in path.parts:
            continue
        any_file = True
        h.update(str(path.relative_to(root)).replace("\\", "/").encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest() if any_file else ""


def parse_deps_pins(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        return {}
    pins: dict[str, str] = {}
    for section, values in loaded.items():
        if not isinstance(section, str) or not isinstance(values, dict):
            continue
        for key in ("pin", "commit", "rev", "branch"):
            value = values.get(key)
            if isinstance(value, str) and value.strip():
                pins[f"{section}_{key}"] = value.strip()
    return pins


def parse_submodule_status(raw: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        marker = stripped[0] if stripped[0] in {"-", "+", "U"} else ""
        parts = stripped.lstrip("-+U ").split()
        if len(parts) >= 2:
            rows[parts[1]] = {"path": parts[1], "commit": parts[0], "marker": marker}
    return rows


@dataclass(frozen=True)
class WrapperSubmoduleIdentity:
    name: str
    relative_path: str
    path: str
    expected_commit: str
    actual_commit: str
    submodule_commit: str
    submodule_marker: str
    package_sentinel: str
    content_sha256: str
    blockers: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


@dataclass(frozen=True)
class WrapperIdentity:
    schema_version: str
    wrapper_path: str
    wrapper_commit: str
    wrapper_ref: str
    deps_yaml_path: str
    deps_yaml_sha256: str
    pins: dict[str, str]
    submodules: dict[str, dict[str, str]]
    components: dict[str, WrapperSubmoduleIdentity]
    blockers: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.blockers and all(component.passed for component in self.components.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "wrapper_path": self.wrapper_path,
            "wrapper_commit": self.wrapper_commit,
            "wrapper_ref": self.wrapper_ref,
            "deps_yaml_path": self.deps_yaml_path,
            "deps_yaml_sha256": self.deps_yaml_sha256,
            "pins": dict(self.pins),
            "submodules": dict(self.submodules),
            "components": {name: component.to_dict() for name, component in self.components.items()},
            "blockers": list(self.blockers),
            "passed": self.passed,
        }


def _sha_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes()) if path.exists() and path.is_file() else ""


def _first_existing(root: Path, candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if (root / candidate).exists():
            return candidate
    return ""


def _expected_pin(name: str, pins: dict[str, str]) -> str:
    return pins.get(f"{name}_pin") or pins.get(f"{name}_commit") or pins.get(f"{name}_rev") or ""


def collect_wrapper_identity(wrapper_dir: Path, *, git_runner: GitRunner = _run_git) -> WrapperIdentity:
    wrapper_dir = wrapper_dir.resolve()
    deps_path = wrapper_dir / "deps.yaml"
    deps_sha = _sha_file(deps_path)
    pins = parse_deps_pins(deps_path)
    raw_status = git_runner(["submodule", "status", "--recursive"], wrapper_dir)
    submodules = parse_submodule_status(raw_status)
    wrapper_commit = git_runner(["rev-parse", "HEAD"], wrapper_dir)
    wrapper_ref = git_runner(["rev-parse", "--abbrev-ref", "HEAD"], wrapper_dir)
    blockers: list[str] = []
    if not wrapper_dir.exists():
        blockers.append("wrapper_missing")
    if not wrapper_commit:
        blockers.append("wrapper_commit_missing")
    if not deps_path.exists():
        blockers.append("deps_yaml_missing")

    components: dict[str, WrapperSubmoduleIdentity] = {}
    for name, rel in REQUIRED_SUBMODULES.items():
        path = wrapper_dir / rel
        status = submodules.get(rel, {})
        component_blockers: list[str] = []
        expected = _expected_pin(name, pins)
        if not expected:
            component_blockers.append(f"submodule_expected_pin_missing:{rel}")
        actual = git_runner(["rev-parse", "HEAD"], path) if path.exists() else ""
        sentinel = _first_existing(path, PACKAGE_SENTINELS[name]) if path.exists() else ""
        digest = _directory_digest(path)
        marker = status.get("marker", "")
        status_commit = status.get("commit", "")
        if rel not in submodules:
            component_blockers.append(f"submodule_status_missing:{rel}")
        if marker == "-":
            component_blockers.append(f"submodule_uninitialized:{rel}")
        elif marker == "+":
            component_blockers.append(f"submodule_dirty:{rel}")
        elif marker == "U":
            component_blockers.append(f"submodule_conflicted:{rel}")
        if not path.exists() or not path.is_dir():
            component_blockers.append(f"submodule_path_missing:{rel}")
        if path.exists() and not digest:
            component_blockers.append(f"submodule_empty:{rel}")
        if not sentinel:
            component_blockers.append(f"package_sentinel_missing:{rel}")
        if not actual:
            component_blockers.append(f"submodule_commit_missing:{rel}")
        if expected and actual and expected != actual:
            component_blockers.append(f"submodule_pin_mismatch:{rel}")
        if status_commit and actual and status_commit != actual:
            component_blockers.append(f"submodule_status_commit_mismatch:{rel}")
        components[name] = WrapperSubmoduleIdentity(
            name=name,
            relative_path=rel,
            path=str(path),
            expected_commit=expected,
            actual_commit=actual,
            submodule_commit=status_commit,
            submodule_marker=marker,
            package_sentinel=sentinel,
            content_sha256=digest,
            blockers=component_blockers,
        )
    blockers.extend(blocker for component in components.values() for blocker in component.blockers)
    return WrapperIdentity(
        schema_version=WRAPPER_IDENTITY_SCHEMA,
        wrapper_path=str(wrapper_dir),
        wrapper_commit=wrapper_commit,
        wrapper_ref=wrapper_ref,
        deps_yaml_path=str(deps_path),
        deps_yaml_sha256=deps_sha,
        pins=pins,
        submodules=submodules,
        components=components,
        blockers=blockers,
    )


def runtime_module_provenance(wrapper_dir: Path, module_files: dict[str, str]) -> dict[str, Any]:
    wrapper_dir = wrapper_dir.resolve()
    expected_roots = {
        "zyphra_verl": wrapper_dir / "src",
        "verl": wrapper_dir / "third_party" / "verl",
        "nemo_gym": wrapper_dir / "third_party" / "nemo-gym",
    }
    modules: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for name, root in expected_roots.items():
        module_file = module_files.get(name, "")
        try:
            resolved = Path(module_file).resolve() if module_file else Path("")
            path_match = bool(module_file) and resolved.is_relative_to(root.resolve())
        except (OSError, RuntimeError, ValueError):
            path_match = False
        if not path_match:
            blockers.append(f"runtime_identity_mismatch:{name}")
        modules[name] = {
            "module_file": module_file,
            "expected_root": str(root),
            "path_match": path_match,
        }
    return {"schema_version": "bb.rl.phase4.runtime_module_provenance.v1", "modules": modules, "blockers": blockers, "passed": not blockers}
