from __future__ import annotations

import hashlib
import json

import pytest

from breadboard.rl.harness.history import (
    HistoricalEpisodeCorrupt,
    HistoricalEpisodeNotFound,
    HistoricalV1EpisodeReader,
)
from breadboard.rl.state.state_ref import ArtifactRef


class FrozenStore:
    def __init__(self, artifact_id: str, payload: bytes, *, digest: str | None = None) -> None:
        self.artifact_id = artifact_id
        self.payload = payload
        sha = digest or "sha256:" + hashlib.sha256(payload).hexdigest()
        self.ref = ArtifactRef(artifact_id, sha, len(payload), "application/json")

    def has(self, value: object) -> bool:
        return value == self.artifact_id

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        if artifact_id != self.artifact_id:
            raise KeyError(artifact_id)
        return self.ref

    def get_bytes(self, value: object) -> bytes:
        return self.payload


def _payload(episode_id: str, cleanup: str = "closed") -> bytes:
    return json.dumps({
        "schema_version": "bb.harness.episode.v1",
        "episode_id": episode_id,
        "create_fingerprint": "sha256:" + "a" * 64,
        "run_fingerprint": "sha256:" + "b" * 64,
        "sandbox_attestation": {"cleanup_state": cleanup},
        "cleanup_state": cleanup,
        "result": {"episode_id": episode_id, "reward": 1.0},
    }, sort_keys=True, separators=(",", ":")).encode()


@pytest.mark.asyncio
async def test_closed_v1_bytes_are_projected_without_reinterpretation_or_mutation() -> None:
    episode_id = "historical-episode"
    payload = _payload(episode_id)
    reader = HistoricalV1EpisodeReader(FrozenStore(f"{episode_id}:episode-tombstone", payload))
    episode = await reader.get(episode_id)
    ref, returned = await reader.artifact(episode_id)
    assert (episode.state, episode.reason, episode.payload) == ("closed", "", payload)
    assert returned == payload
    assert ref.artifact_id == f"{episode_id}:episode-tombstone"
    assert {name for name in dir(reader) if name in {"create", "run", "cancel", "close", "delete", "replay"}} == set()


@pytest.mark.asyncio
async def test_history_fails_closed_for_unknown_invalid_or_corrupt_identity() -> None:
    reader = HistoricalV1EpisodeReader(FrozenStore("other:episode-tombstone", _payload("other")))
    with pytest.raises(HistoricalEpisodeNotFound):
        await reader.get("../other")
    corrupt = HistoricalV1EpisodeReader(FrozenStore("other:episode-tombstone", _payload("wrong")))
    with pytest.raises(HistoricalEpisodeCorrupt):
        await corrupt.get("other")
    bad_digest = HistoricalV1EpisodeReader(FrozenStore("other:episode-tombstone", _payload("other"), digest="sha256:" + "0" * 64))
    with pytest.raises(HistoricalEpisodeCorrupt):
        await bad_digest.get("other")
