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
