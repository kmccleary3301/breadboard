from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import breadboard.product.evidence.replay.journal as replay_journal_module
from breadboard.product.evidence.replay import (
    ReplayCoordinator,
    ReplayExecution,
    ReplayJournal,
    ReplayManifest,
    ReplayManifestEntry,
    ReplayPlan,
    SandboxedReplayWorker,
    TapeReplayWorker,
)
from breadboard.product.evidence.replay.ipc import (
    decode_worker_request,
    decode_worker_response,
    encode_worker_response,
)
from breadboard.product.evidence.workspace import WorkspacePathError
from breadboard.product.integrations.host import SandboxHostAdapter
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore


def _tape(*, value: str = "stable") -> dict[str, Any]:
    return {
        "schema_version": "bb.replay_tape.v1",
        "steps": [
            {
                "kind": "provider.response",
                "span_id": "span-1",
                "parent_span_id": None,
                "payload": {"value": value},
            }
        ],
        "outputs": {"result.json": {"value": value}},
    }


def _plan(store: ArtifactStore, *, value: str = "stable") -> ReplayPlan:
    return ReplayPlan(
        source_session_id="session-r2",
        input_artifact=store.put_json(_tape(value=value)),
        worker_id=TapeReplayWorker.worker_id,
        manifest=ReplayManifest(
            (
                ReplayManifestEntry("result.json", "application/json"),
                ReplayManifestEntry("transcript.json", "application/json"),
            )
        ),
    )


class _CountingWorker(TapeReplayWorker):
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan: ReplayPlan, input_bytes: bytes):
        self.calls += 1
        return super().execute(plan, input_bytes)


class _CanonicalSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.calls: list[tuple[object, dict[str, Any]]] = []

    def get_workspace(self) -> str:
        return str(self.workspace)

    def execute(self, command: object, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((command, kwargs))
        plan, input_bytes = decode_worker_request(kwargs["stdin_data"])
        response = encode_worker_response(TapeReplayWorker().execute(plan, input_bytes))
        return {"exit_code": 0, "stdout": response, "stderr": b"", "orphaned": False}


class _FailingPublicationStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.armed = False
        self.publication_calls = 0

    def put(
        self,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
        created: set[ArtifactRef] | None = None,
    ) -> ArtifactRef:
        if self.armed and created is not None:
            self.publication_calls += 1
            if self.publication_calls == 2:
                raise OSError("injected publication failure")
        return super().put(content, media_type=media_type, created=created)


def _stored_digests(root: Path) -> set[str]:
    sha_root = root / "sha256"
    return (
        {path.name for prefix in sha_root.iterdir() for path in prefix.iterdir()}
        if sha_root.exists()
        else set()
    )


class _FailingCompletionSink:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.failed = False

    def append(self, event: Any) -> None:
        if event.state == "completed" and not self.failed:
            self.failed = True
            raise OSError("injected journal failure")
        self.events.append(event)


class _FailingCompletionJournal:
    def __init__(self) -> None:
        self.sink = _FailingCompletionSink()

    def try_read(self, plan_id: str) -> ReplayExecution | None:
        return (
            ReplayExecution.from_events(self.sink.events) if self.sink.events else None
        )

    def start(self, plan_id: str) -> _FailingCompletionSink:
        return self.sink


def test_completed_replay_survives_coordinator_restart_without_reexecution(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    journal = ReplayJournal(tmp_path / "workspace")
    plan = _plan(store)
    first_worker = _CountingWorker()
    first = ReplayCoordinator(store, first_worker, journal=journal).run(plan)

    second_worker = _CountingWorker()
    recovered = ReplayCoordinator(
        store, second_worker, journal=ReplayJournal(tmp_path / "workspace")
    ).run(plan)

    assert first.claimable is True
    assert recovered.disposition == "reused" and recovered.claimable is True
    assert recovered.execution is not None and first.execution is not None
    assert recovered.execution.as_dict() == first.execution.as_dict()
    assert first_worker.calls == 1 and second_worker.calls == 0


def test_isolated_worker_uses_empty_by_default_environment_and_redacts_durable_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SAFE_LOCALE", "C")
    monkeypatch.setenv("API_TOKEN", "inherited-secret")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = _CanonicalSandbox(workspace)
    host = SandboxHostAdapter("fixture", sandbox)
    worker = SandboxedReplayWorker(
        host,
        environment_names=("SAFE_LOCALE",),
        secret_values=("inherited-secret",),
    )
    store = ArtifactStore(tmp_path / "artifacts")
    plan = _plan(store, value=f"inherited-secret at {workspace.resolve()}")

    result = ReplayCoordinator(store, worker, journal=ReplayJournal(workspace)).run(
        plan
    )

    assert result.claimable is True
    command, call = sandbox.calls[0]
    assert isinstance(command, tuple) and command[-1].endswith("worker_entrypoint")
    assert call["env"] == {"SAFE_LOCALE": "C"}
    assert call["inherit_env"] is False and call["close_fds"] is True
    assert (
        call["shell"] is False
        and call["network"] == "none"
        and call["start_new_session"] is True
    )
    for ref in result.artifacts.values():
        content = store.read(ref)
        assert b"inherited-secret" not in content
        assert str(workspace.resolve()).encode() not in content
    assert (
        json.loads(store.read(result.artifacts["result.json"]))["value"]
        == "[REDACTED] at <workspace>"
    )
    for name in ("API_TOKEN", "AWS_ACCESS_KEY_ID"):
        with pytest.raises(ValueError, match="secret-bearing"):
            SandboxedReplayWorker(host, environment_names=(name,))


def test_journal_and_isolated_worker_deny_symlink_workspace_escape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".breadboard").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspacePathError, match="workspace-local"):
        ReplayJournal(workspace).start("sha256:" + "1" * 64)

    linked_workspace = tmp_path / "linked-workspace"
    linked_workspace.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(WorkspacePathError, match="symlink"):
        SandboxedReplayWorker(
            SandboxHostAdapter("linked", _CanonicalSandbox(linked_workspace))
        )


def test_transactional_publication_rolls_back_every_new_artifact(
    tmp_path: Path,
) -> None:
    store = _FailingPublicationStore(tmp_path / "artifacts")
    plan = _plan(store)
    before = _stored_digests(tmp_path / "artifacts")
    store.armed = True

    result = ReplayCoordinator(store, TapeReplayWorker()).run(plan)

    after = _stored_digests(tmp_path / "artifacts")
    assert result.execution is not None and result.execution.state == "failed"
    assert result.artifacts == {} and result.error == "OSError"
    assert after == before


def test_journal_failure_cannot_authoritatively_publish_prepared_cas_objects(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plan = _plan(store)
    journal = _FailingCompletionJournal()

    result = ReplayCoordinator(store, TapeReplayWorker(), journal=journal).run(plan)  # type: ignore[arg-type]

    assert result.execution is not None and result.execution.state == "failed"
    assert result.artifacts == {} and result.error == "OSError"
    assert [event.state for event in journal.sink.events] == [
        "planned",
        "admitted",
        "running",
        "failed",
    ]


def test_post_link_fsync_error_reconciles_completed_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    workspace = tmp_path / "workspace"
    journal = ReplayJournal(workspace)
    plan = _plan(store)
    real_sync = replay_journal_module._sync_directory
    event_syncs = 0

    def fail_completed_sync(path: Path) -> None:
        nonlocal event_syncs
        if path.name == "events":
            event_syncs += 1
            if event_syncs == 4:
                raise OSError("injected completion fsync failure")
        real_sync(path)

    monkeypatch.setattr(replay_journal_module, "_sync_directory", fail_completed_sync)
    result = ReplayCoordinator(store, TapeReplayWorker(), journal=journal).run(plan)
    recovered = ReplayCoordinator(
        store, TapeReplayWorker(), journal=ReplayJournal(workspace)
    ).run(plan)

    assert result.claimable is True and result.execution is not None
    assert result.execution.state == "completed"
    assert recovered.claimable is True and recovered.disposition == "reused"


def test_journal_startup_sync_failure_cleans_partial_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    journal = ReplayJournal(tmp_path / "workspace")
    plan_id = "sha256:" + "3" * 64
    real_sync = replay_journal_module._sync_directory
    failed = False

    def fail_once(path: Path) -> None:
        nonlocal failed
        if not failed and path == journal.run_path(plan_id):
            failed = True
            raise OSError("injected startup fsync failure")
        real_sync(path)

    monkeypatch.setattr(replay_journal_module, "_sync_directory", fail_once)
    with pytest.raises(OSError, match="startup fsync"):
        journal.start(plan_id)
    assert not journal.run_path(plan_id).exists()

    journal.start(plan_id)


def test_journal_publication_is_no_replace(tmp_path: Path) -> None:
    journal = ReplayJournal(tmp_path / "workspace")
    plan_id = "sha256:" + "2" * 64
    journal.start(plan_id)
    with pytest.raises(FileExistsError):
        journal.start(plan_id)


@pytest.mark.skipif(
    os.name == "nt", reason="process-group cancellation uses POSIX signals"
)
def test_cancellation_contract_terminates_process_group_without_orphan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cancelled = threading.Event()

    class CancelingSandbox:
        child_pid: int | None = None
        parent_pid: int | None = None
        started = threading.Event()

        def get_workspace(self) -> str:
            return str(workspace)

        def execute(self, command: object, **kwargs: Any) -> dict[str, Any]:
            code = (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "print(child.pid, flush=True); time.sleep(60)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={},
                close_fds=True,
                start_new_session=True,
            )
            self.parent_pid = process.pid
            assert process.stdout is not None
            self.child_pid = int(process.stdout.readline().strip())
            self.started.set()
            assert kwargs["cancelled"].wait(5)
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=kwargs["cancellation_grace_seconds"])
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            deadline = time.monotonic() + 5
            orphaned = True
            while time.monotonic() < deadline:
                try:
                    os.kill(self.child_pid, 0)
                except ProcessLookupError:
                    orphaned = False
                    break
                time.sleep(0.01)
            return {
                "exit_code": 130,
                "stdout": b"",
                "stderr": b"",
                "cancelled": True,
                "orphaned": orphaned,
            }

    sandbox = CancelingSandbox()
    worker = SandboxedReplayWorker(
        SandboxHostAdapter("cancel", sandbox),
        cancelled=cancelled,
        timeout_seconds=10,
        cancellation_grace_seconds=0.5,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    plan = _plan(store)
    result_holder: list[Any] = []
    thread = threading.Thread(
        target=lambda: result_holder.append(ReplayCoordinator(store, worker).run(plan))
    )
    thread.start()
    assert sandbox.started.wait(5)
    cancelled.set()
    thread.join(10)

    assert not thread.is_alive()
    result = result_holder[0]
    assert result.execution is not None and result.execution.state == "canceled"
    assert sandbox.parent_pid is not None and sandbox.child_pid is not None
    for pid in (sandbox.parent_pid, sandbox.child_pid):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_canonical_ipc_rejects_noncanonical_and_duplicate_envelopes() -> None:
    with pytest.raises(ValueError, match="canonical"):
        decode_worker_response(b'{"status":"ok", "status":"ok"}')
