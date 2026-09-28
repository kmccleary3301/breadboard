from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard.rl.harness.sandbox import InstalledToolAdapter, TrustedProcessHandle


@pytest.mark.asyncio
async def test_repeated_native_tool_snapshots_executable_once_and_closes_on_terminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = object.__new__(TrustedProcessHandle)
    handle._executable = SimpleNamespace(proc_fd_path="/proc/self/fd/71", close=lambda: None)
    handle._command_executable = None
    handle._workspace_fd = -1
    handle._groups = {}
    handle._launch_lock = asyncio.Lock()
    handle._closed = False
    handle._closing = False
    handle.lease_id = "lease-test"
    handle._native_executables = {}

    snapshot_calls: list[tuple[str, str | None]] = []

    class FakePinned:
        def __init__(self, fd: int) -> None:
            self.fd = fd
            self.proc_fd_path = f"/proc/self/fd/{fd}"
            self.closed = False

        def close(self) -> None:
            self.closed = True

    pinned_node = FakePinned(88)

    def fake_snapshot(path: str, expected_digest: str | None) -> FakePinned:
        snapshot_calls.append((path, expected_digest))
        return pinned_node

    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox._snapshot_installed_executable",
        fake_snapshot,
    )
    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox._validate_native_root",
        lambda binding: None,
    )
    monkeypatch.setattr(
        "breadboard.rl.harness.sandbox._measure_native_file",
        lambda path, expected: None,
    )

    passed_fds: list[tuple[int, ...]] = []

    async def fake_run_pinned_argv(
        argv: tuple[str, ...],
        *,
        timeout_ms: int,
        output_limit: int,
        input_bytes: bytes,
        extra_fds: tuple[int, ...],
    ) -> dict[str, object]:
        passed_fds.append(extra_fds)
        return {"returncode": 0, "stdout": '{"result": "ok"}', "stderr": ""}

    handle._run_pinned_argv = fake_run_pinned_argv  # type: ignore[method-assign]

    binding = InstalledToolAdapter(
        adapter_id="adapter-test",
        tool_ids=("native-tool",),
        runtime_root_path=str(tmp_path),
        runtime_root_device=1,
        runtime_root_inode=1,
        runtime_root_owner_uid=0,
        runtime_root_mode="0755",
        manifest_digest="sha256:" + "0" * 64,
        executable_relative_path="bin/node",
        entrypoint_relative_path="index.js",
        executable_digest="sha256:" + "1" * 64,
        entrypoint_digest="sha256:" + "2" * 64,
    )

    for action in (b'{"action":"first"}', b'{"action":"second"}'):
        assert await handle.run_native_tool(
            binding, "native-tool", action, timeout_ms=1000, output_limit=1000
        ) == {"result": "ok"}
    assert snapshot_calls == [(str(tmp_path / "bin/node"), binding.executable_digest)]
    assert passed_fds == [(88,), (88,)]
    assert not pinned_node.closed

    await handle.terminate()
    assert pinned_node.closed
