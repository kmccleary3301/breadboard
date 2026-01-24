import ray
import pytest

from agentic_coder_prototype.execution.enhanced_executor import EnhancedToolExecutor
from breadboard.sandbox_v2 import DevSandboxV2


@pytest.fixture(scope="module")
def ray_cluster():
    ray.shutdown()
    ray.init(ignore_reinit_error=True)
    yield
    ray.shutdown()


def test_udiff_dev_null_normalization(ray_cluster, tmp_path):
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
    sb = DevSandboxV2.options(name="sb-cfp").remote(image="python-dev:latest", workspace=str(tmp_path))
    exe = EnhancedToolExecutor(sb, cfg)
    patch = """--- a/new.py
+++ b/new.py
@@ -0,0 +1,1 @@
+print(1)
"""
    # Invoke internal helper directly
    normalized = exe._normalize_udiff_add_headers(patch)
    assert "--- /dev/null" in normalized


def test_aider_prefer_write_on_create(ray_cluster, tmp_path):
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
    sb = DevSandboxV2.options(name="sb-cfp2").remote(image="python-dev:latest", workspace=str(tmp_path))
    exe = EnhancedToolExecutor(sb, cfg)
    tool_call = {"function": "apply_search_replace", "arguments": {"file_name": "x.txt", "search": "", "replace": "hello"}}

    # Use the async path via event loop
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    out = loop.run_until_complete(exe.execute_tool_call(tool_call))
    assert isinstance(out, dict)
