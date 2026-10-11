"""Lane-only pre-run fixtures use the unmodified stock import command."""
import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, StockExecutionError
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment


def test_native_empty_root_and_child_import_under_same_isolation(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    options = dict(binary=binary, timeout=60, environment=isolated_environment(tmp_path / "home"))
    seed = OpenCodeToolSession(str(workspace), MODEL, **options)
    try:
        root = seed.api("GET", "/session/" + seed.session_id)
        child = seed.api("POST", "/session", {"parentID": seed.session_id})
        exports = [{"info": info, "messages": seed.api("GET", "/session/" + info["id"] + "/message")}
                   for info in (root, child)]
        assert all(item["messages"] == [] for item in exports)
    finally:
        seed.close()
    resumed = OpenCodeToolSession(str(workspace), MODEL, session_imports=exports,
                                 imported_session_id=root["id"], **options)
    try:
        assert resumed.session_id == root["id"]
        children = resumed.api("GET", "/session/" + root["id"] + "/children")
        assert [info["id"] for info in children] == [child["id"]]
        assert resumed.api("GET", "/session/" + root["id"] + "/message") == []
    finally:
        resumed.close()
    exports[0]["messages"] = [{"info": {"role": "assistant"}}]
    with pytest.raises(StockExecutionError, match="pre-run empty"):
        OpenCodeToolSession(str(workspace), MODEL, session_imports=exports,
                            imported_session_id=root["id"], **options)
