from __future__ import annotations

import pytest

from breadboard.rl.state import InMemoryCAS


def test_cas_refs_are_hash_addressed_and_retrievable() -> None:
    cas = InMemoryCAS()
    ref = cas.put_bytes(b"hello", media_type="text/plain")

    assert ref.sha256.startswith("sha256:")
    assert ref.size_bytes == 5
    assert cas.has(ref)
    assert cas.get_bytes(ref) == b"hello"


def test_cas_rejects_overwrite_for_existing_artifact_id() -> None:
    cas = InMemoryCAS()
    cas.put_bytes(b"first", artifact_id="artifact-1")

    with pytest.raises(ValueError, match="overwrite rejected"):
        cas.put_bytes(b"second", artifact_id="artifact-1")
