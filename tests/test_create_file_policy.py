import pytest

from breadboard_engine.execution.enhanced_executor import EnhancedToolExecutor


class RecordingSandbox:
    def __init__(self) -> None:
        self.patches: list[str] = []
        self.writes: dict[str, str] = {}

    def apply_patch(self, patch: str) -> dict[str, bool]:
        self.patches.append(patch)
        return {"ok": True}

    def write_text(self, path: str, content: str) -> dict[str, bool]:
        self.writes[path] = content
        return {"ok": True}

@pytest.mark.asyncio
async def test_udiff_dev_null_normalization(tmp_path):
    cfg = {
        "workspace": str(tmp_path),
        "tools": {
            "dialects": {
                "create_file_policy": {
                    "unified_diff": {"use_dev_null": True}
                }
            }
        }
    }
    sandbox = RecordingSandbox()
    exe = EnhancedToolExecutor(sandbox, cfg)
    patch = """--- a/new.py
+++ b/new.py
@@ -0,0 +1,1 @@
+print(1)
"""
    result = await exe.execute_tool_call(
        {"function": "apply_unified_patch", "arguments": {"patch": patch}}
    )
    assert result == {"ok": True}
    assert sandbox.patches and "--- /dev/null" in sandbox.patches[0]

    context = exe.get_workspace_context()
    assert context["files_created_this_session"] == ["new.py"]
    assert context["recent_operations"][-1]["files_affected"] == ["new.py"]


@pytest.mark.asyncio
async def test_aider_prefer_write_on_create(tmp_path):
    cfg = {
        "workspace": str(tmp_path),
        "tools": {
            "dialects": {
                "create_file_policy": {
                    "aider_search_replace": {"prefer_write_file_tool": True}
                }
            }
        }
    }
    sandbox = RecordingSandbox()
    exe = EnhancedToolExecutor(sandbox, cfg)
    tool_call = {"function": "apply_search_replace", "arguments": {"file_name": "x.txt", "search": "", "replace": "hello"}}

    out = await exe.execute_tool_call(tool_call)
    assert out == {"ok": True}
    assert sandbox.writes == {"x.txt": "hello"}

    context = exe.get_workspace_context()
    assert context["files_created_this_session"] == ["x.txt"]


@pytest.mark.asyncio
async def test_successful_mutations_update_validation_context(tmp_path):
    cfg = {
        "workspace": str(tmp_path),
        "enhanced_tools": {
            "validation": {
                "enabled": True,
                "rules": ["no_redundant_creation", "reads_before_bash"],
                "rule_config": {"reads_before_bash": {"strictness": "error"}},
            }
        },
    }
    sandbox = RecordingSandbox()
    exe = EnhancedToolExecutor(sandbox, cfg)

    first = await exe.execute_tool_call(
        {"function": "write_file", "arguments": {"path": "created.txt", "content": "one"}}
    )
    assert first == {"ok": True}
    assert exe.get_workspace_context()["files_created_this_session"] == ["created.txt"]

    duplicate = await exe.execute_tool_call(
        {"function": "write_file", "arguments": {"path": "created.txt", "content": "two"}}
    )
    assert duplicate["validation_failure"] is True

    blocked_bash = await exe.execute_tool_call(
        {"function": "run_shell", "arguments": {"command": "pytest -q"}}
    )
    assert blocked_bash["validation_failure"] is True

    patch = """--- a/existing.py
+++ b/existing.py
@@ -1 +1 @@
-old
+new
"""
    edited = await exe.execute_tool_call(
        {"function": "apply_unified_patch", "arguments": {"patch": patch}}
    )
    assert edited == {"ok": True}
    context = exe.get_workspace_context()
    assert context["files_modified_this_session"] == ["existing.py"]
    assert context["recent_operations"][-1]["files_affected"] == ["existing.py"]


@pytest.mark.asyncio
async def test_failed_mutation_does_not_update_file_context(tmp_path):
    exe = EnhancedToolExecutor(RecordingSandbox(), {"workspace": str(tmp_path)})
    patch = """--- a/existing.py
+++ b/existing.py
@@ -1 +1 @@
-old
+new
"""

    result = await exe.execute_tool_call(
        {"function": "apply_unified_patch", "arguments": {"patch": patch}},
        exec_func=lambda _call: {"ok": False, "error": "rejected"},
    )

    assert result["ok"] is False
    context = exe.get_workspace_context()
    assert context["files_created_this_session"] == []
    assert context["files_modified_this_session"] == []
    assert "files_affected" not in context["recent_operations"][-1]
