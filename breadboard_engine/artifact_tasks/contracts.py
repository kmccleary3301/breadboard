from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def safe_relative_path(value: str | Path, *, field_name: str = "path") -> Path:
    raw = _require_text(value, field_name)
    path = Path(raw)
    if path.is_absolute():
        raise ValueError(f"{field_name} must be relative: {raw}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field_name} must not contain empty, current, or parent traversal parts: {raw}")
    return path


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactRequirement:
    path: str
    min_bytes: int | None = None
    max_bytes: int | None = None
    language: str | None = None
    required: bool = True
    sha256: str | None = None
    allow_overwrite: bool = False
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(safe_relative_path(self.path)))
        if self.min_bytes is not None and int(self.min_bytes) < 0:
            raise ValueError("min_bytes must be non-negative")
        if self.max_bytes is not None and int(self.max_bytes) < 0:
            raise ValueError("max_bytes must be non-negative")
        if self.min_bytes is not None and self.max_bytes is not None and int(self.min_bytes) > int(self.max_bytes):
            raise ValueError("min_bytes must be <= max_bytes")
        object.__setattr__(self, "min_bytes", int(self.min_bytes) if self.min_bytes is not None else None)
        object.__setattr__(self, "max_bytes", int(self.max_bytes) if self.max_bytes is not None else None)
        object.__setattr__(self, "language", str(self.language).strip() if self.language else None)
        object.__setattr__(self, "sha256", str(self.sha256).strip().lower() if self.sha256 else None)
        object.__setattr__(self, "description", str(self.description).strip() if self.description else None)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "path": self.path,
            "required": self.required,
            "allow_overwrite": self.allow_overwrite,
        }
        if self.min_bytes is not None:
            payload["min_bytes"] = self.min_bytes
        if self.max_bytes is not None:
            payload["max_bytes"] = self.max_bytes
        if self.language:
            payload["language"] = self.language
        if self.sha256:
            payload["sha256"] = self.sha256
        if self.description:
            payload["description"] = self.description
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ArtifactRequirement":
        return ArtifactRequirement(
            path=str(data.get("path") or ""),
            min_bytes=data.get("min_bytes"),
            max_bytes=data.get("max_bytes"),
            language=data.get("language"),
            required=bool(data.get("required", True)),
            sha256=data.get("sha256"),
            allow_overwrite=bool(data.get("allow_overwrite", False)),
            description=data.get("description"),
        )


@dataclass(frozen=True)
class ArtifactContract:
    requirements: Sequence[ArtifactRequirement | Mapping[str, Any]]
    mode: str = "response_materialize"
    completion_policy: str = "all_required"
    workspace_root: str | None = None
    artifact_root: str | None = None

    def __post_init__(self) -> None:
        requirements = tuple(
            item if isinstance(item, ArtifactRequirement) else ArtifactRequirement.from_dict(item)
            for item in self.requirements
        )
        if not requirements:
            raise ValueError("requirements must contain at least one artifact requirement")
        paths = [item.path for item in requirements]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact requirement paths must be unique")
        mode = _require_text(self.mode, "mode")
        if mode not in {"response_materialize", "file_edit", "external_import"}:
            raise ValueError("mode must be one of: response_materialize, file_edit, external_import")
        policy = _require_text(self.completion_policy, "completion_policy")
        if policy != "all_required":
            raise ValueError("only all_required completion_policy is supported")
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "completion_policy", policy)
        object.__setattr__(self, "workspace_root", str(self.workspace_root) if self.workspace_root else None)
        object.__setattr__(self, "artifact_root", str(self.artifact_root) if self.artifact_root else None)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "requirements": [item.to_dict() for item in self.requirements],
            "mode": self.mode,
            "completion_policy": self.completion_policy,
        }
        if self.workspace_root:
            payload["workspace_root"] = self.workspace_root
        if self.artifact_root:
            payload["artifact_root"] = self.artifact_root
        return payload


@dataclass(frozen=True)
class ArtifactCheck:
    path: str
    required: bool
    exists: bool
    ok: bool
    size_bytes: int | None = None
    sha256: str | None = None
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "path": self.path,
            "required": self.required,
            "exists": self.exists,
            "ok": self.ok,
            "failure_reasons": list(self.failure_reasons),
        }
        if self.size_bytes is not None:
            payload["size_bytes"] = self.size_bytes
        if self.sha256:
            payload["sha256"] = self.sha256
        return payload


@dataclass(frozen=True)
class ArtifactValidationResult:
    ok: bool
    checks: tuple[ArtifactCheck, ...]
    artifact_root: str
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "artifact_root": self.artifact_root,
            "failure_reasons": list(self.failure_reasons),
            "checks": [item.to_dict() for item in self.checks],
        }


def _artifact_root(contract: ArtifactContract, root: Path | None) -> Path:
    if root is not None:
        return root.resolve()
    if contract.artifact_root:
        return Path(contract.artifact_root).resolve()
    if contract.workspace_root:
        return Path(contract.workspace_root).resolve()
    return Path.cwd().resolve()


def validate_artifact_contract(contract: ArtifactContract, *, root: Path | None = None) -> ArtifactValidationResult:
    root_path = _artifact_root(contract, root)
    checks: list[ArtifactCheck] = []
    aggregate_failures: list[str] = []
    for requirement in contract.requirements:
        rel_path = safe_relative_path(requirement.path)
        full_path = (root_path / rel_path).resolve()
        try:
            full_path.relative_to(root_path)
        except ValueError:
            raise ValueError(f"artifact path escapes root: {requirement.path}") from None

        failures: list[str] = []
        exists = full_path.exists()
        size_bytes: int | None = None
        digest: str | None = None
        if not exists:
            if requirement.required:
                failures.append("missing_required_artifact")
        elif not full_path.is_file():
            failures.append("artifact_is_not_a_regular_file")
        elif full_path.is_symlink():
            failures.append("artifact_is_symlink")
        else:
            size_bytes = full_path.stat().st_size
            digest = hash_file(full_path)
            if requirement.min_bytes is not None and size_bytes < requirement.min_bytes:
                failures.append("artifact_below_min_bytes")
            if requirement.max_bytes is not None and size_bytes > requirement.max_bytes:
                failures.append("artifact_above_max_bytes")
            if requirement.sha256 and digest != requirement.sha256:
                failures.append("artifact_sha256_mismatch")

        ok = not failures
        if requirement.required and failures:
            aggregate_failures.extend(f"{requirement.path}:{failure}" for failure in failures)
        checks.append(
            ArtifactCheck(
                path=requirement.path,
                required=requirement.required,
                exists=exists,
                ok=ok,
                size_bytes=size_bytes,
                sha256=digest,
                failure_reasons=tuple(failures),
            )
        )
    required_failures = [check for check in checks if check.required and not check.ok]
    return ArtifactValidationResult(
        ok=not required_failures,
        checks=tuple(checks),
        artifact_root=str(root_path),
        failure_reasons=tuple(aggregate_failures),
    )
