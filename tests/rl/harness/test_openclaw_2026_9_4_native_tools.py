from __future__ import annotations

from pathlib import Path

import pytest

from breadboard.rl.harness.openclaw_native_tools import (
    BOOTSTRAP_ORDER,
    EXEC_YIELD_MS,
    PROCESS_MAX_POLL_MS,
    OpenClawNativeTools,
    build_bootstrap_context,
    load_bootstrap_files,
    materialize_baseline_bootstrap,
)



def test_bootstrap_order_budgets_and_missing_markers(tmp_path: Path) -> None:
    materialize_baseline_bootstrap(tmp_path)
    files = load_bootstrap_files(tmp_path)
    assert [file.name for file in files] == [*BOOTSTRAP_ORDER[:2], "IDENTITY.md", "BOOTSTRAP.md"]
    context = build_bootstrap_context(files, per_file_budget=20_000, total_budget=60_000)
    assert [entry["path"] for entry in context[:2]] == [str(tmp_path / "AGENTS.md"), str(tmp_path / "SOUL.md")]
    assert context[2]["content"] == f"[MISSING] Expected at: {tmp_path / 'IDENTITY.md'}"
    assert context[3]["content"] == f"[MISSING] Expected at: {tmp_path / 'BOOTSTRAP.md'}"


def test_native_file_effects_and_exact_edit(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    tools.execute("write", {"path": "marker.txt", "content": "before\n"})
    result = tools.execute("edit", {"path": "marker.txt", "oldText": "before", "newText": "after"})
    assert result["changed"] is True
    assert (tmp_path / "marker.txt").read_text() == "after\n"
    assert tools.execute("read", {"path": "marker.txt"})["content"] == "after\n"


def test_exec_and_process_poll_clamp(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    result = tools.execute("exec", {"command": "printf READY", "background": True})
    assert result["status"] == "running"
    polled = tools.execute("process", {"action": "poll", "sessionId": result["sessionId"], "timeout": 999_999})
    assert polled["pendingAcknowledgement"] is True
    tools.acknowledge_poll(result["sessionId"])
    assert EXEC_YIELD_MS == 10_000
    assert PROCESS_MAX_POLL_MS == 30_000
    tools.scope.cleanup()


def test_max_live_processes_counts_every_exec_including_foreground(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    try:
        for index in range(4):
            res = tools.execute("exec", {"command": "sleep 30", "background": True})
            assert res.get("status") == "running"
        # The 5th exec call is foreground (no background or pty flags).
        # Per Kyle's process choice in issue 15, max_live_processes=4 must count
        # EVERY live exec process, rejecting the 5th call.
        fifth = tools.execute("exec", {"command": "echo foreground_should_be_rejected"})
        assert fifth.get("isError") is True
        assert fifth.get("status") == "rejected"
        assert "OpenClaw live process cap exceeded" in str(fifth.get("output", ""))
    finally:
        tools.scope.cleanup()

