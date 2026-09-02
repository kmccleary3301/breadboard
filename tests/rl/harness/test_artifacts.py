from __future__ import annotations

from pathlib import Path

import pytest
from breadboard.artifacts.cas import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    FilesystemCAS,
)






def test_filesystem_cas_survives_reopen_and_rejects_mutating_an_artifact_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cas"
    first_store = FilesystemCAS(root)
    first_ref = first_store.put_bytes(
        b"stable artifact bytes",
        artifact_id="episode-7:trajectory",
        media_type="application/json",
        metadata={"kind": "trajectory", "episode_id": "episode-7"},
    )

    reopened_store = FilesystemCAS(root)
    reopened_ref = reopened_store.get_ref("episode-7:trajectory")

    assert reopened_ref == first_ref
    assert reopened_store.get_bytes(reopened_ref) == b"stable artifact bytes"
    with pytest.raises(ArtifactConflictError, match="CAS artifact overwrite rejected"):
        reopened_store.put_bytes(b"different bytes", artifact_id="episode-7:trajectory")


def test_filesystem_cas_detects_corrupted_persisted_blob_bytes(tmp_path: Path) -> None:
    root = tmp_path / "cas"
    store = FilesystemCAS(root)
    ref = store.put_bytes(b"verified payload", artifact_id="artifact-with-integrity")
    blob_path = root / "blobs" / ref.sha256.removeprefix("sha256:")
    blob_path.write_bytes(b"tampered payload")

    with pytest.raises(
        ArtifactIntegrityError, match="CAS artifact integrity check failed"
    ):
        FilesystemCAS(root).get_bytes("artifact-with-integrity")


