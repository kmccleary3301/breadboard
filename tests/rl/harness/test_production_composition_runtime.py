from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
from builtins import BaseExceptionGroup

import pytest
import uvicorn

from breadboard.rl.harness import __main__ as harness_main
from breadboard.rl.harness.composition import (
    ProductionComposition,
    _CASMaterializationSourceReader,
    _DirectoryIdentityGuard,
    _measure_installed_runtime,
    _PinnedDirectoryStorageBackend,
)
from breadboard.rl.harness.contracts import RuntimeClass
from breadboard.rl.harness.sandbox import InstalledRuntime
from breadboard.rl.state.cas import FilesystemCAS


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_composition_retries_failed_runtime_cleanup_before_authorities() -> None:
    calls: list[str] = []
    backend_attempts = 0

    async def close_backend() -> None:
        nonlocal backend_attempts
        backend_attempts += 1
        calls.append(f"backend:{backend_attempts}")
        if backend_attempts == 1:
            raise RuntimeError("runtime cleanup pending")

    composition = ProductionComposition(
        app=None,
        service=None,
        server=None,
        manifest=None,
        manifest_ref=None,
        authority_graph=None,
        bridge_lifecycle=None,
        cleanup_probe=None,
        runtime_close_callbacks=(
            lambda: calls.append("owner"),
            lambda: calls.append("adapter"),
            close_backend,
        ),
        authority_close_callbacks=(lambda: calls.append("authority"),),
    )

    with pytest.raises(BaseExceptionGroup, match="production composition close failed"):
        await composition.close()
    assert calls == ["backend:1"]
    assert not composition._runtime_closed
    assert not composition._closed

    await composition.close()

    assert calls == ["backend:1", "backend:2", "adapter", "owner", "authority"]
    assert composition._runtime_closed
    assert composition._closed


def test_cas_materialization_reader_uses_only_digest_bound_shared_cas(tmp_path) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    member = b"installed source bytes\n"
    member_digest = _digest(member)
    cas.put_bytes(member, artifact_id=member_digest)
    source_digest = "sha256:" + "a" * 64
    document = {
        "entries": [
            {
                "bytes": len(member),
                "digest": member_digest,
                "kind": "file",
                "mode": 0o400,
                "path": "input.txt",
            }
        ],
        "media_type": "application/vnd.breadboard.sealed-source+json;version=1",
        "schema_version": "bb.rl.sealed-source.v1",
        "source_digest": source_digest,
        "total_bytes": len(member),
        "total_files": 1,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    cas.put_bytes(canonical, artifact_id=source_digest)

    reader = _CASMaterializationSourceReader(cas)
    manifest = reader.load_manifest(source_digest, max_bytes=len(canonical))

    assert manifest.source_digest == source_digest
    assert reader.read_member(source_digest, "input.txt", max_bytes=len(member)) == member
    with pytest.raises(ValueError, match="not admitted"):
        reader.read_member(source_digest, "unknown.txt", max_bytes=len(member))
    cas.close()


def test_cas_materialization_reader_rejects_noncanonical_manifest(tmp_path) -> None:
    cas = FilesystemCAS(tmp_path / "cas")
    source_digest = "sha256:" + "b" * 64
    noncanonical = b'{"schema_version": "bb.rl.sealed-source.v1"}'
    cas.put_bytes(noncanonical, artifact_id=source_digest)

    with pytest.raises(ValueError, match="not canonical"):
        _CASMaterializationSourceReader(cas).load_manifest(
            source_digest, max_bytes=len(noncanonical)
        )
    cas.close()


def test_installed_runtime_is_measured_before_app_construction(tmp_path) -> None:
    executable = tmp_path / "runtime"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o500)
    runtime = InstalledRuntime(
        runtime_id="trusted-runtime",
        runtime_class=RuntimeClass.TRUSTED_PROCESS,
        driver_implementation_digest="sha256:" + "1" * 64,
        executable_path=str(executable),
        measured_binary_digest=_digest(executable.read_bytes()),
        oci_runtime_name="",
        supported_platform_versions=("v1",),
    )
    _measure_installed_runtime(runtime)

    executable.chmod(0o700)
    executable.write_bytes(b"changed")
    with pytest.raises(ValueError, match="executable authority mismatch"):
        _measure_installed_runtime(runtime)


def test_workspace_swap_is_rejected_before_path_mutation(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    descriptor = os.open(workspace, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    guard = _DirectoryIdentityGuard(descriptor, str(workspace), "workspace")
    backend = _PinnedDirectoryStorageBackend(guard)
    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir(mode=0o700)
    try:
        with pytest.raises(ValueError, match="directory authority changed"):
            backend.allocate(workspace_id="must-not-exist", root=workspace, max_bytes=1)
        assert not (workspace / "must-not-exist").exists()
        assert not (original / "must-not-exist").exists()
    finally:
        os.close(descriptor)


@pytest.mark.asyncio
async def test_lifecycle_server_starts_service_shutdown_once_and_forces_second_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    def forbidden_super_handle_exit(self, sig, frame) -> None:
        del self, sig, frame
        raise AssertionError("base signal handler records and replays the signal")

    monkeypatch.setattr(
        uvicorn.Server,
        "handle_exit",
        forbidden_super_handle_exit,
    )

    async def app(scope, receive, send) -> None:
        del scope, receive, send

    async def shutdown() -> None:
        nonlocal calls
        calls += 1

    server = harness_main._LifecycleServer(
        uvicorn.Config(app, lifespan="off", log_config=None),
        shutdown,
    )
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit is True
    assert server.force_exit is False
    assert server._captured_signals == []
    assert server._service_shutdown_task is not None
    await server._service_shutdown_task

    server.handle_exit(signal.SIGTERM, None)
    assert server.force_exit is True
    assert server._captured_signals == []
    assert calls == 1


@pytest.mark.asyncio
async def test_lifecycle_server_watcher_closes_before_serve_returns_without_task_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown_entered = asyncio.Event()
    shutdown_release = asyncio.Event()

    async def app(scope, receive, send) -> None:
        del scope, receive, send

    async def shutdown() -> None:
        shutdown_entered.set()
        await shutdown_release.wait()

    async def fake_serve(self, sockets=None) -> None:
        del sockets
        self.should_exit = True
        await shutdown_entered.wait()
        assert self._service_shutdown_task is not None
        assert self._service_shutdown_task.done() is False
        shutdown_release.set()

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    server = harness_main._LifecycleServer(
        uvicorn.Config(app, lifespan="off", log_config=None),
        shutdown,
    )

    await asyncio.wait_for(server.serve(), 1)

    assert server._service_shutdown_task is not None
    assert server._service_shutdown_task.done() is True
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_coro().__qualname__.endswith("_watch_for_exit")
    ]
@pytest.mark.asyncio
async def test_lifecycle_server_immediate_serve_return_still_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def app(scope, receive, send) -> None:
        del scope, receive, send

    async def shutdown() -> None:
        nonlocal calls
        calls += 1

    async def fake_serve(self, sockets=None) -> None:
        del sockets
        self.should_exit = True

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    server = harness_main._LifecycleServer(
        uvicorn.Config(app, lifespan="off", log_config=None),
        shutdown,
    )

    await server.serve()

    assert calls == 1
    assert server._service_shutdown_task is not None
    assert server._service_shutdown_task.done() is True




@pytest.mark.asyncio
async def test_lifecycle_server_cancellation_waits_for_owned_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve_entered = asyncio.Event()
    shutdown_entered = asyncio.Event()
    shutdown_release = asyncio.Event()

    async def app(scope, receive, send) -> None:
        del scope, receive, send

    async def shutdown() -> None:
        shutdown_entered.set()
        await shutdown_release.wait()

    async def fake_serve(self, sockets=None) -> None:
        del self, sockets
        serve_entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    server = harness_main._LifecycleServer(
        uvicorn.Config(app, lifespan="off", log_config=None),
        shutdown,
    )
    serve_task = asyncio.create_task(server.serve())
    await serve_entered.wait()

    serve_task.cancel()
    await shutdown_entered.wait()
    await asyncio.sleep(0)
    assert serve_task.done() is False

    shutdown_release.set()
    with pytest.raises(asyncio.CancelledError):
        await serve_task
    assert server._service_shutdown_task is not None
    assert server._service_shutdown_task.done() is True


def test_cli_run_emits_only_secret_free_result_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def run(
        _path: str,
        *,
        secret_files: dict[str, str],
        provider_credentials: dict[str, str],
    ) -> dict[str, object]:
        assert secret_files == {"composition": "/secrets/composition"}
        assert provider_credentials == {"provider": "/secrets/provider"}
        return {
            "schema_version": "bb.rl.headless-result.v1",
            "episode_id": "episode-one",
            "config_digest": "sha256:" + "a" * 64,
            "terminal": {"status": "succeeded"},
        }

    monkeypatch.setattr(harness_main, "run_headless_request_file", run)

    result = harness_main.main(
        [
            "run",
            "--request",
            "/request.json",
            "--secret-file",
            "composition=/secrets/composition",
            "--provider-credential-file",
            "provider=/secrets/provider",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {
        "schema_version": "bb.rl.headless-result.v1",
        "episode_id": "episode-one",
        "config_digest": "sha256:" + "a" * 64,
        "status": "succeeded",
    }
    assert captured.err == ""


def test_cli_sanitizes_runtime_failure_without_exposing_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_load(ref, bindings):
        del ref, bindings
        raise BaseExceptionGroup(
            "secret /private/runtime/path",
            [RuntimeError("token super-secret")],
        )

    monkeypatch.setattr(harness_main, "load_production_composition", fail_load)

    result = harness_main.main(
        ["inspect", "--composition-ref", "/composition/ref"]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "composition runtime failed\n"


@pytest.mark.asyncio
async def test_lifecycle_server_preserves_cancellation_and_shutdown_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve_entered = asyncio.Event()
    shutdown_entered = asyncio.Event()
    shutdown_release = asyncio.Event()

    async def app(scope, receive, send) -> None:
        del scope, receive, send

    async def shutdown() -> None:
        shutdown_entered.set()
        await shutdown_release.wait()
        raise RuntimeError("shutdown failed")

    async def fake_serve(self, sockets=None) -> None:
        del self, sockets
        serve_entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    server = harness_main._LifecycleServer(
        uvicorn.Config(app, lifespan="off", log_config=None),
        shutdown,
    )
    serve_task = asyncio.create_task(server.serve())
    await serve_entered.wait()
    serve_task.cancel()
    await shutdown_entered.wait()
    shutdown_release.set()

    with pytest.raises(BaseExceptionGroup) as caught:
        await serve_task

    assert any(
        isinstance(exc, asyncio.CancelledError)
        for exc in caught.value.exceptions
    )
    assert any(
        isinstance(exc, RuntimeError)
        for exc in caught.value.exceptions
    )


def test_cli_does_not_swallow_standalone_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exit_load(ref, bindings, **kwargs):
        del ref, bindings, kwargs
        raise SystemExit(17)

    monkeypatch.setattr(harness_main, "load_production_composition", exit_load)

    with pytest.raises(SystemExit) as caught:
        harness_main.main(["inspect", "--composition-ref", "/composition/ref"])

    assert caught.value.code == 17


@pytest.mark.asyncio
async def test_lifecycle_server_propagates_early_service_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ShutdownFailure(RuntimeError):
        pass

    async def app(scope, receive, send) -> None:
        del scope, receive, send

    async def shutdown() -> None:
        raise ShutdownFailure("service shutdown failed")

    async def fake_serve(self, sockets=None) -> None:
        del sockets
        self.should_exit = True
        await asyncio.sleep(0.02)

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    server = harness_main._LifecycleServer(
        uvicorn.Config(app, lifespan="off", log_config=None),
        shutdown,
    )

    with pytest.raises(ShutdownFailure, match="service shutdown failed"):
        await server.serve()
