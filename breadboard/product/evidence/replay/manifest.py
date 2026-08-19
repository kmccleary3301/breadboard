from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from breadboard.product.runtime.artifacts import ArtifactRef

def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("replay manifest path must be a portable relative POSIX path")
    path = PurePosixPath(value)
    if not path.parts or str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("replay manifest path must be canonical and contained")
    return value


@dataclass(frozen=True, slots=True)
class ReplayManifestEntry:
    path: str
    media_type: str

    def __post_init__(self) -> None:
        _safe_relative_path(self.path)
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ValueError("replay manifest media_type must be populated")

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "media_type": self.media_type}


@dataclass(frozen=True, slots=True)
class ReplayManifest:
    entries: tuple[ReplayManifestEntry, ...]
    schema_version: str = "bb.replay_manifest.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "bb.replay_manifest.v1":
            raise ValueError("unsupported replay manifest schema_version")
        entries = tuple(self.entries)
        if not entries:
            raise ValueError("replay manifest requires at least one artifact")
        if any(not isinstance(entry, ReplayManifestEntry) for entry in entries):
            raise TypeError("replay manifest entries must be ReplayManifestEntry values")
        paths = [entry.path for entry in entries]
        if len(paths) != len(set(paths)):
            raise ValueError("replay manifest paths must be unique")
        object.__setattr__(self, "entries", tuple(sorted(entries, key=lambda entry: entry.path)))

    @property
    def media_types(self) -> Mapping[str, str]:
        return MappingProxyType({entry.path: entry.media_type for entry in self.entries})

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(entry.path for entry in self.entries)

    def validate_artifacts(self, artifacts: Mapping[str, ArtifactRef]) -> None:
        if set(artifacts) != self.paths:
            raise ValueError("replay artifacts do not match the immutable manifest")
        for path, ref in artifacts.items():
            _safe_relative_path(path)
            if not isinstance(ref, ArtifactRef) or ref.media_type != self.media_types[path]:
                raise ValueError("replay artifact media type does not match the manifest")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifacts": [entry.as_dict() for entry in self.entries],
        }
