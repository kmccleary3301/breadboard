"""Execute the unmodified 2.1.63 CLI; compare its wire outputs to source strings."""
import json
import os
import subprocess

import pytest

from breadboard_engine.execution.claude_tools import (
    ClaudeToolSession, StockExecutionError,
)


@pytest.fixture
def stock(tmp_path):
    session = ClaudeToolSession(str(tmp_path), "claude-haiku-4-5-20251001", timeout=30, permission_mode="bypassPermissions")
    yield session
    session.close()


def text(session, name, arguments):
    return session.execute(name, arguments)["__mvi_text_output"]


def test_exact_read_name_and_cat_n_format(stock, tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("one\ntwo\n")
    result = text(stock, "Read", {"file_path": str(path)})
    assert result.startswith("     1→one\n     2→two\n     3→\n")
    assert "Whenever you read a file, you should consider whether it would be considered malware." in result
    error = stock.execute("read", {"file_path": str(path)})["__claude_tool_result_blocks"][0]
    assert error["is_error"] is True
    assert error["content"] == "<tool_use_error>Error: No such tool available: read</tool_use_error>"


def test_empty_and_long_read_lines(stock, tmp_path):
    path = tmp_path / "file.txt"
    path.touch()
    assert text(stock, "Read", {"file_path": str(path)}) == "<system-reminder>Warning: the file exists but is shorter than the provided offset (1). The file has 1 lines.</system-reminder>"
    path.write_text("a" * 1999 + "\n" + "b" * 2000 + "\n" + "c" * 2001)
    result = text(stock, "Read", {"file_path": str(path)})
    assert "     1→" + "a" * 1999 + "\n" in result
    assert "     2→" + "b" * 2000 + "\n" in result
    # aX1 preserves this despite the prompt's 2000-character clipping promise.
    assert "     3→" + "c" * 2001 in result


def test_edit_write_guards_and_outputs(stock, tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("one one\n")
    arguments = {"file_path": str(path), "old_string": "one", "new_string": "two"}
    assert "File has not been read yet. Read it first before writing to it." in text(stock, "Edit", arguments)
    assert "File has not been read yet. Read it first before writing to it." in text(stock, "Write", {"file_path": str(path), "content": "new"})
    text(stock, "Read", {"file_path": str(path)})
    assert "Found 2 matches of the string to replace, but replace_all is false." in text(stock, "Edit", arguments)
    assert "String to replace not found in file.\nString: missing" in text(stock, "Edit", {**arguments, "old_string": "missing"})
    assert text(stock, "Edit", {**arguments, "replace_all": True}) == f"The file {path} has been updated. All occurrences of 'one' were successfully replaced with 'two'."
    assert text(stock, "Write", {"file_path": str(path), "content": "new"}) == f"The file {path} has been updated successfully."
    new = tmp_path / "new.txt"
    assert text(stock, "Write", {"file_path": str(new), "content": "new"}) == f"File created successfully at: {new}"


def test_glob_grep_empty_and_pagination(stock, tmp_path):
    assert text(stock, "Glob", {"pattern": "*.missing"}) == "No files found"
    empty = tmp_path / "empty-search"
    empty.mkdir()
    assert text(stock, "Grep", {"pattern": "missing", "path": str(empty)}) == "No files found"
    assert text(stock, "Grep", {"pattern": "missing", "path": str(empty), "output_mode": "content"}) == "No matches found"
    (tmp_path / "one.txt").write_text("hit\nhit\nhit\n")
    assert text(stock, "Grep", {"pattern": "hit", "output_mode": "content", "head_limit": 1}).endswith("[Showing results with pagination = limit: 1, offset: 0]")


def test_bash_foreground_and_background(stock):
    assert text(stock, "Bash", {"command": "printf '\nhello\n'"}) == "hello"
    result = text(stock, "Bash", {"command": "sleep 10", "run_in_background": True})
    assert result.startswith("Command running in background with ID: ")
    task_id = result.split("ID: ", 1)[1].split(".", 1)[0]
    status = text(stock, "TaskOutput", {"task_id": task_id, "block": False, "timeout": 1000})
    assert "<retrieval_status>not_ready</retrieval_status>" in status
    stop = text(stock, "TaskStop", {"task_id": task_id})
    assert json.loads(stop)["task_id"] == task_id


@pytest.mark.parametrize("name,arguments,fragment", [
    ("Agent", {"prompt": "inspect", "subagent_type": "not-an-agent", "description": "inspect"}, "Agent type 'not-an-agent' not found"),
    ("TaskOutput", {"task_id": "missing"}, "No task found with ID: missing"),
    ("TaskStop", {"task_id": "missing"}, "No task found with ID: missing"),
    ("NotebookEdit", {"notebook_path": "/does/not/exist.ipynb", "new_source": "x"}, "does not exist"),
    ("Skill", {"skill": "missing-stock-test-skill"}, "Unknown skill"),
    ("WebFetch", {"url": "not-a-url", "prompt": "inspect"}, "Invalid URL"),
    ("WebSearch", {"query": "x"}, "InputValidationError"),
    ("AskUserQuestion", {"questions": []}, "InputValidationError"),
    ("EnterWorktree", {}, "not in a git repository"),
])
def test_stock_error_strings(stock, name, arguments, fragment):
    result = stock.execute(name, arguments)["__claude_tool_result_blocks"][0]
    assert result["is_error"] is True
    assert fragment in result["content"]


def test_todo_and_plan_mode(stock):
    assert text(stock, "TodoWrite", {"todos": []}) == "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. Please proceed with the current tasks if applicable"
    assert "Entered plan mode" in text(stock, "EnterPlanMode", {})
    # The stock noninteractive CLI does not invent a user's approval.
    assert text(stock, "ExitPlanMode", {}) == "Exit plan mode?"


def test_stock_compaction_restores_files_and_preserves_observed_edit_guard(stock, tmp_path):
    paths = []
    for index in range(6):
        path = tmp_path / f"{index}.txt"
        path.write_text("before\n")
        os.utime(path, (1700000000 + index, 1700000000 + index))
        text(stock, "Read", {"file_path": str(path)})
        paths.append(path)
    stock.compact("<summary>Read six files.</summary>")
    # Stock SDK stream-json retains this guard even when the file is not among
    # the five reattached files. Do not replace executable behavior with an
    # inference from readFileState.clear().
    assert text(stock, "Edit", {"file_path": str(paths[0]), "old_string": "before", "new_string": "after"}) == f"The file {paths[0]} has been updated successfully."
    assert text(stock, "Edit", {"file_path": str(paths[-1]), "old_string": "before", "new_string": "after"}) == f"The file {paths[-1]} has been updated successfully."
    assert any(record["kind"] == "compact" for record in stock.records)
    assert any("Called the Read tool" in json.dumps(record["request"]["messages"]) for record in stock.records if record["kind"] == "main")
    unread = tmp_path / "unread.txt"
    unread.write_text("before")
    assert "File has not been read yet" in text(stock, "Edit", {"file_path": str(unread), "old_string": "before", "new_string": "after"})


def test_classifier_never_forwards_unknown_requests(stock):
    with pytest.raises(StockExecutionError, match="Unclassified"):
        stock.classify({"messages": [{"role": "user", "content": "unknown"}], "system": [], "tools": []})


def test_agent_and_websearch_are_real_auxiliary_exchanges(tmp_path):
    calls = []

    def auxiliary(body, headers, path):
        calls.append(body)
        payload = ClaudeToolSession.encode_response([{"type": "text", "text": "A real test-provider response."}], body)
        return 200, {"Content-Type": "text/event-stream" if body.get("stream") else "application/json"}, payload

    session = ClaudeToolSession(str(tmp_path), "claude-haiku-4-5-20251001", auxiliary=auxiliary, timeout=30, permission_mode="bypassPermissions")
    try:
        agent = text(session, "Agent", {"prompt": "Return the test-provider answer.", "subagent_type": "general-purpose", "description": "test exchange"})
        assert "A real test-provider response." in agent
        assert calls and calls[0].get("system") and calls[0].get("tools")
        search = text(session, "WebSearch", {"query": "Claude Code stock tools"})
        assert 'Web search results for query: "Claude Code stock tools"' in search
        assert any(t.get("type") == "web_search_20250305" for t in calls[-1].get("tools", []))
        assert {r["kind"] for r in session.records} >= {"main", "agent", "web_search"}
    finally:
        session.close()
