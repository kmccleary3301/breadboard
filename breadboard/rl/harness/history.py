from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.artifacts.references import ArtifactRef

_HISTORICAL_SCHEMA = "bb.harness.episode.v1"
_EPISODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_MAX_TOMBSTONE_BYTES = 64 * 1024 * 1024


class HistoricalEpisodeNotFound(LookupError):
    pass


class HistoricalEpisodeCorrupt(RuntimeError):
    pass


class HistoricalArtifactStore(Protocol):
    def has(self, artifact_ref: ArtifactRef | str) -> bool: ...
    def get_ref(self, artifact_id: str) -> ArtifactRef: ...
    def get_bytes(self, artifact_ref: ArtifactRef | str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class HistoricalV1Episode:
    episode_id: str
    state: str
    reason: str
    artifact_id: str
    payload: bytes


class HistoricalV1EpisodeReader:
    """Read-only projection over immutable V1 episode tombstones.

    This owner cannot create, run, cancel, close, replay, or reinterpret an episode.
    It accepts only the two historical tombstone object names emitted by V1.
    """

    def __init__(self, store: HistoricalArtifactStore | None = None) -> None:
        if store is None:
            configured = os.environ.get("BREADBOARD_HARNESS_ARTIFACT_ROOT")
            artifact_root = (
                Path(configured).expanduser()
                if configured
                else Path.home() / ".breadboard" / "rl-harness" / "artifacts"
            )
            store = FilesystemCAS(artifact_root)
        self._store = store

    async def get(self, episode_id: str) -> HistoricalV1Episode:
        if _EPISODE_ID.fullmatch(episode_id) is None:
            raise HistoricalEpisodeNotFound(episode_id)
        candidates = (
            (f"{episode_id}:episode-tombstone", "closed"),
            (f"{episode_id}:episode-tombstone-completed", "active"),
        )
        for artifact_id, expected_cleanup in candidates:
            if not await asyncio.to_thread(self._store.has, artifact_id):
                continue
            try:
                ref = await asyncio.to_thread(self._store.get_ref, artifact_id)
                raw = await asyncio.to_thread(self._store.get_bytes, ref)
                digest = "sha256:" + hashlib.sha256(raw).hexdigest()
                if (
                    len(raw) > _MAX_TOMBSTONE_BYTES
                    or ref.size_bytes != len(raw)
                    or ref.sha256 != digest
                ):
                    raise ValueError("historical tombstone integrity mismatch")
                payload = json.loads(raw)
                if not isinstance(payload, Mapping):
                    raise ValueError("historical tombstone must be an object")
                cleanup = payload.get("cleanup_state") or expected_cleanup
                if (
                    payload.get("schema_version") != _HISTORICAL_SCHEMA
                    or payload.get("episode_id") != episode_id
                    or cleanup not in {"active", "closed"}
                    or not isinstance(payload.get("result"), Mapping)
                ):
                    raise ValueError("historical tombstone identity is invalid")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise HistoricalEpisodeCorrupt(episode_id) from exc
            return HistoricalV1Episode(
                episode_id=episode_id,
                state="closed" if cleanup == "closed" else "completed",
                reason="" if cleanup == "closed" else "sandbox cleanup is not confirmed",
                artifact_id=artifact_id,
                payload=raw,
            )
        raise HistoricalEpisodeNotFound(episode_id)

    async def artifact(self, episode_id: str) -> tuple[ArtifactRef, bytes]:
        episode = await self.get(episode_id)
        ref = await asyncio.to_thread(self._store.get_ref, episode.artifact_id)
        return ref, episode.payload


__all__ = [
    "HistoricalEpisodeCorrupt",
    "HistoricalEpisodeNotFound",
    "HistoricalV1Episode",
    "HistoricalV1EpisodeReader",
]
