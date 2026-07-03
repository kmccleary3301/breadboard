"""State references and content-addressed storage primitives."""

from breadboard.rl.state.cas import InMemoryCAS
from breadboard.rl.state.snapshot import SnapshotManifest, build_snapshot_manifest
from breadboard.rl.state.state_ref import ArtifactRef, StateRef

__all__ = [
    "ArtifactRef",
    "InMemoryCAS",
    "SnapshotManifest",
    "StateRef",
    "build_snapshot_manifest",
]
