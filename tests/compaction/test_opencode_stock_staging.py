import json
from pathlib import Path

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, StockExecutionError
from breadboard_engine.security.stock_install import stock_install_digest


@pytest.mark.parametrize("altered", [False, True], ids=["missing", "altered"])
def test_unstaged_or_altered_sg_rejects_before_startup(tmp_path, altered):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "package.json").write_text(json.dumps({"name": "oh-my-opencode", "version": "3.10.0"}))
    if altered:
        binary = plugin / "node_modules/@ast-grep/cli/sg"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"not the recorded stock ast-grep binary")
        binary.chmod(0o755)
    with pytest.raises(StockExecutionError, match="stage_opencode_stock_dependencies.py"):
        OpenCodeToolSession(str(workspace), "openai/gpt-5.2", binary=Path("not-launched"),
                            plugin_root=plugin, read_only_roots=[{
                                "path": str(plugin), "digest": stock_install_digest(plugin),
                            }])
    assert list((workspace / ".breadboard/tmp").glob("opencode-tools-*")) == []
