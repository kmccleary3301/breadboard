from __future__ import annotations

from breadboard.rl.state import InMemoryCAS, build_snapshot_manifest
from tests.rl.session.helpers import build_successful_toy_session


def test_snapshot_manifest_records_runtime_and_package_hash() -> None:
    session = build_successful_toy_session()
    snapshot = session.snapshot()
    cas = InMemoryCAS()
    artifact = cas.put_bytes(b"state-note", artifact_id="state-note")

    manifest = build_snapshot_manifest(
        snapshot=snapshot,
        package_hash=session.package.package_hash or "",
        runtime_backend=session.runtime.backend_id,
        artifact_refs=[artifact],
        event_ids=[event.event_id for event in session.events],
    )

    assert manifest.package_hash == session.package.package_hash
    assert manifest.runtime_backend == "local_process"
    assert manifest.state_ref.state_hash.startswith("sha256:")
    assert manifest.state_ref.artifact_refs[0].artifact_id == "state-note"
    assert manifest.event_ids
