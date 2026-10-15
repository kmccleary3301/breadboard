"""Stock allocator, per-thread manager, and cancellation regression evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import os
import shlex
import signal
from types import SimpleNamespace

from PIL import Image
import pytest

from breadboard_engine.execution.codex_exec import CodexExecManager, CodexRuntime, truncate_output

POLICY = {"mode": "bytes", "limit": 10000}


def manager(workspace, **kwargs):
    return CodexExecManager(str(workspace), truncation_policy=POLICY, **kwargs)


def runtime(workspace, *, protected_paths=()):
    return CodexRuntime(SimpleNamespace(
        workspace=str(workspace), _protected_credential_paths=protected_paths,
    ))


def test_allocator_reserves_uniform_range_and_redraws_collisions(tmp_path):
    # core/src/unified_exec/process_manager.rs:332-366.
    source = iter([1000, 1000, 99999])
    instance = manager(tmp_path, process_id=source.__next__)
    try:
        assert instance.allocate_process_id() == 1000
        assert instance.allocate_process_id() == 99999
        assert instance.process_id_generator_state() == {"allocation_count": 2, "draw_count": 3}
        with pytest.raises(RuntimeError, match="before the first allocation"):
            instance.set_process_id_generator(lambda: 1001)
        instance.release_process_id(1000)
        assert instance.reserved_process_ids == {99999}
        with pytest.raises(StopIteration):
            instance.allocate_process_id()
    finally:
        instance.close()


@pytest.mark.parametrize("value", [999, 100000, True, "1234"])
def test_invalid_injected_id_fails_before_process_launch(tmp_path, value):
    instance = manager(tmp_path, process_id=lambda: value)
    try:
        with pytest.raises(ValueError, match=r"\[1000, 100000\)"):
            instance.exec_command({"cmd": "touch must-not-exist", "login": False})
        assert not (tmp_path / "must-not-exist").exists()
        assert instance.sessions == {}
    finally:
        instance.close()


def test_exhausted_generator_cannot_wrap_or_launch_another_process(tmp_path):
    instance = manager(tmp_path, process_id=iter([4321]).__next__)
    try:
        assert instance.exec_command({"cmd": "printf completed", "login": False})["exit"] == 0
        with pytest.raises(StopIteration):
            instance.exec_command({"cmd": "touch must-not-exist", "login": False})
        assert not (tmp_path / "must-not-exist").exists()
        assert instance.process_id_generator_state() == {"allocation_count": 1, "draw_count": 2}
    finally:
        instance.close()


@pytest.mark.parametrize("child_id", [1234, 1235])
def test_sessions_are_owned_and_closed_per_thread(tmp_path, child_id):
    # core/src/session/session.rs:992-994: one manager and 64-session cap per thread.
    instance = runtime(tmp_path)
    instance.set_process_id_generator(iter([1234]).__next__, "parent")
    instance.set_process_id_generator(iter([child_id]).__next__, "child")
    args = {"cmd": "sleep 30", "login": False, "yield_time_ms": 250}
    try:
        assert instance.exec_command(args, "parent", POLICY)["session_id"] == 1234
        assert instance.exec_command(args, "child", POLICY)["session_id"] == child_id
        if child_id != 1234:
            denied = instance.write_stdin({"session_id": child_id}, "parent", POLICY)
            assert denied["__mvi_text_output"] == f"write_stdin failed: Unknown process id {child_id}"
        assert len(instance.managers["parent"].sessions) == 1
        assert len(instance.managers["child"].sessions) == 1
        instance.close_thread("parent")
        assert instance.write_stdin({"session_id": child_id, "yield_time_ms": 1}, "child", POLICY)["session_id"] == child_id
        assert instance.process_id_generator_state() == {"allocation_count": 2, "draw_count": 2}
    finally:
        instance.close()


def test_detached_setsid_descendant_does_not_hold_reader_join_forever(tmp_path):
    # process_manager.rs:1082-1168 bounds post-exit draining; the descendant keeps
    # stdout open in a separate POSIX session, so killing the parent group cannot
    # make its descriptor reach EOF.
    pid_file = tmp_path / "detached.pid"
    script = (
        "import subprocess,sys,pathlib; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
        "print('parent-exit',flush=True)"
    )
    instance = manager(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(instance.exec_command, {
                "cmd": f"/usr/bin/python3 -c {shlex.quote(script)}", "login": False, "yield_time_ms": 30000})
            try:
                # Without the post-exit drain bound the join waits out the 30 s yield. The parent
                # itself can take a few seconds to exit under the restricted launcher.
                result = future.result(timeout=15)
                assert result["exit"] == 0
                assert result["stdout"] == "parent-exit\n"
                assert os.getsid(int(pid_file.read_text())) == int(pid_file.read_text())
            finally:
                if pid_file.exists():
                    try:
                        os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
    finally:
        instance.close()


def test_view_image_cannot_read_protected_png(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "credential.png"
    Image.new("RGB", (8, 8), "red").save(protected)
    instance = runtime(workspace, protected_paths=(protected,))
    result = instance.view_image({"path": str(protected)})
    assert result.get("error")
    assert "__mvi_content_output" not in result
    assert "unable to locate image" in result["__mvi_text_output"]
    assert "Permission denied" in result["__mvi_text_output"] or "Operation not permitted" in result["__mvi_text_output"]


@pytest.mark.parametrize("limit", [7, 11, 15, 23])
def test_patch_formatter_preserves_nonmultiple_byte_policy(tmp_path, limit):
    # core/src/tools/events.rs:344-349 -> tools/mod.rs:62-86 and
    # utils/string/src/truncate.rs:38-152. The inner cap uses bytes/chars, not
    # ceil(bytes/4) tokens; only the outer cap converts bytes to tokens.
    instance = CodexExecManager(str(tmp_path), truncation_policy={"mode": "bytes", "limit": limit})
    patch = "*** Begin Patch\n*** Add File: small.txt\n+content\n*** End Patch"
    try:
        result = instance.exec_command({"cmd": f"apply_patch <<'PATCH'\n{patch}\nPATCH", "login": False})
        stdout = "Success. Updated the following files:\nA small.txt\n"
        left, right = limit // 2, limit - limit // 2
        truncated = stdout[:left] + f"…{len(stdout) - limit} chars truncated…" + stdout[-right:]
        inner = "Exit code: 0\nWall time: 0 seconds\nTotal output lines: 2\nOutput:\n" + truncated
        assert result["__mvi_text_output"] == "Wall time: 0.0000 seconds\nOutput:\n" + truncate_output(inner, math.ceil(limit / 4))
    finally:
        instance.close()
