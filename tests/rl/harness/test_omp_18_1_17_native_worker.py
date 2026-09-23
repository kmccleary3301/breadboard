from __future__ import annotations

import pytest

from breadboard.rl.harness.omp_native_tools import NativeToolWorker, deny_excluded_capabilities, pinned_worker_spec


def test_native_worker_spec_binds_source_and_real_leaves() -> None:
    spec = pinned_worker_spec()
    metadata = spec.as_dict()
    assert metadata["commit"] == "3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec"
    assert "pi-natives EditStore/EditSession" in metadata["native_leaves"]
    assert "brush-core Shell" in metadata["native_leaves"]

    assert "native-tool-worker.ts" not in spec.as_dict().get("native_entrypoint", "")
def test_denial_happens_before_native_resolution() -> None:
    with pytest.raises(PermissionError):
        deny_excluded_capabilities({"path": "https://example.invalid"})
    with pytest.raises(PermissionError):
        deny_excluded_capabilities({"pty": True})


def test_worker_refuses_unpinned_offline_execution() -> None:
    worker = NativeToolWorker(cwd="/workspace/repo")
    assert worker.start().command[0].endswith("bun")
    with pytest.raises(RuntimeError):
        worker.execute("bash", {"command": "echo hi"})
    worker.stop()
