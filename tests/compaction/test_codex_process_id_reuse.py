"""A stale collector must not relinquish ownership of a reused process ID."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading

from breadboard_engine.execution.codex_exec import CodexExecManager, _Process


def test_stale_write_stdin_after_prune_cannot_remove_reused_live_session(tmp_path, monkeypatch):
    ids = iter([*range(1000, 1064), 2000, 1000])
    manager = CodexExecManager(
        str(tmp_path), truncation_policy={"mode": "tokens", "limit": 10000},
        process_id=ids.__next__,
    )
    entries = []
    collector_waiting = threading.Event()
    release_collector = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        # Populate the stock 64-session cap with real independent owned groups.
        # Blocking cat processes need neither timed sleeps nor synthetic exit status.
        for index in range(64):
            process_id = manager.allocate_process_id()
            process = subprocess.Popen(
                ["/bin/cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
            entry = _Process(process, False, process.stdout, None, float(index))
            entries.append(entry)
            manager.sessions[process_id] = entry
            entry.reader_thread = threading.Thread(target=entry.read_background, daemon=True)
            entry.reader_thread.start()
        original = manager.sessions[1000]
        collect = manager._collect

        def hold_original_collector(entry, yield_ms):
            if entry is original:
                collector_waiting.set()
                assert release_collector.wait(10), "stale collector was not released"
            return collect(entry, yield_ms)

        monkeypatch.setattr(manager, "_collect", hold_original_collector)
        stale = executor.submit(manager.write_stdin, {"session_id": 1000})
        assert collector_waiting.wait(10), "write_stdin never retained the original entry"

        # These are real launches through the manager's canonical sandbox policy.
        command = {"cmd": "exec /bin/cat", "tty": True, "login": False, "yield_time_ms": 250}
        overflow = manager.exec_command(command)
        assert overflow["session_id"] == 2000, overflow
        entries.append(manager.sessions[2000])
        assert original.terminated and original.process.poll() is not None
        assert 1000 not in manager.reserved_process_ids

        reused = manager.exec_command(command)
        assert reused["session_id"] == 1000, reused
        replacement = manager.sessions[1000]
        entries.append(replacement)
        assert replacement is not original and replacement.process.poll() is None

        release_collector.set()
        result = stale.result(timeout=10)
        assert result["exit"] == -1 and result["session_id"] is None, result
        assert manager.sessions.get(1000) is replacement
        assert 1000 in manager.reserved_process_ids

        manager.close()
        assert replacement.terminated and replacement.process.poll() is not None
        assert not manager.sessions and not manager.reserved_process_ids
    finally:
        release_collector.set()
        # Retain references independent of the manager map so the red regression
        # cannot leak the very replacement process whose ownership was dropped.
        for entry in entries:
            entry.terminate()
            if entry.process.stdin is not None:
                entry.process.stdin.close()
        manager.close()
        executor.shutdown(wait=True)
