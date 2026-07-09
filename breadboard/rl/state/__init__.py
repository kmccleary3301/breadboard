"""State references and content-addressed storage primitives."""

from breadboard.rl.state.cas import (
    ArtifactIntegrityError,
    ArtifactStoreError,
    FilesystemCAS,
    InMemoryCAS,
)
from breadboard.rl.state.snapshot import SnapshotManifest, build_snapshot_manifest
from breadboard.rl.state.state_ref import ArtifactRef, StateRef

__all__ = [
    "ArtifactRef",
    "ArtifactIntegrityError",
    "ArtifactStoreError",
    "FilesystemCAS",
    "InMemoryCAS",
    "SnapshotManifest",
    "StateRef",
    "build_snapshot_manifest",
]
