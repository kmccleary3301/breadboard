"""Regression locks for owned groups and root-owned active child conductors."""
import os
import signal
import subprocess
import threading
import time

import pytest

from breadboard_engine.execution.codex_exec import CodexExecManager, _Process, codex_runtime, execute_codex_tool
from breadboard_engine.compaction_lanes.codex_lane import EngineMockProvider
from tests.compaction.test_codex_agent_tools import owner, value


def _running(pid):
    # A killed orphan can briefly remain a zombie until its system parent reaps it.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                           capture_output=True, text=True, check=False).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _assert_dead(pid):
    deadline = time.monotonic() + 5
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _running(pid), f"owned process {pid} is still running"


def test_redirected_descendant_is_killed_after_leader_exit_and_reader_eof():
    # This exact sequence previously set `closed` and skipped killpg entirely.
    leader = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 120 >/dev/null 2>&1 & printf '%s\n' $!"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    entry = _Process(leader, False, leader.stdout, None, time.monotonic())
    descendant = None
    try:
        entry.reader_thread = threading.Thread(target=entry.read_background, daemon=True)
        entry.reader_thread.start()
        assert leader.wait(timeout=5) == 0
        entry.reader_thread.join(timeout=5)
        assert not entry.reader_thread.is_alive()
        assert entry.closed and entry.reader_closed
        descendant = int(bytes(entry.output).strip())
        assert _running(descendant)
        entry.terminate()
        entry.terminate()  # never signal a reused group after relinquishing ownership
        assert entry.terminated
        _assert_dead(descendant)
    finally:
        # Cleanup remains unconditional even if an assertion exposes the regression.
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        entry.terminate()


def test_root_close_cancels_active_real_child_and_exec_sessions(owner, tmp_path, monkeypatch):
    active = threading.Event()
    child_processes = []
    collect = CodexExecManager._collect

    def observe_collect(manager, entry, yield_ms):
        if threading.current_thread() is not threading.main_thread():
            child_processes.append(entry)
            active.set()
        return collect(manager, entry, yield_ms)

    monkeypatch.setattr(CodexExecManager, "_collect", observe_collect)
    root_result = execute_codex_tool(owner, "exec_command", {
        "cmd": "exec sleep 120", "login": False, "yield_time_ms": 250,
    })
    assert root_result["session_id"] is not None, root_result
    root_manager = next(iter(codex_runtime(owner).managers.values()))
    root_process = next(iter(root_manager.sessions.values()))
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "tool_call", "name": "exec_command", "call_id": "active-child",
         "arguments": '{"cmd":"exec sleep 120","login":false,"yield_time_ms":30000}',
         "usage": usage},
        {"type": "text", "text": "stopped", "usage": usage},
    ]
    try:
        with EngineMockProvider(script=script, record_path=tmp_path / "active-child.jsonl") as provider:
            monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
            monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
            agent_id = value(owner, "multi_agent_v1.spawn_agent", message="Start a command")["agent_id"]
            registry = owner._codex_agent_registry
            child = registry.children[agent_id]
            assert registry in owner._stock_tool_sessions
            assert active.wait(10), "child never entered real exec output collection"
            worker = child.worker
            assert worker is not None and worker.is_alive()
            assert child.status == "running"
            owner.close_stock_tools()
            assert registry.closed and child.closed and child.status == "shutdown"
            assert not worker.is_alive()
            assert child.worker is None
            assert not root_manager.sessions
            assert codex_runtime(owner).closed
            assert codex_runtime(child.conductor).closed
            assert root_process.terminated and root_process.process.poll() is not None
            assert child_processes and all(entry.terminated for entry in child_processes)
            for entry in [root_process, *child_processes]:
                _assert_dead(entry.process.pid)
            assert not child.pending
            with pytest.raises(ValueError, match="agent registry is closed"):
                registry.reserve(owner)
    finally:
        try:
            owner.close_stock_tools()
        finally:
            codex_runtime(owner).close()
            registry = getattr(owner, "_codex_agent_registry", None)
            if registry is not None:
                closing = registry.cancel()
                registry.drain(closing)
