import asyncio
from pathlib import Path

import pytest

from scripts import replay_opencode_session as replay


class StubExecutor:
    def __init__(self):
        self.calls = []

    async def execute_tool_call(self, call):
        self.calls.append(call)
        return {"status": "ok"}


def run(coro):
    return asyncio.run(coro)


def test_load_opencode_tool_calls_parses_metadata():
    session_path = Path(__file__).parent / "data/opencode_session_sample.json"
    tool_calls = replay.load_opencode_tool_calls(session_path)

    assert len(tool_calls) == 2
    write_call = tool_calls[0]
    assert write_call.tool == "write"
    assert write_call.status == "completed"
    assert write_call.expected_exit() is None

    bash_call = tool_calls[1]
    assert bash_call.tool == "bash"
    assert bash_call.expected_exit() == 0
    assert bash_call.output == "done\n"


def test_parse_iterable_status_handles_string_payload():
    stdout, exit_code = replay.parse_iterable_status("['>>>>> ITERABLE', 'line one', {'exit': 2}]")
    assert stdout == "line one"
    assert exit_code == 2


def test_summarize_result_extracts_stdout_and_exit():
    raw_result = {"status": "['>>>>> ITERABLE', 'hello', 'world', {'exit': 0}]"}
    summary = replay.summarize_result("bash", raw_result)
    assert summary["stdout"] == "hello\nworld"
    assert summary["exit"] == 0


@pytest.mark.parametrize(
    "summary,expected_mismatch",
    [
        ({"stdout": "hi", "exit": 1}, "exit mismatch"),
        ({"stdout": "", "exit": 0}, "expected stdout present"),
    ],
)
def test_compare_expected_detects_mismatches(summary, expected_mismatch):
    call = replay.ToolCall(
        tool="bash",
        input={"command": "echo hi"},
        status="completed",
        output="hi\n",
        metadata={"exit": 0},
    )
    mismatches = replay.compare_expected(call, summary, path_normalizers=[])
    assert mismatches and expected_mismatch in mismatches[0]


def test_compare_expected_accepts_matching_results():
    call = replay.ToolCall(
        tool="bash",
        input={"command": "echo hi"},
        status="completed",
        output="hi\n",
        metadata={"exit": 0},
    )
    summary = {"stdout": "hi\n", "exit": 0}
    mismatches = replay.compare_expected(call, summary, path_normalizers=[])
    assert mismatches == []


def test_compare_expected_ignores_workspace_path_differences():
    call = replay.ToolCall(
        tool="bash",
        input={"command": "ls"},
        status="completed",
        output="/old/workspace\nfile.c\n",
        metadata={"exit": 0},
    )
    summary = {"stdout": "/new/workspace\nfile.c\n", "exit": 0}
    mismatches = replay.compare_expected(
        call,
        summary,
        path_normalizers=["/old/workspace", "/new/workspace"],
    )
    assert mismatches == []


def test_replay_tool_call_maps_read(monkeypatch):
    executor = StubExecutor()
    run(
        replay.replay_tool_call(
            executor,
            "read",
            {"filePath": "/tmp/project/src/main.c", "offset": 5, "limit": 100},
            workspace_path=Path("/tmp/project"),
            todo_manager=None,
            strip_prefix="/tmp/project",
        )
    )
    call = executor.calls[-1]
    assert call["function"] == "read"
    assert call["arguments"]["path"] == "src/main.c"
    assert call["arguments"]["offset"] == 5
    assert call["arguments"]["limit"] == 100


def test_replay_tool_call_maps_list():
    executor = StubExecutor()
    run(
        replay.replay_tool_call(
            executor,
            "list",
            {"path": "/tmp/project", "depth": 2},
            workspace_path=Path("/tmp"),
            todo_manager=None,
            strip_prefix="/tmp",
        )
    )
    call = executor.calls[-1]
    assert call["function"] == "list"
    assert call["arguments"]["path"] == "project"
    assert call["arguments"]["depth"] == 2


def test_replay_tool_call_maps_edit_replace_all():
    executor = StubExecutor()
    run(
        replay.replay_tool_call(
            executor,
            "edit",
            {
                "filePath": "/tmp/project/test.c",
                "oldString": "foo",
                "newString": "bar",
                "replaceAll": True,
            },
            workspace_path=Path("/tmp/project"),
            todo_manager=None,
            strip_prefix="/tmp/project",
        )
    )
    call = executor.calls[-1]
    assert call["function"] == "edit"
    assert call["arguments"]["path"] == "test.c"
    assert call["arguments"]["old"] == "foo"
    assert call["arguments"]["new"] == "bar"
    assert call["arguments"]["count"] == 0
