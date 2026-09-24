from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import tarfile

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



_WORKER = Path(__file__).parents[3] / "breadboard/rl/harness/openclaw_tool_worker.mjs"
_LOADER = _WORKER.with_name("openclaw_classifier_loader.mjs")
_PACKET = Path(
    os.environ.get(
        "OPENCLAW_ADMITTED_PACKET",
        "/Users/kylemccleary/projects/breadboard/docs_tmp/bb_direction_assessment/"
        "engine_pr_handoff_20260827/e4_admission_20260914T221653Z/"
        "do2-20260923/openclaw/packet/openclaw-capture-admitted-640-20260923T091850Z.tar.gz",
    )
)


def test_pinned_classifier_refuses_changed_dist_byte(tmp_path: Path) -> None:
    source_dist = Path(os.environ.get("OPENCLAW_DIST", "/tmp/openclaw-npm-20260923/node_modules/openclaw/dist"))
    dist = tmp_path / "dist"
    dist.mkdir()
    for source in source_dist.iterdir():
        if source.name != "agent-exec-BAuhpelg.mjs":
            (dist / source.name).symlink_to(source)
    pinned = source_dist / "agent-exec-BAuhpelg.mjs"
    (dist / pinned.name).write_bytes(pinned.read_bytes() + b"\\n")
    result = subprocess.run(
        ["node", "--import", str(_LOADER), str(_WORKER)],
        env={**os.environ, "OPENCLAW_DIST": str(dist)},
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode != 0
    assert b"pinned OpenClaw dist digest mismatch for agent-exec-BAuhpelg.mjs" in result.stderr


@pytest.mark.skipif(not _PACKET.is_file(), reason="admitted supplier packet is not installed")
def test_packet_results_are_classified_by_pinned_dist(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    try:
        expected = {
            "normal_multiturn_write_read": ("ok", "marker verified", 0),
            "process_exec_effect": ("ok", "process complete", 0),
            "malformed_tool_call": ("error", "", 1),
        }
        with tarfile.open(_PACKET, "r:gz") as packet:
            for case, (status, final, exit_code) in expected.items():
                member = packet.extractfile(f"packet/cases/{case}/supplier.stdout")
                assert member is not None
                captured = json.load(member)
                classified = tools._worker._request({"phase": "classify_result", "result": captured})
                assert classified["envelope"]["status"] == status
                assert classified["envelope"]["final"] == final
                assert classified["exit_code"] == exit_code
                assert classified["envelope"]["payloads"] == captured["payloads"]
    finally:
        tools.scope.cleanup()


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


def test_poll_replays_unacknowledged_output_until_history_commit(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    try:
        launched = tools.execute("exec", {"command": "printf 'ACK_MARKER\\n'", "background": True})
        session = launched["sessionId"]
        first = tools.execute("process", {"action": "poll", "sessionId": session, "timeout": 500})
        second = tools.execute("process", {"action": "poll", "sessionId": session, "timeout": 500})
        assert "ACK_MARKER" in first["output"]
        assert second["output"] == first["output"]
        tools.acknowledge_poll(second["delivery_id"], "sha256:" + "a" * 64)
        third = tools.execute("process", {"action": "poll", "sessionId": session, "timeout": 500})
        assert "ACK_MARKER" not in third["output"]
    finally:
        tools.scope.cleanup()


def test_parallel_native_batch_commits_source_order_with_observed_completion(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    try:
        tools._worker._request({
            "phase": "prepare_tools",
            "calls": [
                {"id": "slow", "name": "exec", "arguments": {"command": "sleep 0.7; printf SLOW"}},
                {"id": "fast", "name": "exec", "arguments": {"command": "printf FAST"}},
            ],
        })
        results = tools._worker._request({"phase": "execute_batch"})["results"]
        assert [item["id"] for item in results] == ["slow", "fast"]
        assert [item["isError"] for item in results] == [False, False]
        assert [item["completion_index"] for item in results] == [1, 0]
        assert "SLOW" in results[0]["content"][0]["text"]
        assert "FAST" in results[1]["content"][0]["text"]
    finally:
        tools.scope.cleanup()


def test_parallel_exec_reservations_enforce_live_process_cap(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    try:
        tools._worker._request({
            "phase": "prepare_tools",
            "calls": [
                {"id": str(index), "name": "exec", "arguments": {"command": "sleep 0.4; printf DONE"}}
                for index in range(5)
            ],
        })
        results = tools._worker._request({"phase": "execute_batch"})["results"]
        assert [item["isError"] for item in results] == [False] * 4 + [True]
        assert results[4]["details"]["status"] == "rejected"
        assert sorted(item["completion_index"] for item in results) == list(range(5))
    finally:
        tools.scope.cleanup()


def test_exec_timeout_admission_rejects_over_thirty_seconds(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    try:
        denied = tools.execute("exec", {"command": "touch timeout-31.txt", "timeoutSeconds": 31})
        assert denied["isError"] is True
        assert not (tmp_path / "timeout-31.txt").exists()
        accepted = tools.execute("exec", {"command": "printf TIMEOUT_30", "timeoutSeconds": 30})
        assert accepted["isError"] is False
        assert "TIMEOUT_30" in accepted["output"]
    finally:
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
def test_classify_result_semantics_and_envelope_invariants(tmp_path: Path) -> None:
    tools = OpenClawNativeTools(tmp_path)
    try:
        # 1. OK execution: exit 0, trailing trim, nullable model/provider, usage/cost/toolSummary preserved
        ok_res = tools._worker._request({
            "phase": "classify_result",
            "result": {
                "payloads": [
                    {"text": "Visible answer.\n\n"},
                    {"text": "internal thought", "isReasoning": True},
                    {"text": "commentary block", "isCommentary": True},
                ],
                "meta": {
                    "durationMs": 1500,
                    "stopReason": "stop",
                    "agentMeta": {
                        "model": None,
                        "provider": None,
                        "sessionId": "session-1",
                        "usage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
                        "costUsd": 0.002,
                    },
                    "toolSummary": {"totalCalls": 2, "toolCounts": {"read": 1, "write": 1}},
                },
            },
        })
        assert ok_res["kind"] == "classified_result"
        assert ok_res["exit_code"] == 0
        env = ok_res["envelope"]
        assert env["ok"] is True
        assert env["status"] == "ok"
        # Trailing whitespace trimmed, reasoning and commentary excluded
        assert env["final"] == "Visible answer."
        assert env["model"] is None
        assert env["provider"] is None
        assert env["usage"] == {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15}
        assert env["costUsd"] == 0.002
        assert env["toolSummary"] == {"totalCalls": 2, "toolCounts": {"read": 1, "write": 1}}

        # 2. Timeout precedence: timeout outranks error -> exit 2
        timeout_res = tools._worker._request({
            "phase": "classify_result",
            "result": {
                "payloads": [
                    {"text": "partial work\n"},
                    {"text": "tool failed", "isError": True},
                ],
                "meta": {
                    "durationMs": 45000,
                    "stopReason": "timeout",
                    "timeoutPhase": "action",
                    "error": {"message": "Timed out", "kind": "timeout"},
                    "agentMeta": {"model": "gpt-4o", "provider": "openai", "sessionId": "s2"},
                },
            },
        })
        assert timeout_res["exit_code"] == 2
        assert timeout_res["envelope"]["status"] == "timeout"
        assert timeout_res["envelope"]["ok"] is False

        # 3. Error precedence: error -> exit 1, and error payload excludes metadata fallback
        err_res = tools._worker._request({
            "phase": "classify_result",
            "result": {
                "payloads": [
                    {"text": "failed run error output", "isError": True},
                ],
                "meta": {
                    "durationMs": 2000,
                    "stopReason": "error",
                    "error": {"message": "Crash", "kind": "agent_error"},
                    "finalAssistantVisibleText": "stale fallback text that must be ignored",
                    "agentMeta": {"model": "gpt-4o", "provider": "openai", "sessionId": "s3"},
                },
            },
        })
        assert err_res["exit_code"] == 1
        assert err_res["envelope"]["status"] == "error"
        assert err_res["envelope"]["ok"] is False
        # finalAssistantVisibleText is ignored because there is an error payload
        assert err_res["envelope"]["final"] == ""

        # 4. Metadata fallback: when no text payload and no error payload, uses finalAssistantVisibleText
        fallback_res = tools._worker._request({
            "phase": "classify_result",
            "result": {
                "payloads": [],
                "meta": {
                    "durationMs": 1200,
                    "stopReason": "stop",
                    "finalAssistantVisibleText": "fallback visible text\n\n",
                    "agentMeta": {"model": "gpt-4o", "provider": "openai", "sessionId": "s4"},
                },
            },
        })
        assert fallback_res["exit_code"] == 0
        assert fallback_res["envelope"]["ok"] is True
        assert fallback_res["envelope"]["final"] == "fallback visible text"
    finally:
        tools.scope.cleanup()
