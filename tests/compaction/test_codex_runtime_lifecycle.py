"""Regression locks for owned groups and root-owned active child conductors."""
import os
import signal
import subprocess
import threading
import time

import pytest

from breadboard_engine.execution.codex_exec import CodexExecManager, _Process, codex_runtime, execute_codex_tool
from breadboard_engine.execution import codex_agents as native
from breadboard_engine.compaction_lanes.codex_lane import EngineMockProvider
from tests.compaction.test_codex_agent_tools import child_execution_provider, owner, value


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
            with pytest.raises(ValueError):
                registry.reserve(owner)
    finally:
        try:
            owner.close_stock_tools()
        finally:
            codex_runtime(owner).close()
            registry = getattr(owner, "_codex_agent_registry", None)
            if registry is not None:
                registry.close()


@pytest.mark.parametrize("close_phase", ["before_cancel", "after_mark", "root_close"])
def test_close_drains_concurrent_nested_spawn(owner, tmp_path, monkeypatch, close_phase):
    """A running child cannot escape its parent's atomic subtree cancellation."""
    owner.config["agents"] = {"max_depth": 2}
    owner.config["provider_tools"]["openai"]["responses_namespaces"] = True
    nested_ready = threading.Event()
    spawn_allowed = threading.Event()
    spawn_finished = threading.Event()
    close_finished = threading.Event()
    nested_results = []
    processes = []
    managers = []
    workers = []
    failures = []
    agent_tool = native._agent_tool

    def nested_tool(conductor, name, args):
        if name == "spawn_agent" and getattr(conductor, "_codex_agent_depth", 0) == 1:
            nested_ready.set()
            assert spawn_allowed.wait(10), "close did not release the nested spawn"
            try:
                result = agent_tool(conductor, name, args)
                nested_results.append(result)
                descendant = conductor._codex_agent_registry.children[result["agent_id"]]
                if descendant.worker is not None:
                    workers.append(descendant.worker)
                # Give the admitted descendant a real owned process to drain.
                execution = execute_codex_tool(descendant.conductor, "exec_command", {
                    "cmd": "exec sleep 120", "login": False, "yield_time_ms": 250,
                })
                assert execution["session_id"] is not None, execution
                manager = next(iter(codex_runtime(descendant.conductor).managers.values()))
                managers.append(manager)
                processes.extend(manager.sessions.values())
                return result
            except native.AgentToolError as error:
                nested_results.append(str(error))
                raise
            finally:
                spawn_finished.set()
        return agent_tool(conductor, name, args)

    monkeypatch.setattr(native, "_agent_tool", nested_tool)
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "tool_call", "name": "exec_command", "call_id": "parent-process",
         "arguments": '{"cmd":"exec sleep 120","login":false,"yield_time_ms":250}',
         "usage": usage},
        {"type": "tool_call", "name": "spawn_agent", "namespace": "multi_agent_v1", "call_id": "nested",
         "arguments": '{"message":"Nested work"}', "usage": usage},
        *({"type": "text", "text": "finished", "usage": usage} for _ in range(4)),
    ]
    try:
        with EngineMockProvider(script=script, record_path=tmp_path / "nested-close.jsonl") as provider:
            monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
            monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
            agent_id = value(owner, "multi_agent_v1.spawn_agent", message="Start nested work")["agent_id"]
            registry = owner._codex_agent_registry
            child = registry.children[agent_id]
            assert nested_ready.wait(10), "child did not reach its nested spawn"
            workers.append(child.worker)
            manager = next(iter(codex_runtime(child.conductor).managers.values()))
            managers.append(manager)
            processes.extend(manager.sessions.values())
            assert processes, "parent never started an owned process"
            if close_phase == "before_cancel":
                close_registry = registry.close

                def close_after_spawn(children=None):
                    spawn_allowed.set()
                    assert spawn_finished.wait(10), "nested spawn did not finish before cancellation"
                    return close_registry(children)

                monkeypatch.setattr(registry, "close", close_after_spawn)
            else:
                request_stop = child.conductor.request_stop

                def stop_after_mark():
                    # Cancellation has made the parent's closed state visible.
                    # Waiting here also proves stopping does not hold the condition.
                    spawn_allowed.set()
                    assert spawn_finished.wait(10), "closed parent blocked nested spawn admission"
                    request_stop()

                monkeypatch.setattr(child.conductor, "request_stop", stop_after_mark)

            def close():
                try:
                    if close_phase == "root_close":
                        registry.close()
                    else:
                        value(owner, "multi_agent_v1.close_agent", target=agent_id)
                except BaseException as error:
                    failures.append(error)
                finally:
                    close_finished.set()

            closer = threading.Thread(target=close, daemon=True)
            closer.start()
            assert close_finished.wait(20), "close did not drain child workers"
            closer.join()
            assert not failures, failures
            assert spawn_finished.is_set()
            if close_phase == "before_cancel":
                assert isinstance(nested_results[0], dict)
                assert len(registry.children) == 2
            else:
                expected = ("agent registry is closed" if close_phase == "root_close"
                            else f"agent with id {agent_id} not found")
                assert nested_results == [expected]
                assert len(registry.children) == 1
            assert all(child.closed and child.status == "shutdown" and not child.pending
                       and child.worker is None for child in registry.children.values())
            assert all(worker is not None and not worker.is_alive() for worker in workers)
            assert all(codex_runtime(child.conductor).closed for child in registry.children.values())
            assert all(not manager.sessions for manager in managers)
            assert all(entry.terminated and entry.process.poll() is not None for entry in processes)
            assert value(owner, "multi_agent_v1.wait_agent", targets=[agent_id]) == {
                "status": {agent_id: "not_found"}, "timed_out": False}
            registry.close()
            registry.close()
    finally:
        spawn_allowed.set()
        owner.close_stock_tools()


@pytest.mark.parametrize("close_kind", ["subtree", "root"])
def test_reserved_worker_start_is_atomic_with_close(
    owner, child_execution_provider, monkeypatch, close_kind,
):
    agent_id = value(owner, "multi_agent_v1.spawn_agent", message="Run real child tools")["agent_id"]
    registry = owner._codex_agent_registry
    child = registry.children[agent_id]
    assert value(owner, "multi_agent_v1.wait_agent", targets=[agent_id])["status"][agent_id] == {
        "completed": "child finished"}
    with registry.condition:
        assert registry.condition.wait_for(lambda: child.worker is None, timeout=10)
        child.pending.append((native._new_thread_id(), "Another real turn",
                              [{"type": "text", "text": "Another real turn"}]))
    execution = execute_codex_tool(child.conductor, "exec_command", {
        "cmd": "exec sleep 120", "login": False, "yield_time_ms": 250,
    })
    assert execution["session_id"] is not None, execution
    manager = next(iter(codex_runtime(child.conductor).managers.values()))
    processes = list(manager.sessions.values())
    constructing = threading.Event()
    close_attempted = threading.Event()
    finished = threading.Event()
    launched = []
    failures = []
    thread_type = threading.Thread
    thread_init = thread_type.__init__

    def pause_worker_construction(worker, *args, **kwargs):
        thread_init(worker, *args, **kwargs)
        if kwargs.get("name") == f"codex-agent-{agent_id}":
            constructing.set()
            assert close_attempted.wait(10), "close never attempted worker-start admission"
            launched.append(worker)

    def close():
        try:
            assert constructing.wait(10), "starter never reached worker construction"
            # If start has released admission too early, finish close before
            # permitting publication. Otherwise signal contention and let close
            # wait for the canonical nonblocking Thread.start handoff.
            acquired = registry.condition.acquire(blocking=False)
            if acquired:
                registry.condition.release()
            else:
                close_attempted.set()
            if close_kind == "root":
                registry.close()
            else:
                value(owner, "multi_agent_v1.close_agent", target=agent_id)
            close_attempted.set()
        except BaseException as error:
            failures.append(error)
            close_attempted.set()
        finally:
            finished.set()

    closer = thread_type(target=close, daemon=True)
    monkeypatch.setattr(thread_type, "__init__", pause_worker_construction)
    try:
        closer.start()
        registry.start(child)
        assert finished.wait(20), "close did not drain the admitted worker"
        closer.join()
        assert not failures, failures
        assert launched and all(not worker.is_alive() for worker in launched)
        assert child.closed and child.status == "shutdown" and child.worker is None
        assert not child.pending
        assert codex_runtime(child.conductor).closed and not manager.sessions
        assert processes and all(entry.terminated and entry.process.poll() is not None for entry in processes)
    finally:
        close_attempted.set()
        owner.close_stock_tools()


@pytest.mark.parametrize("close_kind", ["subtree", "root"])
def test_closing_generation_cannot_resume_before_real_worker_drains(
    owner, tmp_path, monkeypatch, close_kind,
):
    active = threading.Event()
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    close_finished = threading.Event()
    processes = []
    failures = []
    collect = CodexExecManager._collect

    def observe_collect(manager, entry, yield_ms):
        processes.append(entry)
        active.set()
        return collect(manager, entry, yield_ms)

    monkeypatch.setattr(CodexExecManager, "_collect", observe_collect)
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "tool_call", "name": "exec_command", "call_id": "closing-generation",
         "arguments": '{"cmd":"exec sleep 120","login":false,"yield_time_ms":30000}',
         "usage": usage},
        *({"type": "text", "text": "resumed generation", "usage": usage} for _ in range(4)),
    ]
    try:
        with EngineMockProvider(script=script, record_path=tmp_path / "resume-close.jsonl") as provider:
            monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
            monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
            agent_id = value(owner, "multi_agent_v1.spawn_agent", message="Start owned execution")["agent_id"]
            registry = owner._codex_agent_registry
            child = registry.children[agent_id]
            assert active.wait(10), "real child never started execution"
            worker = child.worker
            runtime = codex_runtime(child.conductor)
            manager = next(iter(runtime.managers.values()))
            close_runtime = runtime.close

            def gated_cleanup():
                cleanup_entered.set()
                assert release_cleanup.wait(10), "test never released resource cleanup"
                close_runtime()

            monkeypatch.setattr(runtime, "close", gated_cleanup)

            def close():
                try:
                    if close_kind == "root":
                        registry.close()
                    else:
                        value(owner, "multi_agent_v1.close_agent", target=agent_id)
                except BaseException as error:
                    failures.append(error)
                finally:
                    close_finished.set()

            closer = threading.Thread(target=close, daemon=True)
            closer.start()
            assert cleanup_entered.wait(10), "close never reached owned resource cleanup"
            assert worker is not None and worker.is_alive()
            expected = ("agent registry is closed" if close_kind == "root"
                        else f"agent with id {agent_id} is closing")
            for name, args in [
                ("resume_agent", {"id": agent_id}),
                ("send_input", {"target": agent_id, "message": "Race the close"}),
            ]:
                result = native.execute_codex_agent_tool(owner, f"multi_agent_v1.{name}", args)
                assert result == {"error": expected, "__mvi_text_output": expected}
            with pytest.raises(native.AgentToolError):
                registry.start(child)
            assert codex_runtime(child.conductor) is runtime
            assert child.closed and not child.pending
            release_cleanup.set()
            assert close_finished.wait(20), "close did not drain its generation"
            closer.join()
            assert not failures, failures
            assert not worker.is_alive() and child.worker is None
            assert child.status == "shutdown" and not registry.closing
            assert runtime.closed and not manager.sessions
            assert processes and all(entry.terminated and entry.process.poll() is not None for entry in processes)
            if close_kind == "subtree":
                assert value(owner, "multi_agent_v1.resume_agent", id=agent_id) == {"status": "pending_init"}
                value(owner, "multi_agent_v1.send_input", target=agent_id, message="Run after completed close")
                assert value(owner, "multi_agent_v1.wait_agent", targets=[agent_id])["status"][agent_id] == {
                    "completed": "resumed generation"}
                assert codex_runtime(child.conductor) is not runtime
    finally:
        release_cleanup.set()
        owner.close_stock_tools()


def test_failed_close_keeps_generation_blocked_until_cleanup_retry(
    owner, child_execution_provider, monkeypatch,
):
    agent_id = value(owner, "multi_agent_v1.spawn_agent", message="Run real child tools")["agent_id"]
    registry = owner._codex_agent_registry
    child = registry.children[agent_id]
    value(owner, "multi_agent_v1.wait_agent", targets=[agent_id])
    with registry.condition:
        assert registry.condition.wait_for(lambda: child.worker is None, timeout=10)
    execution = execute_codex_tool(child.conductor, "exec_command", {
        "cmd": "exec sleep 120", "login": False, "yield_time_ms": 250,
    })
    assert execution["session_id"] is not None, execution
    runtime = codex_runtime(child.conductor)
    manager = next(iter(runtime.managers.values()))
    processes = list(manager.sessions.values())
    request_stop = child.conductor.request_stop

    def failed_stop():
        raise OSError("injected shutdown failure")

    monkeypatch.setattr(child.conductor, "request_stop", failed_stop)
    with pytest.raises(RuntimeError, match="Codex agent shutdown failed"):
        value(owner, "multi_agent_v1.close_agent", target=agent_id)
    assert child.closed and registry.closing == {agent_id: None}
    result = native.execute_codex_agent_tool(owner, "multi_agent_v1.resume_agent", {"id": agent_id})
    assert result["error"] == f"agent with id {agent_id} is closing"
    assert codex_runtime(child.conductor) is runtime
    assert not manager.sessions
    assert processes and all(entry.terminated and entry.process.poll() is not None for entry in processes)
    monkeypatch.setattr(child.conductor, "request_stop", request_stop)
    value(owner, "multi_agent_v1.close_agent", target=agent_id)
    assert not registry.closing and child.status == "shutdown"
    assert value(owner, "multi_agent_v1.resume_agent", id=agent_id) == {"status": "pending_init"}
