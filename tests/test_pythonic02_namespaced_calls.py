from tool_calling.core import ToolDefinition, ToolParameter
from tool_calling.pythonic02 import Pythonic02Dialect


def test_pythonic02_parses_namespaced_tool_calls() -> None:
    dialect = Pythonic02Dialect()
    tools = [
        ToolDefinition(
            type_id="python",
            name="todo.create",
            description="Create a todo item.",
            parameters=[
                ToolParameter(name="title", type="string"),
                ToolParameter(name="metadata", type="object"),
            ],
        ),
    ]

    calls = dialect.parse_calls('<TOOL_CALL>todo.create(title="A")</TOOL_CALL>', tools)
    assert len(calls) == 1
    assert calls[0].function == "todo.create"
    assert calls[0].arguments == {"title": "A"}


def test_pythonic02_parses_multiline_string_args() -> None:
    dialect = Pythonic02Dialect()
    tools = [
        ToolDefinition(
            type_id="python",
            name="write",
            description="Write a file.",
            parameters=[
                ToolParameter(name="filePath", type="string"),
                ToolParameter(name="content", type="string"),
            ],
        ),
    ]

    payload = "<TOOL_CALL>write(filePath=\"a.txt\", content=\"line1\nline2\")</TOOL_CALL>"
    calls = dialect.parse_calls(payload, tools)
    assert len(calls) == 1
    assert calls[0].function == "write"
    assert calls[0].arguments["filePath"] == "a.txt"
    assert calls[0].arguments["content"] == "line1\nline2"


def test_pythonic02_parses_bare_tool_call_with_json_args() -> None:
    dialect = Pythonic02Dialect()
    tools = [
        ToolDefinition(
            type_id="diff",
            name="patch",
            description="Apply a patch.",
            parameters=[ToolParameter(name="patchText", type="string")],
        ),
    ]

    # Providers often decode the inner "\\n" escapes into literal newlines, making the JSON invalid.
    payload = "<TOOL_CALL>patch</TOOL_CALL>\n{\"patch\":\"*** Begin Patch\n*** End Patch\"}"
    calls = dialect.parse_calls(payload, tools)
    assert len(calls) == 1
    assert calls[0].function == "patch"
    assert calls[0].arguments == {"patch": "*** Begin Patch\n*** End Patch"}


def test_pythonic02_repairs_missing_closing_paren_and_trailing_brace() -> None:
    dialect = Pythonic02Dialect()
    tools = [
        ToolDefinition(
            type_id="python",
            name="write",
            description="Write a file.",
            parameters=[
                ToolParameter(name="filePath", type="string"),
                ToolParameter(name="content", type="string"),
            ],
        ),
    ]

    # Real provider output can omit the final ")" and include a stray "}" suffix.
    payload = r'<TOOL_CALL>write(filePath="fs.h", content="#ifndef FS_H\n#define FS_H\n"} </TOOL_CALL>'
    calls = dialect.parse_calls(payload, tools)
    assert len(calls) == 1
    assert calls[0].function == "write"
    assert calls[0].arguments["filePath"] == "fs.h"
    assert calls[0].arguments["content"].startswith("#ifndef FS_H\n#define FS_H\n")

